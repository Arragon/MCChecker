"""V3 Workspace 存储门面（M9-04 / INH-2121）。

每个 W+P 一个 `ConfigRepository`，root 是 `data/workspaces/<wid>/profiles/<pid>/config_management`；
legacy 仍用 `data/config_management`。CAS、blob、幂等回执、audit 都复用仓储，
不另写平行的 `workspace_state.json`。损坏 state 会锁存为只读，直到进程重启。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from ..core import fsutil, storage
from ..core.config_repository import ConfigRepository
from ..core.errors import CORRUPT_DATA, NOT_FOUND, REVISION_CONFLICT, AppError, CorruptDataError
from . import workspace_catalog

STATE_KIND = "workspace_config_state"
STATE_ID = "ws_000001"
_READONLY = set()


def repository(workspace_id: str, profile_id: str) -> ConfigRepository:
    """按 W+P 返回仓储；legacy `default` 仍指旧 `data/config_management`。"""
    wid = str(workspace_id or "").strip() or workspace_catalog.LEGACY_WORKSPACE_ID
    ws = workspace_catalog.get_workspace(wid)
    pid = storage._normalize_profile_id(profile_id)
    if not any(str(p.get("id")) == pid for p in ws.get("profiles") or []):
        raise AppError(NOT_FOUND, "机型不存在", profile=pid)
    root = storage.DATA_DIR if wid == workspace_catalog.LEGACY_WORKSPACE_ID else workspace_catalog.profile_root(wid, pid)
    return ConfigRepository(root)


def state(workspace_id: str, profile_id: str, repo: Optional[ConfigRepository] = None) -> Dict[str, Any]:
    """返回不透明 state_id/seq/digest；损坏则锁存只读并抛 CORRUPT_DATA。"""
    wid = str(workspace_id or "").strip() or workspace_catalog.LEGACY_WORKSPACE_ID
    pid = storage._normalize_profile_id(profile_id)
    repo = repo or repository(wid, pid)
    key = os.path.realpath(repo.root)
    if key in _READONLY:
        raise AppError(CORRUPT_DATA, "工作区配置状态已进入只读保护", workspace=wid, profile=pid)
    try:
        raw = repo.load_state()
    except CorruptDataError:
        _READONLY.add(key)
        raise
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    state_id = hashlib.sha256(("%s|%s|%s" % (wid, pid, digest)).encode("utf-8")).hexdigest()[:20]
    return {"state_id": state_id, "seq": int(raw.get("seq") or 0), "digest": digest}


def commit_state(
    workspace_id: str,
    profile_id: str,
    payload: Dict[str, Any],
    actor: Any = None,
    reason: str = "",
    idempotency_key: Optional[str] = None,
    expected_state_id: Optional[str] = None,
) -> Dict[str, Any]:
    """CAS 写入一份不可变 `workspace_config_state`；state.json 最后公布。"""
    wid = str(workspace_id or "").strip() or workspace_catalog.LEGACY_WORKSPACE_ID
    pid = storage._normalize_profile_id(profile_id)
    repo = repository(wid, pid)
    current = state(wid, pid, repo)
    if expected_state_id and expected_state_id != current["state_id"]:
        raise AppError(
            REVISION_CONFLICT,
            "工作区配置状态已变化，请重新读取",
            expected=expected_state_id,
            actual=current["state_id"],
        )
    scoped_key = None
    if idempotency_key:
        scoped_key = "ws|%s|%s|%s" % (wid, pid, idempotency_key)
    # 幂等重放时 `expected` 不能参与 request_hash，否则第二次同键会被误判为不同请求；
    # 带幂等键的写入用回执本身做去重，CAS 由不带键的写入负责。
    expected = None if idempotency_key else {"seq": current["seq"]}
    tx = repo.transaction(
        actor,
        reason,
        action="workspace_config_state.commit",
        idempotency_key=scoped_key,
        expected=expected,
    )
    tx.put(STATE_KIND, payload, object_id=STATE_ID)
    receipt = tx.commit()
    return {"receipt": receipt, "state": state(wid, pid, repo)}


def plan_legacy_migration() -> Dict[str, Any]:
    """只读扫描旧 `data/config_management`；不 mkdir、不写 state。"""
    root = os.path.join(storage.DATA_DIR, "config_management")
    entries: List[Dict[str, Any]] = []
    if os.path.isdir(root):
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                with open(path, "rb") as handle:
                    data = handle.read()
                entries.append(
                    {
                        "path": os.path.relpath(path, root).replace(os.sep, "/"),
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
    return {"source": root, "target": "data/workspaces/default/profiles/<pid>/config_management", "entries": entries}


def reset_for_tests() -> None:
    """测试隔离：清空只读锁存。生产代码不要调用。"""
    _READONLY.clear()
