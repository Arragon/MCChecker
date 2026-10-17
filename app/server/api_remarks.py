"""修改建议接口（§7.8 的 GET/POST；M2-03）。

审阅提交 `POST /review/submit` 属 M3-02，本模块不提供。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..core.errors import INVALID_INPUT, AppError
from ..services import authz, review
from ..services.context import RequestContext
from .router import Response, json_body, query_param


def register(router) -> None:
    router.add("GET", "/api/v1/p/{pid}/remarks", list_remarks)
    router.add("POST", "/api/v1/p/{pid}/remarks", submit_remark)


def list_remarks(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/remarks?file=&status=
    authz.authorize(ctx, "read")
    return Response(
        payload={
            "items": review.list_items(
                params["pid"], query_param(query, "file") or None, query_param(query, "status") or None
            )
        }
    )


def submit_remark(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # POST /api/v1/p/{pid}/remarks
    #   {"file","sha256","loc","proposed_value"}；`id`/`label`/`original_value` 由服务端从当前文档取
    authz.authorize(ctx, "remarks_submit")
    payload = json_body(body)
    file_name = str(payload.get("file") or "").strip()
    loc = str(payload.get("loc") or "").strip()
    if not file_name:
        raise AppError(INVALID_INPUT, "缺少 file", field="file")
    if not loc:
        raise AppError(INVALID_INPUT, "缺少 loc", field="loc")
    item = review.submit(
        params["pid"],
        ctx.actor,
        file_name,
        loc,
        str(payload.get("proposed_value") or ""),
        str(payload.get("sha256") or ""),
    )
    return Response(status=201, payload={"item": item})
