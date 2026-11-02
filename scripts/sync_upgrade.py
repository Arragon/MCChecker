#!/usr/bin/env python3
"""M16-37 旧来源/机型计划升级、备份、校验与回退 CLI。

子命令：`plan | backup | execute | verify | rollback`。全部输出 JSON；
失败时退出码非零，错误写到 stderr。备份与执行都不打印任何 secret 内容。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core.errors import AppError  # noqa: E402
from app.services import sync_upgrade  # noqa: E402


def _print(payload, stream=None) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        file=stream or sys.stdout,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sync_upgrade.py")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, help_text, *, backup_required):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--workspace", required=True)
        item.add_argument("--profile", action="append", default=None)
        item.add_argument("--backup-dir", required=backup_required)
        item.add_argument("--actor", default="system")
        return item

    add("plan", "只读迁移计划", backup_required=False)
    add("backup", "先备份工作区数据", backup_required=True)
    add("execute", "按计划创建来源并合并计划", backup_required=True)
    add("verify", "校验备份完整性", backup_required=True)
    add("rollback", "按备份恢复并移除新建 Sync 文件", backup_required=True)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            _print(sync_upgrade.plan(args.workspace, args.profile))
            return 0
        if args.command == "backup":
            result = sync_upgrade.backup(args.workspace, args.backup_dir, actor=args.actor)
            _print(
                {
                    "workspace_id": result.get("workspace_id"),
                    "backup_dir": result.get("backup_dir"),
                    "reused": bool(result.get("reused")),
                    "file_count": result.get("file_count"),
                    "total_bytes": result.get("total_bytes"),
                    "skipped_secrets": len(result.get("skipped_secrets") or []),
                    "created_at": result.get("created_at"),
                }
            )
            return 0
        if args.command == "execute":
            plan_doc = sync_upgrade.plan(args.workspace, args.profile)
            result = sync_upgrade.execute(
                args.workspace, plan_doc, args.backup_dir, actor=args.actor
            )
            _print(result)
            return 0
        if args.command == "verify":
            result = sync_upgrade.verify(args.workspace, args.backup_dir)
            _print(result)
            return 0 if result.get("ok") else 1
        if args.command == "rollback":
            _print(sync_upgrade.rollback(args.workspace, args.backup_dir))
            return 0
    except AppError as exc:
        _print({"ok": False, "error": exc.to_payload()}, stream=sys.stderr)
        return 1
    except Exception as exc:  # CLI 边界：把不可预期失败也收敛成 JSON + 非零退出码
        _print(
            {
                "ok": False,
                "error": {
                    "code": "STORAGE_FAILURE",
                    "message": str(exc) or exc.__class__.__name__,
                    "detail": {},
                },
            },
            stream=sys.stderr,
        )
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
