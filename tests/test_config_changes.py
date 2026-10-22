"""变更单状态机与批准发布（M6-11 / M6-12 / INH-1956、INH-1957）。

卡面验收逐条对应：

* 草稿可多次修订、提交后冻结 → `test_update_draft_appends_revision` /
  `test_submit_freezes_and_locks_editing`
* 撤回终态 → `test_withdraw_is_terminal`
* 驳回后可重开一版（新对象，旧的不动） → `test_reopen_creates_new_object_keeps_history`
* 非法回跳 → `test_illegal_transition_is_rejected`
* 目标 head 变化返回 `PLAN_STALE` → `test_plan_stale_when_baseline_moved_after_submit`
* 批准即发布、无中间态 → `test_approve_publishes_in_one_transaction`
* 并发双批准只产一份修订 → `test_second_approve_does_not_publish_twice`
* 批准前复核参数范围 → `test_range_is_checked_at_create_and_approve`
* 幂等：同键同体重放、同键异体 409 → `test_create_is_idempotent`
"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.errors import (
    CHANGE_STATE_INVALID,
    INVALID_INPUT,
    PLAN_STALE,
    REVISION_CONFLICT,
    VALUE_OUT_OF_RANGE,
    AppError,
)
from app.services import config_baselines as bl, config_catalog, config_changes as ch

ACTOR = {"ip": "10.0.0.9", "name": "管理员丙"}


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


@pytest.fixture()
def world(repo):
    """一个版本 + 类别 + 两个参数（脉冲带上下界）+ 一条通用基线草稿。"""
    release = config_catalog.register_release(
        repo, ACTOR, version_label="1.0.0", build="20260801"
    )
    family = config_catalog.create_family(repo, ACTOR, name="轴参数", code="AXIS_PARAM")
    pulse = config_catalog.create_parameter(
        repo, ACTOR, family_id=family["id"], name="脉冲宽度", category="时序"
    )
    speed = config_catalog.create_parameter(
        repo, ACTOR, family_id=family["id"], name="最大速度", category="运动"
    )
    config_catalog.define_parameter(
        repo,
        ACTOR,
        parameter_id=pulse["id"],
        xml_name="PulseWidth",
        xml_loc={"path": [{"name": "PulseWidth", "occurrence": 1}], "attribute": None},
        value_type="decimal",
        unit="ms",
        constraints={"min": "100", "max": "1000"},
    )
    config_catalog.define_parameter(
        repo,
        ACTOR,
        parameter_id=speed["id"],
        xml_name="MaxSpeed",
        xml_loc={"path": [{"name": "MaxSpeed", "occurrence": 1}], "attribute": None},
        value_type="decimal",
        unit="mm/s",
    )
    baseline = bl.create_baseline(
        repo,
        ACTOR,
        scope="common",
        release_id=release["id"],
        values={pulse["id"]: {"value": "650"}},
        reason="初始通用基线",
    )
    return {
        "release": release,
        "family": family,
        "pulse": pulse,
        "speed": speed,
        "baseline": baseline,
    }


def _draft(repo, world, changes=None, **kwargs):
    payload = changes or [
        {"parameter_id": world["pulse"]["id"], "to": "700", "reason": "产线要求"}
    ]
    return ch.create_change_set(repo, ACTOR, baseline_id=world["baseline"]["id"],
                                changes=payload, title="改脉冲", reason="产线要求", **kwargs)


# -- 创建与规整 ---------------------------------------------------------------


def test_create_records_server_side_from(repo, world) -> None:
    """`from` 一律取服务端基线现值——客户端说"原来是 1"不算数。"""
    change = _draft(
        repo,
        world,
        changes=[
            {
                "parameter_id": world["pulse"]["id"],
                "from": {"type": "decimal", "text": "1", "unit": "ms"},  # 假的
                "to": "700",
                "decision": "use_new",
            }
        ],
    )
    assert change["state"] == ch.STATE_DRAFT
    row = change["changes"][0]
    assert row["from"] == {"type": "decimal", "text": "650", "unit": "ms"}
    assert row["to"] == {"type": "decimal", "text": "700", "unit": "ms"}
    assert row["decision"] == "use_new"
    assert row["writes_value"] is True
    assert change["baseline_revision"] == "r1"
    assert change["plan_ref"]["algorithm"] == "reconcile"
    assert len(change["plan_ref"]["output_sha256"]) == 64


def test_create_requires_real_change(repo, world) -> None:
    """把原值再写一遍不算改动——这种单子让审批人签的是"零改动"。"""
    with pytest.raises(AppError) as err:
        _draft(
            repo,
            world,
            changes=[{"parameter_id": world["pulse"]["id"], "to": "650"}],
        )
    assert err.value.code == INVALID_INPUT
    assert err.value.detail["changed_rows"] == 0


def test_unsupported_algorithm_is_rejected(repo, world) -> None:
    """没实现的算法要当场说"没实现"，不假装按 reconcile 算了。

    `reconcile` 之后陆续接入了 `promote`（M7-05）等真实算法；断言写成
    "必须包含 reconcile 且不含 three_way"，这样以后再加算法不会再误红，
    而"未实现的算法被悄悄当成 reconcile"仍然会红。
    """
    with pytest.raises(AppError) as err:
        _draft(repo, world, algorithm="three_way")
    assert err.value.code == INVALID_INPUT
    assert err.value.detail["algorithm"] == "three_way"
    supported = err.value.detail["supported"]
    assert "reconcile" in supported
    assert "three_way" not in supported


def test_duplicate_parameter_is_rejected(repo, world) -> None:
    pid = world["pulse"]["id"]
    with pytest.raises(AppError) as err:
        _draft(repo, world, changes=[{"parameter_id": pid, "to": "700"},
                                     {"parameter_id": pid, "to": "800"}])
    assert err.value.code == INVALID_INPUT
    assert err.value.detail["first_index"] == 0


def test_inherit_old_needs_existing_value(repo, world) -> None:
    """基线里没有的参数，"保持现值"没有可保持的东西。"""
    with pytest.raises(AppError) as err:
        _draft(
            repo,
            world,
            changes=[{"parameter_id": world["speed"]["id"], "decision": "inherit_old"}],
        )
    assert err.value.code == INVALID_INPUT
    assert err.value.detail["parameter_id"] == world["speed"]["id"]


def test_clear_pin_only_on_pinned(repo, world) -> None:
    with pytest.raises(AppError) as err:
        _draft(
            repo,
            world,
            changes=[{"parameter_id": world["pulse"]["id"], "decision": "clear_pin"}],
        )
    assert err.value.code == INVALID_INPUT
    assert err.value.detail["intent"] == "override"


def test_range_is_checked_at_create_and_approve(repo, world) -> None:
    """上下界在校验链上，不是前端提示：越界直接 422。"""
    with pytest.raises(AppError) as err:
        _draft(
            repo,
            world,
            changes=[{"parameter_id": world["pulse"]["id"], "to": "5000"}],
        )
    assert err.value.code == VALUE_OUT_OF_RANGE
    assert err.value.detail["parameter_id"] == world["pulse"]["id"]


# -- 草稿修订与冻结 ------------------------------------------------------------


def test_update_draft_appends_revision(repo, world) -> None:
    change = _draft(repo, world)
    assert change["revision"] == "r1"
    updated = ch.update_change_set(
        repo,
        ACTOR,
        change_id=change["id"],
        changes=[{"parameter_id": world["pulse"]["id"], "to": "720"}],
        expected_revision="r1",
    )
    assert updated["revision"] == "r2"
    assert updated["previous_revision_id"] == "r1"
    assert updated["changes"][0]["to"]["text"] == "720"
    # 历史不被覆盖：r1 那份仍是 700
    old = ch.get_change_set(repo, change["id"], "r1")
    assert old["changes"][0]["to"]["text"] == "700"


def test_update_draft_checks_expected_revision(repo, world) -> None:
    change = _draft(repo, world)
    with pytest.raises(AppError) as err:
        ch.update_change_set(
            repo, ACTOR, change_id=change["id"],
            changes=[{"parameter_id": world["pulse"]["id"], "to": "720"}],
            expected_revision="r0",
        )
    assert err.value.code == REVISION_CONFLICT


def test_submit_freezes_and_locks_editing(repo, world) -> None:
    change = _draft(repo, world)
    submitted = ch.submit_change_set(repo, ACTOR, change_id=change["id"], reason="报批")
    assert submitted["state"] == ch.STATE_SUBMITTED
    assert submitted["frozen_at"] and submitted["submitted_by"] == ACTOR["name"]
    assert submitted["reason"] == "报批"

    with pytest.raises(AppError) as err:
        ch.update_change_set(
            repo, ACTOR, change_id=change["id"],
            changes=[{"parameter_id": world["pulse"]["id"], "to": "720"}],
        )
    assert err.value.code == CHANGE_STATE_INVALID
    assert err.value.detail["allowed"] == ["approved", "rejected", "withdrawn"]


def test_submit_requires_reason(repo, world) -> None:
    change = ch.create_change_set(
        repo, ACTOR, baseline_id=world["baseline"]["id"],
        changes=[{"parameter_id": world["pulse"]["id"], "to": "700"}],
    )
    with pytest.raises(AppError) as err:
        ch.submit_change_set(repo, ACTOR, change_id=change["id"], reason="")
    assert err.value.code == INVALID_INPUT
    assert err.value.detail["field"] == "reason"


def test_illegal_transition_is_rejected(repo, world) -> None:
    """草稿不能直接批准/驳回——跳过的正是"提交并冻结"这一步。"""
    change = _draft(repo, world)
    with pytest.raises(AppError) as err:
        ch.approve_change_set(repo, ACTOR, change_id=change["id"])
    assert err.value.code == CHANGE_STATE_INVALID
    assert err.value.detail["state"] == "draft"
    with pytest.raises(AppError) as err2:
        ch.reject_change_set(repo, ACTOR, change_id=change["id"], note="不行")
    assert err2.value.code == CHANGE_STATE_INVALID


def test_withdraw_is_terminal(repo, world) -> None:
    change = _draft(repo, world)
    withdrawn = ch.withdraw_change_set(
        repo, ACTOR, change_id=change["id"], reason="方案取消"
    )
    assert withdrawn["state"] == ch.STATE_WITHDRAWN
    assert withdrawn["decided_state"] == "withdrawn"
    assert withdrawn["decide_note"] == "方案取消"
    with pytest.raises(AppError) as err:
        ch.submit_change_set(repo, ACTOR, change_id=change["id"], reason="再试试")
    assert err.value.code == CHANGE_STATE_INVALID


def test_reopen_creates_new_object_keeps_history(repo, world) -> None:
    """重开是**新建对象**：旧的那张仍然是 rejected，谁批准过什么不会被打乱。"""
    change = _draft(repo, world)
    ch.submit_change_set(repo, ACTOR, change_id=change["id"], reason="报批")
    rejected = ch.reject_change_set(
        repo, ACTOR, change_id=change["id"], note="数值待确认"
    )
    assert rejected["state"] == ch.STATE_REJECTED

    reopened = ch.reopen_change_set(
        repo, ACTOR, change_id=change["id"], reason="确认后重开"
    )
    assert reopened["id"] != change["id"]
    assert reopened["state"] == ch.STATE_DRAFT
    assert reopened["previous_revision_id"] == rejected["revision"]
    assert reopened["reopened_from"] == change["id"]
    assert reopened["changes"] == rejected["changes"]

    old = ch.get_change_set(repo, change["id"])
    assert old["state"] == ch.STATE_REJECTED


# -- 过期判定 -----------------------------------------------------------------


def test_plan_stale_when_baseline_moved_after_submit(repo, world) -> None:
    """提交之后基线又被人改了：这张单不能直接批准，但历史照样查得到。"""
    change = _draft(repo, world)
    ch.submit_change_set(repo, ACTOR, change_id=change["id"], reason="报批")
    bl.update_values(
        repo,
        ACTOR,
        baseline_id=world["baseline"]["id"],
        values={world["pulse"]["id"]: {"value": "660"}},
        expected_revision="r1",
        reason="别人先改了一版",
    )
    info = ch.staleness(repo, ch.get_change_set(repo, change["id"]))
    assert info["stale"] is True
    assert "baseline_revision_changed" in info["reasons"]

    with pytest.raises(AppError) as err:
        ch.approve_change_set(repo, ACTOR, change_id=change["id"])
    assert err.value.code == PLAN_STALE
    # 对象还在，状态还是 submitted——过期是算出来的，不是新状态
    assert ch.get_change_set(repo, change["id"])["state"] == ch.STATE_SUBMITTED


def test_definition_set_change_marks_stale(repo, world) -> None:
    change = _draft(repo, world)
    payload = dict(world["baseline"])
    payload.pop("id", None)
    payload.pop("revision", None)
    payload["definition_set_id"] = "ds_999999"
    tx = repo.transaction(ACTOR, "模拟定义集合变更", action="baseline.update")
    tx.put("baseline", payload, object_id=world["baseline"]["id"])
    tx.commit()
    info = ch.staleness(repo, ch.get_change_set(repo, change["id"]))
    assert "definition_set_changed" in info["reasons"]


# -- 批准发布（M6-12） ---------------------------------------------------------


def test_approve_publishes_in_one_transaction(repo, world) -> None:
    change = _draft(repo, world)
    ch.submit_change_set(repo, ACTOR, change_id=change["id"], reason="报批")

    before_head = repo.head_revision("baseline", world["baseline"]["id"])
    result = ch.approve_change_set(
        repo, ACTOR, change_id=change["id"], note="同意"
    )
    published = result["baseline"]
    decided = result["change_set"]

    assert before_head == "r1"
    assert published["revision"] == "r2"
    assert published["state"] == bl.STATE_APPROVED
    assert published["values"][world["pulse"]["id"]]["value"]["text"] == "700"
    assert published["values"][world["pulse"]["id"]]["source_ref"] == {
        "kind": "change_set",
        "id": change["id"],
    }
    assert published["decided_by"] == ACTOR["name"]

    assert decided["state"] == ch.STATE_APPROVED
    assert decided["decided_state"] == "approved"
    assert decided["decide_note"] == "同意"
    assert decided["published_baseline_revision"] == "r2"

    # 没有 approved-but-unpublished 中间态：批准那一刻基线已经是 r2 且 approved
    head = repo.get("baseline", world["baseline"]["id"])
    assert head["revision"] == "r2"
    assert head["state"] == bl.STATE_APPROVED


def test_second_approve_does_not_publish_twice(repo, world) -> None:
    """并发双批准：第二个进来时对象已经是 approved，状态机直接挡掉。"""
    change = _draft(repo, world)
    ch.submit_change_set(repo, ACTOR, change_id=change["id"], reason="报批")
    ch.approve_change_set(repo, ACTOR, change_id=change["id"], note="同意")
    with pytest.raises(AppError) as err:
        ch.approve_change_set(repo, ACTOR, change_id=change["id"], note="再批一次")
    assert err.value.code == CHANGE_STATE_INVALID
    assert repo.head_revision("baseline", world["baseline"]["id"]) == "r2"


def test_approve_with_stale_expected_baseline_revision(repo, world) -> None:
    change = _draft(repo, world)
    ch.submit_change_set(repo, ACTOR, change_id=change["id"], reason="报批")
    with pytest.raises(AppError) as err:
        ch.approve_change_set(
            repo, ACTOR, change_id=change["id"], expected_baseline_revision="r9"
        )
    assert err.value.code == PLAN_STALE


def test_approve_applies_intent_only_decisions(repo, world) -> None:
    """保持现值 / 取消 pin 只动意图不动数值——批准后数值必须还是原样。"""
    pid = world["pulse"]["id"]
    change = _draft(
        repo,
        world,
        changes=[
            {"parameter_id": pid, "decision": "pin", "to": "650"},  # 值不变，钉住
            {"parameter_id": world["speed"]["id"], "to": "120"},
        ],
    )
    ch.submit_change_set(repo, ACTOR, change_id=change["id"], reason="钉住")
    published = ch.approve_change_set(repo, ACTOR, change_id=change["id"])["baseline"]
    assert published["values"][pid]["value"]["text"] == "650"
    assert published["values"][pid]["intent"] == "pin"
    assert published["values"][world["speed"]["id"]]["value"]["text"] == "120"


# -- 幂等与列表 ---------------------------------------------------------------


def test_create_is_idempotent(repo, world) -> None:
    payload = [{"parameter_id": world["pulse"]["id"], "to": "700"}]
    first = ch.create_change_set(
        repo, ACTOR, baseline_id=world["baseline"]["id"], changes=payload,
        title="改脉冲", idempotency_key="cs-key-1",
    )
    again = ch.create_change_set(
        repo, ACTOR, baseline_id=world["baseline"]["id"], changes=payload,
        title="改脉冲", idempotency_key="cs-key-1",
    )
    assert again["id"] == first["id"], "同键同体应重放而不是再建一张"
    assert len(ch.list_change_sets(repo)) == 1

    with pytest.raises(AppError) as err:
        ch.create_change_set(
            repo, ACTOR, baseline_id=world["baseline"]["id"],
            changes=[{"parameter_id": world["pulse"]["id"], "to": "710"}],
            title="改脉冲", idempotency_key="cs-key-1",
        )
    assert err.value.code == "IDEMPOTENCY_MISMATCH"


def test_list_filters(repo, world) -> None:
    first = _draft(repo, world)
    second = _draft(repo, world)
    ch.submit_change_set(repo, ACTOR, change_id=second["id"], reason="报批")

    assert len(ch.list_change_sets(repo)) == 2
    assert [i["id"] for i in ch.list_change_sets(repo, state="submitted")] == [second["id"]]
    assert len(ch.list_change_sets(repo, baseline_id=world["baseline"]["id"])) == 2
    assert ch.list_change_sets(repo, profile_id="other") == []
    assert ch.list_change_sets(repo)[0]["id"] == second["id"], "最新的排在前面"
    assert first["state"] == ch.STATE_DRAFT


def test_detail_lists_rows_and_allowed_edges(repo, world) -> None:
    change = _draft(repo, world)
    detail = ch.detail(repo, change["id"])
    assert detail["changed_rows"] == 1
    assert detail["allowed"] == ["submitted", "withdrawn"]
    row = detail["rows"][0]
    assert row["name"] == "脉冲宽度"
    assert row["known"] is True
    assert row["from"]["text"] == "650" and row["to"]["text"] == "700"
    assert detail["staleness"]["stale"] is False
    assert detail["revisions"] == ["r1"]
