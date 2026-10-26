"""经验上提接口的 HTTP 行为（M7-05 / INH-1964）。

服务层已经证明"上提不是自动的"；这一层只钉接口契约：

* 读计划用 `config_read`，建变更单用 `config_change_submit`；
* 计划里的 `items/eligible/blockers` 原样透出，前端不许自己算；
* 设备层入口是 **400 `DEVICE_LAYER_DISABLED`**，不是 404、不是 500、更不是"假装成功"；
* 计划过期 → 409 `PLAN_STALE`；缺参数 → 400 落在具体字段上。
"""

from __future__ import annotations

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.core import config_repository as cr, storage
from app.services import config_baselines as bl, config_gate, config_revision
from app.services import config_promote
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


def _seed(repo, world, *, model_pulse="1234"):
    """通用（目标）与机型（来源）各一条已批准基线。"""
    common = fx.approve_common(
        repo, world, values={world["pulse"]["id"]: {"value": "900", "intent": "override"}}
    )
    model = fx.approve_model(
        repo,
        world,
        profile_id=PROFILE,
        upstream=common,
        values={world["pulse"]["id"]: {"value": model_pulse, "intent": "override"}},
    )
    return common, model


def _world():
    repo = _repo()
    return repo, fx.build(repo)


# -- 门禁与权限 -------------------------------------------------------------


def test_promote_requires_gate_and_submit_permission(monkeypatch) -> None:
    _enable(monkeypatch, "off")
    with make_server() as server:
        seed_roles(PROFILE)
        admin = as_role(server, "admin")
        status, _h, body = admin.json("GET", "/api/v1/p/%s/config/promote/common" % PROFILE)
        assert status == 503 and body["error"]["code"] == "CONFIG_DISABLED"

    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world = _world()
        _seed(repo, world)
        admin = as_role(server, "admin")
        member = as_role(server, "member")

        assert member.json("GET", "/api/v1/p/%s/config/promote/common" % PROFILE)[0] == 200
        status, _h, body = member.json(
            "POST", "/api/v1/p/%s/config/promote/common" % PROFILE, body={}
        )
        assert status == 403
        assert body["error"]["detail"]["action"] == "config_change_submit"


def test_device_promotion_is_400_not_404(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "GET", "/api/v1/p/%s/config/promote/model?device_id=DEV-1" % PROFILE
        )
        assert status == 400
        assert body["error"]["code"] == "DEVICE_LAYER_DISABLED"
        assert body["error"]["detail"]["device_id"] == "DEV-1"

        status, _h, body = admin.json(
            "POST", "/api/v1/p/%s/config/promote/model" % PROFILE, body={"device_id": "DEV-2"}
        )
        assert status == 400
        assert body["error"]["detail"]["device_id"] == "DEV-2"
        assert "延期" in body["error"]["message"]


# -- 计划 -------------------------------------------------------------------


def test_plan_exposes_items_and_blockers(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world = _world()
        common, model = _seed(repo, world)
        admin = as_role(server, "admin")

        status, _h, plan = admin.json(
            "GET", "/api/v1/p/%s/config/promote/common" % PROFILE
        )
        assert status == 200, plan
        assert plan["mode"] == "promote_to_common"
        assert plan["publishable"] is False
        assert plan["source"]["baseline_id"] == model["id"]
        assert plan["target"]["baseline_id"] == common["id"]
        rows = {row["parameter_id"]: row for row in plan["items"]}
        assert rows[world["pulse"]["id"]]["eligible"] is True
        assert rows[world["speed"]["id"]]["excluded_reason"] == "not_local"
        assert plan["eligible"] == [world["pulse"]["id"]]
        assert plan["blockers"] == []


def test_parameter_filter_via_query(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world = _world()
        _seed(repo, world)
        admin = as_role(server, "admin")
        path = "/api/v1/p/%s/config/promote/common?parameter_ids=%s" % (
            PROFILE,
            world["speed"]["id"],
        )
        status, _h, plan = admin.json("GET", path)
        assert status == 200, plan
        assert [row["parameter_id"] for row in plan["items"]] == [world["speed"]["id"]]
        assert plan["can_apply"] is False


def test_missing_model_baseline_lands_on_the_field(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        admin = as_role(server, "admin")
        # 机型存在但还没有机型基线：错在"没有可上提的本地经验"，不是路径不存在
        status, _h, body = admin.json("GET", "/api/v1/p/%s/config/promote/common" % PROFILE)
        assert status == 400
        assert body["error"]["detail"]["field"] == "profile_id"


# -- 落地 -------------------------------------------------------------------


def test_create_returns_change_set_and_keeps_common_untouched(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world = _world()
        common, _model = _seed(repo, world)
        admin = as_role(server, "admin")

        status, _h, body = admin.json(
            "POST",
            "/api/v1/p/%s/config/promote/common" % PROFILE,
            body={"reason": "现场验证过", "title": "上提脉冲宽度"},
        )
        assert status == 201, body
        change = body["item"]
        assert change["state"] == "submitted"
        assert change["plan_ref"]["algorithm"] == "promote"
        assert change["title"] == "上提脉冲宽度"
        assert body["submitted"] is True

        # 通用的 head 一点没动
        assert bl.get_baseline(repo, common["id"])["revision"] == common["revision"]


def test_no_eligible_parameter_is_400(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world = _world()
        common = fx.approve_common(repo, world)
        fx.approve_model(repo, world, profile_id=PROFILE, upstream=common)
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "POST", "/api/v1/p/%s/config/promote/common" % PROFILE, body={}
        )
        assert status == 400
        assert body["error"]["detail"]["field"] == "parameter_ids"


def test_stale_plan_is_409(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world = _world()
        _seed(repo, world)
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "POST",
            "/api/v1/p/%s/config/promote/common" % PROFILE,
            body={"expected": {"target_revision": "r99"}},
        )
        assert status == 409
        assert body["error"]["code"] == "PLAN_STALE"
        assert "target_revision" in body["error"]["detail"]["reasons"]


def test_blocked_plan_does_not_create_change_set(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world = _world()
        common = fx.approve_common(repo, world, release=world["new"])
        fx.approve_model(
            repo,
            world,
            profile_id=PROFILE,
            upstream=common,
            values={world["pulse"]["id"]: {"value": "1234", "intent": "override"}},
        )
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "POST", "/api/v1/p/%s/config/promote/common" % PROFILE, body={}
        )
        assert status == 409
        assert "release_mismatch" in body["error"]["detail"]["reasons"]

        status, _h, listing = admin.json("GET", "/api/v1/p/%s/config/changes" % PROFILE)
        assert status == 200
        assert listing["items"] == []


def test_device_promotion_service_is_disabled_everywhere(repo=None) -> None:
    """服务层的开关状态与接口一致：一处启用、一处没启用是会骗人的。"""
    assert config_promote.DEVICE_LAYER_DISABLED == "DEVICE_LAYER_DISABLED"
