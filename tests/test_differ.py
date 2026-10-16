"""版本对比引擎测试

数据目录隔离由 `tests/conftest.py` 的 `isolated_data_root` 夹具统一提供。
"""

from __future__ import annotations

from app.core.differ import compare_versions, compare_with_uploaded
from app.core import storage


SAMPLE_XML_V1 = b"""<?xml version="1.0"?>
<config>
    <timeout>30</timeout>
    <retries>3</retries>
    <host>localhost</host>
</config>
"""

SAMPLE_XML_V2 = b"""<?xml version="1.0"?>
<config>
    <timeout>60</timeout>
    <retries>3</retries>
    <host>localhost</host>
    <debug>true</debug>
</config>
"""

SAMPLE_JSON_V1 = b"""{
    "timeout": 30,
    "retries": 3,
    "host": "localhost"
}"""

SAMPLE_JSON_V2 = b"""{
    "timeout": 60,
    "retries": 3,
    "host": "localhost",
    "debug": true
}"""


class TestCompareVersions:
    def test_identical_files(self):
        result = compare_versions(SAMPLE_XML_V1, SAMPLE_XML_V1, "v1.xml", "v1_copy.xml")
        assert result["has_changes"] is False

    def test_xml_diff_detects_modification(self):
        result = compare_versions(SAMPLE_XML_V1, SAMPLE_XML_V2, "v1.xml", "v2.xml")
        assert result["has_changes"] is True
        struct = result["structural_diff"]
        assert struct["type"] == "structural"
        modified = struct["modified"]
        assert len(modified) > 0
        # timeout 从 30 改为 60
        timeout_mods = [m for m in modified if "timeout" in m["path"]]
        assert len(timeout_mods) > 0

    def test_xml_diff_detects_addition(self):
        result = compare_versions(SAMPLE_XML_V1, SAMPLE_XML_V2, "v1.xml", "v2.xml")
        struct = result["structural_diff"]
        added = struct["added"]
        assert len(added) > 0
        # debug 节点被新增
        debug_adds = [a for a in added if "debug" in a["path"]]
        assert len(debug_adds) > 0

    def test_json_diff(self):
        result = compare_versions(SAMPLE_JSON_V1, SAMPLE_JSON_V2, "v1.json", "v2.json")
        assert result["has_changes"] is True
        struct = result["structural_diff"]
        assert len(struct["modified"]) > 0
        assert len(struct["added"]) > 0

    def test_json_diff_no_changes(self):
        result = compare_versions(SAMPLE_JSON_V1, SAMPLE_JSON_V1, "v1.json", "v2.json")
        assert result["has_changes"] is False

    def test_unified_diff_present(self):
        result = compare_versions(SAMPLE_XML_V1, SAMPLE_XML_V2, "v1.xml", "v2.xml")
        assert len(result["unified_diff"]) > 0

    def test_summary_counts(self):
        result = compare_versions(SAMPLE_JSON_V1, SAMPLE_JSON_V2, "v1.json", "v2.json")
        summary = result["structural_diff"]["summary"]
        # v1 -> v2 只动了两处：timeout 由 30 改 60，新增 debug；没有删除，也没有绑定组。
        assert summary["added_count"] == 1
        assert summary["removed_count"] == 0
        assert summary["modified_count"] == 1
        assert summary["bound_count"] == 0


class TestCompareWithUploaded:
    def test_upload_vs_current(self):
        result = compare_with_uploaded(
            SAMPLE_JSON_V2, "uploaded.json",
            SAMPLE_JSON_V1, "server.json",
        )
        assert result["has_changes"] is True


class TestBindingAnnotation:
    """测试变量绑定标注功能"""

    def test_bound_items_annotated(self):
        storage.add_binding("timeout_group", [
            {"file": "v1.json", "path": "v1.json/timeout"},
            {"file": "v2.json", "path": "v2.json/timeout"},
        ])

        result = compare_versions(SAMPLE_JSON_V1, SAMPLE_JSON_V2, "v1.json", "v2.json", use_bindings=True)
        struct = result["structural_diff"]
        # timeout 是唯一被绑定的变量，应被标注，并带上分组名与两侧旧值/新值
        assert struct["bound_items"] == [
            {
                "path": "/timeout",
                "old_value": "30",
                "new_value": "60",
                "bound": True,
                "bound_group": "timeout_group",
            }
        ]
        assert struct["summary"]["bound_count"] == 1

    def test_no_bindings(self):
        result = compare_versions(SAMPLE_JSON_V1, SAMPLE_JSON_V2, "v1.json", "v2.json", use_bindings=False)
        struct = result["structural_diff"]
        assert len(struct["bound_items"]) == 0
