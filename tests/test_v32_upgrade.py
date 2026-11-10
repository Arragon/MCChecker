"""V3.2 upgrade compares the captured draft and its actual adopted standard."""
from authz_fixtures import as_role, make_server, seed_roles
from test_v3_merge_plan import _base, _param, _release, _patch_values, _freeze, _plan, _decide, _commit


def _scenario(server):
    admin = as_role(server, "admin")
    wid, cid = _base(server)
    params = [_param(server, wid, cid, name) for name in ("Follow", "Fixed", "Custom")]
    old, new = _release(server, wid, cid, "690"), _release(server, wid, cid, "691")
    for rid, value in [(old, "1"), (new, "3")]:
        _patch_values(server, wid, cid, "drafts", "d0-" + rid, [{"parameter_id": p, "membership": "included", "enabled": True, "value_state": "present", "value": value} for p in params])
        _freeze(server, wid, cid, rid)
    base = "/api/v3/workspaces/%s/profiles/default/configs/%s" % (wid, cid)
    machine = admin.json("POST", base + "/machine-branches", {"machine_no": "MC-001", "release_id": old})[2]["machine"]
    path = base + "/machines/" + machine["machine_id"]
    assert admin.json("PATCH", path + "/draft/values", {"request_id": "modes", "expected_revision": 0, "items": [
        {"parameter_id": params[1], "value_state": "present", "value": "5", "intent": "pin"},
        {"parameter_id": params[2], "value_state": "present", "value": "1", "intent": "override"} ]})[0] == 200
    saved = admin.json("POST", path + "/publish", {"expected_revision": 1})[2]["machine"]["head_id"]
    draft = admin.json("POST", base + "/restore-plans", {"request_id": "editing", "revision_id": saved})[2]["draft"]
    assert admin.json("PATCH", path + "/draft/values", {"request_id": "current-edit", "expected_revision": 0,
        "items": [{"parameter_id": params[2], "value_state": "present", "value": "7", "intent": "override"}]})[0] == 200
    return wid, cid, base, machine["machine_id"], saved, draft["draft_id"], old, new, params


def test_upgrade_uses_draft_explicit_modes_and_target_source_without_publishing():
    seed_roles("v32-upgrade")
    with make_server() as server:
        wid, cid, base, mid, saved, did, old, new, params = _scenario(server)
        payload = {"machine_id": mid, "ours_revision_id": did, "theirs_revision_id": "d0-" + new}
        status, _, response = _plan(server, wid, cid, payload)
        assert status == 201, response
        plan = response["plan"]
        assert plan["base"]["revision_id"] == "d0-" + old and plan["ours"]["revision_id"] == did
        assert plan["pending"] == 1
        admin = as_role(server, "admin")
        rows = admin.json("GET", base + "/merge-plans/" + plan["plan_id"] + "/rows")[2]["items"]
        by_id = {r["parameter_id"]: r for r in rows}
        assert by_id[params[0]]["decision"] == "adopt_target"
        assert by_id[params[1]]["decision"] == "keep_local"
        assert by_id[params[2]]["ours_member"]["value"] == "7"
        assert _commit(server, wid, cid, plan["plan_id"], {"request_id": "upgrade"})[0] == 409
        assert _decide(server, wid, cid, plan["plan_id"], {"row_ids": [params[2]], "decision": "adopt_target"})[0] == 200
        status, _, upgraded = _commit(server, wid, cid, plan["plan_id"], {"request_id": "upgrade"})
        assert status == 200, upgraded
        machine = admin.json("GET", base + "/machines/" + mid)[2]["machine"]
        assert machine["head_id"] == saved and machine["release_id"] == new
        assert machine["draft"]["draft_id"] == did
        assert machine["draft"]["base_revision_id"] == "d0-" + new
        values = machine["draft"]["values"]
        assert values[params[0]]["value"] == "3" and values[params[0]]["intent"] == "inherit"
        assert values[params[1]]["value"] == "5" and values[params[1]]["intent"] == "pin"
        assert values[params[2]]["value"] == "3" and values[params[2]]["intent"] == "inherit"
        index = admin.json("GET", base.rsplit("/configs/", 1)[0] + "/config-machines")[2]["items"][0]
        assert index["release_id"] == old and index["draft"]["release_id"] == new
        replay = _commit(server, wid, cid, plan["plan_id"], {"request_id": "upgrade"})[2]
        assert replay["replayed"] is True and replay["draft"]["revision"] == upgraded["draft"]["revision"]
        assert _plan(server, wid, cid, dict(payload, machine_id="other"))[0] == 404
        bad = admin.json("POST", base + "/machine-branches", {"machine_no": "Wrong version", "release_id": new,
            "base_kind": "default", "base_revision_id": "d0-" + old})
        assert bad[0] == 400 and bad[2]["error"]["detail"]["field"] == "base_revision_id", bad


def test_upgrade_plan_detects_source_changes_even_if_values_do_not_change():
    seed_roles("v32-upgrade-stale")
    with make_server() as server:
        wid, cid, base, mid, saved, did, old, new, params = _scenario(server)
        status, _, response = _plan(server, wid, cid, {"machine_id": mid, "ours_revision_id": did, "theirs_revision_id": "d0-" + new})
        assert status == 201, response
        from app.core import storage
        from app.services import config_v3_catalog
        with storage.use_workspace(wid), storage.use_profile("default"):
            doc = config_v3_catalog._load("default")
            doc["configs"][cid]["machines"][mid]["draft"]["base_revision_id"] = "d0-" + new
            config_v3_catalog._save("default", doc)
        result = _commit(server, wid, cid, response["plan"]["plan_id"], {"request_id": "stale"})
        assert result[0] == 409, result


def test_missing_adopted_source_requires_explicit_old_standard_in_the_saved_machine_version():
    seed_roles("v32-upgrade-source-missing")
    with make_server() as server:
        wid, cid, base, mid, saved, did, old, new, params = _scenario(server)
        from app.core import storage
        from app.services import config_v3_catalog
        with storage.use_workspace(wid), storage.use_profile("default"):
            doc = config_v3_catalog._load("default")
            draft = doc["configs"][cid]["machines"][mid]["draft"]
            draft["base_revision_id"] = "legacy-missing-source"
            config_v3_catalog._save("default", doc)
        body = {"machine_id": mid, "ours_revision_id": did, "theirs_revision_id": "d0-" + new}
        status, _, missing = _plan(server, wid, cid, body)
        assert status == 400 and missing["error"]["detail"]["field"] == "base_revision_id", missing
        read = as_role(server, "visitor").json("POST", base + "/parameter-query", {"revision_id": did, "limit": 30})
        assert read[0] == 200 and read[2]["total"] == 3, read
        assert _plan(server, wid, cid, dict(body, base_revision_id="d0-" + new))[0] == 400
        status, _, explicit = _plan(server, wid, cid, dict(body, base_revision_id="d0-" + old))
        assert status == 201 and explicit["plan"]["base"]["revision_id"] == "d0-" + old, explicit
