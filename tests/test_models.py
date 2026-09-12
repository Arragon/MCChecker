"""核心数据模型测试：FileRef, SourceSnapshot, NodeRef, ParsedDocument"""

import pytest
from app.core.models import FileRef, FileKind, SourceSnapshot, NodeRef, ParsedDocument


class TestFileRef:
    def test_create_current(self):
        ref = FileRef("default", FileKind.CURRENT, "config.xml")
        assert ref.kind == FileKind.CURRENT
        assert ref.profile_id == "default"
        assert ref.name == "config.xml"
        assert ref.version_or_token is None

    def test_create_with_version(self):
        ref = FileRef("default", FileKind.ARCHIVE, "config.xml", "v1")
        assert str(ref) == "archive:config.xml@v1"

    def test_str_without_version(self):
        ref = FileRef("default", FileKind.CURRENT, "config.xml")
        assert str(ref) == "current:config.xml"

    def test_str_record(self):
        ref = FileRef("p1", FileKind.RECORD, "data.json", "tok123")
        assert str(ref) == "record:data.json@tok123"

    def test_str_temp(self):
        ref = FileRef("p1", FileKind.TEMP, "tmp.xml")
        assert str(ref) == "temp:tmp.xml"

    def test_frozen(self):
        ref = FileRef("default", FileKind.CURRENT, "config.xml")
        with pytest.raises(AttributeError):
            ref.name = "other.xml"

    def test_frozen_kind(self):
        ref = FileRef("default", FileKind.CURRENT, "config.xml")
        with pytest.raises(AttributeError):
            ref.kind = FileKind.ARCHIVE

    def test_equality(self):
        ref1 = FileRef("default", FileKind.CURRENT, "config.xml")
        ref2 = FileRef("default", FileKind.CURRENT, "config.xml")
        assert ref1 == ref2

    def test_inequality(self):
        ref1 = FileRef("default", FileKind.CURRENT, "config.xml")
        ref2 = FileRef("default", FileKind.ARCHIVE, "config.xml")
        assert ref1 != ref2


class TestSourceSnapshot:
    def test_create(self):
        ref = FileRef("default", FileKind.CURRENT, "config.xml")
        snap = SourceSnapshot(ref, "abc123", "xml", "2.0-full", "/path/to/config.xml")
        assert snap.content_hash == "abc123"
        assert snap.format == "xml"
        assert snap.parser_version == "2.0-full"
        assert snap.source_handle == "/path/to/config.xml"

    def test_frozen(self):
        ref = FileRef("default", FileKind.CURRENT, "config.xml")
        snap = SourceSnapshot(ref, "abc123", "xml", "2.0-full", "handle")
        with pytest.raises(AttributeError):
            snap.content_hash = "def456"

    def test_is_large_file_default(self):
        ref = FileRef("default", FileKind.CURRENT, "big.xml")
        snap = SourceSnapshot(ref, "h", "xml", "2.0-full", "big.xml")
        assert snap.is_large_file is False  # 默认实现


class TestNodeRef:
    def test_json_locator(self):
        ref = NodeRef("abc123", "$/config/name", "string", "test")
        assert ref.locator == "$/config/name"
        assert ref.value_type == "string"
        assert ref.original_value == "test"

    def test_matches(self):
        ref1 = NodeRef("abc123", "$/config/name", "string", "test")
        ref2 = NodeRef("abc123", "$/config/name", "string", "test")
        assert ref1.matches(ref2)

    def test_not_matches_different_hash(self):
        ref1 = NodeRef("abc123", "$/config/name", "string", "test")
        ref2 = NodeRef("def456", "$/config/name", "string", "test")
        assert not ref1.matches(ref2)

    def test_not_matches_different_locator(self):
        ref1 = NodeRef("abc123", "$/config/name", "string", "test")
        ref2 = NodeRef("abc123", "$/config/other", "string", "test")
        assert not ref1.matches(ref2)

    def test_matches_ignores_value(self):
        """matches 只看 hash + locator，不看 value_type / original_value"""
        ref1 = NodeRef("abc123", "$/x", "string", "hello")
        ref2 = NodeRef("abc123", "$/x", "number", 42)
        assert ref1.matches(ref2)

    def test_frozen(self):
        ref = NodeRef("abc", "$/x", "string", "v")
        with pytest.raises(AttributeError):
            ref.locator = "$/y"

    def test_xml_locator(self):
        ref = NodeRef("abc", "xml:server[1]", "element", None)
        assert ref.locator == "xml:server[1]"
        assert ref.value_type == "element"


class TestParsedDocumentGetNode:
    """测试 ParsedDocument.get_node 的 JSON Pointer 解析"""

    def _make_json_doc(self, data_dict):
        """辅助：从 dict 构建一个简易 ParsedDocument"""
        from app.core.parser import parse_json_full
        import json
        content = json.dumps(data_dict).encode("utf-8")
        return parse_json_full(content, "test.json")

    def test_get_node_root(self):
        doc = self._make_json_doc({"a": 1})
        ref = NodeRef(doc.source.content_hash, "$", "object", None)
        node = doc.get_node(ref)
        assert node is not None
        assert node["type"] == "object"

    def test_get_node_nested(self):
        doc = self._make_json_doc({"config": {"name": "hello"}})
        ref = NodeRef(doc.source.content_hash, "$/config/name", "string", "hello")
        node = doc.get_node(ref)
        assert node is not None
        assert node["value"] == "hello"

    def test_get_node_wrong_hash(self):
        doc = self._make_json_doc({"a": 1})
        ref = NodeRef("wrong_hash", "$/a", "number", 1)
        assert doc.get_node(ref) is None

    def test_get_node_missing_path(self):
        doc = self._make_json_doc({"a": 1})
        ref = NodeRef(doc.source.content_hash, "$/nonexistent", "string", "")
        assert doc.get_node(ref) is None
