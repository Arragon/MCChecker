"""V3 事件审计与恢复草稿（M14-05 / INH-2154）。

事件只由服务端在写操作里追加，读接口不产事件。事件按 W/P 隔离，字段包含
principal/角色/IP/时间/before-after/source/requestId/undoOf；恢复从不可变旧
修订生成**新可编辑草稿**，不改历史、不移动 head、不宣称物理降级。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core import fsutil, locks, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, PLAN_STALE, AppError
from . import config_v3_catalog, config_v3_merge

_LOCK = threading.RLock()
_RESTORES: Dict[str, Dict[str, Any]] = {}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _events_path(pid: str) -> str:
    return os.path.join(storage.get_profile_data_dir(pid), "configs_v3", "events.json")


def _load(pid: str) -> List[Dict[str, Any]]:
    raw = fsutil.read_json(_events_path(pid), [])
    return raw if isinstance(raw, list) else []


def record(
    profile_id: str,
    config_id: str,
    event_type: str,
    *,
    actor: Any = None,
    ip: str = "",
    before: Any = None,
    after: Any = None,
    source: Any = None,
    request_id: str = "",
    undo_of: str = "",
) -> Dict[str, Any]:
    event = {
        "event_id": "ev-" + uuid.uuid4().hex[:12],
        "workspace_id": storage.get_active_workspace(),
        "profile_id": profile_id,
        "config_id": config_id,
        "type": event_type,
        "principal": getattr(actor, "name", "") or "",
        "role": getattr(actor, "role_label", "") or "",
        "ip": ip or getattr(actor, "ip", "") or "",
        "at": _now(),
        "before": copy.deepcopy(before),
        "after": copy.deepcopy(after),
        "source": copy.deepcopy(source),
        "request_id": request_id,
        "undo_of": undo_of,
    }
    with _LOCK, locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        events = _load(profile_id)
        event["seq"] = len(events) + 1
        events.append(event)
        fsutil.atomic_write_json(_events_path(profile_id), events)
    return event


def list_events(profile_id: str, config_id: str = "", q: str = "") -> List[Dict[str, Any]]:
    with _LOCK, storage.use_profile(profile_id):
        events = _load(profile_id)
    keyword = str(q or "").lower()
    out = []
    for event in events:
        if config_id and event.get("config_id") != config_id:
            continue
        if keyword and keyword not in json.dumps(event, ensure_ascii=False).lower():
            continue
        out.append(copy.deepcopy(event))
    out.sort(key=lambda row: (row["at"], int(row.get("seq", 0))), reverse=True)
    return out


def restore(profile_id: str, config_id: str, payload: Dict[str, Any], actor: Any = None, ip: str = "") -> Dict[str, Any]:
    revision_id = str(payload.get("revision_id") or "").strip()
    if not revision_id:
        raise AppError(INVALID_INPUT, "必须指定恢复修订", field="revision_id")
    revision = config_v3_merge.resolve_revision(profile_id, config_id, revision_id)
    values = copy.deepcopy(revision["values"])
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = config_v3_catalog._load(profile_id)
        config = doc["configs"].get(config_id)
        if config is None:
            raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
        release = (config.get("releases") or {}).get(revision.get("release_id"))
        if release is None:
            raise AppError(NOT_FOUND, "修订所属软件版本不存在")
        before_head = (config.get("main_branch") or {}).get("head_id")
        if revision["kind"] == "machine":
            machine = (config.get("machines") or {}).get(revision.get("machine_id"))
            if machine is None:
                raise AppError(NOT_FOUND, "机台不存在")
            existing = machine.get("draft")
            if isinstance(existing, dict) and existing.get("status") == "draft":
                raise AppError(PLAN_STALE, "机台已有未发布草稿", draft_id=existing.get("draft_id"))
            draft = {
                "draft_id": "md-" + uuid.uuid4().hex[:12], "status": "draft", "revision": 0,
                "values": values, "base_kind": "history", "base_revision_id": revision_id,
                "restore_source": "history", "parent_id": before_head,
                "definition_revision": int(release.get("definition_revision", 0)),
                "created_at": _now(),
            }
            machine["draft"] = draft
            kind = "machine"
        else:
            if not release.get("default_revision_id"):
                raise AppError(INVALID_INPUT, "目标版本 D0 尚未冻结，不能派生恢复草稿")
            existing = release.get("baseline_draft")
            if isinstance(existing, dict) and existing.get("status") == "draft":
                raise AppError(PLAN_STALE, "该版本已有未发布草稿", draft_id=existing.get("draft_id"))
            draft = {
                "draft_id": "bd-" + uuid.uuid4().hex[:12], "release_id": release["release_id"],
                "status": "draft", "revision": 0, "values": values,
                "source_revision_id": revision_id, "restore_source": "history",
                "parent_id": before_head,
                "definition_revision": int(release.get("definition_revision", 0)),
                "created_at": _now(), "receipts": {},
            }
            release["baseline_draft"] = draft
            kind = "baseline"
        config["updated_at"] = _now()
        config_v3_catalog._save(profile_id, doc)
    event = record(
        profile_id, config_id, "restore", actor=actor, ip=ip,
        before={"head_id": before_head}, after={"draft_id": draft["draft_id"]},
        source={"revision_id": revision_id, "content_hash": revision["content_hash"]},
        request_id=str(payload.get("request_id") or ""),
    )
    plan = {
        "plan_id": "rs-" + uuid.uuid4().hex[:12], "workspace_id": storage.get_active_workspace(),
        "profile_id": profile_id, "config_id": config_id, "draft_id": draft["draft_id"],
        "kind": kind, "revision_id": revision_id, "event_id": event["event_id"], "revision": 0,
    }
    with _LOCK:
        _RESTORES[plan["plan_id"]] = plan
    return {"plan": plan, "draft": draft, "event": event}


def undo(profile_id: str, config_id: str, plan_id: str, actor: Any = None, ip: str = "") -> Dict[str, Any]:
    with _LOCK:
        plan = _RESTORES.get(str(plan_id))
        if plan is None or plan["profile_id"] != profile_id or plan["config_id"] != config_id:
            raise AppError(NOT_FOUND, "恢复计划不存在", plan_id=plan_id)
    with locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = config_v3_catalog._load(profile_id)
        config = doc["configs"].get(config_id)
        draft = None
        for release in (config.get("releases") or {}).values():
            candidate = release.get("baseline_draft")
            if isinstance(candidate, dict) and candidate.get("draft_id") == plan["draft_id"]:
                draft = candidate
                break
        if draft is None:
            for machine in (config.get("machines") or {}).values():
                candidate = machine.get("draft")
                if isinstance(candidate, dict) and candidate.get("draft_id") == plan["draft_id"]:
                    draft = candidate
                    break
        if draft is None:
            raise AppError(NOT_FOUND, "恢复草稿不存在")
        if int(draft.get("revision", 0)) != 0:
            raise AppError(PLAN_STALE, "恢复草稿已被修改，不能撤销")
        draft["status"] = "undone"
        config["updated_at"] = _now()
        config_v3_catalog._save(profile_id, doc)
    event = record(
        profile_id, config_id, "undo", actor=actor, ip=ip,
        before={"draft_id": plan["draft_id"]}, after={"status": "undone"},
        source={"restore_plan_id": plan_id}, undo_of=plan["event_id"],
    )
    return {"plan_id": plan_id, "draft_id": plan["draft_id"], "event": event}
