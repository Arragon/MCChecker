"""主页渲染模块"""

import asyncio
import html
import json
import time

from nicegui import app, ui

from app.core import storage
from app.core import device_models
from app.core import tab_manager
from app.core import tabs_state
from app.core.favorites_live import resolve_overview_favorites
from app.utils.auth import get_identity_info, is_admin, is_deployer
from app.pages.viewer import load_tree_for_viewer, render_file_viewer
from app.pages.management import render_management_page
from app.pages.bindings import render_bindings_page
from app.pages.tools import render_tools_page
from app.pages.comparison import render_comparison_page
from app.pages.history import render_history_page
from app.pages.dltool import render_dltool_page
from app.pages.search import render_search_page
from app.pages.records import render_records_page
from app.pages.review import render_review_page
from app.pages.file_downloads import (
    DOWNLOAD_KIND_ARCHIVE,
    DOWNLOAD_KIND_CURRENT,
    make_download_handler,
)
from app.pages.theme import ensure_theme

_TAB_LIMIT = 15

# ---- 全局繁忙标记：防止重任务期间 drawer/侧边栏同步冲突 ----
_busy_processing = {"value": False}


def render_home_page():
    """渲染主页"""
    ensure_theme()

    selected_model = device_models.normalize_selected(app.storage.user.get("device_model"))
    app.storage.user["device_model"] = selected_model
    storage.set_active_profile(selected_model)

    deployer = is_deployer()
    admin = is_admin()
    identity = get_identity_info()

    session_tabs = []
    session_active_tab = {"name": "overview"}
    session_tab_history = []

    _restore_tabs_state(selected_model, session_tabs, session_active_tab, session_tab_history)
    for t in session_tabs:
        if t.get("type") == "management":
            t["label"] = "更新设置"

    left_drawer = ui.left_drawer(bordered=True).props("show-if-above").classes("w-[320px] max-w-[45vw] q-pa-sm")
    with left_drawer:
        _render_sidebar(session_tabs, session_active_tab, session_tab_history)

    right_drawer = ui.right_drawer(bordered=True).props("show-if-above").classes("w-72 q-pa-sm")
    with right_drawer:
        _render_tools_sidebar(session_tabs, session_active_tab)

    # ---- 顶部导航栏 ----
    with ui.header().classes("items-center no-wrap q-px-sm q-py-none mc-header"):
        ui.button(icon="menu", on_click=left_drawer.toggle).props("flat round dense color=white").classes("lt-md") \
            .tooltip("打开/关闭菜单")

        # 左: 返回 + 标题
        ui.button(icon="home", on_click=lambda: _go_home(session_active_tab)
                  ).props("flat round dense color=white").tooltip("返回主页")
        ui.label("MCChecker").classes("text-subtitle1 text-white font-bold q-mr-sm")

        # 中: 快捷功能按钮组
        ui.button(icon="upload_file", on_click=lambda: _show_upload_dialog(session_tabs, session_active_tab, session_tab_history)
                  ).props("flat round dense color=white size=sm").tooltip("上传文件")
        ui.button(icon="cloud_download", on_click=lambda: _show_url_dialog(session_tabs, session_active_tab, session_tab_history)
                  ).props("flat round dense color=white size=sm").tooltip("URL下载")
        ui.button(icon="search", on_click=lambda: _show_search_dialog(session_tabs, session_active_tab, session_tab_history)
                  ).props("flat round dense color=white size=sm").tooltip("全局搜索")

        ui.label("|").classes("text-white text-weight-light q-mx-xs")

        # 管理功能（仅部署者）
        if deployer:
            ui.button(icon="settings", on_click=lambda: _switch_to_tab("management", "更新设置", session_tabs, session_active_tab, session_tab_history)
                      ).props("flat round dense color=white size=sm").tooltip("更新设置")
            ui.button(icon="link", on_click=lambda: _switch_to_tab("bindings", "变量绑定配置", session_tabs, session_active_tab, session_tab_history)
                      ).props("flat round dense color=white size=sm").tooltip("变量绑定")
            ui.button(icon="build", on_click=lambda: _switch_to_tab("tools", "工具菜单", session_tabs, session_active_tab, session_tab_history)
                      ).props("flat round dense color=white size=sm").tooltip("工具菜单")
        if admin:
            ui.button(icon="rule", on_click=lambda: _switch_to_tab("review", "审阅修改", session_tabs, session_active_tab, session_tab_history)
                      ).props("flat round dense color=white size=sm").tooltip("审阅修改")

        ui.space()

        with ui.button(icon="devices").props("flat round dense color=white").tooltip("机型切换"):
            with ui.menu():
                models = device_models.load_models()
                for m in models:
                    mid = m.get("id")
                    if not mid:
                        continue
                    label = m.get("name") or mid
                    ui.item(label, on_click=lambda e=None, v=mid: _switch_device_model(v))

                ui.separator()
                ui.item("新增机型", on_click=lambda: _show_add_model_dialog(selected_model)).props("clickable")
                if selected_model != device_models.DEFAULT_MODEL_ID:
                    ui.item("删除当前机型", on_click=lambda: _show_delete_model_dialog(selected_model)).props("clickable")
                ui.item("管理机型", on_click=lambda: _show_manage_models_dialog(selected_model)).props("clickable")

        ui.label(device_models.get_model_name(selected_model)).classes("text-white text-caption q-ml-xs ellipsis gt-xs").style("max-width: 160px")
        ui.badge(identity["role_label"], color="positive" if identity["is_admin"] else "grey-7").classes("q-ml-sm")
        ui.label(identity["name"]).classes("text-white text-caption q-ml-xs ellipsis gt-xs").style("max-width: 120px")
        ui.label(identity["ip"]).classes("text-white text-caption q-ml-xs ellipsis gt-sm").style("max-width: 140px")

        ui.button(icon="tune", on_click=right_drawer.toggle).props("flat round dense color=white").classes("lt-md") \
            .tooltip("打开/关闭工具箱")

    # ---- 主内容区 ----
    with ui.column().classes("w-full flex-1 p-4 overflow-auto mc-content"):
        _render_main_content(deployer, session_tabs, session_active_tab, session_tab_history)


def _go_home(session_active_tab: dict):
    """返回主页"""
    session_active_tab["name"] = "overview"
    _persist_tabs_state(storage.get_active_profile(), None, session_active_tab, None)


def _go_back(session_active_tab: dict, session_tab_history: list, session_tabs: list | None = None):
    """返回上一页面，无历史时返回概览"""
    if session_tab_history:
        while session_tab_history:
            prev = session_tab_history.pop()
            if prev == "overview":
                session_active_tab["name"] = "overview"
                break
            if session_tabs is None:
                session_active_tab["name"] = prev
                break
            if any(t.get("name") == prev for t in session_tabs):
                session_active_tab["name"] = prev
                break
        else:
            session_active_tab["name"] = "overview"
    else:
        session_active_tab["name"] = "overview"
    _persist_tabs_state(storage.get_active_profile(), None, session_active_tab, session_tab_history)


def _render_sidebar(session_tabs: list, session_active_tab: dict, session_tab_history: list):
    """左侧导航：紧凑可搜索文件列表"""
    ui.label("文件列表").classes("mc-section-title q-mb-xs")
    search_input = ui.input(placeholder="搜索文件...").props("dense outlined clearable").classes("w-full q-mb-xs mc-sidebar-search")
    container = ui.column().classes("w-full mc-sidebar-files")
    last_sig = {"value": None}

    search_state = {"keyword": ""}

    def _compute_sidebar_sig():
        """轻量级侧边栏签名：用 config_mapping 文件的 mtime 代替逐个文件 stat

        性能优化：避免每次轮询都 stat 所有文件，改用 config_mapping.json 的 mtime
        作为主要变化信号，辅以文件数量。
        """
        import os
        files = storage.list_config_files()
        parts = [("admin", is_admin()), ("count", len(files)), ("search", search_state["keyword"])]
        # 用 mapping 文件的 mtime 作为轻量信号
        try:
            mapping_mtime = os.path.getmtime(storage.MAPPING_FILE)
            parts.append(("mapping", mapping_mtime))
        except OSError:
            parts.append(("mapping", 0))
        # 抽样检查前 5 个文件的 mtime（而非全部）
        for fname in files[:5]:
            path = storage.get_config_path(fname)
            if os.path.exists(path):
                stat = os.stat(path)
                parts.append((fname, stat.st_mtime_ns, stat.st_size))
            else:
                parts.append((fname, 0, 0))
        return tuple(parts)

    def refresh_sidebar():
        if _busy_processing["value"]:
            return
        # 从搜索框读取当前值
        raw = (search_input.value or "").strip().lower()
        if raw != search_state["keyword"]:
            search_state["keyword"] = raw
            last_sig["value"] = None  # force refresh on search change
        sig = _compute_sidebar_sig()
        if sig == last_sig["value"]:
            return
        last_sig["value"] = sig
        container.clear()
        with container:
            _render_sidebar_files(session_tabs, session_active_tab, session_tab_history, search_state["keyword"])

    refresh_sidebar()
    sidebar_timer = ui.timer(1.0, refresh_sidebar)
    # 性能优化：保存 timer 引用，以便在页面隐藏时停止
    return sidebar_timer


def _render_sidebar_files(session_tabs: list, session_active_tab: dict, session_tab_history: list, search_keyword: str = "") -> None:
    """紧凑文件列表，非卡片式"""
    mapping = storage.load_config_mapping()
    mapping_names = {m["name"] for m in mapping}
    admin = is_admin()
    files = storage.list_config_files()

    # 搜索过滤
    if search_keyword:
        files = [f for f in files if search_keyword in f.lower()]

    if not files:
        if search_keyword:
            ui.label("无匹配文件").classes("text-caption text-grey")
        else:
            ui.label("暂无文件").classes("text-caption text-grey")
        return

    for fname in files:
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
        icon_map = {"json": "data_object", "xml": "code"}
        icon = icon_map.get(ext, "description")
        color_map = {"json": "blue", "xml": "orange"}
        color = color_map.get(ext, "grey")
        download_handler = make_download_handler(DOWNLOAD_KIND_CURRENT, fname)

        is_active = any(
            t.get("name") == f"file:{fname}" and t.get("name") == session_active_tab.get("name")
            for t in session_tabs
        )
        row_bg = " mc-file-item-active" if is_active else ""

        with ui.element("div").classes(f"mc-file-item{row_bg}").on(
            "click", lambda f=fname: _open_file_tab(f, session_tabs, session_active_tab, session_tab_history)
        ):
            with ui.row().classes("items-center w-full no-wrap"):
                ui.icon(icon, color=color, size="sm").classes("q-mr-xs mc-file-icon")
                with ui.column().classes("q-ma-none q-pa-none col min-w-0 mc-nav-text"):
                    file_label = ui.label(fname).classes("mc-nav-filename text-body2 text-weight-medium q-mb-none")
                    file_label.tooltip(fname)
                    if fname in mapping_names:
                        update_date = storage.get_file_update_date(fname)
                        if update_date:
                            ui.label(update_date).classes("text-caption text-grey q-mt-none")
                with ui.element("div").classes("mc-file-actions"):
                    download_btn = ui.button(icon="download")
                    download_btn.props("flat round dense color=primary")
                    download_btn.classes("mc-download-btn mc-download-btn--icon")
                    download_btn.tooltip("下载当前文件")
                    download_btn.on("click.stop", download_handler)
                    if admin:
                        delete_btn = ui.button(icon="delete")
                        delete_btn.props("flat round dense color=negative")
                        delete_btn.classes("mc-nav-delete-btn mc-download-btn--icon")
                        delete_btn.tooltip("删除文件")
                        delete_btn.on(
                            "click.stop",
                            lambda e=None, name=fname: _show_delete_file_dialog(name),
                        )


def _render_tools_sidebar(session_tabs: list = None, session_active_tab: dict = None):
    """右侧工具栏：展示已配置的工具 + DL快捷计算入口"""
    ui.label("工具箱").classes("mc-section-title q-mb-xs")

    # ---- DL 快捷计算 ----
    with ui.card().classes("w-full q-mb-xs q-pa-sm mc-nav-card") \
            .on("click", lambda: _switch_to_tab("dltool", "DL快捷计算", session_tabs, session_active_tab, None)):
        with ui.row().classes("items-center"):
            ui.icon("functions", size="sm", color="blue-8").classes("q-mr-sm")
            with ui.column().classes("q-pa-none"):
                ui.label("DL 快捷计算").classes("text-subtitle2 font-bold q-mb-none")
                ui.label("八阶函数计算器").classes("text-caption text-grey q-mt-none")

    tools = storage.load_tools()
    if tools:
        for tool in tools:
            tool_url = tool.get("url", "")
            # 验证 URL scheme，防止 javascript: 等危险协议
            from app.utils.helpers import safe_external_url
            if not safe_external_url(tool_url):
                continue
            with ui.card().classes("w-full q-mb-xs q-pa-sm mc-nav-card").on(
                "click", lambda url=tool_url: ui.run_javascript(f'window.open({json.dumps(url)}, "_blank")')
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
            _persist_tabs_state(storage.get_active_profile(), session_tabs, session_active_tab, session_tab_history)
            return
    if session_tab_history is not None:
        session_tab_history.append(session_active_tab["name"])
    tab_manager.ensure_opened_at(session_tabs)
    session_tabs.append({
        "name": f"file:{filename}",
        "label": filename,
        "type": "file",
        "filename": filename,
        "opened_at": (max([t.get("opened_at", 0) for t in session_tabs], default=-1) + 1),
    })
    session_active_tab["name"] = f"file:{filename}"
    removed = tab_manager.enforce_tab_limit(session_tabs, session_active_tab["name"], _TAB_LIMIT)
    if removed and session_tab_history is not None:
        session_tab_history[:] = tab_manager.prune_history(session_tab_history, removed)
    _persist_tabs_state(storage.get_active_profile(), session_tabs, session_active_tab, session_tab_history)


def _switch_to_tab(tab_name: str, tab_label: str, session_tabs: list, session_active_tab: dict, session_tab_history: list = None):
    for tab in session_tabs:
        if tab["name"] == tab_name:
            if session_tab_history is not None:
                session_tab_history.append(session_active_tab["name"])
            session_active_tab["name"] = tab_name
            _persist_tabs_state(storage.get_active_profile(), session_tabs, session_active_tab, session_tab_history)
            return
    if session_tab_history is not None:
        session_tab_history.append(session_active_tab["name"])
    tab_manager.ensure_opened_at(session_tabs)
    session_tabs.append({
        "name": tab_name,
        "label": tab_label,
        "type": tab_name,
        "opened_at": (max([t.get("opened_at", 0) for t in session_tabs], default=-1) + 1),
    })
    session_active_tab["name"] = tab_name
    removed = tab_manager.enforce_tab_limit(session_tabs, session_active_tab["name"], _TAB_LIMIT)
    if removed and session_tab_history is not None:
        session_tab_history[:] = tab_manager.prune_history(session_tab_history, removed)
    _persist_tabs_state(storage.get_active_profile(), session_tabs, session_active_tab, session_tab_history)


def _render_main_content(deployer: bool, session_tabs: list, session_active_tab: dict, session_tab_history: list):
    tabbar_container = ui.column().classes("w-full")
    overview_container = ui.column().classes("w-full")
    with overview_container:
        _render_overview_panel(deployer, session_tabs, session_active_tab)

    tabs_container = ui.column().classes("w-full hidden")

    def refresh_tabs():
        current = session_active_tab.get("_rendered", None)
        active = session_active_tab["name"]
        if current != active:
            session_active_tab["_rendered"] = active
            _persist_tabs_state(storage.get_active_profile(), session_tabs, session_active_tab, session_tab_history)
            _update_tabs_display(session_tabs, session_active_tab, tabs_container, overview_container, deployer, session_tab_history, tabbar_container)

    tabs_timer = ui.timer(0.3, refresh_tabs)
    refresh_tabs()

    last_overview_sig = {"sig": None}

    def _compute_overview_sig():
        import os
        sig_parts = []
        try:
            sig_parts.append(("fav", os.path.getmtime(storage.get_favorites_file())))
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
        # 性能优化：概览页不可见时跳过刷新
        if session_active_tab["name"] != "overview":
            return
        sig = _compute_overview_sig()
        if last_overview_sig["sig"] != sig:
            last_overview_sig["sig"] = sig
            overview_container.clear()
            with overview_container:
                _render_overview_panel(deployer, session_tabs, session_active_tab)

    overview_timer = ui.timer(1.0, refresh_overview)
    # 返回 timer 引用，以便在页面隐藏时停止
    return overview_timer


def _render_loading_block(text: str = "加载中..."):
    ui.html(
        f'<div class="mc-loading"><div class="mc-spinner"></div><div class="mc-loading-text">{html.escape(text)}</div></div>',
    )


def _render_file_viewer_with_loading(active_tab: dict, deployer: bool, session_tabs: list, session_active_tab: dict):
    slot = ui.column().classes("w-full")
    state = {"done": False}
    load_key = str(time.monotonic_ns())
    active_tab["_load_key"] = load_key

    def _can_update() -> bool:
        return (
            active_tab.get("_load_key") == load_key
            and session_active_tab.get("name") == active_tab.get("name")
        )

    def show_loading():
        if state["done"] or not _can_update():
            return
        slot.clear()
        with slot:
            _render_loading_block("加载中...")

    ui.timer(0.5, show_loading, once=True)

    async def do_load():
        tree, err = await asyncio.to_thread(load_tree_for_viewer, active_tab["filename"])
        if not _can_update():
            return
        slot.clear()
        with slot:
            if err:
                ui.label(err).classes("text-negative")
            else:
                render_file_viewer(
                    active_tab["filename"],
                    deployer,
                    session_tabs,
                    session_active_tab,
                    search_keyword=active_tab.get("search_keyword"),
                    preloaded_tree=tree,
                )
        state["done"] = True

    asyncio.create_task(do_load())


def _load_archive_tree(filename: str, archive_filename: str) -> tuple[dict | None, str | None]:
    from app.core import parser, parse_cache

    source_path = storage.get_archived_path(filename, archive_filename)
    tree = parse_cache.load_tree(source_path)
    if tree is not None:
        return tree, None

    if not os.path.exists(source_path):
        return None, f"归档文件 {archive_filename} 不存在"
    try:
        tree = parser.parse_path(source_path, archive_filename)
    except ValueError as e:
        return None, f"解析失败: {e}"
    parse_cache.save_tree(source_path, tree)
    return tree, None


def _render_archive_viewer_with_loading(active_tab: dict, deployer: bool, session_active_tab: dict | None = None):
    slot = ui.column().classes("w-full")
    state = {"done": False}
    load_key = str(time.monotonic_ns())
    active_tab["_load_key"] = load_key

    def _can_update() -> bool:
        if active_tab.get("_load_key") != load_key or active_tab.get("type") != "archive_view":
            return False
        if session_active_tab is not None and session_active_tab.get("name") != active_tab.get("name"):
            return False
        return True

    def show_loading():
        if state["done"] or not _can_update():
            return
        slot.clear()
        with slot:
            _render_loading_block("加载中...")

    ui.timer(0.5, show_loading, once=True)

    async def do_load():
        tree, err = await asyncio.to_thread(
            _load_archive_tree,
            active_tab.get("filename", ""),
            active_tab.get("archive_filename", ""),
        )
        if not _can_update():
            return
        slot.clear()
        with slot:
            if err:
                ui.label(err).classes("text-negative")
            else:
                _render_archive_viewer(active_tab, deployer, preloaded_tree=tree)
        state["done"] = True

    asyncio.create_task(do_load())


def _render_overview_panel(deployer: bool, session_tabs: list, session_active_tab: dict):
    """配置速览面板 - 每个收藏项一个卡片，卡片内展示层级结构"""
    ui.label("配置速览").classes("mc-page-title q-mb-md")

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
                          on_click=lambda f=file_name, st=session_active_tab: _remove_all_favs_for_file(f, st)
                          ).props("flat round dense size=sm color=grey-5").tooltip("清空该文件所有收藏")

            with ui.column().classes("fav-card-grid w-full q-mt-sm"):
                for idx, item in enumerate(items):
                    card_accent_idx = list(file_colors.keys()).index(file_name) + idx
                    _render_fav_card(file_name, item, session_active_tab, card_accent_idx, _card_line_colors)

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
                            _render_fav_card(file_name, item, session_active_tab, card_accent_idx, _card_line_colors)


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


def _render_fav_card(file_name: str, item: dict, session_active_tab: dict, color_index: int = 0,
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
        safe_path = html.escape(str(item["path"]), quote=True)
        ui.html(f'<span data-fav-path="{safe_path}" style="display:none"></span>')

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
                      on_click=lambda p=item["path"], f=file_name, st=session_active_tab: _quick_remove_fav(p, f, st)
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
        ui.html(prefix_html)
        txt_color = ["dark", "grey-9", "grey-8", "grey-7", "grey-6"][min(depth, 4)]
        lbl_class = f"lbl-{min(depth, 3)}"
        if value is not None:
            ui.html(
                f'<span class="font-mono tree-label text-{txt_color} {lbl_class}">'
                f'{html.escape(str(label))} <span class="val-text">= {html.escape(str(value))}</span></span>',
                
            )
        else:
            ui.html(
                f'<span class="tree-label text-{txt_color} {lbl_class}">'
                f'{html.escape(str(label))}</span>',
                
            )

    # 子节点
    if has_children:
        children_wrap = ui.column().classes("children-wrap q-pa-none q-ma-none")
        with children_wrap:
            for child in children:
                _render_card_tree_node(child, depth + 1, line_colors)


def _quick_remove_fav(path: str, source_file: str, session_active_tab: dict):
    """快捷取消收藏（不刷新页面，保留折叠状态）"""
    storage.remove_favorite(path, source_file)
    ui.notify("已取消收藏", type="info")
    # 直接从 DOM 移除对应卡片
    safe_path_js = json.dumps(path)  # JSON 编码确保 JS 字符串安全
    ui.run_javascript(f"""
        var m = document.querySelector('[data-fav-path=' + {safe_path_js} + ']');
        if (m) {{ var c = m.closest('.q-card'); if (c) c.remove(); }}
    """)
    # 清除渲染标记，确保切页后数据一致
    if session_active_tab is not None:
        session_active_tab.pop("_rendered", None)


def _remove_all_favs_for_file(source_file: str, session_active_tab: dict):
    """清空某文件下的所有收藏"""
    favorites = storage.load_favorites()
    new_favs = [f for f in favorites if f["source_file"] != source_file]
    storage.save_favorites(new_favs)
    ui.notify(f"已清空 {source_file} 的所有收藏", type="info")
    # 移除对应文件分组下所有卡片
    for fav in [f for f in favorites if f["source_file"] == source_file]:
        safe_path_js = json.dumps(fav["path"])  # JSON 编码确保 JS 字符串安全
        ui.run_javascript(f"""
            var m = document.querySelector('[data-fav-path=' + {safe_path_js} + ']');
            if (m) {{ var c = m.closest('.q-card'); if (c) c.remove(); }}
        """)
    if session_active_tab is not None:
        session_active_tab.pop("_rendered", None)


def _save_fav_note(path: str, source_file: str, note: str):
    """保存收藏备注"""
    storage.update_favorite_note(path, source_file, note)


def _update_tabs_display(session_tabs, session_active_tab, tabs_container, overview_container, deployer, session_tab_history, tabbar_container):
    if tabbar_container is not None:
        tabbar_container.clear()
        with tabbar_container:
            _render_tab_bar(session_tabs, session_active_tab, session_tab_history, overview_container, tabs_container)

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
        if active_tab["type"] in ("file", "archive_view"):
            with ui.row().classes("w-full items-center q-mb-md q-mt-sm"):
                ui.label(active_tab["label"]).classes("mc-page-title")
                ui.space()
                if active_tab["type"] == "file":
                    ui.button(
                        "下载文件",
                        icon="download",
                        on_click=make_download_handler(DOWNLOAD_KIND_CURRENT, active_tab["filename"]),
                    ).props("flat dense color=primary").classes("mc-download-btn")
                else:
                    ui.button(
                        "下载历史版本",
                        icon="download",
                        on_click=make_download_handler(
                            DOWNLOAD_KIND_ARCHIVE,
                            active_tab["filename"],
                            active_tab.get("archive_filename"),
                        ),
                    ).props("flat dense color=primary").classes("mc-download-btn")
                if active_tab["type"] == "file":
                    search_input = ui.input(placeholder="搜索当前文件...").props("dense outlined").classes("w-64")
                    ui.button(icon="search",
                              on_click=lambda: _do_local_search(search_input.value, active_tab, session_active_tab)
                              ).props("flat round dense")
                    if active_tab.get("search_keyword"):
                        ui.button(icon="close",
                                  on_click=lambda: _do_local_search("", active_tab, session_active_tab)
                                  ).props("flat round dense").tooltip("清除筛选")

        tab_type = active_tab["type"]
        if tab_type == "file":
            _render_file_viewer_with_loading(active_tab, deployer, session_tabs, session_active_tab)
        elif tab_type == "archive_view":
            _render_archive_viewer_with_loading(active_tab, deployer, session_active_tab)
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
        elif tab_type == "records":
            render_records_page(active_tab, deployer, session_tabs, session_active_tab)
        elif tab_type == "review":
            render_review_page(session_active_tab)
        elif tab_type == "dltool":
            render_dltool_page(
                on_refresh=lambda: session_active_tab.pop("_rendered", None)
            )
        elif tab_type == "search":
            render_search_page(active_tab)


def _show_delete_file_dialog(name: str) -> None:
    if not is_admin():
        ui.notify("权限不足", type="negative")
        return

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("删除文件").classes("text-h6")
        ui.label(f"将删除文件及其关联归档、记录与审阅备注: {name}").classes("text-body2 q-mb-sm")

        def do_delete():
            ok = storage.delete_config_file(name, remove_mapping=True)
            if not ok:
                ui.notify("未找到可删除的文件", type="warning")
                return
            ui.notify("文件已删除", type="positive")
            dialog.close()
            # 局部刷新：sidebar 由 timer 自动检测变化

        with ui.row().classes("w-full justify-end q-gutter-sm"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("删除", icon="delete", on_click=do_delete).props("color=negative")

        dialog.open()


def _render_archive_viewer(tab: dict, deployer: bool, preloaded_tree: dict | None = None):
    """渲染归档文件查看器"""
    filename = tab.get("filename", "")
    archive_filename = tab.get("archive_filename", "")
    tree = preloaded_tree
    if tree is None:
        from app.core import parser, parse_cache

        source_path = storage.get_archived_path(filename, archive_filename)
        tree = parse_cache.load_tree(source_path)
        if tree is None:
            if not os.path.exists(source_path):
                ui.label(f"归档文件 {archive_filename} 不存在").classes("text-negative")
                return
            try:
                tree = parser.parse_path(source_path, archive_filename)
            except ValueError as e:
                ui.label(f"解析失败: {e}").classes("text-negative")
                return
            parse_cache.save_tree(source_path, tree)

    with ui.row().classes("items-center q-mb-md"):
        ui.badge(archive_filename, color="grey")
        ui.label("(历史版本)").classes("text-caption text-grey")

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
    name = tab.get("name") if isinstance(tab, dict) else str(tab)
    if not name:
        return
    current_active = session_active_tab.get("name", "overview")
    session_tabs[:], new_active = tab_manager.close_tab_adjacent(session_tabs, current_active, name)
    if session_tab_history is not None:
        session_tab_history[:] = tab_manager.prune_history(session_tab_history, [name])
    session_active_tab["name"] = new_active
    session_active_tab.pop("_rendered", None)
    _persist_tabs_state(storage.get_active_profile(), session_tabs, session_active_tab, session_tab_history)
    _update_tabs_display(session_tabs, session_active_tab, tabs_container, overview_container, is_deployer(), session_tab_history, None)


def _tabs_storage_key(profile_id: str) -> str:
    return tabs_state.tabs_storage_key(profile_id)


def _persist_tabs_state(profile_id: str, session_tabs: list | None, session_active_tab: dict, session_tab_history: list | None):
    key = _tabs_storage_key(profile_id)
    if session_tabs is None:
        state = tabs_state.load_state(profile_id) or {}
        tabs = state.get("tabs", [])
    else:
        tabs = session_tabs
    active = session_active_tab.get("name", "overview")
    history = list(session_tab_history or (tabs_state.load_state(profile_id) or {}).get("history", []))
    try:
        tabs_state.save_state(profile_id, list(tabs), active, history)
    except Exception:
        app.storage.user[key] = {"tabs": list(tabs), "active": active, "history": history[-100:]}


def _restore_tabs_state(profile_id: str, session_tabs: list, session_active_tab: dict, session_tab_history: list):
    import os

    state = tabs_state.load_state(profile_id)
    if not isinstance(state, dict):
        return
    tabs = state.get("tabs", [])
    if not isinstance(tabs, list):
        return

    restored = []
    for t in tabs:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        tab_type = t.get("type")
        if tab_type == "file":
            fn = t.get("filename")
            if not fn:
                continue
            if not os.path.exists(storage.get_config_path(fn)):
                continue
        if tab_type == "archive_view":
            fn = t.get("filename")
            afn = t.get("archive_filename")
            if not fn or not afn:
                continue
            if not os.path.exists(storage.get_archived_path(fn, afn)):
                continue
        restored.append(t)

    tab_manager.ensure_opened_at(restored)
    session_tabs.extend(restored)

    active = state.get("active", "overview")
    if isinstance(active, str) and any(t.get("name") == active for t in session_tabs):
        session_active_tab["name"] = active
    else:
        session_active_tab["name"] = "overview"

    history = state.get("history", [])
    if isinstance(history, list):
        session_tab_history.extend([h for h in history if isinstance(h, str)])
        existing = {t.get("name") for t in session_tabs if t.get("name")}
        session_tab_history[:] = [h for h in session_tab_history if h == "overview" or h in existing]

    removed = tab_manager.enforce_tab_limit(session_tabs, session_active_tab["name"], _TAB_LIMIT)
    if removed:
        session_tab_history[:] = tab_manager.prune_history(session_tab_history, removed)


def _render_tab_bar(session_tabs: list, session_active_tab: dict, session_tab_history: list, overview_container, tabs_container):
    active = session_active_tab.get("name", "overview")
    with ui.element("div").classes("mc-tabbar"):
        with ui.row().classes("items-center no-wrap mc-tabbar-inner"):
            ui.button(icon="arrow_back",
                      on_click=lambda: _go_back(session_active_tab, session_tab_history, session_tabs)
                      ).props("flat round dense").tooltip("返回上一页")
            with ui.element("div").classes("mc-tabbar-tabs"):
                is_overview = active == "overview"
                overview_el = ui.element("div").classes("mc-tab" + (" mc-tab-active" if is_overview else ""))
                overview_el.on("click", lambda e=None: _activate_tab("overview", session_tabs, session_active_tab, session_tab_history))
                with overview_el:
                    ui.label("主页").classes("mc-tab-label").tooltip("主页")

                for t in session_tabs:
                    name = t.get("name")
                    label = t.get("label") or name
                    is_active = name == active
                    tab_el = ui.element("div").classes("mc-tab" + (" mc-tab-active" if is_active else ""))
                    tab_el.on("click", lambda e=None, n=name: _activate_tab(n, session_tabs, session_active_tab, session_tab_history))
                    with tab_el:
                        ui.label(label).classes("mc-tab-label").tooltip(label)
                        ui.button(icon="close").props("flat round dense size=xs").classes("mc-tab-close") \
                            .on("click.stop", lambda e=None, n=name: _close_tab_by_name(n, session_tabs, session_active_tab, overview_container, tabs_container, session_tab_history)) \
                            .tooltip("关闭")


def _activate_tab(name: str, session_tabs: list, session_active_tab: dict, session_tab_history: list):
    if name == session_active_tab.get("name"):
        return
    if session_tab_history is not None:
        session_tab_history.append(session_active_tab.get("name", "overview"))
    session_active_tab["name"] = name
    _persist_tabs_state(storage.get_active_profile(), session_tabs, session_active_tab, session_tab_history)


def _close_tab_by_name(name: str, session_tabs: list, session_active_tab: dict, overview_container, tabs_container, session_tab_history: list):
    tab = next((t for t in session_tabs if t.get("name") == name), None)
    if not tab:
        return
    _close_tab(tab, session_tabs, session_active_tab, overview_container, tabs_container, session_tab_history)


def _do_local_search(keyword: str, active_tab: dict, session_active_tab: dict):
    kw = (keyword or "").strip()
    if not kw:
        active_tab.pop("search_keyword", None)
        session_active_tab.pop("_rendered", None)
        ui.notify("已清除搜索筛选", type="info")
        return
    active_tab["search_keyword"] = kw
    session_active_tab.pop("_rendered", None)
    ui.notify("已按关键词筛选当前文件", type="info")


def _switch_device_model(model_id: str) -> None:
    mid = device_models.normalize_selected(model_id)
    app.storage.user["device_model"] = mid
    storage.set_active_profile(mid)
    ui.notify(f"已切换机型: {device_models.get_model_name(mid)}", type="info")
    ui.run_javascript("location.reload()")


def _show_add_model_dialog(current_model: str) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("新增机型").classes("text-h6")
        name_input = ui.input(placeholder="机型名称 (例: iPhone 15 Pro)").props("dense outlined").classes("w-full")
        copy_switch = ui.switch("从当前机型复制配置", value=False)
        ui.label("提示：不勾选将创建空白机型，确保与其他机型数据完全隔离").classes("text-caption text-grey q-mt-xs")

        def do_add():
            name = (name_input.value or "").strip()
            if not name:
                ui.notify("请输入机型名称", type="warning")
                return
            try:
                mid = device_models.add_model(name, copy_from=current_model if copy_switch.value else None)
            except Exception:
                ui.notify("新增失败", type="negative")
                return
            try:
                from app.core import scheduler as sched
                sched.setup_scheduled_update()
            except Exception:
                pass
            app.storage.user["device_model"] = mid
            storage.set_active_profile(mid)
            ui.notify(f"已新增机型: {device_models.get_model_name(mid)}", type="positive")
            dialog.close()
            ui.run_javascript("location.reload()")

        with ui.row().classes("w-full justify-end"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("创建", icon="add", on_click=do_add).props("color=primary")

        dialog.open()


def _show_delete_model_dialog(model_id: str) -> None:
    mid = device_models.normalize_selected(model_id)
    if mid == device_models.DEFAULT_MODEL_ID:
        ui.notify("默认机型无法删除", type="warning")
        return

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("删除机型").classes("text-h6")
        ui.label(f"将从机型列表移除: {device_models.get_model_name(mid)}").classes("text-body2 q-mb-sm")
        delete_data = ui.switch("同时删除该机型的全部配置数据", value=False)

        def do_delete():
            ok = device_models.remove_model(mid, delete_data=delete_data.value)
            if not ok:
                ui.notify("删除失败", type="negative")
                return
            try:
                from app.core import scheduler as sched
                sched.setup_scheduled_update()
            except Exception:
                pass
            app.storage.user["device_model"] = device_models.DEFAULT_MODEL_ID
            storage.set_active_profile(device_models.DEFAULT_MODEL_ID)
            ui.notify("机型已删除", type="positive")
            dialog.close()
            ui.run_javascript("location.reload()")

        with ui.row().classes("w-full justify-end"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("删除", icon="delete", on_click=do_delete).props("color=negative")

        dialog.open()


def _show_rename_model_dialog(model_id: str) -> None:
    mid = device_models.normalize_selected(model_id)
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("机型更名").classes("text-h6")
        name_input = ui.input(
            value=device_models.get_model_name(mid),
            placeholder="新的机型名称",
        ).props("dense outlined").classes("w-full")

        def do_rename():
            name = (name_input.value or "").strip()
            if not name:
                ui.notify("请输入新的机型名称", type="warning")
                return
            ok = device_models.rename_model(mid, name)
            if not ok:
                ui.notify("更名失败", type="negative")
                return
            ui.notify("机型名称已更新", type="positive")
            dialog.close()
            ui.run_javascript("location.reload()")

        with ui.row().classes("w-full justify-end"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("保存", icon="save", on_click=do_rename).props("color=primary")

        dialog.open()


def _show_manage_models_dialog(current_model: str) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-[560px]"):
        ui.label("机型管理").classes("text-h6 q-mb-sm")
        ui.label(f"当前机型: {device_models.get_model_name(current_model)}").classes("text-caption text-grey q-mb-md")

        with ui.column().classes("w-full"):
            for m in device_models.load_models():
                mid = m.get("id")
                if not mid:
                    continue
                with ui.row().classes("w-full items-center no-wrap q-py-xs"):
                    ui.label(m.get("name") or mid).classes("text-body2")
                    ui.label(mid).classes("text-caption text-grey q-ml-sm")
                    ui.space()
                    ui.button("切换", on_click=lambda e=None, v=mid: _switch_device_model(v)).props("dense flat")
                    ui.button("更名", on_click=lambda e=None, v=mid: _show_rename_model_dialog(v)).props("dense flat")
                    if mid != device_models.DEFAULT_MODEL_ID:
                        ui.button("删除", on_click=lambda e=None, v=mid: _show_delete_model_dialog(v)).props("dense flat color=negative")

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("关闭", on_click=dialog.close).props("flat")

        dialog.open()


def _show_upload_dialog(session_tabs: list, session_active_tab: dict, session_tab_history: list):
    from app.core import parser

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("上传配置文件").classes("text-h6")
        ui.label("支持 XML 和 JSON 格式").classes("text-caption text-grey q-mb-md")

        uploaded = {"data": None, "name": None}

        async def handle_upload(e):
            uploaded["data"] = e.content.read()
            uploaded["name"] = e.name

        ui.upload(label="选择文件", auto_upload=True, on_upload=handle_upload).props("dense").classes("w-full")

        async def process_upload():
            if uploaded["data"] is None:
                ui.notify("请先上传文件", type="warning")
                return
            filename = uploaded["name"]
            data = uploaded["data"]

            dialog.close()
            ui.notify(f"正在解析: {filename}...", type="ongoing")

            # 标记繁忙，暂停侧边栏刷新
            _busy_processing["value"] = True
            try:
                # 解析放到线程池，避免阻塞事件循环
                try:
                    await asyncio.to_thread(parser.parse_file, data, filename)
                except ValueError as e:
                    _busy_processing["value"] = False
                    ui.notify(f"解析失败: {e}", type="negative")
                    return

                # 保存也放到线程池
                if is_deployer():
                    await asyncio.to_thread(storage.save_config_file, filename, data)
                    ui.notify(f"已解析并保存: {filename}", type="positive")
                else:
                    ui.notify(f"已解析: {filename} (仅会话可见)", type="info")

                _open_file_tab(filename, session_tabs, session_active_tab, session_tab_history)
            finally:
                _busy_processing["value"] = False

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

            dialog.close()
            ui.notify(f"正在下载: {filename}...", type="ongoing")

            _busy_processing["value"] = True
            try:
                content = await asyncio.to_thread(downloader.download_file, url)
                if content is None:
                    _busy_processing["value"] = False
                    ui.notify("下载失败", type="negative")
                    return

                try:
                    await asyncio.to_thread(parser.parse_file, content, filename)
                except ValueError as e:
                    _busy_processing["value"] = False
                    ui.notify(f"解析失败: {e}", type="negative")
                    return

                if is_deployer():
                    await asyncio.to_thread(storage.save_config_file, filename, content)
                    ui.notify(f"已下载并保存: {filename}", type="positive")
                else:
                    ui.notify(f"已下载并解析: {filename} (仅会话可见)", type="info")

                _open_file_tab(filename, session_tabs, session_active_tab, session_tab_history)
            finally:
                _busy_processing["value"] = False

        with ui.row().classes("w-full justify-end"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("下载并解析", on_click=process_download).props("color=primary")

        dialog.open()


def _show_search_dialog(session_tabs: list, session_active_tab: dict, session_tab_history: list):

    with ui.dialog() as dialog, ui.card().classes("w-[600px]"):
        ui.label("全局搜索").classes("text-h6")
        search_input = ui.input(placeholder="输入搜索关键词...").props("dense outlined").classes("w-full")

        def do_search():
            keyword = (search_input.value or "").strip()
            if not keyword:
                ui.notify("请输入搜索关键词", type="warning")
                return
            _open_search_tab(keyword, session_tabs, session_active_tab, session_tab_history)
            dialog.close()

        search_input.on("keydown.enter", do_search)
        ui.button("搜索", icon="search", on_click=do_search).props("color=primary")
        with ui.row().classes("w-full justify-end"):
            ui.button("关闭", on_click=dialog.close).props("flat")

        dialog.open()


def _open_search_tab(keyword: str, session_tabs: list, session_active_tab: dict, session_tab_history: list):
    tab_manager.ensure_opened_at(session_tabs)
    opened_at = (max([t.get("opened_at", 0) for t in session_tabs], default=-1) + 1)
    name = f"search:{opened_at}"
    session_tabs.append({
        "name": name,
        "label": f"搜索: {keyword}",
        "type": "search",
        "query": keyword,
        "opened_at": opened_at,
    })
    if session_tab_history is not None:
        session_tab_history.append(session_active_tab.get("name", "overview"))
    session_active_tab["name"] = name
    removed = tab_manager.enforce_tab_limit(session_tabs, session_active_tab["name"], _TAB_LIMIT)
    if removed and session_tab_history is not None:
        session_tab_history[:] = tab_manager.prune_history(session_tab_history, removed)
    _persist_tabs_state(storage.get_active_profile(), session_tabs, session_active_tab, session_tab_history)
