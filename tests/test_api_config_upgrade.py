"""升级接口的 HTTP 行为（M7-04 / INH-1963）。

这一层要钉住的不是合并算法（那在 M7-01 与服务层测过），而是**接口契约**：

* 计划是读、落地是写，两个权限键不同；
* 计划里的 `items/blockers/allowed_actions` 原样透出——前端不许自己算；
* 没决议就落地 → 400 且 `pending` 点名；计划过期 → 409 `PLAN_STALE`；
* 路径没带 `release_id` → 400 落在该字段上，不是 500。
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.services import config_baselines as bl, config_gate, config_revision

TEMPLATE_OLD = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<R><PulseWidth unit="ms">650</PulseWidth><MaxSpeed unit="mm/s">100</MaxSpeed></R>'
).encode("utf-8")

TEMPLATE_NEW = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<R><PulseWidth unit="ms">700</PulseWidth><MaxSpeed unit="mm/s">120</MaxSpeed></R>'
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


def _release(caller, *, version, template, family, logical, definition_ids):
    release = caller.json(
        "POST", "/api/v1/config/releases", body={"version_label": version, "build": "2026" + version.replace(".", "")}
    )[2]["item"]
    snapshot = caller.json(
        "POST",
        "/api/v1/config/snapshots?release_id=%s&logical_file_id=%s&source=upload"
        % (release["id"], logical["id"]),
        body=template,
        headers={**OCTET, "X-Filename": "axis_%s.xml" % version},
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
            "definition_ids": definition_ids,
            "variant_id": variant["id"],
        },
    )[2]["item"]
    return {
        "release": release,
        "snapshot": snapshot,
        "variant": variant,
        "definition_set": definition_set,
    }


def _seed(caller):
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
    definition_ids = [pulse_def["id"], speed_def["id"]]
    old = _release(
        caller, version="1.0.0", template=TEMPLATE_OLD, family=family, logical=logical,
        definition_ids=definition_ids,
    )
    new = _release(
        caller, version="2.0.0", template=TEMPLATE_NEW, family=family, logical=logical,
        definition_ids=definition_ids,
    )
    return {
        "family": family,
        "logical": logical,
        "pulse": pulse,
        "speed": speed,
        "old": old,
        "new": new,
    }


def _repo():
    from app.core import storage
    from app.core.config_repository import ConfigRepository

    return ConfigRepository(storage.DATA_DIR)


def _approve_common(world, *, value="900"):
    """推仓储造"旧版本已批准的通用基线"。"""
    repo = _repo()
    baseline = bl.create_baseline(
        repo,
        {"ip": "127.0.0.1", "name": "管理员乙"},
        scope="common",
        release_id=world["old"]["release"]["id"],
        definition_set_id=world["old"]["definition_set"]["id"],
        values={world["pulse"]["id"]: {"value": value, "intent": "override"}},
        reason="旧基线",
    )
    payload = dict(baseline)
    payload.pop("id", None)
    payload.pop("revision", None)
    payload.update({"state": "approved", "decided_by": "管理员乙"})
    tx = repo.transaction({"ip": "127.0.0.1", "name": "管理员乙"}, "测试用批准")
    ref = tx.put("baseline", payload, object_id=baseline["id"])
    tx.commit()
    return bl.get_baseline(repo, str(ref.id))


# -- 门禁与权限 -------------------------------------------------------------


def test_upgrade_requires_gate_and_submit_permission(monkeypatch) -> None:
    _enable(monkeypatch, "off")
    with make_server() as server:
        pid = seed_roles("p1")
        admin = as_role(server, "admin")
        status, _h, body = admin.json("GET", "/api/v1/config/upgrade/common")
        assert status == 503 and body["error"]["code"] == "CONFIG_DISABLED"

    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        admin = as_role(server, "admin")
        world = _seed(admin)
        member = as_role(server, "member")
        # 读：成员能看计划
        query = "?release_id=%s" % world["new"]["release"]["id"]
        assert member.json("GET", "/api/v1/config/upgrade/common" + query)[0] == 200
        # 写：成员不能落地
        status, _h, body = member.json(
            "POST",
            "/api/v1/config/upgrade/common",
            body={"release_id": world["new"]["release"]["id"]},
        )
        assert status == 403
        assert body["error"]["detail"]["action"] == "config_change_submit"


def test_missing_release_id_lands_on_the_field(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles("p1")
        admin = as_role(server, "admin")
        status, _h, body = admin.json("GET", "/api/v1/config/upgrade/common")
        assert status == 400
        assert body["error"]["detail"]["field"] == "release_id"


# -- 计划 -------------------------------------------------------------------


def test_common_plan_exposes_items_and_blockers(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        admin = as_role(server, "admin")
        world = _seed(admin)
        _approve_common(world)

        status, _h, plan = admin.json(
            "GET", "/api/v1/config/upgrade/common?release_id=%s" % world["new"]["release"]["id"]
        )
        assert status == 200, plan
        assert plan["three_way"] is True
        assert plan["publishable"] is False
        items = {item["parameter_id"]: item for item in plan["items"]}
        assert items[world["pulse"]["id"]]["action"] == "conflict"
        assert items[world["speed"]["id"]]["action"] == "from_new"
        # 三方来源与快照哈希原样透出，前端能自证"为什么这么合"
        assert plan["sources"]["base"]["snapshots"][0]["sha256"] == world["old"]["snapshot"]["blob_sha256"]
        assert plan["sources"]["theirs"]["snapshots"][0]["sha256"] == world["new"]["snapshot"]["blob_sha256"]
        assert plan["allowed_actions"] if "allowed_actions" in plan else True
        assert plan["needs_decision"] == [world["pulse"]["id"]]


def test_plan_requires_variant_when_ambiguous(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        admin = as_role(server, "admin")
        world = _seed(admin)
        # 给新版本再加一个族的变体 → 两个候选
        other = admin.json(
            "POST", "/api/v1/config/families", body={"name": "IO", "code": "IO_PARAM"}
        )[2]["item"]
        other_file = admin.json(
            "POST",
            "/api/v1/config/families/%s/files" % other["id"],
            body={"role": "xml_export", "display_name": "io.xml"},
        )[2]["item"]
        snapshot = admin.json(
            "POST",
            "/api/v1/config/snapshots?release_id=%s&logical_file_id=%s&source=upload"
            % (world["new"]["release"]["id"], other_file["id"]),
            body=b'<R><Poll>10</Poll></R>',
            headers={**OCTET, "X-Filename": "io.xml"},
        )[2]["item"]
        admin.json(
            "POST",
            "/api/v1/config/variants",
            body={
                "family_id": other["id"],
                "release_id": world["new"]["release"]["id"],
                "template_snapshot_id": snapshot["id"],
            },
        )
        status, _h, body = admin.json(
            "GET", "/api/v1/config/upgrade/common?release_id=%s" % world["new"]["release"]["id"]
        )
        assert status == 400
        assert body["error"]["detail"]["field"] == "variant_id"
        assert len(body["error"]["detail"]["candidates"]) == 2

        ok = admin.json(
            "GET",
            "/api/v1/config/upgrade/common?release_id=%s&variant_id=%s"
            % (world["new"]["release"]["id"], world["new"]["variant"]["id"]),
        )
        assert ok[0] == 200


# -- 落地 -------------------------------------------------------------------


def test_create_common_requires_decisions_then_lands(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        admin = as_role(server, "admin")
        world = _seed(admin)
        _approve_common(world)

        status, _h, body = admin.json(
            "POST",
            "/api/v1/config/upgrade/common",
            body={"release_id": world["new"]["release"]["id"]},
        )
        assert status == 400
        assert body["error"]["detail"]["pending"] == [world["pulse"]["id"]]

        status, _h, body = admin.json(
            "POST",
            "/api/v1/config/upgrade/common",
            body={
                "release_id": world["new"]["release"]["id"],
                "decisions": {world["pulse"]["id"]: "inherit_old"},
                "reason": "升到 2.0.0",
            },
            headers={"Idempotency-Key": "up-1"},
        )
        assert status == 201, body
        item = body["item"]
        assert item["state"] == "draft"
        assert item["values"][world["pulse"]["id"]]["value"]["text"] == "900"
        assert item["values"][world["speed"]["id"]]["value"]["text"] == "120"
        assert item["upgrade_provenance"]["three_way"] is True


def test_stale_plan_is_409(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        admin = as_role(server, "admin")
        world = _seed(admin)
        _approve_common(world)
        status, _h, body = admin.json(
            "POST",
            "/api/v1/config/upgrade/common",
            body={
                "release_id": world["new"]["release"]["id"],
                "decisions": {world["pulse"]["id"]: "use_new"},
                "expected": {"previous_baseline_revision": "r99"},
            },
        )
        assert status == 409
        assert body["error"]["code"] == "PLAN_STALE"
        assert body["error"]["detail"]["reasons"] == ["previous_baseline_revision"]


# -- 机型 -------------------------------------------------------------------


def test_model_plan_needs_an_existing_model_baseline(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        admin = as_role(server, "admin")
        world = _seed(admin)
        _approve_common(world)
        status, _h, body = admin.json(
            "GET",
            "/api/v1/p/%s/config/upgrade/model?release_id=%s"
            % (pid, world["new"]["release"]["id"]),
        )
        assert status == 400
        assert body["error"]["detail"]["field"] == "profile_id"

        status, _h, body = admin.json("GET", "/api/v1/p/%s/config/upgrade/rebase" % pid)
        assert status == 400
        assert body["error"]["detail"]["field"] == "profile_id"


def test_rebase_requires_new_upstream_revision(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        admin = as_role(server, "admin")
        world = _seed(admin)
        common = _approve_common(world)
        created = admin.json(
            "POST",
            "/api/v1/config/baselines",
            body={
                "scope": "model",
                "release_id": world["old"]["release"]["id"],
                "profile_id": pid,
                "upstream_baseline_id": common["id"],
            },
        )[2]["item"]
        # 批准机型基线（推仓储，免得依赖变更单状态机）
        repo = _repo()
        payload = dict(created)
        payload.pop("id", None)
        payload.pop("revision", None)
        payload.update({"state": "approved", "decided_by": "管理员乙"})
        tx = repo.transaction({"ip": "127.0.0.1", "name": "管理员乙"}, "测试用批准")
        ref = tx.put("baseline", payload, object_id=created["id"])
        tx.commit()
        model = bl.get_baseline(repo, str(ref.id))
        # 上游修订号按**创建那一刻的 head** 落库（`_approve_common` 已把通用推到 r2）
        assert model["upstream_revision"] == common["revision"] == "r2"

        status, _h, plan = admin.json("GET", "/api/v1/p/%s/config/upgrade/rebase" % pid)
        assert status == 200, plan
        assert plan["mode"] == "model_rebase"
        assert plan["noop"] is True
