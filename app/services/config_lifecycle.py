"""配置/机型生命周期收口（M7-12 / INH-1971）。

三件事：

1. **机型软退役**：保留 `device_models.json` 条目与 profile 目录，历史对象仍可按
   稳定 ID 访问；退役后写请求返回 409，读请求不受影响。
2. **删除保护**：机型仍被基线/观测/产物/交付/核验引用时，`DELETE /profiles` 只能
   改为退役，不能物理删除管理历史。
3. **旧入口守卫的补充**：sources 改名/删除、定时下载与旧删除都必须在写之前问一次
   追踪策略；本模块只提供生命周期部分，策略判定仍由 `config_policies` 负责。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core import device_models, storage
from ..core.config_repository import ConfigRepository, now_iso
from ..core.errors import INVALID_INPUT, LIFECYCLE_BLOCKED, NOT_FOUND, AppError
from . import config_catalog

_PROFILE_KINDS = (
    "baseline",
    "observation",
    "artifact",
    "delivery",
    "verification",
    "change_set",
    "tracking_policy",
)


def repository() -> ConfigRepository:
    return ConfigRepository(storage.DATA_DIR)


def _label(actor: Any) -> str:
    if actor is None:
        return ""
    if isinstance(actor, dict):
        return str(actor.get("name") or actor.get("ip") or "")
    return str(getattr(actor, "name", "") or getattr(actor, "ip", "") or "")


def profile_status(pid: str) -> str:
    return device_models.model_status(pid) or "missing"


def profile_config_refs(repo: ConfigRepository, pid: str) -> Dict[str, List[str]]:
    """管理域里仍引用该机型的对象；用于删除前拦截与退役提示。"""
    wanted = str(pid)
    refs: Dict[str, List[str]] = {}
    for kind in _PROFILE_KINDS:
        found = []
        for item in repo.list_objects(kind):
            owner = str(item.get("profile_id") or "")
            target = item.get("target") or {}
            if not owner:
                owner = str(target.get("profile_id") or "")
            if owner == wanted:
                found.append(str(item.get("id")))
        if found:
            refs[kind] = found
    return refs


def retire_profile(
    repo: ConfigRepository,
    actor: Any,
    *,
    pid: Any,
    reason: Any = "",
    replaced_by: Any = None,
) -> Dict[str, Any]:
    target = str(pid or "").strip()
    if not target or not device_models.exists(target):
        raise AppError(NOT_FOUND, "机型不存在", profile_id=target)
    if target == device_models.DEFAULT_MODEL_ID:
        raise AppError(INVALID_INPUT, "默认机型不能退役", field="profile_id")
    if device_models.is_retired(target):
        return _profile_payload(target)
    reason_text = str(reason or "").strip() or "机型退役"
    if not device_models.retire_model(
        target,
        retired_by=_label(actor),
        reason=reason_text,
        replaced_by=str(replaced_by or "").strip() or None,
    ):
        raise AppError(NOT_FOUND, "机型不存在", profile_id=target)
    tx = repo.transaction(actor, "退役机型 %s：%s" % (target, reason_text), action="profile.retire")
    try:
        tx.commit()
    except BaseException:
        # 审计没落盘，就不能让注册表显示"已退役"；否则历史与状态对不上。
        device_models.restore_model(target)
        raise
    return _profile_payload(target)


def restore_profile(repo: ConfigRepository, actor: Any, *, pid: Any) -> Dict[str, Any]:
    target = str(pid or "").strip()
    if not target or not device_models.exists(target):
        raise AppError(NOT_FOUND, "机型不存在", profile_id=target)
    if not device_models.is_retired(target):
        return _profile_payload(target)
    previous = _profile_payload(target)
    if not device_models.restore_model(target):
        raise AppError(NOT_FOUND, "机型不存在", profile_id=target)
    tx = repo.transaction(actor, "恢复机型 %s" % target, action="profile.restore")
    try:
        tx.commit()
    except BaseException:
        device_models.retire_model(
            target,
            retired_by=str(previous.get("retired_by") or ""),
            reason=str(previous.get("reason") or "恢复失败回滚"),
            replaced_by=previous.get("replaced_by"),
        )
        raise
    return _profile_payload(target)


def assert_writable(pid: str, method: str, path: str) -> None:
    """退役机型只允许读与显式恢复；其余写请求在进入 handler 之前拦下。"""
    if method in ("GET", "HEAD", "OPTIONS"):
        return
    if str(path or "").rstrip("/").endswith("/restore"):
        return
    if device_models.is_retired(pid):
        raise AppError(
            LIFECYCLE_BLOCKED,
            "机型已退役，不能继续写入",
            profile_id=pid,
            hint="先在机型管理里恢复该机型；历史对象仍可读",
        )


def assert_deletable(repo: ConfigRepository, pid: str) -> None:
    refs = profile_config_refs(repo, pid)
    if refs:
        raise AppError(
            LIFECYCLE_BLOCKED,
            "该机型仍被配置管理历史引用，不能物理删除",
            profile_id=pid,
            references=refs,
            hint="请先退役机型；历史基线/产物/交付记录必须保留",
        )


def record_copy(repo: ConfigRepository, actor: Any, source: str, target: str) -> None:
    """复制机型只复制旧 profile 文件，不复制管理域对象与旧审计。"""
    tx = repo.transaction(
        actor,
        "复制机型 %s → %s（不继承管理域基线/审计）" % (source, target),
        action="profile.copy",
    )
    tx.commit()


def family_refs(repo: ConfigRepository, family_id: str) -> Dict[str, List[str]]:
    refs: Dict[str, List[str]] = {}
    for kind in ("logical_file", "parameter", "baseline", "artifact", "tracking_policy"):
        found = [
            str(item.get("id"))
            for item in repo.list_objects(kind)
            if str(item.get("family_id") or "") == str(family_id)
        ]
        if found:
            refs[kind] = found
    return refs


def retire_family(
    repo: ConfigRepository,
    actor: Any,
    *,
    family_id: Any,
    reason: Any = "",
    acknowledge_refs: bool = False,
) -> Dict[str, Any]:
    family = config_catalog.get_family(repo, str(family_id or ""))
    refs = family_refs(repo, family["id"])
    if refs and not acknowledge_refs:
        raise AppError(
            LIFECYCLE_BLOCKED,
            "配置类别仍被活动对象引用，不能直接退役",
            family_id=family["id"],
            references=refs,
            hint="先迁移/退役引用对象，或显式确认 acknowledge_refs",
        )
    stamp = now_iso()
    tx = repo.transaction(actor, str(reason or "").strip() or "配置类别退役", action="family.retire")
    tx.put(
        "family",
        dict(family, status="retired", retired_at=stamp, retired_by=_label(actor)),
        object_id=family["id"],
    )
    tx.commit()
    return config_catalog.get_family(repo, family["id"])


def restore_family(repo: ConfigRepository, actor: Any, *, family_id: Any) -> Dict[str, Any]:
    family = config_catalog.get_family(repo, str(family_id or ""))
    tx = repo.transaction(actor, "恢复配置类别", action="family.reactivate")
    tx.put(
        "family",
        dict(family, status="active", retired_at=None, retired_by=None),
        object_id=family["id"],
    )
    tx.commit()
    return config_catalog.get_family(repo, family["id"])


def _profile_payload(pid: str) -> Dict[str, Any]:
    for model in device_models.load_models():
        if model.get("id") == pid:
            return dict(model)
    raise AppError(NOT_FOUND, "机型不存在", profile_id=pid)
