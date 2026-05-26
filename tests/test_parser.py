"""XML/JSON 解析引擎测试"""

import json
import pytest
from app.core.parser import parse_file, parse_json, parse_xml, flatten_tree, get_all_values


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
