"""变量绑定配置页面"""

from nicegui import ui

from app.core import storage
from app.utils.auth import is_deployer


def render_bindings_page(deployer: bool):
    """渲染变量绑定配置页面"""
    ui.label("变量绑定配置").classes("text-h6 q-mb-md")

    if not deployer:
        ui.label("访客仅可查看绑定关系，无法修改").classes("text-warning q-mb-md")

    bindings = storage.load_bindings()

    if bindings:
        for b in bindings:
            with ui.card().classes("w-full q-mb-md"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label(b["group_name"]).classes("text-subtitle1 font-bold")
                    if deployer:
                        ui.button(icon="delete",
                                  on_click=lambda gn=b["group_name"]: _remove_binding(gn)
                                  ).props("flat round dense color=red")
                for v in b.get("variables", []):
                    with ui.row().classes("q-ml-md"):
                        ui.badge(v.get("file", ""), color="blue")
                        ui.label(v.get("path", "")).classes("font-mono")
    else:
        ui.label("暂无绑定关系").classes("text-caption text-grey")

    if deployer:
        ui.separator().classes("q-my-md")
        ui.label("新增绑定组").classes("text-subtitle1")
        group_name_input = ui.input(placeholder="绑定组名称").props("dense outlined").classes("w-64")
        variables_container = ui.column().classes("w-full")
        temp_vars = []

        def add_var_row():
            files = storage.list_config_files()
            with variables_container:
                with ui.row().classes("items-center") as row:
                    file_input = ui.select(
                        files,
                        value=files[0] if files else None,
                    ).props("dense outlined").classes("w-48")
                    path_input = ui.input(placeholder="变量路径 (例: /root/timeout)").props("dense outlined").classes("w-64")
                    ui.button(icon="remove",
                              on_click=lambda: _remove_var_row(temp_vars, row)
                              ).props("flat round dense color=red")
                    temp_vars.append({"file": file_input.value, "path": path_input.value})

        ui.button("添加变量", icon="add", on_click=add_var_row).props("dense")
        ui.button("保存绑定组",
                  on_click=lambda: _save_binding_group(group_name_input.value, temp_vars)
                  ).props("color=primary dense")


def _remove_binding(group_name: str):
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    storage.remove_binding(group_name)
    ui.notify(f"已删除: {group_name}", type="positive")


def _remove_var_row(temp_vars: list, row):
    temp_vars.clear()
    row.clear()


def _save_binding_group(group_name: str, variables: list):
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    if not group_name:
        ui.notify("组名不能为空", type="warning")
        return
    storage.add_binding(group_name, variables)
    ui.notify(f"已保存: {group_name}", type="positive")
