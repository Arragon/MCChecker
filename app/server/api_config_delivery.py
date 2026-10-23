"""交付记录与回读核验接口（M7-09 / M7-10）。

两组入口，一起交付是因为它们回答同一个问题：**"到底有没有打进去、打进去的是不是它"**。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/v1/p/{pid}/config/deliveries` | 登记列表（`?artifact_id=&result=`） |
| POST | `/api/v1/p/{pid}/config/deliveries` | 登记一次导入结果（新增对象，201） |
| GET  | `/api/v1/p/{pid}/config/deliveries/summary` | 逐态计数 + "能声称什么" |
| GET  | `/api/v1/p/{pid}/config/deliveries/{did}` | 单条详情 |
| GET  | `/api/v1/p/{pid}/config/artifacts/{aid}/verifications` | 历次核验（新的在前） |
| POST | `/api/v1/p/{pid}/config/artifacts/{aid}/verifications` | 跑一次核验并落档（201） |
| GET  | `/api/v1/p/{pid}/config/verifications/{vid}` | 单份核验报告 |

权限：登记与核验都用 `config_artifact_generate`——它们都属于"交付这个动作"的一部分，
不另造一个"交付专用"能力键（多一个键就多一处配错的机会）。读取仍是 `config_read`。

**没有"下载即已导入"的入口**：这里没有任何由产物下载触发的写路径。登记必须是人做的。
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from ..services import (
    authz,
    config_artifacts,
    config_delivery,
    config_gate,
    config_store,
    config_verify,
)
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param


def _read(ctx: RequestContext) -> None:
    config_gate.require_read()
    authz.authorize(ctx, "config_read")


def _write(ctx: RequestContext) -> None:
    # 与生成产物同一能力键：交付登记不是新的权力，是同一件事的后半段
    authz.authorize(ctx, "config_artifact_generate")
    config_gate.require_write()


def _repo(settings: Settings):
    return config_store.repository(settings)


def _optional(query: Dict[str, List[str]], name: str) -> Optional[str]:
    value = query_param(query, name)
    return value if value else None


def _idempotency(ctx: RequestContext) -> Optional[str]:
    key = str((ctx.headers or {}).get("idempotency-key") or "").strip()
    if not key:
        return None
    if len(key) > 128:
        raise AppError(INVALID_INPUT, "Idempotency-Key 过长（上限 128 字符）")
    return key


def _pid(ctx: RequestContext, params: Dict[str, str]) -> str:
    pid = params.get("pid") or ctx.profile_id
    if not pid:
        raise AppError(INVALID_INPUT, "缺少机型", field="profile_id")
    return str(pid)


def _same_profile(owner: Any, pid: str, field: str, value: Any) -> None:
    """跨机型按 ID 直读一律当不存在：与产物接口同一口径，不泄露存在性。"""
    owner_text = str(owner or "")
    if owner_text and owner_text != str(pid):
        raise AppError(NOT_FOUND, "对象不存在", **{field: value})


def _body_profile(payload: Dict[str, Any], pid: str) -> str:
    """写入永远以路径里的 pid 为准；body 里的 profile_id 只能与它相同。"""
    wanted = str(payload.get("profile_id") or "").strip()
    if wanted and wanted != str(pid):
        raise AppError(
            INVALID_INPUT,
            "profile_id 与路径不一致",
            field="profile_id",
            path_profile_id=str(pid),
            body_profile_id=wanted,
        )
    return str(pid)


# -- 交付记录 ---------------------------------------------------------------


def list_deliveries(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/config/deliveries?artifact_id=&result=
    _read(ctx)
    items = config_delivery.list_deliveries(
        _repo(settings),
        artifact_id=_optional(query, "artifact_id"),
        profile_id=_pid(ctx, params),
        result=_optional(query, "result"),
    )
    return Response(payload={"items": items, "total": len(items)})


def summary(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/config/deliveries/summary?artifact_id=
    _read(ctx)
    artifact_id = _optional(query, "artifact_id")
    if not artifact_id:
        raise AppError(INVALID_INPUT, "缺少 artifact_id", field="artifact_id")
    artifact = config_artifacts.get_artifact(_repo(settings), artifact_id)
    _same_profile(artifact.get("profile_id"), _pid(ctx, params), "artifact_id", artifact_id)
    return Response(
        payload=config_delivery.summary(
            _repo(settings), artifact_id=artifact_id, profile_id=_pid(ctx, params)
        )
    )


def get_delivery(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/config/deliveries/{did}
    _read(ctx)
    item = config_delivery.get_delivery(_repo(settings), params["did"])
    _same_profile(
        (item.get("target") or {}).get("profile_id"), _pid(ctx, params), "delivery_id", params["did"]
    )
    return Response(payload={"item": item})


def register_delivery(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/config/deliveries
    _write(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    pid = _body_profile(payload, _pid(ctx, params))
    item = config_delivery.register(
        _repo(settings),
        ctx.actor,
        artifact_id=payload.get("artifact_id"),
        result=payload.get("result"),
        profile_id=pid,
        device_id=payload.get("device_id"),
        executed_at=payload.get("executed_at"),
        executed_by=payload.get("executed_by") or "",
        evidence=payload.get("evidence"),
        note=payload.get("note") or "",
        failure_reason=payload.get("failure_reason") or "",
        missing_evidence=payload.get("missing_evidence"),
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload={"item": item})


# -- 回读核验 ---------------------------------------------------------------


def list_verifications(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/config/artifacts/{aid}/verifications
    _read(ctx)
    items = config_verify.list_verifications(
        _repo(settings), artifact_id=params["aid"], profile_id=_pid(ctx, params)
    )
    return Response(payload={"items": items, "total": len(items)})


def run_verification(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/config/artifacts/{aid}/verifications
    _write(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    pid = _body_profile(payload, _pid(ctx, params))
    item = config_verify.verify(
        _repo(settings),
        ctx.actor,
        artifact_id=params["aid"],
        observation_id=payload.get("observation_id"),
        profile_id=pid,
        imported_after=payload.get("imported_after"),
        note=payload.get("note") or "",
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload={"item": item})


def get_verification(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/config/verifications/{vid}
    _read(ctx)
    item = config_verify.get_verification(_repo(settings), params["vid"])
    _same_profile(item.get("profile_id"), _pid(ctx, params), "verification_id", params["vid"])
    return Response(payload={"item": item})


def register(router: Router, settings: Settings) -> None:
    router.add(
        "GET", "/api/v1/p/{pid}/config/deliveries", partial(list_deliveries, settings=settings)
    )
    router.add(
        "POST", "/api/v1/p/{pid}/config/deliveries", partial(register_delivery, settings=settings)
    )
    # `summary` 必须排在 `{did}` 之前：路由按注册顺序匹配
    router.add(
        "GET",
        "/api/v1/p/{pid}/config/deliveries/summary",
        partial(summary, settings=settings),
    )
    router.add(
        "GET",
        "/api/v1/p/{pid}/config/deliveries/{did}",
        partial(get_delivery, settings=settings),
    )
    router.add(
        "GET",
        "/api/v1/p/{pid}/config/artifacts/{aid}/verifications",
        partial(list_verifications, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/p/{pid}/config/artifacts/{aid}/verifications",
        partial(run_verification, settings=settings),
    )
    router.add(
        "GET",
        "/api/v1/p/{pid}/config/verifications/{vid}",
        partial(get_verification, settings=settings),
    )
