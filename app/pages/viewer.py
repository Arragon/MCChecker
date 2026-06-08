"""文件解析查看器页面"""

from nicegui import ui

from app.core import storage, parser, parse_cache, searching
from app.utils.auth import is_deployer

# 层级竖线颜色（每级不同色，现代柔和配色）
_LEVEL_LINE_COLORS = [
    "#5C9CE6",  # blue
    "#4DB6AC",  # teal
    "#FFB74D",  # amber
    "#BA68C8",  # purple
    "#81C784",  # green
    "#E57373",  # red
    "#64B5F6",  # light blue
    "#FFD54F",  # gold
]

_LEVEL_TEXT_COLORS = ["dark", "grey-9", "grey-8", "grey-7", "grey-6"]


def _build_row_prefix_html(depth: int, has_children: bool, is_expanded: bool) -> str:
    """构建行前缀 HTML：层级缩进竖线 + 折叠箭头或叶子标记"""
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
        parts.append(
            f'<span class="toggle-btn {arrow_class}" onclick="mct(this)"></span>'
        )
    else:
        parts.append('<span class="leaf-marker"></span>')
    return ''.join(parts)


def render_file_viewer(filename: str, deployer: bool, session_tabs: list, session_active_tab: dict, search_keyword: str | None = None):
    """渲染文件解析查看器"""
    source_path = storage.get_config_path(filename)
    tree = parse_cache.load_tree(source_path)
    if tree is None:
        content = storage.load_config_file(filename)
        if content is None:
            ui.label(f"文件 {filename} 不存在").classes("text-negative")
            return
        try:
            tree = parser.parse_file(content, filename)
        except ValueError as e:
            ui.label(f"解析失败: {e}").classes("text-negative")
            return
        parse_cache.save_tree(source_path, tree)

    tree_full = tree

    # 预加载收藏状态：区分"直接收藏"和"被父级包含"
    favorites = storage.load_favorites()
    fav_direct = set()   # 直接收藏的路径（点击星标会取消收藏）
    fav_covered = set()  # 被父级收藏包含的路径（点击星标会提示）
    fav_entry_map = {}   # path -> favorite entry (for getting children)

    for fav in favorites:
        if fav["source_file"] == filename:
            fav_direct.add(fav["path"])
            fav_entry_map[fav["path"]] = fav
            # 递归收集所有被该收藏项覆盖的子路径
            for child_path in storage._collect_fav_paths(fav):
                if child_path != fav["path"]:
                    fav_covered.add(child_path)

    show_note = False
    note_map = {}
    kw = (search_keyword or "").strip()
    if kw:
        note_map = searching.build_note_map(favorites, filename)
        filtered = searching.filter_tree(tree, kw, note_map)
        if not filtered or not filtered.get("children"):
            with ui.row().classes("items-center q-mb-md"):
                ui.badge(filename, color="blue")
                ui.label(f'搜索: "{kw}"').classes("text-caption text-grey q-ml-sm")
            ui.label("未找到匹配项").classes("text-caption text-grey")
            return
        tree = filtered
        show_note = True

    # 文件信息栏
    with ui.row().classes("items-center q-mb-md"):
        ui.badge(filename, color="blue")
        update_date = storage.get_file_update_date(filename)
        if update_date:
            ui.label(f"更新: {update_date}").classes("text-caption text-grey")
        if kw:
            ui.label(f'搜索: "{kw}"').classes("text-caption text-grey q-ml-sm")
        ui.space()
        ui.button("版本历史", icon="history",
                  on_click=lambda: _open_history_tab(filename, session_tabs, session_active_tab)
                  ).props("flat dense")
        ui.button("修改记录", icon="history_edu",
                  on_click=lambda: _open_records_tab(filename, session_tabs, session_active_tab)
                  ).props("flat dense")
        ui.button("版本对比", icon="compare",
                  on_click=lambda: _open_comparison_tab(filename, session_tabs, session_active_tab)
                  ).props("flat dense")

    # 树形视图
    with ui.column().classes("w-full fav-tree q-pa-sm"):
        for child in tree.get("children", []):
            _render_node(child, filename, fav_direct, fav_covered, fav_entry_map,
                        tree_full, depth=0, expand_state={}, note_map=note_map, show_note=show_note)


def _render_node(node: dict, filename: str, fav_direct: set, fav_covered: set,
                 fav_entry_map: dict, tree: dict, depth: int, expand_state: dict, note_map: dict | None = None, show_note: bool = False):
    """递归渲染单个树节点"""
    label = node["label"]
    value = node.get("value")
    children = node.get("children", [])
    node_path = node["id"]

    has_children = bool(children)
    is_expanded = expand_state.get(node_path, True)
    depth_parity = "depth-even" if depth % 2 == 0 else "depth-odd"

    # 节点行
    row_classes = f"tree-row w-full {depth_parity}" + (" is-parent" if has_children else "")
    with ui.row().classes(row_classes):
        prefix_html = _build_row_prefix_html(depth, has_children, is_expanded)
        ui.html(prefix_html, sanitize=False)

        _render_star(node_path, label, value, filename, fav_direct, fav_covered, fav_entry_map, tree)

        lbl_class = f"lbl-{min(depth, 3)}" if has_children else ""
        txt_color = _LEVEL_TEXT_COLORS[min(depth, len(_LEVEL_TEXT_COLORS) - 1)]
        if value is not None:
            ui.html(
                f'<span class="font-mono text-body2 text-{txt_color} {lbl_class} tree-label">'
                f'{label} <span class="val-text">= {value}</span></span>',
                sanitize=False
            )
            if show_note:
                note = ""
                if isinstance(note_map, dict):
                    note = (node.get("note") or note_map.get(node_path, "") or "").strip()
                if note:
                    ui.label(note).classes("text-caption text-grey q-ml-sm")
        elif has_children:
            ui.html(
                f'<span class="text-body2 text-weight-medium text-{txt_color} {lbl_class} tree-label node-key">'
                f'{label}</span>',
                sanitize=False
            )
        else:
            ui.html(f'<span class="font-mono text-body2 text-grey tree-label">{label}</span>',
                    sanitize=False)

    # 子节点容器
    if has_children:
        children_wrap = ui.column().classes("children-wrap q-pa-none q-ma-none")
        if not is_expanded:
            children_wrap.style("max-height: 0px")
        with children_wrap:
            for child in children:
                _render_node(child, filename, fav_direct, fav_covered, fav_entry_map,
                            tree, depth + 1, expand_state, note_map=note_map, show_note=show_note)


def _serialize_subtree(node: dict) -> list:
    """将解析树节点的子节点序列化为收藏格式（不包含节点自身）"""
    result = []
    for child in node.get("children", []):
        item = {
            "label": child["label"],
            "value": child.get("value"),
            "path": child["id"],
            "children": _serialize_subtree(child),
        }
        result.append(item)
    return result


def _find_node(node: dict, target_path: str):
    """在解析树中查找指定路径的节点"""
    if node["id"] == target_path:
        return node
    for child in node.get("children", []):
        found = _find_node(child, target_path)
        if found:
            return found
    return None


def _render_star(node_path: str, label: str, value, filename: str,
                 fav_direct: set, fav_covered: set, fav_entry_map: dict, tree: dict):
    """渲染收藏星标图标"""
    is_direct = node_path in fav_direct
    is_covered = node_path in fav_covered
    is_fav = is_direct or is_covered

    star_name = "star" if is_fav else "star_outline"
    star_color = "yellow" if is_fav else "grey-5"

    star = ui.icon(star_name, size="xs", color=star_color)
    star.classes("fav-star cursor-pointer q-mr-xs")
    star.on("click", lambda np=node_path, lbl=label, v=value, s=star:
             _toggle_favorite(np, lbl, v, filename, fav_direct, fav_covered, fav_entry_map, tree, s))


def _toggle_favorite(node_path: str, label: str, value, filename: str,
                     fav_direct: set, fav_covered: set, fav_entry_map: dict,
                     tree: dict, star_icon):
    """切换收藏状态"""
    is_direct = node_path in fav_direct
    is_covered = node_path in fav_covered

    if is_direct:
        # 取消直接收藏
        storage.remove_favorite(node_path, filename)
        fav_direct.discard(node_path)
        if node_path in fav_entry_map:
            del fav_entry_map[node_path]
        # 从 fav_covered 中移除该收藏项覆盖的所有子路径
        _refresh_covered(fav_direct, fav_covered, fav_entry_map, filename)

        star_icon._props["name"] = "star_outline"
        star_icon._props["color"] = "grey-5"
        star_icon.update()
        ui.notify(f"已取消收藏: {label}", type="info")

    elif is_covered:
        # 被父级收藏包含，提示用户
        parent_path = storage.find_parent_favorite(node_path, filename)
        parent_label = parent_path.rsplit(".", 1)[-1] if parent_path else "?"
        ui.notify(f"「{label}」已被父级收藏「{parent_label}」包含，请先取消父级收藏", type="warning")

    else:
        # 新增收藏
        if value is not None:
            # 叶子变量：单个收藏
            storage.add_favorite(node_path, label, str(value), filename)
        else:
            # 父级节点：打包子树收藏
            node = _find_node(tree, node_path)
            if node is None:
                ui.notify("节点数据异常", type="negative")
                return
            children = _serialize_subtree(node)
            if not children:
                ui.notify("该节点下无子变量", type="warning")
                return
            storage.add_favorite(node_path, label, None, filename, children=children)

        fav_direct.add(node_path)
        _refresh_covered(fav_direct, fav_covered, fav_entry_map, filename)
        # 重新加载 fav_entry_map
        for fav in storage.load_favorites():
            if fav["source_file"] == filename and fav["path"] == node_path:
                fav_entry_map[node_path] = fav
                break

        star_icon._props["name"] = "star"
        star_icon._props["color"] = "yellow"
        star_icon.update()

        child_count = len(_count_leaf_descendants(node_path, tree))
        ui.notify(
            f"已收藏: {label}" + (f"（含 {child_count} 个变量）" if child_count > 1 else ""),
            type="positive"
        )


def _refresh_covered(fav_direct: set, fav_covered: set, fav_entry_map: dict, filename: str):
    """根据当前的 fav_direct 重新计算 fav_covered 和 fav_entry_map"""
    fav_covered.clear()
    # 重建 fav_entry_map
    for fav in storage.load_favorites():
        if fav["source_file"] == filename:
            fav_entry_map[fav["path"]] = fav
            for child_path in storage._collect_fav_paths(fav):
                if child_path != fav["path"]:
                    fav_covered.add(child_path)


def _count_leaf_descendants(node_path: str, tree: dict) -> list:
    """计算节点下所有叶子变量（用于通知消息）"""
    results = []

    def _find_and_count(node):
        if node["id"] == node_path:
            _collect_from(node)
            return True
        for child in node.get("children", []):
            if _find_and_count(child):
                return True
        return False

    def _collect_from(node):
        if node.get("value") is not None:
            results.append(node["id"])
        for child in node.get("children", []):
            _collect_from(child)

    for child in tree.get("children", []):
        if _find_and_count(child):
            break
    return results


def _open_history_tab(filename: str, session_tabs: list, session_active_tab: dict):
    """打开版本历史标签页"""
    tab_name = f"history:{filename}"
    for tab in session_tabs:
        if tab["name"] == tab_name:
            session_active_tab["name"] = tab_name
            try:
                from app.core import tabs_state
                tabs_state.save_current(session_tabs, session_active_tab["name"])
            except Exception:
                pass
            return
    from app.core import tab_manager, tabs_state
    tab_manager.ensure_opened_at(session_tabs)
    session_tabs.append({
        "name": tab_name,
        "label": f"历史: {filename}",
        "type": "history",
        "filename": filename,
        "opened_at": (max([t.get("opened_at", 0) for t in session_tabs], default=-1) + 1),
    })
    session_active_tab["name"] = tab_name
    tab_manager.enforce_tab_limit(session_tabs, session_active_tab["name"], 15)
    tabs_state.save_current(session_tabs, session_active_tab["name"])


def _open_records_tab(filename: str, session_tabs: list, session_active_tab: dict):
    tab_name = f"records:{filename}"
    for tab in session_tabs:
        if tab["name"] == tab_name:
            session_active_tab["name"] = tab_name
            try:
                from app.core import tabs_state
                tabs_state.save_current(session_tabs, session_active_tab["name"])
            except Exception:
                pass
            return
    from app.core import tab_manager, tabs_state
    tab_manager.ensure_opened_at(session_tabs)
    session_tabs.append({
        "name": tab_name,
        "label": f"修改记录: {filename}",
        "type": "records",
        "filename": filename,
        "opened_at": (max([t.get("opened_at", 0) for t in session_tabs], default=-1) + 1),
    })
    session_active_tab["name"] = tab_name
    tab_manager.enforce_tab_limit(session_tabs, session_active_tab["name"], 15)
    tabs_state.save_current(session_tabs, session_active_tab["name"])


def _open_comparison_tab(filename: str, session_tabs: list, session_active_tab: dict):
    """打开版本对比标签页"""
    tab_name = f"comparison:{filename}"
    for tab in session_tabs:
        if tab["name"] == tab_name:
            session_active_tab["name"] = tab_name
            try:
                from app.core import tabs_state
                tabs_state.save_current(session_tabs, session_active_tab["name"])
            except Exception:
                pass
            return
    from app.core import tab_manager, tabs_state
    tab_manager.ensure_opened_at(session_tabs)
    session_tabs.append({
        "name": tab_name,
        "label": f"对比: {filename}",
        "type": "comparison",
        "filename": filename,
        "opened_at": (max([t.get("opened_at", 0) for t in session_tabs], default=-1) + 1),
    })
    session_active_tab["name"] = tab_name
    tab_manager.enforce_tab_limit(session_tabs, session_active_tab["name"], 15)
    tabs_state.save_current(session_tabs, session_active_tab["name"])
