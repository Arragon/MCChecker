"""MCChecker 的 pytest 全局夹具。

目标（INH-1859 / M0-02）：让测试一律读写临时目录，不碰仓库真实的
`data/` 与 `.nicegui/`。隔离统一放在这里，各测试文件不再各自 patch。
"""

from __future__ import annotations

import os
import sys
import threading
import warnings
from typing import Dict, List, Optional

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.core import device_models, dltool, storage  # noqa: E402
from data_guard import (  # noqa: E402
    compare_snapshots,
    format_diff_report,
    has_differences,
    h3_enforcement_required,
    is_enforcing,
    requirement_failure_notice,
    session_notice_lines,
    session_notice_warnings,
    snapshot_data_tree,
    SnapshotStats,
)

# 真实数据根必须在任何 monkeypatch 发生之前采集，作为后续重绑定的基准。
REAL_DATA_DIR = storage.DATA_DIR

#: H3 守卫在 pytest 生命周期里的**全部接线**（钩子名 + 夹具名）。
#: `tests/test_data_guard_enforcement.py` 生成的临时 conftest 按这份清单原样重导出，
#: 所以给守卫加新钩子必须同时加进这里——漏了那条钩子，执法路径的永久证明会立刻红，
#: 而不是悄悄少测一半。改名同理（`getattr` 不带默认值，改名直接红）。
#: 命名口径（复审 Minor：原来「忘加就红」只对**内部用例真的观察到的**钩子成立，纯增量的
#: 钩子没人断言就永远静默不成立）：conftest 里模块级的 `pytest_*` 钩子与 `h3_*` 夹具
#: 一律必须出现在这个元组里，`tests/test_data_guard.py` 用 AST 扫 conftest 钉住这条。
H3_GUARD_PLUGIN_MEMBERS = (
    "pytest_sessionstart",
    "pytest_terminal_summary",
    "h3_data_guard",
)

#: H3 守卫的会话基线（{归一化相对路径: sha256}），由 `pytest_sessionstart` 在
#: collection 之前填好——session 级夹具是「首次使用才实例化」的，把快照放在夹具
#: 装序里理论上来得及（第一个用例的 setup 先于任何写入），但放在钩子里才是真正
#: 的「会话一开始就拍」，不给未来的 collection 期写入留窗口。
_H3_BASELINE: Optional[Dict[str, str]] = None

#: 两轮快照（基线 + 会话末）的代价与盲区，累计在同一个对象上：公告要把字节数与秒数
#: 印出来，读不到/消失的条目也要点名（复审两条 Minor）。
_H3_COST = SnapshotStats()


def pytest_sessionstart(session):
    """H3 守卫（INH-1914）：在任何测试写入之前对真实 `data/` 拍基线快照。"""
    global _H3_BASELINE
    _H3_BASELINE = snapshot_data_tree(REAL_DATA_DIR, _H3_COST)


def pytest_terminal_summary(terminalreporter) -> None:
    """H3 守卫的会话级公告（C1）+ 警告通道（复审 I1）：「这次到底有没有在执法」。

    `data/` 不存在或快照为空时，守卫手里没有可比对的东西，绿不带来任何保证；这一行
    必须让它说出来，否则「没数据可保护」和「保护完且干净」在终端上长得一模一样——
    那正是本卡要消灭的误读（干净检出 / CI / 别人的 clone 都没有 `data/`，它被 gitignore
    了）。选 `pytest_terminal_summary` 而不是 `pytest_report_header`：后者在 `-q` 下
    根本不打印，而每张卡的验证命令都是 `-q`。

    光有文案不够：两种绿都是退出码 0 加同一句「N passed」，CI 日志、JUnit 消费者、
    以及每张卡贴上来的「905 passed」都读不出差别。所以这里**同时**把结论走一遍
    `warnings`，让结果统计行自己变成「1 passed, 1 warning」。位置就选在这里，理由是
    pytest 7.4.4 自己的接线（`_pytest/warnings.py` 里那个包住 `pytest_terminal_summary`
    的 hookwrapper，用 `catch_warnings_for_item` 记录）在离开上下文时经
    `pytest_warning_recorded` 把警告灌进 `TerminalReporter.stats["warnings"]`；
    而写统计行的是 `_pytest/terminal.py::TerminalReporter.pytest_sessionfinish` 里的
    `summary_stats()`，它排在**整个 `pytest_terminal_summary` 钩子返回之后**——
    于是这里 warn 出去的警告一定落在最终那一行上（`-q` 下也自带一节「warnings
    summary」）。反过来放在 `pytest_sessionstart`/夹具装序里的警告只会散在几十行
    进度点中间，抄数字的人照样看不见。
    """
    for line in session_notice_lines(_H3_BASELINE, _H3_COST):
        terminalreporter.write_line(line, yellow=True)
    for message, category in session_notice_warnings(_H3_BASELINE, _H3_COST):
        warnings.warn(message, category, stacklevel=1)


@pytest.fixture(scope="session", autouse=True)
def h3_data_guard():
    """会话结束重新快照真实 `data/` 并比对，任何差异都判失败（H3 的自动执法）。

    纯函数在 `tests/data_guard.py`（快照、分类比对、点名报告、执法状态公告），这里只
    接线。排除根一级的 `.mcchecker.lock`（运行时产物）；**目录集合同样在比对范围内**
    （B10.2：只哈希常规文件的 tripwire 看不见「读路径长出空目录」这一类改动）；
    口径见该模块 docstring。
    本夹具**不**替代 `isolated_data_root` / `isolated_task_runner` 的事前隔离，是它们
    漏网之后的事后网；也不参与那两个夹具的装/拆序（那是卡 6 复审刻意钉住的）。

    能力边界（I2，完整来历见 `docs/know-how.md`）：绿只证明会话首末两次快照一致，
    不证明期间没有测试写过 `data/`；「写完又删掉」和「最后一个快照之后才落盘的线程」
    都看不见。执法与否由 `pytest_terminal_summary` 每次自己说出口（文案 + 警告两条通道），
    不靠读的人猜。

    `MCCHECKER_REQUIRE_H3=1`（口径与默认值见 `data_guard.h3_enforcement_required`）：
    把「未执法」从警告升级成会话末硬失败——这台装着真实 `data/` 的权威开发机用它，
    让「这一轮根本没保护任何数据」绿不出去。默认关：干净检出与 CI 都没有 `data/`，
    不许因此变红（那是本守卫当初那条 Critical）。「有字节没比较」也照旧只走警告，
    不给它单独加红——它的响动来自「1 warning」这一行。
    """
    yield
    diffs = compare_snapshots(_H3_BASELINE, snapshot_data_tree(REAL_DATA_DIR, _H3_COST))
    if has_differences(diffs):
        pytest.fail(format_diff_report(diffs), pytrace=False)
    if h3_enforcement_required() and not is_enforcing(_H3_BASELINE):
        pytest.fail(requirement_failure_notice(), pytrace=False)


#: 测试里「任务闸门」（Event）自锁的兜底上限：万一没人放行，工作线程最多阻塞这么久
#: （防整轮 pytest 卡死）。`tests/test_api_tasks.py` 从这里取，别再写字面量。
GATE_TIMEOUT = 20.0

#: 拆夹具时等 runner 散尽的上限——**必须大于** `GATE_TIMEOUT`（I2）。
#:
#: 原先这里是 5 秒、闸门等 20 秒，两头都不成立：用例里那句 `assert elapsed < 2.0`
#: 排在 `gate.set()` **前面**，一失败工作线程就还扣在闸门上；夹具 5 秒放弃、把单例丢掉，
#: `monkeypatch` 紧接着把数据根恢复成仓库真实的 `data/`，那条线程十几秒后醒过来照常
#: 经 `updates`/`storage` 落盘——正是本夹具要防的那次污染（H3），而且是静悄悄发生的。
#: 现在两道一起堵：① 闸门由夹具**无条件放行**（见 `isolated_task_runner` / `gates`），
#: ② 等待预算**长过**闸门自己的兜底，所以线程一定是在临时数据根里醒的。
RUNNER_DRAIN_TIMEOUT = 30.0

# 业务目录树中需要真实存在的一级/二级目录。
DATA_SUBDIRS = (
    "profiles",
    "configs",
    "archive",
    "records",
    "cache",
    "cache/parse_tree",
    "temporary",
    "recovery",
    "logs",
)


def _redirect_data_root(monkeypatch, module, new_root: str) -> None:
    """把模块内所有指向真实数据根的字符串常量整体搬到 new_root。

    用"前缀替换"而不是逐条列举常量名：新增 `XXX_FILE` 时无需回来改夹具，
    也不会漏掉 `LEGACY_*` 这类别名。
    """
    for name, value in list(vars(module).items()):
        if not isinstance(value, str):
            continue
        if value == REAL_DATA_DIR:
            monkeypatch.setattr(module, name, new_root)
        elif value.startswith(REAL_DATA_DIR + os.sep):
            monkeypatch.setattr(module, name, new_root + value[len(REAL_DATA_DIR) :])


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path, monkeypatch):
    """为每个测试在 tmp_path 下建立独立的数据根。"""
    data_root = tmp_path / "data"
    for sub in DATA_SUBDIRS:
        (data_root / sub).mkdir(parents=True, exist_ok=True)
    root = str(data_root)

    _redirect_data_root(monkeypatch, storage, root)
    _redirect_data_root(monkeypatch, dltool, root)
    # 机型清单路径由函数在运行期拼出，常量重绑定覆盖不到，单独钉住。
    monkeypatch.setattr(device_models, "_models_file", lambda: os.path.join(root, "device_models.json"))

    # 机型只认 storage 自己的上下文变量（M0-06 已删除 NiceGUI 回退），因此
    # 每个用例开头重置一次即可，不需要再清理浏览器会话。
    storage.set_active_profile(storage.DEFAULT_PROFILE)
    storage._ensure_dirs()
    yield


@pytest.fixture(autouse=True)
def isolated_task_runner(isolated_data_root):
    """后台任务队列是**进程内单例**，工作线程会泄漏到别的用例（卡 6 / M3-07）。

    这里显式依赖 `isolated_data_root`：夹具的拆序是装序的反面，所以本夹具一定在
    monkeypatch 被撤销**之前**拆完——工作线程不管是不是 daemon 都会自己跑到结束
    （daemon 位从创建者继承，`threading.py:828`；现网从 HTTP 线程提交就是 daemon），
    万一还有任务在跑，它至少是在临时数据根里跑，不会把字节写进仓库真实的
    `data/`（H3）。

    `yield` 出去的是**闸门登记簿**（I2）：`gates()` 造的每一条 Event 都记在这里，拆的
    时候先**无条件**放行、再等 runner 散。登记与放行放在同一个夹具、同一个 `finally`
    语义里，不是靠 pytest 的拆序把两个夹具排前后——这一点是刻意的：用例的中途断言失败
    时，原先那句排在断言之后的 `release.set()` 根本跑不到，孤儿线程就活到了 monkeypatch
    撤销之后。等待预算见 `RUNNER_DRAIN_TIMEOUT`。

    超时**不是**放过：`shutdown()` 的公开契约是「记一条 WARNING 就返回」（§10.1），
    `reset_runner` 也照样把单例丢掉，于是那条孤儿线程会在 `monkeypatch` 把
    `storage.DATA_DIR` 恢复成仓库 `data/` **之后**继续经 `updates`/`storage` 落盘
    ——正是本夹具要防的那次污染，而在加本守卫之前它是静悄悄发生的。所以 reset 之后
    再点名一次残留线程并直接把用例判失败：约束由夹具守，不去改生产 API 的签名。
    """
    registry: List[threading.Event] = []
    yield registry

    from app.services import tasks

    for gate in registry:
        gate.set()  # ① 不管用例是绿是红，闸门一律放行：线程不许被扣到夹具之外
    tasks.reset_runner(RUNNER_DRAIN_TIMEOUT)  # ② 再等它散，预算长过闸门自己的兜底
    # ③ 谓词单一出处（Item 5）：`tasks.worker_threads()` 按名字筛且摘掉不写盘的
    #    `mctask-shutdown` 等待线程——这里绝不再自己按裸前缀 enumerate 一遍，
    #    否则排空踩满预算时会把一个非写入方报成「还有人活着」。
    #    上面 ① 的放行环有哨兵盯着：`tests/test_api_tasks.py::TestFixtureOwnsEveryGate`
    #    ——删掉那两行 `gate.set()` 它会红，别把本夹具「简化」回去。
    survivors = [thread.name for thread in tasks.worker_threads()]
    if survivors:
        pytest.fail(
            "任务队列已经拆完，但仍有工作线程活着：%s。它会在这次 monkeypatch 撤销之后"
            "继续往真实的 data/ 里写（H3）。放行这条任务自己的 Event/gate，或让它早点"
            "结束——不要放宽本夹具的等待，也不要改线程名绕过检查。" % ", ".join(sorted(survivors))
        )


@pytest.fixture()
def gates(isolated_task_runner):
    """造「任务闸门」（只存在于测试）：本用例经它创建的每一条都会在拆夹具时放行。

    用法是把 `release = threading.Event()` 换成 `release = gates()`。用例照常自己
    `release.set()`，区别只在**断言中途红**的时候：漏掉的那次放行由夹具补上，
    于是工作线程一定是在临时数据根里醒过来、被 `reset_runner` 等散（I2 / H3）。
    """
    registry: List[threading.Event] = isolated_task_runner

    def make() -> threading.Event:
        event = threading.Event()
        registry.append(event)
        return event

    return make


#: 拆夹具时等调度线程退出的上限。它只是「确认线程真的散了」的兜底：`stop()` 不等 join
#: （R11），而调度线程睡的是 `Event.wait(<=60)`，`stop()` 一置位就立刻醒。
SCHEDULER_DRAIN_TIMEOUT = 5.0


@pytest.fixture(autouse=True)
def isolated_scheduler(isolated_task_runner):
    """卡 8 / M3-08：调度线程是**手搓的 daemon 线程，两道守卫都看不见它**（H3）。

    为什么必须自带这条夹具（卡 8 手递手 §2）：

    * `concurrent.futures.thread._python_exit` 只 join 池线程，不 join 本线程；
    * `isolated_task_runner` 的存活判据按线程名 `mctask` 筛，`mcsched` **故意**不在里面
      （那条判据的语义是「还有没有人在写 `data/`」）；
    * `h3_data_guard` 的第二个已证明盲区正是「最后一次快照之后才落盘的线程」。

    所以泄漏只能由本卡自己钉死，不能指望远端守卫。这里显式依赖 `isolated_task_runner`
    （进而依赖 `isolated_data_root`）：pytest 的拆序是装序的反面，因此本夹具一定在
    `runner` 关掉**之前**先让调度线程停止提交，也在 `storage` 的数据根被恢复成仓库真实的
    `data/` **之前**把线程拆干净——万一它还在睡，醒来也是在临时根里。

    等不散就点名判失败，与 `isolated_task_runner` 对残留工作线程的做法一致。
    """
    from app.services import scheduler

    scheduler.reset_scheduler(SCHEDULER_DRAIN_TIMEOUT)  # 装之前先清：别带着上一条的线程进来
    yield
    scheduler.reset_scheduler(SCHEDULER_DRAIN_TIMEOUT)
    survivors = [
        thread.name
        for thread in threading.enumerate()
        if thread.name.startswith(scheduler.THREAD_NAME)
    ]
    if survivors:
        pytest.fail(
            "调度线程已经 stop 并且等过 %.0f 秒，仍然活着：%s。它会在本次 monkeypatch 撤销之后"
            "继续按周期醒来提交任务（H3）。用例请调用 scheduler.reset_scheduler()，或不要把线程"
            "start 出来——不要放宽上面的等待，也不要改线程名绕过检查。"
            % (SCHEDULER_DRAIN_TIMEOUT, ", ".join(sorted(survivors)))
        )
