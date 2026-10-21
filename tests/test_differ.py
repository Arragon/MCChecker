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


class TestCompareDocuments:
    """§6.6 的新算法：比较键是 `loc`，同名兄弟逐个比较，绑定由调用方传快照。"""

    @staticmethod
    def _nodes(text: str):
        from app.core import parser

        return parser.to_compact(text.encode("utf-8"), "cfg.xml")["nodes"]

    def test_differ_module_does_not_import_storage(self):
        from app.core import differ

        assert not hasattr(differ, "get_bound_paths"), "模块级 import 会造 storage ↔ differ 循环"

    def test_same_name_siblings_produce_two_modified_items(self):
        from app.core import differ

        base = self._nodes("<R><p><item>1</item><item>2</item></p></R>")
        target = self._nodes("<R><p><item>9</item><item>8</item></p></R>")
        result = differ.compare_documents(base, target, "cfg.xml", "cfg.xml", [])
        assert result["type"] == "structural"
        modified = [i for i in result["items"] if i["op"] == "modified"]
        assert len(modified) == 2, result["items"]
        assert result["summary"]["modified"] == 2
        assert [i["old"] for i in modified] == ["1", "2"]
        assert [i["new"] for i in modified] == ["9", "8"]

    def test_bound_group_present_only_for_bound_items(self):
        from app.core import differ

        base = self._nodes("<R><timeout>10</timeout><other>1</other></R>")
        target = self._nodes("<R><timeout>20</timeout><other>2</other></R>")
        bindings = [
            {
                "group_name": "超时组",
                "variables": [{"file": "cfg.xml", "path": "cfg.xml/R/timeout"}],
            }
        ]
        items = differ.compare_documents(base, target, "cfg.xml", "cfg.xml", bindings)["items"]
        by_path = {i["path"]: i for i in items}
        assert by_path["R › timeout"]["bound_group"] == "超时组"
        assert by_path["R › other"]["bound_group"] is None

    def test_bound_group_ignored_for_other_file(self):
        from app.core import differ

        base = self._nodes("<R><timeout>10</timeout></R>")
        target = self._nodes("<R><timeout>20</timeout></R>")
        bindings = [
            {"group_name": "别的文件", "variables": [{"file": "other.xml", "path": "other.xml/R/timeout"}]}
        ]
        items = differ.compare_documents(base, target, "cfg.xml", "cfg.xml", bindings)["items"]
        assert items[0]["bound_group"] is None

    def test_unchanged_entries_are_not_items(self):
        from app.core import differ

        base = self._nodes("<R><a>1</a><b>2</b></R>")
        target = self._nodes("<R><a>1</a><b>3</b></R>")
        result = differ.compare_documents(base, target, "cfg.xml", "cfg.xml", [])
        assert [i["loc"] for i in result["items"]] == ["/R[1]/b[1]"]
        assert result["summary"] == {"added": 0, "removed": 0, "modified": 1, "bound": 0}

    def test_summary_documents_matches_compare_items(self):
        """M2-07 的列表摘要与 M2-08 的条目必须同口径。"""
        from app.core import differ

        base = self._nodes("<R><p><item>1</item><item>2</item></p></R>")
        target = self._nodes("<R><p><item>9</item><item>8</item><extra>3</extra></p></R>")
        comparison = differ.compare_documents(base, target, "cfg.xml", "cfg.xml", [])
        summary = differ.summarize_documents(base, target, "cfg.xml", "cfg.xml", [])
        assert summary["summary"] == comparison["summary"]


class TestUnifiedDiffText:
    def test_lines_match_plain_text_difference(self):
        from app.core import differ

        result = differ.unified_diff_text("a\nb\nc\n", "a\nX\nc\n", "old", "new")
        assert result["too_large"] is False
        assert result["truncated"] is False
        assert result["total"] == len(result["lines"])
        joined = "\n".join(result["lines"])
        assert "-b" in joined and "+X" in joined

    def test_identical_text_has_no_lines(self):
        from app.core import differ

        assert differ.unified_diff_text("a\n", "a\n", "old", "new")["lines"] == []

    def test_text_limit_truncates_and_keeps_total(self):
        from app.core import differ

        base = "".join("line%d\n" % i for i in range(60))
        target = "".join("line%d\n" % i for i in range(10, 70))
        result = differ.unified_diff_text(base, target, "old", "new", 4)
        assert len(result["lines"]) == 4
        assert result["truncated"] is True
        assert result["total"] > 4

    def test_oversized_side_skips_text_diff(self):
        from app.core import differ

        huge = "".join("l%d\n" % i for i in range(differ.TEXT_DIFF_MAX_LINES + 1))
        result = differ.unified_diff_text(huge, "l0\n", "old", "new")
        assert result == {"lines": [], "total": 0, "truncated": True, "too_large": True}
