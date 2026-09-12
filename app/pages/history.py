"""版本历史页面"""

import os

from nicegui import ui

from app.core import storage, parser
from app.core.models import FileRef, FileKind
from app.pages.file_downloads import (
    DOWNLOAD_KIND_ARCHIVE,
    DOWNLOAD_KIND_CURRENT,
    make_download_handler,
)


def render_history_page(tab: dict, deployer: bool, session_tabs: list, session_active_tab: dict):
    """渲染版本历史页面"""
    filename = tab.get("filename", "")
    storage.ensure_archive_diffs(filename)
    versions = storage.list_archived_versions(filename)
    update_date = storage.get_file_update_date(filename)

    with ui.column().classes("w-full"):
        with ui.row().classes("items-start justify-between w-full q-mb-md"):
            with ui.column().classes("q-gutter-xs"):
                ui.label("版本历史").classes("mc-page-title")
                ui.label(filename).classes("mc-page-subtitle mc-mono")
            ui.space()

        current_diff = versions[0].get("diff") if versions else None
        source_path = storage.get_config_path(filename)
        node_count = None
        if os.path.exists(source_path):
            try:
                tree = parser.parse_path(source_path, filename)
                node_count = len(parser.flatten_tree(tree))
            except ValueError:
                node_count = None

        with ui.card().classes("w-full q-pa-md mc-history-card q-mb-md"):
            with ui.row().classes("items-center w-full mc-history-row"):
                with ui.column().classes("q-gutter-xs"):
                    ui.label("当前版本").classes("mc-section-title")
                    ui.label(update_date or "未知").classes("mc-page-subtitle")
                ui.space()
                with ui.row().classes("items-center q-gutter-xs"):
                    ui.button(
                        "下载当前版本",
                        icon="download",
                        on_click=make_download_handler(DOWNLOAD_KIND_CURRENT, filename),
                    ).props("flat dense color=primary").classes("mc-download-btn")
                    if versions:
                        ui.button(
                            "与上一版本对比",
                            icon="compare",
                            on_click=lambda fn=filename, af=versions[0]["filename"]: _compare_with_current(fn, af, session_tabs, session_active_tab),
                        ).props("flat dense color=primary")
            with ui.row().classes("items-center q-gutter-sm q-mt-sm"):
                if node_count is not None:
                    ui.html(f'<span class="mc-chip">节点 {node_count}</span>')
                ui.html(_render_diff_chips(current_diff))

        ui.label("历史版本").classes("mc-section-title q-mb-sm")
        if not versions:
            ui.label("暂无历史版本").classes("mc-page-subtitle")
            return

        for v in versions:
            with ui.card().classes("w-full q-pa-md q-mb-sm mc-history-card"):
                with ui.row().classes("items-start w-full mc-history-row"):
                    with ui.column().classes("q-gutter-xs"):
                        ui.label(v["filename"]).classes("text-body1 text-weight-medium mc-mono")
                        ui.label(v["date"]).classes("mc-page-subtitle")
                        ui.label(f"大小: {v['size']} bytes").classes("mc-page-subtitle")
                    ui.space()
                    with ui.column().classes("items-end q-gutter-xs"):
                        ui.html(_render_diff_chips(v.get("diff")))
                        with ui.row().classes("items-center q-gutter-xs"):
                            ui.button(
                                "下载版本",
                                icon="download",
                                on_click=make_download_handler(DOWNLOAD_KIND_ARCHIVE, filename, v["filename"]),
                            ).props("flat dense color=primary").classes("mc-download-btn")
                            ui.button(
                                "查看内容",
                                icon="visibility",
                                on_click=lambda fn=filename, af=v["filename"]: _view_archived(fn, af, session_tabs, session_active_tab),
                            ).props("flat dense")
                            ui.button(
                                "与当前对比",
                                icon="compare",
                                on_click=lambda fn=filename, af=v["filename"]: _compare_with_current(fn, af, session_tabs, session_active_tab),
                            ).props("flat dense color=primary")


def _render_diff_chips(diff: dict) -> str:
    if not diff:
        return '<span class="mc-chip">未计算</span>'

    if not diff.get("has_changes"):
        return '<span class="mc-chip">无变更</span>'

    summary = diff.get("summary") or {}
    structural_type = diff.get("structural_type")

    added = int(summary.get("added_count", 0) or 0)
    removed = int(summary.get("removed_count", 0) or 0)
    modified = int(summary.get("modified_count", 0) or 0)
    bound = int(summary.get("bound_count", 0) or 0)

    parts = [
        f'<span class="mc-chip mc-chip-success">+{added}</span>',
        f'<span class="mc-chip mc-chip-danger">-{removed}</span>',
        f'<span class="mc-chip mc-chip-warning">~{modified}</span>',
    ]

    if bound > 0:
        parts.append(f'<span class="mc-chip">绑定 {bound}</span>')

    if structural_type != "structural":
        parts.append('<span class="mc-chip">仅文本</span>')

    return "".join(parts)


def _view_archived(filename: str, archive_filename: str, session_tabs: list, session_active_tab: dict):
    """查看归档版本（精确版本）

    Tab 持有 FileRef，确保后续操作（如 comparison）使用该版本。
    """
    content = storage.load_archived_file(filename, archive_filename)
    if content is None:
        ui.notify("归档文件不存在", type="negative")
        return

    # 构建 FileRef 用于精确版本跟踪
    file_ref = FileRef(
        profile_id="",  # 由上下文填充
        kind=FileKind.ARCHIVE,
        name=filename,
        version_or_token=archive_filename,
    )

    tab_name = f"file:archive:{filename}:{archive_filename}"
    for tab in session_tabs:
        if tab["name"] == tab_name:
            session_active_tab["name"] = tab_name
            # 更新 FileRef（确保 reload/reopen 后 exact version 保留）
            tab["file_ref"] = {
                "profile_id": file_ref.profile_id,
                "kind": file_ref.kind.value,
                "name": file_ref.name,
                "version_or_token": file_ref.version_or_token,
            }
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
        "label": f"历史: {archive_filename}",
        "type": "archive_view",
        "filename": filename,
        "archive_filename": archive_filename,
        "file_ref": {
            "profile_id": file_ref.profile_id,
            "kind": file_ref.kind.value,
            "name": file_ref.name,
            "version_or_token": file_ref.version_or_token,
        },
        "opened_at": (max([t.get("opened_at", 0) for t in session_tabs], default=-1) + 1),
    })
    session_active_tab["name"] = tab_name
    tab_manager.enforce_tab_limit(session_tabs, session_active_tab["name"], 15)
    tabs_state.save_current(session_tabs, session_active_tab["name"])


def _compare_with_current(filename: str, archive_filename: str, session_tabs: list, session_active_tab: dict):
    """与当前版本对比（持有 FileRef）

    Tab payload 持有 old_ref（历史版本 FileRef），
    确保 comparison 页面能精确选择对应版本。
    """
    # 构建历史版本 FileRef
    old_ref = {
        "profile_id": "",
        "kind": FileKind.ARCHIVE.value,
        "name": filename,
        "version_or_token": archive_filename,
    }

    tab_name = f"comparison:{filename}"
    for tab in session_tabs:
        if tab["name"] == tab_name:
            # 更新已有 tab 的 FileRef
            tab["old_ref"] = old_ref
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
        "old_ref": old_ref,
        "opened_at": (max([t.get("opened_at", 0) for t in session_tabs], default=-1) + 1),
    })
    session_active_tab["name"] = tab_name
    tab_manager.enforce_tab_limit(session_tabs, session_active_tab["name"], 15)
    tabs_state.save_current(session_tabs, session_active_tab["name"])
