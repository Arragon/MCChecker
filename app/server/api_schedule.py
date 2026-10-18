"""定时更新接口（规范 §7.9、§10.4；卡 8 / M3-08 / INH-1893）。

handler 只做「解析请求 → 授权 → 调 `services.scheduler` → 序列化响应」：不碰 `storage`、
不碰修订号（§3.3 / 全局约束）。机型存在性与 `use_profile` 由 `http.py` 统一处理。

权限（§4.2）：`GET` = 全部角色（速览的定时状态块要给任何人看）；`PUT` = 部署者
（`settings_write`）。CSRF 头由 `http.py` 检查，本模块不重复实现（§11.4）。

载荷形状（§7.9）：`{"enabled","interval_hours","last_run","next_run_at"}`。
`next_run_at` **不持久化**，由调度器算；没有调度器在跑就是 `null`。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..services import authz, scheduler
from ..services.context import RequestContext
from .router import Response, Router, json_body


def register(router: Router) -> None:
    router.add("GET", "/api/v1/p/{pid}/schedule", get_schedule)
    router.add("PUT", "/api/v1/p/{pid}/schedule", put_schedule)


def get_schedule(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # GET /api/v1/p/{pid}/schedule -> {enabled, interval_hours, last_run, next_run_at}
    authz.authorize(ctx, "read")
    return Response(payload=scheduler.schedule_view(params["pid"]))


def put_schedule(
    ctx: RequestContext,
    params: Dict[str, str],
    query: Dict[str, List[str]],
    body: Optional[bytes],
) -> Response:
    # PUT /api/v1/p/{pid}/schedule  {"enabled","interval_hours"} -> 同形状
    # 校验、落盘、递增 settings、`scheduler.reload()` 全在 service 层（§10.4：不重启进程）。
    authz.authorize(ctx, "settings_write")
    payload: Dict[str, Any] = json_body(body)
    return Response(payload=scheduler.save_schedule(params["pid"], payload))
