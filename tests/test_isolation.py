"""测试数据隔离的守卫测试（INH-1859 / M0-02）。

`tests/conftest.py` 的 `isolated_data_root` 把业务数据根搬到 `tmp_path`。
本文件刻意执行写操作，用来守住这条边界：一旦有人把该夹具删掉或漏掉某个
路径常量，这里会立刻失败，而不是悄悄把文件写进仓库。
"""

from __future__ import annotations

import json
import os

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REAL_DATA_DIR = os.path.join(REPO_ROOT, "data")


def _assert_outside_repo_and_inside_tmp(path: str, tmp_root: str) -> None:
    abs_path = os.path.abspath(path)
    assert os.path.abspath(tmp_root) in abs_path, f"{abs_path} 不在临时目录 {tmp_root} 下"
    assert not abs_path.startswith(REAL_DATA_DIR + os.sep), f"{abs_path} 落进了仓库 data/"


class TestBusinessDataIsolation:
    """业务数据写入点必须落在临时目录。"""

    def test_config_write(self, tmp_path):
        from app.core import storage

        storage.save_config_file("probe.xml", b"<a>1</a>")
        _assert_outside_repo_and_inside_tmp(storage.get_config_path("probe.xml"), str(tmp_path))

    def test_bindings_write(self, tmp_path):
        from app.core import storage

        storage.add_binding("g", [
            {"file": "a.json", "path": "a.json/timeout"},
            {"file": "b.json", "path": "b.json/timeout"},
        ])
        _assert_outside_repo_and_inside_tmp(storage.get_bindings_file(), str(tmp_path))

    def test_dltool_config_constant(self, tmp_path):
        from app.core import dltool

        _assert_outside_repo_and_inside_tmp(dltool.DLTOOL_FILE, str(tmp_path))
        with open(dltool.DLTOOL_FILE, "w", encoding="utf-8") as fh:
            json.dump({"probe": True}, fh)
        assert os.path.exists(dltool.DLTOOL_FILE)

    def test_device_models_path(self, tmp_path):
        from app.core import device_models

        # _models_file 由夹具钉住，add_model 会经由它落盘
        _assert_outside_repo_and_inside_tmp(device_models._models_file(), str(tmp_path))
        model_id = device_models.add_model("探针机型")
        assert model_id
        assert os.path.exists(device_models._models_file())

    def test_directory_constants(self, tmp_path):
        from app.core import storage

        for path in (
            storage.DATA_DIR,
            storage.PROFILES_DIR,
            storage.CONFIGS_DIR,
            storage.ARCHIVE_DIR,
            storage.CACHE_DIR,
            storage.PARSE_CACHE_DIR,
            storage.MAPPING_FILE,
            storage.FAVORITES_FILE,
            storage.BINDINGS_FILE,
            storage.TOOLS_FILE,
            storage.SCHEDULE_FILE,
            storage.IP_MAPPING_FILE,
            storage.ADMIN_USERS_FILE,
            storage.EDIT_REMARKS_FILE,
            storage.LEGACY_MAPPING_FILE,
            storage.LEGACY_FAVORITES_FILE,
            storage.LEGACY_BINDINGS_FILE,
            storage.LEGACY_TOOLS_FILE,
            storage.LEGACY_SCHEDULE_FILE,
            storage.LEGACY_IP_MAPPING_FILE,
            storage.LEGACY_ADMIN_USERS_FILE,
            storage.LEGACY_EDIT_REMARKS_FILE,
        ):
            _assert_outside_repo_and_inside_tmp(path, str(tmp_path))
