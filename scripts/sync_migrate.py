#!/usr/bin/env python3
"""M16-06 旧更新源迁移 dry-run 入口。

当前只支持 `plan`：输出 JSON 计划，不写 data、不建 sync 目录。
执行/备份/回退由 M16-37 的 `sync_upgrade.py` 接。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services import sync_migration  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sync_migrate.py")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="只读迁移计划")
    plan.add_argument("--workspace", required=True)
    plan.add_argument("--profile", action="append", default=None)
    args = parser.parse_args(argv)
    if args.command == "plan":
        result = sync_migration.plan_workspace(args.workspace, args.profile)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
