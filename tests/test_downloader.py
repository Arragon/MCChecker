"""下载器测试"""

import pytest
from unittest.mock import patch, MagicMock
from app.core.downloader import download_file


class TestDownloader:
    @patch("app.core.downloader.urllib.request.urlopen")
    def test_download_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b"<config>test</config>"
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = download_file("http://example.com/config.xml")
        assert result == b"<config>test</config>"

    @patch("app.core.downloader.urllib.request.urlopen")
    def test_download_failure(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://example.com/config.xml", 404, "Not Found", {}, None
        )

        result = download_file("http://example.com/config.xml", retries=1)
        assert result is None
