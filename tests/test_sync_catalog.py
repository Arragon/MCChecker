"""M16-32 SDA catalog 读取与来源选择校验。"""

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.core.errors import INVALID_INPUT, AppError
from app.services import sync_catalog, sync_connections, sync_sources, workspace_catalog


def make_workspace():
    return workspace_catalog.create_workspace("W-catalog")["id"]


def make_connection(wid):
    return sync_connections.create_connection(wid, "SDA", "https://sda.invalid")["connection_id"]


ITEMS = [
    {"name": "表 A", "kind": "table", "resource_ref": "table:1", "display_path": "/tables/a"},
    {"name": "文件 A", "kind": "file", "resource_ref": "file:1", "display_path": "/files/a.xml"},
]


def test_catalog_requires_reader_and_validates_source_selection():
    wid = make_workspace()
    cid = make_connection(wid)
    sync_catalog.register_reader(None)
    with pytest.raises(AppError) as exc:
        sync_catalog.read_catalog(wid, cid)
    assert exc.value.code == "DOWNLOAD_FAILED"
    assert exc.value.detail["reason_code"] == "collector_offline"

    sync_catalog.register_reader(lambda workspace_id, connection_id: list(ITEMS))
    try:
        assert [item["resource_ref"] for item in sync_catalog.read_catalog(wid, cid)] == ["table:1", "file:1"]

        good = sync_sources.create_source(
            wid,
            {
                "profile_id": "default",
                "name": "T.xml",
                "kind": "sda_table",
                "target": {"kind": "file_archive", "file_id": "file-table"},
                "connection_id": cid,
                "resource_ref": "table:1",
                "display_path": "/tables/a",
            },
        )
        assert good["resource_ref"] == "table:1"

        with pytest.raises(AppError) as exc:
            sync_sources.update_source(
                wid,
                good["source_id"],
                good["revision"],
                {"display_path": "/tables/renamed"},
            )
        assert exc.value.code == INVALID_INPUT
        assert exc.value.detail["field"] == "resource_ref"

        with pytest.raises(AppError) as exc:
            sync_sources.create_source(
                wid,
                {
                    "profile_id": "default",
                    "name": "Bad.xml",
                    "kind": "sda_file",
                    "target": {"kind": "file_archive", "file_id": "file-x"},
                    "connection_id": cid,
                    "resource_ref": "file:1",
                    "display_path": "/wrong/path.xml",
                },
            )
        assert exc.value.code == INVALID_INPUT
        assert exc.value.detail["field"] == "resource_ref"
    finally:
        sync_catalog.register_reader(None)


def test_catalog_api_is_read_only_and_workspace_scoped():
    seed_roles("sync-catalog")
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        status, _h, body = admin.json("POST", "/api/v3/workspaces", {"name": "W-catalog-api"})
        assert status == 201
        wid = body["workspace"]["id"]
        cid = make_connection(wid)
        sync_catalog.register_reader(lambda workspace_id, connection_id: list(ITEMS))
        try:
            status, _h, listing = visitor.json(
                "GET", "/api/v3/workspaces/%s/connections/%s/catalog" % (wid, cid)
            )
            assert status == 200
            assert [item["kind"] for item in listing["items"]] == ["table", "file"]

            status, _h, missing = visitor.json(
                "GET", "/api/v3/workspaces/%s/connections/conn-missing/catalog" % wid
            )
            assert status == 404
        finally:
            sync_catalog.register_reader(None)
