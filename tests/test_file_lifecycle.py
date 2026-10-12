"""T08 - 文件生命周期测试：temp / current / archive / record

覆盖验收标准：
- 访客上传可完整查看
- 同名 temp 优先显示 temp
- session A 不能访问 session B temp
- temp expired 有明确错误
- cold cache archive 可查看
- profile 切换后旧 record link 不串
"""

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.file_lifecycle import (
    TEMP_MAX_CAPACITY,
    TEMP_TTL_SECONDS,
    TempStorage,
    create_file_ref,
    resolve_file_path,
)
from app.core.models import FileKind, FileRef


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def temp_storage(tmp_path):
    """创建指向 tmp_path 的 TempStorage 实例。"""
    return TempStorage(data_root=tmp_path)


@pytest.fixture()
def data_root(tmp_path):
    """返回已初始化的 data_root（含 profiles 子目录结构）。"""
    profiles_dir = tmp_path / "profiles" / "default" / "configs"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    archive_dir = tmp_path / "profiles" / "default" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    records_dir = tmp_path / "profiles" / "default" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# TempStorage：创建
# ---------------------------------------------------------------------------


class TestCreateTemp:
    def test_create_temp_returns_fileref(self, temp_storage):
        """创建临时文件返回正确的 FileRef"""
        ref = temp_storage.create_temp(
            content=b"<config/>",
            owner_session="session-A",
            original_name="test.xml",
        )
        assert ref.kind == FileKind.TEMP
        assert ref.profile_id == "temp"
        assert ref.name.startswith(ref.version_or_token)
        assert ref.name.endswith("test.xml")

    def test_create_temp_file_exists_on_disk(self, temp_storage):
        """创建后文件实际存在于 temp_dir"""
        ref = temp_storage.create_temp(
            content=b"hello",
            owner_session="session-A",
        )
        temp_path = temp_storage.temp_dir / ref.name
        assert temp_path.is_file()
        assert temp_path.read_bytes() == b"hello"

    def test_create_temp_meta_sidecar(self, temp_storage):
        """创建后元数据侧车文件存在且包含 owner_session"""
        import json

        ref = temp_storage.create_temp(
            content=b"data",
            owner_session="session-X",
        )
        meta_path = temp_storage.temp_dir / (ref.name + ".meta.json")
        assert meta_path.is_file()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["owner_session"] == "session-X"
        assert "created_at" in meta

    def test_create_temp_invalid_filename_falls_back(self, temp_storage):
        """非法原始文件名回退为 'upload'"""
        ref = temp_storage.create_temp(
            content=b"data",
            owner_session="session-A",
            original_name="../evil.xml",  # 非法
        )
        assert "upload" in ref.name
        assert ".." not in ref.name


# ---------------------------------------------------------------------------
# TempStorage：session 隔离
# ---------------------------------------------------------------------------


class TestSessionIsolation:
    def test_session_a_can_access_own_temp(self, temp_storage):
        """session A 可以访问自己创建的 temp"""
        ref = temp_storage.create_temp(
            content=b"secret",
            owner_session="session-A",
        )
        result = temp_storage.get_temp(ref, session="session-A")
        assert result is not None
        assert result.read_bytes() == b"secret"

    def test_session_b_cannot_access_session_a_temp(self, temp_storage):
        """session B 不能访问 session A 的 temp"""
        ref = temp_storage.create_temp(
            content=b"secret",
            owner_session="session-A",
        )
        result = temp_storage.get_temp(ref, session="session-B")
        assert result is None

    def test_get_temp_wrong_kind_raises(self, temp_storage):
        """get_temp 传入非 TEMP kind 时抛出 ValueError"""
        ref = FileRef(profile_id="default", kind=FileKind.CURRENT, name="x.xml")
        with pytest.raises(ValueError, match="Not a temp file"):
            temp_storage.get_temp(ref, session="session-A")


# ---------------------------------------------------------------------------
# TempStorage：TTL
# ---------------------------------------------------------------------------


class TestTTLExpiry:
    def test_fresh_temp_is_accessible(self, temp_storage):
        """未过期的 temp 可以正常访问"""
        ref = temp_storage.create_temp(
            content=b"fresh",
            owner_session="session-A",
        )
        result = temp_storage.get_temp(ref, session="session-A")
        assert result is not None

    def test_expired_temp_returns_none(self, temp_storage, tmp_path):
        """过期的 temp 返回 None 并删除文件"""
        ref = temp_storage.create_temp(
            content=b"old",
            owner_session="session-A",
        )
        temp_file = temp_storage.temp_dir / ref.name

        # 将 mtime 设置为 TTL 之前
        old_time = time.time() - TEMP_TTL_SECONDS - 10
        os.utime(str(temp_file), (old_time, old_time))

        result = temp_storage.get_temp(ref, session="session-A")
        assert result is None
        # 文件应已被删除
        assert not temp_file.exists()

    def test_expired_temp_not_fallback_to_persistent(self, data_root, tmp_path):
        """过期的 temp 不回退到同名 persistent 文件

        验收标准：temp expired 有明确错误（返回 None），
        即使 persistent 目录存在同名文件也不返回。
        """
        # 在 persistent configs 目录放置同名文件
        persistent_file = data_root / "profiles" / "default" / "configs" / "shared.xml"
        persistent_file.write_text("<persistent/>", encoding="utf-8")

        # 创建 TempStorage 并写入同名 temp 文件
        ts = TempStorage(data_root=data_root)
        ref = ts.create_temp(
            content=b"<temp/>",
            owner_session="session-A",
            original_name="shared.xml",
        )
        # 让 temp 文件过期
        temp_file = ts.temp_dir / ref.name
        old_time = time.time() - TEMP_TTL_SECONDS - 10
        os.utime(str(temp_file), (old_time, old_time))

        # get_temp 应返回 None，不回退到 persistent
        result = ts.get_temp(ref, session="session-A")
        assert result is None

        # resolve_file_path 对 TEMP kind 也只查 temp 目录
        resolved = resolve_file_path(ref, data_root, profile_id="default")
        assert resolved is None  # temp 已过期，不回退


# ---------------------------------------------------------------------------
# TempStorage：容量限制
# ---------------------------------------------------------------------------


class TestCapacityLimit:
    def test_capacity_limit_raises(self, temp_storage):
        """超过容量上限时抛出 ValueError"""
        # 先创建一个接近上限的文件
        almost_full = TEMP_MAX_CAPACITY - 100
        temp_storage.create_temp(
            content=b"x" * almost_full,
            owner_session="session-A",
        )
        # 再创建一个超出上限的文件
        with pytest.raises(ValueError, match="capacity exceeded"):
            temp_storage.create_temp(
                content=b"y" * 200,  # 超出剩余 100 bytes
                owner_session="session-A",
            )

    def test_usage_tracking(self, temp_storage):
        """容量统计准确"""
        assert temp_storage.get_temp_usage() == 0
        temp_storage.create_temp(content=b"12345", owner_session="s")
        assert temp_storage.get_temp_usage() == 5
        temp_storage.create_temp(content=b"67890", owner_session="s")
        assert temp_storage.get_temp_usage() == 10


# ---------------------------------------------------------------------------
# TempStorage：清理
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_cleanup_expired(self, temp_storage):
        """cleanup_expired 清理过期文件并返回数量"""
        ref1 = temp_storage.create_temp(content=b"a", owner_session="s")
        ref2 = temp_storage.create_temp(content=b"b", owner_session="s")

        # 使 ref1 过期，ref2 保留
        f1 = temp_storage.temp_dir / ref1.name
        old_time = time.time() - TEMP_TTL_SECONDS - 10
        os.utime(str(f1), (old_time, old_time))

        count = temp_storage.cleanup_expired()
        assert count == 1
        assert not f1.exists()
        # ref2 仍存在
        assert (temp_storage.temp_dir / ref2.name).is_file()

    def test_cleanup_does_not_remove_fresh(self, temp_storage):
        """cleanup_expired 不清理未过期文件"""
        ref = temp_storage.create_temp(content=b"fresh", owner_session="s")
        count = temp_storage.cleanup_expired()
        assert count == 0
        assert (temp_storage.temp_dir / ref.name).is_file()


# ---------------------------------------------------------------------------
# resolve_file_path：各 kind 路径解析
# ---------------------------------------------------------------------------


class TestResolveFilePath:
    def test_resolve_current_path(self, data_root):
        """CURRENT kind 解析到 profiles/{id}/configs/{name}"""
        config_file = data_root / "profiles" / "default" / "configs" / "app.xml"
        config_file.write_text("<app/>", encoding="utf-8")

        ref = FileRef(profile_id="default", kind=FileKind.CURRENT, name="app.xml")
        result = resolve_file_path(ref, data_root, profile_id="default")
        assert result is not None
        assert result == config_file.resolve() or result == config_file

    def test_resolve_archive_path(self, data_root):
        """ARCHIVE kind 解析到 profiles/{id}/archive/{name}"""
        archive_file = data_root / "profiles" / "default" / "archive" / "app_20260101.xml"
        archive_file.write_text("<old/>", encoding="utf-8")

        ref = FileRef(profile_id="default", kind=FileKind.ARCHIVE, name="app_20260101.xml")
        result = resolve_file_path(ref, data_root, profile_id="default")
        assert result is not None
        assert result.name == "app_20260101.xml"

    def test_resolve_record_path(self, data_root):
        """RECORD kind 解析到 profiles/{id}/records/{name}"""
        record_file = data_root / "profiles" / "default" / "records" / "record_20260101.txt"
        record_file.write_text("record content", encoding="utf-8")

        ref = FileRef(profile_id="default", kind=FileKind.RECORD, name="record_20260101.txt")
        result = resolve_file_path(ref, data_root, profile_id="default")
        assert result is not None
        assert "records" in str(result)

    def test_resolve_temp_path(self, data_root):
        """TEMP kind 解析到 temporary/{name}"""
        ts = TempStorage(data_root=data_root)
        ref = ts.create_temp(content=b"tmp", owner_session="s", original_name="t.xml")
        result = resolve_file_path(ref, data_root, profile_id="default")
        assert result is not None
        assert "temporary" in str(result)

    def test_resolve_nonexistent_returns_none(self, data_root):
        """文件不存在时返回 None"""
        ref = FileRef(profile_id="default", kind=FileKind.CURRENT, name="missing.xml")
        result = resolve_file_path(ref, data_root, profile_id="default")
        assert result is None

    def test_resolve_path_traversal_blocked(self, data_root):
        """路径穿越尝试被阻止"""
        ref = FileRef(profile_id="default", kind=FileKind.CURRENT, name="../../../etc/passwd")
        result = resolve_file_path(ref, data_root, profile_id="default")
        assert result is None

    def test_profile_switch_record_not_cross_contaminated(self, data_root):
        """profile 切换后旧 record link 不串到另一个 profile

        验收标准：profile 切换后旧 record link 不串
        """
        # 在 default profile 放置 record
        default_record = data_root / "profiles" / "default" / "records" / "rec.txt"
        default_record.write_text("default record", encoding="utf-8")

        # 创建另一个 profile 目录（不含该 record）
        other_profile = data_root / "profiles" / "other" / "records"
        other_profile.mkdir(parents=True)

        ref = FileRef(profile_id="default", kind=FileKind.RECORD, name="rec.txt")

        # 在 default profile 下可以解析
        result_default = resolve_file_path(ref, data_root, profile_id="default")
        assert result_default is not None

        # 切换到 other profile 后，同一 ref 解析不到（不串）
        result_other = resolve_file_path(ref, data_root, profile_id="other")
        assert result_other is None


# ---------------------------------------------------------------------------
# create_file_ref
# ---------------------------------------------------------------------------


class TestCreateFileRef:
    def test_create_current_ref(self):
        """创建 CURRENT FileRef"""
        ref = create_file_ref(FileKind.CURRENT, "app.xml", profile_id="default")
        assert ref.kind == FileKind.CURRENT
        assert ref.name == "app.xml"
        assert ref.profile_id == "default"

    def test_create_archive_ref_with_version(self):
        """创建 ARCHIVE FileRef（含版本号）"""
        ref = create_file_ref(
            FileKind.ARCHIVE, "app_20260101.xml",
            profile_id="default", version="v1",
        )
        assert ref.version_or_token == "v1"

    def test_invalid_filename_raises(self):
        """非法文件名抛出 ValueError"""
        with pytest.raises(ValueError):
            create_file_ref(FileKind.CURRENT, "..", profile_id="default")

    def test_path_separator_filename_raises(self):
        """含路径分隔符的文件名抛出 ValueError"""
        with pytest.raises(ValueError):
            create_file_ref(FileKind.CURRENT, "../etc/passwd", profile_id="default")


# ---------------------------------------------------------------------------
# 同名 temp 优先显示 temp（验收标准）
# ---------------------------------------------------------------------------


class TestTempPriority:
    def test_same_name_temp_takes_priority(self, data_root):
        """同名文件存在 temp 和 current 时，temp kind 解析到 temp 目录

        验收标准：同名 temp 优先显示 temp
        """
        # 在 current 目录放置同名文件
        current_file = data_root / "profiles" / "default" / "configs" / "shared.xml"
        current_file.write_text("<current/>", encoding="utf-8")

        # 创建 temp 同名文件
        ts = TempStorage(data_root=data_root)
        ref = ts.create_temp(
            content=b"<temp/>",
            owner_session="session-A",
            original_name="shared.xml",
        )

        # TEMP kind 解析到 temp 目录
        temp_result = resolve_file_path(ref, data_root, profile_id="default")
        assert temp_result is not None
        assert "temporary" in str(temp_result)
        assert temp_result.read_bytes() == b"<temp/>"

        # CURRENT kind 解析到 current 目录（互不干扰）
        current_ref = FileRef(
            profile_id="default", kind=FileKind.CURRENT, name="shared.xml"
        )
        current_result = resolve_file_path(current_ref, data_root, profile_id="default")
        assert current_result is not None
        assert "configs" in str(current_result)
