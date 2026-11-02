"""M16-08 工作区更新计划与来源覆盖策略。

默认策略：auto off、默认 6 小时、无锚点、无 profile override。来源三态：
- `inherit`：跟随工作区默认频率；
- `custom`：独立 1–2160 小时；
- `manual`：不进入自动投递，手动更新仍可用。

暂停全局自动只影响自动投递；手动任务仍可由用户启动。批量修改来源计划
使用 `sync_sources.batch_update_schedule` 的全有或全无 CAS。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.errors import INVALID_INPUT, REVISION_CONFLICT, AppError
from app.core.sync_models import (
    SOURCE_SCHEDULE_MODES,
    SyncSource,
    validate_enum,
    validate_id,
    validate_interval_hours,
    validate_revision,
    validate_timestamp,
    validate_workspace_id,
)
from app.services import sync_sources

from . import sync_store

FILE = "policy.json"
DEFAULT_HOURS = 6
DEFAULT_DOC: Dict[str, Any] = {
    "revision": 0,
    "enabled": False,
    "default_interval_hours": DEFAULT_HOURS,
    "anchor": None,
    "profile_overrides": {},
}
PATCH_FIELDS = frozenset({"enabled", "default_interval_hours", "anchor", "profile_overrides"})


def _normalize(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(doc, dict):
        raise AppError(INVALID_INPUT, "policy.json 形状损坏", field="policy")
    revision = validate_revision(doc.get("revision", 0), "revision")
    enabled = doc.get("enabled", False)
    if not isinstance(enabled, bool):
        raise AppError(INVALID_INPUT, "enabled 必须是布尔值", field="enabled")
    default_hours = validate_interval_hours(doc.get("default_interval_hours", DEFAULT_HOURS))
    if default_hours is None:
        raise AppError(INVALID_INPUT, "default_interval_hours 不能为空", field="default_interval_hours")
    anchor = validate_timestamp(doc.get("anchor"), "anchor", allow_none=True)
    overrides = doc.get("profile_overrides") or {}
    if not isinstance(overrides, dict):
        raise AppError(INVALID_INPUT, "profile_overrides 必须是对象", field="profile_overrides")
    clean_overrides: Dict[str, Dict[str, Any]] = {}
    for pid, raw in overrides.items():
        key = validate_id(pid, "profile_id")
        if not isinstance(raw, dict):
            raise AppError(INVALID_INPUT, "profile_override 必须是对象", field="profile_overrides")
        unknown = set(raw) - {"enabled", "interval_hours"}
        if unknown:
            raise AppError(
                INVALID_INPUT,
                "profile_override 含未知字段",
                field="profile_overrides",
                keys=sorted(unknown),
            )
        value: Dict[str, Any] = {}
        if "enabled" in raw:
            if not isinstance(raw["enabled"], bool):
                raise AppError(INVALID_INPUT, "profile_override.enabled 必须是布尔值", field="profile_overrides")
            value["enabled"] = raw["enabled"]
        if "interval_hours" in raw:
            value["interval_hours"] = validate_interval_hours(raw["interval_hours"])
        clean_overrides[key] = value
    return {
        "revision": revision,
        "enabled": enabled,
        "default_interval_hours": default_hours,
        "anchor": anchor,
        "profile_overrides": clean_overrides,
    }


def _load(workspace_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    return _normalize(sync_store.read_doc(wid, FILE, DEFAULT_DOC))


def get_policy(workspace_id: str) -> Dict[str, Any]:
    return _load(workspace_id)


def save_policy(
    workspace_id: str,
    expected_revision: Any,
    patch: Dict[str, Any],
) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    revision = validate_revision(expected_revision, "expected_revision")
    if not isinstance(patch, dict):
        raise AppError(INVALID_INPUT, "patch 必须是对象", field="patch")
    unknown = set(patch) - PATCH_FIELDS
    if unknown:
        raise AppError(INVALID_INPUT, "不允许修改的计划字段", field="patch", keys=sorted(unknown))

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        current = _normalize(doc)
        if current["revision"] != revision:
            raise AppError(REVISION_CONFLICT, "计划 revision 已变化", field="expected_revision")
        merged = dict(current)
        merged.update(patch)
        merged["revision"] = current["revision"] + 1
        return _normalize(merged)

    return _normalize(sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC))


def batch_update_source_schedule(workspace_id: str, updates: Any) -> List[Dict[str, Any]]:
    return sync_sources.batch_update_schedule(workspace_id, updates)


def resolve_interval(policy: Dict[str, Any], source: Any) -> Optional[int]:
    """纯函数：来源在自动计划下的有效间隔；None 表示不自动投递。"""
    if isinstance(source, SyncSource):
        mode = source.schedule_mode
        enabled = source.enabled
        interval = source.interval_hours
    elif isinstance(source, dict):
        mode = source.get("schedule_mode", "inherit")
        enabled = bool(source.get("enabled", True))
        interval = source.get("interval_hours")
    else:
        raise AppError(INVALID_INPUT, "source 必须是 SyncSource 或对象", field="source")
    mode = validate_enum(mode, SOURCE_SCHEDULE_MODES, "schedule_mode")
    if not policy.get("enabled") or not enabled or mode == "manual":
        return None
    if mode == "inherit":
        return int(policy.get("default_interval_hours") or DEFAULT_HOURS)
    return validate_interval_hours(interval)
