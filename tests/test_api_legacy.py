"""兼容旧路由（§7.12、UC-46；M4-01 / INH-1903）。

真 HTTP 打旧链接：

* probe 头 ⇒ 旧版 JSON（成功 `{ok,filename,kind}`，失败 `{ok:false,detail}` + 原状态码）；
* 无 probe ⇒ 文件字节 + RFC 5987 `Content-Disposition`；
* `/record_view` ⇒ 302 到新 hash 路由；`/static/*` ⇒ 404；
* 文件名/历史版本标识只允许单个路径段，穿越一律 400。
"""

from __future__ import annotations

import json
import urllib.parse

from authz_fixtures import as_role, make_server, seed_roles, write_target

from app.core import storage

FILE_A = "a.xml"
CHINESE = "中文文件.xml"
XML_V1 = b"<R><x>1</x></R>"
XML_V2 = b"<R><x>2</x></R>"


def _query(**params):
    return urllib.parse.urlencode(params)


def _download(caller, pid, **params):
    params.setdefault("profile", pid)
    return caller.request("GET", "/api/file-download?" + _query(**params))


def _probe(caller, pid, **params):
    params.setdefault("profile", pid)
    return caller.request(
        "GET",
        "/api/file-download?" + _query(**params),
        headers={"X-MC-Download-Probe": "1"},
    )


class Base:
    def setup_method(self):
        self.pid = seed_roles("legacy")
        write_target(self.pid, FILE_A, XML_V1)
        write_target(self.pid, CHINESE, XML_V1)


class TestDownloadProbe(Base):
    def test_current_probe_returns_the_old_json(self):
        with make_server() as server:
            status, _h, raw = _probe(as_role(server, "visitor"), self.pid, kind="current", filename=FILE_A)
        assert status == 200, raw
        assert json.loads(raw) == {"ok": True, "filename": FILE_A, "kind": "current"}

    def test_missing_file_probe_is_404_with_detail(self):
        with make_server() as server:
            status, _h, raw = _probe(as_role(server, "visitor"), self.pid, kind="current", filename="nope.xml")
        assert status == 404, raw
        body = json.loads(raw)
        assert body["ok"] is False and "不存在" in body["detail"], body

    def test_bad_kind_probe_is_400(self):
        with make_server() as server:
            status, _h, raw = _probe(as_role(server, "visitor"), self.pid, kind="weird", filename=FILE_A)
        assert status == 400, raw
        assert json.loads(raw)["ok"] is False

    def test_unknown_profile_is_404_not_default(self):
        with make_server() as server:
            status, _h, raw = _probe(
                as_role(server, "visitor"), self.pid, kind="current", filename=FILE_A, profile="nope"
            )
        assert status == 404, raw
        assert json.loads(raw)["ok"] is False


class TestDownloadBytes(Base):
    def test_current_bytes_and_ascii_disposition(self):
        with make_server() as server:
            status, headers, raw = _download(
                as_role(server, "visitor"), self.pid, kind="current", filename=FILE_A
            )
        assert status == 200, raw
        assert raw == XML_V1
        assert headers["Content-Type"].startswith("application/xml"), headers
        assert 'filename="a.xml"' in headers["Content-Disposition"], headers

    def test_chinese_name_uses_rfc5987(self):
        with make_server() as server:
            status, headers, raw = _download(
                as_role(server, "visitor"), self.pid, kind="current", filename=CHINESE
            )
        assert status == 200, raw
        disposition = headers["Content-Disposition"]
        encoded = urllib.parse.quote(CHINESE, safe="")
        assert "filename*=UTF-8''%s" % encoded in disposition, disposition
        assert "filename=\"" in disposition, disposition

    def test_archive_download_uses_the_archive_name(self):
        write_target(self.pid, FILE_A, XML_V2)
        storage.set_active_profile(self.pid)
        archive = storage.list_archived_versions(FILE_A)[0]["filename"]
        with make_server() as server:
            status, _h, raw = _probe(
                as_role(server, "visitor"), self.pid,
                kind="archive", filename=FILE_A, archive_filename=archive,
            )
            assert status == 200, raw
            assert json.loads(raw)["filename"] == archive
            status, headers, body = _download(
                as_role(server, "visitor"), self.pid,
                kind="archive", filename=FILE_A, archive_filename=archive,
            )
        assert status == 200, body
        assert body == XML_V1
        assert archive in headers["Content-Disposition"], headers

    def test_traversal_is_rejected(self):
        with make_server() as server:
            caller = as_role(server, "visitor")
            for filename in ("../secret.xml", "..", ".", "a/b.xml", "a\\b.xml"):
                status, _h, _raw = _probe(caller, self.pid, kind="current", filename=filename)
                assert status == 400, filename
            status, _h, _raw = _probe(
                caller, self.pid, kind="archive", filename=FILE_A, archive_filename="../x"
            )
            assert status == 400


class TestRecordViewAndStatic(Base):
    def test_record_view_redirects_to_the_hash_route(self):
        with make_server() as server:
            status, headers, raw = as_role(server, "visitor").request(
                "GET",
                "/record_view?" + _query(config=FILE_A, record="rec_1.xml", profile=self.pid),
            )
        assert status == 302, raw
        assert headers["Location"] == "/#/p/%s/f/%s?version=record:rec_1.xml" % (
            self.pid, FILE_A
        ), headers

    def test_record_view_defaults_profile(self):
        with make_server() as server:
            status, headers, _raw = as_role(server, "visitor").request(
                "GET", "/record_view?" + _query(config=FILE_A, record="rec_1.xml")
            )
        assert status == 302
        assert headers["Location"].startswith("/#/p/default/f/")

    def test_record_view_requires_both_parameters(self):
        with make_server() as server:
            status, _h, raw = as_role(server, "visitor").request(
                "GET", "/record_view?config=" + FILE_A
            )
        assert status == 400, raw

    def test_static_is_always_404(self):
        with make_server() as server:
            caller = as_role(server, "visitor")
            for path in ("/static", "/static/", "/static/css/style.css", "/static/js/app.js"):
                status, _h, raw = caller.request("GET", path)
                assert status == 404, (path, raw)
