"""追踪策略与有效策略解析（M6-05 / INH-1950）。

卡面验收逐条对应：

* 类别级 + 机型级覆盖，服务端算**有效策略**并回继承来源 →
  `test_model_overrides_family` / `test_effective_reports_inheritance_chain`
* 开关写审计与理由 → `test_reason_is_required` / `test_change_leaves_audit`
* 管理权限与旧 `settings_write` 严格区分 → 见 `tests/test_api_config_tracking.py`
* 关 managed 后历史仍可查 → `test_policy_history_survives_switch_back`
* 待审不能借关开关发布 → `test_guard_never_allows_baseline_from_legacy_routes`
"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.errors import AppError
from app.services import config_catalog, config_gate, config_policies

ACTOR = {"ip": "127.0.0.1", "name": "测试管理员"}


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setenv(config_gate.ENV_KEY, config_gate.MODE_ON)
    yield
    monkeypatch.delenv(config_gate.ENV_KEY, raising=False)


@pytest.fixture()
def world(repo):
    family = config_catalog.create_family(repo, ACTOR, name="轴参数", code="AXIS_PARAM")
    other = config_catalog.create_family(repo, ACTOR, name="IO 参数", code="IO_PARAM")
    xml_file = config_catalog.create_logical_file(
        repo, ACTOR, family_id=family["id"], role="xml_export", display_name="轴参数.xml"
    )
    json_file = config_catalog.create_logical_file(
        repo, ACTOR, family_id=family["id"], role="field_json", display_name="轴参数.json"
    )
    return {
        "family": family,
        "other_family": other,
        "xml_file": xml_file,
        "json_file": json_file,
    }


# -- 策略读写 ---------------------------------------------------------------


def test_default_mode_is_archive_only(repo, world) -> None:
    policy = config_policies.effective_policy(repo, family_id=world["family"]["id"])
    assert policy["mode"] == config_policies.MODE_ARCHIVE_ONLY
    assert policy["source"] == "default"
    assert policy["policy_id"] is None
    assert policy["chain"] == []


def test_reason_is_required(repo, world) -> None:
    with pytest.raises(AppError) as caught:
        config_policies.set_policy(
            repo,
            ACTOR,
            scope="family",
            family_id=world["family"]["id"],
            mode="managed",
            reason="   ",
        )
    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.detail["field"] == "reason"
    assert config_policies.list_policies(repo) == []


def test_invalid_scope_and_mode(repo, world) -> None:
    for kwargs in (
        {"scope": "global", "mode": "managed", "reason": "x"},
        {"scope": "family", "family_id": world["family"]["id"], "mode": "on", "reason": "x"},
        {"scope": "family", "mode": "managed", "reason": "x"},
        {"scope": "model", "mode": "managed", "reason": "x"},
    ):
        with pytest.raises(AppError) as caught:
            config_policies.set_policy(repo, ACTOR, **kwargs)
        assert caught.value.code == "INVALID_INPUT"


def test_model_overrides_family(repo, world) -> None:
    family_id = world["family"]["id"]
    config_policies.set_policy(
        repo,
        ACTOR,
        scope="family",
        family_id=family_id,
        mode="managed",
        reason="轴参数进入受管",
    )
    assert config_policies.effective_policy(repo, family_id=family_id)["source"] == "family"
    assert config_policies.effective_policy(repo, family_id=family_id, profile_id="A")[
        "source"
    ] == "family"

    config_policies.set_policy(
        repo,
        ACTOR,
        scope="model",
        profile_id="A",
        mode="archive_only",
        reason="A 机先保持归档",
    )
    for_a = config_policies.effective_policy(repo, family_id=family_id, profile_id="A")
    assert for_a["mode"] == "archive_only"
    assert for_a["source"] == "model"
    for_b = config_policies.effective_policy(repo, family_id=family_id, profile_id="B")
    assert for_b["mode"] == "managed"
    assert for_b["source"] == "family"
    # 别的类别不受影响
    assert config_policies.effective_policy(repo, family_id=world["other_family"]["id"])[
        "source"
    ] == "default"


def test_model_family_specific_beats_model_wide(repo, world) -> None:
    family_id = world["family"]["id"]
    config_policies.set_policy(
        repo, ACTOR, scope="model", profile_id="A", mode="archive_only", reason="整机保持归档"
    )
    config_policies.set_policy(
        repo,
        ACTOR,
        scope="model",
        profile_id="A",
        family_id=family_id,
        mode="managed",
        reason="但轴参数要受管",
    )
    policy = config_policies.effective_policy(repo, family_id=family_id, profile_id="A")
    assert policy["mode"] == "managed"
    assert policy["source"] == "model+family"
    assert [item["source"] for item in policy["chain"]] == ["model+family", "model"]


def test_effective_reports_inheritance_chain(repo, world) -> None:
    family_id = world["family"]["id"]
    config_policies.set_policy(
        repo, ACTOR, scope="family", family_id=family_id, mode="managed", reason="受管"
    )
    config_policies.set_policy(
        repo, ACTOR, scope="model", profile_id="A", mode="managed", reason="A 机明确受管"
    )
    policy = config_policies.effective_policy(repo, family_id=family_id, profile_id="A")
    assert [item["source"] for item in policy["chain"]] == ["model", "family"]
    assert all(item["mode"] == "managed" for item in policy["chain"])
    assert policy["reason"] == "A 机明确受管"


def test_same_value_does_not_create_revision(repo, world) -> None:
    family_id = world["family"]["id"]
    first = config_policies.set_policy(
        repo, ACTOR, scope="family", family_id=family_id, mode="managed", reason="首次"
    )
    again = config_policies.set_policy(
        repo, ACTOR, scope="family", family_id=family_id, mode="managed", reason="又点了一次"
    )
    assert again["id"] == first["id"]
    assert repo.history("tracking_policy", first["id"]) == ["r1"]


def test_change_leaves_audit(repo, world) -> None:
    family_id = world["family"]["id"]
    first = config_policies.set_policy(
        repo, ACTOR, scope="family", family_id=family_id, mode="managed", reason="开始受管"
    )
    config_policies.set_policy(
        repo,
        ACTOR,
        scope="family",
        family_id=family_id,
        mode="archive_only",
        reason="回退到归档",
    )
    assert repo.history("tracking_policy", first["id"]) == ["r1", "r2"]
    events = repo.audit_events(action="tracking_policy.set")["items"]
    assert [event["reason"] for event in events] == ["回退到归档", "开始受管"]
    assert all(event["actor"]["name"] == "测试管理员" for event in events)


def test_stale_expected_revision_is_conflict(repo, world) -> None:
    family_id = world["family"]["id"]
    first = config_policies.set_policy(
        repo, ACTOR, scope="family", family_id=family_id, mode="managed", reason="v1"
    )
    config_policies.set_policy(
        repo, ACTOR, scope="family", family_id=family_id, mode="archive_only", reason="v2"
    )
    with pytest.raises(AppError) as caught:
        config_policies.set_policy(
            repo,
            ACTOR,
            scope="family",
            family_id=family_id,
            mode="managed",
            reason="v3",
            expected_revision=first["revision"],
        )
    assert caught.value.code == "REVISION_CONFLICT"
    assert caught.value.detail["actual_revision"] == "r2"


def test_policy_history_survives_switch_back(repo, world) -> None:
    """关掉 managed 不会抹掉历史：对象、修订、审计全在。"""
    family_id = world["family"]["id"]
    policy = config_policies.set_policy(
        repo, ACTOR, scope="family", family_id=family_id, mode="managed", reason="受管"
    )
    config_policies.set_policy(
        repo, ACTOR, scope="family", family_id=family_id, mode="archive_only", reason="回退"
    )
    assert config_policies.policy_history(repo, policy["id"]) == ["r1", "r2"]
    assert config_policies.effective_policy(repo, family_id=family_id)["mode"] == "archive_only"
    snapshot = repo.get("tracking_policy", policy["id"], "r1")
    assert snapshot["mode"] == "managed", "旧修订逐字节保留"


# -- 旧入口守卫 -------------------------------------------------------------


def test_guard_is_noop_when_feature_off(repo, world) -> None:
    report = config_policies.guard_legacy_write(
        repo, "upload", filename="随便什么.xml", profile_id="A"
    )
    assert report["allowed"] is True
    assert report["route"] == config_policies.ROUTE_LEGACY
    assert report["policy"]["source"] == "disabled"


def test_archive_only_keeps_old_behaviour(repo, world, enabled) -> None:
    report = config_policies.guard_legacy_write(
        repo, "upload", filename="未登记.xml", profile_id="A"
    )
    assert report["allowed"] is True
    assert report["route"] == config_policies.ROUTE_LEGACY


def test_managed_routes_each_legacy_action(repo, world, enabled) -> None:
    config_policies.set_policy(
        repo,
        ACTOR,
        scope="family",
        family_id=world["family"]["id"],
        mode="managed",
        reason="受管",
    )
    cases = {
        ("upload", "轴参数.xml"): config_policies.ROUTE_CANDIDATE,
        ("import", "轴参数.xml"): config_policies.ROUTE_CANDIDATE,
        ("schedule_download", "轴参数.xml"): config_policies.ROUTE_CANDIDATE,
        ("upload", "轴参数.json"): config_policies.ROUTE_OBSERVATION,
        ("schedule_download", "轴参数.json"): config_policies.ROUTE_OBSERVATION,
        ("review_submit", "轴参数.xml"): config_policies.ROUTE_CHANGE_SOURCE,
        ("delete", "轴参数.xml"): config_policies.ROUTE_REASSOCIATE,
        ("rename", "轴参数.xml"): config_policies.ROUTE_REASSOCIATE,
    }
    for (action, filename), expected in cases.items():
        report = config_policies.guard_legacy_write(
            repo, action, filename=filename, profile_id="A"
        )
        assert report["route"] == expected, (action, filename, report)
        assert report["allowed"] is True
        assert report["family_id"] == world["family"]["id"]


def test_unregistered_file_stays_legacy_when_only_family_managed(repo, world, enabled) -> None:
    """只把某个类别设为受管，不该连累这台机器的无关文件。"""
    config_policies.set_policy(
        repo,
        ACTOR,
        scope="family",
        family_id=world["family"]["id"],
        mode="managed",
        reason="受管",
    )
    report = config_policies.guard_legacy_write(
        repo, "upload", filename="没人认识.xml", profile_id="A"
    )
    assert report["allowed"] is True
    assert report["route"] == config_policies.ROUTE_LEGACY


def test_unregistered_file_is_blocked_under_model_wide_managed(repo, world, enabled) -> None:
    """机型被整体设为受管时，"不知道是什么"就不能落盘。"""
    config_policies.set_policy(
        repo, ACTOR, scope="model", profile_id="A", mode="managed", reason="整机受管"
    )
    with pytest.raises(AppError) as caught:
        config_policies.guard_or_raise(
            repo, "upload", filename="没人认识.xml", profile_id="A"
        )
    assert caught.value.code == "POLICY_BLOCKED"
    assert caught.value.http_status == 403
    assert caught.value.detail["route"] == config_policies.ROUTE_BLOCKED
    assert caught.value.detail["hint"]
    # 其他机型不受影响
    for_b = config_policies.guard_legacy_write(
        repo, "upload", filename="没人认识.xml", profile_id="B"
    )
    assert for_b["allowed"] is True


def test_guard_never_allows_baseline_from_legacy_routes(repo, world, enabled) -> None:
    """核心不变量：任何旧入口的动作都不能以"发布基线"的通道返回。"""
    config_policies.set_policy(
        repo,
        ACTOR,
        scope="family",
        family_id=world["family"]["id"],
        mode="managed",
        reason="受管",
    )
    for action in config_policies.LEGACY_ACTIONS:
        for filename in ("轴参数.xml", "轴参数.json", "没人认识.xml"):
            report = config_policies.guard_legacy_write(
                repo, action, filename=filename, profile_id="A"
            )
            assert config_policies.may_publish_baseline(report["route"]) is False
            assert report["publish_baseline"] is False

    for route in (
        config_policies.ROUTE_LEGACY,
        config_policies.ROUTE_OBSERVATION,
        config_policies.ROUTE_CANDIDATE,
        config_policies.ROUTE_CHANGE_SOURCE,
        config_policies.ROUTE_REASSOCIATE,
        config_policies.ROUTE_BLOCKED,
    ):
        assert config_policies.may_publish_baseline(route) is False


def test_unknown_action_fails_fast(repo) -> None:
    with pytest.raises(ValueError):
        config_policies.guard_or_raise(repo, "no_such_action", filename="x.xml")


def test_overview_lists_effective_per_family_and_model(repo, world, enabled) -> None:
    config_policies.set_policy(
        repo,
        ACTOR,
        scope="family",
        family_id=world["family"]["id"],
        mode="managed",
        reason="受管",
    )
    overview = config_policies.tracking_overview(repo, ["A"])
    assert overview["default_mode"] == "archive_only"
    assert overview["modes"] == ["archive_only", "managed"]
    assert overview["counts"] == {"managed": 1}
    sources = {(item.get("scope"), item.get("family_id")) for item in overview["effective"]}
    assert ("family", world["family"]["id"]) in sources
    assert ("model", world["family"]["id"]) in sources
    by_key = {
        (item.get("scope"), item.get("family_id"), item.get("profile_id")): item
        for item in overview["effective"]
    }
    assert by_key[("model", world["family"]["id"], "A")]["mode"] == "managed"
    assert by_key[("family", world["other_family"]["id"], None)]["source"] == "default"


def test_classify_file_matches_alias(repo, world) -> None:
    config_catalog.rename_logical_file(
        repo, ACTOR, file_id=world["xml_file"]["id"], new_name="轴参数表.xml"
    )
    found = config_policies.classify_file(repo, "轴参数.xml")
    assert found is not None
    assert found["logical_file"]["id"] == world["xml_file"]["id"]
    assert found["role"] == "xml_export"
    assert config_policies.classify_file(repo, "不存在的.xml") is None
