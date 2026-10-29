"""M12-01 FixedConfig/SoftwareRelease 版本图（INH-2136）。"""

from __future__ import annotations

from authz_fixtures import as_role, make_server, seed_roles


def _create(server, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces", {"name": name}
    )
    assert status == 201, body
    return body["workspace"]["id"]


def _config(server, wid, pid, code, name="配置"):
    return as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/%s/configs" % (wid, pid),
        {"code": code, "name": name},
    )


def _release(server, wid, pid, cid, version, predecessor=None, order=None):
    payload = {"version": version}
    if predecessor:
        payload["predecessor_id"] = predecessor
    if order is not None:
        payload["order"] = order
    return as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles/%s/configs/%s/releases" % (wid, pid, cid),
        payload,
    )


def test_code_and_version_are_unique_within_workspace_profile_only():
    seed_roles("release")
    with make_server() as server:
        admin = as_role(server, "admin")
        w1 = _create(server, "W1")
        w2 = _create(server, "W2")
        second = admin.json(
            "POST", "/api/v3/workspaces/%s/profiles" % w1, {"name": "Second"}
        )[2]["profile"]["id"]

        status, _h, body = _config(server, w1, "default", "MC-001", "一号配置")
        assert status == 201 and body["config"]["code"] == "MC-001", body
        cid = body["config"]["config_id"]
        assert _config(server, w1, "default", "MC-001")[0] == 409
        assert _config(server, w1, second, "MC-001")[0] == 201, "跨 profile 不串"
        assert _config(server, w2, "default", "MC-001")[0] == 201, "跨 W 不串"

        first = _release(server, w1, "default", cid, "690")
        assert first[0] == 201, first
        rel1 = first[2]["release"]
        assert rel1["predecessor_id"] is None
        assert rel1["d0"] == {"values": {}, "frozen": False, "revision": 0}
        assert _release(server, w1, "default", cid, "690")[0] == 409

        status, _h, graph = admin.json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/graph" % (w1, cid),
        )
        assert status == 200 and len(graph["nodes"]) == 1 and graph["edges"] == [], (
            "第一版无前驱时不得画伪连线"
        )

        second_rel = _release(server, w1, "default", cid, "691", rel1["release_id"])
        assert second_rel[0] == 201, second_rel
        rel2 = second_rel[2]["release"]
        assert rel2["predecessor_id"] == rel1["release_id"]
        assert rel2["d0"]["values"] == {}, "新版本 D0 不得继承旧版实际值"
        status, _h, graph = admin.json(
            "GET",
            "/api/v3/workspaces/%s/profiles/default/configs/%s/graph" % (w1, cid),
        )
        assert status == 200
        assert {(e["from"], e["to"]) for e in graph["edges"]} == {
            (rel1["release_id"], rel2["release_id"])
        }

        status, _h, detail = admin.json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s" % (w1, cid)
        )
        assert status == 200 and [r["version"] for r in detail["config"]["releases"]] == [
            "690",
            "691",
        ]

        # 跨配置的前驱、重复 order 都拒绝。
        other = _config(server, w1, "default", "MC-002")[2]["config"]["config_id"]
        assert _release(server, w1, "default", other, "x", rel1["release_id"])[0] == 400
        assert _release(server, w1, "default", cid, "692", order=2)[0] == 409
