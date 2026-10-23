"""配置/机型生命周期（M7-12 / INH-1971）。"""

from __future__ import annotations

import os

import pytest

from app.core import config_repository as cr, fsutil, storage
from app.core.errors import LIFECYCLE_BLOCKED, AppError
from app.services import (
    config_artifacts,
    config_delivery,
    config_lifecycle,
    config_policies,
)

from tests.test_config_export import ACTOR, _approve, world  # noqa: F401


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


def _publish(repo, world):  # noqa: F811
    from app.core import device_models

    if not device_models.exists("p1"):
        device_models.add_model("p1")
    approved = _approve(
        repo,
        world["baseline"],
        values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
    )
    artifact = config_artifacts.generate_artifact(
        repo, ACTOR, profile_id="p1", baseline_id=approved["id"]
    )["item"]
    config_delivery.register(
        repo, ACTOR, artifact_id=artifact["id"], result="declared_ok",
        profile_id="p1", executed_at="2026-10-22T09:00:00Z",
        evidence={"ref": "photo://1"},
    )
    return approved, artifact


def _write_profile_file(name: str, content: bytes) -> None:
    with storage.use_profile("p1"):
        os.makedirs(storage.get_configs_dir(), exist_ok=True)
        fsutil.atomic_write_bytes(storage.get_config_path(name), content)


def test_retire_keeps_history_and_blocks_writes(repo, world) -> None:  # noqa: F811
    approved, artifact = _publish(repo, world)
    item = config_lifecycle.retire_profile(
        repo, ACTOR, pid="p1", reason="测试退役"
    )
    assert item["status"] == "retired"
    assert os.path.isdir(storage.get_profile_data_dir("p1"))
    assert repo.get("baseline", approved["id"]) is not None
    assert repo.get("artifact", artifact["id"]) is not None
    with pytest.raises(AppError) as caught:
        config_lifecycle.assert_writable("p1", "POST", "/api/v1/p/p1/config/tracking")
    assert caught.value.code == LIFECYCLE_BLOCKED
    config_lifecycle.assert_writable("p1", "GET", "/api/v1/p/p1/files")
    config_lifecycle.assert_writable("p1", "POST", "/api/v1/profiles/p1/restore")
    restored = config_lifecycle.restore_profile(repo, ACTOR, pid="p1")
    assert restored["status"] == "active"
    assert not device_retired("p1")


def device_retired(pid: str) -> bool:
    from app.core import device_models

    return device_models.is_retired(pid)


def test_delete_refuses_when_config_refs_exist(repo, world) -> None:  # noqa: F811
    _publish(repo, world)
    with pytest.raises(AppError) as caught:
        config_lifecycle.assert_deletable(repo, "p1")
    assert caught.value.code == LIFECYCLE_BLOCKED
    assert caught.value.detail["references"].get("delivery")


def test_policy_reopen_requires_drift_acknowledgement(repo, world) -> None:  # noqa: F811
    name = world["logical_file"]["display_name"] if "logical_file" in world else "轴参数.xml"
    _write_profile_file(name, b"<R/>")
    first = config_policies.set_policy(
        repo, ACTOR, scope="model", profile_id="p1", mode="managed", reason="开启"
    )
    assert first["mode"] == "managed"
    disabled = config_policies.set_policy(
        repo, ACTOR, scope="model", profile_id="p1", mode="archive_only", reason="暂停"
    )
    assert disabled["disabled_snapshot"]

    _write_profile_file(name, b"<R changed='1'/>")
    with pytest.raises(AppError) as caught:
        config_policies.set_policy(
            repo, ACTOR, scope="model", profile_id="p1", mode="managed", reason="重开"
        )
    assert caught.value.code == LIFECYCLE_BLOCKED
    assert caught.value.detail["drift"][0]["state"] == "changed"

    reopened = config_policies.set_policy(
        repo, ACTOR, scope="model", profile_id="p1", mode="managed", reason="确认重开",
        acknowledge_drift=True,
    )
    assert reopened["mode"] == "managed"
    assert reopened["drift"][0]["state"] == "changed"
    assert reopened["drift_acknowledged_at"]


def test_family_retire_requires_acknowledgement(repo, world) -> None:  # noqa: F811
    with pytest.raises(AppError) as caught:
        config_lifecycle.retire_family(
            repo, ACTOR, family_id=world["family"]["id"], reason="测试"
        )
    assert caught.value.code == LIFECYCLE_BLOCKED
    retired = config_lifecycle.retire_family(
        repo, ACTOR, family_id=world["family"]["id"], reason="测试",
        acknowledge_refs=True,
    )
    assert retired["status"] == "retired"
    restored = config_lifecycle.restore_family(
        repo, ACTOR, family_id=world["family"]["id"]
    )
    assert restored["status"] == "active"
