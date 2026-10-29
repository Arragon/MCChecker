"""V3 旧数据 dry-run 清单（INH-2156）。

只读扫描旧 `data/`：profiles、config_management、全局身份/机型、Workspace 文件；
输出 manifest（旧路径/hash → workspace=legacy-default 的 W/P/ref）。默认零写入，
`--out` 只写显式指定的 manifest 文件。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _entry(path: str, root: str, **extra: Any) -> Dict[str, Any]:
    row = {
        "path": _rel(path, root),
        "sha256": _sha256(path),
        "size": os.path.getsize(path),
    }
    row.update(extra)
    return row


def scan(data_dir: str) -> Dict[str, Any]:
    root = os.path.abspath(data_dir)
    entries: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    profiles_root = os.path.join(root, "profiles")
    by_name: Dict[str, set] = defaultdict(set)

    def add(path: str, **extra: Any) -> None:
        try:
            entries.append(_entry(path, root, **extra))
        except OSError as exc:
            errors.append({"path": _rel(path, root), "error": str(exc)})

    if os.path.isdir(profiles_root):
        for pid in sorted(os.listdir(profiles_root)):
            base = os.path.join(profiles_root, pid)
            if not os.path.isdir(base):
                continue
            files = os.path.join(base, "files")
            if os.path.isdir(files):
                for name in sorted(os.listdir(files)):
                    path = os.path.join(files, name)
                    if os.path.isfile(path):
                        add(path, kind="file", workspace_id="legacy-default",
                            profile_id=pid, name=name, ref="current")
                        by_name[name].add(pid)
            for section in ("archive", "records"):
                directory = os.path.join(base, section)
                if not os.path.isdir(directory):
                    continue
                for name in sorted(os.listdir(directory)):
                    folder = os.path.join(directory, name)
                    if not os.path.isdir(folder):
                        continue
                    for filename in sorted(os.listdir(folder)):
                        path = os.path.join(folder, filename)
                        if os.path.isfile(path) and not filename.endswith(".diff.json"):
                            add(path, kind=section[:-1] if section == "records" else "archive",
                                workspace_id="legacy-default", profile_id=pid,
                                name=name, ref="%s:%s" % (section[:-1], filename))
    workspaces_root = os.path.join(root, "workspaces")
    if os.path.isdir(workspaces_root):
        for wid in sorted(os.listdir(workspaces_root)):
            wid_dir = os.path.join(workspaces_root, wid)
            if not os.path.isdir(wid_dir):
                continue
            for pid in sorted(os.listdir(os.path.join(wid_dir, "profiles"))):
                files = os.path.join(wid_dir, "profiles", pid, "files")
                if not os.path.isdir(files):
                    continue
                for name in sorted(os.listdir(files)):
                    path = os.path.join(files, name)
                    if os.path.isfile(path):
                        add(path, kind="file", workspace_id=wid, profile_id=pid,
                            name=name, ref="current")
    managed = os.path.join(root, "config_management")
    if os.path.isdir(managed):
        for current, _dirs, files in os.walk(managed):
            for name in sorted(files):
                path = os.path.join(current, name)
                add(path, kind="managed_readonly", workspace_id="legacy-default",
                    profile_id="", name=name, ref="managed")
    for global_name in ("device_models.json", "workspace_catalog.json",
                        "ip_mapping.json", "admin_users.json"):
        path = os.path.join(root, global_name)
        if os.path.isfile(path):
            add(path, kind="global_identity", workspace_id="", profile_id="",
                name=global_name, ref="global")
    duplicate_sha: Dict[str, List[str]] = defaultdict(list)
    for row in entries:
        duplicate_sha[row["sha256"]].append(row["path"])
    duplicates = [
        {"sha256": sha, "paths": paths}
        for sha, paths in duplicate_sha.items() if len(paths) > 1
    ]
    conflicts = [
        {"name": name, "profiles": sorted(profiles)}
        for name, profiles in by_name.items() if len(profiles) > 1
    ]
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "data_dir": root,
        "workspace": "legacy-default",
        "total": len(entries),
        "entries": sorted(entries, key=lambda row: row["path"]),
        "duplicates": duplicates,
        "cross_profile_same_name": conflicts,
        "errors": errors,
        "dry_run": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=os.path.join(REPO_ROOT, "data"))
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    manifest = scan(args.data_dir)
    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    print("scanned %d files, duplicates=%d, conflicts=%d, errors=%d"
          % (manifest["total"], len(manifest["duplicates"]),
             len(manifest["cross_profile_same_name"]), len(manifest["errors"])),
          file=sys.stderr)
    return 0 if not manifest["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
