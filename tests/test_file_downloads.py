"""文件下载辅助测试。"""

import os
import shutil
import tempfile
import urllib.parse

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
    storage_mod._ensure_dirs()
    yield
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)
    os.makedirs(TEST_DATA_DIR, exist_ok=True)


class TestFileDownloads:
    def test_resolve_current_download_target(self):
        from app.core import storage
        from app.pages.file_downloads import DOWNLOAD_KIND_CURRENT, resolve_download_target

        storage.save_config_file("alpha.xml", b"<root />")

        target = resolve_download_target(DOWNLOAD_KIND_CURRENT, "alpha.xml")

        assert target.download_name == "alpha.xml"
        assert target.filename == "alpha.xml"
        assert target.kind == DOWNLOAD_KIND_CURRENT
        assert os.path.isfile(target.path) is True

    def test_resolve_archive_download_target_preserves_version_name(self):
        from app.core import storage
        from app.pages.file_downloads import DOWNLOAD_KIND_ARCHIVE, resolve_download_target

        storage.save_config_file("history.xml", b"<root><a>1</a></root>")
        storage.save_config_file("history.xml", b"<root><a>2</a></root>")
        archive_filename = storage.list_archived_versions("history.xml")[0]["filename"]

        target = resolve_download_target(
            DOWNLOAD_KIND_ARCHIVE,
            "history.xml",
            archive_filename=archive_filename,
        )

        assert target.download_name == archive_filename
        assert target.archive_filename == archive_filename
        assert os.path.basename(target.path) == archive_filename

    def test_resolve_download_target_rejects_invalid_path_segment(self):
        from app.pages.file_downloads import DOWNLOAD_KIND_CURRENT, DownloadResolutionError, resolve_download_target

        with pytest.raises(DownloadResolutionError) as exc_info:
            resolve_download_target(DOWNLOAD_KIND_CURRENT, "../secret.xml")

        assert exc_info.value.status_code == 400
        assert "不合法" in exc_info.value.message

    def test_resolve_download_target_raises_for_missing_resource(self):
        from app.pages.file_downloads import DOWNLOAD_KIND_CURRENT, DownloadResolutionError, resolve_download_target

        with pytest.raises(DownloadResolutionError) as exc_info:
            resolve_download_target(DOWNLOAD_KIND_CURRENT, "missing.xml")

        assert exc_info.value.status_code == 404
        assert "不存在" in exc_info.value.message

    def test_build_download_url_contains_profile_and_archive_name(self):
        from app.pages.file_downloads import DOWNLOAD_KIND_ARCHIVE, build_download_url

        url = build_download_url(
            DOWNLOAD_KIND_ARCHIVE,
            "history.xml",
            archive_filename="history_20260101.xml",
            profile_id="model-a",
        )

        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        assert parsed.path == "/api/file-download"
        assert params["kind"] == [DOWNLOAD_KIND_ARCHIVE]
        assert params["filename"] == ["history.xml"]
        assert params["archive_filename"] == ["history_20260101.xml"]
        assert params["profile"] == ["model-a"]
