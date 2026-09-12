"""统一更新管线测试 (T09 / INH-621)

测试 UpdatePipeline 的核心行为：
- 正常更新成功
- HTML error page 不覆盖
- valid-but-wrong XML/JSON root 不自动覆盖
- same content 不重复 archive
- manual + schedule 同时触发不会重复 commit (防重入)
- structural fingerprint 校验
"""

import json
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core import storage, downloader
from app.core.scheduler import (
    UpdatePipeline,
    update_pipeline,
    get_update_lock,
    _looks_like_html_error,
    _detect_format,
    _extract_root_tag,
    _extract_json_root_type,
    run_single_update,
    run_single_record_update,
)
from app.core.parser import compute_content_hash


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

SAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <server host="localhost" port="8080"/>
    <database name="mydb"/>
</configuration>
"""

SAMPLE_XML_V2 = b"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <server host="localhost" port="9090"/>
    <database name="mydb"/>
    <cache enabled="true"/>
</configuration>
"""

WRONG_ROOT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<settings>
    <item name="foo"/>
</settings>
"""

SAMPLE_JSON = b'{"config": {"name": "test", "value": 42}}'

SAMPLE_JSON_V2 = b'{"config": {"name": "test", "value": 99, "extra": true}}'

WRONG_ROOT_JSON = b'[1, 2, 3]'

HTML_ERROR_PAGE = b"""<!DOCTYPE html>
<html>
<head><title>404 Not Found</title></head>
<body><h1>404 - File not found</h1></body>
</html>
"""


def _make_staging_writer(content: bytes):
    """创建一个 mock safe_download 函数，将 content 写入 dest path"""
    def _mock_safe_download(url, dest, **kwargs):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    return _mock_safe_download


def _setup_mapping(name: str, url: str, record_url: str = ""):
    """设置 config mapping"""
    mapping = [{"name": name, "url": url, "record_url": record_url}]
    storage.save_config_mapping(mapping)
    return mapping


def _setup_existing_config(name: str, content: bytes):
    """预先放置一个配置文件"""
    filepath = storage.get_config_path(name)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(content)
    return filepath


# ---------------------------------------------------------------------------
# 内容验证辅助函数测试
# ---------------------------------------------------------------------------

class TestContentValidation:
    def test_html_error_detection(self):
        assert _looks_like_html_error(HTML_ERROR_PAGE) is True
        assert _looks_like_html_error(b"  <!DOCTYPE html>stuff") is True
        assert _looks_like_html_error(b"<html>stuff") is True
        assert _looks_like_html_error(SAMPLE_XML) is False
        assert _looks_like_html_error(SAMPLE_JSON) is False

    def test_format_detection(self):
        assert _detect_format("config.xml", SAMPLE_XML) == "xml"
        assert _detect_format("config.json", SAMPLE_JSON) == "json"
        assert _detect_format("unknown", SAMPLE_XML) == "xml"
        assert _detect_format("unknown", SAMPLE_JSON) == "json"
        assert _detect_format("unknown", b"plain text") == "unknown"

    def test_xml_root_tag_extraction(self):
        assert _extract_root_tag(SAMPLE_XML) == "configuration"
        assert _extract_root_tag(WRONG_ROOT_XML) == "settings"
        assert _extract_root_tag(b"not xml") is None

    def test_json_root_type_extraction(self):
        assert _extract_json_root_type(SAMPLE_JSON) == "object"
        assert _extract_json_root_type(WRONG_ROOT_JSON) == "array"
        assert _extract_json_root_type(b"not json") is None


# ---------------------------------------------------------------------------
# UpdatePipeline 核心测试
# ---------------------------------------------------------------------------

class TestUpdatePipelineSuccess:
    """正常更新流程"""

    def test_valid_update_success(self, tmp_path, monkeypatch):
        """正常 XML 更新成功"""
        name = "config.xml"
        url = "https://example.com/config.xml"

        _setup_mapping(name, url)
        monkeypatch.setattr(
            downloader, "safe_download",
            _make_staging_writer(SAMPLE_XML),
        )

        pipeline = UpdatePipeline()
        result = pipeline.execute(url, name, "default")

        assert result["status"] == "success"
        assert result["new_hash"] == compute_content_hash(SAMPLE_XML)
        assert result["size"] == len(SAMPLE_XML)

        # 验证文件已写入
        current = storage.load_config_file(name)
        assert current == SAMPLE_XML

    def test_valid_update_json(self, tmp_path, monkeypatch):
        """正常 JSON 更新成功"""
        name = "config.json"
        url = "https://example.com/config.json"

        _setup_mapping(name, url)
        monkeypatch.setattr(
            downloader, "safe_download",
            _make_staging_writer(SAMPLE_JSON),
        )

        pipeline = UpdatePipeline()
        result = pipeline.execute(url, name, "default")

        assert result["status"] == "success"
        current = storage.load_config_file(name)
        assert current == SAMPLE_JSON


class TestUpdatePipelineRejection:
    """拒绝不合法更新"""

    def test_html_error_not_overwrite(self, monkeypatch):
        """200 HTML error 不覆盖已有文件"""
        name = "config.xml"
        url = "https://example.com/config.xml"

        _setup_mapping(name, url)
        _setup_existing_config(name, SAMPLE_XML)
        original_hash = compute_content_hash(SAMPLE_XML)

        monkeypatch.setattr(
            downloader, "safe_download",
            _make_staging_writer(HTML_ERROR_PAGE),
        )

        pipeline = UpdatePipeline()
        result = pipeline.execute(url, name, "default")

        assert result["status"] == "rejected"
        assert "HTML error" in result["errors"][0]

        # 原文件未被覆盖
        current = storage.load_config_file(name)
        assert compute_content_hash(current) == original_hash

    def test_empty_content_rejected(self, monkeypatch):
        """空内容不覆盖"""
        name = "config.xml"
        url = "https://example.com/config.xml"

        _setup_mapping(name, url)
        _setup_existing_config(name, SAMPLE_XML)

        monkeypatch.setattr(
            downloader, "safe_download",
            _make_staging_writer(b""),
        )

        pipeline = UpdatePipeline()
        result = pipeline.execute(url, name, "default")

        assert result["status"] == "rejected"

    def test_malformed_xml_rejected(self, monkeypatch):
        """malformed XML 不覆盖"""
        name = "config.xml"
        url = "https://example.com/config.xml"
        bad_xml = b"<configuration><unclosed>"

        _setup_mapping(name, url)
        _setup_existing_config(name, SAMPLE_XML)

        monkeypatch.setattr(
            downloader, "safe_download",
            _make_staging_writer(bad_xml),
        )

        pipeline = UpdatePipeline()
        result = pipeline.execute(url, name, "default")

        assert result["status"] == "rejected"

    def test_wrong_root_xml_scheduled_rejected(self, monkeypatch):
        """valid-but-wrong XML root 在定时任务中直接 reject"""
        name = "config.xml"
        url = "https://example.com/config.xml"

        _setup_mapping(name, url)
        _setup_existing_config(name, SAMPLE_XML)
        original_hash = compute_content_hash(SAMPLE_XML)

        monkeypatch.setattr(
            downloader, "safe_download",
            _make_staging_writer(WRONG_ROOT_XML),
        )

        pipeline = UpdatePipeline()
        result = pipeline.execute(url, name, "default", is_scheduled=True)

        assert result["status"] == "rejected"
        assert "fingerprint" in result["errors"][0].lower()

        # 原文件未被覆盖
        current = storage.load_config_file(name)
        assert compute_content_hash(current) == original_hash

    def test_wrong_root_xml_manual_needs_confirmation(self, monkeypatch):
        """valid-but-wrong XML root 在手动操作中返回 needs_confirmation"""
        name = "config.xml"
        url = "https://example.com/config.xml"

        _setup_mapping(name, url)
        _setup_existing_config(name, SAMPLE_XML)

        monkeypatch.setattr(
            downloader, "safe_download",
            _make_staging_writer(WRONG_ROOT_XML),
        )

        pipeline = UpdatePipeline()
        result = pipeline.execute(url, name, "default", is_scheduled=False)

        assert result["status"] == "needs_confirmation"
        assert "fingerprint_diff" in result

        # 原文件未被覆盖
        current = storage.load_config_file(name)
        assert current == SAMPLE_XML

    def test_wrong_root_json_scheduled_rejected(self, monkeypatch):
        """valid-but-wrong JSON root type 在定时任务中 reject"""
        name = "config.json"
        url = "https://example.com/config.json"

        _setup_mapping(name, url)
        _setup_existing_config(name, SAMPLE_JSON)

        monkeypatch.setattr(
            downloader, "safe_download",
            _make_staging_writer(WRONG_ROOT_JSON),
        )

        pipeline = UpdatePipeline()
        result = pipeline.execute(url, name, "default", is_scheduled=True)

        assert result["status"] == "rejected"
        assert "fingerprint" in result["errors"][0].lower()


class TestSameContentNoDuplicate:
    """相同内容不重复归档"""

    def test_same_content_no_duplicate_archive(self, monkeypatch):
        """same content 不重复 archive"""
        name = "config.xml"
        url = "https://example.com/config.xml"

        _setup_mapping(name, url)
        _setup_existing_config(name, SAMPLE_XML)

        # 记录归档目录初始状态
        archive_dir = storage.get_archive_dir(name)
        initial_archives = set(os.listdir(archive_dir)) if os.path.exists(archive_dir) else set()

        monkeypatch.setattr(
            downloader, "safe_download",
            _make_staging_writer(SAMPLE_XML),
        )

        pipeline = UpdatePipeline()
        result = pipeline.execute(url, name, "default")

        assert result["status"] == "unchanged"
        assert result["new_hash"] == compute_content_hash(SAMPLE_XML)

        # 归档目录没有新文件
        current_archives = set(os.listdir(archive_dir)) if os.path.exists(archive_dir) else set()
        assert current_archives == initial_archives


class TestAntiReentry:
    """防重入测试"""

    def test_manual_plus_schedule_no_duplicate(self, monkeypatch):
        """manual + schedule 同时触发不会重复 commit"""
        name = "config.xml"
        url = "https://example.com/config.xml"

        _setup_mapping(name, url)

        call_count = {"n": 0}
        original_write = _make_staging_writer(SAMPLE_XML)

        def counting_download(url_arg, dest, **kwargs):
            call_count["n"] += 1
            original_write(url_arg, dest, **kwargs)

        monkeypatch.setattr(downloader, "safe_download", counting_download)

        pipeline = UpdatePipeline()
        results = []
        errors = []

        def run_update(is_scheduled):
            try:
                r = pipeline.execute(url, name, "default", is_scheduled=is_scheduled)
                results.append(r)
            except Exception as e:
                errors.append(e)

        # 同时启动两个线程
        t1 = threading.Thread(target=run_update, args=(False,))
        t2 = threading.Thread(target=run_update, args=(True,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Unexpected errors: {errors}"
        assert len(results) == 2

        # 一个成功，一个 busy
        statuses = sorted(r["status"] for r in results)
        assert "busy" in statuses
        assert "success" in statuses

        # 只下载了一次
        assert call_count["n"] == 1


class TestStructuralFingerprint:
    """结构指纹校验"""

    def test_fingerprint_new_file_passes(self, monkeypatch):
        """新文件（无 current）fingerprint 直接通过"""
        name = "new_config.xml"
        url = "https://example.com/new.xml"

        _setup_mapping(name, url)

        monkeypatch.setattr(
            downloader, "safe_download",
            _make_staging_writer(SAMPLE_XML),
        )

        pipeline = UpdatePipeline()
        result = pipeline.execute(url, name, "default")

        assert result["status"] == "success"

    def test_fingerprint_same_format_passes(self, monkeypatch):
        """同格式不同内容通过 fingerprint"""
        name = "config.xml"
        url = "https://example.com/config.xml"

        _setup_mapping(name, url)
        _setup_existing_config(name, SAMPLE_XML)

        monkeypatch.setattr(
            downloader, "safe_download",
            _make_staging_writer(SAMPLE_XML_V2),
        )

        pipeline = UpdatePipeline()
        result = pipeline.execute(url, name, "default")

        assert result["status"] == "success"

    def test_fingerprint_format_change_rejected(self, monkeypatch):
        """格式变化（xml -> json）被拦截（parse 或 fingerprint 均可）"""
        name = "config.xml"
        url = "https://example.com/config.xml"

        _setup_mapping(name, url)
        _setup_existing_config(name, SAMPLE_XML)

        monkeypatch.setattr(
            downloader, "safe_download",
            _make_staging_writer(SAMPLE_JSON),
        )

        pipeline = UpdatePipeline()
        result = pipeline.execute(url, name, "default", is_scheduled=True)

        # 格式变化要么被 parse validation 拦截（JSON 无法作为 XML 解析），
        # 要么被 fingerprint 拦截
        assert result["status"] == "rejected"
        error_msg = result["errors"][0].lower()
        assert any(keyword in error_msg for keyword in [
            "format changed", "fingerprint", "parse validation"
        ])


class TestRecordUpdate:
    """修改记录更新测试"""

    def test_record_update_success(self, monkeypatch):
        """修改记录正常更新"""
        name = "config.xml"
        record_url = "https://example.com/record.txt"
        record_content = b"2026-01-01: Changed port from 8080 to 9090"

        _setup_mapping(name, "https://example.com/config.xml", record_url=record_url)

        monkeypatch.setattr(
            downloader, "safe_download",
            _make_staging_writer(record_content),
        )

        pipeline = UpdatePipeline()
        result = pipeline.execute(record_url, name, "default", is_record=True)

        assert result["status"] == "success"
        assert result["size"] == len(record_content)


class TestRunSingleUpdateIntegration:
    """run_single_update / run_single_record_update 集成测试"""

    def test_run_single_update_success(self, monkeypatch):
        """通过 run_single_update 接口正常更新"""
        name = "config.xml"
        url = "https://example.com/config.xml"

        _setup_mapping(name, url)

        monkeypatch.setattr(
            downloader, "safe_download",
            _make_staging_writer(SAMPLE_XML),
        )

        result = run_single_update(name, "default")
        assert result["status"] == "success"

    def test_run_single_update_no_mapping(self):
        """不存在的 mapping 返回 not_found"""
        result = run_single_update("nonexistent.xml", "default")
        assert result["status"] == "not_found"

    def test_run_single_update_no_url(self, monkeypatch):
        """mapping 无 url 返回 skipped"""
        name = "config.xml"
        _setup_mapping(name, "")

        result = run_single_update(name, "default")
        assert result["status"] == "skipped"

    def test_run_single_record_update_success(self, monkeypatch):
        """通过 run_single_record_update 接口正常更新"""
        name = "config.xml"
        record_url = "https://example.com/record.txt"
        record_content = b"record content"

        _setup_mapping(name, "https://example.com/config.xml", record_url=record_url)

        monkeypatch.setattr(
            downloader, "safe_download",
            _make_staging_writer(record_content),
        )

        result = run_single_record_update(name, "default")
        assert result["status"] == "success"


class TestDownloadFailure:
    """下载失败不覆盖"""

    def test_download_failure_no_overwrite(self, monkeypatch):
        """下载失败时已有文件不被覆盖"""
        name = "config.xml"
        url = "https://example.com/config.xml"

        _setup_mapping(name, url)
        _setup_existing_config(name, SAMPLE_XML)
        original_hash = compute_content_hash(SAMPLE_XML)

        def failing_download(url_arg, dest, **kwargs):
            raise ConnectionError("Network unreachable")

        monkeypatch.setattr(downloader, "safe_download", failing_download)

        pipeline = UpdatePipeline()
        result = pipeline.execute(url, name, "default")

        assert result["status"] == "download_failed"

        # 原文件未被修改
        current = storage.load_config_file(name)
        assert compute_content_hash(current) == original_hash
