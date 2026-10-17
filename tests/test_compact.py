"""compact 文档解析（INH-1867 / M1-01；规范 §6.1、§6.2；F04、C03、C04）。

用例与前端 `app/web/tests/tests.js` 的解析断言对齐；如两处不一致，以 §6 为准
并同步改 `app/web/assets/js/mock/docparse.js`。
"""

from __future__ import annotations

import json
import os

import pytest

from app.core import parser
from app.core.errors import PARSE_FAILED, TOO_LARGE, AppError

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_XML_PATH = os.path.join(ROOT_DIR, "test.xml")

XML = (
    '<?xml version="1.0"?><R a="1"><item>x</item><item>y<sub>z</sub></item>'
    '<param_1 description="d" min="0" max="9" type="int">5</param_1></R>'
).encode("utf-8")

JSONTXT = b'{"a":{"b/c":1,"t":true,"n":null},"arr":[1,{"k":"v"}]}'


def loc_of(nodes, loc):
    return nodes["loc"].index(loc)


def labels(nodes):
    return nodes["l"]


class TestXmlCompact:
    def test_root_and_element_rows(self):
        nodes = parser.to_compact(XML, "x.xml")["nodes"]
        assert nodes["p"][0] == -1
        assert nodes["t"][0] == "xml"
        assert nodes["l"][0] == "x.xml"
        assert nodes["v"][0] is None
        assert nodes["id"][0] == "root"
        assert nodes["loc"][0] == ""

        assert nodes["t"][1] == "element"
        assert nodes["l"][1] == "R"
        assert nodes["id"][1] == "root/R"
        assert nodes["loc"][1] == "/R[1]"
        # 元素有子元素 → 自身没有值
        assert nodes["v"][1] is None

    def test_attributes_keep_source_order_and_are_skipped_for_xmlns(self):
        content = b'<R xmlns:xsi="urn:xsi" zebra="z" alpha="a"/>'
        nodes = parser.to_compact(content, "x.xml")["nodes"]
        assert labels(nodes) == ["x.xml", "R", "@zebra", "@alpha"]
        assert nodes["id"][2] == "root/R@zebra"
        assert nodes["loc"][2] == "/R[1]/@zebra"
        assert nodes["v"][2] == "z"

    def test_same_name_siblings_get_occurrence_locators(self):
        nodes = parser.to_compact(XML, "x.xml")["nodes"]
        items = [loc for loc in nodes["loc"] if loc.endswith(("item[1]", "item[2]"))]
        assert items == ["/R[1]/item[1]", "/R[1]/item[2]"]
        # 旧 ID 对同名兄弟是共享的（既有语义，§6.2 注）
        assert nodes["id"][loc_of(nodes, "/R[1]/item[1]")] == "root/R/item"
        assert nodes["id"][loc_of(nodes, "/R[1]/item[2]")] == "root/R/item"

    def test_text_only_element_has_value_without_text_row(self):
        nodes = parser.to_compact(b"<R><host>localhost</host></R>", "x.xml")["nodes"]
        host = loc_of(nodes, "/R[1]/host[1]")
        assert nodes["v"][host] == "localhost"
        assert "/R[1]/host[1]/#text" not in nodes["loc"]

    def test_mixed_content_emits_text_row_in_order(self):
        nodes = parser.to_compact(XML, "x.xml")["nodes"]
        seq = list(zip(nodes["t"], nodes["l"], nodes["loc"]))
        # element → 属性 → #text → 子元素
        assert seq[0] == ("xml", "x.xml", "")
        assert seq[1] == ("element", "R", "/R[1]")
        assert seq[2] == ("attribute", "@a", "/R[1]/@a")
        assert seq[3] == ("element", "item", "/R[1]/item[1]")
        item2 = loc_of(nodes, "/R[1]/item[2]")
        assert (nodes["t"][item2 + 1], nodes["l"][item2 + 1], nodes["loc"][item2 + 1]) == (
            "text",
            "#text",
            "/R[1]/item[2]/#text",
        )
        assert nodes["v"][item2 + 1] == "y"
        assert nodes["id"][item2 + 1] == "root/R/item#text"

    def test_comments_dropped_and_tail_ignored(self):
        nodes = parser.to_compact(b"<R><!-- c --><a>1</a>tail<b>2</b></R>", "x.xml")["nodes"]
        assert labels(nodes) == ["x.xml", "R", "a", "b"]
        assert "tail" not in nodes["v"]

    def test_param_metadata_available_as_attributes(self):
        nodes = parser.to_compact(XML, "x.xml")["nodes"]
        param = loc_of(nodes, "/R[1]/param_1[1]")
        assert nodes["v"][param] == "5"
        attr_labels = {nodes["l"][i] for i, parent in enumerate(nodes["p"]) if parent == param}
        assert attr_labels == {"@description", "@min", "@max", "@type"}

    def test_locators_are_unique(self):
        nodes = parser.to_compact(XML, "x.xml")["nodes"]
        assert len(set(nodes["loc"])) == len(nodes["loc"])

    def test_parent_indices_point_backwards(self):
        nodes = parser.to_compact(XML, "x.xml")["nodes"]
        for index, parent in enumerate(nodes["p"]):
            assert parent < index
            if parent >= 0:
                assert nodes["t"][parent] in ("xml", "element", "object", "array")


class TestXmlNamespaces:
    def test_prefixed_label_but_et_tag_for_id_and_loc(self):
        content = b'<a:root xmlns:a="urn:x"><a:child a:k="v"/></a:root>'
        nodes = parser.to_compact(content, "ns.xml")["nodes"]
        # 顺序：元素 → 该元素的属性 → 子元素
        assert nodes["l"] == ["ns.xml", "a:root", "a:child", "@a:k"]
        # id/loc/occurrence 用 ET 原始 tag（§6.2）：元素 {uri}local，属性同理
        assert nodes["id"][1] == "root/{urn:x}root"
        assert nodes["loc"][1] == "/{urn:x}root[1]"
        assert nodes["id"][2] == "root/{urn:x}root/{urn:x}child"
        assert nodes["loc"][2] == "/{urn:x}root[1]/{urn:x}child[1]"
        assert nodes["id"][3] == "root/{urn:x}root/{urn:x}child@{urn:x}k"
        assert nodes["loc"][3] == "/{urn:x}root[1]/{urn:x}child[1]/@{urn:x}k"
        assert nodes["v"][3] == "v"

    def test_undeclared_prefix_is_completed_with_warning(self):
        result = parser.to_compact(b"<a:r><a:c>1</a:c></a:r>", "ns.xml")
        assert result["format"] == "xml"
        assert result["warnings"]
        assert "未声明的命名空间前缀" in result["warnings"][0]
        assert result["nodes"]["l"][1] == "a:r"

    def test_junk_after_document_element_is_tolerated(self):
        result = parser.to_compact(b"<R><a>1</a></R>\n<stray/>", "x.xml")
        assert result["warnings"]
        assert result["nodes"]["l"][1] == "R"

    def test_entity_declaration_is_rejected(self):
        content = (
            b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "boom">]><r>&a;</r>'
        )
        with pytest.raises(AppError) as excinfo:
            parser.to_compact(content, "x.xml")
        assert excinfo.value.code == PARSE_FAILED
        assert "实体声明" in str(excinfo.value)

    def test_invalid_xml_is_parse_failed(self):
        with pytest.raises(AppError) as excinfo:
            parser.to_compact(b"<R><unclosed></R>", "x.xml")
        assert excinfo.value.code == PARSE_FAILED


class TestLargeXml:
    @staticmethod
    def _large_xml(count: int = 4000) -> bytes:
        parts = [b"<root>"]
        for i in range(count):
            parts.append(
                ('<item%d custom_attr="v%d" description="d%d">%d</item%d>' % (i, i, i, i, i)).encode()
            )
        parts.append(b"</root>")
        return b"".join(parts)

    def test_large_xml_is_over_threshold(self):
        assert len(self._large_xml()) > parser._LARGE_XML_BYTES_THRESHOLD

    def test_large_xml_keeps_non_allowlisted_attributes(self):
        # C04/F06：旧实现会按白名单丢掉 custom_attr，to_compact 必须保留
        nodes = parser.to_compact(self._large_xml(), "large.xml")["nodes"]
        assert "/root[1]/item7[1]/@custom_attr" in nodes["loc"]
        index = loc_of(nodes, "/root[1]/item7[1]/@custom_attr")
        assert nodes["v"][index] == "v7"

    def test_old_id_set_matches_parse_file(self):
        with open(TEST_XML_PATH, "rb") as fh:
            content = fh.read()
        compact_ids = set(parser.to_compact(content, "test.xml")["nodes"]["id"])
        old_tree = parser.parse_file(content, "test.xml")
        old_ids = {node["id"] for node in parser.flatten_tree(old_tree)}
        assert compact_ids == old_ids


class TestJsonCompact:
    def test_root_node(self):
        nodes = parser.to_compact(JSONTXT, "c.json")["nodes"]
        assert nodes["p"][0] == -1
        assert nodes["t"][0] == "json"
        assert nodes["l"][0] == "c.json"
        assert nodes["id"][0] == "root"
        assert nodes["loc"][0] == ""

    def test_pointer_escaping_and_old_ids(self):
        nodes = parser.to_compact(JSONTXT, "c.json")["nodes"]
        index = loc_of(nodes, "/a/b~1c")
        assert nodes["id"][index] == "root.a.b/c"
        assert nodes["l"][index] == "b/c"
        assert nodes["v"][index] == "1"
        assert nodes["t"][index] == "int"

        tilde = parser.to_compact(b'{"k~y":2}', "c.json")["nodes"]
        assert "/k~0y" in tilde["loc"]

    def test_types_and_value_formatting(self):
        content = b'{"t":true,"f":false,"n":null,"i":3,"x":1.5,"s":"v"}'
        nodes = parser.to_compact(content, "c.json")["nodes"]
        found = {nodes["l"][i]: (nodes["t"][i], nodes["v"][i]) for i in range(1, len(nodes["p"]))}
        assert found["t"] == ("bool", "true")
        assert found["f"] == ("bool", "false")
        assert found["n"] == ("NoneType", "null")
        assert found["i"] == ("int", "3")
        assert found["x"] == ("float", "1.5")
        assert found["s"] == ("str", "v")

    def test_container_types_and_nested_order(self):
        content = b'{"a":{"b":1},"arr":[1,{"k":"v"},[2]]}'
        nodes = parser.to_compact(content, "c.json")["nodes"]
        assert labels(nodes) == ["c.json", "a", "b", "arr", "[0]", "[1]", "k", "[2]", "[0]"]
        assert nodes["t"][1] == "object"
        assert nodes["t"][3] == "array"
        assert nodes["t"][5] == "object"
        assert nodes["t"][7] == "array"
        assert nodes["id"][loc_of(nodes, "/arr/1/k")] == "root.arr[1].k"
        assert nodes["loc"][-1] == "/arr/2/0"

    def test_scalar_document_has_only_root(self):
        nodes = parser.to_compact(b"42", "c.json")["nodes"]
        assert len(nodes["p"]) == 1

    def test_invalid_json_is_parse_failed(self):
        with pytest.raises(AppError) as excinfo:
            parser.to_compact(b"{not json", "c.json")
        assert excinfo.value.code == PARSE_FAILED

    def test_locators_are_unique(self):
        nodes = parser.to_compact(JSONTXT, "c.json")["nodes"]
        assert len(set(nodes["loc"])) == len(nodes["loc"])


class TestFormatDetection:
    def test_known_extensions(self):
        assert parser.to_compact(XML, "x.xml")["format"] == "xml"
        assert parser.to_compact(JSONTXT, "c.json")["format"] == "json"

    def test_unknown_extension_sniffs_content(self):
        assert parser.to_compact(JSONTXT, "blob")["format"] == "json"
        assert parser.to_compact(XML, "blob")["format"] == "xml"

    def test_unknown_extension_garbage_is_parse_failed(self):
        with pytest.raises(AppError) as excinfo:
            parser.to_compact(b"not xml or json {}", "blob")
        assert excinfo.value.code == PARSE_FAILED


class TestLimits:
    def test_node_limit_is_enforced(self, monkeypatch):
        monkeypatch.setattr(parser, "MAX_NODES", 3)
        with pytest.raises(AppError) as excinfo:
            parser.to_compact(XML, "x.xml")
        assert excinfo.value.code == TOO_LARGE

    def test_depth_limit_is_enforced(self, monkeypatch):
        monkeypatch.setattr(parser, "MAX_DEPTH", 3)
        with pytest.raises(AppError) as excinfo:
            parser.to_compact(b"<r><r><r><r><r>1</r></r></r></r></r>", "d.xml")
        assert excinfo.value.code == TOO_LARGE

    def test_deep_document_within_limit_does_not_recurse(self):
        # 显式栈实现：512 层深文档不应触发 RecursionError
        depth = parser.MAX_DEPTH
        content = (b"<r>" * depth) + b"x" + (b"</r>" * depth)
        nodes = parser.to_compact(content, "d.xml")["nodes"]
        assert len(nodes["p"]) == depth + 1
        assert nodes["v"][-1] == "x"

    def test_deep_json_within_limit(self, monkeypatch):
        monkeypatch.setattr(parser, "MAX_DEPTH", 5)
        content = json.dumps({"a": {"b": {"c": {"d": {"e": 1}}}}}).encode("utf-8")
        nodes = parser.to_compact(content, "d.json")["nodes"]
        assert labels(nodes) == ["d.json", "a", "b", "c", "d", "e"]
