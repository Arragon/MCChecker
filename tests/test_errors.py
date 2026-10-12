"""统一错误码与日志脱敏测试

覆盖：
- errors.py: 错误码完整性、AppError 字符串格式、retryable 标记
- logging_config.py: 日志脱敏、统一日志格式
"""

import logging
from app.core.errors import (
    ErrorCode,
    AppError,
    InvalidInputError,
    ForbiddenError,
    NotFoundError,
    SourceChangedError,
    ReviewConflictError,
    TooLargeError,
    BusyError,
    DownloadFailedError,
    StorageFailureError,
    CorruptDataError,
)
from app.core.logging_config import sanitize_log_value, log_operation


# ── ErrorCode 完整性 ─────────────────────────────────────────

class TestErrorCodes:
    def test_all_codes_defined(self):
        """所有业务错误码均已定义"""
        expected = {
            "INVALID_INPUT", "FORBIDDEN", "NOT_FOUND", "SOURCE_CHANGED",
            "REVIEW_CONFLICT", "TOO_LARGE", "BUSY", "DOWNLOAD_FAILED",
            "STORAGE_FAILURE", "CORRUPT_DATA",
        }
        actual = {
            attr for attr in dir(ErrorCode)
            if not attr.startswith("_") and isinstance(getattr(ErrorCode, attr), str)
        }
        assert expected == actual, f"缺失错误码: {expected - actual}"

    def test_codes_are_strings(self):
        """所有错误码值为字符串"""
        for attr in ("INVALID_INPUT", "FORBIDDEN", "NOT_FOUND", "SOURCE_CHANGED",
                     "REVIEW_CONFLICT", "TOO_LARGE", "BUSY", "DOWNLOAD_FAILED",
                     "STORAGE_FAILURE", "CORRUPT_DATA"):
            assert isinstance(getattr(ErrorCode, attr), str)


# ── AppError 基类 ────────────────────────────────────────────

class TestAppError:
    def test_str_format(self):
        """AppError 字符串格式为 [CODE] message"""
        err = AppError(ErrorCode.INVALID_INPUT, "路径包含非法字符")
        assert str(err) == "[INVALID_INPUT] 路径包含非法字符"

    def test_default_not_retryable(self):
        """默认 retryable=False"""
        err = AppError(ErrorCode.NOT_FOUND, "文件不存在")
        assert err.retryable is False

    def test_context_stored(self):
        """context 字段被正确保存"""
        err = AppError(ErrorCode.INVALID_INPUT, "bad", context={"path": "/x"})
        assert err.context == {"path": "/x"}

    def test_is_exception(self):
        """AppError 可以正常 raise/catch"""
        with pytest.raises(AppError) as exc_info:
            raise AppError(ErrorCode.BUSY, "系统繁忙")
        assert exc_info.value.code == ErrorCode.BUSY


import pytest


# ── 子类行为 ─────────────────────────────────────────────────

class TestErrorSubclasses:
    def test_invalid_input(self):
        err = InvalidInputError("路径越界", path="/etc/passwd")
        assert err.code == ErrorCode.INVALID_INPUT
        assert err.context == {"path": "/etc/passwd"}

    def test_forbidden(self):
        err = ForbiddenError("无写权限")
        assert err.code == ErrorCode.FORBIDDEN
        assert err.retryable is False

    def test_not_found(self):
        err = NotFoundError("profile 不存在", profile="unknown")
        assert err.code == ErrorCode.NOT_FOUND
        assert err.retryable is False

    def test_source_changed(self):
        err = SourceChangedError("文件已被修改")
        assert err.code == ErrorCode.SOURCE_CHANGED

    def test_review_conflict(self):
        err = ReviewConflictError("审阅状态冲突")
        assert err.code == ErrorCode.REVIEW_CONFLICT

    def test_too_large(self):
        err = TooLargeError("文件超出 10MB 限制", size=15_000_000)
        assert err.code == ErrorCode.TOO_LARGE
        assert err.context == {"size": 15_000_000}

    def test_busy_is_retryable(self):
        """BusyError 是唯一默认 retryable=True 的子类"""
        err = BusyError("操作正在进行")
        assert err.code == ErrorCode.BUSY
        assert err.retryable is True

    def test_download_failed(self):
        err = DownloadFailedError("网络超时")
        assert err.code == ErrorCode.DOWNLOAD_FAILED

    def test_storage_failure(self):
        err = StorageFailureError("磁盘写入失败")
        assert err.code == ErrorCode.STORAGE_FAILURE

    def test_corrupt_data(self):
        err = CorruptDataError("XML 解析失败")
        assert err.code == ErrorCode.CORRUPT_DATA

    def test_subclass_is_app_error(self):
        """所有子类都是 AppError 实例"""
        for cls in (InvalidInputError, ForbiddenError, NotFoundError,
                    SourceChangedError, ReviewConflictError, TooLargeError,
                    BusyError, DownloadFailedError, StorageFailureError,
                    CorruptDataError):
            err = cls("test")
            assert isinstance(err, AppError)
            assert isinstance(err, Exception)


# ── 日志脱敏 ─────────────────────────────────────────────────

class TestSanitizeLogValue:
    def test_sanitize_token(self):
        """token=xxx 被脱敏"""
        result = sanitize_log_value("token=abc123secret")
        assert "abc123secret" not in result
        assert "[REDACTED]" in result

    def test_sanitize_password(self):
        """password=xxx 被脱敏"""
        result = sanitize_log_value("password=hunter2")
        assert "hunter2" not in result

    def test_sanitize_api_key(self):
        """apikey=xxx 和 api_key=xxx 均被脱敏"""
        assert "[REDACTED]" in sanitize_log_value("apikey=xyz789")
        assert "[REDACTED]" in sanitize_log_value("api_key=xyz789")

    def test_sanitize_url_userinfo(self):
        """URL 中的 user:pass@host 被脱敏"""
        result = sanitize_log_value("http://admin:pw@internal.host/path")
        assert "admin:pw" not in result
        assert "[REDACTED]@" in result
        assert "internal.host/path" in result

    def test_sanitize_preserves_normal_value(self):
        """普通值不被修改"""
        assert sanitize_log_value("profile=hw3000") == "profile=hw3000"

    def test_sanitize_multiple_credentials(self):
        """多个敏感字段均被脱敏"""
        result = sanitize_log_value("token=abc&secret=xyz")
        assert "abc" not in result
        assert "xyz" not in result


# ── 统一日志格式 ─────────────────────────────────────────────

class TestLogOperation:
    def test_log_format_contains_fields(self, caplog):
        """log_operation 输出包含所有必要字段"""
        logger = logging.getLogger("test.op")
        with caplog.at_level(logging.INFO, logger="test.op"):
            log_operation(
                logger,
                operation_id="import",
                profile="hw3000",
                actor="admin",
                target="config.xml",
                result="ok",
                duration_ms=42.5,
            )
        msg = caplog.records[0].message
        assert "op=import" in msg
        assert "profile=hw3000" in msg
        assert "actor=admin" in msg
        assert "target=config.xml" in msg
        assert "result=ok" in msg
        assert "duration=42.5ms" in msg
        assert "error=none" in msg

    def test_log_with_error_code(self, caplog):
        """失败时记录 error_code"""
        logger = logging.getLogger("test.op_err")
        with caplog.at_level(logging.INFO, logger="test.op_err"):
            log_operation(
                logger,
                operation_id="update",
                profile="hw5000",
                actor="deployer",
                target="fw.json",
                result="error",
                duration_ms=120.0,
                error_code=ErrorCode.SOURCE_CHANGED,
            )
        msg = caplog.records[0].message
        assert f"error={ErrorCode.SOURCE_CHANGED}" in msg

    def test_log_sanitizes_sensitive_target(self, caplog):
        """target 中的敏感信息被脱敏"""
        logger = logging.getLogger("test.op_san")
        with caplog.at_level(logging.INFO, logger="test.op_san"):
            log_operation(
                logger,
                operation_id="download",
                profile="test",
                actor="user",
                target="http://admin:pw@host/file.xml",
                result="ok",
                duration_ms=10.0,
            )
        msg = caplog.records[0].message
        assert "admin:pw" not in msg
        assert "[REDACTED]@" in msg


# ── differ binding_snapshot 注入 ─────────────────────────────

class TestDifferBindingSnapshot:
    """验证 differ 接收 binding_snapshot 而非直接读 storage"""

    SAMPLE_JSON_V1 = b'{"timeout": 30, "host": "localhost"}'
    SAMPLE_JSON_V2 = b'{"timeout": 60, "host": "localhost", "debug": true}'

    def test_binding_snapshot_used(self):
        """传入 binding_snapshot 时，绑定项被正确标注"""
        from app.core.differ import compare_versions

        snapshot = {
            "v1.json:v1.json/timeout": "timeout_group",
            "v2.json:v2.json/timeout": "timeout_group",
        }
        result = compare_versions(
            self.SAMPLE_JSON_V1, self.SAMPLE_JSON_V2,
            "v1.json", "v2.json",
            use_bindings=True,
            binding_snapshot=snapshot,
        )
        struct = result["structural_diff"]
        assert struct["type"] == "structural"
        # timeout 应被标注为绑定项
        bound = struct["bound_items"]
        assert len(bound) > 0
        assert any("timeout" in b["path"] for b in bound)

    def test_no_storage_import_in_differ_module(self):
        """differ 模块顶层不 import storage"""
        import app.core.differ as differ_mod
        import inspect
        src = inspect.getsource(differ_mod)
        # 顶层不应有 from .storage import ...
        # 但函数内惰性 import 是允许的（向后兼容）
        top_level_lines = [
            line for line in src.splitlines()
            if line.startswith("from .storage") or line.startswith("import storage")
        ]
        assert top_level_lines == [], f"differ 顶层不应直接 import storage: {top_level_lines}"

    def test_binding_snapshot_none_falls_back(self, monkeypatch, tmp_path):
        """binding_snapshot=None 时退回 storage（向后兼容）"""
        from app.core import storage
        from app.core.differ import compare_versions

        monkeypatch.setattr(storage, "BINDINGS_FILE", str(tmp_path / "bindings.json"))
        storage.add_binding("test_group", [
            {"file": "v1.json", "path": "v1.json/timeout"},
            {"file": "v2.json", "path": "v2.json/timeout"},
        ])

        # 不传 binding_snapshot，应退回 storage 读取
        result = compare_versions(
            self.SAMPLE_JSON_V1, self.SAMPLE_JSON_V2,
            "v1.json", "v2.json",
            use_bindings=True,
        )
        struct = result["structural_diff"]
        assert len(struct["bound_items"]) > 0
