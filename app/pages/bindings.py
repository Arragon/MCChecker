"""变量绑定配置页面 - 表格布局 + CRUD 局部刷新"""

from nicegui import ui

from app.core import storage
from app.utils.auth import is_deployer


def render_bindings_page(deployer: bool):
    """渲染变量绑定配置页面"""
    ui.label("变量绑定配置").classes("mc-page-title q-mb-sm")

    if not deployer:
        ui.label("访客仅可查看绑定关系，无法修改").classes("text-warning mc-page-subtitle q-mb-sm")

    # ---- 绑定列表（表格） ----
    bindings_container = ui.column().classes("w-full")

    def refresh_bindings():
        bindings_container.clear()
        bindings = storage.load_bindings()
        if not bindings:
            with bindings_container:
                ui.label("暂无绑定关系").classes("text-caption text-grey")
            return
        with bindings_container:
            with ui.element("div").classes("mc-mgmt-table w-full"):
                with ui.element("div").classes("mc-mgmt-thead"):
                    with ui.row().classes("items-center w-full no-wrap"):
                        ui.element("div").text("绑定组").classes("mc-mgmt-th").style("flex: 2")
                        ui.element("div").text("变量").classes("mc-mgmt-th").style("flex: 4")
                        if deployer:
                            ui.element("div").text("操作").classes("mc-mgmt-th").style("flex: 1")

                for b in bindings:
                    _render_binding_row(b, deployer, refresh_bindings)

    refresh_bindings()

    # ---- 新增绑定组 ----
    if deployer:
        ui.separator().classes("q-my-md")
        ui.label("新增绑定组").classes("mc-section-title q-mb-xs")
        group_name_input = ui.input(placeholder="绑定组名称").props("dense outlined").classes("w-64")
        group_err = ui.label("").classes("text-negative text-caption hidden")
        variables_container = ui.column().classes("w-full")
        temp_rows = []

        def add_var_row():
            files = storage.list_config_files()
            with variables_container:
                with ui.row().classes("items-center q-gutter-sm") as row:
                    file_input = ui.select(
                        files,
                        value=files[0] if files else None,
                    ).props("dense outlined").classes("w-48")
                    path_input = ui.input(placeholder="变量路径 (例: /root/timeout)").props("dense outlined").classes("w-80")
                    entry = {"row": row, "file": file_input, "path": path_input, "removed": False}

                    def remove_entry(e=entry):
                        e["removed"] = True
                        e["row"].classes(add="hidden")
                        e["row"].clear()

                    ui.button(icon="remove",
                              on_click=remove_entry
                              ).props("flat round dense color=negative")
                    temp_rows.append(entry)

        def collect_vars():
            items = []
            for e in temp_rows:
                if e.get("removed"):
                    continue
                file_val = e["file"].value if e.get("file") else ""
                path_val = (e["path"].value or "").strip() if e.get("path") else ""
                if file_val and path_val:
                    items.append({"file": file_val, "path": path_val})
            return items

        ui.button("添加变量", icon="add", on_click=add_var_row).props("dense").classes("q-mb-sm")

        def do_save_binding():
            gname = (group_name_input.value or "").strip()
            if not gname:
                group_err.text = "组名不能为空"
                group_err.classes(remove="hidden")
                return
            # duplicate check
            existing = storage.load_bindings()
            if any(b.get("group_name") == gname for b in existing):
                group_err.text = "该绑定组名已存在"
                group_err.classes(remove="hidden")
                return
            group_err.classes(add="hidden")
            variables = collect_vars()
            if not variables:
                ui.notify("至少添加一个有效变量", type="warning")
                return
            storage.add_binding(gname, variables)
            ui.notify(f"已保存: {gname}", type="positive")
            group_name_input.value = ""
            # 清空变量行
            for e in temp_rows:
                e["row"].clear()
            temp_rows.clear()
            variables_container.clear()
            refresh_bindings()

        ui.button("保存绑定组", icon="save",
                  on_click=do_save_binding
                  ).props("color=primary dense")


def _render_binding_row(b: dict, deployer: bool, refresh_cb):
    group_name = b.get("group_name", "")
    variables = b.get("variables", [])

    # 变量摘要
    var_parts = []
    for v in variables:
        f = v.get("file", "")
        p = v.get("path", "")
        var_parts.append(f"{f}:{p}" if f else p)
    var_summary = ", ".join(var_parts) if var_parts else "-"

    with ui.element("div").classes("mc-mgmt-row"):
        with ui.row().classes("items-center w-full no-wrap"):
            ui.element("div").classes("mc-mgmt-td text-weight-medium").style("flex: 2").text(group_name or "-")
            ui.element("div").classes("mc-mgmt-td font-mono text-caption").style("flex: 4").text(var_summary)
            if deployer:
                td_actions = ui.element("div").classes("mc-mgmt-td").style("flex: 1")
                with td_actions:
                    ui.button(icon="delete",
                              on_click=lambda gn=group_name: _remove_binding(gn, refresh_cb)
                              ).props("flat round dense color=negative").tooltip("删除")


def _remove_binding(group_name: str, refresh_cb):
    if not is_deployer():
        ui.notify("权限不足", type="negative")
        return
    storage.remove_binding(group_name)
    ui.notify(f"已删除: {group_name}", type="positive")
    refresh_cb()

