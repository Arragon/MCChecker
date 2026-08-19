"""文件解析查看器页面"""

import hashlib
import os

from nicegui import ui

from app.core import storage, parser, parse_cache, searching, reviewing
from app.pages.file_downloads import DOWNLOAD_KIND_CURRENT, make_download_handler
from app.utils.auth import get_identity_info, is_deployer, is_admin

# 备注显示区容器注册表：key=(filename, node_key) -> remark_box 元素，
# 供删除备注时精准定位并局部刷新对应节点行（避免整页重渲染）。
_REMARK_BOX_REGISTRY: dict = {}


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

_PARAM_SECONDARY_FIELDS = {"@description", "@editPrivilege"}
_PARAM_RANGE_FIELDS = {"@default", "@min", "@max", "@incMax", "@incMin"}


def load_tree_for_viewer(filename: str) -> tuple[dict | None, str | None]:
    source_path = storage.get_config_path(filename)
    tree = parse_cache.load_tree(source_path)
    if tree is not None:
        return tree, None

    if not os.path.exists(source_path):
        return None, f"文件 {filename} 不存在"

    try:
        tree = parser.parse_path(source_path, filename)
    except ValueError as e:
        return None, f"解析失败: {e}"

    parse_cache.save_tree(source_path, tree)
    return tree, None


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


def render_file_viewer(
    filename: str,
    deployer: bool,
    session_tabs: list,
    session_active_tab: dict,
    search_keyword: str | None = None,
    preloaded_tree: dict | None = None,
):
    """渲染文件解析查看器"""
    tree = preloaded_tree
    if tree is None:
        tree, err = load_tree_for_viewer(filename)
        if err:
            ui.label(err).classes("text-negative")
            return

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
    review_remark_map = storage.build_edit_remark_map(filename)
    kw = (search_keyword or "").strip()
    if kw:
        note_map = searching.build_note_map(favorites, filename)
        filtered, match_count = searching.filter_tree_and_count(tree, kw, note_map)
        if not filtered or match_count <= 0:
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
        ui.button(
            "下载文件",
            icon="download",
            on_click=make_download_handler(DOWNLOAD_KIND_CURRENT, filename),
        ).props("flat dense color=primary").classes("mc-download-btn")
        ui.button("版本历史", icon="history",
                  on_click=lambda: _open_history_tab(filename, session_tabs, session_active_tab)
                  ).props("flat dense")
        ui.button("修改记录", icon="history_edu",
                  on_click=lambda: _open_records_tab(filename, session_tabs, session_active_tab)
                  ).props("flat dense")
        ui.button("版本对比", icon="compare",
                  on_click=lambda: _open_comparison_tab(filename, session_tabs, session_active_tab)
                  ).props("flat dense")

    panel_id = _make_tree_panel_id(filename, kw)
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

    # 树形视图
    tree_kind = (tree.get("attrs") or {}).get("type")
    tree_classes = "w-full fav-tree q-pa-sm"
    if tree_kind == "xml":
        tree_classes += " xml-tree"
    with ui.element("div").props(f'id="{panel_id}"').classes("mc-tree-panel w-full"):
        with ui.column().classes(tree_classes):
            for child_index, child in enumerate(tree.get("children", [])):
                _render_node(
                    child,
                    filename,
                    fav_direct,
                    fav_covered,
                    fav_entry_map,
                    tree_full,
                    session_active_tab,
                    depth=0,
                    node_key=child.get("id") or reviewing.make_node_key("", child_index),
                    tree_type=str(tree_kind or ""),
                    note_map=note_map,
                    review_remark_map=review_remark_map,
                    show_note=show_note,
                )


def _render_node(node: dict, filename: str, fav_direct: set, fav_covered: set,
                 fav_entry_map: dict, tree: dict, session_active_tab: dict, depth: int,
                 node_key: str, tree_type: str, note_map: dict | None = None,
                 review_remark_map: dict | None = None, show_note: bool = False):
    """递归渲染单个树节点"""
    label = node["label"]
    value = node.get("value")
    children = node.get("children", [])
    node_path = node["id"]
    node_type = str((node.get("attrs") or {}).get("type") or "")

    has_children = bool(children)
    depth_parity = "depth-even" if depth % 2 == 0 else "depth-odd"
    is_param = _is_param_node(label, value, children)
    secondary_meta, range_meta, children_for_tree = _extract_param_meta(children) if is_param else ({}, {}, children)
    review_remarks = (review_remark_map or {}).get(node_key, [])
    review_texts = [
        f"{str(item.get('actor_display') or item.get('actor_name') or item.get('actor_ip') or '未知')}：{str(item.get('proposed_value') or '')}"
        for item in review_remarks
    ]

    # 节点行
    row_classes = f"tree-row w-full {depth_parity}" + (" is-parent" if has_children else "")
    with ui.row().classes(row_classes):
        ui.html(_build_row_prefix_html(depth, has_children), sanitize=False)

        _render_star(node_path, label, value, filename, fav_direct, fav_covered, fav_entry_map, tree)

        lbl_class = f"lbl-{min(depth, 3)}" if has_children else ""
        txt_color = _LEVEL_TEXT_COLORS[min(depth, len(_LEVEL_TEXT_COLORS) - 1)]
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

        range_btn = None
        if is_param and range_meta:
            range_btn_id = _make_range_btn_id(node_path)
            range_btn = (
                ui.button("展开参数范围", icon="unfold_more")
                .props(f'flat dense size=sm id="{range_btn_id}"')
                .classes("mc-range-toggle")
            )

        ui.html('<span class="mc-row-spacer"></span>', sanitize=False)

        # 备注显示区：独立容器，保存/删除备注后仅局部刷新此处，不再整页重渲染。
        remark_box = ui.element("span").classes("mc-node-remark q-ml-sm")
        _REMARK_BOX_REGISTRY[(filename, node_key)] = remark_box
        with remark_box:
            _render_node_remarks(remark_box, node_key, filename)

        with ui.element("span").classes("mc-node-actions"):
            edit_btn = ui.button(icon="edit")
            edit_btn.props("flat round dense size=sm color=primary")
            edit_btn.classes("mc-inline-edit-btn")
            edit_btn.tooltip("添加修改备注")
            edit_btn.on(
                "click",
                lambda e=None, n=node, nk=node_key, tt=tree_type, nt=node_type, rb=remark_box: _open_edit_remark_dialog(
                    filename,
                    n,
                    nk,
                    tt,
                    nt,
                    session_active_tab,
                    remark_box=rb,
                ),
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

    # 子节点容器
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

            for child_index, child in enumerate(children_for_tree):
                _render_node(
                    child,
                    filename,
                    fav_direct,
                    fav_covered,
                    fav_entry_map,
                    tree,
                    session_active_tab,
                    depth + 1,
                    node_key=child.get("id") or reviewing.make_node_key(node_key, child_index),
                    tree_type=tree_type,
                    note_map=note_map,
                    review_remark_map=review_remark_map,
                    show_note=show_note,
                )


def _open_edit_remark_dialog(
    filename: str,
    node: dict,
    node_key: str,
    tree_type: str,
    node_type: str,
    session_active_tab: dict,
    remark_box: object | None = None,
) -> None:
    identity = get_identity_info()
    current_value = node.get("value")
    if current_value is None:
        ui.notify("当前节点没有直接值，请选择具体叶子条目添加备注", type="warning")
        return

    existing = storage.build_edit_remark_map(filename).get(node_key, [])

    with ui.dialog() as dialog, ui.card().classes("w-[560px] max-w-[96vw]"):
        ui.label("添加修改备注").classes("text-h6 q-mb-sm")
        ui.label(f"节点: {node.get('label') or node.get('id') or '-'}").classes("text-body2")
        ui.label(f"当前值: {current_value}").classes("text-caption text-grey q-mb-sm")
        ui.label(f"提交人: {identity['name']}  ({identity['ip']})").classes("text-caption text-grey q-mb-sm")

        value_input = ui.input(
            placeholder="输入建议修改值",
        ).props("outlined dense").classes("w-full")

        if existing:
            ui.label("已有备注").classes("text-subtitle2 q-mt-sm q-mb-xs")
            with ui.column().classes("w-full mc-review-note-list"):
                for item in existing:
                    _render_remark_chip(item, in_dialog=True)

        def do_save():
            proposed_value = str(value_input.value or "").strip()
            if not proposed_value:
                ui.notify("请输入修改值", type="warning")
                return
            try:
                storage.add_edit_remark(
                    source_file=filename,
                    node_key=node_key,
                    node_path=str(node.get("id") or ""),
                    node_label=str(node.get("label") or ""),
                    original_value=str(current_value),
                    proposed_value=proposed_value,
                    node_type=node_type,
                    tree_type=tree_type,
                    actor_ip=identity["ip"],
                    actor_name=identity["name"],
                )
            except Exception as e:
                ui.notify(str(e) or "保存失败", type="negative")
                return
            # 局部刷新：仅重渲染该节点的备注显示区，不再整页重渲染。
            if remark_box is not None:
                _render_node_remarks(remark_box, node_key, filename)
                remark_box.update()
            ui.notify("修改备注已记录", type="positive")
            dialog.close()

        with ui.row().classes("w-full justify-end q-gutter-sm q-mt-md"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("保存备注", icon="save", on_click=do_save).props("color=primary")

        dialog.open()


def _render_node_remarks(remark_box: object, node_key: str, filename: str) -> None:
    """渲染单个节点行的备注显示区（用于在保存/删除后局部刷新）。"""
    remark_box.clear()
    identity = get_identity_info()
    is_reviewer = bool(identity.get("is_admin"))
    client_ip = str(identity.get("ip") or "")
    remarks = storage.build_edit_remark_map(filename).get(node_key, [])
    if not remarks:
        return
    with remark_box:
        with ui.column().classes("mc-remark-list"):
            for item in remarks:
                _render_remark_chip(
                    item,
                    in_dialog=False,
                    remark_box=remark_box,
                    node_key=node_key,
                    filename=filename,
                    can_delete=(is_reviewer or str(item.get("actor_ip") or "") == client_ip),
                )


def _render_remark_chip(item: dict, in_dialog: bool = False, remark_box: object | None = None,
                        node_key: str = "", filename: str = "", can_delete: bool = False) -> None:
    """渲染单条备注为气泡卡片：含提交人色标、建议值、时间、状态。"""
    actor = str(item.get("actor_display") or item.get("actor_ip") or "未知")
    proposed = str(item.get("proposed_value") or "")
    created_at = str(item.get("created_at") or "")
    status = str(item.get("status") or "pending")
    status_cls = {
        "approved": "mc-remark-approved",
        "rejected": "mc-remark-rejected",
    }.get(status, "mc-remark-pending")

    # 用提交人名做稳定色标（取首字符 + 哈希选色）
    color = _remark_actor_color(actor)

    with ui.row().classes(f"mc-remark-chip {status_cls}" + (" mc-remark-chip--dialog" if in_dialog else "")):
        ui.label(actor[:1]).classes("mc-remark-avatar").style(
            f"background:{color};color:#fff"
        )
        with ui.column().classes("mc-remark-body"):
            ui.label(proposed).classes("mc-remark-value")
            meta = f"{actor}"
            if created_at:
                meta += f" · {created_at}"
            ui.label(meta).classes("mc-remark-meta")
        if can_delete and not in_dialog:
            ui.button(
                icon="delete",
                on_click=lambda e=None, rid=str(item.get("id") or ""), nk=node_key, fn=filename: _delete_viewer_remark(
                    rid, nk, fn,
                ),
            ).props("flat round dense size=xs color=negative").classes("mc-remark-delete-btn").tooltip("删除该备注")


def _delete_viewer_remark(remark_id: str, node_key: str, filename: str) -> None:
    """查看器删除单条备注：仅管理员或原始添加者（客户端 IP 匹配）可删除。"""
    try:
        ok = storage.delete_edit_remark(remark_id)
    except Exception as e:
        ui.notify(str(e) or "删除失败", type="negative")
        return
    if not ok:
        ui.notify("未找到该备注或已被删除", type="warning")
        return
    ui.notify("备注已删除", type="positive")
    # 局部刷新：仅重渲染该节点行的备注显示区（找到对应 remark_box 容器）。
    target_box = _REMARK_BOX_REGISTRY.get((filename, node_key))
    if target_box is not None:
        _render_node_remarks(target_box, node_key, filename)
        target_box.update()


def _remark_actor_color(actor: str) -> str:
    palette = [
        "#2563eb", "#0ea5e9", "#16a34a", "#f59e0b",
        "#dc2626", "#7c3aed", "#db2777", "#0891b2",
    ]
    return palette[abs(hash(actor)) % len(palette)]


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
