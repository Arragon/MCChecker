"""临时文件接口（§7.4；M2-10）。

`POST /temp` 任何角色可用（`temp_upload`），读取只认上传者本人：所有者 = 同一个
`mc_cid` 且同一个 IP，由 `services.temp_store` 判定（他人 403、过期 404，且**绝不
回退去读同名服务器文件**）。
"""

from __future__ import annotations

from functools import partial
from typing import Dict, List, Optional

from ..config import Settings
from ..services import authz, temp_store, upload
from ..services.context import RequestContext
from ..services.documents import RAW_CONTENT_TYPES
from .api_documents import content_disposition
from .router import Response, Router, query_param

DEFAULT_UPLOAD_NAME = "upload.xml"


def register(router: Router, settings: Settings) -> None:
    router.add(
        "POST", "/api/v1/p/{pid}/temp", partial(create_temp, settings=settings)
    )
    router.add(
        "GET",
        "/api/v1/p/{pid}/temp/{token}/document",
        partial(get_temp_document, settings=settings),
    )
    router.add(
        "GET", "/api/v1/p/{pid}/temp/{token}/raw", partial(get_temp_raw, settings=settings)
    )


def create_temp(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/temp   原文字节 + X-Filename（C26）
    authz.authorize(ctx, "temp_upload")
    name = ctx.filename or query_param(query, "name") or DEFAULT_UPLOAD_NAME
    return Response(
        status=201,
        payload=upload.create_temp(
            params["pid"], ctx.cid, ctx.ip, name, body or b"", settings
        ),
    )


def get_temp_document(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/temp/{token}/document
    authz.authorize(ctx, "read")
    return Response(
        payload=temp_store.document(params["pid"], params["token"], ctx.cid, ctx.ip)
    )


def get_temp_raw(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/temp/{token}/raw
    authz.authorize(ctx, "read")
    data, filename = temp_store.raw(params["pid"], params["token"], ctx.cid, ctx.ip)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return Response(
        body=data,
        content_type=RAW_CONTENT_TYPES.get(ext, "application/octet-stream"),
        headers={"Content-Disposition": content_disposition(filename), "Cache-Control": "no-store"},
    )
