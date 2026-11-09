"""V3 精确模板导出 API（M14-04 / INH-2153）。"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from ..services import authz, config_v3_export, config_v3_history, workspace_catalog
from ..services.context import RequestContext
from .api_documents import content_disposition
from .router import Response, Router, json_body


def register(router: Router) -> None:
    base = "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/export-plans"
    router.add("POST", base, create_plan)
    router.add("GET", base + "/{plan_id}", get_plan)
    router.add("POST", base + "/{plan_id}/commit", commit_plan)
    router.add("GET", "/api/v3/workspaces/{wid}/profiles/{pid}/exports/{artifact_id}/download", download)


def _scope(wid: str, pid: str) -> None:
    workspace_catalog.get_workspace(wid)
    if not any(str(p.get("id")) == pid for p in workspace_catalog.list_profiles(wid)):
        raise AppError(NOT_FOUND, "机型不存在", profile=pid)


def create_plan(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(status=201, payload={"plan": config_v3_export.create_plan(
        params["pid"], params["cid"], json_body(body)
    )})


def get_plan(ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    try:
        offset = int((query.get("offset") or ["0"])[0])
        limit = int((query.get("limit") or ["50"])[0])
    except ValueError:
        raise AppError(INVALID_INPUT, "映射分页参数必须为整数", field="offset")
    return Response(payload={"plan": config_v3_export.get_plan(params["pid"], params["cid"], params["plan_id"], offset, limit)})


def commit_plan(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    result = config_v3_export.commit(
        params["pid"], params["cid"], params["plan_id"], json_body(body)
    )
    config_v3_history.record(
        params["pid"], params["cid"], "export", actor=ctx.actor, ip=ctx.ip,
        source={"revision_id": result["artifact"]["revision_id"]},
        after={"artifact_id": (result.get("artifact") or {}).get("artifact_id"),
               "digest": (result.get("artifact") or {}).get("digest")},
    )
    return Response(payload=result)


def download(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    data, filename = config_v3_export.artifact_bytes(params["pid"], params["artifact_id"])
    return Response(
        body=data,
        content_type="application/octet-stream",
        headers={"Content-Disposition": content_disposition(filename),
                 "Cache-Control": "no-store"},
    )
