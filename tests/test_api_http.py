"""HTTP 骨架（INH-1866 / M0-09；规范 §3.4、§3.5、§8.9、§14.2）。

按卡片要求：在**线程里**用随机端口启动真实 server，`http.client` 发请求，
测完关闭。
"""

from __future__ import annotations

import gzip
import http.client
import json
import os
import socket
import subprocess
import sys
import threading

import pytest

from app import __version__
from app.config import Settings

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "web")


@pytest.fixture()
def server(tmp_path):
    from app.server.http import MCServer
    from app.server.router import build_router

    settings = Settings(
        host="127.0.0.1",
        port=0,  # 由系统分配随机端口
        data_dir=str(tmp_path / "data"),
        web_dir=WEB_DIR,
    )
    srv = MCServer(settings, build_router(settings))
    thread = threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def request(server, method: str, path: str, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def raw_request(server, text: str) -> bytes:
    with socket.create_connection(("127.0.0.1", server.server_port), timeout=10) as sock:
        sock.sendall(text.encode("utf-8"))
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)


class TestStaticFiles:
    def test_root_serves_production_index(self, server):
        status, headers, body = request(server, "GET", "/")
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        assert b"<script src=\"config.js\"></script>" in body
        assert b"assets/js/mock" not in body  # 生产入口不加载 mock（§8.9）
        assert headers.get("ETag")

    def test_not_modified_returns_304(self, server):
        _, headers, _ = request(server, "GET", "/")
        status, _, body = request(server, "GET", "/", headers={"If-None-Match": headers["ETag"]})
        assert status == 304
        assert body == b""

    def test_css_is_served(self, server):
        status, headers, body = request(server, "GET", "/assets/css/tokens.css")
        assert status == 200
        assert headers["Content-Type"].startswith("text/css")
        assert len(body) > 0

    def test_config_js_content(self, server):
        status, headers, body = request(server, "GET", "/config.js")
        assert status == 200
        assert headers["Content-Type"].startswith("application/javascript")
        assert body.decode("utf-8") == "window.MC=window.MC||{};window.MC.config={mock:false};"

    def test_mock_template_and_tests_are_hidden(self, server):
        for path in (
            "/assets/js/mock/mock-api.js",
            "/assets/js/mock/docparse.js",
            "/template.html",
            "/tests/index.html",
            "/tests/tests.js",
        ):
            status, _, body = request(server, "GET", path)
            assert status == 404, path
            assert json.loads(body.decode("utf-8"))["error"]["code"] == "NOT_FOUND"

    def test_large_static_is_gzipped(self, server):
        status, headers, body = request(
            server, "GET", "/assets/css/components.css", headers={"Accept-Encoding": "gzip"}
        )
        assert status == 200
        assert headers.get("Content-Encoding") == "gzip"
        assert headers.get("Vary") == "Accept-Encoding"
        with open(os.path.join(WEB_DIR, "assets", "css", "components.css"), "rb") as fh:
            assert gzip.decompress(body) == fh.read()

    def test_large_static_not_gzipped_without_accept_encoding(self, server):
        status, headers, _ = request(server, "GET", "/assets/css/components.css")
        assert status == 200
        assert "Content-Encoding" not in headers

    @pytest.mark.parametrize(
        "path", ["/assets/../run.py", "/assets/%2e%2e/run.py", "/assets/../../etc/passwd"]
    )
    def test_path_traversal_is_refused(self, server, path):
        status, _, body = request(server, "GET", path)
        assert status == 404
        assert json.loads(body.decode("utf-8"))["error"]["code"] == "NOT_FOUND"

    def test_missing_asset_is_404(self, server):
        status, _, _ = request(server, "GET", "/assets/css/nope.css")
        assert status == 404


class TestApiSession:
    @pytest.mark.production_identity
    def test_session_shape_and_local_identity(self, server):
        """本机回环地址不再自动成为管理员。"""
        status, headers, body = request(server, "GET", "/api/v1/session")
        assert status == 200
        assert headers["Content-Type"].startswith("application/json")
        assert "访客".encode("utf-8") in body
        assert b"\\u8bbf" not in body

        payload = json.loads(body.decode("utf-8"))
        assert payload["identity"]["ip"] == "127.0.0.1"
        assert payload["identity"]["is_deployer"] is False
        assert payload["identity"]["is_admin"] is False
        assert payload["identity"]["registered"] is False
        assert payload["identity"]["role_label"] == "访客"
        assert payload["profile"]["id"] == "default"
        assert payload["profiles"] == [{"id": "default", "name": "默认机型"}]
        assert payload["server"]["version"] == __version__
        assert payload["server"]["scheduler"] is False
        assert payload["limits"]["tab_limit"] == 15
        assert payload["limits"]["upload_max_bytes"] == 50 * 1024 * 1024
        assert payload["limits"]["temp_ttl_hours"] == 24

    def test_unknown_route_returns_error_envelope(self, server):
        status, _, body = request(server, "GET", "/api/v1/nope")
        assert status == 404
        error = json.loads(body.decode("utf-8"))["error"]
        assert error["code"] == "NOT_FOUND"
        assert isinstance(error["message"], str) and error["message"]
        assert error["detail"] == {}

    def test_post_without_content_length(self, server):
        # 不支持 chunked：没有 Content-Length 按 §3.4 第 3 步处理（写请求需带自定义头）
        data = raw_request(
            server,
            "POST /api/v1/session HTTP/1.1\r\nHost: test\r\n"
            "X-Requested-With: MCChecker\r\nConnection: close\r\n\r\n",
        )
        assert b"413" in data.split(b"\r\n", 1)[0]
        assert b"TOO_LARGE" in data

    def test_post_over_limit(self, server):
        from app.server.http import JSON_BODY_LIMIT

        data = raw_request(
            server,
            "POST /api/v1/session HTTP/1.1\r\nHost: test\r\n"
            "X-Requested-With: MCChecker\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: %d\r\nConnection: close\r\n\r\n" % (JSON_BODY_LIMIT + 1),
        )
        assert b"413" in data.split(b"\r\n", 1)[0]
        assert b"TOO_LARGE" in data

    def test_post_within_limit_is_not_rejected_by_size(self, server):
        # 有 Content-Length 且在限内：不会被 413 拦下，而是走到路由（占位阶段无 POST 路由 → 404）
        status, _, _ = request(
            server,
            "POST",
            "/api/v1/session",
            body=b"{}",
            headers={"Content-Type": "application/json", "X-Requested-With": "MCChecker"},
        )
        assert status == 404

    def test_write_without_csrf_header_is_forbidden(self, server):
        # §11.4：状态变更请求必须带自定义头（其它安全头在 M4-02）
        status, _, body = request(
            server,
            "POST",
            "/api/v1/session",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )
        assert status == 403
        assert json.loads(body.decode("utf-8"))["error"]["code"] == "FORBIDDEN"


class TestSingleInstanceLock:
    def test_second_instance_is_refused(self, tmp_path):
        from app.server.http import SingleInstanceLock

        first = SingleInstanceLock(str(tmp_path), 50002)
        first.acquire()
        try:
            payload = json.loads((tmp_path / ".mcchecker.lock").read_text(encoding="utf-8"))
            assert payload["pid"] == os.getpid()
            assert payload["port"] == 50002
            assert payload["started_at"]

            second = SingleInstanceLock(str(tmp_path), 50002)
            with pytest.raises(RuntimeError) as excinfo:
                second.acquire()
            assert "已在运行" in str(excinfo.value)
        finally:
            first.release()
        assert not (tmp_path / ".mcchecker.lock").exists()

    def test_stale_lock_is_taken_over(self, tmp_path):
        from app.server.http import SingleInstanceLock

        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        dead_pid = proc.pid
        proc.wait()

        (tmp_path / ".mcchecker.lock").write_text(
            json.dumps({"pid": dead_pid, "port": 1, "started_at": "2026-01-01 00:00:00"}),
            encoding="utf-8",
        )
        lock = SingleInstanceLock(str(tmp_path), 50002)
        lock.acquire()
        try:
            payload = json.loads((tmp_path / ".mcchecker.lock").read_text(encoding="utf-8"))
            assert payload["pid"] == os.getpid()
        finally:
            lock.release()

    def test_corrupt_lock_file_is_taken_over(self, tmp_path):
        from app.server.http import SingleInstanceLock

        (tmp_path / ".mcchecker.lock").write_text("not json", encoding="utf-8")
        lock = SingleInstanceLock(str(tmp_path), 50002)
        lock.acquire()
        lock.release()
        assert not (tmp_path / ".mcchecker.lock").exists()
