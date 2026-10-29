"""V3 草稿值批量保存 / CAS / 幂等（M12-06 / INH-2141）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..core import config_values, locks, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError
from . import config_v3_catalog

MAX_BATCH = 100


def _etag(revision: int) -> str:
    return '"%d"' % int(revision)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _find_release(doc: Dict[str, Any], config_id: str, draft_id: str):
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    for release in (config.get("releases") or {}).values():
        if "d0-" + str(release["release_id"]) == str(draft_id):
            return config, release
    raise AppError(NOT_FOUND, "草稿不存在", draft_id=draft_id)


def _definition_for(release: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    revision = int(release.get("definition_revision", 0))
    for snapshot in (config.get("definition", {}).get("history") or []):
        if int(snapshot.get("revision", -1)) == revision:
            return snapshot
    return {"revision": revision, "parameters": {}}


def get_draft(profile_id: str, config_id: str, draft_id: str) -> Dict[str, Any]:
    with storage.use_profile(profile_id):
        doc = config_v3_catalog._load(profile_id)
    config, release = _find_release(doc, config_id, draft_id)
    d0 = release.setdefault("d0", {"values": {}, "frozen": False, "revision": 0})
    return {
        "draft_id": draft_id,
        "config_id": config_id,
        "release_id": release["release_id"],
        "revision": int(d0.get("revision", 0)),
        "frozen": bool(d0.get("frozen", False)),
        "values": d0.get("values") or {},
    }


def patch_values(
    profile_id: str,
    config_id: str,
    draft_id: str,
    payload: Dict[str, Any],
    if_match: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise AppError(INVALID_INPUT, "items 必须是非空数组", field="items")
    if len(items) > MAX_BATCH:
        raise AppError(INVALID_INPUT, "单批最多 %d 条" % MAX_BATCH, field="items")
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id or len(request_id) > 100:
        raise AppError(INVALID_INPUT, "request_id 必填且 ≤100 字符", field="request_id")
    expected_raw = str(if_match or payload.get("expected_revision") or "").strip().strip('"')
    try:
        expected = int(expected_raw)
    except ValueError:
        raise AppError(INVALID_INPUT, "缺少合法 If-Match/expected_revision", field="expected_revision")

    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = config_v3_catalog._load(profile_id)
        config, release = _find_release(doc, config_id, draft_id)
        d0 = release.setdefault("d0", {"values": {}, "frozen": False, "revision": 0})
        d0.setdefault("values", {})
        d0.setdefault("revision", 0)
        d0.setdefault("receipts", {})
        if d0.get("frozen"):
            raise AppError(REVISION_CONFLICT, "草稿已冻结", draft_id=draft_id)
        if request_id in d0["receipts"]:
            result = get_draft(profile_id, config_id, draft_id)
            result["replayed"] = True
            return result, _etag(result["revision"])
        if int(d0["revision"]) != expected:
            raise AppError(
                REVISION_CONFLICT,
                "草稿已被其他编辑者更新",
                expected=expected,
                current=int(d0["revision"]),
            )
        definition = _definition_for(release, config)
        params = definition.get("parameters") or {}
        changed: List[str] = []
        next_values = dict(d0["values"])
        for item in items:
            if not isinstance(item, dict):
                raise AppError(INVALID_INPUT, "items 的元素必须是对象", field="items")
            pid = str(item.get("parameter_id") or "").strip()
            if pid not in params:
                raise AppError(NOT_FOUND, "参数不在该版本的定义修订中", parameter_id=pid)
            next_values[pid] = config_values.apply_member_patch(
                next_values.get(pid), item, str(params[pid].get("type") or "")
            )
            changed.append(pid)
        d0["values"] = next_values
        d0["revision"] = int(d0["revision"]) + 1
        d0["receipts"][request_id] = {
            "revision": d0["revision"],
            "items": changed,
            "at": _now(),
        }
        config_v3_catalog._save(profile_id, doc)
        result = {
            "draft_id": draft_id,
            "config_id": config_id,
            "release_id": release["release_id"],
            "revision": d0["revision"],
            "frozen": False,
            "values": {pid: next_values[pid] for pid in changed if pid in next_values},
        }
        return result, _etag(d0["revision"])
