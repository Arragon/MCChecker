"""V3 参数值导入（M14-03 / INH-2152）。

名称层级提取与数值读取是两件事：这里只处理**值**，匹配只认已确认绑定，
相似名称不自动绑定。D0 未冻结时更新 canonical 草稿；已冻结时生成新草稿。
未出现在文件里的字段保持原值，绝不推断 remove。
"""

from __future__ import annotations

import copy
import hashlib
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core import config_mapping, locks, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, PLAN_STALE, SOURCE_CHANGED, AppError
from . import (
    config_v3_catalog,
    config_v3_extract,
    config_v3_parse,
    config_v3_provenance,
    documents,
    file_versions,
)

_LOCK = threading.RLock()
_PLANS: Dict[str, Dict[str, Any]] = {}
MAX_PLANS = 20


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _value_kind(row: Dict[str, Any]) -> str:
    value = row.get("value")
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "decimal"
    return "string"


def _binding_index(release: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for binding in ((release.get("binding_set") or {}).get("bindings") or {}).values():
        locator = str(binding.get("locator") or "")
        if locator:
            out[locator] = binding
    return out


def _definition_map(config: Dict[str, Any], release: Dict[str, Any]) -> Dict[str, Any]:
    from . import config_v3_drafts
    return (config_v3_drafts._definition_for(release, config).get("parameters") or {})


def create(wid: str, pid: str, cid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    version_id = str(payload.get("version_id") or "").strip()
    release_id = str(payload.get("release_id") or "").strip()
    if not version_id or not release_id:
        raise AppError(INVALID_INPUT, "version_id 与 release_id 必填", field="version_id")
    with storage.use_profile(pid):
        identified = file_versions.find_version(pid, version_id)
        if identified is None:
            raise AppError(NOT_FOUND, "文件版本不存在", version_id=version_id)
        name = identified["name"]
        source_format = config_v3_extract.format_of(name)
        if source_format not in ("json", "xml"):
            raise AppError(INVALID_INPUT, "只有 JSON/XML 支持值导入", field="version_id")
        ref = config_v3_provenance.make_ref(pid, name, identified["ref"])
        verified = config_v3_provenance.assert_usable(ref)
        raw = documents.load_raw(pid, name, identified["ref"])
        if raw.sha256 != verified["blob_sha256"]:
            raise AppError(SOURCE_CHANGED, "来源文件内容已变化", field="version_id")
        rows = config_v3_parse.xml_locators(raw.data) if source_format == "xml" \
            else config_v3_parse.json_locators(raw.data)
        doc = config_v3_catalog._load(pid)
    config = doc["configs"].get(cid)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=cid)
    release = (config.get("releases") or {}).get(release_id)
    if release is None:
        raise AppError(NOT_FOUND, "软件版本不存在", release_id=release_id)
    bindings = _binding_index(release)
    definitions = _definition_map(config, release)
    by_name_dir = {}
    for param_id, definition in definitions.items():
        by_name_dir[(definition.get("name"), tuple(definition.get("directory") or []))] = param_id
    items = []
    for row in rows:
        locator = row.get("locator") or {}
        if str(locator.get("kind") or "") in ("attribute", "element_text"):
            continue
        canonical = config_mapping.canonical_locator(locator)
        leaf, directory, _kind = config_mapping.locator_leaf(locator)
        binding = bindings.get(canonical) or bindings.get(row.get("display_path") or "")
        if binding and binding.get("file_id") and binding.get("file_id") != identified["file_id"]:
            binding = None
        parameter_id = binding.get("parameter_id") if binding else None
        candidates = []
        if not parameter_id:
            candidate = by_name_dir.get((leaf, tuple(directory)))
            if candidate:
                candidates.append({"parameter_id": candidate, "name": leaf, "directory": directory})
        items.append({
            "row_id": "vi-" + hashlib.sha256(canonical.encode()).hexdigest()[:16],
            "parameter_id": parameter_id,
            "leaf_name": leaf,
            "directory": directory,
            "canonical_locator": canonical,
            "display_path": row.get("display_path") or "",
            "value": row.get("value"),
            "value_kind": _value_kind(row),
            "status": "bound" if parameter_id else "unbound",
            "candidates": candidates,
            "decision": "import" if parameter_id else "skip",
        })
    plan = {
        "plan_id": "vp-" + uuid.uuid4().hex[:16],
        "workspace_id": wid,
        "profile_id": pid,
        "config_id": cid,
        "release_id": release_id,
        "source": {
            "name": name, "file_id": identified["file_id"], "version_id": identified["version_id"],
            "blob_sha256": identified["blob_sha256"], "version_label": identified.get("version_label"),
            "format": source_format,
        },
        "items": items,
        "decisions": {},
        "created_at": _now(),
    }
    with _LOCK:
        _PLANS[plan["plan_id"]] = plan
        scoped = [p for p in _PLANS.values() if p["workspace_id"] == wid and p["profile_id"] == pid]
        scoped.sort(key=lambda p: p["created_at"])
        while len(scoped) > MAX_PLANS:
            _PLANS.pop(scoped.pop(0)["plan_id"], None)
    return _public(plan)


def _public(plan: Dict[str, Any], items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "plan_id": plan["plan_id"],
        "config_id": plan["config_id"],
        "release_id": plan["release_id"],
        "source": copy.deepcopy(plan["source"]),
        "total": len(plan["items"]),
        "bound": sum(1 for row in plan["items"] if row["status"] == "bound"),
        "unbound": sum(1 for row in plan["items"] if row["status"] == "unbound"),
        "items": copy.deepcopy(items if items is not None else plan["items"]),
        "created_at": plan["created_at"],
    }


def get(wid: str, pid: str, cid: str, plan_id: str) -> Dict[str, Any]:
    plan = _get(wid, pid, cid, plan_id)
    items = []
    for row in plan["items"]:
        saved = plan["decisions"].get(row["row_id"]) or {}
        items.append({**copy.deepcopy(row), "decision": saved.get("decision") or row["decision"],
                      "parameter_id": saved.get("parameter_id") or row["parameter_id"]})
    return _public(plan, items)


def _get(wid: str, pid: str, cid: str, plan_id: str) -> Dict[str, Any]:
    with _LOCK:
        plan = _PLANS.get(str(plan_id or ""))
        if (plan is None or plan["workspace_id"] != wid or plan["profile_id"] != pid
                or plan["config_id"] != cid):
            raise AppError(NOT_FOUND, "值导入计划不存在", plan_id=plan_id)
        return plan


def decisions(wid: str, pid: str, cid: str, plan_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    plan = _get(wid, pid, cid, plan_id)
    with _LOCK:
        scope = str(payload.get("scope") or "rows")
        decision = str(payload.get("decision") or "")
        if decision not in ("import", "skip", "link", "create"):
            raise AppError(INVALID_INPUT, "未知决议", field="decision")
        if scope == "rows":
            wanted = {str(value) for value in (payload.get("row_ids") or [])}
            targets = [row for row in plan["items"] if row["row_id"] in wanted]
        elif scope == "filter":
            status = str(payload.get("filter") or "all")
            targets = [row for row in plan["items"] if status == "all" or row["status"] == status]
        else:
            raise AppError(INVALID_INPUT, "未知决议范围", field="scope")
        for row in targets:
            if decision == "link":
                target = str(payload.get("parameter_id") or "")
                if not target or target not in {c["parameter_id"] for c in row.get("candidates") or []}:
                    raise AppError(INVALID_INPUT, "关联目标不是候选", field="parameter_id", row_id=row["row_id"])
                plan["decisions"][row["row_id"]] = {"decision": "link", "parameter_id": target}
            else:
                plan["decisions"][row["row_id"]] = {"decision": decision}
        out = _public(plan)
        out["updated"] = len(targets)
        return out


def commit(wid: str, pid: str, cid: str, plan_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    plan = _get(wid, pid, cid, plan_id)
    with _LOCK:
        with storage.use_profile(pid):
            state = config_v3_extract.current_state(pid, cid, plan["source"])
        if (state["missing"] or state["version_id"] != plan["source"]["version_id"]
                or state["blob_sha256"] != plan["source"]["blob_sha256"]):
            raise AppError(SOURCE_CHANGED, "来源文件当前版本已变化", plan_id=plan_id)
        by_row = {}
        for row in plan["items"]:
            saved = plan["decisions"].get(row["row_id"]) or {}
            decision = saved.get("decision") or row["decision"]
            if decision == "skip":
                continue
            parameter_id = row["parameter_id"] or saved.get("parameter_id")
            if decision == "create":
                parameter_id = None
            by_row[row["row_id"]] = (row, decision, parameter_id)
        with locks.write_gate.write(), storage.use_profile(pid), storage.profile_write(pid):
            doc = config_v3_catalog._load(pid)
            config = doc["configs"].get(cid)
            release = (config.get("releases") or {}).get(plan["release_id"])
            if release is None:
                raise AppError(NOT_FOUND, "软件版本不存在")
            definitions = _definition_map(config, release)
            applied = 0
            created = 0
            for row, decision, parameter_id in by_row.values():
                if decision == "create":
                    definition = {
                        "name": row["leaf_name"], "directory": row["directory"], "type": "",
                        "description": "", "unit": "", "attrs": {}, "source": {},
                    }
                    parameter_id = _create_parameter(config, definition)
                    created += 1
                if parameter_id not in definitions and decision != "create":
                    continue
                target = row
                if not _apply_value(release, config, pid, parameter_id, target):
                    continue
                applied += 1
            if not applied and not created:
                raise AppError(INVALID_INPUT, "没有可导入的值", field="items")
            if created:
                _snapshot(config)
            config_v3_catalog._save(pid, doc)
            return {
                "plan_id": plan_id,
                "applied": applied,
                "created": created,
                "release_id": plan["release_id"],
                "d0_frozen": bool((release.get("d0") or {}).get("frozen")),
            }


def _create_parameter(config: Dict[str, Any], definition: Dict[str, Any]) -> str:
    parameters = config_v3_catalog._definition(config)["current"].setdefault("parameters", {})
    if any(p.get("name") == definition["name"] and p.get("directory") == definition["directory"]
           for p in parameters.values()):
        raise AppError(INVALID_INPUT, "同一目录下已存在同名参数", field="name")
    pid = "p-" + uuid.uuid4().hex[:12]
    parameters[pid] = {
        "parameter_id": pid, "name": definition["name"], "description": "",
        "directory": list(definition["directory"]), "type": "", "unit": "", "attrs": {},
        "source": {}, "created_at": _now(),
    }
    return pid


def _apply_value(release: Dict[str, Any], config: Dict[str, Any], pid: str,
                 parameter_id: Optional[str], row: Dict[str, Any]) -> bool:
    d0 = release.setdefault("d0", {"values": {}, "frozen": False, "revision": 0})
    if not d0.get("frozen"):
        values = d0.setdefault("values", {})
        values[parameter_id] = {
            "membership": "included", "enabled": True,
            "value_state": "null" if row["value_kind"] == "null" else "present",
            "value": None if row["value_kind"] == "null" else row["value"],
        }
        d0["revision"] = int(d0.get("revision", 0)) + 1
        return True
    draft = release.get("baseline_draft")
    if not isinstance(draft, dict) or draft.get("status") != "draft":
        source = config.get("main_branch", {}).get("head_id")
        base_values = {}
        branch = config.get("main_branch") or {}
        if source and source in (branch.get("baselines") or {}):
            base_values = copy.deepcopy(branch["baselines"][source].get("values") or {})
        else:
            base_values = copy.deepcopy(d0.get("frozen_snapshot") or d0.get("values") or {})
        draft = {
            "draft_id": "bd-" + uuid.uuid4().hex[:12], "release_id": release["release_id"],
            "status": "draft", "revision": 0, "values": base_values,
            "source_revision_id": source or release.get("default_revision_id"),
            "definition_revision": int(release.get("definition_revision", 0)),
            "created_at": _now(), "receipts": {},
        }
        release["baseline_draft"] = draft
    draft.setdefault("values", {})[parameter_id] = {
        "membership": "included", "enabled": True,
        "value_state": "null" if row["value_kind"] == "null" else "present",
        "value": None if row["value_kind"] == "null" else row["value"],
    }
    draft["revision"] = int(draft.get("revision", 0)) + 1
    return True


def _snapshot(config: Dict[str, Any]) -> None:
    from . import config_v3_catalog as catalog
    catalog._snapshot_definition(config)
