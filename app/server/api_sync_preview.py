"""M16-10 更新预览 API。

`POST /api/v3/workspaces/{wid}/sync/previews` 生成冻结 read_set；
`GET .../previews/{preview_id}` 读取；`POST .../validate` 重新核对
source/connection/binding/policy revision。预览默认 5 分钟过期。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..core.errors import INVALID_INPUT, AppError
from ..services import authz, sync_preview, workspace_catalog
from ..services.context import RequestContext
from .router import Response, Router, json_body


def register(router: Router) -> None:
    router.add("POST", "/api/v3/workspaces/{wid}/sync/previews", create_preview)
    router.add("GET", "/api/v3/workspaces/{wid}/sync/previews/{preview_id}", get_preview)
    router.add(
        "POST",
        "/api/v3/workspaces/{wid}/sync/previews/{preview_id}/validate",
        validate_preview,
    )


def _scope(wid: str) -> None:
    workspace_catalog.get_workspace(wid)


def create_preview(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "settings_write")
    _scope(params["wid"])
    payload = json_body(body)
    if not isinstance(payload, dict):
        raise AppError(INVALID_INPUT, "payload 必须是对象", field="payload")
    return Response(status=201, payload=sync_preview.create_preview(params["wid"], payload))


def get_preview(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    _scope(params["wid"])
    return Response(payload=sync_preview.get_preview(params["wid"], params["preview_id"]))


def validate_preview(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    _scope(params["wid"])
    return Response(payload=sync_preview.validate_preview(params["wid"], params["preview_id"]))
