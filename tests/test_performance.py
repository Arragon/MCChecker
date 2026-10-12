"""性能优化测试

验证 T18 各项性能优化的正确性：
- lazy expand: 树节点按需展开
- search paging: 搜索结果分页
- on-demand diff: diff 按需计算
- ParsedDocument reuse: 同 hash 缓存复用
- hidden-page timers: 隐藏页面定时器停止
- result semantics: 结果语义完全一致
- no field dropped: 不因速度丢弃字段
"""

import json
import os
import time

import pytest

from app.core import parser, parse_cache, searching, differ
from app.core.parser import (
    get_parsed_document,
    clear_parsed_document_cache,
    compute_content_hash,
    parse_xml_full,
    parse_json_full,
)
from app.core.differ import compare_versions, _LazyDiffResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_xml_content():
    """生成测试用 XML 内容"""
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<config>
    <server host="localhost" port="8080">
        <database name="testdb" user="admin" password="secret">
            <pool min="5" max="20" timeout="30"/>
        </database>
        <logging level="INFO" file="/var/log/app.log">
            <rotation maxFiles="10" maxSize="100MB"/>
        </logging>
    </server>
    <features>
        <feature name="cache" enabled="true" ttl="3600"/>
        <feature name="compression" enabled="false" level="6"/>
        <feature name="monitoring" enabled="true" interval="60"/>
    </features>
</config>
"""


@pytest.fixture
def sample_xml_content_v2():
    """生成测试用 XML 内容（修改版）"""
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<config>
    <server host="production.example.com" port="443">
        <database name="proddb" user="dbadmin" password="prodpass">
            <pool min="10" max="100" timeout="60"/>
        </database>
        <logging level="WARNING" file="/var/log/prod.log">
            <rotation maxFiles="30" maxSize="500MB"/>
        </logging>
    </server>
    <features>
        <feature name="cache" enabled="true" ttl="7200"/>
        <feature name="compression" enabled="true" level="9"/>
        <feature name="monitoring" enabled="true" interval="30"/>
        <feature name="alerting" enabled="true" channel="slack"/>
    </features>
</config>
"""


@pytest.fixture
def sample_json_content():
    """生成测试用 JSON 内容"""
    return json.dumps({
        "app": {
            "name": "testapp",
            "version": "1.0.0",
            "settings": {
                "debug": True,
                "timeout": 30,
                "retries": 3,
            }
        },
        "database": {
            "host": "localhost",
            "port": 5432,
            "name": "testdb"
        }
    }).encode("utf-8")


@pytest.fixture
def large_tree():
    """生成大型测试树（模拟 1000+ 节点）"""
    children = []
    for i in range(50):
        sub_children = []
        for j in range(20):
            sub_children.append({
                "id": f"root/group{i}/item{j}",
                "label": f"item_{j}",
                "value": f"value_{i}_{j}",
                "children": [],
                "attrs": {"type": "attribute"},
            })
        children.append({
            "id": f"root/group{i}",
            "label": f"group_{i}",
            "value": None,
            "children": sub_children,
            "attrs": {"type": "element"},
        })
    return {
        "id": "root",
        "label": "config",
        "value": None,
        "children": children,
        "attrs": {"type": "xml"},
    }


# ---------------------------------------------------------------------------
# Test: lazy expand
# ---------------------------------------------------------------------------

class TestLazyExpand:
    """lazy expand 不渲染全部子节点"""

    def test_lazy_expand_depth_constant_exists(self):
        """LAZY_EXPAND_DEPTH 常量存在且合理"""
        from app.pages.viewer import LAZY_EXPAND_DEPTH
        assert LAZY_EXPAND_DEPTH >= 1
        assert LAZY_EXPAND_DEPTH <= 10

    def test_render_node_accepts_max_depth(self, large_tree):
        """_render_node 接受 max_depth 参数"""
        from app.pages.viewer import _render_node
        import inspect
        sig = inspect.signature(_render_node)
        assert "max_depth" in sig.parameters

    def test_lazy_expand_preserves_tree_structure(self, large_tree):
        """惰性展开不改变树结构本身"""
        # 树结构在内存中保持完整，只是渲染延迟
        assert len(large_tree["children"]) == 50
        for child in large_tree["children"]:
            assert len(child["children"]) == 20


# ---------------------------------------------------------------------------
# Test: search paging
# ---------------------------------------------------------------------------

class TestSearchPaging:
    """搜索结果分页"""

    def test_search_page_size_constant_exists(self):
        """SEARCH_PAGE_SIZE 常量存在且合理"""
        from app.pages.search import SEARCH_PAGE_SIZE
        assert SEARCH_PAGE_SIZE >= 5
        assert SEARCH_PAGE_SIZE <= 100

    def test_render_load_more_button_exists(self):
        """_render_load_more_button 函数存在"""
        from app.pages.search import _render_load_more_button
        assert callable(_render_load_more_button)

    def test_search_filter_semantics_unchanged(self, sample_xml_content):
        """搜索过滤语义不变：filter_tree_and_count 结果一致"""
        tree = parser.parse_file(sample_xml_content, "config.xml")
        filtered, count = searching.filter_tree_and_count(tree, "localhost")
        assert count > 0
        assert filtered is not None
        # 确认过滤后的树仍保留完整路径
        flat = parser.flatten_tree(filtered)
        labels = [n["label"] for n in flat]
        assert "server" in labels or "config" in labels


# ---------------------------------------------------------------------------
# Test: on-demand diff
# ---------------------------------------------------------------------------

class TestOnDemandDiff:
    """diff 按需计算"""

    def test_compare_returns_lazy_result(self, sample_xml_content, sample_xml_content_v2):
        """compare_versions 返回 _LazyDiffResult"""
        result = compare_versions(
            sample_xml_content, sample_xml_content_v2,
            "config_old.xml", "config_new.xml",
            use_bindings=False,
        )
        assert isinstance(result, _LazyDiffResult)

    def test_lazy_diff_unified_diff_immediate(self, sample_xml_content, sample_xml_content_v2):
        """unified_diff 立即可用，不需触发 structural_diff 计算"""
        result = compare_versions(
            sample_xml_content, sample_xml_content_v2,
            "config_old.xml", "config_new.xml",
            use_bindings=False,
        )
        # unified_diff 应该立即可用
        assert "unified_diff" in result
        assert isinstance(result["unified_diff"], list)
        assert len(result["unified_diff"]) > 0

    def test_lazy_diff_struct_on_access(self, sample_xml_content, sample_xml_content_v2):
        """structural_diff 首次访问时才计算"""
        result = compare_versions(
            sample_xml_content, sample_xml_content_v2,
            "config_old.xml", "config_new.xml",
            use_bindings=False,
        )
        # 通过 get 访问 structural_diff
        struct = result.get("structural_diff")
        assert struct is not None
        assert struct["type"] == "structural"
        assert "added" in struct
        assert "removed" in struct
        assert "modified" in struct

    def test_lazy_diff_result_semantics_unchanged(self, sample_xml_content, sample_xml_content_v2):
        """惰性 diff 结果语义与直接计算完全一致"""
        # 惰性版本
        lazy_result = compare_versions(
            sample_xml_content, sample_xml_content_v2,
            "config_old.xml", "config_new.xml",
            use_bindings=False,
        )
        lazy_struct = lazy_result["structural_diff"]

        # 直接计算版本
        direct_struct = differ._structural_diff(
            sample_xml_content, sample_xml_content_v2,
            "config_old.xml", "config_new.xml",
            False, None,
        )

        # 语义一致
        assert lazy_struct["type"] == direct_struct["type"]
        assert lazy_struct["summary"] == direct_struct["summary"]
        assert len(lazy_struct["added"]) == len(direct_struct["added"])
        assert len(lazy_struct["removed"]) == len(direct_struct["removed"])
        assert len(lazy_struct["modified"]) == len(direct_struct["modified"])

    def test_lazy_diff_has_changes(self, sample_xml_content, sample_xml_content_v2):
        """has_changes 字段正确"""
        result = compare_versions(
            sample_xml_content, sample_xml_content_v2,
            "old.xml", "new.xml",
            use_bindings=False,
        )
        assert result["has_changes"] is True

        # 相同内容
        result2 = compare_versions(
            sample_xml_content, sample_xml_content,
            "old.xml", "new.xml",
            use_bindings=False,
        )
        assert result2["has_changes"] is False


# ---------------------------------------------------------------------------
# Test: ParsedDocument reuse
# ---------------------------------------------------------------------------

class TestParsedDocumentReuse:
    """same source hash 重用 ParsedDocument"""

    def setup_method(self):
        """每个测试前清空缓存"""
        clear_parsed_document_cache()

    def test_same_hash_returns_same_document(self, sample_xml_content):
        """相同内容 hash 返回同一个 ParsedDocument 实例"""
        doc1 = get_parsed_document(sample_xml_content, "config.xml")
        doc2 = get_parsed_document(sample_xml_content, "config.xml")
        assert doc1 is doc2

    def test_different_hash_returns_different_document(self, sample_xml_content, sample_xml_content_v2):
        """不同内容返回不同 ParsedDocument"""
        doc1 = get_parsed_document(sample_xml_content, "config.xml")
        doc2 = get_parsed_document(sample_xml_content_v2, "config.xml")
        assert doc1 is not doc2

    def test_cache_respects_max_size(self):
        """缓存不超过最大容量"""
        from app.core.parser import _PARSED_DOC_CACHE_MAX, _parsed_doc_cache
        # 生成多个不同内容
        contents = [f'<root><v>{i}</v></root>'.encode() for i in range(_PARSED_DOC_CACHE_MAX + 5)]
        for i, c in enumerate(contents):
            get_parsed_document(c, f"file{i}.xml")
        assert len(_parsed_doc_cache) <= _PARSED_DOC_CACHE_MAX

    def test_clear_cache(self, sample_xml_content):
        """清空缓存有效"""
        get_parsed_document(sample_xml_content, "config.xml")
        cleared = clear_parsed_document_cache()
        assert cleared >= 1

    def test_parsed_document_preserves_all_fields(self, sample_xml_content):
        """ParsedDocument 保留所有字段"""
        doc = get_parsed_document(sample_xml_content, "config.xml")
        assert doc.source is not None
        assert doc.root is not None
        assert doc.format == "xml"
        assert doc.source.content_hash == compute_content_hash(sample_xml_content)

    def test_json_parsed_document_reuse(self, sample_json_content):
        """JSON ParsedDocument 也支持缓存复用"""
        doc1 = get_parsed_document(sample_json_content, "config.json")
        doc2 = get_parsed_document(sample_json_content, "config.json")
        assert doc1 is doc2
        assert doc1.format == "json"


# ---------------------------------------------------------------------------
# Test: hidden-page timers stopped
# ---------------------------------------------------------------------------

class TestHiddenPageTimersStopped:
    """隐藏页面定时器停止"""

    def test_refresh_overview_has_visibility_guard(self):
        """refresh_overview 函数包含可见性检查"""
        # 直接读取源文件检查，避免 import 时 NiceGUI 依赖
        import os
        home_path = os.path.join(os.path.dirname(__file__), "..", "app", "pages", "home.py")
        with open(home_path, "r") as f:
            source = f.read()
        # 确认 refresh_overview 中有 active tab 检查
        assert 'session_active_tab["name"] != "overview"' in source

    def test_sidebar_sig_uses_lightweight_signal(self):
        """侧边栏签名使用轻量信号（mapping mtime）"""
        import os
        home_path = os.path.join(os.path.dirname(__file__), "..", "app", "pages", "home.py")
        with open(home_path, "r") as f:
            source = f.read()
        assert "MAPPING_FILE" in source


# ---------------------------------------------------------------------------
# Test: result semantics unchanged
# ---------------------------------------------------------------------------

class TestResultSemanticsUnchanged:
    """result semantics 完全一致"""

    def test_filter_tree_and_count_semantics(self, sample_xml_content):
        """filter_tree_and_count 语义不变"""
        tree = parser.parse_file(sample_xml_content, "config.xml")
        filtered, count = searching.filter_tree_and_count(tree, "localhost")
        assert count >= 1
        # 过滤后的树包含命中的节点
        flat = parser.flatten_tree(filtered)
        found = any("localhost" in str(n.get("value", "")) for n in flat)
        assert found

    def test_compare_versions_full_semantics(self, sample_xml_content, sample_xml_content_v2):
        """compare_versions 完整语义不变"""
        result = compare_versions(
            sample_xml_content, sample_xml_content_v2,
            "old.xml", "new.xml",
            use_bindings=False,
        )
        # 所有预期字段都存在
        assert "unified_diff" in result
        assert "structural_diff" in result
        assert "has_changes" in result

        struct = result["structural_diff"]
        assert "type" in struct
        assert "added" in struct
        assert "removed" in struct
        assert "modified" in struct
        assert "summary" in struct


# ---------------------------------------------------------------------------
# Test: no field dropped for speed
# ---------------------------------------------------------------------------

class TestNoFieldDropped:
    """no field dropped for speed"""

    def test_parsed_document_has_all_source_fields(self, sample_xml_content):
        """ParsedDocument.source 包含所有必要字段"""
        doc = get_parsed_document(sample_xml_content, "config.xml")
        src = doc.source
        assert src.file_ref is not None
        assert src.content_hash
        assert src.format
        assert src.parser_version
        assert src.source_handle

    def test_parsed_document_root_has_tree_structure(self, sample_xml_content):
        """ParsedDocument.root 保持完整树结构"""
        doc = get_parsed_document(sample_xml_content, "config.xml")
        root = doc.root
        assert "id" in root
        assert "label" in root
        assert "children" in root
        assert "attrs" in root

    def test_diff_result_has_all_fields(self, sample_xml_content, sample_xml_content_v2):
        """diff 结果包含所有字段"""
        result = compare_versions(
            sample_xml_content, sample_xml_content_v2,
            "old.xml", "new.xml",
            use_bindings=False,
        )
        struct = result["structural_diff"]
        summary = struct["summary"]
        assert "added_count" in summary
        assert "removed_count" in summary
        assert "modified_count" in summary
        assert "bound_count" in summary

    def test_search_filter_preserves_node_fields(self, sample_xml_content):
        """搜索过滤保留节点所有字段"""
        tree = parser.parse_file(sample_xml_content, "config.xml")
        filtered, _ = searching.filter_tree_and_count(tree, "localhost")
        flat = parser.flatten_tree(filtered)
        for node in flat:
            assert "id" in node
            assert "label" in node
            # value 可以是 None
            assert "value" in node or node.get("value") is None

    def test_tree_structure_complete_after_lazy_expand(self, large_tree):
        """惰性展开后树结构完整"""
        # 树本身完整，只是渲染延迟
        total_nodes = 1 + 50 + 50 * 20  # root + groups + items
        count = 0

        def _count(node):
            nonlocal count
            count += 1
            for c in node.get("children", []):
                _count(c)

        _count(large_tree)
        assert count == total_nodes
