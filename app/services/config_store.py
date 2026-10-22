"""配置仓储的构造与只读摘要（M5-08）。

service 层统一从这里拿 `ConfigRepository`，避免每个 handler 自己拼路径。
只做构造与读取摘要，**不写盘**：读接口不应顺手创建目录。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..config import Settings
from ..core.config_repository import KIND_PREFIX, ConfigRepository


def repository(settings: Settings) -> ConfigRepository:
    return ConfigRepository(settings.data_dir)


def state_summary(repo: ConfigRepository) -> Dict[str, Any]:
    """各类型的当前对象数 + 存盘序号；state 损坏时抛 `CorruptDataError`。"""
    state = repo.load_state()
    heads = state.get("heads") or {}
    counts = {kind: len(heads.get(kind) or {}) for kind in KIND_PREFIX}
    return {
        "store_seq": int(state.get("seq") or 0),
        "counts": counts,
        "total": sum(counts.values()),
    }


def profile_summary(repo: ConfigRepository, profile_id: str) -> Dict[str, Any]:
    """某机型在配置域里的可见对象数（基线/观测/变更单/产物/设备）。"""
    state = repo.load_state()
    heads = state.get("heads") or {}

    def owned(kind: str, field: str = "profile_id") -> int:
        total = 0
        for object_id in (heads.get(kind) or {}):
            payload = repo.get(kind, object_id)
            if payload is not None and str(payload.get(field) or "") == profile_id:
                total += 1
        return total

    return {
        "profile_id": profile_id,
        "baselines": owned("baseline"),
        "observations": owned("observation"),
        "change_sets": owned("change_set"),
        "artifacts": _artifacts_for(repo, heads, profile_id),
        "deliveries": _deliveries_for(repo, heads, profile_id),
    }


def _artifacts_for(repo: ConfigRepository, heads: Dict[str, Dict[str, str]], profile_id: str) -> int:
    """产物挂在基线上，因此按"该机型的基线"反查。"""
    owned = {
        object_id
        for object_id in (heads.get("baseline") or {})
        if str((repo.get("baseline", object_id) or {}).get("profile_id") or "") == profile_id
    }
    total = 0
    for object_id in heads.get("artifact") or {}:
        payload = repo.get("artifact", object_id) or {}
        if payload.get("baseline_id") in owned:
            total += 1
    return total


def _deliveries_for(repo: ConfigRepository, heads: Dict[str, Dict[str, str]], profile_id: str) -> int:
    total = 0
    for object_id in heads.get("delivery") or {}:
        target = (repo.get("delivery", object_id) or {}).get("target") or {}
        if str(target.get("profile_id") or "") == profile_id:
            total += 1
    return total


def optional_pointer(repo: ConfigRepository, name: str, key: str) -> Optional[Any]:
    return repo.pointer(name, key)
