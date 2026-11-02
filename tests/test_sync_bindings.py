"""M16-07 source target binding tests."""

import pytest

from app.core import storage
from app.core.errors import INVALID_INPUT, NOT_FOUND, SOURCE_CHANGED, AppError
from app.core.sync_models import SyncTarget
from app.services import (
    config_v3_catalog,
    file_versions,
    sync_bindings,
    workspace_catalog,
)


def make_workspace(name="W-bind"):
    return workspace_catalog.create_workspace(name)["id"]


def put_file(wid, name="A.xml", content=b"<R><x>1</x></R>"):
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_config_file(name, content)
        rows = file_versions.list_resources("default")
    return next(row for row in rows if row["name"] == name)


def make_config_release(wid, code="C1", version="691"):
    with storage.use_workspace(wid):
        config = config_v3_catalog.create_config(
            "default", {"code": code, "name": "Config " + code}
        )
        release = config_v3_catalog.create_release(
            "default", config["config_id"], {"version": version}
        )
    return config["config_id"], release["release_id"]


def test_file_archive_binding_and_current_change():
    wid = make_workspace()
    row = put_file(wid)
    target = {"kind": "file_archive", "file_id": row["file_id"]}
    binding = sync_bindings.resolve_binding(wid, "default", target)
    assert binding["file_id"] == row["file_id"]
    assert binding["version_id"] == row["version_id"]
    assert binding["blob_sha256"] == row["blob_sha256"]

    assert sync_bindings.assert_binding_current(wid, "default", binding)["file_id"] == row["file_id"]

    put_file(wid, content=b"<R><x>2</x></R>")
    with pytest.raises(AppError) as exc:
        sync_bindings.assert_binding_current(wid, "default", binding)
    assert exc.value.code == SOURCE_CHANGED


def test_missing_file_and_workspace_isolation():
    w1 = make_workspace("W1")
    w2 = make_workspace("W2")
    one = put_file(w1)
    two = put_file(w2)
    assert one["file_id"] != two["file_id"]

    b1 = sync_bindings.resolve_binding(
        w1, "default", {"kind": "file_archive", "file_id": one["file_id"]}
    )
    b2 = sync_bindings.resolve_binding(
        w2, "default", {"kind": "file_archive", "file_id": two["file_id"]}
    )
    assert b1["file_id"] != b2["file_id"]

    with pytest.raises(AppError) as exc:
        sync_bindings.resolve_binding(
            w1, "default", {"kind": "file_archive", "file_id": "f-missing"}
        )
    assert exc.value.code == NOT_FOUND


def test_default_candidate_binding_and_missing_release():
    wid = make_workspace()
    row = put_file(wid)
    cid, rid = make_config_release(wid)
    target = {
        "kind": "default_candidate",
        "file_id": row["file_id"],
        "configuration_id": cid,
        "release_id": rid,
    }
    binding = sync_bindings.resolve_binding(wid, "default", target)
    assert binding["configuration_id"] == cid
    assert binding["release_id"] == rid
    assert binding["release_version"] == "691"
    assert binding["file_id"] == row["file_id"]

    with pytest.raises(AppError) as exc:
        sync_bindings.resolve_binding(
            wid,
            "default",
            {
                "kind": "default_candidate",
                "configuration_id": cid,
                "release_id": "rel-missing",
            },
        )
    assert exc.value.code == NOT_FOUND


def test_machine_observation_requires_device_id():
    wid = make_workspace()
    with pytest.raises(AppError):
        SyncTarget(kind="machine_observation")
    target = SyncTarget(kind="machine_observation", device_id="dev-1")
    binding = sync_bindings.resolve_binding(wid, "default", target)
    assert binding["device_id"] == "dev-1"
    assert binding["profile_id"] == "default"
    assert binding["target"]["kind"] == "machine_observation"


def test_catalog_ref_must_match_display_path():
    wid = make_workspace()
    row = put_file(wid)
    target = {"kind": "file_archive", "file_id": row["file_id"]}

    ok = sync_bindings.resolve_binding(
        wid,
        "default",
        target,
        catalog={"res-1": "/synthetic/A.xml"},
        resource_ref="res-1",
        display_path="/synthetic/A.xml",
    )
    assert ok["file_id"] == row["file_id"]

    with pytest.raises(AppError) as exc:
        sync_bindings.resolve_binding(
            wid,
            "default",
            target,
            catalog={"res-1": "/synthetic/A.xml"},
            resource_ref="res-1",
            display_path="/synthetic/other.xml",
        )
    assert exc.value.code == SOURCE_CHANGED

    with pytest.raises(AppError):
        sync_bindings.resolve_binding(
            wid,
            "default",
            target,
            resource_ref="res-1",
            display_path="",
        )
