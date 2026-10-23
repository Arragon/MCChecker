"""配置审计检索与来源谱系（M7-11 / INH-1970）。"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.errors import INVALID_INPUT, AppError
from app.services import config_artifacts, config_audit, config_catalog, config_delivery

from tests.test_config_export import ACTOR, _approve, world  # noqa: F401


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


def _publish_world(repo, world):  # noqa: F811
    approved = _approve(
        repo,
        world["baseline"],
        values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
    )
    artifact = config_artifacts.generate_artifact(
        repo, ACTOR, profile_id="p1", baseline_id=approved["id"]
    )["item"]
    config_delivery.register(
        repo, ACTOR, artifact_id=artifact["id"], result="declared_ok",
        profile_id="p1", executed_at="2026-10-22T09:00:00Z",
        evidence={"ref": "photo://1"},
    )
    return approved, artifact


def test_query_filters_across_object_dimensions(repo, world) -> None:  # noqa: F811
    approved, artifact = _publish_world(repo, world)

    assert config_audit.query(repo, action="baseline.approve")["items"]
    assert all(
        item["action"] == "baseline.approve"
        for item in config_audit.query(repo, action="baseline.approve")["items"]
    )
    assert config_audit.query(repo, category="artifact")["items"]
    assert config_audit.query(repo, object_id=approved["id"])["items"]
    assert config_audit.query(repo, object_kind="artifact")["items"]
    assert config_audit.query(repo, parameter_id=world["pulse"]["id"])["items"]
    assert config_audit.query(repo, family_id=world["family"]["id"])["items"]
    assert config_audit.query(repo, release_id=world["release"]["id"])["items"]
    assert config_audit.query(repo, actor="管理员丁")["items"]
    assert config_audit.query(repo, reason="测试用批准")["items"]
    assert config_audit.query(repo, since="2000-01-01T00:00:00Z")["items"]
    assert config_audit.query(repo, until="2000-01-01T00:00:00Z")["items"] == []
    assert config_audit.query(repo, object_id=artifact["id"])["items"]

    # 机型过滤：p1 的交付登记不能算到 p2；通用层事件由 include_common 显式决定
    p1_events = config_audit.query(
        repo, profile_id="p1", include_common=False
    )["items"]
    assert any(item["action"] == "delivery.register" for item in p1_events)
    p2_events = config_audit.query(
        repo, profile_id="p2", include_common=False
    )["items"]
    assert all(item["action"] != "delivery.register" for item in p2_events)


def test_cursor_is_stable_and_bound_to_filters(repo, world) -> None:  # noqa: F811
    _publish_world(repo, world)
    first = config_audit.query(repo, limit=2)
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    second = config_audit.query(repo, limit=2, cursor=first["next_cursor"])
    assert all(
        left["seq"] != right["seq"]
        for left in first["items"]
        for right in second["items"]
    )
    with pytest.raises(AppError) as caught:
        config_audit.query(repo, limit=2, cursor="not-a-cursor")
    assert caught.value.code == INVALID_INPUT
    with pytest.raises(AppError) as caught:
        config_audit.query(repo, action="artifact.generate", cursor=first["next_cursor"])
    assert caught.value.code == INVALID_INPUT


def test_retired_objects_stay_queryable(repo, world) -> None:  # noqa: F811
    config_catalog.set_parameter_status(
        repo, ACTOR, parameter_id=world["pulse"]["id"], status="retired",
        reason="测试退役",
    )
    retired = config_audit.query(
        repo, action="parameter.retire", include_retired=True
    )["items"]
    assert retired and retired[0]["retired"] is True
    assert config_audit.query(
        repo, action="parameter.retire", include_retired=False
    )["items"] == []
    timeline = config_audit.timeline(repo, world["pulse"]["id"])
    assert timeline["object"]["status"] == "retired"
    assert any(item["action"] == "parameter.retire" for item in timeline["events"])


def test_timeline_explains_parameter_to_artifact_chain(repo, world) -> None:  # noqa: F811
    approved, artifact = _publish_world(repo, world)
    timeline = config_audit.timeline(repo, artifact["id"])
    assert timeline["object_kind"] == "artifact"
    assert timeline["revisions"]
    assert any(item["action"] == "artifact.generate" for item in timeline["events"])
    related = timeline["provenance"]["related"]
    assert related["baseline"][0]["id"] == approved["id"]
    assert related.get("delivery"), "产物要能反查到交付登记"
    assert related.get("parameter"), "产物要能反查到被改的参数"

    parameter_line = config_audit.timeline(repo, world["pulse"]["id"])
    related = parameter_line["provenance"]["related"]
    assert related.get("baseline")
    assert related.get("artifact")
    assert parameter_line["object"]["name"] == "脉冲宽度"


def test_unknown_object_is_not_found(repo) -> None:
    with pytest.raises(AppError) as caught:
        config_audit.timeline(repo, "bl_999999")
    assert caught.value.code == "NOT_FOUND"
    with pytest.raises(AppError) as caught:
        config_audit.timeline(repo, "nonsense")
    assert caught.value.code == INVALID_INPUT
