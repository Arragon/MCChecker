"""M10-02 速览/收藏/搜索按工作区隔离（INH-2125）。"""

from __future__ import annotations

from authz_fixtures import as_role, make_server, seed_roles

FILE = "same.xml"
NODE_ID = "root/R/params/param_01"

XML_W1 = (
    "<R><params>"
    '<param_01 description="宽度">5</param_01>'
    "<item>alpha-one</item><item>alpha-two</item>"
    "</params></R>"
)
XML_W2 = (
    "<R><params>"
    '<param_01 description="宽度">9</param_01>'
    "<item>beta-one</item><item>beta-two</item>"
    "</params></R>"
)


def _create(server, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces", {"name": name}
    )
    assert status == 201, body
    return body["workspace"]["id"]


def _put(server, wid, data):
    return as_role(server, "admin").request(
        "PUT", "/api/v1/p/default/files/%s" % FILE,
        body=data.encode("utf-8"),
        headers={"X-Workspace-Id": wid, "Content-Type": "application/xml"},
    )


def _get(server, wid, path):
    status, _h, body = as_role(server, "admin").json(
        "GET", path, headers={"X-Workspace-Id": wid}
    )
    assert status == 200, body
    return body


def test_overview_favorites_and_search_are_workspace_scoped():
    seed_roles("scope")
    with make_server() as server:
        w1, w2 = _create(server, "W1"), _create(server, "W2")
        assert _put(server, w1, XML_W1)[0] == 201
        assert _put(server, w2, XML_W2)[0] == 201
        admin = as_role(server, "admin")

        s1, _h, b1 = admin.json(
            "POST", "/api/v1/p/default/favorites",
            {"file": FILE, "id": NODE_ID}, headers={"X-Workspace-Id": w1},
        )
        s2, _h, b2 = admin.json(
            "POST", "/api/v1/p/default/favorites",
            {"file": FILE, "id": NODE_ID}, headers={"X-Workspace-Id": w2},
        )
        assert s1 == 201 and b1["item"]["value"] == "5", b1
        assert s2 == 201 and b2["item"]["value"] == "9", "同名参数在 W2 必须取 W2 的值"

        status, _h, body = admin.json(
            "PATCH", "/api/v1/p/default/favorites",
            {"file": FILE, "path": NODE_ID, "note": "只看 W1"},
            headers={"X-Workspace-Id": w1},
        )
        assert status == 200, body
        g1 = _get(server, w1, "/api/v1/p/default/favorites")["groups"]
        g2 = _get(server, w2, "/api/v1/p/default/favorites")["groups"]
        assert g1[0]["items"][0]["note"] == "只看 W1"
        assert g2[0]["items"][0]["note"] == "", "收藏备注不得跨 W 复制"

        hit1 = _get(server, w1, "/api/v1/p/default/search?q=alpha")
        hit2 = _get(server, w2, "/api/v1/p/default/search?q=alpha")
        assert hit1["total"] >= 2 and hit1["files"] == {FILE: hit1["total"]}
        assert hit2["total"] == 0, "搜索索引不得命中其它 W 的文档：%s" % hit2
        back = _get(server, w2, "/api/v1/p/default/search?q=beta")
        miss1 = _get(server, w1, "/api/v1/p/default/search?q=beta")
        assert back["total"] >= 2
        assert miss1["total"] == 0

        page1 = _get(server, w1, "/api/v1/p/default/search?q=alpha&limit=1&offset=0")
        page2 = _get(server, w1, "/api/v1/p/default/search?q=alpha&limit=1&offset=1")
        assert len(page1["items"]) == 1 and page1["truncated"] is True
        assert len(page2["items"]) == 1 and page1["items"] != page2["items"], "分页作用域必须是当前 W/P"


def test_file_list_and_current_values_follow_workspace():
    seed_roles("scope")
    with make_server() as server:
        w1, w2 = _create(server, "W1"), _create(server, "W2")
        assert _put(server, w1, XML_W1)[0] == 201
        assert _put(server, w2, XML_W2)[0] == 201
        admin = as_role(server, "admin")
        f1 = _get(server, w1, "/api/v1/p/default/files")
        f2 = _get(server, w2, "/api/v1/p/default/files")
        assert [f["name"] for f in f1["items"]] == [FILE]
        assert [f["name"] for f in f2["items"]] == [FILE]
        d1 = _get(server, w1, "/api/v1/p/default/files/%s/document" % FILE)
        d2 = _get(server, w2, "/api/v1/p/default/files/%s/document" % FILE)
        assert d1 != d2, "速览/文档响应必须来自当前 W 的原文"
