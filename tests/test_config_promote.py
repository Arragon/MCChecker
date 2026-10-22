"""现场经验上提（M7-05 / INH-1964）。

卡面验收：**一台机型的补偿值不因多次出现自动变通用默认**。这条在这里落成三条
可失败的断言：

* 上提不是自动的 —— 计划只是计划，`publishable` 恒为 False，落地产出的是变更单，
  要管理员批准才动通用基线（`test_promote_creates_change_set_not_baseline`）。
* 上提只认"本地决定" —— 现场采集、单机标定、出口默认一律排除，理由逐条可读
  （`test_only_local_decisions_are_eligible` / `test_non_local_sources_are_excluded`）。
* 上提不改写下级 —— 批准后 A/B 仍引旧上级修订，要跟进必须显式 rebase
  （`test_approval_leaves_downstream_untouched`）。

另外两条阻断：证据撤销（来源对象已删）与版本不匹配，都让计划直接不可落地。
"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.errors import INVALID_INPUT, NOT_FOUND, PLAN_STALE, AppError
from app.services import config_baselines as bl
from app.services import config_changes, config_promote
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


def _setup(repo, world, *, model_values=None, common_values=None):
    """已批准通用（目标） + 已批准机型（来源）。"""
    common = fx.approve_common(repo, world, values=common_values or {})
    model = fx.approve_model(
        repo, world, profile_id=PROFILE, upstream=common, values=model_values or {}
    )
    return common, model


def _override(value, source_ref=None):
    item = {"value": value, "intent": "override"}
    if source_ref is not None:
        item["source_ref"] = source_ref
    return item


# -- 计划：谁能上提 ---------------------------------------------------------


def test_only_local_decisions_are_eligible(repo, world) -> None:
    common, model = _setup(
        repo, world, model_values={world["pulse"]["id"]: _override("900")}
    )
    plan = config_promote.plan_promote_to_common(repo, profile_id=PROFILE)

    assert plan["mode"] == "promote_to_common"
    assert plan["algorithm"] == "promote"
    assert plan["publishable"] is False
    assert plan["source"]["baseline_id"] == model["id"]
    assert plan["target"]["baseline_id"] == common["id"]

    rows = {row["parameter_id"]: row for row in plan["items"]}
    pulse = rows[world["pulse"]["id"]]
    assert pulse["eligible"] is True
    assert pulse["from"]["text"] == "650"
    assert pulse["to"]["text"] == "900"
    assert pulse["decision"] == "use_new"

    # 速度是 inherit：跟着上游走，本来就不是本地经验
    speed = rows[world["speed"]["id"]]
    assert speed["eligible"] is False
    assert speed["excluded_reason"] == "not_local"
    assert plan["eligible"] == [world["pulse"]["id"]]
    assert plan["blockers"] == []
    assert plan["can_apply"] is True


def test_non_local_sources_are_excluded(repo, world) -> None:
    """采集/标定/默认/标不通 四种来源，各有各的理由码。"""
    pulse, speed = world["pulse"]["id"], world["speed"]["id"]
    common, _model = _setup(
        repo,
        world,
        model_values={
            pulse: _override("900", {"kind": "observation"}),
            speed: _override("110", {"kind": "calibration"}),
        },
        common_values={},
    )
    plan = config_promote.plan_promote_to_common(repo, profile_id=PROFILE)
    rows = {row["parameter_id"]: row for row in plan["items"]}
    assert rows[pulse]["excluded_reason"] == "observation_sourced"
    assert rows[speed]["excluded_reason"] == "device_calibration"
    assert plan["eligible"] == []
    assert plan["can_apply"] is False
    assert plan["warnings"], "没有可上提的参数必须给出提示"


def test_factory_default_and_broken_source_are_excluded(repo, world) -> None:
    pulse, speed = world["pulse"]["id"], world["speed"]["id"]
    _setup(
        repo,
        world,
        model_values={
            pulse: _override("900", {"kind": "snapshot"}),
            speed: _override("110", {"kind": "baseline"}),
        },
    )
    plan = config_promote.plan_promote_to_common(repo, profile_id=PROFILE)
    rows = {row["parameter_id"]: row for row in plan["items"]}
    assert rows[pulse]["excluded_reason"] == "factory_default"
    # 标着 baseline 却写 override：标注与意图矛盾，按"来源不明"处理
    assert rows[speed]["excluded_reason"] == "unknown_source"
    assert plan["eligible"] == []


def test_same_value_is_not_promoted(repo, world) -> None:
    pulse = world["pulse"]["id"]
    _setup(
        repo,
        world,
        common_values={pulse: _override("900")},
        model_values={pulse: _override("900")},
    )
    plan = config_promote.plan_promote_to_common(repo, profile_id=PROFILE)
    rows = {row["parameter_id"]: row for row in plan["items"]}
    assert rows[pulse]["eligible"] is False
    assert rows[pulse]["excluded_reason"] == "already_same"


def test_parameter_filter_limits_candidates(repo, world) -> None:
    pulse, speed = world["pulse"]["id"], world["speed"]["id"]
    _setup(
        repo,
        world,
        model_values={pulse: _override("900"), speed: _override("140")},
    )
    plan = config_promote.plan_promote_to_common(
        repo, profile_id=PROFILE, parameter_ids=[speed]
    )
    assert [row["parameter_id"] for row in plan["items"]] == [speed]
    assert plan["eligible"] == [speed]

    with pytest.raises(AppError) as caught:
        config_promote.plan_promote_to_common(
            repo, profile_id=PROFILE, parameter_ids=[]
        )
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["field"] == "parameter_ids"


# -- 阻断 -------------------------------------------------------------------


def test_evidence_revoked_blocks_plan(repo, world) -> None:
    """来源变更单已不存在：证据没法复核，计划不许落地。"""
    pulse = world["pulse"]["id"]
    _setup(
        repo,
        world,
        model_values={
            pulse: _override("900", {"kind": "change_set", "id": "chg_gone_0001"})
        },
    )
    plan = config_promote.plan_promote_to_common(repo, profile_id=PROFILE)
    codes = [item["code"] for item in plan["blockers"]]
    assert "evidence_revoked" in codes
    assert plan["can_apply"] is False

    with pytest.raises(AppError) as caught:
        config_promote.create_promote_to_common(repo, fx.ACTOR, profile_id=PROFILE)
    assert caught.value.code == PLAN_STALE
    assert "evidence_revoked" in caught.value.detail["reasons"]
    # 阻断时不留半成品变更单
    assert config_changes.list_change_sets(repo) == []


def test_release_mismatch_blocks_plan(repo, world) -> None:
    """机型还在旧版本、通用已升到新版本：先升版再谈上提。"""
    common = fx.approve_common(repo, world, release=world["new"])
    fx.approve_model(repo, world, profile_id=PROFILE, upstream=common,
                     values={world["pulse"]["id"]: _override("900")})
    plan = config_promote.plan_promote_to_common(repo, profile_id=PROFILE)
    codes = [item["code"] for item in plan["blockers"]]
    assert codes == ["release_mismatch"]
    assert plan["can_apply"] is False


def test_model_baseline_must_be_approved(repo, world) -> None:
    common = fx.approve_common(repo, world)
    fx.approve_model(
        repo, world, profile_id=PROFILE, upstream=common,
        values={world["pulse"]["id"]: _override("900")}, approved=False,
    )
    with pytest.raises(AppError) as caught:
        config_promote.plan_promote_to_common(repo, profile_id=PROFILE)
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["state"] == "draft"


def test_missing_model_baseline_is_reported(repo, world) -> None:
    fx.approve_common(repo, world)
    with pytest.raises(AppError) as caught:
        config_promote.plan_promote_to_common(repo, profile_id="MDL-NONE")
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["profile_id"] == "MDL-NONE"


def test_baseline_of_another_profile_is_rejected(repo, world) -> None:
    common = fx.approve_common(repo, world)
    other = fx.approve_model(
        repo, world, profile_id="MDL-B", upstream=common,
        values={world["pulse"]["id"]: _override("900")},
    )
    with pytest.raises(AppError) as caught:
        config_promote.plan_promote_to_common(
            repo, profile_id=PROFILE, source_baseline_id=other["id"]
        )
    assert caught.value.code == NOT_FOUND
    assert caught.value.detail["baseline_id"] == other["id"]


# -- 落地 -------------------------------------------------------------------


def test_promote_creates_change_set_not_baseline(repo, world) -> None:
    common, _model = _setup(
        repo, world, model_values={world["pulse"]["id"]: _override("900")}
    )
    result = config_promote.create_promote_to_common(repo, fx.ACTOR, profile_id=PROFILE)
    change = result["item"]

    assert result["submitted"] is True
    assert change["state"] == "submitted"
    assert change["baseline_id"] == common["id"]
    assert change["plan_ref"]["algorithm"] == "promote"
    assert [item["parameter_id"] for item in change["changes"]] == [world["pulse"]["id"]]

    # 关键：通用的 head 一点没动——上提不是发布
    unchanged = bl.get_baseline(repo, common["id"])
    assert unchanged["revision"] == common["revision"]
    assert unchanged["values"][world["pulse"]["id"]]["value"]["text"] == "650"
    # 目标路径上没有新基线对象冒出来
    assert len(bl.list_baselines(repo, scope=bl.SCOPE_COMMON)) == 1


def test_approval_leaves_downstream_untouched(repo, world) -> None:
    """批准上提 → 通用多一条修订；机型仍引旧上游修订，必须显式 rebase 才跟进。"""
    pulse = world["pulse"]["id"]
    common, model = _setup(repo, world, model_values={pulse: _override("900")})
    result = config_promote.create_promote_to_common(repo, fx.ACTOR, profile_id=PROFILE)

    config_changes.approve_change_set(repo, fx.ACTOR, change_id=result["item"]["id"])

    updated = bl.get_baseline(repo, common["id"])
    assert updated["revision"] != common["revision"]
    assert updated["values"][pulse]["value"]["text"] == "900"
    assert updated["values"][world["speed"]["id"]]["value"]["text"] == "100"

    downstream = bl.get_baseline(repo, model["id"])
    assert downstream["revision"] == model["revision"]
    assert downstream["upstream_ref"] == common["id"]
    # 上游修订号还是旧的：光靠 upstream_ref 判断会以为已经跟上了
    assert downstream["upstream_revision"] == common["revision"]
    assert downstream["values"][pulse]["value"]["text"] == "900"


def test_stale_expected_is_rejected(repo, world) -> None:
    _setup(repo, world, model_values={world["pulse"]["id"]: _override("900")})
    with pytest.raises(AppError) as caught:
        config_promote.create_promote_to_common(
            repo,
            fx.ACTOR,
            profile_id=PROFILE,
            expected={"target_revision": "r99"},
        )
    assert caught.value.code == PLAN_STALE
    assert "target_revision" in caught.value.detail["reasons"]


def test_idempotent_promote_replays(repo, world) -> None:
    _setup(repo, world, model_values={world["pulse"]["id"]: _override("900")})
    first = config_promote.create_promote_to_common(
        repo, fx.ACTOR, profile_id=PROFILE, idempotency_key="promote-1"
    )
    again = config_promote.create_promote_to_common(
        repo, fx.ACTOR, profile_id=PROFILE, idempotency_key="promote-1"
    )
    assert again["item"]["id"] == first["item"]["id"]
    assert len(config_changes.list_change_sets(repo)) == 1


# -- 设备层：明确关闭 -------------------------------------------------------


def test_device_promotion_is_reported_as_disabled(repo, world) -> None:
    """M7-07/08 按计划延期：要明确报错，不许假装成功。"""
    with pytest.raises(AppError) as caught:
        config_promote.plan_promote_to_model(repo, profile_id=PROFILE, device_id="DEV-1")
    assert caught.value.code == config_promote.DEVICE_LAYER_DISABLED
    assert caught.value.detail["device_id"] == "DEV-1"
    assert caught.value.detail["device_baselines"] == 0
    assert "延期" in caught.value.message
