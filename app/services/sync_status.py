"""M16-25 last-good / SourceFreshness 持久状态。

只记录真实证据：
- attempt / verified / commit 分开；保活、登录 canary、匿名目录成功不得调用
  `record_verified`/`record_commit`。
- 只有真实提交回执才推进 last_good；unchanged 只刷新 verified。
- 人工修改 current 后通过 `reconcile_current` 标记 manual，不覆盖 last_good，
  也不把 last_good 冒充当前内容。
- receipt 不匹配/缺失 -> unknown，不补造成功时间。

本模块不读文件、不网络、不发布 D0/Bn/rn；文件/记录提交由 M16-24 负责。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.errors import INVALID_INPUT, AppError
from app.core.sync_models import (
    FRESHNESS_ORIGINS,
    SourceFreshness,
    validate_enum,
    validate_id,
    validate_profile_id,
    validate_reason_code,
    validate_safe_details,
    validate_sha256,
    validate_timestamp,
    validate_workspace_id,
)

from . import sync_store

FILE = "status.json"
DEFAULT_DOC: Dict[str, Any] = {"revision": 0, "sources": {}}


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _key(profile_id: str, source_id: str) -> str:
    return validate_profile_id(profile_id) + "\x1f" + validate_id(source_id, "source_id")


def _load(workspace_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    doc = sync_store.read_doc(wid, FILE, DEFAULT_DOC)
    if not isinstance(doc, dict) or not isinstance(doc.get("sources", {}), dict):
        raise AppError(INVALID_INPUT, "status.json 形状损坏", field="status")
    doc.setdefault("revision", 0)
    doc.setdefault("sources", {})
    return doc


def _default(workspace_id: str, profile_id: str, source_id: str) -> Dict[str, Any]:
    return SourceFreshness(
        workspace_id=workspace_id,
        profile_id=profile_id,
        source_id=source_id,
    ).to_dict()


def _normalize(record: Dict[str, Any]) -> Dict[str, Any]:
    known = {
        key: record.get(key)
        for key in (
            "workspace_id",
            "profile_id",
            "source_id",
            "last_attempt_at",
            "last_verified_at",
            "last_commit_at",
            "last_good_sha",
            "last_good_version_id",
            "source_version",
            "current_origin",
            "reason_code",
        )
    }
    normalized = SourceFreshness(**known).to_dict()
    normalized["available"] = normalized["last_good_sha"] is not None
    if "receipt" in record:
        normalized["receipt"] = validate_safe_details(record["receipt"], "receipt")
    return normalized


def get_status(workspace_id: str, profile_id: str, source_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    key = _key(profile_id, source_id)
    doc = _load(wid)
    record = doc["sources"].get(key)
    if not isinstance(record, dict):
        return _normalize(_default(wid, profile_id, source_id))
    return _normalize(record)


def list_status(workspace_id: str, profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
    wid = validate_workspace_id(workspace_id)
    doc = _load(wid)
    result = []
    for record in doc["sources"].values():
        if not isinstance(record, dict):
            continue
        if profile_id and record.get("profile_id") != profile_id:
            continue
        result.append(_normalize(record))
    result.sort(key=lambda row: (row["profile_id"], row["source_id"]))
    return result


def _update(workspace_id: str, profile_id: str, source_id: str, mutator) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    key = _key(profile_id, source_id)

    def apply(doc: Dict[str, Any]) -> Dict[str, Any]:
        current = doc["sources"].get(key)
        if not isinstance(current, dict):
            current = _default(wid, profile_id, source_id)
        updated = _normalize(mutator(dict(current)))
        doc["sources"][key] = updated
        doc["revision"] = int(doc.get("revision", 0)) + 1
        return doc

    sync_store.update_doc(wid, FILE, apply, DEFAULT_DOC)
    return get_status(wid, profile_id, source_id)


def record_attempt(
    workspace_id: str,
    profile_id: str,
    source_id: str,
    at: Optional[str] = None,
    reason_code: Optional[str] = None,
) -> Dict[str, Any]:
    moment = validate_timestamp(at or _now(), "at", allow_none=False)
    reason = validate_reason_code(reason_code)

    def mutate(record: Dict[str, Any]) -> Dict[str, Any]:
        record["last_attempt_at"] = moment
        record["reason_code"] = reason
        return record

    return _update(workspace_id, profile_id, source_id, mutate)


def record_verified(
    workspace_id: str,
    profile_id: str,
    source_id: str,
    at: Optional[str] = None,
    sha256: Optional[str] = None,
    source_version: Optional[str] = None,
) -> Dict[str, Any]:
    moment = validate_timestamp(at or _now(), "at", allow_none=False)
    digest = validate_sha256(sha256, "sha256", allow_none=True)

    def mutate(record: Dict[str, Any]) -> Dict[str, Any]:
        record["last_verified_at"] = moment
        if digest is not None:
            record["reason_code"] = "validated"
        if source_version:
            record["source_version"] = str(source_version)
        return record

    return _update(workspace_id, profile_id, source_id, mutate)


def record_commit(
    workspace_id: str,
    profile_id: str,
    source_id: str,
    sha256: str,
    version_id: str,
    at: Optional[str] = None,
    source_version: Optional[str] = None,
    receipt: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    moment = validate_timestamp(at or _now(), "at", allow_none=False)
    digest = validate_sha256(sha256, "sha256")
    version = validate_id(version_id, "version_id")
    safe_receipt = validate_safe_details(receipt or {}, "receipt")

    def mutate(record: Dict[str, Any]) -> Dict[str, Any]:
        record["last_commit_at"] = moment
        record["last_verified_at"] = moment
        record["last_good_sha"] = digest
        record["last_good_version_id"] = version
        record["current_origin"] = "remote"
        record["reason_code"] = "validated"
        if source_version:
            record["source_version"] = str(source_version)
        record["receipt"] = safe_receipt
        return record

    return _update(workspace_id, profile_id, source_id, mutate)


def record_manual(
    workspace_id: str,
    profile_id: str,
    source_id: str,
    current_sha256: Optional[str],
    at: Optional[str] = None,
) -> Dict[str, Any]:
    moment = validate_timestamp(at or _now(), "at", allow_none=False)
    digest = validate_sha256(current_sha256, "current_sha256", allow_none=True)

    def mutate(record: Dict[str, Any]) -> Dict[str, Any]:
        record["current_origin"] = "manual"
        record["last_attempt_at"] = moment
        if digest is not None and digest != record.get("last_good_sha"):
            record["reason_code"] = "source_changed"
        else:
            record["reason_code"] = "validated" if digest is not None else "unknown"
        return record

    return _update(workspace_id, profile_id, source_id, mutate)


def reconcile_current(
    workspace_id: str,
    profile_id: str,
    source_id: str,
    current_sha256: Optional[str],
) -> Dict[str, Any]:
    digest = validate_sha256(current_sha256, "current_sha256", allow_none=True)
    return _update(
        workspace_id,
        profile_id,
        source_id,
        lambda record: dict(
            record,
            current_origin="manual",
            reason_code=(
                "source_changed"
                if digest is not None and digest != record.get("last_good_sha")
                else record.get("reason_code") or "unknown"
            ),
        ),
    )


def recover_from_receipt(
    workspace_id: str,
    profile_id: str,
    source_id: str,
    receipt: Dict[str, Any],
    current_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    safe = validate_safe_details(receipt, "receipt")
    expected = safe.get("sha256") or safe.get("blob_sha256")
    if expected and current_sha256 and expected == current_sha256:
        return _update(
            workspace_id,
            profile_id,
            source_id,
            lambda record: dict(
                record,
                last_good_sha=expected,
                last_good_version_id=safe.get("version_id"),
                current_origin="remote",
                reason_code="validated",
                receipt=safe,
            ),
        )
    return _update(
        workspace_id,
        profile_id,
        source_id,
        lambda record: dict(
            record,
            current_origin="unknown",
            reason_code="unknown",
            receipt=safe,
        ),
    )
