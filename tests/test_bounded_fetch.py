"""M16-22 response guard / bounded read / archive preflight tests."""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
import zipfile

import pytest

from collector.bounded_fetch import guard_response, preflight_archive, read_response
from collector.stream_reader import StagingSink, StreamError

ORIGIN = "https://sda.example"


class FakeResponse:
    def __init__(self, status=200, headers=None, url=ORIGIN + "/file", chunks=None, body=None):
        self.status = status
        self.headers = headers or {"content-type": "application/json"}
        self.url = url
        self._chunks = list(chunks) if chunks is not None else None
        self._body = body
        self.read_calls = 0

    def read(self, size):
        self.read_calls += 1
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def body(self):
        return self._body


def guard(**overrides):
    values = {
        "status": 200,
        "headers": {"content-type": "application/json"},
        "final_url": ORIGIN + "/file",
        "origin": ORIGIN,
        "limit_bytes": 1024,
    }
    values.update(overrides)
    return guard_response(**values)


def test_guard_rejects_redirect_downgrade_html_and_auth_errors():
    cases = [
        ({"final_url": "https://evil.invalid/file"}, "redirect_blocked"),
        ({"final_url": "http://sda.example/file"}, "redirect_blocked"),
        ({"status": 302, "headers": {"location": "https://evil.invalid"}}, "redirect_blocked"),
        ({"headers": {"content-type": "text/html; charset=utf-8"}}, "invalid_payload"),
        ({"status": 500, "headers": {}}, "auth_failed"),
        ({"status": 403}, "permission_denied"),
        ({"status": 500, "headers": {"content-length": "10"}}, "business_error"),
        ({"headers": {"content-length": "2048"}}, "too_large"),
        ({"headers": {"content-length": "-1"}}, "invalid_payload"),
    ]
    for overrides, reason in cases:
        with pytest.raises(StreamError) as exc:
            guard(**overrides)
        assert exc.value.reason_code == reason


def test_known_length_too_large_aborts_before_read(tmp_path):
    sink = StagingSink(str(tmp_path / "stage"))
    path = sink.path
    response = FakeResponse(headers={"content-length": "2048", "content-type": "application/json"})
    with pytest.raises(StreamError) as exc:
        read_response(response, sink, ORIGIN, limit_bytes=1024)
    assert exc.value.reason_code == "too_large"
    assert response.read_calls == 0
    assert not os.path.exists(path)


def test_unknown_length_stream_is_bounded_and_staging_removed(tmp_path):
    sink = StagingSink(str(tmp_path / "stage"))
    path = sink.path
    response = FakeResponse(chunks=[b"x" * 600, b"y" * 600])
    with pytest.raises(StreamError) as exc:
        read_response(response, sink, ORIGIN, limit_bytes=1000, chunk_size=600)
    assert exc.value.reason_code == "too_large"
    assert not os.path.exists(path)


def test_buffered_body_limit_and_html_prefix(tmp_path):
    sink = StagingSink(str(tmp_path / "stage"))
    response = FakeResponse(headers={"content-type": "application/json"}, body=b"x" * 2000)
    response.read = None
    with pytest.raises(StreamError) as exc:
        read_response(response, sink, ORIGIN, limit_bytes=1000)
    assert exc.value.reason_code == "too_large"

    sink = StagingSink(str(tmp_path / "stage"))
    response = FakeResponse(headers={"content-type": "application/json"}, body=b"  <html>login</html>")
    response.read = None
    with pytest.raises(StreamError) as exc:
        read_response(response, sink, ORIGIN, limit_bytes=1000)
    assert exc.value.reason_code == "invalid_payload"


def test_response_headers_with_cookie_are_not_returned(tmp_path):
    sink = StagingSink(str(tmp_path / "stage"))
    response = FakeResponse(
        headers={"content-type": "application/json", "set-cookie": "sid=super-secret"},
        body=b'{"ok":true}',
    )
    response.read = None
    result = read_response(response, sink, ORIGIN, limit_bytes=1000)
    sink.commit()
    assert result["size"] == len(b'{"ok":true}')
    assert result["sha256"] == hashlib.sha256(b'{"ok":true}').hexdigest()
    assert "set-cookie" not in result
    assert "super-secret" not in repr(result)

    with pytest.raises(StreamError) as exc:
        guard(headers={"content-type": "text/html", "set-cookie": "sid=super-secret"})
    assert "super-secret" not in str(exc.value)
    assert "cookie" not in str(exc.value).lower()


def _zip(path, members, compression=zipfile.ZIP_STORED):
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, body in members:
            archive.writestr(name, body)


def test_archive_preflight_rejects_bomb_before_extraction(tmp_path):
    normal = str(tmp_path / "normal.zip")
    _zip(normal, [("a.json", b"{}"), ("b.txt", b"x" * 100)])
    info = preflight_archive(normal, limit_bytes=1024 * 1024)
    assert info["kind"] == "zip"
    assert info["entries"] == 2
    assert info["uncompressed_bytes"] == 102

    too_big = str(tmp_path / "big.zip")
    _zip(too_big, [("big.bin", b"\0" * (2 * 1024 * 1024))], compression=zipfile.ZIP_DEFLATED)
    with pytest.raises(StreamError) as exc:
        preflight_archive(too_big, limit_bytes=1024 * 1024)
    assert exc.value.reason_code == "too_large"

    ratio = str(tmp_path / "ratio.zip")
    _zip(ratio, [("bomb.bin", b"\0" * (2 * 1024 * 1024))], compression=zipfile.ZIP_DEFLATED)
    with pytest.raises(StreamError) as exc:
        preflight_archive(ratio, limit_bytes=3 * 1024 * 1024)
    assert exc.value.reason_code == "too_large"

    many = str(tmp_path / "many.zip")
    _zip(many, [("a", b"1"), ("b", b"2"), ("c", b"3")])
    with pytest.raises(StreamError) as exc:
        preflight_archive(many, limit_bytes=1024, max_entries=2)
    assert exc.value.reason_code == "too_large"


def test_tar_preflight_and_invalid_archive(tmp_path):
    tar_path = str(tmp_path / "big.tar.gz")
    with tarfile.open(tar_path, "w:gz") as archive:
        info = tarfile.TarInfo("big.bin")
        info.size = 2 * 1024 * 1024
        archive.addfile(info, io.BytesIO(b"\0" * info.size))
    with pytest.raises(StreamError) as exc:
        preflight_archive(tar_path, limit_bytes=1024 * 1024)
    assert exc.value.reason_code == "too_large"

    invalid = str(tmp_path / "not-archive.bin")
    with open(invalid, "wb") as handle:
        handle.write(b"not an archive")
    with pytest.raises(StreamError) as exc:
        preflight_archive(invalid, limit_bytes=1024)
    assert exc.value.reason_code == "invalid_payload"
