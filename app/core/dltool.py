"""DL 快捷计算工具 —— 存储与计算引擎

八阶函数：f(x) = k₁x + k₂x² + k₃x³ + k₄x⁴ + k₅x⁵ + k₆x⁶ + k₇x⁷ + k₈x⁸ + b
"""

from __future__ import annotations

import os
import logging
from copy import deepcopy
from typing import Any, Dict, List, Optional, Sequence, Tuple
import math

from . import fsutil, storage
from .errors import CorruptDataError

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
        # RP 脉宽有两套互相独立的参数：顶层的 coeffs / binding / 范围是 AOM2 delay
        # 这一套（老配置原样兼容），`aom_width` 是 AOM2 width 这一套。`aom_sum` 是两项
        # 之和（固定值），另一项 = aom_sum − y。
        "rp_width": {
            "name": "计算RP脉宽",
            "aom_sum": None,
            "aom_width": deepcopy(DEFAULT_ITEM),
            **deepcopy(DEFAULT_ITEM),
        },
    },
}


def load_config() -> Dict[str, Any]:
    """加载 DL 工具配置。

    `dltool_config.json` 是权威数据：损坏时抛 `CorruptDataError`（§5.4），
    不能回退默认系数再写回——那会把用户调好的系数静默清空。
    """
    cfg_file = _get_config_file()
    cfg = fsutil.read_json(cfg_file, None)
    if cfg is None:
        return deepcopy(DEFAULT_CONFIG)
    if not isinstance(cfg, dict):
        raise CorruptDataError(cfg_file)
    _ensure_defaults(cfg)
    return cfg


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
            if item_id == "rp_width":
                it.setdefault("aom_sum", None)
                width = it.get("aom_width")
                if not isinstance(width, dict):
                    width = deepcopy(DEFAULT_ITEM)
                    it["aom_width"] = width
                width.setdefault("coeffs", dict(DEFAULT_COEFFS))
                width.setdefault("binding", dict(DEFAULT_BINDING))
                for key in ("x_min", "x_max", "y_min", "y_max"):
                    width.setdefault(key, None)
                for k in DEFAULT_COEFFS:
                    width["coeffs"].setdefault(k, 0.0)
            # 确保所有系数键存在
            for k in DEFAULT_COEFFS:
                it["coeffs"].setdefault(k, 0.0)


def save_config(cfg: Dict[str, Any]):
    """保存 DL 工具配置（原子写，§5.4）"""
    cfg_file = _get_config_file()
    # 去除运行时字段（_inputs 等不可序列化的 UI 引用）
    clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
    fsutil.atomic_write_json(cfg_file, clean)


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


# ---------------------------------------------------------------------------
# §7.11 DL 计算 API —— 与 app/web/assets/js/mock/dlmath.js 逐常量、逐函数对齐
# （poly / isConstant / bisect / minAbs / findRoots / inRange / scaleOf /
# validate / forward / reverse / multiCheck），M3-11 的 compute 服务层直接
# 调用这里的 KEYS、find_roots、forward、reverse、multi_check、validate。
# 任何数值语义改动（采样网格与端点钉死、收敛容差、1e-10 舍入、去重半径、
# 排序、采样根数）都必须同步 dlmath.js 并跑前端测试页，否则模板模式与服务
# 端结果会漂移。下方注释中的 JS↔名称对应即对齐清单。
# ---------------------------------------------------------------------------

KEYS = ("k1", "k2", "k3", "k4", "k5", "k6", "k7", "k8")

# dlmath.js：x 范围为 null 时 reverse/multiCheck 回退 ±100 搜索窗
# （空范围是 null，不是 0；find_roots 本身不带该回退——无范围 → no_range）。
_SEARCH_LO = -100.0
_SEARCH_HI = 100.0
# dlmath.js：reverse 求根默认 4000 采样；multiCheck 验证 2000 采样、
# x 轴观测 800+1 点、base 域 21 个等距试探点、cases 上限 8。
_REVERSE_SAMPLES = 4000
_MULTI_CHECK_ROOT_SAMPLES = 2000
_MULTI_CHECK_X_SAMPLES = 800


def _coeff_num(coeffs: Dict[str, Any], key: str) -> float:
    """JS `Number(c[k] || 0)` 的显式化：缺失/null 视作 0.0。
    NaN 不跟 JS 的“或零”路径（validate 在上游已拒绝非有限系数，
    C15 不把 null 悄悄并成 0 之外的语义）。"""
    v = coeffs.get(key)
    if v is None:
        return 0.0
    return float(v)


def _poly(x: float, coeffs: Dict[str, Any]) -> float:
    """dlmath.js poly：f(x)=k₁x+k₂x²+…+k₈x⁸+b。
    与 JS 同用累积乘法 p*=x（而非 x**i）并同样跳过零系数项，
    让 IEEE-754 逐位舍入与前端一致；legacy poly_eval 不做此保证。"""
    y = _coeff_num(coeffs, "b")
    p = 1.0
    for key in KEYS:
        p *= x
        k = _coeff_num(coeffs, key)
        if k != 0.0:
            y += k * p
    return y


def _is_constant(coeffs: Dict[str, Any]) -> bool:
    """dlmath.js isConstant：k1..k8 全为 0（不看 b）。"""
    return all(_coeff_num(coeffs, key) == 0.0 for key in KEYS)


def _round(x: float) -> float:
    """dlmath.js round：Math.round(x*1e10)/1e10。JS Math.round 定义为
    floor(t+0.5)（半值向 +∞），与 Python round() 的银行家舍入不同，
    必须按 JS 语义实现。
    改成 round(x, 10) 会让 1e-10 尺度上的半值往「偶数」方向靠而不是往 +∞，
    既有 31 条用例照样绿而结果与 dlmath.js 漂移；钉死用例是
    tests/test_dltool_multi.py::TestRoundTieDirection 与
    tests/test_dlmath_crosscheck.py（后者直接跑 dlmath.js 对比）。"""
    return math.floor(x * 1e10 + 0.5) / 1e10


def _bisect_root(f, a: float, b: float) -> float:
    """dlmath.js bisect：100 次迭代，收敛判据 (b-a)/2 < 1e-13×(1+|m|)。
    参数不同于 legacy _bisect（tol=1e-12 绝对值、80 次）；后者只服务
    app/pages 旧链路（M4-07 删），本卡冻结不动。仅在变号区间上调用。"""
    fa = f(a)
    if fa == 0:
        return a
    if f(b) == 0:
        return b
    for _ in range(100):
        m = (a + b) / 2.0
        fm = f(m)
        if fm == 0 or (b - a) / 2.0 < 1e-13 * (1.0 + abs(m)):
            return m
        if fa * fm < 0:
            b = m
        else:
            a, fa = m, fm
    return (a + b) / 2.0


def _min_abs(f, a: float, b: float) -> float:
    """dlmath.js minAbs：黄金分割在 [a,b] 上搜 |f| 的极小点，80 次迭代、
    无提前退出，用于发现不变号的切触重根（卡面要点 2）。"""
    g = (math.sqrt(5.0) - 1.0) / 2.0
    c = b - g * (b - a)
    d = a + g * (b - a)
    for _ in range(80):
        if abs(f(c)) < abs(f(d)):
            b = d
        else:
            a = c
        c = b - g * (b - a)
        d = a + g * (b - a)
    return (a + b) / 2.0


def _in_range(v: float, lo: Optional[float], hi: Optional[float]) -> bool:
    """dlmath.js inRange：None/null 表示不设界；0 是真实边界值。"""
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def _scale_of(item: Dict[str, Any], kind: str) -> float:
    """dlmath.js scaleOf：PP 能量 = 传输系数 ÷ 功率转换系数，其余 kind 为 1。
    功率转换系数为 0 时抛 ZeroDivisionError；validate 已前置拒绝（JS 会算出
    Infinity 继续跑，两边都只可能从合法配置进来，这里选择响亮失败）。"""
    if kind != "pp_energy":
        return 1.0
    return float(item["trans_coeff"]) / float(item["power_conv_coeff"])


def _is_finite_number(v: Any) -> bool:
    if v is None:
        return False
    try:
        num = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(num)


def find_roots(target: float, coeffs: Dict[str, Any], lo: Optional[float], hi: Optional[float],
               samples: int = 200) -> Dict[str, Any]:
    """dlmath.js findRoots：求 f(x)=target 在 [lo,hi] 内的候选实根。

    等距网格（第 samples 点钉死为 hi，避免累加漂移）→ |f| ≤ 1e-9×(1+|target|)
    的采样点直接收录（含两端点）→ 变号区间二分精化 → |f| 的严格内局部极小
    且邻点同号时用 minAbs 精化、|f(精化点)| ≤ 1e-7×(1+|target|) 才收录
    （切触重根）→ 去重半径 1e-7×(1+|x|) → 按 x 升序。
    返回“范围内的候选根”，不自动选绝对值最小，也不承诺穷尽。

    status：identity=恒等于 target 的常数函数；constant=恒不等于 target 的
    常数函数（dlmath.js 对此也返回 none，卡面把它拆成 constant，见任务报告）；
    none=非常数函数且范围内无根；lo/hi 任一为 None → no_range 且 roots=[]
    （空范围是 null，不是 0；“不限 x”的反解请走 reverse，它按 JS 回退 ±100）。
    """
    # 顺序即语义（M3-10 修复轮）：no_range 判在 constant/identity **之前**，
    # 所以「常数系数 + lo/hi 任一缺失」返回 no_range 而不是 identity/constant；
    # 哨兵用例 tests/test_dltool_multi.py::TestFindRoots::test_no_range_precedes_constant_and_identity。
    if lo is None or hi is None:
        return {"status": "no_range", "roots": []}
    target = float(target)
    if _is_constant(coeffs):
        if abs(_coeff_num(coeffs, "b") - target) < 1e-12:
            return {"status": "identity", "roots": []}
        return {"status": "constant", "roots": []}

    def f(x: float) -> float:
        return _poly(x, coeffs) - target

    scale = 1.0 + abs(target)
    tol = 1e-9 * scale
    step = (hi - lo) / samples
    roots: List[float] = []

    def add(x: float) -> None:
        for existing in roots:
            if abs(existing - x) <= 1e-7 * (1.0 + abs(x)):
                return
        roots.append(x)

    xs = [hi if i == samples else lo + i * step for i in range(samples + 1)]
    ys = [f(x) for x in xs]
    for i in range(samples + 1):
        if abs(ys[i]) <= tol:
            add(xs[i])
        if i < samples and ys[i] * ys[i + 1] < 0:
            add(_bisect_root(f, xs[i], xs[i + 1]))
        if i > 0 and i < samples:
            left = abs(ys[i - 1])
            mid = abs(ys[i])
            right = abs(ys[i + 1])
            if mid < left and mid < right and ys[i - 1] * ys[i + 1] > 0:
                x = _min_abs(f, xs[i - 1], xs[i + 1])
                if abs(f(x)) <= 1e-7 * scale:
                    add(x)

    roots.sort()
    return {
        "status": "ok" if roots else "none",
        "roots": [{"x": _round(x), "residual": abs(f(x))} for x in roots],
    }


def forward(item: Dict[str, Any], kind: str, xs: Sequence[float]) -> List[Dict[str, Any]]:
    """dlmath.js forward：正向 x→y。越界行标注不丢弃；每行带 input_index。
    out_of_x_range 行只有 {input_index, x, status}，不产出计算字段——
    字段集与 JS 逐一对应（前端与 M3-11 都按此取键）。"""
    s = _scale_of(item, kind)
    coeffs = item["coeffs"]
    x_min = item.get("x_min")
    x_max = item.get("x_max")
    y_min = item.get("y_min")
    y_max = item.get("y_max")
    rows: List[Dict[str, Any]] = []
    for i, x in enumerate(xs):
        row: Dict[str, Any] = {"input_index": i, "x": x}
        if not _in_range(x, x_min, x_max):
            row["status"] = "out_of_x_range"
            rows.append(row)
            continue
        row["y_base"] = _round(_poly(x, coeffs))
        row["y"] = _round(row["y_base"] * s)
        row["status"] = "ok" if _in_range(row["y"], y_min, y_max) else "out_of_y_range"
        rows.append(row)
    return rows


def reverse(item: Dict[str, Any], kind: str, ys: Sequence[float]) -> Tuple[List[Dict[str, Any]], bool]:
    """dlmath.js reverse：反向 y→x，逐行对应输入（带 input_index）。
    第二返回值 exhaustive 恒为 False——§7.11 响应字段，采样求根不承诺穷尽
    （dlmath.js 注释同义：“检测到的候选根”）。x 范围为 null 时按 JS 回退
    ±100 搜索窗；status=out_of_y_range 的行无 y_base；identity 行无
    residuals。"""
    s = _scale_of(item, kind)
    coeffs = item["coeffs"]
    lo = item.get("x_min")
    hi = item.get("x_max")
    if lo is None:
        lo = _SEARCH_LO
    if hi is None:
        hi = _SEARCH_HI
    rows: List[Dict[str, Any]] = []
    for i, y in enumerate(ys):
        row: Dict[str, Any] = {"input_index": i, "y": y, "x_values": []}
        if not _in_range(y, item.get("y_min"), item.get("y_max")):
            row["status"] = "out_of_y_range"
            rows.append(row)
            continue
        # HAZARD（M3-10 修复轮）：s 可以是 **0.0** 而这份配置仍然 validate-legal——
        # validate 只在 power_conv_coeff == 0.0 时拒绝，从不检查 trans_coeff == 0.0，
        # 而 _scale_of 对 pp_energy 返回 trans_coeff / power_conv_coeff。
        # s == 0.0 时下面这句对**任意** y（含 0.0，Python 的浮点除零不产出 inf/nan）
        # 抛 ZeroDivisionError，整个 reverse 调用失败。本卡不改 validate、不在此兜底：
        # 把它翻成 400（或按 spec 改判让 validate 拒绝 trans_coeff=0）归
        # M3-11（app/services/dl.py）。哨兵用例见 tests/test_dltool_multi.py::
        # TestTransCoeffZeroDividesByS（钉住「validate 放行 + 此处抛错」这一现状）。
        row["y_base"] = _round(float(y) / s)
        res = find_roots(row["y_base"], coeffs, lo, hi, samples=_REVERSE_SAMPLES)
        if res["status"] == "identity":
            row["status"] = "identity"
            rows.append(row)
            continue
        row["x_values"] = [r["x"] for r in res["roots"]]
        row["residuals"] = [r["residual"] for r in res["roots"]]
        count = len(row["x_values"])
        row["status"] = "none" if count == 0 else ("single" if count == 1 else "multiple")
        rows.append(row)
    return rows, False


def multi_check(item: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """dlmath.js multiCheck：在 [lo,hi]（null 回退 ±100）等距采样 800+1 点
    观测 f 值域（用户 y 范围则 ÷ 换算系数进 base 域），base 域取 21 个等距
    试探点、findRoots(2000 采样) 检出 ≥2 根的记为 case，y ×换算系数后
    round 回输出域；最多 8 例。返回 {"has_multi", "cases"}。"""
    s = _scale_of(item, kind)
    coeffs = item["coeffs"]
    lo = item.get("x_min")
    hi = item.get("x_max")
    if lo is None:
        lo = _SEARCH_LO
    if hi is None:
        hi = _SEARCH_HI
    observed: List[float] = []
    for i in range(_MULTI_CHECK_X_SAMPLES + 1):
        v = _poly(lo + ((hi - lo) * i) / _MULTI_CHECK_X_SAMPLES, coeffs)
        if math.isfinite(v):
            observed.append(v)
    if not observed:
        return {"has_multi": False, "cases": []}
    y_min = item.get("y_min")
    y_max = item.get("y_max")
    # HAZARD（M3-10 修复轮）：与 reverse 里同一条——trans_coeff == 0.0 时 s == 0.0，
    # 而 validate 仍然放行（它只拒绝 power_conv_coeff == 0.0）。只要 y_min/y_max
    # 有**任一**非 None，下面两句 ÷ s 就抛 ZeroDivisionError（两者都为 None 时走
    # min/max(observed)，本卡不崩）。翻成 400 或改判 validate 归 M3-11
    # （app/services/dl.py）；哨兵用例 tests/test_dltool_multi.py::
    # TestTransCoeffZeroDividesByS。
    base_min = (float(y_min) / s) if y_min is not None else min(observed)
    base_max = (float(y_max) / s) if y_max is not None else max(observed)
    cases: List[Dict[str, Any]] = []
    i = 0
    while i < 21 and len(cases) < 8:
        yb = base_min + ((base_max - base_min) * i) / 20
        res = find_roots(yb, coeffs, lo, hi, samples=_MULTI_CHECK_ROOT_SAMPLES)
        if len(res["roots"]) > 1:
            cases.append({"y": _round(yb * s), "x_values": [r["x"] for r in res["roots"]]})
        i += 1
    return {"has_multi": len(cases) > 0, "cases": cases}


def validate(item: Dict[str, Any], kind: str) -> str:
    """dlmath.js validate：返回错误文案，空串=合法（JS 用 null 表合法，卡面
    契约改为 ""，文案逐字一致）。功率转换系数为 0 → 拒绝（除零，卡面要点 5）。
    与 JS 的收紧差（见任务报告）：JS 把 null 强制成 0 放行，这里系数、
    传输/功率系数的 null 一律按“必须是有限数值”拒绝。"""
    coeffs = item.get("coeffs")
    if not isinstance(coeffs, dict):
        return "系数必须是有限数值"
    for key in KEYS:
        if not _is_finite_number(coeffs.get(key)):
            return "系数必须是有限数值"
    if not _is_finite_number(coeffs.get("b")):
        return "系数必须是有限数值"
    if kind == "pp_energy":
        if not (_is_finite_number(item.get("trans_coeff"))
                and _is_finite_number(item.get("power_conv_coeff"))):
            return "传输系数与功率转换系数必须是有限数值"
        if float(item["power_conv_coeff"]) == 0.0:
            return "功率转换系数不能为 0"
    x_min = item.get("x_min")
    x_max = item.get("x_max")
    if x_min is not None and x_max is not None and x_min >= x_max:
        return "x 范围下限必须小于上限"
    y_min = item.get("y_min")
    y_max = item.get("y_max")
    if y_min is not None and y_max is not None and y_min >= y_max:
        return "y 范围下限必须小于上限"
    return ""


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

    # R15/C15：null 显式处理，不用 `or` 把 None 与 0 并成同一路径（行为与原 `or 0` 一致）
    x_samples = max(int(0 if x_samples is None else x_samples), 50)
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
