"""M16-08 workspace sync policy tests."""

import pytest

from app.core.errors import INVALID_INPUT, REVISION_CONFLICT, AppError
from app.services import sync_connections, sync_policy, sync_sources, workspace_catalog


def make_workspace():
    wid = workspace_catalog.create_workspace("W-policy")["id"]
    cid = sync_connections.create_connection(wid, "SDA main", "https://sda.example.invalid")[
        "connection_id"
    ]
    return wid, cid


def make_source(wid, name, mode="inherit", interval=None, enabled=True):
    return sync_sources.create_source(
        wid,
        {
            "profile_id": "default",
            "name": name,
            "kind": "http_file",
            "target": {"kind": "file_archive", "file_id": "file-1"},
            "enabled": enabled,
            "schedule_mode": mode,
            "interval_hours": interval,
            "connection_id": None,
            "resource_ref": None,
            "display_path": "/synthetic/%s" % name,
            "record_source": None,
            "keyword": "",
        },
    )


def test_default_policy_and_cas():
    wid, _ = make_workspace()
    policy = sync_policy.get_policy(wid)
    assert policy["revision"] == 0
    assert policy["enabled"] is False
    assert policy["default_interval_hours"] == 6
    assert policy["profile_overrides"] == {}

    updated = sync_policy.save_policy(
        wid,
        0,
        {
            "enabled": True,
            "default_interval_hours": 12,
            "anchor": "2026-11-02T00:00:00Z",
            "profile_overrides": {"default": {"enabled": True, "interval_hours": 24}},
        },
    )
    assert updated["revision"] == 1
    assert updated["enabled"] is True
    assert updated["default_interval_hours"] == 12
    assert updated["profile_overrides"]["default"]["interval_hours"] == 24

    with pytest.raises(AppError) as exc:
        sync_policy.save_policy(wid, 0, {"enabled": False})
    assert exc.value.code == REVISION_CONFLICT

    with pytest.raises(AppError):
        sync_policy.save_policy(wid, 1, {"default_interval_hours": 0})
    with pytest.raises(AppError):
        sync_policy.save_policy(wid, 1, {"secret": "x"})
    with pytest.raises(AppError):
        sync_policy.save_policy(wid, 1, {"profile_overrides": {"bad/profile": {}}})
    with pytest.raises(AppError):
        sync_policy.save_policy(
            wid,
            1,
            {"profile_overrides": {"default": {"interval_hours": 5000}}},
        )


def test_resolve_interval_three_modes_and_global_pause():
    wid, _ = make_workspace()
    inherit = make_source(wid, "inherit.xml")
    custom = make_source(wid, "custom.xml", mode="custom", interval=48)
    manual = make_source(wid, "manual.xml", mode="manual")
    disabled = make_source(wid, "disabled.xml", enabled=False)

    policy = {"enabled": True, "default_interval_hours": 12}
    assert sync_policy.resolve_interval(policy, inherit) == 12
    assert sync_policy.resolve_interval(policy, custom) == 48
    assert sync_policy.resolve_interval(policy, manual) is None
    assert sync_policy.resolve_interval(policy, disabled) is None

    paused = {"enabled": False, "default_interval_hours": 12}
    assert sync_policy.resolve_interval(paused, inherit) is None
    assert sync_policy.resolve_interval(paused, custom) is None
    assert sync_policy.resolve_interval(paused, manual) is None


def test_batch_source_policy_is_all_or_nothing():
    wid, _ = make_workspace()
    a = make_source(wid, "A.xml")
    b = make_source(wid, "B.xml")

    updated = sync_policy.batch_update_source_schedule(
        wid,
        [
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
        ],
    )
    assert [item["revision"] for item in updated] == [2, 2]
    assert updated[0]["schedule_mode"] == "custom"
    assert updated[0]["interval_hours"] == 48
    assert updated[1]["schedule_mode"] == "manual"

    with pytest.raises(AppError) as exc:
        sync_policy.batch_update_source_schedule(
            wid,
            [
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
            ],
        )
    assert exc.value.code == REVISION_CONFLICT

    assert sync_sources.get_source(wid, a["source_id"])["revision"] == 2
    assert sync_sources.get_source(wid, b["source_id"])["revision"] == 2
