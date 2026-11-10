"""A retried save must address the reviewed draft, never its replacement."""

from authz_fixtures import as_role, make_server, seed_roles
from test_v3_machine_branch import _config, _create, _freeze_d0, _machine, _param, _release


def test_machine_save_replay_returns_original_record_and_one_audit_event():
    seed_roles("v32-save")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = _create(server, "Save")
        cid = _config(server, wid)
        pid = _param(server, wid, cid)
        rid = _release(server, wid, cid)
        _freeze_d0(server, wid, cid, pid, rid)
        machine = _machine(server, wid, cid, rid)[2]["machine"]
        base = "/api/v3/workspaces/%s/profiles/default/configs/%s" % (wid, cid)
        path = base + "/machines/" + machine["machine_id"]
        first_draft = machine["draft"]["draft_id"]
        save = {"expected_revision": 0, "expected_draft_id": first_draft,
                "request_id": "save-first", "note": "调整送料参数"}
        status, _, first = admin.json("POST", path + "/publish", save)
        assert status == 200, first
        first_id = first["machine"]["published_revision_id"]
        assert admin.json("GET", base + "/revisions/" + first_id)[2]["snapshot"]["note"] == save["note"]
        detail = admin.json("GET", base)[2]["config"]
        assert detail["saved_authors"][first_id]
        assert admin.json("GET", base + "/machines")[2]["items"][0]["revisions"][0]["note"] == save["note"]
        assert admin.json("POST", path + "/drafts", {"release_id": rid})[0] == 201
        new_draft = admin.json("GET", path)[2]["machine"]["draft"]
        stale_patch = {"request_id": "old-values", "expected_revision": 0,
                       "expected_draft_id": first_draft,
                       "items": [{"parameter_id": pid, "value_state": "present", "value": "8"}]}
        assert admin.json("PATCH", path + "/draft/values", stale_patch)[0] == 409
        assert admin.json("POST", path + "/publish", dict(save, request_id="stale-save"))[0] == 409
        second = dict(save, expected_draft_id=new_draft["draft_id"], request_id="save-second", note="第二次")
        status, _, result = admin.json("POST", path + "/publish", second)
        assert status == 200, result
        second_id = result["machine"]["head_id"]
        status, _, replay = admin.json("POST", path + "/publish", save)
        assert status == 200, replay
        assert replay["machine"]["head_id"] == second_id
        assert replay["machine"]["published_revision_id"] == first_id
        assert len(replay["machine"]["revisions"]) == 2
        events = admin.json("GET", base + "/events")[2]["items"]
        assert len([e for e in events if e["type"] == "machine"]) == 2
        assert admin.json("POST", path + "/publish", dict(save, note=3))[0] == 400


def test_standard_save_receipt_survives_opening_another_draft():
    seed_roles("v32-standard-save")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = _create(server, "Standards")
        cid = _config(server, wid)
        pid = _param(server, wid, cid)
        rid = _release(server, wid, cid)
        _freeze_d0(server, wid, cid, pid, rid)
        base = "/api/v3/workspaces/%s/profiles/default/configs/%s" % (wid, cid)
        create = base + "/releases/" + rid + "/baseline-drafts"
        draft = admin.json("POST", create)[2]["draft"]
        path = base + "/baseline-drafts/" + draft["draft_id"] + "/publish"
        payload = {"request_id": "standard-save", "expected_revision": 0, "note": "标准调整"}
        status, _, saved = admin.json("POST", path, payload)
        assert status == 200, saved
        saved_id = saved["baseline"]["baseline_id"]
        assert admin.json("GET", base + "/branches/main")[2]["baselines"][0]["note"] == "标准调整"
        assert admin.json("POST", create)[0] == 201
        status, _, replay = admin.json("POST", path, payload)
        assert status == 200, replay
        assert replay["baseline"]["baseline_id"] == saved_id
        assert replay["baseline"]["replayed"] is True
        assert admin.json("GET", base + "/revisions/" + saved_id)[2]["snapshot"]["note"] == "标准调整"
        assert len(admin.json("GET", base + "/branches/main")[2]["baselines"]) == 1
