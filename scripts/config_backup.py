"""配置管理域的最小备份 / 校验 / 恢复（M8-02a）。

**范围**：只覆盖 `data/config_management/`（`state.json` + 已提交对象 + blob +
审计 + 回执）。它是 M6 在受控现网试用前的安全网——先能回到"最近一次完整提交"，
再谈全量演练（M8-02b 才覆盖 `profiles/`、人员表、机型表、恢复区）。

三条设计约束：

1. **安全点**：备份在 `write_gate.maintenance()` 内抓取，抓到的 state 与对象必然自洽；
2. **恢复顺序与提交顺序一致**：对象/blob/审计先就位，`state.json` **最后**写，
   因此中途失败时旧 state 仍然指向旧对象，不会出现半恢复；
3. **不自动覆盖**：目标已存在且非空时默认拒绝，必须显式 `--force`。

用法：

    python scripts/config_backup.py backup  --data-dir data --out backups/cfg-20261022
    python scripts/config_backup.py verify  --backup backups/cfg-20261022
    python scripts/config_backup.py restore --backup backups/cfg-20261022 --data-dir data --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = 1
#: 备份的子目录（按恢复顺序排列：状态文件放最后）。
SUBDIRS = ("objects", "blobs", "audit", "receipts", "cache")
STATE_NAME = "state.json"
DOMAIN_DIR = "config_management"


class BackupError(RuntimeError):
    """备份/校验/恢复失败。CLI 把它转成非零退出码。"""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def domain_root(data_dir: str) -> str:
    return os.path.join(os.path.abspath(data_dir), DOMAIN_DIR)


def _collect(root: str) -> List[Tuple[str, str, int]]:
    """列出 `(相对路径, sha256, 字节数)`，跳过 `manifest.json` 自身。"""
    items: List[Tuple[str, str, int]] = []
    if not os.path.isdir(root):
        return items
    for current, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name == MANIFEST_NAME:
                continue
            absolute = os.path.join(current, name)
            relative = os.path.relpath(absolute, root).replace(os.sep, "/")
            items.append((relative, _sha256_file(absolute), os.path.getsize(absolute)))
    items.sort()
    return items


def backup(data_dir: str, out_dir: str, *, overwrite: bool = False) -> Dict[str, Any]:
    """把配置域复制到 `out_dir` 并写 manifest。返回 manifest 内容。"""
    source = domain_root(data_dir)
    if not os.path.isdir(source):
        raise BackupError("配置域目录不存在：%s（尚未初始化）" % source)
    state_path = os.path.join(source, STATE_NAME)
    if not os.path.exists(state_path):
        raise BackupError("缺少 state.json，拒绝备份：%s" % state_path)
    if os.path.isdir(out_dir) and os.listdir(out_dir) and not overwrite:
        raise BackupError("目标目录非空：%s（加 --overwrite 覆盖）" % out_dir)

    from app.core.locks import write_gate  # 延迟导入，CLI 也可独立运行

    os.makedirs(out_dir, exist_ok=True)
    with write_gate.maintenance():
        # 安全点：此刻没有进行中的写入，state 与对象自洽
        files = _collect(source)
        for relative, _digest, _size in files:
            target = os.path.join(out_dir, relative)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(os.path.join(source, relative), target)
        state = json.load(open(state_path, "r", encoding="utf-8"))
        manifest = {
            "schema": SCHEMA_VERSION,
            "created_at": _now(),
            "domain": DOMAIN_DIR,
            "store_seq": int(state.get("seq") or 0),
            "id_seq": int(state.get("id_seq") or 0),
            "files": [
                {"path": relative, "sha256": digest, "size": size}
                for relative, digest, size in files
            ],
            "file_count": len(files),
            "total_bytes": sum(size for _rel, _digest, size in files),
        }
        with open(os.path.join(out_dir, MANIFEST_NAME), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return manifest


def load_manifest(backup_dir: str) -> Dict[str, Any]:
    path = os.path.join(backup_dir, MANIFEST_NAME)
    if not os.path.exists(path):
        raise BackupError("备份目录缺少 manifest.json：%s" % backup_dir)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except ValueError as exc:
        raise BackupError("manifest 不是合法 JSON：%s" % exc)
    if manifest.get("schema") != SCHEMA_VERSION:
        raise BackupError("manifest schema 不匹配：%r" % manifest.get("schema"))
    return manifest


def verify(backup_dir: str, *, data_dir: Optional[str] = None) -> Dict[str, Any]:
    """校验备份完整性；可选同时报告"与当前数据域相比缺了什么"。"""
    manifest = load_manifest(backup_dir)
    problems: List[Dict[str, str]] = []
    for item in manifest.get("files") or []:
        path = os.path.join(backup_dir, item["path"])
        if not os.path.exists(path):
            problems.append({"path": item["path"], "issue": "文件缺失"})
            continue
        actual = _sha256_file(path)
        if actual != item["sha256"]:
            problems.append(
                {"path": item["path"], "issue": "哈希不符", "expected": item["sha256"], "actual": actual}
            )
        if os.path.getsize(path) != item.get("size"):
            problems.append({"path": item["path"], "issue": "大小不符"})
    extra = sorted(set(_collect(backup_dir)) - {(i["path"], i["sha256"], i["size"]) for i in manifest.get("files") or []})
    result: Dict[str, Any] = {
        "ok": not problems,
        "problems": problems,
        "manifest": manifest,
        "extra_files": [item[0] for item in extra],
    }
    if data_dir:
        current = domain_root(data_dir)
        current_files = {item[0] for item in _collect(current)} if os.path.isdir(current) else set()
        backed = {item["path"] for item in manifest.get("files") or []}
        result["missing_in_backup"] = sorted(current_files - backed)
    return result


def restore(
    backup_dir: str,
    data_dir: str,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """把备份恢复到 `data_dir/config_management`；`state.json` 最后写。"""
    report = verify(backup_dir)
    if not report["ok"]:
        raise BackupError("备份校验未通过，拒绝恢复：%s" % report["problems"][:3])

    target = domain_root(data_dir)
    if os.path.isdir(target) and os.listdir(target) and not force:
        raise BackupError("目标已存在数据：%s（加 --force 覆盖）" % target)

    manifest = report["manifest"]
    plan = {
        "target": target,
        "files": len(manifest.get("files") or []),
        "store_seq": manifest.get("store_seq"),
        "dry_run": dry_run,
    }
    if dry_run:
        return plan

    parent = os.path.dirname(target) or "."
    os.makedirs(parent, exist_ok=True)
    staging = tempfile.mkdtemp(prefix="mc-cfg-restore-", dir=parent)
    try:
        for item in manifest.get("files") or []:
            if item["path"] == STATE_NAME:
                continue
            source = os.path.join(backup_dir, item["path"])
            destination = os.path.join(staging, item["path"])
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copy2(source, destination)
        state_source = os.path.join(backup_dir, STATE_NAME)
        if not os.path.exists(state_source):
            raise BackupError("备份里没有 state.json")
        # 先并入对象/blob，再单独最后落 state（与提交协议同序）
        for sub in SUBDIRS:
            source_dir = os.path.join(staging, sub)
            if os.path.isdir(source_dir):
                shutil.copytree(source_dir, os.path.join(target, sub), dirs_exist_ok=True)
        shutil.copy2(state_source, os.path.join(staging, STATE_NAME + ".pending"))
        os.makedirs(target, exist_ok=True)
        shutil.copy2(os.path.join(staging, STATE_NAME + ".pending"), os.path.join(target, STATE_NAME))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    from app.core.config_repository import ConfigRepository

    repo = ConfigRepository(data_dir)
    check = repo.verify()
    plan["verify"] = {"ok": check["ok"], "objects": check["objects"], "issues": check["issues"]}
    if not check["ok"]:
        raise BackupError("恢复后一致性检查失败：%s" % check["issues"][:3])
    return plan


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="配置管理域最小备份/校验/恢复（M8-02a）")
    sub = parser.add_subparsers(dest="command", required=True)

    backup_cmd = sub.add_parser("backup", help="备份 data/config_management")
    backup_cmd.add_argument("--data-dir", default=os.path.join(REPO_ROOT, "data"))
    backup_cmd.add_argument("--out", required=True)
    backup_cmd.add_argument("--overwrite", action="store_true")

    verify_cmd = sub.add_parser("verify", help="校验备份完整性")
    verify_cmd.add_argument("--backup", required=True)
    verify_cmd.add_argument("--data-dir", default=None)

    restore_cmd = sub.add_parser("restore", help="从备份恢复")
    restore_cmd.add_argument("--backup", required=True)
    restore_cmd.add_argument("--data-dir", default=os.path.join(REPO_ROOT, "data"))
    restore_cmd.add_argument("--dry-run", action="store_true")
    restore_cmd.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            manifest = backup(args.data_dir, args.out, overwrite=args.overwrite)
            print(
                "已备份 %d 个文件（%d 字节），store_seq=%s → %s"
                % (manifest["file_count"], manifest["total_bytes"], manifest["store_seq"], args.out)
            )
            return 0
        if args.command == "verify":
            result = verify(args.backup, data_dir=args.data_dir)
            print(json.dumps({k: v for k, v in result.items() if k != "manifest"}, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        plan = restore(
            args.backup, args.data_dir, dry_run=args.dry_run, force=args.force
        )
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    except BackupError as exc:
        print("失败：%s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    sys.exit(main())
