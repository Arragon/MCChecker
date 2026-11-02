"""M16-26 更新候选只读 API（创建由 sync 执行层调用 service）。"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..core.errors import INVALID_INPUT, AppError
from ..services import authz, sync_candidates, workspace_catalog
from ..services.context import RequestContext
from .router import Response, Router, query_param


def register(router: Router) -> None:
    router.add("GET", "/api/v3/workspaces/{wid}/sync/candidates", list_candidates)
    router.add("GET", "/api/v3/workspaces/{wid}/sync/candidates/{cid}", get_candidate)


def _scope(wid: str) -> None:
    workspace_catalog.get_workspace(wid)


def _int(raw: str, default: int) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def list_candidates(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid = params["wid"]
    _scope(wid)
    return Response(
        payload=sync_candidates.list_candidates(
            wid,
            profile_id=query_param(query, "profile_id") or None,
            state=query_param(query, "state") or None,
            limit=max(1, min(_int(query_param(query, "limit"), 50), 200)),
            cursor=query_param(query, "cursor") or "0",
        )
    )


def get_candidate(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid = params["wid"]
    _scope(wid)
    return Response(payload=sync_candidates.get_candidate(wid, params["cid"]))
