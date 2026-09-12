"""全局测试配置 — 数据隔离基础设施

确保所有 pytest 测试不触碰真实 data/ 目录。
提供共享 fixture 替代各 test 文件重复的 monkeypatch 逻辑。
"""

import os
import shutil
import sys
import tempfile

import pytest

# ---------------------------------------------------------------------------
# sys.path 设置
# ---------------------------------------------------------------------------
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# 真实 data 目录的绝对路径（用于 write guard 校验）
_REAL_DATA_DIR = os.path.join(ROOT_DIR, "data")

# ---------------------------------------------------------------------------
# storage 模块中需要重定向的路径属性
# ---------------------------------------------------------------------------
_STORAGE_PATH_ATTRS = (
    "DATA_DIR",
    "PROFILES_DIR",
    "CONFIGS_DIR",
    "ARCHIVE_DIR",
    "CACHE_DIR",
    "PARSE_CACHE_DIR",
    "MAPPING_FILE",
    "FAVORITES_FILE",
    "BINDINGS_FILE",
    "TOOLS_FILE",
    "SCHEDULE_FILE",
    "IP_MAPPING_FILE",
    "ADMIN_USERS_FILE",
    "EDIT_REMARKS_FILE",
)

_STORAGE_LEGACY_ATTRS = (
    "LEGACY_MAPPING_FILE",
    "LEGACY_FAVORITES_FILE",
    "LEGACY_BINDINGS_FILE",
    "LEGACY_TOOLS_FILE",
    "LEGACY_SCHEDULE_FILE",
    "LEGACY_IP_MAPPING_FILE",
    "LEGACY_ADMIN_USERS_FILE",
    "LEGACY_EDIT_REMARKS_FILE",
)


# ---------------------------------------------------------------------------
# Session-scoped: 创建临时 data root，确保测试不触碰真实数据
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True, scope="session")
def test_data_root():
    """创建临时 data root，确保测试不触碰真实 data/ 目录。

    设置环境变量 MCHECKER_DATA_DIR（在业务模块 import 前生效），
    并在测试结束后清理。
    """
    temp_root = tempfile.mkdtemp(prefix="mcchecker_test_")
    old_env = os.environ.get("MCHECKER_DATA_DIR")
    os.environ["MCHECKER_DATA_DIR"] = temp_root

    yield temp_root

    # cleanup
    shutil.rmtree(temp_root, ignore_errors=True)
    if old_env is None:
        os.environ.pop("MCHECKER_DATA_DIR", None)
    else:
        os.environ["MCHECKER_DATA_DIR"] = old_env


# ---------------------------------------------------------------------------
# Function-scoped: 每个测试重定向 storage 路径到临时目录
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def isolated_data_env(monkeypatch, tmp_path):
    """将所有 storage 路径重定向到 tmp_path，确保测试间数据隔离。

    替代各 test 文件中重复的 setup_test_env fixture。
    """
    import app.core.storage as storage_mod

    data_dir = str(tmp_path)

    # 先设置 DATA_DIR
    monkeypatch.setattr(storage_mod, "DATA_DIR", data_dir)

    # 派生路径属性
    monkeypatch.setattr(storage_mod, "PROFILES_DIR", os.path.join(data_dir, "profiles"))
    monkeypatch.setattr(storage_mod, "CONFIGS_DIR", os.path.join(data_dir, "configs"))
    monkeypatch.setattr(storage_mod, "ARCHIVE_DIR", os.path.join(data_dir, "archive"))
    monkeypatch.setattr(storage_mod, "CACHE_DIR", os.path.join(data_dir, "cache"))
    monkeypatch.setattr(storage_mod, "PARSE_CACHE_DIR", os.path.join(data_dir, "cache", "parse_tree"))

    # 文件路径属性
    monkeypatch.setattr(storage_mod, "MAPPING_FILE", os.path.join(data_dir, "config_mapping.json"))
    monkeypatch.setattr(storage_mod, "FAVORITES_FILE", os.path.join(data_dir, "favorites.json"))
    monkeypatch.setattr(storage_mod, "BINDINGS_FILE", os.path.join(data_dir, "bindings.json"))
    monkeypatch.setattr(storage_mod, "TOOLS_FILE", os.path.join(data_dir, "tools.json"))
    monkeypatch.setattr(storage_mod, "SCHEDULE_FILE", os.path.join(data_dir, "schedule.json"))
    monkeypatch.setattr(storage_mod, "IP_MAPPING_FILE", os.path.join(data_dir, "ip_mapping.json"))
    monkeypatch.setattr(storage_mod, "ADMIN_USERS_FILE", os.path.join(data_dir, "admin_users.json"))
    monkeypatch.setattr(storage_mod, "EDIT_REMARKS_FILE", os.path.join(data_dir, "edit_remarks.json"))

    # LEGACY_ 属性指向对应的当前值（与生产代码行为一致）
    monkeypatch.setattr(storage_mod, "LEGACY_MAPPING_FILE", storage_mod.MAPPING_FILE)
    monkeypatch.setattr(storage_mod, "LEGACY_FAVORITES_FILE", storage_mod.FAVORITES_FILE)
    monkeypatch.setattr(storage_mod, "LEGACY_BINDINGS_FILE", storage_mod.BINDINGS_FILE)
    monkeypatch.setattr(storage_mod, "LEGACY_TOOLS_FILE", storage_mod.TOOLS_FILE)
    monkeypatch.setattr(storage_mod, "LEGACY_SCHEDULE_FILE", storage_mod.SCHEDULE_FILE)
    monkeypatch.setattr(storage_mod, "LEGACY_IP_MAPPING_FILE", storage_mod.IP_MAPPING_FILE)
    monkeypatch.setattr(storage_mod, "LEGACY_ADMIN_USERS_FILE", storage_mod.ADMIN_USERS_FILE)
    monkeypatch.setattr(storage_mod, "LEGACY_EDIT_REMARKS_FILE", storage_mod.EDIT_REMARKS_FILE)

    # 创建必要目录
    storage_mod._ensure_dirs()

    yield storage_mod


# ---------------------------------------------------------------------------
# Function-scoped: 每个测试重置 ContextVar
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_context_vars():
    """每个测试重置 ContextVar，防止测试间状态泄漏。"""
    from app.core.storage import _profile_var, DEFAULT_PROFILE

    token = _profile_var.set(DEFAULT_PROFILE)
    yield
    _profile_var.reset(token)


# ---------------------------------------------------------------------------
# Function-scoped: write guard — 验证真实 data/ 未被触碰
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _write_guard():
    """任何写操作落到 fixture root 外（即真实 data/）立即失败。

    在每个测试后检查真实 data/ 目录是否被意外写入。
    """
    yield

    if os.path.isdir(_REAL_DATA_DIR):
        contents = os.listdir(_REAL_DATA_DIR)
        if contents:
            pytest.fail(
                f"WRITE GUARD: 真实 data/ 目录被意外写入! "
                f"发现文件: {contents}"
            )
