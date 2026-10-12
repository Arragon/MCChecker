"""DL 快捷计算工具 —— UI 页面"""

from __future__ import annotations

import hashlib
import html
import json
import os
import threading

from nicegui import ui

from app.core import dltool as engine
from app.core import storage, parser, parse_cache


# ---------------------------------------------------------------------------
# DLDraft — 会话草稿 (T17 / INH-629)
# ---------------------------------------------------------------------------

class DLDraft:
    """DL 会话草稿

    保存 editing state / inputs / temporary binding selection，
    与 authoritative config 隔离。commit 时校验 base_config_hash
    确保源配置未被其他客户端修改。
    """

    def __init__(self, base_config: dict):
        self.editing_state: dict = {}
        self.inputs: dict = {}  # coeff_key -> value
        self.temp_bindings: dict = {}  # item_id -> binding dict
        self.base_config_hash: str = self._compute_config_hash(base_config)

    @staticmethod
    def _compute_config_hash(cfg: dict) -> str:
        """计算 authoritative config 的 hash（排除运行时字段）"""
        clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
        raw = json.dumps(clean, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def capture_inputs(self, cfg: dict) -> None:
        """从 cfg 的 _inputs 捕获当前输入值"""
        for inp in cfg.get("_inputs", []):
            key = getattr(inp, "_coeff_key", None)
            section = getattr(inp, "_coeff_section", None)
            if key and section:
                self.inputs[f"{section}.{key}"] = inp.value

    def restore_inputs(self, cfg: dict) -> None:
        """恢复输入值到 cfg 的 _inputs"""
        for inp in cfg.get("_inputs", []):
            key = getattr(inp, "_coeff_key", None)
            section = getattr(inp, "_coeff_section", None)
            if key and section:
                stored = self.inputs.get(f"{section}.{key}")
                if stored is not None:
                    inp.value = stored
                    try:
                        inp.update()
                    except Exception:
                        pass

    def check_base_unchanged(self, cfg: dict) -> bool:
        """校验 authoritative config 自 draft 创建后未变"""
        current_hash = self._compute_config_hash(cfg)
        return current_hash == self.base_config_hash

    def commit(self, cfg: dict) -> bool:
        """提交草稿到 authoritative config

        Returns:
            bool: True 表示提交成功，False 表示 base config 已变（冲突）
        """
        if not self.check_base_unchanged(cfg):
            return False
        # 将 inputs 写入 cfg
        for full_key, value in self.inputs.items():
            parts = full_key.split(".")
            d = cfg
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            d[parts[-1]] = value if value is not None else 0.0
        # 将 temp_bindings 写入
        for item_id, binding in self.temp_bindings.items():
            cfg.setdefault("items", {}).setdefault(item_id, {})["binding"] = binding
        cfg["editing"] = False
        return True

    def cancel(self) -> None:
        """取消草稿：清空编辑状态，authoritative config 不变"""
        self.editing_state.clear()
        self.inputs.clear()
        self.temp_bindings.clear()


# ---------------------------------------------------------------------------
# 防重复提交锁 (T17 / INH-629)
# ---------------------------------------------------------------------------

_submit_locks: dict[str, bool] = {}
_submit_locks_guard = threading.Lock()


def submit_with_lock(operation_key: str, submit_fn):
    """防重复提交：同一 operation_key 同时只允许一次业务提交"""
    with _submit_locks_guard:
        if operation_key in _submit_locks:
            return None  # 已经在处理
        _submit_locks[operation_key] = True
    try:
        return submit_fn()
    finally:
        with _submit_locks_guard:
            _submit_locks.pop(operation_key, None)


_COEFF_LABELS = [
    ("k1", "k₁"), ("k2", "k₂"), ("k3", "k₃"),
    ("k4", "k₄"), ("k5", "k₅"), ("k6", "k₆"),
    ("k7", "k₇"), ("k8", "k₈"), ("b",  "b"),
]

_ITEM_IDS = ["pp_energy", "rp_energy", "rp_width"]


def render_dltool_page(on_refresh=None):
    """渲染 DL 快捷计算工具页面"""
    cfg = engine.load_config()
    cfg["_inputs"] = []

    with ui.column().classes("dl-page w-full q-pa-md"):
        # ---- 页面标题 + 控制按钮 ----
        with ui.row().classes("items-center q-mb-md"):
            ui.icon("functions", size="md", color="blue-8").classes("q-mr-sm")
            ui.label("DL 快捷计算").classes("mc-page-title")
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
        ui.button("保存", icon="save", on_click=lambda: submit_with_lock(
            f"dl_save_{id(cfg)}", lambda: _save_and_exit(cfg, on_refresh)
        )).props("flat color=positive")
        ui.button("取消", icon="close", on_click=lambda: submit_with_lock(
            f"dl_cancel_{id(cfg)}", lambda: _cancel_edit(cfg, on_refresh)
        )).props("flat color=grey-5")


def _enter_edit_mode(cfg: dict, on_refresh=None):
    cfg["editing"] = True
    # 创建草稿快照（T17 / INH-629）
    draft = DLDraft(cfg)
    cfg["_draft"] = draft
    engine.save_config(cfg)
    ui.notify("已进入编辑模式", type="info")
    if on_refresh:
        on_refresh()


def _save_and_exit(cfg: dict, on_refresh=None):
    draft = cfg.get("_draft")
    if draft is not None:
        # 捕获当前输入到草稿
        draft.capture_inputs(cfg)
        # 提交草稿（带 base config hash 校验）
        ok = draft.commit(cfg)
        if not ok:
            ui.notify("配置已被其他客户端修改，请刷新后重试", type="warning")
            return
    else:
        _collect_inputs(cfg)
        cfg["editing"] = False
    engine.save_config(cfg)
    cfg.pop("_draft", None)
    ui.notify("配置已保存", type="positive")
    if on_refresh:
        on_refresh()


def _cancel_edit(cfg: dict, on_refresh=None):
    draft = cfg.get("_draft")
    if draft is not None:
        # 取消草稿：authoritative config hash 不变
        draft.cancel()
        cfg.pop("_draft", None)
    # 重新加载原始配置
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

    source_path = storage.get_config_path(source_file)
    if not os.path.exists(source_path):
        ui.notify(f"文件 {source_file} 不存在", type="warning")
        return
    try:
        tree = parse_cache.load_tree(source_path)
        if tree is None:
            tree = parser.parse_path(source_path, source_file)
            parse_cache.save_tree(source_path, tree)
    except ValueError as e:
        ui.notify(f"解析失败: {e}", type="negative")
        return

    data = _find_variable_data(tree, variable_path)
    if data is None:
        ui.notify(f"在 {source_file} 中未找到 {variable_path}", type="warning")
        return

    coeffs = engine.parse_coeffs_from_data(data, field_map)
    item["coeffs"].update(coeffs)


def _refresh_coeffs_for_item(cfg: dict, item_id: str, on_refresh=None):
    item = cfg["items"][item_id]
    binding = item.get("binding", {})
    if not binding.get("source_file"):
        ui.notify("未设置系数绑定，无法刷新", type="warning")
        return

    before = dict(item.get("coeffs", {}))
    _try_extract_coeffs_for_item(cfg, item_id)
    after = item.get("coeffs", {})
    engine.save_config(cfg)

    for inp in cfg.get("_inputs", []):
        if getattr(inp, "_coeff_section", "") != f"items.{item_id}.coeffs":
            continue
        k = getattr(inp, "_coeff_key", None)
        if not k:
            continue
        if k in after:
            try:
                inp.value = after[k]
                inp.update()
            except Exception:
                pass

    if before == after:
        ui.notify("系数未变化（已是最新）", type="info")
    else:
        ui.notify("系数已刷新", type="positive")

    res = _detect_multi_for_item(item_id, item)
    if res and res.get("has_multi"):
        _show_multi_solution_dialog(res)

    if on_refresh and not cfg.get("editing", False):
        on_refresh()


def _detect_multi_for_item(item_id: str, item: dict) -> dict | None:
    coeffs = _get_item_coeffs(item)
    xr = (item.get("x_min"), item.get("x_max"))
    yr = (item.get("y_min"), item.get("y_max"))
    search_min = xr[0] if xr[0] is not None else -100.0
    search_max = xr[1] if xr[1] is not None else 100.0

    effective_yr = yr
    scale = 1.0
    if item_id == "pp_energy":
        info = _pp_scale_info(item)
        if not info:
            return None
        scale = info["scale"]
        y_min, y_max = yr
        base_min = _pp_to_base_y(y_min, scale) if y_min is not None else None
        base_max = _pp_to_base_y(y_max, scale) if y_max is not None else None
        effective_yr = (base_min, base_max)

    res = engine.detect_multi_solutions(
        coeffs=coeffs,
        x_range=xr,
        y_range=effective_yr,
        search_range=(search_min, search_max),
    )

    if item_id == "pp_energy" and scale != 1.0 and res.get("has_multi"):
        res = {
            **res,
            "cases": [
                {"y": float(c.get("y", 0.0)) * scale, "x_values": c.get("x_values") or []}
                for c in (res.get("cases") or [])
            ],
            "points": [
                {"x": p.get("x"), "y": float(p.get("y", 0.0)) * scale}
                for p in (res.get("points") or [])
            ],
        }
    return res


def _show_multi_solution_dialog(res: dict):
    cases = res.get("cases") or []
    if not cases:
        return
    with ui.dialog() as dlg, ui.card().classes("w-[760px] max-w-[95vw]"):
        with ui.row().classes("items-start justify-between w-full q-mb-sm"):
            with ui.column().classes("q-gutter-xs"):
                ui.label("多解提示").classes("text-h6")
                ui.label("检测到 y→x 存在一对多，多解可能导致反向求解结果不唯一。").classes("text-caption text-grey")
            ui.button(icon="close", on_click=dlg.close).props("flat round dense")

        with ui.card().classes("w-full q-pa-sm").style("border: 1px solid #fb923c; background: #fff7ed"):
            with ui.row().classes("items-center no-wrap"):
                ui.icon("warning", color="orange-9").classes("q-mr-sm")
                ui.label("建议缩小 x / y 范围，或在“反向求解结果”弹窗中选择最优解以确保唯一性。").classes("text-body2 text-orange-9")

        ui.label(f"多解点位（最多展示 {min(len(cases), 8)} 组 y 值）").classes("mc-section-title q-mt-md q-mb-sm")
        ui.html('<table class="result-table"><thead><tr><th>目标 y</th><th>解 x（同一 y 对应多个 x）</th></tr></thead><tbody>')
        for c in cases[:8]:
            xs = c.get("x_values") or []
            x_str = ", ".join(html.escape(str(round(float(x), 10))) for x in xs)
            ui.html(f"<tr><td>{html.escape(str(round(float(c.get('y', 0.0)), 10)))}</td><td>{x_str}</td></tr>")
        ui.html("</tbody></table>")

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("知道了", on_click=dlg.close).props("color=primary")
    dlg.open()


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
                ui.button(
                    icon="refresh",
                    on_click=lambda iid=item_id: _refresh_coeffs_for_item(cfg, iid, on_refresh),
                ).props("flat round dense size=sm color=blue-7").tooltip("手动刷新系数（从绑定源重新提取）")
            ui.button("绑定", icon="link",
                      on_click=lambda iid=item_id: _open_binding_dialog(cfg, iid, on_refresh)) \
                .props("flat dense size=sm color=blue-7").tooltip("从配置文件提取系数")

        with ui.card().classes("dl-card q-pa-md w-full"):
            # ---- 系数网格 ----
            with ui.row().classes("items-center q-mb-sm"):
                ui.icon("tune", size="xs", color="grey-6").classes("q-mr-xs")
                ui.label("函数系数").classes("mc-section-title")

            coeff_inputs = []
            range_inputs = {}
            extra_inputs = {}

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
                        coeff_inputs.append(inp)

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
                    extra_inputs["trans_coeff"] = trans_inp

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
                    extra_inputs["power_conv_coeff"] = power_conv_inp

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
                range_inputs["x_min"] = x_min_inp

                x_max_inp = ui.number(value=item.get("x_max"), label="x max",
                                      format="%.4g").props("dense outlined size=sm").classes("w-20")
                if not editing:
                    x_max_inp.props(add="readonly")
                x_max_inp._coeff_key = "x_max"
                x_max_inp._coeff_section = f"items.{item_id}"
                cfg["_inputs"].append(x_max_inp)
                range_inputs["x_max"] = x_max_inp

                ui.label("y").classes("text-caption text-grey-7 q-ml-sm")
                y_min_inp = ui.number(value=item.get("y_min"), label="y min",
                                      format="%.4g").props("dense outlined size=sm").classes("w-20")
                if not editing:
                    y_min_inp.props(add="readonly")
                y_min_inp._coeff_key = "y_min"
                y_min_inp._coeff_section = f"items.{item_id}"
                cfg["_inputs"].append(y_min_inp)
                range_inputs["y_min"] = y_min_inp

                y_max_inp = ui.number(value=item.get("y_max"), label="y max",
                                      format="%.4g").props("dense outlined size=sm").classes("w-20")
                if not editing:
                    y_max_inp.props(add="readonly")
                y_max_inp._coeff_key = "y_max"
                y_max_inp._coeff_section = f"items.{item_id}"
                cfg["_inputs"].append(y_max_inp)
                range_inputs["y_max"] = y_max_inp

            warn_container = ui.column().classes("w-full q-mb-md")

            def _num(v, default=None):
                if v is None or v == "":
                    return default
                try:
                    return float(v)
                except (ValueError, TypeError):
                    return default

            def run_multi_check():
                coeffs = {}
                for k in engine.DEFAULT_COEFFS:
                    coeffs[k] = 0.0
                for inp in coeff_inputs:
                    k = getattr(inp, "_coeff_key", None)
                    if k:
                        coeffs[k] = _num(inp.value, 0.0) or 0.0

                xr = (_num(range_inputs["x_min"].value, None), _num(range_inputs["x_max"].value, None))
                yr = (_num(range_inputs["y_min"].value, None), _num(range_inputs["y_max"].value, None))
                search_min = xr[0] if xr[0] is not None else -100.0
                search_max = xr[1] if xr[1] is not None else 100.0

                display_item = dict(item)
                if "trans_coeff" in extra_inputs:
                    display_item["trans_coeff"] = _num(extra_inputs["trans_coeff"].value, 1.0) or 1.0
                if "power_conv_coeff" in extra_inputs:
                    display_item["power_conv_coeff"] = _num(extra_inputs["power_conv_coeff"].value, 1.0) or 1.0

                effective_yr = yr
                scale = 1.0
                if item_id == "pp_energy":
                    info = _pp_scale_info(display_item)
                    if not info:
                        warn_container.clear()
                        return {"has_multi": False}
                    scale = info["scale"]
                    y_min, y_max = yr
                    base_min = _pp_to_base_y(y_min, scale) if y_min is not None else None
                    base_max = _pp_to_base_y(y_max, scale) if y_max is not None else None
                    effective_yr = (base_min, base_max)

                res = engine.detect_multi_solutions(
                    coeffs=coeffs,
                    x_range=xr,
                    y_range=effective_yr,
                    search_range=(search_min, search_max),
                )

                warn_container.clear()
                if not res.get("has_multi"):
                    return res

                if item_id == "pp_energy" and scale != 1.0:
                    res = {
                        **res,
                        "cases": [
                            {"y": float(c.get("y", 0.0)) * scale, "x_values": c.get("x_values") or []}
                            for c in (res.get("cases") or [])
                        ],
                        "points": [
                            {"x": p.get("x"), "y": float(p.get("y", 0.0)) * scale}
                            for p in (res.get("points") or [])
                        ],
                    }

                with warn_container:
                    with ui.card().classes("w-full q-pa-sm").style("border: 1px solid #fb923c; background: #fff7ed"):
                        with ui.row().classes("items-center no-wrap"):
                            ui.icon("warning", color="orange-9").classes("q-mr-sm")
                            ui.label("检测到多解：当前系数与范围组合存在 y→x 的一对多场景").classes("text-body2 text-orange-9")
                        cases = res.get("cases") or []
                        ui.label(f"多解点位（最多展示 {len(cases)} 组 y 值）").classes("text-caption text-orange-9 q-mt-sm")
                        ui.html('<table class="result-table"><thead><tr><th>目标 y</th><th>解 x（同一 y 对应多个 x）</th></tr></thead><tbody>')
                        for c in cases:
                            xs = c.get("x_values") or []
                            x_str = ", ".join(html.escape(str(round(float(x), 10))) for x in xs)
                            ui.html(f"<tr><td>{html.escape(str(round(float(c.get('y', 0.0)), 10)))}</td><td>{x_str}</td></tr>")
                        ui.html("</tbody></table>")
                return res

            for inp in list(coeff_inputs) + list(range_inputs.values()) + list(extra_inputs.values()):
                inp.on("blur", lambda e=None: run_multi_check())
                inp.on("change", lambda e=None: run_multi_check())

            if binding.get("source_file"):
                run_multi_check()

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


def _pp_scale_info(item: dict) -> dict | None:
    trans_coeff = item.get("trans_coeff", 1.0) or 1.0
    power_conv_coeff = item.get("power_conv_coeff", 1.0) or 1.0
    if power_conv_coeff == 0:
        ui.notify("功率转换系数不能为 0", type="warning")
        return None
    return {
        "trans_coeff": float(trans_coeff),
        "power_conv_coeff": float(power_conv_coeff),
        "scale": float(trans_coeff) / float(power_conv_coeff),
    }


def _pp_to_base_y(y: float, scale: float) -> float:
    if scale == 0:
        return float(y)
    return float(y) / float(scale)


def _pp_from_base_y(y: float, item: dict) -> float:
    info = _pp_scale_info(item)
    if not info:
        return float(y)
    return engine.pp_energy_from_y(float(y), info["trans_coeff"], info["power_conv_coeff"])


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
    x_range = (item.get("x_min"), item.get("x_max"))
    y_range = (item.get("y_min"), item.get("y_max"))

    effective_y_range = y_range
    pp_info = None
    if item_id == "pp_energy":
        pp_info = _pp_scale_info(item)
        if not pp_info:
            return
        y_min, y_max = y_range
        base_min = _pp_to_base_y(y_min, pp_info["scale"]) if y_min is not None else None
        base_max = _pp_to_base_y(y_max, pp_info["scale"]) if y_max is not None else None
        effective_y_range = (base_min, base_max)

    results = engine.batch_calc(x_vals, coeffs, x_range, effective_y_range)
    if item_id == "pp_energy":
        for r in results:
            r["y_base"] = r.get("y")
            r["y"] = round(_pp_from_base_y(r.get("y", 0.0), item), 10)
    _show_results(results, "forward", item_id=item_id, item=item, pp_info=pp_info)


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
    x_range = (item.get("x_min"), item.get("x_max"))
    y_range = (item.get("y_min"), item.get("y_max"))
    # 反解时在配置的 x 范围内搜索根；未设置则用默认 ±100
    x_min = item.get("x_min") if item.get("x_min") is not None else -100.0
    x_max = item.get("x_max") if item.get("x_max") is not None else 100.0
    search_range = (x_min, x_max)

    effective_y_vals = y_vals
    effective_y_range = y_range
    pp_info = None
    if item_id == "pp_energy":
        pp_info = _pp_scale_info(item)
        if not pp_info:
            return
        effective_y_vals = [_pp_to_base_y(v, pp_info["scale"]) for v in y_vals]
        y_min, y_max = y_range
        base_min = _pp_to_base_y(y_min, pp_info["scale"]) if y_min is not None else None
        base_max = _pp_to_base_y(y_max, pp_info["scale"]) if y_max is not None else None
        effective_y_range = (base_min, base_max)

    results = engine.batch_reverse(effective_y_vals, coeffs, x_range, effective_y_range, search_range)
    if item_id == "pp_energy":
        for idx, r in enumerate(results):
            r["y_base"] = r.get("y")
            r["y"] = y_vals[idx] if idx < len(y_vals) else r.get("y")
    _show_results(results, "reverse", item_id=item_id, item=item, pp_info=pp_info)


def _get_item_coeffs(item: dict) -> dict:
    return {k: item["coeffs"].get(k, 0.0) for k in engine.DEFAULT_COEFFS}


def _show_results(results: list, mode: str, *, item_id: str, item: dict, pp_info: dict | None = None):
    if not results:
        ui.notify("无有效结果（可能被范围过滤或无解）", type="warning")
        return

    is_forward = (mode == "forward")
    title = "正向计算 结果" if is_forward else "反向求解 结果"
    icon = "trending_flat" if is_forward else "settings_backup_restore"
    has_multi = any(r.get("multiple") for r in results)

    # 弹窗主体卡片：改用固定高度 h-[600px] 确保内部弹性容器不塌陷，且能容纳底部扩展内容
    with ui.dialog() as dlg, ui.card().classes("w-[780px] h-[640px] rounded-xl shadow-xl p-6 gap-4 flex flex-col"):

        # 1. 头部区域
        with ui.row().classes("w-full items-center justify-between border-b border-slate-100 pb-3 shrink-0"):
            with ui.row().classes("items-center gap-2"):
                ui.icon(icon).classes("text-primary text-h5")
                ui.label(title).classes("text-h6 font-bold text-slate-800")
            ui.badge(f"{len(results)} 条数据", color="primary").props("outline")

        # 2. PP 能量特殊信息
        if item_id == "pp_energy" and pp_info:
            with ui.row().classes("w-full bg-blue-50 border border-blue-100 rounded-lg p-2 gap-3 shrink-0"):
                ui.icon("info", size="xs").classes("text-blue-600 mt-0.5")
                with ui.column().classes("gap-0"):
                    ui.label("PP能量 = Y × 传输系数 ÷ 功率转换系数").classes("text-[11px] text-blue-800 font-medium")
                    with ui.row().classes("gap-3 text-[11px] text-blue-600"):
                        ui.label(f"传输效率: {pp_info['scale']:.6g}")
                        ui.label(f"传输系数: {pp_info['trans_coeff']:.6g}")
                        ui.label(f"功率转换系数: {pp_info['power_conv_coeff']:.6g}")

        # 3. 表头区域
        col1_title = "输入 x" if is_forward else "目标 y"
        col2_title = "输出 y = f(x)" if is_forward else "解 x"
        with ui.row().classes(
                "w-full bg-slate-50 border border-slate-200 rounded-t-lg p-3 font-semibold text-slate-600 text-sm no-wrap shrink-0"):
            ui.label(col1_title).classes("w-1/2 pl-2 shrink-0")
            ui.label(col2_title).classes("w-1/2 pl-2 shrink-0")

        # 4. 数据滚动区域
        with ui.scroll_area().classes("w-full border-x border-b border-slate-200 rounded-b-lg flex-1"):
            with ui.column().classes("w-full gap-0 divide-y divide-slate-100"):
                for r in results:
                    with ui.row().classes(
                            "w-full p-3 hover:bg-slate-50 transition-colors items-center no-wrap text-sm text-slate-700"):
                        if is_forward:
                            ui.label(str(r.get("x"))).classes("w-1/2 font-mono text-slate-500 pl-2 truncate shrink-0")
                            ui.label(str(r.get("y"))).classes("w-1/2 font-mono text-primary font-semibold pl-2 truncate shrink-0")
                        else:
                            ui.label(str(r.get("y"))).classes("w-1/2 font-mono text-slate-500 pl-2 truncate shrink-0")
                            with ui.row().classes("w-1/2 pl-2 items-center gap-2 no-wrap shrink-0"):
                                x_vals = r.get("x_values") or []
                                if not x_vals:
                                    ui.label("无实数解").classes(
                                        "px-2 py-0.5 rounded text-xs font-medium bg-red-50 text-red-700 border border-red-200 shrink-0")
                                elif r.get("multiple"):
                                    x_str = ", ".join(str(x) for x in x_vals)
                                    ui.label(x_str).classes("font-mono text-amber-700 font-semibold flex-1 truncate")
                                    ui.label(f"一对多 ({len(x_vals)}解)").classes(
                                        "px-2 py-0.5 rounded text-[11px] font-medium bg-amber-50 text-amber-800 border border-amber-200 shrink-0")
                                else:
                                    ui.label(str(x_vals[0])).classes("font-mono text-emerald-600 font-semibold truncate flex-1")

        # 5. 多解确认与参数明细（可滚动）
        with ui.scroll_area().classes("w-full h-32 shrink-0"):
            with ui.column().classes("w-full gap-2"):
                # 多解确认
                if not is_forward and has_multi:
                    with ui.column().classes("w-full bg-amber-50 border border-amber-100 rounded-lg p-3 gap-2"):
                        with ui.row().classes("items-center gap-2 text-amber-900 text-xs"):
                            ui.icon("warning", size="xs").classes("text-amber-600")
                            ui.label("存在一对多结果，请选择最优解以生成唯一解表格。").classes("font-medium")
                        
                        chosen = {}
                        for idx, r in enumerate(results):
                            if not r.get("multiple"): continue
                            xs = r.get("x_values") or []
                            if not xs: continue
                            
                            default_x = min(xs, key=lambda v: abs(float(v)))
                            chosen[idx] = default_x
                            with ui.row().classes("items-center gap-2 w-full"):
                                ui.label(f"目标 Y: {r.get('y')}").classes("text-[11px] text-slate-500 w-24")
                                ui.select(
                                    options=[str(x) for x in xs],
                                    value=str(default_x),
                                    on_change=lambda e, i=idx: chosen.__setitem__(i, float(e.value)),
                                ).props("dense outlined").classes("flex-1 bg-white").style("font-size: 11px")

                        def apply_choice():
                            # 创建新的弹窗展示唯一解结果
                            with ui.dialog() as sub_dlg, ui.card().classes("w-[500px] p-6 gap-4"):
                                ui.label("唯一解结果").classes("text-h6 font-bold")
                                with ui.scroll_area().classes("w-full h-80 border rounded-lg"):
                                    with ui.column().classes("w-full divide-y"):
                                        for i, r in enumerate(results):
                                            yv = r.get("y")
                                            xs = r.get("x_values") or []
                                            val = chosen.get(i) if r.get("multiple") else (xs[0] if xs else "无解")
                                            with ui.row().classes("w-full p-2 justify-between"):
                                                ui.label(f"Y: {yv}").classes("text-slate-500")
                                                ui.label(str(val)).classes("font-mono font-bold text-primary")
                                ui.button("关闭", on_click=sub_dlg.close).props("flat").classes("ml-auto")
                            sub_dlg.open()

                        ui.button("生成唯一解表格", icon="auto_fix_high", on_click=apply_choice).props(
                            "color=amber-9 text-color=white dense").classes("rounded-md text-xs")

                # 参数明细
                with ui.expansion("查看参数明细", icon="settings").classes("w-full border border-slate-100 rounded-lg text-sm"):
                    coeffs = item.get("coeffs") or {}
                    with ui.grid(columns=3).classes("w-full p-2 gap-2 text-[11px]"):
                        for i in range(1, 9):
                            k = f"k{i}"
                            ui.label(f"{k}: {coeffs.get(k, 0.0)}").classes("font-mono text-slate-500")
                        ui.label(f"b: {coeffs.get('b', 0.0)}").classes("font-mono text-slate-500")
                        ui.label(f"x_range: [{item.get('x_min')}, {item.get('x_max')}]").classes("text-slate-400")
                        ui.label(f"y_range: [{item.get('y_min')}, {item.get('y_max')}]").classes("text-slate-400")

        # 6. 操作按钮
        with ui.row().classes("w-full justify-end gap-2 mt-auto shrink-0"):
            ui.button("关闭", on_click=dlg.close).props("flat color=primary").classes("px-4 rounded-lg font-medium")

    dlg.open()
