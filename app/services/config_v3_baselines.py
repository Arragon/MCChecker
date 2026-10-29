"""V3 基线 Bn 草稿与不可变 head（M12-04 / INH-2139）。"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..core import config_values, locks, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError
from . import config_v3_catalog, config_v3_drafts

MAX_BATCH = 100


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _etag(revision: int) -> str:
    return '"%d"' % int(revision)


def _branch(config: Dict[str, Any]) -> Dict[str, Any]:
    return config.setdefault(
        "main_branch", {"head_id": None, "next_seq": 1, "baselines": {}}
    )


def _validated_values(release: Dict[str, Any], config: Dict[str, Any], values: Dict[str, Any]) -> None:
    definition = config_v3_drafts._definition_for(release, config)
    params = definition.get("parameters") or {}
    for pid, row in (values or {}).items():
        if pid not in params:
            raise AppError(NOT_FOUND, "草稿值引用了不存在的参数", parameter_id=pid)
        if row.get("membership", "not_introduced") == "removed" or not row.get("enabled", True):
            continue
        if row.get("value_state") == "present":
            kind = {
                "number": "decimal", "decimal": "decimal",
                "int": "int", "integer": "int", "float": "decimal",
                "bool": "bool", "boolean": "bool",
            }.get(str(params[pid].get("type") or "").lower(), "string")
            parsed = config_values.parse_typed(
                row.get("value"), kind, params[pid].get("unit") or None
            )
            parsed.validate()


def create_baseline_draft(profile_id: str, config_id: str, release_id: str) -> Dict[str, Any]:
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = config_v3_catalog._load(profile_id)
        config = doc["configs"].get(config_id)
        if config is None:
            raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
        release = (config.get("releases") or {}).get(release_id)
        if release is None:
            raise AppError(NOT_FOUND, "软件版本不存在", release_id=release_id)
        if not release.get("default_revision_id"):
            raise AppError(INVALID_INPUT, "canonical D0 尚未冻结，不能派生基线", release_id=release_id)
        existing = release.get("baseline_draft")
        if isinstance(existing, dict) and existing.get("status") == "draft":
            raise AppError(REVISION_CONFLICT, "该版本已有未发布的基线草稿", draft_id=existing["draft_id"])
        branch = _branch(config)
        head_id = branch.get("head_id")
        if head_id:
            source = branch["baselines"][head_id]["values"]
            source_label = head_id
        else:
            source = release["d0"].get("frozen_snapshot") or {}
            source_label = release["default_revision_id"]
        draft_id = "bd-" + uuid_hex()
        draft = {
            "draft_id": draft_id,
            "release_id": release_id,
            "status": "draft",
            "revision": 0,
            "values": copy.deepcopy(source),
            "source_revision_id": source_label,
            "definition_revision": int(release.get("definition_revision", 0)),
            "created_at": _now(),
            "receipts": {},
        }
        release["baseline_draft"] = draft
        config_v3_catalog._save(profile_id, doc)
        return _public_draft(draft)


def uuid_hex() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


def _public_draft(draft: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "draft_id": draft["draft_id"],
        "release_id": draft["release_id"],
        "status": draft["status"],
        "revision": int(draft.get("revision", 0)),
        "values": copy.deepcopy(draft.get("values") or {}),
        "source_revision_id": draft.get("source_revision_id"),
        "baseline_id": draft.get("baseline_id"),
    }


def _find_draft(config: Dict[str, Any], draft_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    for release in (config.get("releases") or {}).values():
        draft = release.get("baseline_draft")
        if isinstance(draft, dict) and draft.get("draft_id") == draft_id:
            return release, draft
    raise AppError(NOT_FOUND, "基线草稿不存在", draft_id=draft_id)


def get_baseline_draft(profile_id: str, config_id: str, draft_id: str) -> Dict[str, Any]:
    with storage.use_profile(profile_id):
        doc = config_v3_catalog._load(profile_id)
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    _release, draft = _find_draft(config, draft_id)
    return _public_draft(draft)


def patch_baseline_values(
    profile_id: str,
    config_id: str,
    draft_id: str,
    payload: Dict[str, Any],
    if_match: Optional[str],
) -> Tuple[Dict[str, Any], str]:
    items = payload.get("items")
    if not isinstance(items, list) or not items or len(items) > MAX_BATCH:
        raise AppError(INVALID_INPUT, "items 必须为 1–%d 条" % MAX_BATCH, field="items")
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id or len(request_id) > 100:
        raise AppError(INVALID_INPUT, "request_id 必填且 ≤100 字符", field="request_id")
    expected_raw = str(if_match or payload.get("expected_revision") or "").strip().strip('"')
    try:
        expected = int(expected_raw)
    except ValueError:
        raise AppError(INVALID_INPUT, "缺少合法 If-Match", field="expected_revision")
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = config_v3_catalog._load(profile_id)
        config = doc["configs"].get(config_id)
        if config is None:
            raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
        release, draft = _find_draft(config, draft_id)
        if draft.get("status") != "draft":
            raise AppError(REVISION_CONFLICT, "基线已发布，不可修改", draft_id=draft_id)
        receipts = draft.setdefault("receipts", {})
        if request_id in receipts:
            result = _public_draft(draft)
            result["replayed"] = True
            return result, _etag(result["revision"])
        if int(draft.get("revision", 0)) != expected:
            raise AppError(REVISION_CONFLICT, "草稿已被其他编辑者更新", current=int(draft.get("revision", 0)))
        definition = config_v3_drafts._definition_for(release, config)
        params = definition.get("parameters") or {}
        values = dict(draft.get("values") or {})
        changed: List[str] = []
        for item in items:
            if not isinstance(item, dict):
                raise AppError(INVALID_INPUT, "items 元素必须是对象", field="items")
            pid = str(item.get("parameter_id") or "")
            if pid not in params:
                raise AppError(NOT_FOUND, "参数不在该版本定义中", parameter_id=pid)
            values[pid] = config_values.apply_member_patch(
                values.get(pid), item, str(params[pid].get("type") or "")
            )
            changed.append(pid)
        draft["values"] = values
        draft["revision"] = int(draft.get("revision", 0)) + 1
        receipts[request_id] = {"revision": draft["revision"], "items": changed, "at": _now()}
        config_v3_catalog._save(profile_id, doc)
        result = _public_draft(draft)
        result["values"] = {pid: values[pid] for pid in changed if pid in values}
        return result, _etag(draft["revision"])


def publish_baseline(
    profile_id: str,
    config_id: str,
    draft_id: str,
    payload: Dict[str, Any],
    if_match: Optional[str],
) -> Tuple[Dict[str, Any], str]:
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id or len(request_id) > 100:
        raise AppError(INVALID_INPUT, "request_id 必填且 ≤100 字符", field="request_id")
    expected_raw = str(if_match or payload.get("expected_revision") or "").strip().strip('"')
    try:
        expected = int(expected_raw)
    except ValueError:
        raise AppError(INVALID_INPUT, "缺少合法 If-Match", field="expected_revision")
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = config_v3_catalog._load(profile_id)
        config = doc["configs"].get(config_id)
        if config is None:
            raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
        release, draft = _find_draft(config, draft_id)
        receipts = draft.setdefault("publish_receipts", {})
        if request_id in receipts:
            head_id = receipts[request_id]["baseline_id"]
            result = get_baseline(profile_id, config_id, head_id)
            result["replayed"] = True
            return result, _etag(int(draft.get("revision", 0)))
        if draft.get("status") != "draft":
            raise AppError(REVISION_CONFLICT, "基线已发布", baseline_id=draft.get("baseline_id"))
        if int(draft.get("revision", 0)) != expected:
            raise AppError(REVISION_CONFLICT, "草稿已被其他编辑者更新", current=int(draft.get("revision", 0)))
        _validated_values(release, config, draft.get("values") or {})
        branch = _branch(config)
        seq = int(branch.get("next_seq", 1))
        baseline_id = "bl-" + uuid_hex()
        baseline = {
            "baseline_id": baseline_id,
            "label": "B%d" % seq,
            "release_id": release["release_id"],
            "definition_revision": int(draft.get("definition_revision", 0)),
            "source_revision_id": draft.get("source_revision_id"),
            "values": copy.deepcopy(draft.get("values") or {}),
            "published_at": _now(),
            "draft_id": draft_id,
        }
        branch["baselines"][baseline_id] = baseline
        branch["head_id"] = baseline_id
        branch["next_seq"] = seq + 1
        draft["status"] = "published"
        draft["baseline_id"] = baseline_id
        draft["published_at"] = baseline["published_at"]
        receipts[request_id] = {"baseline_id": baseline_id, "at": baseline["published_at"]}
        config_v3_catalog._save(profile_id, doc)
        return copy.deepcopy(baseline), _etag(int(draft.get("revision", 0)))


def get_branch(profile_id: str, config_id: str) -> Dict[str, Any]:
    with storage.use_profile(profile_id):
        doc = config_v3_catalog._load(profile_id)
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    branch = _branch(config)
    return {
        "branch_id": "main",
        "head_id": branch.get("head_id"),
        "baselines": [
            {
                "baseline_id": row["baseline_id"],
                "label": row["label"],
                "release_id": row["release_id"],
                "definition_revision": row["definition_revision"],
                "published_at": row["published_at"],
            }
            for row in branch["baselines"].values()
        ],
    }


def get_baseline(profile_id: str, config_id: str, baseline_id: str) -> Dict[str, Any]:
    with storage.use_profile(profile_id):
        doc = config_v3_catalog._load(profile_id)
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    row = _branch(config)["baselines"].get(baseline_id)
    if row is None:
        raise AppError(NOT_FOUND, "基线不存在", baseline_id=baseline_id)
    return copy.deepcopy(row)
