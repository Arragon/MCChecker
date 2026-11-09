"""M16-20 会话保活、续期证据与 Cookie/CSRF 轮换边界。

主应用只维护**健康元数据**（`sync/heartbeat.json`）：上次心跳、上次续期活动、
下次到期、结果与 reason_code；真正的浏览器 context/Cookie/CSRF 轮换在独立
collector 里由同一 `BrowserContext` 完成，本模块不接触 Cookie 值。

硬边界：
- 一次成功 probe 只证明「现在还能取数」，**不**自动把 `heartbeat_validated`
  置真；只有显式 E3 真实 idle 证据（`record_idle_evidence`）才能置真；
- 心跳成功不写 `sync/status.json` 的 `last_verified_at`，也不递增 files；
- 同一连接最多一个心跳（进程内 non-blocking 锁；并发第二请求返回 busy）；
- 间隔 1–60 分钟；`next_due = last_renewing_activity + interval`，没有续期
  活动时用本次时间 + interval 兜底，避免扫描风暴；
- 关闭保活后 `run_heartbeat` 直接 skipped，不保留隐藏循环。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple

from app.core.errors import (
    INVALID_INPUT,
    REVISION_CONFLICT,
    SOURCE_CHANGED,
    AppError,
)
from app.core.sync_models import validate_id, validate_revision, validate_workspace_id

from . import sync_connections, sync_store

FILE = "heartbeat.json"
DEFAULT_DOC: Dict[str, Any] = {"items": {}}
MIN_INTERVAL = 1
MAX_INTERVAL = 60
DEFAULT_INTERVAL = 10
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


logger = logging.getLogger(__name__)

_LOCKS: Dict[Tuple[str, str], threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.utcnow().strftime(_TIME_FORMAT)


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), _TIME_FORMAT)
    except ValueError:
        return None


def _default(connection_id: str) -> Dict[str, Any]:
    return {
        "connection_id": connection_id,
        "enabled": False,
        "auto_relogin": True,
        "interval_minutes": DEFAULT_INTERVAL,
        "last_heartbeat_at": None,
        "last_renewing_activity": None,
        "next_due_at": None,
        "heartbeat_validated": False,
        "validated_at": None,
        "evidence": {},
        "last_result": None,
        "reason_code": None,
        "revision": 0,
    }


def _load(workspace_id: str) -> Dict[str, Any]:
    doc = sync_store.read_doc(workspace_id, FILE, DEFAULT_DOC)
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), dict):
        raise AppError(INVALID_INPUT, "heartbeat.json 形状损坏", field="heartbeat")
    return doc


def _record(doc: Dict[str, Any], connection_id: str) -> Optional[Dict[str, Any]]:
    value = doc["items"].get(connection_id)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AppError(INVALID_INPUT, "心跳元数据损坏", field="heartbeat")
    record = _default(connection_id)
    record.update(value)
    return record


def _safe(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "connection_id": record["connection_id"],
        "enabled": bool(record.get("enabled")),
        "auto_relogin": bool(record.get("auto_relogin", True)),
        "interval_minutes": int(record.get("interval_minutes") or DEFAULT_INTERVAL),
        "last_heartbeat_at": record.get("last_heartbeat_at"),
        "last_renewing_activity": record.get("last_renewing_activity"),
        "next_due_at": record.get("next_due_at"),
        "heartbeat_validated": bool(record.get("heartbeat_validated")),
        "validated_at": record.get("validated_at"),
        "evidence": dict(record.get("evidence") or {}),
        "last_result": record.get("last_result"),
        "reason_code": record.get("reason_code"),
        "revision": int(record.get("revision") or 0),
    }


def _lock_for(workspace_id: str, connection_id: str) -> threading.Lock:
    key = (workspace_id, connection_id)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def _probe_outcome(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        status = str(result.get("status") or "")
        reason = result.get("reason_code")
        session_valid = result.get("session_valid")
        renewal = result.get("renewal_observed")
    else:
        status = str(getattr(result, "status", "") or "")
        reason = getattr(result, "reason_code", None)
        session_valid = getattr(result, "session_valid", None)
        renewal = getattr(result, "renewal_observed", None)
    return {
        "success": status == "succeeded" and bool(session_valid),
        "status": status,
        "reason_code": str(reason) if reason else None,
        "renewal_observed": renewal is True,
    }


def get_state(workspace_id: str, connection_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    cid = validate_id(connection_id, "connection_id")
    record = _record(_load(wid), cid)
    return _safe(record or _default(cid))


def configure(
    workspace_id: str,
    connection_id: str,
    interval_minutes: Any,
    enabled: Any,
    expected_revision: Any,
    auto_relogin: Any = None,
) -> Dict[str, Any]:
    """保存保活开关/间隔，以及独立的自动重登开关；不改 heartbeat_validated。

    `auto_relogin is None` 表示这次请求不改该开关。关闭保活不会顺带关掉自动重登。
    """
    wid = validate_workspace_id(workspace_id)
    cid = validate_id(connection_id, "connection_id")
    sync_connections.get_connection(wid, cid)
    if isinstance(interval_minutes, bool) or not isinstance(interval_minutes, int):
        raise AppError(INVALID_INPUT, "interval_minutes 必须是整数", field="interval_minutes")
    if interval_minutes < MIN_INTERVAL or interval_minutes > MAX_INTERVAL:
        raise AppError(INVALID_INPUT, "interval_minutes 必须在 1–60", field="interval_minutes")
    if not isinstance(enabled, bool):
        raise AppError(INVALID_INPUT, "enabled 必须是布尔值", field="enabled")
    if auto_relogin is not None and not isinstance(auto_relogin, bool):
        raise AppError(INVALID_INPUT, "auto_relogin 必须是布尔值", field="auto_relogin")
    expected = validate_revision(expected_revision, "expected_revision")

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        record = _record(doc, cid) or _default(cid)
        if int(record.get("revision") or 0) != expected:
            raise AppError(REVISION_CONFLICT, "心跳配置 revision 已变化", field="expected_revision")
        record["enabled"] = enabled
        record["interval_minutes"] = interval_minutes
        if auto_relogin is not None:
            record["auto_relogin"] = auto_relogin
        elif "auto_relogin" not in record:
            record["auto_relogin"] = True
        if not enabled:
            record["next_due_at"] = None
        elif not record.get("next_due_at"):
            record["next_due_at"] = _now()
        record["revision"] = expected + 1
        doc["items"][cid] = record
        return doc

    updated = sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    return _safe(_record(updated, cid))


def run_heartbeat(
    workspace_id: str,
    connection_id: str,
    probe: Callable[[], Any],
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    """单连接 singleflight 心跳；返回 `{status, state, reason_code}`。"""
    wid = validate_workspace_id(workspace_id)
    cid = validate_id(connection_id, "connection_id")
    moment = _parse(now) or _parse(_now())
    assert moment is not None
    lock = _lock_for(wid, cid)
    if not lock.acquire(blocking=False):
        return {"status": "busy", "reason_code": "heartbeat_pending", "state": get_state(wid, cid)}
    try:
        current = _record(_load(wid), cid) or _default(cid)
        if not current.get("enabled"):
            return {"status": "skipped", "reason_code": "disabled", "state": _safe(current)}
        try:
            outcome = _probe_outcome(probe())
        except Exception:
            logger.warning("心跳 probe 异常：%s", cid, exc_info=True)
            outcome = {"success": False, "status": "failed", "reason_code": "network_error", "renewal_observed": False}
        return _store_result(wid, cid, current, outcome, moment)
    finally:
        lock.release()


def _store_result(
    workspace_id: str,
    connection_id: str,
    current: Dict[str, Any],
    outcome: Dict[str, Any],
    moment: datetime,
) -> Dict[str, Any]:
    interval = int(current.get("interval_minutes") or DEFAULT_INTERVAL)
    expected = int(current.get("revision") or 0)
    success = bool(outcome.get("success"))
    reason = None if success else (outcome.get("reason_code") or "auth_failed")
    stamp = moment.strftime(_TIME_FORMAT)
    anchor = _parse(current.get("last_renewing_activity")) or moment
    if success and outcome.get("renewal_observed") is True:
        anchor = moment
    next_due = (anchor + timedelta(minutes=interval)).strftime(_TIME_FORMAT)

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        record = _record(doc, connection_id) or _default(connection_id)
        if int(record.get("revision") or 0) != expected:
            raise AppError(REVISION_CONFLICT, "心跳配置在本次探测期间变化", field="revision")
        record["last_heartbeat_at"] = stamp
        record["last_result"] = "success" if success else "failed"
        record["reason_code"] = reason
        record["next_due_at"] = next_due
        if success and outcome.get("renewal_observed") is True:
            record["last_renewing_activity"] = stamp
        record["revision"] = expected + 1
        doc["items"][connection_id] = record
        return doc

    try:
        updated = sync_store.update_doc(workspace_id, FILE, mutate, DEFAULT_DOC)
    except AppError as exc:
        if exc.code == REVISION_CONFLICT:
            return {"status": "conflict", "reason_code": "revision_conflict", "state": get_state(workspace_id, connection_id)}
        raise
    state = _safe(_record(updated, connection_id))
    return {
        "status": "succeeded" if success else "failed",
        "reason_code": reason,
        "state": state,
    }


def record_idle_evidence(
    workspace_id: str,
    connection_id: str,
    evidence: Any,
    expected_revision: Any,
) -> Dict[str, Any]:
    """只有 E3 真实 idle 证据允许置 `heartbeat_validated=true`。"""
    wid = validate_workspace_id(workspace_id)
    cid = validate_id(connection_id, "connection_id")
    expected = validate_revision(expected_revision, "expected_revision")
    if not isinstance(evidence, dict):
        raise AppError(INVALID_INPUT, "evidence 必须是对象", field="evidence")
    if evidence.get("real_idle") is not True or evidence.get("renewal_observed") is not True:
        raise AppError(INVALID_INPUT, "只有真实 idle 续期证据才能标记已验证", field="evidence")
    idle = evidence.get("idle_window_minutes")
    if isinstance(idle, bool) or not isinstance(idle, int) or idle < 1:
        raise AppError(INVALID_INPUT, "idle_window_minutes 非法", field="evidence")
    observed = _parse(evidence.get("observed_at"))
    if observed is None:
        raise AppError(INVALID_INPUT, "observed_at 非法", field="evidence")
    operator = str(evidence.get("operator") or "").strip()
    authorization = str(evidence.get("authorization_ref") or "").strip()
    if not operator or not authorization:
        raise AppError(INVALID_INPUT, "证据缺少 operator/authorization_ref", field="evidence")
    safe_evidence = {
        "real_idle": True,
        "renewal_observed": True,
        "idle_window_minutes": idle,
        "observed_at": observed.strftime(_TIME_FORMAT),
        "operator": operator[:80],
        "authorization_ref": authorization[:120],
    }
    ttl = evidence.get("validated_ttl_minutes")
    if isinstance(ttl, int) and not isinstance(ttl, bool) and ttl > 0:
        safe_evidence["validated_ttl_minutes"] = min(ttl, 24 * 60)

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        record = _record(doc, cid) or _default(cid)
        if int(record.get("revision") or 0) != expected:
            raise AppError(REVISION_CONFLICT, "心跳配置 revision 已变化", field="expected_revision")
        interval = int(record.get("interval_minutes") or DEFAULT_INTERVAL)
        if idle < interval:
            raise AppError(INVALID_INPUT, "idle 窗口短于当前心跳间隔", field="evidence")
        record["heartbeat_validated"] = True
        record["validated_at"] = observed.strftime(_TIME_FORMAT)
        record["evidence"] = safe_evidence
        record["revision"] = expected + 1
        doc["items"][cid] = record
        return doc

    updated = sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    return _safe(_record(updated, cid))


def is_due(state: Dict[str, Any], now: Optional[Any] = None) -> bool:
    if not state.get("enabled"):
        return False
    due = _parse(state.get("next_due_at"))
    moment = _parse(now) or _parse(_now())
    if due is None or moment is None:
        return False
    return moment >= due


def assert_evidence(state: Dict[str, Any]) -> Dict[str, Any]:
    """UI/门禁读取口径：未验证就是未验证，不用成功心跳冒充。"""
    if state.get("heartbeat_validated") is not True:
        raise AppError(SOURCE_CHANGED, "保活尚未通过真实 idle 验证", field="heartbeat_validated")
    return state
