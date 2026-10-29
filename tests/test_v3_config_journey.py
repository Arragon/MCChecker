"""M14-06 配置管理全流程与旧 config 域停写（INH-2155）。"""

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
    return as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
        {"name": name, "directory": ["G"], "type": "number"},
    )[2]["parameter"]["parameter_id"]


def _release(server, wid, cid, version):
    return as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/releases" % (wid, cid),
        {"version": version},
    )[2]["release"]["release_id"]


def _values(server, wid, cid, kind, did, items, expected=0):
    status, _h, body = as_role(server, "admin").json(
        "PATCH", "/api/v3/workspaces/%s/profiles/default/configs/%s/%s/%s/values" % (wid, cid, kind, did),
        {"request_id": "v", "expected_revision": expected, "items": items},
        headers={"If-Match": '"%d"' % expected},
    )
    assert status == 200, body
    return body["revision"]


def _freeze(server, wid, cid, rid):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/d0-%s/freeze" % (wid, cid, rid),
        {"request_id": "f-" + rid},
    )
    assert status == 200, body


def _baseline(server, wid, cid, rid, items):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/baseline-drafts" % (wid, cid, rid), {}
    )
    assert status in (200, 201), body
    did = body["draft"]["draft_id"]
    _values(server, wid, cid, "baseline-drafts", did, items)
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/baseline-drafts/%s/publish" % (wid, cid, did),
        {"request_id": "b-" + did, "expected_revision": 1},
    )
    assert status == 200, body
    return body["baseline"]["baseline_id"]


def _all(sp, p1, p2, first, second):
    return [
        {"parameter_id": p1, "membership": "included", "enabled": True, "value_state": "present", "value": first},
        {"parameter_id": p2, "membership": "included", "enabled": True, "value_state": "present", "value": second},
    ]


def test_full_journey_and_old_config_readonly(monkeypatch):
    seed_roles("journey")
    with make_server() as server:
        wid, cid = _base(server)
        p1 = _param(server, wid, cid, "P1")
        p2 = _param(server, wid, cid, "P2")
        r680 = _release(server, wid, cid, "680")
        _values(server, wid, cid, "drafts", "d0-" + r680, _all(server, p1, p2, "1", "1"))
        _freeze(server, wid, cid, r680)
        b1 = _baseline(server, wid, cid, r680, _all(server, p1, p2, "2", "1"))
        r690 = _release(server, wid, cid, "690")
        _values(server, wid, cid, "drafts", "d0-" + r690, _all(server, p1, p2, "2", "2"))
        _freeze(server, wid, cid, r690)
        bn = _baseline(server, wid, cid, r690, _all(server, p1, p2, "9", "2"))
        r691 = _release(server, wid, cid, "691")
        _values(server, wid, cid, "drafts", "d0-" + r691, _all(server, p1, p2, "3", "3"))
        _freeze(server, wid, cid, r691)

        status, _h, body = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/machine-branches" % (wid, cid),
            {"machine_no": "MC-001", "release_id": r690, "base_kind": "baseline", "base_revision_id": bn},
        )
        assert status == 201, body
        mid = body["machine"]["machine_id"]
        for expected in (0, 0):
            status, _h, body = as_role(server, "admin").json(
                "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/machines/%s/publish" % (wid, cid, mid),
                {"expected_revision": expected}, headers={"If-Match": '"%d"' % expected},
            )
            assert status == 200, body
            if expected == 0:
                status, _h, body = as_role(server, "admin").json(
                    "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/machines/%s/drafts" % (wid, cid, mid),
                    {"release_id": r690, "base_kind": "baseline", "base_revision_id": bn},
                )
                assert status == 201, body
        machine = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/machines" % (wid, cid)
        )[2]["items"][0]
        assert [row["label"] for row in machine["revisions"]] == ["r1", "r2"]

        plan = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/merge-plans" % (wid, cid),
            {"base_revision_id": b1, "ours_revision_id": bn, "theirs_revision_id": "d0-" + r691},
        )[2]["plan"]
        assert plan["pending"] >= 1
        conflict_ids = [
            row["parameter_id"] for row in as_role(server, "admin").json(
                "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/merge-plans/%s/rows?filter=conflict"
                % (wid, cid, plan["plan_id"])
            )[2]["items"]
        ]
        for pid in conflict_ids:
            status, _h, body = as_role(server, "admin").json(
                "PATCH", "/api/v3/workspaces/%s/profiles/default/configs/%s/merge-plans/%s/decisions"
                % (wid, cid, plan["plan_id"]),
                {"scope": "rows", "row_ids": [pid], "decision": "adopt_target"},
            )
            assert status == 200, body
        status, _h, body = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/merge-plans/%s/commit"
            % (wid, cid, plan["plan_id"]), {}
        )
        assert status == 200 and body["draft"]["kind"] == "baseline", body

        status, _h, body = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/restore-plans" % (wid, cid),
            {"revision_id": b1},
        )
        assert status == 201, body
        restore_plan = body["plan"]
        status, _h, body = as_role(server, "admin").json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/restore-plans/%s/undo"
            % (wid, cid, restore_plan["plan_id"]), {}
        )
        assert status == 200, body

        events = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/events" % (wid, cid)
        )[2]["items"]
        types = {row["type"] for row in events}
        assert {"merge_commit", "restore", "undo"} <= types
        member = as_role(server, "member")
        assert member.json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/branches/main" % (wid, cid)
        )[0] == 200
        assert member.json(
            "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
            {"name": "Denied", "directory": ["X"]},
        )[0] == 403

        monkeypatch.setenv("MCHECKER_CONFIG_MANAGEMENT", "readonly")
        status, _h, body = as_role(server, "deployer").json(
            "POST", "/api/v1/config/releases", {"version": "legacy"}
        )
        assert status == 503 and body["error"]["code"] == "CONFIG_DISABLED", body
        assert as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/graph" % (wid, cid)
        )[0] == 200, "旧域停写不影响 V3 读"
