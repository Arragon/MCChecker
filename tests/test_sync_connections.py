"""M16-04 workspace connection registry tests."""

import json
import os

import pytest

from app.core.errors import INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError
from app.services import sync_connections, sync_paths


def create(wid="w-1", name="SDA main", origin="https://sda.example.invalid"):
    return sync_connections.create_connection(wid, name, origin)


def test_read_only_does_not_create_sync_directory():
    assert not os.path.exists(sync_paths.sync_root("w-1"))
    assert sync_connections.list_connections("w-1") == []
    assert not os.path.exists(sync_paths.sync_root("w-1"))


def test_workspace_isolation_and_duplicate_name():
    one = create("w-1", "SDA main")
    two = create("w-2", "SDA main")

    assert one["workspace_id"] == "w-1"
    assert two["workspace_id"] == "w-2"
    assert one["connection_id"] != two["connection_id"]
    assert one["policy_revision"] == 1

    assert [item["connection_id"] for item in sync_connections.list_connections("w-1")] == [
        one["connection_id"]
    ]
    assert [item["connection_id"] for item in sync_connections.list_connections("w-2")] == [
        two["connection_id"]
    ]

    with pytest.raises(AppError):
        create("w-1", "sda main")
    with pytest.raises(AppError):
        sync_connections.get_connection("w-2", one["connection_id"])


def test_origin_and_protocol_validation():
    for origin in (
        "http://sda.example.invalid",
        "https://user:pass@sda.example.invalid",
        "https://sda.example.invalid/path",
        "https://sda.example.invalid:8443",
        "https://sda.example.invalid?x=1",
    ):
        with pytest.raises(AppError) as exc:
            create("w-1", "bad", origin)
        assert exc.value.code == INVALID_INPUT

    allowed = create("w-1", "SDA 443", "https://sda.example.invalid:443")
    assert allowed["origin"] == "https://sda.example.invalid:443"

    with pytest.raises(AppError):
        sync_connections.create_connection(
            "w-1", "bad protocol", "https://sda.example.invalid", protocol="sda_v2"
        )


def test_update_cas_disable_and_events():
    created = create()
    cid = created["connection_id"]

    updated = sync_connections.update_connection(
        "w-1", cid, 1, {"name": "SDA renamed", "enabled": False}
    )
    assert updated["name"] == "SDA renamed"
    assert updated["enabled"] is False
    assert updated["policy_revision"] == 2

    with pytest.raises(AppError) as exc:
        sync_connections.update_connection("w-1", cid, 1, {"name": "stale"})
    assert exc.value.code == REVISION_CONFLICT

    with pytest.raises(AppError):
        sync_connections.update_connection("w-1", cid, 2, {"secret": "x"})

    enabled = sync_connections.update_connection("w-1", cid, 2, {"enabled": True})
    assert enabled["enabled"] is True
    disabled = sync_connections.disable_connection("w-1", cid, enabled["policy_revision"])
    assert disabled["enabled"] is False
    assert disabled["policy_revision"] == 4

    events = sync_connections.connection_events("w-1", cid)
    assert [event["event_type"] for event in events] == [
        "configured",
        "disabled",
        "enabled",
        "disabled",
    ]
    assert all(event["connection_id"] == cid for event in events)


def test_delete_is_tombstone_and_keeps_history():
    created = create()
    cid = created["connection_id"]

    deleted = sync_connections.delete_connection("w-1", cid, 1)
    assert deleted["deleted"] is True
    assert deleted["enabled"] is False
    assert deleted["policy_revision"] == 2
    assert sync_connections.list_connections("w-1") == []
    with pytest.raises(AppError) as exc:
        sync_connections.get_connection("w-1", cid)
    assert exc.value.code == NOT_FOUND

    tombstones = sync_connections.list_connections("w-1", include_deleted=True)
    assert len(tombstones) == 1
    assert tombstones[0]["deleted"] is True

    with pytest.raises(AppError):
        create("w-1", "SDA main")


def test_connection_file_has_no_secret_shapes():
    created = create()
    cid = created["connection_id"]
    sync_connections.update_connection("w-1", cid, 1, {"name": "renamed"})
    sync_connections.delete_connection("w-1", cid, 2)

    path = sync_paths.sync_file("w-1", "connections.json")
    with open(path, "r", encoding="utf-8") as handle:
        doc = json.load(handle)

    forbidden = {"password", "passwd", "cookie", "csrf", "authorization", "token", "secret", "har"}

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                assert key.lower() not in forbidden
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            assert "Bearer " not in value
            assert "Basic " not in value
            assert "password=" not in value

    walk(doc)
    assert doc["items"][0]["deleted"] is True
