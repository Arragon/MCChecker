"""V3 工作区事件与恢复 API（M14-05 / INH-2154）。"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..core.errors import NOT_FOUND, AppError
from ..services import authz, config_v3_history, workspace_catalog
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param


def register(router: Router) -> None:
    router.add("GET", "/api/v3/workspaces/{wid}/profiles/{pid}/events", list_events)
    router.add("GET", "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/events", list_config_events)
    router.add("POST", "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/restore-plans", restore)
    router.add(
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/restore-plans/{plan_id}/undo",
        undo,
    )


def _scope(wid: str, pid: str) -> None:
    workspace_catalog.get_workspace(wid)
    if not any(str(p.get("id")) == pid for p in workspace_catalog.list_profiles(wid)):
        raise AppError(NOT_FOUND, "机型不存在", profile=pid)


def list_events(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload={"items": config_v3_history.list_events(
        params["pid"], query_param(query, "config_id"), query_param(query, "q")
    )})


def list_config_events(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload={"items": config_v3_history.list_events(
        params["pid"], params["cid"], query_param(query, "q")
    )})


def restore(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(status=201, payload=config_v3_history.restore(
        params["pid"], params["cid"], json_body(body), ctx.actor, ctx.ip
    ))


def undo(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(payload=config_v3_history.undo(
        params["pid"], params["cid"], params["plan_id"], ctx.actor, ctx.ip
    ))
