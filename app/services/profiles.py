"""机型新增/复制/重命名/删除（规范 §7.2、§7.3、§5.6；M3-16 / INH-1901）。

机型注册表是**全局数据**（`data/device_models.json`），不属于任何一个机型，所以成功写之后
递增 `default` 机型的 `settings` 分量——被删机型的计数没有意义，当前会话机型也没有随
`POST /profiles` 传进来。这个选择写在 PR 与测试里，避免接手者以为漏了。

删除是破坏性操作：数据目录整个移入恢复区（复制 → 逐文件 sha256 校验 → 才删源 → 写
manifest），失败逆序回滚；`storage.delete_profile_data` 的旧硬删语义一行不动。

复制范围由 `storage.copy_profile_data` 决定（`configs/` + `archive/` + 六个 JSON，
`schedule.last_run` 置 null；不含 cache/records/建议/IP 表/管理员/update_status），本层不重写。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from ..core import device_models, storage
from ..core.errors import FILE_EXISTS, INVALID_INPUT, NOT_FOUND, AppError
from . import recovery, revision
from . import scheduler as scheduler_service

logger = logging.getLogger(__name__)

PART = "settings"

NAME_FIELD = "name"
COPY_FROM_FIELD = "copy_from"
MAX_NAME = 100

BAD_NAME_MESSAGE = "机型名称需为 1-%d 个字符，且不含控制字符" % MAX_NAME
DUPLICATE_NAME_MESSAGE = "已存在同名机型"
BAD_COPY_FROM_MESSAGE = "要复制的机型不存在"
DEFAULT_PROTECTED_MESSAGE = "默认机型不能删除"

#: 注册表级的写操作串行化：查重与 `add_model`/`rename_model` 之间有重 I/O（复制/恢复），
#: 不能靠注册表锁跨过去（§3.5），所以用本模块的一把粗锁只串行机型管理，不碰业务写。
_LOCK = threading.Lock()


def list_profiles() -> Dict[str, Any]:
    """`GET /session` 之外没有单独的机型列表接口；保留服务层入口给后续复用。"""
    return {"items": [dict(model) for model in device_models.load_models()]}


def create_profile(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`POST /profiles` → 201 `{"profile": {"id","name"}}`。"""
    body = payload if isinstance(payload, dict) else {}
    name = _validated_name(body.get(NAME_FIELD))
    copy_from = body.get(COPY_FROM_FIELD)
    if copy_from is not None:
        copy_from = str(copy_from).strip()
        if not copy_from:
            copy_from = None
        elif not device_models.exists(copy_from):
            raise AppError(
                INVALID_INPUT, BAD_COPY_FROM_MESSAGE, field=COPY_FROM_FIELD
            )
    with _LOCK:
        if _name_index(device_models.load_models(), name) is not None:
            raise AppError(FILE_EXISTS, DUPLICATE_NAME_MESSAGE, field=NAME_FIELD)
        model_id = device_models.add_model(name, copy_from)
    revision.bump(storage.DEFAULT_PROFILE, PART)
    _reload_scheduler()
    return {
        "profile": {"id": model_id, "name": device_models.get_model_name(model_id)}
    }


def rename_profile(model_id: str, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`PATCH /profiles/{pid}` → `{"profile": {"id","name"}}`。"""
    body = payload if isinstance(payload, dict) else {}
    name = _validated_name(body.get(NAME_FIELD))
    target = str(model_id)
    with _LOCK:
        models = device_models.load_models()
        if _model_index(models, target) is None:
            raise AppError(NOT_FOUND, "机型不存在")
        if _name_index(models, name, ignore_id=target) is not None:
            raise AppError(FILE_EXISTS, DUPLICATE_NAME_MESSAGE, field=NAME_FIELD)
        if not device_models.rename_model(target, name):
            raise AppError(NOT_FOUND, "机型不存在")
    revision.bump(storage.DEFAULT_PROFILE, PART)
    return {"profile": {"id": target, "name": device_models.get_model_name(target)}}


def delete_profile(
    model_id: str, actor_name: str = "", actor_ip: str = ""
) -> Dict[str, Any]:
    """`DELETE /profiles/{pid}` → `{"deleted": true, "recovery": "<目录名>"}`。

    默认机型 → 400 `INVALID_INPUT` + `detail.field="id"`（R18：底层 `ValueError` 不许透成 500）。
    """
    target = str(model_id)
    if target == storage.DEFAULT_PROFILE:
        raise AppError(
            INVALID_INPUT, DEFAULT_PROTECTED_MESSAGE, field="id"
        )
    with _LOCK:
        if _model_index(device_models.load_models(), target) is None:
            raise AppError(NOT_FOUND, "机型不存在")
        entry = recovery.move_profile_to_recovery(
            target, actor_name=actor_name, actor_ip=actor_ip
        )
        if not device_models.remove_model(target, delete_data=False):
            raise AppError(NOT_FOUND, "机型不存在")
    revision.bump(storage.DEFAULT_PROFILE, PART)
    _reload_scheduler()
    return {"deleted": True, "recovery": entry}


def _validated_name(raw: Any) -> str:
    text = str(raw if raw is not None else "").strip()
    if (
        not text
        or len(text) > MAX_NAME
        or any(ord(char) < 32 or ord(char) == 127 for char in text)
    ):
        raise AppError(INVALID_INPUT, BAD_NAME_MESSAGE, field=NAME_FIELD)
    return text


def _model_index(models: List[Dict[str, Any]], model_id: str) -> Optional[int]:
    for index, model in enumerate(models):
        if model.get("id") == model_id:
            return index
    return None


def _name_index(
    models: List[Dict[str, Any]], name: str, ignore_id: Optional[str] = None
) -> Optional[int]:
    for index, model in enumerate(models):
        if model.get("id") == ignore_id:
            continue
        if str(model.get("name") or "").strip() == name:
            return index
    return None


def _reload_scheduler() -> None:
    """机型增删后重算排期；调度器没在跑或重载失败不能把已成功的写报成失败。"""
    try:
        sched = scheduler_service.get_scheduler()
        if sched is not None:
            sched.reload()
    except Exception as exc:
        logger.warning("机型变更后调度器重载失败：%s", exc)
