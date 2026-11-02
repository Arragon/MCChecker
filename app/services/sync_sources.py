"""M16-05 Workspace 级更新源注册表（CRUD / 分页筛选 / CAS）。

来源属于 workspace+profile；SDA 来源必须绑定已注册 connection 与
resource_ref；`http_file` 来源不要求 connection。删除只写 tombstone，
不删除 FileResource/历史/归档。

本模块不下载、不改文件；执行由 M16-13/23/24 负责。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from app.core.errors import INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError
from app.core.sync_models import (
    SOURCE_KINDS,
    SOURCE_SCHEDULE_MODES,
    SyncSource,
    SyncTarget,
    make_source_id,
    validate_enum,
    validate_id,
    validate_interval_hours,
    validate_revision,
    validate_workspace_id,
)
from app.services import sync_connections, workspace_context

from . import sync_store

FILE = "sources.json"
DEFAULT_DOC: Dict[str, Any] = {"revision": 0, "items": []}
CREATE_FIELDS = frozenset(
    {
        "profile_id",
        "name",
        "kind",
        "target",
        "enabled",
        "schedule_mode",
        "interval_hours",
        "connection_id",
        "resource_ref",
        "display_path",
        "record_source",
        "keyword",
    }
)
PATCH_FIELDS = frozenset(CREATE_FIELDS - {"profile_id"})


def _check_url(value: Any, field: str) -> None:
    text = str(value or "").strip()
    if not text:
        return
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https"):
        return
    if parts.username or parts.password:
        raise AppError(INVALID_INPUT, "URL 不允许内嵌账号密码", field=field)


def _check_urls(payload: Dict[str, Any]) -> None:
    _check_url(payload.get("resource_ref"), "resource_ref")
    _check_url(payload.get("record_source"), "record_source")


def safe_url(value: Any) -> Any:
    """只读投影：去掉 userinfo/query/fragment，保留 scheme/host/path。"""
    text = str(value or "").strip()
    if not text:
        return value
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return value
    host = parts.hostname
    if ":" in host:
        host = "[" + host + "]"
    if parts.port and parts.port != (443 if parts.scheme.lower() == "https" else 80):
        host = host + ":" + str(parts.port)
    return parts.scheme.lower() + "://" + host + (parts.path or "/")


def project_for_read(source: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(source)
    payload["resource_ref"] = safe_url(payload.get("resource_ref"))
    payload["record_source"] = safe_url(payload.get("record_source"))
    return payload


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(workspace_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    doc = sync_store.read_doc(wid, FILE, DEFAULT_DOC)
    if not isinstance(doc, dict):
        raise AppError(INVALID_INPUT, "sources.json 形状损坏", field="sources")
    revision = doc.get("revision", 0)
    items = doc.get("items")
    if not isinstance(items, list):
        raise AppError(INVALID_INPUT, "sources.json 形状损坏", field="sources")
    doc["revision"] = validate_revision(revision, "revision")
    doc["items"] = items
    return doc


def _find(doc: Dict[str, Any], source_id: str, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
    sid = validate_id(source_id, "source_id")
    for item in doc["items"]:
        if not isinstance(item, dict) or item.get("source_id") != sid:
            continue
        if item.get("deleted") and not include_deleted:
            return None
        return item
    return None


def _safe(item: Dict[str, Any], include_deleted: bool = False) -> Dict[str, Any]:
    source = SyncSource.from_dict(item)
    payload = source.to_dict()
    if include_deleted:
        payload["deleted"] = bool(item.get("deleted"))
    return payload


def _target(value: Any) -> Optional[SyncTarget]:
    if value is None:
        return None
    if isinstance(value, SyncTarget):
        return value
    if isinstance(value, dict):
        return SyncTarget.from_dict(value)
    raise AppError(INVALID_INPUT, "target 必须是对象", field="target")


def _name_taken(doc: Dict[str, Any], profile_id: str, name: str, exclude: Optional[str] = None) -> bool:
    folded = name.casefold()
    for item in doc["items"]:
        if not isinstance(item, dict):
            continue
        if item.get("profile_id") != profile_id or item.get("source_id") == exclude:
            continue
        if item.get("deleted"):
            continue
        if str(item.get("name", "")).casefold() == folded:
            return True
    return False


def _check_connection(workspace_id: str, kind: str, connection_id: Optional[str]) -> None:
    if kind.startswith("sda_"):
        if not connection_id:
            raise AppError(INVALID_INPUT, "SDA 来源必须绑定 connection_id", field="connection_id")
        sync_connections.get_connection(workspace_id, connection_id)


def list_sources(
    workspace_id: str,
    profile_id: Optional[str] = None,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    query: str = "",
    limit: int = 50,
    cursor: str = "0",
) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    if profile_id:
        workspace_context.assert_profile(wid, profile_id)
    if kind is not None:
        validate_enum(kind, SOURCE_KINDS, "kind")
    if status not in (None, "enabled", "disabled", "deleted", "all"):
        raise AppError(INVALID_INPUT, "status 不在允许值内", field="status")
    try:
        offset = int(str(cursor or "0"))
    except ValueError:
        raise AppError(INVALID_INPUT, "cursor 非法", field="cursor")
    if offset < 0:
        raise AppError(INVALID_INPUT, "cursor 非法", field="cursor")
    if limit < 1 or limit > 200:
        raise AppError(INVALID_INPUT, "limit 必须在 1–200", field="limit")

    doc = _load(wid)
    needle = str(query or "").strip().casefold()
    rows: List[Dict[str, Any]] = []
    for item in doc["items"]:
        if not isinstance(item, dict):
            raise AppError(INVALID_INPUT, "sources.json 条目损坏", field="sources")
        deleted = bool(item.get("deleted"))
        if profile_id and item.get("profile_id") != profile_id:
            continue
        if kind and item.get("kind") != kind:
            continue
        if status == "deleted" and not deleted:
            continue
        if status in (None, "enabled", "disabled") and deleted:
            continue
        if status == "enabled" and not item.get("enabled"):
            continue
        if status == "disabled" and item.get("enabled"):
            continue
        if needle:
            haystack = (str(item.get("name", "")) + " " + str(item.get("display_path", ""))).casefold()
            if needle not in haystack:
                continue
        rows.append(item)

    rows.sort(key=lambda row: (str(row.get("name", "")).casefold(), str(row.get("source_id", ""))))
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "items": [_safe(row, include_deleted=bool(row.get("deleted"))) for row in page],
        "total": total,
        "next_cursor": str(offset + limit) if offset + limit < total else None,
        "query_revision": doc["revision"],
    }


def get_source(workspace_id: str, source_id: str, include_deleted: bool = False) -> Dict[str, Any]:
    doc = _load(workspace_id)
    item = _find(doc, source_id, include_deleted=include_deleted)
    if item is None:
        raise AppError(NOT_FOUND, "来源不存在", field="source_id")
    return _safe(item, include_deleted=include_deleted)


def create_source(workspace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    if not isinstance(payload, dict):
        raise AppError(INVALID_INPUT, "payload 必须是对象", field="payload")
    unknown = set(payload) - CREATE_FIELDS
    if unknown:
        raise AppError(INVALID_INPUT, "不允许的来源字段", field="payload", keys=sorted(unknown))
    profile_id = payload.get("profile_id")
    if not profile_id:
        raise AppError(INVALID_INPUT, "profile_id 不能为空", field="profile_id")
    workspace_context.assert_profile(wid, profile_id)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise AppError(INVALID_INPUT, "来源名称不能为空", field="name")
    _check_urls(payload)
    kind = payload.get("kind", "http_file")
    target = _target(payload.get("target"))
    connection_id = payload.get("connection_id")
    if connection_id:
        validate_id(connection_id, "connection_id")
    _check_connection(wid, kind, connection_id)
    schedule_mode = payload.get("schedule_mode", "inherit")
    interval_hours = validate_interval_hours(payload.get("interval_hours"))
    source_id = make_source_id(wid, profile_id, name)
    now = _now()

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        if _find(doc, source_id, include_deleted=True) is not None:
            raise AppError(INVALID_INPUT, "来源名称已存在（含 tombstone）", field="name")
        if _name_taken(doc, profile_id, name):
            raise AppError(INVALID_INPUT, "来源名称已存在", field="name")
        source = SyncSource(
            source_id=source_id,
            workspace_id=wid,
            profile_id=profile_id,
            revision=1,
            name=name,
            kind=kind,
            target=target,
            enabled=payload.get("enabled", True),
            schedule_mode=schedule_mode,
            interval_hours=interval_hours,
            connection_id=connection_id,
            resource_ref=payload.get("resource_ref"),
            display_path=payload.get("display_path"),
            record_source=payload.get("record_source"),
            keyword=payload.get("keyword", ""),
            created_at=now,
            updated_at=now,
        )
        doc["items"].append(source.to_dict())
        doc["revision"] = doc["revision"] + 1
        return doc

    sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    return get_source(wid, source_id)


def update_source(
    workspace_id: str,
    source_id: str,
    expected_revision: Any,
    patch: Dict[str, Any],
) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    sid = validate_id(source_id, "source_id")
    revision = validate_revision(expected_revision, "expected_revision")
    if not isinstance(patch, dict):
        raise AppError(INVALID_INPUT, "patch 必须是对象", field="patch")
    unknown = set(patch) - PATCH_FIELDS
    if unknown:
        raise AppError(INVALID_INPUT, "不允许修改来源字段", field="patch", keys=sorted(unknown))
    now = _now()

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        item = _find(doc, sid)
        if item is None:
            raise AppError(NOT_FOUND, "来源不存在", field="source_id")
        if item.get("revision") != revision:
            raise AppError(REVISION_CONFLICT, "来源 revision 已变化", field="expected_revision")
        current = SyncSource.from_dict(item)
        values = current.to_dict()
        values.update(patch)
        _check_urls(values)
        values["source_id"] = current.source_id
        values["workspace_id"] = current.workspace_id
        values["profile_id"] = current.profile_id
        values["revision"] = current.revision + 1
        values["updated_at"] = now
        values["target"] = _target(values.get("target"))
        _check_connection(wid, values["kind"], values.get("connection_id"))
        if _name_taken(doc, current.profile_id, str(values["name"]), exclude=sid):
            raise AppError(INVALID_INPUT, "来源名称已存在", field="name")
        updated = SyncSource(**values)
        merged = dict(item)
        merged.update(updated.to_dict())
        merged["deleted"] = False
        for index, existing in enumerate(doc["items"]):
            if isinstance(existing, dict) and existing.get("source_id") == sid:
                doc["items"][index] = merged
                break
        doc["revision"] = doc["revision"] + 1
        return doc

    sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    return get_source(wid, sid)


def batch_update_schedule(workspace_id: str, updates: Any) -> List[Dict[str, Any]]:
    """一次性更新多个 source 的计划；任一项 stale/缺失则 0 项生效。"""
    wid = validate_workspace_id(workspace_id)
    if not isinstance(updates, list) or not updates:
        raise AppError(INVALID_INPUT, "updates 必须是非空数组", field="updates")
    if len(updates) > 100:
        raise AppError(INVALID_INPUT, "updates 最多 100 项", field="updates")
    parsed = []
    seen = set()
    for raw in updates:
        if not isinstance(raw, dict):
            raise AppError(INVALID_INPUT, "updates 每项必须是对象", field="updates")
        unknown = set(raw) - {"source_id", "expected_revision", "schedule_mode", "interval_hours"}
        if unknown:
            raise AppError(INVALID_INPUT, "不允许的批量字段", field="updates", keys=sorted(unknown))
        sid = validate_id(raw.get("source_id"), "source_id")
        if sid in seen:
            raise AppError(INVALID_INPUT, "同一来源不能重复", field="source_id")
        seen.add(sid)
        revision = validate_revision(raw.get("expected_revision"), "expected_revision")
        mode = validate_enum(raw.get("schedule_mode"), SOURCE_SCHEDULE_MODES, "schedule_mode")
        interval = validate_interval_hours(raw.get("interval_hours"))
        if mode == "custom" and interval is None:
            raise AppError(INVALID_INPUT, "custom 计划必须给 interval_hours", field="interval_hours")
        if mode != "custom" and interval is not None:
            raise AppError(INVALID_INPUT, "inherit/manual 不能带 interval_hours", field="interval_hours")
        parsed.append((sid, revision, mode, interval))

    now = _now()

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        replacements = []
        for sid, revision, mode, interval in parsed:
            item = _find(doc, sid)
            if item is None:
                raise AppError(NOT_FOUND, "来源不存在", field="source_id")
            if item.get("revision") != revision:
                raise AppError(REVISION_CONFLICT, "来源 revision 已变化", field="expected_revision")
            current = SyncSource.from_dict(item)
            values = current.to_dict()
            values["schedule_mode"] = mode
            values["interval_hours"] = interval
            values["revision"] = current.revision + 1
            values["updated_at"] = now
            values["target"] = _target(values.get("target"))
            updated = SyncSource(**values)
            merged = dict(item)
            merged.update(updated.to_dict())
            merged["deleted"] = False
            replacements.append((sid, merged))
        for sid, merged in replacements:
            for index, item in enumerate(doc["items"]):
                if isinstance(item, dict) and item.get("source_id") == sid:
                    doc["items"][index] = merged
                    break
        doc["revision"] = doc["revision"] + 1
        return doc

    sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    return [get_source(wid, raw["source_id"]) for raw in updates]


def delete_source(workspace_id: str, source_id: str, expected_revision: Any) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    sid = validate_id(source_id, "source_id")
    revision = validate_revision(expected_revision, "expected_revision")
    now = _now()

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        item = _find(doc, sid)
        if item is None:
            raise AppError(NOT_FOUND, "来源不存在", field="source_id")
        if item.get("revision") != revision:
            raise AppError(REVISION_CONFLICT, "来源 revision 已变化", field="expected_revision")
        current = SyncSource.from_dict(item)
        tombstone = dict(item)
        tombstone.update(current.to_dict())
        tombstone["revision"] = current.revision + 1
        tombstone["enabled"] = False
        tombstone["updated_at"] = now
        tombstone["deleted"] = True
        for index, existing in enumerate(doc["items"]):
            if isinstance(existing, dict) and existing.get("source_id") == sid:
                doc["items"][index] = tombstone
                break
        doc["revision"] = doc["revision"] + 1
        return doc

    sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    return get_source(wid, sid, include_deleted=True)
