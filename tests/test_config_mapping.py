"""参数定位与模板变体契约（M5-06 / INH-1940）。"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

import pytest

from app.core import config_mapping as cm
from app.core import config_values as cv
from app.core.errors import AppError

SAMPLES = os.path.join(
    os.path.dirname(__file__), "fixtures", "config_management", "samples"
)


def _xml(name: str):
    return ET.parse(os.path.join(SAMPLES, name)).getroot()


# --- JSON Pointer ---------------------------------------------------------


def test_pointer_roundtrip_with_escaping() -> None:
    parts = ["a/b", "c~d", "plain"]
    pointer = cm.encode_pointer(parts)
    assert pointer == "/a~1b/c~0d/plain"
    assert cm.parse_pointer(pointer) == parts


def test_pointer_root_is_empty_string() -> None:
    assert cm.parse_pointer("") == []
    assert cm.encode_pointer([]) == ""


@pytest.mark.parametrize("bad", ["a/b", "/a~2b", "/a~"])
def test_invalid_pointer_is_rejected(bad: str) -> None:
    with pytest.raises(AppError):
        cm.parse_pointer(bad)


def test_resolve_pointer_walks_objects_and_arrays() -> None:
    document = {"A": {"B": [{"C": "x"}]}, "E": "y"}
    assert cm.resolve_pointer(document, "/A/B/0/C") == "x"
    assert cm.resolve_pointer(document, "/E") == "y"
    assert cm.resolve_pointer(document, "/A/Z") is None
    assert cm.resolve_pointer(document, "/A/B/9") is None


# --- XML 定位 ------------------------------------------------------------


def test_xml_loc_parse_and_format() -> None:
    loc = cm.XmlLoc.parse("Axis[2]/Velocity")
    assert str(loc) == "Axis[2]/Velocity"
    assert loc.steps == (("Axis", 2), ("Velocity", 1))
    assert cm.XmlLoc.parse("Pulse").canonical() == "Pulse", "序号 1 可省略"


def test_xml_loc_json_roundtrip() -> None:
    loc = cm.XmlLoc([("Axis", 2), ("Velocity", 1)], attribute=None)
    restored = cm.XmlLoc.from_json(loc.to_json())
    assert restored == loc
    assert loc.to_json()["path"][0] == {"name": "Axis", "occurrence": 2}


def test_xml_loc_rejects_bad_input() -> None:
    for bad in ("", "A//B", "A[0]"):
        with pytest.raises(AppError):
            cm.XmlLoc.parse(bad)


def test_resolve_xml_finds_nodes_in_samples() -> None:
    old = _xml("v1_0_A.xml")
    assert cm.read_xml_value(old, cm.XmlLoc.parse("Pulse")) == "650"
    assert cm.read_xml_value(old, cm.XmlLoc.parse("Axis[1]/Velocity")) == "120.5"
    assert cm.read_xml_value(old, cm.XmlLoc.parse("Axis[2]/Velocity")) == "118.0"
    assert cm.read_xml_value(old, cm.XmlLoc.parse("Flags/Enable")) is None


def test_resolve_xml_after_structure_move() -> None:
    new = _xml("v1_1_A.xml")
    assert cm.read_xml_value(new, cm.XmlLoc.parse("Pulse")) is None
    assert cm.read_xml_value(new, cm.XmlLoc.parse("PulseWidth")) == "0.65"
    assert cm.read_xml_value(new, cm.XmlLoc.parse("Flags/Enable")) == "true"


def test_namespace_is_qualified_automatically() -> None:
    root = _xml("v1_0_A.xml")
    assert cm.default_namespace(root) == "urn:mc:cfg:1"
    node = cm.resolve_xml(root, cm.XmlLoc.parse("Mode"))
    assert node is not None and node.tag == "{urn:mc:cfg:1}Mode"


# --- 映射表 --------------------------------------------------------------


def test_mapping_table_detects_duplicate_targets() -> None:
    table = cm.MappingTable()
    table.add(cm.MappingEntry("pm_000001", xml=cm.XmlLoc.parse("Pulse")))
    table.add(cm.MappingEntry("pm_000002", xml=cm.XmlLoc.parse("Pulse")))
    duplicates = table.duplicates()
    assert "xml:Pulse" in duplicates
    with pytest.raises(AppError) as error:
        table.assert_unique()
    assert error.value.code == "MAPPING_AMBIGUOUS"
    assert error.value.detail["parameters"] == ["pm_000001", "pm_000002"]


def test_mapping_table_treats_implicit_occurrence_as_same_node() -> None:
    table = cm.MappingTable()
    table.add(cm.MappingEntry("pm_000001", xml=cm.XmlLoc.parse("Axis[1]/Velocity")))
    table.add(cm.MappingEntry("pm_000002", xml=cm.XmlLoc.parse("Axis/Velocity")))
    assert table.duplicates(), "序号 1 的省略写法必须被识别为同一节点"


def test_mapping_table_unique_entries_pass() -> None:
    table = cm.MappingTable(
        [
            cm.MappingEntry("pm_000001", xml=cm.XmlLoc.parse("Axis[1]/Velocity")),
            cm.MappingEntry("pm_000002", xml=cm.XmlLoc.parse("Axis[2]/Velocity")),
            cm.MappingEntry("pm_000003", json_pointer="/AXIS1_VELOCITY", json_key="AXIS1_VELOCITY"),
        ]
    )
    table.assert_unique()
    assert len(table) == 3
    root = _xml("v1_0_A.xml")
    assert table.resolve_xml("pm_000002", root) == "118.0"
    assert table.resolve_json("pm_000003", {"AXIS1_VELOCITY": "120.5"}) == "120.5"


def test_entry_requires_at_least_one_locator() -> None:
    with pytest.raises(AppError):
        cm.MappingEntry("pm_000001")


def test_duplicate_parameter_registration_is_rejected() -> None:
    table = cm.MappingTable([cm.MappingEntry("pm_000001", xml=cm.XmlLoc.parse("Pulse"))])
    with pytest.raises(AppError) as error:
        table.add(cm.MappingEntry("pm_000001", xml=cm.XmlLoc.parse("Mode")))
    assert error.value.code == "MAPPING_AMBIGUOUS"


# --- 结构指纹 ------------------------------------------------------------


def test_structure_signature_ignores_values_and_model_attribute() -> None:
    a = open(os.path.join(SAMPLES, "v1_0_A.xml"), "rb").read()
    b = open(os.path.join(SAMPLES, "v1_0_B.xml"), "rb").read()
    assert cm.structure_signature(a) == cm.structure_signature(b), "只有值不同不改变结构"


def test_structure_signature_changes_with_structure() -> None:
    old = open(os.path.join(SAMPLES, "v1_0_A.xml"), "rb").read()
    new = open(os.path.join(SAMPLES, "v1_1_A.xml"), "rb").read()
    assert cm.structure_signature(old) != cm.structure_signature(new)


def test_structure_signature_is_stable_across_bytes_roundtrip() -> None:
    data = open(os.path.join(SAMPLES, "v1_1_A.xml"), "rb").read()
    assert cm.structure_signature(data) == cm.structure_signature(data)
    assert len(cm.structure_signature(data)) == 64


# --- 变体兼容 ------------------------------------------------------------


def _definition_set(**overrides):
    base = {"release_id": "sr_000003", "variant_id": "tv_000006", "definition_ids": ["dr_1"]}
    base.update(overrides)
    return base


def test_variant_compatibility_accepts_matching_inputs() -> None:
    cm.check_variant_compatibility(
        variant_id="tv_000006",
        definition_set=_definition_set(),
        release_id="sr_000003",
        template_sha256="a" * 64,
        expected_template_sha256="a" * 64,
    )


def test_variant_mismatch_is_rejected() -> None:
    cases = [
        dict(variant_id=None),
        dict(variant_id="tv_other"),
        dict(release_id="sr_other"),
        dict(expected_template_sha256="b" * 64),
        dict(template_sha256=""),
    ]
    for override in cases:
        arguments = dict(
            variant_id="tv_000006",
            definition_set=_definition_set(),
            release_id="sr_000003",
            template_sha256="a" * 64,
        )
        arguments.update(override)
        with pytest.raises(AppError) as error:
            cm.check_variant_compatibility(**arguments)
        assert error.value.code == "ARTIFACT_TEMPLATE_INCOMPATIBLE"


# --- 与值模型联动 --------------------------------------------------------


def test_mapping_then_typed_value_from_sample() -> None:
    root = _xml("v1_1_A.xml")
    loc = cm.XmlLoc.parse("PulseWidth")
    raw = cm.read_xml_value(root, loc)
    value = cv.parse_typed(raw, "decimal", "s")
    assert value.text == "0.65"
    assert cv.convert(value, "ms").text == "650"
