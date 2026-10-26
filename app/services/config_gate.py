"""配置管理特性的门禁（M5-08 / INH-1942；决策 D5）。

`MCHECKER_CONFIG_MANAGEMENT` 三档：

| 值 | 读接口 | 写接口 |
|---|---|---|
| `off` | 503 `CONFIG_DISABLED` | 503 `CONFIG_DISABLED` |
| `readonly` | 正常 | 503 `CONFIG_DISABLED`（`detail.mode = readonly`） |
| `on`（默认） | 正常 | 正常，仍需 `authz` 判定 |

未设置环境变量时默认 `on`；若需与旧版一致或暂时关闭入口，可显式设为 `off`。
门禁在 handler 入口判定（而不是路由注册时开关），这样改环境变量后重启即可生效，
测试也不必重建路由表。
"""

from __future__ import annotations

import os

from ..core.errors import CONFIG_DISABLED, AppError

ENV_KEY = "MCHECKER_CONFIG_MANAGEMENT"

MODE_OFF = "off"
MODE_READONLY = "readonly"
MODE_ON = "on"

MODES = (MODE_OFF, MODE_READONLY, MODE_ON)

_ON_WORDS = {"on", "1", "true", "yes", "enabled"}
_READONLY_WORDS = {"readonly", "read-only", "ro", "read"}


def mode() -> str:
    """当前档位。未设置时默认 `on`；无法识别的取值按 `off` 处理（失败关闭，不失败开放）。"""
    if ENV_KEY not in os.environ:
        return MODE_ON
    raw = str(os.environ.get(ENV_KEY, "")).strip().lower()
    if not raw:
        return MODE_OFF
    if raw in _ON_WORDS:
        return MODE_ON
    if raw in _READONLY_WORDS:
        return MODE_READONLY
    return MODE_OFF


def enabled() -> bool:
    return mode() != MODE_OFF


def require_read() -> None:
    """读接口门禁：`off` 直接 503。"""
    current = mode()
    if current == MODE_OFF:
        raise AppError(
            CONFIG_DISABLED,
            "配置管理功能未开启",
            mode=current,
            env=ENV_KEY,
        )


def require_write() -> None:
    """写接口门禁：`off` 与 `readonly` 都拒绝，且给出明确原因。"""
    current = mode()
    if current != MODE_ON:
        raise AppError(
            CONFIG_DISABLED,
            "配置管理当前不可写",
            mode=current,
            env=ENV_KEY,
            hint="将 %s 设为 on 后重启服务" % ENV_KEY,
        )
