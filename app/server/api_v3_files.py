"""V3 唯一文件选择器与精确版本 API（M11-05 / INH-2134）。

只暴露 FileResource/FileVersion 身份，不新建上传入口：
- `GET .../file-selector`：当前 W/P 的文件 + current version；
- `GET .../file-versions/{vid}` / `/document` / `/raw`：按不可变 version_id 读取。

跨 W/P 的 vid 一律 404；临时文件不在选择器里，必须先保存。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core import storage
from ..core.errors import NOT_FOUND, AppError
from ..services import authz, documents, file_versions, workspace_catalog
from ..services.context import RequestContext
from .api_documents import content_disposition
from .router import Response, Router, query_param


def register(router: Router) -> None:
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/file-selector",
        file_selector,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/file-versions/{vid}",
        file_version,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/file-versions/{vid}/document",
        file_version_document,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/file-versions/{vid}/raw",
        file_version_raw,
    )


def _scope(wid: str, pid: str) -> None:
    workspace_catalog.get_workspace(wid)
    if not any(str(p.get("id")) == pid for p in workspace_catalog.list_profiles(wid)):
        raise AppError(NOT_FOUND, "机型不存在", profile=pid)


def _int(raw: str, default: int) -> int:
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def file_selector(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid, pid = params["wid"], params["pid"]
    _scope(wid, pid)
    q = query_param(query, "q").strip().lower()
    limit = max(1, min(_int(query_param(query, "limit"), 200), 1000))
    offset = max(0, _int(query_param(query, "offset"), 0))
    with storage.use_workspace(wid), storage.use_profile(pid):
        items = file_versions.list_resources(pid)
    if q:
        items = [row for row in items if q in row["name"].lower()]
    total = len(items)
    return Response(payload={"items": items[offset : offset + limit], "total": total, "limit": limit, "offset": offset})


def _find(wid: str, pid: str, vid: str):
    with storage.use_workspace(wid), storage.use_profile(pid):
        for item in file_versions.list_resources(pid):
            for row in file_versions.versions(pid, item["name"]):
                if row["version_id"] == vid:
                    return row
    raise AppError(NOT_FOUND, "文件版本不存在或不属于当前工作区/机型", version_id=vid)


def file_version(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid, pid = params["wid"], params["pid"]
    _scope(wid, pid)
    return Response(payload={"version": _find(wid, pid, params["vid"])})


def file_version_document(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid, pid = params["wid"], params["pid"]
    _scope(wid, pid)
    row = _find(wid, pid, params["vid"])
    with storage.use_workspace(wid), storage.use_profile(pid):
        payload, etag = documents.document_bytes(pid, row["name"], row["ref"])
    return Response(body=payload, headers={"ETag": etag, "Cache-Control": "no-cache"})


def file_version_raw(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid, pid = params["wid"], params["pid"]
    _scope(wid, pid)
    row = _find(wid, pid, params["vid"])
    with storage.use_workspace(wid), storage.use_profile(pid):
        raw = documents.load_raw(pid, row["name"], row["ref"])
    return Response(
        body=raw.data,
        content_type=raw.content_type,
        headers={
            "Content-Disposition": content_disposition(raw.filename),
            "ETag": documents.etag_for_sha(raw.sha256),
            "Cache-Control": "no-cache",
        },
    )
