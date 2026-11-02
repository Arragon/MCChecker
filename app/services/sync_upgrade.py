"""M16-37 旧来源/按机型计划的无损升级、备份、校验与回退。

本模块只做三件事：

1. `plan` 复用 M16-06 的只读迁移计划（`sync_migration.plan_workspace`），
   执行前再算一次，`plan_id` 不一致即拒绝（旧文件在计划之后被改过）。
2. `backup` 先备份整个工作区数据（含 legacy `profiles/<pid>` 与
   `workspaces/<wid>`），manifest 记录相对路径、sha256、字节数与时间戳；
   `*.enc`、`*.key`、含 `M16SEC1` magic 的文件、日志/锁/缓存一律不复制。
3. `execute` 只通过既有 `sync_sources.create_source` / `sync_policy.save_policy`
   落地草稿，重复执行不产生重复来源、不改 policy revision；旧
   config/records/archive 在升级前后逐文件 sha256 必须完全一致，任何写入失败
   或指纹变化都会按备份回滚 Sync 文件后重新抛出。

`rollback` 只恢复 manifest 记录过的路径，并且只删除 `sync/` 下确实由新版本
创建的已知文件；`sync/runs/`、secret 根与 manifest 之外的路径一律不碰。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from app.core import fsutil, storage
from app.core.errors import (
    INVALID_INPUT,
    NOT_FOUND,
    REVISION_CONFLICT,
    STORAGE_FAILURE,
    AppError,
)
from app.core.sync_models import validate_interval_hours, validate_workspace_id

from . import sync_migration, sync_paths, sync_policy, sync_sources

logger = logging.getLogger(__name__)

BACKUP_MANIFEST = "backup_manifest.json"
SCHEMA_VERSION = 1
SECRET_MAGIC = b"M16SEC1"
SECRET_SUFFIXES = (".enc", ".key")
DEFAULT_WORKSPACE = "default"

_SKIP_DIRS = frozenset(
    {
        "cache",
        "logs",
        "log",
        "locks",
        "__pycache__",
        ".git",
        "secrets",
        "secretstore",
        "vault",
    }
)
_SKIP_FILE_SUFFIXES = (".log", ".lock", ".tmp", ".pid")
_ROOT_LEGACY_FILES = ("config_mapping.json", "schedule.json", "update_status.json")
_ROOT_LEGACY_DIRS = ("configs", "archive", "files", "records")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(path: str) -> str:
    moment = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_magic(path: str) -> bool:
    """逐块查找 `M16SEC1`，块之间保留 magic 长度的重叠，避免跨块漏判。"""
    overlap = max(len(SECRET_MAGIC) - 1, 0)
    tail = b""
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                return False
            if SECRET_MAGIC in tail + chunk:
                return True
            tail = (tail + chunk)[-overlap:] if overlap else b""


def _secret_reason(path: str) -> Optional[str]:
    if os.path.basename(path).lower().endswith(SECRET_SUFFIXES):
        return "secret_suffix"
    if _contains_magic(path):
        return "secret_magic"
    return None


def _rel_from_data(path: str) -> str:
    rel = os.path.relpath(path, storage.DATA_DIR)
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        raise AppError(STORAGE_FAILURE, "备份路径越出数据根", path=path)
    return rel.replace(os.sep, "/")


def _safe_join(root: str, relative: str) -> str:
    """把 manifest 中的 POSIX 相对路径拼到 root 下，越界（含符号链接）即拒绝。"""
    parts = relative.split("/")
    root_real = os.path.realpath(os.path.abspath(root))
    target = os.path.realpath(os.path.join(root_real, *parts))
    if target != root_real and not target.startswith(root_real + os.sep):
        raise AppError(INVALID_INPUT, "备份路径越界", field="path", path=relative)
    return target


def _normalize_rel(relative: Any) -> str:
    text = str(relative or "").strip()
    if not text or "\\" in text or text.startswith("/"):
        raise AppError(INVALID_INPUT, "备份路径非法", field="path", path=relative)
    if re.match(r"^[A-Za-z]:", text):
        raise AppError(INVALID_INPUT, "备份路径非法", field="path", path=relative)
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise AppError(INVALID_INPUT, "备份路径非法", field="path", path=relative)
    return "/".join(parts)


def _allowed_workspace_path(workspace_id: str, relative: str) -> bool:
    """只允许本工作区的数据路径，避免备份/回退跨工作区或写全局文件。"""
    if relative.startswith("workspaces/%s/" % workspace_id):
        return True
    if workspace_id != DEFAULT_WORKSPACE:
        return False
    if relative.startswith("profiles/"):
        return True
    if relative in _ROOT_LEGACY_FILES:
        return True
    return any(relative.startswith(name + "/") for name in _ROOT_LEGACY_DIRS)


def _skip_dir(name: str) -> bool:
    return name in _SKIP_DIRS


def _skip_file(name: str) -> bool:
    if name == BACKUP_MANIFEST:
        return True
    return name.lower().endswith(_SKIP_FILE_SUFFIXES)


def _iter_root(root: str, *, include_sync: bool) -> Iterator[Tuple[str, str]]:
    """产出 `(relative_path, absolute_path)`；不跟随符号链接。"""
    root = os.path.abspath(root)
    if os.path.isfile(root):
        if not os.path.islink(root) and not _skip_file(os.path.basename(root)):
            yield _rel_from_data(root), root
        return
    if not os.path.isdir(root):
        return
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(
            name
            for name in dirs
            if not _skip_dir(name)
            and (include_sync or name != "sync")
            and not os.path.islink(os.path.join(current, name))
        )
        for name in sorted(names):
            path = os.path.join(current, name)
            if os.path.islink(path) or _skip_file(name):
                continue
            yield _rel_from_data(path), path


def _source_roots(workspace_id: str) -> List[str]:
    """工作区需要备份/校验的数据根；不含 secret 根、日志、锁与缓存。"""
    wid = validate_workspace_id(workspace_id)
    roots: List[str] = []
    if wid == DEFAULT_WORKSPACE:
        roots.append(storage.PROFILES_DIR)
        default_sync = os.path.join(storage.WORKSPACES_DIR, DEFAULT_WORKSPACE)
        if os.path.exists(default_sync):
            roots.append(default_sync)
        # v1 旧布局的根级回退文件/目录（`load_*` 在 profiles 缺文件时会读这里）。
        for name in _ROOT_LEGACY_FILES:
            path = os.path.join(storage.DATA_DIR, name)
            if os.path.exists(path):
                roots.append(path)
        for name in _ROOT_LEGACY_DIRS:
            path = os.path.join(storage.DATA_DIR, name)
            if os.path.isdir(path):
                roots.append(path)
    else:
        roots.append(os.path.join(storage.WORKSPACES_DIR, wid))
    return [os.path.abspath(path) for path in roots if os.path.exists(path)]


def _is_within(child: str, parent: str) -> bool:
    child_real = os.path.realpath(os.path.abspath(child))
    parent_real = os.path.realpath(os.path.abspath(parent))
    return child_real == parent_real or child_real.startswith(parent_real + os.sep)


def _assert_backup_location(backup_dir: str, roots: List[str]) -> str:
    raw = str(backup_dir or "").strip()
    if not raw:
        raise AppError(INVALID_INPUT, "backup_dir 不能为空", field="backup_dir")
    target = os.path.abspath(os.path.expanduser(raw))
    if not target or os.path.basename(target) in ("", os.curdir):
        raise AppError(INVALID_INPUT, "backup_dir 非法", field="backup_dir")
    if os.path.exists(target) and not os.path.isdir(target):
        raise AppError(INVALID_INPUT, "backup_dir 不是目录", field="backup_dir")
    for root in roots:
        if _is_within(target, root) or _is_within(root, target):
            raise AppError(
                INVALID_INPUT,
                "backup_dir 不能位于待备份数据目录内（也不能包含它）",
                field="backup_dir",
            )
    return target


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------


def plan(workspace_id: str, profile_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """只读迁移计划；`writes=0`，不建 sync 目录、不改旧文件。"""
    return sync_migration.plan_workspace(workspace_id, profile_ids)


# ---------------------------------------------------------------------------
# 备份
# ---------------------------------------------------------------------------


def _collect_entries(workspace_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    entries: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    seen = set()
    for root in _source_roots(workspace_id):
        for relative, path in _iter_root(root, include_sync=True):
            if relative in seen:
                continue
            seen.add(relative)
            reason = _secret_reason(path)
            if reason:
                skipped.append({"path": relative, "reason": reason})
                continue
            entries.append(
                {
                    "path": relative,
                    "sha256": _sha256_file(path),
                    "size": os.path.getsize(path),
                    "timestamp": _stamp(path),
                }
            )
    entries.sort(key=lambda row: row["path"])
    skipped.sort(key=lambda row: row["path"])
    return entries, skipped


def _read_manifest(workspace_id: str, backup_dir: str) -> Dict[str, Any]:
    path = os.path.join(backup_dir, BACKUP_MANIFEST)
    if not os.path.isfile(path):
        raise AppError(INVALID_INPUT, "备份缺少 manifest", field="backup_dir")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        raise AppError(INVALID_INPUT, "备份 manifest 不是合法 JSON", field="backup_dir", error=str(exc))
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA_VERSION:
        raise AppError(INVALID_INPUT, "备份 manifest 版本不支持", field="backup_dir")
    if raw.get("workspace_id") != workspace_id:
        raise AppError(INVALID_INPUT, "备份属于其他工作区", field="workspace_id")
    if not isinstance(raw.get("files"), list):
        raise AppError(INVALID_INPUT, "备份 manifest 缺少 files", field="backup_dir")
    return raw


def _existing_backup(workspace_id: str, backup_dir: str) -> Optional[Dict[str, Any]]:
    if not os.path.isdir(backup_dir):
        return None
    if not os.listdir(backup_dir):
        return None
    manifest = _read_manifest(workspace_id, backup_dir)
    report = verify(workspace_id, backup_dir)
    if not report["ok"]:
        raise AppError(
            INVALID_INPUT,
            "现有备份校验失败，拒绝覆盖或复用",
            field="backup_dir",
            problems=report["problems"][:20],
        )
    return manifest


def _cleanup_partial(backup_dir: str, copied: List[str], created: bool) -> None:
    if created:
        # 本调用新建的目录只属于这次备份，失败时整体清掉，别留下半份副本。
        shutil.rmtree(backup_dir, ignore_errors=True)
        return
    for relative in copied:
        try:
            os.unlink(_safe_join(backup_dir, relative))
        except OSError:
            pass
    try:
        os.unlink(os.path.join(backup_dir, BACKUP_MANIFEST))
    except OSError:
        pass


def backup(workspace_id: str, backup_dir: str, actor: str = "system") -> Dict[str, Any]:
    """把工作区数据复制到 `backup_dir` 并写 `backup_manifest.json`。"""
    wid = validate_workspace_id(workspace_id)
    roots = _source_roots(wid)
    target = _assert_backup_location(backup_dir, roots)
    existing = _existing_backup(wid, target)
    if existing is not None:
        return dict(existing, backup_dir=target, reused=True)

    entries, skipped = _collect_entries(wid)
    created = not os.path.isdir(target)
    os.makedirs(target, exist_ok=True)
    copied: List[str] = []
    try:
        for row in entries:
            source = _safe_join(storage.DATA_DIR, row["path"])
            destination = _safe_join(target, row["path"])
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(row["path"])
            if _sha256_file(destination) != row["sha256"]:
                raise AppError(STORAGE_FAILURE, "备份复制后 sha256 不一致", field="path", path=row["path"])
        manifest = {
            "schema": SCHEMA_VERSION,
            "kind": "sync_upgrade",
            "workspace_id": wid,
            "actor": str(actor or "system")[:80],
            "created_at": _now(),
            "file_count": len(entries),
            "total_bytes": sum(int(row["size"]) for row in entries),
            "files": entries,
            "skipped_secrets": skipped,
        }
        fsutil.atomic_write_json(os.path.join(target, BACKUP_MANIFEST), manifest)
    except BaseException:
        _cleanup_partial(target, copied, created)
        raise
    return dict(manifest, backup_dir=target, reused=False)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _manifest_problems(
    workspace_id: str, backup_dir: str, manifest: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], int, int]:
    problems: List[Dict[str, Any]] = []
    recorded = set()
    matched = 0
    files = manifest.get("files") or []
    for item in files:
        try:
            if not isinstance(item, dict):
                raise AppError(INVALID_INPUT, "manifest 条目不是对象", field="files")
            relative = _normalize_rel(item.get("path"))
            if not _allowed_workspace_path(workspace_id, relative):
                raise AppError(INVALID_INPUT, "manifest 路径不属于该工作区", field="path", path=relative)
            expected = str(item.get("sha256") or "").lower()
            if not _SHA_RE.match(expected):
                raise AppError(INVALID_INPUT, "manifest sha256 非法", field="sha256", path=relative)
            size = item.get("size")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise AppError(INVALID_INPUT, "manifest size 非法", field="size", path=relative)
        except AppError as exc:
            problems.append({"path": str(item.get("path") if isinstance(item, dict) else item), "status": "invalid_entry", "detail": exc.message})
            continue
        recorded.add(relative)
        if relative.lower().endswith(SECRET_SUFFIXES):
            problems.append({"path": relative, "status": "secret_path"})
            continue
        try:
            path = _safe_join(backup_dir, relative)
        except AppError as exc:
            problems.append({"path": relative, "status": "unsafe_path", "detail": exc.message})
            continue
        if not os.path.isfile(path):
            problems.append({"path": relative, "status": "missing", "expected_sha256": expected})
            continue
        if os.path.islink(path):
            problems.append({"path": relative, "status": "symlink"})
            continue
        actual_size = os.path.getsize(path)
        actual = _sha256_file(path)
        if actual_size != size or actual != expected:
            problems.append(
                {
                    "path": relative,
                    "status": "changed",
                    "expected_sha256": expected,
                    "actual_sha256": actual,
                    "expected_size": size,
                    "actual_size": actual_size,
                }
            )
            continue
        if _contains_magic(path):
            problems.append({"path": relative, "status": "secret_content"})
            continue
        matched += 1

    # 备份目录里多出来的文件同样视为可疑：回退只应消费 manifest 记录的内容。
    for current, dirs, names in os.walk(backup_dir):
        dirs[:] = sorted(name for name in dirs if not os.path.islink(os.path.join(current, name)))
        for name in sorted(names):
            path = os.path.join(current, name)
            if os.path.islink(path):
                relative = _rel_from_backup(path, backup_dir)
                if relative != BACKUP_MANIFEST:
                    problems.append({"path": relative, "status": "symlink"})
                continue
            relative = _rel_from_backup(path, backup_dir)
            if relative == BACKUP_MANIFEST or relative in recorded:
                continue
            problems.append({"path": relative, "status": "unexpected"})
    problems.sort(key=lambda row: (str(row.get("path")), str(row.get("status"))))
    return problems, matched, len(files)


def _rel_from_backup(path: str, backup_dir: str) -> str:
    return os.path.relpath(path, backup_dir).replace(os.sep, "/")


def verify(workspace_id: str, backup_dir: str) -> Dict[str, Any]:
    """逐文件重算备份 sha256；缺文件/改内容/多文件都返回结构化列表。"""
    wid = validate_workspace_id(workspace_id)
    target = os.path.abspath(os.path.expanduser(str(backup_dir or "")))
    result: Dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "workspace_id": wid,
        "backup_dir": target,
        "ok": False,
        "checked": 0,
        "matched": 0,
        "problems": [],
    }
    if not os.path.isdir(target):
        result["problems"] = [{"path": BACKUP_MANIFEST, "status": "missing_backup"}]
        return result
    manifest_path = os.path.join(target, BACKUP_MANIFEST)
    if not os.path.isfile(manifest_path):
        result["problems"] = [{"path": BACKUP_MANIFEST, "status": "missing"}]
        return result
    try:
        manifest = _read_manifest(wid, target)
    except AppError as exc:
        result["problems"] = [
            {"path": BACKUP_MANIFEST, "status": "invalid_manifest", "detail": exc.message}
        ]
        return result
    problems, matched, checked = _manifest_problems(wid, target, manifest)
    result.update(
        {
            "ok": not problems,
            "checked": checked,
            "matched": matched,
            "problems": problems,
            "created_at": manifest.get("created_at"),
            "file_count": manifest.get("file_count"),
            "skipped_secrets": len(manifest.get("skipped_secrets") or []),
        }
    )
    return result


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------


def _legacy_hashes(workspace_id: str) -> Dict[str, str]:
    """旧 config/records/archive 与机型文件的逐文件 sha256；不包含 sync。"""
    out: Dict[str, str] = {}
    for root in _source_roots(workspace_id):
        for relative, path in _iter_root(root, include_sync=False):
            out[relative] = _sha256_file(path)
    return out


def _list_all_sources(workspace_id: str, profile_id: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    cursor = "0"
    while True:
        page = sync_sources.list_sources(
            workspace_id,
            profile_id=profile_id,
            status="all",
            limit=200,
            cursor=cursor,
        )
        rows.extend(page["items"])
        cursor = page.get("next_cursor")
        if not cursor:
            return rows


def _find_existing_source(
    workspace_id: str, draft: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    source_id = str(draft.get("source_id") or "")
    if source_id:
        try:
            return sync_sources.get_source(workspace_id, source_id, include_deleted=True)
        except AppError as exc:
            if exc.code != NOT_FOUND:
                raise
    name = str(draft.get("name") or "").casefold()
    for row in _list_all_sources(workspace_id, str(draft.get("profile_id"))):
        if str(row.get("name") or "").casefold() == name:
            return row
    return None


def _source_payload(draft: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "profile_id": draft["profile_id"],
        "name": draft["name"],
        "kind": draft.get("kind", "http_file"),
        "target": draft["target"],
        "enabled": True,
        "schedule_mode": "inherit",
        "interval_hours": None,
        "connection_id": None,
        "resource_ref": draft.get("resource_ref"),
        "display_path": None,
        "record_source": draft.get("record_source"),
        "keyword": draft.get("keyword") or "",
    }


def _create_one_source(
    workspace_id: str,
    draft: Dict[str, Any],
    skipped: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    target = draft.get("target")
    if (
        draft.get("target_status") != "ready"
        or not isinstance(target, dict)
        or not str(target.get("file_id") or "").strip()
    ):
        skipped.append(
            {
                "profile_id": draft.get("profile_id"),
                "name": draft.get("name"),
                "reason": "target_missing",
            }
        )
        return None

    existing = _find_existing_source(workspace_id, draft)
    if existing is not None:
        reason = "source_exists"
        if existing.get("deleted"):
            reason = "source_tombstoned"
        elif existing.get("source_id") != draft.get("source_id"):
            reason = "source_name_conflict"
        skipped.append(
            {
                "source_id": existing.get("source_id"),
                "profile_id": draft.get("profile_id"),
                "name": draft.get("name"),
                "reason": reason,
            }
        )
        return None

    try:
        created = sync_sources.create_source(workspace_id, _source_payload(draft))
    except AppError as exc:
        if exc.code == INVALID_INPUT and exc.detail.get("field") == "name":
            # 并发创建同名来源：仍然保持「不重复」，不把它当升级失败。
            skipped.append(
                {
                    "profile_id": draft.get("profile_id"),
                    "name": draft.get("name"),
                    "reason": "source_exists",
                }
            )
            return None
        raise
    return {
        "source_id": created["source_id"],
        "profile_id": created["profile_id"],
        "name": created["name"],
    }


def _merge_policy(workspace_id: str, overrides: Dict[str, Any]) -> Dict[str, Any]:
    if not overrides:
        return {"changed": False, "revision": None, "profile_ids": []}
    desired: Dict[str, Dict[str, Any]] = {}
    for profile_id, raw in overrides.items():
        if not isinstance(raw, dict):
            raise AppError(INVALID_INPUT, "policy override 必须是对象", field="policy_overrides")
        interval = validate_interval_hours(raw.get("interval_hours"))
        if interval is None:
            raise AppError(
                INVALID_INPUT,
                "profile override 缺少 interval_hours",
                field="policy_overrides",
            )
        desired[str(profile_id)] = {
            "enabled": bool(raw.get("enabled")),
            "interval_hours": interval,
        }

    for attempt in range(5):
        current = sync_policy.get_policy(workspace_id)
        merged = dict(current.get("profile_overrides") or {})
        changed = False
        for profile_id, value in desired.items():
            if merged.get(profile_id) != value:
                merged[profile_id] = value
                changed = True
        if not changed:
            return {
                "changed": False,
                "revision": current["revision"],
                "profile_ids": sorted(desired),
            }
        try:
            saved = sync_policy.save_policy(
                workspace_id,
                current["revision"],
                {"profile_overrides": merged},
            )
        except AppError as exc:
            if exc.code == REVISION_CONFLICT and attempt < 4:
                continue
            raise
        return {
            "changed": True,
            "revision": saved["revision"],
            "profile_ids": sorted(desired),
        }
    raise AppError(REVISION_CONFLICT, "计划 revision 持续冲突", field="expected_revision")


def _sync_rel(workspace_id: str, name: str) -> str:
    return _rel_from_data(sync_paths.sync_file(workspace_id, name))


def _restore_sync_from_backup(workspace_id: str, backup_dir: str) -> List[str]:
    """把 `sources.json`/`policy.json` 恢复成备份里的样子；备份没有则删除。"""
    manifest = _read_manifest(workspace_id, backup_dir)
    report = verify(workspace_id, backup_dir)
    if not report["ok"]:
        raise AppError(
            INVALID_INPUT,
            "备份校验失败，无法回滚 Sync 写入",
            field="backup_dir",
            problems=report["problems"][:20],
        )
    by_path = {
        row["path"]: row
        for row in manifest["files"]
        if isinstance(row, dict) and isinstance(row.get("path"), str)
    }
    restored: List[str] = []
    for name in ("sources.json", "policy.json"):
        relative = _sync_rel(workspace_id, name)
        destination = _safe_join(storage.DATA_DIR, relative)
        entry = by_path.get(relative)
        if entry is not None:
            source = _safe_join(backup_dir, relative)
            with open(source, "rb") as handle:
                data = handle.read()
            if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
                raise AppError(STORAGE_FAILURE, "回滚源文件已变化", field="path", path=relative)
            fsutil.atomic_write_bytes(destination, data)
            restored.append(relative)
            continue
        try:
            if os.path.isfile(destination) and not os.path.islink(destination):
                os.unlink(destination)
                restored.append(relative)
        except OSError as exc:
            raise AppError(STORAGE_FAILURE, "回滚时无法删除 Sync 文件", field="path", path=relative, error=str(exc))
    return restored


def execute(
    workspace_id: str,
    plan: Dict[str, Any],
    backup_dir: str,
    actor: str = "system",
) -> Dict[str, Any]:
    """按计划创建来源、合并计划并保持旧数据逐字节不变。"""
    wid = validate_workspace_id(workspace_id)
    if not isinstance(plan, dict):
        raise AppError(INVALID_INPUT, "plan 必须是对象", field="plan")
    if plan.get("workspace_id") not in (None, wid):
        raise AppError(INVALID_INPUT, "plan 属于其他工作区", field="workspace_id")
    profile_ids = plan.get("profile_ids")
    if profile_ids is not None and not isinstance(profile_ids, list):
        raise AppError(INVALID_INPUT, "profile_ids 必须是数组", field="profile_ids")

    current = sync_migration.plan_workspace(wid, profile_ids)
    supplied = str(plan.get("plan_id") or "")
    if not supplied or supplied != current["plan_id"]:
        raise AppError(
            REVISION_CONFLICT,
            "迁移计划已过期，请重新生成 plan",
            field="plan_id",
        )

    manifest = backup(wid, backup_dir, actor=actor)
    backup_root = manifest["backup_dir"]
    before = _legacy_hashes(wid)
    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    try:
        for draft in current["sources"]:
            row = _create_one_source(wid, draft, skipped)
            if row is not None:
                created.append(row)
        policy_result = _merge_policy(wid, current["policy_overrides"])
        after = _legacy_hashes(wid)
        if after != before:
            raise AppError(
                STORAGE_FAILURE,
                "旧 config/records/archive 在升级后发生变化",
                field="legacy",
            )
    except BaseException:
        try:
            _restore_sync_from_backup(wid, backup_root)
        except Exception:
            logger.exception("M16-37 升级失败后的 Sync 回滚未完成：%s", wid)
        raise

    return {
        "workspace_id": wid,
        "plan_id": current["plan_id"],
        "backup_dir": backup_root,
        "backup_reused": bool(manifest.get("reused")),
        "sources_created": created,
        "sources_skipped": skipped,
        "warnings": current["warnings"],
        "policy": policy_result,
        "legacy_verified": True,
        "writes": len(created) + (1 if policy_result["changed"] else 0),
    }


# ---------------------------------------------------------------------------
# 回退
# ---------------------------------------------------------------------------


def _removable_sync_rel(workspace_id: str, relative: str) -> bool:
    prefix = "workspaces/%s/sync/" % workspace_id
    if not relative.startswith(prefix):
        return False
    rest = relative[len(prefix):]
    if rest.startswith("runs/"):
        return False
    if "/" not in rest:
        return rest in sync_paths.SYNC_FILES
    return rest.startswith("profiles/")


def _remove_unrecorded_sync_files(workspace_id: str, recorded: set) -> List[str]:
    sync_root = sync_paths.sync_root(workspace_id)
    if not os.path.isdir(sync_root):
        return []
    removed: List[str] = []
    sync_abs = os.path.abspath(sync_root)
    for current, dirs, names in os.walk(sync_root, topdown=False):
        dirs[:] = sorted(
            name
            for name in dirs
            if not _skip_dir(name)
            and not (os.path.abspath(current) == sync_abs and name == "runs")
            and not os.path.islink(os.path.join(current, name))
        )
        for name in sorted(names):
            path = os.path.join(current, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            relative = _rel_from_data(path)
            if relative in recorded or not _removable_sync_rel(workspace_id, relative):
                continue
            if name.lower().endswith(SECRET_SUFFIXES) or _contains_magic(path):
                continue
            os.unlink(path)
            removed.append(relative)
        if os.path.abspath(current) != os.path.abspath(sync_root):
            try:
                os.rmdir(current)
            except OSError:
                pass
    return removed


def rollback(workspace_id: str, backup_dir: str) -> Dict[str, Any]:
    """按 manifest 恢复记录过的路径，并删除新版本创建的已知 Sync 文件。"""
    wid = validate_workspace_id(workspace_id)
    target = os.path.abspath(os.path.expanduser(str(backup_dir or "")))
    manifest = _read_manifest(wid, target)
    report = verify(wid, target)
    if not report["ok"]:
        raise AppError(
            INVALID_INPUT,
            "备份校验失败（缺失/篡改/多余文件），拒绝回退",
            field="backup_dir",
            problems=report["problems"][:20],
        )

    restored: List[str] = []
    recorded = set()
    for item in manifest["files"]:
        relative = _normalize_rel(item.get("path"))
        if not _allowed_workspace_path(wid, relative):
            raise AppError(INVALID_INPUT, "备份路径不属于该工作区", field="path", path=relative)
        source = _safe_join(target, relative)
        destination = _safe_join(storage.DATA_DIR, relative)
        with open(source, "rb") as handle:
            data = handle.read()
        if hashlib.sha256(data).hexdigest() != str(item.get("sha256")).lower():
            raise AppError(STORAGE_FAILURE, "回退源文件已变化", field="path", path=relative)
        fsutil.atomic_write_bytes(destination, data)
        restored.append(relative)
        recorded.add(relative)

    removed = _remove_unrecorded_sync_files(wid, recorded)
    return {
        "workspace_id": wid,
        "backup_dir": target,
        "restored": restored,
        "removed": removed,
        "verified": True,
        "manifest_created_at": manifest.get("created_at"),
    }
