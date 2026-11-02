"""M16-08 workspace sync policy API tests."""

from authz_fixtures import as_role, make_server, seed_roles

from app.services import sync_sources


def make_workspace(caller, name):
    status, _headers, body = caller.json("POST", "/api/v3/workspaces", {"name": name})
    assert status == 201, body
    return body["workspace"]["id"]


def make_source(wid, name):
    return sync_sources.create_source(
        wid,
        {
            "profile_id": "default",
            "name": name,
            "kind": "http_file",
            "target": {"kind": "file_archive", "file_id": "file-1"},
            "enabled": True,
            "schedule_mode": "inherit",
            "interval_hours": None,
            "connection_id": None,
            "resource_ref": None,
            "display_path": "/synthetic/%s" % name,
            "record_source": None,
            "keyword": "",
        },
    )


def test_policy_get_put_and_permissions():
    seed_roles("sync-policy")
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        wid = make_workspace(admin, "W1")

        status, _headers, policy = visitor.json(
            "GET", "/api/v3/workspaces/%s/sync/policy" % wid
        )
        assert status == 200, policy
        assert policy["enabled"] is False
        assert policy["revision"] == 0
        assert policy["default_interval_hours"] == 6

        status, _headers, updated = admin.json(
            "PUT",
            "/api/v3/workspaces/%s/sync/policy" % wid,
            {"enabled": True, "default_interval_hours": 12},
            headers={"If-Match": "0"},
        )
        assert status == 200, updated
        assert updated["revision"] == 1
        assert updated["enabled"] is True
        assert updated["default_interval_hours"] == 12

        status, _headers, stale = admin.json(
            "PUT",
            "/api/v3/workspaces/%s/sync/policy" % wid,
            {"enabled": False},
            headers={"If-Match": "0"},
        )
        assert status == 409, stale

        status, _headers, missing = admin.json(
            "PUT",
            "/api/v3/workspaces/%s/sync/policy" % wid,
            {"enabled": False},
        )
        assert status == 409, missing

        status, _headers, denied = visitor.json(
            "PUT",
            "/api/v3/workspaces/%s/sync/policy" % wid,
            {"enabled": False},
            headers={"If-Match": "1"},
        )
        assert status == 403, denied


def test_batch_source_policy_all_or_nothing():
    seed_roles("sync-policy-batch")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "W1")
        a = make_source(wid, "A.xml")
        b = make_source(wid, "B.xml")

        status, _headers, updated = admin.json(
            "POST",
            "/api/v3/workspaces/%s/sync/source-policy-batches" % wid,
            {
                "updates": [
                    {
                        "source_id": a["source_id"],
                        "expected_revision": 1,
                        "schedule_mode": "custom",
                        "interval_hours": 48,
                    },
                    {
                        "source_id": b["source_id"],
                        "expected_revision": 1,
                        "schedule_mode": "manual",
                        "interval_hours": None,
                    },
                ]
            },
        )
        assert status == 200, updated
        assert [item["revision"] for item in updated["items"]] == [2, 2]

        status, _headers, stale = admin.json(
            "POST",
            "/api/v3/workspaces/%s/sync/source-policy-batches" % wid,
            {
                "updates": [
                    {
                        "source_id": a["source_id"],
                        "expected_revision": 1,
                        "schedule_mode": "inherit",
                        "interval_hours": None,
                    },
                    {
                        "source_id": b["source_id"],
                        "expected_revision": 2,
                        "schedule_mode": "custom",
                        "interval_hours": 24,
                    },
                ]
            },
        )
        assert status == 409, stale

        status, _headers, listing = admin.json(
            "GET", "/api/v3/workspaces/%s/sync/sources" % wid
        )
        assert status == 200, listing
        assert {item["source_id"]: item["revision"] for item in listing["items"]} == {
            a["source_id"]: 2,
            b["source_id"]: 2,
        }
