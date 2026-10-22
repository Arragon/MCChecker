"""三方合并纯算法（M7-01 / INH-1960）。

卡面验收逐条对应：

* L 与 O 等值但 pin → 提示决定 —— `test_pin_forces_a_decision`
* `650ms` ↔ `0.8s` 先转换并给证据 —— `test_unit_conversion_gives_evidence`
* 参数拆分/合并、目标已删除却 override、未知参数 → 人工处理 ——
  `test_split_or_merge_is_manual` / `test_retired_target_with_override_is_manual` /
  `test_unknown_definition_is_manual`
* 表驱动正反例 —— `test_action_matrix`

三条不变量也在这里钉住：**纯函数**（不碰仓储/磁盘）、**不发布**（`publishable=False`）、
**不猜**（任何不确定都落到 `manual`，绝不自己选一个值写下去）。
"""

from __future__ import annotations

import pytest

from app.core import config_merge
from app.core.errors import INVALID_INPUT, AppError


def dec(text, unit=None):
    return {"type": "decimal", "text": str(text), "unit": unit}


def entry(text, unit=None, intent="override"):
    return {"value": dec(text, unit), "intent": intent}


def inherit(text, unit=None):
    return {"value": dec(text, unit), "intent": "inherit"}


P = "pm_000001"


# -- 表驱动：动作矩阵 -------------------------------------------------------


MATRIX = [
    (
        "三方全同：不用人动手",
        {P: inherit("650", "ms")},
        {P: inherit("650", "ms")},
        {P: inherit("650", "ms")},
        "keep",
        None,
        "all_three_equal",
    ),
    (
        "本地没动：跟随上游",
        {P: inherit("650", "ms")},
        {P: inherit("650", "ms")},
        {P: inherit("700", "ms")},
        "from_new",
        "use_new",
        "ours_unchanged",
    ),
    (
        "上游没动：保留本地 override",
        {P: inherit("650", "ms")},
        {P: entry("700", "ms")},
        {P: inherit("650", "ms")},
        "keep",
        None,
        "theirs_unchanged",
    ),
    (
        "两边改到同一处：结论一致，不必裁决",
        {P: inherit("650", "ms")},
        {P: entry("700", "ms")},
        {P: entry("700", "ms")},
        "keep",
        None,
        "both_sides_agree",
    ),
    (
        "两边改得不一样：冲突",
        {P: inherit("650", "ms")},
        {P: entry("700", "ms")},
        {P: inherit("800", "ms")},
        "conflict",
        None,
        "both_sides_changed",
    ),
    (
        "上游新增参数：直接取新值",
        {},
        {},
        {P: inherit("120", "ms")},
        "from_new",
        "use_new",
        "new_in_target",
    ),
    (
        "本地没有、上游有（旧版本没这个参数）：取新值",
        {P: inherit("650", "ms")},
        {},
        {P: inherit("700", "ms")},
        "from_new",
        "use_new",
        "not_present_locally",
    ),
    (
        "本地没改、上游删了：跟着丢（丢的不是定制）",
        {P: inherit("650", "ms")},
        {P: inherit("650", "ms")},
        {},
        "drop",
        None,
        "removed_upstream",
    ),
]


@pytest.mark.parametrize("name,base,ours,theirs,action,decision,reason", MATRIX)
def test_action_matrix(name, base, ours, theirs, action, decision, reason) -> None:
    result = config_merge.merge(base=base, ours=ours, theirs=theirs)
    assert result["total"] == 1, result
    item = result["items"][0]
    assert item["action"] == action, (name, item)
    assert item["decision"] == decision, (name, item)
    assert reason in item["reasons"], (name, item["reasons"])
    assert item["needs_decision"] is (action == "conflict")
    # 计划永远不发布
    assert result["publishable"] is False
    assert result["can_apply"] == (action in ("keep", "from_new", "drop"))


def test_summary_counts_every_action() -> None:
    result = config_merge.merge(
        base={"pm_a": inherit("1"), "pm_b": inherit("2"), "pm_c": inherit("3")},
        ours={"pm_a": inherit("1"), "pm_b": entry("9"), "pm_c": entry("3")},
        theirs={"pm_a": entry("5"), "pm_b": entry("8"), "pm_c": entry("3")},
    )
    assert result["total"] == 3
    assert result["summary"]["from_new"] == 1  # a：本地没动
    assert result["summary"]["conflict"] == 1  # b：两边都改
    assert result["summary"]["keep"] == 1  # c：两边同意
    assert result["needs_decision"] == ["pm_b"]
    assert result["can_apply"] is False


# -- pin 必须让人确认 -------------------------------------------------------


def test_pin_forces_a_decision() -> None:
    """L 与 O **等值**但 pin：这不是"没改"，升级时必须让人确认还钉不钉。"""
    result = config_merge.merge(
        base={P: inherit("650", "ms")},
        ours={P: {"value": dec("650", "ms"), "intent": "pin"}},
        theirs={P: inherit("700", "ms")},
    )
    item = result["items"][0]
    assert item["action"] == "conflict", item
    assert item["needs_decision"] is True
    assert "pin_needs_confirm" in item["reasons"]
    # 值一个都没变，光意图变了 —— 只比数值的实现会把它判成 keep，然后静默覆盖 pin
    assert item["ours"]["value"] == item["base"]["value"]
    assert "pin" in item["allowed"] and "clear_pin" in item["allowed"]
    assert result["can_apply"] is False


def test_pin_survives_when_upstream_did_not_change() -> None:
    """上游没动时 pin 原样保留，不制造无谓的人工项。

    这是对卡面那句"等值但 pin 要提示决定"的**边界解读**：真正危险的是"上游改了、
    而本地 pin 会被静默覆盖"；上游一行没改时，pin 不会被谁覆盖，逼人确认只是噪声。
    """
    result = config_merge.merge(
        base={P: inherit("650", "ms")},
        ours={P: {"value": dec("650", "ms"), "intent": "pin"}},
        theirs={P: inherit("650", "ms")},
    )
    item = result["items"][0]
    assert item["action"] == "keep"
    assert item["reasons"] == ["theirs_unchanged"]
    assert item["candidate"]["text"] == "650"


# -- 单位换算与证据 ---------------------------------------------------------


def test_unit_conversion_gives_evidence() -> None:
    """`650ms` 与 `0.8s`：先换算再比，且把换算过程记成证据。"""
    result = config_merge.merge(
        base={P: inherit("650", "ms")},
        ours={P: entry("650", "ms")},
        theirs={P: inherit("0.8", "s")},
        theirs_definitions={P: {"unit": "s", "type": "decimal"}},
    )
    item = result["items"][0]
    assert item["action"] == "conflict", item
    assert "both_sides_changed" in item["reasons"]
    notes = [note for note in result["evidence"] if note["kind"] == "unit_conversion"]
    assert notes, result["evidence"]
    ours_note = [note for note in notes if note["pair"] == "ours_vs_theirs"][0]
    assert ours_note["parameter_id"] == P
    assert ours_note["converted"]["text"] == "0.65"
    assert ours_note["converted"]["unit"] == "s"
    assert ours_note["note"] == "650 ms = 0.65 s"
    # 候选值要换到**目标定义**的单位（这里目标是 s）
    assert item["candidate"]["unit"] == "s"
    assert item["candidate"]["text"] == "0.65"


def test_equal_after_conversion_is_not_a_conflict() -> None:
    """`650ms` 与 `0.65s` 是同一物理量：换算后相等就不该报冲突。"""
    result = config_merge.merge(
        base={P: inherit("650", "ms")},
        ours={P: entry("650", "ms")},
        theirs={P: inherit("0.65", "s")},
    )
    item = result["items"][0]
    assert item["action"] == "keep", item
    assert item["needs_decision"] is False
    # 上游这侧"没变"（0.65s == 650ms），所以走的是"保留本地"，不是冲突
    assert "theirs_unchanged" in item["reasons"], item["reasons"]
    # 证据仍然要给：结论一样不代表过程不用复核
    assert any(note["kind"] == "unit_conversion" for note in result["evidence"])


def test_incomparable_units_are_manual() -> None:
    """`mm/s` 不在换算表里：不能判等，也不能猜，交给人。"""
    result = config_merge.merge(
        base={P: inherit("100", "mm/s")},
        ours={P: inherit("100", "mm/s")},
        theirs={P: inherit("100", "rpm")},
    )
    item = result["items"][0]
    assert item["action"] == "manual", item
    assert item["blockers"][0]["code"] == "incomparable"
    assert item["candidate"] is None


def test_conversion_into_target_unit_happens_on_take_new() -> None:
    """本地没动、上游改了单位：候选值必须已经换成目标单位。"""
    result = config_merge.merge(
        base={P: inherit("650", "ms")},
        ours={P: inherit("650", "ms")},
        theirs={P: inherit("0.7", "s")},
        theirs_definitions={P: {"unit": "s", "type": "decimal"}},
    )
    item = result["items"][0]
    assert item["action"] == "from_new"
    assert item["candidate"]["unit"] == "s"
    assert item["candidate"]["text"] == "0.7"


# -- 人工处理的三类来源 -----------------------------------------------------


def test_split_or_merge_is_manual() -> None:
    """参数被拆分/合并：旧 ID 在目标定义集合里没了，本地定制没有归属。"""
    result = config_merge.merge(
        base={P: inherit("650", "ms")},
        ours={P: entry("700", "ms")},
        theirs={},  # 新版拆成了两个新参数，旧 ID 不在了
        ours_definitions={P: {"unit": "ms", "xml_loc": "/R[1]/PulseWidth[1]"}},
        theirs_definitions={},
    )
    item = result["items"][0]
    assert item["action"] == "manual", item
    assert item["blockers"][0]["code"] == "unknown_parameter"
    assert item["blockers"][0]["detail"]["intent"] == "override"
    assert result["manual"] == [P]
    assert result["can_apply"] is False


def test_retired_target_with_override_is_manual() -> None:
    """本地 override 了，但目标版本里这个参数已退役：不能继续写。"""
    result = config_merge.merge(
        base={P: inherit("650", "ms")},
        ours={P: entry("700", "ms")},
        theirs={},
        ours_definitions={P: {"unit": "ms", "xml_loc": "/R[1]/P[1]"}},
        theirs_definitions={P: {"unit": "ms", "retired": True}},
    )
    item = result["items"][0]
    assert item["action"] == "manual"
    assert item["blockers"][0]["code"] == "retired_target"


def test_retired_target_without_local_change_is_dropped() -> None:
    result = config_merge.merge(
        base={P: inherit("650", "ms")},
        ours={P: inherit("650", "ms")},
        theirs={},
        ours_definitions={P: {"unit": "ms"}},
        theirs_definitions={P: {"unit": "ms", "retired": True}},
    )
    item = result["items"][0]
    assert item["action"] == "drop"
    assert item["blockers"] == []


def test_unknown_definition_is_manual() -> None:
    """本地有值但定义集合里查不到定义：来源不明，不猜类型。"""
    result = config_merge.merge(
        base={},
        ours={P: entry("700")},
        theirs={P: inherit("700")},
        ours_definitions={},
        theirs_definitions={},
    )
    item = result["items"][0]
    assert item["action"] == "manual"
    assert item["blockers"][0]["code"] == "missing_definition"


def test_location_moved_is_manual() -> None:
    """落点从元素搬到属性：值能对上，但直接写会写错地方。"""
    result = config_merge.merge(
        base={P: inherit("650", "ms")},
        ours={P: inherit("650", "ms")},
        theirs={P: inherit("700", "ms")},
        ours_definitions={P: {"unit": "ms", "xml_loc": "/R[1]/PulseWidth[1]"}},
        theirs_definitions={P: {"unit": "ms", "xml_loc": "/R[1]/PulseWidth[1]@value"}},
    )
    item = result["items"][0]
    assert item["action"] == "manual"
    assert item["blockers"][0]["code"] == "location_moved"
    assert item["blockers"][0]["detail"]["to_loc"].endswith("@value")


def test_out_of_range_after_conversion_is_manual() -> None:
    """新版收紧了范围：旧值换算后越界，必须在计划阶段暴露。"""
    result = config_merge.merge(
        base={P: inherit("650", "ms")},
        ours={P: inherit("1500", "ms")},
        theirs={P: inherit("300", "ms")},
        theirs_definitions={P: {"unit": "ms", "constraints": {"min": 0, "max": 1000}}},
    )
    item = result["items"][0]
    assert item["action"] == "manual", item
    assert item["blockers"][0]["code"] == "out_of_range"


# -- 纯函数与契约 -----------------------------------------------------------


def test_merge_does_not_touch_its_inputs() -> None:
    base = {P: inherit("650", "ms")}
    ours = {P: entry("700", "ms")}
    theirs = {P: inherit("800", "ms")}
    snapshot = (dict(base), dict(ours), dict(theirs))
    config_merge.merge(base=base, ours=ours, theirs=theirs)
    assert (base, ours, theirs) == snapshot


def test_empty_inputs_are_allowed() -> None:
    result = config_merge.merge(base=None, ours=None, theirs=None)
    assert result["items"] == []
    assert result["total"] == 0
    assert result["can_apply"] is True
    assert result["publishable"] is False


def test_bad_value_shape_is_rejected() -> None:
    with pytest.raises(AppError) as caught:
        config_merge.merge(
            base={}, ours={P: {"value": {"type": "decimal"}}}, theirs={}
        )
    assert caught.value.code == INVALID_INPUT


def test_order_is_stable_and_sorted() -> None:
    result = config_merge.merge(
        base={"pm_b": inherit("1"), "pm_a": inherit("2")},
        ours={"pm_c": entry("3")},
        theirs={},
    )
    assert [item["parameter_id"] for item in result["items"]] == ["pm_a", "pm_b", "pm_c"]


def test_blocking_ids_lists_manual_items_in_order() -> None:
    result = config_merge.merge(
        base={"pm_a": inherit("1", "ms"), "pm_b": inherit("2", "ms")},
        ours={"pm_a": inherit("1", "ms"), "pm_b": inherit("2", "ms")},
        theirs={"pm_a": inherit("1", "rpm"), "pm_b": inherit("2", "deg")},
    )
    assert config_merge.blocking_ids(result) == ["pm_a", "pm_b"]


# -- 计划 → 候选行 ----------------------------------------------------------


def test_draft_changes_only_carries_explicit_decisions() -> None:
    result = config_merge.merge(
        base={"pm_a": inherit("1"), "pm_b": inherit("2")},
        ours={"pm_a": inherit("1"), "pm_b": entry("9")},
        theirs={"pm_a": entry("5"), "pm_b": entry("8")},
    )
    rows = config_merge.draft_changes(result)
    # a 自动取新值；b 是冲突且没人决议 → 不进候选
    assert [(row["parameter_id"], row["decision"]) for row in rows] == [("pm_a", "use_new")]
    assert rows[0]["value"]["text"] == "5"

    # 人工把 b 判成"保持现值"后才进候选
    rows = config_merge.draft_changes(result, {"pm_b": "inherit_old"})
    assert [(row["parameter_id"], row["decision"]) for row in rows] == [
        ("pm_a", "use_new"),
        ("pm_b", "inherit_old"),
    ]
    assert rows[1]["value"]["text"] == "9"


def test_draft_changes_skips_manual_items() -> None:
    result = config_merge.merge(
        base={P: inherit("650", "ms")},
        ours={P: entry("700", "ms")},
        theirs={},
        ours_definitions={P: {"unit": "ms"}},
        theirs_definitions={},
    )
    assert config_merge.draft_changes(result, {P: "use_new"}) == []
