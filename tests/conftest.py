"""MCChecker 的 pytest 全局夹具。

目标（INH-1859 / M0-02）：让测试一律读写临时目录，不碰仓库真实的
`data/` 与 `.nicegui/`。隔离统一放在这里，各测试文件不再各自 patch。
"""

from __future__ import annotations

import os
import sys
import threading
from typing import List

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.core import device_models, dltool, storage  # noqa: E402

# 真实数据根必须在任何 monkeypatch 发生之前采集，作为后续重绑定的基准。
REAL_DATA_DIR = storage.DATA_DIR

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


@pytest.fixture(scope="session", autouse=True)
def isolated_nicegui_storage(tmp_path_factory):
    """把 NiceGUI 的持久化目录从仓库 `.nicegui/` 挪到临时目录。

    做法与 NiceGUI 自带 `nicegui.testing` 一致：改 `Storage.path` 后重建
    `app.storage`，让 `FilePersistentDict` 使用新路径。
    """
    try:
        from nicegui import app as nicegui_app
        from nicegui.storage import Storage
    except ImportError:
        yield
        return

    original_path = Storage.path
    original_storage = nicegui_app.storage
    Storage.path = tmp_path_factory.mktemp("nicegui")
    nicegui_app.storage = Storage()
    try:
        yield
    finally:
        Storage.path = original_path
        nicegui_app.storage = original_storage


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

