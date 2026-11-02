"""M16-08 工作区更新计划 API。

`GET/PUT /api/v3/workspaces/{wid}/sync/policy`；
`POST /api/v3/workspaces/{wid}/sync/source-policy-batches`（全有或全无 CAS）。
首版写权限复用 `settings_write`；M16-30/38 再收口独立 sync 能力。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core.errors import INVALID_INPUT, REVISION_CONFLICT, AppError
from ..services import authz, sync_policy, workspace_catalog
from ..services.context import RequestContext
from .router import Response, Router, json_body


def register(router: Router) -> None:
    router.add("GET", "/api/v3/workspaces/{wid}/sync/policy", get_policy)
    router.add("PUT", "/api/v3/workspaces/{wid}/sync/policy", put_policy)
    router.add(
        "POST",
        "/api/v3/workspaces/{wid}/sync/source-policy-batches",
        batch_source_policy,
    )


def _scope(wid: str) -> None:
    workspace_catalog.get_workspace(wid)


def _if_match(ctx: RequestContext) -> int:
    raw = (ctx.headers or {}).get("if-match")
    if raw is None:
        raise AppError(REVISION_CONFLICT, "缺少 If-Match", field="expected_revision")
    try:
        return int(str(raw).strip().strip('"'))
    except ValueError:
        raise AppError(REVISION_CONFLICT, "If-Match 非法", field="expected_revision")


def get_policy(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    _scope(params["wid"])
    return Response(payload=sync_policy.get_policy(params["wid"]))


def put_policy(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "settings_write")
    _scope(params["wid"])
    payload = json_body(body)
    if not isinstance(payload, dict):
        raise AppError(INVALID_INPUT, "payload 必须是对象", field="payload")
    return Response(
        payload=sync_policy.save_policy(
            params["wid"],
            _if_match(ctx),
            payload,
        )
    )


def batch_source_policy(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "settings_write")
    _scope(params["wid"])
    payload = json_body(body)
    if not isinstance(payload, dict) or "updates" not in payload:
        raise AppError(INVALID_INPUT, "payload 必须包含 updates", field="updates")
    return Response(
        payload={
            "items": sync_policy.batch_update_source_schedule(
                params["wid"],
                payload["updates"],
            )
        }
    )
