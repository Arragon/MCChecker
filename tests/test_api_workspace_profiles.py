"""M10-01 机型 CRUD/复制/切换按 Workspace 隔离（INH-2124）。"""

from __future__ import annotations

import json
import os

from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage

XML_W1 = b"<root><value>w1</value></root>"
XML_W2 = b"<root><value>w2</value></root>"


def _ws(server, name):
    status, _h, body = as_role(server, "admin").json(
        "POST", "/api/v3/workspaces", {"name": name}
    )
    assert status == 201, body
    return body["workspace"]["id"]


def _profiles(server, wid):
    status, _h, body = as_role(server, "admin").json(
        "GET", "/api/v3/workspaces/%s/profiles" % wid
    )
    assert status == 200, body
    return body["items"]


def _create(server, wid, name, copy_from=None):
    return as_role(server, "admin").json(
        "POST",
        "/api/v3/workspaces/%s/profiles" % wid,
        {"name": name, "copy_from": copy_from},
    )


def test_profile_crud_is_workspace_scoped():
    seed_roles("prof")
    with make_server() as server:
        w1, w2 = _ws(server, "W1"), _ws(server, "W2")
        status, _h, body = _create(server, w1, "P2")
        assert status == 201, body
        pid = body["profile"]["id"]
        assert pid != "default"
        assert [p["id"] for p in _profiles(server, w1)] == ["default", pid]
        assert [p["id"] for p in _profiles(server, w2)] == ["default"]

        status, _h, body = as_role(server, "admin").json(
            "PATCH", "/api/v3/workspaces/%s/profiles/%s" % (w1, pid), {"name": "P2x"}
        )
        assert status == 200 and body["profile"]["name"] == "P2x", body
        assert all(p["name"] != "P2x" for p in _profiles(server, w2))

        admin = as_role(server, "admin")
        status, _h, body = admin.request(
            "PUT",
            "/api/v1/p/%s/files/probe.xml" % pid,
            body=XML_W1,
            headers={"X-Workspace-Id": w1},
        )
        assert status == 201, body
        status, _h, body = admin.json(
            "DELETE", "/api/v3/workspaces/%s/profiles/%s" % (w1, pid)
        )
        assert status == 200, body
        assert body["deleted"] is True
        assert "_w" + w1 in body["recovery"]
        manifest_path = os.path.join(
            storage.get_recovery_root_dir(), body["recovery"], "manifest.json"
        )
        with open(manifest_path, "rb") as fh:
            manifest = json.loads(fh.read().decode("utf-8"))
        assert manifest["workspace"] == w1
        assert manifest["op"] == "delete_profile"
        moved = os.path.join(
            storage.get_recovery_root_dir(), body["recovery"], "profiles", pid, "configs", "probe.xml"
        )
        assert os.path.isfile(moved), "恢复条目要带上该机型的文件引用"
        assert [p["id"] for p in _profiles(server, w1)] == ["default"]
        assert [p["id"] for p in _profiles(server, w2)] == ["default"]


def test_same_profile_id_in_two_workspaces_is_independent():
    seed_roles("prof")
    with make_server() as server:
        w1, w2 = _ws(server, "W1"), _ws(server, "W2")
        s1, _h, a = _create(server, w1, "DL-200")
        s2, _h, b = _create(server, w2, "DL-200")
        assert s1 == 201 and s2 == 201
        assert a["profile"]["id"] == b["profile"]["id"]
        pid = a["profile"]["id"]
        assert [p["id"] for p in _profiles(server, w1)] == ["default", pid]
        assert [p["id"] for p in _profiles(server, w2)] == ["default", pid]

        admin = as_role(server, "admin")
        s1, _h, body = admin.request(
            "PUT", "/api/v1/p/%s/files/same.xml" % pid, body=XML_W1,
            headers={"X-Workspace-Id": w1},
        )
        assert s1 == 201, body
        s2, _h, body = admin.request(
            "PUT", "/api/v1/p/%s/files/same.xml" % pid, body=XML_W2,
            headers={"X-Workspace-Id": w2},
        )
        assert s2 == 201, body
        r1 = admin.request(
            "GET", "/api/v1/p/%s/files/same.xml/raw" % pid,
            headers={"X-Workspace-Id": w1},
        )
        r2 = admin.request(
            "GET", "/api/v1/p/%s/files/same.xml/raw" % pid,
            headers={"X-Workspace-Id": w2},
        )
        assert r1[0] == 200 and r1[2] == XML_W1
        assert r2[0] == 200 and r2[2] == XML_W2, "同名 pid 在不同 W 必须指向不同数据"


def test_copy_is_same_workspace_only():
    seed_roles("prof")
    with make_server() as server:
        w1, w2 = _ws(server, "W1"), _ws(server, "W2")
        status, _h, body = _create(server, w1, "Source")
        assert status == 201, body
        source_id = body["profile"]["id"]
        admin = as_role(server, "admin")
        status, _h, body = admin.request(
            "PUT", "/api/v1/p/%s/files/probe.xml" % source_id, body=XML_W1,
            headers={"X-Workspace-Id": w1},
        )
        assert status == 201, body
        status, _h, body = _create(server, w1, "Copy", copy_from=source_id)
        assert status == 201, body
        copy_id = body["profile"]["id"]
        assert copy_id != source_id
        status, _h, raw = admin.request(
            "GET", "/api/v1/p/%s/files/probe.xml/raw" % copy_id,
            headers={"X-Workspace-Id": w1},
        )
        assert status == 200 and raw == XML_W1, "复制必须带上当前 W 内的机型数据"
        status, _h, body = _create(server, w2, "Cross", copy_from=source_id)
        assert status == 400, body
        assert body["error"]["detail"]["field"] == "copy_from"


def test_member_cannot_create_rename_or_delete():
    seed_roles("prof")
    with make_server() as server:
        w1 = _ws(server, "W1")
        status, _h, body = _create(server, w1, "P2")
        assert status == 201
        pid = body["profile"]["id"]
        member = as_role(server, "member")
        assert member.json("POST", "/api/v3/workspaces/%s/profiles" % w1, {"name": "X"})[0] == 403
        assert member.json("PATCH", "/api/v3/workspaces/%s/profiles/%s" % (w1, pid), {"name": "Y"})[0] == 403
        assert member.json("DELETE", "/api/v3/workspaces/%s/profiles/%s" % (w1, pid))[0] == 403


def test_legacy_workspace_session_still_lists_v1_profiles():
    pid = seed_roles("legacy")
    with make_server() as server:
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "GET", "/api/v3/session?workspace=default&profile=%s" % pid
        )
        assert status == 200, body
        assert body["profile"]["id"] == pid
        ids = [p["id"] for p in body["profiles"]]
        assert "default" in ids and pid in ids, "legacy 工作区仍要按 v1 机型表列出机型"

        status, _h, body = admin.json(
            "GET", "/api/v3/session?workspace=default&profile=not-a-profile"
        )
        assert status == 200, body
        assert body["profile"]["id"] == "default", "v1 /session 语义：未知机型回落默认"


def test_default_profile_cannot_be_deleted():
    seed_roles("prof")
    with make_server() as server:
        w1 = _ws(server, "W1")
        status, _h, body = as_role(server, "admin").json(
            "DELETE", "/api/v3/workspaces/%s/profiles/default" % w1
        )
        assert status == 400, body
        assert body["error"]["detail"]["field"] == "id"
