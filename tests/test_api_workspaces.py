"""M9-02 workspace API 测试（INH-2119）。"""

from __future__ import annotations

from authz_fixtures import as_role, make_server, seed_roles


def test_admin_creates_workspace_and_context_keeps_identity():
    seed_roles("wtest")
    with make_server() as server:
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "POST", "/api/v3/workspaces", {"name": "W2", "description": "第二工作区"}
        )
        assert status == 201, body
        wid = body["workspace"]["id"]
        status, _h, ctx = admin.json("GET", "/api/v3/workspaces/%s/context" % wid)
        assert status == 200, ctx
        assert ctx["workspace"]["id"] == wid
        assert ctx["profile"]["id"] == "default"
        assert ctx["profiles"] == [{"id": "default", "name": "默认机型"}]
        v1_status, _h, v1 = admin.json("GET", "/api/v1/session")
        assert v1_status == 200
        assert ctx["identity"] == v1["identity"]


def test_member_and_visitor_cannot_create_or_rename():
    seed_roles("wtest")
    with make_server() as server:
        admin = as_role(server, "admin")
        status, _h, body = admin.json("POST", "/api/v3/workspaces", {"name": "W2"})
        assert status == 201
        wid = body["workspace"]["id"]
        for role in ("member", "visitor"):
            caller = as_role(server, role)
            status, _h, body = caller.json("POST", "/api/v3/workspaces", {"name": "W3"})
            assert status == 403, (role, body)
            status, _h, body = caller.json(
                "PATCH", "/api/v3/workspaces/%s" % wid, {"name": "W2-x"}
            )
            assert status == 403, (role, body)


def test_duplicate_name_is_409_with_field():
    seed_roles("wtest")
    with make_server() as server:
        admin = as_role(server, "admin")
        assert admin.json("POST", "/api/v3/workspaces", {"name": "W2"})[0] == 201
        status, _h, body = admin.json("POST", "/api/v3/workspaces", {"name": "W2"})
        assert status == 409, body
        assert body["error"]["detail"]["field"] == "name"


def test_unknown_workspace_is_404_without_fallback():
    seed_roles("wtest")
    with make_server() as server:
        admin = as_role(server, "admin")
        status, _h, body = admin.json("GET", "/api/v3/session?workspace=w-deadbeef")
        assert status == 404, body
        status, _h, body = admin.json("GET", "/api/v3/workspaces/w-deadbeef/context")
        assert status == 404, body


def test_v1_session_payload_keys_unchanged():
    seed_roles("wtest")
    with make_server() as server:
        status, _h, body = as_role(server, "visitor").json("GET", "/api/v1/session")
        assert status == 200
        assert {"identity", "profile", "profiles", "server", "limits"} <= set(body)
