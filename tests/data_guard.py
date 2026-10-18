"""H3 数据守卫的纯函数：真实 `data/` 树的快照、分类比对与会话级公告（INH-1914）。

这里刻意不含任何 pytest 夹具、全局状态或真实路径——执法接线在
`tests/conftest.py`（session 级 autouse 夹具 + `pytest_sessionstart` 基线快照 +
`pytest_terminal_summary` 公告）。背景与约束见 `docs/know-how.md`
「H3 的唯一可信证据是本守卫的红/绿」一条。

三个口径约定：
- 快照的键是**相对数据根的归一化 posix 路径**（无 `./`、无绝对前缀、无 `data/` 前缀）。
  历史上 `data` / `./data` / `data/` 三种拼法各自算出不同的聚合哈希，抄数字的
  人无从发现漂移；归一化让同一棵树无论根路径怎么写都得到同一份快照。
- `.mcchecker.lock` 是运行时产物、不是数据，**数据根一级**的那个不计入快照（深层同名
  文件照算，理由见 `EXCLUDED_ROOT_FILENAMES`）；空目录的增删也不算数据变化
  （只哈希常规文件内容）。
- 守卫只在**会话两端各拍一次快照**，因此「绿」的含义是末态一致，不是期间无人写入
  （两个已证明的盲区见 `GREEN_SEMANTICS_NOTE`，全部来历见 `docs/know-how.md`）。

看不见的东西（刻意口径，别当 bug 修）：只比内容哈希，**权限位、mtime、属主的单独
变化一律不可见**——H3 保护的是数据字节；一次 `chmod`/`touch` 不会让守卫红，
一次「改权限同时改内容」只会以内容变更的名义红。
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Optional, Tuple

#: 快照 = {归一化相对路径: 内容 sha256 十六进制}。
Snapshot = Dict[str, str]

#: 差异类别**唯一手写的一份**：键就是 `compare_snapshots` 的键，值是给人看的名。
#: 每类元素形状：created/deleted 是路径；content_changed 是 (路径, 旧哈希, 新哈希)；
#: renamed 是 (旧路径, 新路径, 哈希)。
DIFF_LABELS: Dict[str, str] = {
    "created": "新建 (created)",
    "deleted": "删除 (deleted)",
    "content_changed": "内容变更 (content-changed)",
    "renamed": "改名 (renamed)",
}

#: 类别键与遍历序由上面那份映射派生，不再另写一遍：`has_differences`、报告行序、
#: 比对结果的键集都从这里取，谁也没法和它漂移（复审 Minor：原来是三处各列一次）。
DIFF_CATEGORIES: Tuple[str, ...] = tuple(DIFF_LABELS)

#: 比对结果 = 上面四类，每类的元素形状见 `DIFF_LABELS` 注释。
DiffResult = Dict[str, List[Any]]

#: 运行时产物，不是数据：不计入快照。**只豁免数据根一级**——现网唯一的磁盘锁是
#: `app/server/http.py::SingleInstanceLock` 在 `<data_root>/.mcchecker.lock` 创建的，
#: 深层出现同名文件不是运行时产物而是未知文件，必须让守卫看见。
#: （历史 shell 配方 `find ... -not -name '.mcchecker.lock'` 是全树豁免；守卫比它更严，
#: 两者对「根一级有锁、别处没有」的正常树结果一致。）
EXCLUDED_ROOT_FILENAMES = frozenset({".mcchecker.lock"})

_READ_CHUNK = 65536

#: 数据根不存在或快照为空 = 守卫手里没有可比对的东西，绝不允许静默绿（C1）。
NOT_ENFORCING_NOTICE = (
    "H3 守卫未执法：data/ 不存在或为空，本次运行没有保护任何真实数据；"
    "这一轮的「绿」是没有可比对对象的结果，不是「数据完好」的证据。"
)

#: 绿的含义说清楚（I2）：两个盲区都是实测存在的，不是理论顾虑。
GREEN_SEMANTICS_NOTE = (
    "守卫只在会话两端各拍一次快照：绿只证明首末两次快照一致（末态完好），"
    "不证明本次运行期间没有测试写过 data/。看不见的两类——"
    "① 用例往数据根写完又把文件删掉（瞬时破坏）；"
    "② 最后一个快照之后才落盘的线程（`isolated_task_runner` 的 liveness 谓词只认 "
    "`mctask` 前缀的 runner 线程，app/pages 下 ui.timer/asyncio 调用点与未来的调度线程"
    "都不在它的能力范围内）。要「没人写过」的强保证得换机制（写入方一律过 runner 或 "
    "会话级审计），别把本守卫当成那个机制。"
)

#: 本机应用与测试共用同一个 `data/`，红的第一嫌疑常常不是测试（Minor）。
RUN_PROCESS_TRIAGE = (
    "若这台机器上同时开着 `run.py` 的服务进程：应用与测试用的是同一个 data/，"
    "先停掉服务再重跑一遍，能稳定复现才说明是测试写穿了数据根。"
)


def is_enforcing(baseline: Optional[Snapshot]) -> bool:
    """守卫这次到底有没有东西可比。空快照（根不存在/树里没文件）就是没有。"""
    return bool(baseline)


def session_notice_lines(baseline: Optional[Snapshot]) -> List[str]:
    """会话级公告（打在终端摘要里，`-q` 也看得见）：两种绿必须是两种观测结果。

    两个分支没有共享文案：未执法时**不出现**「执法中」，执法时**不出现**「未执法」，
    钩子完全没跑时两条都不出现——「没数据可保护」「保护完且干净」「守卫没接线」
    是三个可区分的观测结果，任何一个都不许被读成「已验证」。
    """
    if not is_enforcing(baseline):
        return [
            NOT_ENFORCING_NOTICE,
            "→ 本轮结论不能当 H3 的证据；换一台 `data/` 非空的机器重跑，或直接读上一条说明。",
        ]
    return [
        "H3 守卫执法中：真实 data/ 基线快照 {} 个文件，会话末逐文件比对；"
        "绿=首末快照一致，不证明期间无人写入（盲区见 docs/know-how.md）".format(len(baseline))
    ]


def snapshot_data_tree(root: str) -> Snapshot:
    """对 `root` 下每个常规文件做 {归一化相对路径: sha256} 快照。

    同一棵树无论 `root` 写成 `data`、`./data` 还是 `data/`，返回值必须一致
    （键已归一化）。目录不进快照；`root` 不存在时返回空快照——会话结束仍会
    比对，凭空出现一棵树同样是变化。快照为空**不代表安全**，只代表没东西可保护，
    执法状态由 `is_enforcing` / `session_notice_lines` 明确说出口。
    """
    base = os.path.normpath(root)
    snapshot: Snapshot = {}
    if not os.path.isdir(base):
        return snapshot
    for dirpath, _dirnames, filenames in os.walk(base):
        at_root = dirpath == base
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            # 磁盘锁只豁免根一级（理由见 EXCLUDED_ROOT_FILENAMES）。
            if at_root and name in EXCLUDED_ROOT_FILENAMES:
                continue
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

    归类的精度上限（只影响 triage 文案，不影响红/绿）：**空文件都共享同一个
    sha256**，所以「删掉一个空文件 + 新建另一个空文件」会被归成 `改名` 而不是
    `删除 + 新建`。它照样红，只是别按「谁改名了」去查，先看是不是两个空文件。
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

    found: DiffResult = {
        "created": [p for p in created_paths if p not in paired_new],
        "deleted": [p for p in deleted_paths if p not in paired_old],
        "content_changed": content_changed,
        "renamed": renamed,
    }
    # 键集由 DIFF_CATEGORIES 兜一遍：将来新增类别时这里先炸，不让报告漏报一类。
    return {category: found[category] for category in DIFF_CATEGORIES}


def has_differences(diffs: DiffResult) -> bool:
    return any(diffs[category] for category in DIFF_CATEGORIES)


def _describe_entry(category: str, item: Any) -> str:
    if category == "content_changed":
        path, old, new = item
        return "{}（{}… -> {}…）".format(path, old[:12], new[:12])
    if category == "renamed":
        old, new, sha = item
        return "{} -> {}（{}…）".format(old, new, sha[:12])
    return str(item)


def format_diff_report(diffs: DiffResult) -> str:
    """把分类差异排成失败消息——必须逐文件点名，不许只报一句「哈希不匹配」。"""
    lines = [
        "H3 数据守卫：仓库真实的 data/ 树在本次 pytest 会话期间发生了变化。"
        "测试一律不许碰真实数据根（永久对照：`tests/test_data_guard_enforcement.py` "
        "在独立进程里对临时数据根各造一次四类破坏，本消息一律红）。"
    ]
    for category in DIFF_CATEGORIES:
        items = diffs[category]
        if not items:
            continue
        lines.append(
            "  {}: {}".format(DIFF_LABELS[category], ", ".join(_describe_entry(category, i) for i in items))
        )
    lines.append(
        "  处置：先找出是谁写穿了数据根（泄漏路径见 conftest 的 isolated_task_runner 注释），"
        "修夹具或改用例；不要手动改 data/ 来「过检」。"
    )
    lines.append("  " + RUN_PROCESS_TRIAGE)
    lines.append("  " + GREEN_SEMANTICS_NOTE)
    return "\n".join(lines)
