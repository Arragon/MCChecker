"""修订号计数（§7.3；M2-01）。

每个机型五个单调递增计数，**只存在内存里**：进程重启后归 1 是规范行为，客户端只比较
是否变化（§7.3）。读路径不碰磁盘，以满足 `/revision` 的 P99 < 5 ms 预算。

写接口的责任：成功之后调用 `bump(profile_id, part)`（§7.1「所有写接口在成功后递增对应
修订计数」）。递增发生在 service 层写成功之后，handler 不直接碰计数。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Tuple

from ..core import storage

PARTS = ("files", "favorites", "remarks", "settings", "tasks")

_LOCK = threading.Lock()
_COUNTERS: Dict[Tuple[str, str], Dict[str, int]] = {}


def _scope(profile_id: str) -> Tuple[str, str]:
    """计数键 = (工作区, 机型)；legacy 工作区记作 `default`。"""
    return (storage.get_active_workspace() or "default", profile_id)


def _entry(profile_id: str) -> Dict[str, int]:
    """取（或按 §7.3 的起始值 1 建）某机型的计数。调用方必须持锁。"""
    key = _scope(profile_id)
    entry = _COUNTERS.get(key)
    if entry is None:
        entry = {part: 1 for part in PARTS}
        _COUNTERS[key] = entry
    return entry


def bump(profile_id: str, part: str) -> None:
    """递增一个修订分量。分量名写错是编码错误，不静默忽略。"""
    if part not in PARTS:
        raise ValueError("未知的修订分量：%s（可选：%s）" % (part, "/".join(PARTS)))
    with _LOCK:
        _entry(profile_id)[part] += 1


def snapshot(profile_id: str) -> Dict[str, Any]:
    """§7.3 的响应体：`rev` 是五段点分串，`parts` 是各分量的当前值。"""
    with _LOCK:
        parts = dict(_entry(profile_id))
    return {"rev": ".".join(str(parts[part]) for part in PARTS), "parts": parts}


def reset() -> None:
    """测试钩子：清空所有机型的计数（纯进程内状态，不涉及持久化）。"""
    with _LOCK:
        _COUNTERS.clear()
