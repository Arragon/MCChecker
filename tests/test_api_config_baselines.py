"""基线与现场对账的 HTTP 行为（M6-09 / M6-10）。

重点钉住三条容易退化的性质：

* 上游必须点名：`POST /config/baselines` 不带 `upstream_baseline_id` 的机型基线一律 400；
* 草稿"能看不能发"：就绪度把缺口逐条列出来，`publishable=false` 时详情照常 200；
* 对账三栏：`changed` 才给"改成现场值"的建议，`missing_observed` 不给删除建议。
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.services import config_gate, config_revision

XML_DOC = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<R><PulseWidth unit="ms">650</PulseWidth><MaxSpeed unit="mm/s">100</MaxSpeed></R>'
).encode("utf-8")

FIELD_DOC = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<R><PulseWidth unit="ms">700</PulseWidth><MaxSpeed unit="mm/s">100</MaxSpeed></R>'
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
    """版本 + 类别 + 两个参数 + 定义集合 + 默认批次 + 通用基线（已批准）。"""
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
    speed = caller.json(
        "POST",
        "/api/v1/config/parameters",
        body={"family_id": family["id"], "name": "最大速度", "category": "运动"},
    )[2]["item"]
    pulse_def = caller.json(
        "POST",
        "/api/v1/config/parameters/%s/definitions" % pulse["id"],
        body={
            "xml_name": "PulseWidth",
            "xml_loc": {"path": [{"name": "PulseWidth", "occurrence": 1}], "attribute": None},
            "type": "decimal",
            "unit": "ms",
            "reason": "首次登记",
        },
    )[2]["item"]
    speed_def = caller.json(
        "POST",
        "/api/v1/config/parameters/%s/definitions" % speed["id"],
        body={
            "xml_name": "MaxSpeed",
            "xml_loc": {"path": [{"name": "MaxSpeed", "occurrence": 1}], "attribute": None},
            "type": "decimal",
            "unit": "mm/s",
            "reason": "首次登记",
        },
    )[2]["item"]
    snapshot = caller.json(
        "POST",
        "/api/v1/config/snapshots?release_id=%s&logical_file_id=%s&source=upload"
        % (release["id"], logical["id"]),
        body=XML_DOC,
        # 请求头只能装 latin-1：中文文件名走查询串，这里用 ASCII 名做元数据
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
            "definition_ids": [pulse_def["id"], speed_def["id"]],
            "variant_id": variant["id"],
        },
    )[2]["item"]
    return {
        "release": release,
        "family": family,
        "logical": logical,
        "pulse": pulse,
        "speed": speed,
        "snapshot": snapshot,
        "variant": variant,
        "definition_set": definition_set,
    }


def _common(caller, world, **overrides):
    payload = {"scope": "common", "release_id": world["release"]["id"]}
    payload.update(overrides)
    return caller.json("POST", "/api/v1/config/baselines", body=payload)


def _approve(baseline: dict) -> dict:
    """没有批准接口（M6-12）之前，直接用仓储把状态推到 approved 造前置条件。

    对账只认已批准的基线，所以凡是要拿基线当基准的用例都得先过这一步；
    等 M6-12 落地后这里应换成 `POST /config/change-sets/{id}/approve`。
    """
    from app.core import storage
    from app.core.config_repository import ConfigRepository

    repo = ConfigRepository(storage.DATA_DIR)
    payload = dict(baseline)
    payload.pop("id", None)
    payload.update({"state": "approved", "decided_by": "管理员乙"})
    tx = repo.transaction({"ip": "127.0.0.1", "name": "管理员乙"}, "测试用批准")
    ref = tx.put("baseline", payload, object_id=baseline["id"])
    tx.commit()
    approved = dict(baseline)
    approved["state"] = "approved"
    approved["revision"] = ref.revision
    return approved


# -- 门禁与权限 -------------------------------------------------------------


def test_baselines_require_gate_and_admin(monkeypatch) -> None:
    with make_server() as server:
        seed_roles("p1")
        admin = as_role(server, "admin")
        status, _h, body = admin.json("GET", "/api/v1/config/baselines")
        assert status == 503 and body["error"]["code"] == "CONFIG_DISABLED"

    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        admin = as_role(server, "admin")
        world = _seed(admin)
        member = as_role(server, "member")
        assert member.json("GET", "/api/v1/config/baselines")[0] == 200
        status, _h, body = member.json(
            "POST",
            "/api/v1/config/baselines",
            body={"scope": "common", "release_id": world["release"]["id"]},
        )
        assert status == 403
        assert body["error"]["detail"]["action"] == "config_change_submit"


# -- 通用基线 ---------------------------------------------------------------


def test_common_baseline_from_default_batch(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        status, _h, body = _common(caller, world)
        assert status == 201, body
        item = body["item"]
        assert item["scope"] == "common"
        assert item["variant_id"] == world["variant"]["id"]
        assert item["values"][world["pulse"]["id"]]["value"]["text"] == "650"

        detail = caller.json("GET", "/api/v1/config/baselines/%s" % item["id"])[2]
        assert detail["readiness"]["publishable"] is True, detail["readiness"]
        names = [row["name"] for row in detail["rows"]]
        assert names == ["脉冲宽度", "最大速度"]
        assert detail["lineage"] == [item["id"]]


def test_model_baseline_requires_upstream(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/baselines",
            body={"scope": "model", "release_id": world["release"]["id"], "profile_id": "p1"},
        )
        assert status == 400
        assert body["error"]["detail"]["field"] == "upstream_baseline_id"


def test_model_baseline_inherits_and_chains(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        common = _approve(_common(caller, world)[2]["item"])

        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/baselines",
            body={
                "scope": "model",
                "release_id": world["release"]["id"],
                "profile_id": "p1",
                "upstream_baseline_id": common["id"],
            },
        )
        assert status == 201, body
        model = body["item"]
        assert model["upstream_ref"] == common["id"]
        for parameter_id, value in model["values"].items():
            assert value["intent"] == "inherit"
            assert value["source_ref"]["id"] == common["id"]
        assert model["values"][world["pulse"]["id"]]["value"] == common["values"][world["pulse"]["id"]]["value"]


# -- 草稿编辑 ---------------------------------------------------------------


def test_update_values_and_intent(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        common = _common(caller, world)[2]["item"]

        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/baselines/%s/values" % common["id"],
            body={
                "values": {
                    world["pulse"]["id"]: {"value": "700", "intent": "override", "reason": "现场"}
                },
                "reason": "按现场调",
            },
        )
        assert status == 201, body
        assert body["item"]["values"][world["pulse"]["id"]]["value"]["text"] == "700"

        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/baselines/%s/intent" % common["id"],
            body={"parameter_id": world["speed"]["id"], "intent": "pin"},
        )
        assert status == 201, body
        assert body["item"]["values"][world["speed"]["id"]]["intent"] == "pin"

        detail = caller.json("GET", "/api/v1/config/baselines/%s" % common["id"])[2]
        assert detail["revisions"] == ["r1", "r2", "r3"]


def test_update_values_conflict_is_409(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        common = _common(caller, world)[2]["item"]
        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/baselines/%s/values" % common["id"],
            body={
                "values": {world["pulse"]["id"]: {"value": "700"}},
                "expected_revision": "r9",
            },
        )
        assert status == 409
        assert body["error"]["code"] == "REVISION_CONFLICT"


# -- 对账 -------------------------------------------------------------------


def _capture(caller, world, filename="轴参数.xml", body=XML_DOC):
    return caller.json(
        "POST",
        "/api/v1/config/observations?profile_id=p1&filename=%s" % quote(filename),
        body=body,
        headers=OCTET,
    )[2]["item"]


def test_reconcile_without_anything_is_honest(monkeypatch) -> None:
    """没有观测也没有基线：接口照常 200，三栏都是空的，不编造数据。"""
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        payload = caller.json(
            "GET",
            "/api/v1/p/p1/config/reconcile?logical_file_id=%s" % world["logical"]["id"],
        )[2]
        assert payload["has_observation"] is False
        assert payload["has_baseline"] is False
        assert all(row["diff"] == "missing_both" for row in payload["rows"])
        assert all(row["proposed"]["source"] == "none" for row in payload["rows"])


def test_reconcile_marks_changed_and_keeps_missing_untouched(monkeypatch) -> None:
    """现场 700 vs 基线 650 → changed 并建议改成现场值；没采到的参数不建议删除。"""
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        common = _approve(_common(caller, world)[2]["item"])

        # 观测只带 PulseWidth（没有 MaxSpeed）
        _capture(
            caller,
            world,
            body=b'<R><PulseWidth unit="ms">700</PulseWidth></R>',
        )

        payload = caller.json(
            "GET",
            "/api/v1/p/p1/config/reconcile?logical_file_id=%s&baseline_id=%s"
            % (world["logical"]["id"], common["id"]),
        )[2]
        rows = {row["parameter_id"]: row for row in payload["rows"]}
        changed = rows[world["pulse"]["id"]]
        assert changed["diff"] == "changed"
        assert changed["observed"]["value"]["text"] == "700"
        assert changed["baseline"]["value"]["text"] == "650"
        assert changed["proposed"] == {
            "value": {"type": "decimal", "text": "700", "unit": "ms"},
            "source": "observed",
        }

        missing = rows[world["speed"]["id"]]
        assert missing["diff"] == "missing_observed"
        # 拟值仍是基线值，绝不出现"建议删除"
        assert missing["proposed"]["source"] == "baseline"
        assert missing["proposed"]["value"]["text"] == "100"


def test_reconcile_same_value_is_same(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        common = _approve(_common(caller, world)[2]["item"])
        _capture(caller, world, body=XML_DOC)
        status, _h, payload = caller.json(
            "GET",
            "/api/v1/p/p1/config/reconcile?logical_file_id=%s&baseline_id=%s"
            % (world["logical"]["id"], common["id"]),
        )
        assert status == 200, payload
        assert payload["counts"]["changed"] == 0
        assert payload["counts"]["same"] == 2
        assert all(
            row["proposed"]["source"] == "baseline" for row in payload["rows"]
        )


def test_reconcile_rejects_unapproved_baseline(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        draft = _common(caller, world)[2]["item"]
        status, _h, body = caller.json(
            "GET",
            "/api/v1/p/p1/config/reconcile?logical_file_id=%s&baseline_id=%s"
            % (world["logical"]["id"], draft["id"]),
        )
        assert status == 400
        assert body["error"]["detail"]["field"] == "baseline_id"


def test_reconcile_reports_stale_observation(monkeypatch) -> None:
    """指定旧观测时不静默换掉：给出过期提醒和更新的那条 ID。"""
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        common = _approve(_common(caller, world)[2]["item"])

        older = _capture(caller, world, body=XML_DOC)
        # 再采一次改过的内容（视为更新）——同内容会去重，所以这次必须换内容
        newer = _capture(
            caller, world, body=XML_DOC.replace(b"650", b"720")
        )
        status, _h, payload = caller.json(
            "GET",
            "/api/v1/p/p1/config/reconcile?logical_file_id=%s&baseline_id=%s&observation_id=%s"
            % (world["logical"]["id"], common["id"], older["id"]),
        )
        assert status == 200, payload
        assert payload["observation"]["id"] == older["id"]
        assert payload["stale_observation"] is True
        assert payload["newer_observation_id"] == newer["id"]


def test_reconcile_unknown_file_is_404(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        caller = as_role(server, "admin")
        _seed(caller)
        status, _h, body = caller.json(
            "GET",
            "/api/v1/p/p1/config/reconcile?filename=%s" % quote("没人认识.xml"),
        )
        assert status == 404
        assert "hint" in body["error"]["detail"]
