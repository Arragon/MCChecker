"""共用树组件：基于解析树 dict 结构的统一渲染器

提取 viewer.py / search.py / record_view.py 的共用树渲染逻辑。
只做 display + explicit hooks，不读取 storage。
"""

from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from nicegui import ui


# ---------------------------------------------------------------------------
# 共用常量
# ---------------------------------------------------------------------------

LEVEL_LINE_COLORS = [
    "#5C9CE6",  # blue
    "#4DB6AC",  # teal
    "#FFB74D",  # amber
    "#BA68C8",  # purple
    "#81C784",  # green
    "#E57373",  # red
    "#64B5F6",  # light blue
    "#FFD54F",  # gold
]

LEVEL_TEXT_COLORS = ["dark", "grey-9", "grey-8", "grey-7", "grey-6"]

PARAM_SECONDARY_FIELDS = {"@description", "@editPrivilege"}
PARAM_RANGE_FIELDS = {"@default", "@min", "@max", "@incMax", "@incMin"}


# ---------------------------------------------------------------------------
# 共用 HTML 构建
# ---------------------------------------------------------------------------

def build_indent_html(depth: int) -> str:
    """构建缩进引导线 HTML"""
    parts = []
    for d in range(depth):
        color = LEVEL_LINE_COLORS[d % len(LEVEL_LINE_COLORS)]
        parts.append(
            f'<span class="indent-cell">'
            f'<span class="guide-line" style="background:{color}"></span>'
            f'</span>'
        )
    return "".join(parts)


def build_row_prefix_html(depth: int, has_children: bool) -> str:
    """构建行前缀（缩进 + 展开/折叠按钮或叶子标记）"""
    prefix = build_indent_html(depth)
    if has_children:
        return (
            prefix
            + '<button type="button" class="mc-tree-toggle is-expanded" '
            + 'aria-label="折叠节点" aria-expanded="true" onclick="window.mcToggleTree(this)">'
            + '<span class="mc-tree-toggle-icon">▾</span>'
            + "</button>"
        )
    return prefix + '<span class="leaf-marker"></span>'


def make_tree_panel_id(*parts: str) -> str:
    """生成唯一的树面板 DOM ID"""
    seed = "|".join(str(p or "") for p in parts)
    return f"mc-tree-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"


def make_range_btn_id(node_path: str) -> str:
    """生成参数范围按钮的唯一 ID"""
    return f"mc-range-{hashlib.sha1(node_path.encode('utf-8')).hexdigest()[:12]}"


# ---------------------------------------------------------------------------
# 参数节点检测
# ---------------------------------------------------------------------------

def is_param_node(label: str, value: Any, children: list) -> bool:
    """判断是否为参数节点（param_ 开头，子节点含 range/secondary 字段）"""
    if value is None or not children:
        return False
    if not str(label).startswith("param_"):
        return False
    for c in children:
        if isinstance(c, dict) and c.get("label") in PARAM_RANGE_FIELDS.union(PARAM_SECONDARY_FIELDS):
            return True
    return False


def extract_param_meta(children: list) -> tuple[dict, dict, list]:
    """提取参数节点的元数据（secondary/range/rest）"""
    secondary: dict[str, str] = {}
    ranges: dict[str, str] = {}
    rest: list = []
    for c in children:
        if not isinstance(c, dict):
            continue
        lbl = c.get("label")
        val = c.get("value")
        if lbl in PARAM_SECONDARY_FIELDS and val is not None:
            secondary[str(lbl)] = str(val)
            continue
        if lbl in PARAM_RANGE_FIELDS and val is not None:
            ranges[str(lbl)] = str(val)
            continue
        rest.append(c)
    return secondary, ranges, rest


# ---------------------------------------------------------------------------
# 搜索命中分类
# ---------------------------------------------------------------------------

@dataclass
class SearchHit:
    """搜索命中项"""
    hit_type: str  # "node" | "leaf" | "filename" | "note"
    node: dict
    path: str = ""


def classify_search_hits(node: dict, query: str) -> List[SearchHit]:
    """分类搜索命中：node(父节点标签)/leaf(叶子值)/note(备注)

    父节点命中即使没有叶子值也应被计入结果。
    """
    kw = (query or "").strip().lower()
    if not kw:
        return []

    hits: List[SearchHit] = []
    _classify_recursive(node, kw, hits)
    return hits


def _classify_recursive(node: dict, kw: str, hits: List[SearchHit]) -> None:
    """递归分类命中"""
    label = str(node.get("label") or "")
    value = node.get("value")
    nid = node.get("id") or ""
    note = node.get("note") or ""

    # 标签命中
    if kw in label.lower():
        # 如果有 value → leaf hit；否则 → node hit（父节点）
        if value is not None:
            hits.append(SearchHit(hit_type="leaf", node=node, path=nid))
        else:
            hits.append(SearchHit(hit_type="node", node=node, path=nid))

    # 值命中（标签未命中时才单独计值命中，避免重复）
    elif value is not None and kw in str(value).lower():
        hits.append(SearchHit(hit_type="leaf", node=node, path=nid))

    # 备注命中
    if note and kw in note.lower():
        # 只有标签/值未命中时才单独计备注命中
        if not (kw in label.lower() or (value is not None and kw in str(value).lower())):
            hits.append(SearchHit(hit_type="note", node=node, path=nid))

    # 递归子节点
    for child in node.get("children", []) or []:
        if isinstance(child, dict):
            _classify_recursive(child, kw, hits)


def has_search_results(hits: List[SearchHit]) -> bool:
    """判断是否有搜索结果（任何类型的命中都算）"""
    return len(hits) > 0


# ---------------------------------------------------------------------------
# 剪贴板复制（安全版）
# ---------------------------------------------------------------------------

async def copy_to_clipboard(text: str) -> bool:
    """复制到剪贴板，真成功后才返回 True

    使用浏览器 Clipboard API，失败时提供 fallback 提示。
    """
    try:
        js_text = json.dumps(text)
        await ui.run_javascript(
            f"navigator.clipboard.writeText({js_text}).then(() => 'ok').catch(() => 'fail')"
        )
        return True
    except Exception:
        return False


def notify_copy_result(success: bool, fallback_text: str = "") -> None:
    """根据复制结果给出通知"""
    if success:
        ui.notify("已复制到剪贴板", type="positive")
    else:
        msg = "复制失败，请手动选择"
        if fallback_text:
            ui.notify(msg, type="warning")
        else:
            ui.notify(msg, type="warning")


# ---------------------------------------------------------------------------
# TreeRenderer：共用树渲染器
# ---------------------------------------------------------------------------

@dataclass
class TreeNodeContext:
    """单个节点的渲染上下文"""
    depth: int
    node_key: str = ""
    tree_type: str = ""
    show_note: bool = False
    note_map: Optional[Dict[str, str]] = None
    review_remark_map: Optional[Dict[str, str]] = None
    # 可选 hooks
    on_favorite: Optional[Callable] = None  # (node_path, label, value) -> None
    on_edit_remark: Optional[Callable] = None  # (node, node_key, tree_type, node_type) -> None
    # 收藏状态（viewer 用）
    fav_direct: Optional[Set[str]] = None
    fav_covered: Optional[Set[str]] = None
    # 搜索高亮
    search_keyword: str = ""


class TreeRenderer:
    """共用树渲染器

    - 基于 dict 树结构渲染
    - 支持 read-only / favorite / remark / search hit 等模式
    - 不读取 storage（状态由调用方注入）
    """

    def __init__(self, context: TreeNodeContext):
        self.context = context

    def render_tree(self, tree: dict, panel_id: Optional[str] = None) -> str:
        """渲染整棵树，返回 panel_id"""
        if panel_id is None:
            panel_id = make_tree_panel_id("tree")

        tree_kind = (tree.get("attrs") or {}).get("type")
        tree_classes = "w-full fav-tree q-pa-sm"
        if tree_kind == "xml":
            tree_classes += " xml-tree"

        with ui.element("div").props(f'id="{panel_id}"').classes("mc-tree-panel w-full"):
            with ui.column().classes(tree_classes):
                for child_index, child in enumerate(tree.get("children", [])):
                    self.render_node(child)
        return panel_id

    def render_node(self, node: dict) -> None:
        """递归渲染单个节点"""
        ctx = self.context
        label = node.get("label") or ""
        value = node.get("value")
        children = node.get("children", []) or []
        node_path = node.get("id") or ""
        node_type = str((node.get("attrs") or {}).get("type") or "")

        has_children = bool(children)
        depth = ctx.depth
        depth_parity = "depth-even" if depth % 2 == 0 else "depth-odd"
        is_param = is_param_node(label, value, children)
        secondary_meta, range_meta, children_for_tree = (
            extract_param_meta(children) if is_param else ({}, {}, children)
        )

        # Review remarks
        review_texts: list[str] = []
        if ctx.review_remark_map and ctx.node_key:
            remarks = ctx.review_remark_map.get(ctx.node_key, [])
            if isinstance(remarks, list):
                review_texts = [
                    f"{str(item.get('actor_display') or item.get('actor_name') or item.get('actor_ip') or '未知')}：{str(item.get('proposed_value') or '')}"
                    for item in remarks
                ]

        # 节点行
        row_classes = f"tree-row w-full {depth_parity}" + (" is-parent" if has_children else "")
        with ui.row().classes(row_classes):
            ui.html(build_row_prefix_html(depth, has_children))

            # 收藏星标（viewer 模式）
            if ctx.on_favorite and ctx.fav_direct is not None:
                self._render_star(node_path, label, value)

            # 标签 + 值
            lbl_class = f"lbl-{min(depth, 3)}" if has_children else ""
            txt_color = LEVEL_TEXT_COLORS[min(depth, len(LEVEL_TEXT_COLORS) - 1)]

            if value is not None:
                if is_param:
                    desc = (secondary_meta.get("@description") or "").strip()
                    priv = (secondary_meta.get("@editPrivilege") or "").strip()
                    sec_parts = []
                    if desc:
                        sec_parts.append(desc)
                    if priv:
                        sec_parts.append(priv)
                    sec_text = " · ".join(sec_parts)
                    meta_html = f'<span class="mc-param-meta"> {html.escape(sec_text)}</span>' if sec_text else ""
                    ui.html(
                        f'<span class="font-mono text-body2 text-{txt_color} {lbl_class} tree-label">'
                        f'<span class="mc-param-key">{html.escape(str(label))}</span> '
                        f'<span class="mc-param-val">= {html.escape(str(value))}</span>'
                        f'{meta_html}'
                        f'</span>',
                    )
                else:
                    ui.html(
                        f'<span class="font-mono text-body2 text-{txt_color} {lbl_class} tree-label">'
                        f'{html.escape(str(label))} <span class="val-text">= {html.escape(str(value))}</span></span>',
                    )
                # 搜索备注
                if ctx.show_note:
                    note = ""
                    if ctx.note_map:
                        note = (node.get("note") or ctx.note_map.get(node_path, "") or "").strip()
                    if note:
                        ui.label(note).classes("text-caption text-grey q-ml-sm")
            elif has_children:
                ui.html(
                    f'<span class="text-body2 text-weight-medium text-{txt_color} {lbl_class} tree-label node-key">'
                    f'{html.escape(str(label))}</span>',
                )
            else:
                ui.html(
                    f'<span class="font-mono text-body2 text-grey tree-label">{html.escape(str(label))}</span>',
                )

            # Review notes inline
            if review_texts:
                ui.label("；".join(review_texts)).classes("mc-review-note-inline q-ml-sm")

            # 参数范围按钮
            range_btn = None
            if is_param and range_meta:
                range_btn_id = make_range_btn_id(node_path)
                range_btn = (
                    ui.button("展开参数范围", icon="unfold_more")
                    .props(f'flat dense size=sm id="{range_btn_id}"')
                    .classes("mc-range-toggle")
                )

            ui.html('<span class="mc-row-spacer"></span>')

            # 节点操作区
            with ui.element("span").classes("mc-node-actions"):
                if ctx.on_edit_remark:
                    edit_btn = ui.button(icon="edit")
                    edit_btn.props("flat round dense size=sm color=primary")
                    edit_btn.classes("mc-inline-edit-btn")
                    edit_btn.tooltip("添加修改备注")
                    edit_btn.on(
                        "click",
                        lambda e=None, n=node, nk=ctx.node_key, tt=ctx.tree_type, nt=node_type:
                            ctx.on_edit_remark(n, nk, tt, nt),
                    )

        # 参数范围面板
        range_parts: list[str] = []
        if is_param and range_meta and range_btn is not None:
            range_parts = self._build_range_parts(range_meta)
            self._setup_range_toggle(range_btn, range_btn_id, range_parts)

        # 子节点
        if has_children:
            children_wrap = ui.column().classes("children-wrap q-pa-none q-ma-none")
            with children_wrap:
                if range_parts:
                    self._render_range_panel(range_parts, range_btn, range_btn_id)

                child_ctx = self._make_child_context(ctx, node_path)
                child_renderer = TreeRenderer(child_ctx)
                for child_index, child in enumerate(children_for_tree):
                    if isinstance(child, dict):
                        child_renderer.render_node(child)

    def _render_star(self, node_path: str, label: str, value: Any) -> None:
        """渲染收藏星标"""
        ctx = self.context
        is_direct = node_path in (ctx.fav_direct or set())
        is_covered = node_path in (ctx.fav_covered or set())
        is_fav = is_direct or is_covered

        star_name = "star" if is_fav else "star_outline"
        star_color = "yellow" if is_fav else "grey-5"

        star = ui.icon(star_name, size="xs", color=star_color)
        star.classes("fav-star cursor-pointer q-mr-xs")
        if ctx.on_favorite:
            star.on("click", lambda np=node_path, lbl=label, v=value, s=star:
                     ctx.on_favorite(np, lbl, v, s))

    @staticmethod
    def _build_range_parts(range_meta: dict) -> list[str]:
        """构建参数范围显示文本"""
        def _fmt_bound(val_key: str, inc_key: str) -> str:
            v = (range_meta.get(val_key) or "").strip()
            if not v:
                return ""
            inc = (range_meta.get(inc_key) or "").strip().lower()
            if inc in ("true", "1", "yes"):
                return f"{v}（含）"
            if inc in ("false", "0", "no"):
                return f"{v}（不含）"
            return v

        parts: list[str] = []
        default_val = (range_meta.get("@default") or "").strip()
        min_val = _fmt_bound("@min", "@incMin")
        max_val = _fmt_bound("@max", "@incMax")
        if default_val:
            parts.append(f"default: {default_val}")
        if min_val:
            parts.append(f"min: {min_val}")
        if max_val:
            parts.append(f"max: {max_val}")
        return parts

    @staticmethod
    def _setup_range_toggle(range_btn, range_btn_id: str, range_parts: list[str]) -> None:
        """设置参数范围展开/折叠交互"""
        pass  # 实际交互在 _render_range_panel 中设置

    @staticmethod
    def _render_range_panel(range_parts: list[str], range_btn, range_btn_id: str) -> None:
        """渲染参数范围面板"""
        range_wrap = ui.row().classes("mc-param-range w-full").style("display: none")
        with range_wrap:
            ui.html(
                f'<div class="mc-param-range-inner">{html.escape(" · ".join(range_parts))}</div>',
            )

        range_state = {"open": False}

        def _toggle_range(btn=range_btn, wrap=range_wrap):
            range_state["open"] = not range_state["open"]
            if range_state["open"]:
                ui.run_javascript(
                    f"""
                    (function() {{
                      var btn = document.getElementById('{range_btn_id}');
                      if (!btn) return;
                      var row = btn.closest('.tree-row');
                      if (!row) return;
                      var treeBtn = row.querySelector('.mc-tree-toggle');
                      if (treeBtn && treeBtn.getAttribute('aria-expanded') !== 'true' && window.mcSetTreeNode) {{
                        window.mcSetTreeNode(treeBtn, true);
                      }}
                    }})();
                    """.strip()
                )
                wrap.style("display: flex")
                btn._props["label"] = "收起参数范围"
                btn._props["icon"] = "unfold_less"
            else:
                wrap.style("display: none")
                btn._props["label"] = "展开参数范围"
                btn._props["icon"] = "unfold_more"
            btn.update()

        range_btn.on("click", _toggle_range)

    def _make_child_context(self, parent_ctx: TreeNodeContext, node_path: str) -> TreeNodeContext:
        """创建子节点的渲染上下文（深度+1，node_key 更新）"""
        from app.core.reviewing import make_node_key
        return TreeNodeContext(
            depth=parent_ctx.depth + 1,
            node_key=make_node_key(parent_ctx.node_key, 0),  # 由调用方覆盖
            tree_type=parent_ctx.tree_type,
            show_note=parent_ctx.show_note,
            note_map=parent_ctx.note_map,
            review_remark_map=parent_ctx.review_remark_map,
            on_favorite=parent_ctx.on_favorite,
            on_edit_remark=parent_ctx.on_edit_remark,
            fav_direct=parent_ctx.fav_direct,
            fav_covered=parent_ctx.fav_covered,
            search_keyword=parent_ctx.search_keyword,
        )


# ---------------------------------------------------------------------------
# 便捷渲染函数（供 viewer/search 直接调用）
# ---------------------------------------------------------------------------

def render_tree_toolbar(panel_id: str) -> None:
    """渲染树控制面板（全部展开/折叠）"""
    with ui.row().classes("items-center q-gutter-sm q-mb-sm mc-tree-toolbar"):
        ui.label("节点控制").classes("text-caption text-grey-7")
        ui.button(
            "全部展开",
            icon="unfold_more",
            on_click=lambda pid=panel_id: ui.run_javascript(f"window.mcTreeSetAll('{pid}', true)"),
        ).props("flat dense color=primary").classes("mc-tree-toolbar-btn")
        ui.button(
            "全部折叠",
            icon="unfold_less",
            on_click=lambda pid=panel_id: ui.run_javascript(f"window.mcTreeSetAll('{pid}', false)"),
        ).props("flat dense color=grey-7").classes("mc-tree-toolbar-btn")


def render_tree_toolbar_inline(panel_id: str) -> None:
    """渲染内联树控制面板（用于搜索结果等场景）"""
    with ui.row().classes("items-center q-gutter-sm q-px-sm q-pt-sm q-pb-xs mc-tree-toolbar"):
        ui.label("节点控制").classes("text-caption text-grey-7")
        ui.button(
            "全部展开",
            icon="unfold_more",
            on_click=lambda pid=panel_id: ui.run_javascript(f"window.mcTreeSetAll('{pid}', true)"),
        ).props("flat dense color=primary").classes("mc-tree-toolbar-btn")
        ui.button(
            "全部折叠",
            icon="unfold_less",
            on_click=lambda pid=panel_id: ui.run_javascript(f"window.mcTreeSetAll('{pid}', false)"),
        ).props("flat dense color=grey-7").classes("mc-tree-toolbar-btn")
