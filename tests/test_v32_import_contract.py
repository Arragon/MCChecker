"""An import must start from this software version's saved standard."""

import copy

from authz_fixtures import as_role, make_server, seed_roles
from test_v3_value_import import _base, _binding, _commit, _param, _plan, _put, _release


def test_import_uses_its_release_standard_instead_of_global_head():
    seed_roles("v32-import-source")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid, cid = _base(server)
        p1, p2 = _param(server, wid, cid, "P1"), _param(server, wid, cid, "P2")
        rid = _release(server, wid, cid)
        base = "/api/v3/workspaces/%s/profiles/default/configs/%s" % (wid, cid)
        file = _put(server, wid, b'{"G":{"P1":7}}')
        _binding(server, wid, cid, rid, p1, file)
        assert admin.json("PATCH", base + "/drafts/d0-" + rid + "/values", {"request_id": "initial-values", "expected_revision": 0,
            "items": [{"parameter_id": p1, "membership": "included", "value_state": "present", "value": "1"}]})[0] == 200
        assert admin.json("POST", base + "/drafts/d0-" + rid + "/freeze", {"request_id": "initial"})[0] == 200
        other = admin.json("POST", base + "/releases", {"version": "691"})[2]["release"]["release_id"]
        from app.core import storage
        from app.services import config_v3_catalog
        # Only pytest's isolated catalog receives the synthetic historical records.
        with storage.use_workspace(wid), storage.use_profile("default"):
            doc = config_v3_catalog._load("default")
            config = doc["configs"][cid]
            values = {p: {"membership": "included", "enabled": True, "value_state": "present", "value": value}
                      for p, value in [(p1, "1"), (p2, "4")]}
            row = {"baseline_id": "bl-a", "release_id": rid, "label": "B1", "values": values,
                   "definition_revision": config["releases"][rid]["definition_revision"], "published_at": "2026-11-09 12:00:00"}
            other_row = copy.deepcopy(row)
            other_row.update(baseline_id="bl-b", release_id=other, label="B2")
            other_row["values"][p2]["value"] = "999"
            config["main_branch"] = {"head_id": "bl-b", "next_seq": 3, "baselines": {"bl-a": row, "bl-b": other_row}}
            config_v3_catalog._save("default", doc)
        plan = _plan(server, wid, cid, rid, file["version_id"])[2]["plan"]
        status, _, imported = _commit(server, wid, cid, plan["plan_id"])
        assert status == 200, imported
        release = next(r for r in admin.json("GET", base)[2]["config"]["releases"] if r["release_id"] == rid)
        draft = admin.json("GET", base + "/baseline-drafts/" + release["baseline_draft"]["draft_id"])[2]
        assert draft["source_revision_id"] == "bl-a"
        assert draft["values"][p1]["value"] == 7
        assert draft["values"][p2]["value"] == "4"
        assert admin.json("GET", base + "/branches/main")[2]["head_id"] == "bl-b"


def test_reviewed_import_rejects_changed_draft_and_replays_one_commit():
    seed_roles("v32-import-cas")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid, cid = _base(server)
        pid = _param(server, wid, cid, "P1")
        rid = _release(server, wid, cid)
        base = "/api/v3/workspaces/%s/profiles/default/configs/%s" % (wid, cid)
        file = _put(server, wid, b'{"G":{"P1":2}}')
        _binding(server, wid, cid, rid, pid, file)
        plan = _plan(server, wid, cid, rid, file["version_id"])[2]["plan"]
        assert plan["target"] == {"draft_id": "d0-" + rid, "revision": 0}
        assert admin.json("PATCH", base + "/drafts/d0-" + rid + "/values", {"request_id": "other", "expected_revision": 0,
            "items": [{"parameter_id": pid, "membership": "included", "value_state": "present", "value": "7"}]})[0] == 200
        commit = base + "/value-import-plans/" + plan["plan_id"] + "/commit"
        payload = {"request_id": "reviewed-import", "expected_draft_id": plan["target"]["draft_id"], "expected_revision": 0}
        assert admin.json("POST", commit, payload)[0] == 409
        draft = admin.json("GET", base + "/drafts/d0-" + rid)[2]
        assert draft["values"][pid]["value"] == "7"
        plan = _plan(server, wid, cid, rid, file["version_id"])[2]["plan"]
        commit = base + "/value-import-plans/" + plan["plan_id"] + "/commit"
        payload["expected_revision"] = plan["target"]["revision"]
        status, _, first = admin.json("POST", commit, payload)
        assert status == 200, first
        assert first["draft_id"] == "d0-" + rid and first["draft_revision"] == 2
        status, _, replay = admin.json("POST", commit, payload)
        assert status == 200 and replay["replayed"] is True, replay
        assert admin.json("GET", base + "/drafts/d0-" + rid)[2]["revision"] == 2
        assert admin.json("POST", commit, dict(payload, expected_revision=2))[0] == 409
        events = admin.json("GET", base + "/events")[2]["items"]
        assert len([event for event in events if event["type"] == "value_import"]) == 1


def test_import_reports_invalid_typed_value_before_it_can_enter_draft():
    seed_roles("v32-import-types")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid, cid = _base(server)
        pid = _param(server, wid, cid, "P1")
        rid = _release(server, wid, cid)
        file = _put(server, wid, b'{"G":{"P1":"not-a-number"}}')
        _binding(server, wid, cid, rid, pid, file)
        plan = _plan(server, wid, cid, rid, file["version_id"])[2]["plan"]
        assert plan["items"][0]["error"]
        assert _commit(server, wid, cid, plan["plan_id"])[0] == 400
        base = "/api/v3/workspaces/%s/profiles/default/configs/%s" % (wid, cid)
        assert admin.json("GET", base + "/drafts/d0-" + rid)[2]["revision"] == 0
