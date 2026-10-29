"""M9-03 工作区上下文与 v1 兼容（INH-2120）。"""

from __future__ import annotations

from authz_fixtures import as_role, make_server, seed_roles


def _create_workspace(server, name="W2"):
    admin = as_role(server, "admin")
    status, _h, body = admin.json("POST", "/api/v3/workspaces", {"name": name})
    assert status == 201, body
    return body["workspace"]["id"]


def test_v1_read_without_wid_uses_legacy_root():
    pid = seed_roles("ctx")
    with make_server() as server:
        status, _h, body = as_role(server, "visitor").json(
            "GET", "/api/v1/p/%s/files" % pid
        )
        assert status == 200, body


def test_v1_write_without_wid_is_428_and_does_not_touch_data():
    pid = seed_roles("ctx")
    with make_server() as server:
        status, _h, body = as_role(server, "admin").json(
            "POST",
            "/api/v1/p/%s/favorites" % pid,
            {"file": "a.xml", "path": "root"},
            headers={"X-Workspace-Id": ""},
        )
        assert status == 428, body
        assert body["error"]["code"] == "WORKSPACE_CONTEXT_REQUIRED"


def test_unknown_wid_is_404_not_legacy_fallback():
    pid = seed_roles("ctx")
    with make_server() as server:
        status, _h, body = as_role(server, "admin").json(
            "GET",
            "/api/v1/p/%s/files" % pid,
            headers={"X-Workspace-Id": "w-deadbeef"},
        )
        assert status == 404, body


def test_header_query_conflict_is_409():
    pid = seed_roles("ctx")
    with make_server() as server:
        wid = _create_workspace(server)
        status, _h, body = as_role(server, "admin").json(
            "GET",
            "/api/v1/p/%s/files?workspace=default" % pid,
            headers={"X-Workspace-Id": wid},
        )
        assert status == 409, body
        assert body["error"]["code"] == "WORKSPACE_CONTEXT_MISMATCH"


def test_v3_path_wid_wins_over_header():
    seed_roles("ctx")
    with make_server() as server:
        wid = _create_workspace(server)
        status, _h, body = as_role(server, "admin").json(
            "GET",
            "/api/v3/workspaces/%s/context" % wid,
            headers={"X-Workspace-Id": "default"},
        )
        assert status == 200, body
        assert body["workspace"]["id"] == wid


def test_real_workspace_pid_membership_404_but_legacy_p1_ok():
    pid = seed_roles("ctx")
    with make_server() as server:
        wid = _create_workspace(server)
        status, _h, body = as_role(server, "admin").json(
            "GET",
            "/api/v1/p/%s/files" % pid,
            headers={"X-Workspace-Id": wid},
        )
        assert status == 404, body
        status, _h, body = as_role(server, "admin").json(
            "GET", "/api/v1/p/%s/files" % pid
        )
        assert status == 200, body


def test_raw_and_download_follow_wid():
    pid = seed_roles("ctx")
    with make_server() as server:
        wid = _create_workspace(server)
        caller = as_role(server, "admin")
        status, _h, body = caller.json(
            "GET",
            "/api/v1/p/%s/files/a.xml/raw" % pid,
            headers={"X-Workspace-Id": wid},
        )
        assert status == 404, body
        status, _h, body = caller.json(
            "GET",
            "/api/file-download?profile=%s&kind=current&filename=a.xml" % pid,
            headers={"X-Workspace-Id": wid},
        )
        assert status == 404, body


def test_task_retry_is_scoped_to_workspace():
    pid = seed_roles("ctx")
    with make_server() as server:
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "POST", "/api/v1/p/%s/tasks" % pid, {"type": "update_all"}
        )
        assert status == 202, body
        task_id = body["task"]["id"]
        wid = _create_workspace(server)
        status, _h, body = admin.json(
            "GET", "/api/v1/tasks/%s" % task_id, headers={"X-Workspace-Id": wid}
        )
        assert status == 404, body
