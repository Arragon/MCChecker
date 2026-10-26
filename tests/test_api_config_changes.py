"""变更单与批准发布的 HTTP 行为（M6-11 / M6-12）。

钉住五条容易退化的性质：

* 提交面（`config_change_submit`）与批准面（`config_change_approve`）是**两条**
  可分别授予的路径，部署者能提交但批不了；
* 特性门禁关闭时一律 503，且**先授权后门禁**（未授权身份拿 403 而不是 503）；
* 路径里的 `pid` 与对象上的 `profile_id` 不一致按不存在处理（404），
  不能靠改 URL 操作别的机型；
* 草稿改完不提交，基线一个字都不动；批准之后基线才有新修订；
* 同键同体重放、同键异体 409。
"""

from __future__ import annotations

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.services import config_gate, config_revision

XML_DOC = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<R><PulseWidth unit="ms">650</PulseWidth><MaxSpeed unit="mm/s">100</MaxSpeed></R>'
).encode("utf-8")

OCTET = {"Content-Type": "application/octet-stream"}


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.delenv(config_gate.ENV_KEY, raising=False)
    config_revision.reset()
    yield
    config_revision.reset()


def _enable(monkeypatch, mode="on"):
    monkeypatch.setenv(config_gate.ENV_KEY, mode)


def _seed(caller):
    """版本 + 类别 + 两个参数 + 定义集合 + 默认批次 + 一条通用基线草稿。"""
    release = caller.json(
        "POST", "/api/v1/config/releases", body={"version_label": "1.0.0", "build": "20260801"}
    )[2]["item"]
    family = caller.json(
        "POST", "/api/v1/config/families", body={"name": "轴参数", "code": "AXIS_PARAM"}
    )[2]["item"]
    logical = caller.json(
        "POST",
        "/api/v1/config/families/%s/files" % family["id"],
        body={"role": "xml_export", "display_name": "轴参数.xml"},
    )[2]["item"]
    pulse = caller.json(
        "POST",
        "/api/v1/config/parameters",
        body={"family_id": family["id"], "name": "脉冲宽度", "category": "时序"},
    )[2]["item"]
    pulse_def = caller.json(
        "POST",
        "/api/v1/config/parameters/%s/definitions" % pulse["id"],
        body={
            "xml_name": "PulseWidth",
            "xml_loc": {"path": [{"name": "PulseWidth", "occurrence": 1}], "attribute": None},
            "type": "decimal",
            "unit": "ms",
            "constraints": {"min": "100", "max": "1000"},
            "reason": "首次登记",
        },
    )[2]["item"]
    snapshot = caller.json(
        "POST",
        "/api/v1/config/snapshots?release_id=%s&logical_file_id=%s&source=upload"
        % (release["id"], logical["id"]),
        body=XML_DOC,
        headers={**OCTET, "X-Filename": "axis_param.xml"},
    )[2]["item"]
    variant = caller.json(
        "POST",
        "/api/v1/config/variants",
        body={
            "family_id": family["id"],
            "release_id": release["id"],
            "template_snapshot_id": snapshot["id"],
        },
    )[2]["item"]
    caller.json(
        "POST",
        "/api/v1/config/releases/%s/default" % release["id"],
        body={"snapshot_ids": [snapshot["id"]]},
    )
    definition_set = caller.json(
        "POST",
        "/api/v1/config/definition-sets",
        body={
            "release_id": release["id"],
            "definition_ids": [pulse_def["id"]],
            "variant_id": variant["id"],
        },
    )[2]["item"]
    baseline = caller.json(
        "POST",
        "/api/v1/config/baselines",
        body={
            "scope": "common",
            "release_id": release["id"],
            "definition_set_id": definition_set["id"],
            "values": {pulse["id"]: {"value": "650"}},
            "reason": "初始通用基线",
        },
    )[2]["item"]
    return {
        "release": release,
        "family": family,
        "logical": logical,
        "pulse": pulse,
        "snapshot": snapshot,
        "variant": variant,
        "definition_set": definition_set,
        "baseline": baseline,
    }


def _create(caller, world, **overrides):
    payload = {
        "baseline_id": world["baseline"]["id"],
        "changes": [{"parameter_id": world["pulse"]["id"], "to": "700", "reason": "产线要求"}],
        "title": "改脉冲宽度",
        "reason": "产线要求",
    }
    payload.update(overrides)
    return caller.json("POST", "/api/v1/p/p1/config/changes", body=payload)


# -- 门禁与权限 -------------------------------------------------------------


def test_changes_require_gate_and_split_permissions(monkeypatch) -> None:
    _enable(monkeypatch, "off")
    with make_server() as server:
        seed_roles("p1")
        admin = as_role(server, "admin")
        status, _h, body = admin.json("GET", "/api/v1/p/p1/config/changes")
        assert status == 503 and body["error"]["code"] == "CONFIG_DISABLED"

    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        admin = as_role(server, "admin")
        world = _seed(admin)
        change = _create(admin, world)[2]["item"]

        member = as_role(server, "member")
        assert member.json("GET", "/api/v1/p/p1/config/changes")[0] == 200, "读要放行"

        # 提交面与批准面查的是**两个不同的能力键**（403 的 detail.action 就是证据）。
        # 决策 D4 取消部署者身份后 `admin`/`deployer` 同义，所以这里用普通成员来验：
        # 两条路径各自 deny，且各自指出自己该有的键。
        status, _h, body = member.json(
            "POST",
            "/api/v1/p/p1/config/changes/%s/submit" % change["id"],
            body={"reason": "报批"},
        )
        assert status == 403
        assert body["error"]["detail"]["action"] == "config_change_submit"

        status, _h, body = member.json(
            "POST",
            "/api/v1/p/p1/config/changes/%s/approve" % change["id"],
            body={"note": "我自己批"},
        )
        assert status == 403
        assert body["error"]["detail"]["action"] == "config_change_approve"

        status, _h, body = member.json(
            "POST",
            "/api/v1/p/p1/config/changes/%s/reject" % change["id"],
            body={"note": "驳回"},
        )
        assert status == 403
        assert body["error"]["detail"]["action"] == "config_change_approve"


def test_feature_off_does_not_leak_gate_to_anon(monkeypatch) -> None:
    """未授权身份先吃 403：门禁档位不向未授权方泄露。"""
    with make_server() as server:
        seed_roles("p1")
        visitor = as_role(server, "visitor")
        status, _h, body = visitor.json("POST", "/api/v1/p/p1/config/changes", body={})
        assert status == 403
        assert body["error"]["detail"]["action"] == "config_change_submit"


# -- 主流程 -----------------------------------------------------------------


def test_full_flow_draft_to_published_baseline(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        baseline_id = world["baseline"]["id"]

        status, _h, body = _create(caller, world)
        assert status == 201, body
        change = body["item"]
        assert change["state"] == "draft"
        assert change["changes"][0]["from"]["text"] == "650"

        # 草稿阶段基线不动
        assert caller.json("GET", "/api/v1/config/baselines/%s" % baseline_id)[2]["item"][
            "revision"
        ] == "r1"

        status, _h, body = caller.json(
            "POST",
            "/api/v1/p/p1/config/changes/%s/submit" % change["id"],
            body={"reason": "报批：产线要求"},
        )
        assert status == 201, body
        assert body["item"]["state"] == "submitted"

        status, _h, body = caller.json(
            "POST",
            "/api/v1/p/p1/config/changes/%s/approve" % change["id"],
            body={"note": "同意"},
        )
        assert status == 201, body
        assert body["change_set"]["state"] == "approved"
        assert body["change_set"]["published_baseline_revision"] == "r2"
        assert body["baseline"]["revision"] == "r2"
        assert body["baseline"]["state"] == "approved"
        assert body["baseline"]["values"][world["pulse"]["id"]]["value"]["text"] == "700"

        detail = caller.json("GET", "/api/v1/p/p1/config/changes/%s" % change["id"])[2]
        assert detail["changed_rows"] == 1
        assert detail["allowed"] == []
        assert detail["staleness"]["stale"] is False


def test_detail_and_list_shape(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        _create(caller, world)
        listing = caller.json("GET", "/api/v1/p/p1/config/changes")[2]
        assert listing["total"] == 1
        assert "submitted" in listing["states"]
        assert "clear_pin" in listing["decisions"]
        assert listing["algorithms"][0] == "reconcile"
        assert caller.json("GET", "/api/v1/p/p1/config/changes?state=submitted")[2]["total"] == 0


def test_profile_mismatch_is_not_found(monkeypatch) -> None:
    """改 URL 里的 pid 拿不到别的机型的单。"""
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1", "p2")
        caller = as_role(server, "admin")
        world = _seed(caller)
        change = _create(caller, world)[2]["item"]

        status, _h, body = caller.json(
            "GET", "/api/v1/p/p2/config/changes/%s" % change["id"]
        )
        assert status == 404
        assert body["error"]["code"] == "NOT_FOUND"
        status, _h, body = caller.json(
            "POST",
            "/api/v1/p/p2/config/changes/%s/approve" % change["id"],
            body={"note": "越权"},
        )
        assert status == 404


def test_transition_errors_carry_state_and_allowed(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        change = _create(caller, world)[2]["item"]

        status, _h, body = caller.json(
            "POST", "/api/v1/p/p1/config/changes/%s/approve" % change["id"], body={}
        )
        assert status == 409
        assert body["error"]["code"] == "CHANGE_STATE_INVALID"
        assert body["error"]["detail"]["state"] == "draft"

        status, _h, body = caller.json(
            "POST",
            "/api/v1/p/p1/config/changes/%s/submit" % change["id"],
            body={"reason": "报批"},
        )
        assert status == 201
        # 提交之后改草稿：409 且告诉我们还能往哪走
        status, _h, body = caller.json(
            "POST",
            "/api/v1/p/p1/config/changes/%s" % change["id"],
            body={"changes": [{"parameter_id": world["pulse"]["id"], "to": "720"}]},
        )
        assert status == 409
        assert body["error"]["detail"]["allowed"] == ["approved", "rejected", "withdrawn"]


def test_out_of_range_is_422_with_parameter(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        status, _h, body = _create(
            caller,
            world,
            changes=[{"parameter_id": world["pulse"]["id"], "to": "5000"}],
        )
        assert status == 422
        assert body["error"]["code"] == "VALUE_OUT_OF_RANGE"
        assert body["error"]["detail"]["parameter_id"] == world["pulse"]["id"]


def test_reject_then_reopen(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        change = _create(caller, world)[2]["item"]
        caller.json(
            "POST", "/api/v1/p/p1/config/changes/%s/submit" % change["id"],
            body={"reason": "报批"},
        )
        status, _h, body = caller.json(
            "POST", "/api/v1/p/p1/config/changes/%s/reject" % change["id"],
            body={"note": "数值待确认"},
        )
        assert status == 201 and body["item"]["state"] == "rejected"

        status, _h, body = caller.json(
            "POST", "/api/v1/p/p1/config/changes/%s/reopen" % change["id"],
            body={"reason": "确认后重开"},
        )
        assert status == 201, body
        assert body["item"]["id"] != change["id"]
        assert body["item"]["state"] == "draft"
        assert body["item"]["reopened_from"] == change["id"]
        # 旧单还是 rejected
        assert caller.json("GET", "/api/v1/p/p1/config/changes/%s" % change["id"])[2][
            "item"
        ]["state"] == "rejected"


def test_create_is_idempotent_over_http(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        payload = {
            "baseline_id": world["baseline"]["id"],
            "changes": [{"parameter_id": world["pulse"]["id"], "to": "700"}],
            "title": "改脉冲宽度",
        }
        headers = {"Idempotency-Key": "cs-http-1"}
        first = caller.json(
            "POST", "/api/v1/p/p1/config/changes", body=payload, headers=headers
        )[2]["item"]
        again = caller.json(
            "POST", "/api/v1/p/p1/config/changes", body=payload, headers=headers
        )[2]["item"]
        assert again["id"] == first["id"]
        assert caller.json("GET", "/api/v1/p/p1/config/changes")[2]["total"] == 1

        status, _h, body = caller.json(
            "POST",
            "/api/v1/p/p1/config/changes",
            body={
                "baseline_id": world["baseline"]["id"],
                "changes": [{"parameter_id": world["pulse"]["id"], "to": "710"}],
                "title": "改脉冲宽度",
            },
            headers=headers,
        )
        assert status == 409
        assert body["error"]["code"] == "IDEMPOTENCY_MISMATCH"


def test_plan_stale_after_baseline_moved(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        change = _create(caller, world)[2]["item"]
        caller.json(
            "POST", "/api/v1/p/p1/config/changes/%s/submit" % change["id"],
            body={"reason": "报批"},
        )
        # 别人先把基线改了一版（草稿仍可改值）
        caller.json(
            "POST",
            "/api/v1/config/baselines/%s/values" % world["baseline"]["id"],
            body={"values": {world["pulse"]["id"]: {"value": "660"}}, "reason": "先改一版"},
        )
        status, _h, body = caller.json(
            "POST", "/api/v1/p/p1/config/changes/%s/approve" % change["id"], body={}
        )
        assert status == 409
        assert body["error"]["code"] == "PLAN_STALE"
        # 单子还在
        assert caller.json("GET", "/api/v1/p/p1/config/changes/%s" % change["id"])[2][
            "item"
        ]["state"] == "submitted"
