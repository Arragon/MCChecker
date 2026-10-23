"""配置审计接口（M7-11 / INH-1970）。"""

from __future__ import annotations

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.core import config_repository as cr, storage
from app.services import config_artifacts, config_gate

from tests.test_config_export import ACTOR, _approve, world  # noqa: F401


@pytest.fixture()
def repo():
    instance = cr.ConfigRepository(storage.DATA_DIR)
    instance.initialize()
    return instance


def _seed(repo, world):  # noqa: F811
    approved = _approve(
        repo,
        world["baseline"],
        values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
    )
    artifact = config_artifacts.generate_artifact(
        repo, ACTOR, profile_id="p1", baseline_id=approved["id"]
    )["item"]
    return approved, artifact


def test_audit_and_history_are_readable_with_filters(monkeypatch, world) -> None:  # noqa: F811
    monkeypatch.setenv(config_gate.ENV_KEY, "on")
    with make_server() as server:
        seed_roles("p1")
        repo = cr.ConfigRepository(storage.DATA_DIR)
        approved, artifact = _seed(repo, world)
        admin = as_role(server, "admin")

        status, _h, body = admin.json("GET", "/api/v1/p/p1/config/audit?limit=5")
        assert status == 200
        assert body["items"] and body["scanned"] >= 1

        status, _h, body = admin.json(
            "GET", "/api/v1/p/p1/config/audit?action=artifact.generate"
        )
        assert status == 200
        assert all(item["action"] == "artifact.generate" for item in body["items"])

        status, _h, body = admin.json(
            "GET", "/api/v1/config/history/%s" % artifact["id"]
        )
        assert status == 200
        assert body["object_kind"] == "artifact"
        assert body["revisions"]
        assert body["provenance"]["related"]["baseline"][0]["id"] == approved["id"]

        status, _h, body = admin.json(
            "GET", "/api/v1/config/history/%s/%s" % (approved["id"], approved["revision"])
        )
        assert status == 200
        assert body["object"]["revision"] == approved["revision"]

        member = as_role(server, "member")
        assert member.json("GET", "/api/v1/config/audit?limit=2")[0] == 200


def test_audit_is_read_only_and_rejects_bad_cursor(monkeypatch, world) -> None:  # noqa: F811
    monkeypatch.setenv(config_gate.ENV_KEY, "on")
    with make_server() as server:
        seed_roles("p1")
        repo = cr.ConfigRepository(storage.DATA_DIR)
        _seed(repo, world)
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "GET", "/api/v1/config/audit?cursor=not-a-cursor"
        )
        assert status == 400
        assert body["error"]["detail"]["field"] == "cursor"

        # 审计检索没有写入口；POST 必须被路由层拒绝
        status, _h, _body = admin.json("POST", "/api/v1/config/audit", body={})
        assert status in (404, 405)


def test_audit_is_gated(monkeypatch) -> None:
    monkeypatch.delenv(config_gate.ENV_KEY, raising=False)
    with make_server() as server:
        seed_roles("p1")
        admin = as_role(server, "admin")
        status, _h, body = admin.json("GET", "/api/v1/p/p1/config/audit")
        assert status == 503
        assert body["error"]["code"] == "CONFIG_DISABLED"
