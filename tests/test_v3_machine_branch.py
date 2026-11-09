"""M12-05 Machine 实例与 r1..rn 连续分支（INH-2140）。"""

from __future__ import annotations

from authz_fixtures import as_role, make_server, seed_roles


def test_machine_workbench_edits_cas_and_reads_immutable_revision():
    seed_roles("machine-workbench")
    with make_server() as server:
        wid = _create(server, "Workbench")
        cid = _config(server, wid)
        pid = _param(server, wid, cid)
        rid = _release(server, wid, cid)
        _freeze_d0(server, wid, cid, pid, rid)
        status, _, result = _machine(server, wid, cid, rid)
        assert status == 201
        mid = result["machine"]["machine_id"]
        base = "/api/v3/workspaces/%s/profiles/default/configs/%s" % (wid, cid)
        path = base + "/machines/%s/draft/values" % mid
        payload = {"request_id": "edit-1", "items": [{"parameter_id": pid,
            "membership": "included", "value_state": "present", "value": "7", "enabled": True}]}
        assert as_role(server, "visitor").json("PATCH", path, payload, headers={"If-Match": '"0"'})[0] == 403
        admin = as_role(server, "admin")
        status, _, result = admin.json("PATCH", path, payload, headers={"If-Match": '"0"'})
        assert status == 200, result
        assert result["draft"]["revision"] == 1
        assert result["draft"]["values"][pid]["value"] == "7"
        assert admin.json("PATCH", path, payload, headers={"If-Match": '"0"'})[0] == 200
        assert admin.json("PATCH", path, dict(payload, request_id="stale"), headers={"If-Match": '"0"'})[0] == 409
        status, _, published = _publish(server, wid, cid, mid, '"1"')
        assert status == 200, published
        revision = published["machine"]["head_id"]
        status, _, snapshot = admin.json("GET", base + "/revisions/" + revision)
        assert status == 200, snapshot
        assert snapshot["snapshot"]["values"][pid]["value"] == "7"
        assert admin.json("PATCH", path, dict(payload, request_id="after-publish"), headers={"If-Match": '"1"'})[0] == 409
        assert admin.json("GET", base + "/revisions/mr-absent")[0] == 404
        other = _create(server, "Other")
        assert admin.json("GET", base.replace(wid, other) + "/revisions/" + revision)[0] == 404


def test_machine_pin_roundtrip_and_inherit_uses_server_basis():
    seed_roles("machine-intent")
    with make_server() as server:
        wid = _create(server, "Intent")
        cid = _config(server, wid)
        pid = _param(server, wid, cid)
        rid = _release(server, wid, cid)
        _freeze_d0(server, wid, cid, pid, rid)
        machine = _machine(server, wid, cid, rid)[2]["machine"]
        path = "/api/v3/workspaces/%s/profiles/default/configs/%s/machines/%s" % (wid, cid, machine["machine_id"])
        admin = as_role(server, "admin")
        payload = {"request_id": "pin", "expected_revision": 0, "items": [{"parameter_id": pid, "intent": "pin", "value_state": "present", "value": "7"}]}
        status, _, result = admin.json("PATCH", path + "/draft/values", payload)
        assert status == 200 and result["draft"]["values"][pid]["intent"] == "pin"
        assert admin.json("GET", path)[2]["machine"]["draft"]["values"][pid]["intent"] == "pin"
        bad = {"request_id": "bad", "expected_revision": 1, "items": [{"parameter_id": pid, "intent": "unknown"}]}
        assert admin.json("PATCH", path + "/draft/values", bad)[0] == 400
        assert admin.json("GET", path)[2]["machine"]["draft"]["revision"] == 1
        inherit = {"request_id": "inherit", "expected_revision": 1, "items": [{"parameter_id": pid, "intent": "inherit", "value": "999"}]}
        status, _, result = admin.json("PATCH", path + "/draft/values", inherit)
        assert status == 200, result
        assert result["draft"]["values"][pid]["value"] == "1"
        assert result["draft"]["values"][pid]["intent"] == "inherit"


def _create(server, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces", {"name": name}
    )
    assert status == 201, body
    return body["workspace"]["id"]


def _config(server, wid):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces/%s/profiles/default/configs" % wid,
        {"code": "MC-001", "name": "配置"},
    )
    assert status == 201, body
    return body["config"]["config_id"]


def _param(server, wid, cid):
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
        {"name": "Pulse", "directory": ["G"], "type": "number"},
    )
    assert status == 201, body
    return body["parameter"]["parameter_id"]


def _release(server, wid, cid):
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/releases" % (wid, cid),
        {"version": "690"},
    )
    assert status == 201, body
    return body["release"]["release_id"]


def _freeze_d0(server, wid, cid, pid, rid):
    did = "d0-" + rid
    assert as_role(server, "admin").json(
        "PATCH",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s/values" % (wid, cid, did),
        {"request_id": "v", "items": [
            {"parameter_id": pid, "membership": "included", "enabled": True,
             "value_state": "present", "value": "1"}
        ]}, headers={"If-Match": '"0"'},
    )[0] == 200
    assert as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s/freeze" % (wid, cid, did),
        {"request_id": "f"},
    )[0] == 200


def _machine(server, wid, cid, release_id, no="MC-01", kind="default", base=""):
    return as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/machine-branches" % (wid, cid),
        {"machine_no": no, "release_id": release_id, "base_kind": kind,
         "base_revision_id": base},
    )


def _publish(server, wid, cid, mid, etag='"0"'):
    return as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/machines/%s/publish"
        % (wid, cid, mid),
        {}, headers={"If-Match": etag},
    )


def test_machine_starts_unpublished_and_r_numbers_never_restart():
    seed_roles("machine")
    with make_server() as server:
        admin = as_role(server, "admin")
        w1 = _create(server, "W1")
        cid = _config(server, w1)
        pid = _param(server, w1, cid)
        rid = _release(server, w1, cid)
        assert _machine(server, w1, cid, rid)[0] == 400, "D0 未冻结不能建机台"
        _freeze_d0(server, w1, cid, pid, rid)

        status, _h, body = _machine(server, w1, cid, rid)
        assert status == 201, body
        machine = body["machine"]
        assert machine["published"] is False and machine["head_id"] is None
        assert machine["revisions"] == [], "初建不得显示已发布"
        mid = machine["machine_id"]
        assert _machine(server, w1, cid, rid)[0] == 400, "机台编号唯一"

        status, _h, first = _publish(server, w1, cid, mid)
        assert status == 200, first
        assert first["machine"]["published"] is True
        assert [r["label"] for r in first["machine"]["revisions"]] == ["r1"]
        r1 = first["machine"]["head_id"]

        # 从 B1 新建机台草稿，发布后连续 r2，父是 r1。
        status, _h, bd = as_role(server, "admin").json(
            "POST",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/baseline-drafts"
            % (w1, cid, rid),
        )
        assert status == 201
        bdid = bd["draft"]["draft_id"]
        status, _h, b1 = as_role(server, "admin").json(
            "POST",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/baseline-drafts/%s/publish"
            % (w1, cid, bdid), {"request_id": "b1"}, headers={"If-Match": '"0"'},
        )
        assert status == 200, b1
        b1id = b1["baseline"]["baseline_id"]

        status, _h, md = as_role(server, "admin").json(
            "POST",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/machines/%s/drafts"
            % (w1, cid, mid),
            {"release_id": rid, "base_kind": "baseline", "base_revision_id": b1id},
        )
        assert status == 201, md
        status, _h, second = _publish(server, w1, cid, mid)
        assert status == 200, second
        labels = [r["label"] for r in second["machine"]["revisions"]]
        assert labels == ["r1", "r2"], labels
        r2 = [r for r in second["machine"]["revisions"] if r["label"] == "r2"][0]
        assert r2["parent_id"] == r1

        # 再发布 B2 不改变机台 head。
        status, _h, bd2 = as_role(server, "admin").json(
            "POST",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/baseline-drafts"
            % (w1, cid, rid),
        )
        assert status == 201
        assert as_role(server, "admin").json(
            "POST",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/baseline-drafts/%s/publish"
            % (w1, cid, bd2["draft"]["draft_id"]), {"request_id": "b2"},
            headers={"If-Match": '"0"'},
        )[0] == 200
        status, _h, current = admin.json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s/machines/%s"
            % (w1, cid, mid)
        )
        assert status == 200 and current["machine"]["head_id"] == r2["revision_id"]
        assert [r["label"] for r in current["machine"]["revisions"]] == ["r1", "r2"]
        assert current["machine"]["management_only"] is True
