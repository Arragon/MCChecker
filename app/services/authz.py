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
from typing import Callable, Dict, Optional, Tuple

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
    # --- 配置管理域（M5-07；docs/MCChecker_CONFIG_DOMAIN_SPEC.md §7）---
    "config_read": ANY,
    "config_registry_write": ADMIN,
    "config_tracking_admin": ADMIN,
    "config_change_submit": ADMIN,
    "config_change_approve": ADMIN,
    "config_artifact_generate": ADMIN,
}

#: 配置域动作 → 前端能力键。与动作同名，便于前后端一一对账。
CONFIG_CAPABILITIES: Tuple[str, ...] = (
    "config_read",
    "config_registry_write",
    "config_tracking_admin",
    "config_change_submit",
    "config_change_approve",
    "config_artifact_generate",
)


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


def capabilities(actor) -> Dict[str, bool]:
    """当前身份的能力矩阵，随 `/session` 下发（M5-07）。

    * 每个已登记 action 都给一个布尔值，前端据此禁用入口；
    * `admin` / `deployer` 是**兼容键**：两者同义，都等于"是管理员"。
      `8a1d8ba` 取消部署者身份后，本机回环不再获得任何额外权力（决策 D4）；
    * 未列出的能力一律视为 False —— 前端 `can()` 同样 deny-by-default，
      因此两边不会出现"前端放行、服务端 403"的错位。
    """
    matrix = {name: allowed(actor, name) for name in ACTIONS}
    is_admin = bool(actor is not None and actor.is_admin)
    matrix["admin"] = is_admin
    matrix["deployer"] = is_admin
    return matrix


def has_capability(actor, capability: str) -> bool:
    """未知能力返回 False（不抛异常）：前端旧页面可能传历史键。"""
    if capability in ACTIONS:
        return allowed(actor, capability)
    return capabilities(actor).get(capability, False)
