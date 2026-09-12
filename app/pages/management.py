"""配置管理页面 - 表格/行/表单布局"""

import re

from nicegui import ui

from app.core import storage, scheduler as sched
from app.utils.auth import is_deployer

# IP 地址正则
_IP_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$"
)


def _is_valid_ip(ip: str) -> bool:
    return bool(_IP_RE.match(ip or ""))


def render_management_page(deployer: bool):
    """渲染配置管理页面（分 tab 表格布局）"""
    ui.label("配置管理").classes("mc-page-title q-mb-sm")
    if not deployer:
        ui.label("访客仅可查看，无法修改").classes("text-warning mc-page-subtitle q-mb-sm")

    with ui.tabs().classes("w-full") as tabs:
        update_tab = ui.tab("Update").props("label='更新源' icon='cloud_download'")
        identity_tab = ui.tab("Identity").props("label='身份权限' icon='badge'")

    with ui.tab_panels(tabs, value=update_tab).classes("w-full"):
        with ui.tab_panel(update_tab):
            _render_update_panel(deployer)

        with ui.tab_panel(identity_tab):
            _render_identity_panel(deployer)


# ---------------------------------------------------------------------------
# Update tab: 更新链接 + 定时配置
# ---------------------------------------------------------------------------

def _render_update_panel(deployer: bool):
    status_state: dict = {}

    # ---- 更新链接表 ----
    ui.label("更新链接管理").classes("mc-section-title q-mb-xs")

    mapping_container = ui.column().classes("w-full")

    def refresh_mapping_list():
        mapping_container.clear()
        mapping = storage.load_config_mapping()
        if not mapping:
            with mapping_container:
                ui.label("暂无更新链接配置").classes("text-caption text-grey")
            return
        with mapping_container:
            with ui.element("div").classes("mc-mgmt-table w-full"):
                # 表头
                with ui.element("div").classes("mc-mgmt-thead"):
                    with ui.row().classes("items-center w-full no-wrap"):
                        ui.element("div").text("文件名").classes("mc-mgmt-th").style("flex: 2")
                        ui.element("div").text("更新时间").classes("mc-mgmt-th").style("flex: 1")
                        ui.element("div").text("下载链接").classes("mc-mgmt-th").style("flex: 3")
                        ui.element("div").text("状态").classes("mc-mgmt-th").style("flex: 1")
                        if deployer:
                            ui.element("div").text("操作").classes("mc-mgmt-th").style("flex: 1.2")

                for item in mapping:
                    _render_mapping_row(item, deployer, status_state, refresh_mapping_list)

    refresh_mapping_list()

    if deployer:
        with ui.element("div").classes("mc-mgmt-add-form w-full q-mt-sm"):
            ui.label("新增更新链接").classes("text-caption text-weight-bold q-mb-xs")
            with ui.row().classes("items-center q-gutter-sm w-full"):
                name_input = ui.input(placeholder="文件名 (例: config.xml)").props("dense outlined").classes("w-48")
                url_input = ui.input(placeholder="下载链接").props("dense outlined").classes("flex-1")
                name_err = ui.label("").classes("text-negative text-caption hidden")
                url_err = ui.label("").classes("text-negative text-caption hidden")

                def do_add_mapping():
                    name = (name_input.value or "").strip()
                    url = (url_input.value or "").strip()
                    # inline validation
                    has_err = False
                    if not name:
                        name_err.text = "文件名不能为空"
                        name_err.classes(remove="hidden")
                        has_err = True
                    else:
                        name_err.classes(add="hidden")
                    if not url:
                        url_err.text = "链接不能为空"
                        url_err.classes(remove="hidden")
                        has_err = True
                    else:
                        url_err.classes(add="hidden")
                    if has_err:
                        return
                    storage.add_config_mapping(name, url)
                    ui.notify(f"已添加: {name}", type="positive")
                    name_input.value = ""
                    url_input.value = ""
                    refresh_mapping_list()

                ui.button("添加", icon="add", on_click=do_add_mapping).props("color=primary dense")

    ui.separator().classes("q-my-md")

    # ---- 全量更新 ----
    with ui.row().classes("items-center q-mb-md"):
        ui.button("全量更新", icon="cloud_download",
                  on_click=lambda: _run_full_update(status_state, refresh_mapping_list)
                  ).props("color=primary")
        ui.label("从所有配置链接爬取最新版本文件").classes("mc-page-subtitle")

    # ---- 定时配置 ----
    if deployer:
        ui.separator().classes("q-my-sm")
        ui.label("定时更新配置").classes("mc-section-title q-mt-sm q-mb-xs")
        schedule_config = storage.load_schedule()
        interval_hours = schedule_config.get("interval_hours", 24)
        enabled_val = schedule_config.get("enabled", False)

        with ui.row().classes("items-center q-gutter-sm"):
            enabled_switch = ui.switch("启用定时更新", value=enabled_val)

            # UI: 根据 interval_hours 智能显示 天/小时
            if interval_hours >= 24 and interval_hours % 24 == 0:
                ui_default = interval_hours // 24
                ui_unit = "days"
            else:
                ui_default = interval_hours
                ui_unit = "hours"

            interval_input = ui.number(
                value=ui_default, min=1, step=1,
            ).props("dense outlined").classes("w-28")
            unit_select = ui.select(
                {"hours": "小时", "days": "天"},
                value=ui_unit,
            ).props("dense outlined").classes("w-24")
            schedule_err = ui.label("").classes("text-negative text-caption hidden")

            def do_save_schedule():
                val = interval_input.value
                if val is None or val < 1:
                    schedule_err.text = "间隔须 >= 1"
                    schedule_err.classes(remove="hidden")
                    return
                schedule_err.classes(add="hidden")
                unit = unit_select.value
                hours = val if unit == "hours" else val * 24
                sched.update_schedule_config(enabled_switch.value, hours)
                ui.notify("定时配置已保存", type="positive")

            ui.button("保存", icon="save", on_click=do_save_schedule).props("dense color=primary")


def _render_mapping_row(item: dict, deployer: bool, status_state: dict, refresh_cb):
    name = item.get("name", "")
    url = item.get("url", "")
    file_time = storage.get_file_update_date(name) if name else None
    status_info = status_state.get(name) or {}
    status = status_info.get("status")
    status_label = status_info.get("label")
    status_color = status_info.get("color")
    status_reason = status_info.get("reason")

    with ui.element("div").classes("mc-mgmt-row"):
        with ui.row().classes("items-center w-full no-wrap"):
            ui.element("div").classes("mc-mgmt-td").style("flex: 2").text(name or "-")
            ui.element("div").classes("mc-mgmt-td").style("flex: 1").text(file_time or "-")
            td_link = ui.element("div").classes("mc-mgmt-td text-grey-8").style("flex: 3")
            td_link.text(url if url else "未配置")
            # 状态
            td_status = ui.element("div").classes("mc-mgmt-td").style("flex: 1")
            if status and status_label:
                with td_status:
                    ui.badge(status_label, color=status_color or "grey")
                    if status_reason:
                        ui.label(status_reason).classes("text-caption text-grey")
            else:
                td_status.text("-")

            if deployer:
                td_actions = ui.element("div").classes("mc-mgmt-td").style("flex: 1.2")
                with td_actions:
                    with ui.row().classes("items-center q-gutter-xs"):
                        ui.button(icon="refresh",
                                  on_click=lambda n=name: _run_single_update(n, status_state, refresh_cb)
                                  ).props("flat round dense color=primary").tooltip("刷新")
                        ui.button(icon="edit",
                                  on_click=lambda it=item: _show_edit_mapping_dialog(it, status_state, refresh_cb)
                                  ).props("flat round dense color=primary").tooltip("编辑")
                        ui.button(icon="history",
                                  on_click=lambda it=item: _show_record_link_dialog(it)
                                  ).props("flat round dense").tooltip("记录链接")
                        ui.button(icon="delete",
                                  on_click=lambda n=name: _show_delete_mapping_dialog(n, status_state, refresh_cb)
                                  ).props("flat round dense color=negative").tooltip("删除")


# ---------------------------------------------------------------------------
# Identity tab: IP 对应 + 管理员
# ---------------------------------------------------------------------------

def _render_identity_panel(deployer: bool):
    with ui.tabs().classes("w-full") as id_tabs:
        ip_tab = ui.tab("IP").props("label='IP 对应表'")
        admin_tab = ui.tab("Admin").props("label='管理员列表'")

    with ui.tab_panels(id_tabs, value=ip_tab).classes("w-full"):
        with ui.tab_panel(ip_tab):
            _render_ip_panel(deployer)
        with ui.tab_panel(admin_tab):
            _render_admin_panel(deployer)


def _render_ip_panel(deployer: bool):
    ui.label("IP 对应表").classes("mc-section-title q-mb-xs")
    ui.label("维护操作 IP 与实际使用人之间的映射").classes("mc-page-subtitle q-mb-sm")

    ip_container = ui.column().classes("w-full")

    def refresh_ip_mapping():
        ip_container.clear()
        items = storage.load_ip_mapping()
        if not items:
            with ip_container:
                ui.label("暂无 IP 对应关系").classes("text-caption text-grey")
            return
        with ip_container:
            with ui.element("div").classes("mc-mgmt-table w-full"):
                with ui.element("div").classes("mc-mgmt-thead"):
                    with ui.row().classes("items-center w-full no-wrap"):
                        ui.element("div").text("IP 地址").classes("mc-mgmt-th").style("flex: 2")
                        ui.element("div").text("姓名").classes("mc-mgmt-th").style("flex: 2")
                        ui.element("div").text("更新时间").classes("mc-mgmt-th").style("flex: 2")
                        if deployer:
                            ui.element("div").text("操作").classes("mc-mgmt-th").style("flex: 1")

                for item in items:
                    _render_ip_row(item, deployer, refresh_ip_mapping)

    refresh_ip_mapping()

    if deployer:
        with ui.element("div").classes("mc-mgmt-add-form w-full q-mt-sm"):
            ui.label("新增 IP 对应").classes("text-caption text-weight-bold q-mb-xs")
            with ui.row().classes("items-center q-gutter-sm w-full"):
                ip_input = ui.input(placeholder="IP 地址").props("dense outlined").classes("w-48")
                name_input = ui.input(placeholder="人员姓名").props("dense outlined").classes("w-48")
                ip_err = ui.label("").classes("text-negative text-caption hidden")
                name_err = ui.label("").classes("text-negative text-caption hidden")
                dup_err = ui.label("").classes("text-negative text-caption hidden")

                def do_add_ip():
                    ip_val = (ip_input.value or "").strip()
                    name_val = (name_input.value or "").strip()
                    has_err = False
                    # inline validation
                    if not ip_val:
                        ip_err.text = "IP 不能为空"
                        ip_err.classes(remove="hidden")
                        has_err = True
                    elif not _is_valid_ip(ip_val):
                        ip_err.text = "IP 格式不合法"
                        ip_err.classes(remove="hidden")
                        has_err = True
                    else:
                        ip_err.classes(add="hidden")
                    if not name_val:
                        name_err.text = "姓名不能为空"
                        name_err.classes(remove="hidden")
                        has_err = True
                    else:
                        name_err.classes(add="hidden")
                    # duplicate check
                    existing = storage.load_ip_mapping()
                    if ip_val and any(e.get("ip") == ip_val for e in existing):
                        dup_err.text = "该 IP 已存在"
                        dup_err.classes(remove="hidden")
                        has_err = True
                    else:
                        dup_err.classes(add="hidden")
                    if has_err:
                        return
                    storage.upsert_ip_mapping(ip_val, name_val)
                    ui.notify("IP 对应已保存", type="positive")
                    ip_input.value = ""
                    name_input.value = ""
                    refresh_ip_mapping()

                ui.button("添加", icon="add", on_click=do_add_ip).props("color=primary dense")


def _render_ip_row(item: dict, deployer: bool, refresh_cb):
    ip = item.get("ip", "")
    name = item.get("name", "")
    updated_at = item.get("updated_at", "")

    with ui.element("div").classes("mc-mgmt-row"):
        with ui.row().classes("items-center w-full no-wrap"):
            ui.element("div").classes("mc-mgmt-td font-mono").style("flex: 2").text(ip or "-")
            ui.element("div").classes("mc-mgmt-td").style("flex: 2").text(name or "-")
            ui.element("div").classes("mc-mgmt-td text-grey-7").style("flex: 2").text(updated_at or "-")
            if deployer:
                td_actions = ui.element("div").classes("mc-mgmt-td").style("flex: 1")
                with td_actions:
                    with ui.row().classes("items-center q-gutter-xs"):
                        ui.button(icon="edit",
                                  on_click=lambda entry=item: _show_edit_ip_mapping_dialog(entry, refresh_cb)
                                  ).props("flat round dense color=primary").tooltip("编辑")
                        ui.button(icon="delete",
                                  on_click=lambda value=ip: _show_delete_ip_mapping_dialog(value, refresh_cb)
                                  ).props("flat round dense color=negative").tooltip("删除")


def _render_admin_panel(deployer: bool):
    ui.label("管理员列表").classes("mc-section-title q-mb-xs")
    ui.label("维护拥有审阅修改和文件删除权限的管理员名单").classes("mc-page-subtitle q-mb-sm")

    admin_container = ui.column().classes("w-full")

    def refresh_admin_users():
        admin_container.clear()
        items = storage.load_admin_users()
        if not items:
            with admin_container:
                ui.label("暂无管理员").classes("text-caption text-grey")
            return
        with admin_container:
            with ui.element("div").classes("mc-mgmt-table w-full"):
                with ui.element("div").classes("mc-mgmt-thead"):
                    with ui.row().classes("items-center w-full no-wrap"):
                        ui.element("div").text("姓名").classes("mc-mgmt-th").style("flex: 3")
                        ui.element("div").text("更新时间").classes("mc-mgmt-th").style("flex: 3")
                        if deployer:
                            ui.element("div").text("操作").classes("mc-mgmt-th").style("flex: 1")

                for item in items:
                    _render_admin_row(item, deployer, refresh_admin_users)

    refresh_admin_users()

    if deployer:
        with ui.element("div").classes("mc-mgmt-add-form w-full q-mt-sm"):
            ui.label("新增管理员").classes("text-caption text-weight-bold q-mb-xs")
            with ui.row().classes("items-center q-gutter-sm w-full"):
                admin_input = ui.input(placeholder="管理员姓名").props("dense outlined").classes("w-48")
                admin_err = ui.label("").classes("text-negative text-caption hidden")
                dup_err = ui.label("").classes("text-negative text-caption hidden")

                def do_add_admin():
                    name_val = (admin_input.value or "").strip()
                    has_err = False
                    if not name_val:
                        admin_err.text = "姓名不能为空"
                        admin_err.classes(remove="hidden")
                        has_err = True
                    else:
                        admin_err.classes(add="hidden")
                    existing = storage.load_admin_users()
                    if name_val and any(e.get("name") == name_val for e in existing):
                        dup_err.text = "该管理员已存在"
                        dup_err.classes(remove="hidden")
                        has_err = True
                    else:
                        dup_err.classes(add="hidden")
                    if has_err:
                        return
                    storage.upsert_admin_user(name_val)
                    ui.notify("管理员已保存", type="positive")
                    admin_input.value = ""
                    refresh_admin_users()

                ui.button("添加", icon="add", on_click=do_add_admin).props("color=primary dense")


def _render_admin_row(item: dict, deployer: bool, refresh_cb):
    name = item.get("name", "")
    updated_at = item.get("updated_at", "")

    with ui.element("div").classes("mc-mgmt-row"):
        with ui.row().classes("items-center w-full no-wrap"):
            ui.element("div").classes("mc-mgmt-td").style("flex: 3").text(name or "-")
            ui.element("div").classes("mc-mgmt-td text-grey-7").style("flex: 3").text(updated_at or "-")
            if deployer:
                td_actions = ui.element("div").classes("mc-mgmt-td").style("flex: 1")
                with td_actions:
                    with ui.row().classes("items-center q-gutter-xs"):
                        ui.button(icon="edit",
                                  on_click=lambda entry=item: _show_edit_admin_user_dialog(entry, refresh_cb)
                                  ).props("flat round dense color=primary").tooltip("编辑")
                        ui.button(icon="delete",
                                  on_click=lambda value=name: _show_delete_admin_user_dialog(value, refresh_cb)
                                  ).props("flat round dense color=negative").tooltip("删除")


# ---------------------------------------------------------------------------
# CRUD helpers (dialog + actions)
# ---------------------------------------------------------------------------

def _run_single_update(name: str, status_state: dict, refresh_cb):
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    ui.notify(f"开始刷新: {name}", type="info")
    result = sched.run_single_update(name)
    status_state[name] = _format_update_status(result)
    if result.get("status") == "success":
        ui.notify(f"刷新成功: {name}", type="positive")
    else:
        ui.notify(f"刷新完成: {name}", type="warning")
    refresh_cb()


def _run_full_update(status_state: dict, refresh_cb):
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    result = sched.run_full_update()
    for d in result.get("details", []):
        n = d.get("name")
        if not n:
            continue
        status_state[n] = _format_update_status(d)
    ui.notify(f"更新完成: 成功 {result['success']}, 失败 {result['failed']}", type="info")
    refresh_cb()


def _format_update_status(result: dict) -> dict:
    status = result.get("status")
    if status == "success":
        return {"status": status, "label": "刷新成功", "color": "green"}
    if status == "skipped":
        return {"status": status, "label": "已跳过", "color": "grey", "reason": result.get("reason")}
    if status == "not_found":
        return {"status": status, "label": "未找到", "color": "grey"}
    if status == "download_failed":
        return {"status": status, "label": "下载失败", "color": "red"}
    if status == "error":
        return {"status": status, "label": "刷新失败", "color": "red", "reason": result.get("reason")}
    return {"status": status, "label": "未知状态", "color": "grey"}


def _show_edit_mapping_dialog(item: dict, status_state: dict, refresh_cb):
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return

    original_name = item.get("name", "")
    with ui.dialog() as dialog, ui.card().classes("w-[600px] max-w-[95vw]"):
        ui.label("修改更新链接").classes("text-h6 q-mb-sm")
        name_input = ui.input(value=original_name, placeholder="文件名 (例: config.xml)").props("dense outlined").classes("w-full")
        url_input = ui.input(value=item.get("url", ""), placeholder="下载链接").props("dense outlined").classes("w-full")
        migrate_switch = ui.switch("同时迁移本地文件与归档目录（改名时）", value=True)
        err_label = ui.label("").classes("text-negative text-caption hidden")

        def do_save():
            new_name = (name_input.value or "").strip()
            new_url = (url_input.value or "").strip()
            if not new_name:
                err_label.text = "文件名不能为空"
                err_label.classes(remove="hidden")
                return
            if not new_url:
                err_label.text = "链接不能为空"
                err_label.classes(remove="hidden")
                return
            err_label.classes(add="hidden")
            try:
                ok = storage.update_config_mapping(
                    original_name, new_name, new_url,
                    migrate_files=bool(migrate_switch.value),
                )
            except Exception as e:
                ui.notify(str(e) or "保存失败", type="negative")
                return
            if not ok:
                ui.notify("未找到对应映射项", type="warning")
                return
            if original_name in status_state and new_name != original_name:
                status_state[new_name] = status_state.pop(original_name)
            ui.notify("已保存", type="positive")
            dialog.close()
            refresh_cb()

        with ui.row().classes("w-full justify-end q-gutter-sm"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("保存", icon="save", on_click=do_save).props("color=primary")

        dialog.open()


def _show_delete_mapping_dialog(name: str, status_state: dict, refresh_cb):
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("删除更新链接").classes("text-h6")
        ui.label(f"将从映射表移除: {name}").classes("text-body2 q-mb-sm")
        ui.label("仅删除链接配置，不会删除本地已缓存文件与归档版本。").classes("text-caption text-grey q-mb-sm")

        def do_delete():
            ok = storage.delete_config_mapping(name, delete_files=False)
            if not ok:
                ui.notify("删除失败或条目不存在", type="warning")
                return
            status_state.pop(name, None)
            ui.notify("已删除", type="positive")
            dialog.close()
            refresh_cb()

        with ui.row().classes("w-full justify-end q-gutter-sm"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("删除", icon="delete", on_click=do_delete).props("color=negative")

        dialog.open()


def _show_record_link_dialog(item: dict):
    name = item.get("name", "")
    with ui.dialog() as dialog, ui.card().classes("w-[600px] max-w-[95vw]"):
        ui.label("修改记录爬取链接").classes("text-h6 q-mb-sm")
        ui.label(f"配置文件: {name}").classes("text-caption text-grey q-mb-sm")
        record_input = ui.input(
            value=item.get("record_url", ""),
            placeholder="用于拉取修改记录的链接（http/https）",
        ).props("dense outlined").classes("w-full")

        def do_save():
            if not is_deployer():
                ui.notify("权限不足", type="negative")
                return
            ok = storage.update_config_record_url(name, record_input.value or "")
            if not ok:
                ui.notify("未找到对应映射项", type="warning")
                return
            ui.notify("已保存", type="positive")
            dialog.close()

        with ui.row().classes("w-full justify-end q-gutter-sm"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("保存", icon="save", on_click=do_save).props("color=primary")

        dialog.open()


def _show_edit_ip_mapping_dialog(item: dict, refresh_cb) -> None:
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return

    original_ip = item.get("ip", "")
    with ui.dialog() as dialog, ui.card().classes("w-[520px] max-w-[95vw]"):
        ui.label("编辑 IP 对应").classes("text-h6 q-mb-sm")
        ip_input = ui.input(value=original_ip, placeholder="IP 地址").props("dense outlined").classes("w-full")
        name_input = ui.input(value=item.get("name", ""), placeholder="人员姓名").props("dense outlined").classes("w-full")
        err_label = ui.label("").classes("text-negative text-caption hidden")

        def do_save():
            new_ip = str(ip_input.value or "").strip()
            new_name = str(name_input.value or "").strip()
            if not new_ip:
                err_label.text = "IP 不能为空"
                err_label.classes(remove="hidden")
                return
            if not _is_valid_ip(new_ip):
                err_label.text = "IP 格式不合法"
                err_label.classes(remove="hidden")
                return
            if not new_name:
                err_label.text = "姓名不能为空"
                err_label.classes(remove="hidden")
                return
            err_label.classes(add="hidden")
            if new_ip != original_ip:
                storage.remove_ip_mapping(original_ip)
            try:
                storage.upsert_ip_mapping(new_ip, new_name)
            except Exception as e:
                ui.notify(str(e) or "保存失败", type="negative")
                return
            ui.notify("已保存", type="positive")
            dialog.close()
            refresh_cb()

        with ui.row().classes("w-full justify-end q-gutter-sm"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("保存", icon="save", on_click=do_save).props("color=primary")

        dialog.open()


def _show_delete_ip_mapping_dialog(ip: str, refresh_cb) -> None:
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("删除 IP 对应").classes("text-h6")
        ui.label(f"确认删除 IP: {ip}").classes("text-body2 q-mb-sm")

        def do_delete():
            ok = storage.remove_ip_mapping(ip)
            if not ok:
                ui.notify("记录不存在", type="warning")
                return
            ui.notify("已删除", type="positive")
            dialog.close()
            refresh_cb()

        with ui.row().classes("w-full justify-end q-gutter-sm"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("删除", icon="delete", on_click=do_delete).props("color=negative")

        dialog.open()


def _show_edit_admin_user_dialog(item: dict, refresh_cb) -> None:
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return

    original_name = item.get("name", "")
    with ui.dialog() as dialog, ui.card().classes("w-[520px] max-w-[95vw]"):
        ui.label("编辑管理员").classes("text-h6 q-mb-sm")
        name_input = ui.input(value=original_name, placeholder="管理员姓名").props("dense outlined").classes("w-full")
        err_label = ui.label("").classes("text-negative text-caption hidden")

        def do_save():
            new_name = str(name_input.value or "").strip()
            if not new_name:
                err_label.text = "管理员姓名不能为空"
                err_label.classes(remove="hidden")
                return
            err_label.classes(add="hidden")
            if new_name != original_name:
                storage.remove_admin_user(original_name)
            try:
                storage.upsert_admin_user(new_name)
            except Exception as e:
                ui.notify(str(e) or "保存失败", type="negative")
                return
            ui.notify("已保存", type="positive")
            dialog.close()
            refresh_cb()

        with ui.row().classes("w-full justify-end q-gutter-sm"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("保存", icon="save", on_click=do_save).props("color=primary")

        dialog.open()


def _show_delete_admin_user_dialog(name: str, refresh_cb) -> None:
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("删除管理员").classes("text-h6")
        ui.label(f"确认删除管理员: {name}").classes("text-body2 q-mb-sm")

        def do_delete():
            ok = storage.remove_admin_user(name)
            if not ok:
                ui.notify("记录不存在", type="warning")
                return
            ui.notify("已删除", type="positive")
            dialog.close()
            refresh_cb()

        with ui.row().classes("w-full justify-end q-gutter-sm"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("删除", icon="delete", on_click=do_delete).props("color=negative")

        dialog.open()
