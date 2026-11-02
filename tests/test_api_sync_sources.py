"""M16-05 workspace sync source API tests."""

from authz_fixtures import as_role, make_server, seed_roles

from app.services import sync_connections


def make_workspace(caller, name):
    status, _headers, body = caller.json("POST", "/api/v3/workspaces", {"name": name})
    assert status == 201, body
    return body["workspace"]["id"]


def make_connection(wid, name="SDA main"):
    return sync_connections.create_connection(wid, name, "https://sda.example.invalid")[
        "connection_id"
    ]


def source_payload(connection_id, name="A.xml"):
    return {
        "profile_id": "default",
        "name": name,
        "kind": "sda_file",
        "target": {"kind": "file_archive", "file_id": "file-1"},
        "enabled": True,
        "schedule_mode": "inherit",
        "interval_hours": None,
        "connection_id": connection_id,
        "resource_ref": "res-1",
        "display_path": "/synthetic/A.xml",
        "record_source": None,
        "keyword": "",
    }


def test_source_crud_cas_and_workspace_isolation():
    seed_roles("sync-sources")
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        w1 = make_workspace(admin, "W1")
        w2 = make_workspace(admin, "W2")
        cid = make_connection(w1)

        status, _headers, created = admin.json(
            "POST",
            "/api/v3/workspaces/%s/sync/sources" % w1,
            source_payload(cid),
        )
        assert status == 201, created
        sid = created["source_id"]
        assert created["revision"] == 1
        assert created["workspace_id"] == w1

        status, _headers, listing = visitor.json(
            "GET", "/api/v3/workspaces/%s/sync/sources" % w1
        )
        assert status == 200, listing
        assert listing["total"] == 1
        assert listing["items"][0]["source_id"] == sid

        status, _headers, other = visitor.json(
            "GET", "/api/v3/workspaces/%s/sync/sources" % w2
        )
        assert status == 200, other
        assert other["total"] == 0

        status, _headers, updated = admin.json(
            "PATCH",
            "/api/v3/workspaces/%s/sync/sources/%s" % (w1, sid),
            {"name": "B.xml"},
            headers={"If-Match": "1"},
        )
        assert status == 200, updated
        assert updated["revision"] == 2

        status, _headers, stale = admin.json(
            "PATCH",
            "/api/v3/workspaces/%s/sync/sources/%s" % (w1, sid),
            {"name": "Stale.xml"},
            headers={"If-Match": "1"},
        )
        assert status == 409, stale

        status, _headers, missing = admin.json(
            "PATCH",
            "/api/v3/workspaces/%s/sync/sources/%s" % (w1, sid),
            {"name": "Missing.xml"},
        )
        assert status == 409, missing

        status, _headers, other_w = admin.json(
            "GET", "/api/v3/workspaces/%s/sync/sources/%s" % (w2, sid)
        )
        assert status == 404, other_w

        status, _headers, denied = visitor.json(
            "POST",
            "/api/v3/workspaces/%s/sync/sources" % w1,
            source_payload(cid, "Visitor.xml"),
        )
        assert status == 403, denied

        status, _headers, deleted = admin.json(
            "DELETE",
            "/api/v3/workspaces/%s/sync/sources/%s" % (w1, sid),
            headers={"If-Match": "2"},
        )
        assert status == 200, deleted
        assert deleted["deleted"] is True

        status, _headers, empty = visitor.json(
            "GET", "/api/v3/workspaces/%s/sync/sources" % w1
        )
        assert status == 200
        assert empty["total"] == 0


def test_source_pagination_and_filters():
    seed_roles("sync-sources-page")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "W1")
        cid = make_connection(wid)
        for name in ("A.xml", "B.xml", "C.xml"):
            status, _headers, body = admin.json(
                "POST",
                "/api/v3/workspaces/%s/sync/sources" % wid,
                source_payload(cid, name),
            )
            assert status == 201, body

        status, _headers, page = admin.json(
            "GET",
            "/api/v3/workspaces/%s/sync/sources?limit=2" % wid,
        )
        assert status == 200, page
        assert page["total"] == 3
        assert len(page["items"]) == 2
        assert page["next_cursor"] == "2"

        status, _headers, rest = admin.json(
            "GET",
            "/api/v3/workspaces/%s/sync/sources?limit=2&cursor=2" % wid,
        )
        assert status == 200, rest
        assert len(rest["items"]) == 1

        status, _headers, filtered = admin.json(
            "GET",
            "/api/v3/workspaces/%s/sync/sources?q=b." % wid,
        )
        assert status == 200, filtered
        assert filtered["total"] == 1
        assert filtered["items"][0]["name"] == "B.xml"
