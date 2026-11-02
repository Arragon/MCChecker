"""M16-26 默认配置更新候选：稳定去重与统一文件版本引用。

候选只引用既有 FileVersion/blob，不复制原文到 sync/；不创建/移动 D0/Bn/rn
发布 head。相同 source+binding+hash+target 重复同步返回同一候选并累积
seen_count；同一绑定出现新 hash 时旧 pending 候选标 superseded。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core import storage
from app.core.errors import INVALID_INPUT, NOT_FOUND, AppError
from app.core.sync_models import (
    CANDIDATE_STATES,
    SyncCandidate,
    make_candidate_id,
    validate_enum,
    validate_id,
    validate_revision,
    validate_sha256,
    validate_workspace_id,
)

from . import file_versions, sync_store, workspace_context

FILE = "candidates.json"
DEFAULT_DOC: Dict[str, Any] = {"revision": 0, "items": []}


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(workspace_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    doc = sync_store.read_doc(wid, FILE, DEFAULT_DOC)
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        raise AppError(INVALID_INPUT, "candidates.json 形状损坏", field="candidates")
    doc.setdefault("revision", 0)
    return doc


def _find(doc: Dict[str, Any], candidate_id: str) -> Optional[Dict[str, Any]]:
    cid = validate_id(candidate_id, "candidate_id")
    for item in doc["items"]:
        if isinstance(item, dict) and item.get("candidate_id") == cid:
            return item
    return None


def _safe(item: Dict[str, Any]) -> Dict[str, Any]:
    candidate = SyncCandidate.from_dict(item)
    payload = candidate.to_dict()
    payload["seen_count"] = int(item.get("seen_count", 1))
    payload["last_seen_at"] = item.get("last_seen_at") or candidate.created_at
    payload["superseded_by"] = item.get("superseded_by")
    return payload


def _file_version_exists(workspace_id: str, profile_id: str, file_version_id: str) -> None:
    with storage.use_workspace(workspace_id), storage.use_profile(profile_id):
        found = file_versions.find_version(profile_id, file_version_id)
    if found is None:
        raise AppError(NOT_FOUND, "候选引用的文件版本不存在", field="file_version_id")


def create_candidate(
    workspace_id: str,
    profile_id: str,
    source_id: str,
    binding_revision: int,
    content_hash: str,
    target_scope: str,
    file_version_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    workspace_context.assert_profile(wid, profile_id)
    sid = validate_id(source_id, "source_id")
    revision = validate_revision(binding_revision, "binding_revision")
    digest = validate_sha256(content_hash, "content_hash")
    scope = str(target_scope or "").strip()
    if not scope or len(scope) > 160:
        raise AppError(INVALID_INPUT, "target_scope 非法", field="target_scope")
    version_id = validate_id(file_version_id, "file_version_id") if file_version_id else None
    if version_id:
        _file_version_exists(wid, profile_id, version_id)
    candidate_id = make_candidate_id(sid, revision, digest, scope)
    moment = created_at or _now()

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        existing = _find(doc, candidate_id)
        if existing is not None:
            existing["seen_count"] = int(existing.get("seen_count", 1)) + 1
            existing["last_seen_at"] = moment
            return doc
        for item in doc["items"]:
            if not isinstance(item, dict):
                continue
            if (
                item.get("source_id") == sid
                and item.get("binding_revision") == revision
                and item.get("target_scope") == scope
                and item.get("content_hash") != digest
                and item.get("state") == "pending"
            ):
                item["state"] = "superseded"
                item["superseded_by"] = candidate_id
        candidate = SyncCandidate(
            candidate_id=candidate_id,
            workspace_id=wid,
            profile_id=profile_id,
            source_id=sid,
            binding_revision=revision,
            content_hash=digest,
            target_scope=scope,
            state="pending",
            created_at=moment,
            file_version_id=version_id,
        )
        payload = candidate.to_dict()
        payload["seen_count"] = 1
        payload["last_seen_at"] = moment
        doc["items"].append(payload)
        doc["revision"] = int(doc.get("revision", 0)) + 1
        return doc

    sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    return get_candidate(wid, candidate_id)


def get_candidate(workspace_id: str, candidate_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    doc = _load(wid)
    item = _find(doc, candidate_id)
    if item is None:
        raise AppError(NOT_FOUND, "候选不存在", field="candidate_id")
    return _safe(item)


def list_candidates(
    workspace_id: str,
    profile_id: Optional[str] = None,
    state: Optional[str] = None,
    limit: int = 50,
    cursor: str = "0",
) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    if profile_id:
        workspace_context.assert_profile(wid, profile_id)
    if state is not None:
        validate_enum(state, CANDIDATE_STATES, "state")
    try:
        offset = int(str(cursor or "0"))
    except ValueError:
        raise AppError(INVALID_INPUT, "cursor 非法", field="cursor")
    if offset < 0:
        raise AppError(INVALID_INPUT, "cursor 非法", field="cursor")
    if limit < 1 or limit > 200:
        raise AppError(INVALID_INPUT, "limit 必须在 1–200", field="limit")
    doc = _load(wid)
    rows = []
    for item in doc["items"]:
        if not isinstance(item, dict):
            continue
        if profile_id and item.get("profile_id") != profile_id:
            continue
        if state and item.get("state") != state:
            continue
        rows.append(_safe(item))
    rows.sort(key=lambda row: (str(row.get("created_at") or ""), row["candidate_id"]), reverse=True)
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "items": page,
        "total": total,
        "next_cursor": str(offset + limit) if offset + limit < total else None,
    }
