"""M14-01 V3 修订上的 O/L/N 合并矩阵（INH-2150）。"""

from __future__ import annotations

from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage
from app.services import config_v3_catalog, config_v3_merge


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


def _release(server, wid, cid, version):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs/%s/releases" % (wid, cid),
        {"version": version},
    )
    assert status == 201, body
    return body["release"]["release_id"]


def _values(server, wid, cid, draft_id, items, expected=0):
    kind = "baseline-drafts" if str(draft_id).startswith("bd-") else "drafts"
    status, _h, body = as_role(server, "admin").json(
        "PATCH",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/%s/%s/values" % (wid, cid, kind, draft_id),
        {"request_id": "v-" + draft_id, "expected_revision": expected, "items": items},
        headers={"If-Match": '"%d"' % expected},
    )
    assert status == 200, body
    return body["revision"]


def _freeze(server, wid, cid, rid):
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/d0-%s/freeze" % (wid, cid, rid),
        {"request_id": "f-" + rid},
    )
    assert status == 200, body


def _baseline(server, wid, cid, rid, items):
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/baseline-drafts" % (wid, cid, rid),
        {},
    )
    assert status in (200, 201), body
    did = body["draft"]["draft_id"]
    _values(server, wid, cid, did, items, 0)
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/baseline-drafts/%s/publish" % (wid, cid, did),
        {"request_id": "b-" + did, "expected_revision": 1},
    )
    assert status == 200, body
    return body["baseline"]["baseline_id"]


def _machine(server, wid, cid, rid):
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/machine-branches" % (wid, cid),
        {"machine_no": "MC-001", "release_id": rid, "base_kind": "default"},
    )
    assert status == 201, body
    machine = body["machine"]
    return machine["machine_id"], machine["draft"]["draft_id"]


def _seed_machine_values(wid, cid, draft_id, entries):
    with storage.use_workspace(wid), storage.use_profile("default"):
        doc = config_v3_catalog._load("default")
        config = doc["configs"][cid]
        for machine in (config.get("machines") or {}).values():
            if isinstance(machine.get("draft"), dict) and machine["draft"].get("draft_id") == draft_id:
                machine["draft"]["values"] = entries
                config_v3_catalog._save("default", doc)
                return
    raise AssertionError("machine draft not found")


def _publish_machine(server, wid, cid, mid):
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/machines/%s/publish" % (wid, cid, mid),
        {"expected_revision": 0},
        headers={"If-Match": '"0"'},
    )
    assert status == 200, body
    return body["machine"]["head_id"]


def test_merge_matrix_uses_real_revision_refs_and_never_moves_heads():
    seed_roles("v3-merge")
    with make_server() as server:
        wid, cid = _base(server)
        p1 = _param(server, wid, cid, "P1")
        p2 = _param(server, wid, cid, "P2")
        p3 = _param(server, wid, cid, "P3")
        r690 = _release(server, wid, cid, "690")
        _values(server, wid, cid, "d0-" + r690, [
            {"parameter_id": p1, "membership": "included", "enabled": True, "value_state": "present", "value": "1"},
            {"parameter_id": p2, "membership": "included", "enabled": True, "value_state": "present", "value": "1"},
            {"parameter_id": p3, "membership": "included", "enabled": True, "value_state": "present", "value": "1"},
        ])
        _freeze(server, wid, cid, r690)
        b1 = _baseline(server, wid, cid, r690, [
            {"parameter_id": p1, "membership": "included", "enabled": True, "value_state": "present", "value": "2"},
            {"parameter_id": p2, "membership": "included", "enabled": True, "value_state": "present", "value": "1"},
            {"parameter_id": p3, "membership": "included", "enabled": True, "value_state": "present", "value": "1"},
        ])
        b2 = _baseline(server, wid, cid, r690, [
            {"parameter_id": p1, "membership": "included", "enabled": True, "value_state": "present", "value": "9"},
            {"parameter_id": p2, "membership": "included", "enabled": True, "value_state": "present", "value": "1"},
            {"parameter_id": p3, "membership": "included", "enabled": True, "value_state": "present", "value": "1"},
        ])
        machine_id, mdraft = _machine(server, wid, cid, r690)
        _seed_machine_values(wid, cid, mdraft, {
            p1: {"membership": "included", "enabled": True, "value_state": "present", "value": "1"},
            p2: {"membership": "included", "enabled": True, "value_state": "present", "value": "1", "intent": "pin"},
            p3: {"membership": "included", "enabled": True, "value_state": "present", "value": "1"},
        })
        mr1 = _publish_machine(server, wid, cid, machine_id)
        r691 = _release(server, wid, cid, "691")
        _values(server, wid, cid, "d0-" + r691, [
            {"parameter_id": p1, "membership": "included", "enabled": True, "value_state": "present", "value": "3"},
            {"parameter_id": p2, "membership": "included", "enabled": True, "value_state": "present", "value": "3"},
            {"parameter_id": p3, "membership": "included", "enabled": True, "value_state": "present", "value": "1"},
        ])

        def do_merge(base, ours, theirs):
            with storage.use_workspace(wid):
                return config_v3_merge.merge_revisions("default", cid, base, ours, theirs)

        head_before = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/branches/main" % (wid, cid)
        )[2]["head_id"]
        main = do_merge(b1, b2, "d0-" + r691)
        assert main["mode"] == "main_cross_version"
        assert main["base"]["revision_id"] == b1 and main["base"]["content_hash"]
        assert main["ours"]["revision_id"] == b2
        assert main["theirs"]["revision_id"] == "d0-" + r691
        assert main["publishable"] is False
        by_id = {item["parameter_id"]: item for item in main["items"]}
        assert by_id[p1]["action"] == "conflict", by_id[p1]
        assert p1 in main["needs_decision"]
        assert by_id[p3]["action"] == "keep"
        assert main["can_apply"] is False

        machine_same = do_merge("d0-" + r690, mr1, "d0-" + r690)
        assert machine_same["mode"] == "machine_same_version", machine_same["ours"]
        assert machine_same["ours"]["kind"] == "machine"
        by_id = {item["parameter_id"]: item for item in machine_same["items"]}
        assert by_id[p1]["action"] == "keep"
        assert by_id[p2]["ours"]["intent"] == "pin" or by_id[p2]["action"] in ("conflict", "manual")

        machine_cross = do_merge("d0-" + r690, mr1, "d0-" + r691)
        assert machine_cross["mode"] == "machine_cross_version"
        by_id = {item["parameter_id"]: item for item in machine_cross["items"]}
        assert by_id[p2]["action"] in ("conflict", "manual"), by_id[p2]

        no_base = do_merge(None, mr1, "d0-" + r691)
        assert no_base["has_common_base"] is False
        assert no_base["can_apply"] is False
        assert any(row["code"] == "base_missing" for row in no_base["blockers"])

        head_after = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/branches/main" % (wid, cid)
        )[2]["head_id"]
        assert head_before == head_after == b2
        machine = as_role(server, "admin").json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/machines" % (wid, cid)
        )[2]["items"][0]
        assert machine["head_id"] == mr1
