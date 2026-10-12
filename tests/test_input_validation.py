"""输入边界验证测试 (T02 / INH-613)

覆盖:
- validate_filename: 路径穿越、UNC、盘符、Windows 保留名、ADS、Unicode
- resolve_within: 正常路径、..  越界、symlink 越界
- validate_download_url: 方案、凭据、SSRF、allowed_hosts 覆盖
- sanitize_url_for_log: 凭据脱敏、敏感 query 脱敏
"""

import pytest
from pathlib import Path

from app.utils.helpers import validate_filename, resolve_within
from app.core.downloader import validate_download_url, sanitize_url_for_log


# ---------------------------------------------------------------------------
# validate_filename
# ---------------------------------------------------------------------------

class TestValidateFilename:
    """文件名安全验证。"""

    # -- 合法文件名 --------------------------------------------------------

    def test_normal_filename(self):
        assert validate_filename("config.xml") == "config.xml"

    def test_unicode_filename(self):
        assert validate_filename("配置.json") == "配置.json"

    def test_japanese_filename(self):
        assert validate_filename("設定ファイル.yaml") == "設定ファイル.yaml"

    def test_filename_with_spaces(self):
        assert validate_filename("my config file.xml") == "my config file.xml"

    def test_filename_with_hyphens(self):
        assert validate_filename("my-config-v2.xml") == "my-config-v2.xml"

    # -- 拒绝空 / 特殊 -----------------------------------------------------

    def test_empty_string(self):
        with pytest.raises(ValueError):
            validate_filename("")

    def test_dot(self):
        with pytest.raises(ValueError):
            validate_filename(".")

    def test_dotdot(self):
        with pytest.raises(ValueError):
            validate_filename("..")

    # -- 路径穿越 -----------------------------------------------------------

    def test_path_traversal_forward_slash(self):
        with pytest.raises(ValueError):
            validate_filename("../secret.xml")

    def test_path_traversal_backslash(self):
        with pytest.raises(ValueError):
            validate_filename(r"path\file.xml")

    def test_absolute_path_unix(self):
        with pytest.raises(ValueError):
            validate_filename("/etc/passwd")

    def test_windows_drive_letter(self):
        with pytest.raises(ValueError):
            validate_filename("C:file.xml")

    def test_unc_path(self):
        with pytest.raises(ValueError):
            validate_filename(r"\\server\share")

    # -- Windows 保留名 -----------------------------------------------------

    def test_nul_device(self):
        with pytest.raises(ValueError):
            validate_filename("NUL")

    def test_con_device(self):
        with pytest.raises(ValueError):
            validate_filename("CON")

    def test_aux_device(self):
        with pytest.raises(ValueError):
            validate_filename("AUX")

    def test_com1_device(self):
        with pytest.raises(ValueError):
            validate_filename("COM1")

    def test_lpt1_device(self):
        with pytest.raises(ValueError):
            validate_filename("LPT1")

    def test_reserved_name_with_extension(self):
        """NUL.txt 等带扩展名的保留名也应被拒绝。"""
        with pytest.raises(ValueError):
            validate_filename("NUL.txt")

    # -- ADS / 尾部点号 -----------------------------------------------------

    def test_ads_colon(self):
        with pytest.raises(ValueError):
            validate_filename("file:stream")

    def test_trailing_dot(self):
        with pytest.raises(ValueError):
            validate_filename("file.")

    # -- NUL 字节 -----------------------------------------------------------

    def test_nul_byte(self):
        with pytest.raises(ValueError):
            validate_filename("file\x00.xml")


# ---------------------------------------------------------------------------
# resolve_within
# ---------------------------------------------------------------------------

class TestResolveWithin:
    """路径边界验证。"""

    def test_normal_path(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        result = resolve_within(root, "config.xml")
        assert result == (root / "config.xml").resolve()

    def test_nested_relative(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        sub = root / "sub"
        sub.mkdir()
        result = resolve_within(root, "sub/config.xml")
        assert result == (root / "sub" / "config.xml").resolve()

    def test_path_escape_dotdot(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        with pytest.raises(ValueError, match="escape"):
            resolve_within(root, "../secret.xml")

    def test_path_escape_complex(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        with pytest.raises(ValueError, match="escape"):
            resolve_within(root, "sub/../../secret.xml")

    def test_symlink_escape(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        link = root / "link"
        link.symlink_to(outside)
        with pytest.raises(ValueError, match="escape"):
            resolve_within(root, "link/secret.xml")

    def test_symlink_to_file_within(self, tmp_path):
        """指向 root 内部的 symlink 应被允许。"""
        root = tmp_path / "data"
        root.mkdir()
        real_file = root / "real.xml"
        real_file.write_text("<xml/>")
        link = root / "link.xml"
        link.symlink_to(real_file)
        result = resolve_within(root, "link.xml")
        assert result.exists()


# ---------------------------------------------------------------------------
# validate_download_url
# ---------------------------------------------------------------------------

class TestValidateDownloadUrl:
    """下载 URL 安全验证。"""

    # -- 合法 URL ----------------------------------------------------------

    def test_valid_http(self):
        assert validate_download_url("http://example.com/file.xml") is True

    def test_valid_https(self):
        assert validate_download_url("https://example.com/file.xml") is True

    def test_valid_https_with_path(self):
        assert validate_download_url("https://cdn.example.com/path/to/file.xml") is True

    # -- 拒绝非法方案 -------------------------------------------------------

    def test_ftp_rejected(self):
        with pytest.raises(ValueError, match="scheme"):
            validate_download_url("ftp://example.com/file.xml")

    def test_file_scheme_rejected(self):
        with pytest.raises(ValueError, match="scheme"):
            validate_download_url("file:///etc/passwd")

    def test_data_scheme_rejected(self):
        with pytest.raises(ValueError, match="scheme"):
            validate_download_url("data:text/plain,hello")

    def test_javascript_scheme_rejected(self):
        with pytest.raises(ValueError, match="scheme"):
            validate_download_url("javascript:alert(1)")

    # -- 拒绝凭据 -----------------------------------------------------------

    def test_credentials_rejected(self):
        with pytest.raises(ValueError, match="credentials"):
            validate_download_url("http://user:pass@example.com/file.xml")

    def test_username_only_rejected(self):
        with pytest.raises(ValueError, match="credentials"):
            validate_download_url("http://user@example.com/file.xml")

    # -- SSRF 防护 ----------------------------------------------------------

    def test_loopback_ipv4_rejected(self):
        with pytest.raises(ValueError, match="Blocked"):
            validate_download_url("http://127.0.0.1/file.xml")

    def test_loopback_ipv6_rejected(self):
        with pytest.raises(ValueError, match="Blocked"):
            validate_download_url("http://[::1]/file.xml")

    def test_localhost_rejected(self):
        with pytest.raises(ValueError, match="Blocked"):
            validate_download_url("http://localhost/file.xml")

    def test_aws_metadata_rejected(self):
        with pytest.raises(ValueError, match="Blocked"):
            validate_download_url("http://169.254.169.254/metadata")

    def test_gcp_metadata_rejected(self):
        with pytest.raises(ValueError, match="Blocked"):
            validate_download_url("http://metadata.google.internal/metadata")

    def test_aliyun_metadata_rejected(self):
        with pytest.raises(ValueError, match="Blocked"):
            validate_download_url("http://100.100.100.200/metadata")

    # -- allowed_hosts 覆盖 -------------------------------------------------

    def test_allowed_host_override_loopback(self):
        assert validate_download_url(
            "http://127.0.0.1/file.xml",
            allowed_hosts={'127.0.0.1'},
        ) is True

    def test_allowed_host_override_metadata(self):
        assert validate_download_url(
            "http://169.254.169.254/metadata",
            allowed_hosts={'169.254.169.254'},
        ) is True

    def test_non_blocked_host_no_override_needed(self):
        assert validate_download_url(
            "http://example.com/file.xml",
            allowed_hosts=set(),
        ) is True


# ---------------------------------------------------------------------------
# sanitize_url_for_log
# ---------------------------------------------------------------------------

class TestSanitizeUrlForLog:
    """URL 日志脱敏。"""

    def test_plain_url_unchanged(self):
        url = "https://example.com/path/file.xml"
        assert sanitize_url_for_log(url) == url

    def test_remove_userinfo(self):
        url = "http://user:pass@example.com/path"
        sanitized = sanitize_url_for_log(url)
        assert "user" not in sanitized
        assert "pass" not in sanitized
        assert "example.com" in sanitized

    def test_redact_token_query(self):
        url = "https://api.example.com/file?token=secret123&format=xml"
        sanitized = sanitize_url_for_log(url)
        assert "secret123" not in sanitized
        assert "[REDACTED]" in sanitized

    def test_redact_apikey_query(self):
        url = "https://api.example.com/file?apikey=abc123"
        sanitized = sanitize_url_for_log(url)
        assert "abc123" not in sanitized
        assert "[REDACTED]" in sanitized

    def test_non_sensitive_query_preserved(self):
        url = "https://example.com/file?format=xml&version=2"
        sanitized = sanitize_url_for_log(url)
        assert "format=xml" in sanitized
        assert "version=2" in sanitized

    def test_port_preserved(self):
        url = "https://example.com:8443/path"
        sanitized = sanitize_url_for_log(url)
        assert ":8443" in sanitized

    def test_invalid_url_returns_placeholder(self):
        # urlparse 通常不会抛异常，但极端情况返回占位符
        result = sanitize_url_for_log("not a url at all \x00")
        assert isinstance(result, str)
