"""安全下载器测试（M3-04 / INH-1889，规范 §10.2、§11.2、§11.5）。

本卡要证明的三件事——**每一跳重新校验**、**分块限流**、**日志/消息脱敏**——都会被
「打桩 `urlopen`」一起屏蔽掉：打桩之后既不会发生重定向，也不会真的读流，
更不会留下日志。所以这里一律起本地 `http.server`（127.0.0.1 随机端口）发真实请求：
被策略拒绝的连接必须**根本没发出去**（用服务端记到的请求条数证明），
只有真实 socket 才证得了这一点。

旧签名 `download_file(url, retries)` 的两条兼容回归保留在 `TestLegacySignature` 里，
断言语义与改造前一致（成功返回正文、下载失败返回 None），只是把打桩换成了真实服务器。

`_sleep`、`socket.getaddrinfo`、`urllib.request.getproxies` 三处是环境 seam：
前者让退避序列可断言而不真等，后两者让 DNS/代理行为可复现（开发机上有系统代理）。
"""

from __future__ import annotations

import base64
import dataclasses
import logging
import socket
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional

import pytest

from app.config import Settings
from app.core import downloader
from app.core.downloader import DownloadPolicy, default_policy, fetch
from app.core.errors import DOWNLOAD_FAILED, TOO_LARGE, AppError

XML_BODY = b"<config><a>1</a></config>"
PLAIN_BODY = b"plain text, neither XML nor JSON {{{"
HTML_BODY = b"<html><body><h1>Server Error</h1><p>token=SECRET</p></body></html>"
CHUNK = b"x" * 512


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - http.server 的命名约定
        try:
            self._handle()
        except (BrokenPipeError, ConnectionResetError):
            # 客户端超时后先断开是预期情况（慢响应那几条用例）
            pass

    def _handle(self) -> None:
        self.server.requests.append((self.path, dict(self.headers.items())))
        route = self.server.routes.get(self.path.split("?", 1)[0])
        if route is None:
            self._reply(404, {}, b"not found")
            return
        if "delay" in route:
            time.sleep(route["delay"])
        if "location" in route:
            self._reply(302, {"Location": route["location"]}, b"")
            return
        if "chunks" in route:
            self._reply_chunked(route.get("status", 200), route["chunks"])
            return
        self._reply(route.get("status", 200), route.get("headers", {}), route.get("body", b""))

    def _reply(self, status: int, headers: dict, body: bytes) -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _reply_chunked(self, status: int, chunks) -> None:
        self.send_response(status)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
        self.wfile.write(b"0\r\n\r\n")

    def log_message(self, *args):  # 访问日志会淹掉 pytest 输出
        pass


class _Server:
    """测试用的本地服务器句柄：只提供 URL 与「服务端实际收到过什么」。"""

    def __init__(self, httpd, port: int, host: str = "127.0.0.1") -> None:
        self.httpd = httpd
        self.port = port
        self.host = host

    @property
    def requests(self):
        return self.httpd.requests

    @property
    def paths(self):
        return [path for path, _headers in self.httpd.requests]

    def url(self, path: str = "/config.xml", userinfo: str = "", query: str = "") -> str:
        return "http://%s%s:%d%s%s" % (userinfo, self.host, self.port, path, query)


def _local_test_address() -> Optional[str]:
    """挑一个「能 bind 也能 connect」的本机非回环地址，用于首跳非回环的用例。

    只按地址段猜是不可靠的：开发机上 Clash/TUN 会占走 198.18.0.0/15，bind 成功但
    connect 直接被黑洞吞掉（表现是超时），所以这里逐个做 1 秒的 bind+connect 探针。
    """
    candidates: List[str] = []
    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 53))  # UDP connect 只选路由，不发包
        candidates.append(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()

    for candidate in candidates:
        if candidate.startswith(("127.", "169.254.", "0.")):
            continue
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(1.0)
        try:
            listener.bind((candidate, 0))
            listener.listen(1)
            client.connect(listener.getsockname())
            return candidate
        except OSError:
            continue
        finally:
            client.close()
            listener.close()
    return None


@pytest.fixture()
def servers(monkeypatch):
    """按需起本地服务器（默认 127.0.0.1 随机端口），用例结束统一关掉。

    `getproxies` 打桩是必要的：macOS 上 urllib 会读系统代理设置，那样被测的
    就不是「下载器 → 本地服务器」这条连接了（开发机上带 userinfo 的 URL
    会被丢进代理返回 502，就是这么发现的）。生产路径仍然按环境/系统代理走。
    """
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    started = []

    def make(routes: dict, host: str = "127.0.0.1") -> _Server:
        httpd = ThreadingHTTPServer((host, 0), _Handler)
        httpd.daemon_threads = True
        httpd.routes = routes
        httpd.requests = []
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        started.append((httpd, thread))
        return _Server(httpd, httpd.server_address[1], host)

    yield make

    for httpd, thread in started:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


@pytest.fixture(autouse=True)
def sleep_recorder(monkeypatch):
    """把重试等待注入成记录器：断言退避序列，但不真的睡 1s/2s。"""
    delays = []
    monkeypatch.setattr(downloader, "_sleep", delays.append)
    return delays


def _policy(**overrides) -> DownloadPolicy:
    base = dict(timeout_s=5, retries=1, max_bytes=1024 * 1024, allow=(), block_loopback=False)
    base.update(overrides)
    return DownloadPolicy(**base)


def _all_text(excinfo) -> str:
    """错误消息 + detail 的合并文本，用来断言「什么都没泄漏」。"""
    exc = excinfo.value
    return "%s|%s|%s" % (exc.message, exc.detail, exc.code)


class TestInterfaceContract:
    """卡 5 / 卡 9 按这份签名调用，字段顺序与名称都算契约。"""

    def test_policy_fields_are_stable(self):
        names = [f.name for f in dataclasses.fields(DownloadPolicy)]
        assert names == ["timeout_s", "retries", "max_bytes", "allow", "block_loopback"]

    def test_download_file_keeps_legacy_callable_shape(self):
        import inspect

        spec = inspect.signature(downloader.download_file)
        assert list(spec.parameters) == ["url", "retries"]
        assert callable(fetch)


class TestFiveAcceptanceSamples:
    """卡面验收：本地 http.server 提供五类样本。"""

    def test_small_xml_is_returned(self, servers):
        srv = servers({"/config.xml": {"body": XML_BODY}})
        assert fetch(srv.url(), _policy()) == XML_BODY

    def test_non_xml_json_body_is_returned_verbatim(self, servers):
        """内容判定不属于本卡：正文原样交给上层（卡 5 才判 rejected）。"""
        srv = servers({"/model.txt": {"body": PLAIN_BODY, "headers": {"Content-Type": "text/plain"}}})
        assert fetch(srv.url("/model.txt"), _policy()) == PLAIN_BODY

    def test_html_error_page_served_as_200_is_returned_verbatim(self, servers):
        srv = servers({"/err": {"status": 200, "body": HTML_BODY, "headers": {"Content-Type": "text/html"}}})
        assert fetch(srv.url("/err"), _policy()) == HTML_BODY

    def test_declared_oversize_is_too_large(self, servers):
        srv = servers({"/big.xml": {"body": b"<R>" + b" " * 4096 + b"</R>"}})
        with pytest.raises(AppError) as excinfo:
            fetch(srv.url("/big.xml"), _policy(max_bytes=1024))
        exc = excinfo.value
        assert exc.code == TOO_LARGE and exc.http_status == 413
        assert exc.detail["limit_bytes"] == 1024

    def test_chunked_oversize_is_too_large_without_partial_body(self, servers):
        """无 Content-Length 的分块正文：累计超限就断，且不能把半截字节当配置返回。"""
        srv = servers({"/stream.xml": {"chunks": [CHUNK] * 8}})
        with pytest.raises(AppError) as excinfo:
            fetch(srv.url("/stream.xml"), _policy(max_bytes=1024))
        assert excinfo.value.code == TOO_LARGE
        assert CHUNK[:64].decode("ascii") not in _all_text(excinfo)

    def test_redirect_to_metadata_address_is_download_failed(self, servers):
        srv = servers({"/start.xml": {"location": "http://169.254.169.254/latest/meta-data/"}})
        with pytest.raises(AppError) as excinfo:
            fetch(srv.url("/start.xml"), _policy())
        exc = excinfo.value
        assert exc.code == DOWNLOAD_FAILED and exc.http_status == 502
        assert exc.detail["reason"] == "policy"
        assert "169.254.169.254" in exc.message
        assert srv.paths == ["/start.xml"], "第二跳必须在校验阶段就被拒，不能真的发出去"


class TestRedirectPolicy:
    def test_scheme_change_on_redirect_is_rejected(self, servers):
        srv = servers({"/start.xml": {"location": "file:///etc/passwd"}})
        with pytest.raises(AppError) as excinfo:
            fetch(srv.url("/start.xml"), _policy())
        assert excinfo.value.code == DOWNLOAD_FAILED
        assert excinfo.value.detail["reason"] == "policy"

    def test_whitelist_is_re_evaluated_on_every_hop(self, servers):
        """首跳命中白名单，第二跳指向别的地址时必须重新校验（SSRF 的关键一条）。"""
        srv = servers({"/start.xml": {"location": "http://198.51.100.1/config.xml"}})
        with pytest.raises(AppError) as excinfo:
            fetch(srv.url("/start.xml"), _policy(allow=("127.0.0.1",)))
        assert excinfo.value.code == DOWNLOAD_FAILED
        assert excinfo.value.detail["reason"] == "policy"
        assert "198.51.100.1" in excinfo.value.message
        assert srv.paths == ["/start.xml"]

    def test_two_hops_are_followed_when_each_hop_is_allowed(self, servers):
        """逐跳校验不是「只允许一跳」：每跳都合法的多跳链要能走通。

        第一跳用相对 `Location`（同主机同端口），第二跳指向另一台本地服务器。
        """
        final = servers({"/final.xml": {"body": XML_BODY}})
        middle = servers({"/start.xml": {"location": "/hop2.xml"},
                          "/hop2.xml": {"location": final.url("/final.xml")}})
        assert fetch(middle.url("/start.xml"), _policy()) == XML_BODY
        assert middle.paths == ["/start.xml", "/hop2.xml"]
        assert final.paths == ["/final.xml"]


class TestBlockLoopbackAndAllowList:
    def test_allow_entry_hostname_hit(self, servers):
        srv = servers({"/config.xml": {"body": XML_BODY}})
        host = urllib.parse.urlsplit(srv.url()).hostname
        assert fetch(srv.url(), _policy(allow=(host,))) == XML_BODY

    def test_allow_entry_cidr_hit(self, servers):
        srv = servers({"/config.xml": {"body": XML_BODY}})
        assert fetch(srv.url(), _policy(allow=("127.0.0.0/8",))) == XML_BODY

    def test_allow_entry_miss_is_rejected_before_connect(self, servers):
        srv = servers({"/config.xml": {"body": XML_BODY}})
        with pytest.raises(AppError) as excinfo:
            fetch(srv.url(), _policy(allow=("config.example.com",)))
        assert excinfo.value.code == DOWNLOAD_FAILED
        assert excinfo.value.detail["reason"] == "policy"
        assert srv.requests == [], "白名单未命中时不能发出任何请求"

    def test_block_loopback_rejects_local_server(self, servers):
        srv = servers({"/config.xml": {"body": XML_BODY}})
        with pytest.raises(AppError) as excinfo:
            fetch(srv.url(), _policy(block_loopback=True))
        assert excinfo.value.code == DOWNLOAD_FAILED
        assert excinfo.value.detail["reason"] == "policy"
        assert srv.requests == []

    def test_block_loopback_rejects_redirect_target(self, servers):
        """访客临时导入：首跳是内网真实地址，302 指向 127.0.0.1 也要拦。

        首跳不能用 127.0.0.1（那样在入口就被拒了，测不到「逐跳」），所以绑到本机
        对局域网可见的地址上；第二跳指向回环，端口不必真的在听。
        """
        addr = _local_test_address()
        if not addr:
            pytest.skip("本机没有可 bind 也可 connect 的非回环地址，无法构造「首跳非回环」链路")
        srv = servers({"/start.xml": {"body": XML_BODY}}, host=addr)
        srv.httpd.routes["/start.xml"] = {
            "location": "http://127.0.0.1:%d/inner.xml" % srv.port
        }
        with pytest.raises(AppError) as excinfo:
            fetch(srv.url("/start.xml"), _policy(block_loopback=True))
        assert excinfo.value.code == DOWNLOAD_FAILED
        assert excinfo.value.detail["reason"] == "policy"
        assert srv.paths == ["/start.xml"], "指向回环的第二跳不能真的发出去"

    def test_denied_address_table(self):
        """§10.2/§11.2 的拒绝清单，写死成表格避免实现随性放宽。"""
        import ipaddress

        denied = [
            ("0.0.0.0", False),
            ("::", False),
            ("169.254.169.254", False),
            ("169.254.1.1", False),
            ("fe80::1", False),
            ("::ffff:169.254.169.254", False),
            ("::ffff:0.0.0.0", False),
            ("127.0.0.1", True),
            ("::1", True),
            ("::ffff:127.0.0.1", True),
        ]
        for text, block_loopback in denied:
            assert downloader._is_denied_address(ipaddress.ip_address(text), block_loopback) is True, text

        allowed = [
            ("10.0.0.1", False),
            ("10.0.0.1", True),
            ("172.16.5.9", False),
            ("192.168.1.10", True),
            ("127.0.0.1", False),
            ("8.8.8.8", False),
            ("2001:db8::1", True),
        ]
        for text, block_loopback in allowed:
            assert downloader._is_denied_address(ipaddress.ip_address(text), block_loopback) is False, text


class TestSchemesAndUrlValidation:
    @pytest.mark.parametrize(
        "bad",
        ["file:///etc/passwd", "javascript:alert(1)", "ftp://h/x.xml", "gopher://h:1/x",
         "httpx://127.0.0.1/x.xml", "/relative/only.xml", ""],
    )
    def test_non_http_scheme_is_rejected(self, bad):
        with pytest.raises(AppError) as excinfo:
            fetch(bad, _policy())
        assert excinfo.value.code == DOWNLOAD_FAILED
        assert excinfo.value.detail["reason"] == "policy"

    def test_denied_literal_addresses_never_open_a_socket(self):
        for url in ("http://169.254.169.254/latest/meta-data/", "http://0.0.0.0:8/x",
                    "http://[fe80::1]:80/x", "http://[::ffff:169.254.169.254]:80/x", "http://[::]:80/x"):
            with pytest.raises(AppError) as excinfo:
                fetch(url, _policy())
            assert excinfo.value.code == DOWNLOAD_FAILED, url
            assert excinfo.value.detail["reason"] == "policy", url

    def test_host_without_address_is_connection_failure(self, monkeypatch):
        def refuse(host, port, **kwargs):
            raise socket.gaierror(-2, "Name or service not known")

        monkeypatch.setattr(downloader.socket, "getaddrinfo", refuse)
        with pytest.raises(AppError) as excinfo:
            fetch("http://config.internal.example/config.xml", _policy())
        assert excinfo.value.code == DOWNLOAD_FAILED
        assert excinfo.value.detail["reason"] == "connection"

    def test_hostname_resolving_to_metadata_address_is_rejected(self, monkeypatch):
        """DNS 指到 169.254.169.254（常见 DNS-rebinding 式取巧）同样要在连接前拦下。"""

        def fake(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]

        monkeypatch.setattr(downloader.socket, "getaddrinfo", fake)
        with pytest.raises(AppError) as excinfo:
            fetch("http://anything.example.com/lua", _policy())
        assert excinfo.value.detail["reason"] == "policy"

    def test_mixed_dns_records_are_rejected_when_any_is_denied(self, monkeypatch):
        """一个域名同时给出正常地址和元数据地址时，整体拒绝（任一命中即拒）。

        主机名必须不是 IP 字面量，否则 `_resolve` 不走 DNS，这条用例就测不到东西了。
        """

        def fake(host, port, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
            ]

        monkeypatch.setattr(downloader.socket, "getaddrinfo", fake)
        with pytest.raises(AppError) as excinfo:
            fetch("http://config.corp.example/config.xml", _policy())
        assert excinfo.value.detail["reason"] == "policy"
        assert "169.254.169.254" in excinfo.value.message

    def test_invalid_port_is_rejected(self):
        with pytest.raises(AppError) as excinfo:
            fetch("http://127.0.0.1:notaport/x.xml", _policy())
        assert excinfo.value.code == DOWNLOAD_FAILED
        assert excinfo.value.detail["reason"] == "policy"


class TestFailureReasons:
    def test_http_error_status_reason(self, servers):
        srv = servers({"/config.xml": {"status": 500, "body": HTML_BODY}})
        with pytest.raises(AppError) as excinfo:
            fetch(srv.url(), _policy())
        exc = excinfo.value
        assert exc.code == DOWNLOAD_FAILED
        assert exc.detail["reason"] == "http_status"
        assert "500" in exc.message

    def test_not_found_reason(self, servers):
        srv = servers({"/other.xml": {"body": XML_BODY}})
        with pytest.raises(AppError) as excinfo:
            fetch(srv.url("/missing.xml"), _policy())
        assert excinfo.value.detail["reason"] == "http_status"
        assert "404" in excinfo.value.message

    def test_connection_refused_reason(self, servers):
        srv = servers({"/config.xml": {"body": XML_BODY}})
        srv.httpd.shutdown()
        srv.httpd.server_close()
        with pytest.raises(AppError) as excinfo:
            fetch(srv.url(), _policy(timeout_s=2))
        assert excinfo.value.code == DOWNLOAD_FAILED
        assert excinfo.value.detail["reason"] == "connection"

    def test_timeout_reason(self, servers):
        srv = servers({"/slow.xml": {"delay": 2.5, "body": XML_BODY}})
        with pytest.raises(AppError) as excinfo:
            fetch(srv.url("/slow.xml"), _policy(timeout_s=1))
        assert excinfo.value.detail["reason"] == "timeout"


class TestRetryAndBackoff:
    def test_backoff_is_one_then_two_seconds(self, servers, sleep_recorder):
        srv = servers({"/config.xml": {"status": 500, "body": b"boom"}})
        with pytest.raises(AppError) as excinfo:
            fetch(srv.url(), _policy(retries=3))
        assert excinfo.value.code == DOWNLOAD_FAILED
        assert sleep_recorder == [1.0, 2.0]
        assert srv.paths == ["/config.xml"] * 3, "重试 3 次 = 共 3 次尝试（与旧实现一致）"

    def test_policy_rejection_is_not_retried(self, servers, sleep_recorder):
        srv = servers({"/start.xml": {"location": "http://169.254.169.254/"}})
        with pytest.raises(AppError):
            fetch(srv.url("/start.xml"), _policy(retries=3))
        assert sleep_recorder == [], "被策略拒绝是确定性失败，不该重试也不该等待"
        assert srv.paths == ["/start.xml"]

    def test_too_large_is_not_retried(self, servers, sleep_recorder):
        srv = servers({"/big.xml": {"chunks": [CHUNK] * 8}})
        with pytest.raises(AppError):
            fetch(srv.url("/big.xml"), _policy(retries=3, max_bytes=1024))
        assert sleep_recorder == []
        assert srv.paths == ["/big.xml"]

    def test_redirect_limit_is_five(self, servers):
        # 相对 Location：每次落到同一个自跳转路由上，用来数跳数。
        srv = servers({"/loop.xml": {"location": "/loop.xml"}})
        with pytest.raises(AppError) as excinfo:
            fetch(srv.url("/loop.xml"), _policy())
        assert excinfo.value.code == DOWNLOAD_FAILED
        assert excinfo.value.detail["reason"] == "policy"
        assert "重定向" in excinfo.value.message
        assert len(srv.paths) == 6, "首请求 + 最多 5 跳"


class TestSettingsWiring:
    def test_default_policy_reads_settings_fields(self, monkeypatch):
        monkeypatch.setenv("MCHECKER_DOWNLOAD_ALLOW", "config.example.com,10.0.0.0/8")
        monkeypatch.setenv("MCHECKER_UPLOAD_MAX_MB", "7")
        policy = default_policy()
        assert policy.allow == ("config.example.com", "10.0.0.0/8")
        assert policy.max_bytes == 7 * 1024 * 1024

    def test_default_policy_defaults_match_config_defaults(self):
        from app import config

        policy = default_policy(Settings())
        assert policy.timeout_s == config.DEFAULT_DOWNLOAD_TIMEOUT_S
        assert policy.retries == config.DEFAULT_DOWNLOAD_RETRIES
        assert policy.max_bytes == config.DEFAULT_UPLOAD_MAX_MB * 1024 * 1024
        assert policy.allow == () and policy.block_loopback is False

    def test_policy_can_be_built_from_explicit_settings(self):
        policy = default_policy(Settings(download_timeout_s=11, download_retries=5, upload_max_bytes=333))
        assert (policy.timeout_s, policy.retries, policy.max_bytes) == (11, 5, 333)

    def test_download_file_takes_retries_from_settings(self, servers, monkeypatch):
        """超时/重试不再写死在 downloader 里：调用方不传时按 Settings。"""
        settings = Settings(download_timeout_s=3, download_retries=2)
        monkeypatch.setattr(downloader, "load_settings", lambda *a, **kw: settings)
        srv = servers({"/config.xml": {"status": 500, "body": b"boom"}})
        assert downloader.download_file(srv.url()) is None
        assert srv.paths == ["/config.xml"] * 2


class TestLegacySignature:
    """M2-10 `services/upload.py` 依赖的两条语义，逐条保留。"""

    def test_download_success_returns_bytes(self, servers):
        srv = servers({"/config.xml": {"body": XML_BODY}})
        assert downloader.download_file(srv.url()) == XML_BODY

    def test_download_failure_returns_none(self, servers):
        srv = servers({"/other.xml": {"body": XML_BODY}})
        assert downloader.download_file(srv.url("/missing.xml"), retries=1) is None
        assert srv.paths == ["/missing.xml"], "retries=1 只尝试一次"

    def test_explicit_retries_overrides_settings(self, servers):
        srv = servers({"/config.xml": {"status": 500, "body": b"boom"}})
        assert downloader.download_file(srv.url(), retries=2) is None
        assert srv.paths == ["/config.xml"] * 2

    def test_too_large_is_passed_through_not_collapsed(self, servers, monkeypatch):
        """R4：超限必须让 AppError(TOO_LARGE) 向上抛，服务边界才是 413；
        下载类失败仍返回 None，保住 UC-29 的 502 与文案。"""
        big = b"<R>" + b" " * 4096 + b"</R>"
        srv = servers({"/big.xml": {"body": big}})
        settings = Settings(download_timeout_s=3, download_retries=1, upload_max_bytes=1024)
        monkeypatch.setattr(downloader, "load_settings", lambda *a, **kw: settings)
        with pytest.raises(AppError) as excinfo:
            downloader.download_file(srv.url("/big.xml"))
        assert excinfo.value.code == TOO_LARGE and excinfo.value.http_status == 413

    def test_download_file_rejects_file_scheme(self, servers):
        assert downloader.download_file("file:///etc/passwd") is None


class TestCredentialHygiene:
    """§11.5：日志与错误消息只允许 `scheme://host/path`。"""

    def test_logs_do_not_contain_userinfo_or_query(self, servers, caplog):
        srv = servers({"/config.xml": {"body": XML_BODY}})
        url = srv.url(userinfo="user:pass@", query="?token=QUERYSECRET")
        with caplog.at_level(logging.DEBUG, logger="app.core.downloader"):
            assert downloader.download_file(url) == XML_BODY
        text = caplog.text
        for secret in ("QUERYSECRET", "user:pass", "token=", "pass@"):
            assert secret not in text, secret
        assert "http://127.0.0.1:%d/config.xml" % srv.port in text
        # 脱敏只作用于日志与消息：请求本身照样带着 query 出去，否则功能就废了
        assert srv.requests[0][0] == "/config.xml?token=QUERYSECRET"

    def test_error_message_excludes_query_userinfo_and_body(self, servers):
        srv = servers({"/config.xml": {"status": 500, "body": HTML_BODY}})
        url = srv.url(userinfo="user:pass@", query="?token=QUERYSECRET")
        with pytest.raises(AppError) as excinfo:
            fetch(url, _policy())
        text = _all_text(excinfo)
        for secret in ("QUERYSECRET", "user:pass", "token=", "SECRET", "pass@"):
            assert secret not in text, secret
        assert "config.xml" in text

    def test_policy_rejection_message_is_masked(self, servers):
        srv = servers({"/start.xml": {"location": "http://169.254.169.254/meta?token=REDIRECTSECRET"}})
        with pytest.raises(AppError) as excinfo:
            fetch(srv.url("/start.xml", userinfo="user:pass@", query="?token=QUERYSECRET"), _policy())
        text = _all_text(excinfo)
        assert "REDIRECTSECRET" not in text
        assert "QUERYSECRET" not in text
        assert "user:pass" not in text

    def test_basic_auth_from_userinfo_is_used_for_the_request(self, servers):
        expected = "Basic " + base64.b64encode(b"user:pass").decode("ascii")
        srv = servers({"/config.xml": {"body": XML_BODY}})
        assert fetch(srv.url(userinfo="user:pass@"), _policy()) == XML_BODY
        assert srv.requests[0][1]["Authorization"] == expected

    def test_authorization_is_not_forwarded_to_another_host(self, servers):
        target = servers({"/final.xml": {"body": XML_BODY}})
        source = servers({"/start.xml": {"location": "http://localhost:%d/final.xml" % target.port}})
        assert fetch(source.url("/start.xml", userinfo="user:pass@"), _policy()) == XML_BODY
        assert "Authorization" in source.requests[0][1]
        assert "Authorization" not in target.requests[0][1], "跨主机重定向不得把凭据带过去"

    def test_url_with_userinfo_does_not_break_the_connection(self, servers):
        """带凭据的 URL 由本模块拆成「主机 + Authorization 头」：连接目标不含凭据，
        也就不会把凭据混进 Host 头或系统代理。"""
        srv = servers({"/config.xml": {"body": XML_BODY}})
        assert fetch(srv.url(userinfo="user:pass@"), _policy()) == XML_BODY
        assert "@" not in srv.requests[0][1]["Host"]
