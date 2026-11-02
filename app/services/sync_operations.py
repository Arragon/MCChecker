"""M16-30 凭据操作记录：workspace 级异步状态与安全结果。

`sync/operations.json` 只保存 `op_id`、连接、kind、state、时间戳、revision、
安全 result 与 reason_code；password/Cookie/CSRF/token 一律不进入该文件。
probe/vault 由调用方注入：本模块不联网、不接触账密明文，也没有“假成功”的
默认路径——未配置 probe 时操作停在 `blocked/collector_offline`。

幂等：可选 `idempotency_key`。同一 key + 同一 kind/连接/payload 返回同一操作；
同 key 不同请求抛 `IDEMPOTENCY_MISMATCH`（409）。幂等窗口受 `OPERATION_LIMIT`
约束，最旧记录会被裁剪。
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

from app.core.errors import (
    IDEMPOTENCY_MISMATCH,
    INVALID_INPUT,
    NOT_FOUND,
    REVISION_CONFLICT,
    SOURCE_CHANGED,
    STORAGE_FAILURE,
    AppError,
)
from app.core.sync_models import (
    validate_enum,
    validate_id,
    validate_reason_code,
    validate_revision,
    validate_safe_details,
    validate_workspace_id,
)

from . import sync_connections, sync_credentials, sync_heartbeat, sync_store

FILE = "operations.json"
DEFAULT_DOC: Dict[str, Any] = {"items": []}
OPERATION_LIMIT = 500
KINDS = ("credential_probe", "credential_activate", "credential_revoke", "heartbeat_run")
STATES = ("queued", "running", "succeeded", "failed", "blocked")
TERMINAL_STATES = frozenset({"succeeded", "failed", "blocked"})
PAYLOAD_KEYS = {
    "credential_probe": frozenset({"candidate_id", "probe_epoch"}),
    "credential_activate": frozenset({"candidate_id", "probe_epoch"}),
    "credential_revoke": frozenset({"expected_revision"}),
    "heartbeat_run": frozenset(),
}

logger = logging.getLogger(__name__)

ProbeCallable = Callable[[str, str, str], Dict[str, Any]]


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(workspace_id: str) -> Dict[str, Any]:
    doc = sync_store.read_doc(workspace_id, FILE, DEFAULT_DOC)
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        raise AppError(INVALID_INPUT, "operations.json 形状损坏", field="operations")
    return doc


def _find(doc: Dict[str, Any], op_id: str) -> Optional[Dict[str, Any]]:
    for item in doc["items"]:
        if isinstance(item, dict) and item.get("op_id") == op_id:
            return item
    return None


def _safe_credential(workspace_id: str, connection_id: str) -> Dict[str, Any]:
    try:
        return sync_credentials.get_credential_state(workspace_id, connection_id)
    except AppError:
        return {}


def _safe_result(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: Dict[str, Any] = {}
    credential = value.get("credential")
    if isinstance(credential, dict):
        result["credential"] = dict(credential)
    probe = value.get("probe")
    if probe in ("succeeded", "failed", "blocked"):
        result["probe"] = probe
    heartbeat = value.get("heartbeat")
    if isinstance(heartbeat, dict):
        result["heartbeat"] = dict(heartbeat)
    candidate_id = value.get("candidate_id")
    if isinstance(candidate_id, str):
        result["candidate_id"] = candidate_id
    return result


def _safe(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "op_id": item["op_id"],
        "workspace_id": item["workspace_id"],
        "connection_id": item["connection_id"],
        "kind": item["kind"],
        "state": item["state"],
        "created_at": item.get("created_at"),
        "started_at": item.get("started_at"),
        "finished_at": item.get("finished_at"),
        "reason_code": item.get("reason_code"),
        "revision": int(item.get("revision") or 0),
        "result": _safe_result(item.get("result")),
    }


def _request_hash(kind: str, connection_id: str, payload: Dict[str, Any]) -> str:
    """幂等指纹只覆盖客户端可决定的请求面；probe 的内部 epoch 不参与。"""
    material = payload
    if kind == "credential_probe":
        material = {"candidate_id": payload.get("candidate_id")}
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw = "%s\x1f%s\x1f%s" % (kind, connection_id, encoded)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validated_payload(kind: str, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = validate_safe_details({} if payload is None else payload, "payload")
    unknown = set(data) - PAYLOAD_KEYS[kind]
    if unknown:
        raise AppError(INVALID_INPUT, "操作 payload 含未知字段", field="payload", keys=sorted(unknown))
    if kind in ("credential_probe", "credential_activate"):
        data["candidate_id"] = validate_id(data.get("candidate_id"), "candidate_id")
    if kind == "credential_probe":
        if data.get("probe_epoch") is not None:
            data["probe_epoch"] = validate_revision(data["probe_epoch"], "probe_epoch")
    elif kind == "credential_activate":
        data["probe_epoch"] = validate_revision(data.get("probe_epoch"), "probe_epoch")
    elif kind == "credential_revoke":
        data["expected_revision"] = validate_revision(data.get("expected_revision"), "expected_revision")
    return data


def create_operation(
    workspace_id: str,
    connection_id: str,
    kind: str,
    payload: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """登记操作（queued）；不执行、不接触 secret。"""
    wid = validate_workspace_id(workspace_id)
    cid = validate_id(connection_id, "connection_id")
    op_kind = validate_enum(kind, KINDS, "kind")
    data = _validated_payload(op_kind, payload)
    sync_connections.get_connection(wid, cid)
    key = validate_id(idempotency_key, "idempotency_key") if idempotency_key else None
    request_hash = _request_hash(op_kind, cid, data)
    created_op = "op-" + secrets.token_hex(8)
    target = {"op_id": created_op}
    now = _now()

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        if key:
            for existing in doc["items"]:
                if isinstance(existing, dict) and existing.get("idempotency_key") == key:
                    if existing.get("request_hash") != request_hash:
                        raise AppError(
                            IDEMPOTENCY_MISMATCH,
                            "幂等键已用于不同的操作请求",
                            field="idempotency_key",
                        )
                    target["op_id"] = existing["op_id"]
                    return doc
        doc["items"].append(
            {
                "op_id": created_op,
                "workspace_id": wid,
                "connection_id": cid,
                "kind": op_kind,
                "state": "queued",
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "reason_code": None,
                "revision": 1,
                "result": {},
                "payload": data,
                "idempotency_key": key,
                "request_hash": request_hash,
            }
        )
        if len(doc["items"]) > OPERATION_LIMIT:
            del doc["items"][:-OPERATION_LIMIT]
        return doc

    updated = sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    item = _find(updated, target["op_id"])
    if item is None:
        raise AppError(NOT_FOUND, "操作记录不存在", field="op_id")
    return _safe(item)


def get_operation(workspace_id: str, op_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    oid = validate_id(op_id, "op_id")
    item = _find(_load(wid), oid)
    if item is None:
        raise AppError(NOT_FOUND, "操作不存在", field="op_id")
    return _safe(item)


def _claim(workspace_id: str, op_id: str) -> Tuple[Dict[str, Any], bool]:
    """把 queued 操作原子地推进到 running；已结束/已在跑的不重复执行。

    返回 `(item, claimed)`：`claimed=False` 时 item 是安全投影，调用方直接返回；
    `claimed=True` 时 item 是内部记录（含 payload），只在本进程内使用。
    """
    with sync_store.workspace_lock(workspace_id):
        doc = _load(workspace_id)
        item = _find(doc, op_id)
        if item is None:
            raise AppError(NOT_FOUND, "操作不存在", field="op_id")
        if item.get("state") != "queued":
            return _safe(item), False
        item["state"] = "running"
        item["started_at"] = _now()
        item["revision"] = int(item.get("revision") or 0) + 1
        sync_store.write_doc(workspace_id, FILE, doc)
        return dict(item), True


def _finish(
    workspace_id: str,
    op_id: str,
    state: str,
    reason_code: Optional[str],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    final_state = validate_enum(state, STATES, "state")
    reason = validate_reason_code(reason_code)
    safe_payload = validate_safe_details(_safe_result(result), "result")
    now = _now()

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        item = _find(doc, op_id)
        if item is None:
            raise AppError(NOT_FOUND, "操作不存在", field="op_id")
        if item.get("state") in TERMINAL_STATES:
            return doc
        item["state"] = final_state
        item["reason_code"] = reason
        item["result"] = safe_payload
        item["finished_at"] = now
        item["revision"] = int(item.get("revision") or 0) + 1
        return doc

    updated = sync_store.update_doc(workspace_id, FILE, mutate, DEFAULT_DOC)
    item = _find(updated, op_id)
    if item is None:
        raise AppError(NOT_FOUND, "操作不存在", field="op_id")
    return _safe(item)


def _reason_for_error(exc: AppError) -> str:
    return {
        SOURCE_CHANGED: "source_changed",
        REVISION_CONFLICT: "revision_conflict",
        NOT_FOUND: "not_found",
        STORAGE_FAILURE: "storage_failure",
        INVALID_INPUT: "invalid_payload",
    }.get(exc.code, "unknown")


def _probe_result(
    workspace_id: str,
    connection_id: str,
    payload: Dict[str, Any],
    vault: Any,
    probe: Optional[ProbeCallable],
) -> Tuple[str, Optional[str], Dict[str, Any]]:
    candidate_id = payload["candidate_id"]
    state = sync_credentials.get_credential_state(workspace_id, connection_id)
    candidate = state.get("candidate")
    if not isinstance(candidate, dict) or candidate.get("candidate_id") != candidate_id:
        return "failed", "not_found", {
            "credential": state,
            "probe": "failed",
            "candidate_id": candidate_id,
        }
    if state.get("state") == "locked":
        # 错密码已锁存：不再调用任何凭据提供方，避免重复尝试。
        return "blocked", "locked", {
            "credential": state,
            "probe": "blocked",
            "candidate_id": candidate_id,
        }
    if candidate.get("state") != "pending":
        reason = str(candidate.get("reason_code") or state.get("last_error") or "unknown")
        if reason not in sync_credentials.FAIL_REASONS:
            reason = "unknown"
        return "blocked", reason, {
            "credential": state,
            "probe": "blocked",
            "candidate_id": candidate_id,
        }
    epoch = int(candidate.get("auth_epoch") or 0)
    expected_epoch = payload.get("probe_epoch")
    if expected_epoch is not None and int(expected_epoch) != epoch:
        return "blocked", "source_changed", {
            "credential": state,
            "probe": "blocked",
            "candidate_id": candidate_id,
        }
    if probe is None:
        return "blocked", "collector_offline", {
            "credential": state,
            "probe": "blocked",
            "candidate_id": candidate_id,
        }

    try:
        outcome = probe(workspace_id, connection_id, candidate_id)
    except Exception:
        logger.exception("凭据 probe 调用失败（connection=%s）", connection_id)
        return "failed", "unknown", {
            "credential": state,
            "probe": "failed",
            "candidate_id": candidate_id,
        }
    if not isinstance(outcome, dict):
        return "blocked", "collector_offline", {
            "credential": state,
            "probe": "blocked",
            "candidate_id": candidate_id,
        }

    if outcome.get("ok") is True:
        latest = sync_credentials.get_credential_state(workspace_id, connection_id)
        latest_candidate = latest.get("candidate")
        if not isinstance(latest_candidate, dict) or latest_candidate.get("candidate_id") != candidate_id:
            return "blocked", "source_changed", {
                "credential": latest,
                "probe": "blocked",
                "candidate_id": candidate_id,
            }
        return "succeeded", None, {
            "credential": latest,
            "probe": "succeeded",
            "candidate_id": candidate_id,
        }

    reason = str(outcome.get("reason_code") or "auth_failed").strip()
    if outcome.get("blocked") is True or reason == "collector_offline":
        if reason not in sync_credentials.FAIL_REASONS and reason != "collector_offline":
            reason = "collector_offline"
        return "blocked", reason, {
            "credential": state,
            "probe": "blocked",
            "candidate_id": candidate_id,
        }
    if reason not in sync_credentials.FAIL_REASONS:
        reason = "auth_failed"
    if vault is None:
        return "failed", "storage_failure", {
            "credential": state,
            "probe": "failed",
            "candidate_id": candidate_id,
        }
    try:
        latest = sync_credentials.fail_candidate(
            workspace_id, connection_id, candidate_id, epoch, reason, vault
        )
    except AppError as exc:
        return "failed", _reason_for_error(exc), {
            "credential": _safe_credential(workspace_id, connection_id),
            "probe": "failed",
            "candidate_id": candidate_id,
        }
    return "failed", reason, {
        "credential": latest,
        "probe": "failed",
        "candidate_id": candidate_id,
    }


def _activate_result(
    workspace_id: str,
    connection_id: str,
    payload: Dict[str, Any],
    vault: Any,
) -> Tuple[str, Optional[str], Dict[str, Any]]:
    candidate_id = payload["candidate_id"]
    if vault is None:
        return "blocked", "collector_offline", {
            "credential": _safe_credential(workspace_id, connection_id),
            "candidate_id": candidate_id,
        }
    try:
        latest = sync_credentials.activate_candidate(
            workspace_id,
            connection_id,
            candidate_id,
            payload["probe_epoch"],
            vault,
        )
    except AppError as exc:
        return "failed", _reason_for_error(exc), {
            "credential": _safe_credential(workspace_id, connection_id),
            "candidate_id": candidate_id,
        }
    return "succeeded", None, {"credential": latest, "candidate_id": candidate_id}


def _revoke_result(
    workspace_id: str,
    connection_id: str,
    payload: Dict[str, Any],
    vault: Any,
) -> Tuple[str, Optional[str], Dict[str, Any]]:
    if vault is None:
        return "blocked", "collector_offline", {
            "credential": _safe_credential(workspace_id, connection_id)
        }
    try:
        latest = sync_credentials.revoke_credential(
            workspace_id, connection_id, payload["expected_revision"], vault
        )
    except AppError as exc:
        return "failed", _reason_for_error(exc), {
            "credential": _safe_credential(workspace_id, connection_id)
        }
    return "succeeded", None, {"credential": latest}


def _heartbeat_result(
    workspace_id: str,
    connection_id: str,
    heartbeat_probe: Optional[Callable[[str, str], Dict[str, Any]]],
) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """保活运行：未启用/未注入 probe 都 blocked，绝不伪造成功。"""
    state = sync_heartbeat.get_state(workspace_id, connection_id)
    if not state.get("enabled"):
        return "blocked", "disabled", {"heartbeat": state}
    if heartbeat_probe is None:
        return "blocked", "collector_offline", {"heartbeat": state}
    try:
        outcome = sync_heartbeat.run_heartbeat(
            workspace_id,
            connection_id,
            lambda: heartbeat_probe(workspace_id, connection_id),
        )
    except Exception:
        logger.exception("心跳运行失败（connection=%s）", connection_id)
        return "failed", "network_error", {
            "heartbeat": sync_heartbeat.get_state(workspace_id, connection_id)
        }
    status = str(outcome.get("status") or "")
    latest = outcome.get("state") or sync_heartbeat.get_state(workspace_id, connection_id)
    if status == "succeeded":
        return "succeeded", None, {"heartbeat": latest}
    if status == "failed":
        reason = str(outcome.get("reason_code") or "auth_failed")
        if reason not in sync_credentials.FAIL_REASONS:
            reason = "auth_failed"
        return "failed", reason, {"heartbeat": latest}
    if status == "skipped":
        return "blocked", "disabled", {"heartbeat": latest}
    if status == "busy":
        return "blocked", "interrupted", {"heartbeat": latest}
    if status == "conflict":
        return "blocked", "revision_conflict", {"heartbeat": latest}
    return "blocked", "collector_offline", {"heartbeat": latest}


def _failure_result(workspace_id: str, connection_id: str, kind: str) -> Dict[str, Any]:
    if kind == "heartbeat_run":
        try:
            return {"heartbeat": sync_heartbeat.get_state(workspace_id, connection_id)}
        except AppError:
            return {}
    return {"credential": _safe_credential(workspace_id, connection_id)}


def run_operation(
    workspace_id: str,
    op_id: str,
    probe: Optional[ProbeCallable] = None,
    vault: Any = None,
    heartbeat_probe: Optional[Callable[[str, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """同步执行一个操作（测试/单进程部署用）；重复调用不会重复执行。"""
    wid = validate_workspace_id(workspace_id)
    oid = validate_id(op_id, "op_id")
    item, claimed = _claim(wid, oid)
    if not claimed:
        return item

    kind = item["kind"]
    connection_id = item["connection_id"]
    payload = item.get("payload")
    if not isinstance(payload, dict):
        return _finish(wid, oid, "failed", "invalid_payload", {})
    try:
        if kind == "credential_probe":
            state, reason, result = _probe_result(wid, connection_id, payload, vault, probe)
        elif kind == "credential_activate":
            state, reason, result = _activate_result(wid, connection_id, payload, vault)
        elif kind == "credential_revoke":
            state, reason, result = _revoke_result(wid, connection_id, payload, vault)
        elif kind == "heartbeat_run":
            state, reason, result = _heartbeat_result(wid, connection_id, heartbeat_probe)
        else:
            state, reason, result = "failed", "unknown", {}
    except AppError as exc:
        state, reason = "failed", _reason_for_error(exc)
        result = _failure_result(wid, connection_id, kind)
    except Exception:
        logger.exception("同步操作执行失败（kind=%s）", kind)
        state, reason = "failed", "unknown"
        result = _failure_result(wid, connection_id, kind)
    return _finish(wid, oid, state, reason, result)
