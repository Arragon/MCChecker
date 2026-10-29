"""M13-05 服务端提取计划与决议（INH-2147）。"""

from __future__ import annotations

import json

from authz_fixtures import as_role, make_server, seed_roles


def _create(server, name):
    status, _h, body = as_role(server, "admin").json("POST", "/api/v3/workspaces", {"name": name})
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


def _plan(server, wid, cid, version_id):
    return as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans" % (wid, cid),
        {"version_id": version_id},
    )


def _rows(server, wid, cid, plan_id, **query):
    path = "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans/%s/rows" % (
        wid, cid, plan_id,
    )
    parts = ["%s=%s" % (k, v) for k, v in query.items()]
    if parts:
        path += "?" + "&".join(parts)
    return as_role(server, "admin").json("GET", path)


def _decide(server, wid, cid, plan_id, payload):
    return as_role(server, "admin").json(
        "PATCH",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans/%s/decisions"
        % (wid, cid, plan_id),
        payload,
    )


def test_plan_rows_and_decisions_do_not_create_parameters():
    seed_roles("plan")
    payload = {"G": {"Gain": 1, "New": 2}, "Keep": {"A": 3}}
    with make_server() as server:
        wid = _create(server, "W1")
        cid = _config(server, wid)
        item = _put(server, wid, "plan.json", json.dumps(payload).encode())
        status, _h, body = _plan(server, wid, cid, item["version_id"])
        assert status == 201, body
        plan = body["plan"]
        assert plan["status"] == "open" and plan["counts"]["new"] == 3
        assert plan["pending"] == 0
        plan_id = plan["plan_id"]

        first = _rows(server, wid, cid, plan_id, limit=2)[2]
        assert first["total"] == 3 and len(first["items"]) == 2
        assert first["items"][0]["decision"]["action"] == "add"
        assert first["filter_digest"]

        status, _h, decided = _decide(server, wid, cid, plan_id, {
            "scope": "rows", "row_ids": [first["items"][0]["row_id"]],
            "decision": {"action": "skip"},
        })
        assert status == 200, decided
        assert decided["updated"] == 1
        again = _rows(server, wid, cid, plan_id, limit=2)[2]
        assert again["items"][0]["decision"]["action"] == "skip"

        status, _h, body = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid)
        )
        assert status == 200 and body["items"] == [], "计划/决议不得创建参数"


def test_batch_filter_decision_requires_matching_digest():
    seed_roles("plan-batch")
    payload = {"G": {"A": 1, "B": 2, "C": 3}}
    with make_server() as server:
        wid = _create(server, "W1")
        cid = _config(server, wid)
        item = _put(server, wid, "batch.json", json.dumps(payload).encode())
        plan = _plan(server, wid, cid, item["version_id"])[2]["plan"]
        page = _rows(server, wid, cid, plan["plan_id"], filter="new")[2]
        assert page["total"] == 3

        status, _h, body = _decide(server, wid, cid, plan["plan_id"], {
            "scope": "filter", "filter": "new", "filter_digest": "wrong",
            "decision": {"action": "skip"},
        })
        assert status == 409 and body["error"]["code"] == "PLAN_STALE", body

        status, _h, body = _decide(server, wid, cid, plan["plan_id"], {
            "scope": "filter", "filter": "new", "filter_digest": page["filter_digest"],
            "decision": {"action": "skip"},
        })
        assert status == 200 and body["updated"] == 3, body
        after = _rows(server, wid, cid, plan["plan_id"], filter="new")[2]
        assert all(row["decision"]["action"] == "skip" for row in after["items"])


def test_conflict_requires_explicit_decision_and_null_type():
    seed_roles("plan-conflict")
    payload = {"G": {"Gain": 1}, "H": {"Gain": 2}, "N": None}
    with make_server() as server:
        wid = _create(server, "W1")
        cid = _config(server, wid)
        status, _h, body = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
            {"name": "Gain", "directory": ["G"], "type": "number"},
        )
        assert status == 201, body
        item = _put(server, wid, "conflict.json", json.dumps(payload).encode())
        plan = _plan(server, wid, cid, item["version_id"])[2]["plan"]
        assert plan["counts"]["same_name_other_path"] == 1
        assert plan["counts"]["null_uncertain"] == 1
        assert plan["pending"] == 2
        page = _rows(server, wid, cid, plan["plan_id"], filter="conflict")[2]
        by_name = {row["leaf_name"]: row for row in page["items"]}

        null_row = by_name["N"]
        status, _h, body = _decide(server, wid, cid, plan["plan_id"], {
            "scope": "rows", "row_ids": [null_row["row_id"]],
            "decision": {"action": "add", "type": ""},
        })
        assert status == 400 and body["error"]["code"] == "INVALID_INPUT", body
        status, _h, body = _decide(server, wid, cid, plan["plan_id"], {
            "scope": "rows", "row_ids": [null_row["row_id"]],
            "decision": {"action": "add", "type": "number"},
        })
        assert status == 200 and body["pending"] == 1, body

        other = by_name["Gain"]
        status, _h, body = _decide(server, wid, cid, plan["plan_id"], {
            "scope": "rows", "row_ids": [other["row_id"]],
            "decision": {"action": "skip"},
        })
        assert status == 200 and body["pending"] == 0, body


def test_3001_rows_are_paginated_server_side():
    seed_roles("plan-3001")
    payload = {"Channels": [{"Gain": index} for index in range(3001)]}
    with make_server() as server:
        wid = _create(server, "W1")
        cid = _config(server, wid)
        item = _put(server, wid, "many.json", json.dumps(payload).encode())
        plan = _plan(server, wid, cid, item["version_id"])[2]["plan"]
        assert plan["counts"]["total"] == 3001 and plan["counts"]["new"] == 3001
        page = _rows(server, wid, cid, plan["plan_id"], offset=0, limit=50)[2]
        assert page["total"] == 3001 and len(page["items"]) == 50
        last = _rows(server, wid, cid, plan["plan_id"], offset=3000, limit=50)[2]
        assert len(last["items"]) == 1
        assert last["items"][0]["row_id"] not in {row["row_id"] for row in page["items"]}


def test_stale_plan_keeps_decisions_and_recompute_merges_by_row_id():
    seed_roles("plan-stale")
    payload = {"G": {"A": 1, "B": 2}}
    with make_server() as server:
        wid = _create(server, "W1")
        cid = _config(server, wid)
        item = _put(server, wid, "stale.json", json.dumps(payload).encode())
        plan = _plan(server, wid, cid, item["version_id"])[2]["plan"]
        page = _rows(server, wid, cid, plan["plan_id"], filter="new")[2]
        status, _h, body = _decide(server, wid, cid, plan["plan_id"], {
            "scope": "rows", "row_ids": [page["items"][0]["row_id"]],
            "decision": {"action": "skip"},
        })
        assert status == 200, body

        status, _h, body = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
            {"name": "Unrelated", "directory": ["Other"]},
        )
        assert status == 201, body

        stale = _rows(server, wid, cid, plan["plan_id"])[2]
        assert stale["status"] == "stale" and stale["stale"] is True
        assert stale["items"][0]["decision"]["action"] == "skip", "stale 计划必须保留决议"
        status, _h, body = _decide(server, wid, cid, plan["plan_id"], {
            "scope": "rows", "row_ids": [page["items"][0]["row_id"]],
            "decision": {"action": "add"},
        })
        assert status == 409 and body["error"]["code"] == "PLAN_STALE", body

        status, _h, body = as_role(server, "admin").json(
            "POST",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans/%s/recompute"
            % (wid, cid, plan["plan_id"]),
            {},
        )
        assert status == 200, body
        assert body["plan"]["status"] == "open"
        assert body["preserved"] == 1
        fresh = _rows(server, wid, cid, plan["plan_id"], filter="new")[2]
        assert fresh["items"][0]["decision"]["action"] == "skip"


def test_plan_is_scoped_and_member_cannot_create():
    seed_roles("plan-scope")
    with make_server() as server:
        wid = _create(server, "W1")
        other = _create(server, "W2")
        cid = _config(server, wid)
        item = _put(server, wid, "scope.json", b'{"A":1}')
        other_item = _put(server, other, "other.json", b'{"B":1}')
        status, _h, body = as_role(server, "member").json(
            "POST",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans" % (wid, cid),
            {"version_id": item["version_id"]},
        )
        assert status == 403, body
        plan = _plan(server, wid, cid, item["version_id"])[2]["plan"]
        status, _h, body = as_role(server, "admin").json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans/%s/rows"
            % (other, cid, plan["plan_id"]),
        )
        assert status == 404, body
        status, _h, body = as_role(server, "admin").json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/extraction-plans/%s/rows"
            % (wid, cid, plan["plan_id"]),
        )
        assert status == 200 and body["plan_id"] == plan["plan_id"]
