"""配置/机型生命周期接口（M7-12 / INH-1971）。"""

from __future__ import annotations

import os

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.core import config_repository as cr, fsutil, storage
from app.services import config_artifacts, config_delivery, config_gate

from tests.test_config_export import ACTOR, _approve, world  # noqa: F401


@pytest.fixture()
def repo():
    instance = cr.ConfigRepository(storage.DATA_DIR)
    instance.initialize()
    return instance


def _publish(repo, world):  # noqa: F811
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


def test_retire_blocks_writes_but_keeps_reads(monkeypatch) -> None:
    monkeypatch.setenv(config_gate.ENV_KEY, "on")
    with make_server() as server:
        seed_roles("p1")
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "POST", "/api/v1/profiles/p1/retire", body={"reason": "测试退役"}
        )
        assert status == 200, body
        assert body["profile"]["status"] == "retired"

        assert admin.json("GET", "/api/v1/p/p1/files")[0] == 200
        status, _h, body = admin.json(
            "POST", "/api/v1/config/tracking",
            body={"scope": "model", "profile_id": "p1", "mode": "managed", "reason": "x"},
        )
        assert status == 409
        assert body["error"]["code"] == "LIFECYCLE_BLOCKED"

        status, _h, body = admin.json("POST", "/api/v1/profiles/p1/restore")
        assert status == 200
        assert body["profile"]["status"] == "active"
        assert not body["profile"].get("retired_at")


def test_delete_refuses_managed_history(monkeypatch, world) -> None:  # noqa: F811
    monkeypatch.setenv(config_gate.ENV_KEY, "on")
    with make_server() as server:
        seed_roles("p1")
        repo = cr.ConfigRepository(storage.DATA_DIR)
        _publish(repo, world)
        admin = as_role(server, "admin")
        status, _h, body = admin.json("DELETE", "/api/v1/profiles/p1")
        assert status == 409
        assert body["error"]["code"] == "LIFECYCLE_BLOCKED"
        assert body["error"]["detail"]["hint"].find("退役") >= 0
        # 历史对象仍可读
        assert repo.get("delivery", body["error"]["detail"]["references"]["delivery"][0])


def test_family_retire_and_reactivate(monkeypatch, world) -> None:  # noqa: F811
    monkeypatch.setenv(config_gate.ENV_KEY, "on")
    with make_server() as server:
        seed_roles("p1")
        admin = as_role(server, "admin")
        fid = world["family"]["id"]
        status, _h, body = admin.json(
            "POST", "/api/v1/config/families/%s/retire" % fid,
            body={"reason": "测试", "acknowledge_refs": True},
        )
        assert status == 200, body
        assert body["item"]["status"] == "retired"
        status, _h, body = admin.json(
            "POST", "/api/v1/config/families/%s/reactivate" % fid, body={}
        )
        assert status == 200
        assert body["item"]["status"] == "active"


def test_policy_reopen_drift_through_api(monkeypatch, world) -> None:  # noqa: F811
    monkeypatch.setenv(config_gate.ENV_KEY, "on")
    with make_server() as server:
        seed_roles("p1")
        repo = cr.ConfigRepository(storage.DATA_DIR)
        # world fixture 已经建好；确保 p1 有同名文件
        name = world["xml_file"]["display_name"]
        with storage.use_profile("p1"):
            os.makedirs(storage.get_configs_dir(), exist_ok=True)
            fsutil.atomic_write_bytes(storage.get_config_path(name), b"<R/>")
        admin = as_role(server, "admin")

        def policy(mode, extra=None):
            body = {
                "scope": "model", "profile_id": "p1", "mode": mode, "reason": "测试",
            }
            body.update(extra or {})
            return admin.json("POST", "/api/v1/config/tracking", body=body)

        assert policy("managed")[0] == 201
        assert policy("archive_only")[0] == 201
        with storage.use_profile("p1"):
            fsutil.atomic_write_bytes(storage.get_config_path(name), b"<R changed='1'/>")
        status, _h, body = policy("managed")
        assert status == 409
        assert body["error"]["code"] == "LIFECYCLE_BLOCKED"
        assert body["error"]["detail"]["drift"][0]["state"] == "changed"
        status, _h, body = policy("managed", {"acknowledge_drift": True})
        assert status == 201
        assert body["item"]["drift_acknowledged_at"]
