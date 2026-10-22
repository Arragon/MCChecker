"""参数主档、跨版本定义修订与冻结定义集合（M6-03 / INH-1948）。

卡面验收逐条对应：

* 旧名检索回同一 ID → `test_rename_keeps_id_and_old_name_still_resolves`
* 改引用生成新修订且历史基线仍指旧集合 → `test_new_definition_does_not_touch_old_one`
  / `test_definition_set_is_frozen_by_new_object`
* 定位冲突阻断发布 → `test_two_parameters_on_same_node_is_ambiguous`
* 单位与 XML 元信息不符阻断发布 → `test_unit_mismatch_blocks_when_conversion_is_lossy`
* retire / reactivate → `test_parameter_retire_and_reactivate`
"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.errors import AppError
from app.services import config_catalog

ACTOR = {"ip": "127.0.0.1", "name": "测试管理员"}

TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<MachineConfig xmlns="urn:mc:cfg:1" model="A">'
    '<PulseWidth unit="ms">650</PulseWidth>'
    '<Angle unit="rad">1.0</Angle>'
    "</MachineConfig>"
).encode("utf-8")


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


@pytest.fixture()
def setup(repo):
    release = config_catalog.register_release(repo, ACTOR, version_label="1.1.0", build="20260901")
    family = config_catalog.create_family(repo, ACTOR, name="轴参数", code="AXIS_PARAM")
    logical_file = config_catalog.create_logical_file(
        repo, ACTOR, family_id=family["id"], role="xml_export", display_name="轴参数.xml"
    )
    snapshot = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=release["id"],
        logical_file_id=logical_file["id"],
        data=TEMPLATE,
        original_filename="轴参数.xml",
        source="upload",
    )
    return {
        "release": release,
        "family": family,
        "logical_file": logical_file,
        "snapshot": snapshot,
    }


def _parameter(repo, setup, name="脉冲宽度"):
    return config_catalog.create_parameter(
        repo, ACTOR, family_id=setup["family"]["id"], name=name, category="时序"
    )


def _define(repo, parameter, **overrides):
    payload = {
        "xml_name": "PulseWidth",
        "xml_loc": {"path": [{"name": "PulseWidth", "occurrence": 1}], "attribute": None},
        "json_key": "PULSE_WIDTH",
        "json_pointer": "/PULSE_WIDTH",
        "value_type": "decimal",
        "unit": "s",
        "constraints": {"min": "0.0", "max": "10.0"},
        "remarks": "单位由 ms 改为 s",
    }
    payload.update(overrides)
    return config_catalog.define_parameter(
        repo, ACTOR, parameter_id=parameter["id"], **payload
    )


# -- 参数主档 ---------------------------------------------------------------


def test_create_parameter_is_unique_per_family(repo, setup) -> None:
    parameter = _parameter(repo, setup)
    assert parameter["name"] == "脉冲宽度"
    assert parameter["category"] == "时序"
    assert parameter["aliases"] == []
    assert parameter["status"] == "active"
    with pytest.raises(AppError) as caught:
        _parameter(repo, setup)
    assert caught.value.code == "DEFINITION_CONFLICT"
    assert caught.value.detail["parameter_id"] == parameter["id"]


def test_rename_keeps_id_and_old_name_still_resolves(repo, setup) -> None:
    parameter = _parameter(repo, setup)
    renamed = config_catalog.rename_parameter(
        repo, ACTOR, parameter_id=parameter["id"], new_name="脉冲宽度时间", reason="术语统一"
    )
    assert renamed["id"] == parameter["id"]
    assert renamed["name"] == "脉冲宽度时间"
    assert [item["name"] for item in renamed["aliases"]] == ["脉冲宽度"]

    for text in ("脉冲宽度", "脉冲宽度时间"):
        hits = config_catalog.search_parameters(repo, text, family_id=setup["family"]["id"])
        assert [item["id"] for item in hits] == [parameter["id"]]
    assert "alias" in config_catalog.search_parameters(repo, "脉冲宽度")[0]["matched"]


def test_parameter_retire_and_reactivate(repo, setup) -> None:
    parameter = _parameter(repo, setup)
    retired = config_catalog.set_parameter_status(
        repo, ACTOR, parameter_id=parameter["id"], status="retired", reason="停用"
    )
    assert retired["status"] == "retired"
    active_only = config_catalog.list_parameters(repo, setup["family"]["id"], include_retired=False)
    assert active_only == []
    back = config_catalog.set_parameter_status(
        repo, ACTOR, parameter_id=parameter["id"], status="active"
    )
    assert back["status"] == "active"
    assert repo.history("parameter", parameter["id"]) == ["r1", "r2", "r3"]


# -- 定义修订 ---------------------------------------------------------------


def test_definition_revisions_are_append_only(repo, setup) -> None:
    parameter = _parameter(repo, setup)
    first = _define(repo, parameter, unit="ms", remarks="初版")
    second = _define(repo, parameter, remarks="1.1.0 起改为秒")
    assert first["revision_no"] == 1
    assert first["supersedes"] is None
    assert second["revision_no"] == 2
    assert second["supersedes"] == first["id"]
    assert config_catalog.current_definition(repo, parameter["id"])["id"] == second["id"]
    assert [item["id"] for item in config_catalog.list_definitions(repo, parameter["id"])] == [
        second["id"],
        first["id"],
    ]


def test_new_definition_does_not_touch_old_one(repo, setup) -> None:
    parameter = _parameter(repo, setup)
    first = _define(repo, parameter, unit="ms", remarks="初版")
    before = dict(first)
    _define(repo, parameter, remarks="改单位")
    after = config_catalog.get_definition(repo, first["id"])
    assert after == before, "定义修订不可原地覆盖"
    assert repo.history("definition_revision", first["id"]) == ["r1"]


def test_definition_validation(repo, setup) -> None:
    parameter = _parameter(repo, setup)
    with pytest.raises(AppError) as bad_type:
        _define(repo, parameter, value_type="float")
    assert bad_type.value.code == "INVALID_INPUT"

    with pytest.raises(AppError) as bad_loc:
        _define(repo, parameter, xml_loc=None)
    assert bad_loc.value.code == "INVALID_INPUT"

    with pytest.raises(AppError) as bad_pointer:
        _define(repo, parameter, json_key="PULSE_WIDTH", json_pointer="/OTHER")
    assert bad_pointer.value.code == "INVALID_INPUT"

    with pytest.raises(AppError) as bad_unit:
        _define(repo, parameter, value_type="text", unit="s")
    assert bad_unit.value.code == "INVALID_INPUT"


def test_constraints_are_normalized_and_checked(repo, setup) -> None:
    parameter = _parameter(repo, setup)
    definition = _define(repo, parameter, constraints={"min": "0.0", "max": "10.00"})
    constraints = definition["constraints"]
    assert set(constraints) == {"min", "max", "enum", "pattern", "max_length"}
    assert constraints["min"] == "0" and constraints["max"] == "10"

    with pytest.raises(AppError) as inverted:
        _define(repo, parameter, constraints={"min": "5", "max": "1"})
    assert inverted.value.code == "INVALID_INPUT"

    with pytest.raises(AppError) as unknown_key:
        _define(repo, parameter, constraints={"maximum": "1"})
    assert "maximum" in unknown_key.value.detail["unknown"]

    with pytest.raises(AppError) as enum_empty:
        _define(repo, parameter, value_type="enum", unit="")
    assert enum_empty.value.code == "INVALID_INPUT"

    enum_ok = _define(repo, parameter, value_type="enum", unit="", constraints={"enum": ["auto", "manual"]})
    assert enum_ok["constraints"]["enum"] == ["auto", "manual"]

    with pytest.raises(AppError) as bad_pattern:
        _define(repo, parameter, constraints={"pattern": "[unclosed"})
    assert bad_pattern.value.code == "INVALID_INPUT"


def test_retired_definition_leaves_current(repo, setup) -> None:
    parameter = _parameter(repo, setup)
    first = _define(repo, parameter)
    config_catalog.retire_definition(repo, ACTOR, definition_id=first["id"])
    assert config_catalog.current_definition(repo, parameter["id"]) is None
    assert config_catalog.list_definitions(repo, parameter["id"])[0]["status"] == "retired"

    second = _define(repo, parameter, remarks="重新定义")
    assert second["revision_no"] == 2
    assert second["supersedes"] is None, "退役后没有可承接的前驱"


# -- 定义集合（冻结）--------------------------------------------------------


def test_definition_set_is_frozen_by_new_object(repo, setup) -> None:
    parameter = _parameter(repo, setup)
    definition = _define(repo, parameter)
    first_set = config_catalog.create_definition_set(
        repo,
        ACTOR,
        release_id=setup["release"]["id"],
        variant_id=None,
        definition_ids=[definition["id"]],
        note="1.1.0 初版",
    )
    second_definition = _define(repo, parameter, remarks="再修一次")
    second_set = config_catalog.create_definition_set(
        repo,
        ACTOR,
        release_id=setup["release"]["id"],
        definition_ids=[second_definition["id"]],
        note="1.1.0 修订",
    )
    assert first_set["id"] != second_set["id"]
    assert config_catalog.get_definition_set(repo, first_set["id"])["definition_ids"] == [
        definition["id"]
    ], "已发布的集合不被新集合改写"
    assert repo.history("definition_set", first_set["id"]) == ["r1"]
    assert (
        config_catalog.latest_definition_set(repo, setup["release"]["id"])["id"] == second_set["id"]
    )

    detail = config_catalog.definition_set_detail(repo, first_set["id"])
    assert detail["definition_set"]["id"] == first_set["id"]
    assert [item["id"] for item in detail["definitions"]] == [definition["id"]]


def test_definition_set_rejects_duplicate_parameter(repo, setup) -> None:
    parameter = _parameter(repo, setup)
    first = _define(repo, parameter)
    second = _define(repo, parameter, remarks="第二条")
    with pytest.raises(AppError) as caught:
        config_catalog.create_definition_set(
            repo,
            ACTOR,
            release_id=setup["release"]["id"],
            definition_ids=[first["id"], second["id"]],
        )
    assert caught.value.code == "DEFINITION_CONFLICT"
    assert caught.value.detail["parameter_id"] == parameter["id"]


def test_two_parameters_on_same_node_is_ambiguous(repo, setup) -> None:
    one = _parameter(repo, setup, name="脉冲宽度")
    two = _parameter(repo, setup, name="脉冲宽度备份")
    first = _define(repo, one)
    second = _define(repo, two, remarks="故意指同一个节点")
    with pytest.raises(AppError) as caught:
        config_catalog.create_definition_set(
            repo,
            ACTOR,
            release_id=setup["release"]["id"],
            definition_ids=[first["id"], second["id"]],
        )
    assert caught.value.code == "MAPPING_AMBIGUOUS"
    assert caught.value.detail["parameters"] == sorted([one["id"], two["id"]])


def test_definition_set_rejects_retired_and_empty(repo, setup) -> None:
    parameter = _parameter(repo, setup)
    definition = _define(repo, parameter)
    config_catalog.retire_definition(repo, ACTOR, definition_id=definition["id"])
    with pytest.raises(AppError) as retired:
        config_catalog.create_definition_set(
            repo, ACTOR, release_id=setup["release"]["id"], definition_ids=[definition["id"]]
        )
    assert retired.value.code == "DEFINITION_CONFLICT"

    with pytest.raises(AppError) as empty:
        config_catalog.create_definition_set(
            repo, ACTOR, release_id=setup["release"]["id"], definition_ids=[]
        )
    assert empty.value.code == "INVALID_INPUT"


def test_definition_set_requires_known_release_and_variant(repo, setup) -> None:
    parameter = _parameter(repo, setup)
    definition = _define(repo, parameter)
    with pytest.raises(AppError) as missing_release:
        config_catalog.create_definition_set(
            repo, ACTOR, release_id="sr_999999", definition_ids=[definition["id"]]
        )
    assert missing_release.value.code == "NOT_FOUND"
    with pytest.raises(AppError) as missing_variant:
        config_catalog.create_definition_set(
            repo,
            ACTOR,
            release_id=setup["release"]["id"],
            variant_id="tv_999999",
            definition_ids=[definition["id"]],
        )
    assert missing_variant.value.code == "NOT_FOUND"


# -- 与真实模板对账 ---------------------------------------------------------


def test_template_check_converts_lossless_unit_mismatch(repo, setup) -> None:
    parameter = _parameter(repo, setup)
    definition = _define(repo, parameter, unit="s")
    report = config_catalog.verify_definition_against_template(
        repo, definition, setup["snapshot"]["id"]
    )
    assert report["template_unit"] == "ms"
    assert report["declared_unit"] == "s"
    assert report["raw"] == "650"
    assert report["converted"] == "0.65"


def test_unit_mismatch_blocks_when_conversion_is_lossy(repo, setup) -> None:
    parameter = _parameter(repo, setup, name="角度")
    definition = _define(
        repo,
        parameter,
        xml_name="Angle",
        xml_loc={"path": [{"name": "Angle", "occurrence": 1}], "attribute": None},
        json_key="ANGLE",
        json_pointer="/ANGLE",
        unit="deg",
    )
    with pytest.raises(AppError) as caught:
        config_catalog.verify_definition_against_template(
            repo, definition, setup["snapshot"]["id"]
        )
    assert caught.value.code == "VALUE_LOSSY_CONVERSION"
    assert caught.value.detail["template_unit"] == "rad"
    assert caught.value.detail["declared_unit"] == "deg"


def test_missing_node_is_a_definition_conflict(repo, setup) -> None:
    parameter = _parameter(repo, setup)
    definition = _define(
        repo,
        parameter,
        xml_name="NotThere",
        xml_loc={"path": [{"name": "NotThere", "occurrence": 1}], "attribute": None},
    )
    with pytest.raises(AppError) as caught:
        config_catalog.verify_definition_against_template(
            repo, definition, setup["snapshot"]["id"]
        )
    assert caught.value.code == "DEFINITION_CONFLICT"
    assert caught.value.detail["snapshot_id"] == setup["snapshot"]["id"]


def test_search_covers_four_dimensions(repo, setup) -> None:
    parameter = _parameter(repo, setup)
    _define(repo, parameter)
    config_catalog.rename_parameter(
        repo, ACTOR, parameter_id=parameter["id"], new_name="脉冲宽度时间"
    )
    for needle, dimension in (
        ("脉冲宽度时间", "name"),
        ("脉冲宽度", "alias"),
        ("PulseWidth", "xml"),
        ("PULSE_WIDTH", "json"),
    ):
        hits = config_catalog.search_parameters(repo, needle)
        assert [item["id"] for item in hits] == [parameter["id"]], needle
        assert dimension in hits[0]["matched"], (needle, hits[0]["matched"])
        assert hits[0]["current_definition_id"]
