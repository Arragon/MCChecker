"""V3 提取计划（M13-05 / INH-2147）。

服务端保存**全量计划**，浏览器只按页取行；决议（新增/关联/跳过）也保存在
计划上。计划只影响参数定义，不读取/写入任何参数值。

* 计划带 `filter_digest`：批量决议必须在同一筛选快照上执行，筛选或来源变化
  先重算，避免把决议应用到错误的行集合。
* 来源/目录修订变化时计划标为 `stale`，旧行与旧决议保留；`recompute` 按稳定
  `row_id` 合并仍合法的决议，不静默丢弃。
* 计划存放在内存：服务重启后需重新预览；参数与文件不受影响。
"""

from __future__ import annotations

import copy
import hashlib
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.errors import INVALID_INPUT, NOT_FOUND, PLAN_STALE, AppError
from . import config_v3_extract

_LOCK = threading.RLock()
_PLANS: Dict[str, Dict[str, Any]] = {}
_MAX_PLANS_PER_SCOPE = 20

STATUS_OPEN = "open"
STATUS_STALE = "stale"

ACTION_ADD = config_v3_extract.ACTION_ADD
ACTION_LINK = config_v3_extract.ACTION_LINK
ACTION_SKIP = config_v3_extract.ACTION_SKIP
ACTIONS = (ACTION_ADD, ACTION_LINK, ACTION_SKIP)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _default_decision(item: Dict[str, Any]) -> Dict[str, Any]:
    status = item["status"]
    if status == config_v3_extract.STATUS_NEW:
        return {"action": ACTION_ADD, "type": item.get("suggested_type") or "",
                "parameter_id": "", "source": "default"}
    if status == config_v3_extract.STATUS_EXISTING:
        return {"action": ACTION_LINK, "type": item.get("suggested_type") or "",
                "parameter_id": item.get("existing_parameter_id") or "", "source": "default"}
    if status == config_v3_extract.STATUS_UNSUPPORTED:
        return {"action": ACTION_SKIP, "type": "", "parameter_id": "", "source": "default"}
    return {"action": "pending", "type": item.get("suggested_type") or "",
            "parameter_id": item.get("existing_parameter_id") or "", "source": "default"}


def effective_decision(plan: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    saved = plan["decisions"].get(item["row_id"])
    if saved is None:
        return _default_decision(item)
    out = dict(saved)
    out["source"] = "user"
    return out


def _decision_ready(item: Dict[str, Any], decision: Dict[str, Any]) -> bool:
    action = str(decision.get("action") or "")
    if action in ("pending", ""):
        return False
    if action not in ACTIONS:
        return False
    if item["status"] == config_v3_extract.STATUS_UNSUPPORTED:
        return action == ACTION_SKIP
    if action == ACTION_LINK:
        return bool(decision.get("parameter_id"))
    if action == ACTION_ADD and item["status"] == config_v3_extract.STATUS_NULL_UNCERTAIN:
        return str(decision.get("type") or "") in config_v3_extract.TYPE_OPTIONS
    return True


def _pending(plan: Dict[str, Any]) -> int:
    return sum(
        1
        for item in plan["rows"]
        if item["status"] in config_v3_extract.CONFLICT_STATUSES
        and not _decision_ready(item, effective_decision(plan, item))
    )


def _filtered(plan: Dict[str, Any], filter_name: str, q: str) -> List[Dict[str, Any]]:
    rows = config_v3_extract.filter_items(plan["rows"], filter_name)
    keyword = str(q or "").strip().lower()
    if keyword:
        rows = [
            row
            for row in rows
            if keyword in str(row.get("leaf_name") or "").lower()
            or keyword in str(row.get("display_path") or "").lower()
            or keyword in "/".join(row.get("directory") or []).lower()
        ]
    return rows


def _filter_digest(rows: List[Dict[str, Any]], filter_name: str, q: str) -> str:
    ids = ",".join(sorted(row["row_id"] for row in rows))
    return _digest("%s|%s|%s" % (filter_name, q, ids))


def _summary(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "plan_id": plan["plan_id"],
        "config_id": plan["config_id"],
        "status": plan["status"],
        "stale": plan["status"] == STATUS_STALE,
        "source": copy.deepcopy(plan["source"]),
        "revision": plan["revision"],
        "counts": copy.deepcopy(plan["counts"]),
        "pending": _pending(plan),
        "created_at": plan["created_at"],
    }


def _get(wid: str, pid: str, cid: str, plan_id: str) -> Dict[str, Any]:
    with _LOCK:
        plan = _PLANS.get(str(plan_id or ""))
        if (
            plan is None
            or plan["workspace_id"] != wid
            or plan["profile_id"] != pid
            or plan["config_id"] != cid
        ):
            raise AppError(NOT_FOUND, "提取计划不存在或已过期", plan_id=plan_id)
        return plan


def _refresh(plan: Dict[str, Any]) -> None:
    """来源/目录修订变化时把计划标 stale；旧行与决议保留。"""
    state = config_v3_extract.current_state(plan["profile_id"], plan["config_id"], plan["source"])
    source = plan["source"]
    changed = (
        state["missing"]
        or int(state["revision"]) != int(plan["revision"])
        or str(state["blob_sha256"] or "") != str(source.get("blob_sha256") or "")
        or str(state["version_id"] or "") != str(source.get("version_id") or "")
    )
    plan["status"] = STATUS_STALE if changed else STATUS_OPEN


def _drop_old_plans(wid: str, pid: str, cid: str) -> None:
    scoped = [
        plan
        for plan in _PLANS.values()
        if plan["workspace_id"] == wid and plan["profile_id"] == pid and plan["config_id"] == cid
    ]
    scoped.sort(key=lambda row: row["created_at"])
    while len(scoped) > _MAX_PLANS_PER_SCOPE:
        oldest = scoped.pop(0)
        _PLANS.pop(oldest["plan_id"], None)


def create(wid: str, pid: str, cid: str, version_id: str) -> Dict[str, Any]:
    built = config_v3_extract.build(pid, cid, version_id)
    plan = {
        "plan_id": "ep-" + uuid.uuid4().hex[:16],
        "workspace_id": wid,
        "profile_id": pid,
        "config_id": cid,
        "source": copy.deepcopy(built["source"]),
        "revision": built["revision"],
        "counts": copy.deepcopy(built["counts"]),
        "rows": copy.deepcopy(built["items"]),
        "decisions": {},
        "status": STATUS_OPEN,
        "created_at": _now(),
    }
    with _LOCK:
        _PLANS[plan["plan_id"]] = plan
        _drop_old_plans(wid, pid, cid)
    return _summary(plan)


def get(wid: str, pid: str, cid: str, plan_id: str) -> Dict[str, Any]:
    plan = _get(wid, pid, cid, plan_id)
    with _LOCK:
        _refresh(plan)
        return _summary(plan)


def rows(
    wid: str,
    pid: str,
    cid: str,
    plan_id: str,
    offset: int = 0,
    limit: int = 50,
    filter_name: str = config_v3_extract.FILTER_ALL,
    q: str = "",
) -> Dict[str, Any]:
    plan = _get(wid, pid, cid, plan_id)
    with _LOCK:
        _refresh(plan)
        filter_name = str(filter_name or config_v3_extract.FILTER_ALL).strip().lower()
        filtered = _filtered(plan, filter_name, q)
        digest = _filter_digest(filtered, filter_name, q)
        start = max(0, int(offset))
        size = max(1, min(int(limit), 200))
        items = []
        for item in filtered[start : start + size]:
            decision = effective_decision(plan, item)
            items.append(
                {
                    **copy.deepcopy(item),
                    "decision": decision,
                    "decision_ready": _decision_ready(item, decision),
                }
            )
        out = _summary(plan)
        out.update(
            {
                "filter": filter_name,
                "q": str(q or ""),
                "filter_digest": digest,
                "total": len(filtered),
                "offset": start,
                "limit": size,
                "items": items,
            }
        )
        return out


def _validate(item: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    action = str(decision.get("action") or "").strip().lower()
    if action not in ACTIONS:
        raise AppError(INVALID_INPUT, "决议必须是新增/关联/跳过", field="action", row_id=item["row_id"])
    if item["status"] == config_v3_extract.STATUS_UNSUPPORTED and action != ACTION_SKIP:
        raise AppError(INVALID_INPUT, "不支持的行只能跳过", field="action", row_id=item["row_id"])
    if item["status"] == config_v3_extract.STATUS_EXISTING and action == ACTION_ADD:
        raise AppError(INVALID_INPUT, "已存在的行请关联或跳过", field="action", row_id=item["row_id"])
    out = {"action": action, "type": str(decision.get("type") or ""), "parameter_id": ""}
    if action == ACTION_LINK:
        allowed = {item.get("existing_parameter_id")}
        allowed.update(row.get("parameter_id") for row in (item.get("candidates") or []))
        target = str(decision.get("parameter_id") or "")
        if not target or target not in allowed:
            raise AppError(INVALID_INPUT, "关联目标不是该行的候选参数", field="parameter_id", row_id=item["row_id"])
        out["parameter_id"] = target
        if item.get("existing_parameter_id") == target:
            out["type"] = item.get("suggested_type") or ""
    if action == ACTION_ADD:
        if item["status"] == config_v3_extract.STATUS_NULL_UNCERTAIN:
            if out["type"] not in config_v3_extract.TYPE_OPTIONS:
                raise AppError(INVALID_INPUT, "null 行必须先选择类型", field="type", row_id=item["row_id"])
        else:
            out["type"] = out["type"] or item.get("suggested_type") or ""
    return out


def _apply(plan: Dict[str, Any], rows_to_update: List[Dict[str, Any]], decision: Dict[str, Any], strict: bool) -> int:
    updated = 0
    for item in rows_to_update:
        try:
            plan["decisions"][item["row_id"]] = _validate(item, decision)
            updated += 1
        except AppError:
            if strict:
                raise
    return updated


def decisions(wid: str, pid: str, cid: str, plan_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    plan = _get(wid, pid, cid, plan_id)
    with _LOCK:
        _refresh(plan)
        if plan["status"] == STATUS_STALE:
            raise AppError(PLAN_STALE, "计划已过期，请先重算", plan_id=plan_id)
        scope = str(payload.get("scope") or "rows").strip().lower()
        decision = payload.get("decision") or {}
        if not isinstance(decision, dict):
            raise AppError(INVALID_INPUT, "decision 必须是对象", field="decision")
        if scope == "rows":
            row_ids = payload.get("row_ids") or []
            if not isinstance(row_ids, list) or not row_ids:
                raise AppError(INVALID_INPUT, "row_ids 不能为空", field="row_ids")
            wanted = {str(value) for value in row_ids}
            targets = [item for item in plan["rows"] if item["row_id"] in wanted]
            missing = wanted - {item["row_id"] for item in targets}
            if missing:
                raise AppError(NOT_FOUND, "计划中不存在这些行", field="row_ids", row_ids=sorted(missing))
            updated = _apply(plan, targets, decision, strict=True)
        elif scope == "filter":
            filter_name = str(payload.get("filter") or config_v3_extract.FILTER_ALL).strip().lower()
            q = str(payload.get("q") or "")
            targets = _filtered(plan, filter_name, q)
            expected = _filter_digest(targets, filter_name, q)
            if str(payload.get("filter_digest") or "") != expected:
                raise AppError(PLAN_STALE, "筛选快照已变化，请刷新后再批量决议", field="filter_digest")
            updated = _apply(plan, targets, decision, strict=False)
        else:
            raise AppError(INVALID_INPUT, "未知的决议范围", field="scope")
        out = _summary(plan)
        out["updated"] = updated
        return out


def recompute(wid: str, pid: str, cid: str, plan_id: str, version_id: Optional[str] = None) -> Dict[str, Any]:
    plan = _get(wid, pid, cid, plan_id)
    vid = str(version_id or plan["source"].get("version_id") or "")
    built = config_v3_extract.build(pid, cid, vid)
    new_ids = {item["row_id"] for item in built["items"]}
    kept = {
        row_id: decision
        for row_id, decision in plan["decisions"].items()
        if row_id in new_ids
    }
    with _LOCK:
        plan["source"] = copy.deepcopy(built["source"])
        plan["revision"] = built["revision"]
        plan["counts"] = copy.deepcopy(built["counts"])
        plan["rows"] = copy.deepcopy(built["items"])
        plan["decisions"] = kept
        plan["status"] = STATUS_OPEN
        out = _summary(plan)
        out["preserved"] = len(kept)
        return out
