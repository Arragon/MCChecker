"""M16-28 候选决议一次事务写入 V3 草稿、审计与幂等回执。

- 先取 M16-27 预览并做 draft/head/frozen CAS；预览后变化 409；
- 决议只允许预览给出的 allowed decisions；未决 conflict 必须 use_new/inherit_old/manual；
- 所有写值先整体转换、由 `config_v3_drafts.patch_values` 单事务落盘；任一类型非法则 0 写入；
- 幂等键写 `sync/candidate_applies.json`，同键同正文返回同一回执，不同正文 409；
- 只写 D0 草稿，不移动 D0/Bn/rn 已发布 head。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core import storage
from app.core.errors import INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError
from app.core.sync_models import validate_id, validate_workspace_id

from . import (
    config_v3_baselines,
    config_v3_catalog,
    config_v3_drafts,
    sync_candidate_plans,
    sync_candidates,
    sync_store,
    workspace_context,
)

FILE = "candidate_applies.json"
DEFAULT_DOC: Dict[str, Any] = {"items": {}}
DECISIONS = frozenset({"use_new", "inherit_old", "manual"})


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash(preview_id: str, decisions: Dict[str, str]) -> str:
    payload = json.dumps(
        {"preview_id": preview_id, "decisions": decisions},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_doc(wid: str) -> Dict[str, Any]:
    doc = sync_store.read_doc(wid, FILE, DEFAULT_DOC)
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), dict):
        raise AppError(INVALID_INPUT, "candidate_applies.json 形状损坏", field="applies")
    return doc


def _patch_for(typed: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(typed.get("type") or "")
    if kind == "null":
        return {"value_state": "null"}
    text = typed.get("text")
    if not isinstance(text, str):
        raise AppError(INVALID_INPUT, "候选值缺少文本", field="candidate")
    return {"value_state": "present", "value": text}


def _mark_applied(wid: str, candidate_id: Any, revision: Any, applied_at: Any) -> None:
    """回写候选状态；候选记录缺失时不影响已成功的草稿写入。"""
    if not candidate_id:
        return
    try:
        sync_candidates.mark_state(
            wid,
            str(candidate_id),
            "applied",
            applied_revision=revision,
            applied_at=applied_at,
        )
    except AppError as exc:
        if exc.code == NOT_FOUND:
            return
        raise


def _existing_draft_receipt(
    wid: str, pid: str, cid: str, did: str, request_id: str
) -> Optional[Dict[str, Any]]:
    with storage.use_workspace(wid):
        doc = config_v3_catalog._load(pid)
        _config, release = config_v3_drafts._find_release(doc, cid, did)
        d0 = release.get("d0") or {}
        receipt = (d0.get("receipts") or {}).get(request_id)
    if not isinstance(receipt, dict):
        return None
    return {
        "status": "applied",
        "replayed": True,
        "changed": list(receipt.get("items") or []),
        "draft_revision": int(receipt.get("revision") or 0),
        "applied_at": receipt.get("at"),
        "recovered_from_draft_receipt": True,
    }


def _write_baseline_draft(
    wid: str,
    profile_id: str,
    preview: Dict[str, Any],
    patch_items: List[Dict[str, Any]],
    request_id: str,
) -> tuple:
    """从当前基线复制一份变更草稿并写入勾选的新值。不改冻结的 D0，不发布。"""
    snapshot = sync_candidate_plans._draft_snapshot(
        wid, profile_id, preview["config_id"], preview["draft_id"]
    )
    release_id = snapshot["release"]["release_id"]
    config_id = preview["config_id"]
    with storage.use_workspace(wid):
        try:
            draft = config_v3_baselines.create_baseline_draft(profile_id, config_id, release_id)
        except AppError as exc:
            existing = (exc.detail or {}).get("draft_id")
            if exc.code != REVISION_CONFLICT or not existing:
                raise
            draft = config_v3_baselines.get_baseline_draft(profile_id, config_id, existing)
        draft_id = draft["draft_id"]
        revision = int(draft.get("revision") or 0)
        if not patch_items:
            return draft_id, revision, []
        patched, _etag = config_v3_baselines.patch_baseline_values(
            profile_id,
            config_id,
            draft_id,
            {"request_id": request_id, "items": patch_items, "expected_revision": revision},
            '"%d"' % revision,
        )
    return draft_id, int(patched["revision"]), sorted((patched.get("values") or {}).keys())


def apply_preview(
    workspace_id: str,
    profile_id: str,
    preview_id: str,
    request_id: Any,
    decisions: Any,
    create_baseline: bool = False,
) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    workspace_context.assert_profile(wid, profile_id)
    pid = validate_id(preview_id, "preview_id")
    key = str(request_id or "").strip()
    if not key or len(key) > 100:
        raise AppError(INVALID_INPUT, "request_id 必填且 ≤100 字符", field="request_id")
    if decisions is None:
        decisions = {}
    if not isinstance(decisions, dict):
        raise AppError(INVALID_INPUT, "decisions 必须是对象", field="decisions")
    normalized = {str(k): str(v) for k, v in decisions.items()}
    for decision in normalized.values():
        if decision not in DECISIONS:
            raise AppError(INVALID_INPUT, "未知决议", field="decisions", decision=decision)
    digest = _hash(pid, normalized)
    receipt_key = pid + "|" + key

    with sync_store.workspace_lock(wid), storage.use_workspace(wid), storage.use_profile(profile_id), storage.profile_write(profile_id):
        receipt = _load_doc(wid)["items"].get(receipt_key)
        if isinstance(receipt, dict):
            if receipt.get("request_hash") != digest:
                raise AppError(REVISION_CONFLICT, "幂等键已用于不同决议", field="request_id")
            # 幂等重放：回执已存在，补齐可能因崩溃未落盘的候选状态（不产生第二次写入）。
            _mark_applied(
                wid,
                receipt.get("candidate_id"),
                receipt.get("draft_revision"),
                receipt.get("applied_at"),
            )
            return dict(receipt, replayed=True)

        preview = sync_candidate_plans.get_preview(wid, pid)
        frozen = bool(preview.get("d0_frozen"))
        if frozen and not create_baseline:
            raise AppError(REVISION_CONFLICT, "D0 已冻结，不能应用候选", field="d0_frozen")
        if create_baseline and not frozen:
            raise AppError(INVALID_INPUT, "D0 仍是准备稿，请写入准备版草稿", field="create_baseline")
        blockers = [
            row for row in preview.get("blockers") or []
            if row.get("code") != "base_missing"
            and not (create_baseline and row.get("code") == "d0_frozen")
        ]
        if blockers:
            raise AppError(
                INVALID_INPUT,
                "候选预览存在阻断，不能应用",
                field="preview",
                blockers=blockers,
            )

        items_by_id = {item["parameter_id"]: item for item in preview.get("items") or []}
        unknown = set(normalized) - set(items_by_id)
        if unknown:
            raise AppError(INVALID_INPUT, "决议引用了未知参数", field="decisions", keys=sorted(unknown))

        patch_items: List[Dict[str, Any]] = []
        changed: List[str] = []
        for parameter_id, item in items_by_id.items():
            action = item.get("action")
            decision = normalized.get(parameter_id)
            allowed = list(item.get("allowed") or [])
            if create_baseline and not allowed:
                allowed = ["use_new", "inherit_old", "manual"]
            if decision and decision not in allowed:
                raise AppError(
                    INVALID_INPUT,
                    "该参数不允许此决议",
                    field="decisions",
                    parameter_id=parameter_id,
                )
            if action == "conflict" and not decision:
                raise AppError(INVALID_INPUT, "仍有未决冲突", field="decisions", parameter_id=parameter_id)
            if action == "manual" and decision != "manual":
                raise AppError(INVALID_INPUT, "该参数必须人工处理", field="decisions", parameter_id=parameter_id)
            write = False
            if action == "from_new":
                write = decision not in ("inherit_old", "manual")
            elif action == "conflict":
                write = decision == "use_new"
            elif action == "keep":
                write = decision == "use_new"
            elif action == "manual":
                write = False
            if not write:
                continue
            typed = (item.get("theirs") or {}).get("value")
            if not isinstance(typed, dict):
                raise AppError(INVALID_INPUT, "候选值缺失", field="candidate", parameter_id=parameter_id)
            patch = _patch_for(typed)
            patch.update(
                {
                    "parameter_id": parameter_id,
                    "membership": "included",
                }
            )
            patch_items.append(patch)
            changed.append(parameter_id)

        if create_baseline:
            draft_id, draft_revision, changed = _write_baseline_draft(
                wid, profile_id, preview, patch_items, key
            )
            result = {
                "status": "applied",
                "changed": changed,
                "draft_revision": draft_revision,
                "draft_id": draft_id,
                "target": "baseline_draft",
                "applied_at": _now(),
                "request_hash": digest,
                "preview_id": pid,
            }
        elif not patch_items:
            result = {
                "status": "applied",
                "changed": [],
                "draft_revision": int(preview["draft_revision"]),
                "applied_at": _now(),
                "request_hash": digest,
                "preview_id": pid,
            }
        else:
            existing = _existing_draft_receipt(
                wid, profile_id, preview["config_id"], preview["draft_id"], key
            )
            if existing is not None:
                result = dict(existing, request_hash=digest, preview_id=pid)
            else:
                with storage.use_workspace(wid):
                    draft_result, _etag = config_v3_drafts.patch_values(
                        profile_id,
                        preview["config_id"],
                        preview["draft_id"],
                        {"request_id": key, "items": patch_items},
                        if_match='"%d"' % int(preview["draft_revision"]),
                    )
                result = {
                    "status": "applied",
                    "changed": changed,
                    "draft_revision": int(draft_result["revision"]),
                    "applied_at": _now(),
                    "request_hash": digest,
                    "preview_id": pid,
                }
        result.update(
            {
                "workspace_id": wid,
                "profile_id": profile_id,
                "config_id": preview["config_id"],
                "candidate_id": preview["candidate_id"],
                "request_id": key,
            }
        )
        result.setdefault("draft_id", preview["draft_id"])

        def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
            doc["items"][receipt_key] = result
            return doc

        sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
        # M16-36：写入成功后回写候选状态，列表才不会永远显示 pending（重复 apply）。
        # 只改候选状态与回执字段；D0/Bn/rn 已发布 head 不动。
        _mark_applied(
            wid,
            preview["candidate_id"],
            result.get("draft_revision"),
            result.get("applied_at"),
        )
        return result


def get_apply(workspace_id: str, preview_id: str, request_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    pid = validate_id(preview_id, "preview_id")
    key = str(request_id or "").strip()
    record = _load_doc(wid)["items"].get(pid + "|" + key)
    if not isinstance(record, dict):
        raise AppError(INVALID_INPUT, "回执不存在", field="request_id")
    return dict(record)
