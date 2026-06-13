"""更新设置页面"""

from nicegui import ui

from app.core import storage, scheduler as sched
from app.utils.auth import is_deployer


def render_management_page(deployer: bool):
    """渲染更新设置页面"""
    ui.label("更新设置").classes("mc-page-title q-mb-sm")
    if not deployer:
        ui.label("访客仅可查看配置映射表，无法修改").classes("text-warning mc-page-subtitle q-mb-sm")

    ui.label("更新链接管理").classes("mc-section-title q-mb-xs")
    ui.label("为每个配置文件维护独立的下载链接，并支持单条刷新与编辑。").classes("mc-page-subtitle q-mb-sm")

    status_state: dict = {}
    mapping_container = ui.column().classes("w-full q-gutter-sm")

    def refresh_mapping_list():
        mapping_container.clear()
        mapping = storage.load_config_mapping()
        if not mapping:
            with mapping_container:
                ui.label("暂无更新链接配置").classes("text-caption text-grey")
            return

        with mapping_container:
            for item in mapping:
                _render_mapping_item(item, deployer, status_state, refresh_mapping_list)

    refresh_mapping_list()

    if deployer:
        with ui.card().classes("w-full q-mt-md q-pa-sm"):
            ui.label("新增更新链接").classes("text-subtitle2 font-bold q-mb-sm")
            with ui.row().classes("items-center q-gutter-sm w-full"):
                name_input = ui.input(placeholder="文件名 (例: config.xml)").props("dense outlined").classes("w-48")
                url_input = ui.input(placeholder="下载链接").props("dense outlined").classes("w-full")
                ui.button(
                    "添加",
                    icon="add",
                    on_click=lambda: _add_mapping(name_input.value, url_input.value, refresh_mapping_list),
                ).props("color=primary dense")

    ui.separator().classes("q-my-md")

    # 全量更新
    with ui.row().classes("items-center q-mb-md"):
        ui.button("全量更新", icon="cloud_download",
                  on_click=lambda: _run_full_update(status_state, refresh_mapping_list)
                  ).props("color=primary")
        ui.label("从所有配置链接爬取最新版本文件").classes("mc-page-subtitle")

    # 定时配置
    if deployer:
        ui.label("定时更新配置").classes("mc-section-title q-mt-md")
        schedule_config = storage.load_schedule()
        with ui.row().classes("items-center"):
            enabled_switch = ui.switch("启用定时更新", value=schedule_config.get("enabled", False))
            interval_input = ui.number(
                value=schedule_config.get("interval_hours", 24),
                min=1, step=1,
            ).props("dense outlined").classes("w-32")
            ui.select({"hours": "小时", "days": "天"}, value="hours").props("dense outlined").classes("w-24")
            ui.button("保存",
                      on_click=lambda: _save_schedule(enabled_switch.value, interval_input.value)
                      ).props("dense color=primary")

    ui.separator().classes("q-my-lg")
    ui.label("IP 对应表").classes("mc-section-title q-mb-xs")
    ui.label("维护操作 IP 与实际使用人之间的映射，用于自动归属修改备注。").classes("mc-page-subtitle q-mb-sm")

    ip_container = ui.column().classes("w-full q-gutter-sm")

    def refresh_ip_mapping():
        ip_container.clear()
        items = storage.load_ip_mapping()
        if not items:
            with ip_container:
                ui.label("暂无 IP 对应关系").classes("text-caption text-grey")
            return
        with ip_container:
            for item in items:
                _render_ip_mapping_item(item, deployer, refresh_ip_mapping)

    refresh_ip_mapping()

    if deployer:
        with ui.card().classes("w-full q-mt-md q-pa-sm"):
            ui.label("新增 IP 对应").classes("text-subtitle2 font-bold q-mb-sm")
            with ui.row().classes("items-center q-gutter-sm w-full"):
                ip_input = ui.input(placeholder="IP 地址").props("dense outlined").classes("w-48")
                name_input = ui.input(placeholder="人员姓名").props("dense outlined").classes("w-48")
                ui.button(
                    "添加",
                    icon="add",
                    on_click=lambda: _add_ip_mapping(ip_input.value, name_input.value, refresh_ip_mapping),
                ).props("color=primary dense")

    ui.separator().classes("q-my-lg")
    ui.label("管理员列表").classes("mc-section-title q-mb-xs")
    ui.label("维护拥有审阅修改和文件删除权限的管理员名单。").classes("mc-page-subtitle q-mb-sm")

    admin_container = ui.column().classes("w-full q-gutter-sm")

    def refresh_admin_users():
        admin_container.clear()
        items = storage.load_admin_users()
        if not items:
            with admin_container:
                ui.label("暂无管理员").classes("text-caption text-grey")
            return
        with admin_container:
            for item in items:
                _render_admin_user_item(item, deployer, refresh_admin_users)

    refresh_admin_users()

    if deployer:
        with ui.card().classes("w-full q-mt-md q-pa-sm"):
            ui.label("新增管理员").classes("text-subtitle2 font-bold q-mb-sm")
            with ui.row().classes("items-center q-gutter-sm w-full"):
                admin_input = ui.input(placeholder="管理员姓名").props("dense outlined").classes("w-48")
                ui.button(
                    "添加",
                    icon="add",
                    on_click=lambda: _add_admin_user(admin_input.value, refresh_admin_users),
                ).props("color=primary dense")


def _render_mapping_item(item: dict, deployer: bool, status_state: dict, refresh_cb):
    name = item.get("name", "")
    url = item.get("url", "")
    file_time = storage.get_file_update_date(name) if name else None
    status_info = status_state.get(name) or {}
    status = status_info.get("status")
    status_label = status_info.get("label")
    status_color = status_info.get("color")
    status_reason = status_info.get("reason")

    with ui.card().classes("w-full q-pa-sm"):
        with ui.row().classes("items-start justify-between w-full q-gutter-sm"):
            with ui.column().classes("q-gutter-xs"):
                with ui.row().classes("items-center q-gutter-xs"):
                    ui.badge(name or "-", color="blue")
                    if file_time:
                        ui.label(f"本地更新时间: {file_time}").classes("text-caption text-grey")
                if url:
                    ui.label(url).classes("text-body2 break-all")
                else:
                    ui.label("未配置下载链接").classes("text-caption text-grey")
                if status and status_label:
                    with ui.row().classes("items-center q-gutter-xs"):
                        ui.badge(status_label, color=status_color or "grey")
                        if status_reason:
                            ui.label(status_reason).classes("text-caption text-grey")

            if deployer:
                with ui.row().classes("items-center justify-end q-gutter-xs wrap"):
                    ui.button(
                        icon="refresh",
                        on_click=lambda n=name: _run_single_update(n, status_state, refresh_cb),
                    ).props("flat round dense color=primary")
                    ui.button(
                        icon="edit",
                        on_click=lambda it=item: _show_edit_mapping_dialog(it, status_state, refresh_cb),
                    ).props("flat round dense color=primary")
                    ui.button(
                        icon="history",
                        on_click=lambda it=item: _show_record_link_dialog(it),
                    ).props("flat round dense")
                    ui.button(
                        icon="delete",
                        on_click=lambda n=name: _show_delete_mapping_dialog(n, status_state, refresh_cb),
                    ).props("flat round dense color=red")


def _add_mapping(name: str, url: str, refresh_cb):
    if not name or not url:
        ui.notify("文件名和链接不能为空", type="warning")
        return
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    storage.add_config_mapping(name, url)
    ui.notify(f"已添加: {name}", type="positive")
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

        def do_save():
            new_name = (name_input.value or "").strip()
            new_url = (url_input.value or "").strip()
            if not new_name or not new_url:
                ui.notify("文件名和链接不能为空", type="warning")
                return
            try:
                ok = storage.update_config_mapping(
                    original_name,
                    new_name,
                    new_url,
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


def _save_schedule(enabled: bool, interval: float):
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    sched.update_schedule_config(enabled, interval)
    ui.notify("定时配置已保存", type="positive")


def _render_ip_mapping_item(item: dict, deployer: bool, refresh_cb) -> None:
    ip = item.get("ip", "")
    name = item.get("name", "")
    updated_at = item.get("updated_at", "")

    with ui.card().classes("w-full q-pa-sm"):
        with ui.row().classes("items-start justify-between w-full q-gutter-sm"):
            with ui.column().classes("q-gutter-xs"):
                with ui.row().classes("items-center q-gutter-xs"):
                    ui.badge(ip or "-", color="indigo")
                    if updated_at:
                        ui.label(f"更新时间: {updated_at}").classes("text-caption text-grey")
                ui.label(name or "-").classes("text-body2")

            if deployer:
                with ui.row().classes("items-center justify-end q-gutter-xs wrap"):
                    ui.button(
                        icon="edit",
                        on_click=lambda entry=item: _show_edit_ip_mapping_dialog(entry, refresh_cb),
                    ).props("flat round dense color=primary")
                    ui.button(
                        icon="delete",
                        on_click=lambda value=ip: _show_delete_ip_mapping_dialog(value, refresh_cb),
                    ).props("flat round dense color=red")


def _add_ip_mapping(ip: str, name: str, refresh_cb) -> None:
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    try:
        storage.upsert_ip_mapping(ip, name)
    except Exception as e:
        ui.notify(str(e) or "保存失败", type="negative")
        return
    ui.notify("IP 对应已保存", type="positive")
    refresh_cb()
    ui.run_javascript("location.reload()")


def _show_edit_ip_mapping_dialog(item: dict, refresh_cb) -> None:
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return

    original_ip = item.get("ip", "")
    with ui.dialog() as dialog, ui.card().classes("w-[520px] max-w-[95vw]"):
        ui.label("编辑 IP 对应").classes("text-h6 q-mb-sm")
        ip_input = ui.input(value=original_ip, placeholder="IP 地址").props("dense outlined").classes("w-full")
        name_input = ui.input(value=item.get("name", ""), placeholder="人员姓名").props("dense outlined").classes("w-full")

        def do_save():
            new_ip = str(ip_input.value or "").strip()
            new_name = str(name_input.value or "").strip()
            if not new_ip or not new_name:
                ui.notify("IP 和姓名不能为空", type="warning")
                return
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
            ui.run_javascript("location.reload()")

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
            ui.run_javascript("location.reload()")

        with ui.row().classes("w-full justify-end q-gutter-sm"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("删除", icon="delete", on_click=do_delete).props("color=negative")

        dialog.open()


def _render_admin_user_item(item: dict, deployer: bool, refresh_cb) -> None:
    name = item.get("name", "")
    updated_at = item.get("updated_at", "")

    with ui.card().classes("w-full q-pa-sm"):
        with ui.row().classes("items-start justify-between w-full q-gutter-sm"):
            with ui.column().classes("q-gutter-xs"):
                with ui.row().classes("items-center q-gutter-xs"):
                    ui.badge(name or "-", color="positive")
                    if updated_at:
                        ui.label(f"更新时间: {updated_at}").classes("text-caption text-grey")
            if deployer:
                with ui.row().classes("items-center justify-end q-gutter-xs wrap"):
                    ui.button(
                        icon="edit",
                        on_click=lambda entry=item: _show_edit_admin_user_dialog(entry, refresh_cb),
                    ).props("flat round dense color=primary")
                    ui.button(
                        icon="delete",
                        on_click=lambda value=name: _show_delete_admin_user_dialog(value, refresh_cb),
                    ).props("flat round dense color=red")


def _add_admin_user(name: str, refresh_cb) -> None:
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    try:
        storage.upsert_admin_user(name)
    except Exception as e:
        ui.notify(str(e) or "保存失败", type="negative")
        return
    ui.notify("管理员已保存", type="positive")
    refresh_cb()
    ui.run_javascript("location.reload()")


def _show_edit_admin_user_dialog(item: dict, refresh_cb) -> None:
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return

    original_name = item.get("name", "")
    with ui.dialog() as dialog, ui.card().classes("w-[520px] max-w-[95vw]"):
        ui.label("编辑管理员").classes("text-h6 q-mb-sm")
        name_input = ui.input(value=original_name, placeholder="管理员姓名").props("dense outlined").classes("w-full")

        def do_save():
            new_name = str(name_input.value or "").strip()
            if not new_name:
                ui.notify("管理员姓名不能为空", type="warning")
                return
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
            ui.run_javascript("location.reload()")

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
            ui.run_javascript("location.reload()")

        with ui.row().classes("w-full justify-end q-gutter-sm"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("删除", icon="delete", on_click=do_delete).props("color=negative")

        dialog.open()
