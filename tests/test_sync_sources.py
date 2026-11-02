"""M16-05 workspace sync source registry tests."""

import json
import os

import pytest

from app.core import fsutil
from app.core.errors import INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError
from app.services import sync_connections, sync_paths, sync_sources, workspace_catalog


def make_workspace(conn_name="SDA main", wid_name="W1"):
    workspace = workspace_catalog.create_workspace(wid_name)
    wid = workspace["id"]
    connection = sync_connections.create_connection(wid, conn_name, "https://sda.example.invalid")
    return wid, connection["connection_id"]


def payload(connection_id, name="MachineConstant.xml", kind="sda_file", **overrides):
    data = {
        "profile_id": "default",
        "name": name,
        "kind": kind,
        "target": {"kind": "file_archive", "file_id": "file-1"},
        "enabled": True,
        "schedule_mode": "inherit",
        "interval_hours": None,
        "connection_id": connection_id if kind.startswith("sda_") else None,
        "resource_ref": "res-1" if kind.startswith("sda_") else None,
        "display_path": "/synthetic/file.xml",
        "record_source": None,
        "keyword": "",
    }
    data.update(overrides)
    return data


def test_create_list_filter_and_pagination():
    wid, cid = make_workspace()
    a = sync_sources.create_source(wid, payload(cid, "A.xml"))
    b = sync_sources.create_source(wid, payload(cid, "B.xml", kind="http_file"))
    c = sync_sources.create_source(wid, payload(cid, "C.xml", kind="sda_table"))

    assert a["workspace_id"] == wid
    assert a["profile_id"] == "default"
    assert a["revision"] == 1
    assert a["target"]["kind"] == "file_archive"

    page1 = sync_sources.list_sources(wid, limit=2)
    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    assert page1["next_cursor"] == "2"
    page2 = sync_sources.list_sources(wid, limit=2, cursor=page1["next_cursor"])
    assert len(page2["items"]) == 1
    assert page2["next_cursor"] is None

    only_http = sync_sources.list_sources(wid, kind="http_file")
    assert [item["source_id"] for item in only_http["items"]] == [b["source_id"]]
    assert sync_sources.list_sources(wid, query="b.")["total"] == 1
    assert sync_sources.list_sources(wid, status="enabled")["total"] == 3

    other_wid, _ = make_workspace(wid_name="W2")
    assert sync_sources.list_sources(other_wid)["total"] == 0
    with pytest.raises(AppError):
        sync_sources.get_source(other_wid, a["source_id"])


def test_read_only_list_does_not_create_sync_dir():
    wid = workspace_catalog.create_workspace("W-readonly")["id"]
    assert not os.path.exists(sync_paths.sync_root(wid))
    assert sync_sources.list_sources(wid)["items"] == []
    assert not os.path.exists(sync_paths.sync_root(wid))


def test_cas_update_delete_and_unknown_fields_preserved():
    wid, cid = make_workspace()
    created = sync_sources.create_source(wid, payload(cid))
    sid = created["source_id"]

    path = sync_paths.sync_file(wid, "sources.json")
    with open(path, "r", encoding="utf-8") as handle:
        doc = json.load(handle)
    doc["items"][0]["legacy_extra"] = {"keep": True}
    fsutil.atomic_write_json(path, doc)

    updated = sync_sources.update_source(wid, sid, 1, {"name": "Renamed.xml", "enabled": False})
    assert updated["name"] == "Renamed.xml"
    assert updated["enabled"] is False
    assert updated["revision"] == 2

    with open(path, "r", encoding="utf-8") as handle:
        after = json.load(handle)
    assert after["items"][0]["legacy_extra"] == {"keep": True}

    with pytest.raises(AppError) as exc:
        sync_sources.update_source(wid, sid, 1, {"name": "Stale.xml"})
    assert exc.value.code == REVISION_CONFLICT

    deleted = sync_sources.delete_source(wid, sid, 2)
    assert deleted["deleted"] is True
    assert deleted["enabled"] is False
    assert deleted["revision"] == 3
    assert sync_sources.list_sources(wid)["total"] == 0
    assert sync_sources.list_sources(wid, status="deleted")["total"] == 1
    assert sync_sources.get_source(wid, sid, include_deleted=True)["deleted"] is True


def test_validation_and_connection_binding():
    wid, cid = make_workspace()

    no_connection = payload(cid)
    no_connection["connection_id"] = None
    with pytest.raises(AppError):
        sync_sources.create_source(wid, no_connection)

    no_resource = payload(cid)
    no_resource["resource_ref"] = None
    with pytest.raises(AppError):
        sync_sources.create_source(wid, no_resource)
    with pytest.raises(AppError):
        sync_sources.create_source(wid, payload(cid, target=None))
    with pytest.raises(AppError):
        sync_sources.create_source(wid, payload(cid, extra="no"))
    with pytest.raises(AppError):
        sync_sources.create_source(wid, payload(cid, profile_id="missing"))

    created = sync_sources.create_source(wid, payload(cid))
    with pytest.raises(AppError):
        sync_sources.create_source(wid, payload(cid))
    with pytest.raises(AppError):
        sync_sources.update_source(wid, created["source_id"], 1, {"profile_id": "default"})

    other_wid, other_cid = make_workspace(wid_name="W2")
    cross = payload(other_cid)
    cross["connection_id"] = cid
    with pytest.raises(AppError) as exc:
        sync_sources.create_source(other_wid, cross)
    assert exc.value.code in (NOT_FOUND, INVALID_INPUT)
