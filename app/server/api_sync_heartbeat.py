"""M16-34 连接保活/自动重登 API（workspace 级）。

路径：`/api/v3/workspaces/{wid}/connections/{cid}/heartbeat`。

- GET 只要 `read`，返回 `sync_heartbeat` 的安全状态投影（无 Cookie/凭据/明文）；
- PUT 配置保活开关与间隔，先要求 `integration_credentials_manage`，再要求独立
  运维 token（`X-Operator-Token`），并用 `If-Match` 做 revision CAS；
- POST `/run` 立即跑一次心跳：未启用或未注入 probe 时返回 202 操作
  `blocked/disabled`、`blocked/collector_offline`，绝不伪造成功。

probe 由部署端通过 `configure_probe` 注入（`(workspace_id, connection_id) ->
{status, session_valid, reason_code, renewal_observed}`）；本模块不联网、
不接触凭据明文。自动重登不是独立开关：它由凭据状态/失败原因推导，UI 只读展示。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ..core.errors import INVALID_INPUT, REVISION_CONFLICT, AppError
from ..services import (
    authz,
    sync_connections,
    sync_heartbeat,
    sync_operator_auth,
    sync_operations,
    workspace_catalog,
)
from ..services.context import RequestContext
from .router import Response, Router, json_body

OPERATOR_HEADER = "x-operator-token"
IDEMPOTENCY_HEADER = "idempotency-key"

_PROBE: Optional[Callable[[str, str], Dict[str, Any]]] = None


def configure_probe(
    probe: Optional[Callable[[str, str], Dict[str, Any]]],
) -> None:
    """部署接线：注入心跳 probe；未注入时 run 为 blocked/collector_offline。"""
    global _PROBE
    _PROBE = probe


def _probe() -> Optional[Callable[[str, str], Dict[str, Any]]]:
    return _PROBE


def register(router: Router) -> None:
    base = "/api/v3/workspaces/{wid}/connections/{cid}/heartbeat"
    router.add("GET", base, get_heartbeat)
    router.add("PUT", base, configure_heartbeat)
    router.add("POST", base + "/run", run_heartbeat)


def _scope(wid: str) -> None:
    workspace_catalog.get_workspace(wid)


def _connection(wid: str, cid: str) -> Dict[str, Any]:
    _scope(wid)
    return sync_connections.get_connection(wid, cid)


def _operator(ctx: RequestContext) -> None:
    authz.authorize(ctx, sync_operator_auth.OPERATOR_CAPABILITY)
    sync_operator_auth.require_operator((ctx.headers or {}).get(OPERATOR_HEADER))


def _if_match(ctx: RequestContext) -> int:
    raw = (ctx.headers or {}).get("if-match")
    if raw is None:
        raise AppError(REVISION_CONFLICT, "缺少 If-Match", field="expected_revision")
    try:
        return int(str(raw).strip().strip('"'))
    except ValueError:
        raise AppError(REVISION_CONFLICT, "If-Match 非法", field="expected_revision")


def _idempotency_key(ctx: RequestContext) -> Optional[str]:
    raw = (ctx.headers or {}).get(IDEMPOTENCY_HEADER)
    text = str(raw).strip() if raw is not None else ""
    return text or None


def _only_keys(payload: Dict[str, Any], allowed: Any) -> None:
    unknown = set(payload) - set(allowed)
    if unknown:
        raise AppError(INVALID_INPUT, "请求含未知字段", field="payload", keys=sorted(unknown))


def get_heartbeat(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid = params["wid"]
    _connection(wid, params["cid"])
    return Response(payload=sync_heartbeat.get_state(wid, params["cid"]))


def configure_heartbeat(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    _operator(ctx)
    wid = params["wid"]
    _connection(wid, params["cid"])
    payload = json_body(body)
    _only_keys(payload, {"interval_minutes", "enabled"})
    state = sync_heartbeat.configure(
        wid,
        params["cid"],
        payload.get("interval_minutes"),
        payload.get("enabled"),
        _if_match(ctx),
    )
    return Response(payload=state)


def run_heartbeat(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    _operator(ctx)
    wid = params["wid"]
    _connection(wid, params["cid"])
    payload = json_body(body)
    _only_keys(payload, set())
    op = sync_operations.create_operation(
        wid,
        params["cid"],
        "heartbeat_run",
        {},
        idempotency_key=_idempotency_key(ctx),
    )
    op = sync_operations.run_operation(
        wid, op["op_id"], heartbeat_probe=_probe()
    )
    return Response(status=202, payload={"operation": op})
