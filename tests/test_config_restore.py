"""历史回滚 / 跨变体迁移 / 出口补发（M7-06 / INH-1965）。

三条验收都在这里变成可失败的断言：

* **回滚 → 新已批准对象**：旧修订、旧对象、旧产物的字节一个都不许变
  （`test_restore_creates_new_approved_object` / `test_artifact_of_rolled_back_revision_stays_readable`）。
* **不得把旧软件 XML 当新软件 XML 下发**：补发刷新的新草稿仍钉在原版本与原变体上
  （`test_reissue_keeps_release_and_variant`）。
* **补发不改变已发布基线使用的批次引用**：补发后基线的变体/模板快照不变
  （`test_reissue_does_not_repoint_baseline`）。
"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.errors import INVALID_INPUT, NOT_FOUND, PLAN_STALE, AppError
from app.services import config_artifacts, config_baselines as bl, config_catalog
from app.services import config_restore
import upgrade_fixtures as fx

PROFILE = "MDL-A"


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


@pytest.fixture()
def world(repo):
    return fx.build(repo)


def _values(repo, world, pulse_text: str, speed_text: str = "100", extra=None):
    """按定义集合规整成正式形态——`push` 只是追加修订，不会替我们归一化。"""
    raw = {
        world["pulse"]["id"]: {"value": pulse_text, "intent": "override"},
        world["speed"]["id"]: {"value": speed_text, "intent": "override"},
    }
    if extra:
        raw.update(extra)
    return bl.normalize_values(repo, raw, definition_set_id=None)


def _history_baseline(repo, world):
    """r1 草稿 → r2 已批准（脉冲 900）→ r3 已批准（脉冲 1000）。"""
    common = fx.approve_common(repo, world, values=_values(repo, world, "900"))
    head = fx.push(
        repo,
        common,
        values=_values(repo, world, "1000"),
        state=bl.STATE_APPROVED,
    )
    assert common["revision"] == "r2"
    assert head["revision"] == "r3"
    return common, head


# -- 回滚 -------------------------------------------------------------------


def test_plan_lists_what_restore_would_change(repo, world) -> None:
    common, head = _history_baseline(repo, world)
    plan = config_restore.plan_restore(
        repo, baseline_id=common["id"], revision="r2"
    )
    assert plan["mode"] == "restore"
    assert plan["baseline"]["head_revision"] == "r3"
    assert plan["source"]["revision"] == "r2"
    assert plan["changed"] == 1
    row = plan["items"][0]
    assert row["parameter_id"] == world["pulse"]["id"]
    assert row["from"]["text"] == "1000"
    assert row["to"]["text"] == "900"
    assert plan["publishable"] is False
    # 只读：head 还是 r3
    assert bl.get_baseline(repo, common["id"])["revision"] == "r3"
    assert head["revision"] == "r3"


def test_restore_creates_new_approved_object(repo, world) -> None:
    common, _head = _history_baseline(repo, world)
    history_before = repo.history("baseline", common["id"])

    result = config_restore.restore_baseline(
        repo, fx.ACTOR, baseline_id=common["id"], revision="r2", reason="回滚到 900"
    )
    restored = result["item"]

    # 新对象、新修订、已批准——而不是把 head 指回 r2
    assert restored["id"] != common["id"]
    assert restored["state"] == bl.STATE_APPROVED
    assert restored["values"][world["pulse"]["id"]]["value"]["text"] == "900"
    assert restored["previous_revision_id"] == common["id"]

    provenance = restored["restore_provenance"]
    assert provenance["mode"] == "restore"
    assert provenance["from_revision"] == "r2"
    assert provenance["head_revision_at_restore"] == "r3"
    assert provenance["restored_by"] == fx.ACTOR["name"]
    assert provenance["changed"] == 1

    # 旧对象一个字没动，旧修订仍在链上、仍可读
    assert repo.history("baseline", common["id"]) == history_before == ["r1", "r2", "r3"]
    old = bl.get_baseline(repo, common["id"], "r2")
    assert old["values"][world["pulse"]["id"]]["value"]["text"] == "900"
    assert bl.get_baseline(repo, common["id"])["revision"] == "r3"

    assert result["previous_head"] == {"baseline_id": common["id"], "revision": "r3"}
    assert result["source"]["still_readable"] is True


def test_artifact_of_rolled_back_revision_stays_readable(repo, world) -> None:
    """回滚不能动到已经发出去的产物字节。"""
    common, head = _history_baseline(repo, world)
    produced = config_artifacts.generate_artifact(
        repo, fx.ACTOR, profile_id=PROFILE, baseline_id=common["id"]
    )
    artifact = produced["item"]
    before = config_artifacts.artifact_bytes(repo, artifact["id"])
    assert artifact["baseline_revision"] == "r3"

    config_restore.restore_baseline(repo, fx.ACTOR, baseline_id=common["id"], revision="r2")

    after = config_artifacts.get_artifact(repo, artifact["id"])
    assert after["baseline_revision"] == "r3"
    assert config_artifacts.artifact_bytes(repo, artifact["id"]) == before
    # 旧基线对象也还在
    assert bl.get_baseline(repo, common["id"])["revision"] == head["revision"] == "r3"


def test_restore_to_current_head_is_rejected(repo, world) -> None:
    common, _head = _history_baseline(repo, world)
    with pytest.raises(AppError) as caught:
        config_restore.plan_restore(repo, baseline_id=common["id"], revision="r3")
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["revision"] == "r3"


def test_unknown_revision_lists_available_ones(repo, world) -> None:
    common, _head = _history_baseline(repo, world)
    with pytest.raises(AppError) as caught:
        config_restore.plan_restore(repo, baseline_id=common["id"], revision="r9")
    assert caught.value.code == NOT_FOUND
    assert caught.value.detail["revisions"] == ["r1", "r2", "r3"]


def test_stale_head_is_rejected(repo, world) -> None:
    common, _head = _history_baseline(repo, world)
    with pytest.raises(AppError) as caught:
        config_restore.restore_baseline(
            repo,
            fx.ACTOR,
            baseline_id=common["id"],
            revision="r2",
            expected_head="r2",
        )
    assert caught.value.code == PLAN_STALE
    assert caught.value.detail["drift"]["head_revision"]["actual"] == "r3"


def test_restored_object_can_only_go_back_to_its_own_history(repo, world) -> None:
    """新对象只有一条修订：想再回滚就得对着旧对象操作，谱系不会被"穿过去"。"""
    common, _head = _history_baseline(repo, world)
    restored = config_restore.restore_baseline(
        repo, fx.ACTOR, baseline_id=common["id"], revision="r2"
    )["item"]
    assert repo.history("baseline", restored["id"]) == ["r1"]
    with pytest.raises(AppError) as caught:
        config_restore.plan_restore(repo, baseline_id=restored["id"], revision="r3")
    assert caught.value.code == NOT_FOUND


# -- 跨变体迁移 -------------------------------------------------------------


def _old_baseline(repo, world):
    common = fx.approve_common(repo, world, values=_values(repo, world, "900"))
    return common


def test_migration_plan_aligns_by_parameter_id(repo, world) -> None:
    common = _old_baseline(repo, world)
    plan = config_restore.plan_variant_migration(
        repo,
        baseline_id=common["id"],
        target_variant_id=world["new"]["variant"]["id"],
    )
    assert plan["mode"] == "variant_migration"
    assert plan["source"]["variant_id"] == world["old"]["variant"]["id"]
    assert plan["target"]["variant_id"] == world["new"]["variant"]["id"]
    assert plan["target"]["release_id"] == world["new"]["release"]["id"]
    assert plan["blockers"] == []
    assert plan["can_apply"] is True
    assert plan["publishable"] is False
    # 单位相同：不换算，也不假装换算过
    rows = {row["parameter_id"]: row for row in plan["items"]}
    assert rows[world["pulse"]["id"]]["converted"] is False
    assert rows[world["pulse"]["id"]]["to"]["text"] == "900"


def test_migration_blocks_unknown_parameter(repo, world) -> None:
    """目标变体的定义集合里没有这个参数：阻断，不丢值也不猜。"""
    torque = config_catalog.create_parameter(
        repo, fx.ACTOR, family_id=world["family"]["id"], name="扭矩", category="运动"
    )
    torque_def = config_catalog.define_parameter(
        repo,
        fx.ACTOR,
        parameter_id=torque["id"],
        xml_name="Torque",
        xml_loc={"path": [{"name": "Torque", "occurrence": 1}], "attribute": None},
        value_type="decimal",
        unit="N·m",
    )
    bigger = config_catalog.create_definition_set(
        repo,
        fx.ACTOR,
        release_id=world["old"]["release"]["id"],
        variant_id=world["old"]["variant"]["id"],
        definition_ids=[
            world["pulse_def"]["id"],
            world["speed_def"]["id"],
            torque_def["id"],
        ],
    )
    source = bl.create_baseline(
        repo,
        fx.ACTOR,
        scope=bl.SCOPE_COMMON,
        release_id=world["old"]["release"]["id"],
        definition_set_id=bigger["id"],
        values=_values(
            repo,
            world,
            "900",
            extra={torque["id"]: {"value": "12", "intent": "override"}},
        ),
    )
    source = fx.push(repo, source, state=bl.STATE_APPROVED, decided_by=fx.ACTOR["name"])

    plan = config_restore.plan_variant_migration(
        repo,
        baseline_id=source["id"],
        target_variant_id=world["new"]["variant"]["id"],
    )
    codes = {(item["code"], item["parameter_id"]) for item in plan["blockers"]}
    assert ("unknown_parameter", torque["id"]) in codes
    assert plan["can_apply"] is False

    with pytest.raises(AppError) as caught:
        config_restore.create_variant_migration(
            repo,
            fx.ACTOR,
            baseline_id=source["id"],
            target_variant_id=world["new"]["variant"]["id"],
        )
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["blockers"][0]["code"] == "unknown_parameter"


def test_migration_blocks_unconvertible_unit(repo, world) -> None:
    """目标定义把速度改成时间单位：量纲对不上，阻断而不是按数字照搬。"""
    # 先把基线建出来（此时速度还是 mm/s），再改目标定义——顺序反了源值会被
    # 按新定义解释成 "100 s"，那就测不到"换不动"这件事了
    common = _old_baseline(repo, world)
    assert common["values"][world["speed"]["id"]]["value"]["unit"] == "mm/s"

    speed_redef = config_catalog.define_parameter(
        repo,
        fx.ACTOR,
        parameter_id=world["speed"]["id"],
        xml_name="MaxSpeed",
        xml_loc={"path": [{"name": "MaxSpeed", "occurrence": 1}], "attribute": None},
        value_type="decimal",
        unit="s",
        remarks="故意与源单位不同，用来验证换算失败",
    )
    target_set = config_catalog.create_definition_set(
        repo,
        fx.ACTOR,
        release_id=world["new"]["release"]["id"],
        variant_id=world["new"]["variant"]["id"],
        definition_ids=[world["pulse_def"]["id"], speed_redef["id"]],
    )
    assert target_set["id"] != world["new"]["definition_set"]["id"]

    plan = config_restore.plan_variant_migration(
        repo,
        baseline_id=common["id"],
        target_variant_id=world["new"]["variant"]["id"],
    )
    codes = {(item["code"], item["parameter_id"]) for item in plan["blockers"]}
    assert ("incomparable", world["speed"]["id"]) in codes
    assert plan["can_apply"] is False
    # 别的参数照常出计划，不要"一个坏就全不显示"
    assert [row["parameter_id"] for row in plan["items"]] == [world["pulse"]["id"]]


def test_migration_creates_draft_on_target_variant(repo, world) -> None:
    common = _old_baseline(repo, world)
    result = config_restore.create_variant_migration(
        repo,
        fx.ACTOR,
        baseline_id=common["id"],
        target_variant_id=world["new"]["variant"]["id"],
        reason="换模板",
    )
    created = result["item"]
    assert created["state"] == bl.STATE_DRAFT
    assert created["release_id"] == world["new"]["release"]["id"]
    assert created["variant_id"] == world["new"]["variant"]["id"]
    assert created["definition_set_id"] == world["new"]["definition_set"]["id"]
    assert created["upgrade_provenance"]["mode"] == "variant_migration"
    assert created["upgrade_provenance"]["from_baseline_id"] == common["id"]
    assert created["values"][world["pulse"]["id"]]["value"]["text"] == "900"
    # 源基线不被覆盖
    assert bl.get_baseline(repo, common["id"])["revision"] == common["revision"]


def test_migration_needs_definitions_on_target(repo, world) -> None:
    bare = config_catalog.register_release(
        repo, fx.ACTOR, version_label="3.0.0", build="20260300"
    )
    snapshot = config_catalog.register_snapshot(
        repo,
        fx.ACTOR,
        release_id=bare["id"],
        logical_file_id=world["logical"]["id"],
        data=fx.TEMPLATE_OLD,
        original_filename="axis_3.xml",
    )
    variant = config_catalog.ensure_variant(
        repo,
        fx.ACTOR,
        family_id=world["family"]["id"],
        release_id=bare["id"],
        template_snapshot_id=snapshot["id"],
    )
    common = _old_baseline(repo, world)
    with pytest.raises(AppError) as caught:
        config_restore.plan_variant_migration(
            repo, baseline_id=common["id"], target_variant_id=variant["id"]
        )
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["field"] == "target_variant_id"


# -- 出口补发 ---------------------------------------------------------------


def _reissue(repo, world):
    """同版本再发一批出口文件（内容换成新模板），返回新快照。"""
    snapshot = config_catalog.register_snapshot(
        repo,
        fx.ACTOR,
        release_id=world["old"]["release"]["id"],
        logical_file_id=world["logical"]["id"],
        data=fx.TEMPLATE_NEW,
        original_filename="axis_1.0.0_reissue.xml",
    )
    config_catalog.publish_default_batch(
        repo, fx.ACTOR, release_id=world["old"]["release"]["id"],
        snapshot_ids=[snapshot["id"]], note="同版补发",
    )
    return snapshot


def test_reissue_keeps_release_and_variant(repo, world) -> None:
    common = _old_baseline(repo, world)
    _reissue(repo, world)
    plan = config_restore.plan_reissue_impact(repo, baseline_id=common["id"])

    assert plan["mode"] == "reissue_refresh"
    assert plan["baseline"]["release_id"] == world["old"]["release"]["id"]
    assert plan["baseline"]["variant_id"] == world["old"]["variant"]["id"]
    assert plan["baseline_reference_unchanged"] is True
    # 基线的模板快照不在补发后的批次里——但基线自己没被改指向
    assert plan["template"]["in_current_batch"] is False
    assert plan["template"]["snapshot_id"] == world["old"]["snapshot"]["id"]

    result = config_restore.create_reissue_refresh(
        repo, fx.ACTOR, baseline_id=common["id"]
    )
    created = result["item"]
    # 新草稿钉在原版本、原变体上：不会把旧软件 XML 当新版本下发
    assert created["release_id"] == world["old"]["release"]["id"]
    assert created["variant_id"] == world["old"]["variant"]["id"]
    assert created["state"] == bl.STATE_DRAFT
    assert created["values"][world["pulse"]["id"]]["value"]["text"] == "700"
    assert created["values"][world["speed"]["id"]]["value"]["text"] == "120"
    assert created["upgrade_provenance"]["mode"] == "reissue_refresh"


def test_reissue_does_not_repoint_baseline(repo, world) -> None:
    common = _old_baseline(repo, world)
    variant_before = config_catalog.get_variant(repo, world["old"]["variant"]["id"])
    reissued = _reissue(repo, world)

    after = bl.get_baseline(repo, common["id"])
    assert after["revision"] == common["revision"]
    assert after["values"][world["pulse"]["id"]]["value"]["text"] == "900"
    variant_after = config_catalog.get_variant(repo, world["old"]["variant"]["id"])
    assert variant_after["template_snapshot_id"] == variant_before["template_snapshot_id"]
    assert variant_after["template_snapshot_id"] != reissued["id"]
    assert variant_after["revision"] == variant_before["revision"]


def test_reissue_without_change_is_not_refreshed(repo, world) -> None:
    """补发内容与基线一致：不建空草稿。"""
    # 一条没改过值的基线：两条都是 inherit，和出口默认逐项相同
    aligned = fx.approve_common(repo, world)
    plan = config_restore.plan_reissue_impact(repo, baseline_id=aligned["id"])
    assert plan["changed"] == 0
    with pytest.raises(AppError) as caught:
        config_restore.create_reissue_refresh(
            repo, fx.ACTOR, baseline_id=aligned["id"]
        )
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["field"] == "baseline_id"


def test_reissue_without_batch_is_reported(repo, world) -> None:
    bare = config_catalog.register_release(
        repo, fx.ACTOR, version_label="4.0.0", build="20260400"
    )
    baseline = bl.create_baseline(
        repo,
        fx.ACTOR,
        scope=bl.SCOPE_COMMON,
        release_id=bare["id"],
        values=_values(repo, world, "900"),
    )
    baseline = fx.push(repo, baseline, state=bl.STATE_APPROVED, decided_by=fx.ACTOR["name"])
    plan = config_restore.plan_reissue_impact(repo, baseline_id=baseline["id"])
    assert plan["current_batch"] is None
    assert plan["warnings"]
    with pytest.raises(AppError) as caught:
        config_restore.create_reissue_refresh(repo, fx.ACTOR, baseline_id=baseline["id"])
    assert caught.value.code == INVALID_INPUT
