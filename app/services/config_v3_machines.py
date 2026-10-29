"""V3 机台实例与 r1..rn 连续分支（M12-05 / INH-2140）。"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..core import config_values, locks, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError
from . import config_v3_catalog, config_v3_drafts


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _machines(config: Dict[str, Any]) -> Dict[str, Any]:
    return config.setdefault("machines", {})


def _validated(release: Dict[str, Any], config: Dict[str, Any], values: Dict[str, Any]) -> None:
    params = config_v3_drafts._definition_for(release, config).get("parameters") or {}
    for pid, row in (values or {}).items():
        if pid not in params:
            raise AppError(NOT_FOUND, "草稿值引用了不存在的参数", parameter_id=pid)
        if row.get("membership") == "removed" or not row.get("enabled", True):
            continue
        if row.get("value_state") == "present":
            kind = {
                "number": "decimal", "decimal": "decimal",
                "int": "int", "integer": "int", "float": "decimal",
                "bool": "bool", "boolean": "bool",
            }.get(str(params[pid].get("type") or "").lower(), "string")
            config_values.parse_typed(
                row.get("value"), kind, params[pid].get("unit") or None
            ).validate()


def _base(config: Dict[str, Any], release: Dict[str, Any], kind: str, revision_id: str):
    if kind == "default":
        if not release.get("default_revision_id"):
            raise AppError(INVALID_INPUT, "canonical D0 尚未冻结，不能建机台分支")
        return release["d0"].get("frozen_snapshot") or {}
    if kind == "baseline":
        branch = config.setdefault("main_branch", {"head_id": None, "next_seq": 1, "baselines": {}})
        row = (branch.get("baselines") or {}).get(revision_id)
        if row is None:
            raise AppError(NOT_FOUND, "基线不存在", baseline_id=revision_id)
        return row.get("values") or {}
    raise AppError(INVALID_INPUT, "base_kind 只能是 default/baseline", field="base_kind")


def _public(machine: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "machine_id": machine["machine_id"],
        "machine_no": machine["machine_no"],
        "head_id": machine.get("head_id"),
        "published": bool(machine.get("head_id")),
        "draft": copy.deepcopy(machine.get("draft")),
        "revisions": [
            {
                "revision_id": row["revision_id"],
                "label": row["label"],
                "parent_id": row.get("parent_id"),
                "base_kind": row.get("base_kind"),
                "published_at": row["published_at"],
            }
            for row in (machine.get("revisions") or {}).values()
        ],
        "management_only": True,
    }


def list_machines(profile_id: str, config_id: str) -> List[Dict[str, Any]]:
    with storage.use_profile(profile_id):
        doc = config_v3_catalog._load(profile_id)
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    return [_public(row) for row in _machines(config).values()]


def _find(config: Dict[str, Any], machine_id: str) -> Dict[str, Any]:
    row = _machines(config).get(machine_id)
    if row is None:
        raise AppError(NOT_FOUND, "机台不存在", machine_id=machine_id)
    return row


def create_machine(
    profile_id: str, config_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    machine_no = str(payload.get("machine_no") or "").strip()
    if not machine_no or len(machine_no) > 100:
        raise AppError(INVALID_INPUT, "机台编号需为 1-100 个字符", field="machine_no")
    kind = str(payload.get("base_kind") or "default")
    base_revision_id = str(payload.get("base_revision_id") or "").strip()
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = config_v3_catalog._load(profile_id)
        config = doc["configs"].get(config_id)
        if config is None:
            raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
        release = (config.get("releases") or {}).get(payload.get("release_id"))
        if release is None:
            raise AppError(INVALID_INPUT, "必须指定 release_id", field="release_id")
        machines = _machines(config)
        if any(row.get("machine_no") == machine_no for row in machines.values()):
            raise AppError(INVALID_INPUT, "同一配置内机台编号已存在", field="machine_no")
        values = _base(config, release, kind, base_revision_id)
        mid = "m-" + uuid.uuid4().hex[:12]
        machine = {
            "machine_id": mid,
            "machine_no": machine_no,
            "release_id": release["release_id"],
            "head_id": None,
            "next_rev": 1,
            "revisions": {},
            "draft": {
                "draft_id": "md-" + uuid.uuid4().hex[:12],
                "status": "draft",
                "revision": 0,
                "values": copy.deepcopy(values),
                "base_kind": kind,
                "base_revision_id": base_revision_id or release.get("default_revision_id"),
                "created_at": _now(),
            },
        }
        machines[mid] = machine
        config_v3_catalog._save(profile_id, doc)
        return _public(machine)


def new_draft(profile_id: str, config_id: str, machine_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(payload.get("base_kind") or "default")
    base_revision_id = str(payload.get("base_revision_id") or "").strip()
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = config_v3_catalog._load(profile_id)
        config = doc["configs"].get(config_id)
        if config is None:
            raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
        machine = _find(config, machine_id)
        release = (config.get("releases") or {}).get(payload.get("release_id") or machine["release_id"])
        if release is None:
            raise AppError(INVALID_INPUT, "必须指定 release_id", field="release_id")
        existing = machine.get("draft")
        if isinstance(existing, dict) and existing.get("status") == "draft":
            raise AppError(REVISION_CONFLICT, "机台已有未发布草稿", draft_id=existing.get("draft_id"))
        values = _base(config, release, kind, base_revision_id)
        machine["draft"] = {
            "draft_id": "md-" + uuid.uuid4().hex[:12],
            "status": "draft",
            "revision": 0,
            "values": copy.deepcopy(values),
            "base_kind": kind,
            "base_revision_id": base_revision_id or release.get("default_revision_id"),
            "created_at": _now(),
        }
        machine["release_id"] = release["release_id"]
        config_v3_catalog._save(profile_id, doc)
        return _public(machine)


def publish_machine(
    profile_id: str,
    config_id: str,
    machine_id: str,
    payload: Dict[str, Any],
    if_match: Optional[str],
) -> Tuple[Dict[str, Any], int]:
    raw = if_match if if_match is not None and str(if_match).strip() else payload.get("expected_revision")
    expected_raw = str(raw if raw is not None else "").strip().strip('"')
    try:
        expected = int(expected_raw)
    except ValueError:
        raise AppError(INVALID_INPUT, "缺少合法 If-Match", field="expected_revision")
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = config_v3_catalog._load(profile_id)
        config = doc["configs"].get(config_id)
        if config is None:
            raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
        machine = _find(config, machine_id)
        draft = machine.get("draft")
        if not isinstance(draft, dict) or draft.get("status") != "draft":
            raise AppError(REVISION_CONFLICT, "机台没有可发布的草稿", machine_id=machine_id)
        if int(draft.get("revision", 0)) != expected:
            raise AppError(REVISION_CONFLICT, "草稿已被其他编辑者更新", current=int(draft.get("revision", 0)))
        release = (config.get("releases") or {}).get(machine["release_id"])
        if release is None:
            raise AppError(NOT_FOUND, "软件版本不存在", release_id=machine["release_id"])
        _validated(release, config, draft.get("values") or {})
        seq = int(machine.get("next_rev", 1))
        rid = "mr-" + uuid.uuid4().hex[:12]
        machine["revisions"][rid] = {
            "revision_id": rid,
            "label": "r%d" % seq,
            "parent_id": machine.get("head_id"),
            "release_id": machine["release_id"],
            "definition_revision": int(release.get("definition_revision", 0)),
            "base_kind": draft.get("base_kind"),
            "base_revision_id": draft.get("base_revision_id"),
            "values": copy.deepcopy(draft.get("values") or {}),
            "published_at": _now(),
            "draft_id": draft.get("draft_id"),
        }
        machine["head_id"] = rid
        machine["next_rev"] = seq + 1
        draft["status"] = "published"
        config_v3_catalog._save(profile_id, doc)
        return _public(machine), seq


def get_machine(profile_id: str, config_id: str, machine_id: str) -> Dict[str, Any]:
    with storage.use_profile(profile_id):
        doc = config_v3_catalog._load(profile_id)
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    return _public(_find(config, machine_id))
