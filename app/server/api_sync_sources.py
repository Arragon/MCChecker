"""M16-05 Workspace 级更新源 API。

路径统一为 `/api/v3/workspaces/{wid}/sync/sources`；旧 `/api/v1/p/{pid}/sources`
保留只读兼容。写操作用 `If-Match` 做 source revision CAS；首版写权限复用
`settings_write`，M16-30 再收口独立的 sync 能力。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core.errors import INVALID_INPUT, REVISION_CONFLICT, AppError
from ..services import authz, sync_sources, sync_tasks, workspace_catalog
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param


def register(router: Router) -> None:
    router.add("GET", "/api/v3/workspaces/{wid}/sync/sources", list_sources)
    router.add("POST", "/api/v3/workspaces/{wid}/sync/sources", create_source)
    router.add("PATCH", "/api/v3/workspaces/{wid}/sync/sources/{sid}", update_source)
    router.add("DELETE", "/api/v3/workspaces/{wid}/sync/sources/{sid}", delete_source)
    router.add("POST", "/api/v3/workspaces/{wid}/sync/sources/{sid}/run", run_source)


def _scope(wid: str) -> None:
    workspace_catalog.get_workspace(wid)


def _int(raw: str, default: int) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _if_match(ctx: RequestContext) -> int:
    raw = (ctx.headers or {}).get("if-match")
    if raw is None:
        raise AppError(REVISION_CONFLICT, "缺少 If-Match", field="expected_revision")
    try:
        return int(str(raw).strip().strip('"'))
    except ValueError:
        raise AppError(REVISION_CONFLICT, "If-Match 非法", field="expected_revision")


def list_sources(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid = params["wid"]
    _scope(wid)
    return Response(
        payload=sync_sources.list_sources(
            wid,
            profile_id=query_param(query, "profile_id") or None,
            kind=query_param(query, "kind") or None,
            status=query_param(query, "status") or None,
            query=query_param(query, "q") or "",
            limit=max(1, min(_int(query_param(query, "limit"), 50), 200)),
            cursor=query_param(query, "cursor") or "0",
        )
    )


def create_source(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "settings_write")
    wid = params["wid"]
    _scope(wid)
    payload = json_body(body)
    if not isinstance(payload, dict):
        raise AppError(INVALID_INPUT, "payload 必须是对象", field="payload")
    return Response(status=201, payload=sync_sources.create_source(wid, payload))


def update_source(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "settings_write")
    wid = params["wid"]
    _scope(wid)
    payload = json_body(body)
    if not isinstance(payload, dict):
        raise AppError(INVALID_INPUT, "payload 必须是对象", field="payload")
    return Response(
        payload=sync_sources.update_source(
            wid,
            params["sid"],
            _if_match(ctx),
            payload,
        )
    )


def run_source(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    """单项更新：用来源所属 profile 投一个 sync_run；不改来源本身。"""
    authz.authorize(ctx, "tasks_run")
    wid = params["wid"]
    _scope(wid)
    source = sync_sources.get_source(wid, params["sid"])
    run_ctx = RequestContext(
        ip=ctx.ip,
        cid=ctx.cid,
        profile_id=source["profile_id"],
        workspace_id=wid,
        actor=ctx.actor,
        headers=ctx.headers,
    )
    result = sync_tasks.submit_sync_run(run_ctx, [source])
    return Response(status=202, payload=result)


def delete_source(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "settings_write")
    wid = params["wid"]
    _scope(wid)
    return Response(
        payload=sync_sources.delete_source(
            wid,
            params["sid"],
            _if_match(ctx),
        )
    )
