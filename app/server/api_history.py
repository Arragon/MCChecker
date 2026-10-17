"""历史版本与修改记录接口（§7.4；M2-07）。

handler 只做「解析参数 → 授权 → 调 `services.history` → 组装 `Response`」（§3.3）。
两个接口都是 `read` 档，访客也能看历史。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..services import authz, history
from ..services.context import RequestContext
from .router import Response, Router


def register(router: Router) -> None:
    router.add("GET", "/api/v1/p/{pid}/files/{name}/archives", list_archives)
    router.add("GET", "/api/v1/p/{pid}/files/{name}/records", list_records)


def list_archives(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/files/{name}/archives
    authz.authorize(ctx, "read")
    return Response(payload=history.archives(params["pid"], params["name"]))


def list_records(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/files/{name}/records
    authz.authorize(ctx, "read")
    return Response(payload=history.records(params["pid"], params["name"]))
