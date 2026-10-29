"""M13-03 参数差异与历程（INH-2145）。"""

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


def _param(server, wid, cid, name, unit="s"):
    status, _h, body = as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters" % (wid, cid),
        {"name": name, "directory": ["A"], "type": "number", "unit": unit},
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


def _freeze(server, wid, cid, rid, items):
    did = "d0-" + rid
    assert as_role(server, "admin").json(
        "PATCH",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s/values" % (wid, cid, did),
        {"request_id": "d-" + rid, "items": items}, headers={"If-Match": '"0"'},
    )[0] == 200
    assert as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/default/configs/%s/drafts/%s/freeze" % (wid, cid, did),
        {"request_id": "f-" + rid},
    )[0] == 200


def test_diff_categories_and_parameter_history():
    seed_roles("diff")
    with make_server() as server:
        admin = as_role(server, "admin")
        w1 = _create(server, "W1")
        cid = _config(server, w1)
        p1 = _param(server, w1, cid, "OldName")
        p2 = _param(server, w1, cid, "Gone")

        rid690 = _release(server, w1, cid, "690")
        _freeze(server, w1, cid, rid690, [
            {"parameter_id": p1, "membership": "included", "enabled": True,
             "value_state": "present", "value": "0"},
            {"parameter_id": p2, "membership": "included", "enabled": True,
             "value_state": "present", "value": "5"},
        ])
        bd1 = admin.json(
            "POST",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/releases/%s/baseline-drafts"
            % (w1, cid, rid690),
        )[2]["draft"]["draft_id"]
        b1 = admin.json(
            "POST",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/baseline-drafts/%s/publish"
            % (w1, cid, bd1), {"request_id": "b1", "expected_revision": 0},
        )[2]["baseline"]["baseline_id"]

        # 改名/单位/属性/目录变化 + 新参数；用 691 的 D0 对比 690 D0。
        admin.json(
            "PATCH", "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters/%s"
            % (w1, cid, p1),
            {"name": "NewName", "unit": "ms", "directory": ["B"], "attrs": {"precision": 2}},
        )
        p3 = _param(server, w1, cid, "Added")
        rid691 = _release(server, w1, cid, "691", predecessor=rid690)
        _freeze(server, w1, cid, rid691, [
            {"parameter_id": p1, "membership": "included", "enabled": True, "value_state": "null"},
            {"parameter_id": p2, "membership": "removed"},
            {"parameter_id": p3, "membership": "included", "enabled": True,
             "value_state": "present", "value": "7"},
        ])

        status, _h, out = admin.json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/revisions/d0-%s/diff"
            % (w1, cid, rid691),
        )
        assert status == 200 and out["previous_id"] == "d0-" + rid690, out
        rows = {r["parameter_id"]: r for r in out["rows"]}
        cats = set(rows[p1]["categories"])
        assert {"renamed", "hierarchy_changed", "attributes_changed", "value_changed"} <= cats, cats
        assert rows[p1]["name_before"] == "OldName" and rows[p1]["name_after"] == "NewName"
        assert rows[p1]["after"]["status"] == "null"
        assert "removed" in rows[p2]["categories"]
        assert "added" in rows[p3]["categories"]

        # 显式 against 是 release_pair，不改 previous。
        status, _h, pair = admin.json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/revisions/d0-%s/diff?against=d0-%s"
            % (w1, cid, rid690, rid691),
        )
        assert status == 200 and pair["release_pair"] is True
        status, _h, again = admin.json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/revisions/d0-%s/diff"
            % (w1, cid, rid691),
        )
        assert again["previous_id"] == "d0-" + rid690

        # 参数历程：D0 690 -> B1 -> D0 691（按真实修订，跨页前驱保留）。
        status, _h, hist = admin.json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/parameters/%s/history"
            % (w1, cid, p1),
        )
        assert status == 200, hist
        ids = [row["revision_id"] for row in hist["items"]]
        assert "d0-" + rid690 in ids and b1 in ids and "d0-" + rid691 in ids
        first = [r for r in hist["items"] if r["revision_id"] == "d0-" + rid691][0]
        assert first["previous_id"] == "d0-" + rid690
        assert first["value"]["status"] == "null"
