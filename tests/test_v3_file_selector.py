"""M11-05 唯一文件选择器与精确版本 API（INH-2134）。"""

from __future__ import annotations

import json

from authz_fixtures import as_role, make_server, seed_roles

FILE = "pick.xml"


def _create(server, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces", {"name": name}
    )
    assert status == 201, body
    return body["workspace"]["id"]


def _put(server, wid, pid, data, overwrite=False):
    url = "/api/v1/p/%s/files/%s%s" % (pid, FILE, "?overwrite=1" if overwrite else "")
    status, _h, raw = as_role(server, "admin").request(
        "PUT", url, body=data,
        headers={"X-Workspace-Id": wid, "Content-Type": "application/xml"},
    )
    assert status in (200, 201), raw
    return json.loads(raw.decode("utf-8"))


def _selector(server, wid, pid, q=""):
    path = "/api/v3/workspaces/%s/profiles/%s/file-selector" % (wid, pid)
    if q:
        path += "?q=" + q
    status, _h, body = as_role(server, "member").json("GET", path)
    assert status == 200, body
    return body


def test_selector_is_wp_scoped_and_versions_are_addressable():
    seed_roles("pick")
    with make_server() as server:
        admin = as_role(server, "admin")
        w1 = _create(server, "W1")
        w2 = _create(server, "W2")
        second = admin.json(
            "POST", "/api/v3/workspaces/%s/profiles" % w1, {"name": "Second"}
        )[2]["profile"]["id"]
        v1 = b"<R><item>w1</item></R>"
        _put(server, w1, "default", v1)
        _put(server, w1, second, b"<R><item>second</item></R>")
        _put(server, w2, "default", b"<R><item>w2</item></R>")

        sel = _selector(server, w1, "default")
        assert sel["total"] == 1 and [i["name"] for i in sel["items"]] == [FILE]
        item = sel["items"][0]
        assert item["workspace_id"] == w1 and item["profile_id"] == "default"
        assert item["file_id"].startswith("f-") and item["version_id"].startswith("v-")
        assert item["blob_sha256"]
        assert _selector(server, w1, second)["items"][0]["file_id"] != item["file_id"]
        assert _selector(server, w2, "default")["items"][0]["file_id"] != item["file_id"]
        assert _selector(server, w1, "default", FILE)["total"] == 1
        assert _selector(server, w1, "default", "nope")["items"] == []

        current_vid = item["version_id"]
        status, _h, meta = as_role(server, "member").json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/file-versions/%s" % (w1, current_vid),
        )
        assert status == 200 and meta["version"]["blob_sha256"] == item["blob_sha256"]
        status, _h, raw = as_role(server, "member").request(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/file-versions/%s/raw" % (w1, current_vid),
        )
        assert status == 200 and raw == v1
        status, _h, doc = as_role(server, "member").json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/file-versions/%s/document" % (w1, current_vid),
        )
        assert status == 200 and "w1" in doc["nodes"]["v"]

        overwritten = _put(server, w1, "default", b"<R><item>next</item></R>", overwrite=True)
        archived = overwritten["archived"]
        sel2 = _selector(server, w1, "default")["items"][0]
        assert sel2["version_id"] != current_vid
        status, _h, old_meta = as_role(server, "member").json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/file-versions/%s" % (w1, current_vid),
        )
        assert status == 200 and old_meta["version"]["version_label"] == archived
        status, _h, old_raw = as_role(server, "member").request(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/file-versions/%s/raw" % (w1, current_vid),
        )
        assert status == 200 and old_raw == v1

        # 同 W 的另一个 profile / 另一个 W 都不能按 vid 越界读取。
        vid_second = _selector(server, w1, second)["items"][0]["version_id"]
        status, _h, _b = as_role(server, "member").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/file-versions/%s" % (w1, vid_second)
        )
        assert status == 404
        status, _h, _b = as_role(server, "member").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/file-versions/%s" % (w2, current_vid)
        )
        assert status == 404

        # 临时文件不在选择器里。
        status, _h, body = as_role(server, "member").request(
            "POST", "/api/v1/p/default/temp", body=b"<R><item>temp</item></R>",
            headers={"X-Filename": "temp.xml", "Content-Type": "application/xml"},
        )
        assert status == 201, body
        assert _selector(server, w1, "default")["total"] == 1
