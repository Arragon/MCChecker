"""修改记录解析页"""

from fastapi import Request
from nicegui import ui

from app.core import storage, parser, parse_cache


@ui.page("/record_view")
def record_view(request: Request):
    config_name = (request.query_params.get("config") or "").strip()
    record_filename = (request.query_params.get("record") or "").strip()
    if not config_name or not record_filename:
        ui.label("参数缺失").classes("text-negative")
        return

    ui.label("修改记录解析").classes("mc-page-title q-mb-md")
    with ui.row().classes("items-center q-mb-md"):
        ui.badge(config_name, color="blue")
        ui.badge(record_filename, color="grey")

    content = storage.load_record_file(config_name, record_filename)
    if content is None:
        ui.label("修改记录文件不存在").classes("text-negative")
        return

    source_path = storage.get_record_path(config_name, record_filename)
    tree = parse_cache.load_tree(source_path)
    if tree is None:
        try:
            tree = parser.parse_file(content, record_filename)
            parse_cache.save_tree(source_path, tree)
        except ValueError:
            ui.label("该修改记录无法作为 XML/JSON 解析，已展示原始内容").classes("mc-page-subtitle q-mb-sm")
            text = content.decode("utf-8", errors="replace")
            ui.code(text).classes("w-full")
            return

    with ui.card().classes("w-full overflow-auto fav-tree"):
        for child in tree.get("children", []):
            _render_node(child, depth=0)


def _render_node(node: dict, depth: int = 0):
    label = node.get("label", "")
    value = node.get("value")
    children = node.get("children", []) or []
    level_colors = ["dark", "grey-9", "grey-8", "grey-7", "grey-6"]
    color = level_colors[min(depth, len(level_colors) - 1)]
    depth_class = f"depth-{min(depth, 3)}" if depth > 0 else ""

    if children:
        with ui.expansion(value=True).classes(f"fav-node {depth_class} w-full"):
            with ui.row().classes("items-center fav-node-header w-full no-wrap"):
                ui.icon("description", size="xs", color="grey-4").classes("q-mr-xs")
                if value is not None:
                    ui.label(f"{label} = {value}").classes(f"font-mono text-body2 text-{color}")
                else:
                    ui.label(label).classes(f"text-body2 text-weight-medium text-{color}")
            for child in children:
                _render_node(child, depth + 1)
    else:
        with ui.row().classes(f"items-center q-py-xs fav-leaf fav-node {depth_class} w-full"):
            ui.icon("description", size="xs", color="grey-4").classes("q-mr-xs")
            if value is not None:
                ui.label(f"{label} = {value}").classes("font-mono text-body2")
            else:
                ui.label(label).classes("font-mono text-body2 text-grey")
