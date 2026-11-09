"""Read-only source checks using unsaved form input; never commit files or freshness."""
from typing import Any, Dict
import hashlib
import threading

from app.core import parser, storage
from . import sync_bindings, sync_http_adapter, sync_sources, sync_tasks, updates


def check_source(workspace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    source = sync_sources.validate_source_input(workspace_id, dict(payload, enabled=True))
    binding = sync_bindings.resolve_binding(workspace_id, source.profile_id, source.target) if source.target else {}
    if source.kind.startswith("sda_"):
        read = sync_tasks.read_sda_input(source.to_dict(), threading.Event())
        if read.get("status") != "succeeded":
            return dict(read, items=[])
        if source.kind == "sda_catalog":
            return {"status": "succeeded", "message": "目录已读取；不代表受保护内容可读。", "items": []}
        content = read.get("content")
        if not isinstance(content, bytes):
            return {"status": "failed", "reason_code": "invalid_payload", "message": "SDA 未返回可校验内容", "items": []}
        result = sync_http_adapter.HttpFetchResult(kind="http_file", status="succeeded", source_id=source.source_id, message="SDA 内容已读取",
            profile_id=source.profile_id, items=(sync_http_adapter.HttpFetchItem(basename=read.get("name") or source.name,
                content=content, size=len(content), sha256=hashlib.sha256(content).hexdigest()),))
    else:
        result = sync_http_adapter.fetch_source(source)
    if result.status not in ("succeeded", "unchanged"):
        return {"status": "failed", "reason_code": result.reason_code,
                "message": result.message, "items": []}
    items = []
    current = None
    name = (binding.get("ref") or {}).get("name")
    if name:
        with storage.use_workspace(workspace_id), storage.use_profile(source.profile_id):
            current = storage.load_config_file(name)
    expected = updates._root_fingerprint(current, name) if current is not None else None
    for item in result.items:
        document = parser.to_compact(item.content, item.basename)
        actual = updates._fingerprint(document, item.content)
        if actual and (actual[0].lower() == "xml:html" or expected and expected[0] != actual[0]):
            return {"status": "failed", "reason_code": "invalid_payload", "message": "来源内容与目标文件结构不符，可能返回了登录页或其他页面。", "items": []}
        items.append({"name": item.basename, "size": item.size, "sha256": item.sha256})
    return {"status": "succeeded", "message": "内容已读取并通过格式校验；未保存来源或更新业务文件。", "items": items}
