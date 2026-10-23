"""回读核验：覆盖率与新鲜度（M7-10 / INH-1969）。

四条验收逐条对应：

* **缺项不当 0** → `test_missing_parameter_is_not_treated_as_zero`
* **XML 独有字段不宣传全量通过** → `test_unregistered_nodes_limit_the_claim`
* **采集早于导入 → 拒绝 verified** → `test_capture_before_import_is_not_verified`
* **同产物允许多次核验且旧结论留档** → `test_repeated_verification_keeps_old_reports`

外加一条：版本不一致时逐参数比较没有意义，直接给 `version_mismatch`。
"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.errors import INVALID_INPUT, NOT_FOUND, AppError
from app.services import (
    config_artifacts,
    config_catalog,
    config_delivery,
    config_ingest,
    config_verify,
)

from tests.test_config_export import ACTOR, TEMPLATE, _approve, world  # noqa: F401

EARLY = "2026-10-20T09:00:00Z"   # 早于导入
LATE = "2026-10-21T09:00:00Z"    # 晚于导入


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


@pytest.fixture()
def artifact(repo, world):  # noqa: F811
    """脉冲改成 700 后生成的产物（XML 里是 700，模板里是 650）。"""
    approved = _approve(
        repo,
        world["baseline"],
        values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
    )
    return config_artifacts.generate_artifact(
        repo, ACTOR, profile_id="p1", baseline_id=approved["id"]
    )["item"]


def _capture(repo, world, content, *, release_id=None, captured_at=LATE):
    return config_ingest.record_capture(
        repo,
        ACTOR,
        profile_id="p1",
        content=content,
        filename="axis.xml",
        logical_file_id=world["xml_file"]["id"],
        release_id=release_id or world["release"]["id"],
        captured_at=captured_at,
    )


def _same_as_artifact(repo, artifact):
    return config_artifacts.artifact_bytes(repo, artifact["id"])


# -- 通过 -------------------------------------------------------------------


def test_matching_observation_is_verified(repo, world, artifact) -> None:
    observation = _capture(repo, world, _same_as_artifact(repo, artifact))
    report = config_verify.verify(
        repo, ACTOR, artifact_id=artifact["id"], observation_id=observation["id"],
        imported_after=EARLY,
    )
    assert report["conclusion"] == config_verify.CONCLUSION_VERIFIED
    assert report["conclusion_label"] == "核验通过"
    assert report["reasons"] == []
    assert report["counts"]["mismatched"] == 0
    assert report["counts"]["matched"] >= 2
    rows = {row["parameter_id"]: row for row in report["rows"]}
    assert rows[world["pulse"]["id"]]["status"] == "matched"
    assert rows[world["pulse"]["id"]]["observed"]["text"] == "700"
    assert rows[world["pulse"]["id"]]["expected"]["text"] == "700"
    # 溯源三件套都要有
    assert report["capture_time"] == LATE
    assert report["source_fingerprint"] == observation["blob_sha256"]
    assert report["verification_scope"]["release_id"] == world["release"]["id"]
    assert report["artifact_sha256"] == artifact["blob_sha256"]


def test_profile_is_derived_from_the_observation_when_not_given(repo, world, artifact) -> None:
    """不传 profile_id 时，报告必须落在观测所属机型上，否则按机型列表会漏掉它。"""
    observation = _capture(repo, world, _same_as_artifact(repo, artifact))
    report = config_verify.verify(
        repo, ACTOR, artifact_id=artifact["id"], observation_id=observation["id"],
        imported_after=EARLY,
    )
    assert report["profile_id"] == "p1"
    assert report["verification_scope"]["profile_id"] == "p1"
    listed = config_verify.list_verifications(
        repo, artifact_id=artifact["id"], profile_id="p1"
    )
    assert [item["id"] for item in listed] == [report["id"]]


def test_import_time_defaults_to_latest_declared_delivery(repo, world, artifact) -> None:
    """没有显式 imported_after 时，用最近一次“已声明成功”的实际执行时间。"""
    observation = _capture(repo, world, _same_as_artifact(repo, artifact))
    config_delivery.register(
        repo,
        ACTOR,
        artifact_id=artifact["id"],
        result="declared_ok",
        profile_id="p1",
        executed_at="2026-10-22T09:00:00Z",
        evidence={"ref": "photo://late-import"},
    )
    report = config_verify.verify(
        repo, ACTOR, artifact_id=artifact["id"], observation_id=observation["id"]
    )
    assert report["conclusion"] == config_verify.CONCLUSION_INCOMPLETE
    assert "capture_before_import" in report["reasons"]
    assert report["imported_after_source"].startswith("delivery:")


def test_freshness_compares_absolute_time_not_string_order(repo, world, artifact) -> None:
    """`+08:00` 的 20:00 是 UTC 12:00，不能因为字符串更大就被当成更晚。"""
    observation = _capture(
        repo, world, _same_as_artifact(repo, artifact),
        captured_at="2026-10-20T20:00:00+08:00",
    )
    report = config_verify.verify(
        repo, ACTOR, artifact_id=artifact["id"], observation_id=observation["id"],
        imported_after="2026-10-20T18:00:00Z",
    )
    assert report["conclusion"] == config_verify.CONCLUSION_INCOMPLETE
    assert "capture_before_import" in report["reasons"]


def test_incomparable_units_are_unobservable_not_mismatched(repo, world) -> None:
    observation = {
        "mapping": {
            "entries": [
                {
                    "parameter_id": world["pulse"]["id"],
                    "status": config_ingest.ENTRY_MAPPED,
                    "value_ref": {"type": "decimal", "text": "700", "unit": "kg"},
                }
            ]
        }
    }
    rows = config_verify._rows(
        repo,
        {world["pulse"]["id"]: {"value": {"type": "decimal", "text": "700", "unit": "ms"}}},
        observation,
    )
    assert rows[0]["status"] == config_verify.ROW_UNOBSERVABLE


def test_stale_capture_is_never_verified(repo, world, artifact) -> None:
    """旧任务晚到的观测可以留档，但不能拿来证明当前导入成功。"""
    _capture(repo, world, _same_as_artifact(repo, artifact))
    stale_content = _same_as_artifact(repo, artifact).replace(
        b"</R>", b"<!-- stale probe --></R>", 1
    )
    stale = _capture(
        repo, world, stale_content, captured_at=EARLY
    )
    assert stale["stale_capture"] is True
    report = config_verify.verify(
        repo, ACTOR, artifact_id=artifact["id"], observation_id=stale["id"],
        imported_after="2026-10-19T00:00:00Z",
    )
    assert report["conclusion"] == config_verify.CONCLUSION_INCOMPLETE
    assert "stale_source" in report["reasons"]


def test_json_observation_does_not_claim_xml_full_coverage(repo, world, artifact) -> None:
    tx = repo.transaction(ACTOR, "测试用 JSON 观测", action="observation.capture")
    ref = tx.put(
        "observation",
        {
            "scope": "profile",
            "profile_id": "p1",
            "logical_file_id": world["xml_file"]["id"],
            "family_id": world["family"]["id"],
            "role": "field_json",
            "release_ref": {"release_id": world["release"]["id"]},
            "variant_id": artifact.get("variant_id"),
            "device_id": None,
            "blob_sha256": "",
            "format": "json",
            "captured_at": LATE,
            "captured_by": ACTOR["name"],
            "mapping": {
                "entries": [],
                "definition_set_id": artifact.get("definition_set_id"),
            },
            "definition_set_id": artifact.get("definition_set_id"),
            "status": config_ingest.STATUS_PARSED,
            "stale_capture": False,
        },
    )
    tx.commit()
    observation = repo.get("observation", str(ref.id))
    report = config_verify.verify(
        repo, ACTOR, artifact_id=artifact["id"], observation_id=observation["id"],
        imported_after=EARLY,
    )
    assert report["scope_cross_format"] is True
    assert any("XML 独有节点" in text for text in report["warnings"])


def test_unregistered_nodes_limit_the_claim(repo, world, artifact) -> None:
    """模板里有 Name / 注释这些没登记成参数的节点：不许宣传"全量一致"。"""
    report = config_verify.verify(
        repo,
        ACTOR,
        artifact_id=artifact["id"],
        observation_id=_capture(repo, world, _same_as_artifact(repo, artifact))["id"],
        imported_after=EARLY,
    )
    assert report["scope_complete"] is False
    assert report["unregistered_keys"], "未登记节点要列出来"
    assert report["claim"].startswith("只能断言")
    assert any("未纳入本次核验" in text for text in report["warnings"])


# -- 缺项与不一致 -----------------------------------------------------------


def test_missing_parameter_is_not_treated_as_zero(repo, world, artifact) -> None:
    """观测里没有 MaxSpeed：算 missing，不是"等于 0"，也不许通过。"""
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<R xmlns="urn:test:axis">\n'
        '  <PulseWidth unit="ms">700</PulseWidth>\n'
        '  <Axis>\n    <Name>X</Name>\n    <Limit>10</Limit>\n  </Axis>\n'
        '  <Axis>\n    <Name>Y</Name>\n    <Limit>20</Limit>\n  </Axis>\n'
        '</R>\n'
    ).encode("utf-8")
    report = config_verify.verify(
        repo,
        ACTOR,
        artifact_id=artifact["id"],
        observation_id=_capture(repo, world, content)["id"],
        imported_after=EARLY,
    )
    assert report["conclusion"] == config_verify.CONCLUSION_INCOMPLETE
    assert "missing" in report["reasons"]
    missing = [row for row in report["rows"] if row["status"] == "missing"]
    assert [row["parameter_id"] for row in missing] == [world["speed"]["id"]]
    assert missing[0]["observed"] is None, "缺项不能填 0 或空串冒充观测值"
    assert missing[0]["reason"] == "观测文件里没有这个节点"


def test_value_difference_is_mismatch(repo, world, artifact) -> None:
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<R xmlns="urn:test:axis">\n'
        '  <PulseWidth unit="ms">650</PulseWidth>\n'
        '  <MaxSpeed unit="mm/s">100</MaxSpeed>\n'
        '  <Axis>\n    <Name>X</Name>\n    <Limit>10</Limit>\n  </Axis>\n'
        '  <Axis>\n    <Name>Y</Name>\n    <Limit>20</Limit>\n  </Axis>\n'
        '</R>\n'
    ).encode("utf-8")
    report = config_verify.verify(
        repo,
        ACTOR,
        artifact_id=artifact["id"],
        observation_id=_capture(repo, world, content)["id"],
        imported_after=EARLY,
    )
    assert report["conclusion"] == config_verify.CONCLUSION_MISMATCH
    assert report["counts"]["mismatched"] == 1
    row = [row for row in report["rows"] if row["status"] == "mismatched"][0]
    assert row["parameter_id"] == world["pulse"]["id"]
    assert row["expected"]["text"] == "700"
    assert row["observed"]["text"] == "650"


# -- 新鲜度与版本 -----------------------------------------------------------


def test_capture_before_import_is_not_verified(repo, world, artifact) -> None:
    """采集发生在导入之前：哪怕值都对，也证明不了导入后的状态。"""
    observation = _capture(
        repo, world, _same_as_artifact(repo, artifact), captured_at=EARLY
    )
    report = config_verify.verify(
        repo,
        ACTOR,
        artifact_id=artifact["id"],
        observation_id=observation["id"],
        imported_after=LATE,
    )
    assert report["conclusion"] == config_verify.CONCLUSION_INCOMPLETE
    assert "capture_before_import" in report["reasons"]
    assert report["fresh"] is False
    assert report["counts"]["mismatched"] == 0


def test_version_mismatch_short_circuits(repo, world, artifact) -> None:
    other = config_catalog.register_release(
        repo, ACTOR, version_label="2.0.0", build="20260900"
    )
    observation = _capture(
        repo, world, _same_as_artifact(repo, artifact), release_id=other["id"]
    )
    report = config_verify.verify(
        repo,
        ACTOR,
        artifact_id=artifact["id"],
        observation_id=observation["id"],
        imported_after=EARLY,
    )
    assert report["conclusion"] == config_verify.CONCLUSION_VERSION_MISMATCH
    assert "version_mismatch" in report["reasons"]
    assert report["verification_scope"]["observation_release_id"] == other["id"]


# -- 留档 -------------------------------------------------------------------


def test_repeated_verification_keeps_old_reports(repo, world, artifact) -> None:
    first = _capture(repo, world, _same_as_artifact(repo, artifact), captured_at=LATE)
    ok_report = config_verify.verify(
        repo, ACTOR, artifact_id=artifact["id"], observation_id=first["id"],
        imported_after=EARLY,
    )
    broken = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<R xmlns="urn:test:axis">\n'
        '  <PulseWidth unit="ms">650</PulseWidth>\n'
        '  <MaxSpeed unit="mm/s">100</MaxSpeed>\n'
        '  <Axis>\n    <Name>X</Name>\n    <Limit>10</Limit>\n  </Axis>\n'
        '  <Axis>\n    <Name>Y</Name>\n    <Limit>20</Limit>\n  </Axis>\n'
        '</R>\n'
    ).encode("utf-8")
    second = _capture(repo, world, broken, captured_at="2026-10-22T09:00:00Z")
    bad_report = config_verify.verify(
        repo, ACTOR, artifact_id=artifact["id"], observation_id=second["id"],
        imported_after=EARLY,
    )

    assert ok_report["id"] != bad_report["id"]
    reports = config_verify.list_verifications(repo, artifact_id=artifact["id"])
    assert [item["id"] for item in reports] == [bad_report["id"], ok_report["id"]]
    # 旧结论没有被覆盖：它还是"通过"，只是不再是最近一次
    kept = config_verify.get_verification(repo, ok_report["id"])
    assert kept["conclusion"] == config_verify.CONCLUSION_VERIFIED
    assert kept["observation_id"] == first["id"]


# -- 边界 -------------------------------------------------------------------


def test_missing_artifact_is_not_found(repo) -> None:
    with pytest.raises(AppError) as caught:
        config_verify.verify(repo, ACTOR, artifact_id="ar_999999")
    assert caught.value.code == NOT_FOUND


def test_unknown_verification_is_not_found(repo) -> None:
    with pytest.raises(AppError) as caught:
        config_verify.get_verification(repo, "vf_999999")
    assert caught.value.code == NOT_FOUND


def test_observation_of_another_profile_is_rejected(repo, world, artifact) -> None:
    """点名 p1 却拿 p2 的观测来核验：不许把两台机器的数据混在一起。"""
    observation = _capture(repo, world, _same_as_artifact(repo, artifact))
    raw = dict(observation)
    raw.pop("id", None)
    raw.pop("revision", None)
    raw["profile_id"] = "p2"
    tx = repo.transaction(ACTOR, "挪到别的机型")
    ref = tx.put("observation", raw)
    tx.commit()
    with pytest.raises(AppError) as caught:
        config_verify.verify(
            repo,
            ACTOR,
            artifact_id=artifact["id"],
            observation_id=str(ref.id),
            profile_id="p1",
        )
    assert caught.value.code == NOT_FOUND


def test_common_artifact_requires_profile(repo, world) -> None:
    """通用产物没有机型归属时，核验必须点名机型。"""
    common = _approve(
        repo,
        world["baseline"],
        values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
    )
    # 直接改一个"无机型"的产物：验证入口的前置校验，不依赖造数据路径
    artifact = config_artifacts.generate_artifact(
        repo, ACTOR, profile_id="p1", baseline_id=common["id"]
    )["item"]
    assert artifact["profile_id"] is None
    with pytest.raises(AppError) as caught:
        config_verify.verify(repo, ACTOR, artifact_id=artifact["id"])
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["field"] == "profile_id"
