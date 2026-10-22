"""配置域的全局修订号（M5-08 / INH-1942；决策 D3）。

与旧 `services/revision.py` **分开**：那是 per-profile 的 5 段计数，被
`/api/v1/p/{pid}/revision` 的契约与大量断言依赖，动它会静默打破旧前端。
配置域是全局的（`/api/v1/config/*`），因此这里单独维护一个全局计数。

**纯内存**：进程重启归 1，客户端只比较"是否变化"（与 §7.3 同一口径）。
写路径成功后由 service 层调用 `bump()`；读路径零 I/O。
"""

from __future__ import annotations

import threading
from typing import Any, Dict

_LOCK = threading.Lock()
_REV = 0


def bump() -> int:
    """配置域发生一次成功写入后调用。返回新的修订号。"""
    global _REV
    with _LOCK:
        _REV += 1
        return _REV


def current() -> int:
    with _LOCK:
        return _REV


def snapshot(store_seq: Any = None) -> Dict[str, Any]:
    """`/api/v1/config/revision` 的响应体。

    `rev` 是本进程的写入计数；`store_seq` 是仓库存盘序号（跨重启可比），
    两者用途不同，不要互相顶替。
    """
    rev = current()
    payload: Dict[str, Any] = {"rev": "c%d" % rev, "seq": rev}
    if store_seq is not None:
        payload["store_seq"] = int(store_seq)
    return payload


def reset() -> None:
    """测试钩子。"""
    global _REV
    with _LOCK:
        _REV = 0
