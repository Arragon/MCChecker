"""全量配置管理页面"""

from nicegui import ui

from app.core import storage, scheduler as sched
from app.utils.auth import is_deployer


def render_management_page(deployer: bool):
    """渲染全量配置管理页面"""
    ui.label("全量配置管理").classes("text-h6 q-mb-md")

    if not deployer:
        ui.label("访客仅可查看配置映射表，无法修改").classes("text-warning q-mb-md")

    # 映射表
    mapping = storage.load_config_mapping()
    columns = [
        {"name": "name", "label": "文件名", "field": "name", "align": "left"},
        {"name": "url", "label": "下载链接", "field": "url", "align": "left"},
        {"name": "actions", "label": "操作", "field": "actions", "align": "center"},
    ]
    rows = [{"name": m["name"], "url": m["url"]} for m in mapping]

    with ui.table(columns=columns, rows=rows).classes("w-full") as table:
        table.props("flat bordered dense")

    # 添加映射
    if deployer:
        with ui.row().classes("items-center q-mt-md"):
            name_input = ui.input(placeholder="文件名 (例: config.xml)").props("dense outlined").classes("w-48")
            url_input = ui.input(placeholder="下载链接").props("dense outlined").classes("w-96")
            ui.button("添加", icon="add",
                      on_click=lambda: _add_mapping(name_input.value, url_input.value)
                      ).props("color=primary dense")

    ui.separator().classes("q-my-md")

    # 全量更新
    with ui.row().classes("items-center q-mb-md"):
        ui.button("全量更新", icon="cloud_download",
                  on_click=lambda: _run_full_update()
                  ).props("color=primary")
        ui.label("从所有配置链接爬取最新版本文件").classes("text-caption text-grey")

    # 定时配置
    if deployer:
        ui.label("定时更新配置").classes("text-subtitle1 q-mt-md")
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


def _add_mapping(name: str, url: str):
    if not name or not url:
        ui.notify("文件名和链接不能为空", type="warning")
        return
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    storage.add_config_mapping(name, url)
    ui.notify(f"已添加: {name}", type="positive")


def _run_full_update():
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    result = sched.run_full_update()
    ui.notify(f"更新完成: 成功 {result['success']}, 失败 {result['failed']}", type="info")


def _save_schedule(enabled: bool, interval: float):
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    sched.update_schedule_config(enabled, interval)
    ui.notify("定时配置已保存", type="positive")
