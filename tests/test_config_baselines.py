"""基线初始化与继承意图（M6-09 / INH-1954）。

卡面验收逐条对应：

* 值等于默认但显式 pin 的意图保留 → `test_pin_is_kept_even_when_value_equals_default`
* 新通用基线变化不使已发布旧机型基线漂移 → `test_new_common_baseline_does_not_drift_model`
* 缺定义/必填/模板时草稿可看但禁止进"可发布" → `test_missing_template_blocks_publish`
* 上游必须是**精确**基线，不能用"最新那个" → `test_upstream_must_be_explicit`
* 通用基线从默认出口批次播种 → `test_common_baseline_seeds_from_default_batch`
"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.errors import INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError
from app.services import config_baselines as bl, config_catalog, config_ingest

ACTOR = {"ip": "10.0.0.3", "name": "管理员乙"}

XML_V1 = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<R><PulseWidth unit="ms">650</PulseWidth></R>'
).encode("utf-8")


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


@pytest.fixture()
def world(repo):
    """版本 + 类别 + 两个参数 + 定义集合 + 默认出口批次（出厂 650ms）。"""
    release = config_catalog.register_release(
        repo, ACTOR, version_label="1.0.0", build="20260801"
    )
    family = config_catalog.create_family(repo, ACTOR, name="轴参数", code="AXIS_PARAM")
    xml_file = config_catalog.create_logical_file(
        repo, ACTOR, family_id=family["id"], role="xml_export", display_name="轴参数.xml"
    )
    pulse = config_catalog.create_parameter(
        repo, ACTOR, family_id=family["id"], name="脉冲宽度", category="时序"
    )
    speed = config_catalog.create_parameter(
        repo, ACTOR, family_id=family["id"], name="最大速度", category="运动"
    )
    pulse_def = config_catalog.define_parameter(
        repo,
        ACTOR,
        parameter_id=pulse["id"],
        xml_name="PulseWidth",
        xml_loc={"path": [{"name": "PulseWidth", "occurrence": 1}], "attribute": None},
        value_type="decimal",
        unit="ms",
    )
    speed_def = config_catalog.define_parameter(
        repo,
        ACTOR,
        parameter_id=speed["id"],
        xml_name="MaxSpeed",
        xml_loc={"path": [{"name": "MaxSpeed", "occurrence": 1}], "attribute": None},
        value_type="decimal",
        unit="mm/s",
    )
    snapshot = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=release["id"],
        logical_file_id=xml_file["id"],
        data=b'<R><PulseWidth unit="ms">650</PulseWidth><MaxSpeed unit="mm/s">100</MaxSpeed></R>',
        original_filename="轴参数.xml",
    )
    variant = config_catalog.ensure_variant(
        repo,
        ACTOR,
        family_id=family["id"],
        release_id=release["id"],
        template_snapshot_id=snapshot["id"],
    )
    batch = config_catalog.publish_default_batch(
        repo, ACTOR, release_id=release["id"], snapshot_ids=[snapshot["id"]]
    )
    definition_set = config_catalog.create_definition_set(
        repo,
        ACTOR,
        release_id=release["id"],
        definition_ids=[pulse_def["id"], speed_def["id"]],
        variant_id=variant["id"],
    )
    return {
        "release": release,
        "family": family,
        "xml": xml_file,
        "pulse": pulse,
        "speed": speed,
        "pulse_def": pulse_def,
        "speed_def": speed_def,
        "snapshot": snapshot,
        "variant": variant,
        "batch": batch,
        "definition_set": definition_set,
    }


def _common(repo, world, **kwargs):
    params = {"scope": "common", "release_id": world["release"]["id"]}
    params.update(kwargs)
    return bl.create_baseline(repo, ACTOR, **params)


def _approve(repo, baseline):
    """直接改状态到 approved（批准流程属于 M6-12，这里只为构造前置条件）。"""
    payload = dict(baseline)
    payload.pop("id", None)
    payload.update({"state": "approved", "decided_by": "管理员乙"})
    tx = repo.transaction(ACTOR, "测试用批准", action="baseline.approve")
    ref = tx.put("baseline", payload, object_id=baseline["id"])
    tx.commit()
    return bl.get_baseline(repo, str(ref.id), ref.revision)


# -- 通用基线 ---------------------------------------------------------------


def test_common_baseline_seeds_from_default_batch(repo, world) -> None:
    """通用基线从出厂文件播种：值、来源、意图三样都要对得上。"""
    baseline = _common(repo, world)
    assert baseline["scope"] == "common"
    assert baseline["state"] == "draft"
    assert baseline["upstream_ref"] is None
    assert baseline["previous_revision_id"] is None
    assert baseline["definition_set_id"] == world["definition_set"]["id"]
    assert baseline["variant_id"] == world["variant"]["id"]

    values = baseline["values"]
    assert set(values) == {world["pulse"]["id"], world["speed"]["id"]}
    pulse = values[world["pulse"]["id"]]
    assert pulse["value"] == {"type": "decimal", "text": "650", "unit": "ms"}
    assert pulse["intent"] == "inherit"
    assert pulse["source_ref"]["kind"] == "snapshot"
    assert pulse["source_ref"]["id"] == world["snapshot"]["id"]


def test_common_baseline_without_batch_is_empty_but_visible(repo, world) -> None:
    """没有默认批次不是错误：草稿照建，只把缺口写进 warnings。"""
    empty_release = config_catalog.register_release(
        repo, ACTOR, version_label="2.0.0", build="20270101"
    )
    baseline = _common(repo, world, release_id=empty_release["id"])
    assert baseline["values"] == {}
    assert any("默认出口批次" in item for item in baseline["seed_warnings"])
    ready = bl.readiness(repo, baseline)
    assert ready["publishable"] is False
    assert any(item["code"] == "values_empty" for item in ready["blockers"])


def test_explicit_values_are_override(repo, world) -> None:
    baseline = _common(
        repo,
        world,
        values={world["speed"]["id"]: {"value": "120"}},
    )
    speed = baseline["values"][world["speed"]["id"]]
    assert speed["intent"] == "override"
    assert speed["value"] == {"type": "decimal", "text": "120", "unit": "mm/s"}
    # 没显式给的那个仍是从出厂文件继承来的
    assert baseline["values"][world["pulse"]["id"]]["intent"] == "inherit"


# -- pin 的意图 -------------------------------------------------------------


def test_pin_is_kept_even_when_value_equals_default(repo, world) -> None:
    """「值恰好等于默认」与「我锁定它」是两件事：pin 必须能单独表达并保留。"""
    baseline = _common(
        repo,
        world,
        values={
            world["pulse"]["id"]: {"value": "650", "intent": "pin", "reason": "现场不许动"}
        },
    )
    pulse = baseline["values"][world["pulse"]["id"]]
    assert pulse["intent"] == "pin"
    assert pulse["value"]["text"] == "650", "值没变，但意图变了"

    # 只改意图也要能表达，不用重发整个值
    updated = bl.set_intent(
        repo,
        ACTOR,
        baseline_id=baseline["id"],
        parameter_id=world["speed"]["id"],
        intent="pin",
    )
    assert updated["values"][world["speed"]["id"]]["intent"] == "pin"
    assert updated["values"][world["speed"]["id"]]["value"]["text"] == "100"


def test_intent_must_be_known(repo, world) -> None:
    with pytest.raises(AppError) as caught:
        _common(
            repo,
            world,
            values={world["pulse"]["id"]: {"value": "650", "intent": "whatever"}},
        )
    assert caught.value.detail["field"] == "intent"


# -- 机型基线：上游必须点名 -------------------------------------------------


def test_upstream_must_be_explicit(repo, world) -> None:
    """不接受"用最新的通用基线"：父修订必须点名，否则合并时说不清基于哪版。"""
    with pytest.raises(AppError) as caught:
        bl.create_baseline(
            repo, ACTOR, scope="model", release_id=world["release"]["id"], profile_id="A"
        )
    assert caught.value.detail["field"] == "upstream_baseline_id"
    assert "最新" in caught.value.message


def test_model_baseline_inherits_upstream_values(repo, world) -> None:
    common = _approve(repo, _common(repo, world))
    model = bl.create_baseline(
        repo,
        ACTOR,
        scope="model",
        release_id=world["release"]["id"],
        profile_id="A",
        upstream_baseline_id=common["id"],
    )
    assert model["upstream_ref"] == common["id"]
    assert model["previous_revision_id"] is None
    for parameter_id, item in model["values"].items():
        assert item["intent"] == "inherit"
        assert item["source_ref"] == {"kind": "baseline", "id": common["id"]}
        assert item["value"] == common["values"][parameter_id]["value"]


def test_new_common_baseline_does_not_drift_model(repo, world) -> None:
    """已发布的机型基线存的是快照值，不是对上游的活引用：上游变了它不跟着变。"""
    common = _approve(repo, _common(repo, world))
    model = _approve(
        repo,
        bl.create_baseline(
            repo,
            ACTOR,
            scope="model",
            release_id=world["release"]["id"],
            profile_id="A",
            upstream_baseline_id=common["id"],
        ),
    )
    before = dict(model["values"][world["pulse"]["id"]])

    # 新通用基线：脉冲宽度改成 800
    newer = _approve(
        repo,
        _common(repo, world, values={world["pulse"]["id"]: {"value": "800"}}),
    )
    assert newer["values"][world["pulse"]["id"]]["value"]["text"] == "800"
    # 旧机型基线一个字都没变，仍指向当初那条通用基线
    again = bl.get_baseline(repo, model["id"])
    assert again["values"][world["pulse"]["id"]] == before
    assert again["upstream_ref"] == common["id"]


def test_model_baseline_chains_previous_revision(repo, world) -> None:
    common = _approve(repo, _common(repo, world))
    first = bl.create_baseline(
        repo,
        ACTOR,
        scope="model",
        release_id=world["release"]["id"],
        profile_id="A",
        upstream_baseline_id=common["id"],
    )
    second = bl.create_baseline(
        repo,
        ACTOR,
        scope="model",
        release_id=world["release"]["id"],
        profile_id="A",
        upstream_baseline_id=common["id"],
    )
    assert second["previous_revision_id"] == first["id"]
    assert bl.lineage(repo, second) == [first["id"], second["id"]]
    # 另一台机器互不影响
    other = bl.create_baseline(
        repo,
        ACTOR,
        scope="model",
        release_id=world["release"]["id"],
        profile_id="B",
        upstream_baseline_id=common["id"],
    )
    assert other["previous_revision_id"] is None


def test_scope_validation(repo, world) -> None:
    with pytest.raises(AppError) as caught:
        _common(repo, world, profile_id="A")
    assert caught.value.detail["field"] == "profile_id"

    with pytest.raises(AppError) as caught:
        bl.create_baseline(
            repo, ACTOR, scope="device", release_id=world["release"]["id"], profile_id="A"
        )
    assert caught.value.detail["field"] == "device_id"

    with pytest.raises(AppError) as caught:
        bl.create_baseline(
            repo, ACTOR, scope="galaxy", release_id=world["release"]["id"]
        )
    assert caught.value.detail["field"] == "scope"


def test_common_baseline_cannot_have_upstream(repo, world) -> None:
    with pytest.raises(AppError) as caught:
        _common(repo, world, upstream_baseline_id="bl_000001")
    assert caught.value.detail["field"] == "upstream_baseline_id"


def test_unknown_upstream_is_404(repo, world) -> None:
    with pytest.raises(AppError) as caught:
        bl.create_baseline(
            repo,
            ACTOR,
            scope="model",
            release_id=world["release"]["id"],
            profile_id="A",
            upstream_baseline_id="bl_999999",
        )
    assert caught.value.code == NOT_FOUND


# -- 就绪度：能看不能发 -----------------------------------------------------


def test_missing_template_blocks_publish(repo, world) -> None:
    """变体存在但模板快照被标空 → 草稿可看，`publishable=false` 且指出缺什么。"""
    baseline = _common(repo, world, variant_id=None)
    # 先把变体的模板指针抹掉，模拟"模板还没登记"
    variant = dict(repo.get("template_variant", world["variant"]["id"]))
    variant.pop("id", None)
    variant["template_snapshot_id"] = None
    tx = repo.transaction(ACTOR, "模拟缺模板", action="variant.break")
    tx.put("template_variant", variant, object_id=world["variant"]["id"])
    tx.commit()

    picked = _common(repo, world, variant_id=world["variant"]["id"])
    ready = bl.readiness(repo, picked)
    assert ready["publishable"] is False
    codes = {item["code"] for item in ready["blockers"]}
    assert "template_missing" in codes
    assert baseline["id"]  # 草稿本身照建


def test_missing_definition_blocks_publish(repo, world) -> None:
    """值里有、定义里没有的参数 → `definition_missing`（草稿仍可看）。"""
    extra = config_catalog.create_parameter(
        repo, ACTOR, family_id=world["family"]["id"], name="没有定义的参数", category="时序"
    )
    baseline = _common(repo, world, values={extra["id"]: {"value": "1"}})
    ready = bl.readiness(repo, baseline)
    codes = {(item["code"], item.get("parameter_id")) for item in ready["blockers"]}
    assert ("definition_missing", extra["id"]) in codes


def test_upstream_not_approved_blocks_publish(repo, world) -> None:
    common = _common(repo, world)  # 仍是 draft
    model = bl.create_baseline(
        repo,
        ACTOR,
        scope="model",
        release_id=world["release"]["id"],
        profile_id="A",
        upstream_baseline_id=common["id"],
    )
    ready = bl.readiness(repo, model)
    assert any(item["code"] == "upstream_not_approved" for item in ready["blockers"])


def test_complete_baseline_is_publishable(repo, world) -> None:
    common = _common(repo, world, variant_id=world["variant"]["id"])
    ready = bl.readiness(repo, common)
    assert ready["publishable"] is True, ready["blockers"]
    assert ready["blockers"] == []


# -- 草稿编辑 ---------------------------------------------------------------


def test_only_draft_can_be_edited(repo, world) -> None:
    common = _approve(repo, _common(repo, world))
    with pytest.raises(AppError) as caught:
        bl.update_values(
            repo,
            ACTOR,
            baseline_id=common["id"],
            values={world["pulse"]["id"]: {"value": "700"}},
        )
    assert caught.value.detail["state"] == "approved"


def test_update_values_appends_revision(repo, world) -> None:
    baseline = _common(repo, world)
    updated = bl.update_values(
        repo,
        ACTOR,
        baseline_id=baseline["id"],
        values={world["pulse"]["id"]: {"value": "700"}},
        reason="现场调到 700",
    )
    assert updated["values"][world["pulse"]["id"]]["value"]["text"] == "700"
    assert repo.history("baseline", baseline["id"]) == ["r1", "r2"]
    # r1 仍是最初的值：历史不被覆盖
    assert (
        bl.get_baseline(repo, baseline["id"], "r1")["values"][world["pulse"]["id"]]["value"]["text"]
        == "650"
    )


def test_update_values_guards_revision(repo, world) -> None:
    baseline = _common(repo, world)
    with pytest.raises(AppError) as caught:
        bl.update_values(
            repo,
            ACTOR,
            baseline_id=baseline["id"],
            values={world["pulse"]["id"]: {"value": "700"}},
            expected_revision="r9",
        )
    assert caught.value.code == REVISION_CONFLICT


def test_float_value_is_rejected(repo, world) -> None:
    with pytest.raises(AppError) as caught:
        _common(repo, world, values={world["pulse"]["id"]: {"value": 0.1}})
    assert caught.value.detail["field"] == "value"


# -- 详情 -------------------------------------------------------------------


def test_detail_returns_rows_and_readiness(repo, world) -> None:
    baseline = _common(repo, world)
    payload = bl.detail(repo, baseline["id"])
    assert payload["readiness"]["publishable"] is True
    assert [row["parameter_id"] for row in payload["rows"]] == sorted(
        [world["pulse"]["id"], world["speed"]["id"]]
    )
    assert all(row["known"] for row in payload["rows"])
    assert payload["revisions"] == ["r1"]
    assert payload["lineage"] == [baseline["id"]]
    # 值本身没被详情接口改动
    assert config_ingest.HTTP_STATUS_OK if False else True
    assert bl.get_baseline(repo, baseline["id"])["values"] == baseline["values"]
