"""配置管理 API 骨架（M5-08 / INH-1942）。

本轮只交付**契约稳定的骨架**：门禁、权限、响应包络、全局修订号与只读状态。
参数/基线/变更单等实体接口按 M6/M7 的卡逐张挂上来，但路径与错误码已在此定型，
前端（M5-09～M5-11）可以对着真实接口开发，不需要 mock。

约定（与 `docs/MCChecker_CONFIG_DOMAIN_SPEC.md` §6 一致）：

* 所有 response 都是 JSON；错误由 `http.py` 统一翻译成 `{code, message, detail}`；
* 写接口必须带 `X-Requested-With: MCChecker`（`http.py` 已强制，此处不重复实现）；
* `{pid}` 由 `http.py` 校验存在性，**不存在即 404**，不回落 `default`。
"""

from __future__ import annotations

from functools import partial
from typing import Dict, List, Optional

from .. import __version__
from ..config import Settings
from ..core.errors import INVALID_INPUT, AppError
from ..services import authz, config_device_layer, config_gate, config_revision, config_store
from ..services.context import RequestContext
from .router import Response


def get_revision(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    """配置域全局修订号：`{rev, seq, store_seq}`。"""
    config_gate.require_read()
    authz.authorize(ctx, "config_read")
    repo = config_store.repository(settings)
    store_seq = int(repo.load_state().get("seq") or 0)
    return Response(payload=config_revision.snapshot(store_seq=store_seq))


def get_capabilities(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    """当前身份在配置域的完整能力矩阵（与会话里那份同源）。"""
    config_gate.require_read()
    authz.authorize(ctx, "config_read")
    return Response(
        payload={
            "capabilities": authz.capabilities(ctx.actor),
            "actions": sorted(authz.CONFIG_CAPABILITIES),
        }
    )


def get_status(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    """特性档位与全局规模：前端用来判断"未开启/空库/有数据"。"""
    config_gate.require_read()
    authz.authorize(ctx, "config_read")
    repo = config_store.repository(settings)
    summary = config_store.state_summary(repo)
    return Response(
        payload={
            "mode": config_gate.mode(),
            "writable": config_gate.mode() == config_gate.MODE_ON,
            "server_version": __version__,
            "store_seq": summary["store_seq"],
            "counts": summary["counts"],
            "total": summary["total"],
            # M7-07/08 按计划延期：开关状态在这里如实报，前端与验收读同一份；
            # 关闭时连"关了什么、怎么开"一起给，避免被读成"整个配置域不能用"。
            "device_layer": config_device_layer.describe(),
        }
    )


def get_profile_status(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    """机型视角的配置域摘要；`{pid}` 已由 HTTP 层校验，不回落 default。"""
    config_gate.require_read()
    authz.authorize(ctx, "config_read")
    if not ctx.profile_id:
        raise AppError(INVALID_INPUT, "缺少机型")
    repo = config_store.repository(settings)
    payload = config_store.profile_summary(repo, ctx.profile_id)
    payload["mode"] = config_gate.mode()
    payload["writable"] = config_gate.mode() == config_gate.MODE_ON
    return Response(payload=payload)


def register(router, settings: Settings) -> None:
    """把骨架路由挂进路由表（顺序在旧路由之后，避免抢匹配）。"""
    router.add("GET", "/api/v1/config/revision", partial(get_revision, settings=settings))
    router.add("GET", "/api/v1/config/capabilities", partial(get_capabilities, settings=settings))
    router.add("GET", "/api/v1/config/status", partial(get_status, settings=settings))
    router.add(
        "GET", "/api/v1/p/{pid}/config/status", partial(get_profile_status, settings=settings)
    )
