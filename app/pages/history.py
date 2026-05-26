"""版本历史页面"""

from nicegui import ui

from app.core import storage, parser


def render_history_page(tab: dict, deployer: bool, session_tabs: list, session_active_tab: dict):
    """渲染版本历史页面"""
    filename = tab.get("filename", "")
    versions = storage.list_archived_versions(filename)
    update_date = storage.get_file_update_date(filename)

    ui.label(f"版本历史 - {filename}").classes("text-h6 q-mb-md")

    with ui.timeline().classes("w-full"):
        # 当前版本
        with ui.timeline_entry(
            title="当前版本",
            subtitle=update_date or "未知",
            color="green",
        ):
            content = storage.load_config_file(filename)
            if content:
                try:
                    tree = parser.parse_file(content, filename)
                    flat = parser.flatten_tree(tree)
                    ui.label(f"共 {len(flat)} 个节点").classes("text-caption")
                except ValueError:
                    pass

        # 历史版本
        for v in versions:
            with ui.timeline_entry(
                title=v["filename"],
                subtitle=v["date"],
                color="grey",
            ):
                ui.label(f"大小: {v['size']} bytes").classes("text-caption")
                with ui.row().classes("q-mt-sm"):
                    ui.button(
                        "查看内容",
                        icon="visibility",
                        on_click=lambda fn=filename, af=v["filename"]: _view_archived(fn, af, session_tabs, session_active_tab),
                    ).props("flat dense")
                    ui.button(
                        "与当前对比",
                        icon="compare",
                        on_click=lambda fn=filename, af=v["filename"]: _compare_with_current(fn, af, session_tabs, session_active_tab),
                    ).props("flat dense")


def _view_archived(filename: str, archive_filename: str, session_tabs: list, session_active_tab: dict):
    """查看归档版本"""
    content = storage.load_archived_file(filename, archive_filename)
    if content is None:
        ui.notify("归档文件不存在", type="negative")
        return

    tab_name = f"file:archive:{filename}:{archive_filename}"
    for tab in session_tabs:
        if tab["name"] == tab_name:
            session_active_tab["name"] = tab_name
            return

    session_tabs.append({
        "name": tab_name,
        "label": f"历史: {archive_filename}",
        "type": "archive_view",
        "filename": filename,
        "archive_filename": archive_filename,
    })
    session_active_tab["name"] = tab_name


def _compare_with_current(filename: str, archive_filename: str, session_tabs: list, session_active_tab: dict):
    """与当前版本对比"""
    tab_name = f"comparison:{filename}"
    for tab in session_tabs:
        if tab["name"] == tab_name:
            session_active_tab["name"] = tab_name
            return
    session_tabs.append({
        "name": tab_name,
        "label": f"对比: {filename}",
        "type": "comparison",
        "filename": filename,
    })
    session_active_tab["name"] = tab_name
