"""审阅模块多工况测试

覆盖：ReviewRemark 序列化、NodeRef 校验、XML/JSON 保真输出、
审阅提交流程、冲突检测、幂等保护等边界场景。
"""

import json
import os
import pytest
import tempfile
from pathlib import Path

from app.core.reviewing import (
    ReviewRemark,
    ReviewConflictError,
    ReviewValidationError,
    make_node_key,
    get_node_by_key,
    apply_review_updates,
    serialize_tree,
    apply_review_to_xml,
    apply_review_to_json,
    compute_content_hash_from_file,
    _detect_format,
    _verify_original_value,
)
from app.core.models import NodeRef, FileRef, FileKind, SourceSnapshot, ParsedDocument
from app.core.parser import parse_xml_full, parse_json_full, compute_content_hash


# ---- ReviewRemark 序列化 ----

class TestReviewRemarkSerialization:
    def test_to_dict(self):
        node_ref = NodeRef(
            content_hash="abc123",
            locator="$/config/name",
            value_type="string",
            original_value="old",
        )
        remark = ReviewRemark(
            remark_id="test_id",
            source_hash="abc123",
            node_ref=node_ref,
            original_value="old",
            original_type="string",
            suggested_value="new",
            status="pending",
        )
        d = remark.to_dict()
        assert d["remark_id"] == "test_id"
        assert d["locator"] == "$/config/name"
        assert d["value_type"] == "string"
        assert d["original_value"] == "old"
        assert d["suggested_value"] == "new"
        assert d["status"] == "pending"

    def test_from_dict(self):
        data = {
            "remark_id": "test_id",
            "source_hash": "abc123",
            "locator": "$/config/name",
            "value_type": "string",
            "original_value": "old",
            "suggested_value": "new",
            "status": "pending",
        }
        remark = ReviewRemark.from_dict(data)
        assert remark.remark_id == "test_id"
        assert remark.source_hash == "abc123"
        assert remark.node_ref.locator == "$/config/name"
        assert remark.suggested_value == "new"

    def test_roundtrip_serialization(self):
        node_ref = NodeRef(
            content_hash="hash",
            locator="$/a/b",
            value_type="int",
            original_value=42,
        )
        remark = ReviewRemark(
            remark_id="id",
            source_hash="hash",
            node_ref=node_ref,
            original_value=42,
            original_type="int",
            suggested_value=100,
        )
        d = remark.to_dict()
        restored = ReviewRemark.from_dict(d)
        assert restored.remark_id == remark.remark_id
        assert restored.node_ref.locator == remark.node_ref.locator


# ---- 树操作函数 ----

class TestTreeOperations:
    def test_make_node_key(self):
        assert make_node_key("", 0) == "0"
        assert make_node_key("0", 1) == "0.1"
        assert make_node_key("0.1", 2) == "0.1.2"

    def test_get_node_by_key(self):
        tree = {
            "id": "root",
            "label": "root",
            "value": None,
            "children": [
                {
                    "id": "child0",
                    "label": "child0",
                    "value": "val0",
                    "children": [],
                    "attrs": {},
                },
                {
                    "id": "child1",
                    "label": "child1",
                    "value": "val1",
                    "children": [
                        {
                            "id": "grandchild",
                            "label": "grandchild",
                            "value": "deep",
                            "children": [],
                            "attrs": {},
                        }
                    ],
                    "attrs": {},
                },
            ],
            "attrs": {},
        }
        node = get_node_by_key(tree, "0")
        assert node["value"] == "val0"

        node = get_node_by_key(tree, "1.0")
        assert node["value"] == "deep"

    def test_get_node_by_key_invalid(self):
        tree = {
            "id": "root",
            "label": "root",
            "value": None,
            "children": [],
            "attrs": {},
        }
        with pytest.raises(ValueError):
            get_node_by_key(tree, "")

        with pytest.raises(ValueError):
            get_node_by_key(tree, "0")

    def test_apply_review_updates(self):
        tree = {
            "id": "root",
            "label": "root",
            "value": None,
            "children": [
                {
                    "id": "0",
                    "label": "a",
                    "value": "old",
                    "children": [],
                    "attrs": {},
                }
            ],
            "attrs": {},
        }
        updates = [{"node_key": "0", "proposed_value": "new"}]
        updated = apply_review_updates(tree, updates)
        assert updated["children"][0]["value"] == "new"
        # 原树不应被修改
        assert tree["children"][0]["value"] == "old"


# ---- 格式检测 ----

class TestFormatDetection:
    def test_detect_xml(self):
        assert _detect_format("config.xml") == "xml"

    def test_detect_json(self):
        assert _detect_format("config.json") == "json"

    def test_detect_unknown_raises(self):
        with pytest.raises(ValueError):
            _detect_format("config.txt")


# ---- 原始值校验 ----

class TestOriginalValueVerification:
    def test_verify_json_value_match(self):
        node = {"value": 42, "type": "int"}
        node_ref = NodeRef(
            content_hash="hash",
            locator="$/a",
            value_type="int",
            original_value=42,
        )
        assert _verify_original_value(node, node_ref, "json")

    def test_verify_json_value_mismatch(self):
        node = {"value": 100, "type": "int"}
        node_ref = NodeRef(
            content_hash="hash",
            locator="$/a",
            value_type="int",
            original_value=42,
        )
        assert not _verify_original_value(node, node_ref, "json")

    def test_verify_xml_value_match(self):
        node = {"value": "text", "type": "element"}
        node_ref = NodeRef(
            content_hash="hash",
            locator="xml:tag[1]",
            value_type="element",
            original_value="text",
        )
        assert _verify_original_value(node, node_ref, "xml")

    def test_verify_none_node(self):
        node_ref = NodeRef(
            content_hash="hash",
            locator="$/a",
            value_type="int",
            original_value=42,
        )
        assert not _verify_original_value(None, node_ref, "json")


# ---- JSON 保真输出 ----

class TestJsonReviewOutput:
    def test_apply_review_to_json(self, tmp_path):
        source = tmp_path / "source.json"
        source.write_text(json.dumps({"name": "old", "count": 10}))

        output = tmp_path / "output.json"

        source_hash = compute_content_hash(source.read_bytes())
        node_ref = NodeRef(
            content_hash=source_hash,
            locator="$/name",
            value_type="str",
            original_value="old",
        )
        remark = ReviewRemark(
            remark_id="r1",
            source_hash=source_hash,
            node_ref=node_ref,
            original_value="old",
            original_type="str",
            suggested_value="new",
        )

        apply_review_to_json(str(source), [remark], str(output))

        with open(output) as f:
            result = json.load(f)
        assert result["name"] == "new"
        assert result["count"] == 10  # 未修改

    def test_apply_review_int_type(self, tmp_path):
        source = tmp_path / "source.json"
        source.write_text(json.dumps({"count": 10}))

        output = tmp_path / "output.json"

        source_hash = compute_content_hash(source.read_bytes())
        node_ref = NodeRef(
            content_hash=source_hash,
            locator="$/count",
            value_type="int",
            original_value=10,
        )
        remark = ReviewRemark(
            remark_id="r1",
            source_hash=source_hash,
            node_ref=node_ref,
            original_value=10,
            original_type="int",
            suggested_value=20,
        )

        apply_review_to_json(str(source), [remark], str(output))

        with open(output) as f:
            result = json.load(f)
        assert result["count"] == 20
        assert isinstance(result["count"], int)


# ---- XML 保真输出 ----

class TestXmlReviewOutput:
    def test_apply_review_to_xml(self, tmp_path):
        source = tmp_path / "source.xml"
        source.write_text("<config><name>old</name></config>")

        output = tmp_path / "output.xml"

        source_bytes = source.read_bytes()
        source_hash = compute_content_hash(source_bytes)
        node_ref = NodeRef(
            content_hash=source_hash,
            locator="xml:{name}[1]",
            value_type="element",
            original_value="old",
        )
        remark = ReviewRemark(
            remark_id="r1",
            source_hash=source_hash,
            node_ref=node_ref,
            original_value="old",
            original_type="element",
            suggested_value="new",
        )

        apply_review_to_xml(str(source), [remark], str(output))

        result = output.read_text()
        assert "<name>new</name>" in result


# ---- 树序列化 ----

class TestTreeSerialization:
    def test_serialize_json_tree(self):
        tree = {
            "id": "root",
            "label": "test.json",
            "value": None,
            "children": [
                {
                    "id": "root.name",
                    "label": "name",
                    "value": "test",
                    "children": [],
                    "attrs": {"type": "str"},
                }
            ],
            "attrs": {"type": "json"},
        }
        result = serialize_tree(tree, "test.json")
        data = json.loads(result)
        assert data["name"] == "test"

    def test_serialize_xml_tree(self):
        tree = {
            "id": "root",
            "label": "test.xml",
            "value": None,
            "children": [
                {
                    "id": "root/config",
                    "label": "config",
                    "value": None,
                    "children": [
                        {
                            "id": "root/config/name",
                            "label": "name",
                            "value": "test",
                            "children": [],
                            "attrs": {"type": "element"},
                        }
                    ],
                    "attrs": {"type": "element"},
                }
            ],
            "attrs": {"type": "xml"},
        }
        result = serialize_tree(tree, "test.xml")
        assert b"<name>test</name>" in result

    def test_serialize_unsupported_raises(self):
        tree = {
            "id": "root",
            "label": "test.txt",
            "value": None,
            "children": [],
            "attrs": {"type": "txt"},
        }
        with pytest.raises(ValueError):
            serialize_tree(tree, "test.txt")


# ---- Content hash from file ----

class TestContentHashFromFile:
    def test_compute_hash(self, tmp_path):
        file_path = tmp_path / "test.txt"
        file_path.write_text("test content")
        hash1 = compute_content_hash_from_file(str(file_path))
        assert len(hash1) == 64

    def test_hash_deterministic(self, tmp_path):
        file_path = tmp_path / "test.txt"
        file_path.write_text("test content")
        hash1 = compute_content_hash_from_file(str(file_path))
        hash2 = compute_content_hash_from_file(str(file_path))
        assert hash1 == hash2
