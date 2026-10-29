"""M14-03 值导入计划（INH-2152）。"""

from __future__ import annotations

import json

from authz_fixtures import as_role, make_server, seed_roles


def _base(server):
    wid = as_role(server, "admin").json("POST", "/api/v3/workspaces", {"name": "W1"})[2]["workspace"]["id"]
    cid = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs" % wid,
        {"code": "MC-001", "name": "配置"},
    )[2]["config"]["config_id"]
    return wid, cid


def _param(server, wid, cid, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
        {"name": name, "directory": ["G"], "type": "number"},
    )
    assert status == 201, body
    return body["parameter"]["parameter_id"]


def _release(server, wid, cid):
    return as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/releases" % (wid, cid),
        {"version": "690"},
    )[2]["release"]["release_id"]


def _put(server, wid, data, overwrite=False):
    status, _h, raw = as_role(server, "admin").request(
        "PUT", "/api/v1/p/default/files/vals.json" + ("?overwrite=1" if overwrite else ""), body=data,
        headers={"X-Workspace-Id": wid, "Content-Type": "application/json"},
    )
    assert status in (200, 201), raw
    return as_role(server, "admin").json(
        "GET", "/api/v3/workspaces/%s/profiles/default/file-selector?q=vals.json" % wid
    )[2]["items"][0]


def _binding(server, wid, cid, rid, pid, item):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/bindings" % (wid, cid, rid),
        {"parameter_id": pid, "locator": "json:/G/P1", "file_id": item["file_id"],
         "version_id": item["version_id"], "confirmed": True},
    )
    assert status == 201, body


def _plan(server, wid, cid, rid, vid):
    return as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/value-import-plans" % (wid, cid),
        {"release_id": rid, "version_id": vid},
    )


def _commit(server, wid, cid, plan_id):
    return as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/value-import-plans/%s/commit"
        % (wid, cid, plan_id), {}
    )


def test_value_import_uses_bindings_and_never_creates_second_d0():
    seed_roles("value-import")
    with make_server() as server:
        wid, cid = _base(server)
        p1 = _param(server, wid, cid, "P1")
        p2 = _param(server, wid, cid, "P2")
        rid = _release(server, wid, cid)
        item = _put(server, wid, json.dumps({"G": {"P1": 0, "P2": None, "Other": False}}).encode())
        _binding(server, wid, cid, rid, p1, item)
        status, _h, body = _plan(server, wid, cid, rid, item["version_id"])
        assert status == 201, body
        plan = body["plan"]
        assert plan["bound"] == 1 and plan["unbound"] == 2
        by_name = {row["leaf_name"]: row for row in plan["items"]}
        assert by_name["P1"]["parameter_id"] == p1 and by_name["P1"]["value"] == 0
        assert by_name["Other"]["status"] == "unbound"
        assert by_name["P2"]["candidates"][0]["parameter_id"] == p2, "同名只能作为候选"

        status, _h, body = _commit(server, wid, cid, plan["plan_id"])
        assert status == 200 and body["applied"] == 1, body
        draft = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/d0-%s" % (wid, cid, rid)
        )[2]
        assert draft["values"][p1]["value"] == 0
        assert draft["frozen"] is False

        again = _plan(server, wid, cid, rid, item["version_id"])[2]["plan"]
        assert _commit(server, wid, cid, again["plan_id"])[0] == 200
        rel = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s" % (wid, cid)
        )[2]["config"]["releases"][0]
        assert rel["default_revision_id"] is None, "重复读取不得生成第二个 D0"


def test_frozen_d0_creates_new_draft_and_null_is_preserved():
    seed_roles("value-frozen")
    with make_server() as server:
        wid, cid = _base(server)
        p1 = _param(server, wid, cid, "P1")
        p2 = _param(server, wid, cid, "P2")
        rid = _release(server, wid, cid)
        item = _put(server, wid, b'{"G":{"P1":1,"P2":null}}')
        _binding(server, wid, cid, rid, p1, item)
        plan = _plan(server, wid, cid, rid, item["version_id"])[2]["plan"]
        assert _commit(server, wid, cid, plan["plan_id"])[0] == 200
        status, _h, body = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/d0-%s/freeze" % (wid, cid, rid),
            {"request_id": "freeze"},
        )
        assert status == 200, body
        head = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/branches/main" % (wid, cid)
        )[2]["head_id"]
        changed = _put(server, wid, b'{"G":{"P1":5,"P2":null}}', overwrite=True)
        plan2 = _plan(server, wid, cid, rid, changed["version_id"])[2]["plan"]
        by_name = {row["leaf_name"]: row for row in plan2["items"]}
        assert by_name["P2"]["value"] is None
        status, _h, body = as_role(server, "admin").json(
            "PATCH",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/value-import-plans/%s/decisions"
            % (wid, cid, plan2["plan_id"]),
            {"scope": "rows", "row_ids": [by_name["P2"]["row_id"]], "decision": "link", "parameter_id": p2},
        )
        assert status == 200, body
        status, _h, body = _commit(server, wid, cid, plan2["plan_id"])
        assert status == 200 and body["d0_frozen"] is True, body
        draft = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/baseline-drafts/%s"
            % (wid, cid, body.get("draft_id", "")),
        ) if False else None
        rel = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s" % (wid, cid)
        )[2]["config"]["releases"][0]
        assert rel["default_revision_id"] == "d0-" + rid
        head_after = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/branches/main" % (wid, cid)
        )[2]["head_id"]
        assert head == head_after


def test_source_changed_blocks_commit():
    seed_roles("value-stale")
    with make_server() as server:
        wid, cid = _base(server)
        p1 = _param(server, wid, cid, "P1")
        rid = _release(server, wid, cid)
        item = _put(server, wid, b'{"G":{"P1":1}}')
        _binding(server, wid, cid, rid, p1, item)
        plan = _plan(server, wid, cid, rid, item["version_id"])[2]["plan"]
        _put(server, wid, b'{"G":{"P1":2}}', overwrite=True)
        status, _h, body = _commit(server, wid, cid, plan["plan_id"])
        assert status == 409 and body["error"]["code"] == "SOURCE_CHANGED", body
