"""M15-03 跨 Workspace/权限/并发总门禁（INH-2158）。"""

from __future__ import annotations

import concurrent.futures
import json

from authz_fixtures import as_role, make_server, seed_roles


def _w(server, name):
    return as_role(server, "admin").json("POST", "/api/v3/workspaces", {"name": name})[2]["workspace"]["id"]


def _cfg(server, wid):
    return as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs" % wid,
        {"code": "MC-001", "name": "配置"},
    )[2]["config"]["config_id"]


def _put(server, wid, data):
    status, _h, raw = as_role(server, "admin").request(
        "PUT", "/api/v1/p/default/files/same.json", body=data,
        headers={"X-Workspace-Id": wid, "Content-Type": "application/json"},
    )
    assert status in (200, 201), raw
    return as_role(server, "admin").json(
        "GET", "/api/v3/workspaces/%s/profiles/default/file-selector?q=same.json" % wid
    )[2]["items"][0]


def test_cross_workspace_objects_and_downloads_are_404():
    seed_roles("security")
    with make_server() as server:
        w1, w2 = _w(server, "W1"), _w(server, "W2")
        c1, c2 = _cfg(server, w1), _cfg(server, w2)
        item1 = _put(server, w1, b'{"A":1}')
        item2 = _put(server, w2, b'{"A":2}')
        assert item1["file_id"] != item2["file_id"]
        for path in (
            "/api/v3/workspaces/%s/profiles/default/file-versions/%s/raw" % (w1, item2["version_id"]),
            "/api/v3/workspaces/%s/profiles/default/configs/%s" % (w1, c2),
        ):
            assert as_role(server, "admin").json("GET", path)[0] == 404
        plan = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans" % (w1, c1),
            {"version_id": item1["version_id"]},
        )[2]["plan"]
        assert as_role(server, "admin").json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans/%s/rows" % (w2, c2, plan["plan_id"]),
        )[0] == 404
        assert as_role(server, "admin").json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans/%s" % (w2, c2, plan["plan_id"]),
        )[0] == 404
        assert as_role(server, "member").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans" % (w1, c1),
            {"version_id": item1["version_id"]},
        )[0] == 403


def test_unsafe_documents_are_rejected_before_write():
    seed_roles("security-parse")
    with make_server() as server:
        wid = _w(server, "W1")
        cid = _cfg(server, wid)
        for name, body, ctype in (
            ("dup.json", b'{"A":1,"A":2}', "application/json"),
            ("nan.json", b'{"A":NaN}', "application/json"),
            ("dtd.xml", b'<!DOCTYPE R><R><A>1</A></R>', "application/xml"),
        ):
            status, _h, raw = as_role(server, "admin").request(
                "PUT", "/api/v1/p/default/files/" + name, body=body,
                headers={"X-Workspace-Id": wid, "Content-Type": ctype},
            )
            if status in (200, 201):
                vid = as_role(server, "admin").json(
                    "GET", "/api/v3/workspaces/%s/profiles/default/file-selector?q=%s" % (wid, name)
                )[2]["items"][0]["version_id"]
                assert as_role(server, "admin").json(
                    "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans" % (wid, cid),
                    {"version_id": vid},
                )[0] == 422
        assert as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid)
        )[2]["items"] == []


def test_concurrent_draft_cas_and_plan_commits_do_not_duplicate():
    seed_roles("security-concurrent")
    with make_server() as server:
        wid = _w(server, "W1")
        cid = _cfg(server, wid)
        pid = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
            {"name": "P", "directory": ["G"], "type": "number"},
        )[2]["parameter"]["parameter_id"]
        rid = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/releases" % (wid, cid),
            {"version": "690"},
        )[2]["release"]["release_id"]
        did = "d0-" + rid

        def patch(value):
            return as_role(server, "admin").json(
                "PATCH", "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s/values" % (wid, cid, did),
                {"request_id": "c-" + value, "expected_revision": 0,
                 "items": [{"parameter_id": pid, "membership": "included", "enabled": True,
                            "value_state": "present", "value": value}]},
                headers={"If-Match": '"0"'},
            )[0]

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(patch, ("1", "2")))
        assert sorted(statuses) == [200, 409], statuses

        item = _put(server, wid, b'{"H":{"Q":1}}')
        plan = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans" % (wid, cid),
            {"version_id": item["version_id"]},
        )[2]["plan"]

        def commit(request_id):
            return as_role(server, "admin").json(
                "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans/%s/commit"
                % (wid, cid, plan["plan_id"]), {"request_id": request_id},
            )[0]

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(commit, ("r1", "r2")))
        assert sorted(statuses) == [200, 409], statuses
        assert len(as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid)
        )[2]["items"]) == 2, "并发提交不得重复建参"

        def get_rows(_index):
            return as_role(server, "admin").json(
                "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans/%s/rows?limit=10"
                % (wid, cid, plan["plan_id"])
            )[0]

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            statuses = list(pool.map(get_rows, range(200)))
        assert statuses.count(200) >= 199, "200 并发读不得大面积失败：%s" % statuses[:10]
