"""临时文件、上传保存与链接导入（§7.4、§5.3、§5.4；M2-10）。

覆盖：`POST /temp` 建档与解析校验、所有者 = 同 cid **且** 同 IP（他人 404）、过期 404
且绝不回退读同名服务器文件（F01）、配额与体积上限、惰性清理、`PUT /files/{name}` 的
201/200/409/unchanged 与权限、`POST /import-url` 的 http/https 校验与下载失败 502、
以及 M2-08 遗留的「temp 侧参与对比」。

`TestImportUrlLoopbackGuard` 是 N2 / INH-1913 补上的那一半：`persist=false` 的字节落在
**访客可见的临时区**，所以这条调用路径必须额外拒绝回环地址（SSRF 收口）。这里一律起真实
`http.server` 而不是打桩下载器——口径同 `tests/test_downloader.py` 开头的说明：只有真连接
才能证明「被策略拒绝的请求根本没发出去」。
"""

from __future__ import annotations

import json
import os
import socket
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from authz_fixtures import as_role, make_server, seed_roles, sha256_of, write_target

# `_local_test_address` 是 `tests/test_downloader.py` 的环境探针：挑一个「能 bind 也能
# connect」的本机非回环地址。逐跳校验的用例必须有非回环首跳（否则在入口就被拒了，测不到
# 「每一跳重新校验」），而按地址段猜在装有 Clash/TUN 的开发机上会撞上黑洞，所以复用同一个
# 探针，不在这里重新实现一遍。
from test_downloader import _local_test_address

from app.config import DEFAULT_UPLOAD_MAX_MB
from app.core import downloader, storage
from app.core.errors import DOWNLOAD_FAILED, TOO_LARGE, AppError
from app.services import revision, temp_store, upload

FILE_A = "scenario_a.xml"
OTHER = "someone-else"
MINE = "my-cid"

XML_V1 = "<R><a>1</a></R>\n"
XML_V2 = "<R><a>2</a></R>\n"
BROKEN = b"<R><oops"


def _temp_url(pid: str) -> str:
    return "/api/v1/p/%s/temp" % pid


def _upload(caller, pid, data: bytes, name="up.xml", cid=MINE):
    # 前端按 C26 发 `encodeURIComponent(name)`，HTTP 层再 unquote 回来。
    headers = {"X-Filename": urllib.parse.quote(name)}
    if cid:
        headers["Cookie"] = "mc_cid=%s" % cid
    return caller.request("POST", _temp_url(pid), body=data, headers=headers)


def _token_of(caller, pid, data=XML_V2.encode("utf-8"), name="up.xml", cid=MINE) -> str:
    status, _h, body = _upload(caller, pid, data, name, cid)
    assert status == 201, body
    return json.loads(body)["token"]


def _expire(pid: str, token: str) -> None:
    """把 meta 的到期时间改到过去（等价于时间真的走过去了，比等 24 小时可靠）。"""
    path = os.path.join(storage.get_temporary_dir(token), "meta.json")
    with open(path, "rb") as fh:
        meta = json.loads(fh.read().decode("utf-8"))
    meta["expires_at"] = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh)


#: `downloader.fetch` 的失败面：真实实现里下载类失败抛的就是这个码（502），
#: 按同一口径打桩，省得在 lambda 里塞 raise。
def _download_failed_fetch(url, policy):
    raise AppError(DOWNLOAD_FAILED, "下载失败：连接超时")


class TestTempUpload:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("tmp")
        write_target(self.pid, FILE_A, XML_V1.encode("utf-8"))

    def test_create_returns_meta(self):
        with make_server() as server:
            caller = as_role(server, "visitor")
            status, _h, body = _upload(caller, self.pid, XML_V2.encode("utf-8"), "我 的.xml")
            assert status == 201, body
            payload = json.loads(body)
            assert set(payload) == {"token", "name", "format", "size", "expires_at"}
            assert payload["name"] == "我 的.xml"
            assert payload["format"] == "xml"
            assert payload["size"] == len(XML_V2.encode("utf-8"))
            assert datetime.strptime(payload["expires_at"], "%Y-%m-%d %H:%M:%S") > datetime.now()

    def test_unparsable_upload_is_422_and_writes_nothing(self):
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = _upload(caller, self.pid, BROKEN, "bad.xml")
            assert status == 422, body
            payload = json.loads(body)
            assert payload["error"]["code"] == "PARSE_FAILED"
            assert payload["error"]["detail"]["raw_available"] is True
        assert temp_store.total_bytes() == 0

    def test_owner_must_match_both_cid_and_ip(self):
        with make_server() as server:
            mine = as_role(server, "member")
            token = _token_of(mine, self.pid)
            url = "%s/document" % _temp_url(self.pid).rsplit("/temp", 1)[0]
            doc_url = "/api/v1/p/%s/temp/%s/document" % (self.pid, token)

            status, _h, body = mine.request("GET", doc_url, headers={"Cookie": "mc_cid=%s" % MINE})
            assert status == 200, body
            payload = json.loads(body)
            assert payload["file"]["kind"] == "temp"
            assert payload["file"]["token"] == token
            assert payload["file"]["read_only"] is True
            assert payload["file"]["expires_at"]
            assert payload["nodes"]["l"][0] == "up.xml"

            other_cid = as_role(server, "member").request(
                "GET", doc_url, headers={"Cookie": "mc_cid=%s" % OTHER}
            )
            assert other_cid[0] == 404, other_cid[2]

            other_ip = as_role(server, "visitor")
            status, _h, body = other_ip.request(
                "GET", doc_url, headers={"Cookie": "mc_cid=%s" % MINE}
            )
            assert status == 404, body

    def test_expired_token_is_404_and_never_falls_back_to_server_file(self):
        """F01：临时文件过期后绝不退回读同名服务器文件。"""
        with make_server() as server:
            caller = as_role(server, "member")
            token = _token_of(caller, self.pid, XML_V2.encode("utf-8"), FILE_A)
            _expire(self.pid, token)
            doc_url = "/api/v1/p/%s/temp/%s/document" % (self.pid, token)
            status, _h, body = caller.request(
                "GET", doc_url, headers={"Cookie": "mc_cid=%s" % MINE}
            )
            assert status == 404, body
            assert json.loads(body)["error"]["message"] == temp_store.EXPIRED_MESSAGE
            # 同名服务器文件仍在，且内容没有被这次访问改写
            raw = caller.request("GET", "/api/v1/p/%s/files/%s/raw" % (self.pid, FILE_A))
            assert raw[0] == 200 and raw[2].decode("utf-8") == XML_V1

    def test_purge_is_lazy(self):
        with make_server() as server:
            caller = as_role(server, "member")
            token = _token_of(caller, self.pid)
            _expire(self.pid, token)
            assert os.path.isdir(storage.get_temporary_dir(token))
            revision_before = revision.snapshot(self.pid)["parts"]
            _token_of(caller, self.pid, XML_V2.encode("utf-8"), "second.xml")
            assert not os.path.isdir(storage.get_temporary_dir(token)), "下一次写入时应顺手清掉过期目录"
            assert revision.snapshot(self.pid)["parts"] == revision_before

    def test_per_owner_limit_is_413(self):
        with make_server() as server:
            caller = as_role(server, "member")
            for i in range(temp_store.MAX_PER_OWNER):
                _token_of(caller, self.pid, XML_V2.encode("utf-8"), "f%02d.xml" % i)
            status, _h, body = _upload(caller, self.pid, XML_V2.encode("utf-8"), "over.xml")
            assert status == 413, body
            assert json.loads(body)["error"]["code"] == "TOO_LARGE"
            # 别的 cid 不受影响
            other = _token_of(as_role(server, "member"), self.pid, XML_V2.encode("utf-8"), "x.xml", cid=OTHER)
            assert other

    def test_oversize_is_413_at_the_service_boundary(self):
        """HTTP 层在读 body 前就按上限拒（连接会被关掉），所以这里在服务边界断言。"""
        big = b"<R>" + b" " * 4096 + b"</R>"
        try:
            upload.save_bytes(self.pid, "big.xml", big, True, 1024)
        except AppError as exc:
            assert exc.code == TOO_LARGE and exc.http_status == 413
        else:
            raise AssertionError("超过 upload_max_bytes 必须 413")
        try:
            temp_store.create(self.pid, MINE, "10.0.0.2", "big.xml", big, 24, 1024)
        except AppError as exc:
            assert exc.code == TOO_LARGE and exc.http_status == 413
        else:
            raise AssertionError("临时文件同样要守体积上限")

    def test_raw_download_keeps_name_and_bytes(self):
        with make_server() as server:
            caller = as_role(server, "member")
            token = _token_of(caller, self.pid, XML_V2.encode("utf-8"), "我 的.xml")
            status, headers, body = caller.request(
                "GET",
                "/api/v1/p/%s/temp/%s/raw" % (self.pid, token),
                headers={"Cookie": "mc_cid=%s" % MINE},
            )
            assert status == 200, body
            assert body.decode("utf-8") == XML_V2
            disposition = headers.get("Content-Disposition") or headers.get("content-disposition")
            assert "filename*=UTF-8''" in disposition

    def test_unknown_and_unsafe_tokens_are_404(self):
        with make_server() as server:
            caller = as_role(server, "member")
            for token in ("nope", "..%2F..%2Fetc", "a" * 200):
                status, _h, body = caller.request(
                    "GET",
                    "/api/v1/p/%s/temp/%s/document" % (self.pid, token),
                    headers={"Cookie": "mc_cid=%s" % MINE},
                )
                assert status == 404, (token, body)


class TestPutFile:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("put")
        write_target(self.pid, FILE_A, XML_V1.encode("utf-8"))
        self.url = "/api/v1/p/%s/files/%s" % (self.pid, FILE_A)

    def _put(self, caller, data: bytes, path=None):
        return caller.request("PUT", path or self.url, body=data)

    def test_new_file_is_201_and_bumps_files(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            url = "/api/v1/p/%s/files/fresh.xml" % self.pid
            status, _h, body = self._put(caller, XML_V1.encode("utf-8"), url)
            assert status == 201, body
            payload = json.loads(body)
            assert payload == {"file": {"name": "fresh.xml"}, "archived": None, "unchanged": False}
            assert revision.snapshot(self.pid)["parts"]["files"] == 2

    def test_overwrite_without_flag_is_409_and_bytes_untouched(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            before = sha256_of(self.pid, FILE_A)
            status, _h, body = self._put(caller, XML_V2.encode("utf-8"))
            assert status == 409, body
            assert json.loads(body)["error"]["code"] == "FILE_EXISTS"
            assert sha256_of(self.pid, FILE_A) == before

    def test_overwrite_with_flag_archives_old_content(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _h, body = self._put(caller, XML_V2.encode("utf-8"))
            assert status == 409
            status, _h, body = caller.request(
                "PUT", self.url + "?overwrite=1", body=XML_V2.encode("utf-8")
            )
            assert status == 200, body
            payload = json.loads(body)
            assert payload["unchanged"] is False
            assert payload["archived"]
            assert storage.load_config_file(FILE_A).decode("utf-8") == XML_V2

    def test_same_content_writes_nothing_and_does_not_bump(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            before = revision.snapshot(self.pid)["parts"]["files"]
            status, _h, body = caller.request(
                "PUT", self.url + "?overwrite=1", body=XML_V1.encode("utf-8")
            )
            assert status == 200, body
            payload = json.loads(body)
            assert payload["unchanged"] is True and payload["archived"] is None
            assert revision.snapshot(self.pid)["parts"]["files"] == before

    def test_member_is_403_and_file_unchanged(self):
        with make_server() as server:
            caller = as_role(server, "member")
            before = sha256_of(self.pid, FILE_A)
            status, _h, body = self._put(caller, XML_V2.encode("utf-8"))
            assert status == 403, body
            assert json.loads(body)["error"]["detail"]["action"] == "upload_persist"
            assert sha256_of(self.pid, FILE_A) == before

    def test_unparsable_body_is_422_and_keeps_old_file(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            before = sha256_of(self.pid, FILE_A)
            status, _h, body = caller.request(
                "PUT", self.url + "?overwrite=1", body=BROKEN
            )
            assert status == 422, body
            assert sha256_of(self.pid, FILE_A) == before

    def test_traversal_in_name_is_400(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            url = "/api/v1/p/%s/files/..%%2Fescape.xml" % self.pid
            status, _h, body = self._put(caller, XML_V1.encode("utf-8"), url)
            assert status in (400, 404), body


class TestImportUrl:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("imp")
        write_target(self.pid, FILE_A, XML_V1.encode("utf-8"))

    def test_non_http_url_is_400(self):
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = caller.json(
                "POST", "/api/v1/p/%s/import-url" % self.pid, {"url": "ftp://x/y.xml"}
            )
            assert status == 400, body
            assert body["error"]["detail"]["field"] == "url"

    def test_download_failure_is_502(self, monkeypatch):
        # N2 之后 `import_url` 走的是 `downloader.fetch(url, policy)`，打桩点跟着挪到这个
        # 原语上（旧 `download_file` 已经不是这条路径的入口）。断言逐字未改。
        monkeypatch.setattr(downloader, "fetch", _download_failed_fetch)
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = caller.json(
                "POST", "/api/v1/p/%s/import-url" % self.pid, {"url": "http://example.invalid/a.xml"}
            )
            assert status == 502, body
            assert body["error"]["code"] == "DOWNLOAD_FAILED"

    def test_temp_import_then_compare_with_current(self, monkeypatch):
        """补 M2-08 留下的那条：temp 侧现在真的能参与对比（UC-17 的服务端部分）。"""
        monkeypatch.setattr(downloader, "fetch", lambda url, policy: XML_V2.encode("utf-8"))
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = caller.json(
                "POST",
                "/api/v1/p/%s/import-url" % self.pid,
                {"url": "http://example.invalid/%s" % FILE_A, "name": "from-net.xml"},
            )
            assert status == 200, body
            assert body["persisted"] is False and body["name"] == "from-net.xml"
            assert storage.load_config_file(FILE_A).decode("utf-8") == XML_V1, "服务器文件不得被改"

            status, _h, cmp = caller.json(
                "POST",
                "/api/v1/p/%s/compare" % self.pid,
                {"base": {"file": FILE_A, "version": "current"}, "target": {"temp": body["token"]}},
            )
            assert status == 200, cmp
            assert cmp["target"]["label"] == "临时文件（仅自己可见）"
            assert cmp["structural"]["summary"]["modified"] == 1

    def test_persist_requires_deployer_and_writes_file(self, monkeypatch):
        monkeypatch.setattr(downloader, "fetch", lambda url, policy: XML_V2.encode("utf-8"))
        payload = {
            "url": "http://example.invalid/new.xml",
            "name": "imported.xml",
            "persist": True,
        }
        with make_server() as server:
            status, _h, body = as_role(server, "member").json(
                "POST", "/api/v1/p/%s/import-url" % self.pid, payload
            )
            assert status == 403, body
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").json(
                "POST", "/api/v1/p/%s/import-url" % self.pid, payload
            )
            assert status == 200, body
            assert body["persisted"] is True and body["file"]["name"] == "imported.xml"
            assert body["archived"] is None
            assert storage.load_config_file("imported.xml").decode("utf-8") == XML_V2


# ---------------------------------------------------------------------------
# N2 / INH-1913：访客临时导入的回环地址拒绝（SSRF 收口）
# ---------------------------------------------------------------------------


class _LocalHandler(BaseHTTPRequestHandler):
    """最小「受害者」服务器：按路由回正文 / 302 / 谎报的 Content-Length，并记下收到的路径。"""

    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - http.server 的命名约定
        try:
            self.server.requests.append(self.path)
            route = self.server.routes.get(self.path.split("?", 1)[0])
            if route is None:
                # 没登记的路径按 404 回：探测用例要靠「端口开着但路径没有」这一侧拿 HTTP 错误。
                status, headers, body, declared = 404, {}, b"not found", None
            else:
                status = route.get("status", 200)
                headers = {}
                body = route.get("body", b"")
                declared = route.get("declared")
                if "location" in route:
                    status, headers, body = 302, {"Location": route["location"]}, b""
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            # `declared` 只声明不发送：正文有 50 MB 那么大的用例没必要真的传 50 MB。
            self.send_header("Content-Length", str(len(body) if declared is None else declared))
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # 下载器判定超限后就先断开连接，是预期情况
            pass

    def log_message(self, *args):  # 访问日志会淹掉 pytest 输出
        pass


class _Served:
    """服务器句柄：只暴露 URL 与「真的收到过哪些请求」。"""

    def __init__(self, httpd, host: str) -> None:
        self._httpd = httpd
        self.host = host
        self.port = httpd.server_address[1]

    @property
    def paths(self):
        return list(self._httpd.requests)

    def url(self, path: str = "/config.xml") -> str:
        return "http://%s:%d%s" % (self.host, self.port, path)


@pytest.fixture()
def local_server(monkeypatch):
    """按需起本地真实服务器（默认 127.0.0.1 随机端口），用例结束统一关掉。

    `getproxies` 打桩的理由与 `tests/test_downloader.py` 相同：macOS 上 urllib 会读系统
    代理设置，那样被测的就不是「下载器 → 本地服务器」这条连接了。
    """
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    started = []

    def make(routes: dict, host: str = "127.0.0.1") -> _Served:
        httpd = ThreadingHTTPServer((host, 0), _LocalHandler)
        httpd.daemon_threads = True
        httpd.routes = routes
        httpd.requests = []
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return _Served(httpd, host)

    yield make

    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(autouse=True)
def no_real_retry_backoff(monkeypatch):
    """重试退避统一走 `downloader._sleep` 这个既有 seam，于是本地服务器用例不必真睡。

    口径与 `tests/test_downloader.py` 的 autouse 夹具一致。本文件**不**断言退避序列
    （那是下载器那张卡的职责），所以打桩不会让任何断言变松；它只是免得「探测口径」那
    两条各白等 1 s + 2 s。
    """
    delays = []
    monkeypatch.setattr(downloader, "_sleep", delays.append)
    return delays


class TestImportUrlLoopbackGuard:
    """`persist` 决定下载策略：访客临时导入拒绝回环，部署者分支照旧放行。

    三条不变量各自钉住一条用例，删掉任何一条就少一块证明：
    ① 访客分支被拒且**什么都没落地**（临时区是访客可见的，这才是本卡要关的门）；
    ② 部署者分支同一个地址照样成功（内网更新源不能坏）；
    ③ 逐跳重新校验在这个调用点也生效（首跳非回环、302 指向回环照样拒）。
    """

    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("ssrf")
        write_target(self.pid, FILE_A, XML_V1.encode("utf-8"))

    def _import(self, caller, url: str, persist: bool = False):
        return caller.json(
            "POST", "/api/v1/p/%s/import-url" % self.pid, {"url": url, "persist": persist}
        )

    def test_temp_import_refuses_loopback_and_lands_nothing(self, local_server):
        srv = local_server({"/victim.xml": {"body": XML_V2.encode("utf-8")}})
        with make_server() as server:
            status, _h, body = self._import(as_role(server, "visitor"), srv.url("/victim.xml"))
        assert status == 502, body
        assert body["error"]["code"] == DOWNLOAD_FAILED, "拒绝要用既有的下载失败口径，不另立错误码"
        assert srv.paths == [], "被策略拒绝的连接必须根本没发出去：连一次都没碰，才不泄漏端口开合"
        assert os.listdir(storage.get_temporary_dir()) == [], "拒绝后临时区必须一条记录都不留"
        assert temp_store.total_bytes() == 0

    def test_deployer_still_imports_the_same_loopback_url(self, local_server):
        """同一条 URL 在 `persist=true` 上照旧成功：改动只收在访客面上。"""
        srv = local_server({"/internal.xml": {"body": XML_V2.encode("utf-8")}})
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _h, body = self._import(caller, srv.url("/internal.xml"), persist=True)
            assert status == 200, body
            assert body["persisted"] is True and body["file"]["name"] == "internal.xml"
            assert storage.load_config_file("internal.xml").decode("utf-8") == XML_V2
        assert srv.paths == ["/internal.xml"], "部署者分支要真的取到内容，不能被顺手一起拦掉"

    def test_temp_import_refuses_redirect_into_loopback(self, local_server):
        """首跳是内网真实地址、302 指向 127.0.0.1：策略必须逐跳重新执行。"""
        addr = _local_test_address()
        if not addr:
            pytest.skip("本机没有可 bind 也可 connect 的非回环地址，无法构造「首跳非回环」链路")
        victim = local_server({"/inner.xml": {"body": XML_V2.encode("utf-8")}})
        front = local_server(
            {"/start.xml": {"location": "http://127.0.0.1:%d/inner.xml" % victim.port}}, host=addr
        )
        with make_server() as server:
            status, _h, body = self._import(
                as_role(server, "visitor"), front.url("/start.xml")
            )
        assert status == 502, body
        assert body["error"]["code"] == DOWNLOAD_FAILED
        assert victim.paths == [], "指向回环的第二跳不能真的发出去"
        assert front.paths == ["/start.xml"], "只有首跳真的发生过"

    def test_oversize_response_is_413_on_deployer_branch(self, local_server):
        """`TOO_LARGE` 不许在这条路径上塌成 502：超限仍然是 413（卡 4 的 R4 语义）。"""
        too_big = DEFAULT_UPLOAD_MAX_MB * 1024 * 1024 + 1
        srv = local_server({"/big.xml": {"declared": too_big}})
        with make_server() as server:
            status, _h, body = self._import(
                as_role(server, "deployer"), srv.url("/big.xml"), persist=True
            )
        assert status == 413, body
        assert body["error"]["code"] == TOO_LARGE

    def test_oversize_response_is_413_on_temp_branch(self, local_server):
        """访客分支同样要给出 413：`block_loopback` 只改地址策略，不改体积口径。"""
        addr = _local_test_address()
        if not addr:
            pytest.skip("本机没有可 bind 也可 connect 的非回环地址，无法构造非回环首跳")
        too_big = DEFAULT_UPLOAD_MAX_MB * 1024 * 1024 + 1
        front = local_server({"/big.xml": {"declared": too_big}}, host=addr)
        with make_server() as server:
            status, _h, body = self._import(as_role(server, "visitor"), front.url("/big.xml"))
        assert status == 413, body
        assert body["error"]["code"] == TOO_LARGE
        assert os.listdir(storage.get_temporary_dir()) == []


class TestImportUrlErrorHygiene:
    """拒绝回环的响应不能变成 SSRF 探测 oracle：开着的端口与关着的端口要说同一句话。"""

    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("hygiene")
        write_target(self.pid, FILE_A, XML_V1.encode("utf-8"))

    def _import(self, caller, url: str):
        return caller.json("POST", "/api/v1/p/%s/import-url" % self.pid, {"url": url})

    def test_closed_and_open_loopback_ports_are_indistinguishable(self, local_server):
        srv = local_server({"/victim.xml": {"body": XML_V2.encode("utf-8")}})
        open_port = srv.url("/victim.xml")
        # 随机端口上大概率没人监听：这代表「内部端口关着」那一侧。
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        closed_port = "http://127.0.0.1:%d/gone.xml" % probe.getsockname()[1]
        probe.close()
        with make_server() as server:
            caller = as_role(server, "visitor")
            open_status, _h, open_body = self._import(caller, open_port)
            closed_status, _h, closed_body = self._import(caller, closed_port)
        for status, body in ((open_status, open_body), (closed_status, closed_body)):
            assert status == 502, body
            assert body["error"]["code"] == DOWNLOAD_FAILED
        assert open_body["error"] == closed_body["error"], (
            "两种情况的响应体必须逐字节相同，否则「端口开没开」就被读出来了"
        )

    def test_refused_and_erroring_private_ports_are_indistinguishable(self, local_server):
        """内网私网段**故意不拦**（§10.2），所以访客面上的探测 oracle 只能靠「失败口径统一」来堵。

        这条用例就是那个口径的证据：`Connection refused`（端口关着）与 `HTTP 404`（端口开着、
        路径没有）必须换成同一句话。一旦谁把下载器的 `AppError` 原样透出到响应体，这里立刻红——
        那等于把「下载失败要不要收紧」变成可以被静悄悄改回去的注释。
        """
        addr = _local_test_address()
        if not addr:
            pytest.skip("本机没有可 bind 也可 connect 的非回环地址，无法构造私网探测")
        srv = local_server({"/here.xml": {"body": XML_V2.encode("utf-8")}}, host=addr)
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind((addr, 0))
        closed = "http://%s:%d/gone.xml" % (addr, probe.getsockname()[1])
        probe.close()
        with make_server() as server:
            caller = as_role(server, "visitor")
            ref_status, _h, refused = self._import(caller, closed)
            err_status, _h, errored = self._import(caller, srv.url("/missing.xml"))
        for status, body in ((ref_status, refused), (err_status, errored)):
            assert status == 502, body
            assert body["error"]["code"] == DOWNLOAD_FAILED
        assert refused["error"] == errored["error"], refused["error"]
