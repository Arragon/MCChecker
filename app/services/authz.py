"""服务端权限入口（§4.2、§4.3、§11.4；T-07；C19）。

矩阵在这里**单点定义**，服务端强制；前端隐藏/禁用按钮只是体验（§4.3）。

* `authorize(ctx, action)`——service 入口在碰数据之前调用；不足即 403。
* `require(action)`——同一件事的装饰器写法（函数第一个参数是 `ctx`）。

每类 action 的允许范围（`§4.2`）：

| action | 允许 |
|---|---|
| `read` / `temp_upload` / `favorites_write` / `remarks_submit` / `dl_compute` | 所有角色（含访客） |
| `upload_persist` / `settings_write` / `tasks_run` / `profiles_write` / `review` / `delete_file` | 管理员 |

身份在每个请求重新解析（`http.py` 第 4 步），因此撤权后下一次请求即 403。
"""

from __future__ import annotations

import functools
from typing import Callable, Dict, Optional

from ..core.errors import FORBIDDEN, AppError
from .context import RequestContext

ANY = "any"
DEPLOYER = "deployer"
ADMIN = "admin"

ACTIONS: Dict[str, str] = {
    "read": ANY,
    "temp_upload": ANY,
    "favorites_write": ANY,
    "remarks_submit": ANY,
    "dl_compute": ANY,
    "upload_persist": ADMIN,
    "settings_write": ADMIN,
    "tasks_run": ADMIN,
    "profiles_write": ADMIN,
    "review": ADMIN,
    "delete_file": ADMIN,
}


def action_level(action: str) -> str:
    """查 action 对应的权限档；未定义的 action 是**编码错误**，不伪装成 403。"""
    try:
        return ACTIONS[action]
    except KeyError:
        raise ValueError("未定义的操作：%s（请先在 authz.ACTIONS 登记）" % action)


def allowed(actor, action: str) -> bool:
    level = action_level(action)
    if level == ANY:
        return True
    if actor is None:  # 没有身份即无权（例如后台任务误用请求上下文）
        return False
    return bool(actor.is_admin)


def authorize(ctx: RequestContext, action: str) -> None:
    """权限校验：不足抛 403 FORBIDDEN（`detail.action` 便于前端解释）。"""
    if not allowed(ctx.actor, action):
        raise AppError(FORBIDDEN, "当前身份无权执行该操作", action=action)


def require(action: str) -> Callable:
    """装饰器形式（与 `authorize` 同一张矩阵）：

        @require("settings_write")
        def save_source(ctx, payload): ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(ctx: Optional[RequestContext], *args, **kwargs):
            authorize(ctx, action)
            return func(ctx, *args, **kwargs)

        return wrapper

    return decorator
