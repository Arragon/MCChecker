"""V3 全域备份 / 校验 / 恢复（INH-2156）。

备份整个数据根（全局身份、每个 workspace、config_management、恢复区）；
恢复默认 dry-run，只比对；`--force` 才覆盖。manifest 记录逐文件 sha。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = "backup_manifest.json"


def _sha(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect(root: str) -> List[Dict[str, Any]]:
    rows = []
    for current, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name == MANIFEST:
                continue
            path = os.path.join(current, name)
            rows.append({"path": os.path.relpath(path, root).replace(os.sep, "/"),
                         "sha256": _sha(path), "size": os.path.getsize(path)})
    rows.sort(key=lambda row: row["path"])
    return rows


def backup(data_dir: str, out: str) -> Dict[str, Any]:
    source = os.path.abspath(data_dir)
    target = os.path.abspath(out)
    if os.path.exists(target) and os.listdir(target):
        raise SystemExit("目标已存在且非空：%s" % target)
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("backups", "*.log"))
    manifest = {"created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "source": source, "files": _collect(target)}
    with open(os.path.join(target, MANIFEST), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    return manifest


def verify(backup_dir: str) -> Dict[str, Any]:
    with open(os.path.join(backup_dir, MANIFEST), "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    bad = []
    for row in manifest["files"]:
        path = os.path.join(backup_dir, row["path"])
        if not os.path.isfile(path) or _sha(path) != row["sha256"]:
            bad.append(row["path"])
    return {"ok": not bad, "bad": bad, "files": len(manifest["files"])}


def restore(backup_dir: str, data_dir: str, dry_run: bool = True) -> Dict[str, Any]:
    with open(os.path.join(backup_dir, MANIFEST), "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    target = os.path.abspath(data_dir)
    diff = []
    for row in manifest["files"]:
        path = os.path.join(target, row["path"])
        current = _sha(path) if os.path.isfile(path) else None
        if current != row["sha256"]:
            diff.append({"path": row["path"], "current": current, "backup": row["sha256"]})
    if not dry_run:
        for row in manifest["files"]:
            source = os.path.join(backup_dir, row["path"])
            destination = os.path.join(target, row["path"])
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copy2(source, destination)
    return {"dry_run": dry_run, "files": len(manifest["files"]), "diff": diff}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("backup", "verify", "restore"))
    parser.add_argument("--data-dir", default=os.path.join(REPO_ROOT, "data"))
    parser.add_argument("--out", default="")
    parser.add_argument("--backup", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "backup":
        result = backup(args.data_dir, args.out)
        print(json.dumps({"files": len(result["files"]), "out": args.out}, ensure_ascii=False))
        return 0
    if args.command == "verify":
        result = verify(args.backup)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    result = restore(args.backup, args.data_dir, dry_run=not args.force)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
