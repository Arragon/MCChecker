"""工具菜单配置页面"""

from nicegui import ui

from app.core import storage
from app.utils.auth import is_deployer


def render_tools_page(deployer: bool):
    """渲染工具菜单配置页面"""
    ui.label("工具菜单配置").classes("text-h6 q-mb-md")

    if not deployer:
        ui.label("访客仅可查看工具列表，无法修改").classes("text-warning q-mb-md")

    tools = storage.load_tools()

    if tools:
        for tool in tools:
            with ui.card().classes("w-full q-mb-sm"):
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.column().classes("flex-1"):
                        ui.label(tool["name"]).classes("text-subtitle1 font-bold")
                        ui.label(tool.get("description", "")).classes("text-caption text-grey")
                        ui.label(f"地址: {tool.get('url', '')}").classes("text-caption font-mono")
                    if deployer:
                        with ui.row():
                            ui.button(icon="edit",
                                      on_click=lambda t=tool: _edit_tool_dialog(t)
                                      ).props("flat round dense")
                            ui.button(icon="delete",
                                      on_click=lambda n=tool["name"]: _remove_tool(n)
                                      ).props("flat round dense color=red")
    else:
        ui.label("暂无工具配置").classes("text-caption text-grey")

    if deployer:
        ui.separator().classes("q-my-md")
        ui.label("添加工具").classes("text-subtitle1")
        name_input = ui.input(placeholder="工具名称").props("dense outlined").classes("w-64")
        desc_input = ui.input(placeholder="工具介绍/备注").props("dense outlined").classes("w-96")
        url_input = ui.input(placeholder="访问地址").props("dense outlined").classes("w-96")
        ui.button("添加", icon="add",
                  on_click=lambda: _add_tool(name_input.value, desc_input.value, url_input.value)
                  ).props("color=primary dense")


def _add_tool(name: str, desc: str, url: str):
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    if not name or not url:
        ui.notify("名称和地址不能为空", type="warning")
        return
    storage.add_tool(name, desc, url)
    ui.notify(f"已添加: {name}", type="positive")


def _remove_tool(name: str):
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    storage.remove_tool(name)
    ui.notify(f"已删除: {name}", type="positive")


def _edit_tool_dialog(tool: dict):
    """编辑工具对话框"""
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("编辑工具").classes("text-h6")
        name_input = ui.input(value=tool["name"]).props("dense outlined").classes("w-full")
        desc_input = ui.input(value=tool.get("description", "")).props("dense outlined").classes("w-full")
        url_input = ui.input(value=tool.get("url", "")).props("dense outlined").classes("w-full")

        def save():
            if not is_deployer():
                ui.notify("权限不足", type="negative")
                return
            storage.update_tool(tool["name"], name_input.value, desc_input.value, url_input.value)
            ui.notify("已更新", type="positive")
            dialog.close()

        with ui.row().classes("w-full justify-end"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("保存", on_click=save).props("color=primary")

        dialog.open()
