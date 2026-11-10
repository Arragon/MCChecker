"""Machine standard changes use one captured editing session; never publish it.

Kept behind the existing merge-plan API. Saved snapshot resolution remains
saved-only, so accepting a draft here cannot make it exportable as history.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Dict

from ..core import locks, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, PLAN_STALE, AppError
from . import config_v3_catalog, config_v3_diff, config_v3_machines, config_v3_merge


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _load(pid, cid):
    doc = config_v3_catalog._load(pid)
    config = doc["configs"].get(cid)
    if config is None:
        raise AppError(NOT_FOUND, "配置不存在", config_id=cid)
    return doc, config


def _machine(config, mid):
    machine = (config.get("machines") or {}).get(mid)
    if not machine:
        raise AppError(NOT_FOUND, "机台不存在", machine_id=mid)
    return machine


def _guard(machine):
    # Includes modes, adopted source, target version, head and draft identity;
    # value equality alone does not establish that this is still the same session.
    return _hash({"head": machine.get("head_id"), "release": machine.get("release_id"),
                  "draft": machine.get("draft")})


def _standard(config, rid):
    record = config_v3_diff.resolve(config, rid)
    if record["kind"] == "machine":
        raise AppError(INVALID_INPUT, "请选择已保存的版本标准", field="theirs_revision_id")
    release, _ = config_v3_catalog.configuration_record(config, rid)
    return dict(record, release_id=release["release_id"], release_version=release["version"],
                content_hash=_hash(record))


def _member(row):
    if row is None:
        return None
    # Compare full member state while keeping explicit 0/false/null/empty values.
    return {k: row.get(k) for k in ("membership", "enabled", "value_state", "value")}


def _adopted_standard(config, revision_id):
    visited = set()
    while revision_id and revision_id not in visited:
        visited.add(revision_id)
        try:
            record = config_v3_diff.resolve(config, revision_id)
        except AppError as exc:
            if exc.code != NOT_FOUND:
                raise
            return None
        if record["kind"] != "machine":
            return revision_id
        _, raw = config_v3_catalog.configuration_record(config, revision_id)
        revision_id = raw.get("base_revision_id")
    return None


def create(pid: str, cid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    with storage.use_profile(pid), storage.profile_write(pid):
        _, config = _load(pid, cid)
        mid = str(payload.get("machine_id") or "")
        machine = _machine(config, mid)
        draft = machine.get("draft") or {}
        active = draft.get("status") == "draft"
        wanted = str(payload.get("ours_revision_id") or "")
        expected = draft.get("draft_id") if active else machine.get("head_id")
        if wanted != expected:
            raise AppError(PLAN_STALE, "机台配置已变化，请重新打开当前草稿", field="ours_revision_id")
        release, source = config_v3_catalog.configuration_record(config, wanted)
        ours = dict(copy.deepcopy(source), revision_id=wanted, kind="machine", machine_id=mid,
                    release_id=release["release_id"], release_version=release["version"])
        ours.setdefault("definition_revision", release["definition_revision"])
        ours["content_hash"] = _hash(ours)
        basis = _adopted_standard(config, source.get("base_revision_id"))
        explicit = str(payload.get("base_revision_id") or "")
        if basis and explicit and explicit != basis:
            raise AppError(INVALID_INPUT, "原来的标准与机台实际来源不一致", field="base_revision_id")
        if not basis and not explicit:
            raise AppError(INVALID_INPUT, "缺少原来的标准，请选择此机台原来采用的已保存标准", field="base_revision_id")
        base = _standard(config, basis or explicit)
        original_release = ((machine.get("revisions") or {}).get(machine.get("head_id")) or {}).get("release_id") or release["release_id"]
        if not basis and base["release_id"] != original_release:
            raise AppError(INVALID_INPUT, "原来的标准不属于机台原软件版本", field="base_revision_id")
        target = _standard(config, str(payload.get("theirs_revision_id") or ""))
        definitions = config_v3_merge._definitions(config, target["definition_revision"])
        old_defs = config_v3_merge._definitions(config, ours["definition_revision"])
        items = []
        for parameter_id in sorted(set(definitions) | set(old_defs) | set(ours.get("values") or {})):
            old = (base.get("values") or {}).get(parameter_id)
            current = (ours.get("values") or {}).get(parameter_id)
            new = (target.get("values") or {}).get(parameter_id)
            intent = (current or {}).get("intent") or "inherit"
            incompatible = parameter_id not in definitions or (
                parameter_id in old_defs and any(old_defs[parameter_id].get(k) != definitions[parameter_id].get(k) for k in ("type", "unit")))
            if incompatible:
                action = "conflict"
            elif intent == "pin":
                action = "keep"
            elif intent == "override":
                action = "conflict" if _member(old) != _member(new) and _member(current) != _member(new) else "keep"
            else:
                action = "from_new"
            allowed = ["keep_local", "adopt_target"]
            # A value absent from the target definition cannot be retained there.
            if parameter_id not in definitions:
                allowed = ["adopt_target"]
            items.append({"parameter_id": parameter_id,
                          "name": (definitions.get(parameter_id) or old_defs.get(parameter_id) or {}).get("name") or parameter_id,
                          "action": action, "allowed_decisions": allowed,
                          "default_decision": "pending" if action == "conflict" else "keep_local" if action == "keep" else "adopt_target",
                          "base_member": copy.deepcopy(old), "ours_member": copy.deepcopy(current),
                          "theirs_member": copy.deepcopy(new), "definition_changed": incompatible})
        return {"mode": "machine_upgrade", "base": config_v3_merge._ref(base),
                "ours": config_v3_merge._ref(ours), "theirs": config_v3_merge._ref(target),
                "read_set": [config_v3_merge._ref(r) for r in (base, ours, target)],
                "items": items, "machine_id": mid, "guard": _guard(machine)}


def validate(pid, cid, plan):
    _, config = _load(pid, cid)
    machine = _machine(config, plan["machine_id"])
    if _guard(machine) != plan["guard"]:
        raise AppError(PLAN_STALE, "配置已被其他人修改，请重新比较")
    for key in ("base", "theirs"):
        ref = plan["merge"][key]
        current = _standard(config, ref["revision_id"])
        if current["content_hash"] != ref["content_hash"]:
            raise AppError(PLAN_STALE, "参照标准已变化，请重新比较")


def commit(pid, cid, plan, payload, effective):
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 100:
        raise AppError(INVALID_INPUT, "缺少有效保存请求标识", field="request_id")
    signature = _hash({"plan": plan["plan_id"], "decisions": plan["decisions"]})
    with locks.write_gate.write(), storage.use_profile(pid), storage.profile_write(pid):
        doc, config = _load(pid, cid)
        machine = _machine(config, plan["machine_id"])
        receipts = machine.setdefault("upgrade_receipts", {})
        if request_id in receipts:
            receipt = receipts[request_id]
            if receipt["signature"] != signature:
                raise AppError(PLAN_STALE, "此请求标识已用于其他处理方式")
            return dict(copy.deepcopy(receipt["result"]), replayed=True)
        validate(pid, cid, plan)
        pending = [r["parameter_id"] for r in plan["rows"] if effective(plan, r) == "pending"]
        if pending:
            raise AppError(PLAN_STALE, "还有参数需要选择处理方式", fields={"parameter_ids": pending})
        target = _standard(config, plan["merge"]["theirs"]["revision_id"])
        values = {}
        for row in plan["rows"]:
            adopt = effective(plan, row) == "adopt_target"
            member = copy.deepcopy(row["theirs_member"] if adopt else row["ours_member"])
            if member is not None:
                if adopt:
                    member["intent"] = "inherit"
                elif member.get("intent") != "pin":
                    member["intent"] = "override"
                values[row["parameter_id"]] = member
        release = config["releases"][target["release_id"]]
        config_v3_machines._validated(release, config, values)
        previous = machine.get("draft") or {}
        active = previous.get("status") == "draft"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        draft = {"draft_id": previous["draft_id"] if active else "md-" + uuid.uuid4().hex[:12],
                 "status": "draft", "revision": int(previous.get("revision", 0)) + 1 if active else 0,
                 "values": values, "base_kind": target["kind"], "base_revision_id": target["revision_id"],
                 "definition_revision": target["definition_revision"], "created_at": previous.get("created_at", now) if active else now}
        machine["draft"] = draft
        machine["release_id"] = target["release_id"]
        config["updated_at"] = now
        result = {"draft": dict(copy.deepcopy(draft), machine_id=machine["machine_id"], release_id=target["release_id"], kind="machine")}
        receipts[request_id] = {"signature": signature, "result": copy.deepcopy(result)}
        config_v3_catalog._save(pid, doc)
        return result
