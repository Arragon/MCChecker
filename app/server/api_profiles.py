"""机型管理接口（规范 §7.2；M3-16 / INH-1901）。

handler 只做「解析请求 → 授权 → 调 `services.profiles` → 序列化响应」：不碰 `storage`、
不碰修订号（§3.3 / 全局约束）。

权限（§4.2）：三个写接口都只给部署者（`settings_write`）。`/profiles` 没有 `{pid}`，
身份按 default 解析；`/profiles/{pid}` 由 `http.py` 先校验机型存在再解析身份。
CSRF 头由 `http.py` 统一检查，本模块不重复实现（§11.4）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..services import authz, profiles
from ..services.context import RequestContext
from .router import Response, Router, json_body


def register(router: Router) -> None:
    router.add("POST", "/api/v1/profiles", create_profile)
    router.add("PATCH", "/api/v1/profiles/{pid}", rename_profile)
    router.add("DELETE", "/api/v1/profiles/{pid}", delete_profile)


def create_profile(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # POST /api/v1/profiles  {"name","copy_from"} -> 201 {profile:{id,name}}
    authz.authorize(ctx, "settings_write")
    return Response(status=201, payload=profiles.create_profile(json_body(body)))


def rename_profile(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # PATCH /api/v1/profiles/{pid}  {"name"} -> {profile}
    authz.authorize(ctx, "settings_write")
    return Response(
        payload=profiles.rename_profile(params["pid"], json_body(body))
    )


def delete_profile(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # DELETE /api/v1/profiles/{pid} -> {deleted: true, recovery}
    authz.authorize(ctx, "settings_write")
    actor = ctx.actor
    return Response(
        payload=profiles.delete_profile(
            params["pid"],
            actor_name=getattr(actor, "name", "") or "",
            actor_ip=ctx.ip,
        )
    )
