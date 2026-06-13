"""审阅修改页面。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from nicegui import ui

from app.core import parser, parse_cache, reviewing, storage
from app.pages.file_downloads import DOWNLOAD_KIND_CURRENT, make_download_handler
from app.utils.auth import get_identity_info, is_admin


_REJECT_OPTION = "__reject__"


def render_review_page(session_active_tab: dict | None = None) -> None:
    ui.label("审阅修改").classes("mc-page-title q-mb-sm")

    identity = get_identity_info()
    if not is_admin():
        ui.label("仅管理员可访问该功能").classes("text-negative")
        return

    ui.label(
        f"当前审阅人: {identity['name']}  ({identity['ip']})"
    ).classes("mc-page-subtitle q-mb-md")

    review_items = storage.list_review_items()
    if not review_items:
        ui.label("暂无待审阅的修改备注").classes("text-caption text-grey")
        return

    selection_state: dict[str, dict[str, str | None]] = {}
    generated_container = ui.column().classes("w-full q-gutter-sm q-mt-md")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in review_items:
        grouped[item.get("source_file") or ""].append(item)

    for source_file in sorted(grouped.keys()):
        items = grouped[source_file]
        with ui.card().classes("w-full q-pa-sm"):
            with ui.row().classes("items-center q-gutter-sm q-mb-sm"):
                ui.badge(source_file, color="blue")
                ui.label(f"{len(items)} 个待审节点").classes("text-caption text-grey")

            for item in items:
                _render_review_item(item, selection_state)

    def submit_review():
        pending_missing = [
            item for item in review_items
            if not (selection_state.get(_selection_key(item), {}).get("value"))
        ]
        if pending_missing:
            ui.notify("请先为每个待审节点选择通过项或不通过", type="warning")
            return

        remark_lookup = {}
        approved_by_file: dict[str, list[dict]] = defaultdict(list)
        approved_ids: list[str] = []
        rejected_ids: list[str] = []

        for item in review_items:
            chosen = selection_state[_selection_key(item)]["value"]
            remarks = item.get("remarks") or []
            for remark in remarks:
                remark_lookup[str(remark.get("id") or "")] = remark
            if chosen == _REJECT_OPTION:
                rejected_ids.extend(str(remark.get("id") or "") for remark in remarks)
                continue
            approved_item = remark_lookup.get(str(chosen))
            if approved_item is None:
                ui.notify("审阅选择异常，请重新选择", type="negative")
                return
            approved_ids.append(str(approved_item.get("id") or ""))
            approved_by_file[item.get("source_file") or ""].append(approved_item)
            rejected_ids.extend(
                str(remark.get("id") or "")
                for remark in remarks
                if str(remark.get("id") or "") != str(chosen)
            )

        generated_files: dict[str, str] = {}
        download_targets: list[str] = []

        for source_file, approved_items in approved_by_file.items():
            if not approved_items:
                continue
            source_path = storage.get_config_path(source_file)
            tree = parse_cache.load_tree(source_path)
            if tree is None:
                tree = parser.parse_path(source_path, source_file)
            updated_tree = reviewing.apply_review_updates(tree, approved_items)
            new_filename = _build_generated_filename(source_file)
            content = reviewing.serialize_tree(updated_tree, new_filename)
            storage.save_config_file(new_filename, content)
            parse_cache.save_tree(storage.get_config_path(new_filename), updated_tree)
            generated_files[source_file] = new_filename
            download_targets.append(new_filename)

        result = storage.apply_review_results(
            approved_ids=approved_ids,
            rejected_ids=rejected_ids,
            reviewer_name=identity["name"],
            reviewer_ip=identity["ip"],
            generated_files=generated_files,
        )

        generated_container.clear()
        with generated_container:
            if generated_files:
                ui.label("审阅结果文件").classes("mc-section-title")
                for source_file, generated_name in generated_files.items():
                    with ui.card().classes("w-full q-pa-sm"):
                        with ui.row().classes("items-center q-gutter-sm"):
                            ui.badge(source_file, color="grey-7")
                            ui.icon("arrow_right_alt", color="grey-6")
                            ui.badge(generated_name, color="positive")
                            ui.space()
                            ui.button(
                                "下载",
                                icon="download",
                                on_click=make_download_handler(DOWNLOAD_KIND_CURRENT, generated_name),
                            ).props("flat dense color=primary")
            else:
                ui.label("本次审阅未选择任何通过项，未生成新文件").classes("text-caption text-grey")

        if session_active_tab is not None:
            session_active_tab.pop("_rendered", None)

        ui.notify(
            f"审阅完成：通过 {result['approved']} 条，驳回 {result['rejected']} 条",
            type="positive",
        )

    with ui.row().classes("w-full justify-end q-gutter-sm q-mt-md"):
        ui.button("提交审阅结果", icon="done_all", on_click=submit_review).props("color=primary")


def _render_review_item(item: dict, selection_state: dict[str, dict[str, str | None]]) -> None:
    remarks = item.get("remarks") or []
    selection_key = _selection_key(item)
    selection_state.setdefault(selection_key, {"value": None})

    with ui.card().classes("w-full q-mb-sm q-pa-sm mc-review-card"):
        with ui.row().classes("items-center q-gutter-sm q-mb-xs"):
            ui.label(item.get("node_label") or item.get("node_path") or "-").classes("text-subtitle2 text-weight-medium")
            ui.badge(item.get("node_type") or "-", color="teal")
        ui.label(f"节点路径: {item.get('node_path') or '-'}").classes("text-caption text-grey")
        ui.label(f"当前值: {item.get('original_value') or '-'}").classes("text-caption text-grey q-mb-sm")

        options = {
            str(remark.get("id") or ""): (
                f"{str(remark.get('actor_display') or remark.get('actor_ip') or '未知')}："
                f"{str(remark.get('proposed_value') or '')}"
            )
            for remark in remarks
        }
        options[_REJECT_OPTION] = "不通过"

        ui.radio(
            options,
            value=selection_state[selection_key]["value"],
            on_change=lambda e, key=selection_key: selection_state[key].update(value=e.value),
        ).props("inline")

        with ui.column().classes("w-full q-gutter-xs q-mt-sm"):
            for remark in remarks:
                actor = str(remark.get("actor_display") or remark.get("actor_ip") or "未知")
                created_at = str(remark.get("created_at") or "-")
                ui.label(
                    f"{actor}  提交于 {created_at}  建议值: {str(remark.get('proposed_value') or '')}"
                ).classes("mc-review-note-item")


def _selection_key(item: dict) -> str:
    return f"{item.get('source_file') or ''}|{item.get('node_key') or ''}"


def _build_generated_filename(source_file: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if "." in source_file:
        name, ext = source_file.rsplit(".", 1)
        return f"{name}_{timestamp}.{ext}"
    return f"{source_file}_{timestamp}"
