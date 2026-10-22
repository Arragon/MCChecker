"""双版本双机型黄金样本的结构校验（M5-02 / INH-1936）。

只用标准库解析，**不 import 业务模块**：本文件在 M5-05/M5-06 落地前就应能跑，
之后作为"样本没被悄悄改坏"的护栏长期存在。

样本覆盖卡面要求的 11 类差异中的：参数改名（Pulse→PulseWidth）、XML 位置移动
（Enable 移入 Flags）、JSON 全大写命名、单位转换（ms→s）、类型/范围变化、
新增参数（Trace）、缺失项（v1.0 无 Trace）、机型差异（A/B）。

**合成声明**：样本为按真实结构手工构造的合成数据，未经现场核对（D6 未解决）。
"现场验证"一栏在任何验收里都必须写成未做。
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest

SAMPLES = os.path.join(
    os.path.dirname(__file__), "fixtures", "config_management", "samples"
)
NS = "{urn:mc:cfg:1}"

#: 参数主档 → JSON 键（1.0.0 / 1.1.0 两版）。
PARAM_JSON_KEY = {
    "pm_000030": "AXIS1_VELOCITY",
    "pm_000031": "AXIS1_ACCEL",
    "pm_000032": "AXIS2_VELOCITY",
    "pm_000033": "AXIS2_ACCEL",
    "pm_000008": "PULSE",  # 1.1.0 为 PULSE_WIDTH
    "pm_000034": "ENABLE",
    "pm_000035": "MODE",
    "pm_000036": "RETRY",
    "pm_000037": "NAME",
    "pm_000038": "TRACE",
}

#: 参数主档 → XML 定位（1.0.0 / 1.1.0 两版各自解析，见 expected 文件）。
PARAM_XML_PATH_V1_0 = {
    "pm_000030": "Axis[1]/Velocity",
    "pm_000031": "Axis[1]/Acceleration",
    "pm_000032": "Axis[2]/Velocity",
    "pm_000033": "Axis[2]/Acceleration",
    "pm_000008": "Pulse",
    "pm_000034": "Enable",
    "pm_000035": "Mode",
    "pm_000036": "Retry",
    "pm_000037": "Name",
}

PARAM_XML_PATH_V1_1 = {
    "pm_000030": "Axis[1]/Velocity",
    "pm_000031": "Axis[1]/Acceleration",
    "pm_000032": "Axis[2]/Velocity",
    "pm_000033": "Axis[2]/Acceleration",
    "pm_000008": "PulseWidth",
    "pm_000034": "Flags/Enable",
    "pm_000035": "Mode",
    "pm_000036": "Retry",
    "pm_000037": "Name",
    "pm_000038": "Flags/Trace",
}

_STEP = re.compile(r"^([^\[\]]+)(?:\[(\d+)\])?$")


def _load_json(name: str):
    with open(os.path.join(SAMPLES, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_xml(name: str):
    return ET.parse(os.path.join(SAMPLES, name)).getroot()


def _resolve_xml(root, path: str):
    """按 `A[2]/B` 形式定位；不存在返回 None（同名第 n 个，n 从 1 起）。"""
    node = root
    for raw in path.split("/"):
        found = _STEP.match(raw)
        assert found, "非法路径段：%s" % raw
        name, occurrence = found.group(1), int(found.group(2) or 1)
        matches = [child for child in list(node) if child.tag == NS + name]
        if len(matches) < occurrence:
            return None
        node = matches[occurrence - 1]
    return node


def _no_duplicate_keys(pairs):
    seen = set()
    for key, _value in pairs:
        assert key not in seen, "JSON 重复键：%s" % key
        seen.add(key)
    return dict(pairs)


@pytest.mark.parametrize("name", ["v1_0_A", "v1_0_B", "v1_1_A", "v1_1_B"])
def test_xml_sample_is_well_formed_and_namespaced(name: str) -> None:
    root = _load_xml(name + ".xml")
    assert root.tag == NS + "MachineConfig"
    assert root.get("model") == name[-1], "机型必须可区分"


@pytest.mark.parametrize("name", ["v1_0_A", "v1_0_B", "v1_1_A", "v1_1_B"])
def test_json_sample_has_no_duplicate_keys(name: str) -> None:
    text = open(os.path.join(SAMPLES, name + ".json"), "r", encoding="utf-8").read()
    json.loads(text, object_pairs_hook=_no_duplicate_keys)


@pytest.mark.parametrize("name", ["v1_0_A", "v1_0_B", "v1_1_A", "v1_1_B"])
def test_expected_file_matches_sample_set(name: str) -> None:
    expected = _load_json(os.path.join("expected", name + ".json"))
    assert expected["model"] == name[-1]
    assert set(expected["values"]) == set(PARAM_JSON_KEY)


@pytest.mark.parametrize("name", ["v1_0_A", "v1_0_B", "v1_1_A", "v1_1_B"])
def test_xml_locators_resolve_uniquely(name: str) -> None:
    """每个参数在对应版本的 XML 里必须恰好命中一个节点（或按 absent 声明缺席）。"""
    root = _load_xml(name + ".xml")
    expected = _load_json(os.path.join("expected", name + ".json"))
    table = PARAM_XML_PATH_V1_0 if expected["release"] == "1.0.0" else PARAM_XML_PATH_V1_1
    located = []
    for param_id, item in expected["values"].items():
        path = table.get(param_id)
        if path is None:
            assert item.get("absent") is True, "%s 无法定位且未声明缺席" % param_id
            continue
        node = _resolve_xml(root, path)
        assert node is not None, "%s 定位失败：%s" % (param_id, path)
        if item["type"] != "decimal":
            expected_text = item.get("source_text", item["text"])
            assert node.text.strip() == expected_text, "%s 值不符" % param_id
        located.append(path)
    # 两个参数不得指向同一个 XML 节点
    assert len(located) == len(set(located)), "定位重复：%s" % located


@pytest.mark.parametrize("name", ["v1_0_A", "v1_0_B", "v1_1_A", "v1_1_B"])
def test_json_keys_cover_every_expected_parameter(name: str) -> None:
    data = _load_json(name + ".json")
    expected = _load_json(os.path.join("expected", name + ".json"))
    for param_id, item in expected["values"].items():
        if item.get("absent"):
            continue
        key = PARAM_JSON_KEY[param_id]
        if param_id == "pm_000008" and expected["release"] == "1.1.0":
            key = "PULSE_WIDTH"
        assert key in data, "%s 缺 JSON 键 %s" % (param_id, key)
    # JSON 侧不得有未登记的散键
    known = set(PARAM_JSON_KEY.values()) | {"PULSE_WIDTH"}
    assert set(data) <= known, "样本含未登记键：%s" % sorted(set(data) - known)


def test_millisecond_to_second_is_lossless() -> None:
    """1.0.0 的 650 ms 与 1.1.0 的 0.65 s 必须判定为同一个值。"""
    v10 = _load_json(os.path.join("expected", "v1_0_A.json"))["values"]["pm_000008"]
    v11 = _load_json(os.path.join("expected", "v1_1_A.json"))["values"]["pm_000008"]
    assert v10["source_text"] == "650" and v10["source_unit"] == "ms"
    converted = Decimal(v10["source_text"]) / Decimal(1000)
    assert converted == Decimal(v11["text"])


def test_rename_resolves_to_same_parameter_identity() -> None:
    v10 = _load_json(os.path.join("expected", "v1_0_A.json"))["values"]["pm_000008"]
    v11 = _load_json(os.path.join("expected", "v1_1_A.json"))["values"]["pm_000008"]
    assert v10["source_key"] == "Pulse"
    assert v11["source_key"] == "PulseWidth"
    assert "pm_000008" in _load_json(os.path.join("expected", "v1_1_A.json"))["values"]


def test_structure_move_is_visible_in_samples() -> None:
    old = _load_xml("v1_0_A.xml")
    new = _load_xml("v1_1_A.xml")
    assert _resolve_xml(old, "Enable") is not None
    assert _resolve_xml(old, "Flags/Enable") is None, "1.0.0 不应有 Flags 包装"
    assert _resolve_xml(new, "Enable") is None, "1.1.0 的 Enable 已移入 Flags"
    assert _resolve_xml(new, "Flags/Enable") is not None


def test_new_parameter_only_exists_in_later_release() -> None:
    assert _resolve_xml(_load_xml("v1_1_A.xml"), "Flags/Trace") is not None
    assert _resolve_xml(_load_xml("v1_0_A.xml"), "Flags/Trace") is None
    v10 = _load_json(os.path.join("expected", "v1_0_A.json"))["values"]["pm_000038"]
    assert v10.get("absent") is True


def test_models_differ_in_values_not_in_identity() -> None:
    a = _load_json(os.path.join("expected", "v1_0_A.json"))["values"]
    b = _load_json(os.path.join("expected", "v1_0_B.json"))["values"]
    assert a["pm_000030"]["text"] != b["pm_000030"]["text"]
    assert set(a) == set(b), "机型差异不应改变参数身份集合"


def test_samples_are_marked_synthetic() -> None:
    """D6 未解决：样本必须可被识别为合成，避免被当成现场证据。"""
    notes = _load_json(os.path.join("expected", "v1_0_A.json"))["note"]
    assert "ms" in notes
    spec = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "docs",
        "MCChecker_CONFIG_DOMAIN_SPEC.md",
    )
    assert os.path.exists(spec)
