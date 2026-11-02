"""M16-10 更新范围解析、预览与冻结 read_set。

预览只读来源/策略/绑定并冻结 revision，不下载、不启动任务、不改文件。
默认 5 分钟过期；start（M16-12/23）必须带 plan_id + expected revision +
Idempotency-Key，并在提交前再次核对来源/连接/绑定/策略 revision。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core.errors import INVALID_INPUT, NOT_FOUND, PLAN_STALE, REVISION_CONFLICT, SOURCE_CHANGED, AppError
from app.core.sync_models import validate_enum, validate_id, validate_workspace_id

from . import sync_bindings, sync_connections, sync_policy, sync_sources, sync_store

FILE = "previews.json"
DEFAULT_DOC: Dict[str, Any] = {"revision": 0, "items": {}}
SCOPES = (
    "current_configuration",
    "current_profile",
    "selected_sources",
    "workspace_all",
    "failures_retry",
)
TTL_SECONDS = 300


def _now() -> datetime:
    return datetime.utcnow()


def _format(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise AppError(INVALID_INPUT, "预览时间非法", field="expires_at")


def _load(workspace_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    doc = sync_store.read_doc(wid, FILE, DEFAULT_DOC)
    if not isinstance(doc, dict) or not isinstance(doc.get("items", {}), dict):
        raise AppError(INVALID_INPUT, "previews.json 形状损坏", field="previews")
    doc.setdefault("revision", 0)
    doc.setdefault("items", {})
    return doc


def _all_sources(workspace_id: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    cursor = "0"
    while True:
        page = sync_sources.list_sources(
            workspace_id, status="all", limit=200, cursor=cursor
        )
        items.extend(page["items"])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return items


def _id_list(value: Any, field: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AppError(INVALID_INPUT, "%s 必须是数组" % field, field=field)
    return [validate_id(item, field) for item in value]


def _scope_sources(payload: Dict[str, Any], workspace_id: str) -> List[Dict[str, Any]]:
    scope = validate_enum(payload.get("scope"), SCOPES, "scope")
    profile_id = payload.get("profile_id")
    if profile_id:
        validate_id(profile_id, "profile_id")
    configuration_id = payload.get("configuration_id")
    if configuration_id:
        validate_id(configuration_id, "configuration_id")
    selected = _id_list(payload.get("source_ids"), "source_ids")
    failed = _id_list(payload.get("failed_source_ids"), "failed_source_ids")

    sources = _all_sources(workspace_id)
    if scope == "current_profile":
        if not profile_id:
            raise AppError(INVALID_INPUT, "current_profile 必须给 profile_id", field="profile_id")
    if scope == "current_configuration":
        if not profile_id:
            raise AppError(INVALID_INPUT, "current_configuration 必须给 profile_id", field="profile_id")
    if scope == "selected_sources" and not selected:
        raise AppError(INVALID_INPUT, "selected_sources 必须给 source_ids", field="source_ids")
    if scope == "failures_retry" and not failed:
        raise AppError(INVALID_INPUT, "failures_retry 必须给 failed_source_ids", field="failed_source_ids")

    selected_set = set(selected)
    failed_set = set(failed)
    result = []
    for source in sources:
        if source.get("deleted"):
            continue
        if not source.get("enabled"):
            continue
        if scope == "current_profile" and source.get("profile_id") != profile_id:
            continue
        if scope == "current_configuration":
            target = source.get("target") or {}
            if source.get("profile_id") != profile_id:
                continue
            if configuration_id and target.get("configuration_id") != configuration_id:
                continue
        if scope == "selected_sources" and source["source_id"] not in selected_set:
            continue
        if scope == "failures_retry" and source["source_id"] not in failed_set:
            continue
        result.append(source)
    return result


def _freeze_source(workspace_id: str, source: Dict[str, Any], policy_revision: int) -> Dict[str, Any]:
    pid = source["profile_id"]
    item: Dict[str, Any] = {
        "source_id": source["source_id"],
        "profile_id": pid,
        "name": source.get("name"),
        "kind": source.get("kind"),
        "source_revision": source.get("revision"),
        "target": source.get("target"),
        "enabled": source.get("enabled"),
        "schedule_mode": source.get("schedule_mode"),
        "interval_hours": source.get("interval_hours"),
        "policy_revision": policy_revision,
    }
    connection_id = source.get("connection_id")
    if connection_id:
        connection = sync_connections.get_connection(workspace_id, connection_id)
        item["connection_id"] = connection_id
        item["connection_revision"] = connection["policy_revision"]
    target = source.get("target")
    if target:
        item["binding"] = sync_bindings.resolve_binding(workspace_id, pid, target)
    payload = json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8")
    item["read_set_sha256"] = hashlib.sha256(payload).hexdigest()
    return item


def create_preview(workspace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    if not isinstance(payload, dict):
        raise AppError(INVALID_INPUT, "payload 必须是对象", field="payload")
    sources = _scope_sources(payload, wid)
    policy = sync_policy.get_policy(wid)
    created = _now()
    expires = created + timedelta(seconds=TTL_SECONDS)
    preview_id = "prev-" + secrets.token_hex(8)
    items: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []

    for source in sources:
        try:
            items.append(_freeze_source(wid, source, policy["revision"]))
        except AppError as exc:
            excluded.append(
                {
                    "source_id": source["source_id"],
                    "profile_id": source["profile_id"],
                    "reason_code": exc.code,
                    "field": exc.detail.get("field"),
                }
            )

    preview = {
        "preview_id": preview_id,
        "workspace_id": wid,
        "scope": validate_enum(payload.get("scope"), SCOPES, "scope"),
        "profile_id": payload.get("profile_id"),
        "configuration_id": payload.get("configuration_id"),
        "policy_revision": policy["revision"],
        "created_at": _format(created),
        "expires_at": _format(expires),
        "items": items,
        "excluded": excluded,
        "total": len(items),
        "read_set_sha256": hashlib.sha256(
            json.dumps(items, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }

    def mutate(doc: Dict[str, Any]) -> Dict[str, Any]:
        items_map = doc["items"]
        for key in list(items_map):
            try:
                if _parse(items_map[key].get("expires_at")) <= created:
                    del items_map[key]
            except AppError:
                del items_map[key]
        items_map[preview_id] = preview
        doc["revision"] = int(doc.get("revision", 0)) + 1
        return doc

    sync_store.update_doc(wid, FILE, mutate, DEFAULT_DOC)
    return preview


def get_preview(workspace_id: str, preview_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    pid = validate_id(preview_id, "preview_id")
    doc = _load(wid)
    preview = doc["items"].get(pid)
    if not isinstance(preview, dict):
        raise AppError(NOT_FOUND, "预览不存在", field="preview_id")
    if _parse(preview.get("expires_at")) <= _now():
        raise AppError(PLAN_STALE, "预览已过期，请重新预览", field="preview_id")
    return preview


def validate_preview(workspace_id: str, preview_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    preview = get_preview(wid, preview_id)
    policy = sync_policy.get_policy(wid)
    if policy["revision"] != preview.get("policy_revision"):
        raise AppError(REVISION_CONFLICT, "工作区计划已变化，请重新预览", field="policy_revision")
    for item in preview.get("items") or []:
        source = sync_sources.get_source(wid, item["source_id"])
        if source.get("revision") != item.get("source_revision"):
            raise AppError(REVISION_CONFLICT, "来源 revision 已变化，请重新预览", field="source_revision")
        connection_id = item.get("connection_id")
        if connection_id:
            connection = sync_connections.get_connection(wid, connection_id)
            if connection["policy_revision"] != item.get("connection_revision"):
                raise AppError(REVISION_CONFLICT, "连接 revision 已变化，请重新预览", field="connection_revision")
        binding = item.get("binding")
        if isinstance(binding, dict) and binding.get("target"):
            try:
                sync_bindings.assert_binding_current(wid, item["profile_id"], binding)
            except AppError as exc:
                if exc.code == SOURCE_CHANGED:
                    raise AppError(SOURCE_CHANGED, "目标绑定已变化，请重新预览", field="binding")
                raise
    return preview
