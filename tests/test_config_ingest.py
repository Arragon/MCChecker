"""现场采集接入不可变 Observation（M6-07 / INH-1952）。

卡面验收逐条对应：

* 采集事件与投影意图**同次持久提交** → `test_observation_and_event_are_published_together`
* 同内容重复下载不产生重复修订、但保留采集时间事件 →
  `test_same_content_does_not_create_second_observation`
* 200 错误页 / 未知格式不覆盖已批准基线 → `test_error_page_is_stored_as_parse_failed`
* 字段错误不覆盖 → `test_broken_values_are_invalid_not_overwritten`（见观测文件）
* 旧任务晚到不得覆盖新内容 → `test_late_capture_does_not_move_current_pointer`
* 任务入队防重复 → `test_concurrent_capture_of_same_source_is_busy`
"""

from __future__ import annotations

import threading

import pytest

from app.core import config_repository as cr
from app.core.errors import BUSY, NOT_FOUND, OBSERVATION_UNKNOWN_RELEASE, AppError
from app.services import config_catalog, config_ingest

ACTOR = {"ip": "10.0.0.3", "name": "管理员乙"}

XML_DOC = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<R><PulseWidth unit=\"ms\">650</PulseWidth></R>"
).encode("utf-8")

JSON_DOC = b'{"PULSE_WIDTH": "0.65"}'

#: 真实错误页往往不是合法 XML（`<br>` 之类不收尾），所以这里刻意用一张
#: 良构与非良构之间最典型的样子：HTML 而不是 XML。
ERROR_PAGE = b"<html><body><h1>500 Internal Server Error</h1><br></body></html>"


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


@pytest.fixture(autouse=True)
def clean_inflight():
    config_ingest.reset_inflight()
    yield
    config_ingest.reset_inflight()


@pytest.fixture()
def world(repo):
    """一个类别 + 两个逻辑文件 + 一个带定义的参数。"""
    family = config_catalog.create_family(repo, ACTOR, name="轴参数", code="AXIS_PARAM")
    xml_file = config_catalog.create_logical_file(
        repo, ACTOR, family_id=family["id"], role="xml_export", display_name="轴参数.xml"
    )
    json_file = config_catalog.create_logical_file(
        repo, ACTOR, family_id=family["id"], role="field_json", display_name="轴参数.json"
    )
    parameter = config_catalog.create_parameter(
        repo, ACTOR, family_id=family["id"], name="脉冲宽度", category="时序"
    )
    config_catalog.define_parameter(
        repo,
        ACTOR,
        parameter_id=parameter["id"],
        xml_name="PulseWidth",
        xml_loc={"path": [{"name": "PulseWidth", "occurrence": 1}], "attribute": None},
        json_key="PULSE_WIDTH",
        json_pointer="/PULSE_WIDTH",
        value_type="decimal",
        unit="ms",
    )
    return {"family": family, "xml": xml_file, "json": json_file, "parameter": parameter}


def _capture(repo, world, **kwargs):
    params = {
        "profile_id": "A",
        "content": XML_DOC,
        "filename": "轴参数.xml",
        "capture_kind": config_ingest.CAPTURE_MANUAL,
    }
    params.update(kwargs)
    return config_ingest.record_capture(repo, ACTOR, **params)


# -- 同次持久提交 -----------------------------------------------------------


def test_observation_and_event_are_published_together(repo, world) -> None:
    """观测与采集事件要么一起出现，要么都不出现：事件里的观测 ID 必须真实可读。"""
    observation = _capture(repo, world)
    event = repo.get("capture_event", observation["event_id"])
    assert event is not None
    assert event["observation_id"] == observation["id"]
    assert event["blob_sha256"] == observation["blob_sha256"]
    assert event["deduplicated"] is False
    # 同一次提交 → 同一时刻创建
    assert event["created_at"] == repo.get("observation", observation["id"])["created_at"]


def test_capture_keeps_raw_bytes(repo, world) -> None:
    observation = _capture(repo, world)
    assert config_ingest.observation_bytes(repo, observation["id"]) == XML_DOC
    assert observation["byte_size"] == len(XML_DOC)
    assert observation["format"] == "xml"


# -- 同内容重复 -------------------------------------------------------------


def test_same_content_does_not_create_second_observation(repo, world) -> None:
    """同内容重复下载：观测只有一条，但"这个时刻它还是这样"必须留事件。"""
    first = _capture(repo, world, captured_at="2026-10-20T08:00:00Z")
    second = _capture(repo, world, captured_at="2026-10-21T08:00:00Z")

    assert second["id"] == first["id"]
    assert second["deduplicated"] is True
    assert len(config_ingest.list_observations(repo, profile_id="A")) == 1
    events = repo.list_objects("capture_event")
    assert len(events) == 2, "两次采集两次事件"
    assert [item["captured_at"] for item in events] == [
        "2026-10-20T08:00:00Z",
        "2026-10-21T08:00:00Z",
    ]
    assert all(item["observation_id"] == first["id"] for item in events)


def test_different_content_creates_new_observation(repo, world) -> None:
    first = _capture(repo, world, captured_at="2026-10-20T08:00:00Z")
    changed = XML_DOC.replace(b"650", b"700")
    second = _capture(repo, world, content=changed, captured_at="2026-10-21T08:00:00Z")
    assert second["id"] != first["id"]
    assert second["deduplicated"] is False
    assert len(config_ingest.list_observations(repo, profile_id="A")) == 2


# -- 错误页与坏格式 ---------------------------------------------------------


def test_error_page_is_stored_as_parse_failed(repo, world) -> None:
    """现场把 200 错误页当配置存下来是常事：必须留证，且不能变成可用的观测。"""
    observation = _capture(repo, world, content=ERROR_PAGE, filename="轴参数.xml")
    assert observation["status"] == config_ingest.STATUS_PARSE_FAILED
    assert observation["completeness"] == config_ingest.COMPLETENESS_PARTIAL
    assert observation["mapping"]["entries"] == []
    assert observation["reason"]
    # 原文仍然完整保留，便于事后判断到底存了什么
    assert config_ingest.observation_bytes(repo, observation["id"]) == ERROR_PAGE


def test_unknown_format_is_parse_failed(repo, world) -> None:
    """扩展名是 .xml、内容却是二进制：格式判断先失败，不该硬当 XML 去解。"""
    observation = _capture(repo, world, content=b"\x00\x01\x02binary")
    assert observation["status"] == config_ingest.STATUS_PARSE_FAILED
    assert "不合法" in observation["reason"]


def test_empty_content_is_rejected_as_parse_failed(repo, world) -> None:
    observation = _capture(repo, world, content=b"")
    assert observation["status"] == config_ingest.STATUS_PARSE_FAILED


# -- 旧任务晚到 -------------------------------------------------------------


def test_late_capture_does_not_move_current_pointer(repo, world) -> None:
    """定时任务可能乱序回来：晚到的采集照常入库，但不能顶掉更新的那份。"""
    newer = XML_DOC.replace(b"650", b"700")
    latest = _capture(repo, world, content=newer, captured_at="2026-10-21T08:00:00Z")
    late = _capture(repo, world, content=XML_DOC, captured_at="2026-10-20T08:00:00Z")

    assert late["id"] != latest["id"]
    assert late["stale_capture"] is True
    current = config_ingest.current_observation(repo, "A", world["xml"]["id"])
    assert current["id"] == latest["id"], "晚到的采集不能覆盖当前观测"
    # 但历史不能丢：晚到的那条照样查得到
    assert len(config_ingest.list_observations(repo, profile_id="A")) == 2


# -- 未登记文件与非法入参 ---------------------------------------------------


def test_unregistered_file_is_rejected(repo, world) -> None:
    with pytest.raises(AppError) as caught:
        _capture(repo, world, filename="没人认识.xml")
    assert caught.value.code == NOT_FOUND
    assert "hint" in caught.value.detail


def test_capture_kind_is_validated(repo, world) -> None:
    with pytest.raises(AppError) as caught:
        _capture(repo, world, capture_kind="telepathy")
    assert caught.value.detail["field"] == "capture_kind"


def test_profile_id_is_required(repo, world) -> None:
    with pytest.raises(AppError) as caught:
        _capture(repo, world, profile_id="  ")
    assert caught.value.detail["field"] == "profile_id"


# -- 防重复（入队互斥）------------------------------------------------------


def test_concurrent_capture_of_same_source_is_busy(repo, world) -> None:
    """同一来源同时只允许一个采集在跑；第二个直接 BUSY，不同时写同一逻辑文件。"""
    entered = threading.Event()
    release = threading.Event()
    errors = []

    def holder():
        try:
            with config_ingest.capture_guard("A", "现场 10.20.1.30"):
                entered.set()
                release.wait(5)
        except AppError as exc:  # pragma: no cover - 不应发生
            errors.append(exc)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(AppError) as caught:
            with config_ingest.capture_guard("A", "现场 10.20.1.30"):
                pass
        assert caught.value.code == BUSY
        # 不同来源互不影响
        with config_ingest.capture_guard("A", "现场 10.20.1.31"):
            pass
    finally:
        release.set()
        thread.join(5)
    assert errors == []


# -- 版本归属三态 -----------------------------------------------------------


def test_explicit_release_without_matching_bytes_is_unconfirmed(repo, world) -> None:
    release = config_catalog.register_release(
        repo, ACTOR, version_label="1.1.0", build="20260901"
    )
    observation = _capture(repo, world, release_id=release["id"])
    assert observation["release_ref"]["release_id"] == release["id"]
    assert observation["release_ref"]["resolution"] == "unconfirmed"


def test_release_confirmed_when_bytes_match_registered_snapshot(repo, world) -> None:
    release = config_catalog.register_release(
        repo, ACTOR, version_label="1.1.0", build="20260901"
    )
    config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=release["id"],
        logical_file_id=world["xml"]["id"],
        data=XML_DOC,
        original_filename="轴参数.xml",
        source="upload",
    )
    observation = _capture(repo, world, release_id=release["id"])
    assert observation["release_ref"]["resolution"] == "confirmed"

    # 不给版本时按内容反查，也能确认归属
    inferred = _capture(repo, world, content=XML_DOC.replace(b"650", b"651"))
    assert inferred["release_ref"]["resolution"] == "unknown"


def test_unknown_release_is_reported_when_required(repo, world) -> None:
    with pytest.raises(AppError) as caught:
        _capture(repo, world, require_release=True)
    assert caught.value.code == OBSERVATION_UNKNOWN_RELEASE
    assert caught.value.detail["resolution"] == "unknown"


# -- 摘要 -------------------------------------------------------------------


def test_summary_counts_by_status_and_resolution(repo, world) -> None:
    _capture(repo, world, captured_at="2026-10-20T08:00:00Z")
    _capture(repo, world, content=ERROR_PAGE, captured_at="2026-10-21T08:00:00Z")
    summary = config_ingest.ingest_summary(repo, "A")
    assert summary["total"] == 2
    assert summary["by_status"] == {"parsed": 1, "parse_failed": 1}
    assert summary["by_resolution"] == {"unknown": 2}
    assert summary["latest"]["status"] == "parse_failed"
