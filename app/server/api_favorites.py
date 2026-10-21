"""收藏接口（§7.7；M2-01）。

handler 只做「解析参数 → 授权 → 调 `services.favorites` → 组装 `Response`」；
落盘、锁与修订号递增都在 service 层（§3.3）。机型存在性与 `use_profile` 由
`http.py` 统一处理，这里不再检查机型。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..core.errors import INVALID_INPUT, AppError
from ..services import authz, favorites
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param

FILE_FIELD = "file"


def register(router: Router) -> None:
    router.add("GET", "/api/v1/p/{pid}/favorites", list_favorites)
    router.add("POST", "/api/v1/p/{pid}/favorites", add_favorite)
    router.add("PATCH", "/api/v1/p/{pid}/favorites", patch_favorite)
    router.add("DELETE", "/api/v1/p/{pid}/favorites", delete_favorite)


def list_favorites(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/favorites
    authz.authorize(ctx, "read")
    return Response(payload={"groups": favorites.list_groups(params["pid"])})


def add_favorite(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # POST /api/v1/p/{pid}/favorites  {"file","id"}
    authz.authorize(ctx, "favorites_write")
    payload = json_body(body)
    file_name = str(payload.get(FILE_FIELD) or "").strip()
    node_id = str(payload.get("id") or "").strip()
    if not file_name:
        raise AppError(INVALID_INPUT, "缺少 file", field=FILE_FIELD)
    if not node_id:
        raise AppError(INVALID_INPUT, "缺少 id", field="id")
    item, created = favorites.add(params["pid"], file_name, node_id)
    return Response(status=201 if created else 200, payload={"item": item})


def patch_favorite(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # PATCH /api/v1/p/{pid}/favorites  {"file","path","note"}
    authz.authorize(ctx, "favorites_write")
    payload = json_body(body)
    file_name = str(payload.get(FILE_FIELD) or "").strip()
    path = str(payload.get("path") or "").strip()
    if not file_name:
        raise AppError(INVALID_INPUT, "缺少 file", field=FILE_FIELD)
    if not path:
        raise AppError(INVALID_INPUT, "缺少 path", field="path")
    item = favorites.set_note(params["pid"], file_name, path, str(payload.get("note") or ""))
    return Response(payload={"item": item})


def delete_favorite(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # DELETE /api/v1/p/{pid}/favorites?file=&path=   （不带 path 清空该文件）
    authz.authorize(ctx, "favorites_write")
    file_name = query_param(query, FILE_FIELD)
    if not file_name:
        raise AppError(INVALID_INPUT, "缺少 file", field=FILE_FIELD)
    path = query_param(query, "path") or None
    return Response(payload={"removed": favorites.remove(params["pid"], file_name, path)})
