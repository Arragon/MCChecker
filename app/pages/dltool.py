"""DL 快捷计算工具 —— UI 页面"""

import json

from nicegui import ui

from app.core import dltool as engine
from app.core import storage, parser


_COEFF_LABELS = [
    ("k1", "k₁"), ("k2", "k₂"), ("k3", "k₃"),
    ("k4", "k₄"), ("k5", "k₅"), ("k6", "k₆"),
    ("k7", "k₇"), ("k8", "k₈"), ("b",  "b"),
]

_ITEM_IDS = ["pp_energy", "rp_energy", "rp_width"]

_PAGE_CSS = """
<style>
.dl-page {
    font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
    max-width: 1000px;
    margin: 0 auto;
}
.dl-page .dl-card {
    border-radius: 10px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    border: 1px solid #e8e8e8;
    transition: box-shadow 0.2s;
}
.dl-page .dl-card:hover {
    box-shadow: 0 2px 8px rgba(0,0,0,0.10);
}
/* ---- 系数网格 ---- */
.dl-page .coeff-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 6px 16px;
}
.dl-page .coeff-grid .coeff-cell {
    display: flex;
    align-items: baseline;
    gap: 4px;
}
.dl-page .coeff-grid .coeff-cell label {
    font-size: 0.78rem;
    color: #666;
    white-space: nowrap;
    min-width: 24px;
}
.dl-page .coeff-grid .coeff-cell .q-field {
    flex: 1;
    min-width: 0;
}
.dl-page .coeff-grid input {
    text-align: center;
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 0.88rem !important;
}
.dl-page .readonly input {
    background: #f8f8f8 !important;
    color: #555 !important;
}
.dl-page .calc-section {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
}
.dl-page .result-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.88rem;
}
.dl-page .result-table th {
    background: #f5f5f5;
    padding: 6px 12px;
    text-align: left;
    font-weight: 600;
    border-bottom: 2px solid #ddd;
}
.dl-page .result-table td {
    padding: 5px 12px;
    border-bottom: 1px solid #eee;
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
}
.dl-page .result-table tr:hover td {
    background: #fafafa;
}
.dl-page .alert-badge {
    display: inline-block;
    background: #FFF3E0;
    color: #E65100;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.78rem;
    font-weight: 500;
}
.dl-page .calc-panel {
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 12px;
}
.dl-page .calc-panel .calc-title {
    font-weight: 600;
    font-size: 0.92rem;
}
/* ---- 淡化提示文本 ---- */
.dl-page ::placeholder {
    color: #bbb !important;
    opacity: 0.7;
}
.dl-page .q-field__label {
    color: #bbb !important;
}
</style>
"""


def render_dltool_page(on_refresh=None):
    """渲染 DL 快捷计算工具页面"""
    cfg = engine.load_config()
    cfg["_inputs"] = []

    ui.add_head_html(_PAGE_CSS)

    with ui.column().classes("dl-page w-full q-pa-md"):
        # ---- 页面标题 + 控制按钮 ----
        with ui.row().classes("items-center q-mb-md"):
            ui.icon("functions", size="md", color="blue-8").classes("q-mr-sm")
            ui.label("DL 快捷计算").classes("text-h5 font-bold")
            ui.space()
            _render_edit_buttons(cfg, on_refresh)

        editing = cfg.get("editing", False)

        # 检查是否有任何绑定
        has_any_binding = any(
            cfg["items"][iid].get("binding", {}).get("source_file")
            for iid in _ITEM_IDS
        )
        if has_any_binding:
            parts = []
            for iid in _ITEM_IDS:
                b = cfg["items"][iid].get("binding", {})
                if b.get("source_file"):
                    parts.append(f"{cfg['items'][iid]['name']}: {b['source_file']} → {b.get('variable_path', '?')}")
            ui.label("系数绑定: " + " | ".join(parts)).classes("text-caption text-blue-8 q-mb-sm")

        ui.separator().classes("q-mb-md")

        # ---- 三个计算项 ----
        for item_id in _ITEM_IDS:
            item = cfg["items"][item_id]
            _render_calc_item(cfg, item_id, item, editing, on_refresh)


def _render_edit_buttons(cfg: dict, on_refresh=None):
    editing = cfg.get("editing", False)
    if not editing:
        ui.button("修改", icon="edit", on_click=lambda: _enter_edit_mode(cfg, on_refresh)) \
            .props("flat color=primary")
    else:
        ui.button("保存", icon="save", on_click=lambda: _save_and_exit(cfg, on_refresh)) \
            .props("flat color=positive")
        ui.button("取消", icon="close", on_click=lambda: _cancel_edit(cfg, on_refresh)) \
            .props("flat color=grey-5")


def _enter_edit_mode(cfg: dict, on_refresh=None):
    cfg["editing"] = True
    engine.save_config(cfg)
    ui.notify("已进入编辑模式", type="info")
    if on_refresh:
        on_refresh()


def _save_and_exit(cfg: dict, on_refresh=None):
    _collect_inputs(cfg)
    cfg["editing"] = False
    engine.save_config(cfg)
    ui.notify("配置已保存", type="positive")
    if on_refresh:
        on_refresh()


def _cancel_edit(cfg: dict, on_refresh=None):
    fresh = engine.load_config()
    cfg.clear()
    cfg.update(fresh)
    cfg["editing"] = False
    engine.save_config(cfg)
    ui.notify("已取消修改", type="info")
    if on_refresh:
        on_refresh()


def _collect_inputs(cfg: dict):
    for inp in cfg.get("_inputs", []):
        key = getattr(inp, "_coeff_key", None)
        section = getattr(inp, "_coeff_section", None)
        if key and section:
            if "." in section:
                parts = section.split(".")
                d = cfg
                for p in parts:
                    d = d.setdefault(p, {})
                d[key] = inp.value or 0.0
            else:
                cfg[section][key] = inp.value or 0.0


def _open_binding_dialog(cfg: dict, item_id: str, on_refresh=None):
    """打开指定计算项的系数绑定配置对话框"""
    item = cfg["items"][item_id]
    binding = item.get("binding", {})

    with ui.dialog() as dialog, ui.card().classes("w-[520px]"):
        ui.label(f"系数绑定 — {item['name']}").classes("text-h6 q-mb-md")

        files = storage.list_config_files()
        file_opts = {f: f for f in files}
        file_val = binding.get("source_file", "")
        ui.label("目标配置文件").classes("text-caption text-grey")
        file_select = ui.select(
            options=file_opts, value=file_val if file_val in file_opts else None,
            label="选择文件"
        ).props("dense outlined").classes("w-full")

        ui.label("目标变量路径").classes("text-caption text-grey q-mt-sm")
        path_input = ui.input(
            value=binding.get("variable_path", ""),
            placeholder="例如: root.PPParams",
            label="变量路径"
        ).props("dense outlined").classes("w-full")

        ui.label("字段 → 系数 映射").classes("text-caption text-grey q-mt-sm")
        ui.label("每行: 字段名=系数名 (如 m1=k1, n=b)").classes("text-caption text-grey-5")

        field_map = binding.get("field_map", {})
        map_text = "\n".join(f"{k}={v}" for k, v in field_map.items())
        map_input = ui.textarea(
            value=map_text, placeholder="m1=k1\nm2=k2\nn=b", label="映射关系"
        ).props("dense outlined").classes("w-full").style("height: 100px")

        def apply():
            item["binding"] = item.get("binding", {})
            item["binding"]["source_file"] = file_select.value or ""
            item["binding"]["variable_path"] = path_input.value or ""
            new_map = {}
            for line in map_input.value.strip().split("\n"):
                line = line.strip()
                if "=" in line:
                    fld, ck = line.split("=", 1)
                    fld, ck = fld.strip(), ck.strip()
                    if fld and ck:
                        new_map[fld] = ck
            item["binding"]["field_map"] = new_map
            _try_extract_coeffs_for_item(cfg, item_id)
            engine.save_config(cfg)
            dialog.close()
            ui.notify(f"{item['name']} 系数已提取", type="positive")
            if on_refresh:
                on_refresh()

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("应用并提取", on_click=apply).props("color=primary")

    dialog.open()


def _try_extract_coeffs_for_item(cfg: dict, item_id: str):
    """从绑定配置中提取系数到指定计算项"""
    item = cfg["items"][item_id]
    binding = item.get("binding", {})
    source_file = binding.get("source_file", "")
    variable_path = binding.get("variable_path", "")
    field_map = binding.get("field_map", {})
    if not source_file or not variable_path or not field_map:
        return

    content = storage.load_config_file(source_file)
    if content is None:
        ui.notify(f"文件 {source_file} 不存在", type="warning")
        return
    try:
        tree = parser.parse_file(content, source_file)
    except ValueError as e:
        ui.notify(f"解析失败: {e}", type="negative")
        return

    data = _find_variable_data(tree, variable_path)
    if data is None:
        ui.notify(f"在 {source_file} 中未找到 {variable_path}", type="warning")
        return

    coeffs = engine.parse_coeffs_from_data(data, field_map)
    item["coeffs"].update(coeffs)


def _find_variable_data(tree: dict, path: str):
    node = _find_node_in_tree(tree, path)
    if node is None:
        return None
    value = node.get("value")
    if value is not None:
        try:
            if isinstance(value, str):
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return None
    children = node.get("children", [])
    if children:
        data = {}
        for child in children:
            if child.get("value") is not None:
                try:
                    data[child["label"]] = float(child["value"])
                except (ValueError, TypeError):
                    data[child["label"]] = child["value"]
        return data
    return None


def _find_node_in_tree(node: dict, target_path: str):
    if node["id"] == target_path:
        return node
    for child in node.get("children", []):
        found = _find_node_in_tree(child, target_path)
        if found:
            return found
    return None


def _render_calc_item(cfg: dict, item_id: str, item: dict, editing: bool,
                      on_refresh=None):
    """渲染单个计算项"""
    item_name = item.get("name", item_id)

    with ui.expansion(value=True).classes("w-full q-mb-sm").props(
        f'header-class="bg-grey-1"'
    ):
        with ui.row().classes("items-center w-full"):
            ui.icon("calculate", size="sm", color="blue-6").classes("q-mr-sm")
            ui.label(item_name).classes("text-subtitle1 font-bold")
            ui.space()
            # 每个计算项独立的绑定按钮
            binding = item.get("binding", {})
            if binding.get("source_file"):
                ui.label(f"已绑定: {binding['source_file']}").classes("text-caption text-blue-8 q-mr-sm")
            ui.button("绑定", icon="link",
                      on_click=lambda iid=item_id: _open_binding_dialog(cfg, iid, on_refresh)) \
                .props("flat dense size=sm color=blue-7").tooltip("从配置文件提取系数")

        with ui.card().classes("dl-card q-pa-md w-full"):
            # ---- 系数网格 ----
            with ui.row().classes("items-center q-mb-sm"):
                ui.icon("tune", size="xs", color="grey-6").classes("q-mr-xs")
                ui.label("函数系数").classes("text-body2 font-bold text-grey-7")

            with ui.element("div").classes("coeff-grid w-full q-mb-md"):
                for key, title in _COEFF_LABELS:
                    val = item["coeffs"].get(key, 0.0)
                    with ui.element("div").classes("coeff-cell"):
                        ui.label(title).classes("text-caption text-grey-6")
                        inp = ui.number(value=val, format="%.8g") \
                            .props("dense outlined hide-bottom-space") \
                            .classes(f"{'readonly' if not editing else ''}")
                        if not editing:
                            inp.props(add="readonly")
                        inp._coeff_key = key
                        inp._coeff_section = f"items.{item_id}.coeffs"
                        cfg["_inputs"].append(inp)

            if item_id == "pp_energy":
                with ui.row().classes("q-mb-md q-gutter-sm items-center"):
                    ui.icon("settings", size="xs", color="grey-6").classes("q-mr-xs")
                    ui.label("参数:").classes("text-caption text-grey-7")
                    trans_inp = ui.number(
                        value=item.get("trans_coeff", 1.0),
                        label="传输系数",
                        format="%.8g",
                    ).props("dense outlined size=sm").classes("w-32")
                    if not editing:
                        trans_inp.props(add="readonly")
                    trans_inp._coeff_key = "trans_coeff"
                    trans_inp._coeff_section = f"items.{item_id}"
                    cfg["_inputs"].append(trans_inp)

                    power_conv_inp = ui.number(
                        value=item.get("power_conv_coeff", 1.0),
                        label="功率转换系数",
                        format="%.8g",
                    ).props("dense outlined size=sm").classes("w-36")
                    if not editing:
                        power_conv_inp.props(add="readonly")
                    power_conv_inp._coeff_key = "power_conv_coeff"
                    power_conv_inp._coeff_section = f"items.{item_id}"
                    cfg["_inputs"].append(power_conv_inp)

            # ---- 范围设置 ----
            with ui.row().classes("q-mt-md q-mb-md q-gutter-sm items-center"):
                ui.icon("filter_alt", size="xs", color="grey-6").classes("q-mr-xs")
                ui.label("范围过滤:").classes("text-caption text-grey-7")
                x_min_inp = ui.number(value=item.get("x_min"), label="x min",
                                      format="%.4g").props("dense outlined size=sm").classes("w-20")
                if not editing:
                    x_min_inp.props(add="readonly")
                x_min_inp._coeff_key = "x_min"
                x_min_inp._coeff_section = f"items.{item_id}"
                cfg["_inputs"].append(x_min_inp)

                x_max_inp = ui.number(value=item.get("x_max"), label="x max",
                                      format="%.4g").props("dense outlined size=sm").classes("w-20")
                if not editing:
                    x_max_inp.props(add="readonly")
                x_max_inp._coeff_key = "x_max"
                x_max_inp._coeff_section = f"items.{item_id}"
                cfg["_inputs"].append(x_max_inp)

                ui.label("y").classes("text-caption text-grey-7 q-ml-sm")
                y_min_inp = ui.number(value=item.get("y_min"), label="y min",
                                      format="%.4g").props("dense outlined size=sm").classes("w-20")
                if not editing:
                    y_min_inp.props(add="readonly")
                y_min_inp._coeff_key = "y_min"
                y_min_inp._coeff_section = f"items.{item_id}"
                cfg["_inputs"].append(y_min_inp)

                y_max_inp = ui.number(value=item.get("y_max"), label="y max",
                                      format="%.4g").props("dense outlined size=sm").classes("w-20")
                if not editing:
                    y_max_inp.props(add="readonly")
                y_max_inp._coeff_key = "y_max"
                y_max_inp._coeff_section = f"items.{item_id}"
                cfg["_inputs"].append(y_max_inp)

            ui.separator().classes("q-mb-md")

            # ---- 正向 / 反向 并排 ----
            with ui.element("div").classes("calc-section w-full"):
                with ui.column().classes("calc-panel"):
                    ui.label("正向计算  x → y").classes("calc-title q-mb-sm")
                    fwd_input = ui.textarea(
                        label="输入 x 值", placeholder="1.0 2.0 3.0",
                    ).props("dense outlined").classes("w-full").style("height: 70px")

                    def do_fwd(item=item, inp=fwd_input):
                        _do_forward_calc(item_id, item, inp)

                    ui.button("计算", icon="arrow_forward", on_click=do_fwd) \
                        .props("flat color=primary dense").classes("q-mt-sm")

                with ui.column().classes("calc-panel"):
                    ui.label("反向求解  y → x").classes("calc-title q-mb-sm")
                    rev_input = ui.textarea(
                        label="输入 y 值", placeholder="100 200 300",
                    ).props("dense outlined").classes("w-full").style("height: 70px")

                    def do_rev(item=item, y_inp=rev_input):
                        _do_reverse_calc(item_id, item, y_inp)

                    ui.button("求解", icon="search", on_click=do_rev) \
                        .props("flat color=deep-orange-7 dense").classes("q-mt-sm")


def _parse_number_list(text: str) -> list:
    values = []
    if not text:
        return values
    for part in text.replace(",", " ").replace(";", " ").split():
        try:
            values.append(float(part))
        except ValueError:
            pass
    for line in text.split("\n"):
        for part in line.strip().replace(",", " ").split():
            try:
                v = float(part)
                if v not in values:
                    values.append(v)
            except ValueError:
                pass
    return values


def _scale_coeffs_for_item(item_id: str, item: dict, coeffs: dict) -> dict:
    if item_id != "pp_energy":
        return coeffs
    trans_coeff = item.get("trans_coeff", 1.0) or 1.0
    power_conv_coeff = item.get("power_conv_coeff", 1.0) or 1.0
    if power_conv_coeff == 0:
        ui.notify("功率转换系数不能为 0", type="warning")
        return {}
    scale = trans_coeff / power_conv_coeff
    return {k: v * scale for k, v in coeffs.items()}


def _do_forward_calc(item_id: str, item: dict, input_el):
    text = input_el.value
    if not text or not text.strip():
        ui.notify("请输入 x 值", type="warning")
        return
    x_vals = _parse_number_list(text)
    if not x_vals:
        ui.notify("未能解析有效的数值", type="warning")
        return

    coeffs = _get_item_coeffs(item)
    coeffs = _scale_coeffs_for_item(item_id, item, coeffs)
    if not coeffs:
        return
    x_range = (item.get("x_min"), item.get("x_max"))
    y_range = (item.get("y_min"), item.get("y_max"))
    results = engine.batch_calc(x_vals, coeffs, x_range, y_range)
    _show_results(results, "forward")


def _do_reverse_calc(item_id: str, item: dict, input_el):
    text = input_el.value
    if not text or not text.strip():
        ui.notify("请输入 y 值", type="warning")
        return
    y_vals = _parse_number_list(text)
    if not y_vals:
        ui.notify("未能解析有效的数值", type="warning")
        return

    coeffs = _get_item_coeffs(item)
    coeffs = _scale_coeffs_for_item(item_id, item, coeffs)
    if not coeffs:
        return
    x_range = (item.get("x_min"), item.get("x_max"))
    y_range = (item.get("y_min"), item.get("y_max"))
    # 反解时在配置的 x 范围内搜索根；未设置则用默认 ±100
    x_min = item.get("x_min") if item.get("x_min") is not None else -100.0
    x_max = item.get("x_max") if item.get("x_max") is not None else 100.0
    search_range = (x_min, x_max)

    results = engine.batch_reverse(y_vals, coeffs, x_range, y_range, search_range)
    _show_results(results, "reverse")


def _get_item_coeffs(item: dict) -> dict:
    return {k: item["coeffs"].get(k, 0.0) for k in engine.DEFAULT_COEFFS}


def _show_results(results: list, mode: str):
    if not results:
        ui.notify("无有效结果（可能被范围过滤或无解）", type="warning")
        return

    with ui.dialog() as dlg, ui.card().classes("w-[700px] max-h-[500px] overflow-auto"):
        title = "正向计算 结果" if mode == "forward" else "反向求解 结果"
        ui.label(f"{title} ({len(results)} 条)").classes("text-h6 q-mb-md")

        has_multi = any(r.get("multiple") for r in results)

        with ui.column().classes("w-full"):
            head = ('<thead><tr><th>输入 x</th><th>输出 y = f(x)</th></tr></thead>'
                    if mode == "forward" else
                    '<thead><tr><th>目标 y</th><th>解 x</th></tr></thead>')
            ui.html(f'<table class="result-table">{head}<tbody>', sanitize=False)

            for r in results:
                if mode == "forward":
                    ui.html(f'<tr><td>{r["x"]}</td><td>{r["y"]}</td></tr>', sanitize=False)
                else:
                    x_vals = r["x_values"]
                    if not x_vals:
                        ui.html(f'<tr><td>{r["y"]}</td>'
                                f'<td><span style="color:#E65100">无实数解</span></td></tr>',
                                sanitize=False)
                    elif r["multiple"]:
                        x_str = ", ".join(str(x) for x in x_vals)
                        ui.html(f'<tr><td>{r["y"]}</td><td>{x_str} '
                                f'<span class="alert-badge">一对多({len(x_vals)}解)</span></td></tr>',
                                sanitize=False)
                    else:
                        ui.html(f'<tr><td>{r["y"]}</td><td>{x_vals[0]}</td></tr>', sanitize=False)

            ui.html('</tbody></table>', sanitize=False)

        if has_multi:
            ui.label("存在一对多结果，请核对期望的输出范围").classes("text-caption text-orange-8 q-mt-sm")

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("关闭", on_click=dlg.close).props("flat")

    dlg.open()
