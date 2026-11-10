"""M16-11 持久 SyncRun/SyncItem 清单与清理保全。

每个 run 一个目录：
`<data>/workspaces/<wid>/sync/runs/<run_id>/manifest.json`
`manifest_revision` 做 CAS；`frozen`/`receipt` 只存安全字段；事件日志由
`sync_events` 负责。清理只删除已完成 run，未完成 run 永不删除。
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core import fsutil
from app.core.errors import INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError
from app.core.sync_models import (
    RUN_STATES,
    SyncRun,
    validate_enum,
    validate_id,
    validate_revision,
    validate_safe_details,
    validate_timestamp,
    validate_workspace_id,
)

from . import sync_events, sync_store, sync_config_scope, workspace_context
from .sync_paths import ensure_dir, run_dir, runs_root

MANIFEST = "manifest.json"
UNFINISHED_STATES = frozenset({"queued", "running"})
TERMINAL_STATES = frozenset({"partial", "succeeded", "failed", "canceled", "interrupted"})
RUN_PATCH_FIELDS = frozenset(
    {"state", "counts", "started_at", "finished_at", "parent_run_id", "plan_id", "request_id"}
)
MAX_ITEMS = 500


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _manifest_path(workspace_id: str, run_id: str) -> str:
    return os.path.join(run_dir(workspace_id, run_id), MANIFEST)


def _safe_items(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AppError(INVALID_INPUT, "items 必须是数组", field="items")
    if len(value) > MAX_ITEMS:
        raise AppError(INVALID_INPUT, "items 过多", field="items")
    return [validate_safe_details(item, "items") for item in value]


def _read(workspace_id: str, run_id: str) -> Dict[str, Any]:
    path = _manifest_path(workspace_id, run_id)
    if not os.path.exists(path):
        raise AppError(NOT_FOUND, "run 不存在", field="run_id")
    raw = fsutil.read_json(path)
    if not isinstance(raw, dict):
        raise AppError(INVALID_INPUT, "run manifest 形状损坏", field="run")
    run = SyncRun.from_dict(raw.get("run") or {})
    revision = validate_revision(raw.get("manifest_revision", 0), "manifest_revision")
    return {
        "manifest_revision": revision,
        "run": run.to_dict(),
        "frozen": validate_safe_details(raw.get("frozen") or {}, "frozen"),
        "receipt": validate_safe_details(raw.get("receipt") or {}, "receipt"),
        "items": _safe_items(raw.get("items") or []),
    }


def _write(workspace_id: str, run_id: str, doc: Dict[str, Any]) -> None:
    path = _manifest_path(workspace_id, run_id)
    ensure_dir(os.path.dirname(path))
    fsutil.atomic_write_json(path, doc)


def create_run(
    workspace_id: str,
    run: SyncRun,
    frozen: Optional[Dict[str, Any]] = None,
    receipt: Optional[Dict[str, Any]] = None,
    items: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    if not isinstance(run, SyncRun):
        raise AppError(INVALID_INPUT, "run 必须是 SyncRun", field="run")
    if run.workspace_id != wid:
        raise AppError(INVALID_INPUT, "run 与 workspace 不匹配", field="workspace_id")
    if os.path.exists(_manifest_path(wid, run.run_id)):
        raise AppError(INVALID_INPUT, "run 已存在", field="run_id")
    doc = {
        "manifest_revision": 1,
        "run": run.to_dict(),
        "frozen": validate_safe_details(frozen or {}, "frozen"),
        "receipt": validate_safe_details(receipt or {}, "receipt"),
        "items": _safe_items(items or []),
    }
    with sync_store.workspace_lock(wid):
        if os.path.exists(_manifest_path(wid, run.run_id)):
            raise AppError(INVALID_INPUT, "run 已存在", field="run_id")
        _write(wid, run.run_id, doc)
    return get_run(wid, run.run_id)


def get_run(workspace_id: str, run_id: str) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    rid = validate_id(run_id, "run_id")
    with sync_store.workspace_lock(wid):
        return _read(wid, rid)


def update_run(
    workspace_id: str,
    run_id: str,
    patch: Optional[Dict[str, Any]] = None,
    run: Optional[SyncRun] = None,
    receipt: Optional[Dict[str, Any]] = None,
    frozen: Optional[Dict[str, Any]] = None,
    items: Optional[List[Dict[str, Any]]] = None,
    expected_manifest_revision: Optional[Any] = None,
) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    rid = validate_id(run_id, "run_id")
    if patch is not None and not isinstance(patch, dict):
        raise AppError(INVALID_INPUT, "patch 必须是对象", field="patch")
    if patch:
        unknown = set(patch) - RUN_PATCH_FIELDS
        if unknown:
            raise AppError(INVALID_INPUT, "不允许修改的 run 字段", field="patch", keys=sorted(unknown))
    with sync_store.workspace_lock(wid):
        current = _read(wid, rid)
        if expected_manifest_revision is not None:
            expected = validate_revision(expected_manifest_revision, "expected_manifest_revision")
            if current["manifest_revision"] != expected:
                raise AppError(REVISION_CONFLICT, "run manifest revision 已变化", field="manifest_revision")
        if run is not None:
            if not isinstance(run, SyncRun):
                raise AppError(INVALID_INPUT, "run 必须是 SyncRun", field="run")
            if run.run_id != rid or run.workspace_id != wid:
                raise AppError(INVALID_INPUT, "run 身份不匹配", field="run")
            next_run = run
        else:
            values = dict(current["run"])
            if patch:
                values.update(patch)
            next_run = SyncRun.from_dict(values)
        doc = {
            "manifest_revision": current["manifest_revision"] + 1,
            "run": next_run.to_dict(),
            "frozen": (
                validate_safe_details(frozen, "frozen")
                if frozen is not None
                else current["frozen"]
            ),
            "receipt": (
                validate_safe_details(receipt, "receipt")
                if receipt is not None
                else current["receipt"]
            ),
            "items": _safe_items(items) if items is not None else current["items"],
        }
        _write(wid, rid, doc)
    return get_run(wid, rid)


def finish_run(
    workspace_id: str,
    run_id: str,
    state: str,
    counts: Optional[Dict[str, int]] = None,
    finished_at: Optional[str] = None,
    receipt: Optional[Dict[str, Any]] = None,
    items: Optional[List[Dict[str, Any]]] = None,
    expected_manifest_revision: Optional[Any] = None,
) -> Dict[str, Any]:
    state = validate_enum(state, RUN_STATES, "state")
    if state not in TERMINAL_STATES:
        raise AppError(INVALID_INPUT, "finish_run 只接受终态", field="state")
    return update_run(
        workspace_id,
        run_id,
        patch={
            "state": state,
            "counts": counts or {},
            "finished_at": validate_timestamp(finished_at or _now(), "finished_at"),
        },
        receipt=receipt,
        items=items,
        expected_manifest_revision=expected_manifest_revision,
    )


def list_runs(
    workspace_id: str,
    state: Optional[str] = None,
    trigger: Optional[str] = None,
    limit: int = 50,
    cursor: str = "0",
    query: str = "",
    failed_only: bool = False,
    configuration_id: Optional[str] = None,
    profile_id: Optional[str] = None,
) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    if profile_id:
        workspace_context.assert_profile(wid, profile_id)
    if configuration_id:
        validate_id(configuration_id, "configuration_id")
        if not profile_id:
            raise AppError(INVALID_INPUT, "按配置筛选时必须指定机型", field="profile_id")
    owned_file = sync_config_scope.file_id(wid, profile_id, configuration_id)
    if state is not None:
        validate_enum(state, RUN_STATES, "state")
    if trigger is not None:
        validate_enum(trigger, ("manual", "schedule", "retry", "collector"), "trigger")
    try:
        offset = int(str(cursor or "0"))
    except ValueError:
        raise AppError(INVALID_INPUT, "cursor 非法", field="cursor")
    if offset < 0:
        raise AppError(INVALID_INPUT, "cursor 非法", field="cursor")
    if limit < 1 or limit > 200:
        raise AppError(INVALID_INPUT, "limit 必须在 1–200", field="limit")
    root = runs_root(wid)
    needle = str(query or "").strip().casefold()
    items: List[Dict[str, Any]] = []
    if os.path.isdir(root):
        for name in os.listdir(root):
            if not os.path.isdir(os.path.join(root, name)):
                continue
            try:
                doc = _read(wid, name)
            except AppError:
                continue
            run = doc["run"]
            if profile_id and (doc.get("frozen") or {}).get("profile_id") != profile_id:
                continue
            if configuration_id and not any(
                row.get("profile_id") == profile_id and sync_config_scope.matches(row.get("target") or {}, configuration_id, owned_file)
                for row in (doc.get("frozen") or {}).get("source_targets") or []
            ):
                # Old runs without a frozen target remain in workspace history;
                # rejoining today's source would incorrectly reassign their past.
                continue
            if state is not None and run.get("state") != state:
                continue
            if trigger is not None and run.get("trigger") != trigger:
                continue
            if failed_only and run.get("state") not in ("failed", "partial", "interrupted"):
                continue
            if needle:
                names = [str(run.get("run_id") or "")]
                for row in doc["items"]:
                    names.extend(str(row.get(key) or "") for key in ("name", "source_id", "profile_id"))
                if needle not in " ".join(names).casefold():
                    continue
            items.append(doc)
    items.sort(
        key=lambda item: (str(item["run"].get("created_at") or ""), str(item["run"]["run_id"])),
        reverse=True,
    )
    total = len(items)
    page = items[offset : offset + limit]
    return {
        "items": page,
        "total": total,
        "next_cursor": str(offset + limit) if offset + limit < total else None,
    }


def prune_runs(
    workspace_id: str,
    now: Optional[str] = None,
    retain_days: int = 30,
    max_runs: int = 2000,
) -> int:
    wid = validate_workspace_id(workspace_id)
    if retain_days < 1 or retain_days > 3650:
        raise AppError(INVALID_INPUT, "retain_days 必须在 1–3650", field="retain_days")
    if max_runs < 1 or max_runs > 10000:
        raise AppError(INVALID_INPUT, "max_runs 必须在 1–10000", field="max_runs")
    moment = validate_timestamp(now or _now(), "now", allow_none=False)
    try:
        current = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError:
        raise AppError(INVALID_INPUT, "now 非法", field="now")
    cutoff = current - timedelta(days=retain_days)
    root = runs_root(wid)
    finished: List[Dict[str, Any]] = []
    if os.path.isdir(root):
        for name in os.listdir(root):
            if not os.path.isdir(os.path.join(root, name)):
                continue
            try:
                doc = _read(wid, name)
            except AppError:
                continue
            run = doc["run"]
            if run.get("state") in UNFINISHED_STATES:
                continue
            created = run.get("created_at")
            try:
                created_at = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            finished.append({"run_id": run["run_id"], "created_at": created_at})
    finished.sort(key=lambda item: item["created_at"], reverse=True)
    removed = 0
    for index, item in enumerate(finished):
        should_remove = item["created_at"] < cutoff or index >= max_runs
        if not should_remove:
            continue
        path = os.path.join(root, item["run_id"])
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    return removed


def append_event(workspace_id: str, run_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    return sync_events.append_event(workspace_id, run_id, event)


def read_events(
    workspace_id: str,
    run_id: str,
    after_seq: int = 0,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    return sync_events.read_events(workspace_id, run_id, after_seq=after_seq, limit=limit)
