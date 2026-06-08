"""修改记录页面"""

import urllib.parse

from nicegui import ui

from app.core import storage, scheduler as sched
from app.core.differ import compare_versions
from app.utils.auth import is_deployer


def render_records_page(tab: dict, deployer: bool, session_tabs: list, session_active_tab: dict):
    filename = tab.get("filename", "")
    mapping = storage.load_config_mapping()
    mapping_item = next((m for m in mapping if m.get("name") == filename), None) or {}
    record_url = (mapping_item.get("record_url") or "").strip()

    ui.label("修改记录").classes("mc-page-title q-mb-sm")
    with ui.row().classes("items-center q-mb-md"):
        ui.badge(filename, color="blue")
        if record_url:
            ui.label(record_url).classes("text-caption text-grey q-ml-sm break-all")
        else:
            ui.label("未配置修改记录链接").classes("text-caption text-grey q-ml-sm")
        ui.space()

        refresh_btn = ui.button("手动刷新", icon="refresh")
        refresh_btn.props("flat dense color=primary")

    container = ui.column().classes("w-full")

    state = {"versions": []}

    def _load_versions():
        container.clear()
        versions = storage.list_record_versions(filename)
        state["versions"] = versions
        if not versions:
            with container:
                ui.label("暂无修改记录").classes("mc-page-subtitle")
                if not record_url:
                    ui.label("请到“更新设置”中为该文件配置修改记录爬取链接").classes("mc-page-subtitle")
            return

        with container:
            ui.label("历史修改记录").classes("mc-section-title q-mb-sm")
            for idx, v in enumerate(versions):
                prev = versions[idx + 1] if idx + 1 < len(versions) else None
                _render_record_card(filename, v, prev)

    def _refresh_records_once(show_toast: bool):
        if not record_url:
            if show_toast:
                ui.notify("未配置修改记录链接", type="warning")
            return
        if not is_deployer():
            if show_toast:
                ui.notify("权限不足", type="negative")
            return

        refresh_btn.props(add="loading")
        refresh_btn.update()
        ok = None
        try:
            r = sched.run_single_record_update(filename)
            ok = r.get("status") == "success"
            if show_toast:
                if ok:
                    ui.notify("修改记录已刷新", type="positive")
                else:
                    ui.notify("修改记录刷新失败，请稍后重试", type="warning")
        except Exception:
            ok = False
            if show_toast:
                ui.notify("修改记录刷新失败，请稍后重试", type="negative")
        finally:
            refresh_btn.props(remove="loading")
            refresh_btn.update()
        _load_versions()

    refresh_btn.on("click", lambda e=None: _refresh_records_once(True))

    _load_versions()

    interval = sched.get_auto_refresh_interval_seconds(min_seconds=60.0)
    if interval and deployer:
        ui.timer(interval, lambda: _refresh_records_once(False))


def _diff_line_counts(diff_lines: list[str]) -> tuple[int, int]:
    added = 0
    removed = 0
    for line in diff_lines or []:
        if line.startswith("+++"):
            continue
        if line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _render_diff_chips_for_result(result: dict) -> str:
    if not result:
        return '<span class="mc-chip">未计算</span>'

    if not result.get("has_changes"):
        return '<span class="mc-chip">无变更</span>'

    struct = result.get("structural_diff") or {}
    if isinstance(struct, dict) and struct.get("type") == "structural":
        summary = struct.get("summary") or {}
        added = int(summary.get("added_count", 0) or 0)
        removed = int(summary.get("removed_count", 0) or 0)
        modified = int(summary.get("modified_count", 0) or 0)
        return (
            f'<span class="mc-chip mc-chip-success">+{added}</span>'
            f'<span class="mc-chip mc-chip-danger">-{removed}</span>'
            f'<span class="mc-chip mc-chip-warning">~{modified}</span>'
        )

    diff_lines = result.get("unified_diff") or []
    added, removed = _diff_line_counts(diff_lines)
    return (
        f'<span class="mc-chip mc-chip-success">+{added}</span>'
        f'<span class="mc-chip mc-chip-danger">-{removed}</span>'
        f'<span class="mc-chip">仅文本</span>'
    )


def _render_record_card(config_name: str, current: dict, previous: dict | None):
    record_filename = current.get("filename", "")
    header_date = current.get("date", "")
    size = current.get("size", 0)

    diff_result = None
    if previous:
        old = storage.load_record_file(config_name, previous.get("filename", ""))
        new = storage.load_record_file(config_name, record_filename)
        if old is not None and new is not None:
            try:
                diff_result = compare_versions(old, new, previous.get("filename", ""), record_filename)
            except Exception:
                diff_result = None

    def _open_record():
        cfg = urllib.parse.quote(str(config_name))
        rf = urllib.parse.quote(str(record_filename))
        ui.run_javascript(f'window.open("/record_view?config={cfg}&record={rf}", "_blank")')

    card = ui.card().classes("w-full q-pa-md q-mb-sm mc-history-card cursor-pointer")
    card.on("click", lambda e=None: _open_record())
    with card:
        with ui.row().classes("items-start w-full mc-history-row"):
            with ui.column().classes("q-gutter-xs"):
                ui.label(record_filename).classes("text-body1 text-weight-medium mc-mono")
                ui.label(header_date).classes("mc-page-subtitle")
                ui.label(f"大小: {size} bytes").classes("mc-page-subtitle")
            ui.space()
            with ui.column().classes("items-end q-gutter-xs"):
                ui.html(_render_diff_chips_for_result(diff_result), sanitize=False)
                with ui.row().classes("items-center q-gutter-xs"):
                    ui.button(
                        "打开解析",
                        icon="open_in_new",
                        on_click=lambda e=None: _open_record(),
                    ).props("flat dense color=primary")

        if previous and diff_result and diff_result.get("has_changes"):
            with ui.expansion("变更点（与上一条对比）", value=False).classes("w-full q-mt-sm"):
                struct = diff_result.get("structural_diff") or {}
                if isinstance(struct, dict) and struct.get("type") == "structural":
                    _render_structural_diff_blocks(struct)
                    with ui.expansion("原始差异（文本）", value=False).classes("w-full q-mt-sm"):
                        _render_unified_diff(diff_result.get("unified_diff") or [])
                else:
                    _render_unified_diff(diff_result.get("unified_diff") or [])


def _render_structural_diff_blocks(struct: dict):
    added = struct.get("added") or []
    removed = struct.get("removed") or []
    modified = struct.get("modified") or []

    def _esc(v) -> str:
        s = "" if v is None else str(v)
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    blocks = []
    if added:
        blocks.append(("新增", added, "mc-change-add"))
    if removed:
        blocks.append(("删除", removed, "mc-change-del"))
    if modified:
        blocks.append(("修改", modified, "mc-change-mod"))

    if not blocks:
        ui.label("无结构化变更").classes("mc-page-subtitle")
        return

    for title, items, cls in blocks:
        limited = items[:80]
        rows = []
        for it in limited:
            path = _esc(it.get("path"))
            if title == "新增":
                rows.append(f'<div class="mc-change-row"><span class="mc-change-path">{path}</span><span class="mc-change-val">= {_esc(it.get("new_value"))}</span></div>')
            elif title == "删除":
                rows.append(f'<div class="mc-change-row"><span class="mc-change-path">{path}</span><span class="mc-change-val">= {_esc(it.get("old_value"))}</span></div>')
            else:
                rows.append(
                    f'<div class="mc-change-row"><span class="mc-change-path">{path}</span>'
                    f'<span class="mc-change-val mc-change-old">{_esc(it.get("old_value"))}</span>'
                    f'<span class="mc-change-arrow">→</span>'
                    f'<span class="mc-change-val mc-change-new">{_esc(it.get("new_value"))}</span></div>'
                )

        more_html = '<div class="mc-change-more">仅展示前 80 条</div>' if len(items) > 80 else ''
        ui.html(
            f'<div class="mc-change-block {cls}">'
            f'<div class="mc-change-title">{title} <span class="mc-change-count">{len(items)}</span></div>'
            f'{"".join(rows)}'
            f'{more_html}'
            f"</div>",
            sanitize=False,
        )


def _render_unified_diff(lines: list[str]):
    limited = lines[:400]
    html_lines = []
    for line in limited:
        cls = "mc-diff-line"
        if line.startswith("+++"):
            cls += " mc-diff-meta"
        elif line.startswith("---"):
            cls += " mc-diff-meta"
        elif line.startswith("@@"):
            cls += " mc-diff-hunk"
        elif line.startswith("+"):
            cls += " mc-diff-add"
        elif line.startswith("-"):
            cls += " mc-diff-del"
        else:
            cls += " mc-diff-ctx"
        safe = (
            line.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        html_lines.append(f'<div class="{cls}">{safe}</div>')
    ui.html(f'<div class="mc-diff-wrap">{"".join(html_lines)}</div>', sanitize=False)
