"""MCChecker 的 pytest 全局夹具。

目标（INH-1859 / M0-02）：让测试一律读写临时目录，不碰仓库真实的
`data/` 与 `.nicegui/`。隔离统一放在这里，各测试文件不再各自 patch。
"""

from __future__ import annotations

import os
import sys

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.core import device_models, dltool, storage  # noqa: E402

# 真实数据根必须在任何 monkeypatch 发生之前采集，作为后续重绑定的基准。
REAL_DATA_DIR = storage.DATA_DIR

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
    monkeypatch 被撤销**之前**拆完——`ThreadPoolExecutor` 的工作线程是非 daemon 的
    （R9），万一还有任务在跑，它至少是在临时数据根里跑，不会把字节写进仓库真实的
    `data/`（H3）。超时给 5 秒：忘了放行的假任务只会让这一条用例慢，不会卡死整轮。
    """
    yield
    from app.services import tasks

    tasks.reset_runner(timeout=5.0)
