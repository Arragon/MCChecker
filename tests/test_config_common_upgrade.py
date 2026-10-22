"""通用层升版（M7-02 / INH-1961）。

三方 = **旧出口默认 A / 旧已批准通用 B / 新出口默认 C**。卡面验收：

* 多出口批次 + 两变体时不"随机选最新" —— `test_multiple_variants_must_be_named`
* 无上版来源可新建但不得显示"三方继承成功" —— `test_first_release_is_not_three_way`
* 比对原始 XML hash、provenance、新基线谱系 —— `test_apply_records_provenance_and_lineage`

一条额外的不变量：旧通用**只提供受控继承意图**，绝不覆盖新版出口的原 XML
（`test_old_common_never_touches_new_templates` 用快照哈希比对）。
"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.errors import INVALID_INPUT, PLAN_STALE, AppError
from app.services import config_catalog, config_upgrade
import upgrade_fixtures as fx


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


@pytest.fixture()
def world(repo):
    return fx.build(repo)


# -- 三方计划 ---------------------------------------------------------------


def test_three_way_plan_uses_old_default_and_approved_baseline(repo, world) -> None:
    # 旧出口默认 650/100；旧通用把脉冲改成 900；新出口默认 700/120
    old_common = fx.approve_common(
        repo, world, values={world["pulse"]["id"]: {"value": "900", "intent": "override"}}
    )
    plan = config_upgrade.plan_common_upgrade(
        repo, release_id=world["new"]["release"]["id"]
    )

    assert plan["three_way"] is True
    assert plan["mode"] == "common_release_upgrade"
    assert plan["previous"]["baseline_id"] == old_common["id"]
    items = {item["parameter_id"]: item for item in plan["items"]}

    pulse = items[world["pulse"]["id"]]
    assert pulse["action"] == "conflict", pulse
    assert pulse["base"]["value"]["text"] == "650"
    assert pulse["ours"]["value"]["text"] == "900"
    assert pulse["theirs"]["value"]["text"] == "700"
    assert "both_sides_changed" in pulse["reasons"]

    speed = items[world["speed"]["id"]]
    assert speed["action"] == "from_new"
    assert speed["decision"] == "use_new"
    assert speed["candidate"]["text"] == "120"

    assert plan["needs_decision"] == [world["pulse"]["id"]]
    assert plan["can_apply"] is False
    assert plan["publishable"] is False


def test_sources_carry_batch_snapshot_hashes(repo, world) -> None:
    fx.approve_common(repo, world)
    plan = config_upgrade.plan_common_upgrade(repo, release_id=world["new"]["release"]["id"])
    base = plan["sources"]["base"]
    theirs = plan["sources"]["theirs"]
    assert base["kind"] == "release_default"
    assert theirs["kind"] == "release_default"
    assert base["snapshots"][0]["sha256"] == world["old"]["snapshot"]["blob_sha256"]
    assert theirs["snapshots"][0]["sha256"] == world["new"]["snapshot"]["blob_sha256"]
    # 两个版本的原模板确实不同——否则这套测试什么也没测到
    assert base["snapshots"][0]["sha256"] != theirs["snapshots"][0]["sha256"]


def test_first_release_is_not_three_way(repo) -> None:
    """只有一个版本：能新建，但**不许**说"三方继承成功"。"""
    world = fx.build(repo, versions=("1.0.0",))
    plan = config_upgrade.plan_common_upgrade(repo, release_id=world["new"]["release"]["id"])
    assert plan["three_way"] is False
    assert plan["merge"] is None
    assert plan["sources"]["base"]["present"] is False
    assert plan["sources"]["ours"]["present"] is False
    assert any("无法做三方继承" in text for text in plan["warnings"])
    assert all(item["reasons"] == ["no_previous_source"] for item in plan["items"])
    assert plan["can_apply"] is True


def test_apply_of_first_release_is_flagged_as_not_three_way(repo) -> None:
    world = fx.build(repo, versions=("1.0.0",))
    result = config_upgrade.create_common_upgrade_baseline(
        repo, fx.ACTOR, release_id=world["new"]["release"]["id"]
    )
    item = result["item"]
    assert item["state"] == "draft"
    assert item["upgrade_provenance"]["three_way"] is False
    assert result["applied"]["three_way"] is False
    # 值来自新出口默认
    assert item["values"][world["pulse"]["id"]]["value"]["text"] == "650"
    assert item["values"][world["speed"]["id"]]["value"]["text"] == "100"


# -- 多变体：必须点名 -------------------------------------------------------


def test_multiple_variants_must_be_named(repo, world) -> None:
    """同一版本出现第二个族的模板变体：没点名就 400，不许"取最新那个"。"""
    other_family = config_catalog.create_family(repo, fx.ACTOR, name="IO", code="IO_PARAM")
    other_file = config_catalog.create_logical_file(
        repo, fx.ACTOR, family_id=other_family["id"], role="xml_export", display_name="io.xml"
    )
    poll = config_catalog.create_parameter(
        repo, fx.ACTOR, family_id=other_family["id"], name="轮询间隔", category="通讯"
    )
    poll_def = config_catalog.define_parameter(
        repo,
        fx.ACTOR,
        parameter_id=poll["id"],
        xml_name="Poll",
        xml_loc={"path": [{"name": "Poll", "occurrence": 1}], "attribute": None},
        value_type="int",
    )
    snapshot = config_catalog.register_snapshot(
        repo,
        fx.ACTOR,
        release_id=world["new"]["release"]["id"],
        logical_file_id=other_file["id"],
        data=fx.TEMPLATE_OTHER,
        original_filename="io.xml",
    )
    config_catalog.ensure_variant(
        repo,
        fx.ACTOR,
        family_id=other_family["id"],
        release_id=world["new"]["release"]["id"],
        template_snapshot_id=snapshot["id"],
    )

    with pytest.raises(AppError) as caught:
        config_upgrade.plan_common_upgrade(repo, release_id=world["new"]["release"]["id"])
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["field"] == "variant_id"
    assert len(caught.value.detail["candidates"]) == 2

    # 点名之后就能算
    plan = config_upgrade.plan_common_upgrade(
        repo, release_id=world["new"]["release"]["id"], variant_id=world["new"]["variant"]["id"]
    )
    assert plan["target"]["variant_id"] == world["new"]["variant"]["id"]


def test_variant_must_belong_to_target_release(repo, world) -> None:
    with pytest.raises(AppError) as caught:
        config_upgrade.plan_common_upgrade(
            repo, release_id=world["new"]["release"]["id"], variant_id=world["old"]["variant"]["id"]
        )
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["field"] == "variant_id"


def test_new_release_without_batch_is_rejected(repo, world) -> None:
    bare = config_catalog.register_release(
        repo, fx.ACTOR, version_label="3.0.0", build="20260300"
    )
    with pytest.raises(AppError) as caught:
        config_upgrade.plan_common_upgrade(repo, release_id=bare["id"])
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["field"] == "release_id"


def test_previous_release_must_be_earlier(repo, world) -> None:
    with pytest.raises(AppError) as caught:
        config_upgrade.plan_common_upgrade(
            repo,
            release_id=world["old"]["release"]["id"],
            previous_release_id=world["new"]["release"]["id"],
        )
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["field"] == "previous_release_id"


# -- 落地 -------------------------------------------------------------------


def test_apply_requires_decisions(repo, world) -> None:
    fx.approve_common(
        repo, world, values={world["pulse"]["id"]: {"value": "900", "intent": "override"}}
    )
    with pytest.raises(AppError) as caught:
        config_upgrade.create_common_upgrade_baseline(
            repo, fx.ACTOR, release_id=world["new"]["release"]["id"]
        )
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["pending"] == [world["pulse"]["id"]]


def test_apply_records_provenance_and_lineage(repo, world) -> None:
    old_common = fx.approve_common(
        repo, world, values={world["pulse"]["id"]: {"value": "900", "intent": "override"}}
    )
    old_hashes = [
        snap["blob_sha256"]
        for snap in config_catalog.batch_snapshots(
            repo, config_catalog.default_batch(repo, world["new"]["release"]["id"])
        )
    ]
    result = config_upgrade.create_common_upgrade_baseline(
        repo,
        fx.ACTOR,
        release_id=world["new"]["release"]["id"],
        decisions={world["pulse"]["id"]: "inherit_old"},
        reason="升到 2.0.0",
    )
    item = result["item"]
    assert item["scope"] == "common"
    assert item["release_id"] == world["new"]["release"]["id"]
    assert item["state"] == "draft"
    assert item["upstream_ref"] is None

    # 值：脉冲保留本地 900，速度跟新版 120
    assert item["values"][world["pulse"]["id"]]["value"]["text"] == "900"
    assert item["values"][world["pulse"]["id"]]["intent"] == "override"
    assert item["values"][world["speed"]["id"]]["value"]["text"] == "120"

    provenance = item["upgrade_provenance"]
    assert provenance["mode"] == "common_release_upgrade"
    assert provenance["three_way"] is True
    assert provenance["to_release_id"] == world["new"]["release"]["id"]
    assert provenance["from_release_id"] == world["old"]["release"]["id"]
    assert provenance["ours"]["baseline_id"] == old_common["id"]
    assert provenance["decisions"][world["pulse"]["id"]] == "inherit_old"
    assert provenance["theirs"]["snapshots"][0]["sha256"] in old_hashes
    assert result["applied"]["explicit"] == [world["pulse"]["id"]]

    # 谱系：新基线接在旧通用基线后面（跨版本仍是一条链），靠 provenance 与
    # previous_revision_id 两条线都能追溯
    assert item["previous_revision_id"] == old_common["id"]
    from app.services import config_baselines as bl

    assert bl.lineage(repo, item) == [old_common["id"], item["id"]]


def test_old_common_never_touches_new_templates(repo, world) -> None:
    """旧通用的值只进基线对象，新版出口的快照字节一个都不许变。"""
    before = config_catalog.get_snapshot(repo, world["new"]["snapshot"]["id"])
    fx.approve_common(
        repo, world, values={world["pulse"]["id"]: {"value": "900", "intent": "override"}}
    )
    config_upgrade.create_common_upgrade_baseline(
        repo,
        fx.ACTOR,
        release_id=world["new"]["release"]["id"],
        decisions={world["pulse"]["id"]: "inherit_old"},
    )
    after = config_catalog.get_snapshot(repo, world["new"]["snapshot"]["id"])
    assert after["blob_sha256"] == before["blob_sha256"]
    assert after["revision"] == before["revision"]


def test_pin_decision_keeps_intent(repo, world) -> None:
    fx.approve_common(
        repo, world, values={world["pulse"]["id"]: {"value": "900", "intent": "pin"}}
    )
    result = config_upgrade.create_common_upgrade_baseline(
        repo,
        fx.ACTOR,
        release_id=world["new"]["release"]["id"],
        decisions={world["pulse"]["id"]: "pin"},
    )
    assert result["item"]["values"][world["pulse"]["id"]]["intent"] == "pin"


def test_stale_plan_is_rejected(repo, world) -> None:
    old_common = fx.approve_common(repo, world)
    with pytest.raises(AppError) as caught:
        config_upgrade.create_common_upgrade_baseline(
            repo,
            fx.ACTOR,
            release_id=world["new"]["release"]["id"],
            expected={"previous_baseline_revision": "r99"},
        )
    assert caught.value.code == PLAN_STALE
    assert "previous_baseline_revision" in caught.value.detail["reasons"]
    # 正确的修订号可以过
    result = config_upgrade.create_common_upgrade_baseline(
        repo,
        fx.ACTOR,
        release_id=world["new"]["release"]["id"],
        expected={"previous_baseline_revision": old_common["revision"]},
    )
    assert result["item"]["state"] == "draft"
