"""回滚 / 跨变体迁移 / 出口补发接口的 HTTP 行为（M7-06 / INH-1965）。

服务层已经证明"历史不被改写"；这一层钉接口契约：

* 六个入口都在基线下面，计划是读（`config_read`）、落地是写（`config_change_submit`）；
* 三条写入口都返回 **201**——它们新增对象，不是"改"已有对象；
* 缺 `revision` / 缺 `target_variant_id` → 400 落在该字段上；
* 回滚到当前 head → 400；head 变了 → 409 `PLAN_STALE`。
"""

from __future__ import annotations

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.core import config_repository as cr, storage
from app.services import config_baselines as bl, config_catalog, config_gate, config_revision
import upgrade_fixtures as fx

PROFILE = "p1"


@pytest.fixture(autouse=True)
def clean_state():
    config_revision.reset()
    yield
    config_revision.reset()


def _enable(monkeypatch, mode="on"):
    monkeypatch.setenv(config_gate.ENV_KEY, mode)


def _repo():
    return cr.ConfigRepository(storage.DATA_DIR)


def _values(repo, world, pulse, speed="100"):
    return bl.normalize_values(
        repo,
        {
            world["pulse"]["id"]: {"value": pulse, "intent": "override"},
            world["speed"]["id"]: {"value": speed, "intent": "override"},
        },
        definition_set_id=None,
    )


def _history(repo, world):
    """r1 → r2（已批准，900）→ r3（已批准，1000）。"""
    common = fx.approve_common(repo, world, values=_values(repo, world, "900"))
    fx.push(
        repo, common, values=_values(repo, world, "1000"), state=bl.STATE_APPROVED
    )
    return common


def _world():
    repo = _repo()
    return repo, fx.build(repo)


# -- 门禁与权限 -------------------------------------------------------------


def test_restore_requires_gate_and_submit_permission(monkeypatch) -> None:
    _enable(monkeypatch, "off")
    with make_server() as server:
        seed_roles(PROFILE)
        admin = as_role(server, "admin")
        status, _h, body = admin.json("GET", "/api/v1/config/baselines/bl_000001/restore")
        assert status == 503 and body["error"]["code"] == "CONFIG_DISABLED"

    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world = _world()
        common = _history(repo, world)
        member = as_role(server, "member")
        base = "/api/v1/config/baselines/%s" % common["id"]

        assert member.json("GET", base + "/restore?revision=r2")[0] == 200
        status, _h, body = member.json("POST", base + "/restore", body={"revision": "r2"})
        assert status == 403
        assert body["error"]["detail"]["action"] == "config_change_submit"


# -- 回滚 -------------------------------------------------------------------


def test_restore_plan_and_apply(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world = _world()
        common = _history(repo, world)
        admin = as_role(server, "admin")
        base = "/api/v1/config/baselines/%s" % common["id"]

        status, _h, plan = admin.json("GET", base + "/restore?revision=r2")
        assert status == 200, plan
        assert plan["baseline"]["head_revision"] == "r3"
        assert plan["changed"] == 1
        assert plan["publishable"] is False

        status, _h, body = admin.json(
            "POST", base + "/restore", body={"revision": "r2", "reason": "退回去"}
        )
        assert status == 201, body
        restored = body["item"]
        assert restored["id"] != common["id"]
        assert restored["state"] == "approved"
        assert restored["values"][world["pulse"]["id"]]["value"]["text"] == "900"
        assert restored["restore_provenance"]["from_revision"] == "r2"
        assert body["source"]["still_readable"] is True

        # 旧对象、旧修订照旧
        status, _h, old = admin.json("GET", base)
        assert status == 200
        assert old["item"]["revision"] == "r3"


def test_restore_missing_revision_lands_on_the_field(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world = _world()
        common = _history(repo, world)
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "GET", "/api/v1/config/baselines/%s/restore" % common["id"]
        )
        assert status == 400
        assert body["error"]["detail"]["field"] == "revision"


def test_restore_to_head_is_400(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world = _world()
        common = _history(repo, world)
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "POST",
            "/api/v1/config/baselines/%s/restore" % common["id"],
            body={"revision": "r3"},
        )
        assert status == 400
        assert body["error"]["detail"]["revision"] == "r3"


def test_stale_restore_is_409(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world = _world()
        common = _history(repo, world)
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "POST",
            "/api/v1/config/baselines/%s/restore" % common["id"],
            body={"revision": "r2", "expected_head": "r2"},
        )
        assert status == 409
        assert body["error"]["code"] == "PLAN_STALE"


# -- 跨变体迁移 -------------------------------------------------------------


def test_migration_needs_target_variant(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world = _world()
        common = _history(repo, world)
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "GET", "/api/v1/config/baselines/%s/migrate" % common["id"]
        )
        assert status == 400
        assert body["error"]["detail"]["field"] == "target_variant_id"


def test_migration_plan_and_create(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world = _world()
        common = _history(repo, world)
        admin = as_role(server, "admin")
        target = world["new"]["variant"]["id"]

        status, _h, plan = admin.json(
            "GET",
            "/api/v1/config/baselines/%s/migrate?target_variant_id=%s"
            % (common["id"], target),
        )
        assert status == 200, plan
        assert plan["mode"] == "variant_migration"
        assert plan["target"]["variant_id"] == target
        assert plan["blockers"] == []
        assert plan["publishable"] is False

        status, _h, body = admin.json(
            "POST",
            "/api/v1/config/baselines/%s/migrate" % common["id"],
            body={"target_variant_id": target, "reason": "换模板"},
        )
        assert status == 201, body
        created = body["item"]
        assert created["variant_id"] == target
        assert created["release_id"] == world["new"]["release"]["id"]
        assert created["state"] == "draft"


# -- 出口补发 ---------------------------------------------------------------


def test_reissue_plan_reports_batch_and_keeps_reference(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world = _world()
        common = _history(repo, world)
        admin = as_role(server, "admin")
        base = "/api/v1/config/baselines/%s/reissue" % common["id"]

        status, _h, plan = admin.json("GET", base)
        assert status == 200, plan
        assert plan["mode"] == "reissue_refresh"
        assert plan["baseline_reference_unchanged"] is True
        assert plan["template"]["in_current_batch"] is True

        # 同版补发：换一份出口文件，再建刷新草稿
        snapshot = config_catalog.register_snapshot(
            repo,
            fx.ACTOR,
            release_id=world["old"]["release"]["id"],
            logical_file_id=world["logical"]["id"],
            data=fx.TEMPLATE_NEW,
            original_filename="axis_1.0.0_reissue.xml",
        )
        config_catalog.publish_default_batch(
            repo,
            fx.ACTOR,
            release_id=world["old"]["release"]["id"],
            snapshot_ids=[snapshot["id"]],
            note="同版补发",
        )

        status, _h, plan = admin.json("GET", base)
        assert status == 200
        assert plan["template"]["in_current_batch"] is False
        assert plan["changed"] == 2

        status, _h, body = admin.json("POST", base, body={"reason": "跟补发"})
        assert status == 201, body
        created = body["item"]
        assert created["release_id"] == world["old"]["release"]["id"]
        assert created["variant_id"] == world["old"]["variant"]["id"]
        assert created["values"][world["pulse"]["id"]]["value"]["text"] == "700"

        # 原基线仍钉在老模板快照上
        status, _h, old = admin.json("GET", "/api/v1/config/baselines/%s" % common["id"])
        assert old["item"]["revision"] == "r3"
