"""文档服务与读接口（INH-1868 / M1-02；§6.3–§6.5、§7.4；F03、F14、F27）。

按卡片要求：线程里以随机端口启动真实 server，`http.client` 发请求；缓存命中用
`documents.cache_info()` 观测，不靠日志口头判断。
"""

from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import os
import threading
import urllib.parse

import pytest

from app.config import Settings
from app.services import documents

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "web")

XML_V1 = b'<?xml version="1.0"?><R a="1"><item>x</item><param_1 min="0">5</param_1></R>'
XML_V2 = b'<?xml version="1.0"?><R a="2"><item>x</item><param_1 min="0">7</param_1></R>'


@pytest.fixture()
def server(tmp_path):
    from app.server.http import MCServer
    from app.server.router import build_router

    documents.reset_cache()
    settings = Settings(
        host="127.0.0.1", port=0, data_dir=str(tmp_path / "data"), web_dir=WEB_DIR
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


def request(server, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        conn.request("GET", path, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def seed_profile(model_name="p1"):
    """建一个机型并在其中放一个配置文件，返回 (pid, sha256)。"""
    from app.core import device_models, storage

    storage.set_active_profile("default")
    pid = device_models.add_model(model_name)
    storage.set_active_profile(pid)
    storage.save_config_file("a.xml", XML_V1)
    return pid, hashlib.sha256(XML_V1).hexdigest()


def files_url(pid):
    return "/api/v1/p/%s/files" % pid


def document_url(pid, name="a.xml", version=None):
    url = "/api/v1/p/%s/files/%s/document" % (pid, urllib.parse.quote(name))
    return url + ("?version=" + urllib.parse.quote(version, safe="") if version else "")


def raw_url(pid, name="a.xml", version=None):
    url = "/api/v1/p/%s/files/%s/raw" % (pid, urllib.parse.quote(name))
    return url + ("?version=" + urllib.parse.quote(version, safe="") if version else "")


class TestFileList:
    def test_fields(self, server):
        from app.core import storage

        pid, sha = seed_profile()
        storage.save_config_file("a.xml", XML_V2)  # 产生一个归档
        storage.save_record_file("a.xml", b"record body", source_url="http://example.invalid/a.xml")
        storage.add_edit_remark(
            source_file="a.xml",
            node_key="k",
            node_path="/R[1]/item[1]",
            node_label="item",
            original_value="x",
            proposed_value="y",
            node_type="element",
            tree_type="xml",
            actor_ip="10.0.0.1",
            actor_name="tester",
        )
        storage.add_config_mapping("a.xml", "http://example.invalid/a.xml")

        status, _, body = request(server, files_url(pid))
        assert status == 200
        items = json.loads(body.decode("utf-8"))["items"]
        assert len(items) == 1
        item = items[0]
        assert item["name"] == "a.xml"
        assert item["format"] == "xml"
        assert item["size"] == len(XML_V2)
        assert item["mapped"] is True
        assert item["archive_count"] == 1
        assert item["record_count"] == 1
        assert item["pending_remarks"] == 1
        assert len(item["updated_at"]) == 19

    def test_sorted_by_name_and_only_configs(self, server):
        from app.core import storage

        pid, _ = seed_profile()
        storage.save_config_file("b.xml", XML_V1)
        storage.save_config_file("c.json", b'{"k":1}')
        status, _, body = request(server, files_url(pid))
        assert status == 200
        names = [item["name"] for item in json.loads(body)["items"]]
        assert names == ["a.xml", "b.xml", "c.json"]

    def test_unknown_profile_is_404(self, server):
        status, _, body = request(server, files_url("nope"))
        assert status == 404
        assert json.loads(body.decode("utf-8"))["error"]["code"] == "NOT_FOUND"


class TestDocument:
    def test_payload_matches_spec_6_3(self, server):
        pid, sha = seed_profile()
        status, headers, body = request(server, document_url(pid))
        assert status == 200
        assert headers["ETag"] == '"%s-v2"' % sha
        assert headers["Cache-Control"] == "no-cache"

        payload = json.loads(body.decode("utf-8"))
        assert set(payload) == {"file", "nodes"}
        file_block = payload["file"]
        assert file_block["profile"] == pid
        assert file_block["name"] == "a.xml"
        assert file_block["kind"] == "current"
        assert file_block["version"] == "current"
        assert file_block["version_label"] == "当前版本"
        assert file_block["format"] == "xml"
        assert file_block["sha256"] == sha
        assert file_block["parser_version"] == 2
        assert file_block["node_count"] == len(payload["nodes"]["p"]) - 1
        assert file_block["read_only"] is False
        assert file_block["warnings"] == []
        assert file_block["expires_at"] is None
        assert set(payload["nodes"]) == {"p", "t", "l", "v", "id", "loc"}
        assert payload["nodes"]["l"][0] == "a.xml"

    def test_second_request_hits_memory_cache(self, server):
        pid, _ = seed_profile()
        request(server, document_url(pid))
        first = documents.cache_info()
        status, _, _ = request(server, document_url(pid))
        second = documents.cache_info()
        assert status == 200
        assert first["misses"] == 1 and first["hits"] == 0
        assert second["hits"] == 1 and second["misses"] == 1

    def test_modification_changes_sha_and_etag(self, server):
        from app.core import storage

        pid, sha_v1 = seed_profile()
        _, headers_v1, _ = request(server, document_url(pid))

        storage.set_active_profile(pid)
        storage.save_config_file("a.xml", XML_V2)  # 内容变化 → 新 sha

        status, headers_v2, body = request(server, document_url(pid))
        sha_v2 = hashlib.sha256(XML_V2).hexdigest()
        assert status == 200
        assert headers_v2["ETag"] == '"%s-v2"' % sha_v2
        assert headers_v2["ETag"] != headers_v1["ETag"]
        payload = json.loads(body.decode("utf-8"))
        assert payload["file"]["sha256"] == sha_v2
        assert payload["file"]["sha256"] != sha_v1

    def test_etag_returns_304(self, server):
        pid, _ = seed_profile()
        _, headers, _ = request(server, document_url(pid))
        status, headers2, body = request(
            server, document_url(pid), headers={"If-None-Match": headers["ETag"]}
        )
        assert status == 304
        assert body == b""
        assert headers2["ETag"] == headers["ETag"]

    def test_missing_file_is_404(self, server):
        pid, _ = seed_profile()
        status, _, body = request(server, document_url(pid, "nope.xml"))
        assert status == 404
        assert json.loads(body.decode("utf-8"))["error"]["code"] == "NOT_FOUND"

    def test_unparseable_returns_422_with_raw_available(self, server):
        from app.core import storage

        pid, _ = seed_profile()
        storage.set_active_profile(pid)
        storage.save_config_file("bad.xml", b"<R><unclosed></R>")

        status, _, body = request(server, document_url(pid, "bad.xml"))
        assert status == 422
        error = json.loads(body.decode("utf-8"))["error"]
        assert error["code"] == "PARSE_FAILED"
        assert error["detail"]["raw_available"] is True

        # 原文仍可下载
        status, headers, raw = request(server, raw_url(pid, "bad.xml"))
        assert status == 200
        assert raw == b"<R><unclosed></R>"

    def test_large_document_is_gzipped(self, server):
        from app.core import storage

        pid, _ = seed_profile()
        rows = "".join("<v%d>%d</v%d>" % (i, i, i) for i in range(2000))
        storage.set_active_profile(pid)
        storage.save_config_file("big.xml", ("<R>%s</R>" % rows).encode("utf-8"))

        status, headers, body = request(
            server, document_url(pid, "big.xml"), headers={"Accept-Encoding": "gzip"}
        )
        assert status == 200
        assert headers.get("Content-Encoding") == "gzip"
        assert headers.get("Vary") == "Accept-Encoding"
        payload = json.loads(gzip.decompress(body).decode("utf-8"))
        assert payload["file"]["node_count"] > 2000


class TestVersions:
    def test_archive_version_is_served(self, server):
        from app.core import storage

        pid, _ = seed_profile()
        storage.save_config_file("a.xml", XML_V2)
        archives = storage.list_archived_versions("a.xml")
        assert archives
        archive_name = archives[0]["filename"]

        status, _, body = request(server, document_url(pid, version="archive:" + archive_name))
        assert status == 200
        payload = json.loads(body.decode("utf-8"))
        assert payload["file"]["kind"] == "archive"
        assert payload["file"]["version"] == "archive:" + archive_name
        assert payload["file"]["version_label"] == archive_name
        assert payload["file"]["read_only"] is True

        status, headers, raw = request(server, raw_url(pid, version="archive:" + archive_name))
        assert status == 200
        assert raw == XML_V1
        assert urllib.parse.quote(archive_name, safe="") in headers["Content-Disposition"]

    def test_record_version_is_served(self, server):
        from app.core import storage

        pid, _ = seed_profile()
        path = storage.save_record_file("a.xml", XML_V1, source_url="http://example.invalid/a.xml")
        record_name = os.path.basename(path)

        status, _, body = request(server, document_url(pid, version="record:" + record_name))
        assert status == 200
        payload = json.loads(body.decode("utf-8"))
        assert payload["file"]["kind"] == "record"
        assert payload["file"]["read_only"] is True

        status, headers, raw = request(server, raw_url(pid, version="record:" + record_name))
        assert status == 200
        assert raw == XML_V1
        assert urllib.parse.quote(record_name, safe="") in headers["Content-Disposition"]

    @pytest.mark.parametrize(
        "version",
        ["archive:../x", "archive:..%2Fx", "record:../../etc/passwd", "bogus", "archive:", "record:a/b"],
    )
    def test_illegal_version_is_rejected(self, server, version):
        pid, _ = seed_profile()
        status, _, body = request(server, document_url(pid) + "?version=" + version)
        assert status in (400, 403), version
        code = json.loads(body.decode("utf-8"))["error"]["code"]
        assert code in ("INVALID_INPUT", "FORBIDDEN", "NOT_FOUND")

    @pytest.mark.parametrize(
        "raw_version",
        ["archive:..%2Fx", "record:..%2F..%2Fsecret.xml", "archive:%2e%2e%2Fx"],
    )
    def test_encoded_traversal_in_version_is_rejected(self, server, raw_version):
        pid, _ = seed_profile()
        status, _, body = request(server, document_url(pid) + "?version=" + raw_version)
        assert status in (400, 403)
        assert b"<secret" not in body

    def test_version_may_not_escape_archive_dir(self, server, tmp_path):
        from app.core import storage

        pid, _ = seed_profile()
        # 在归档目录之外放一个"敏感"文件：任何越界写法都不能读到它的内容
        outside = tmp_path / "secret.xml"
        outside.write_bytes(b"<secret/>")
        storage.set_active_profile(pid)

        # 双重编码：解码后是字面量文件名，不存在 → 404，同样读不到目录外文件内容
        status, _, body = request(server, raw_url(pid, version="archive:..%2F..%2Fsecret.xml"))
        assert status in (404, 400, 403)
        assert b"<secret" not in body


class TestRawDownload:
    def test_headers_and_body(self, server):
        pid, sha = seed_profile()
        status, headers, body = request(server, raw_url(pid))
        assert status == 200
        assert headers["Content-Type"].startswith("application/xml")
        assert headers["Content-Disposition"].startswith('attachment; filename="a.xml"')
        assert headers["ETag"] == '"%s-v2"' % sha
        assert body == XML_V1

    def test_chinese_filename_uses_rfc5987(self, server):
        from app.core import storage

        pid, _ = seed_profile()
        chinese = "配置 A.xml"
        storage.set_active_profile(pid)
        storage.save_config_file(chinese, XML_V1)

        status, headers, body = request(server, raw_url(pid, chinese))
        assert status == 200
        disposition = headers["Content-Disposition"]
        assert "filename*=UTF-8''" + urllib.parse.quote(chinese, safe="") in disposition
        assert "配置" not in disposition  # ASCII 回退里不含非 ASCII
        assert body == XML_V1

    def test_raw_etag_returns_304(self, server):
        pid, _ = seed_profile()
        _, headers, _ = request(server, raw_url(pid))
        status, _, body = request(server, raw_url(pid), headers={"If-None-Match": headers["ETag"]})
        assert status == 304
        assert body == b""


class TestCache:
    def test_memory_cache_avoids_rereading_file(self, server, monkeypatch):
        pid, _ = seed_profile()
        request(server, document_url(pid))

        calls = {"count": 0}
        original = documents._read_bytes

        def counting(path):
            calls["count"] += 1
            return original(path)

        monkeypatch.setattr(documents, "_read_bytes", counting)
        request(server, document_url(pid))
        assert calls["count"] == 0  # 命中缓存：不再读盘

    def test_disk_cache_rebuilds_memory_after_reset(self, server):
        from app.core import storage

        pid, sha = seed_profile()
        request(server, document_url(pid))

        documents.reset_cache()
        status, headers, body = request(server, document_url(pid))
        info = documents.cache_info()
        assert status == 200
        assert info["misses"] == 1  # 内存未命中
        assert info["entries"] == 1  # 重新填好
        assert headers["ETag"] == '"%s-v2"' % sha

        disk_dir = os.path.join(storage.get_cache_root_dir(pid), "doc_v2")
        assert os.path.isfile(os.path.join(disk_dir, sha + ".json.gz"))

    def test_corrupt_disk_cache_is_discarded_and_rebuilt(self, server):
        from app.core import storage

        pid, sha = seed_profile()
        request(server, document_url(pid))
        disk_path = os.path.join(storage.get_cache_root_dir(pid), "doc_v2", sha + ".json.gz")

        with open(disk_path, "wb") as fh:
            fh.write(b"not gzip")
        documents.reset_cache()

        status, headers, body = request(server, document_url(pid))
        assert status == 200
        assert headers["ETag"] == '"%s-v2"' % sha
        assert json.loads(body.decode("utf-8"))["file"]["sha256"] == sha
        # 已重建为可用缓存
        with gzip.open(disk_path, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["parser_version"] == 2
