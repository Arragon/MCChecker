"""删除文件接口（§7.4、§5.6；M2-12）。

权限（§4.2）：`delete_file` = **管理员**。部署者从本机访问但不在管理员名单时同样 403
（§4.2 脚注 1）——这条只在服务端强制，前端把按钮置灰只是体验。
移入恢复区、清理引用与回滚都在 `services/recovery.py`；handler 不碰业务文件（§3.3）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..services import authz, recovery
from ..services.context import RequestContext
from .router import Response, Router


def register(router: Router) -> None:
    router.add("DELETE", "/api/v1/p/{pid}/files/{name}", delete_file)


def delete_file(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # DELETE /api/v1/p/{pid}/files/{name}
    authz.authorize(ctx, "delete_file")
    actor = ctx.actor
    return Response(
        payload=recovery.delete_file(
            params["pid"],
            params["name"],
            ip=ctx.ip,
            actor_name=actor.name if actor is not None else "",
        )
    )
