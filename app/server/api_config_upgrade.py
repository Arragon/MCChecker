"""跨版本升级接口（M7-04 / INH-1963）。

六个入口，计划与落地成对：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/v1/config/upgrade/common` | 通用层升版计划 |
| POST | `/api/v1/config/upgrade/common` | 按决议建通用基线草稿 |
| GET  | `/api/v1/p/{pid}/config/upgrade/model` | 机型跨版本升级计划 |
| POST | `/api/v1/p/{pid}/config/upgrade/model` | 按决议建机型基线草稿 |
| GET  | `/api/v1/p/{pid}/config/upgrade/rebase` | 同版本刷新计划 |
| POST | `/api/v1/p/{pid}/config/upgrade/rebase` | 按决议建刷新后的机型基线 |

通用层是全局对象（基线无 `profile_id`），所以走 `/config/*`；机型的两个模式以机型为
坐标，走 `/p/{pid}/`。权限：计划是读（`config_read`），落地是写（`config_change_submit`
——与建基线的键一致，不另造一个"升级专用"权限）。

**前端不许自己算合并**：候选值、阻断项、允许动作全部来自这里的 `items`。
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..core.errors import INVALID_INPUT, AppError
from ..services import authz, config_gate, config_store, config_upgrade
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param


def _read(ctx: RequestContext) -> None:
    config_gate.require_read()
    authz.authorize(ctx, "config_read")


def _write(ctx: RequestContext) -> None:
    """建升级草稿与建普通基线是同一类写动作，共用一个能力键。"""
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


def _decisions(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw = payload.get("decisions")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise AppError(INVALID_INPUT, "decisions 必须是对象", field="decisions")
    return {str(key): value for key, value in raw.items()}


def _expected(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw = payload.get("expected")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise AppError(INVALID_INPUT, "expected 必须是对象", field="expected")
    return raw


# -- 通用层 ----------------------------------------------------------------


def plan_common(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/upgrade/common?release_id=&variant_id=&previous_release_id=
    _read(ctx)
    plan = config_upgrade.plan_common_upgrade(
        _repo(settings),
        release_id=_optional(query, "release_id"),
        variant_id=_optional(query, "variant_id"),
        previous_release_id=_optional(query, "previous_release_id"),
    )
    return Response(payload=plan)


def create_common(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/upgrade/common  {"release_id","variant_id","decisions","expected"}
    _write(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    result = config_upgrade.create_common_upgrade_baseline(
        _repo(settings),
        ctx.actor,
        release_id=payload.get("release_id"),
        variant_id=payload.get("variant_id"),
        previous_release_id=payload.get("previous_release_id"),
        decisions=_decisions(payload),
        reason=payload.get("reason") or "",
        idempotency_key=_idempotency(ctx),
        expected=_expected(payload),
    )
    return Response(status=201, payload=result)


# -- 机型 ------------------------------------------------------------------


def plan_model(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/config/upgrade/model?release_id=
    _read(ctx)
    plan = config_upgrade.plan_model_upgrade(
        _repo(settings),
        profile_id=params["pid"],
        release_id=_optional(query, "release_id"),
    )
    return Response(payload=plan)


def create_model(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/config/upgrade/model
    _write(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    result = config_upgrade.create_model_upgrade_baseline(
        _repo(settings),
        ctx.actor,
        profile_id=params["pid"],
        release_id=payload.get("release_id"),
        decisions=_decisions(payload),
        reason=payload.get("reason") or "",
        idempotency_key=_idempotency(ctx),
        expected=_expected(payload),
    )
    return Response(status=201, payload=result)


def plan_rebase(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/config/upgrade/rebase
    _read(ctx)
    plan = config_upgrade.plan_model_rebase(_repo(settings), profile_id=params["pid"])
    return Response(payload=plan)


def create_rebase(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/config/upgrade/rebase
    _write(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    result = config_upgrade.create_model_rebase_baseline(
        _repo(settings),
        ctx.actor,
        profile_id=params["pid"],
        decisions=_decisions(payload),
        reason=payload.get("reason") or "",
        idempotency_key=_idempotency(ctx),
        expected=_expected(payload),
    )
    return Response(status=201, payload=result)


def register(router: Router, settings: Settings) -> None:
    router.add("GET", "/api/v1/config/upgrade/common", partial(plan_common, settings=settings))
    router.add("POST", "/api/v1/config/upgrade/common", partial(create_common, settings=settings))
    router.add(
        "GET", "/api/v1/p/{pid}/config/upgrade/model", partial(plan_model, settings=settings)
    )
    router.add(
        "POST", "/api/v1/p/{pid}/config/upgrade/model", partial(create_model, settings=settings)
    )
    router.add(
        "GET", "/api/v1/p/{pid}/config/upgrade/rebase", partial(plan_rebase, settings=settings)
    )
    router.add(
        "POST", "/api/v1/p/{pid}/config/upgrade/rebase", partial(create_rebase, settings=settings)
    )
