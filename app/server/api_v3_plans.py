"""V3 提取计划 API（M13-05 / INH-2147）。

服务端保存全量计划与逐行决议；浏览器只按页取行，3000+ 行不需要一次加载。
创建/查询/批量决议都只改计划，不产生任何参数。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..core.errors import NOT_FOUND, AppError
from ..services import authz, config_v3_merge_plans, config_v3_plans, workspace_catalog
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param


def register(router: Router) -> None:
    base = "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/extraction-plans"
    router.add("POST", base, create_plan)
    router.add("GET", base + "/{plan_id}", get_plan)
    router.add("GET", base + "/{plan_id}/rows", plan_rows)
    router.add("PATCH", base + "/{plan_id}/decisions", patch_decisions)
    router.add("POST", base + "/{plan_id}/recompute", recompute_plan)
    router.add("POST", base + "/{plan_id}/commit", commit_plan)
    merge_base = "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/merge-plans"
    router.add("POST", merge_base, create_merge_plan)
    router.add("GET", merge_base + "/{plan_id}", get_merge_plan)
    router.add("GET", merge_base + "/{plan_id}/rows", merge_plan_rows)
    router.add("PATCH", merge_base + "/{plan_id}/decisions", patch_merge_decisions)
    router.add("POST", merge_base + "/{plan_id}/commit", commit_merge_plan)


def _scope(wid: str, pid: str) -> None:
    workspace_catalog.get_workspace(wid)
    if not any(str(p.get("id")) == pid for p in workspace_catalog.list_profiles(wid)):
        raise AppError(NOT_FOUND, "机型不存在", profile=pid)


def _int(raw: str, default: int) -> int:
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def create_plan(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    payload = json_body(body)
    return Response(
        status=201,
        payload={
            "plan": config_v3_plans.create(
                params["wid"], params["pid"], params["cid"], payload.get("version_id")
            )
        },
    )


def get_plan(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(
        payload={
            "plan": config_v3_plans.get(
                params["wid"], params["pid"], params["cid"], params["plan_id"]
            )
        }
    )


def plan_rows(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(
        payload=config_v3_plans.rows(
            params["wid"],
            params["pid"],
            params["cid"],
            params["plan_id"],
            _int(query_param(query, "offset"), 0),
            _int(query_param(query, "limit"), 50),
            query_param(query, "filter") or "all",
            query_param(query, "q"),
        )
    )


def patch_decisions(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(
        payload=config_v3_plans.decisions(
            params["wid"],
            params["pid"],
            params["cid"],
            params["plan_id"],
            json_body(body),
        )
    )


def recompute_plan(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    payload = json_body(body) if body else {}
    result = config_v3_plans.recompute(
        params["wid"],
        params["pid"],
        params["cid"],
        params["plan_id"],
        payload.get("version_id"),
    )
    preserved = result.pop("preserved", 0)
    return Response(payload={"plan": result, "preserved": preserved})


def commit_plan(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(
        payload={
            "result": config_v3_plans.commit(
                params["wid"], params["pid"], params["cid"], params["plan_id"], json_body(body)
            )
        }
    )


def create_merge_plan(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(
        status=201,
        payload={"plan": config_v3_merge_plans.create(
            params["wid"], params["pid"], params["cid"], json_body(body)
        )},
    )


def get_merge_plan(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload={"plan": config_v3_merge_plans.get(
        params["wid"], params["pid"], params["cid"], params["plan_id"]
    )})


def merge_plan_rows(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload=config_v3_merge_plans.rows(
        params["wid"], params["pid"], params["cid"], params["plan_id"],
        _int(query_param(query, "offset"), 0),
        _int(query_param(query, "limit"), 50),
        query_param(query, "filter") or "all",
        query_param(query, "q"),
    ))


def patch_merge_decisions(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(payload=config_v3_merge_plans.decisions(
        params["wid"], params["pid"], params["cid"], params["plan_id"], json_body(body)
    ))


def commit_merge_plan(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(payload=config_v3_merge_plans.commit(
        params["wid"], params["pid"], params["cid"], params["plan_id"], json_body(body)
    ))
