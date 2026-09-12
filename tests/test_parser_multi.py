"""解析引擎多工况测试

覆盖：空内容、嵌套、特殊字符、BOM、命名空间修复、大文件、
损坏内容、缓存命中、格式推断等边界场景。
"""

import json
import pytest

from app.core.parser import (
    parse_file,
    parse_json,
    parse_xml,
    parse_json_full,
    parse_xml_full,
    get_parsed_document,
    clear_parsed_document_cache,
    compute_content_hash,
    flatten_tree,
    get_all_values,
    _preprocess_xml_namespaces,
)
from app.core.models import ParsedDocument


# ---- 空 / 极简内容 ----

class TestEmptyAndMinimal:
    def test_empty_json_object(self):
        result = parse_json(b"{}", "empty.json")
        assert result["children"] == []

    def test_empty_json_array(self):
        result = parse_json(b"[]", "arr.json")
        # 根为 list，children 为空 object 节点
        assert result["attrs"]["type"] == "json"

    def test_minimal_xml(self):
        result = parse_xml(b"<r/>", "min.xml")
        values = get_all_values(result)
        assert len(values) == 0

    def test_xml_with_only_text(self):
        result = parse_xml(b"<r>hello</r>", "t.xml")
        values = get_all_values(result)
        assert "hello" in values.values()

    def test_json_root_scalar_string(self):
        result = parse_json(b'"just a string"', "s.json")
        assert result["attrs"]["type"] == "json"

    def test_json_root_number(self):
        result = parse_json(b"3.14", "n.json")
        assert result["attrs"]["type"] == "json"


# ---- BOM 和编码 ----

class TestEncoding:
    def test_json_utf8_bom(self):
        content = b"\xef\xbb\xbf" + b'{"key": "value"}'
        result = parse_json(content, "bom.json")
        values = get_all_values(result)
        assert "value" in values.values()

    def test_xml_utf8_bom(self):
        content = b"\xef\xbb\xbf" + b"<r><a>1</a></r>"
        result = parse_xml(content, "bom.xml")
        values = get_all_values(result)
        assert "1" in values.values()

    def test_json_unicode_values(self):
        data = json.dumps({"name": "测试中文", "emoji": "🎉"}).encode("utf-8")
        result = parse_json(data, "uni.json")
        values = get_all_values(result)
        assert "测试中文" in values.values()


# ---- 深层嵌套 ----

class TestDeepNesting:
    def test_deeply_nested_json(self):
        data = b'{"a": {"b": {"c": {"d": {"e": "deep"}}}}}'
        result = parse_json(data, "deep.json")
        values = get_all_values(result)
        assert "deep" in values.values()

    def test_deeply_nested_xml(self):
        xml = b"<a><b><c><d><e>deep</e></d></c></b></a>"
        result = parse_xml(xml, "deep.xml")
        values = get_all_values(result)
        assert "deep" in values.values()

    def test_json_full_deep_locator(self):
        data = b'{"a": {"b": {"c": 42}}}'
        doc = parse_json_full(data, "d.json")
        obj = doc.root["children"][0]
        deep = obj["children"]["a"]["children"]["b"]["children"]["c"]
        assert deep["locator"] == "$/a/b/c"
        assert deep["value"] == 42


# ---- 特殊字符 ----

class TestSpecialChars:
    def test_json_key_with_slash(self):
        data = b'{"a/b": 1}'
        doc = parse_json_full(data, "t.json")
        obj = doc.root["children"][0]
        assert "a/b" in obj["children"]
        assert obj["children"]["a/b"]["locator"] == "$/a~1b"

    def test_json_key_with_tilde(self):
        data = b'{"a~b": 1}'
        doc = parse_json_full(data, "t.json")
        obj = doc.root["children"][0]
        assert obj["children"]["a~b"]["locator"] == "$/a~0b"

    def test_json_key_with_spaces(self):
        data = b'{"hello world": "val"}'
        result = parse_json(data, "sp.json")
        values = get_all_values(result)
        assert "val" in values.values()

    def test_json_empty_string_value(self):
        data = b'{"key": ""}'
        result = parse_json(data, "e.json")
        values = get_all_values(result)
        assert any(v == "" for v in values.values())

    def test_xml_mixed_content(self):
        xml = b"<p>Hello <b>bold</b> world</p>"
        result = parse_xml(xml, "mix.xml")
        flat = flatten_tree(result)
        labels = [n["label"] for n in flat]
        assert "#text" in labels


# ---- 命名空间 ----

class TestNamespaces:
    def test_unbound_prefix_auto_fix(self):
        xml = b'<root><ns:item>val</ns:item></root>'
        result = parse_xml(xml, "ns.xml")
        values = get_all_values(result)
        assert "val" in values.values()

    def test_multiple_unbound_prefixes(self):
        xml = b'<root><a:x>1</a:x><b:y>2</b:y></root>'
        text = xml.decode()
        fixed = _preprocess_xml_namespaces(text)
        assert "xmlns:a=" in fixed
        assert "xmlns:b=" in fixed

    def test_declared_namespace_not_duplicated(self):
        xml = b'<root xmlns:ns="http://test.com"><ns:item>val</ns:item></root>'
        text = xml.decode()
        fixed = _preprocess_xml_namespaces(text)
        # 已声明的不应再添加
        assert fixed.count("xmlns:ns=") == 1

    def test_namespace_full_parse(self):
        xml = b'<root xmlns:ns="http://example.com"><ns:item>val</ns:item></root>'
        doc = parse_xml_full(xml, "ns.xml")
        item = doc.root["children"][0]["children"][0]
        assert "{http://example.com}" in item["tag"]


# ---- 损坏内容 ----

class TestCorruptContent:
    def test_invalid_json_raises(self):
        with pytest.raises((json.JSONDecodeError, ValueError)):
            parse_json(b"not json at all", "bad.json")

    def test_invalid_xml_raises(self):
        with pytest.raises(ValueError):
            parse_xml(b"<<<not xml>>>", "bad.xml")

    def test_parse_file_unsupported(self):
        with pytest.raises(ValueError):
            parse_file(b"\x00\x01\x02\x03", "binary.dat")

    def test_xml_empty_content(self):
        with pytest.raises(ValueError):
            parse_xml(b"", "empty.xml")

    def test_json_empty_content(self):
        with pytest.raises(Exception):
            parse_json(b"", "empty.json")


# ---- 格式推断 ----

class TestFormatInference:
    def test_json_extension(self):
        result = parse_file(b'{"a":1}', "config.json")
        assert result["attrs"]["type"] == "json"

    def test_xml_extension(self):
        result = parse_file(b"<r><a/></r>", "config.xml")
        assert result["attrs"]["type"] == "xml"

    def test_unknown_ext_fallback_to_json(self):
        result = parse_file(b'{"a":1}', "config.cfg")
        assert result is not None

    def test_unknown_ext_fallback_to_xml(self):
        result = parse_file(b"<r><a/></r>", "config.cfg")
        assert result is not None

    def test_no_extension(self):
        result = parse_file(b'{"a":1}', "noext")
        assert result is not None


# ---- 缓存 ----

class TestDocumentCache:
    def setup_method(self):
        clear_parsed_document_cache()

    def test_same_content_returns_cached(self):
        content = b'{"x": 1}'
        doc1 = get_parsed_document(content, "a.json")
        doc2 = get_parsed_document(content, "a.json")
        assert doc1 is doc2

    def test_different_content_different_doc(self):
        doc1 = get_parsed_document(b'{"x": 1}', "a.json")
        doc2 = get_parsed_document(b'{"x": 2}', "b.json")
        assert doc1 is not doc2

    def test_cache_clear(self):
        get_parsed_document(b'{"x": 1}', "a.json")
        count = clear_parsed_document_cache()
        assert count >= 1

    def test_format_inferred_from_extension(self):
        doc = get_parsed_document(b'{"x": 1}', "test.json")
        assert doc.format == "json"

    def test_format_xml_from_extension(self):
        doc = get_parsed_document(b"<r/>", "test.xml")
        assert doc.format == "xml"


# ---- XML 重复元素 ----

class TestXmlRepeatElements:
    def test_repeated_siblings(self):
        xml = b"<root><item>A</item><item>B</item><item>C</item></root>"
        result = parse_xml(xml, "rep.xml")
        values = get_all_values(result)
        vals = list(values.values())
        assert "A" in vals
        assert "B" in vals
        assert "C" in vals

    def test_repeated_siblings_full_occurrence(self):
        xml = b"<root><item>A</item><item>B</item></root>"
        doc = parse_xml_full(xml, "rep.xml")
        items = doc.root["children"][0]["children"]
        assert items[0]["occurrence"] == 1
        assert items[1]["occurrence"] == 2

    def test_nested_repeated_elements(self):
        xml = b"<root><group><item>1</item></group><group><item>2</item></group></root>"
        doc = parse_xml_full(xml, "nest.xml")
        groups = doc.root["children"][0]["children"]
        assert len(groups) == 2
        assert groups[0]["occurrence"] == 1
        assert groups[1]["occurrence"] == 2


# ---- JSON 类型保留 ----

class TestJsonTypePreservation:
    def test_int_preserved(self):
        doc = parse_json_full(b'{"n": 42}', "t.json")
        obj = doc.root["children"][0]
        assert obj["children"]["n"]["value_type"] == "int"
        assert obj["children"]["n"]["value"] == 42

    def test_float_preserved(self):
        doc = parse_json_full(b'{"f": 3.14}', "t.json")
        obj = doc.root["children"][0]
        assert obj["children"]["f"]["value_type"] == "float"

    def test_bool_preserved(self):
        doc = parse_json_full(b'{"b": true}', "t.json")
        obj = doc.root["children"][0]
        assert obj["children"]["b"]["value_type"] == "bool"
        assert obj["children"]["b"]["value"] is True

    def test_null_preserved(self):
        doc = parse_json_full(b'{"n": null}', "t.json")
        obj = doc.root["children"][0]
        assert obj["children"]["n"]["value_type"] == "NoneType"
        assert obj["children"]["n"]["value"] is None

    def test_nested_array_types(self):
        doc = parse_json_full(b'{"a": [1, "two", true, null]}', "t.json")
        obj = doc.root["children"][0]
        arr = obj["children"]["a"]
        assert arr["children"][0]["value_type"] == "int"
        assert arr["children"][1]["value_type"] == "str"
        assert arr["children"][2]["value_type"] == "bool"
        assert arr["children"][3]["value_type"] == "NoneType"
