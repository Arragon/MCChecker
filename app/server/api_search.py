"""搜索接口（§7.6；M2-05）。

`read` 档：访客也能搜（§4.2）。`limit`/`offset` 解析不出数字时按默认值处理，
与模板 mock 的 `+x || 默认` 一致——分页参数不该让一次搜索失败。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..services import authz
from ..services import search as search_service
from ..services.context import RequestContext
from .router import Response, Router, query_param


def register(router: Router) -> None:
    router.add("GET", "/api/v1/p/{pid}/search", search_nodes)


def _int(raw: str, default: int) -> int:
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def search_nodes(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/search?q=&file=&limit=200&offset=0
    authz.authorize(ctx, "read")
    return Response(
        payload=search_service.search(
            params["pid"],
            query_param(query, "q"),
            query_param(query, "file"),
            _int(query_param(query, "limit"), search_service.DEFAULT_LIMIT),
            _int(query_param(query, "offset"), 0),
        )
    )
