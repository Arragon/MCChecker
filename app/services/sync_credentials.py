"""M16-19 候选账密验证、CAS 激活/轮换/撤销与 auth_epoch 竞态。

主应用只保存**非敏感元数据**（`sync/credentials.json`）：状态、generation、
auth_epoch、候选 id、revision。账密明文字节交给调用方注入的 `vault`（生产由
独立 collector 的 SecretStore 实现，密文根在 data/ 之外）；本模块不提供明文
回退，也不把 secret 写进 metadata/异常/日志。

竞态边界：
- begin 时给候选记下当时的 `auth_epoch`；activate 必须带同一个 epoch，晚到的
  probe、已被撤销/轮换的候选一律 `SOURCE_CHANGED`，不能激活或发布；
- 错密码/锁定/表单变化只把候选标 failed 并锁存，**不替换**原 active 密文；
- revoke 先删密文再写元数据；删除失败抛 `STORAGE_FAILURE`，保持 active 状态，
  绝不返回「已撤销」；
- 每次激活/锁定/撤销都递增 auth_epoch，collector 在覆盖 Cookie 前用
  `assert_epoch` 复核，旧 epoch 不能覆盖新会话。
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime
from typing import Any, Dict, Optional

from app.core.errors import (
    INVALID_INPUT,
    NOT_FOUND,
    REVISION_CONFLICT,
    SOURCE_CHANGED,
    STORAGE_FAILURE,
    AppError,
)
from app.core.sync_models import AUTH_STATES, validate_id, validate_revision, validate_workspace_id

from . import sync_connections, sync_store

FILE = "credentials.json"
DEFAULT_DOC: Dict[str, Any] = {"items": {}}
LOCK_REASONS = frozenset({"bad_credentials", "locked", "login_flow_changed"})
FAIL_REASONS = frozenset(
    {
        "bad_credentials",
        "locked",
        "login_flow_changed",
        "permission_denied",
        "network_error",
        "auth_failed",
        "protocol_mismatch",
    }
)

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_secret(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or "\x00" in value:
        raise AppError(INVALID_INPUT, "%s 非法" % name, field=name)
    return value


def _default_record(connection_id: str) -> Dict[str, Any]:
    return {
        "connection_id": connection_id,
        "state": "unconfigured",
        "auth_epoch": 0,
        "active_generation": None,
        "revision": 0,
        "candidate": None,
        "last_candidate_id": None,
        "last_error": None,
        "last_activated_at": None,
        "last_revoked_at": None,
    }


def _load(workspace_id: str) -> Dict[str, Any]:
    doc = sync_store.read_doc(workspace_id, FILE, DEFAULT_DOC)
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), dict):
        raise AppError(INVALID_INPUT, "credentials.json 形状损坏", field="credentials")
    return doc


def _record(doc: Dict[str, Any], connection_id: str) -> Optional[Dict[str, Any]]:
    value = doc["items"].get(connection_id)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AppError(INVALID_INPUT, "凭据元数据损坏", field="credentials")
    record = _default_record(connection_id)
    record.update(value)
    return record


def _safe(record: Dict[str, Any]) -> Dict[str, Any]:
    candidate = record.get("candidate")
    safe_candidate = None
    if isinstance(candidate, dict):
        safe_candidate = {
            "candidate_id": candidate.get("candidate_id"),
            "generation": candidate.get("generation"),
            "auth_epoch": candidate.get("auth_epoch"),
            "state": candidate.get("state"),
            "created_at": candidate.get("created_at"),
            "reason_code": candidate.get("reason_code"),
        }
    return {
        "connection_id": record["connection_id"],
        "state": record.get("state", "unconfigured"),
        "auth_epoch": int(record.get("auth_epoch") or 0),
        "active_generation": record.get("active_generation"),
        "revision": int(record.get("revision") or 0),
        "candidate": safe_candidate,
        "last_candidate_id": record.get("last_candidate_id"),
        "last_error": record.get("last_error"),
        "last_activated_at": record.get("last_activated_at"),
        "last_revoked_at": record.get("last_revoked_at"),
    }


def _check_connection(workspace_id: str, connection_id: str) -> Dict[str, Any]:
    return sync_connections.get_connection(workspace_id, connection_id)


def get_credential_state(workspace_id: str, connection_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    cid = validate_id(connection_id, "connection_id")
    record = _record(_load(wid), cid)
    return _safe(record or _default_record(cid))


def _next_generation(record: Dict[str, Any]) -> int:
    values = [int(record.get("active_generation") or 0)]
    candidate = record.get("candidate")
    if isinstance(candidate, dict):
        values.append(int(candidate.get("generation") or 0))
    return max(values) + 1


def _put_secret(vault: Any, workspace_id: str, connection_id: str, generation: int, username: str, password: str) -> None:
    payload = json.dumps(
        {"username": username, "password": password},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        vault.put(workspace_id, connection_id, generation, payload)
    except Exception as exc:
        logger.warning("凭据密文写入失败（%s）", type(exc).__name__)
        raise AppError(STORAGE_FAILURE, "无法写入凭据密文", field="credentials")


def _delete_secret(vault: Any, workspace_id: str, connection_id: str, generation: Optional[int]) -> bool:
    if generation is None:
        return True
    try:
        return bool(vault.delete(workspace_id, connection_id, int(generation)))
    except Exception as exc:
        logger.warning("凭据密文删除失败（%s）", type(exc).__name__)
        raise AppError(STORAGE_FAILURE, "撤销失败，凭据仍保留", field="credentials")


def begin_candidate(
    workspace_id: str,
    connection_id: str,
    username: Any,
    password: Any,
    vault: Any,
    expected_revision: Optional[Any] = None,
) -> Dict[str, Any]:
    """写入新一代候选密文并返回 pending 元数据；不改变当前 active 引用。"""
    wid = validate_workspace_id(workspace_id)
    cid = validate_id(connection_id, "connection_id")
    _check_connection(wid, cid)
    account = _validate_secret(username, "username", 256)
    secret = _validate_secret(password, "password", 1024)

    current = _record(_load(wid), cid) or _default_record(cid)
    expected = (
        validate_revision(expected_revision, "expected_revision")
        if expected_revision is not None
        else int(current.get("revision") or 0)
    )
    generation = _next_generation(current)
    candidate_id = "cred-" + secrets.token_hex(8)
    now = _now()
    _put_secret(vault, wid, cid, generation, account, secret)
    old_candidate = current.get("candidate") if isinstance(current.get("candidate"), dict) else None

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        record = _record(doc, cid) or _default_record(cid)
        if int(record.get("revision") or 0) != expected:
            raise AppError(REVISION_CONFLICT, "凭据元数据 revision 已变化", field="expected_revision")
        if _next_generation(record) > generation:
            raise AppError(REVISION_CONFLICT, "已有更新的凭据代次", field="generation")
        record["candidate"] = {
            "candidate_id": candidate_id,
            "generation": generation,
            "auth_epoch": int(record.get("auth_epoch") or 0),
            "state": "pending",
            "created_at": now,
            "reason_code": None,
        }
        record["revision"] = int(record.get("revision") or 0) + 1
        record["last_error"] = None
        doc["items"][cid] = record
        return doc

    try:
        updated = sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    except AppError:
        try:
            _delete_secret(vault, wid, cid, generation)
        except AppError:
            logger.warning("候选密文回滚失败：%s", generation)
        raise

    if old_candidate and int(old_candidate.get("generation") or 0) != generation:
        try:
            _delete_secret(vault, wid, cid, old_candidate.get("generation"))
        except AppError:
            logger.warning("旧候选密文清理失败：%s", generation)
    return _safe(_record(updated, cid))


def fail_candidate(
    workspace_id: str,
    connection_id: str,
    candidate_id: str,
    probe_epoch: Any,
    reason: str,
    vault: Any,
) -> Dict[str, Any]:
    """候选失败：错密码/锁定锁存并递增 epoch，绝不触碰 active 密文。"""
    wid = validate_workspace_id(workspace_id)
    cid = validate_id(connection_id, "connection_id")
    candidate = validate_id(candidate_id, "candidate_id")
    epoch = validate_revision(probe_epoch, "probe_epoch")
    code = str(reason or "").strip()
    if code not in FAIL_REASONS:
        raise AppError(INVALID_INPUT, "未知的登录失败原因", field="reason_code")
    now = _now()

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        record = _record(doc, cid)
        if record is None:
            raise AppError(NOT_FOUND, "凭据候选不存在", field="candidate_id")
        current = record.get("candidate")
        if not isinstance(current, dict) or current.get("candidate_id") != candidate:
            raise AppError(NOT_FOUND, "凭据候选不存在或已被替换", field="candidate_id")
        if current.get("state") != "pending":
            raise AppError(REVISION_CONFLICT, "凭据候选已结束", field="candidate_id")
        if int(record.get("auth_epoch") or 0) != epoch or int(current.get("auth_epoch") or 0) != epoch:
            raise AppError(SOURCE_CHANGED, "认证纪元已变化，晚到的失败结果被丢弃", field="probe_epoch")
        current["state"] = "failed"
        current["reason_code"] = code
        record["last_error"] = code
        if code in LOCK_REASONS:
            record["state"] = "locked"
            record["auth_epoch"] = epoch + 1
        record["revision"] = int(record.get("revision") or 0) + 1
        doc["items"][cid] = record
        return doc

    updated = sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    record = _record(updated, cid)
    try:
        _delete_secret(vault, wid, cid, record.get("candidate", {}).get("generation"))
    except AppError:
        # 候选已经持久化为 failed/locked；清理失败单独记账，不冒充 active 已替换。
        def mark_cleanup_failure(doc: Dict[str, Any]) -> Dict[str, Any]:
            latest = _record(doc, cid) or _default_record(cid)
            latest["last_error"] = "storage_failure"
            latest["revision"] = int(latest.get("revision") or 0) + 1
            doc["items"][cid] = latest
            return doc

        updated = sync_store.update_doc(wid, FILE, mark_cleanup_failure, DEFAULT_DOC)
        record = _record(updated, cid)
    return _safe(record)


def activate_candidate(
    workspace_id: str,
    connection_id: str,
    candidate_id: str,
    probe_epoch: Any,
    vault: Any,
) -> Dict[str, Any]:
    """CAS 激活候选并轮换 active generation；晚到/旧 epoch 一律拒绝。"""
    wid = validate_workspace_id(workspace_id)
    cid = validate_id(connection_id, "connection_id")
    candidate = validate_id(candidate_id, "candidate_id")
    epoch = validate_revision(probe_epoch, "probe_epoch")
    now = _now()
    previous: Dict[str, Any] = {}

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        record = _record(doc, cid)
        if record is None:
            raise AppError(NOT_FOUND, "凭据候选不存在", field="candidate_id")
        current = record.get("candidate")
        if not isinstance(current, dict) or current.get("candidate_id") != candidate:
            raise AppError(NOT_FOUND, "凭据候选不存在或已被替换", field="candidate_id")
        if current.get("state") != "pending":
            raise AppError(REVISION_CONFLICT, "凭据候选已结束", field="candidate_id")
        if int(record.get("auth_epoch") or 0) != epoch or int(current.get("auth_epoch") or 0) != epoch:
            raise AppError(SOURCE_CHANGED, "认证纪元已变化，晚到的 probe 不能激活", field="probe_epoch")
        previous["generation"] = record.get("active_generation")
        record["active_generation"] = int(current.get("generation"))
        record["state"] = "valid"
        record["auth_epoch"] = epoch + 1
        record["last_candidate_id"] = candidate
        record["last_activated_at"] = now
        record["last_error"] = None
        record["candidate"] = None
        record["revision"] = int(record.get("revision") or 0) + 1
        doc["items"][cid] = record
        return doc

    updated = sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    record = _record(updated, cid)
    old_generation = previous.get("generation")
    if old_generation is not None and int(old_generation) != int(record.get("active_generation") or 0):
        try:
            _delete_secret(vault, wid, cid, old_generation)
        except AppError:
            logger.warning("旧凭据密文清理失败：%s", old_generation)
    return _safe(record)


def revoke_credential(
    workspace_id: str,
    connection_id: str,
    expected_revision: Any,
    vault: Any,
) -> Dict[str, Any]:
    """删除 active/候选密文后写元数据；删除失败保持 active 并报 STORAGE_FAILURE。"""
    wid = validate_workspace_id(workspace_id)
    cid = validate_id(connection_id, "connection_id")
    expected = validate_revision(expected_revision, "expected_revision")
    now = _now()

    with sync_store.workspace_lock(wid):
        doc = _load(wid)
        record = _record(doc, cid)
        if record is None:
            return _safe(_default_record(cid))
        if int(record.get("revision") or 0) != expected:
            raise AppError(REVISION_CONFLICT, "凭据元数据 revision 已变化", field="expected_revision")
        candidate = record.get("candidate") if isinstance(record.get("candidate"), dict) else None
        _delete_secret(vault, wid, cid, candidate.get("generation") if candidate else None)
        _delete_secret(vault, wid, cid, record.get("active_generation"))

        def mutate(inner: Dict[str, Any]) -> Dict[str, Any]:
            latest = _record(inner, cid) or _default_record(cid)
            latest["state"] = "unconfigured"
            latest["active_generation"] = None
            latest["candidate"] = None
            latest["auth_epoch"] = int(latest.get("auth_epoch") or 0) + 1
            latest["last_error"] = None
            latest["last_revoked_at"] = now
            latest["revision"] = int(latest.get("revision") or 0) + 1
            inner["items"][cid] = latest
            return inner

        updated = sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    return _safe(_record(updated, cid))


def assert_epoch(workspace_id: str, connection_id: str, epoch: Any) -> Dict[str, Any]:
    """collector 覆盖 Cookie/会话前复核；旧 epoch 不能覆盖新会话。"""
    wid = validate_workspace_id(workspace_id)
    cid = validate_id(connection_id, "connection_id")
    wanted = validate_revision(epoch, "auth_epoch")
    record = _record(_load(wid), cid) or _default_record(cid)
    if int(record.get("auth_epoch") or 0) != wanted:
        raise AppError(
            SOURCE_CHANGED,
            "认证纪元已变化，旧会话不能覆盖新会话",
            expected=wanted,
            current=int(record.get("auth_epoch") or 0),
        )
    return _safe(record)
