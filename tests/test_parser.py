"""XML/JSON 解析引擎测试"""

import json
import pytest
from app.core.parser import (
    parse_file, parse_json, parse_xml, flatten_tree, get_all_values,
    parse_xml_full, parse_json_full, compute_content_hash,
)
from app.core.models import ParsedDocument, NodeRef


SAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <server>
        <host>localhost</host>
        <port>8080</port>
        <ssl enabled="true">
            <cert>/path/to/cert.pem</cert>
        </ssl>
    </server>
    <logging level="INFO">
        <file>app.log</file>
        <maxSize>10MB</maxSize>
    </logging>
</configuration>
"""

SAMPLE_JSON = b"""{
    "server": {
        "host": "localhost",
        "port": 8080,
        "ssl": {
            "enabled": true,
            "cert": "/path/to/cert.pem"
        }
    },
    "logging": {
        "level": "INFO",
        "file": "app.log",
        "maxSize": "10MB"
    }
}
"""


class TestJSONParser:
    def test_parse_basic_json(self):
        result = parse_json(SAMPLE_JSON, "config.json")
        assert result["id"] == "root"
        assert result["label"] == "config.json"
        assert result["attrs"]["type"] == "json"
        assert len(result["children"]) > 0

    def test_json_tree_structure(self):
        result = parse_json(SAMPLE_JSON, "config.json")
        # 找到 server 子节点
        server_nodes = [c for c in result["children"] if c["label"] == "server"]
        assert len(server_nodes) == 1
        server = server_nodes[0]
        assert server["attrs"]["type"] == "object"
        assert len(server["children"]) > 0

    def test_json_primitive_values(self):
        result = parse_json(SAMPLE_JSON, "config.json")
        flat = flatten_tree(result)
        # 应包含 host 节点
        host_nodes = [n for n in flat if n["label"] == "host"]
        assert len(host_nodes) >= 1
        assert host_nodes[0]["value"] == "localhost"

    def test_json_boolean_value(self):
        result = parse_json(SAMPLE_JSON, "config.json")
        values = get_all_values(result)
        # 布尔值应被格式化为字符串
        has_bool = any(v in ("true", "false") for v in values.values())
        assert has_bool

    def test_json_null_value(self):
        data = b'{"key": null}'
        result = parse_json(data, "test.json")
        values = get_all_values(result)
        assert any(v == "null" for v in values.values())

    def test_json_array(self):
        data = b'{"items": ["a", "b", "c"]}'
        result = parse_json(data, "test.json")
        items_nodes = [c for c in result["children"] if c["label"] == "items"]
        assert len(items_nodes) == 1
        assert items_nodes[0]["attrs"]["type"] == "array"
        assert len(items_nodes[0]["children"]) == 3


class TestXMLParser:
    def test_parse_basic_xml(self):
        result = parse_xml(SAMPLE_XML, "config.xml")
        assert result["id"] == "root"
        assert result["label"] == "config.xml"
        assert result["attrs"]["type"] == "xml"

    def test_xml_element_values(self):
        result = parse_xml(SAMPLE_XML, "config.xml")
        values = get_all_values(result)
        # 应包含 host 和 port
        path_values = set(values.values())
        assert "localhost" in path_values
        assert "8080" in path_values

    def test_xml_attributes(self):
        result = parse_xml(SAMPLE_XML, "config.xml")
        flat = flatten_tree(result)
        # 应包含属性节点
        attr_nodes = [n for n in flat if n["label"].startswith("@")]
        assert len(attr_nodes) > 0
        # ssl 元素应有 enabled 属性
        enabled_attrs = [n for n in attr_nodes if n["label"] == "@enabled"]
        assert len(enabled_attrs) >= 1

    def test_xml_nested_elements(self):
        result = parse_xml(SAMPLE_XML, "config.xml")
        flat = flatten_tree(result)
        # 应有 server/host 路径
        paths = [n["path"] for n in flat]
        has_host = any("host" in p for p in paths)
        assert has_host


class TestParseFile:
    def test_auto_detect_json(self):
        result = parse_file(SAMPLE_JSON, "config.json")
        assert result["attrs"]["type"] == "json"

    def test_auto_detect_xml(self):
        result = parse_file(SAMPLE_XML, "config.xml")
        assert result["attrs"]["type"] == "xml"

    def test_unknown_format_fallback(self):
        result = parse_file(SAMPLE_JSON, "config.unknown")
        # 应自动尝试两种格式
        assert result is not None

    def test_invalid_content(self):
        with pytest.raises(ValueError):
            parse_file(b"not valid xml or json {}", "test.txt")


class TestFlattenTree:
    def test_flatten_json(self):
        result = parse_json(SAMPLE_JSON, "config.json")
        flat = flatten_tree(result)
        assert len(flat) > 0
        assert all("path" in n for n in flat)
        assert all("label" in n for n in flat)

    def test_flatten_xml(self):
        result = parse_xml(SAMPLE_XML, "config.xml")
        flat = flatten_tree(result)
        assert len(flat) > 0

    def test_get_all_values(self):
        result = parse_json(SAMPLE_JSON, "config.json")
        values = get_all_values(result)
        assert isinstance(values, dict)
        assert len(values) > 0
        # 所有值应为字符串
        assert all(isinstance(v, str) for v in values.values())


# ---------------------------------------------------------------------------
# 完整语义解析测试（parse_xml_full / parse_json_full）
# ---------------------------------------------------------------------------

class TestParseXmlFull:
    def test_returns_parsed_document(self):
        doc = parse_xml_full(SAMPLE_XML, "config.xml")
        assert isinstance(doc, ParsedDocument)
        assert doc.format == "xml"
        assert doc.source.content_hash == compute_content_hash(SAMPLE_XML)

    def test_preserves_all_attributes(self):
        """大 XML 也不丢属性：所有 attribute 都保留"""
        xml = b'<root><item a="1" b="2" c="3" d="4" e="5" f="6" g="7" h="8"/></root>'
        doc = parse_xml_full(xml, "test.xml")
        # 找到 item 节点
        item_node = doc.root["children"][0]["children"][0]
        attr_children = [c for c in item_node["children"] if c["attrs"]["type"] == "attribute"]
        assert len(attr_children) == 8  # 全部 8 个属性都保留

    def test_xml_sibling_occurrence(self):
        """重复 sibling 的 occurrence 定位"""
        xml = b'<root><item>A</item><item>B</item><item>C</item></root>'
        doc = parse_xml_full(xml, "test.xml")
        items = doc.root["children"][0]["children"]
        assert len(items) == 3
        assert items[0]["occurrence"] == 1
        assert items[1]["occurrence"] == 2
        assert items[2]["occurrence"] == 3
        assert items[0]["locator"] != items[1]["locator"]

    def test_namespace_handling(self):
        """命名空间展开"""
        xml = b'<root xmlns:ns="http://example.com"><ns:item>val</ns:item></root>'
        doc = parse_xml_full(xml, "test.xml")
        item = doc.root["children"][0]["children"][0]
        assert "{http://example.com}" in item["tag"]

    def test_locator_format(self):
        xml = b'<root><child/></root>'
        doc = parse_xml_full(xml, "test.xml")
        child = doc.root["children"][0]["children"][0]
        assert child["locator"].startswith("xml:")
        assert "[1]" in child["locator"]

    def test_text_and_tail(self):
        xml = b'<root><a>hello</a><b>world</b></root>'
        doc = parse_xml_full(xml, "test.xml")
        a_node = doc.root["children"][0]["children"][0]
        assert a_node["value"] == "hello"

    def test_large_xml_no_attr_filtering(self):
        """超过 200000 bytes 的 XML 也不丢属性"""
        # 构建一个大 XML（填充 padding 属性使其超过阈值）
        attrs = " ".join(f'attr{i}="val{i}"' for i in range(200))
        xml = f'<root><item {attrs}>text</item></root>'.encode("utf-8")
        # 确保超过阈值
        if len(xml) < 200_000:
            padding = "x" * (200_000 - len(xml) + 100)
            xml = f'<root><item {attrs}>{padding}</item></root>'.encode("utf-8")
        doc = parse_xml_full(xml, "big.xml")
        item = doc.root["children"][0]["children"][0]
        attr_children = [c for c in item["children"] if c["attrs"]["type"] == "attribute"]
        assert len(attr_children) == 200  # 全部保留

    def test_unbound_prefix_recovery(self):
        """未绑定前缀的 XML 能正常解析"""
        xml = b'<root><ns:item>val</ns:item></root>'
        doc = parse_xml_full(xml, "test.xml")
        assert doc.format == "xml"
        # 应能解析出子节点
        assert len(doc.root["children"][0]["children"]) >= 1


class TestParseJsonFull:
    def test_returns_parsed_document(self):
        doc = parse_json_full(SAMPLE_JSON, "config.json")
        assert isinstance(doc, ParsedDocument)
        assert doc.format == "json"
        assert doc.source.content_hash == compute_content_hash(SAMPLE_JSON)

    def test_preserves_types(self):
        """保留 int/float/bool/null/str 类型"""
        data = b'{"i": 42, "f": 3.14, "b": true, "n": null, "s": "hello"}'
        doc = parse_json_full(data, "test.json")
        # wrapper.children[0] 是 root object node，其 children 是 dict
        obj = doc.root["children"][0]
        children = obj["children"]
        assert isinstance(children, dict)
        assert children["i"]["value_type"] == "int"
        assert children["i"]["value"] == 42
        assert children["f"]["value_type"] == "float"
        assert children["b"]["value_type"] == "bool"
        assert children["n"]["value_type"] == "NoneType"
        assert children["s"]["value_type"] == "str"
        assert children["s"]["value"] == "hello"

    def test_nested_object(self):
        data = b'{"config": {"name": "test", "count": 5}}'
        doc = parse_json_full(data, "test.json")
        obj = doc.root["children"][0]
        config = obj["children"]["config"]
        assert config["type"] == "object"
        assert config["children"]["name"]["value"] == "test"
        assert config["children"]["count"]["value"] == 5

    def test_array(self):
        data = b'{"items": [1, "two", null]}'
        doc = parse_json_full(data, "test.json")
        obj = doc.root["children"][0]
        items = obj["children"]["items"]
        assert items["type"] == "array"
        assert len(items["children"]) == 3
        assert items["children"][0]["value"] == 1
        assert items["children"][1]["value"] == "two"
        assert items["children"][2]["value"] is None

    def test_empty_object_and_array(self):
        data = b'{"empty_obj": {}, "empty_arr": []}'
        doc = parse_json_full(data, "test.json")
        obj = doc.root["children"][0]
        assert obj["children"]["empty_obj"]["children"] == {}
        assert obj["children"]["empty_arr"]["children"] == []

    def test_key_with_special_chars(self):
        """JSON key 带 / ~ . 特殊字符"""
        data = b'{"a/b": 1, "c~d": 2, "e.f": 3}'
        doc = parse_json_full(data, "test.json")
        obj = doc.root["children"][0]
        children = obj["children"]
        # a/b -> JSON Pointer 中应被 escaped 为 a~1b
        assert "a/b" in children
        assert children["a/b"]["locator"] == "$/a~1b"
        # c~d -> c~0d
        assert children["c~d"]["locator"] == "$/c~0d"
        # e.f 不需要特殊转义
        assert children["e.f"]["locator"] == "$/e.f"

    def test_root_scalar(self):
        """JSON 根为标量"""
        data = b'42'
        doc = parse_json_full(data, "test.json")
        # root scalar is wrapped directly in wrapper children
        children = doc.root["children"]
        assert isinstance(children, list)
        assert len(children) == 1
        assert children[0]["value"] == 42

    def test_type_change_same_key(self):
        """不同值类型变化"""
        data1 = b'{"x": 42}'
        data2 = b'{"x": "hello"}'
        doc1 = parse_json_full(data1, "a.json")
        doc2 = parse_json_full(data2, "b.json")
        obj1 = doc1.root["children"][0]
        obj2 = doc2.root["children"][0]
        assert obj1["children"]["x"]["value_type"] == "int"
        assert obj2["children"]["x"]["value_type"] == "str"

    def test_locator_json_pointer(self):
        data = b'{"a": {"b": {"c": "deep"}}}'
        doc = parse_json_full(data, "test.json")
        obj = doc.root["children"][0]
        deep = obj["children"]["a"]["children"]["b"]["children"]["c"]
        assert deep["locator"] == "$/a/b/c"

    def test_array_index_locator(self):
        data = b'{"arr": [10, 20, 30]}'
        doc = parse_json_full(data, "test.json")
        obj = doc.root["children"][0]
        arr = obj["children"]["arr"]
        assert arr["children"][0]["locator"] == "$/arr/0"
        assert arr["children"][2]["locator"] == "$/arr/2"


class TestBackwardCompat:
    """确保新旧 API 共存，旧 API 行为不变"""

    def test_old_parse_xml_still_works(self):
        result = parse_xml(SAMPLE_XML, "config.xml")
        assert result["attrs"]["type"] == "xml"
        assert result["id"] == "root"

    def test_old_parse_json_still_works(self):
        result = parse_json(SAMPLE_JSON, "config.json")
        assert result["attrs"]["type"] == "json"

    def test_full_and_old_produce_same_tree_shape(self):
        """parse_xml_full 的 wrapper 结构与旧 parse_xml 基本一致"""
        old = parse_xml(SAMPLE_XML, "config.xml")
        new_doc = parse_xml_full(SAMPLE_XML, "config.xml")
        new = new_doc.root
        # 顶层结构一致
        assert old["id"] == new["id"] == "root"
        assert old["attrs"]["type"] == new["attrs"]["type"] == "xml"
        # 子节点数量应相同（单根元素）
        assert len(old["children"]) == len(new["children"]) == 1
