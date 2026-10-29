"""M13-02 参数总览矩阵（INH-2144）。"""

from __future__ import annotations

from authz_fixtures import as_role, make_server, seed_roles


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


def _param(server, wid, cid, name, type_name="number"):
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
        {"name": name, "directory": ["G"], "type": type_name},
    )
    assert status == 201, body
    return body["parameter"]["parameter_id"]


def _release(server, wid, cid, version, predecessor=None):
    payload = {"version": version}
    if predecessor:
        payload["predecessor_id"] = predecessor
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/releases" % (wid, cid),
        payload,
    )
    assert status == 201, body
    return body["release"]["release_id"]


def _freeze_with(server, wid, cid, rid, items):
    did = "d0-" + rid
    assert as_role(server, "admin").json(
        "PATCH",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s/values" % (wid, cid, did),
        {"request_id": "ov", "items": items}, headers={"If-Match": '"0"'},
    )[0] == 200
    assert as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s/freeze" % (wid, cid, did),
        {"request_id": "ov-f"},
    )[0] == 200


def _overview(server, wid, cid, rid, **query):
    parts = ["%s=%s" % (k, v) for k, v in query.items() if v]
    path = "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/overview" % (wid, cid, rid)
    if parts:
        path += "?" + "&".join(parts)
    return as_role(server, "admin").json("GET", path)


def test_overview_uses_real_snapshots_and_distinguishes_states():
    seed_roles("overview")
    with make_server() as server:
        admin = as_role(server, "admin")
        w1 = _create(server, "W1")
        cid = _config(server, w1)
        p_ok = _param(server, w1, cid, "Ok")
        p_unset = _param(server, w1, cid, "Unset")
        p_removed = _param(server, w1, cid, "Removed")
        p_disabled = _param(server, w1, cid, "Disabled")
        rid690 = _release(server, w1, cid, "690")
        assert _overview(server, w1, cid, rid690)[2]["columns"] == []
        _freeze_with(server, w1, cid, rid690, [
            {"parameter_id": p_ok, "membership": "included", "enabled": True,
             "value_state": "present", "value": "0"},
            {"parameter_id": p_unset, "membership": "included", "enabled": True,
             "value_state": "unset"},
            {"parameter_id": p_removed, "membership": "removed"},
            {"parameter_id": p_disabled, "membership": "included", "enabled": False,
             "value_state": "present", "value": "5"},
        ])
        status, _h, ov = _overview(server, w1, cid, rid690)
        assert status == 200 and [c["kind"] for c in ov["columns"]] == ["default"]
        assert ov["has_previous"] is False
        rows = {r["name"]: r for r in ov["rows"]}
        d0col = ov["columns"][0]["column_id"]
        assert rows["Ok"]["cells"][d0col]["status"] == "ok"
        assert rows["Ok"]["cells"][d0col]["value"] == "0"
        assert rows["Unset"]["cells"][d0col]["status"] == "unset"
        assert rows["Removed"]["cells"][d0col]["status"] == "removed"
        assert rows["Disabled"]["cells"][d0col]["status"] == "disabled"
        assert "value" not in rows["Unset"]["cells"][d0col]

        # B1 与机台 r1 都进入列；机台只有本版发布才显示。
        bd = admin.json(
            "POST",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/baseline-drafts"
            % (w1, cid, rid690),
        )[2]["draft"]["draft_id"]
        bid = admin.json(
            "POST",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/baseline-drafts/%s/publish"
            % (w1, cid, bd), {"request_id": "b1", "expected_revision": 0},
        )[2]["baseline"]["baseline_id"]
        status, _h, body = admin.json(
            "POST",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/machine-branches" % (w1, cid),
            {"machine_no": "MC-001", "release_id": rid690, "base_kind": "default"},
        )
        mid = body["machine"]["machine_id"]
        admin.json(
            "POST",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/machines/%s/publish"
            % (w1, cid, mid), {"expected_revision": 0},
        )
        status, _h, ov2 = _overview(server, w1, cid, rid690, baselines=bid, machines=mid)
        assert [c["kind"] for c in ov2["columns"]] == ["default", "baseline", "machine"]
        mcol = ov2["columns"][2]["column_id"]
        mrows = {r["name"]: r for r in ov2["rows"]}
        assert mrows["Ok"]["cells"][mcol]["value"] == "0"

        # 第二版：D0/Bn 未形成；不能拿 690 的机台 current 冒充。
        rid691 = _release(server, w1, cid, "691", predecessor=rid690)
        status, _h, ov3 = _overview(server, w1, cid, rid691, compare_release_id=rid690, machines=mid)
        assert status == 200
        assert [c["kind"] for c in ov3["columns"]] == ["compare"], ov3["columns"]
        assert ov3["has_previous"] is True
        assert ov3["rows"][0]["cells"].popitem()[1]["revision_id"].startswith("d0-")
        status, _h, no_compare = _overview(server, w1, cid, rid690, compare_release_id="")
        assert "compare" not in [c["kind"] for c in no_compare["columns"]]
