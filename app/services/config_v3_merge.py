"""V3 O/L/N 三方合并适配（M14-01 / INH-2150）。

旧 `core/config_merge.py` 是纯算法；这里只负责把 V3 的真实修订
（D0 / Bn / 机台 rn）转成它的输入，并把成员状态、定义快照、引用 hash
带回结果。不写任何 head，不发布，不产生草稿。

三态：
* 主线跨版本：ours=manifest head（Bn 或 D0），theirs=目标版本 D0；
* 机台同版本：ours/theirs 来自同一 release 的机台修订/D0；
* 机台跨版本：ours=机台 rn，theirs=另一个版本 D0/Bn。
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from ..core import config_merge, config_values, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from . import config_v3_catalog


def _now_hash(values: Dict[str, Any], definition_revision: int) -> str:
    text = json.dumps(
        {"values": values, "definition_revision": definition_revision},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _definitions(config: Dict[str, Any], revision: int) -> Dict[str, Any]:
    for snapshot in (config.get("definition", {}).get("history") or []):
        if int(snapshot.get("revision", -1)) == int(revision):
            return copy.deepcopy(snapshot.get("parameters") or {})
    return {}


def resolve_revision(profile_id: str, config_id: str, revision_id: str) -> Dict[str, Any]:
    """把 d0-/bl-/mr- 解析成精确引用（只读）。"""
    rid = str(revision_id or "").strip()
    if not rid:
        raise AppError(INVALID_INPUT, "必须指定修订", field="revision_id")
    with storage.use_profile(profile_id):
        doc = config_v3_catalog._load(profile_id)
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    release = None
    kind = None
    values: Dict[str, Any] = {}
    definition_revision = 0
    parent_id = None
    machine_id = None
    if rid.startswith("d0-"):
        release_id = rid[3:]
        release = (config.get("releases") or {}).get(release_id)
        if release is None:
            raise AppError(NOT_FOUND, "默认修订不存在", revision_id=rid)
        d0 = release.get("d0") or {}
        values = d0.get("frozen_snapshot") or d0.get("values") or {}
        definition_revision = int(release.get("definition_revision", 0))
        kind = "default"
    else:
        branch = config.get("main_branch") or {}
        baseline = (branch.get("baselines") or {}).get(rid)
        if baseline is not None:
            kind = "baseline"
            release = (config.get("releases") or {}).get(baseline.get("release_id"))
            values = baseline.get("values") or {}
            definition_revision = int(baseline.get("definition_revision", 0))
            parent_id = baseline.get("parent_id")
        else:
            for machine in (config.get("machines") or {}).values():
                row = (machine.get("revisions") or {}).get(rid)
                if row is not None:
                    kind = "machine"
                    machine_id = machine.get("machine_id")
                    release = (config.get("releases") or {}).get(row.get("release_id"))
                    values = row.get("values") or {}
                    definition_revision = int(row.get("definition_revision", 0))
                    parent_id = row.get("parent_id")
                    break
    if kind is None or release is None:
        raise AppError(NOT_FOUND, "修订不存在或不属于该配置", revision_id=rid)
    return {
        "revision_id": rid,
        "kind": kind,
        "release_id": release.get("release_id"),
        "release_version": release.get("version"),
        "definition_revision": definition_revision,
        "parent_id": parent_id,
        "machine_id": machine_id,
        "values": copy.deepcopy(values),
        "content_hash": _now_hash(values, definition_revision),
    }


def _state(row: Dict[str, Any]) -> str:
    membership = str(row.get("membership") or "not_introduced")
    if membership != "included":
        return membership
    if not row.get("enabled", True):
        return "disabled"
    return str(row.get("value_state") or "unset")


def _entries(values: Dict[str, Any], definitions: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    entries: Dict[str, Any] = {}
    states: Dict[str, str] = {}
    for pid, row in values.items():
        state = _state(row)
        states[pid] = state
        if state != "present":
            continue
        definition = definitions.get(pid) or {}
        kind = {
            "number": "decimal", "decimal": "decimal", "int": "int", "integer": "int",
            "float": "decimal", "bool": "bool", "boolean": "bool",
        }.get(str(definition.get("type") or "").lower(), "string")
        try:
            typed = config_values.parse_typed(row.get("value"), kind, definition.get("unit") or None)
        except AppError:
            continue
        entries[pid] = {
            "value": typed.to_json(),
            "intent": row.get("intent") or config_merge.INTENT_INHERIT,
        }
    return entries, states


def _mode(ours: Dict[str, Any], theirs: Dict[str, Any]) -> str:
    if ours["kind"] == "machine":
        return (
            "machine_same_version"
            if ours.get("release_id") == theirs.get("release_id")
            else "machine_cross_version"
        )
    return "main_cross_version"


def merge_revisions(
    profile_id: str,
    config_id: str,
    base_revision_id: Optional[str],
    ours_revision_id: str,
    theirs_revision_id: str,
) -> Dict[str, Any]:
    """读取三个精确修订并跑 O/L/N；结果不可发布。"""
    with storage.use_profile(profile_id):
        doc = config_v3_catalog._load(profile_id)
    config = doc["configs"].get(config_id)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
    ours = resolve_revision(profile_id, config_id, ours_revision_id)
    theirs = resolve_revision(profile_id, config_id, theirs_revision_id)
    base = None
    base_missing = not str(base_revision_id or "").strip()
    if not base_missing:
        base = resolve_revision(profile_id, config_id, base_revision_id)
    base_defs = _definitions(config, base["definition_revision"]) if base else None
    ours_defs = _definitions(config, ours["definition_revision"])
    theirs_defs = _definitions(config, theirs["definition_revision"])
    base_entries, base_states = _entries(base["values"], base_defs or {}) if base else (None, {})
    ours_entries, ours_states = _entries(ours["values"], ours_defs)
    theirs_entries, theirs_states = _entries(theirs["values"], theirs_defs)
    names = {
        pid: (theirs_defs.get(pid) or ours_defs.get(pid) or {}).get("name") or pid
        for pid in set(list(ours_defs) + list(theirs_defs))
    }
    result = config_merge.merge(
        base=base_entries,
        ours=ours_entries,
        theirs=theirs_entries,
        base_definitions=base_defs,
        ours_definitions=ours_defs,
        theirs_definitions=theirs_defs,
        names=names,
    )
    blockers: List[Dict[str, Any]] = list(result.get("blockers") or [])
    for item in result.get("items") or []:
        pid = item["parameter_id"]
        item["base_state"] = base_states.get(pid)
        item["ours_state"] = ours_states.get(pid)
        item["theirs_state"] = theirs_states.get(pid)
        if ours_states.get(pid) in ("removed", "disabled") and item.get("theirs"):
            item["action"] = "manual"
            blockers.append({"code": "local_removed", "parameter_id": pid,
                             "message": "本地已移除/停用，目标仍有值；必须显式确认"})
        if theirs_states.get(pid) in ("removed", "disabled") and item.get("ours"):
            item["action"] = "manual"
            blockers.append({"code": "target_removed", "parameter_id": pid,
                             "message": "目标已移除/停用，本地仍有值；必须显式确认"})
    if base_missing:
        blockers.append({"code": "base_missing", "parameter_id": None,
                         "message": "缺少共同基准，不能一键合并；请先选择真实基准修订"})
    needs_decision = [item["parameter_id"] for item in result["items"] if item.get("action") == "conflict"]
    manual = [item["parameter_id"] for item in result["items"] if item.get("action") == "manual"]
    summary = {action: 0 for action in config_merge.ACTIONS}
    for item in result["items"]:
        summary[item.get("action", "manual")] = summary.get(item.get("action", "manual"), 0) + 1
    return {
        "mode": _mode(ours, theirs),
        "base": _ref(base) if base else None,
        "ours": _ref(ours),
        "theirs": _ref(theirs),
        "has_common_base": base is not None,
        "read_set": [_ref(ours), _ref(theirs)] + ([_ref(base)] if base else []),
        "items": result["items"],
        "summary": summary,
        "blockers": blockers,
        "needs_decision": needs_decision,
        "manual": manual,
        "allowed_decisions": list(config_merge.DECISIONS),
        "publishable": False,
        "can_apply": not blockers and not needs_decision,
    }


def _ref(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "revision_id": row["revision_id"],
        "kind": row["kind"],
        "release_id": row.get("release_id"),
        "release_version": row.get("release_version"),
        "definition_revision": row.get("definition_revision"),
        "parent_id": row.get("parent_id"),
        "machine_id": row.get("machine_id"),
        "content_hash": row.get("content_hash"),
    }
