"""修订号接口（§7.3；M2-01）。

`GET /api/v1/p/{pid}/revision` 只读内存计数，不解析请求体、不碰磁盘。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..services import revision
from ..services.context import RequestContext
from .router import Response


def get_revision(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/revision
    return Response(payload=revision.snapshot(params["pid"]))
