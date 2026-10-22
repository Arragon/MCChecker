"""基线接口（M6-09 / INH-1954）。

* `GET  /config/baselines` —— 按作用域/机型/状态列基线；
* `POST /config/baselines` —— 建草稿（通用从默认出口播种，机型从**点名**的上游派生）；
* `GET  /config/baselines/{bid}` —— 详情：值行 + 就绪度 + 修订/血缘；
* `POST /config/baselines/{bid}/values` —— 改草稿的值（追加修订）；
* `POST /config/baselines/{bid}/intent` —— 只改继承意图（`pin` 与"值恰好一样"是两件事）。

状态流转（提交/批准/驳回）属于 M6-11/M6-12，这里不做——草稿之外的一切都只读。
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..core.errors import INVALID_INPUT, AppError
from ..services import authz, config_baselines, config_gate, config_store
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param


def _read(ctx: RequestContext) -> None:
    config_gate.require_read()
    authz.authorize(ctx, "config_read")


def _write(ctx: RequestContext) -> None:
    """先授权后门禁：未授权身份拿到 403，不泄露特性档位。"""
    authz.authorize(ctx, "config_change_submit")
    config_gate.require_write()


def _repo(settings: Settings):
    return config_store.repository(settings)


def _idempotency(ctx: RequestContext) -> Optional[str]:
    key = str((ctx.headers or {}).get("idempotency-key") or "").strip()
    if not key:
        return None
    if len(key) > 128:
        raise AppError(INVALID_INPUT, "Idempotency-Key 过长（上限 128 字符）")
    return key


def _optional(query: Dict[str, List[str]], name: str) -> Optional[str]:
    value = query_param(query, name)
    return value if value else None


def list_baselines(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/baselines?scope=&profile_id=&device_id=&state=&release_id=
    _read(ctx)
    items = config_baselines.list_baselines(
        _repo(settings),
        scope=_optional(query, "scope"),
        profile_id=_optional(query, "profile_id"),
        device_id=_optional(query, "device_id"),
        state=_optional(query, "state"),
        release_id=_optional(query, "release_id"),
    )
    return Response(
        payload={
            "items": items,
            "total": len(items),
            "scopes": list(config_baselines.SCOPES),
            "states": list(config_baselines.STATES),
            "intents": list(config_baselines.INTENTS),
        }
    )


def create_baseline(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/baselines
    #   {"scope","release_id","profile_id","device_id","definition_set_id",
    #    "variant_id","upstream_baseline_id","values","reason"}
    _write(ctx)
    payload: Dict[str, Any] = json_body(body)
    item = config_baselines.create_baseline(
        _repo(settings),
        ctx.actor,
        scope=payload.get("scope"),
        release_id=payload.get("release_id"),
        profile_id=payload.get("profile_id"),
        device_id=payload.get("device_id"),
        definition_set_id=payload.get("definition_set_id"),
        variant_id=payload.get("variant_id"),
        upstream_baseline_id=payload.get("upstream_baseline_id"),
        values=payload.get("values"),
        reason=payload.get("reason"),
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload={"item": item})


def get_baseline(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/baselines/{bid}
    _read(ctx)
    return Response(payload=config_baselines.detail(_repo(settings), params["bid"]))


def update_values(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/baselines/{bid}/values
    #   {"values":{pm:{value,intent,reason}},"expected_revision","reason"}
    _write(ctx)
    payload: Dict[str, Any] = json_body(body)
    item = config_baselines.update_values(
        _repo(settings),
        ctx.actor,
        baseline_id=params["bid"],
        values=payload.get("values"),
        expected_revision=payload.get("expected_revision"),
        reason=payload.get("reason"),
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload={"item": item})


def set_intent(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/baselines/{bid}/intent  {"parameter_id","intent","expected_revision"}
    _write(ctx)
    payload: Dict[str, Any] = json_body(body)
    item = config_baselines.set_intent(
        _repo(settings),
        ctx.actor,
        baseline_id=params["bid"],
        parameter_id=payload.get("parameter_id"),
        intent=payload.get("intent"),
        expected_revision=payload.get("expected_revision"),
    )
    return Response(status=201, payload={"item": item})


def register(router: Router, settings: Settings) -> None:
    router.add(
        "GET", "/api/v1/config/baselines", partial(list_baselines, settings=settings)
    )
    router.add(
        "POST", "/api/v1/config/baselines", partial(create_baseline, settings=settings)
    )
    router.add(
        "GET",
        "/api/v1/config/baselines/{bid}",
        partial(get_baseline, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/config/baselines/{bid}/values",
        partial(update_values, settings=settings),
    )
    router.add(
        "POST",
        "/api/v1/config/baselines/{bid}/intent",
        partial(set_intent, settings=settings),
    )
