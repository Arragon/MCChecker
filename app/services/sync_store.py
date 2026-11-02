"""M16 Sync 的 JSON 原子存储与 workspace 锁。

- 读：不存在返回 default 的深拷贝；损坏由 `fsutil.read_json` 抛
  `CorruptDataError`，不把损坏当空数据。
- 写：`fsutil.atomic_write_json`；写前确保目录存在。
- 读改写：同一 workspace 内串行；不同 workspace 不互相阻塞。
- 跨进程仍由现有单实例锁与单写者约束保证；这里只解决进程内并发。
"""

from __future__ import annotations

import copy
import os
import threading
from typing import Any, Callable, Dict

from app.core import fsutil

from .sync_paths import ensure_dir, sync_file

_MUTEX = threading.Lock()
_LOCKS: Dict[str, threading.RLock] = {}


def workspace_lock(workspace_id: str) -> threading.RLock:
    with _MUTEX:
        lock = _LOCKS.get(workspace_id)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[workspace_id] = lock
        return lock


def read_doc(workspace_id: str, name: str, default: Any) -> Any:
    path = sync_file(workspace_id, name)
    with workspace_lock(workspace_id):
        return fsutil.read_json(path, copy.deepcopy(default))


def write_doc(workspace_id: str, name: str, doc: Any) -> None:
    path = sync_file(workspace_id, name)
    with workspace_lock(workspace_id):
        ensure_dir(os.path.dirname(path))
        fsutil.atomic_write_json(path, doc)


def update_doc(
    workspace_id: str,
    name: str,
    mutator: Callable[[Dict[str, Any]], Dict[str, Any]],
    default: Dict[str, Any],
) -> Dict[str, Any]:
    path = sync_file(workspace_id, name)
    with workspace_lock(workspace_id):
        current = fsutil.read_json(path, copy.deepcopy(default))
        if not isinstance(current, dict):
            raise ValueError("%s must be a JSON object" % name)
        updated = mutator(copy.deepcopy(current))
        if not isinstance(updated, dict):
            raise ValueError("%s mutator must return an object" % name)
        ensure_dir(os.path.dirname(path))
        fsutil.atomic_write_json(path, updated)
        return updated
