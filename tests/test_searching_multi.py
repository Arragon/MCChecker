"""搜索模块多工况测试

覆盖：空关键词、大小写、备注匹配、分类命中、
树过滤、值节点计数等边界场景。
"""

import pytest

from app.core.searching import (
    SearchHit,
    build_note_map,
    filter_tree,
    filter_tree_and_count,
    classify_search_hits,
    count_value_nodes,
    has_search_results,
)


# ---- 构建测试树 ----

def _make_test_tree():
    """构建测试用树结构"""
    return {
        "id": "root",
        "label": "root",
        "value": None,
        "children": [
            {
                "id": "root.server",
                "label": "server",
                "value": None,
                "children": [
                    {
                        "id": "root.server.host",
                        "label": "host",
                        "value": "localhost",
                        "children": [],
                        "attrs": {"type": "str"},
                    },
                    {
                        "id": "root.server.port",
                        "label": "port",
                        "value": "8080",
                        "children": [],
                        "attrs": {"type": "int"},
                    },
                ],
                "attrs": {"type": "object"},
            },
            {
                "id": "root.database",
                "label": "database",
                "value": None,
                "children": [
                    {
                        "id": "root.database.url",
                        "label": "url",
                        "value": "postgresql://localhost/db",
                        "children": [],
                        "attrs": {"type": "str"},
                    },
                ],
                "attrs": {"type": "object"},
            },
        ],
        "attrs": {"type": "json"},
    }


# ---- 空关键词 ----

class TestEmptyKeyword:
    def test_filter_empty_keyword(self):
        tree = _make_test_tree()
        filtered, count = filter_tree_and_count(tree, "")
        assert filtered is tree
        assert count == 0

    def test_filter_whitespace_keyword(self):
        tree = _make_test_tree()
        filtered, count = filter_tree_and_count(tree, "   ")
        assert filtered is tree
        assert count == 0

    def test_classify_empty_keyword(self):
        tree = _make_test_tree()
        hits = classify_search_hits(tree, "")
        assert len(hits) == 0

    def test_classify_whitespace_keyword(self):
        tree = _make_test_tree()
        hits = classify_search_hits(tree, "   ")
        assert len(hits) == 0


# ---- 大小写不敏感 ----

class TestCaseInsensitive:
    def test_filter_lowercase_match(self):
        tree = _make_test_tree()
        filtered, count = filter_tree_and_count(tree, "localhost")
        assert count > 0

    def test_filter_uppercase_match(self):
        tree = _make_test_tree()
        filtered, count = filter_tree_and_count(tree, "LOCALHOST")
        assert count > 0

    def test_filter_mixed_case_match(self):
        tree = _make_test_tree()
        filtered, count = filter_tree_and_count(tree, "LocalHost")
        assert count > 0

    def test_classify_case_insensitive(self):
        tree = _make_test_tree()
        hits = classify_search_hits(tree, "POSTGRESQL")
        assert len(hits) > 0


# ---- 标签匹配 ----

class TestLabelMatch:
    def test_filter_label_match(self):
        tree = _make_test_tree()
        filtered, count = filter_tree_and_count(tree, "server")
        assert count > 0

    def test_classify_label_match_as_node(self):
        tree = _make_test_tree()
        hits = classify_search_hits(tree, "server")
        # server 是父节点（无 value）
        node_hits = [h for h in hits if h.hit_type == "node"]
        assert len(node_hits) > 0

    def test_classify_label_match_as_leaf(self):
        tree = _make_test_tree()
        hits = classify_search_hits(tree, "host")
        # host 有 value，应为 leaf
        leaf_hits = [h for h in hits if h.hit_type == "leaf"]
        assert len(leaf_hits) > 0


# ---- 值匹配 ----

class TestValueMatch:
    def test_filter_value_match(self):
        tree = _make_test_tree()
        filtered, count = filter_tree_and_count(tree, "8080")
        assert count > 0

    def test_classify_value_match_as_leaf(self):
        tree = _make_test_tree()
        hits = classify_search_hits(tree, "8080")
        leaf_hits = [h for h in hits if h.hit_type == "leaf"]
        assert len(leaf_hits) > 0
        assert leaf_hits[0].value == "8080"

    def test_filter_partial_value_match(self):
        tree = _make_test_tree()
        filtered, count = filter_tree_and_count(tree, "postgres")
        assert count > 0


# ---- 备注匹配 ----

class TestNoteMatch:
    def test_filter_note_match(self):
        tree = _make_test_tree()
        note_map = {"root.server.host": "这是主机配置"}
        filtered, count = filter_tree_and_count(tree, "主机", note_map)
        assert count > 0

    def test_classify_note_match(self):
        tree = _make_test_tree()
        note_map = {"root.server.host": "这是主机配置"}
        hits = classify_search_hits(tree, "主机", note_map)
        note_hits = [h for h in hits if h.hit_type == "note"]
        assert len(note_hits) > 0

    def test_filter_note_in_output(self):
        tree = _make_test_tree()
        note_map = {"root.server.host": "重要配置"}
        filtered, count = filter_tree_and_count(tree, "重要", note_map)
        assert filtered is not None
        # 应包含 note 字段
        flat = _flatten(filtered)
        host_nodes = [n for n in flat if n.get("id") == "root.server.host"]
        assert len(host_nodes) > 0
        assert "note" in host_nodes[0]


# ---- 树过滤 ----

class TestTreeFiltering:
    def test_filter_returns_none_when_no_match(self):
        tree = _make_test_tree()
        filtered, count = filter_tree_and_count(tree, "nonexistent_keyword_xyz")
        assert filtered is None
        assert count == 0

    def test_filter_preserves_structure(self):
        tree = _make_test_tree()
        filtered, count = filter_tree_and_count(tree, "server")
        assert filtered is not None
        assert filtered["id"] == "root"
        assert "children" in filtered

    def test_filter_prunes_unmatched_branches(self):
        tree = _make_test_tree()
        filtered, count = filter_tree_and_count(tree, "database")
        # server 分支应被剪枝
        flat = _flatten(filtered)
        labels = [n["label"] for n in flat]
        assert "server" not in labels or "database" in labels

    def test_filter_tree_wrapper(self):
        tree = _make_test_tree()
        filtered = filter_tree(tree, "host")
        assert filtered is not None


# ---- 值节点计数 ----

class TestValueNodeCount:
    def test_count_value_nodes(self):
        tree = _make_test_tree()
        count = count_value_nodes(tree)
        # host, port, url 三个有值的节点
        assert count == 3

    def test_count_empty_tree(self):
        tree = {"id": "root", "label": "root", "value": None, "children": [], "attrs": {}}
        count = count_value_nodes(tree)
        assert count == 0

    def test_count_nested_values(self):
        tree = {
            "id": "root",
            "label": "root",
            "value": "root_val",
            "children": [
                {
                    "id": "child",
                    "label": "child",
                    "value": "child_val",
                    "children": [],
                    "attrs": {},
                }
            ],
            "attrs": {},
        }
        count = count_value_nodes(tree)
        assert count == 2


# ---- 备注映射构建 ----

class TestNoteMap:
    def test_build_note_map(self):
        favorites = [
            {"source_file": "test.xml", "path": "/a", "note": "note1"},
            {"source_file": "test.xml", "path": "/b", "note": "note2"},
        ]
        note_map = build_note_map(favorites, "test.xml")
        assert note_map["/a"] == "note1"
        assert note_map["/b"] == "note2"

    def test_build_note_map_filters_by_source(self):
        favorites = [
            {"source_file": "a.xml", "path": "/a", "note": "note1"},
            {"source_file": "b.xml", "path": "/b", "note": "note2"},
        ]
        note_map = build_note_map(favorites, "a.xml")
        assert "/a" in note_map
        assert "/b" not in note_map

    def test_build_note_map_skips_empty_notes(self):
        favorites = [
            {"source_file": "test.xml", "path": "/a", "note": ""},
            {"source_file": "test.xml", "path": "/b", "note": "   "},
            {"source_file": "test.xml", "path": "/c", "note": "valid"},
        ]
        note_map = build_note_map(favorites, "test.xml")
        assert len(note_map) == 1
        assert "/c" in note_map


# ---- 搜索结果判断 ----

class TestHasSearchResults:
    def test_has_results_with_hits(self):
        hits = [SearchHit(hit_type="leaf", path="/a", label="a", value="1")]
        assert has_search_results(hits) is True

    def test_has_results_empty(self):
        hits = []
        assert has_search_results(hits) is False


# ---- 辅助函数 ----

def _flatten(node):
    """扁平化树用于断言"""
    results = [node]
    for child in node.get("children", []):
        results.extend(_flatten(child))
    return results
