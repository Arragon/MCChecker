"""历史回滚 / 跨变体迁移 / 出口补发接口（M7-06 / INH-1965）。

六个入口，全部挂在基线下面——这三件事的对象都是"某条基线的历史"：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/v1/config/baselines/{bid}/restore?revision=r2` | 回滚会改什么 |
| POST | `/api/v1/config/baselines/{bid}/restore` | 用历史内容建**新的已批准基线** |
| GET  | `/api/v1/config/baselines/{bid}/migrate?target_variant_id=` | 跨变体迁移计划 |
| POST | `/api/v1/config/baselines/{bid}/migrate` | 按计划建目标变体草稿 |
| GET  | `/api/v1/config/baselines/{bid}/reissue` | 补发影响（模板是否还在这批里） |
| POST | `/api/v1/config/baselines/{bid}/reissue` | 按补发后的出口默认建草稿 |

三条写入口都返回 201：它们都**新增**对象，不改写已有对象。回滚尤其如此——
把 head 指回旧修订会让"上周批准的是什么"永远答不上来。
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..core.errors import INVALID_INPUT, AppError
from ..services import authz, config_gate, config_restore, config_store
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param


def _read(ctx: RequestContext) -> None:
    config_gate.require_read()
    authz.authorize(ctx, "config_read")


def _write(ctx: RequestContext) -> None:
    # 回滚/迁移/刷新都直接落对象，与建基线同一权限口径
    authz.authorize(ctx, "config_change_submit")
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


# -- 回滚 -------------------------------------------------------------------


def plan_restore(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/baselines/{bid}/restore?revision=r2
    _read(ctx)
    plan = config_restore.plan_restore(
        _repo(settings), baseline_id=params["bid"], revision=_optional(query, "revision")
    )
    return Response(payload=plan)


def create_restore(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/baselines/{bid}/restore  {"revision","reason","expected_head"}
    _write(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    result = config_restore.restore_baseline(
        _repo(settings),
        ctx.actor,
        baseline_id=params["bid"],
        revision=payload.get("revision"),
        reason=payload.get("reason") or "",
        expected_head=payload.get("expected_head"),
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload=result)


# -- 跨变体迁移 -------------------------------------------------------------


def plan_migration(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/baselines/{bid}/migrate?target_variant_id=
    _read(ctx)
    plan = config_restore.plan_variant_migration(
        _repo(settings),
        baseline_id=params["bid"],
        target_variant_id=_optional(query, "target_variant_id"),
    )
    return Response(payload=plan)


def create_migration(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/baselines/{bid}/migrate  {"target_variant_id","reason"}
    _write(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    result = config_restore.create_variant_migration(
        _repo(settings),
        ctx.actor,
        baseline_id=params["bid"],
        target_variant_id=payload.get("target_variant_id"),
        reason=payload.get("reason") or "",
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload=result)


# -- 出口补发 ---------------------------------------------------------------


def plan_reissue(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/baselines/{bid}/reissue
    _read(ctx)
    plan = config_restore.plan_reissue_impact(_repo(settings), baseline_id=params["bid"])
    return Response(payload=plan)


def create_reissue(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/baselines/{bid}/reissue  {"reason"}
    _write(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    result = config_restore.create_reissue_refresh(
        _repo(settings),
        ctx.actor,
        baseline_id=params["bid"],
        reason=payload.get("reason") or "",
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload=result)


def register(router: Router, settings: Settings) -> None:
    router.add(
        "GET",
        "/api/v1/config/baselines/{bid}/restore",
        partial(plan_restore, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/config/baselines/{bid}/restore",
        partial(create_restore, settings=settings),
    )
    router.add(
        "GET",
        "/api/v1/config/baselines/{bid}/migrate",
        partial(plan_migration, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/config/baselines/{bid}/migrate",
        partial(create_migration, settings=settings),
    )
    router.add(
        "GET",
        "/api/v1/config/baselines/{bid}/reissue",
        partial(plan_reissue, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/config/baselines/{bid}/reissue",
        partial(create_reissue, settings=settings),
    )
