"""V3 合并计划与提交（M14-02 / INH-2151）。

计划只读；提交只创建**新草稿**（基线草稿或机台草稿），不移动 head，
不发布，不触碰其他机台。计划 read_set 记录 O/L/N 的引用 hash；来源变化后
决议保留但必须重算才能再次提交。
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core import config_values, locks, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, PLAN_STALE, AppError
from . import config_v3_baselines, config_v3_catalog, config_v3_machines, config_v3_merge

_LOCK = threading.RLock()
_PLANS: Dict[str, Dict[str, Any]] = {}
_MAX_PLANS = 20

STATUS_OPEN = "open"
STATUS_STALE = "stale"

DECISION_KEEP = "keep_local"
DECISION_ADOPT = "adopt_target"
DECISION_CUSTOM = "custom"
DECISION_DEACTIVATE = "deactivate"
DECISION_REMOVE = "remove"
DECISION_PIN = "pin"
DECISION_PENDING = "pending"
DECISIONS = (DECISION_KEEP, DECISION_ADOPT, DECISION_CUSTOM, DECISION_DEACTIVATE,
             DECISION_REMOVE, DECISION_PIN)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _default_decision(item: Dict[str, Any]) -> str:
    action = item.get("action")
    if action == "keep":
        return DECISION_KEEP
    if action == "from_new":
        return DECISION_ADOPT
    if action == "drop":
        return DECISION_REMOVE
    return DECISION_PENDING


def _allowed(item: Dict[str, Any]) -> List[str]:
    if item.get("action") == "keep":
        return [DECISION_KEEP]
    if item.get("action") == "from_new":
        return [DECISION_ADOPT, DECISION_KEEP]
    if item.get("action") == "drop":
        return [DECISION_REMOVE, DECISION_KEEP]
    if item.get("action") == "manual":
        return [DECISION_CUSTOM, DECISION_KEEP, DECISION_REMOVE]
    raw = item.get("allowed") or []
    out = []
    mapping = {
        "use_new": DECISION_ADOPT, "inherit_old": DECISION_KEEP,
        "manual": DECISION_CUSTOM, "pin": DECISION_PIN, "clear_pin": DECISION_KEEP,
    }
    for name in raw:
        mapped = mapping.get(name)
        if mapped and mapped not in out:
            out.append(mapped)
    if DECISION_CUSTOM not in out:
        out.append(DECISION_CUSTOM)
    return out


def _summary(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "plan_id": plan["plan_id"],
        "config_id": plan["config_id"],
        "status": plan["status"],
        "stale": plan["status"] == STATUS_STALE,
        "mode": plan["merge"]["mode"],
        "base": copy.deepcopy(plan["merge"]["base"]),
        "ours": copy.deepcopy(plan["merge"]["ours"]),
        "theirs": copy.deepcopy(plan["merge"]["theirs"]),
        "read_set": copy.deepcopy(plan["read_set"]),
        "counts": _counts(plan),
        "pending": sum(1 for item in plan["rows"] if _effective(plan, item) == DECISION_PENDING),
        "created_at": plan["created_at"],
    }


def _counts(plan: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in plan["rows"]:
        counts[item["action"]] = counts.get(item["action"], 0) + 1
    counts["total"] = len(plan["rows"])
    counts["conflict"] = counts.get("conflict", 0) + counts.get("manual", 0)
    return counts


def _effective(plan: Dict[str, Any], item: Dict[str, Any]) -> str:
    return plan["decisions"].get(item["parameter_id"]) or item["default_decision"]


def _refresh(plan: Dict[str, Any], force: bool = False) -> None:
    now = time.monotonic()
    if not force and now - float(plan.get("_checked_at") or 0.0) < 2.0:
        return
    plan["_checked_at"] = now
    try:
        for ref in plan["read_set"]:
            current = config_v3_merge.resolve_revision(plan["profile_id"], plan["config_id"], ref["revision_id"])
            if current["content_hash"] != ref["content_hash"]:
                plan["status"] = STATUS_STALE
                return
    except AppError:
        plan["status"] = STATUS_STALE
        return
    plan["status"] = STATUS_OPEN


def _get(wid: str, pid: str, cid: str, plan_id: str) -> Dict[str, Any]:
    with _LOCK:
        plan = _PLANS.get(str(plan_id or ""))
        if (plan is None or plan["workspace_id"] != wid or plan["profile_id"] != pid
                or plan["config_id"] != cid):
            raise AppError(NOT_FOUND, "合并计划不存在或已过期", plan_id=plan_id)
        return plan


def create(wid: str, pid: str, cid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    result = config_v3_merge.merge_revisions(
        pid, cid, payload.get("base_revision_id"), payload.get("ours_revision_id"),
        payload.get("theirs_revision_id"),
    )
    rows = []
    for item in result["items"]:
        row = copy.deepcopy(item)
        row["default_decision"] = _default_decision(item)
        row["allowed_decisions"] = _allowed(item)
        rows.append(row)
    plan = {
        "plan_id": "mp-" + uuid.uuid4().hex[:16],
        "workspace_id": wid,
        "profile_id": pid,
        "config_id": cid,
        "merge": {
            "mode": result["mode"], "base": result["base"], "ours": result["ours"],
            "theirs": result["theirs"],
        },
        "read_set": copy.deepcopy(result["read_set"]),
        "rows": rows,
        "decisions": {},
        "status": STATUS_OPEN,
        "created_at": _now(),
        "_checked_at": 0.0,
    }
    with _LOCK:
        _PLANS[plan["plan_id"]] = plan
        scoped = [p for p in _PLANS.values() if p["workspace_id"] == wid and p["profile_id"] == pid]
        scoped.sort(key=lambda p: p["created_at"])
        while len(scoped) > _MAX_PLANS:
            _PLANS.pop(scoped.pop(0)["plan_id"], None)
    return _summary(plan)


def get(wid: str, pid: str, cid: str, plan_id: str) -> Dict[str, Any]:
    plan = _get(wid, pid, cid, plan_id)
    with _LOCK:
        _refresh(plan, force=True)
        return _summary(plan)


def rows(
    wid: str, pid: str, cid: str, plan_id: str,
    offset: int = 0, limit: int = 50, filter_name: str = "all", q: str = "",
) -> Dict[str, Any]:
    plan = _get(wid, pid, cid, plan_id)
    with _LOCK:
        _refresh(plan, force=True)
        selected = plan["rows"]
        if filter_name == "conflict":
            selected = [row for row in selected if row["action"] in ("conflict", "manual")]
        elif filter_name == "resolved":
            selected = [row for row in selected if _effective(plan, row) != DECISION_PENDING]
        keyword = str(q or "").strip().lower()
        if keyword:
            selected = [
                row for row in selected
                if keyword in str(row.get("name") or "").lower()
                or keyword in str(row["parameter_id"]).lower()
            ]
        start = max(0, int(offset))
        size = max(1, min(int(limit), 200))
        out = _summary(plan)
        out.update({
            "total": len(selected), "offset": start, "limit": size, "filter": filter_name,
            "items": [
                {
                    **copy.deepcopy(row),
                    "decision": _effective(plan, row),
                }
                for row in selected[start : start + size]
            ],
        })
        return out


def decisions(wid: str, pid: str, cid: str, plan_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    plan = _get(wid, pid, cid, plan_id)
    with _LOCK:
        _refresh(plan, force=True)
        if plan["status"] == STATUS_STALE:
            raise AppError(PLAN_STALE, "合并计划已过期，请重算", plan_id=plan_id)
        scope = str(payload.get("scope") or "rows")
        decision = str(payload.get("decision") or "").strip()
        if scope == "rows":
            wanted = {str(value) for value in (payload.get("row_ids") or [])}
            targets = [row for row in plan["rows"] if row["parameter_id"] in wanted]
            if len(targets) != len(wanted):
                raise AppError(NOT_FOUND, "计划中不存在这些行", field="row_ids")
        elif scope == "filter":
            filter_name = str(payload.get("filter") or "all")
            targets = plan["rows"]
            if filter_name == "conflict":
                targets = [row for row in targets if row["action"] in ("conflict", "manual")]
        else:
            raise AppError(INVALID_INPUT, "未知的决议范围", field="scope")
        updated = 0
        for row in targets:
            if decision and decision not in row["allowed_decisions"] and decision != DECISION_PENDING:
                raise AppError(INVALID_INPUT, "该行不允许这个决议", field="decision",
                               parameter_id=row["parameter_id"])
            if decision == DECISION_PENDING:
                plan["decisions"].pop(row["parameter_id"], None)
            elif decision:
                plan["decisions"][row["parameter_id"]] = decision
            updated += 1
        out = _summary(plan)
        out["updated"] = updated
        return out


def commit(wid: str, pid: str, cid: str, plan_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    plan = _get(wid, pid, cid, plan_id)
    with _LOCK:
        _refresh(plan, force=True)
        if plan["status"] == STATUS_STALE:
            raise AppError(PLAN_STALE, "合并计划已过期，请重算", plan_id=plan_id)
        pending = [row["parameter_id"] for row in plan["rows"] if _effective(plan, row) == DECISION_PENDING]
        if pending:
            raise AppError(PLAN_STALE, "仍有未决议的冲突行", fields={"parameter_ids": pending})
        ours_ref = plan["merge"]["ours"]
        theirs_ref = plan["merge"]["theirs"]
        ours = config_v3_merge.resolve_revision(pid, cid, ours_ref["revision_id"])
        theirs = config_v3_merge.resolve_revision(pid, cid, theirs_ref["revision_id"])
        values = copy.deepcopy(ours["values"])
        for row in plan["rows"]:
            decision = _effective(plan, row)
            if decision == DECISION_KEEP:
                continue
            if decision == DECISION_PIN:
                if row["parameter_id"] in values:
                    values[row["parameter_id"]]["intent"] = "pin"
                continue
            if decision == DECISION_REMOVE:
                values[row["parameter_id"]] = {
                    "membership": "removed", "enabled": True, "value_state": "unset", "value": None,
                }
                continue
            if decision == DECISION_DEACTIVATE:
                row_value = values.setdefault(
                    row["parameter_id"],
                    {"membership": "included", "enabled": True, "value_state": "unset", "value": None},
                )
                row_value["enabled"] = False
                continue
            if decision == DECISION_CUSTOM:
                raw = payload.get("custom_values", {}).get(row["parameter_id"])
                if raw is None:
                    raise AppError(INVALID_INPUT, "custom 决议必须给值", field="custom_values",
                                   parameter_id=row["parameter_id"])
                values[row["parameter_id"]] = {
                    "membership": "included", "enabled": True, "value_state": "present", "value": raw,
                }
                continue
            if decision == DECISION_ADOPT:
                if row.get("theirs_state") == "removed":
                    values[row["parameter_id"]] = {
                        "membership": "removed", "enabled": True, "value_state": "unset", "value": None,
                    }
                elif row.get("theirs_state") == "disabled":
                    row_value = values.setdefault(row["parameter_id"], {})
                    row_value.update({"membership": "included", "enabled": False})
                elif row.get("theirs"):
                    typed = row["theirs"].get("value") or {}
                    value = typed.get("text")
                    state = "null" if typed.get("type") == "null" else "present"
                    values[row["parameter_id"]] = {
                        "membership": "included", "enabled": True,
                        "value_state": state, "value": None if state == "null" else value,
                        "intent": row["theirs"].get("intent") or "inherit",
                    }
        draft = _write_draft(pid, cid, ours, theirs, values)
        out = _summary(plan)
        out["draft"] = draft
        return out


def _write_draft(
    pid: str, cid: str, ours: Dict[str, Any], theirs: Dict[str, Any], values: Dict[str, Any]
) -> Dict[str, Any]:
    with locks.write_gate.write(), storage.use_profile(pid), storage.profile_write(pid):
        doc = config_v3_catalog._load(pid)
        config = doc["configs"].get(cid)
        if config is None:
            raise AppError(NOT_FOUND, "配置不存在", config_id=cid)
        if ours["kind"] == "machine":
            machine = (config.get("machines") or {}).get(ours.get("machine_id"))
            if machine is None:
                raise AppError(NOT_FOUND, "机台不存在", machine_id=ours.get("machine_id"))
            existing = machine.get("draft")
            if isinstance(existing, dict) and existing.get("status") == "draft":
                raise AppError(PLAN_STALE, "机台已有未发布草稿", draft_id=existing.get("draft_id"))
            release = (config.get("releases") or {}).get(theirs.get("release_id"))
            if release is None:
                raise AppError(NOT_FOUND, "目标软件版本不存在")
            config_v3_machines._validated(release, config, values)
            machine["draft"] = {
                "draft_id": "md-" + uuid.uuid4().hex[:12],
                "status": "draft", "revision": 0, "values": copy.deepcopy(values),
                "base_kind": ours["kind"], "base_revision_id": ours["revision_id"],
                "definition_revision": int(release.get("definition_revision", 0)),
                "created_at": _now(),
            }
            machine["release_id"] = release["release_id"]
            draft = copy.deepcopy(machine["draft"])
        else:
            release = (config.get("releases") or {}).get(theirs.get("release_id"))
            if release is None:
                raise AppError(NOT_FOUND, "目标软件版本不存在")
            if not release.get("default_revision_id"):
                raise AppError(INVALID_INPUT, "目标版本 D0 尚未冻结，不能创建合并草稿")
            existing = release.get("baseline_draft")
            if isinstance(existing, dict) and existing.get("status") == "draft":
                raise AppError(PLAN_STALE, "该版本已有未发布草稿", draft_id=existing.get("draft_id"))
            config_v3_baselines._validated_values(release, config, values)
            draft = {
                "draft_id": "bd-" + uuid.uuid4().hex[:12],
                "release_id": release["release_id"], "status": "draft", "revision": 0,
                "values": copy.deepcopy(values),
                "source_revision_id": ours["revision_id"],
                "definition_revision": int(release.get("definition_revision", 0)),
                "created_at": _now(), "receipts": {},
            }
            release["baseline_draft"] = draft
        config["updated_at"] = _now()
        config_v3_catalog._save(pid, doc)
        return {
            "draft_id": draft["draft_id"],
            "release_id": draft.get("release_id"),
            "kind": "machine" if ours["kind"] == "machine" else "baseline",
            "revision": draft.get("revision", 0),
            "values": copy.deepcopy(draft.get("values") or {}),
        }
