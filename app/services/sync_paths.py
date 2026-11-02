"""M16 Sync 持久路径。

新增的 workspace 级 Sync 数据统一落在
`<data>/workspaces/<wid>/sync/`；legacy default 也使用
`<data>/workspaces/default/sync/`，不把 Sync 文件混进 `data/profiles/`。

本模块只算路径与建目录；不读写业务 JSON。所有写入必须走
`sync_store` 的原子写 + workspace 锁。
"""

from __future__ import annotations

import os
from typing import FrozenSet

from app.core import storage
from app.core.sync_models import validate_id, validate_profile_id, validate_workspace_id

SYNC_FILES: FrozenSet[str] = frozenset(
    {
        "connections.json",
        "sources.json",
        "policy.json",
        "status.json",
    }
)


def sync_root(workspace_id: str) -> str:
    wid = validate_workspace_id(workspace_id)
    return os.path.join(storage.DATA_DIR, "workspaces", wid, "sync")


def sync_file(workspace_id: str, name: str) -> str:
    if name not in SYNC_FILES:
        raise ValueError("unsupported sync file: %s" % name)
    return os.path.join(sync_root(workspace_id), name)


def runs_root(workspace_id: str) -> str:
    return os.path.join(sync_root(workspace_id), "runs")


def run_dir(workspace_id: str, run_id: str) -> str:
    return os.path.join(runs_root(workspace_id), validate_id(run_id, "run_id"))


def profile_sync_root(workspace_id: str, profile_id: str) -> str:
    return os.path.join(
        sync_root(workspace_id),
        "profiles",
        validate_profile_id(profile_id),
    )


def ensure_dir(path: str) -> None:
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
