"""文件下载辅助测试。"""

import os
import urllib.parse

import pytest


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
