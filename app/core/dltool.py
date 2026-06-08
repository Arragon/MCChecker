"""DL 快捷计算工具 —— 存储与计算引擎

八阶函数：f(x) = k₁x + k₂x² + k₃x³ + k₄x⁴ + k₅x⁵ + k₆x⁶ + k₇x⁷ + k₈x⁸ + b
"""

import json
import os
import logging
from copy import deepcopy
from typing import Any, Dict, List, Optional
import math

from . import storage

logger = logging.getLogger(__name__)

DLTOOL_FILE = os.path.join(storage.DATA_DIR, "dltool_config.json")


def _get_config_file() -> str:
    profile = storage.get_active_profile()
    target = os.path.join(storage.get_profile_data_dir(profile), "dltool_config.json")
    if profile == storage.DEFAULT_PROFILE and not os.path.exists(target) and os.path.exists(DLTOOL_FILE):
        return DLTOOL_FILE
    return target

DEFAULT_COEFFS: Dict[str, float] = {
    f"k{i}": 0.0 for i in range(1, 9)
}
DEFAULT_COEFFS["b"] = 0.0

DEFAULT_BINDING: Dict[str, Any] = {
    "source_file": "",
    "variable_path": "",
    "field_map": {},  # {"field_name": "coefficient_key"}
}

DEFAULT_ITEM = {
    "coeffs": dict(DEFAULT_COEFFS),
    "binding": dict(DEFAULT_BINDING),
    "x_min": None,
    "x_max": None,
    "y_min": None,
    "y_max": None,
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "editing": False,
    "items": {
        "pp_energy": {
            "name": "计算PP能量",
            "trans_coeff": 1.0,
            "power_conv_coeff": 1.0,
            **deepcopy(DEFAULT_ITEM),
        },
        "rp_energy": {
            "name": "计算RP能量",
            **deepcopy(DEFAULT_ITEM),
        },
        "rp_width": {
            "name": "计算RP脉宽",
            **deepcopy(DEFAULT_ITEM),
        },
    },
}


def load_config() -> Dict[str, Any]:
    """加载 DL 工具配置"""
    cfg_file = _get_config_file()
    if not os.path.exists(cfg_file):
        return deepcopy(DEFAULT_CONFIG)
    try:
        with open(cfg_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # 补齐缺失字段
        _ensure_defaults(cfg)
        return cfg
    except (json.JSONDecodeError, IOError) as e:
        logger.error("加载 dltool_config 失败: %s", e)
        return deepcopy(DEFAULT_CONFIG)


def _ensure_defaults(cfg: dict):
    """确保配置包含所有默认字段"""
    cfg.setdefault("editing", False)
    cfg.setdefault("items", {})
    for item_id, item_default in DEFAULT_CONFIG["items"].items():
        if item_id not in cfg["items"]:
            cfg["items"][item_id] = deepcopy(item_default)
        else:
            it = cfg["items"][item_id]
            it.setdefault("name", item_default["name"])
            it.setdefault("coeffs", dict(DEFAULT_COEFFS))
            it.setdefault("binding", dict(DEFAULT_BINDING))
            it.setdefault("x_min", None)
            it.setdefault("x_max", None)
            it.setdefault("y_min", None)
            it.setdefault("y_max", None)
            if item_id == "pp_energy":
                it.setdefault("trans_coeff", 1.0)
                it.setdefault("power_conv_coeff", 1.0)
            # 确保所有系数键存在
            for k in DEFAULT_COEFFS:
                it["coeffs"].setdefault(k, 0.0)


def save_config(cfg: Dict[str, Any]):
    """保存 DL 工具配置"""
    cfg_file = _get_config_file()
    os.makedirs(os.path.dirname(cfg_file), exist_ok=True)
    # 去除运行时字段（_inputs 等不可序列化的 UI 引用）
    clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)


def poly_eval(x: float, coeffs: Dict[str, float]) -> float:
    """计算 f(x) = k₁x + k₂x² + ... + k₈x⁸ + b"""
    result = coeffs.get("b", 0.0)
    for i in range(1, 9):
        ki = coeffs.get(f"k{i}", 0.0)
        if ki != 0.0:
            result += ki * (x ** i)
    return result


def pp_energy_from_y(y: float, trans_coeff: float, power_conv_coeff: float) -> float:
    if power_conv_coeff == 0:
        raise ZeroDivisionError("power_conv_coeff cannot be 0")
    result = float(y) * float(trans_coeff) / float(power_conv_coeff)
    logger.info(
        "PP能量计算: y=%s trans_coeff=%s power_conv_coeff=%s -> pp_energy=%s",
        y,
        trans_coeff,
        power_conv_coeff,
        result,
    )
    return result


def poly_find_x(target_y: float, coeffs: Dict[str, float],
                x_min: float = -100.0, x_max: float = 100.0,
                samples: int = 20000) -> List[float]:
    """反解：给定 y，求所有满足 f(x)=y 的 x 值

    使用采样 + 二分法定位所有实根。
    """
    def f(x):
        return poly_eval(x, coeffs) - target_y

    roots = []
    step = (x_max - x_min) / samples
    prev_y = f(x_min)

    for i in range(1, samples + 1):
        x_curr = x_min + i * step
        curr_y = f(x_curr)

        # 检查是否恰好命中
        if curr_y == 0.0:
            roots.append(x_curr)
        elif prev_y * curr_y < 0:
            # 符号变化 → 二分法精确定位
            root = _bisect(f, x_curr - step, x_curr, tol=1e-12, max_iter=80)
            if root is not None:
                root = round(root, 10)
                # 去重
                if not roots or abs(root - roots[-1]) > 1e-9:
                    roots.append(root)

        prev_y = curr_y

    return roots


def _bisect(f, a: float, b: float, tol: float = 1e-12, max_iter: int = 80) -> Optional[float]:
    """二分法求根"""
    fa, fb = f(a), f(b)
    if fa == 0:
        return a
    if fb == 0:
        return b
    if fa * fb > 0:
        return None

    for _ in range(max_iter):
        m = (a + b) / 2.0
        fm = f(m)
        if fm == 0 or (b - a) / 2.0 < tol:
            return m
        if fa * fm < 0:
            b, fb = m, fm
        else:
            a, fa = m, fm
    return (a + b) / 2.0


def batch_calc(x_values: List[float], coeffs: Dict[str, float],
               x_range: tuple = None, y_range: tuple = None) -> List[Dict[str, Any]]:
    """批量正向计算：输入多个 x，输出 f(x)

    x_range: (min, max)  输入范围
    y_range: (min, max)  输出范围
    """
    results = []
    x_min, x_max = x_range if x_range else (None, None)
    y_min, y_max = y_range if y_range else (None, None)

    for x in x_values:
        # 输入范围校验
        if x_min is not None and x < x_min:
            continue
        if x_max is not None and x > x_max:
            continue

        y = poly_eval(x, coeffs)

        # 输出范围校验
        if y_min is not None and y < y_min:
            continue
        if y_max is not None and y > y_max:
            continue

        results.append({"x": x, "y": round(y, 10)})

    return results


def batch_reverse(y_values: List[float], coeffs: Dict[str, float],
                  x_range: tuple = None, y_range: tuple = None,
                  search_range: tuple = (-100.0, 100.0)) -> List[Dict[str, Any]]:
    """批量反向求解：输入多个 y，求所有满足 f(x)=y 的 x

    x_range: 输出 x 的有效范围
    y_range: 输入 y 的有效范围
    """
    results = []
    x_min, x_max = x_range if x_range else (None, None)
    y_min, y_max = y_range if y_range else (None, None)
    search_min, search_max = search_range

    for y_target in y_values:
        # y 范围校验
        if y_min is not None and y_target < y_min:
            continue
        if y_max is not None and y_target > y_max:
            continue

        roots = poly_find_x(y_target, coeffs, search_min, search_max)

        # x 范围过滤
        filtered = []
        for x in roots:
            if x_min is not None and x < x_min:
                continue
            if x_max is not None and x > x_max:
                continue
            filtered.append(x)

        results.append({
            "y": y_target,
            "x_values": filtered,
            "multiple": len(filtered) > 1,
        })

    return results


def detect_multi_solutions(
    coeffs: Dict[str, float],
    x_range: tuple = None,
    y_range: tuple = None,
    search_range: tuple = (-100.0, 100.0),
    x_samples: int = 800,
    y_samples: int = 21,
    max_cases: int = 8,
    max_points: int = 60,
    root_samples: int = 4000,
) -> Dict[str, Any]:
    x_min, x_max = x_range if x_range else (None, None)
    y_min, y_max = y_range if y_range else (None, None)
    search_min, search_max = search_range

    if x_min is not None and x_max is not None and x_min >= x_max:
        return {"has_multi": False, "error": "x_range_invalid"}
    if y_min is not None and y_max is not None and y_min >= y_max:
        return {"has_multi": False, "error": "y_range_invalid"}

    effective_x_min = x_min if x_min is not None else search_min
    effective_x_max = x_max if x_max is not None else search_max
    if effective_x_min >= effective_x_max:
        return {"has_multi": False, "error": "search_range_invalid"}

    x_samples = max(int(x_samples or 0), 50)
    ys: List[float] = []
    for i in range(x_samples + 1):
        x = effective_x_min + (effective_x_max - effective_x_min) * i / x_samples
        try:
            y = float(poly_eval(x, coeffs))
        except Exception:
            continue
        if math.isfinite(y):
            ys.append(y)
    if not ys:
        return {"has_multi": False, "cases": [], "points": []}

    observed_min = min(ys)
    observed_max = max(ys)
    check_y_min = y_min if y_min is not None else observed_min
    check_y_max = y_max if y_max is not None else observed_max
    if check_y_min > check_y_max:
        return {"has_multi": False, "cases": [], "points": [], "y_span": (check_y_min, check_y_max)}

    y_targets: List[float] = []
    if y_samples <= 1 or check_y_min == check_y_max:
        y_targets = [check_y_min]
    else:
        y_samples = max(int(y_samples), 5)
        for i in range(y_samples):
            y_targets.append(check_y_min + (check_y_max - check_y_min) * i / (y_samples - 1))
    if check_y_min <= 0.0 <= check_y_max:
        y_targets.append(0.0)

    seen = set()
    unique_targets: List[float] = []
    for y in y_targets:
        ky = round(float(y), 12)
        if ky in seen:
            continue
        seen.add(ky)
        unique_targets.append(float(y))
    y_targets = unique_targets

    cases: List[Dict[str, Any]] = []
    points: List[Dict[str, float]] = []

    for y_target in y_targets:
        roots = poly_find_x(y_target, coeffs, effective_x_min, effective_x_max, samples=root_samples)
        filtered: List[float] = []
        for x in roots:
            if x_min is not None and x < x_min:
                continue
            if x_max is not None and x > x_max:
                continue
            filtered.append(x)
        if len(filtered) > 1:
            cases.append({"y": y_target, "x_values": filtered})
            for x in filtered:
                points.append({"x": x, "y": y_target})
                if len(points) >= max_points:
                    break
        if len(cases) >= max_cases or len(points) >= max_points:
            break

    return {
        "has_multi": bool(cases),
        "cases": cases,
        "points": points,
        "x_span": (effective_x_min, effective_x_max),
        "y_span": (check_y_min, check_y_max),
    }


def parse_coeffs_from_data(data: dict, field_map: Dict[str, str]) -> Dict[str, float]:
    """从解析后的数据字典中提取系数

    data: {"m1": 1, "m2": 2, "n": 7, ...}
    field_map: {"m1": "k1", "m2": "k2", "n": "b", ...}
    """
    coeffs = dict(DEFAULT_COEFFS)
    for field_name, coeff_key in field_map.items():
        if field_name in data:
            try:
                coeffs[coeff_key] = float(data[field_name])
            except (ValueError, TypeError):
                pass
    return coeffs
