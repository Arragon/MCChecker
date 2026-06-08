from __future__ import annotations

import os

from nicegui import ui

from app.core import storage, parse_cache, parser
from app.core import searching


_LEVEL_LINE_COLORS = [
    "#5C9CE6",
    "#4DB6AC",
    "#FFB74D",
    "#BA68C8",
    "#81C784",
    "#E57373",
    "#64B5F6",
    "#FFD54F",
]

_LEVEL_TEXT_COLORS = ["dark", "grey-9", "grey-8", "grey-7", "grey-6"]


def _build_row_prefix_html(depth: int, has_children: bool, is_expanded: bool) -> str:
    parts = []
    for d in range(depth):
        color = _LEVEL_LINE_COLORS[d % len(_LEVEL_LINE_COLORS)]
        parts.append(
            f'<span class="indent-cell">'
            f'<span class="guide-line" style="background:{color}"></span>'
            f'</span>'
        )
    if has_children:
        arrow_class = "expanded" if is_expanded else "collapsed"
        parts.append(f'<span class="toggle-btn {arrow_class}" onclick="mct(this)"></span>')
    else:
        parts.append('<span class="leaf-marker"></span>')
    return "".join(parts)


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
            content = storage.load_config_file(fname)
            if content is None:
                continue
            try:
                tree = parser.parse_file(content, fname)
            except ValueError:
                continue
            parse_cache.save_tree(source_path, tree)

        note_map = searching.build_note_map(favorites, fname)
        filtered = searching.filter_tree(tree, keyword, note_map)
        if not filtered:
            continue

        match_count = searching.count_value_nodes(filtered)
        if match_count <= 0:
            continue

        total_matches += match_count
        groups.append((fname, filtered, match_count))

    with ui.row().classes("items-center q-gutter-sm q-mb-md"):
        ui.badge(f"{len(groups)} 个文件", color="blue")
        ui.badge(f"{total_matches} 个变量", color="teal")

    if not groups:
        ui.label("未找到匹配项").classes("text-caption text-grey q-mt-md")
        return

    for fname, filtered, match_count in groups:
        with ui.expansion(value=True).classes("w-full").props(
            f"label='{fname}  ({match_count} 项)' header-class='fav-group-header'"
        ):
            with ui.card().classes("w-full overflow-auto fav-tree"):
                note_map = searching.build_note_map(favorites, fname)
                for child in filtered.get("children", []):
                    _render_node(child, note_map, depth=0, expand_state={})


def _render_node(node: dict, note_map: dict, depth: int, expand_state: dict) -> None:
    label = node.get("label") or ""
    value = node.get("value")
    children = node.get("children", []) or []
    node_id = node.get("id") or ""

    has_children = bool(children)
    is_expanded = expand_state.get(node_id, True)
    depth_parity = "depth-even" if depth % 2 == 0 else "depth-odd"

    row_classes = f"tree-row w-full {depth_parity}" + (" is-parent" if has_children else "")
    with ui.row().classes(row_classes):
        prefix_html = _build_row_prefix_html(depth, has_children, is_expanded)
        ui.html(prefix_html, sanitize=False)

        lbl_class = f"lbl-{min(depth, 3)}" if has_children else ""
        txt_color = _LEVEL_TEXT_COLORS[min(depth, len(_LEVEL_TEXT_COLORS) - 1)]
        note = (node.get("note") or note_map.get(node_id, "") or "").strip()

        if value is not None:
            ui.html(
                f'<span class="font-mono text-body2 text-{txt_color} {lbl_class} tree-label">'
                f'{label} <span class="val-text">= {value}</span></span>',
                sanitize=False,
            )
            if note:
                ui.label(note).classes("text-caption text-grey q-ml-sm")
        elif has_children:
            ui.html(
                f'<span class="text-body2 text-weight-medium text-{txt_color} {lbl_class} tree-label node-key">'
                f'{label}</span>',
                sanitize=False,
            )
        else:
            ui.html(
                f'<span class="font-mono text-body2 text-grey tree-label">{label}</span>',
                sanitize=False,
            )

    if has_children:
        children_wrap = ui.column().classes("children-wrap q-pa-none q-ma-none")
        if not is_expanded:
            children_wrap.style("max-height: 0px")
        with children_wrap:
            for child in children:
                if isinstance(child, dict):
                    _render_node(child, note_map, depth + 1, expand_state)

