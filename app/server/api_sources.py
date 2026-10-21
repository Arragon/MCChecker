"""更新源接口（规范 §7.9、§10.2；M3-05 / INH-1890）。

handler 只做「解析参数 → 授权 → 调 `services.updates` → 组装 `Response`」；
映射读写、下载校验与归档都在 service 层，这里不碰 `storage`（§3.3）。
机型存在性与 `use_profile` 由 `http.py` 统一处理，这里不再检查机型。

权限（§4.2）：读=全部角色；写=`settings_write`（部署者）。CSRF 头由 `http.py` 检查，
本模块不重复实现（§11.4）。

「立即更新」不在这里：本卡的函数可被同步测试直接调用，HTTP 触发走卡 6 的任务队列
（§7.10 `POST /p/{pid}/tasks`），所以本模块**没有**下载类接口。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..services import authz, updates
from ..services.context import RequestContext
from .router import Response, Router, json_body


def register(router: Router) -> None:
    router.add("GET", "/api/v1/p/{pid}/sources", list_sources)
    router.add("POST", "/api/v1/p/{pid}/sources", create_source)
    router.add("PATCH", "/api/v1/p/{pid}/sources/{name}", rename_source)
    router.add("DELETE", "/api/v1/p/{pid}/sources/{name}", delete_source)


def list_sources(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/sources
    authz.authorize(ctx, "read")
    return Response(payload={"items": updates.list_sources(params["pid"])})


def create_source(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # POST /api/v1/p/{pid}/sources  {"name","url","record_url"}
    authz.authorize(ctx, "settings_write")
    payload = json_body(body)
    item = updates.create_source(
        params["pid"],
        payload.get("name"),
        payload.get("url"),
        payload.get("record_url"),
        payload.get("keyword"),
    )
    return Response(status=201, payload={"item": item})


def rename_source(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # PATCH /api/v1/p/{pid}/sources/{name}  {"name","url","record_url","migrate_files"}
    authz.authorize(ctx, "settings_write")
    payload = json_body(body)
    item = updates.rename_source(
        params["pid"],
        params["name"],
        payload.get("name"),
        payload.get("url"),
        payload.get("record_url"),
        # 缺省按 True：与 `storage.update_config_mapping` 同默认，改名把本地文件与
        # 历史留在旧名下是更难发现的数据孤儿。
        bool(payload.get("migrate_files", True)),
        payload.get("keyword") if "keyword" in payload else None,
    )
    return Response(payload={"item": item})


def delete_source(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # DELETE /api/v1/p/{pid}/sources/{name}   只删映射，不删文件
    authz.authorize(ctx, "settings_write")
    return Response(payload={"removed": updates.delete_source(params["pid"], params["name"])})
