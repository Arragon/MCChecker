"""旧 data/ 配置管理预检、初始化与幂等迁移（M8-01 / INH-1972）。

只做三件可审计的事：

1. `inspect`：只读盘点 layout、机型、全局表、恢复区与管理域状态；
2. `initialize`：先做安全副本，再建立空的 `config_management` 域（幂等）；
3. `migrate`：按**显式 plan** 把文件登记成不可变快照；缺版本/逻辑文件的一律
   写入 `imports/pending_links.json`，不猜"最新版本"。

用法：

    python scripts/inspect_config_management.py inspect --data-dir data
    python scripts/inspect_config_management.py initialize --data-dir data --backup-dir /tmp/bak --yes
    python scripts/inspect_config_management.py migrate --data-dir data --plan plan.json --apply
    python scripts/inspect_config_management.py verify --data-dir data
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

MIGRATION_DIR = "imports"
INIT_RECEIPT_NAME = "initialize-receipt.json"
RECEIPT_NAME = "migration-receipt.json"
PENDING_NAME = "pending_links.json"
PLAN_NAME = "plan.json"
MANIFEST_NAME = "manifest.json"
_IGNORE = {"cache", "temporary", ".mcchecker.lock"}


class MigrationError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _adopt_data_root(root: str) -> None:
    """把 storage 模块里按旧数据根固化的路径常量改到目标根（只改进程内常量）。"""
    from app.core import storage

    old = storage.DATA_DIR
    new = os.path.abspath(root)
    if old == new:
        return
    for name, value in list(vars(storage).items()):
        if not isinstance(value, str):
            continue
        if value == old:
            setattr(storage, name, new)
        elif value.startswith(old + os.sep):
            setattr(storage, name, new + value[len(old):])
    storage.DATA_DIR = new


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _classify(data_dir: str) -> str:
    root = os.path.abspath(data_dir)
    v1 = os.path.isdir(os.path.join(root, "configs")) or os.path.isdir(os.path.join(root, "archive"))
    profiles = os.path.join(root, "profiles")
    v2 = os.path.isdir(profiles) and any(
        os.path.isdir(os.path.join(profiles, name)) for name in os.listdir(profiles)
    )
    if v1 and v2:
        return "mixed"
    if v1:
        return "v1"
    if v2:
        return "v2"
    return "empty"


def _inventory(data_dir: str) -> Dict[str, Any]:
    root = os.path.abspath(data_dir)
    files = []
    dirs = []
    if os.path.isdir(root):
        for current, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(name for name in dirnames if name not in _IGNORE)
            relative_dir = os.path.relpath(current, root).replace(os.sep, "/")
            if relative_dir != ".":
                dirs.append(relative_dir)
            for name in sorted(filenames):
                if name in _IGNORE:
                    continue
                absolute = os.path.join(current, name)
                relative = os.path.relpath(absolute, root).replace(os.sep, "/")
                files.append(
                    {"path": relative, "sha256": _sha256_file(absolute),
                     "size": os.path.getsize(absolute)}
                )
    files.sort(key=lambda item: item["path"])
    return {"data_dir": root, "files": files, "directories": sorted(dirs),
            "file_count": len(files), "directory_count": len(dirs),
            "layout": _classify(root)}


def _read_state(data_dir: str) -> Dict[str, Any]:
    path = os.path.join(os.path.abspath(data_dir), "config_management", "state.json")
    if not os.path.exists(path):
        return {"initialized": False, "seq": 0, "objects": 0}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return {"initialized": False, "corrupt": True, "seq": 0, "objects": 0}
    heads = state.get("heads") if isinstance(state, dict) else {}
    count = sum(len(items or {}) for items in (heads or {}).values())
    return {
        "initialized": True,
        "seq": int(state.get("seq") or 0),
        "id_seq": int(state.get("id_seq") or 0),
        "objects": count,
    }


def inspect(data_dir: str) -> Dict[str, Any]:
    """只读盘点；不创建目录、不初始化管理域。"""
    root = os.path.abspath(data_dir)
    inventory = _inventory(root)
    profiles = []
    profiles_dir = os.path.join(root, "profiles")
    if os.path.isdir(profiles_dir):
        for name in sorted(os.listdir(profiles_dir)):
            profile_dir = os.path.join(profiles_dir, name)
            if not os.path.isdir(profile_dir):
                continue
            profiles.append(
                {
                    "id": name,
                    "configs": _count_files(os.path.join(profile_dir, "configs")),
                    "archive": _count_files(os.path.join(profile_dir, "archive")),
                    "records": _count_files(os.path.join(profile_dir, "records")),
                }
            )
    global_files = {}
    for name in ("ip_mapping.json", "admin_users.json", "device_models.json"):
        path = os.path.join(root, name)
        global_files[name] = _sha256_file(path) if os.path.exists(path) else None
    state = _read_state(root)
    blockers = []
    if state.get("corrupt"):
        blockers.append("config_management/state.json 损坏：必须从备份恢复，不能归零")
    if inventory["layout"] == "mixed":
        blockers.append("检测到 v1/v2 混合布局：先确认哪个目录是权威来源")
    return {
        "generated_at": _now(),
        "inventory": inventory,
        "profiles": profiles,
        "global_files": global_files,
        "config_management": state,
        "pending_review": _count_files(os.path.join(root, "review")),
        "recovery": _count_files(os.path.join(root, "recovery")),
        "blockers": blockers,
    }


def _count_files(path: str) -> int:
    count = 0
    for _current, _dirs, files in os.walk(path):
        count += len(files)
    return count


def _safety_backup(data_dir: str, backup_dir: str, *, overwrite: bool = False) -> Dict[str, Any]:
    source = os.path.abspath(data_dir)
    target = os.path.abspath(backup_dir)
    if source == target or target.startswith(source + os.sep):
        raise MigrationError("备份目录不能放在数据目录内部：%s" % target)
    if os.path.isdir(target) and os.listdir(target) and not overwrite:
        raise MigrationError("备份目录非空：%s（加 --overwrite 覆盖）" % target)
    if os.path.isdir(target):
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(*_IGNORE))
    inventory = _inventory(target)
    manifest = {
        "schema": 1,
        "created_at": _now(),
        "source": source,
        "layout": inventory["layout"],
        "file_count": inventory["file_count"],
        "files": inventory["files"],
    }
    with open(os.path.join(target, MANIFEST_NAME), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return manifest


def initialize(data_dir: str, backup_dir: str, *, dry_run: bool = False) -> Dict[str, Any]:
    root = os.path.abspath(data_dir)
    state = _read_state(root)
    if state.get("corrupt"):
        raise MigrationError("config_management/state.json 已损坏，拒绝初始化；先从备份恢复")
    if state.get("initialized"):
        return {"status": "already_initialized", "config_management": state}
    plan = {"status": "dry_run" if dry_run else "initialized", "inspect": inspect(root)}
    if dry_run:
        return plan
    manifest = _safety_backup(root, backup_dir)
    from app.core import storage
    from app.core.config_repository import ConfigRepository
    from app.core import fsutil

    _adopt_data_root(root)
    repo = ConfigRepository(root)
    repo.initialize()
    check = repo.verify()
    if not check["ok"]:
        raise MigrationError("初始化后一致性检查失败：%s" % check["issues"][:3])
    imports_dir = os.path.join(repo.root, MIGRATION_DIR)
    os.makedirs(imports_dir, exist_ok=True)
    receipt = {
        "schema": 1,
        "created_at": _now(),
        "backup": os.path.abspath(backup_dir),
        "backup_file_count": manifest["file_count"],
        "inventory_before": plan["inspect"]["inventory"],
        "inventory_after": _inventory(root),
        "verify": check,
    }
    fsutil.atomic_write_json(os.path.join(imports_dir, INIT_RECEIPT_NAME), receipt)
    return {"status": "initialized", "receipt": receipt}


def _source_path(root: str, item: Dict[str, Any]) -> Optional[str]:
    raw = str(item.get("source_path") or "").strip()
    if raw:
        absolute = os.path.abspath(os.path.join(root, raw))
        if not (absolute == root or absolute.startswith(root + os.sep)):
            raise MigrationError("plan 的 source_path 越界：%s" % raw)
        return absolute if os.path.isfile(absolute) else None
    pid = str(item.get("profile_id") or "").strip()
    filename = str(item.get("filename") or "").strip()
    if not filename:
        return None
    candidates = [
        os.path.join(root, "profiles", pid, "configs", filename) if pid else "",
        os.path.join(root, "configs", filename),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def migrate(
    data_dir: str,
    plan_path: str,
    *,
    apply: bool = False,
    force_plan: bool = False,
) -> Dict[str, Any]:
    root = os.path.abspath(data_dir)
    with open(plan_path, "r", encoding="utf-8") as fh:
        plan = json.load(fh)
    if not isinstance(plan, dict) or not isinstance(plan.get("items"), list):
        raise MigrationError("plan 必须是 {items:[...]} 结构")
    state = _read_state(root)
    if not state.get("initialized"):
        raise MigrationError("配置管理域尚未初始化；先跑 initialize")
    from app.core import storage
    from app.core.config_repository import ConfigRepository
    from app.services import config_catalog

    _adopt_data_root(root)
    repo = ConfigRepository(root)
    imports_dir = os.path.join(repo.root, MIGRATION_DIR)
    receipt_path = os.path.join(imports_dir, RECEIPT_NAME)
    if os.path.exists(receipt_path) and not force_plan:
        raise MigrationError("已有迁移收据；换 plan 需显式 --force-plan")

    actor = {"ip": "127.0.0.1", "name": "m8-01-migration"}
    results = []
    pending = []
    for item in plan["items"]:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "").strip()
        source = _source_path(root, item)
        decision = str(item.get("decision") or "pending")
        row = {"filename": filename, "profile_id": item.get("profile_id"),
               "decision": decision, "status": "pending"}
        if source is None:
            row["reason"] = "找不到源文件"
            pending.append(dict(row, **item))
            results.append(row)
            continue
        data = open(source, "rb").read()
        digest = hashlib.sha256(data).hexdigest()
        expected = str(item.get("sha256") or "").strip()
        if expected and expected != digest:
            row["reason"] = "源文件哈希与 plan 不一致"
            pending.append(dict(row, **item))
            results.append(row)
            continue
        release_id = str(item.get("release_id") or "").strip()
        logical_file_id = str(item.get("logical_file_id") or "").strip()
        if decision != "link" or not release_id or not logical_file_id:
            row["reason"] = "缺 release/logical_file 或未显式 link"
            pending.append(dict(row, **item, sha256=digest))
            results.append(row)
            continue
        if not apply:
            row.update({"status": "would_link", "sha256": digest})
            results.append(row)
            continue
        try:
            snapshot = config_catalog.register_snapshot(
                repo,
                actor,
                release_id=release_id,
                logical_file_id=logical_file_id,
                data=data,
                original_filename=filename or os.path.basename(source),
                source=config_catalog.SOURCE_ARCHIVE,
                archive_ref=item.get("archive_ref"),
            )
            row.update({"status": "linked", "sha256": digest, "snapshot_id": snapshot["id"]})
        except Exception as exc:  # 单条失败不假装整批成功
            row.update({"status": "pending", "reason": str(exc), "sha256": digest})
            pending.append(dict(row, **item))
        results.append(row)

    if not apply:
        return {"status": "dry_run", "results": results, "pending": pending}
    from app.core import fsutil

    os.makedirs(imports_dir, exist_ok=True)
    fsutil.atomic_write_json(os.path.join(imports_dir, PENDING_NAME), pending)
    fsutil.atomic_write_json(
        os.path.join(imports_dir, PLAN_NAME), plan
    )
    receipt = {
        "schema": 1,
        "created_at": _now(),
        "plan_sha256": _sha256_file(plan_path),
        "results": results,
        "pending_count": len(pending),
        "inventory": _inventory(root),
    }
    fsutil.atomic_write_json(receipt_path, receipt)
    return {"status": "applied", "results": results, "pending": pending, "receipt": receipt}


def verify(data_dir: str) -> Dict[str, Any]:
    root = os.path.abspath(data_dir)
    from app.core.config_repository import ConfigRepository

    repo = ConfigRepository(root)
    check = repo.verify()
    receipt_path = os.path.join(repo.root, MIGRATION_DIR, RECEIPT_NAME)
    receipt = None
    if os.path.exists(receipt_path):
        with open(receipt_path, "r", encoding="utf-8") as fh:
            receipt = json.load(fh)
    return {"ok": bool(check["ok"]), "verify": check, "receipt": receipt}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="旧 data/ 配置管理预检、初始化与迁移（M8-01）")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect_cmd = sub.add_parser("inspect")
    inspect_cmd.add_argument("--data-dir", default=os.path.join(REPO_ROOT, "data"))
    init_cmd = sub.add_parser("initialize")
    init_cmd.add_argument("--data-dir", default=os.path.join(REPO_ROOT, "data"))
    init_cmd.add_argument("--backup-dir", required=True)
    init_cmd.add_argument("--dry-run", action="store_true")
    init_cmd.add_argument("--yes", action="store_true")
    migrate_cmd = sub.add_parser("migrate")
    migrate_cmd.add_argument("--data-dir", default=os.path.join(REPO_ROOT, "data"))
    migrate_cmd.add_argument("--plan", required=True)
    migrate_cmd.add_argument("--apply", action="store_true")
    migrate_cmd.add_argument("--force-plan", action="store_true")
    verify_cmd = sub.add_parser("verify")
    verify_cmd.add_argument("--data-dir", default=os.path.join(REPO_ROOT, "data"))
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect(args.data_dir)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result["blockers"] else 0
        if args.command == "initialize":
            if not args.dry_run and not args.yes:
                raise MigrationError("initialize 会写 data/config_management，必须显式 --yes")
            result = initialize(args.data_dir, args.backup_dir, dry_run=args.dry_run)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "migrate":
            result = migrate(
                args.data_dir, args.plan, apply=args.apply, force_plan=args.force_plan
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        result = verify(args.data_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    except MigrationError as exc:
        print("失败：%s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
