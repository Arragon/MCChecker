"""机型/配置空间隔离测试

数据目录隔离由 `tests/conftest.py` 的 `isolated_data_root` 夹具统一提供。
"""

from __future__ import annotations

import os

import pytest


class TestProfileIsolation:
    def test_favorites_isolated_by_profile(self):
        from app.core import storage

        storage.set_active_profile("default")
        storage.add_favorite("/a", "a", "1", "f.xml")

        storage.set_active_profile("p1")
        assert storage.load_favorites() == []
        storage.add_favorite("/b", "b", "2", "f.xml")

        storage.set_active_profile("default")
        favs = storage.load_favorites()
        assert len(favs) == 1
        assert favs[0]["path"] == "/a"

        storage.set_active_profile("p1")
        favs = storage.load_favorites()
        assert len(favs) == 1
        assert favs[0]["path"] == "/b"

    def test_config_files_isolated_by_profile(self):
        from app.core import storage

        storage.set_active_profile("default")
        storage.save_config_file("a.xml", b"a")
        assert "a.xml" in storage.list_config_files()

        storage.set_active_profile("p1")
        assert storage.list_config_files() == []
        storage.save_config_file("b.xml", b"b")
        assert storage.list_config_files() == ["b.xml"]

        storage.set_active_profile("default")
        assert storage.list_config_files() == ["a.xml"]

    def test_dltool_config_isolated_by_profile(self):
        from app.core import storage, dltool

        storage.set_active_profile("default")
        cfg = dltool.load_config()
        cfg["items"]["rp_energy"]["x_min"] = 1.0
        dltool.save_config(cfg)

        storage.set_active_profile("p1")
        cfg2 = dltool.load_config()
        assert cfg2["items"]["rp_energy"]["x_min"] is None
        cfg2["items"]["rp_energy"]["x_min"] = 2.0
        dltool.save_config(cfg2)

        storage.set_active_profile("default")
        cfg3 = dltool.load_config()
        assert cfg3["items"]["rp_energy"]["x_min"] == 1.0

        storage.set_active_profile("p1")
        cfg4 = dltool.load_config()
        assert cfg4["items"]["rp_energy"]["x_min"] == 2.0

    def test_copy_profile_data(self):
        from app.core import storage

        storage.set_active_profile("default")
        storage.add_favorite("/a", "a", "1", "f.xml")
        storage.save_config_file("a.xml", b"a")

        storage.copy_profile_data("default", "p2")

        storage.set_active_profile("p2")
        assert storage.list_config_files() == ["a.xml"]
        favs = storage.load_favorites()
        assert len(favs) == 1
        assert favs[0]["path"] == "/a"


class TestDeviceModels:
    def test_add_remove_model(self):
        from app.core import device_models, storage

        assert device_models.exists(device_models.DEFAULT_MODEL_ID)
        mid = device_models.add_model("Test Model")
        assert device_models.exists(mid)

        storage.set_active_profile(mid)
        storage.save_config_file("x.xml", b"x")
        assert os.path.exists(storage.get_config_path("x.xml"))

        assert device_models.remove_model(mid, delete_data=True) is True
        assert device_models.exists(mid) is False

    def test_rename_model(self):
        from app.core import device_models

        mid = device_models.add_model("Old Name")
        assert device_models.get_model_name(mid) == "Old Name"
        assert device_models.rename_model(mid, "New Name") is True
        assert device_models.get_model_name(mid) == "New Name"


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class TestProfileIdValidation:
    """非法机型 id 必须报错，不能静默回退 default（§5.2、C26）。"""

    @pytest.mark.parametrize("bad", ["../x", "a/b", "a\\b", ".", "..", "has space", "中文", "a:b"])
    def test_illegal_id_raises_not_found(self, bad):
        from app.core import storage
        from app.core.errors import NOT_FOUND, AppError

        storage.set_active_profile("default")
        with pytest.raises(AppError) as excinfo:
            storage.set_active_profile(bad)
        assert excinfo.value.code == NOT_FOUND
        # 上下文保持在上一个合法机型，不会被非法值污染
        assert storage.get_active_profile() == "default"

    def test_illegal_id_cannot_read_default_data(self):
        from app.core import storage
        from app.core.errors import AppError

        storage.set_active_profile("default")
        storage.save_config_file("secret.xml", b"secret")

        with pytest.raises(AppError):
            with storage.use_profile("bad/id"):
                storage.list_config_files()
        with pytest.raises(AppError):
            storage.get_configs_dir("bad/id")


class TestCopyProfileScope:
    """复制机型的数据范围（§7.2、C27）。"""

    def test_copy_excludes_cache_and_runtime_data(self):
        from app.core import storage

        storage.set_active_profile("p1")
        storage.save_config_file("keep.xml", b"keep")
        storage.add_favorite("/a", "a", "1", "keep.xml")
        src_base = storage.get_profile_data_dir("p1")

        # 运行期数据与缓存：可重建/属于源机型历史，不随机型复制
        _write_text(os.path.join(src_base, "cache", "c.json"), "{}")
        _write_text(os.path.join(src_base, "records", "r.txt"), "x")
        for rel in ("edit_remarks.json", "ip_mapping.json", "admin_users.json", "update_status.json"):
            _write_text(os.path.join(src_base, rel), "[]")

        storage.copy_profile_data("p1", "p2")

        dst_base = storage.get_profile_data_dir("p2")
        assert not os.path.exists(os.path.join(dst_base, "cache"))
        assert not os.path.exists(os.path.join(dst_base, "records"))
        for rel in ("edit_remarks.json", "ip_mapping.json", "admin_users.json", "update_status.json"):
            assert not os.path.exists(os.path.join(dst_base, rel)), rel
        # 该复制的仍在
        assert os.path.exists(os.path.join(dst_base, "configs", "keep.xml"))
        assert os.path.exists(os.path.join(dst_base, "favorites.json"))

    def test_copy_clears_schedule_last_run(self):
        from app.core import storage

        storage.set_active_profile("p1")
        storage.save_schedule(
            {"enabled": True, "interval_hours": 48, "last_run": "2026-01-01 00:00:00"}
        )

        storage.copy_profile_data("p1", "p2")

        storage.set_active_profile("p2")
        schedule = storage.load_schedule()
        assert schedule["enabled"] is True
        assert schedule["interval_hours"] == 48
        assert schedule["last_run"] is None

    def test_copy_requires_non_default_target(self):
        from app.core import storage

        storage.set_active_profile("p1")
        with pytest.raises(ValueError):
            storage.copy_profile_data("p1", storage.DEFAULT_PROFILE)


class TestConfigRenameMigration:
    """配置映射改名时的引用迁移与失败回滚（M0-07）。"""

    @staticmethod
    def _seed() -> None:
        from app.core import dltool, storage

        storage.set_active_profile("p1")
        storage.save_config_file("old.xml", b"<r/>")
        storage.add_config_mapping("old.xml", "http://example.invalid/old.xml")
        storage.add_favorite("/root/a", "a", "1", "old.xml")
        storage.add_binding("g", [{"file": "old.xml", "path": "/root/a"}])
        storage.add_edit_remark(
            source_file="old.xml",
            node_key="k",
            node_path="/root/a",
            node_label="a",
            original_value="1",
            proposed_value="2",
            node_type="string",
            tree_type="xml",
            actor_ip="10.0.0.1",
            actor_name="tester",
        )
        cfg = dltool.load_config()
        cfg["items"]["pp_energy"]["binding"] = {
            "source_file": "old.xml",
            "variable_path": "/root/a",
            "field_map": {},
        }
        dltool.save_config(cfg)

    def test_rename_migrates_references(self):
        from app.core import dltool, storage

        self._seed()

        assert storage.update_config_mapping(
            "old.xml", "new.xml", "http://example.invalid/new.xml"
        ) is True

        assert os.path.exists(storage.get_config_path("new.xml"))
        assert not os.path.exists(storage.get_config_path("old.xml"))
        assert [f["source_file"] for f in storage.load_favorites()] == ["new.xml"]
        assert [v["file"] for b in storage.load_bindings() for v in b["variables"]] == ["new.xml"]
        assert [i["source_file"] for i in storage.load_edit_remarks()] == ["new.xml"]
        assert dltool.load_config()["items"]["pp_energy"]["binding"]["source_file"] == "new.xml"
        assert {m["name"] for m in storage.load_config_mapping()} == {"new.xml"}

    def test_rename_rolls_back_when_target_exists(self):
        from app.core import storage
        from app.core.errors import FILE_EXISTS, AppError

        self._seed()
        storage.save_config_file("new.xml", b"<n/>")

        with pytest.raises(AppError) as excinfo:
            storage.update_config_mapping("old.xml", "new.xml", "http://example.invalid/new.xml")
        assert excinfo.value.code == FILE_EXISTS

        # 源文件、目标文件、映射与引用全部保持原样
        assert os.path.exists(storage.get_config_path("old.xml"))
        assert storage.load_config_file("new.xml") == b"<n/>"
        mapping = {m["name"]: m for m in storage.load_config_mapping()}
        assert mapping["old.xml"]["url"] == "http://example.invalid/old.xml"
        assert "new.xml" not in mapping
        assert [f["source_file"] for f in storage.load_favorites()] == ["old.xml"]


class TestDeviceModelRegistry:
    """机型注册表的原子写与损坏保护（M0-07、§5.4）。"""

    def test_default_always_present(self):
        from app.core import device_models

        assert device_models.exists(device_models.DEFAULT_MODEL_ID)
        device_models.save_models([{"id": "x", "name": "X"}])
        ids = [m["id"] for m in device_models.load_models()]
        assert ids[0] == device_models.DEFAULT_MODEL_ID
        assert "x" in ids

    def test_corrupt_registry_raises_and_is_not_reset(self):
        from app.core import device_models
        from app.core.errors import CorruptDataError

        path = device_models._models_file()
        broken = '{"models": '
        _write_text(path, broken)

        with pytest.raises(CorruptDataError):
            device_models.load_models()
        # 损坏内容不被覆盖成空注册表
        assert _read_text(path) == broken

    def test_registry_write_is_atomic(self, monkeypatch):
        from app.core import device_models, fsutil

        device_models.add_model("Keep")
        before = _read_text(device_models._models_file())

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(fsutil, "atomic_write_bytes", boom)
        with pytest.raises(OSError):
            device_models.add_model("Boom")
        assert _read_text(device_models._models_file()) == before


class TestDltoolConfigIntegrity:
    """dltool_config.json 的损坏保护（§5.4）。"""

    def test_corrupt_config_raises(self):
        from app.core import dltool, storage
        from app.core.errors import CorruptDataError

        storage.set_active_profile("p1")
        _write_text(dltool._get_config_file(), "{not json")

        with pytest.raises(CorruptDataError):
            dltool.load_config()

    def test_missing_config_returns_defaults(self):
        from app.core import dltool, storage

        storage.set_active_profile("p1")
        cfg = dltool.load_config()
        assert set(cfg["items"]) == {"pp_energy", "rp_energy", "rp_width"}
