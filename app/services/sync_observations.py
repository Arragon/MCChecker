"""M16-29 机台实际值的可信观测接入与 last-good 观测指针。

机台观测的原文/解析仍在既有 V3 `config_ingest` 观测库；本模块只维护
`sync/observations.json` 里的**安全引用与顺序**：

- 来源目标必须是 `machine_observation` 且 `device_id` 与目标一致，否则明确阻断；
- 解析失败也保留引用/审计，但绝不移动 last-good；
- 同设备乱序（旧的 captured_at 晚到）不倒退 last-good；
- 只写引用与状态，不改机型基线文件、不发布 D0/Bn/rn。

真实设备/采集协议证据仍属外部验收；这里用注入的 capture 回调钉住状态机。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.errors import INVALID_INPUT, REVISION_CONFLICT, AppError
from app.core.sync_models import validate_id, validate_revision, validate_timestamp, validate_workspace_id

from . import sync_sources, sync_store

FILE = "observations.json"
DEFAULT_DOC: Dict[str, Any] = {"items": {}}
HISTORY_LIMIT = 200
STATUSES = frozenset({"parsed", "parse_failed", "failed"})


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _key(source_id: str, device_id: str) -> str:
    return "%s|%s" % (source_id, device_id)


def _default(source_id: str, device_id: str, workspace_id: str, profile_id: str) -> Dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "profile_id": profile_id,
        "source_id": source_id,
        "device_id": device_id,
        "last_good_observation_id": None,
        "last_good_captured_at": None,
        "last_observation_id": None,
        "last_captured_at": None,
        "last_status": None,
        "last_reason": None,
        "revision": 0,
        "history": [],
    }


def _load(workspace_id: str) -> Dict[str, Any]:
    doc = sync_store.read_doc(workspace_id, FILE, DEFAULT_DOC)
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), dict):
        raise AppError(INVALID_INPUT, "observations.json 形状损坏", field="observations")
    return doc


def _record(doc: Dict[str, Any], source_id: str, device_id: str) -> Optional[Dict[str, Any]]:
    value = doc["items"].get(_key(source_id, device_id))
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AppError(INVALID_INPUT, "观测元数据损坏", field="observations")
    return value


def _safe(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "workspace_id": value.get("workspace_id"),
        "profile_id": value.get("profile_id"),
        "source_id": value.get("source_id"),
        "device_id": value.get("device_id"),
        "last_good_observation_id": value.get("last_good_observation_id"),
        "last_good_captured_at": value.get("last_good_captured_at"),
        "last_observation_id": value.get("last_observation_id"),
        "last_captured_at": value.get("last_captured_at"),
        "last_status": value.get("last_status"),
        "last_reason": value.get("last_reason"),
        "revision": int(value.get("revision") or 0),
        "history": [dict(item) for item in value.get("history") or []],
    }


def get_state(workspace_id: str, source_id: str, device_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    sid = validate_id(source_id, "source_id")
    did = validate_id(device_id, "device_id")
    value = _record(_load(wid), sid, did)
    return _safe(value or _default(sid, did, wid, ""))


def list_states(workspace_id: str, profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
    wid = validate_workspace_id(workspace_id)
    items = []
    for value in _load(wid)["items"].values():
        if not isinstance(value, dict):
            continue
        if profile_id and value.get("profile_id") != profile_id:
            continue
        items.append(_safe(value))
    items.sort(key=lambda row: (str(row.get("source_id")), str(row.get("device_id"))))
    return items


def _check_target(source: Dict[str, Any], device_id: str) -> str:
    target = source.get("target") or {}
    if target.get("kind") != "machine_observation":
        raise AppError(
            INVALID_INPUT,
            "该来源未绑定机台观测目标，不能采集机台实际值",
            field="target",
        )
    wanted = str(target.get("device_id") or "").strip()
    if not wanted:
        raise AppError(INVALID_INPUT, "机台观测必须绑定 device_id", field="device_id")
    if wanted != device_id:
        raise AppError(
            INVALID_INPUT,
            "device_id 与来源绑定不一致",
            field="device_id",
            expected=wanted,
        )
    return wanted


def _newer(captured_at: str, observation_id: str, record: Dict[str, Any]) -> bool:
    if observation_id is None and captured_at is None:
        return False
    current = (
        str(record.get("last_good_captured_at") or ""),
        str(record.get("last_good_observation_id") or ""),
    )
    return (str(captured_at or ""), str(observation_id or "")) > current


def record_observation(
    workspace_id: str,
    source_id: str,
    device_id: str,
    capture: Any,
    expected_revision: Optional[Any] = None,
) -> Dict[str, Any]:
    """调用注入的 capture 后只记账；解析失败/乱序仍保留审计引用。"""
    wid = validate_workspace_id(workspace_id)
    sid = validate_id(source_id, "source_id")
    did = validate_id(device_id, "device_id")
    source = sync_sources.get_source(wid, sid)
    _check_target(source, did)
    profile_id = str(source.get("profile_id") or "")
    current = _record(_load(wid), sid, did) or _default(sid, did, wid, profile_id)
    expected = (
        validate_revision(expected_revision, "expected_revision")
        if expected_revision is not None
        else int(current.get("revision") or 0)
    )
    if int(current.get("revision") or 0) != expected:
        raise AppError(REVISION_CONFLICT, "观测元数据 revision 已变化", field="expected_revision")

    try:
        result = capture()
    except Exception as exc:
        result = {"status": "failed", "reason_code": "network_error", "message": str(exc)}
    if not isinstance(result, dict):
        raise AppError(INVALID_INPUT, "capture 结果必须是对象", field="capture")
    status = str(result.get("status") or "failed")
    if status not in STATUSES:
        raise AppError(INVALID_INPUT, "capture.status 非法", field="status")
    observation_id = result.get("observation_id")
    if observation_id not in (None, ""):
        observation_id = validate_id(observation_id, "observation_id")
    else:
        observation_id = None
    captured_at = result.get("captured_at")
    if captured_at not in (None, ""):
        captured_at = validate_timestamp(captured_at, "captured_at", allow_none=False)
    else:
        captured_at = None
    reason = result.get("reason_code")
    reason = str(reason)[:64] if reason else None
    parse_ok = status == "parsed" and observation_id is not None and captured_at is not None
    if status == "parsed" and not parse_ok:
        raise AppError(INVALID_INPUT, "成功观测必须带 observation_id/captured_at", field="capture")

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        record = _record(doc, sid, did) or _default(sid, did, wid, profile_id)
        if int(record.get("revision") or 0) != expected:
            raise AppError(REVISION_CONFLICT, "观测元数据 revision 已变化", field="expected_revision")
        stale = not parse_ok or not _newer(captured_at, observation_id, record)
        if parse_ok and not stale:
            record["last_good_observation_id"] = observation_id
            record["last_good_captured_at"] = captured_at
        record["last_observation_id"] = observation_id
        record["last_captured_at"] = captured_at
        record["last_status"] = status
        record["last_reason"] = reason
        record["revision"] = expected + 1
        history = list(record.get("history") or [])
        history.append(
            {
                "observation_id": observation_id,
                "captured_at": captured_at,
                "status": status,
                "reason_code": reason,
                "stale": stale,
            }
        )
        record["history"] = history[-HISTORY_LIMIT:]
        doc["items"][_key(sid, did)] = record
        return doc

    updated = sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    return _safe(_record(updated, sid, did))
