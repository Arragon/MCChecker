from __future__ import annotations

import hashlib
import html
import os

from nicegui import ui

from app.core import storage, parse_cache, parser
from app.core import searching
from app.core.tree_component import (
    LEVEL_TEXT_COLORS,
    build_row_prefix_html,
    make_tree_panel_id,
    make_range_btn_id,
    is_param_node,
    extract_param_meta,
    render_tree_toolbar_inline,
)

# 性能优化：每个文件分组初始显示的子节点数
SEARCH_PAGE_SIZE = 20


def render_search_page(tab: dict) -> None:
    keyword = (tab.get("query") or "").strip()
    ui.label(f'搜索结果: "{keyword}"').classes("mc-page-title q-mb-sm")
    if not keyword:
        ui.label("请输入搜索关键词").classes("text-caption text-grey")
        return

    files = storage.list_config_files()
    favorites = storage.load_favorites()

    groups = []
    total_matches = 0

    for fname in files:
        source_path = storage.get_config_path(fname)
        if not os.path.exists(source_path):
            continue
        tree = parse_cache.load_tree(source_path)
        if tree is None:
            try:
                tree = parser.parse_path(source_path, fname)
            except ValueError:
                continue
            parse_cache.save_tree(source_path, tree)

        note_map = searching.build_note_map(favorites, fname)
        filtered, match_count = searching.filter_tree_and_count(tree, keyword, note_map)
        if not filtered:
            continue

        if match_count <= 0:
            continue

        total_matches += match_count
        groups.append((fname, filtered, match_count, note_map))

    with ui.row().classes("items-center q-gutter-sm q-mb-md"):
        ui.badge(f"{len(groups)} 个文件", color="blue")
        ui.badge(f"{total_matches} 个变量", color="teal")

    if not groups:
        ui.label("未找到匹配项").classes("text-caption text-grey q-mt-md")
        return

    for fname, filtered, match_count, note_map in groups:
        panel_id = make_tree_panel_id(keyword, fname)
        tree_kind = ((filtered.get("attrs") or {}).get("type")) or ""
        tree_classes = "w-full overflow-auto fav-tree"
        if tree_kind == "xml":
            tree_classes += " xml-tree"
        with ui.expansion(value=True).classes("w-full").props(
            f"label='{fname}  ({match_count} 项)' header-class='fav-group-header'"
        ):
            render_tree_toolbar_inline(panel_id)
            with ui.card().classes(tree_classes):
                with ui.element("div").props(f'id="{panel_id}"').classes("mc-tree-panel w-full"):
                    children = filtered.get("children", [])
                    # 分页显示：初始显示 SEARCH_PAGE_SIZE 个子树，其余按需加载
                    visible_children = children[:SEARCH_PAGE_SIZE]
                    remaining = len(children) - len(visible_children)
                    for child in visible_children:
                        _render_node(child, note_map, depth=0)
                    if remaining > 0:
                        _render_load_more_button(
                            children[SEARCH_PAGE_SIZE:],
                            note_map,
                            remaining,
                        )


def _render_node(node: dict, note_map: dict, depth: int) -> None:
    """渲染搜索结果树节点（使用共用组件）"""
    label = node.get("label") or ""
    value = node.get("value")
    children = node.get("children", []) or []
    node_id = node.get("id") or ""

    has_children = bool(children)
    is_param = is_param_node(label, value, children)
    secondary_meta, range_meta, children_for_tree = extract_param_meta(children) if is_param else ({}, {}, children)

    row_classes = f"tree-row w-full {'depth-even' if depth % 2 == 0 else 'depth-odd'}" + (" is-parent" if has_children else "")
    with ui.row().classes(row_classes):
        ui.html(build_row_prefix_html(depth, has_children))

        lbl_class = f"lbl-{min(depth, 3)}" if has_children else ""
        txt_color = LEVEL_TEXT_COLORS[min(depth, len(LEVEL_TEXT_COLORS) - 1)]
        note = (node.get("note") or note_map.get(node_id, "") or "").strip()

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

        range_btn = None
        if is_param and range_meta:
            range_btn_id = make_range_btn_id(node_id)
            range_btn = (
                ui.button("展开参数范围", icon="unfold_more")
                .props(f'flat dense size=sm id="{range_btn_id}"')
                .classes("mc-range-toggle")
            )

    range_parts: list[str] = []
    if is_param and range_meta and range_btn is not None:
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

        default_val = (range_meta.get("@default") or "").strip()
        min_val = _fmt_bound("@min", "@incMin")
        max_val = _fmt_bound("@max", "@incMax")
        if default_val:
            range_parts.append(f"default: {default_val}")
        if min_val:
            range_parts.append(f"min: {min_val}")
        if max_val:
            range_parts.append(f"max: {max_val}")

    if has_children:
        children_wrap = ui.column().classes("children-wrap q-pa-none q-ma-none")
        with children_wrap:
            if range_parts:
                range_wrap = ui.row().classes("mc-param-range w-full").style("display: none")
                with range_wrap:
                    ui.html(
                        f'<div class="mc-param-range-inner">{ html.escape(" · ".join(range_parts)) }</div>',
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

            for child in children_for_tree:
                if isinstance(child, dict):
                    _render_node(child, note_map, depth + 1)


def _render_load_more_button(children: list, note_map: dict, remaining_count: int) -> None:
    """渲染「加载更多」按钮，按需分批显示剩余搜索结果"""
    batch_size = SEARCH_PAGE_SIZE
    btn = ui.button(
        f"加载剩余 {remaining_count} 个节点",
        icon="expand_more",
    ).props("flat dense color=primary").classes("mc-search-load-more w-full q-my-sm")

    container = ui.column().classes("mc-search-more-results w-full")
    state = {"loaded": False}

    def _do_load(b=btn, c=container, items=list(children), nm=note_map, cnt=remaining_count, bs=batch_size, s=state):
        if s["loaded"]:
            return
        s["loaded"] = True
        with c:
            # 分批显示，每批 bs 个
            for child in items[:bs]:
                if isinstance(child, dict):
                    _render_node(child, nm, depth=0)
        remaining_after = len(items) - bs
        if remaining_after > 0:
            # 还有更多，再显示一个按钮
            items[:] = items[bs:]
            s["loaded"] = False
            b._props["label"] = f"加载剩余 {remaining_after} 个节点"
            b.visible = True
        else:
            b.visible = False

    btn.on("click", _do_load)
