"""人员接口：IP 对应表与管理员名单（规范 §7.9、§9.8.10、§11.1；M3-13 / INH-1898）。

handler 只做「解析请求 → 授权 → 调 `services.people` → 序列化响应」：不碰 `storage`、
不碰修订号（§3.3 / 全局约束）。机型存在性与 `use_profile(pid)` 由 `http.py` 统一处理。

权限（§4.2）：`GET` = 全部角色（速览与审阅页要显示姓名，人员表本身也要给非部署者看）；
四个写接口 = 部署者（`settings_write`）。CSRF 头由 `http.py` 检查，本模块不重复实现（§11.4）。

改这张表等于改「谁能写」，所以「403 时一个字节都不动」由 `tests/test_api_people.py` 逐角色钉住。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..services import authz, people
from ..services.context import RequestContext
from .router import Response, Router, json_body


def register(router: Router) -> None:
    router.add("GET", "/api/v1/p/{pid}/ip-mapping", list_ip_mapping)
    router.add("PUT", "/api/v1/p/{pid}/ip-mapping/{ip}", put_ip_mapping)
    router.add("DELETE", "/api/v1/p/{pid}/ip-mapping/{ip}", delete_ip_mapping)
    router.add("GET", "/api/v1/p/{pid}/admins", list_admins)
    router.add("POST", "/api/v1/p/{pid}/admins", add_admin)
    router.add("DELETE", "/api/v1/p/{pid}/admins/{name}", remove_admin)


def list_ip_mapping(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/ip-mapping -> {items: [{ip, name, updated_at}]}
    authz.authorize(ctx, "read")
    return Response(payload=people.list_ip_mapping(params["pid"]))


def put_ip_mapping(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # PUT /api/v1/p/{pid}/ip-mapping/{ip}  {"ip","name"} -> {item}
    # 路径 ip 与 body ip 不同 = 改 IP，先删后写；两者都在 service 层的同一次机型锁里。
    authz.authorize(ctx, "settings_write")
    return Response(payload=people.put_ip_mapping(params["pid"], params["ip"], json_body(body)))


def delete_ip_mapping(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # DELETE /api/v1/p/{pid}/ip-mapping/{ip} -> {removed: bool}
    authz.authorize(ctx, "settings_write")
    return Response(payload=people.delete_ip_mapping(params["pid"], params["ip"]))


def list_admins(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/admins -> {items: [{name, updated_at}]}
    authz.authorize(ctx, "read")
    return Response(payload=people.list_admins(params["pid"]))


def add_admin(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # POST /api/v1/p/{pid}/admins  {"name"} -> 201 {item}；重名 409 FILE_EXISTS
    # 姓名含 `/` 或 `\` -> 400 `detail.field="name"`（`people._validated_admin_name`）：
    # 这一档必须挡在创建端，因为同一个人只能由 `DELETE /admins/{name}` 撤——名字进路径段就
    # 必被 §16.8 的编码分隔符守卫拦成一只不带字段的 400，人是「加得进来、删不出去」。
    # service 返回的就是**响应载荷**（`{"item": …}`，与 `list_*` 的 `{"items": …}` 同一口径），
    # 这里只补状态码，不再拆一遍重新拼（§7.1：201 = 创建）。
    authz.authorize(ctx, "settings_write")
    return Response(status=201, payload=people.add_admin(params["pid"], json_body(body).get("name")))


def remove_admin(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # DELETE /api/v1/p/{pid}/admins/{name} -> {removed: bool}
    authz.authorize(ctx, "settings_write")
    return Response(payload=people.remove_admin(params["pid"], params["name"]))
