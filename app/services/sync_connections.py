"""M16-04 Workspace 连接注册表（非敏感元数据）。

连接属于 workspace，不内嵌密码/Cookie。首版只保存：
- 连接稳定 ID、显示名、允许的 SDA origin、协议模板枚举；
- policy_revision（CAS）、enabled、tombstone、created/updated；
- 最近 200 条安全连接事件（configured/enabled/disabled/policy_changed）。

真实凭据由 M16-18 的 SecretStore 管理；本模块不接触 secret_ref 的明文，
也不提供任意 URL/JS 代理。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from app.core.errors import INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError
from app.core.sync_models import (
    ConnectionEvent,
    SdaConnection,
    make_connection_id,
    make_event_id,
    validate_id,
    validate_revision,
    validate_workspace_id,
)

from . import sync_store

FILE = "connections.json"
DEFAULT_DOC: Dict[str, Any] = {"items": [], "events": []}
EVENT_LIMIT = 200
PATCH_FIELDS = frozenset({"name", "origin", "protocol", "enabled"})


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(workspace_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    doc = sync_store.read_doc(wid, FILE, DEFAULT_DOC)
    if not isinstance(doc, dict):
        raise AppError(INVALID_INPUT, "connections.json 形状损坏", field="connections")
    items = doc.get("items")
    events = doc.get("events")
    if not isinstance(items, list) or not isinstance(events, list):
        raise AppError(INVALID_INPUT, "connections.json 形状损坏", field="connections")
    return doc


def _find(doc: Dict[str, Any], connection_id: str, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
    cid = validate_id(connection_id, "connection_id")
    for item in doc["items"]:
        if not isinstance(item, dict) or item.get("connection_id") != cid:
            continue
        if item.get("deleted") and not include_deleted:
            return None
        return item
    return None


def _safe(item: Dict[str, Any], include_deleted: bool = False) -> Dict[str, Any]:
    connection = SdaConnection.from_dict(item)
    payload = connection.to_dict()
    if include_deleted:
        payload["deleted"] = bool(item.get("deleted"))
    return payload


def _append_event(
    doc: Dict[str, Any],
    workspace_id: str,
    connection_id: str,
    event_type: str,
    at: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    sequence = len(doc["events"]) + 1
    event = ConnectionEvent(
        event_id=make_event_id(connection_id, at, sequence),
        workspace_id=workspace_id,
        connection_id=connection_id,
        event_type=event_type,
        at=at,
        details=details or {},
    )
    doc["events"].append(event.to_dict())
    if len(doc["events"]) > EVENT_LIMIT:
        del doc["events"][:-EVENT_LIMIT]


def _origin_without_port(origin: Any) -> str:
    from app.core.sync_models import validate_origin

    value = validate_origin(origin)
    parts = urlsplit(value)
    if parts.port is not None and parts.port != 443:
        raise AppError(INVALID_INPUT, "连接端口只允许 443", field="origin")
    return value


def _name_taken(doc: Dict[str, Any], name: str, exclude: Optional[str] = None) -> bool:
    folded = name.casefold()
    for item in doc["items"]:
        if not isinstance(item, dict):
            continue
        if item.get("deleted") or item.get("connection_id") == exclude:
            continue
        if str(item.get("name", "")).casefold() == folded:
            return True
    return False


def list_connections(workspace_id: str, include_deleted: bool = False) -> List[Dict[str, Any]]:
    doc = _load(workspace_id)
    result: List[Dict[str, Any]] = []
    for item in doc["items"]:
        if not isinstance(item, dict):
            raise AppError(INVALID_INPUT, "connections.json 条目损坏", field="connections")
        if item.get("deleted") and not include_deleted:
            continue
        result.append(_safe(item, include_deleted=include_deleted))
    return result


def get_connection(workspace_id: str, connection_id: str, include_deleted: bool = False) -> Dict[str, Any]:
    doc = _load(workspace_id)
    item = _find(doc, connection_id, include_deleted=include_deleted)
    if item is None:
        raise AppError(NOT_FOUND, "连接不存在", field="connection_id")
    return _safe(item, include_deleted=include_deleted)


def create_connection(
    workspace_id: str,
    name: Any,
    origin: Any,
    protocol: str = "sda_v1",
    enabled: bool = True,
) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    text = str(name or "").strip()
    if not text:
        raise AppError(INVALID_INPUT, "连接名称不能为空", field="name")
    connection_id = make_connection_id(wid, text)
    now = _now()

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        if _find(doc, connection_id, include_deleted=True) is not None:
            raise AppError(INVALID_INPUT, "连接名称已存在（含 tombstone）", field="name")
        if _name_taken(doc, text):
            raise AppError(INVALID_INPUT, "连接名称已存在", field="name")
        connection = SdaConnection(
            connection_id=connection_id,
            workspace_id=wid,
            name=text,
            origin=_origin_without_port(origin),
            protocol=protocol,
            policy_revision=1,
            enabled=enabled,
            created_at=now,
            updated_at=now,
        )
        doc["items"].append(connection.to_dict())
        _append_event(doc, wid, connection_id, "configured", now, {"origin": connection.origin})
        return doc

    sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    return get_connection(wid, connection_id)


def update_connection(
    workspace_id: str,
    connection_id: str,
    expected_revision: Any,
    patch: Dict[str, Any],
) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    cid = validate_id(connection_id, "connection_id")
    revision = validate_revision(expected_revision, "expected_revision")
    if not isinstance(patch, dict):
        raise AppError(INVALID_INPUT, "patch 必须是对象", field="patch")
    unknown = set(patch) - PATCH_FIELDS
    if unknown:
        raise AppError(INVALID_INPUT, "不允许修改连接字段", field="patch", keys=sorted(unknown))
    now = _now()

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        item = _find(doc, cid)
        if item is None:
            raise AppError(NOT_FOUND, "连接不存在", field="connection_id")
        if item.get("policy_revision") != revision:
            raise AppError(REVISION_CONFLICT, "连接 revision 已变化", field="expected_revision")
        current = SdaConnection.from_dict(item)
        values = {
            "name": current.name,
            "origin": current.origin,
            "protocol": current.protocol,
            "enabled": current.enabled,
        }
        values.update(patch)
        name = str(values["name"] or "").strip()
        if _name_taken(doc, name, exclude=cid):
            raise AppError(INVALID_INPUT, "连接名称已存在", field="name")
        updated = SdaConnection(
            connection_id=cid,
            workspace_id=wid,
            name=name,
            origin=_origin_without_port(values["origin"]),
            protocol=values["protocol"],
            policy_revision=current.policy_revision + 1,
            enabled=values["enabled"],
            created_at=current.created_at,
            updated_at=now,
        )
        for index, existing in enumerate(doc["items"]):
            if isinstance(existing, dict) and existing.get("connection_id") == cid:
                doc["items"][index] = updated.to_dict()
                break
        event_type = "policy_changed"
        if current.enabled and not updated.enabled:
            event_type = "disabled"
        elif not current.enabled and updated.enabled:
            event_type = "enabled"
        _append_event(doc, wid, cid, event_type, now, {"fields": sorted(patch)})
        return doc

    sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    return get_connection(wid, cid)


def disable_connection(workspace_id: str, connection_id: str, expected_revision: Any) -> Dict[str, Any]:
    return update_connection(workspace_id, connection_id, expected_revision, {"enabled": False})


def delete_connection(workspace_id: str, connection_id: str, expected_revision: Any) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    cid = validate_id(connection_id, "connection_id")
    revision = validate_revision(expected_revision, "expected_revision")
    now = _now()

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        item = _find(doc, cid)
        if item is None:
            raise AppError(NOT_FOUND, "连接不存在", field="connection_id")
        if item.get("policy_revision") != revision:
            raise AppError(REVISION_CONFLICT, "连接 revision 已变化", field="expected_revision")
        current = SdaConnection.from_dict(item)
        tombstone = current.to_dict()
        tombstone["policy_revision"] = current.policy_revision + 1
        tombstone["enabled"] = False
        tombstone["updated_at"] = now
        tombstone["deleted"] = True
        for index, existing in enumerate(doc["items"]):
            if isinstance(existing, dict) and existing.get("connection_id") == cid:
                doc["items"][index] = tombstone
                break
        _append_event(doc, wid, cid, "disabled", now, {"deleted": True})
        return doc

    sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    return get_connection(wid, cid, include_deleted=True)


def connection_events(
    workspace_id: str,
    connection_id: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    doc = _load(workspace_id)
    cid = validate_id(connection_id, "connection_id") if connection_id else None
    if limit < 1 or limit > EVENT_LIMIT:
        raise AppError(INVALID_INPUT, "limit 必须在 1–200", field="limit")
    events = [
        dict(event)
        for event in doc["events"]
        if isinstance(event, dict) and (cid is None or event.get("connection_id") == cid)
    ]
    return events[-limit:]
