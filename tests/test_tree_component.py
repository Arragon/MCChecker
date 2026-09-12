"""共用树组件与搜索语义测试"""

import pytest

from app.core.searching import (
    SearchHit,
    classify_search_hits,
    filter_tree_and_count,
    has_search_results,
    build_note_map,
)
from app.core.tree_component import (
    build_indent_html,
    build_row_prefix_html,
    is_param_node,
    extract_param_meta,
    make_tree_panel_id,
    make_range_btn_id,
)
from app.core.models import FileRef, FileKind


# ---------------------------------------------------------------------------
# 测试数据构建
# ---------------------------------------------------------------------------

def _make_tree():
    """构建测试用解析树"""
    return {
        "id": "root",
        "label": "root",
        "attrs": {"type": "json"},
        "children": [
            {
                "id": "root.config",
                "label": "config",
                "value": None,
                "attrs": {"type": "object"},
                "children": [
                    {
                        "id": "root.config.server",
                        "label": "server",
                        "value": None,
                        "attrs": {"type": "object"},
                        "children": [
                            {
                                "id": "root.config.server.host",
                                "label": "host",
                                "value": "localhost",
                                "attrs": {"type": "string"},
                                "children": [],
                            },
                            {
                                "id": "root.config.server.port",
                                "label": "port",
                                "value": "8080",
                                "attrs": {"type": "int"},
                                "children": [],
                            },
                        ],
                    },
                    {
                        "id": "root.config.debug",
                        "label": "debug",
                        "value": "true",
                        "attrs": {"type": "bool"},
                        "children": [],
                    },
                ],
            },
            {
                "id": "root.param_settings",
                "label": "param_settings",
                "value": "some_value",
                "attrs": {"type": "object"},
                "children": [
                    {
                        "id": "root.param_settings.@default",
                        "label": "@default",
                        "value": "10",
                        "attrs": {},
                        "children": [],
                    },
                    {
                        "id": "root.param_settings.@min",
                        "label": "@min",
                        "value": "0",
                        "attrs": {},
                        "children": [],
                    },
                    {
                        "id": "root.param_settings.@max",
                        "label": "@max",
                        "value": "100",
                        "attrs": {},
                        "children": [],
                    },
                ],
            },
        ],
    }


def _make_tree_with_note():
    """构建带备注的测试树"""
    tree = _make_tree()
    # 给 server 节点添加 note
    tree["children"][0]["children"][0]["note"] = "这是服务器配置"
    return tree


# ---------------------------------------------------------------------------
# 共用组件基础测试
# ---------------------------------------------------------------------------

class TestTreeComponentBasics:
    """共用树组件基础功能测试"""

    def test_build_indent_html_empty(self):
        """深度 0 无缩进"""
        result = build_indent_html(0)
        assert result == ""

    def test_build_indent_html_depth(self):
        """深度 > 0 有缩进"""
        result = build_indent_html(2)
        assert "indent-cell" in result
        assert result.count("indent-cell") == 2

    def test_build_row_prefix_leaf(self):
        """叶子节点前缀无按钮"""
        result = build_row_prefix_html(0, False)
        assert "leaf-marker" in result
        assert "mc-tree-toggle" not in result

    def test_build_row_prefix_parent(self):
        """父节点前缀有展开按钮"""
        result = build_row_prefix_html(0, True)
        assert "mc-tree-toggle" in result
        assert "aria-expanded" in result

    def test_make_tree_panel_id_unique(self):
        """面板 ID 唯一"""
        id1 = make_tree_panel_id("file1", "kw1")
        id2 = make_tree_panel_id("file2", "kw1")
        assert id1 != id2

    def test_make_tree_panel_id_deterministic(self):
        """面板 ID 确定性"""
        id1 = make_tree_panel_id("file1", "kw1")
        id2 = make_tree_panel_id("file1", "kw1")
        assert id1 == id2

    def test_make_range_btn_id_unique(self):
        """范围按钮 ID 唯一"""
        id1 = make_range_btn_id("path1")
        id2 = make_range_btn_id("path2")
        assert id1 != id2

    def test_is_param_node_true(self):
        """参数节点检测：是"""
        children = [
            {"label": "@default", "value": "10"},
            {"label": "@min", "value": "0"},
        ]
        assert is_param_node("param_test", "some_value", children) is True

    def test_is_param_node_false_no_param_prefix(self):
        """参数节点检测：否（无 param_ 前缀）"""
        children = [{"label": "@default", "value": "10"}]
        assert is_param_node("config", "value", children) is False

    def test_is_param_node_false_no_value(self):
        """参数节点检测：否（无值）"""
        children = [{"label": "@default", "value": "10"}]
        assert is_param_node("param_test", None, children) is False

    def test_is_param_node_false_no_children(self):
        """参数节点检测：否（无子节点）"""
        assert is_param_node("param_test", "value", []) is False

    def test_extract_param_meta(self):
        """提取参数元数据"""
        children = [
            {"label": "@default", "value": "10"},
            {"label": "@min", "value": "0"},
            {"label": "@max", "value": "100"},
            {"label": "@description", "value": "测试参数"},
            {"label": "other_child", "value": "x"},
        ]
        secondary, ranges, rest = extract_param_meta(children)
        assert secondary["@description"] == "测试参数"
        assert ranges["@default"] == "10"
        assert ranges["@min"] == "0"
        assert ranges["@max"] == "100"
        assert len(rest) == 1
        assert rest[0]["label"] == "other_child"


# ---------------------------------------------------------------------------
# 搜索语义测试
# ---------------------------------------------------------------------------

class TestSearchSemantics:
    """搜索语义正确性测试"""

    def test_search_leaf_hit(self):
        """搜索叶子值命中"""
        tree = _make_tree()
        hits = classify_search_hits(tree, "localhost")
        assert len(hits) >= 1
        assert any(h.hit_type == "leaf" and h.path == "root.config.server.host" for h in hits)

    def test_search_node_hit(self):
        """搜索父节点标签命中"""
        tree = _make_tree()
        hits = classify_search_hits(tree, "server")
        assert len(hits) >= 1
        # server 是父节点（value=None），应为 node hit
        assert any(h.hit_type == "node" and h.path == "root.config.server" for h in hits)

    def test_search_note_hit(self):
        """搜索备注命中"""
        tree = _make_tree_with_note()
        hits = classify_search_hits(tree, "服务器配置", note_map={})
        # note 命中
        assert any(h.hit_type == "note" for h in hits)

    def test_search_filename_hit(self):
        """搜索标签命中（类似文件名）"""
        tree = _make_tree()
        hits = classify_search_hits(tree, "config")
        assert len(hits) >= 1
        # config 是父节点
        assert any(h.hit_type == "node" and "config" in h.path for h in hits)

    def test_parent_hit_not_zero_count(self):
        """父节点命中不因 leaf count=0 被误判无结果

        关键修复：当父节点标签匹配时，filter_tree_and_count 应返回 count >= 1
        """
        tree = _make_tree()
        # 搜索 "server" - 这是父节点标签，value=None
        filtered, count = filter_tree_and_count(tree, "server")
        assert filtered is not None
        assert count >= 1, "父节点标签命中应计为至少 1 个结果"

    def test_parent_hit_has_search_results(self):
        """has_search_results 对父节点命中返回 True"""
        tree = _make_tree()
        hits = classify_search_hits(tree, "config")
        assert has_search_results(hits) is True

    def test_no_match_returns_empty(self):
        """无匹配返回空"""
        tree = _make_tree()
        hits = classify_search_hits(tree, "nonexistent_keyword_xyz")
        assert hits == []
        assert has_search_results(hits) is False

    def test_filter_tree_parent_match_count(self):
        """filter_tree_and_count: 父节点匹配时 count >= 1"""
        tree = _make_tree()
        # "debug" 是叶子节点标签（value="true"）
        filtered, count = filter_tree_and_count(tree, "debug")
        assert count >= 1

        # "config" 是父节点标签（value=None）
        filtered, count = filter_tree_and_count(tree, "config")
        assert count >= 1, "父节点标签匹配时 count 应 >= 1"

    def test_search_case_insensitive(self):
        """搜索大小写不敏感"""
        tree = _make_tree()
        hits = classify_search_hits(tree, "LOCALHOST")
        assert len(hits) >= 1

    def test_search_multiple_hits(self):
        """多个命中"""
        tree = _make_tree()
        # "8080" 只出现在 port 的值中
        hits = classify_search_hits(tree, "8080")
        assert len(hits) == 1
        assert hits[0].hit_type == "leaf"


# ---------------------------------------------------------------------------
# FileRef payload 测试
# ---------------------------------------------------------------------------

class TestFileRefPayload:
    """FileRef payload 结构测试"""

    def test_file_ref_creation(self):
        """FileRef 创建"""
        ref = FileRef(
            profile_id="test_profile",
            kind=FileKind.ARCHIVE,
            name="config.xml",
            version_or_token="config_20240101.xml",
        )
        assert ref.profile_id == "test_profile"
        assert ref.kind == FileKind.ARCHIVE
        assert ref.name == "config.xml"
        assert ref.version_or_token == "config_20240101.xml"

    def test_file_ref_serializable(self):
        """FileRef 可序列化为 dict（用于 tab payload）"""
        ref = FileRef(
            profile_id="",
            kind=FileKind.ARCHIVE,
            name="test.xml",
            version_or_token="test_v2.xml",
        )
        # Tab payload 使用 dict 格式
        payload = {
            "profile_id": ref.profile_id,
            "kind": ref.kind.value,
            "name": ref.name,
            "version_or_token": ref.version_or_token,
        }
        assert payload["kind"] == "archive"
        assert payload["version_or_token"] == "test_v2.xml"

    def test_comparison_tab_payload_structure(self):
        """comparison tab payload 持有 old_ref/new_ref"""
        # 模拟从 history 打开 comparison 时的 tab payload
        tab = {
            "name": "comparison:test.xml",
            "type": "comparison",
            "filename": "test.xml",
            "old_ref": {
                "profile_id": "",
                "kind": "archive",
                "name": "test.xml",
                "version_or_token": "test_v1.xml",
            },
        }
        assert "old_ref" in tab
        assert tab["old_ref"]["version_or_token"] == "test_v1.xml"

    def test_history_tab_file_ref(self):
        """history archive_view tab 持有 file_ref"""
        tab = {
            "name": "file:archive:test.xml:test_v1.xml",
            "type": "archive_view",
            "filename": "test.xml",
            "archive_filename": "test_v1.xml",
            "file_ref": {
                "profile_id": "",
                "kind": "archive",
                "name": "test.xml",
                "version_or_token": "test_v1.xml",
            },
        }
        assert tab["file_ref"]["version_or_token"] == "test_v1.xml"


# ---------------------------------------------------------------------------
# Note map 测试
# ---------------------------------------------------------------------------

class TestNoteMap:
    """备注映射测试"""

    def test_build_note_map_empty(self):
        """空收藏列表"""
        result = build_note_map([], "test.xml")
        assert result == {}

    def test_build_note_map_with_notes(self):
        """有备注的收藏"""
        favorites = [
            {"source_file": "test.xml", "path": "root.config", "note": "配置节点"},
            {"source_file": "test.xml", "path": "root.debug", "note": ""},
            {"source_file": "other.xml", "path": "root.x", "note": "其他文件"},
        ]
        result = build_note_map(favorites, "test.xml")
        assert "root.config" in result
        assert result["root.config"] == "配置节点"
        assert "root.debug" not in result  # 空备注不包含
        assert "root.x" not in result  # 其他文件不包含


# ---------------------------------------------------------------------------
# History exact version 测试
# ---------------------------------------------------------------------------

class TestHistoryExactVersion:
    """历史版本精确跟踪测试"""

    def test_archive_view_tab_has_file_ref(self):
        """archive_view tab 包含 file_ref"""
        # 模拟 _view_archived 创建的 tab
        tab = {
            "name": "file:archive:test.xml:test_v1.xml",
            "type": "archive_view",
            "filename": "test.xml",
            "archive_filename": "test_v1.xml",
            "file_ref": {
                "profile_id": "",
                "kind": "archive",
                "name": "test.xml",
                "version_or_token": "test_v1.xml",
            },
        }
        assert "file_ref" in tab
        assert tab["file_ref"]["kind"] == "archive"
        assert tab["file_ref"]["version_or_token"] == "test_v1.xml"

    def test_comparison_tab_receives_old_ref(self):
        """comparison tab 从 history 接收 old_ref"""
        # 模拟 _compare_with_current 创建的 tab
        old_ref = {
            "profile_id": "",
            "kind": "archive",
            "name": "test.xml",
            "version_or_token": "test_v1.xml",
        }
        tab = {
            "name": "comparison:test.xml",
            "type": "comparison",
            "filename": "test.xml",
            "old_ref": old_ref,
        }
        assert tab["old_ref"]["version_or_token"] == "test_v1.xml"

    def test_file_ref_kind_values(self):
        """FileKind 枚举值"""
        assert FileKind.CURRENT.value == "current"
        assert FileKind.ARCHIVE.value == "archive"
        assert FileKind.RECORD.value == "record"
        assert FileKind.TEMP.value == "temp"
