"""启动配置解析（INH-1865 / M0-08；规范 §3.6、T-04；C25、F28）。

用例只依赖自己设置的环境变量：`clean_env` 夹具会先清掉外部 `MCHECKER_*`。
"""

from __future__ import annotations

import os
import sys

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("MCHECKER_") or name == "NICEGUI_STORAGE_SECRET":
            monkeypatch.delenv(name, raising=False)
    yield


class TestDefaults:
    def test_defaults_match_spec_3_6(self):
        from app import config

        settings = config.load_settings([])

        assert settings.host == "0.0.0.0"
        assert settings.port == 50002
        assert settings.data_dir == os.path.join(config.REPO_ROOT, "data")
        assert settings.web_dir == os.path.join(config.REPO_ROOT, "app", "web")
        assert settings.trusted_proxies == ()
        assert settings.upload_max_bytes == 50 * 1024 * 1024
        assert settings.download_timeout_s == 30
        assert settings.download_retries == 3
        assert settings.download_allow == ()
        assert settings.temp_ttl_hours == 24
        assert settings.tab_limit == 15
        assert settings.max_workers == 4
        assert settings.log_level == "INFO"

    def test_nicegui_secret_is_not_read(self, monkeypatch):
        from app import config

        monkeypatch.setenv("NICEGUI_STORAGE_SECRET", "legacy-value")
        # 只设这个变量时结果必须与全默认完全一致
        assert config.load_settings([]) == config.Settings()

    def test_argv_defaults_to_sys_argv(self, monkeypatch):
        from app import config

        monkeypatch.setattr(sys, "argv", ["run.py", "--port", "50123"])
        assert config.load_settings().port == 50123


class TestEnv:
    def test_env_values(self, monkeypatch, tmp_path):
        from app import config

        data_dir = str(tmp_path / "custom-data")
        monkeypatch.setenv("MCHECKER_HOST", "127.0.0.1")
        monkeypatch.setenv("MCHECKER_PORT", "50110")
        monkeypatch.setenv("MCHECKER_DATA_DIR", data_dir)
        monkeypatch.setenv("MCHECKER_TRUSTED_PROXIES", "10.0.0.1, 10.0.0.2 ,, ")
        monkeypatch.setenv("MCHECKER_UPLOAD_MAX_MB", "10")
        monkeypatch.setenv("MCHECKER_DOWNLOAD_ALLOW", "example.com,10.0.0.0/8")
        monkeypatch.setenv("MCHECKER_LOG_LEVEL", "debug")

        settings = config.load_settings([])

        assert settings.host == "127.0.0.1"
        assert settings.port == 50110
        assert settings.data_dir == os.path.abspath(data_dir)
        assert settings.trusted_proxies == ("10.0.0.1", "10.0.0.2")
        assert settings.upload_max_bytes == 10 * 1024 * 1024
        assert settings.download_allow == ("example.com", "10.0.0.0/8")
        assert settings.log_level == "DEBUG"

    @pytest.mark.parametrize("value", ["abc", "0", "65536", "-1", "", "50.5", "  "])
    def test_invalid_port_fails_with_reason(self, monkeypatch, value):
        from app import config

        monkeypatch.setenv("MCHECKER_PORT", value)
        with pytest.raises(config.SettingsError) as excinfo:
            config.load_settings([])
        assert "MCHECKER_PORT" in str(excinfo.value)

    @pytest.mark.parametrize("value", ["abc", "0", "-3", ""])
    def test_invalid_upload_limit_fails_with_reason(self, monkeypatch, value):
        from app import config

        monkeypatch.setenv("MCHECKER_UPLOAD_MAX_MB", value)
        with pytest.raises(config.SettingsError) as excinfo:
            config.load_settings([])
        assert "MCHECKER_UPLOAD_MAX_MB" in str(excinfo.value)

    @pytest.mark.parametrize("value", ["verbose", "info!", "  "])
    def test_invalid_log_level_fails_with_reason(self, monkeypatch, value):
        from app import config

        monkeypatch.setenv("MCHECKER_LOG_LEVEL", value)
        with pytest.raises(config.SettingsError) as excinfo:
            config.load_settings([])
        assert "MCHECKER_LOG_LEVEL" in str(excinfo.value)

    def test_empty_data_dir_fails_with_reason(self, monkeypatch):
        from app import config

        monkeypatch.setenv("MCHECKER_DATA_DIR", "   ")
        with pytest.raises(config.SettingsError) as excinfo:
            config.load_settings([])
        assert "MCHECKER_DATA_DIR" in str(excinfo.value)

    def test_empty_proxy_and_allow_lists_are_allowed(self, monkeypatch):
        from app import config

        monkeypatch.setenv("MCHECKER_TRUSTED_PROXIES", "")
        monkeypatch.setenv("MCHECKER_DOWNLOAD_ALLOW", " , ")
        settings = config.load_settings([])
        assert settings.trusted_proxies == ()
        assert settings.download_allow == ()


class TestCli:
    def test_cli_overrides_env(self, monkeypatch, tmp_path):
        from app import config

        monkeypatch.setenv("MCHECKER_HOST", "10.0.0.9")
        monkeypatch.setenv("MCHECKER_PORT", "1")
        monkeypatch.setenv("MCHECKER_DATA_DIR", str(tmp_path / "from-env"))

        settings = config.load_settings(
            ["--host", "127.0.0.1", "--port", "50110", "--data-dir", str(tmp_path)]
        )

        assert settings.host == "127.0.0.1"
        assert settings.port == 50110
        assert settings.data_dir == os.path.abspath(str(tmp_path))

    @pytest.mark.parametrize("value", ["abc", "0", "70000"])
    def test_cli_invalid_port_fails_with_reason(self, value):
        from app import config

        with pytest.raises(config.SettingsError) as excinfo:
            config.load_settings(["--port", value])
        assert "--port" in str(excinfo.value)

    def test_cli_data_dir_must_not_be_empty(self):
        from app import config

        with pytest.raises(config.SettingsError) as excinfo:
            config.load_settings(["--data-dir", "  "])
        assert "--data-dir" in str(excinfo.value)

    def test_cli_data_dir_is_absolute(self, tmp_path):
        from app import config

        settings = config.load_settings(["--data-dir", "relative/path"])
        assert os.path.isabs(settings.data_dir)
        assert settings.data_dir.endswith(os.path.join("relative", "path"))
