"""M16-10 preview/read_set tests."""

import json

import pytest

from app.core import fsutil, storage
from app.core.errors import NOT_FOUND, PLAN_STALE, REVISION_CONFLICT, SOURCE_CHANGED, AppError
from app.services import (
    file_versions,
    sync_connections,
    sync_paths,
    sync_preview,
    sync_sources,
    workspace_catalog,
)


def make_fixture():
    wid = workspace_catalog.create_workspace("W-preview")["id"]
    cid = sync_connections.create_connection(wid, "SDA main", "https://sda.example.invalid")[
        "connection_id"
    ]
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("A.xml", b"<R><x>1</x></R>")
        rows = file_versions.list_resources("default")
    file_id = next(row["file_id"] for row in rows if row["name"] == "A.xml")
    return wid, cid, file_id


def make_source(wid, cid, file_id, name, profile="default", enabled=True, mode="inherit", target=True):
    data = {
        "profile_id": profile,
        "name": name,
        "kind": "http_file",
        "target": {"kind": "file_archive", "file_id": file_id} if target else None,
        "enabled": enabled,
        "schedule_mode": mode,
        "interval_hours": None,
        "connection_id": None,
        "resource_ref": None,
        "display_path": "/synthetic/%s" % name,
        "record_source": None,
        "keyword": "",
    }
    if not target:
        data["kind"] = "sda_catalog"
        data["connection_id"] = cid
        data["resource_ref"] = "res-catalog-1"
    return sync_sources.create_source(wid, data)


def test_workspace_all_includes_manual_and_excludes_disabled_deleted():
    wid, cid, file_id = make_fixture()
    manual = make_source(wid, cid, file_id, "manual.xml", mode="manual")
    inherit = make_source(wid, cid, file_id, "inherit.xml")
    disabled = make_source(wid, cid, file_id, "disabled.xml", enabled=False)
    deleted = make_source(wid, cid, file_id, "deleted.xml")
    sync_sources.delete_source(wid, deleted["source_id"], 1)

    preview = sync_preview.create_preview(wid, {"scope": "workspace_all"})
    ids = {item["source_id"] for item in preview["items"]}
    assert ids == {manual["source_id"], inherit["source_id"]}
    assert preview["total"] == 2
    assert all(item["read_set_sha256"] for item in preview["items"])
    assert preview["excluded"] == []


def test_current_profile_selected_and_failures_scopes():
    wid, cid, file_id = make_fixture()
    one = make_source(wid, cid, file_id, "one.xml")
    two = make_source(wid, cid, file_id, "two.xml")
    workspace_catalog.add_profile(wid, "p2")
    p2 = make_source(wid, cid, file_id, "p2.xml", profile="p2", target=False)

    current = sync_preview.create_preview(wid, {"scope": "current_profile", "profile_id": "default"})
    assert {item["source_id"] for item in current["items"]} == {one["source_id"], two["source_id"]}

    all_profiles = sync_preview.create_preview(wid, {"scope": "workspace_all"})
    assert {item["source_id"] for item in all_profiles["items"]} == {
        one["source_id"],
        two["source_id"],
        p2["source_id"],
    }

    selected = sync_preview.create_preview(
        wid, {"scope": "selected_sources", "source_ids": [two["source_id"]]}
    )
    assert [item["source_id"] for item in selected["items"]] == [two["source_id"]]

    retry = sync_preview.create_preview(
        wid,
        {
            "scope": "failures_retry",
            "failed_source_ids": [one["source_id"], "src-missing"],
        },
    )
    assert [item["source_id"] for item in retry["items"]] == [one["source_id"]]


def test_preview_expiry_and_stale_validation():
    wid, cid, file_id = make_fixture()
    source = make_source(wid, cid, file_id, "A.xml")
    preview = sync_preview.create_preview(wid, {"scope": "workspace_all"})

    path = sync_paths.sync_file(wid, "previews.json")
    with open(path, "r", encoding="utf-8") as handle:
        doc = json.load(handle)
    doc["items"][preview["preview_id"]]["expires_at"] = "2020-01-01T00:00:00Z"
    fsutil.atomic_write_json(path, doc)
    with pytest.raises(AppError) as exc:
        sync_preview.get_preview(wid, preview["preview_id"])
    assert exc.value.code == PLAN_STALE

    fresh = sync_preview.create_preview(wid, {"scope": "workspace_all"})
    sync_sources.update_source(wid, source["source_id"], 1, {"name": "renamed.xml"})
    with pytest.raises(AppError) as exc:
        sync_preview.validate_preview(wid, fresh["preview_id"])
    assert exc.value.code == REVISION_CONFLICT


def test_binding_change_invalidates_preview():
    wid, cid, file_id = make_fixture()
    make_source(wid, cid, file_id, "A.xml")
    preview = sync_preview.create_preview(wid, {"scope": "workspace_all"})
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file("A.xml", b"<R><x>2</x></R>")
    with pytest.raises(AppError) as exc:
        sync_preview.validate_preview(wid, preview["preview_id"])
    assert exc.value.code == SOURCE_CHANGED


def test_preview_is_workspace_scoped():
    wid, cid, file_id = make_fixture()
    make_source(wid, cid, file_id, "A.xml")
    preview = sync_preview.create_preview(wid, {"scope": "workspace_all"})
    other = workspace_catalog.create_workspace("W-other")["id"]
    with pytest.raises(AppError) as exc:
        sync_preview.get_preview(other, preview["preview_id"])
    assert exc.value.code == NOT_FOUND
