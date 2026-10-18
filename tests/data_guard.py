"""H3 数据守卫的纯函数：真实 `data/` 树的快照与分类比对（INH-1914）。

这里刻意不含任何 pytest 夹具、全局状态或真实路径——执法接线在
`tests/conftest.py`（session 级 autouse 夹具 + `pytest_sessionstart` 基线快照）。
背景与约束见 `docs/know-how.md`「H3 的唯一可信证据是本守卫的红/绿」一条。

两个口径约定：
- 快照的键是**相对数据根的归一化 posix 路径**（无 `./`、无绝对前缀、无 `data/` 前缀）。
  历史上 `data` / `./data` / `data/` 三种拼法各自算出不同的聚合哈希，抄数字的
  人无从发现漂移；归一化让同一棵树无论根路径怎么写都得到同一份快照。
- `.mcchecker.lock` 是运行时产物、不是数据，不计入快照；空目录的增删也不算
  数据变化（只哈希常规文件内容）。
"""

from __future__ import annotations

import hashlib
import os
from typing import Dict, List, Tuple

#: 快照 = {归一化相对路径: 内容 sha256 十六进制}。
Snapshot = Dict[str, str]

#: 比对结果 = 四类差异，每类的元素形状：
#: created/deleted: 路径；content_changed: (路径, 旧哈希, 新哈希)；renamed: (旧路径, 新路径, 哈希)。
DiffResult = Dict[str, List]

#: 运行时产物，不是数据：不计入快照（与历史 shell 配方 `-not -name '.mcchecker.lock'` 同口径）。
EXCLUDED_FILENAMES = frozenset({".mcchecker.lock"})

_READ_CHUNK = 65536


def snapshot_data_tree(root: str) -> Snapshot:
    """对 `root` 下每个常规文件做 {归一化相对路径: sha256} 快照。

    同一棵树无论 `root` 写成 `data`、`./data` 还是 `data/`，返回值必须一致
    （键已归一化）。目录不进快照；`root` 不存在时返回空快照——会话结束仍会
    比对，凭空出现一棵树同样是变化。
    """
    base = os.path.normpath(root)
    snapshot: Snapshot = {}
    if not os.path.isdir(base):
        return snapshot
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in sorted(filenames):
            if name in EXCLUDED_FILENAMES:
                continue
            path = os.path.join(dirpath, name)
            # 与 BSD find `-type f` 同口径：不跟随符号链接，非常规文件不哈希内容。
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            digest = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(_READ_CHUNK), b""):
                    digest.update(chunk)
            rel = os.path.relpath(path, base).replace(os.sep, "/")
            snapshot[rel] = digest.hexdigest()
    return snapshot


def compare_snapshots(before: Snapshot, after: Snapshot) -> DiffResult:
    """两份快照进，分类差异出；完全一致时四类都是空列表。

    改名判定：同一 sha256 同时出现在「只在 before」与「只在 after」的路径集合里。
    一个哈希对应多对候选时按路径字典序一一配对，配不上的残余仍如实报告为
    新建/删除——宁可多报，不漏报。
    """
    created_paths = sorted(p for p in after if p not in before)
    deleted_paths = sorted(p for p in before if p not in after)
    content_changed = sorted(
        (p, before[p], after[p]) for p in after if p in before and before[p] != after[p]
    )

    deleted_by_sha: Dict[str, List[str]] = {}
    for p in deleted_paths:
        deleted_by_sha.setdefault(before[p], []).append(p)
    created_by_sha: Dict[str, List[str]] = {}
    for p in created_paths:
        created_by_sha.setdefault(after[p], []).append(p)

    renamed: List[Tuple[str, str, str]] = []
    for sha in sorted(set(deleted_by_sha) & set(created_by_sha)):
        olds = deleted_by_sha[sha]
        news = created_by_sha[sha]
        for i in range(min(len(olds), len(news))):
            renamed.append((olds[i], news[i], sha))
    paired_old = {old for old, _new, _sha in renamed}
    paired_new = {new for _old, new, _sha in renamed}
    return {
        "created": [p for p in created_paths if p not in paired_new],
        "deleted": [p for p in deleted_paths if p not in paired_old],
        "content_changed": content_changed,
        "renamed": renamed,
    }


def has_differences(diffs: DiffResult) -> bool:
    return any(diffs[category] for category in ("created", "deleted", "content_changed", "renamed"))


def format_diff_report(diffs: DiffResult) -> str:
    """把分类差异排成失败消息——必须逐文件点名，不许只报一句「哈希不匹配」。"""
    lines = [
        "H3 数据守卫：仓库真实的 data/ 树在本次 pytest 会话期间发生了变化。"
        "测试一律不许碰真实数据根（对照实验：临时让某个用例往 data/ 写一个 "
        "zz_h3_selftest.tmp 且不删，本消息必红）。"
    ]
    if diffs["created"]:
        lines.append("  新建 (created): " + ", ".join(diffs["created"]))
    if diffs["deleted"]:
        lines.append("  删除 (deleted): " + ", ".join(diffs["deleted"]))
    if diffs["content_changed"]:
        lines.append(
            "  内容变更 (content-changed): "
            + ", ".join("{}（{}… -> {}…）".format(p, old[:12], new[:12]) for p, old, new in diffs["content_changed"])
        )
    if diffs["renamed"]:
        lines.append(
            "  改名 (renamed): "
            + ", ".join("{} -> {}（{}…）".format(old, new, sha[:12]) for old, new, sha in diffs["renamed"])
        )
    lines.append(
        "  处置：先找出是谁写穿了数据根（泄漏路径见 conftest 的 isolated_task_runner 注释），"
        "修夹具或改用例；不要手动改 data/ 来「过检」。"
    )
    return "\n".join(lines)
