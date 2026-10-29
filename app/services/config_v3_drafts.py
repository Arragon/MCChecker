"""V3 草稿值批量保存 / CAS / 幂等（M12-06 / INH-2141）。"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..core import config_values, locks, storage
from ..core.errors import (
    DEFAULT_ALREADY_FROZEN,
    INVALID_INPUT,
    NOT_FOUND,
    REVISION_CONFLICT,
    AppError,
)
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
    raw = if_match if if_match is not None and str(if_match).strip() else payload.get("expected_revision")
    expected_raw = str(raw if raw is not None else "").strip().strip('"')
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


def freeze_default(
    profile_id: str, config_id: str, draft_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """canonical D0 只冻结一次；同 request_id 重放同一回执。"""
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id or len(request_id) > 100:
        raise AppError(INVALID_INPUT, "request_id 必填且 ≤100 字符", field="request_id")
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = config_v3_catalog._load(profile_id)
        config, release = _find_release(doc, config_id, draft_id)
        d0 = release.setdefault("d0", {"values": {}, "frozen": False, "revision": 0})
        receipts = d0.setdefault("freeze_receipts", {})
        if request_id in receipts:
            result = get_draft(profile_id, config_id, draft_id)
            result["replayed"] = True
            return result
        if d0.get("frozen"):
            raise AppError(
                DEFAULT_ALREADY_FROZEN,
                "该版本的 canonical D0 已冻结",
                draft_id=draft_id,
            )
        definition = _definition_for(release, config)
        params = definition.get("parameters") or {}
        for pid, row in (d0.get("values") or {}).items():
            if pid not in params:
                raise AppError(NOT_FOUND, "草稿值引用了不存在的参数", parameter_id=pid)
            membership = row.get("membership", "not_introduced")
            if membership == "removed" or not row.get("enabled", True):
                continue
            state = row.get("value_state", "unset")
            if state == "present":
                kind = {
                    "number": "decimal", "decimal": "decimal",
                    "int": "int", "integer": "int", "float": "decimal",
                    "bool": "bool", "boolean": "bool",
                }.get(str(params[pid].get("type") or "").lower(), "string")
                parsed = config_values.parse_typed(
                    row.get("value"), kind, params[pid].get("unit") or None
                )
                parsed.validate()
        binding_set = release.setdefault(
            "binding_set", {"revision": 1, "frozen": False, "bindings": {}}
        )
        for binding in (binding_set.get("bindings") or {}).values():
            pid = str(binding.get("parameter_id") or "")
            if pid not in params:
                raise AppError(NOT_FOUND, "绑定引用了不存在的参数", parameter_id=pid)
            if not binding.get("file_id") or not binding.get("version_id") or not binding.get("locator"):
                raise AppError(INVALID_INPUT, "绑定来源不完整", binding_id=binding.get("binding_id"))
            row = (d0.get("values") or {}).get(pid) or {}
            if row.get("membership", "not_introduced") != "included" or not row.get("enabled", True):
                raise AppError(INVALID_INPUT, "绑定参数未被 included+enabled", parameter_id=pid)
        d0["frozen"] = True
        d0["frozen_at"] = _now()
        d0["frozen_snapshot"] = copy.deepcopy(d0.get("values") or {})
        d0["freeze_revision"] = int(d0.get("revision", 0))
        binding_set["frozen"] = True
        release["default_revision_id"] = "d0-" + release["release_id"]
        release["default_frozen_at"] = d0["frozen_at"]
        receipts[request_id] = {"frozen_at": d0["frozen_at"], "revision": d0["freeze_revision"]}
        config_v3_catalog._save(profile_id, doc)
        return get_draft(profile_id, config_id, draft_id)
