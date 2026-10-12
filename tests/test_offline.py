"""T12 可复现完全离线发布测试"""
import pytest
import os
import logging
from pathlib import Path


def _get_port() -> int:
    """从环境变量获取端口，默认 50001（与 run.py 保持一致）"""
    raw = os.getenv("MCHECKER_PORT")
    if not raw:
        return 50001
    try:
        port = int(str(raw).strip())
    except ValueError:
        return 50001
    if port < 1 or port > 65535:
        return 50001
    return port


class TestPortConfiguration:
    def test_default_port(self):
        """默认端口 50001"""
        os.environ.pop("MCHECKER_PORT", None)
        assert _get_port() == 50001

    def test_env_port(self):
        """环境变量端口生效"""
        os.environ["MCHECKER_PORT"] = "8080"
        try:
            assert _get_port() == 8080
        finally:
            os.environ.pop("MCHECKER_PORT", None)

    def test_invalid_port_fallback(self):
        """无效端口回退默认"""
        os.environ["MCHECKER_PORT"] = "invalid"
        try:
            assert _get_port() == 50001
        finally:
            os.environ.pop("MCHECKER_PORT", None)


class TestGitignore:
    def test_nicegui_ignored(self):
        """.nicegui/ 在 .gitignore"""
        gitignore = Path(".gitignore").read_text()
        assert ".nicegui/" in gitignore

    def test_pycache_ignored(self):
        """__pycache__/ 在 .gitignore"""
        gitignore = Path(".gitignore").read_text()
        assert "__pycache__/" in gitignore

    def test_pyc_ignored(self):
        """*.pyc 在 .gitignore"""
        gitignore = Path(".gitignore").read_text()
        assert "*.pyc" in gitignore or "*.py[cod]" in gitignore


class TestRequirements:
    def test_requirements_exist(self):
        """requirements.txt 存在"""
        assert Path("requirements.txt").exists()

    def test_nicegui_declared(self):
        """nicegui 在 requirements.txt"""
        reqs = Path("requirements.txt").read_text()
        assert "nicegui" in reqs

    def test_apscheduler_declared(self):
        """apscheduler 在 requirements.txt"""
        reqs = Path("requirements.txt").read_text()
        assert "apscheduler" in reqs
