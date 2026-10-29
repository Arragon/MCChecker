"""M12-07 配置管理端到端闭环（INH-2142）。"""

from __future__ import annotations

from authz_fixtures import as_role, make_server, seed_roles


def test_empty_config_to_b1_and_machine_r1_is_reloadable():
    seed_roles("e2e")
    with make_server() as server:
        admin = as_role(server, "admin")
        status, _h, ws = admin.json("POST", "/api/v3/workspaces", {"name": "E2E"})
        assert status == 201, ws
        wid = ws["workspace"]["id"]
        base = "/api/v3/workspaces/%s/profiles/default/configs" % wid

        status, _h, body = admin.json("POST", base, {"code": "MC-001", "name": "端到端配置"})
        assert status == 201, body
        cid = body["config"]["config_id"]
        status, _h, body = admin.json(
            "POST", base + "/%s/parameters" % cid,
            {"name": "Pulse", "directory": ["G"], "type": "number"},
        )
        assert status == 201, body
        pid = body["parameter"]["parameter_id"]
        status, _h, body = admin.json(
            "POST", base + "/%s/releases" % cid, {"version": "690"}
        )
        assert status == 201 and body["release"]["d0"]["values"] == {}
        rid = body["release"]["release_id"]
        did = "d0-" + rid

        status, _h, body = admin.json(
            "PATCH", base + "/%s/drafts/%s/values" % (cid, did),
            {"request_id": "e2e-v", "items": [
                {"parameter_id": pid, "membership": "included", "enabled": True,
                 "value_state": "present", "value": "1.5"}
            ]}, headers={"If-Match": '"0"'},
        )
        assert status == 200, body
        status, _h, body = admin.json(
            "POST", base + "/%s/drafts/%s/freeze" % (cid, did), {"request_id": "e2e-f"}
        )
        assert status == 200 and body["frozen"] is True, body

        status, _h, body = admin.json(
            "POST", base + "/%s/releases/%s/baseline-drafts" % (cid, rid)
        )
        assert status == 201, body
        bd = body["draft"]["draft_id"]
        status, _h, body = admin.json(
            "POST", base + "/%s/baseline-drafts/%s/publish" % (cid, bd),
            {"request_id": "e2e-b1", "expected_revision": 0},
        )
        assert status == 200 and body["baseline"]["label"] == "B1", body

        status, _h, body = admin.json(
            "POST", base + "/%s/machine-branches" % cid,
            {"machine_no": "MC-001", "release_id": rid, "base_kind": "default"},
        )
        assert status == 201, body
        mid = body["machine"]["machine_id"]
        assert body["machine"]["published"] is False
        status, _h, body = admin.json(
            "POST", base + "/%s/machines/%s/publish" % (cid, mid),
            {"expected_revision": 0},
        )
        assert status == 200 and [r["label"] for r in body["machine"]["revisions"]] == ["r1"], body

        # “刷新页面”后回看：同一个 W/P 重新读详情/分支/机台。
        status, _h, detail = admin.json("GET", base + "/%s" % cid)
        assert status == 200 and detail["config"]["code"] == "MC-001"
        status, _h, branch = admin.json("GET", base + "/%s/branches/main" % cid)
        assert status == 200 and branch["head_id"] and branch["baselines"][0]["label"] == "B1"
        status, _h, machines = admin.json("GET", base + "/%s/machines" % cid)
        assert status == 200 and machines["items"][0]["machine_no"] == "MC-001"
        assert machines["items"][0]["management_only"] is True
