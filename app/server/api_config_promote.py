"""现场经验上提接口（M7-05 / INH-1964）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/v1/p/{pid}/config/promote/common` | 机型 → 通用的上提计划 |
| POST | `/api/v1/p/{pid}/config/promote/common` | 按计划建上提变更单（默认直接提交） |
| GET  | `/api/v1/p/{pid}/config/promote/model` | 设备 → 机型：**功能关闭** |
| POST | `/api/v1/p/{pid}/config/promote/model` | 同上，明确报 `DEVICE_LAYER_DISABLED` |

设备层的两个入口是刻意留着的：留着才能给出"为什么不能做"的明确回答（而不是
404 让人以为是路径写错）。M7-07/08 一旦启用，只需要换掉这两处的实现。
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..core.errors import INVALID_INPUT, AppError
from ..services import authz, config_gate, config_promote, config_store
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param


def _read(ctx: RequestContext) -> None:
    config_gate.require_read()
    authz.authorize(ctx, "config_read")


def _write(ctx: RequestContext) -> None:
    # 上提产出的是变更单，权限键与建变更单一致
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


def _parameter_ids(payload: Dict[str, Any], query: Dict[str, List[str]]):
    """`parameter_ids` 支持 `a,b,c` 查询串与数组两种写法。"""
    raw = payload.get("parameter_ids")
    if raw is None:
        text = _optional(query, "parameter_ids")
        if text:
            raw = [item.strip() for item in text.split(",") if item.strip()]
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or not raw:
        raise AppError(INVALID_INPUT, "parameter_ids 必须是非空数组", field="parameter_ids")
    return [str(item) for item in raw]


def _expected(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw = payload.get("expected")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise AppError(INVALID_INPUT, "expected 必须是对象", field="expected")
    return raw


def plan_common(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/config/promote/common?parameter_ids=&source_baseline_id=
    _read(ctx)
    plan = config_promote.plan_promote_to_common(
        _repo(settings),
        profile_id=params["pid"],
        parameter_ids=_parameter_ids({}, query),
        source_baseline_id=_optional(query, "source_baseline_id"),
    )
    return Response(payload=plan)


def create_common(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/config/promote/common
    _write(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    result = config_promote.create_promote_to_common(
        _repo(settings),
        ctx.actor,
        profile_id=params["pid"],
        parameter_ids=_parameter_ids(payload, query),
        source_baseline_id=payload.get("source_baseline_id"),
        title=payload.get("title") or "",
        reason=payload.get("reason") or "",
        submit=payload.get("submit", True) is not False,
        expected=_expected(payload),
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload=result)


def plan_model(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/p/{pid}/config/promote/model?device_id=
    _read(ctx)
    plan = config_promote.plan_promote_to_model(
        _repo(settings), profile_id=params["pid"], device_id=_optional(query, "device_id")
    )
    return Response(payload=plan)


def create_model(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/p/{pid}/config/promote/model
    _write(ctx)
    payload: Dict[str, Any] = json_body(body) if body else {}
    plan = config_promote.plan_promote_to_model(
        _repo(settings),
        profile_id=params["pid"],
        device_id=payload.get("device_id"),
    )
    # 设备层关闭时上面必然抛错，走不到这里；保留分支是为了启用后语义不打折
    return Response(status=201, payload=plan)


def register(router: Router, settings: Settings) -> None:
    router.add(
        "GET",
        "/api/v1/p/{pid}/config/promote/common",
        partial(plan_common, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/p/{pid}/config/promote/common",
        partial(create_common, settings=settings),
    )
    router.add(
        "GET",
        "/api/v1/p/{pid}/config/promote/model",
        partial(plan_model, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/p/{pid}/config/promote/model",
        partial(create_model, settings=settings),
    )
