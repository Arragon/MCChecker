"""M16-10 preview API tests."""

from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage
from app.services import file_versions, sync_connections, sync_sources


def make_workspace(caller, name):
    status, _headers, body = caller.json("POST", "/api/v3/workspaces", {"name": name})
    assert status == 201, body
    return body["workspace"]["id"]


def make_source(wid):
    cid = sync_connections.create_connection(wid, "SDA main", "https://sda.example.invalid")[
        "connection_id"
    ]
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("A.xml", b"<R><x>1</x></R>")
        rows = file_versions.list_resources("default")
    file_id = next(row["file_id"] for row in rows if row["name"] == "A.xml")
    return cid, sync_sources.create_source(
        wid,
        {
            "profile_id": "default",
            "name": "A.xml",
            "kind": "http_file",
            "target": {"kind": "file_archive", "file_id": file_id},
            "enabled": True,
            "schedule_mode": "inherit",
            "interval_hours": None,
            "connection_id": None,
            "resource_ref": None,
            "display_path": "/synthetic/A.xml",
            "record_source": None,
            "keyword": "",
        },
    )


def test_preview_create_get_validate_and_permissions():
    seed_roles("sync-preview")
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        wid = make_workspace(admin, "W1")
        _cid, source = make_source(wid)

        status, _headers, preview = admin.json(
            "POST",
            "/api/v3/workspaces/%s/sync/previews" % wid,
            {"scope": "workspace_all"},
        )
        assert status == 201, preview
        assert preview["total"] == 1
        assert preview["items"][0]["source_id"] == source["source_id"]
        assert preview["items"][0]["read_set_sha256"]
        preview_id = preview["preview_id"]

        status, _headers, fetched = visitor.json(
            "GET",
            "/api/v3/workspaces/%s/sync/previews/%s" % (wid, preview_id),
        )
        assert status == 200, fetched
        assert fetched["preview_id"] == preview_id

        status, _headers, validated = admin.json(
            "POST",
            "/api/v3/workspaces/%s/sync/previews/%s/validate" % (wid, preview_id),
        )
        assert status == 200, validated
        assert validated["total"] == 1

        status, _headers, denied = visitor.json(
            "POST",
            "/api/v3/workspaces/%s/sync/previews" % wid,
            {"scope": "workspace_all"},
        )
        assert status == 403, denied
