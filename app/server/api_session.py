"""会话接口（规范 §7.2、§4.1；M1-03）。

`GET /api/v1/session?profile=`：
* 身份按机型解释；**仅此接口**在机型不存在时回落 `default`（其余 `{pid}` 接口
  由 HTTP 层直接 404）；
* 无 `mc_cid` cookie 时下发 `Set-Cookie`（HttpOnly + SameSite=Lax，1 年）；
* 顶栏不显示 IP——IP 只在身份弹层展示，由前端用 `identity.ip` 渲染。
"""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Any, Dict, List, Optional

from .. import __version__
from ..config import Settings
from ..core import device_models
from ..services import auth
from ..services.context import RequestContext
from .router import Response

_PROFILE_QUERY_KEY = "profile"


def get_session(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
    settings: Settings,
) -> Response:
    profile_id = _resolve_profile(ctx, query)
    actor = ctx.actor
    if actor is None or ctx.profile_id != profile_id:
        actor = auth.resolve_actor(ctx.ip, profile_id)

    headers: Dict[str, str] = {}
    if not ctx.cid:
        headers["Set-Cookie"] = auth.session_cookie(auth.new_session_cid())

    models = device_models.load_models()
    profile = next((m for m in models if m.get("id") == profile_id), {"id": profile_id, "name": profile_id})
    return Response(
        payload={
            "identity": {
                "ip": actor.ip,
                "name": actor.name,
                "registered": actor.registered,
                "is_deployer": actor.is_deployer,
                "is_admin": actor.is_admin,
                "role_label": actor.role_label,
            },
            "profile": dict(profile),
            "profiles": [dict(model) for model in models],
            "server": {
                "version": __version__,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                # 调度器状态：新架构调度在 M2-05 才接入，接入前恒为 False
                "scheduler": False,
            },
            "limits": {
                "upload_max_bytes": settings.upload_max_bytes,
                "tab_limit": settings.tab_limit,
                "temp_ttl_hours": settings.temp_ttl_hours,
            },
        },
        headers=headers,
    )


def _resolve_profile(ctx: RequestContext, query: Dict[str, List[str]]) -> str:
    """/session 特例：查询参数里的机型不存在（或非法）时回落 default。"""
    values = query.get(_PROFILE_QUERY_KEY)
    requested = str(values[-1]).strip() if values else ""
    if requested and requested != ctx.profile_id and device_models.exists(requested):
        return requested
    if ctx.profile_id and device_models.exists(ctx.profile_id):
        return ctx.profile_id
    return device_models.DEFAULT_MODEL_ID


def register(router, settings: Settings) -> None:
    """把会话接口挂到路由表。"""
    router.add("GET", "/api/v1/session", partial(get_session, settings=settings))
