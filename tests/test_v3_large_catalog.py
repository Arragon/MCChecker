"""M13-07 大参数分页完整性与混合 fixture（INH-2149）。"""

from __future__ import annotations

import json

from authz_fixtures import as_role, make_server, seed_roles


def _create(server):
    status, _h, body = as_role(server, "admin").json("POST", "/api/v3/workspaces", {"name": "W1"})
    assert status == 201, body
    return body["workspace"]["id"]


def _config(server, wid):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs" % wid,
        {"code": "MC-001", "name": "配置"},
    )
    assert status == 201, body
    return body["config"]["config_id"]


def _put(server, wid, name, data, content_type="application/json"):
    status, _h, raw = as_role(server, "admin").request(
        "PUT", "/api/v1/p/default/files/%s" % name, body=data,
        headers={"X-Workspace-Id": wid, "Content-Type": content_type},
    )
    assert status in (200, 201), raw
    status, _h, body = as_role(server, "admin").json(
        "GET", "/api/v3/workspaces/%s/profiles/default/file-selector?q=%s" % (wid, name)
    )
    assert status == 200 and body["items"]
    return body["items"][0]


def _plan(server, wid, cid, vid):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans" % (wid, cid),
        {"version_id": vid},
    )
    assert status == 201, body
    return body["plan"]


def _page(server, wid, cid, plan_id, offset, limit=100, **query):
    path = "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans/%s/rows?offset=%d&limit=%d" % (
        wid, cid, plan_id, offset, limit,
    )
    for key, value in query.items():
        path += "&%s=%s" % (key, value)
    status, _h, body = as_role(server, "admin").json("GET", path)
    assert status == 200, body
    return body


def test_3200_json_pages_have_no_missing_or_duplicate_rows():
    seed_roles("large-json")
    payload = {"G": {"P%04d" % index: index for index in range(3200)}}
    payload["G"]["Null"] = None
    payload["H"] = {"P0001": 1}
    deep = payload
    for index in range(20):
        deep["L%d" % index] = {"leaf": index}
        deep = deep["L%d" % index]
    with make_server() as server:
        wid = _create(server)
        cid = _config(server, wid)
        item = _put(server, wid, "large.json", json.dumps(payload).encode())
        plan = _plan(server, wid, cid, item["version_id"])
        assert plan["counts"]["total"] == 3200 + 1 + 1 + 20
        assert plan["counts"]["null_uncertain"] == 1
        seen = []
        offset = 0
        while offset < plan["counts"]["total"]:
            page = _page(server, wid, cid, plan["plan_id"], offset)
            assert len(page["items"]) <= 100
            seen.extend(row["row_id"] for row in page["items"])
            offset += 100
        assert len(seen) == len(set(seen)) == plan["counts"]["total"], "分页不得漏行或重复"
        conflict = _page(server, wid, cid, plan["plan_id"], 0, filter="conflict")
        assert conflict["total"] == 1
        keyword = _page(server, wid, cid, plan["plan_id"], 0, q="P0001")
        assert keyword["total"] == 2, "关键词筛选应命中 G 与 H 下的同名叶"


def test_3200_xml_deep_and_null_are_classified():
    seed_roles("large-xml")
    deep_open = "".join("<D%d>" % index for index in range(30))
    deep_close = "".join("</D%d>" % index for index in reversed(range(30)))
    parts = ["<R><G>"]
    for index in range(3200):
        parts.append("<P%04d>%d</P%04d>" % (index, index, index))
    parts.append("<Null/>")
    parts.append(deep_open + "<Leaf>1</Leaf>" + deep_close)
    parts.append("</G></R>")
    with make_server() as server:
        wid = _create(server)
        cid = _config(server, wid)
        item = _put(server, wid, "large.xml", "".join(parts).encode(), "application/xml")
        plan = _plan(server, wid, cid, item["version_id"])
        assert plan["counts"]["total"] == 3202
        assert plan["counts"]["null_uncertain"] == 1
        leaf = _page(server, wid, cid, plan["plan_id"], 0, q="Leaf")
        assert leaf["total"] == 1
        row = leaf["items"][0]
        assert len(row["directory"]) == 31 and row["directory"][0] == "G" and row["directory"][1] == "D0"
