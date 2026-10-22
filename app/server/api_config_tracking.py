"""追踪策略接口（M6-05 / INH-1950）。

* 读：策略总览（含每个机型的有效档与**继承来源**）；
* 写：设置类别级 / 机型级策略——必须带理由，改动追加修订而不是新建对象。

权限：写接口用 `config_tracking_admin`（不是旧的 `settings_write`）。两者严格区分：
追踪策略改变的是"旧入口的去向"，属于配置域管理动作；`settings_write` 仍然只
管机型设置/更新源这类旧配置。读接口只要 `config_read`。
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..core.errors import INVALID_INPUT, AppError
from ..services import authz, config_gate, config_policies, config_store
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param


def _repo(settings: Settings):
    return config_store.repository(settings)


def _read(ctx: RequestContext) -> None:
    config_gate.require_read()
    authz.authorize(ctx, "config_read")


def _write(ctx: RequestContext) -> None:
    """先授权后门禁：未授权身份拿到 403，不泄露特性档位。"""
    authz.authorize(ctx, "config_tracking_admin")
    config_gate.require_write()


def _idempotency(ctx: RequestContext) -> Optional[str]:
    headers = ctx.headers or {}
    key = str(headers.get("idempotency-key") or "").strip()
    if not key:
        return None
    if len(key) > 128:
        raise AppError(INVALID_INPUT, "Idempotency-Key 过长（上限 128 字符）")
    return key


def _query_pids(query: Dict[str, List[str]]) -> List[str]:
    raw = query_param(query, "profiles")
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def get_tracking(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/tracking?profiles=a,b
    _read(ctx)
    repo = _repo(settings)
    payload = config_policies.tracking_overview(repo, _query_pids(query))
    payload["writable"] = config_gate.mode() == config_gate.MODE_ON
    payload["mode"] = config_gate.mode()
    return Response(payload=payload)


def get_effective(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/tracking/effective?family_id=&profile_id=
    _read(ctx)
    repo = _repo(settings)
    family_id = query_param(query, "family_id") or None
    profile_id = query_param(query, "profile_id") or None
    return Response(
        payload={
            **config_policies.effective_policy(
                repo, family_id=family_id, profile_id=profile_id
            ),
            "family_id": family_id,
            "profile_id": profile_id,
            "modes": list(config_policies.MODES),
        }
    )


def set_tracking(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # POST /api/v1/config/tracking
    # {"scope","family_id","profile_id","mode","reason","notes","expected_revision"}
    _write(ctx)
    payload = json_body(body)
    item = config_policies.set_policy(
        _repo(settings),
        ctx.actor,
        scope=str(payload.get("scope") or ""),
        mode=str(payload.get("mode") or ""),
        reason=payload.get("reason"),
        family_id=payload.get("family_id"),
        profile_id=payload.get("profile_id"),
        notes=payload.get("notes"),
        expected_revision=payload.get("expected_revision") or None,
        idempotency_key=_idempotency(ctx),
    )
    return Response(status=201, payload={"item": item})


def get_policy_history(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    # GET /api/v1/config/tracking/{tpid}/history
    _read(ctx)
    repo = _repo(settings)
    revisions = config_policies.policy_history(repo, params["tpid"])
    return Response(
        payload={
            "policy_id": params["tpid"],
            "revisions": revisions,
            "current": repo.get("tracking_policy", params["tpid"]),
        }
    )


def register(router: Router, settings: Settings) -> None:
    router.add("GET", "/api/v1/config/tracking", partial(get_tracking, settings=settings))
    router.add("POST", "/api/v1/config/tracking", partial(set_tracking, settings=settings))
    router.add(
        "GET",
        "/api/v1/config/tracking/effective",
        partial(get_effective, settings=settings),
    )
    router.add(
        "GET",
        "/api/v1/config/tracking/{tpid}/history",
        partial(get_policy_history, settings=settings),
    )
