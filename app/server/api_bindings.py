"""变量绑定接口（规范 §7.5、§8.3；M3-14 / INH-1899）。

handler 只做「解析请求 → 授权 → 调 `services.bindings` → 序列化响应」：不碰 `storage`、
不碰修订号（§3.3 / 全局约束）。机型存在性与 `use_profile(pid)` 由 `http.py` 统一处理。

权限（§4.2）：`GET` = 全部角色；`PUT` / `DELETE` = 部署者（`settings_write`）。
CSRF 头由 `http.py` 统一检查，本模块不重复实现（§11.4）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..services import authz, bindings
from ..services.context import RequestContext
from .router import Response, Router, json_body


def register(router: Router) -> None:
    router.add("GET", "/api/v1/p/{pid}/bindings", list_bindings)
    router.add("PUT", "/api/v1/p/{pid}/bindings/{group}", put_binding)
    router.add("DELETE", "/api/v1/p/{pid}/bindings/{group}", delete_binding)


def list_bindings(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/bindings -> {items: [{group_name, variables}]}
    authz.authorize(ctx, "read")
    return Response(payload=bindings.list_bindings(params["pid"]))


def put_binding(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # PUT /api/v1/p/{pid}/bindings/{group}
    # {"group_name": str, "variables": [{"file","path"}]} -> {item}
    # 路径组名是原名，body.group_name 是新名；不存在即新增，撞已有组 409 FILE_EXISTS。
    authz.authorize(ctx, "settings_write")
    return Response(
        payload=bindings.put_binding(params["pid"], params["group"], json_body(body))
    )


def delete_binding(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # DELETE /api/v1/p/{pid}/bindings/{group} -> {removed: bool}
    authz.authorize(ctx, "settings_write")
    return Response(payload=bindings.delete_binding(params["pid"], params["group"]))
