"""M16-27 候选三方参数差异计划/类型校验/草稿 CAS 预览。

三方口径：base=B、当前草稿=A、候选=C，复用 `core.config_merge` 的纯算法：

- A=B 且 C 不同 → `from_new`（取 C）；
- C=A 且 B 不同 → `keep`（保留 A）；
- B=C 且 A 不同 → `keep`（上游没变，保留 A）；
- 三方不同 → `conflict`，必须显式决议。

预览只写 `sync/candidate_previews.json`；不改 V3 草稿/D0/Bn/rn。预览记录创建时的
`draft_revision`、`head_id` 与冻结状态，读取时重新核对；任一变化返回 409
`REVISION_CONFLICT`。D0 已冻结时只给 blocker，不提供覆盖决议。

候选内容提取（文件 → 参数值）不在本模块：调用方必须提供已类型化的 base/candidate
值；本模块负责类型校验、三方计划、CAS 与读取。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core import config_merge, config_values, storage
from app.core.errors import INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError
from app.core.sync_models import validate_id, validate_workspace_id

from . import config_v3_catalog, config_v3_drafts, sync_store, workspace_context

FILE = "candidate_previews.json"
DEFAULT_DOC: Dict[str, Any] = {"items": {}}
_KIND_BY_TYPE = {
    "number": "decimal",
    "decimal": "decimal",
    "float": "decimal",
    "int": "int",
    "integer": "int",
    "bool": "bool",
    "boolean": "bool",
    "string": "string",
    "enum": "string",
    "raw": "string",
}


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _stable(*parts: Any) -> str:
    payload = "\x1f".join(str(part) for part in parts)
    return "prv-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _load_doc(workspace_id: str) -> Dict[str, Any]:
    doc = sync_store.read_doc(workspace_id, FILE, DEFAULT_DOC)
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), dict):
        raise AppError(INVALID_INPUT, "candidate_previews.json 形状损坏", field="previews")
    return doc


def _safe(preview: Dict[str, Any]) -> Dict[str, Any]:
    return dict(preview)


def _draft_snapshot(workspace_id: str, profile_id: str, config_id: str, draft_id: str) -> Dict[str, Any]:
    with storage.use_workspace(workspace_id):
        draft = config_v3_drafts.get_draft(profile_id, config_id, draft_id)
        with storage.use_profile(profile_id):
            doc = config_v3_catalog._load(profile_id)
    config, release = config_v3_drafts._find_release(doc, config_id, draft_id)
    definition = config_v3_drafts._definition_for(release, config)
    head_id = str((config.get("main_branch") or {}).get("head_id") or "")
    return {
        "draft": draft,
        "config": config,
        "release": release,
        "params": definition.get("parameters") or {},
        "head_id": head_id,
    }


def _typed_entry(raw: Any, field: str) -> Dict[str, Any]:
    if isinstance(raw, dict) and "value" in raw:
        value = raw.get("value")
        intent = str(raw.get("intent") or config_merge.INTENT_OVERRIDE)
    else:
        value = raw
        intent = config_merge.INTENT_OVERRIDE
    if value is None:
        typed = config_values.TypedValue.null()
    else:
        try:
            typed = config_values.TypedValue.from_json(value)
        except AppError:
            raise AppError(INVALID_INPUT, "%s 不是合法类型化值" % field, field=field)
    return {"value": typed.to_json(), "intent": intent}


def _entries(mapping: Any, field: str) -> Dict[str, Any]:
    if mapping is None:
        return {}
    if not isinstance(mapping, dict):
        raise AppError(INVALID_INPUT, "%s 必须是对象" % field, field=field)
    return {str(pid): _typed_entry(raw, field) for pid, raw in mapping.items()}


def _current_entries(draft: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for pid, row in (draft.get("values") or {}).items():
        if pid not in params:
            continue
        if row.get("membership", "not_introduced") != "included" or not row.get("enabled", True):
            continue
        if row.get("value_state") != "present":
            continue
        kind = _KIND_BY_TYPE.get(str((params.get(pid) or {}).get("type") or "").lower(), "string")
        try:
            typed = config_values.parse_typed(
                row.get("value"), kind, (params.get(pid) or {}).get("unit") or None
            )
        except AppError:
            continue
        out[pid] = {"value": typed.to_json(), "intent": row.get("intent") or config_merge.INTENT_INHERIT}
    return out


def _type_blockers(candidate: Dict[str, Any], params: Dict[str, Any]) -> List[Dict[str, Any]]:
    blockers = []
    for pid, entry in candidate.items():
        typed = entry["value"]
        if typed.get("type") == "null":
            continue
        definition = params.get(pid)
        if not definition:
            blockers.append(
                {"code": "type_invalid", "parameter_id": pid, "message": "候选参数不在目标定义中"}
            )
            continue
        expected = _KIND_BY_TYPE.get(str(definition.get("type") or "").lower())
        if expected is None:
            continue
        kind = typed.get("type")
        compatible = kind == expected or (expected == "decimal" and kind == "int")
        if not compatible:
            blockers.append(
                {
                    "code": "type_invalid",
                    "parameter_id": pid,
                    "message": "候选值类型与参数定义不一致",
                    "expected": expected,
                    "actual": kind,
                }
            )
    return blockers


def _same_value(left: Optional[Dict[str, Any]], right: Optional[Dict[str, Any]]) -> bool:
    if left is None or right is None:
        return left is right
    return left.get("value") == right.get("value")


def _normalize_actions(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """意图差异不应把「C=A」误报成上游变更；按值本身收敛 action。"""
    out = []
    for item in items:
        row = dict(item)
        if row.get("action") in ("manual", "drop") or row.get("blockers"):
            out.append(row)
            continue
        base = row.get("base")
        ours = row.get("ours")
        theirs = row.get("theirs")
        if _same_value(ours, theirs):
            row["action"] = "keep"
            row["decision"] = None
            row["candidate"] = (ours or {}).get("value")
        elif _same_value(base, ours):
            row["action"] = "from_new"
            row["decision"] = None
            row["candidate"] = (theirs or {}).get("value")
        elif _same_value(base, theirs):
            row["action"] = "keep"
            row["decision"] = None
            row["candidate"] = (ours or {}).get("value")
        out.append(row)
    return out


def create_preview(
    workspace_id: str,
    profile_id: str,
    config_id: str,
    draft_id: str,
    candidate_id: str,
    base: Any,
    candidate: Any,
) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    workspace_context.assert_profile(wid, profile_id)
    cid = validate_id(config_id, "config_id")
    did = validate_id(draft_id, "draft_id")
    candidate_key = validate_id(candidate_id, "candidate_id")
    snapshot = _draft_snapshot(wid, profile_id, cid, did)
    draft = snapshot["draft"]
    params = snapshot["params"]
    base_entries = _entries(base, "base")
    candidate_entries = _entries(candidate, "candidate")
    current_entries = _current_entries(draft, params)
    names = {pid: str((params.get(pid) or {}).get("name") or pid) for pid in params}
    merged = config_merge.merge(
        base=base_entries,
        ours=current_entries,
        theirs=candidate_entries,
        base_definitions=params,
        ours_definitions=params,
        theirs_definitions=params,
        names=names,
    )
    items = _normalize_actions(merged.get("items") or [])
    summary = {action: 0 for action in config_merge.ACTIONS}
    for item in items:
        summary[item.get("action", "manual")] = summary.get(item.get("action", "manual"), 0) + 1
    needs_decision = [item["parameter_id"] for item in items if item.get("action") == "conflict"]
    manual = [item["parameter_id"] for item in items if item.get("action") == "manual"]
    blockers = list(merged.get("blockers") or [])
    blockers.extend(_type_blockers(candidate_entries, params))
    frozen = bool(draft.get("frozen"))
    if frozen:
        blockers.append(
            {"code": "d0_frozen", "parameter_id": None, "message": "D0 已冻结，不提供覆盖"}
        )
    can_apply = bool(merged.get("can_apply")) and not blockers and not frozen and not needs_decision
    preview_id = _stable(
        wid,
        profile_id,
        cid,
        did,
        candidate_key,
        draft["revision"],
        snapshot["head_id"],
        frozen,
        json.dumps(base_entries, sort_keys=True, ensure_ascii=False),
        json.dumps(candidate_entries, sort_keys=True, ensure_ascii=False),
    )
    preview = {
        "preview_id": preview_id,
        "workspace_id": wid,
        "profile_id": profile_id,
        "config_id": cid,
        "draft_id": did,
        "candidate_id": candidate_key,
        "draft_revision": int(draft["revision"]),
        "head_id": snapshot["head_id"] or None,
        "d0_frozen": frozen,
        "base": base_entries,
        "current": current_entries,
        "candidate": candidate_entries,
        "items": items,
        "summary": summary,
        "blockers": blockers,
        "needs_decision": needs_decision,
        "manual": manual,
        "allowed_decisions": [] if frozen else list(merged.get("allowed_actions") or []),
        "can_apply": can_apply,
        "created_at": _now(),
    }

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        doc["items"][preview_id] = preview
        return doc

    sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    return _safe(preview)


def get_preview(workspace_id: str, preview_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    pid = validate_id(preview_id, "preview_id")
    preview = _load_doc(wid)["items"].get(pid)
    if not isinstance(preview, dict):
        raise AppError(NOT_FOUND, "候选预览不存在", field="preview_id")
    snapshot = _draft_snapshot(wid, preview["profile_id"], preview["config_id"], preview["draft_id"])
    draft = snapshot["draft"]
    changes = []
    if int(draft["revision"]) != int(preview["draft_revision"]):
        changes.append("draft_revision")
    if (snapshot["head_id"] or None) != preview.get("head_id"):
        changes.append("head_id")
    if bool(draft.get("frozen")) != bool(preview.get("d0_frozen")):
        changes.append("d0_frozen")
    if changes:
        raise AppError(
            REVISION_CONFLICT,
            "预览后草稿/发布头/冻结状态已变化，请重新预览",
            changed=changes,
            preview_revision=preview["draft_revision"],
            current_revision=int(draft["revision"]),
        )
    return _safe(preview)


def list_previews(
    workspace_id: str,
    profile_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    wid = validate_workspace_id(workspace_id)
    rows = []
    for preview in _load_doc(wid)["items"].values():
        if not isinstance(preview, dict):
            continue
        if profile_id and preview.get("profile_id") != profile_id:
            continue
        if candidate_id and preview.get("candidate_id") != candidate_id:
            continue
        rows.append(_safe(preview))
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return rows
