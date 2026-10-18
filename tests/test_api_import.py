"""临时文件、上传保存与链接导入（§7.4、§5.3、§5.4；M2-10）。

覆盖：`POST /temp` 建档与解析校验、所有者 = 同 cid **且** 同 IP（他人 404）、过期 404
且绝不回退读同名服务器文件（F01）、配额与体积上限、惰性清理、`PUT /files/{name}` 的
201/200/409/unchanged 与权限、`POST /import-url` 的 http/https 校验与下载失败 502、
以及 M2-08 遗留的「temp 侧参与对比」。
"""

from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime, timedelta

from authz_fixtures import as_role, make_server, seed_roles, sha256_of, write_target

from app.core import storage
from app.core.errors import TOO_LARGE, AppError
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
        """未知/超长 token 是 404（不承认它存在）；**穿越形状**的 token 是 400/403。

        后半句由 M4-02（INH-1904）按 §16.8 第 3 条改：`_dispatch` 现在在路由之前拒掉
        编码分隔符，所以它不再伪装成「没有这个接口」。见 `tests/test_api_security.py`
        的 `test_temp_token_traversal_is_rejected`。
        """
        with make_server() as server:
            caller = as_role(server, "member")
            for token in ("nope", "a" * 200):
                status, _h, body = caller.request(
                    "GET",
                    "/api/v1/p/%s/temp/%s/document" % (self.pid, token),
                    headers={"Cookie": "mc_cid=%s" % MINE},
                )
                assert status == 404, (token, body)
            for token in ("..%2F..%2Fetc", "..%5C..%5Cwindows"):
                status, _h, body = caller.request(
                    "GET",
                    "/api/v1/p/%s/temp/%s/document" % (self.pid, token),
                    headers={"Cookie": "mc_cid=%s" % MINE},
                )
                assert status in (400, 403), (token, body)


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
        from app.core import downloader

        monkeypatch.setattr(downloader, "download_file", lambda url, retries=3: None)
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = caller.json(
                "POST", "/api/v1/p/%s/import-url" % self.pid, {"url": "http://example.invalid/a.xml"}
            )
            assert status == 502, body
            assert body["error"]["code"] == "DOWNLOAD_FAILED"

    def test_temp_import_then_compare_with_current(self, monkeypatch):
        """补 M2-08 留下的那条：temp 侧现在真的能参与对比（UC-17 的服务端部分）。"""
        from app.core import downloader

        monkeypatch.setattr(
            downloader, "download_file", lambda url, retries=3: XML_V2.encode("utf-8")
        )
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
        from app.core import downloader

        monkeypatch.setattr(
            downloader, "download_file", lambda url, retries=3: XML_V2.encode("utf-8")
        )
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
