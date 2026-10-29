"""M14-05 事件审计与恢复草稿（INH-2154）。"""

from __future__ import annotations

from authz_fixtures import as_role, make_server, seed_roles


def _base(server, name="W1"):
    wid = as_role(server, "admin").json("POST", "/api/v3/workspaces", {"name": name})[2]["workspace"]["id"]
    cid = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs" % wid,
        {"code": "MC-001", "name": "配置"},
    )[2]["config"]["config_id"]
    return wid, cid


def _param(server, wid, cid):
    return as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
        {"name": "P1", "directory": ["G"], "type": "number"},
    )[2]["parameter"]["parameter_id"]


def _release(server, wid, cid):
    return as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/releases" % (wid, cid),
        {"version": "690"},
    )[2]["release"]["release_id"]


def _values(server, wid, cid, did, items, expected=0, kind="drafts"):
    status, _h, body = as_role(server, "admin").json(
        "PATCH", "/api/v3/workspaces/%s/profiles/default/configs/%s/%s/%s/values" % (wid, cid, kind, did),
        {"request_id": "v", "expected_revision": expected, "items": items},
        headers={"If-Match": '"%d"' % expected},
    )
    assert status == 200, body


def test_restore_creates_new_draft_and_events_are_scoped():
    seed_roles("history")
    with make_server() as server:
        wid, cid = _base(server)
        p1 = _param(server, wid, cid)
        rid = _release(server, wid, cid)
        _values(server, wid, cid, "d0-" + rid, [
            {"parameter_id": p1, "membership": "included", "enabled": True,
             "value_state": "present", "value": "1"},
        ])
        status, _h, body = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/d0-%s/freeze" % (wid, cid, rid),
            {"request_id": "f"},
        )
        assert status == 200, body
        status, _h, body = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/baseline-drafts" % (wid, cid, rid), {}
        )
        assert status in (200, 201), body
        did = body["draft"]["draft_id"]
        _values(server, wid, cid, did, [
            {"parameter_id": p1, "membership": "included", "enabled": True,
             "value_state": "present", "value": "2"},
        ], 0, "baseline-drafts")
        status, _h, body = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/baseline-drafts/%s/publish" % (wid, cid, did),
            {"request_id": "b", "expected_revision": 1},
        )
        assert status == 200, body
        baseline = body["baseline"]["baseline_id"]
        head = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/branches/main" % (wid, cid)
        )[2]["head_id"]

        status, _h, body = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/restore-plans" % (wid, cid),
            {"revision_id": baseline, "request_id": "restore-1"},
        )
        assert status == 201, body
        plan = body["plan"]
        assert body["draft"]["values"][p1]["value"] == "2"
        assert body["draft"]["restore_source"] == "history"
        head_after = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/branches/main" % (wid, cid)
        )[2]["head_id"]
        assert head == head_after == baseline

        events = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/events" % (wid, cid)
        )[2]["items"]
        assert len(events) == 1 and events[0]["type"] == "restore"
        assert events[0]["principal"] and events[0]["ip"] and events[0]["undo_of"] == ""
        assert events[0]["source"]["revision_id"] == baseline

        status, _h, body = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/restore-plans/%s/undo"
            % (wid, cid, plan["plan_id"]), {}
        )
        assert status == 200, body
        assert body["event"]["undo_of"] == events[0]["event_id"]
        events2 = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/events" % wid
        )[2]["items"]
        assert len(events2) == 2 and events2[0]["type"] == "undo", "读审计不得再产生事件"

        other, _ocid = _base(server, "W2")
        assert as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/events" % other
        )[2]["items"] == []

        status, _h, body = as_role(server, "member").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/restore-plans" % (wid, cid),
            {"revision_id": baseline},
        )
        assert status == 403, body
