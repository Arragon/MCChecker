"""版本对比页面"""

import json

from nicegui import ui

from app.core import storage
from app.core.differ import compare_versions, compare_with_uploaded
from app.core.tree_component import copy_to_clipboard, notify_copy_result

_DIFF_PREVIEW_LIMIT = 200


def render_comparison_page(tab: dict, deployer: bool):
    """渲染版本对比页面

    Tab payload 可持有 FileRef:
    - old_ref: 旧版本 FileRef (dict)
    - new_ref: 新版本 FileRef (dict)
    - upload_original_name: 上传文件原始名
    """
    filename = tab.get("filename", "")
    # 支持从历史版本直接打开对比（持有 FileRef）
    old_ref = tab.get("old_ref")
    new_ref = tab.get("new_ref")

    ui.label(f"版本对比 - {filename}").classes("mc-page-title q-mb-md")

    versions = storage.list_archived_versions(filename)
    version_options = [v["filename"] for v in versions]

    # 如果 tab 持有 FileRef，自动选择对应版本
    initial_mode = "current_vs_history"
    if old_ref and new_ref:
        initial_mode = "history_vs_history"
    elif old_ref:
        initial_mode = "current_vs_history"

    mode = {"value": initial_mode}

    diff_container = ui.column().classes("w-full")

    def setup_compare_ui():
        diff_container.clear()
        with diff_container:
            result_container = ui.column().classes("w-full q-mt-md")
            if mode["value"] == "current_vs_history":
                _render_current_vs_history(filename, version_options, result_container, old_ref)
            elif mode["value"] == "history_vs_history":
                _render_history_vs_history(filename, version_options, result_container, old_ref, new_ref)
            elif mode["value"] == "upload_vs_current":
                _render_upload_vs_current(filename, result_container)
            elif mode["value"] == "upload_vs_history":
                _render_upload_vs_history(filename, version_options, result_container)

    def on_mode_change(e):
        mode["value"] = e.value
        setup_compare_ui()

    with ui.card().classes("w-full q-pa-md q-mb-md"):
        ui.label("对比方式").classes("mc-section-title q-mb-sm")
        ui.select(
            {
                "current_vs_history": "当前版本 vs 历史版本",
                "history_vs_history": "历史版本 vs 历史版本",
                "upload_vs_current": "上传文件 vs 当前版本",
                "upload_vs_history": "上传文件 vs 历史版本",
            },
            value="current_vs_history",
            on_change=on_mode_change,
        ).props("dense outlined").classes("w-full md:w-96")

    setup_compare_ui()


def _render_current_vs_history(filename: str, version_options: list, result_container, old_ref=None):
    """当前版本 vs 历史版本"""
    if not version_options:
        with ui.card().classes("w-full q-pa-md"):
            ui.label("无历史版本可对比").classes("text-warning")
        return

    # 如果有 old_ref，使用其 version_or_token 作为初始选择
    initial_value = version_options[0]
    if old_ref and old_ref.get("version_or_token"):
        ref_name = old_ref.get("version_or_token")
        if ref_name in version_options:
            initial_value = ref_name

    selected = {"value": initial_value}
    with ui.card().classes("w-full q-pa-md"):
        ui.label("选择历史版本").classes("mc-section-title q-mb-sm")
        with ui.row().classes("w-full items-center gap-2"):
            ui.select(
                version_options,
                value=selected["value"],
                on_change=lambda e: selected.update(value=e.value),
            ).props("dense outlined").classes("w-full md:w-96")
            ui.button(
                "对比",
                icon="compare_arrows",
                on_click=lambda: _run_current_vs_history(filename, selected["value"], result_container),
            ).props("color=primary dense")


def _render_history_vs_history(filename: str, version_options: list, result_container, old_ref=None, new_ref=None):
    """历史版本 vs 历史版本"""
    if len(version_options) < 2:
        with ui.card().classes("w-full q-pa-md"):
            ui.label("需要至少2个历史版本").classes("text-warning")
        return

    # 如果有 FileRef，使用其版本作为初始选择
    initial_a = version_options[0]
    initial_b = version_options[1] if len(version_options) > 1 else ""
    if old_ref and old_ref.get("version_or_token"):
        ref_name = old_ref.get("version_or_token")
        if ref_name in version_options:
            initial_a = ref_name
    if new_ref and new_ref.get("version_or_token"):
        ref_name = new_ref.get("version_or_token")
        if ref_name in version_options:
            initial_b = ref_name

    selected_a = {"value": initial_a}
    selected_b = {"value": initial_b}

    with ui.card().classes("w-full q-pa-md"):
        ui.label("选择两个历史版本").classes("mc-section-title q-mb-sm")
        with ui.row().classes("w-full items-center gap-2"):
            ui.select(
                version_options,
                value=selected_a["value"],
                on_change=lambda e: selected_a.update(value=e.value),
            ).props("dense outlined").classes("w-full md:w-72")
            ui.label("vs").classes("mc-muted")
            ui.select(
                version_options,
                value=selected_b["value"],
                on_change=lambda e: selected_b.update(value=e.value),
            ).props("dense outlined").classes("w-full md:w-72")
            ui.button(
                "对比",
                icon="compare_arrows",
                on_click=lambda: _run_history_vs_history(
                    filename,
                    selected_a["value"],
                    selected_b["value"],
                    result_container,
                ),
            ).props("color=primary dense")


def _render_upload_vs_current(filename: str, result_container):
    """上传文件 vs 当前版本"""
    uploaded = {"data": None, "name": None}

    with ui.card().classes("w-full q-pa-md"):
        ui.label("上传文件并与当前版本对比").classes("mc-section-title q-mb-sm")
        status = ui.label("未选择文件").classes("mc-muted text-caption q-mb-sm")

        def update_status():
            status.text = uploaded["name"] if uploaded.get("name") else "未选择文件"

        def clear_upload():
            uploaded["data"] = None
            uploaded["name"] = None
            update_status()

        def open_upload():
            _open_upload_dialog(uploaded, on_uploaded=update_status)

        with ui.row().classes("w-full items-center gap-2"):
            ui.button("上传文件", icon="upload_file", on_click=open_upload).props("color=primary outline dense")
            ui.button("清除", icon="close", on_click=clear_upload).props("flat dense")
            ui.space()
            ui.button(
                "对比",
                icon="compare_arrows",
                on_click=lambda: _run_upload_vs_current(filename, uploaded, result_container),
            ).props("color=primary dense")


def _render_upload_vs_history(filename: str, version_options: list, result_container):
    """上传文件 vs 历史版本"""
    if not version_options:
        with ui.card().classes("w-full q-pa-md"):
            ui.label("无历史版本").classes("text-warning")
        return

    uploaded = {"data": None, "name": None}
    selected = {"value": version_options[0]}

    with ui.card().classes("w-full q-pa-md"):
        ui.label("上传文件并选择历史版本对比").classes("mc-section-title q-mb-sm")
        status = ui.label("未选择文件").classes("mc-muted text-caption q-mb-sm")

        def update_status():
            status.text = uploaded["name"] if uploaded.get("name") else "未选择文件"

        def clear_upload():
            uploaded["data"] = None
            uploaded["name"] = None
            update_status()

        def open_upload():
            _open_upload_dialog(uploaded, on_uploaded=update_status)

        with ui.row().classes("w-full items-center gap-2 q-mb-sm"):
            ui.button("上传文件", icon="upload_file", on_click=open_upload).props("color=primary outline dense")
            ui.button("清除", icon="close", on_click=clear_upload).props("flat dense")
            ui.space()

        with ui.row().classes("w-full items-center gap-2"):
            ui.select(
                version_options,
                value=selected["value"],
                on_change=lambda e: selected.update(value=e.value),
            ).props("dense outlined").classes("w-full md:w-96")
            ui.button(
                "对比",
                icon="compare_arrows",
                on_click=lambda: _run_upload_vs_history(filename, selected["value"], uploaded, result_container),
            ).props("color=primary dense")


# ===================== 对比执行函数 =====================


def _run_current_vs_history(filename: str, archive_filename: str, container):
    container.clear()
    current_content = storage.load_config_file(filename)
    archive_content = storage.load_archived_file(filename, archive_filename)
    if current_content is None or archive_content is None:
        with container:
            ui.label("文件内容加载失败").classes("text-negative")
        return
    result = compare_versions(archive_content, current_content, archive_filename, filename)
    _render_diff_result(result, container)


def _run_history_vs_history(filename: str, archive_a: str, archive_b: str, container):
    container.clear()
    content_a = storage.load_archived_file(filename, archive_a)
    content_b = storage.load_archived_file(filename, archive_b)
    if content_a is None or content_b is None:
        with container:
            ui.label("文件内容加载失败").classes("text-negative")
        return
    result = compare_versions(content_a, content_b, archive_a, archive_b)
    _render_diff_result(result, container)


def _run_upload_vs_current(filename: str, uploaded: dict, container):
    container.clear()
    if uploaded.get("data") is None:
        ui.notify("请先上传文件", type="warning")
        return
    current_content = storage.load_config_file(filename)
    if current_content is None:
        ui.notify("服务端文件不存在", type="negative")
        return
    result = compare_with_uploaded(uploaded["data"], f"上传_{filename}", current_content, filename)
    _render_diff_result(result, container)


def _run_upload_vs_history(filename: str, archive_filename: str, uploaded: dict, container):
    container.clear()
    if uploaded.get("data") is None:
        ui.notify("请先上传文件", type="warning")
        return
    archive_content = storage.load_archived_file(filename, archive_filename)
    if archive_content is None:
        ui.notify("历史版本不存在", type="negative")
        return
    result = compare_with_uploaded(uploaded["data"], f"上传_{filename}", archive_content, archive_filename)
    _render_diff_result(result, container)


# ===================== 对比结果渲染 =====================


def _render_diff_result(result: dict, container):
    """渲染对比结果"""
    with container:
        if not result["has_changes"]:
            with ui.card().classes("w-full q-pa-md"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("check_circle", color="positive")
                    ui.label("两个版本内容相同，无差异").classes("text-positive text-h6")
            return

        struct = result.get("structural_diff", {})
        summary = struct.get("summary", {})

        # 摘要统计
        with ui.row().classes("q-mb-md gap-2"):
            ui.label(f"新增 {summary.get('added_count', 0)}").classes("mc-chip mc-chip-success")
            ui.label(f"删除 {summary.get('removed_count', 0)}").classes("mc-chip mc-chip-danger")
            ui.label(f"修改 {summary.get('modified_count', 0)}").classes("mc-chip mc-chip-warning")
            if summary.get("bound_count", 0) > 0:
                ui.label(f"绑定变量 {summary.get('bound_count', 0)}").classes("mc-chip")

        # 结构化差异
        if struct.get("type") == "structural":
            with ui.card().classes("w-full q-pa-md q-mb-md"):
                ui.label("结构化差异").classes("mc-section-title q-mb-sm")
                _render_structural_diff(struct)

        # 文本差异
        diff_lines = result.get("unified_diff", [])
        if diff_lines:
            _render_unified_diff(diff_lines)


def _open_upload_dialog(uploaded: dict, on_uploaded=None):
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("上传文件").classes("text-h6")
        ui.label("上传后会用于当前页面对比，不会自动覆盖服务端文件").classes("text-caption mc-muted q-mb-md")

        async def handle_upload(e):
            uploaded["data"] = await e.file.read()
            uploaded["name"] = e.file.name
            if on_uploaded:
                on_uploaded()
            dialog.close()

        ui.upload(label="选择文件", auto_upload=True, on_upload=handle_upload).props("dense").classes("w-full")

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("关闭", on_click=dialog.close).props("flat")

        dialog.open()


def _render_unified_diff(diff_lines: list[str]) -> None:
    full_text = "\n".join(diff_lines)
    preview_lines = diff_lines[:_DIFF_PREVIEW_LIMIT]
    preview_text = "\n".join(preview_lines)
    is_truncated = len(diff_lines) > _DIFF_PREVIEW_LIMIT

    def copy_all():
        js_text = json.dumps(full_text)

        async def do_copy():
            success = await copy_to_clipboard(full_text)
            notify_copy_result(success)

        ui.run_javascript(
            f"navigator.clipboard.writeText({js_text}).then(() => window._copyOk=true).catch(() => window._copyOk=false)"
        )
        # 延迟检查复制结果
        ui.timer(0.2, lambda: _check_copy_result(), once=True)

    def _check_copy_result():
        """检查剪贴板复制结果"""
        ui.run_javascript(
            "return window._copyOk",
            on_error=lambda: ui.notify("复制失败，请手动选择", type="warning"),
        )

    def open_full_dialog():
        with ui.dialog() as dialog, ui.card().classes("w-11/12 max-w-6xl"):
            with ui.row().classes("w-full items-center no-wrap q-mb-sm"):
                ui.label(f"文本差异 (unified diff) · 共 {len(diff_lines)} 行").classes("mc-section-title")
                ui.space()
                ui.button(icon="content_copy", on_click=copy_all).props("flat round dense").tooltip("复制")
                ui.button(icon="close", on_click=dialog.close).props("flat round dense").tooltip("关闭")
            ui.code(full_text, language="diff").classes("w-full mc-diff-code mc-diff-code--dialog")
        dialog.open()

    with ui.card().classes("w-full mc-diff-card"):
        with ui.row().classes("w-full items-center no-wrap mc-diff-toolbar"):
            ui.label("文本差异 (unified diff)").classes("mc-section-title")
            ui.space()
            ui.label(f"{len(diff_lines)} 行").classes("text-caption mc-muted")
            ui.button(icon="content_copy", on_click=copy_all).props("flat round dense").tooltip("复制")
            if is_truncated:
                ui.button(icon="open_in_full", on_click=open_full_dialog).props("flat round dense").tooltip("展开全部")

        if is_truncated:
            preview_text_with_hint = preview_text + f"\n... (预览 {_DIFF_PREVIEW_LIMIT} 行 / 共 {len(diff_lines)} 行)"
        else:
            preview_text_with_hint = preview_text
        ui.code(preview_text_with_hint, language="diff").classes("w-full mc-diff-code")


def _render_structural_diff(struct: dict):
    """渲染结构化差异"""
    # 新增项
    for item in struct.get("added", []):
        with ui.row().classes("items-center q-ml-md"):
            ui.icon("add_circle", color="green").classes("text-sm")
            ui.label(item["path"]).classes("font-mono")
            ui.label(f"= {item['new_value']}").classes("font-mono text-green")
            if item.get("bound"):
                ui.badge(item.get("bound_group", ""), color="purple").props("outline dense")

    # 删除项
    for item in struct.get("removed", []):
        with ui.row().classes("items-center q-ml-md"):
            ui.icon("remove_circle", color="red").classes("text-sm")
            ui.label(item["path"]).classes("font-mono")
            ui.label(f"= {item['old_value']}").classes("font-mono text-red")
            if item.get("bound"):
                ui.badge(item.get("bound_group", ""), color="purple").props("outline dense")

    # 修改项
    for item in struct.get("modified", []):
        with ui.row().classes("items-center q-ml-md"):
            ui.icon("edit", color="orange").classes("text-sm")
            ui.label(item["path"]).classes("font-mono")
            ui.label(item["old_value"]).classes("font-mono text-red")
            ui.icon("arrow_forward").classes("text-grey text-sm")
            ui.label(item["new_value"]).classes("font-mono text-green")
            if item.get("bound"):
                ui.badge(item.get("bound_group", ""), color="purple").props("outline dense")
