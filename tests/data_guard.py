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
  文件照算，理由见 `EXCLUDED_ROOT_FILENAMES`）。
- **目录集合也在快照里**（B10.2）：键是目录的归一化相对路径**再加一个结尾 `/`**，值固定
  是 `DIRECTORY_ENTRY_MARKER`。加这一条是因为「只读接口不改数据目录」这句话原先是假的
  ——实测 `GET /files` 会在真实数据根里长出 `archive/`、`records/`、`cache/`、
  `cache/parse_tree/`，而只哈希常规文件的 tripwire 对空目录完全看不见，于是一类安全
  主张无法验证。结尾 `/` 沿用 `SnapshotStats.unhashed` 早就在用的标记，不新开差异类别；
  符号链接（无论指向文件还是目录）一律不进快照，与「不跟随符号链接、非常规文件不哈希
  内容」同口径。
- 守卫只在**会话两端各拍一次快照**，因此「绿」的含义是末态一致，不是期间无人写入
  （两个已证明的盲区见 `GREEN_SEMANTICS_NOTE`，全部来历见 `docs/know-how.md`）。

看不见的东西（刻意口径，别当 bug 修）：只比内容哈希，**权限位、mtime、属主的单独
变化一律不可见**——H3 保护的是数据字节；一次 `chmod`/`touch` 不会让守卫红，
一次「改权限同时改内容」只会以内容变更的名义红。

反过来，目录进快照之后**响动会变多**：这台机器上 `run.py` 的服务若在会话中途建目录
（最典型是 `data/temporary/<token>/`，一次上传一个），守卫会红。那不是机制坏了，处置
仍然是 `RUN_PROCESS_TRIAGE` 那一句（先停服务再重跑，能稳定复现才说明是测试写穿了数据
根），别为了让它安静就把目录再退回快照之外。

两条会话级开关/通道（INH-1914 复审 I1 + 三项 Minor）：
- **警告通道**：`session_notice_warnings()` 把「本轮没有执法」和「有字节根本没比较」
  两类结论交给 `warnings`，由 `tests/conftest.py::pytest_terminal_summary` 发出去。
  光靠终端文案不算证据——两种绿照样都是退出码 0 和同一句「N passed」，抄数字的人
  读不出来；警告会让结果统计行变成「… 1 warning」，机器读得到。
- **硬要求开关** `MCCHECKER_REQUIRE_H3`：**默认关**（干净检出 / CI / 别人的 clone 都
  没有 `data/`，不能因此变红，那正是本守卫当初那条 Critical）。设成 `1`（或任何非空
  且非 `0` 的值）后，「未执法」从警告升级为**会话末硬失败**；只有这台装着真实
  `data/` 的权威开发机该这么跑，让「没在保护」变成发不出去的东西。
  见 `h3_enforcement_required`。
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple, Type

#: 快照 = {归一化相对路径: 内容 sha256 十六进制}；目录条目见 `DIRECTORY_ENTRY_MARKER`。
Snapshot = Dict[str, str]

#: 目录进快照时充当「值」的固定标记。它刻意不是 64 位十六进制，所以不可能与某个
#: sha256 撞上（`tests/test_data_guard.py::_assert_key_shape` 就按这两类分别认）。
#: 目录条目本身长这样：`"archive/"` —— 归一化相对路径 + 结尾 `/`。
#: 为什么用「键的结尾斜杠」而不是新开一个差异类别：类别键只有手写的一份
#: （`DIFF_LABELS`），新建/删除本来就要点名目录，加第五类只会让报告与判定三处各多写一遍。
DIRECTORY_ENTRY_MARKER = "dir"


def is_directory_entry(key: str) -> bool:
    """快照的键是不是目录条目（结尾 `/`；口径见 `DIRECTORY_ENTRY_MARKER`）。"""
    return key.endswith("/")


def split_entries(snapshot: Optional[Snapshot]) -> Tuple[List[str], List[str]]:
    """把快照拆成 `(文件键, 目录键)` 两个有序列表。公告与比对都从这里取，不各写一遍。"""
    items = sorted((snapshot or {}).items())
    files = [key for key, _value in items if not is_directory_entry(key)]
    dirs = [key for key, _value in items if is_directory_entry(key)]
    return files, dirs


#: 差异类别**唯一手写的一份**：键就是 `compare_snapshots` 的键，值是给人看的名。
#: 每类元素形状：created/deleted 是路径（目录条目带结尾 `/`，见 `DIRECTORY_ENTRY_MARKER`）；
#: content_changed 是 (路径, 旧哈希, 新哈希)；renamed 是 (旧路径, 新路径, 哈希)。
#: 目录只走 created/deleted 两类：它没有内容哈希，不参与改名配对（见 `compare_snapshots`）。
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

#: 打开「未执法即硬失败」的环境变量名（口径见模块 docstring 与 `h3_enforcement_required`）。
REQUIRE_H3_ENV_VAR = "MCCHECKER_REQUIRE_H3"

#: 快照期间「根本没读到手」的文件里，这一类的原因写得出来（消失）；其余按异常类名写。
VANISHED_REASON = "os.walk 列出之后、打开之前消失了"


@dataclass
class SnapshotStats:
    """一次（或连续两次）快照的**代价与盲区**——复审两条 Minor 都落在这里。

    - `total_bytes` / `seconds`：守卫每会话把整棵树**哈希两遍**（会话开头一遍、会话末
      一遍），代价随真实用户数据的体积线性增长。三张小文件时无所谓，但 `data/` 的设计
      用途是装用户的归档与记录，第二遍还安静地跑在 teardown 里——几 GB 的树看起来就是
      「pytest 卡住了」。所以把价格印到公告上，不许假装没有。
      **停止可接受的门槛（复审建议）：单会话 > 约 1 GB 或 > 约 30 秒**；越过这两条就
      该换机制（增量/清单式快照、只保护被引用子集），**不是**在这里加体积上限、
      **更不是**跳过文件：跳过的字节必须进 `unhashed` 被点名，永远不许静默省略。
    - `unhashed`：本棵树里守卫**没能比较**的部分（读不到的文件、遍历不了的目录、
      快照途中消失的文件），元素是 `(归一化相对路径, 原因)`。目录遍历失败尤其容易
      漏：`os.walk` 默认把 scandir 的异常咽掉，那整个子树会静默变成「没有差异」。
    """

    files: int = 0
    total_bytes: int = 0
    seconds: float = 0.0
    unhashed: List[Tuple[str, str]] = field(default_factory=list)


class H3NonEnforcementWarning(UserWarning):
    """「本轮没有执法」的警告类别：类别名会印在警告行上，机器可以按名 grep，
    不必依赖读中文段落里的某一句（复审 I1 的正身）。"""


class H3UncomparedWarning(UserWarning):
    """「有字节本轮根本没比较」的警告类别（读不到/遍历不了/中途消失）。"""


def h3_enforcement_required(environ: Optional[Mapping[str, str]] = None) -> bool:
    """`MCCHECKER_REQUIRE_H3` 这次是不是被打开（口径见模块 docstring）。

    `MCCHECKER_REQUIRE_H3=1` 即打开；除「未设置 / 空串 / `0`」之外**一律算打开**——刻意不把
    `true`/`yes` 这类写法当成「关」：一个想要求执法却写成 `=true` 的人，得到的却是
    默认不执法的绿，那正是 H3 要消灭的静默失效。
    """
    env = os.environ if environ is None else environ
    value = (env.get(REQUIRE_H3_ENV_VAR) or "").strip()
    return value not in ("", "0")


#: 数据根不存在或快照为空 = 守卫手里没有可比对的东西，绝不允许静默绿（C1）。
NOT_ENFORCING_NOTICE = (
    "H3 守卫未执法：data/ 不存在或为空，本次运行没有保护任何真实数据；"
    "这一轮的「绿」是没有可比对对象的结果，不是「数据完好」的证据。"
)


def requirement_failure_notice() -> str:
    """`MCCHECKER_REQUIRE_H3` 打开却没能执法时的失败文案（要被「点名」，故独立成函数）。"""
    return (
        "H3 守卫执法要求未满足（{var}=1）：{notice} "
        "要么在这台机器上准备好非空的真实 data/ 再跑，要么取消 {var}；"
        "不许把这一轮的绿读成 H3 已通过。"
    ).format(var=REQUIRE_H3_ENV_VAR, notice=NOT_ENFORCING_NOTICE)


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
    """守卫这次到底有没有东西可比。空快照就是没有。

    「空」的含义随快照口径一起变了（B10.2）：目录也进快照之后，**只有**根不存在、
    或根存在但里面一个文件一个目录都没有，才是空快照。一棵只装着空目录的树现在算
    「有东西可比」——它确实可比，而且公告那行会把「几个文件几个目录」分开说，
    不会把它读成「有 N 个文件」。
    """
    return bool(baseline)


def unhashed_notice_lines(stats: Optional[SnapshotStats]) -> List[str]:
    """守卫**没能比较**的那部分字节，逐条点名（复审 Minor：读不到≠没变化）。

    这一行既不归「没变」也不归「被删/被建」：读不到就是没有证据，说它没变是静默跳过，
    说它被某个用例删掉是冤枉用例。所以单独成一条，并且逐个点名、一个都不省略。
    """
    if stats is None or not stats.unhashed:
        return []
    named = ", ".join("{}（{}）".format(rel, reason) for rel, reason in sorted(stats.unhashed))
    return [
        "H3 守卫有 {} 个条目本轮**没有被比较**：{}。它们不是「没变」，是根本没读到手，"
        "这一轮的绿不覆盖这些字节；先修可读性（或停掉正在改 data/ 的 run.py 服务）再重跑，"
        "别把它读成「数据完好」。".format(len(stats.unhashed), named)
    ]


def session_notice_lines(
    baseline: Optional[Snapshot], cost: Optional[SnapshotStats] = None
) -> List[str]:
    """会话级公告（打在终端摘要里，`-q` 也看得见）：两种绿必须是两种观测结果。

    两个分支没有共享文案：未执法时**不出现**「执法中」，执法时**不出现**「未执法」，
    钩子完全没跑时两条都不出现——「没数据可保护」「保护完且干净」「守卫没接线」
    是三个可区分的观测结果，任何一个都不许被读成「已验证」。

    `cost` 是这两轮快照的代价与盲区（见 `SnapshotStats`）：执法那一行要把**总共哈希了
    多少字节、花了多少秒**印出来，价格不许隐形；有没读到的条目时两种状态都要追加一行。
    """
    if not is_enforcing(baseline):
        lines = [
            NOT_ENFORCING_NOTICE,
            "→ 本轮结论不能当 H3 的证据；换一台 `data/` 非空的机器重跑，或直接读上一条说明。",
        ]
    else:
        cost_segment = ""
        if cost is not None:
            cost_segment = "，本会话两轮快照共 {} 字节、耗时 {:.2f} 秒".format(
                cost.total_bytes, cost.seconds
            )
        # 文件数与目录数分开报（B10.2）：目录进快照之后 `len(baseline)` 不再是文件数，
        # 把它当文件数印出去就是撒谎——一树空目录能报成「N 个文件」。
        files, dirs = split_entries(baseline)
        dir_segment = "、{} 个目录".format(len(dirs)) if dirs else ""
        lines = [
            "H3 守卫执法中：真实 data/ 基线快照 {} 个文件{}，会话末逐文件与目录比对{}；"
            "绿=首末快照一致，不证明期间无人写入（盲区见 docs/know-how.md）".format(
                len(files), dir_segment, cost_segment
            )
        ]
    return lines + unhashed_notice_lines(cost)


def session_notice_warnings(
    baseline: Optional[Snapshot], cost: Optional[SnapshotStats] = None
) -> List[Tuple[str, Type[Warning]]]:
    """必须走**警告通道**的结论（复审 I1）：文案只给眼睛看，警告才改结果统计行。

    返回 `(消息, 类别)` 列表，由 `tests/conftest.py::pytest_terminal_summary` 原样
    `warnings.warn` 出去。两类各自独立可 grep：`H3NonEnforcementWarning`（本轮没人执法）
    和 `H3UncomparedWarning`（有字节根本没比较）；执法且全树可读时列表为空——
    「执法中」这一档既无警告也无失败，三个状态就此在输出上分开。
    """
    items: List[Tuple[str, Type[Warning]]] = []
    if not is_enforcing(baseline):
        items.append((NOT_ENFORCING_NOTICE, H3NonEnforcementWarning))
    if cost is not None and cost.unhashed:
        items.append((unhashed_notice_lines(cost)[0], H3UncomparedWarning))
    return items


def snapshot_data_tree(root: str, stats: Optional[SnapshotStats] = None) -> Snapshot:
    """对 `root` 做 {归一化相对路径: 内容 sha256} 快照，常规文件与**目录**都进。

    文件键 → 64 位十六进制 sha256；目录键 → 结尾带 `/` 且值为 `DIRECTORY_ENTRY_MARKER`
    （为什么这么表示、以及数据根一级 `.mcchecker.lock` 的豁免，见模块 docstring）。

    同一棵树无论 `root` 写成 `data`、`./data` 还是 `data/`，返回值必须一致
    （键已归一化）。`root` 不存在时返回空快照——会话结束仍会
    比对，凭空出现一棵树同样是变化。快照为空**不代表安全**，只代表没东西可保护，
    执法状态由 `is_enforcing` / `session_notice_lines` 明确说出口。

    快照期间目录条目本身不会失败：一个读不进去的子目录照样出现在父目录的列表里
    （列它只需要读父目录），于是它的键进快照、它**里面**的东西走 `unhashed` 点名。
    `stats` 传进来就顺便记账：哈希过的文件数、字节数、耗时，以及**没读到手**的条目
    （见 `SnapshotStats`；目录不计入 `files`/`total_bytes`，那两项是哈希的价格）。
    单个文件读失败绝不许裸抛 traceback 出到 teardown——那会把
    「这台机器的 data/ 正在被 churn / 权限不对」渲染成「守卫自己炸了」，反而更没法信；
    但也绝不许当成「没变化」。分类口径见函数体里那三段注释。
    """
    started = time.perf_counter()
    base = os.path.normpath(root)
    snapshot: Snapshot = {}
    try:
        if not os.path.isdir(base):
            return snapshot

        def _walk_error(error: OSError) -> None:
            # os.walk 默认把 scandir 的异常咽掉：一个读不了的目录会让整棵子树静默
            # 消失，看起来「没有差异」。记账并点名（尾部带 / 表明这是目录，不是文件）。
            if stats is None:
                return
            rel = os.path.relpath(getattr(error, "filename", base) or base, base)
            stats.unhashed.append(
                (
                    rel.replace(os.sep, "/") + "/",
                    "目录无法遍历 {}".format(error.__class__.__name__),
                )
            )

        for dirpath, dirnames, filenames in os.walk(base, onerror=_walk_error):
            at_root = dirpath == base
            # 目录先进快照，再处理文件：空目录的增删同样是「数据根被改过」（B10.2）。
            # 数据根自己不算条目（它是比对的范围，不是范围里的东西）；符号链接目录不算
            # 目录条目——os.walk 默认也不进去，进了快照反而是一半可见一半隐形。
            for name in sorted(dirnames):
                path = os.path.join(dirpath, name)
                if os.path.islink(path) or not os.path.isdir(path):
                    continue
                snapshot[os.path.relpath(path, base).replace(os.sep, "/") + "/"] = (
                    DIRECTORY_ENTRY_MARKER
                )
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                # 磁盘锁只豁免根一级（理由见 EXCLUDED_ROOT_FILENAMES）。
                if at_root and name in EXCLUDED_ROOT_FILENAMES:
                    continue
                # 与 BSD find `-type f` 同口径：不跟随符号链接，非常规文件不哈希内容。
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                rel = os.path.relpath(path, base).replace(os.sep, "/")
                digest = hashlib.sha256()
                size = 0
                try:
                    with open(path, "rb") as fh:
                        for chunk in iter(lambda: fh.read(_READ_CHUNK), b""):
                            digest.update(chunk)
                            size += len(chunk)
                except FileNotFoundError:
                    # 列进目录之后、打开之前没了：对 H3 来说这不是「什么都没发生」，
                    # 而是**这些字节已经不在这棵树里**。所以如实让它不进快照——比对
                    # 会按事实把它归到 删除（末尾没它）或 新建（基线没它），类别是
                    # 「数据少了」而不是「守卫炸了」；同时登记原因，免得读者以为是
                    # 某个用例动的手，而真实原因只是这台机器上有个服务在 churn。
                    if stats is not None:
                        stats.unhashed.append((rel, VANISHED_REASON))
                    continue
                except OSError as error:
                    # 文件在、读不到（权限 / EBUSY / 编码外的 IO 错误）：四类里哪一类
                    # 都是撒谎——说「没变」是静默跳过，说「被删/被建」是冤枉用例。
                    # 只登记为未比较并在公告+警告里点名，让它响、但不给它扣罪名。
                    if stats is not None:
                        stats.unhashed.append(
                            (rel, "读取失败 {}".format(error.__class__.__name__))
                        )
                    continue
                if stats is not None:
                    stats.files += 1
                    stats.total_bytes += size
                snapshot[rel] = digest.hexdigest()
        return snapshot
    finally:
        if stats is not None:
            stats.seconds += time.perf_counter() - started


def compare_snapshots(before: Snapshot, after: Snapshot) -> DiffResult:
    """两份快照进，分类差异出；完全一致时四类都是空列表。

    目录条目（结尾 `/`，见 `DIRECTORY_ENTRY_MARKER`）只走 新建/删除 两类：它没有内容
    哈希，值恒等于那个标记，混进改名配对就会把「删掉一个空目录 + 建了另一个空目录」
    读成一次改名。文件与目录就此分开算，两类路径最后合到同一个 created/deleted 列表里
    （结尾 `/` 就是区分），不新开差异类别。

    改名判定：同一 sha256 同时出现在「只在 before」与「只在 after」的路径集合里。
    一个哈希对应多对候选时按路径字典序一一配对，配不上的残余仍如实报告为
    新建/删除——宁可多报，不漏报。

    归类的精度上限（只影响 triage 文案，不影响红/绿）：**空文件都共享同一个
    sha256**，所以「删掉一个空文件 + 新建另一个空文件」会被归成 `改名` 而不是
    `删除 + 新建`。它照样红，只是别按「谁改名了」去查，先看是不是两个空文件。
    """
    before_dirs = {p for p in before if is_directory_entry(p)}
    after_dirs = {p for p in after if is_directory_entry(p)}
    file_before = {p: sha for p, sha in before.items() if not is_directory_entry(p)}
    file_after = {p: sha for p, sha in after.items() if not is_directory_entry(p)}

    created_paths = sorted(p for p in file_after if p not in file_before)
    deleted_paths = sorted(p for p in file_before if p not in file_after)
    content_changed = sorted(
        (p, file_before[p], file_after[p])
        for p in file_after
        if p in file_before and file_before[p] != file_after[p]
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

    dir_created = sorted(after_dirs - before_dirs)
    dir_deleted = sorted(before_dirs - after_dirs)
    found: DiffResult = {
        "created": sorted([p for p in created_paths if p not in paired_new] + dir_created),
        "deleted": sorted([p for p in deleted_paths if p not in paired_old] + dir_deleted),
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
    """把分类差异排成失败消息——必须逐文件点名，不许只报一句「哈希不匹配」。

    目录条目在 新建/删除 里长这样：`archive/`（结尾一个 `/`，见 `DIRECTORY_ENTRY_MARKER`）。
    这一条要写在文案里，因为 B10.2 实测到的形状恰好是「新建 4 项，4 项全是**空目录**，
    一个字节都没碰」——只看文件哈希的报告会把它读成「没变化」，读了文案的人才会知道
    自己正在看的是目录被改动，不是数据被写坏。
    """
    lines = [
        "H3 数据守卫：仓库真实的 data/ 树在本次 pytest 会话期间发生了变化。"
        "测试一律不许碰真实数据根（永久对照：`tests/test_data_guard_enforcement.py` "
        "在独立进程里对临时数据根各造一次破坏——含目录的新建与删除——本消息一律红）。"
        "结尾带 `/` 的条目是目录。"
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
