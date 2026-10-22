"""批准并发布（M6-12 / INH-1957）。

单独一个模块、单独的权限键 `config_change_approve`：**提交**与**批准**必须是
两条可分别授予的路径。放在同一个文件里共用一个 `_write()` 是最容易出错的地方——
那样任何能提交的人都能顺手批准。

`POST /p/{pid}/config/changes/{cid}/approve` 在**一个事务**里同时写新基线修订与
变更单决议，没有 approved-but-unpublished 中间态。批准前重新校验：权限（这里）、
状态、目标 head、定义集合、参数范围、未决冲突（服务层）。
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from ..services import authz, config_changes, config_gate, config_store
from ..services.context import RequestContext
from .router import Response, Router, json_body


def _approve(ctx: RequestContext) -> None:
    """先授权后门禁：部署者在这里拿到 403，而不是被门禁档位挡回去。"""
    authz.authorize(ctx, "config_change_approve")
    config_gate.require_write()


def _idempotency(ctx: RequestContext) -> Optional[str]:
    key = str((ctx.headers or {}).get("idempotency-key") or "").strip()
    if not key:
        return None
    if len(key) > 128:
        raise AppError(INVALID_INPUT, "Idempotency-Key 过长（上限 128 字符）")
    return key


def approve_change(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/config/changes/{cid}/approve
    #   {"note","expected_revision","expected_baseline_revision"}
    _approve(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    repo = config_store.repository(settings)
    result = config_changes.approve_change_set(
        repo,
        ctx.actor,
        change_id=params["cid"],
        note=payload.get("note"),
        expected_revision=payload.get("expected_revision"),
        expected_baseline_revision=payload.get("expected_baseline_revision"),
        idempotency_key=_idempotency(ctx),
    )
    item = result.get("change_set") or {}
    owner = str(item.get("profile_id") or "")
    if owner and owner != str(params["pid"]):
        # 通用层变更（owner 为空）从任一机型路径都可见，与提交面一致
        raise AppError(NOT_FOUND, "变更单不存在", change_id=params.get("cid"))
    return Response(status=201, payload=result)


def reject_change(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/config/changes/{cid}/reject  {"note"}
    _approve(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    repo = config_store.repository(settings)
    item = config_changes.reject_change_set(
        repo,
        ctx.actor,
        change_id=params["cid"],
        note=payload.get("note"),
        expected_revision=payload.get("expected_revision"),
        idempotency_key=_idempotency(ctx),
    )
    owner = str(item.get("profile_id") or "")
    if owner and owner != str(params["pid"]):
        raise AppError(NOT_FOUND, "变更单不存在", change_id=params.get("cid"))
    return Response(status=201, payload={"item": item})


def register(router: Router, settings: Settings) -> None:
    router.add(
        "POST",
        "/api/v1/p/{pid}/config/changes/{cid}/approve",
        partial(approve_change, settings=settings),
    )
    # 驳回同样属于"批准权"：能决定放行的人才能决定不放行
    router.add(
        "POST",
        "/api/v1/p/{pid}/config/changes/{cid}/reject",
        partial(reject_change, settings=settings),
    )
