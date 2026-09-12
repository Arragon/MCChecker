"""存储层多工况测试

覆盖：原子写入、JSON 加载、profile 隔离、WriteGate 状态机、
配置映射 CRUD、收藏变量、绑定关系、记录文件等边界场景。
"""

import json
import os
import pytest
import tempfile
import shutil

from app.core.storage import (
    atomic_write_json,
    _load_json,
    _save_json,
    CorruptDataError,
    WriteGate,
    WriteBlockedError,
    add_config_mapping,
    load_config_mapping,
    remove_config_mapping,
    update_config_mapping,
    add_favorite,
    load_favorites,
    remove_favorite,
    update_favorite_note,
    add_binding,
    load_bindings,
    remove_binding,
    get_bound_paths,
    save_record_file,
    load_record_file,
    list_record_versions,
    _sanitize_config_filename,
    unique_archive_name,
)


# ---- 原子写入 ----

class TestAtomicWrite:
    def test_atomic_write_creates_file(self, tmp_path):
        path = tmp_path / "test.json"
        atomic_write_json(str(path), {"key": "value"})
        assert path.exists()
        with open(path) as f:
            data = json.load(f)
        assert data["key"] == "value"

    def test_atomic_write_overwrites(self, tmp_path):
        path = tmp_path / "test.json"
        atomic_write_json(str(path), {"v": 1})
        atomic_write_json(str(path), {"v": 2})
        with open(path) as f:
            data = json.load(f)
        assert data["v"] == 2

    def test_atomic_write_creates_dirs(self, tmp_path):
        path = tmp_path / "subdir" / "nested" / "test.json"
        atomic_write_json(str(path), {"nested": True})
        assert path.exists()

    def test_atomic_write_unicode(self, tmp_path):
        path = tmp_path / "unicode.json"
        atomic_write_json(str(path), {"中文": "测试"})
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["中文"] == "测试"


# ---- JSON 加载 ----

class TestJsonLoading:
    def test_load_missing_returns_default(self, tmp_path):
        path = tmp_path / "missing.json"
        result = _load_json(str(path), default=[])
        assert result == []

    def test_load_corrupt_raises(self, tmp_path):
        path = tmp_path / "corrupt.json"
        with open(path, "w") as f:
            f.write("not valid json {{{")
        with pytest.raises(CorruptDataError):
            _load_json(str(path))

    def test_load_valid_json(self, tmp_path):
        path = tmp_path / "valid.json"
        with open(path, "w") as f:
            json.dump({"test": True}, f)
        result = _load_json(str(path))
        assert result["test"] is True


# ---- 文件名 sanitize ----

class TestSanitizeFilename:
    def test_valid_filename(self):
        assert _sanitize_config_filename("config.xml") == "config.xml"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _sanitize_config_filename("")

    def test_slash_raises(self):
        with pytest.raises(ValueError):
            _sanitize_config_filename("path/config.xml")

    def test_backslash_raises(self):
        with pytest.raises(ValueError):
            _sanitize_config_filename("path\\config.xml")

    def test_dot_dot_raises(self):
        with pytest.raises(ValueError):
            _sanitize_config_filename("..")

    def test_whitespace_stripped(self):
        assert _sanitize_config_filename("  config.xml  ") == "config.xml"


# ---- 归档名唯一性 ----

class TestUniqueArchiveName:
    def test_generates_unique_names(self):
        name1 = unique_archive_name("config.xml")
        name2 = unique_archive_name("config.xml")
        assert name1 != name2

    def test_preserves_extension(self):
        name = unique_archive_name("config.xml")
        assert name.endswith(".xml")

    def test_contains_timestamp(self):
        name = unique_archive_name("config.xml")
        # 应包含日期格式
        assert "_" in name
        assert len(name) > len("config.xml")


# ---- WriteGate 状态机 ----

class TestWriteGate:
    def test_initial_state_open(self):
        gate = WriteGate()
        assert gate.state == WriteGate.OPEN

    def test_acquire_release(self):
        gate = WriteGate()
        gate.acquire_write()
        gate.release_write()
        assert gate.state == WriteGate.OPEN

    def test_maintenance_mode(self):
        gate = WriteGate()
        assert gate.enter_maintenance(timeout=1.0)
        assert gate.state == WriteGate.MAINTENANCE
        gate.exit_maintenance()
        assert gate.state == WriteGate.OPEN

    def test_write_blocked_in_maintenance(self):
        gate = WriteGate()
        gate.enter_maintenance(timeout=1.0)
        with pytest.raises(WriteBlockedError):
            gate.acquire_write()
        gate.exit_maintenance()

    def test_write_scope_context_manager(self):
        gate = WriteGate()
        with gate.write_scope():
            assert gate.state == WriteGate.OPEN
        assert gate.state == WriteGate.OPEN

    def test_draining_state(self):
        gate = WriteGate()
        gate.acquire_write()
        # 进入维护模式会先变为 DRAINING
        import threading

        def enter_maint():
            gate.enter_maintenance(timeout=0.1)

        t = threading.Thread(target=enter_maint)
        t.start()
        t.join(timeout=0.2)
        # 应该超时回到 OPEN
        assert gate.state == WriteGate.OPEN
        gate.release_write()


# ---- 配置映射 CRUD ----

class TestConfigMapping:
    def test_add_mapping(self):
        add_config_mapping("test.xml", "http://example.com/test.xml")
        mapping = load_config_mapping()
        assert any(m["name"] == "test.xml" for m in mapping)

    def test_add_duplicate_updates_url(self):
        add_config_mapping("test.xml", "http://old.com")
        add_config_mapping("test.xml", "http://new.com")
        mapping = load_config_mapping()
        test_mapping = [m for m in mapping if m["name"] == "test.xml"][0]
        assert test_mapping["url"] == "http://new.com"

    def test_remove_mapping(self):
        add_config_mapping("test.xml", "http://example.com")
        removed = remove_config_mapping("test.xml")
        assert removed
        mapping = load_config_mapping()
        assert not any(m["name"] == "test.xml" for m in mapping)

    def test_remove_nonexistent(self):
        removed = remove_config_mapping("nonexistent.xml")
        assert not removed


# ---- 收藏变量 ----

class TestFavorites:
    def test_add_favorite(self):
        add_favorite("/path/to/var", "var_label", "value", "test.xml", note="test note")
        favs = load_favorites()
        assert len(favs) == 1
        assert favs[0]["path"] == "/path/to/var"
        assert favs[0]["note"] == "test note"

    def test_add_duplicate_updates_note(self):
        add_favorite("/path", "label", "val", "test.xml", note="old")
        add_favorite("/path", "label", "val", "test.xml", note="new")
        favs = load_favorites()
        assert len(favs) == 1
        assert favs[0]["note"] == "new"

    def test_remove_favorite(self):
        add_favorite("/path", "label", "val", "test.xml")
        removed = remove_favorite("/path", "test.xml")
        assert removed
        favs = load_favorites()
        assert len(favs) == 0

    def test_remove_nonexistent(self):
        removed = remove_favorite("/nonexistent", "test.xml")
        assert not removed

    def test_update_note(self):
        add_favorite("/path", "label", "val", "test.xml", note="old")
        updated = update_favorite_note("/path", "test.xml", "new note")
        assert updated
        favs = load_favorites()
        assert favs[0]["note"] == "new note"


# ---- 变量绑定 ----

class TestBindings:
    def test_add_binding(self):
        add_binding("group1", [{"file": "a.xml", "path": "/a"}])
        bindings = load_bindings()
        assert len(bindings) == 1
        assert bindings[0]["group_name"] == "group1"

    def test_update_binding(self):
        add_binding("group1", [{"file": "a.xml", "path": "/a"}])
        add_binding("group1", [{"file": "b.xml", "path": "/b"}])
        bindings = load_bindings()
        assert len(bindings) == 1
        assert bindings[0]["variables"][0]["file"] == "b.xml"

    def test_remove_binding(self):
        add_binding("group1", [{"file": "a.xml", "path": "/a"}])
        removed = remove_binding("group1")
        assert removed
        bindings = load_bindings()
        assert len(bindings) == 0

    def test_get_bound_paths(self):
        add_binding("group1", [
            {"file": "a.xml", "path": "/a"},
            {"file": "b.xml", "path": "/b"},
        ])
        bound = get_bound_paths()
        assert "a.xml:/a" in bound
        assert bound["a.xml:/a"] == "group1"


# ---- 记录文件 ----

class TestRecordFiles:
    def test_save_and_load_record(self):
        content = b"test record content"
        path = save_record_file("test.xml", content)
        assert os.path.exists(path)

        # 从路径中提取文件名
        filename = os.path.basename(path)
        loaded = load_record_file("test.xml", filename)
        assert loaded == content

    def test_list_record_versions(self):
        save_record_file("test.xml", b"v1")
        save_record_file("test.xml", b"v2")
        versions = list_record_versions("test.xml")
        assert len(versions) >= 2

    def test_load_nonexistent_record(self):
        result = load_record_file("nonexistent.xml", "missing.txt")
        assert result is None
