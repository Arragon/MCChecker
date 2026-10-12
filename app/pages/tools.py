"""工具菜单配置页面 - 表格布局 + CRUD 局部刷新"""

from nicegui import ui

from app.core import storage
from app.utils.auth import is_deployer
from app.utils.helpers import safe_external_url


def render_tools_page(deployer: bool):
    """渲染工具菜单配置页面"""
    ui.label("工具菜单配置").classes("mc-page-title q-mb-sm")

    if not deployer:
        ui.label("访客仅可查看工具列表，无法修改").classes("text-warning mc-page-subtitle q-mb-sm")

    # ---- 工具列表（表格） ----
    tools_container = ui.column().classes("w-full")

    def refresh_tools():
        tools_container.clear()
        tools = storage.load_tools()
        if not tools:
            with tools_container:
                ui.label("暂无工具配置").classes("text-caption text-grey")
            return
        with tools_container:
            with ui.element("div").classes("mc-mgmt-table w-full"):
                with ui.element("div").classes("mc-mgmt-thead"):
                    with ui.row().classes("items-center w-full no-wrap"):
                        ui.element("div").text("名称").classes("mc-mgmt-th").style("flex: 2")
                        ui.element("div").text("介绍").classes("mc-mgmt-th").style("flex: 3")
                        ui.element("div").text("地址").classes("mc-mgmt-th").style("flex: 3")
                        if deployer:
                            ui.element("div").text("操作").classes("mc-mgmt-th").style("flex: 1")

                for tool in tools:
                    _render_tool_row(tool, deployer, refresh_tools)

    refresh_tools()

    # ---- 添加工具 ----
    if deployer:
        ui.separator().classes("q-my-md")
        ui.label("添加工具").classes("mc-section-title q-mb-xs")
        with ui.row().classes("items-center q-gutter-sm w-full flex-wrap"):
            name_input = ui.input(placeholder="工具名称").props("dense outlined").classes("w-48")
            desc_input = ui.input(placeholder="工具介绍/备注").props("dense outlined").classes("w-64")
            url_input = ui.input(placeholder="访问地址").props("dense outlined").classes("w-64")
            name_err = ui.label("").classes("text-negative text-caption hidden")
            url_err = ui.label("").classes("text-negative text-caption hidden")

            def do_add_tool():
                name_val = (name_input.value or "").strip()
                url_val = (url_input.value or "").strip()
                has_err = False
                if not name_val:
                    name_err.text = "名称不能为空"
                    name_err.classes(remove="hidden")
                    has_err = True
                else:
                    name_err.classes(add="hidden")
                if not url_val:
                    url_err.text = "地址不能为空"
                    url_err.classes(remove="hidden")
                    has_err = True
                elif not safe_external_url(url_val):
                    url_err.text = "链接格式无效，仅支持 http/https"
                    url_err.classes(remove="hidden")
                    has_err = True
                else:
                    url_err.classes(add="hidden")
                if has_err:
                    return
                storage.add_tool(name_val, (desc_input.value or "").strip(), url_val)
                ui.notify(f"已添加: {name_val}", type="positive")
                name_input.value = ""
                desc_input.value = ""
                url_input.value = ""
                refresh_tools()

            ui.button("添加", icon="add", on_click=do_add_tool).props("color=primary dense")


def _render_tool_row(tool: dict, deployer: bool, refresh_cb):
    name = tool.get("name", "")
    desc = tool.get("description", "")
    url = tool.get("url", "")

    with ui.element("div").classes("mc-mgmt-row"):
        with ui.row().classes("items-center w-full no-wrap"):
            ui.element("div").classes("mc-mgmt-td text-weight-medium").style("flex: 2").text(name or "-")
            ui.element("div").classes("mc-mgmt-td text-grey-7").style("flex: 3").text(desc or "-")
            ui.element("div").classes("mc-mgmt-td font-mono text-caption").style("flex: 3").text(url or "-")
            if deployer:
                td_actions = ui.element("div").classes("mc-mgmt-td").style("flex: 1")
                with td_actions:
                    with ui.row().classes("items-center q-gutter-xs"):
                        ui.button(icon="edit",
                                  on_click=lambda t=tool: _edit_tool_dialog(t, refresh_cb)
                                  ).props("flat round dense color=primary").tooltip("编辑")
                        ui.button(icon="delete",
                                  on_click=lambda n=name: _remove_tool(n, refresh_cb)
                                  ).props("flat round dense color=negative").tooltip("删除")


def _add_tool(name: str, desc: str, url: str):
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    if not name or not url:
        ui.notify("名称和地址不能为空", type="warning")
        return
    if not safe_external_url(url):
        ui.notify("链接格式无效，仅支持 http/https 链接", type="warning")
        return
    storage.add_tool(name, desc, url)
    ui.notify(f"已添加: {name}", type="positive")


def _remove_tool(name: str, refresh_cb=None):
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    storage.remove_tool(name)
    ui.notify(f"已删除: {name}", type="positive")
    if refresh_cb:
        refresh_cb()


def _edit_tool_dialog(tool: dict, refresh_cb=None):
    """编辑工具对话框"""
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("编辑工具").classes("text-h6")
        name_input = ui.input(value=tool["name"]).props("dense outlined").classes("w-full")
        desc_input = ui.input(value=tool.get("description", "")).props("dense outlined").classes("w-full")
        url_input = ui.input(value=tool.get("url", "")).props("dense outlined").classes("w-full")
        err_label = ui.label("").classes("text-negative text-caption hidden")

        def save():
            if not is_deployer():
                ui.notify("权限不足", type="negative")
                return
            url_val = (url_input.value or "").strip()
            if not url_val:
                err_label.text = "地址不能为空"
                err_label.classes(remove="hidden")
                return
            if not safe_external_url(url_val):
                err_label.text = "链接格式无效，仅支持 http/https"
                err_label.classes(remove="hidden")
                return
            err_label.classes(add="hidden")
            storage.update_tool(tool["name"], name_input.value, desc_input.value, url_val)
            ui.notify("已更新", type="positive")
            dialog.close()
            if refresh_cb:
                refresh_cb()

        with ui.row().classes("w-full justify-end"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("保存", on_click=save).props("color=primary")

        dialog.open()

