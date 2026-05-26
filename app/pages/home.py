"""主页渲染模块"""

from nicegui import ui

from app.core import storage
from app.core.favorites_live import resolve_overview_favorites
from app.utils.auth import is_deployer
from app.pages.viewer import render_file_viewer
from app.pages.management import render_management_page
from app.pages.bindings import render_bindings_page
from app.pages.tools import render_tools_page
from app.pages.comparison import render_comparison_page
from app.pages.history import render_history_page
from app.pages.dltool import render_dltool_page

# 模块级引用，用于取消收藏时触发 UI 刷新
_session_ref = {"tab": None}
_OVERVIEW_ASSETS_SENT = False


def render_home_page():
    """渲染主页"""
    deployer = is_deployer()

    session_tabs = []
    session_active_tab = {"name": "overview"}
    session_tab_history = []
    _session_ref["tab"] = session_active_tab

    # ---- 顶部导航栏 ----
    with ui.header().classes("items-center no-wrap bg-primary q-px-sm q-py-none"):
        # 左: 返回 + 标题
        ui.button(icon="home", on_click=lambda: _go_home(session_active_tab)
                  ).props("flat round dense color=white").tooltip("返回主页")
        ui.label("MCChecker").classes("text-subtitle1 text-white font-bold q-mr-sm")

        # 中: 快捷功能按钮组
        ui.button(icon="upload_file", on_click=lambda: _show_upload_dialog(session_tabs, session_active_tab, session_tab_history)
                  ).props("flat round dense color=white size=sm").tooltip("上传文件")
        ui.button(icon="cloud_download", on_click=lambda: _show_url_dialog(session_tabs, session_active_tab, session_tab_history)
                  ).props("flat round dense color=white size=sm").tooltip("URL下载")
        ui.button(icon="search", on_click=lambda: _show_search_dialog(session_tabs)
                  ).props("flat round dense color=white size=sm").tooltip("全局搜索")

        ui.label("|").classes("text-white text-weight-light q-mx-xs")

        # 管理功能（仅部署者）
        if deployer:
            ui.button(icon="settings", on_click=lambda: _switch_to_tab("management", "全量配置管理", session_tabs, session_active_tab, session_tab_history)
                      ).props("flat round dense color=white size=sm").tooltip("全量管理")
            ui.button(icon="link", on_click=lambda: _switch_to_tab("bindings", "变量绑定配置", session_tabs, session_active_tab, session_tab_history)
                      ).props("flat round dense color=white size=sm").tooltip("变量绑定")
            ui.button(icon="build", on_click=lambda: _switch_to_tab("tools", "工具菜单", session_tabs, session_active_tab, session_tab_history)
                      ).props("flat round dense color=white size=sm").tooltip("工具菜单")

        ui.space()

    # ---- 左侧文件导航 ----
    with ui.left_drawer(bordered=True).classes("w-48 bg-grey-1 q-pa-sm"):
        _render_sidebar(session_tabs, session_active_tab, session_tab_history)

    # ---- 右侧工具栏 ----
    with ui.right_drawer(bordered=True).classes("w-56 bg-grey-1 q-pa-sm"):
        _render_tools_sidebar(session_tabs, session_active_tab)

    # ---- 主内容区 ----
    with ui.column().classes("w-full flex-1 p-4 overflow-auto"):
        _render_main_content(deployer, session_tabs, session_active_tab, session_tab_history)


def _go_home(session_active_tab: dict):
    """返回主页"""
    session_active_tab["name"] = "overview"


def _go_back(session_active_tab: dict, session_tab_history: list):
    """返回上一页面，无历史时返回概览"""
    if session_tab_history:
        session_active_tab["name"] = session_tab_history.pop()
    else:
        session_active_tab["name"] = "overview"


def _render_sidebar(session_tabs: list, session_active_tab: dict, session_tab_history: list):
    """左侧导航：文件列表（卡片式）

    全量配置中的文件：显示配置名 + 灰色小字（真实文件名 + 更新时间）
    其他文件：直接显示文件名
    """
    ui.label("文件列表").classes("text-body2 text-weight-bold text-grey-7 q-mb-sm")

    # 加载全量配置映射，建立 文件名->配置名 的关系
    mapping = storage.load_config_mapping()
    mapping_names = {m["name"] for m in mapping}

    files = storage.list_config_files()
    if files:
        for fname in files:
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            icon_map = {"json": "data_object", "xml": "code"}
            icon = icon_map.get(ext, "description")
            color_map = {"json": "blue", "xml": "orange"}
            color = color_map.get(ext, "grey")

            with ui.card().classes(
                "w-full cursor-pointer q-mb-xs q-pa-sm bg-white hover:bg-blue-50"
            ).on("click", lambda f=fname: _open_file_tab(f, session_tabs, session_active_tab, session_tab_history)):
                with ui.row().classes("items-center no-wrap"):
                    ui.icon(icon, color=color, size="sm").classes("q-mr-sm")
                    with ui.column().classes("q-ma-none q-pa-none"):
                        if fname in mapping_names:
                            # 全量配置中的文件：显示配置记录的名称
                            ui.label(fname).classes("text-body1 text-weight-medium q-mb-none")
                            update_date = storage.get_file_update_date(fname) or ""
                            sub_text = fname
                            if update_date:
                                sub_text += "  |  " + update_date
                            ui.label(sub_text).classes("text-caption text-grey q-mt-none")
                        else:
                            # 普通上传的文件：直接显示文件名
                            ui.label(fname).classes("text-body1 text-weight-medium q-mb-none")
    else:
        ui.label("暂无文件").classes("text-caption text-grey")


def _render_tools_sidebar(session_tabs: list = None, session_active_tab: dict = None):
    """右侧工具栏：展示已配置的工具 + DL快捷计算入口"""
    ui.label("工具箱").classes("text-caption text-weight-bold text-grey-7 q-mb-xs")

    # ---- DL 快捷计算 ----
    with ui.card().classes("w-full cursor-pointer q-mb-xs q-pa-sm bg-white hover:bg-blue-50") \
            .on("click", lambda: _switch_to_tab("dltool", "DL快捷计算", session_tabs, session_active_tab, None)):
        with ui.row().classes("items-center"):
            ui.icon("functions", size="sm", color="blue-8").classes("q-mr-sm")
            with ui.column().classes("q-pa-none"):
                ui.label("DL 快捷计算").classes("text-subtitle2 font-bold q-mb-none")
                ui.label("八阶函数计算器").classes("text-caption text-grey q-mt-none")

    tools = storage.load_tools()
    if tools:
        for tool in tools:
            with ui.card().classes("w-full cursor-pointer q-mb-xs q-pa-sm bg-white").on(
                "click", lambda url=tool["url"]: ui.run_javascript(f'window.open("{url}", "_blank")')
            ):
                ui.label(tool["name"]).classes("text-subtitle2 font-bold q-mb-none")
                desc = tool.get("description", "")
                if desc:
                    ui.label(desc).classes("text-caption text-grey q-mt-none")
    else:
        ui.label("暂无工具").classes("text-caption text-grey")
        ui.label("在工具菜单中添加").classes("text-caption text-grey")


def _open_file_tab(filename: str, session_tabs: list, session_active_tab: dict, session_tab_history: list = None):
    for tab in session_tabs:
        if tab["name"] == f"file:{filename}":
            if session_tab_history is not None:
                session_tab_history.append(session_active_tab["name"])
            session_active_tab["name"] = tab["name"]
            return
    if session_tab_history is not None:
        session_tab_history.append(session_active_tab["name"])
    session_tabs.append({"name": f"file:{filename}", "label": filename, "type": "file", "filename": filename})
    session_active_tab["name"] = f"file:{filename}"


def _switch_to_tab(tab_name: str, tab_label: str, session_tabs: list, session_active_tab: dict, session_tab_history: list = None):
    for tab in session_tabs:
        if tab["name"] == tab_name:
            if session_tab_history is not None:
                session_tab_history.append(session_active_tab["name"])
            session_active_tab["name"] = tab_name
            return
    if session_tab_history is not None:
        session_tab_history.append(session_active_tab["name"])
    session_tabs.append({"name": tab_name, "label": tab_label, "type": tab_name})
    session_active_tab["name"] = tab_name


def _render_main_content(deployer: bool, session_tabs: list, session_active_tab: dict, session_tab_history: list):
    overview_container = ui.column().classes("w-full")
    with overview_container:
        _render_overview_panel(deployer, session_tabs, session_active_tab)

    tabs_container = ui.column().classes("w-full hidden")

    def refresh_tabs():
        current = session_active_tab.get("_rendered", None)
        active = session_active_tab["name"]
        if current != active:
            session_active_tab["_rendered"] = active
            _update_tabs_display(session_tabs, session_active_tab, tabs_container, overview_container, deployer, session_tab_history)

    ui.timer(0.3, refresh_tabs)

    last_overview_sig = {"sig": None}

    def _compute_overview_sig():
        import os
        sig_parts = []
        try:
            sig_parts.append(("fav", os.path.getmtime(storage.FAVORITES_FILE)))
        except OSError:
            sig_parts.append(("fav", 0))
        favorites = storage.load_favorites()
        sig_parts.append(("fav_count", len(favorites)))
        files = sorted({f.get("source_file") for f in favorites if f.get("source_file")})
        for fname in files:
            path = storage.get_config_path(fname)
            if os.path.exists(path):
                st = os.stat(path)
                sig_parts.append((fname, 1, st.st_mtime_ns, st.st_size))
            else:
                sig_parts.append((fname, 0))
        return tuple(sig_parts)

    def refresh_overview():
        if session_active_tab["name"] != "overview":
            return
        sig = _compute_overview_sig()
        if last_overview_sig["sig"] != sig:
            last_overview_sig["sig"] = sig
            overview_container.clear()
            with overview_container:
                _render_overview_panel(deployer, session_tabs, session_active_tab)

    ui.timer(1.0, refresh_overview)


def _render_overview_panel(deployer: bool, session_tabs: list, session_active_tab: dict):
    """配置速览面板 - 每个收藏项一个卡片，卡片内展示层级结构"""
    ui.label("配置速览").classes("text-h5 q-mb-md")

    global _OVERVIEW_ASSETS_SENT
    if not _OVERVIEW_ASSETS_SENT:
        ui.add_head_html("""<style>
.fav-card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 12px;
}
.fav-card {
    border-left: 4px solid;
    border-radius: 8px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    transition: box-shadow 0.2s;
    overflow: hidden;
}
.fav-card:hover {
    box-shadow: 0 2px 8px rgba(0,0,0,0.12);
}
/* ---- card-internal tree ---- */
.fav-card .card-tree {
    font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
    line-height: 1.4;
    user-select: none;
}
.fav-card .card-tree .tree-row {
    display: flex;
    align-items: center;
    height: 26px;
    padding: 0 4px 0 0;
    border-radius: 3px;
    cursor: default;
    transition: background 0.08s ease;
    gap: 0;
    margin: 0;
    box-sizing: border-box;
}
.fav-card .card-tree .tree-row:hover {
    background: rgba(0, 0, 0, 0.03);
}
.fav-card .card-tree .indent-cell {
    width: 16px;
    align-self: stretch;
    position: relative;
    flex-shrink: 0;
}
.fav-card .card-tree .indent-cell .guide-line {
    position: absolute;
    left: 50%;
    top: -1px;
    bottom: -1px;
    width: 1.5px;
    transform: translateX(-50%);
    border-radius: 1px;
    opacity: 0.35;
    transition: opacity 0.12s ease;
}
.fav-card .card-tree .tree-row:hover .indent-cell .guide-line {
    opacity: 0.60;
}
.fav-card .card-tree .toggle-btn {
    width: 16px;
    align-self: stretch;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    cursor: pointer;
    border-radius: 2px;
    transition: background 0.08s ease;
    outline: none;
}
.fav-card .card-tree .toggle-btn:hover {
    background: rgba(0, 0, 0, 0.06);
}
.fav-card .card-tree .toggle-btn::before {
    content: '';
    display: block;
    width: 0;
    height: 0;
    border-left: 4px solid #999;
    border-top: 2.8px solid transparent;
    border-bottom: 2.8px solid transparent;
    transition: transform 0.15s cubic-bezier(0.4, 0, 0.2, 1);
}
.fav-card .card-tree .toggle-btn.expanded::before {
    transform: rotate(90deg);
}
.fav-card .card-tree .leaf-marker {
    width: 16px;
    align-self: stretch;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
}
.fav-card .card-tree .leaf-marker::after {
    content: '';
    width: 3.5px;
    height: 3.5px;
    border-radius: 50%;
    background: #bbb;
}
.fav-card .card-tree .tree-label {
    display: flex;
    align-items: center;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 0.8rem;
}
.fav-card .card-tree .lbl-0 { font-weight: 600; font-size: 0.82rem; }
.fav-card .card-tree .lbl-1 { font-weight: 500; font-size: 0.79rem; }
.fav-card .card-tree .tree-label .val-text {
    color: #1565C0;
    opacity: 0.68;
    font-size: 0.88em;
}
.fav-card .card-tree .children-wrap {
    overflow: hidden;
    margin: 0 !important;
    padding: 0 !important;
    transition: max-height 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}
.fav-note-input .q-field__control {
    min-height: 28px !important;
    height: 28px !important;
}
.fav-note-input .q-field__marginal {
    height: 28px !important;
}
.fav-group-header {
    border-bottom: 1px solid #e8e8e8;
}
</style>""")

        ui.add_head_html("""<style>
.fav-group-deleted .q-expansion-item__header {
    background: rgba(229, 115, 115, 0.08);
}
</style>""")

        ui.run_javascript("""
        if (!window.mct) {
            window.mct = function(el) {
                var row = el.closest('.tree-row');
                if (!row) return;
                var kids = row.nextElementSibling;
                if (!kids || !kids.classList.contains('children-wrap')) return;
                if (kids.style.maxHeight === '0px') {
                    kids.style.maxHeight = 'none';
                    el.classList.remove('collapsed');
                    el.classList.add('expanded');
                } else {
                    kids.style.maxHeight = '0px';
                    el.classList.add('collapsed');
                    el.classList.remove('expanded');
                }
            };
        }
    """)
        _OVERVIEW_ASSETS_SENT = True

    # 树节点颜色（与 viewer 一致）
    _card_line_colors = [
        "#5C9CE6", "#4DB6AC", "#FFB74D", "#BA68C8",
        "#81C784", "#E57373", "#64B5F6", "#FFD54F",
    ]

    favorites = storage.load_favorites()

    # ---- 收藏变量 ----
    if not favorites:
        ui.label("暂无收藏变量，可在文件解析页面中收藏关键变量").classes("text-caption text-grey q-mb-lg q-mt-md")
        return

    active_grouped, deleted_grouped = resolve_overview_favorites(favorites)
    file_order = list(active_grouped.keys()) + [f for f in deleted_grouped.keys() if f not in active_grouped]
    file_colors = {fname: _CARD_ACCENTS[i % len(_CARD_ACCENTS)] for i, fname in enumerate(file_order)}

    for file_name, items in active_grouped.items():
        count = len(items)
        with ui.expansion(value=True).classes("w-full").props(
            f"label='{file_name}  ({count} 项)' header-class='fav-group-header'"
        ):
            with ui.row().classes("items-center q-gutter-xs"):
                update_date = storage.get_file_update_date(file_name)
                if update_date:
                    ui.label(f"更新: {update_date}").classes("text-caption text-grey")
                ui.button(icon="delete_sweep",
                          on_click=lambda f=file_name: _remove_all_favs_for_file(f)
                          ).props("flat round dense size=sm color=grey-5").tooltip("清空该文件所有收藏")

            with ui.column().classes("fav-card-grid w-full q-mt-sm"):
                for idx, item in enumerate(items):
                    card_accent_idx = list(file_colors.keys()).index(file_name) + idx
                    _render_fav_card(file_name, item, card_accent_idx, _card_line_colors)

    if deleted_grouped:
        deleted_count = sum(len(v) for v in deleted_grouped.values())
        with ui.expansion(value=True).classes("w-full fav-group-deleted").props(
            f"label='已删除  ({deleted_count} 项)' header-class='fav-group-header'"
        ):
            for file_name, items in deleted_grouped.items():
                count = len(items)
                with ui.expansion(value=False).classes("w-full").props(
                    f"label='{file_name}  ({count} 项)' header-class='fav-group-header'"
                ):
                    ui.label("文件不存在，相关收藏项已临时移入已删除。恢复文件后将自动回到原分组。").classes("text-caption text-orange-8 q-mb-sm")
                    with ui.column().classes("fav-card-grid w-full q-mt-sm"):
                        for idx, item in enumerate(items):
                            card_accent_idx = list(file_colors.keys()).index(file_name) + idx if file_name in file_colors else idx
                            _render_fav_card(file_name, item, card_accent_idx, _card_line_colors)


# 卡片色彩列表：循环分配
_CARD_ACCENTS = [
    ("blue", "#1976D2"),
    ("teal", "#00897B"),
    ("orange", "#EF6C00"),
    ("purple", "#7B1FA2"),
    ("green", "#388E3C"),
    ("deep-orange", "#D84315"),
    ("cyan", "#00838F"),
    ("indigo", "#303F9F"),
    ("pink", "#C2185B"),
    ("lime", "#689F38"),
]


def _render_fav_card(file_name: str, item: dict, color_index: int = 0,
                     line_colors: list = None):
    """渲染单个收藏卡片，内含完整层级树"""
    if line_colors is None:
        line_colors = ["#5C9CE6", "#4DB6AC", "#FFB74D", "#BA68C8",
                       "#81C784", "#E57373", "#64B5F6", "#FFD54F"]

    accent_name, accent_hex = _CARD_ACCENTS[color_index % len(_CARD_ACCENTS)]
    children = item.get("children", [])
    has_children = bool(children)
    is_leaf = not has_children and item.get("value") is not None

    with ui.card().classes("fav-card q-pa-sm w-full").style(
        f"border-left-color: {accent_hex}"
    ):
        # 隐藏标记，用于取消收藏时定位 DOM 元素
        ui.html(f'<span data-fav-path="{item["path"]}" style="display:none"></span>', sanitize=False)

        # ---- 卡片头部 ----
        with ui.row().classes("items-center w-full"):
            ui.icon("star", size="sm", color="yellow").classes("q-mr-xs")
            ui.label(item["label"]).classes("text-subtitle2 font-bold q-mr-sm")
            if is_leaf and item.get("value") is not None:
                ui.label(str(item["value"])).classes("font-mono text-caption text-grey-8")
            if item.get("_missing"):
                ui.label("未检测到，请检查").classes("text-caption text-orange-8")
            ui.space()
            ui.button(icon="close",
                      on_click=lambda p=item["path"], f=file_name: _quick_remove_fav(p, f)
                      ).props("flat round dense size=xs color=grey-5").tooltip("取消收藏")

        # ---- 备注输入 ----
        note_val = item.get("note", "")
        note_input = ui.input(
            value=note_val,
            placeholder="添加备注...",
        ).props("dense outlined hide-bottom-space").classes(
            "fav-note-input w-full text-caption q-mt-xs"
        ).style("font-size: 0.75rem")
        note_input.on("blur", lambda v=note_input, p=item["path"], f=file_name: _save_fav_note(p, f, v.value))
        note_input.on("keydown.enter", lambda v=note_input, p=item["path"], f=file_name: _save_fav_note(p, f, v.value))

        # ---- 层级树 ----
        if has_children:
            with ui.column().classes("card-tree w-full q-mt-sm q-pa-xs"):
                for child in children:
                    _render_card_tree_node(child, depth=0, line_colors=line_colors)


def _render_card_tree_node(node: dict, depth: int = 0, line_colors: list = None):
    """递归渲染卡片内的树节点"""
    if line_colors is None:
        line_colors = ["#5C9CE6", "#4DB6AC", "#FFB74D", "#BA68C8",
                       "#81C784", "#E57373", "#64B5F6", "#FFD54F"]

    label = node["label"]
    value = node.get("value")
    children = node.get("children", [])
    has_children = bool(children)

    # 构建缩进竖线 HTML
    indent_parts = []
    for d in range(depth):
        color = line_colors[d % len(line_colors)]
        indent_parts.append(
            f'<span class="indent-cell">'
            f'<span class="guide-line" style="background:{color}"></span>'
            f'</span>'
        )

    # 箭头或叶子标记
    if has_children:
        indent_parts.append(
            '<span class="toggle-btn expanded" onclick="window.mct&&window.mct(this)"></span>'
        )
    else:
        indent_parts.append('<span class="leaf-marker"></span>')

    prefix_html = ''.join(indent_parts)

    # 行
    row_classes = "tree-row w-full" + (" is-parent" if has_children else "")
    with ui.row().classes(row_classes):
        ui.html(prefix_html, sanitize=False)
        txt_color = ["dark", "grey-9", "grey-8", "grey-7", "grey-6"][min(depth, 4)]
        lbl_class = f"lbl-{min(depth, 3)}"
        if value is not None:
            ui.html(
                f'<span class="font-mono tree-label text-{txt_color} {lbl_class}">'
                f'{label} <span class="val-text">= {value}</span></span>',
                sanitize=False
            )
        else:
            ui.html(
                f'<span class="tree-label text-{txt_color} {lbl_class}">'
                f'{label}</span>',
                sanitize=False
            )

    # 子节点
    if has_children:
        children_wrap = ui.column().classes("children-wrap q-pa-none q-ma-none")
        with children_wrap:
            for child in children:
                _render_card_tree_node(child, depth + 1, line_colors)


def _quick_remove_fav(path: str, source_file: str):
    """快捷取消收藏（不刷新页面，保留折叠状态）"""
    storage.remove_favorite(path, source_file)
    ui.notify("已取消收藏", type="info")
    # 直接从 DOM 移除对应卡片
    ui.run_javascript(f"""
        var m = document.querySelector('[data-fav-path="{path}"]');
        if (m) {{ var c = m.closest('.q-card'); if (c) c.remove(); }}
    """)
    # 清除渲染标记，确保切页后数据一致
    tab = _session_ref.get("tab")
    if tab:
        tab.pop("_rendered", None)


def _remove_all_favs_for_file(source_file: str):
    """清空某文件下的所有收藏"""
    favorites = storage.load_favorites()
    new_favs = [f for f in favorites if f["source_file"] != source_file]
    storage.save_favorites(new_favs)
    ui.notify(f"已清空 {source_file} 的所有收藏", type="info")
    # 移除对应文件分组下所有卡片
    for fav in [f for f in favorites if f["source_file"] == source_file]:
        ui.run_javascript(f"""
            var m = document.querySelector('[data-fav-path="{fav['path']}"]');
            if (m) {{ var c = m.closest('.q-card'); if (c) c.remove(); }}
        """)
    tab = _session_ref.get("tab")
    if tab:
        tab.pop("_rendered", None)


def _save_fav_note(path: str, source_file: str, note: str):
    """保存收藏备注"""
    storage.update_favorite_note(path, source_file, note)


def _update_tabs_display(session_tabs, session_active_tab, tabs_container, overview_container, deployer, session_tab_history):
    if session_active_tab["name"] == "overview":
        overview_container.classes(remove="hidden")
        tabs_container.classes(add="hidden")
        return

    overview_container.classes(add="hidden")
    tabs_container.classes(remove="hidden")

    active_tab = None
    for tab in session_tabs:
        if tab["name"] == session_active_tab["name"]:
            active_tab = tab
            break

    if not active_tab:
        return

    tabs_container.clear()
    with tabs_container:
        with ui.row().classes("w-full items-center q-mb-md"):
            ui.button(icon="arrow_back",
                      on_click=lambda: _go_back(session_active_tab, session_tab_history)
                      ).props("flat round dense").tooltip("返回上一页")
            ui.label(active_tab["label"]).classes("text-h6 q-ml-sm")
            ui.space()
            if active_tab["type"] in ("file", "archive_view"):
                search_input = ui.input(placeholder="搜索当前文件...").props("dense outlined").classes("w-64")
                ui.button(icon="search",
                          on_click=lambda: _do_local_search(search_input.value, active_tab.get("filename", ""))
                          ).props("flat round dense")
            ui.button(icon="close",
                      on_click=lambda t=active_tab: _close_tab(t, session_tabs, session_active_tab, overview_container, tabs_container, session_tab_history)
                      ).props("flat round dense").tooltip("关闭标签页")

        tab_type = active_tab["type"]
        if tab_type == "file":
            render_file_viewer(active_tab["filename"], deployer, session_tabs, session_active_tab)
        elif tab_type == "archive_view":
            _render_archive_viewer(active_tab, deployer)
        elif tab_type == "management":
            render_management_page(deployer)
        elif tab_type == "bindings":
            render_bindings_page(deployer)
        elif tab_type == "tools":
            render_tools_page(deployer)
        elif tab_type == "comparison":
            render_comparison_page(active_tab, deployer)
        elif tab_type == "history":
            render_history_page(active_tab, deployer, session_tabs, session_active_tab)
        elif tab_type == "dltool":
            render_dltool_page(
                on_refresh=lambda: session_active_tab.pop("_rendered", None)
            )


def _render_archive_viewer(tab: dict, deployer: bool):
    """渲染归档文件查看器"""
    from app.core import parser, parse_cache

    filename = tab.get("filename", "")
    archive_filename = tab.get("archive_filename", "")
    source_path = storage.get_archived_path(filename, archive_filename)
    tree = parse_cache.load_tree(source_path)
    if tree is None:
        content = storage.load_archived_file(filename, archive_filename)
        if content is None:
            ui.label(f"归档文件 {archive_filename} 不存在").classes("text-negative")
            return
        try:
            tree = parser.parse_file(content, archive_filename)
        except ValueError as e:
            ui.label(f"解析失败: {e}").classes("text-negative")
            return
        parse_cache.save_tree(source_path, tree)

    with ui.row().classes("items-center q-mb-md"):
        ui.badge(archive_filename, color="grey")
        ui.label("(历史版本)").classes("text-caption text-grey")

    ui.add_head_html("""<style>
.fav-tree .depth-1 { padding-left: 20px; }
.fav-tree .depth-2 { padding-left: 40px; }
.fav-tree .depth-3 { padding-left: 60px; }
.fav-tree .depth-4 { padding-left: 80px; }
.fav-tree .fav-node-header { border-radius: 4px; padding: 2px 6px; margin: 1px 0; }
.fav-tree .fav-node-header:hover { background: #f5f5f5; }
</style>""")

    with ui.card().classes("w-full overflow-auto fav-tree"):
        for child in tree.get("children", []):
            _render_archive_node(child, depth=0)


def _render_archive_node(node: dict, depth: int = 0):
    """递归渲染归档文件的树节点（只读，无收藏功能）"""
    label = node["label"]
    value = node.get("value")
    children = node.get("children", [])
    level_colors = ["dark", "grey-9", "grey-8", "grey-7", "grey-6"]
    color = level_colors[min(depth, len(level_colors) - 1)]
    depth_class = f"depth-{min(depth, 3)}" if depth > 0 else ""

    if children:
        with ui.expansion(value=True).classes(f"fav-node {depth_class} w-full"):
            with ui.row().classes("items-center fav-node-header w-full no-wrap"):
                ui.icon("star_outline", size="xs", color="grey-4").classes("q-mr-xs")
                if value is not None:
                    ui.label(f"{label} = {value}").classes(f"font-mono text-body2 text-{color}")
                else:
                    ui.label(label).classes(f"text-body2 text-weight-medium text-{color}")
            for child in children:
                _render_archive_node(child, depth + 1)
    else:
        with ui.row().classes(f"items-center q-py-xs fav-leaf fav-node {depth_class} w-full"):
            ui.icon("star_outline", size="xs", color="grey-4").classes("q-mr-xs")
            if value is not None:
                ui.label(f"{label} = {value}").classes("font-mono text-body2")
            else:
                ui.label(label).classes("font-mono text-body2 text-grey")


def _close_tab(tab, session_tabs, session_active_tab, overview_container, tabs_container, session_tab_history=None):
    if tab in session_tabs:
        session_tabs.remove(tab)
    # Return to previous tab if available, otherwise go to overview
    if session_tab_history and session_tabs:
        # Find the most recent history entry that still exists
        while session_tab_history:
            prev = session_tab_history.pop()
            if any(t["name"] == prev for t in session_tabs):
                session_active_tab["name"] = prev
                return
    session_active_tab["name"] = "overview"
    overview_container.classes(remove="hidden")
    tabs_container.classes(add="hidden")


def _do_local_search(keyword: str, filename: str):
    from app.core import parser as parser_mod, parse_cache

    if not keyword:
        ui.notify("请输入搜索关键词", type="warning")
        return
    try:
        source_path = storage.get_config_path(filename)
        tree = parse_cache.load_tree(source_path)
        if tree is None:
            content = storage.load_config_file(filename)
            if content is None:
                return
            tree = parser_mod.parse_file(content, filename)
            parse_cache.save_tree(source_path, tree)
        flat = parser_mod.flatten_tree(tree)
        matches = [n for n in flat if keyword.lower() in (n.get("label", "") + str(n.get("value", ""))).lower()]
        ui.notify(f"找到 {len(matches)} 项匹配", type="info")
    except ValueError:
        ui.notify("解析失败", type="negative")


def _show_upload_dialog(session_tabs: list, session_active_tab: dict, session_tab_history: list):
    from app.core import parser

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("上传配置文件").classes("text-h6")
        ui.label("支持 XML 和 JSON 格式").classes("text-caption text-grey q-mb-md")

        uploaded = {"data": None, "name": None}

        async def handle_upload(e):
            uploaded["data"] = await e.file.read()
            uploaded["name"] = e.file.name

        ui.upload(label="选择文件", auto_upload=True, on_upload=handle_upload).props("dense").classes("w-full")

        def process_upload():
            if uploaded["data"] is None:
                ui.notify("请先上传文件", type="warning")
                return
            filename = uploaded["name"]
            try:
                parser.parse_file(uploaded["data"], filename)
            except ValueError as e:
                ui.notify(f"解析失败: {e}", type="negative")
                return
            if is_deployer():
                storage.save_config_file(filename, uploaded["data"])
                ui.notify(f"已解析并保存: {filename}", type="positive")
            else:
                ui.notify(f"已解析: {filename} (仅会话可见)", type="info")
            _open_file_tab(filename, session_tabs, session_active_tab, session_tab_history)
            dialog.close()

        with ui.row().classes("w-full justify-end"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("解析", on_click=process_upload).props("color=primary")

        dialog.open()


def _show_url_dialog(session_tabs: list, session_active_tab: dict, session_tab_history: list):
    from app.core import downloader, parser

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("从链接下载配置文件").classes("text-h6")
        url_input = ui.input(placeholder="输入文件下载链接").props("dense outlined").classes("w-full")
        filename_input = ui.input(placeholder="保存文件名 (例: config.xml)").props("dense outlined").classes("w-full")

        async def process_download():
            url = url_input.value
            filename = filename_input.value
            if not url:
                ui.notify("请输入链接", type="warning")
                return
            if not filename:
                filename = url.rsplit("/", 1)[-1] if "/" in url else "downloaded_config"
            content = downloader.download_file(url)
            if content is None:
                ui.notify("下载失败", type="negative")
                return
            try:
                parser.parse_file(content, filename)
            except ValueError as e:
                ui.notify(f"解析失败: {e}", type="negative")
                return
            if is_deployer():
                storage.save_config_file(filename, content)
                ui.notify(f"已下载并保存: {filename}", type="positive")
            else:
                ui.notify(f"已下载并解析: {filename} (仅会话可见)", type="info")
            _open_file_tab(filename, session_tabs, session_active_tab, session_tab_history)
            dialog.close()

        with ui.row().classes("w-full justify-end"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("下载并解析", on_click=process_download).props("color=primary")

        dialog.open()


def _show_search_dialog(session_tabs: list):
    from app.core import parser as parser_mod

    with ui.dialog() as dialog, ui.card().classes("w-[600px]"):
        ui.label("全局搜索").classes("text-h6")
        search_input = ui.input(placeholder="输入搜索关键词...").props("dense outlined").classes("w-full")
        results_container = ui.column().classes("w-full max-h-96 overflow-auto")

        def do_search():
            keyword = search_input.value.strip()
            if not keyword:
                return
            results_container.clear()
            with results_container:
                files = storage.list_config_files()
                for fname in files:
                    content = storage.load_config_file(fname)
                    if content is None:
                        continue
                    try:
                        tree = parser_mod.parse_file(content, fname)
                        flat = parser_mod.flatten_tree(tree)
                        matches = [n for n in flat if keyword.lower() in (n.get("label", "") + str(n.get("value", ""))).lower()]
                        if matches:
                            ui.label(f"{fname} ({len(matches)} 项匹配)").classes("text-subtitle2 q-mt-md")
                            for m in matches[:20]:
                                with ui.row().classes("items-center q-ml-md"):
                                    ui.label(m["path"]).classes("font-mono text-body2")
                                    if m.get("value"):
                                        ui.label(f"= {m['value']}").classes("font-mono text-caption text-grey")
                    except ValueError:
                        pass

                favorites = storage.load_favorites()
                note_matches = [f for f in favorites if keyword.lower() in f.get("note", "").lower()]
                if note_matches:
                    ui.label(f"备注 ({len(note_matches)} 项匹配)").classes("text-subtitle2 q-mt-md")
                    for fav in note_matches:
                        with ui.row().classes("items-center q-ml-md"):
                            ui.label(f"{fav['label']} ({fav['source_file']})").classes("text-body2")
                            ui.label(f"备注: {fav['note']}").classes("text-caption text-grey")

        search_input.on("keydown.enter", do_search)
        ui.button("搜索", icon="search", on_click=do_search).props("color=primary")
        with ui.row().classes("w-full justify-end"):
            ui.button("关闭", on_click=dialog.close).props("flat")

        dialog.open()
