"""fsutil 测试：原子写与损坏 JSON 保护（M0-03）。

文件名与路径校验的用例见同文件的 M0-04 补充。
"""

from __future__ import annotations

import hashlib
import os

import pytest

from app.core import fsutil
from app.core.errors import CORRUPT_DATA, AppError, CorruptDataError


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _tmp_files(directory: str):
    return [n for n in os.listdir(directory) if n.startswith(".") and n.endswith(".tmp")]


# ---------------------------------------------------------------------------
# 原子写
# ---------------------------------------------------------------------------


class TestAtomicWriteBytes:
    def test_writes_content(self, tmp_path):
        target = tmp_path / "a.xml"
        fsutil.atomic_write_bytes(str(target), b"<a>1</a>")
        assert target.read_bytes() == b"<a>1</a>"

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / "a.xml"
        target.write_bytes(b"old")
        fsutil.atomic_write_bytes(str(target), b"new")
        assert target.read_bytes() == b"new"

    def test_creates_missing_parent(self, tmp_path):
        target = tmp_path / "sub" / "deep" / "a.xml"
        fsutil.atomic_write_bytes(str(target), b"x")
        assert target.read_bytes() == b"x"

    def test_failure_keeps_old_content_and_leaves_no_temp(self, tmp_path, monkeypatch):
        target = tmp_path / "a.xml"
        target.write_bytes(b"original")
        before = _sha(str(target))

        def boom(src, dst):
            raise RuntimeError("模拟写入中途失败")

        monkeypatch.setattr(fsutil.os, "replace", boom)
        with pytest.raises(RuntimeError):
            fsutil.atomic_write_bytes(str(target), b"corrupted")

        # 旧内容字节不变
        assert _sha(str(target)) == before
        assert target.read_bytes() == b"original"
        # 没有残留的临时文件
        assert _tmp_files(str(tmp_path)) == []

    def test_failure_on_json_write_leaves_no_temp(self, tmp_path, monkeypatch):
        target = tmp_path / "x.json"
        fsutil.atomic_write_json(str(target), {"a": 1})
        before = _sha(str(target))

        def boom(src, dst):
            raise OSError("no")

        monkeypatch.setattr(fsutil.os, "replace", boom)
        with pytest.raises(OSError):
            fsutil.atomic_write_json(str(target), {"a": 2})

        assert _sha(str(target)) == before
        assert _tmp_files(str(tmp_path)) == []

    def test_replace_retries_on_permission_error_then_succeeds(self, tmp_path, monkeypatch):
        target = tmp_path / "a.xml"
        target.write_bytes(b"old")
        calls = {"n": 0}
        real_replace = fsutil.os.replace

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("模拟 Windows 占用")
            return real_replace(src, dst)

        monkeypatch.setattr(fsutil.os, "replace", flaky)
        fsutil.atomic_write_bytes(str(target), b"new")
        assert calls["n"] == 3
        assert target.read_bytes() == b"new"


class TestAtomicWriteText:
    def test_roundtrip_utf8(self, tmp_path):
        target = tmp_path / "t.txt"
        fsutil.atomic_write_text(str(target), "中文内容")
        assert target.read_text(encoding="utf-8") == "中文内容"


class TestAtomicWriteJson:
    def test_format_matches_legacy_style(self, tmp_path):
        target = tmp_path / "d.json"
        fsutil.atomic_write_json(str(target), {"b": 1, "a": "中文"})
        raw = target.read_text(encoding="utf-8")
        # ensure_ascii=False（中文不转义）、indent=2、与旧实现一致不带尾随换行
        assert "中文" in raw
        assert raw.startswith("{\n  ")
        assert not raw.endswith("\n")


# ---------------------------------------------------------------------------
# read_json
# ---------------------------------------------------------------------------


class TestReadJson:
    def test_missing_returns_default(self, tmp_path):
        assert fsutil.read_json(str(tmp_path / "nope.json"), []) == []
        assert fsutil.read_json(str(tmp_path / "nope.json"), {"k": 1}) == {"k": 1}

    def test_missing_returns_deepcopy_of_default(self, tmp_path):
        default = {"items": [1]}
        first = fsutil.read_json(str(tmp_path / "nope.json"), default)
        first["items"].append(2)
        second = fsutil.read_json(str(tmp_path / "nope.json"), default)
        assert second == {"items": [1]}

    def test_valid_file(self, tmp_path):
        target = tmp_path / "d.json"
        fsutil.atomic_write_json(str(target), {"a": 1})
        assert fsutil.read_json(str(target), None) == {"a": 1}

    def test_corrupt_raises_corrupt_data_error(self, tmp_path):
        target = tmp_path / "d.json"
        target.write_text("{ not json", encoding="utf-8")
        with pytest.raises(CorruptDataError) as exc:
            fsutil.read_json(str(target), [])
        assert exc.value.code == CORRUPT_DATA
        assert exc.value.path == str(target)

    def test_empty_file_is_corrupt_not_empty_list(self, tmp_path):
        target = tmp_path / "d.json"
        target.write_text("", encoding="utf-8")
        with pytest.raises(CorruptDataError):
            fsutil.read_json(str(target), [])

    def test_invalid_utf8_is_corrupt(self, tmp_path):
        target = tmp_path / "d.json"
        target.write_bytes(b'{"a": "\xff\xfe"}')
        with pytest.raises(CorruptDataError):
            fsutil.read_json(str(target), [])

    def test_missing_and_corrupt_are_distinguishable(self, tmp_path):
        """缺失返回缺省值，损坏抛异常——两者结果必须不同。"""
        missing = fsutil.read_json(str(tmp_path / "nope.json"), "DEFAULT")
        assert missing == "DEFAULT"

        corrupt = tmp_path / "c.json"
        corrupt.write_text("[1,", encoding="utf-8")
        with pytest.raises(CorruptDataError):
            fsutil.read_json(str(corrupt), "DEFAULT")

    def test_corrupt_error_is_app_error(self, tmp_path):
        target = tmp_path / "d.json"
        target.write_text("oops", encoding="utf-8")
        with pytest.raises(AppError):
            fsutil.read_json(str(target), [])
