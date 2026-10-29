"""V3 固定配置 / 软件版本 / 版本图 API（M12-01 / INH-2136）。"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..core.errors import NOT_FOUND, AppError
from ..services import authz, config_v3_catalog, workspace_catalog
from ..services.context import RequestContext
from .router import Response, Router, json_body


def register(router: Router) -> None:
    router.add("GET", "/api/v3/workspaces/{wid}/profiles/{pid}/configs", list_configs)
    router.add("POST", "/api/v3/workspaces/{wid}/profiles/{pid}/configs", create_config)
    router.add("GET", "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}", get_config)
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/releases",
        list_releases,
    )
    router.add(
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/releases",
        create_release,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/graph",
        graph,
    )


def _scope(wid: str, pid: str) -> None:
    workspace_catalog.get_workspace(wid)
    if not any(str(p.get("id")) == pid for p in workspace_catalog.list_profiles(wid)):
        raise AppError(NOT_FOUND, "机型不存在", profile=pid)


def list_configs(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload={"items": config_v3_catalog.list_configs(params["pid"])})


def create_config(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(status=201, payload={"config": config_v3_catalog.create_config(params["pid"], json_body(body))})


def get_config(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload={"config": config_v3_catalog.get_config(params["pid"], params["cid"])})


def list_releases(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload={"items": config_v3_catalog.list_releases(params["pid"], params["cid"])})


def create_release(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(
        status=201,
        payload={"release": config_v3_catalog.create_release(params["pid"], params["cid"], json_body(body))},
    )


def graph(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload=config_v3_catalog.graph(params["pid"], params["cid"]))
