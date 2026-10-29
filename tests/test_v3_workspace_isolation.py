"""M9-06 全局工作区安全/兼容首轮门禁（INH-2123）。

覆盖 Q1-A/C/D、A13 与文件/下载/task 隔离；Q1-B 的固定配置/参数 W+P
验收在 M12-01/02/03 完成后恢复为真实用例。
"""

from __future__ import annotations

import json

from authz_fixtures import as_role, make_server, seed_roles

XML_A = b"<R><same>1</same></R>"
XML_B = b"<R><same>2</same></R>"


def _create(server, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces", {"name": name}
    )
    assert status == 201, body
    return body["workspace"]["id"]


def _put(server, wid, name="same.xml", data=XML_A):
    return as_role(server, "admin").request(
        "PUT",
        "/api/v1/p/default/files/%s" % name,
        body=data,
        headers={"X-Workspace-Id": wid},
    )


def _raw(server, wid, name="same.xml"):
    return as_role(server, "admin").request(
        "GET",
        "/api/v1/p/default/files/%s/raw" % name,
        headers={"X-Workspace-Id": wid},
    )


def test_two_workspaces_same_default_and_filename_are_isolated():
    seed_roles("gate")
    with make_server() as server:
        w1, w2 = _create(server, "W1"), _create(server, "W2")
        s1, _h, body = _put(server, w1, data=XML_A)
        assert s1 == 201, body
        s2, _h, body = _put(server, w2, data=XML_B)
        assert s2 == 201, body
        r1 = _raw(server, w1)
        r2 = _raw(server, w2)
        assert r1[0] == 200 and r2[0] == 200
        assert r1[2] == XML_A
        assert r2[2] == XML_B
        assert r1[2] != r2[2]


def test_switch_workspace_keeps_identity_and_capabilities():
    seed_roles("gate")
    with make_server() as server:
        w1, w2 = _create(server, "W1"), _create(server, "W2")
        admin = as_role(server, "admin")
        s1, _h, a = admin.json("GET", "/api/v3/workspaces/%s/context" % w1)
        s2, _h, b = admin.json("GET", "/api/v3/workspaces/%s/context" % w2)
        assert s1 == 200 and s2 == 200
        assert a["identity"] == b["identity"]
        assert a["identity"]["capabilities"] == b["identity"]["capabilities"]
        assert a["workspace"]["id"] != b["workspace"]["id"]


def test_member_cannot_create_or_rename_workspace():
    seed_roles("gate")
    with make_server() as server:
        wid = _create(server, "W1")
        member = as_role(server, "member")
        assert member.json("POST", "/api/v3/workspaces", {"name": "W2"})[0] == 403
        assert member.json("PATCH", "/api/v3/workspaces/%s" % wid, {"name": "W1x"})[0] == 403


def test_cross_workspace_enumeration_and_download_are_404():
    pid = seed_roles("gate")
    with make_server() as server:
        wid = _create(server, "W1")
        caller = as_role(server, "admin")
        status, _h, body = caller.json(
            "GET", "/api/v1/p/%s/files" % pid, headers={"X-Workspace-Id": wid}
        )
        assert status == 404, body
        status, _h, body = caller.json(
            "GET",
            "/api/file-download?profile=%s&kind=current&filename=a.xml" % pid,
            headers={"X-Workspace-Id": wid},
        )
        assert status == 404, body
        status, _h, body = caller.json(
            "GET", "/api/v1/p/default/files", headers={"X-Workspace-Id": "w-deadbeef"}
        )
        assert status == 404, body


def test_task_retry_is_scoped_to_workspace():
    pid = seed_roles("gate")
    with make_server() as server:
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "POST", "/api/v1/p/%s/tasks" % pid, {"type": "update_all"}
        )
        assert status == 202, body
        task_id = body["task"]["id"]
        wid = _create(server, "W1")
        status, _h, body = admin.json(
            "GET", "/api/v1/tasks/%s" % task_id, headers={"X-Workspace-Id": wid}
        )
        assert status == 404, body


def test_q1_b_fixed_config_and_parameter_are_wp_scoped():
    seed_roles("q1b")
    with make_server() as server:
        admin = as_role(server, "admin")
        w1, w2 = _create(server, "Q1 W1"), _create(server, "Q1 W2")
        ids = {}
        for wid, value in ((w1, "1.25"), (w2, "9.5")):
            base = "/api/v3/workspaces/%s/profiles/default/configs" % wid
            status, _h, body = admin.json(
                "POST", base, {"code": "MC-001", "name": "同名配置"}
            )
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
            rid = body["release"]["release_id"]
            did = "d0-" + rid
            status, _h, body = admin.json(
                "PATCH", base + "/%s/drafts/%s/values" % (cid, did),
                {"request_id": "q1b", "items": [
                    {"parameter_id": pid, "membership": "included", "enabled": True,
                     "value_state": "present", "value": value}
                ]}, headers={"If-Match": '"0"'},
            )
            assert status == 200, body
            status, _h, body = admin.json(
                "POST", base + "/%s/drafts/%s/freeze" % (cid, did),
                {"request_id": "q1b-f"},
            )
            assert status == 200, body
            ids[wid] = (cid, pid)

        c1, p1 = ids[w1]
        c2, p2 = ids[w2]
        assert c1 != c2 and p1 != p2, "同名配置/参数在 W1/W2 必须是不同 ID"
        detail1 = admin.json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s" % (w1, c1)
        )[2]
        detail2 = admin.json(
            "GET", "/api/v3/workspaces/%s/profiles/default/configs/%s" % (w2, c2)
        )[2]
        assert detail1["config"]["releases"][0]["d0"]["frozen"] is True
        assert detail2["config"]["releases"][0]["d0"]["frozen"] is True
        assert detail1["config"]["releases"][0]["d0"]["values"][p1]["value"] == "1.25"
        assert detail2["config"]["releases"][0]["d0"]["values"][p2]["value"] == "9.5"
