import os

import pytest


@pytest.fixture(autouse=True)
def _profile_setup(isolated_data_env):
    """profile 测试额外初始化：重置 profile 和 nicegui 用户存储"""
    isolated_data_env.set_active_profile(isolated_data_env.DEFAULT_PROFILE)
    try:
        from nicegui import app
        app.storage.user.pop("device_model", None)
    except Exception:
        pass


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
