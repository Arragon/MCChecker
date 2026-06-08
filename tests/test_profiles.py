import os
import shutil
import tempfile

import pytest


TEST_DATA_DIR = tempfile.mkdtemp()


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    import app.core.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DATA_DIR", TEST_DATA_DIR)
    monkeypatch.setattr(storage_mod, "PROFILES_DIR", os.path.join(TEST_DATA_DIR, "profiles"))
    monkeypatch.setattr(storage_mod, "CONFIGS_DIR", os.path.join(TEST_DATA_DIR, "configs"))
    monkeypatch.setattr(storage_mod, "ARCHIVE_DIR", os.path.join(TEST_DATA_DIR, "archive"))
    monkeypatch.setattr(storage_mod, "CACHE_DIR", os.path.join(TEST_DATA_DIR, "cache"))
    monkeypatch.setattr(storage_mod, "PARSE_CACHE_DIR", os.path.join(TEST_DATA_DIR, "cache", "parse_tree"))
    monkeypatch.setattr(storage_mod, "MAPPING_FILE", os.path.join(TEST_DATA_DIR, "config_mapping.json"))
    monkeypatch.setattr(storage_mod, "FAVORITES_FILE", os.path.join(TEST_DATA_DIR, "favorites.json"))
    monkeypatch.setattr(storage_mod, "BINDINGS_FILE", os.path.join(TEST_DATA_DIR, "bindings.json"))
    monkeypatch.setattr(storage_mod, "TOOLS_FILE", os.path.join(TEST_DATA_DIR, "tools.json"))
    monkeypatch.setattr(storage_mod, "SCHEDULE_FILE", os.path.join(TEST_DATA_DIR, "schedule.json"))
    monkeypatch.setattr(storage_mod, "LEGACY_MAPPING_FILE", storage_mod.MAPPING_FILE)
    monkeypatch.setattr(storage_mod, "LEGACY_FAVORITES_FILE", storage_mod.FAVORITES_FILE)
    monkeypatch.setattr(storage_mod, "LEGACY_BINDINGS_FILE", storage_mod.BINDINGS_FILE)
    monkeypatch.setattr(storage_mod, "LEGACY_TOOLS_FILE", storage_mod.TOOLS_FILE)
    monkeypatch.setattr(storage_mod, "LEGACY_SCHEDULE_FILE", storage_mod.SCHEDULE_FILE)
    storage_mod.set_active_profile(storage_mod.DEFAULT_PROFILE)
    try:
        from nicegui import app
        app.storage.user.pop("device_model", None)
    except Exception:
        pass
    storage_mod._ensure_dirs()
    yield
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)
    os.makedirs(TEST_DATA_DIR, exist_ok=True)


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
