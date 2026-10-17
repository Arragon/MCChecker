"""对比接口（§7.5；M2-08）。

`POST` 但属于 `read` 档：它只算差异、不写任何权威文件。走 POST 是因为两侧引用与
`text_limit` 放进请求体比塞进 URL 更自然（§7.5）。写请求的 `X-Requested-With`
硬条件由 HTTP 层统一把关。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..core import differ
from ..services import authz, compare
from ..services.context import RequestContext
from .router import Response, Router, json_body


def register(router: Router) -> None:
    router.add("POST", "/api/v1/p/{pid}/compare", compare_versions)


def compare_versions(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # POST /api/v1/p/{pid}/compare  {"base": {...}, "target": {...}, "text_limit": 2000}
    authz.authorize(ctx, "read")
    payload = json_body(body)
    return Response(
        payload=compare.compare(
            params["pid"],
            payload.get("base"),
            payload.get("target"),
            payload.get("text_limit", differ.DEFAULT_TEXT_LIMIT),
            ctx.cid,
            ctx.ip,
        )
    )
