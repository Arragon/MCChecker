from __future__ import annotations

import hashlib
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

_PARAM_SECONDARY_FIELDS = {"@description", "@editPrivilege"}
_PARAM_RANGE_FIELDS = {"@default", "@min", "@max", "@incMax", "@incMin"}


def _build_indent_html(depth: int) -> str:
    parts = []
    for d in range(depth):
        color = _LEVEL_LINE_COLORS[d % len(_LEVEL_LINE_COLORS)]
        parts.append(
            f'<span class="indent-cell">'
            f'<span class="guide-line" style="background:{color}"></span>'
            f'</span>'
        )
    return "".join(parts)


def _build_row_prefix_html(depth: int, has_children: bool) -> str:
    prefix = _build_indent_html(depth)
    if has_children:
        return (
            prefix
            + '<button type="button" class="mc-tree-toggle is-expanded" '
            + 'aria-label="折叠节点" aria-expanded="true" onclick="window.mcToggleTree(this)">'
            + '<span class="mc-tree-toggle-icon">▾</span>'
            + "</button>"
        )
    return prefix + '<span class="leaf-marker"></span>'


def _make_tree_panel_id(*parts: str) -> str:
    seed = "|".join(str(p or "") for p in parts)
    return f"mc-tree-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"


def _make_range_btn_id(node_path: str) -> str:
    return f"mc-range-{hashlib.sha1(node_path.encode('utf-8')).hexdigest()[:12]}"


def _is_param_node(label: str, value, children: list) -> bool:
    if value is None or not children:
        return False
    if not str(label).startswith("param_"):
        return False
    for c in children:
        if isinstance(c, dict) and c.get("label") in _PARAM_RANGE_FIELDS.union(_PARAM_SECONDARY_FIELDS):
            return True
    return False


def _extract_param_meta(children: list) -> tuple[dict, dict, list]:
    secondary: dict[str, str] = {}
    ranges: dict[str, str] = {}
    rest: list = []
    for c in children:
        if not isinstance(c, dict):
            continue
        lbl = c.get("label")
        val = c.get("value")
        if lbl in _PARAM_SECONDARY_FIELDS and val is not None:
            secondary[str(lbl)] = str(val)
            continue
        if lbl in _PARAM_RANGE_FIELDS and val is not None:
            ranges[str(lbl)] = str(val)
            continue
        rest.append(c)
    return secondary, ranges, rest


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
        panel_id = _make_tree_panel_id(keyword, fname)
        tree_kind = ((filtered.get("attrs") or {}).get("type")) or ""
        tree_classes = "w-full overflow-auto fav-tree"
        if tree_kind == "xml":
            tree_classes += " xml-tree"
        with ui.expansion(value=True).classes("w-full").props(
            f"label='{fname}  ({match_count} 项)' header-class='fav-group-header'"
        ):
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
            with ui.card().classes(tree_classes):
                with ui.element("div").props(f'id="{panel_id}"').classes("mc-tree-panel w-full"):
                    for child in filtered.get("children", []):
                        _render_node(child, note_map, depth=0)


def _render_node(node: dict, note_map: dict, depth: int) -> None:
    label = node.get("label") or ""
    value = node.get("value")
    children = node.get("children", []) or []
    node_id = node.get("id") or ""

    has_children = bool(children)
    depth_parity = "depth-even" if depth % 2 == 0 else "depth-odd"
    is_param = _is_param_node(label, value, children)
    secondary_meta, range_meta, children_for_tree = _extract_param_meta(children) if is_param else ({}, {}, children)

    row_classes = f"tree-row w-full {depth_parity}" + (" is-parent" if has_children else "")
    with ui.row().classes(row_classes):
        ui.html(_build_row_prefix_html(depth, has_children), sanitize=False)

        lbl_class = f"lbl-{min(depth, 3)}" if has_children else ""
        txt_color = _LEVEL_TEXT_COLORS[min(depth, len(_LEVEL_TEXT_COLORS) - 1)]
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
                meta_html = f'<span class="mc-param-meta"> {sec_text}</span>' if sec_text else ""
                ui.html(
                    f'<span class="font-mono text-body2 text-{txt_color} {lbl_class} tree-label">'
                    f'<span class="mc-param-key">{label}</span> '
                    f'<span class="mc-param-val">= {value}</span>'
                    f'{meta_html}'
                    f'</span>',
                    sanitize=False,
                )
            else:
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

        range_btn = None
        if is_param and range_meta:
            range_btn_id = _make_range_btn_id(node_id)
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
                        f'<div class="mc-param-range-inner">{ " · ".join(range_parts) }</div>',
                        sanitize=False,
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
