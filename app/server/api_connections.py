"""M16-30 连接与认证管理 API（workspace 级）。

路径：`/api/v3/workspaces/{wid}/connections`。读接口只给 `read` 能力并返回
安全投影（连接元数据 + 凭据状态；无 account/secret）。所有写接口先要求
`integration_credentials_manage`（管理员），再要求独立运维 token
（`MCHECKER_OPERATOR_TOKEN`，头 `X-Operator-Token`），两者缺一即 403。

明文只在创建候选的请求体里短暂存在，交给部署端注入的 vault；本应用不保存、
不回显。vault/probe 未配置时：候选/撤销/激活返回 503，probe 操作收在
`blocked/collector_offline`——绝不伪造成功。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ..core.errors import (
    CONFIG_DISABLED,
    INVALID_INPUT,
    NOT_FOUND,
    REVISION_CONFLICT,
    SOURCE_CHANGED,
    STORAGE_FAILURE,
    AppError,
)
from ..core.sync_models import validate_id, validate_revision
from ..services import (
    authz,
    sync_catalog,
    sync_connections,
    sync_credentials,
    sync_operator_auth,
    sync_operations,
    workspace_catalog,
)
from ..services.context import RequestContext
from .router import Response, Router, json_body

OPERATOR_HEADER = "x-operator-token"
IDEMPOTENCY_HEADER = "idempotency-key"

_VAULT: Any = None
_PROBE: Optional[Callable[[str, str, str], Dict[str, Any]]] = None


def configure_vault(vault: Any) -> None:
    """部署接线：注入 SecretStore 适配器；传 None 表示未配置。"""
    global _VAULT
    _VAULT = vault


def configure_probe(probe: Optional[Callable[[str, str, str], Dict[str, Any]]]) -> None:
    """部署接线：注入 collector probe；未注入时操作 blocked/collector_offline。"""
    global _PROBE
    _PROBE = probe


def _vault() -> Any:
    return _VAULT


def _probe() -> Optional[Callable[[str, str, str], Dict[str, Any]]]:
    return _PROBE


def register(router: Router) -> None:
    base = "/api/v3/workspaces/{wid}/connections"
    router.add("GET", base, list_connections)
    router.add("POST", base, create_connection)
    router.add("PATCH", base + "/{cid}", update_connection)
    router.add("GET", base + "/{cid}/credentials", get_credentials)
    router.add("GET", base + "/{cid}/catalog", get_catalog)
    router.add("POST", base + "/{cid}/credentials/candidates", create_candidate)
    router.add(
        "POST",
        base + "/{cid}/credentials/candidates/{candidate_id}/activate",
        activate_candidate,
    )
    router.add("POST", base + "/{cid}/credentials/probe", probe_credentials)
    router.add("POST", base + "/{cid}/credentials/revoke", revoke_credentials)
    router.add("GET", base + "/{cid}/operations/{opid}", get_operation)


def _scope(wid: str) -> None:
    workspace_catalog.get_workspace(wid)


def _connection(wid: str, cid: str) -> Dict[str, Any]:
    """先确认 workspace 存在，再确认连接属于该 workspace（跨 W 一律 404）。"""
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


def _optional_if_match(ctx: RequestContext) -> Optional[int]:
    if (ctx.headers or {}).get("if-match") is None:
        return None
    return _if_match(ctx)


def _idempotency_key(ctx: RequestContext) -> Optional[str]:
    raw = (ctx.headers or {}).get(IDEMPOTENCY_HEADER)
    text = str(raw).strip() if raw is not None else ""
    return text or None


def _only_keys(payload: Dict[str, Any], allowed: Any) -> None:
    unknown = set(payload) - set(allowed)
    if unknown:
        raise AppError(INVALID_INPUT, "请求含未知字段", field="payload", keys=sorted(unknown))


def _summary(wid: str, connection: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(connection)
    item["credential"] = sync_credentials.get_credential_state(wid, connection["connection_id"])
    return item


def list_connections(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid = params["wid"]
    _scope(wid)
    return Response(
        payload={"items": [_summary(wid, item) for item in sync_connections.list_connections(wid)]}
    )


def create_connection(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    _operator(ctx)
    wid = params["wid"]
    _scope(wid)
    payload = json_body(body)
    _only_keys(payload, {"name", "origin", "protocol", "enabled"})
    item = sync_connections.create_connection(
        wid,
        payload.get("name"),
        payload.get("origin"),
        protocol=payload.get("protocol") or "sda_v1",
        enabled=payload.get("enabled", True),
    )
    return Response(status=201, payload=item)


def update_connection(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    _operator(ctx)
    wid = params["wid"]
    _connection(wid, params["cid"])
    payload = json_body(body)
    return Response(
        payload=sync_connections.update_connection(
            wid, params["cid"], _if_match(ctx), payload
        )
    )


def get_credentials(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid = params["wid"]
    _connection(wid, params["cid"])
    return Response(payload=sync_credentials.get_credential_state(wid, params["cid"]))


def create_candidate(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    _operator(ctx)
    wid = params["wid"]
    _connection(wid, params["cid"])
    vault = _vault()
    if vault is None:
        raise AppError(CONFIG_DISABLED, "凭据存储未配置", field="vault")
    payload = json_body(body)
    _only_keys(payload, {"username", "password"})
    state = sync_credentials.begin_candidate(
        wid,
        params["cid"],
        payload.get("username"),
        payload.get("password"),
        vault,
        expected_revision=_optional_if_match(ctx),
    )
    return Response(status=201, payload=state)


def _resolve_candidate(wid: str, cid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    state = sync_credentials.get_credential_state(wid, cid)
    candidate = state.get("candidate")
    requested = payload.get("candidate_id")
    if requested is not None:
        requested = validate_id(requested, "candidate_id")
    if not isinstance(candidate, dict):
        raise AppError(NOT_FOUND, "没有待验证的凭据候选", field="candidate_id")
    if requested is not None and candidate.get("candidate_id") != requested:
        raise AppError(NOT_FOUND, "凭据候选不存在或已被替换", field="candidate_id")
    return candidate


def probe_credentials(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    _operator(ctx)
    wid = params["wid"]
    _connection(wid, params["cid"])
    payload = json_body(body)
    _only_keys(payload, {"candidate_id"})
    candidate = _resolve_candidate(wid, params["cid"], payload)
    candidate_id = candidate["candidate_id"]
    op = sync_operations.create_operation(
        wid,
        params["cid"],
        "credential_probe",
        {
            "candidate_id": candidate_id,
            "probe_epoch": int(candidate.get("auth_epoch") or 0),
        },
        idempotency_key=_idempotency_key(ctx),
    )
    op = sync_operations.run_operation(wid, op["op_id"], probe=_probe(), vault=_vault())
    return Response(status=202, payload={"operation": op})


def activate_candidate(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    _operator(ctx)
    wid = params["wid"]
    _connection(wid, params["cid"])
    candidate_id = validate_id(params["candidate_id"], "candidate_id")
    payload = json_body(body)
    _only_keys(payload, {"probe_epoch"})
    probe_epoch = validate_revision(payload.get("probe_epoch"), "probe_epoch")

    state = sync_credentials.get_credential_state(wid, params["cid"])
    candidate = state.get("candidate")
    if not isinstance(candidate, dict) or candidate.get("candidate_id") != candidate_id:
        raise AppError(NOT_FOUND, "凭据候选不存在或已被替换", field="candidate_id")
    if candidate.get("state") != "pending":
        raise AppError(REVISION_CONFLICT, "凭据候选已结束", field="candidate_id")
    if int(candidate.get("auth_epoch") or 0) != probe_epoch:
        raise AppError(
            SOURCE_CHANGED,
            "认证纪元已变化，晚到的 probe 不能激活",
            field="probe_epoch",
        )

    vault = _vault()
    if vault is None:
        raise AppError(CONFIG_DISABLED, "凭据存储未配置", field="vault")
    op = sync_operations.create_operation(
        wid,
        params["cid"],
        "credential_activate",
        {"candidate_id": candidate_id, "probe_epoch": probe_epoch},
        idempotency_key=_idempotency_key(ctx),
    )
    op = sync_operations.run_operation(wid, op["op_id"], vault=vault)
    return Response(status=202, payload={"operation": op})


def revoke_credentials(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    _operator(ctx)
    wid = params["wid"]
    _connection(wid, params["cid"])
    expected_revision = _if_match(ctx)
    vault = _vault()
    if vault is None:
        raise AppError(CONFIG_DISABLED, "凭据存储未配置", field="vault")
    op = sync_operations.create_operation(
        wid,
        params["cid"],
        "credential_revoke",
        {"expected_revision": expected_revision},
        idempotency_key=_idempotency_key(ctx),
    )
    op = sync_operations.run_operation(wid, op["op_id"], vault=vault)
    if op.get("state") != "succeeded":
        if op.get("reason_code") == "revision_conflict":
            raise AppError(REVISION_CONFLICT, "凭据元数据 revision 已变化", field="expected_revision")
        raise AppError(STORAGE_FAILURE, "撤销失败，凭据仍保留", field="credentials")
    credential = (op.get("result") or {}).get("credential")
    if not isinstance(credential, dict):
        credential = sync_credentials.get_credential_state(wid, params["cid"])
    return Response(payload=credential)


def get_operation(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid = params["wid"]
    _connection(wid, params["cid"])
    op = sync_operations.get_operation(wid, params["opid"])
    if op.get("connection_id") != params["cid"]:
        raise AppError(NOT_FOUND, "操作不存在", field="op_id")
    return Response(payload={"operation": op})


def get_catalog(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    authz.authorize(ctx, "read")
    wid = params["wid"]
    _connection(wid, params["cid"])
    return Response(payload={"items": sync_catalog.read_catalog(wid, params["cid"])})
