"""Observation 的严格解析、版本归属与映射结果（M6-08 / INH-1953）。

卡面验收逐条对应：

* 缺失 / null / 空串 / 0 / false 必须分得清 → `test_json_value_states_are_distinct`
* 数组错位不静默匹配 → `test_array_index_out_of_range_is_missing`
* 重复键不静默匹配 → `test_duplicate_json_keys_fail_the_capture`
* 重新映射不覆写原始字节与上次结果 → `test_remap_appends_revision_and_keeps_r1`
* 采集者/采集时间与修改人/修改时间分离 → `test_capture_and_remap_stamps_are_separate`
* 未匹配键要报出来 → `test_unresolved_keys_are_reported`
"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.errors import REVISION_CONFLICT, AppError
from app.services import config_catalog, config_ingest

ACTOR = {"ip": "10.0.0.3", "name": "采集员甲"}
EDITOR = {"ip": "10.0.0.9", "name": "管理员乙"}


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


@pytest.fixture()
def world(repo):
    """一个现场 JSON 逻辑文件 + 五个参数，覆盖缺失/空值/数值/布尔/字符串。"""
    family = config_catalog.create_family(repo, ACTOR, name="轴参数", code="AXIS_PARAM")
    json_file = config_catalog.create_logical_file(
        repo, ACTOR, family_id=family["id"], role="field_json", display_name="轴参数.json"
    )
    xml_file = config_catalog.create_logical_file(
        repo, ACTOR, family_id=family["id"], role="xml_export", display_name="轴参数.xml"
    )
    spec = [
        ("脉冲宽度", "/PULSE_WIDTH", "decimal", "ms"),
        ("空字符串项", "/EMPTY_TEXT", "text", ""),
        ("零值项", "/ZERO", "int", ""),
        ("布尔项", "/ENABLED", "bool", ""),
        ("显式空项", "/NULLABLE", "text", ""),
    ]
    parameters = {}
    for name, pointer, value_type, unit in spec:
        parameter = config_catalog.create_parameter(
            repo, ACTOR, family_id=family["id"], name=name, category="时序"
        )
        config_catalog.define_parameter(
            repo,
            ACTOR,
            parameter_id=parameter["id"],
            xml_name=pointer.lstrip("/"),
            xml_loc={"path": [{"name": pointer.lstrip("/"), "occurrence": 1}], "attribute": None},
            json_key=pointer.lstrip("/"),
            json_pointer=pointer,
            value_type=value_type,
            unit=unit,
        )
        parameters[name] = parameter
    return {
        "family": family,
        "json": json_file,
        "xml": xml_file,
        "parameters": parameters,
    }


def _capture(repo, world, content, **kwargs):
    params = {
        "profile_id": "A",
        "content": content,
        "filename": "轴参数.json",
        "capture_kind": config_ingest.CAPTURE_MANUAL,
    }
    params.update(kwargs)
    return config_ingest.record_capture(repo, ACTOR, **params)


def _by_name(observation, world):
    """把 entry 按参数名索引，便于逐条断言。"""
    names = {
        parameter["id"]: name for name, parameter in world["parameters"].items()
    }
    return {
        names[entry["parameter_id"]]: entry
        for entry in observation["mapping"]["entries"]
        if entry.get("parameter_id") in names
    }


# -- 值状态 -----------------------------------------------------------------


def test_json_value_states_are_distinct(repo, world) -> None:
    """`missing` / `null` / `""` / `0` / `false` 五件事，五种结论，互不冒充。"""
    document = b'{"EMPTY_TEXT": "", "ZERO": 0, "ENABLED": false, "NULLABLE": null}'
    observation = _capture(repo, world, document)
    assert observation["status"] == config_ingest.STATUS_PARSED
    entries = _by_name(observation, world)

    # 键不在文件里 → missing，且不是"被删了"
    assert entries["脉冲宽度"]["status"] == config_ingest.ENTRY_MISSING
    assert entries["脉冲宽度"]["reason"] == "key_missing"
    assert entries["脉冲宽度"]["value_ref"] is None

    # 显式 null → mapped，值是 null 类型
    assert entries["显式空项"]["status"] == config_ingest.ENTRY_MAPPED
    assert entries["显式空项"]["value_ref"]["type"] == "null"

    # 空串仍是字符串值，不是 null
    assert entries["空字符串项"]["status"] == config_ingest.ENTRY_MAPPED
    assert entries["空字符串项"]["value_ref"] == {"type": "string", "text": "", "unit": None}

    # 0 与 false 不能被当成"没有值"
    assert entries["零值项"]["value_ref"]["type"] == "int"
    assert entries["零值项"]["value_ref"]["text"] == "0"
    assert entries["布尔项"]["value_ref"]["type"] == "bool"
    assert entries["布尔项"]["value_ref"]["text"] == "false"

    # 有缺失 → 完整度降级
    assert observation["completeness"] == config_ingest.COMPLETENESS_PARTIAL


def test_all_values_present_is_full(repo, world) -> None:
    document = (
        b'{"PULSE_WIDTH": "0.65", "EMPTY_TEXT": "x", "ZERO": 0,'
        b' "ENABLED": true, "NULLABLE": null}'
    )
    observation = _capture(repo, world, document)
    assert observation["completeness"] == config_ingest.COMPLETENESS_FULL
    entries = _by_name(observation, world)
    assert entries["脉冲宽度"]["value_ref"] == {"type": "decimal", "text": "0.65", "unit": "ms"}


def test_wrong_type_is_invalid_not_silently_kept(repo, world) -> None:
    """数值参数拿到文本 → `invalid`，并且不给出一个"看起来能用"的 value_ref。"""
    observation = _capture(repo, world, '{"PULSE_WIDTH": "快一点"}'.encode("utf-8"))
    entry = _by_name(observation, world)["脉冲宽度"]
    assert entry["status"] == config_ingest.ENTRY_INVALID
    assert entry["value_ref"] is None
    assert entry["reason"]
    assert observation["completeness"] == config_ingest.COMPLETENESS_PARTIAL


# -- 数组错位与重复键 -------------------------------------------------------


def test_array_index_out_of_range_is_missing(repo, world) -> None:
    """数组短了一格时不能"取到隔壁"：越界就是缺失，不是静默匹配。"""
    family = world["family"]
    parameter = config_catalog.create_parameter(
        repo, ACTOR, family_id=family["id"], name="第二轴速度", category="时序"
    )
    config_catalog.define_parameter(
        repo,
        ACTOR,
        parameter_id=parameter["id"],
        xml_name="Axis",
        xml_loc={"path": [{"name": "Axis", "occurrence": 1}], "attribute": None},
        json_key="SPEED",
        json_pointer="/AXIS/1/SPEED",
        value_type="decimal",
    )
    observation = _capture(repo, world, b'{"AXIS": [{"SPEED": "1.0"}]}')
    entries = {
        entry["raw_key"]: entry for entry in observation["mapping"]["entries"]
    }
    assert entries["/AXIS/1/SPEED"]["status"] == config_ingest.ENTRY_MISSING
    assert entries["/AXIS/1/SPEED"]["reason"] == "index_out_of_range"


def test_duplicate_json_keys_fail_the_capture(repo, world) -> None:
    """重复键在普通解析里是"后者覆盖前者"，对配置就是静默丢数据 → 直接判失败。"""
    observation = _capture(repo, world, b'{"PULSE_WIDTH": "0.65", "PULSE_WIDTH": "0.70"}')
    assert observation["status"] == config_ingest.STATUS_PARSE_FAILED
    assert "重复键" in observation["reason"]
    assert observation["mapping"]["entries"] == []
    # 原文照样留着，事后能看出到底哪两个键打架
    assert b"0.70" in config_ingest.observation_bytes(repo, observation["id"])


def test_nan_is_rejected(repo, world) -> None:
    observation = _capture(repo, world, b'{"PULSE_WIDTH": NaN}')
    assert observation["status"] == config_ingest.STATUS_PARSE_FAILED


# -- 未匹配键与歧义 ---------------------------------------------------------


def test_unresolved_keys_are_reported(repo, world) -> None:
    document = b'{"PULSE_WIDTH": "0.65", "UNKNOWN_KEY": 1, "NESTED": {"DEEP": 2}}'
    observation = _capture(repo, world, document)
    unresolved = observation["mapping"]["unresolved_keys"]
    assert "/UNKNOWN_KEY" in unresolved
    assert "/NESTED/DEEP" in unresolved
    unmapped = [
        entry
        for entry in observation["mapping"]["entries"]
        if entry["status"] == config_ingest.ENTRY_UNMAPPED
    ]
    assert {entry["raw_key"] for entry in unmapped} == {"/UNKNOWN_KEY", "/NESTED/DEEP"}
    assert all(entry["parameter_id"] is None for entry in unmapped)


def test_two_parameters_on_same_target_are_ambiguous(repo, world) -> None:
    """两个参数抢同一个节点：全部标 ambiguous，不静默选一个。"""
    parameter = config_catalog.create_parameter(
        repo, ACTOR, family_id=world["family"]["id"], name="重复登记项", category="时序"
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
    )
    observation = _capture(repo, world, b'{"PULSE_WIDTH": "0.65"}')
    owners = [
        entry
        for entry in observation["mapping"]["entries"]
        if entry["raw_key"] == "/PULSE_WIDTH"
    ]
    assert len(owners) == 2
    assert {entry["status"] for entry in owners} == {config_ingest.ENTRY_AMBIGUOUS}
    assert all(entry["value_ref"] is None for entry in owners)


# -- 重新映射 ---------------------------------------------------------------


def test_remap_appends_revision_and_keeps_r1(repo, world) -> None:
    """重新映射 = 新修订；原始字节与第一次的结论都还在。"""
    observation = _capture(repo, world, b'{"PULSE_WIDTH": "0.65"}', captured_at="2026-10-20T08:00:00Z")
    original = config_ingest.get_observation(repo, observation["id"], "r1")
    digest = observation["blob_sha256"]

    # 之后才登记一个新参数，再重新映射：这次能映射到它
    parameter = config_catalog.create_parameter(
        repo, ACTOR, family_id=world["family"]["id"], name="新加项", category="时序"
    )
    config_catalog.define_parameter(
        repo,
        ACTOR,
        parameter_id=parameter["id"],
        xml_name="NEW_KEY",
        xml_loc={"path": [{"name": "NEW_KEY", "occurrence": 1}], "attribute": None},
        json_key="NEW_KEY",
        json_pointer="/NEW_KEY",
        value_type="decimal",
    )
    remapped = config_ingest.remap_observation(
        repo, EDITOR, observation_id=observation["id"], reason="补登记了新参数"
    )

    assert remapped["id"] == observation["id"]
    assert remapped["mapping_revision"] == 2
    assert repo.history("observation", observation["id"]) == ["r1", "r2"]
    # r1 一个字都没变
    assert original["mapping"] == config_ingest.get_observation(
        repo, observation["id"], "r1"
    )["mapping"]
    assert original["mapping_revision"] == 1
    # 字节不变：重新映射不碰 blob
    assert remapped["blob_sha256"] == digest
    assert config_ingest.observation_bytes(repo, observation["id"]) == b'{"PULSE_WIDTH": "0.65"}'
    # 新结论里 /NEW_KEY 仍不在文件里，所以还是 unresolved 之外的空缺
    assert remapped["mapping"]["unresolved_keys"] == []


def test_remap_conflicts_on_stale_revision(repo, world) -> None:
    observation = _capture(repo, world, b'{"PULSE_WIDTH": "0.65"}')
    with pytest.raises(AppError) as caught:
        config_ingest.remap_observation(
            repo, EDITOR, observation_id=observation["id"], expected_revision="r9"
        )
    assert caught.value.code == REVISION_CONFLICT
    assert caught.value.detail["actual_revision"] == "r1"


def test_capture_and_remap_stamps_are_separate(repo, world) -> None:
    """采集者/采集时间 ≠ 修改人/修改时间：重新映射不冒充"重新采集过"。"""
    observation = _capture(repo, world, b'{"PULSE_WIDTH": "0.65"}', captured_at="2026-10-20T08:00:00Z")
    assert observation["captured_by"] == "采集员甲"
    assert observation["captured_at"] == "2026-10-20T08:00:00Z"

    remapped = config_ingest.remap_observation(
        repo, EDITOR, observation_id=observation["id"], reason="重算"
    )
    assert remapped["captured_by"] == "采集员甲", "采集者不会因为重新映射而改变"
    assert remapped["captured_at"] == "2026-10-20T08:00:00Z"
    assert remapped["remapped_by"] == "管理员乙"
    assert remapped["remapped_at"] >= remapped["captured_at"]


# -- XML 侧 ---------------------------------------------------------------


def test_xml_occurrence_and_attribute_resolution(repo, world) -> None:
    """同名兄弟靠序号区分；属性与文本各自定位，且缺属性不等于缺元素。"""
    family = world["family"]
    for name, steps, attribute in (
        ("第二速度", [("Axis", 2), ("Speed", 1)], None),
        ("第一速度单位", [("Axis", 1), ("Speed", 1)], "unit"),
    ):
        parameter = config_catalog.create_parameter(
            repo, ACTOR, family_id=family["id"], name=name, category="时序"
        )
        config_catalog.define_parameter(
            repo,
            ACTOR,
            parameter_id=parameter["id"],
            xml_name=steps[-1][0],
            xml_loc={
                "path": [{"name": n, "occurrence": o} for n, o in steps],
                "attribute": attribute,
            },
            value_type="text",
        )
    document = (
        b"<R><Axis><Speed unit=\"mm/s\">10</Speed></Axis>"
        b"<Axis><Speed>20</Speed></Axis></R>"
    )
    observation = _capture(repo, world, document, filename="轴参数.xml")
    entries = {entry["raw_key"]: entry for entry in observation["mapping"]["entries"]}
    assert entries["Axis[2]/Speed"]["value_ref"]["text"] == "20"
    assert entries["Axis/Speed@unit"]["value_ref"]["text"] == "mm/s"
    # 第一个 Axis 没有 unit 属性的兄弟被正确区分，第二个 Axis 的 Speed 没有 unit
    assert entries["Axis/Speed@unit"]["status"] == config_ingest.ENTRY_MAPPED


def test_xml_missing_occurrence_is_reported(repo, world) -> None:
    family = world["family"]
    parameter = config_catalog.create_parameter(
        repo, ACTOR, family_id=family["id"], name="第三轴", category="时序"
    )
    config_catalog.define_parameter(
        repo,
        ACTOR,
        parameter_id=parameter["id"],
        xml_name="Axis",
        xml_loc={"path": [{"name": "Axis", "occurrence": 3}], "attribute": None},
        value_type="text",
    )
    observation = _capture(repo, world, b"<R><Axis>1</Axis></R>", filename="轴参数.xml")
    entries = {entry["raw_key"]: entry for entry in observation["mapping"]["entries"]}
    assert entries["Axis[3]"]["status"] == config_ingest.ENTRY_MISSING
    assert entries["Axis[3]"]["reason"] == "occurrence_missing"
