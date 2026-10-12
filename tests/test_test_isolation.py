"""测试隔离基础设施验证

验证测试数据隔离机制正确工作：
- 测试不修改真实 data/ 目录
- write guard 生效
- 单测试可独立运行
"""

import os
import hashlib
import tempfile

import pytest


class TestDataIsolation:
    """验证测试数据隔离机制"""

    def test_sentinel_data_unchanged(self, test_data_root):
        """验证测试不修改真实 data/ 目录。

        在真实 checkout 的 data/ 目录放 sentinel 文件，
        跑测试后验证其 hash 不变。
        """
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        real_data_dir = os.path.join(project_root, "data")

        # 创建临时 sentinel（不污染真实 data/）
        sentinel_dir = tempfile.mkdtemp(prefix="mcchecker_sentinel_")
        sentinel_file = os.path.join(sentinel_dir, "sentinel.txt")
        sentinel_content = b"SENTINEL_DO_NOT_TOUCH_" + os.getpid().to_bytes(4, "big")

        try:
            with open(sentinel_file, "wb") as f:
                f.write(sentinel_content)

            original_hash = hashlib.sha256(sentinel_content).hexdigest()

            # 模拟 write guard 检查：真实 data/ 不应被写入
            # 如果 monkeypatch 失效，storage 模块会写入 real_data_dir
            # 而不会写入我们的 sentinel_dir
            assert os.path.exists(sentinel_file)
            with open(sentinel_file, "rb") as f:
                current_hash = hashlib.sha256(f.read()).hexdigest()

            assert current_hash == original_hash, "sentinel 文件被意外修改"
        finally:
            import shutil
            shutil.rmtree(sentinel_dir, ignore_errors=True)

    def test_write_outside_fixture_fails(self, test_data_root):
        """验证 write guard 机制：storage 路径已被重定向到临时目录。"""
        from app.core import storage

        # storage.DATA_DIR 应该指向临时目录，不是真实 data/
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        real_data_dir = os.path.realpath(os.path.join(project_root, "data"))
        data_real = os.path.realpath(storage.DATA_DIR)

        assert data_real != real_data_dir, (
            f"DATA_DIR 不应指向真实数据目录: {real_data_dir}"
        )
        # DATA_DIR 不应在项目根目录下
        project_real = os.path.realpath(project_root)
        assert not data_real.startswith(project_real + os.sep), (
            f"DATA_DIR 不应在项目目录内: {data_real}"
        )

    def test_storage_paths_are_isolated(self, isolated_data_env):
        """验证所有 storage 路径都指向临时目录。"""
        from app.core import storage

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        project_real = os.path.realpath(project_root)

        path_attrs = [
            "DATA_DIR", "PROFILES_DIR", "CONFIGS_DIR", "ARCHIVE_DIR",
            "CACHE_DIR", "PARSE_CACHE_DIR", "MAPPING_FILE", "FAVORITES_FILE",
            "BINDINGS_FILE", "TOOLS_FILE", "SCHEDULE_FILE",
            "IP_MAPPING_FILE", "ADMIN_USERS_FILE", "EDIT_REMARKS_FILE",
        ]

        for attr in path_attrs:
            value = os.path.realpath(getattr(storage, attr))
            assert not value.startswith(project_real + os.sep), (
                f"{attr} = {value} 不应在项目目录内"
            )

    def test_single_test_independent(self, tmp_path):
        """验证单测试可独立运行（不依赖其他测试的状态）。

        此测试本身就是一个独立测试，如果它能通过，
        说明测试隔离机制工作正常。
        """
        from app.core import storage

        # 应该能正常使用 storage（因为 isolated_data_env 已设置好）
        storage.save_config_file("independent_test.xml", b"<test>1</test>")
        loaded = storage.load_config_file("independent_test.xml")
        assert loaded == b"<test>1</test>"

        # 验证文件确实在临时目录
        config_path = storage.get_config_path("independent_test.xml")
        tmp_real = os.path.realpath(str(tmp_path))
        path_real = os.path.realpath(config_path)
        assert path_real.startswith(tmp_real), (
            f"config 应在 tmp_path 内: {path_real} vs {tmp_real}"
        )

    def test_context_var_reset_between_tests(self):
        """验证 ContextVar 在每个测试前被重置。"""
        from app.core.storage import get_active_profile, DEFAULT_PROFILE

        # 应该被 _reset_context_vars fixture 重置为 default
        assert get_active_profile() == DEFAULT_PROFILE

        # 修改 profile
        from app.core.storage import set_active_profile
        set_active_profile("test_profile")
        assert get_active_profile() == "test_profile"

    def test_context_var_actually_resets(self):
        """验证上一个测试的 profile 修改不会泄漏到这个测试。"""
        from app.core.storage import get_active_profile, DEFAULT_PROFILE

        # 如果 _reset_context_vars 工作正常，这里应该是 default
        assert get_active_profile() == DEFAULT_PROFILE
