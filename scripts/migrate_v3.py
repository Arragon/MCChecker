"""V3 旧布局迁移执行 / 回退（INH-2157）。

`plan` 只读；`execute` 必须提供已校验备份，先在 staging 复制并校验，再原子
公布到 `workspaces/legacy-default`；`rollback` 只移除该 workspace，不扁平化
其他 workspace，也不删除旧 `profiles/`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import backup_v3  # noqa: E402

LEGACY_WID = "legacy-default"
CATALOG = "workspace_catalog.json"


def _sha(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree(root: str) -> Dict[str, str]:
    out = {}
    if not os.path.isdir(root):
        return out
    for current, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(current, name)
            out[os.path.relpath(path, root).replace(os.sep, "/")] = _sha(path)
    return out


def _catalog_path(data_dir: str) -> str:
    return os.path.join(data_dir, CATALOG)


def _load_catalog(data_dir: str) -> Dict[str, Any]:
    path = _catalog_path(data_dir)
    if not os.path.isfile(path):
        return {"schema": 1, "workspaces": []}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_catalog(data_dir: str, doc: Dict[str, Any]) -> None:
    path = _catalog_path(data_dir)
    temp = path + ".tmp-" + uuid.uuid4().hex[:8]
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=2)
    os.replace(temp, path)


def _profiles(data_dir: str) -> List[str]:
    root = os.path.join(data_dir, "profiles")
    return sorted(name for name in os.listdir(root) if os.path.isdir(os.path.join(root, name))) if os.path.isdir(root) else []


def plan(data_dir: str) -> Dict[str, Any]:
    profiles = _profiles(data_dir)
    target = os.path.join(data_dir, "workspaces", LEGACY_WID, "profiles")
    existing = _tree(target)
    source = {}
    for pid in profiles:
        for relative, sha in _tree(os.path.join(data_dir, "profiles", pid)).items():
            source["%s/%s" % (pid, relative)] = sha
    missing = [key for key in source if key not in existing]
    conflicts = [key for key in source if key in existing and existing[key] != source[key]]
    catalog = _load_catalog(data_dir)
    has_workspace = any(item.get("id") == LEGACY_WID for item in catalog.get("workspaces", []))
    return {
        "dry_run": True, "data_dir": os.path.abspath(data_dir), "profiles": profiles,
        "source_files": len(source), "existing_files": len(existing),
        "missing": missing, "conflicts": conflicts, "workspace_exists": has_workspace,
        "can_execute": not conflicts,
    }


def execute(data_dir: str, backup_dir: str) -> Dict[str, Any]:
    verified = backup_v3.verify(backup_dir)
    if not verified["ok"]:
        raise SystemExit("备份校验失败，拒绝迁移：%s" % verified["bad"][:5])
    report = plan(data_dir)
    if report["conflicts"]:
        raise SystemExit("目标已有不同内容的文件，拒绝覆盖：%s" % report["conflicts"][:5])
    lock_path = os.path.join(data_dir, ".migration.lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        raise SystemExit("已有迁移进程持有锁：%s" % lock_path)
    staging = os.path.join(data_dir, ".migration-staging-" + uuid.uuid4().hex[:8])
    published = 0
    try:
        os.makedirs(os.path.join(staging, "profiles"), exist_ok=True)
        for pid in report["profiles"]:
            source = os.path.join(data_dir, "profiles", pid)
            target = os.path.join(staging, "profiles", pid)
            shutil.copytree(source, target, dirs_exist_ok=True)
        staged = _tree(os.path.join(staging, "profiles"))
        expected = {}
        for pid in report["profiles"]:
            for relative, sha in _tree(os.path.join(data_dir, "profiles", pid)).items():
                expected["%s/%s" % (pid, relative)] = sha
        if staged != expected:
            raise SystemExit("staging 校验失败，未公布")
        target_root = os.path.join(data_dir, "workspaces", LEGACY_WID, "profiles")
        if not os.path.exists(target_root):
            os.makedirs(os.path.dirname(target_root), exist_ok=True)
            os.replace(os.path.join(staging, "profiles"), target_root)
            published = len(staged)
        else:
            for relative, sha in staged.items():
                destination = os.path.join(target_root, relative)
                if os.path.isfile(destination) and _sha(destination) == sha:
                    continue
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                os.replace(os.path.join(staging, "profiles", relative), destination)
                published += 1
        doc = _load_catalog(data_dir)
        items = doc.setdefault("workspaces", [])
        entry = next((row for row in items if row.get("id") == LEGACY_WID), None)
        if entry is None:
            items.append({
                "id": LEGACY_WID, "name": "Legacy 默认工作区", "description": "从旧 data/profiles 迁移",
                "default_profile_id": "default",
                "profiles": [{"id": pid, "name": pid} for pid in report["profiles"]],
                "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            })
        _save_catalog(data_dir, doc)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            os.remove(lock_path)
        except OSError:
            pass
    return {"published": published, "target": target_root, "workspace": LEGACY_WID}


def rollback(data_dir: str, confirmed: bool) -> Dict[str, Any]:
    if not confirmed:
        raise SystemExit("回退需要 --yes")
    target = os.path.join(data_dir, "workspaces", LEGACY_WID)
    if os.path.isdir(target):
        shutil.rmtree(target)
    doc = _load_catalog(data_dir)
    items = [row for row in doc.get("workspaces", []) if row.get("id") != LEGACY_WID]
    doc["workspaces"] = items
    _save_catalog(data_dir, doc)
    return {"removed": target, "remaining_workspaces": [row.get("id") for row in items]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("plan", "execute", "rollback"))
    parser.add_argument("--data-dir", default=os.path.join(REPO_ROOT, "data"))
    parser.add_argument("--backup", default="")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "plan":
        result = plan(args.data_dir)
    elif args.command == "execute":
        if not args.backup:
            raise SystemExit("execute 必须提供 --backup（已校验的全域备份）")
        result = execute(args.data_dir, args.backup)
    else:
        result = rollback(args.data_dir, args.yes)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
