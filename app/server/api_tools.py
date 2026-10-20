"""外部工具菜单接口（规范 §7.10、§8.3；M3-15 / INH-1900）。

handler 只做「解析请求 → 授权 → 调 `services.tools` → 序列化响应」：不碰 `storage`、
不碰修订号（§3.3 / 全局约束）。机型存在性与 `use_profile(pid)` 由 `http.py` 统一处理。

权限（§4.2）：`GET` = 全部角色；`PUT` / `DELETE` = 部署者（`settings_write`）。
CSRF 头由 `http.py` 统一检查，本模块不重复实现（§11.4）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..services import authz, tools
from ..services.context import RequestContext
from .router import Response, Router, json_body


def register(router: Router) -> None:
    router.add("GET", "/api/v1/p/{pid}/tools", list_tools)
    router.add("PUT", "/api/v1/p/{pid}/tools/{name}", put_tool)
    router.add("DELETE", "/api/v1/p/{pid}/tools/{name}", delete_tool)


def list_tools(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/tools -> {items: [{name, description, url, added_at}]}
    authz.authorize(ctx, "read")
    return Response(payload=tools.list_tools(params["pid"]))


def put_tool(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # PUT /api/v1/p/{pid}/tools/{name}
    # {"name","description","url"} -> {item}
    # 路径名称是原名，body.name 是新名；不存在即新增，撞已有工具 409 FILE_EXISTS。
    authz.authorize(ctx, "settings_write")
    return Response(
        payload=tools.put_tool(params["pid"], params["name"], json_body(body))
    )


def delete_tool(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # DELETE /api/v1/p/{pid}/tools/{name} -> {removed: bool}
    authz.authorize(ctx, "settings_write")
    return Response(payload=tools.delete_tool(params["pid"], params["name"]))
