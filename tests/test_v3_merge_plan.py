"""M14-02 合并计划、决议与新草稿（INH-2151）。"""

from __future__ import annotations

from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage
from app.services import config_v3_catalog


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


def _patch_values(server, wid, cid, kind, did, items, expected=0):
    status, _h, body = as_role(server, "admin").json(
        "PATCH", "/api/v3/workspaces/%s/profiles/default/configs/%s/%s/%s/values" % (wid, cid, kind, did),
        {"request_id": "v-" + did, "expected_revision": expected, "items": items},
        headers={"If-Match": '"%d"' % expected},
    )
    assert status == 200, body


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
    _patch_values(server, wid, cid, "baseline-drafts", did, items)
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/baseline-drafts/%s/publish" % (wid, cid, did),
        {"request_id": "b-" + did, "expected_revision": 1},
    )
    assert status == 200, body
    return body["baseline"]["baseline_id"]


def _plan(server, wid, cid, payload):
    return as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/merge-plans" % (wid, cid), payload
    )


def _decide(server, wid, cid, plan_id, payload):
    return as_role(server, "admin").json(
        "PATCH", "/api/v3/workspaces/%s/profiles/default/configs/%s/merge-plans/%s/decisions" % (wid, cid, plan_id), payload
    )


def _commit(server, wid, cid, plan_id, payload):
    return as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/merge-plans/%s/commit" % (wid, cid, plan_id), payload
    )


def _scenario(server):
    wid, cid = _base(server)
    p1 = _param(server, wid, cid, "P1")
    p2 = _param(server, wid, cid, "P2")
    values = lambda first: [
        {"parameter_id": p1, "membership": "included", "enabled": True, "value_state": "present", "value": first},
        {"parameter_id": p2, "membership": "included", "enabled": True, "value_state": "present", "value": "1"},
    ]
    r690 = _release(server, wid, cid, "690")
    _patch_values(server, wid, cid, "drafts", "d0-" + r690, values("1"))
    _freeze(server, wid, cid, r690)
    b1 = _baseline(server, wid, cid, r690, values("2"))
    b2 = _baseline(server, wid, cid, r690, values("9"))
    r691 = _release(server, wid, cid, "691")
    target = values("3")
    target[1]["value"] = "3"
    _patch_values(server, wid, cid, "drafts", "d0-" + r691, target)
    _freeze(server, wid, cid, r691)
    return wid, cid, p1, p2, b1, b2, r691


def test_merge_plan_blocks_pending_and_commits_new_baseline_draft():
    seed_roles("merge-plan")
    with make_server() as server:
        wid, cid, p1, p2, b1, b2, r691 = _scenario(server)
        status, _h, body = _plan(server, wid, cid, {
            "base_revision_id": b1, "ours_revision_id": b2, "theirs_revision_id": "d0-" + r691,
        })
        assert status == 201, body
        plan = body["plan"]
        assert plan["pending"] == 1
        rows = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/merge-plans/%s/rows?filter=conflict"
            % (wid, cid, plan["plan_id"]),
        )[2]
        assert rows["total"] == 1
        row = rows["items"][0]
        assert row["parameter_id"] == p1
        assert row["decision"] == "pending"
        assert "adopt_target" in row["allowed_decisions"]

        status, _h, body = _commit(server, wid, cid, plan["plan_id"], {})
        assert status == 409 and body["error"]["code"] == "PLAN_STALE", body
        assert p1 in body["error"]["detail"]["fields"]["parameter_ids"]

        status, _h, body = _decide(server, wid, cid, plan["plan_id"], {
            "scope": "rows", "row_ids": [p1], "decision": "adopt_target",
        })
        assert status == 200 and body["pending"] == 0, body

        head_before = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/branches/main" % (wid, cid)
        )[2]["head_id"]
        status, _h, body = _commit(server, wid, cid, plan["plan_id"], {})
        assert status == 200, body
        draft = body["draft"]
        all_rows = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/merge-plans/%s/rows?limit=50"
            % (wid, cid, plan["plan_id"]),
        )[2]["items"]
        by_pid = {row["parameter_id"]: row for row in all_rows}
        assert draft["kind"] == "baseline" and draft["values"][p1]["value"] == "3", (draft, by_pid.get(p2))
        assert draft["values"][p2]["value"] == "3", by_pid[p2]
        head_after = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/branches/main" % (wid, cid)
        )[2]["head_id"]
        assert head_before == head_after == b2, "提交只产草稿，不得移动 head"


def test_stale_merge_plan_keeps_decisions_and_requires_recompute():
    seed_roles("merge-stale")
    with make_server() as server:
        wid, cid, p1, p2, b1, b2, r691 = _scenario(server)
        plan = _plan(server, wid, cid, {
            "base_revision_id": b1, "ours_revision_id": b2, "theirs_revision_id": "d0-" + r691,
        })[2]["plan"]
        assert _decide(server, wid, cid, plan["plan_id"], {
            "scope": "rows", "row_ids": [p1], "decision": "keep_local",
        })[0] == 200
        with storage.use_workspace(wid), storage.use_profile("default"):
            doc = config_v3_catalog._load("default")
            config = doc["configs"][cid]
            config["main_branch"]["baselines"][b2]["values"][p1]["value"] = "10"
            config_v3_catalog._save("default", doc)
        rows = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/merge-plans/%s/rows"
            % (wid, cid, plan["plan_id"]),
        )[2]
        assert rows["stale"] is True
        decision = {row["parameter_id"]: row["decision"] for row in rows["items"]}
        assert decision[p1] == "keep_local", "stale 计划必须保留决议"
        status, _h, body = _commit(server, wid, cid, plan["plan_id"], {})
        assert status == 409 and body["error"]["code"] == "PLAN_STALE", body

