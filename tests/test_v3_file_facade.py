"""M11-02 统一文件管理：文本格式原文与 v1 facade（INH-2131）。"""

from __future__ import annotations

import json

from authz_fixtures import as_role, make_server, seed_roles

TEXT = "[main]\nkey=value\n# 中文注释\n".encode("utf-8")


def _create(server, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces", {"name": name}
    )
    assert status == 201, body
    return body["workspace"]["id"]


def _put(server, wid, name, data, overwrite=False):
    url = "/api/v1/p/default/files/%s%s" % (name, "?overwrite=1" if overwrite else "")
    status, _h, raw = as_role(server, "admin").request(
        "PUT", url, body=data,
        headers={"X-Workspace-Id": wid, "Content-Type": "text/plain"},
    )
    assert status in (200, 201), raw
    return json.loads(raw.decode("utf-8"))


def _get(server, wid, path):
    return as_role(server, "visitor").json(
        "GET", "/api/v1/p/default" + path, headers={"X-Workspace-Id": wid}
    )


def test_text_formats_are_raw_only_and_stay_in_their_workspace():
    seed_roles("files")
    with make_server() as server:
        w1, w2 = _create(server, "W1"), _create(server, "W2")
        for name in ("a.ini", "b.cfg", "c.conf", "d.txt"):
            body = _put(server, w1, name, TEXT)
            assert body["archived"] is None
            status, _h, listing = _get(server, w1, "/files")
            assert status == 200, listing
            item = {f["name"]: f for f in listing["items"]}[name]
            assert item["format"] == "text", item

            status, _h, raw = as_role(server, "visitor").request(
                "GET", "/api/v1/p/default/files/%s/raw" % name,
                headers={"X-Workspace-Id": w1},
            )
            assert status == 200 and raw == TEXT
            status, _h, doc = _get(server, w1, "/files/%s/document" % name)
            assert status == 422 and doc["error"]["code"] == "PARSE_FAILED", doc
            assert doc["error"]["detail"]["unsupported"] is True

            assert _put(server, w1, name, b"updated\n", overwrite=True)["archived"]
            status, _h, archives = _get(server, w1, "/files/%s/archives" % name)
            assert status == 200 and archives["items"], archives

        status, _h, listing = _get(server, w2, "/files")
        assert status == 200 and listing["items"] == [], "W2 不得看到 W1 的文本文件"

        # XML 仍走结构树，不被文本规则误伤。
        xml = b"<R><item>1</item></R>"
        assert _put(server, w1, "cfg.xml", xml)["archived"] is None
        status, _h, doc = _get(server, w1, "/files/cfg.xml/document")
        assert status == 200 and doc["file"]["format"] == "xml"


def test_text_temp_file_keeps_raw_ttl_and_name():
    seed_roles("files2")
    with make_server() as server:
        status, _h, body = as_role(server, "member").request(
            "POST", "/api/v1/p/default/temp", body=TEXT,
            headers={"X-Filename": "note.conf", "Content-Type": "text/plain"},
        )
        assert status == 201, body
        meta = json.loads(body.decode("utf-8"))
        token = meta["token"]
        status, _h, raw = as_role(server, "member").request(
            "GET", "/api/v1/p/default/temp/%s/raw" % token
        )
        assert status == 200 and raw == TEXT
        status, _h, doc = as_role(server, "member").json(
            "GET", "/api/v1/p/default/temp/%s/document" % token
        )
        assert status == 422 and doc["error"]["detail"]["unsupported"] is True
        # 临时文件不会自动变成长久来源。
        status, _h, listing = as_role(server, "member").json(
            "GET", "/api/v1/p/default/files"
        )
        assert status == 200 and listing["items"] == []
