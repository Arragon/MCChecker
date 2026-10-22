"""机型跨版本升级 / 同版本刷新（M7-03 / INH-1962）。

卡面验收：

* A 引旧 r2、B 引 r4 时各用自身父修订 —— `test_each_model_uses_its_own_parent_revision`
* 通用 r5 更新后机型头不漂移，显式刷新才发 r+1 ——
  `test_common_bump_does_not_touch_model` / `test_rebase_is_explicit_only`

这一卡真正的难点是"共同基准取哪一版"。只存 `upstream_ref`（id）是不够的：
通用层一发新修订，`get_baseline(id)` 就返回新的 head，三方合并会退化成
"base == theirs"，结论变成"什么都没变"。所以机型基线必须记 `upstream_revision`。
"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.errors import INVALID_INPUT, AppError
from app.services import config_baselines as bl, config_upgrade
import upgrade_fixtures as fx


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


@pytest.fixture()
def world(repo):
    return fx.build(repo)


def _model(repo, world, pid, *, upstream, values=None):
    return fx.approve_model(repo, world, profile_id=pid, upstream=upstream, values=values)


def _approved_new_common(repo, world, *, decisions=None):
    """做完通用层升版并批准，供机型升版用。

    冲突项默认取 `use_new`（新出口默认），这样"新通用"就是干净的一版；
    需要别的组合时用 `decisions` 覆盖。
    """
    resolved = dict(decisions or {})
    plan = config_upgrade.plan_common_upgrade(repo, release_id=world["new"]["release"]["id"])
    for parameter_id in plan["needs_decision"]:
        resolved.setdefault(parameter_id, "use_new")
    result = config_upgrade.create_common_upgrade_baseline(
        repo, fx.ACTOR, release_id=world["new"]["release"]["id"], decisions=resolved
    )
    return fx.push(repo, result["item"], state=bl.STATE_APPROVED, decided_by=fx.ACTOR["name"])


# -- 共同基准必须是"当初引用的那一版" ---------------------------------------


def test_each_model_uses_its_own_parent_revision(repo, world) -> None:
    """A 引 r2、B 引 r4：两条机型基线各用自己那份父修订做共同基准。"""
    # 夹具里的"批准"是一次追加修订：建草稿 r1 → 批准 r2，所以已批准通用基线是 **r2**
    common = fx.approve_common(repo, world)
    assert common["revision"] == "r2"
    first_parent = common["revision"]
    model_a = _model(repo, world, "p1", upstream=common)

    # 通用层继续演进：r2 改脉冲、r3 改速度、r4 再改脉冲
    common = fx.push(
        repo,
        common,
        values={
            **common["values"],
            world["pulse"]["id"]: {
                "value": {"type": "decimal", "text": "800", "unit": "ms"},
                "intent": "override",
                "source_ref": {"kind": "baseline", "id": common["id"]},
                "reason": "",
            },
        },
    )
    common = fx.push(
        repo,
        common,
        values={
            **common["values"],
            world["speed"]["id"]: {
                "value": {"type": "decimal", "text": "150", "unit": "mm/s"},
                "intent": "override",
                "source_ref": {"kind": "baseline", "id": common["id"]},
                "reason": "",
            },
        },
    )
    model_b = _model(repo, world, "p2", upstream=common)

    assert model_a["upstream_revision"] == first_parent == "r2"
    assert model_b["upstream_revision"] == common["revision"] == "r4"

    new_common = _approved_new_common(repo, world)

    plan_a = config_upgrade.plan_model_upgrade(
        repo, profile_id="p1", release_id=world["new"]["release"]["id"]
    )
    plan_b = config_upgrade.plan_model_upgrade(
        repo, profile_id="p2", release_id=world["new"]["release"]["id"]
    )
    assert plan_a["previous"]["upstream_revision"] == "r2"
    assert plan_b["previous"]["upstream_revision"] == "r4"
    # 两条计划取的是**不同**的父值，说明没有偷懒用 head
    assert plan_a["sources"]["base"]["revision"] != plan_b["sources"]["base"]["revision"]
    assert plan_a["sources"]["theirs"]["baseline_id"] == new_common["id"]
    assert plan_b["sources"]["theirs"]["baseline_id"] == new_common["id"]


def test_model_upgrade_detects_local_override(repo, world) -> None:
    common = fx.approve_common(repo, world)
    model = _model(
        repo,
        world,
        "p1",
        upstream=common,
        values={world["pulse"]["id"]: {"value": "950", "intent": "override"}},
    )
    assert model["values"][world["pulse"]["id"]]["value"]["text"] == "950"

    _approved_new_common(repo, world)
    plan = config_upgrade.plan_model_upgrade(
        repo, profile_id="p1", release_id=world["new"]["release"]["id"]
    )
    items = {item["parameter_id"]: item for item in plan["items"]}
    pulse = items[world["pulse"]["id"]]
    # 本地 950、旧通用 650、新通用 700 → 两边都改过
    assert pulse["action"] == "conflict", pulse
    assert pulse["ours"]["value"]["text"] == "950"
    assert pulse["base"]["value"]["text"] == "650"
    assert pulse["theirs"]["value"]["text"] == "700"

    speed = items[world["speed"]["id"]]
    assert speed["action"] == "from_new"


def test_target_common_must_be_approved(repo, world) -> None:
    common = fx.approve_common(repo, world)
    _model(repo, world, "p1", upstream=common)
    # 只建草稿，不批准
    config_upgrade.create_common_upgrade_baseline(
        repo, fx.ACTOR, release_id=world["new"]["release"]["id"]
    )
    with pytest.raises(AppError) as caught:
        config_upgrade.plan_model_upgrade(
            repo, profile_id="p1", release_id=world["new"]["release"]["id"]
        )
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["field"] == "release_id"


def test_model_without_baseline_is_rejected(repo, world) -> None:
    _approved_new_common(repo, world)
    with pytest.raises(AppError) as caught:
        config_upgrade.plan_model_upgrade(
            repo, profile_id="p1", release_id=world["new"]["release"]["id"]
        )
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["field"] == "profile_id"


# -- 不自动传播 -------------------------------------------------------------


def test_common_bump_does_not_touch_model(repo, world) -> None:
    """通用层出新修订，机型基线一个字节都不动——这是"头不漂移"的可验证形式。"""
    common = fx.approve_common(repo, world)
    model = _model(repo, world, "p1", upstream=common)
    before = bl.get_baseline(repo, model["id"])

    bumped = fx.push(
        repo,
        common,
        values={
            **common["values"],
            world["pulse"]["id"]: {
                "value": {"type": "decimal", "text": "777", "unit": "ms"},
                "intent": "override",
                "source_ref": {"kind": "baseline", "id": common["id"]},
                "reason": "",
            },
        },
    )
    assert bumped["revision"] != common["revision"]

    after = bl.get_baseline(repo, model["id"])
    assert after["revision"] == before["revision"]
    assert after["values"] == before["values"]
    assert after["upstream_revision"] == before["upstream_revision"]
    assert repo.history("baseline", model["id"]) == ["r1", "r2"]


# -- 同版本刷新：显式才发 r+1 -----------------------------------------------


def test_rebase_without_new_revision_is_noop(repo, world) -> None:
    common = fx.approve_common(repo, world)
    _model(repo, world, "p1", upstream=common)
    plan = config_upgrade.plan_model_rebase(repo, profile_id="p1")
    assert plan["mode"] == "model_rebase"
    assert plan["noop"] is True
    assert any("没有新修订" in text for text in plan["warnings"])


def test_rebase_is_explicit_only(repo, world) -> None:
    """通用层出了 r2：计划能看见变化，但**不调用就不产生任何基线**。"""
    common = fx.approve_common(repo, world)
    model = _model(repo, world, "p1", upstream=common)
    fx.push(
        repo,
        common,
        values={
            **common["values"],
            world["pulse"]["id"]: {
                "value": {"type": "decimal", "text": "820", "unit": "ms"},
                "intent": "override",
                "source_ref": {"kind": "baseline", "id": common["id"]},
                "reason": "",
            },
        },
    )
    # 没有调用 → 机型还是只有一条
    assert len(bl.list_baselines(repo, scope="model", profile_id="p1")) == 1
    assert bl.get_baseline(repo, model["id"])["revision"] == model["revision"]

    plan = config_upgrade.plan_model_rebase(repo, profile_id="p1")
    assert plan["noop"] is False
    items = {item["parameter_id"]: item for item in plan["items"]}
    # 本地 650 未改、上游 820：跟随上游
    assert items[world["pulse"]["id"]]["action"] == "from_new"
    assert items[world["pulse"]["id"]]["candidate"]["text"] == "820"

    result = config_upgrade.create_model_rebase_baseline(repo, fx.ACTOR, profile_id="p1")
    item = result["item"]
    assert item["state"] == "draft"
    assert item["upstream_revision"] == bl.get_baseline(repo, common["id"])["revision"]
    assert item["upstream_revision"] != common["revision"], "上游确实出了新修订"
    assert item["values"][world["pulse"]["id"]]["value"]["text"] == "820"
    # 同版本、同上游对象，谱系接到旧机型基线后面
    assert item["release_id"] == model["release_id"]
    assert item["previous_revision_id"] == model["id"]
    assert bl.lineage(repo, item) == [model["id"], item["id"]]


def test_rebase_keeps_local_override(repo, world) -> None:
    common = fx.approve_common(repo, world)
    _model(
        repo,
        world,
        "p1",
        upstream=common,
        values={world["speed"]["id"]: {"value": "180", "intent": "pin"}},
    )
    fx.push(
        repo,
        common,
        values={
            **common["values"],
            world["speed"]["id"]: {
                "value": {"type": "decimal", "text": "130", "unit": "mm/s"},
                "intent": "override",
                "source_ref": {"kind": "baseline", "id": common["id"]},
                "reason": "",
            },
        },
    )
    plan = config_upgrade.plan_model_rebase(repo, profile_id="p1")
    items = {item["parameter_id"]: item for item in plan["items"]}
    speed = items[world["speed"]["id"]]
    # 本地是 pin（意图变了，视为已改动），上游也改了 → 必须人确认
    assert speed["action"] == "conflict", speed
    assert "pin_needs_confirm" in speed["reasons"]

    result = config_upgrade.create_model_rebase_baseline(
        repo, fx.ACTOR, profile_id="p1", decisions={world["speed"]["id"]: "pin"}
    )
    assert result["item"]["values"][world["speed"]["id"]]["value"]["text"] == "180"
    assert result["item"]["values"][world["speed"]["id"]]["intent"] == "pin"


# -- 落地：升版到新版本 -----------------------------------------------------


def test_model_upgrade_creates_draft_with_provenance(repo, world) -> None:
    common = fx.approve_common(repo, world)
    model = _model(repo, world, "p1", upstream=common)
    new_common = _approved_new_common(repo, world)

    result = config_upgrade.create_model_upgrade_baseline(
        repo, fx.ACTOR, profile_id="p1", release_id=world["new"]["release"]["id"]
    )
    item = result["item"]
    assert item["scope"] == "model"
    assert item["profile_id"] == "p1"
    assert item["release_id"] == world["new"]["release"]["id"]
    assert item["upstream_ref"] == new_common["id"]
    assert item["upstream_revision"] == new_common["revision"]
    assert item["previous_revision_id"] == model["id"]
    assert item["values"][world["speed"]["id"]]["value"]["text"] == "120"
    assert item["upgrade_provenance"]["mode"] == "model_release_upgrade"
    assert item["upgrade_provenance"]["ours"]["baseline_id"] == model["id"]


def test_model_upgrade_is_stale_when_parent_changed(repo, world) -> None:
    common = fx.approve_common(repo, world)
    model = _model(repo, world, "p1", upstream=common)
    _approved_new_common(repo, world)

    with pytest.raises(AppError) as caught:
        config_upgrade.create_model_upgrade_baseline(
            repo,
            fx.ACTOR,
            profile_id="p1",
            release_id=world["new"]["release"]["id"],
            expected={"model_revision": "r9"},
        )
    assert caught.value.code == "PLAN_STALE"
    assert caught.value.detail["reasons"] == ["model_revision"]
    assert caught.value.detail["drift"]["model_revision"]["actual"] == model["revision"]

    result = config_upgrade.create_model_upgrade_baseline(
        repo,
        fx.ACTOR,
        profile_id="p1",
        release_id=world["new"]["release"]["id"],
        expected={"model_revision": model["revision"], "parent_revision": common["revision"]},
    )
    assert result["item"]["previous_revision_id"] == model["id"]
