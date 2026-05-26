"""版本对比页面"""

from nicegui import ui

from app.core import storage
from app.core.differ import compare_versions, compare_with_uploaded


def render_comparison_page(tab: dict, deployer: bool):
    """渲染版本对比页面"""
    filename = tab.get("filename", "")

    ui.label(f"版本对比 - {filename}").classes("text-h6 q-mb-md")

    versions = storage.list_archived_versions(filename)
    version_options = [v["filename"] for v in versions]

    mode = {"value": "current_vs_history"}

    with ui.row().classes("w-full items-center q-mb-md"):
        ui.select(
            {
                "current_vs_history": "当前版本 vs 历史版本",
                "history_vs_history": "历史版本 vs 历史版本",
                "upload_vs_current": "上传文件 vs 当前版本",
                "upload_vs_history": "上传文件 vs 历史版本",
            },
            value="current_vs_history",
            on_change=lambda e: mode.update(value=e.value),
        ).props("dense outlined").classes("w-80")

    diff_container = ui.column().classes("w-full")

    def setup_compare_ui():
        diff_container.clear()
        with diff_container:
            if mode["value"] == "current_vs_history":
                _render_current_vs_history(filename, version_options, diff_container)
            elif mode["value"] == "history_vs_history":
                _render_history_vs_history(filename, version_options, diff_container)
            elif mode["value"] == "upload_vs_current":
                _render_upload_vs_current(filename, diff_container)
            elif mode["value"] == "upload_vs_history":
                _render_upload_vs_history(filename, version_options, diff_container)

    setup_compare_ui()


def _render_current_vs_history(filename: str, version_options: list, container):
    """当前版本 vs 历史版本"""
    if not version_options:
        ui.label("无历史版本可对比").classes("text-warning")
        return

    selected = {"value": version_options[0]}
    with ui.row().classes("items-center q-mb-md"):
        ui.select(version_options, value=selected["value"],
                  on_change=lambda e: selected.update(value=e.value)
                  ).props("dense outlined").classes("w-64")
        ui.button("对比",
                  on_click=lambda: _run_current_vs_history(filename, selected["value"], container)
                  ).props("color=primary dense")


def _render_history_vs_history(filename: str, version_options: list, container):
    """历史版本 vs 历史版本"""
    if len(version_options) < 2:
        ui.label("需要至少2个历史版本").classes("text-warning")
        return

    selected_a = {"value": version_options[0]}
    selected_b = {"value": version_options[1] if len(version_options) > 1 else ""}

    with ui.row().classes("items-center q-mb-md"):
        ui.select(version_options, value=selected_a["value"],
                  on_change=lambda e: selected_a.update(value=e.value)
                  ).props("dense outlined").classes("w-56")
        ui.label("vs")
        ui.select(version_options, value=selected_b["value"],
                  on_change=lambda e: selected_b.update(value=e.value)
                  ).props("dense outlined").classes("w-56")
        ui.button("对比",
                  on_click=lambda: _run_history_vs_history(filename, selected_a["value"], selected_b["value"], container)
                  ).props("color=primary dense")


def _render_upload_vs_current(filename: str, container):
    """上传文件 vs 当前版本"""
    uploaded = {"data": None}

    ui.label("请上传文件:").classes("text-subtitle2")
    ui.upload(
        label="上传文件",
        auto_upload=True,
        on_upload=lambda e: uploaded.update(data=e.content.read()),
    ).props("dense").classes("w-full")

    ui.button("对比",
              on_click=lambda: _run_upload_vs_current(filename, uploaded, container)
              ).props("color=primary dense")


def _render_upload_vs_history(filename: str, version_options: list, container):
    """上传文件 vs 历史版本"""
    if not version_options:
        ui.label("无历史版本").classes("text-warning")
        return

    uploaded = {"data": None}
    selected = {"value": version_options[0]}

    ui.label("请上传文件:").classes("text-subtitle2")
    ui.upload(
        label="上传文件",
        auto_upload=True,
        on_upload=lambda e: uploaded.update(data=e.content.read()),
    ).props("dense").classes("w-full")

    with ui.row().classes("items-center q-mt-md"):
        ui.select(version_options, value=selected["value"],
                  on_change=lambda e: selected.update(value=e.value)
                  ).props("dense outlined").classes("w-56")
        ui.button("对比",
                  on_click=lambda: _run_upload_vs_history(filename, selected["value"], uploaded, container)
                  ).props("color=primary dense")


# ===================== 对比执行函数 =====================


def _run_current_vs_history(filename: str, archive_filename: str, container):
    current_content = storage.load_config_file(filename)
    archive_content = storage.load_archived_file(filename, archive_filename)
    if current_content is None or archive_content is None:
        with container:
            ui.label("文件内容加载失败").classes("text-negative")
        return
    result = compare_versions(archive_content, current_content, archive_filename, filename)
    _render_diff_result(result, container)


def _run_history_vs_history(filename: str, archive_a: str, archive_b: str, container):
    content_a = storage.load_archived_file(filename, archive_a)
    content_b = storage.load_archived_file(filename, archive_b)
    if content_a is None or content_b is None:
        with container:
            ui.label("文件内容加载失败").classes("text-negative")
        return
    result = compare_versions(content_a, content_b, archive_a, archive_b)
    _render_diff_result(result, container)


def _run_upload_vs_current(filename: str, uploaded: dict, container):
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
            ui.label("两个版本内容相同，无差异").classes("text-positive text-h6")
            return

        struct = result.get("structural_diff", {})
        summary = struct.get("summary", {})

        # 摘要统计
        with ui.row().classes("q-mb-md"):
            ui.badge(f"新增: {summary.get('added_count', 0)}", color="green")
            ui.badge(f"删除: {summary.get('removed_count', 0)}", color="red")
            ui.badge(f"修改: {summary.get('modified_count', 0)}", color="orange")
            if summary.get("bound_count", 0) > 0:
                ui.badge(f"绑定变量: {summary.get('bound_count', 0)}", color="purple")

        # 结构化差异
        if struct.get("type") == "structural":
            _render_structural_diff(struct)

        # 文本差异
        diff_lines = result.get("unified_diff", [])
        if diff_lines:
            ui.separator().classes("q-my-md")
            ui.label("文本差异 (unified diff)").classes("text-subtitle1 q-mb-sm")
            with ui.card().classes("w-full bg-grey-2"):
                diff_text = "\n".join(diff_lines[:200])
                if len(diff_lines) > 200:
                    diff_text += f"\n... (共 {len(diff_lines)} 行差异)"
                ui.code(diff_text, language="diff").classes("w-full")


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
