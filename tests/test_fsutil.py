"""fsutil 测试：原子写、损坏 JSON 保护（M0-03）与文件名/路径校验（M0-04）。"""

from __future__ import annotations

import hashlib
import os

import pytest

from app.core import fsutil
from app.core.errors import CORRUPT_DATA, FORBIDDEN, INVALID_INPUT, AppError, CorruptDataError


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


# ---------------------------------------------------------------------------
# validate_filename（M0-04）
# ---------------------------------------------------------------------------


class TestValidateFilenameRejects:
    @pytest.mark.parametrize(
        "name",
        [
            "../a",  # 路径分隔符 + 上级目录
            "a\\b",  # Windows 分隔符
            "a/b",  # POSIX 分隔符
            "C:x",  # 盘符冒号
            "CON.xml",  # Windows 保留名（带扩展名）
            "con",  # Windows 保留名（小写）
            "COM1",  # 保留名 COM1-9
            "LPT9.log",  # 保留名 LPT1-9
            "NUL",
            "a.",  # 以点结尾
            "",  # 空串
            "   ",  # 纯空白
            "a" * 181,  # 超长
        ],
    )
    def test_rejected(self, name):
        with pytest.raises(AppError) as exc:
            fsutil.validate_filename(name)
        assert exc.value.code == INVALID_INPUT


class TestValidateFilenameAccepts:
    @pytest.mark.parametrize("name", ["中文配置.xml", "a.xml", "my-config_1.2.json", "a b.xml"])
    def test_accepted(self, name):
        assert fsutil.validate_filename(name) == name

    def test_strips_surrounding_whitespace(self):
        assert fsutil.validate_filename("  a.xml  ") == "a.xml"

    def test_boundary_length_180_accepted(self):
        name = "a" * 176 + ".xml"  # 180 字符
        assert len(name) == 180
        assert fsutil.validate_filename(name) == name


class TestValidateFilenameEdgeCases:
    def test_none_rejected(self):
        with pytest.raises(AppError):
            fsutil.validate_filename(None)

    def test_non_string_rejected(self):
        with pytest.raises(AppError):
            fsutil.validate_filename(123)

    @pytest.mark.parametrize("name", [".", ".."])
    def test_dot_and_dotdot_rejected(self, name):
        with pytest.raises(AppError):
            fsutil.validate_filename(name)

    @pytest.mark.parametrize("name", ["a\x00.xml", "a\x1f.xml", "a\x7f.xml"])
    def test_control_chars_rejected(self, name):
        with pytest.raises(AppError):
            fsutil.validate_filename(name)

    def test_reserved_name_with_whitespace_is_still_rejected(self):
        with pytest.raises(AppError):
            fsutil.validate_filename("  CON.txt  ")


# ---------------------------------------------------------------------------
# resolve_within（M0-04）
# ---------------------------------------------------------------------------


class TestResolveWithin:
    def test_returns_joined_path(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        got = fsutil.resolve_within(str(root), "configs", "a.xml")
        assert got == os.path.realpath(str(root / "configs" / "a.xml"))

    def test_root_itself_allowed(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        assert fsutil.resolve_within(str(root)) == os.path.realpath(str(root))

    def test_parent_escape_rejected(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(AppError) as exc:
            fsutil.resolve_within(str(root), "../outside.xml")
        assert exc.value.code == FORBIDDEN

    def test_symlink_directory_escape_rejected(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(str(outside), str(root / "escape"))
        with pytest.raises(AppError) as exc:
            fsutil.resolve_within(str(root), "escape")
        assert exc.value.code == FORBIDDEN

    def test_symlink_file_escape_rejected(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        secret = tmp_path / "secret.xml"
        secret.write_bytes(b"<a/>")
        os.symlink(str(secret), str(root / "linked.xml"))
        with pytest.raises(AppError) as exc:
            fsutil.resolve_within(str(root), "linked.xml")
        assert exc.value.code == FORBIDDEN

    def test_symlink_inside_root_allowed(self, tmp_path):
        root = tmp_path / "root"
        (root / "real").mkdir(parents=True)
        (root / "real" / "a.xml").write_bytes(b"<a/>")
        os.symlink(str(root / "real"), str(root / "alias"))
        got = fsutil.resolve_within(str(root), "alias", "a.xml")
        assert got == os.path.realpath(str(root / "real" / "a.xml"))
