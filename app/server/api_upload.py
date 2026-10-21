"""上传保存与链接导入接口（§7.4；M2-10）。

权限（§4.2）：`PUT /files/{name}` 与 `import-url` 的 `persist=true` 要 `upload_persist`
（部署者）；`persist=false` 只是把下载结果放进临时区，任何角色都可以（`temp_upload`）。
"""

from __future__ import annotations

from functools import partial
from typing import Dict, List, Optional

from ..config import Settings
from ..services import authz, upload
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param


def register(router: Router, settings: Settings) -> None:
    router.add(
        "PUT", "/api/v1/p/{pid}/files/{name}", partial(save_file, settings=settings)
    )
    router.add(
        "POST", "/api/v1/p/{pid}/import-url", partial(import_url, settings=settings)
    )


def save_file(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # PUT /api/v1/p/{pid}/files/{name}?overwrite=1   原文字节
    authz.authorize(ctx, "upload_persist")
    overwrite = query_param(query, "overwrite") == "1"
    outcome = upload.save_bytes(
        params["pid"],
        params["name"],
        body or b"",
        overwrite,
        settings.upload_max_bytes,
        actor=getattr(ctx.actor, "name", "") or "",
    )
    return Response(status=outcome.status, payload=outcome.payload)


def import_url(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/import-url  {"url","name","persist","overwrite"}
    payload = json_body(body)
    persist = bool(payload.get("persist"))
    authz.authorize(ctx, "upload_persist" if persist else "temp_upload")
    return Response(
        payload=upload.import_url(
            params["pid"],
            ctx.cid,
            ctx.ip,
            str(payload.get("url") or ""),
            str(payload.get("name") or ""),
            persist,
            bool(payload.get("overwrite")),
            settings,
            actor=getattr(ctx.actor, "name", "") or "",
        )
    )
