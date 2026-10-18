"""JS↔Python DL 等价性 cross-check（M3-10 / INH-1895 修复轮）。

前一份等价性证明活在上一位实现者的 /tmp/dl10_check.js + /tmp/dl10_compare.py 里，
硬编码绝对路径、没入库，文件一删就没人能复现。这里把它变成仓库内的常驻机制：

- JS 侧 oracle：tests/utils/dlmath_crosscheck.js 载入并**执行** app/web/assets/js/mock/dlmath.js
  的浏览器 IIFE（vm 沙箱里给它一个 window），从 stdin 收用例、把结果 JSON 打到 stdout。
- 本文件：同一批输入喂给 app/core/dltool.py，逐值比对，diff 必须为空。

比的是行为而不是源码文本（know-how / 计划 R45 禁的就是后者）：tol 从 1e-9 改成 1e-8、
默认 samples 从 4000 改成 400、round 改成 round(x, 10) 这类改动，源码字面串可以几乎不变，
而这里的用例必然点名不一致。

三处**卡面裁定的**语义差异在下面 _normalize 里显式归一，其余必须逐位相等：
1. 常数函数且 target != b：dlmath.js 返回 status "none"，卡面把 Python 拆成 "constant"。
2. validate 合法值：dlmath.js 用 null，§7.11 契约改成 ""。
3. dlmath.js 的 findRoots 默认 samples=4000、Python find_roots 默认 200：
   本用例一律显式传 samples，因此这个默认值差不参与比对
   （reverse/multi_check 内部各自用 JS 同值的 4000 / 2000，走真实路径）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core import dltool

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "tests" / "utils" / "dlmath_crosscheck.js"
DLMATH_JS = REPO_ROOT / "app" / "web" / "assets" / "js" / "mock" / "dlmath.js"

# 用例只许增不许减：低于这个数量就不再是在证明等价性了。
MIN_PAIRS = 40


def _coeffs(**over):
    c = {k: 0.0 for k in dltool.KEYS}
    c["b"] = 0.0
    c.update(over)
    return c


def _item(coeffs, x_min=None, x_max=None, y_min=None, y_max=None,
          trans_coeff=1.0, power_conv_coeff=1.0):
    return {
        "coeffs": coeffs,
        "x_min": x_min,
        "x_max": x_max,
        "y_min": y_min,
        "y_max": y_max,
        "trans_coeff": trans_coeff,
        "power_conv_coeff": power_conv_coeff,
    }


# (x-3)^2 = x^2 - 6x + 9：变号根 + 切触重根都在这条上。
SQUAT = _coeffs(k1=-6.0, k2=1.0, b=9.0)
# x^4 - 5x^2 + 4 = (x^2-1)(x^2-4)：四个单根，覆盖多根去重与排序。
WIGGLE = _coeffs(k2=-5.0, k4=1.0, b=4.0)
FULL8 = _coeffs(k1=1.0, k2=1.0, k3=1.0, k4=1.0, k5=1.0, k6=1.0, k7=1.0, k8=1.0, b=-0.5)


def _poly_cases():
    out = []
    grid = [-3.0, -0.5, 0.0, 1.0, 3.7]
    for coeffs in (SQUAT, _coeffs(k1=1.0), WIGGLE, FULL8):
        for x in grid:
            out.append(({"fn": "poly", "x": x, "coeffs": coeffs}, dltool._poly(x, coeffs)))
    return out


def _roots_status(res):
    """归一卡面裁定 1：Python 的 constant 对应 JS 的 none。"""
    normalized = dict(res)
    if normalized["status"] == "constant":
        normalized["status"] = "none"
    return normalized


def _find_roots_cases():
    out = []
    combos = [
        # (target, coeffs, lo, hi, samples)
        (4.0, SQUAT, -10.0, 10.0, 200),
        (0.0, SQUAT, -10.0, 10.0, 200),
        # samples=97 时网格取不到 x=3、函数又不变号 → 只有 minAbs 极值路检得出
        (0.0, SQUAT, -10.0, 10.0, 97),
        (-1.0, SQUAT, -10.0, 10.0, 200),
        (4.0, SQUAT, -10.0, 10.0, 4000),
        (0.5, WIGGLE, -3.0, 3.0, 200),
        (0.0, WIGGLE, -3.0, 3.0, 4000),
        (0.0, WIGGLE, 1.5, 2.5, 200),
        (0.0, _coeffs(k1=1.0, b=5.0), -5.0, 5.0, 200),
        (-5.0, _coeffs(k1=1.0), 0.0, 10.0, 200),
        (0.5, FULL8, 0.0, 1.0, 200),
        (1e-09, SQUAT, -10.0, 10.0, 200),
    ]
    for target, coeffs, lo, hi, samples in combos:
        expected = _roots_status(dltool.find_roots(target, coeffs, lo, hi, samples=samples))
        out.append(({"fn": "findRoots", "target": target, "coeffs": coeffs,
                     "lo": lo, "hi": hi, "samples": samples}, expected))
    # 常数函数：identity 两边同名；target != b 走归一 1
    for target, coeffs in ((2.0, _coeffs(b=2.0)), (3.0, _coeffs(b=2.0))):
        out.append(({"fn": "findRoots", "target": target, "coeffs": coeffs,
                     "lo": -5.0, "hi": 5.0, "samples": 200},
                    _roots_status(dltool.find_roots(target, coeffs, -5.0, 5.0, samples=200))))
    return out


def _forward_cases():
    linear = _coeffs(k1=2.0, b=1.0)
    combos = [
        (_item(linear, x_min=0, x_max=10, y_max=15), "rp_width", [-1, 3, 9]),
        (_item(linear, trans_coeff=2.0, power_conv_coeff=0.5), "pp_energy", [1.0, 2.5]),
        # y_min=0.0 是真实下界而不是「不设限」：0 与 null 在这条路上必须同判
        (_item(_coeffs(k1=1.0), x_min=-10.0, x_max=10.0, y_min=0.0), "rp_width", [-1.0, 0.0]),
        (_item(SQUAT), "rp_width", [3.0, 1.0, 5.0]),
        (_item(WIGGLE, trans_coeff=-1.5, power_conv_coeff=0.75), "pp_energy", [-2.0, 0.5]),
    ]
    # 1e-10 尺度上的半值：round 的破平方向（JS floor(t+0.5) 朝 +∞）在 y_base 上可观测。
    # 写成 round(x, 10) 的话 3.5e-10→3e-10、-2.5e-10→-3e-10，这里立刻报差异。
    for b in (3.5e-10, -2.5e-10, 2.5e-10):
        combos.append((_item(_coeffs(b=b), x_min=-1.0, x_max=1.0), "rp_width", [0.0]))
    out = []
    for item, kind, inputs in combos:
        # item/kind/inputs 只算一次，两侧共用同一个对象，防止用例与期望各写一遍写歪
        expected = dltool.forward(item, kind, inputs)
        out.append(({"fn": "forward", "kind": kind, "item": item, "inputs": list(inputs)},
                    expected))
    return out


def _reverse_cases():
    out = []
    combos = [
        (_item(SQUAT, x_min=-10.0, x_max=10.0), "rp_width", [0, 4, -1]),
        # x 范围 null → 两边都按 ±100 搜索窗回退
        (_item(SQUAT), "rp_width", [4]),
        (_item(_coeffs(k1=2.0, b=1.0), trans_coeff=2.0, power_conv_coeff=0.5),
         "pp_energy", [12.0, 3.0]),
        (_item(SQUAT, x_min=-10.0, x_max=10.0, y_min=0.0, y_max=10.0), "rp_width", [-1, 0]),
        (_item(_coeffs(k1=1.0), x_min=-100.0, x_max=100.0), "rp_energy", [-50.0, 0.0]),
    ]
    for item, kind, inputs in combos:
        expected = dltool.reverse(item, kind, inputs)[0]
        out.append(({"fn": "reverse", "kind": kind, "item": item, "inputs": list(inputs)},
                    expected))
    # 常数函数走 identity / none；同时把 y_base = round(y / s) 的半值方向暴露出来
    tie_item = _item(_coeffs(b=0.0), x_min=-10.0, x_max=10.0)
    out.append(({"fn": "reverse", "kind": "rp_width", "item": tie_item,
                 "inputs": [3.5e-10, -2.5e-10, -3.5e-10]},
                dltool.reverse(tie_item, "rp_width", [3.5e-10, -2.5e-10, -3.5e-10])[0]))
    const_item = _item(_coeffs(b=2.0))
    for y, kind in ((2, "rp_width"), (3, "rp_width")):
        out.append(({"fn": "reverse", "kind": kind, "item": const_item, "inputs": [y]},
                    dltool.reverse(const_item, kind, [y])[0]))
    return out


def _multi_check_cases():
    out = []
    combos = [
        (_item(SQUAT, x_min=-10.0, x_max=10.0), "rp_width"),
        (_item(SQUAT), "rp_width"),
        (_item(SQUAT, x_max=20.0, trans_coeff=2.0, power_conv_coeff=0.5), "pp_energy"),
        (_item(_coeffs(k1=1.0), x_min=-10.0, x_max=10.0), "rp_energy"),
        (_item(WIGGLE, x_min=-3.0, x_max=3.0), "rp_energy"),
    ]
    for item, kind in combos:
        out.append(({"fn": "multiCheck", "kind": kind, "item": item},
                    dltool.multi_check(item, kind)))
    return out


def _validate_cases():
    valid = _item(_coeffs(k1=1.0), x_min=-5.0, x_max=5.0)
    pp = _item(_coeffs(k1=1.0), trans_coeff=2.0, power_conv_coeff=0.5)
    # JS Number("NaN")=NaN、Number("1e999")=Infinity；Python float() 同值。
    # 真 NaN/inf 过不了 JSON，所以用字符串形式把「非有限」送进两边。
    nan_item = _item(_coeffs(k3="NaN"))
    inf_item = _item(_coeffs(k8="1e999"))
    combos = [
        (valid, "rp_width"),
        (pp, "pp_energy"),
        (_item(_coeffs(), x_min=5.0, x_max=3.0), "rp_width"),
        (_item(_coeffs(), y_min=2.0, y_max=2.0), "rp_width"),
        (_item(_coeffs(), x_min=None, x_max=None, y_min=None, y_max=None), "rp_energy"),
        (nan_item, "rp_width"),
        (inf_item, "rp_width"),
        (_item(_coeffs(k1=1.0), trans_coeff=1.0, power_conv_coeff=0.0), "pp_energy"),
        (_item(_coeffs(k1=1.0), trans_coeff=1.0, power_conv_coeff=0.0), "rp_energy"),
    ]
    out = []
    for item, kind in combos:
        out.append(({"fn": "validate", "kind": kind, "item": item}, dltool.validate(item, kind)))
    return out


def _all_cases():
    cases = []
    cases.extend(_poly_cases())
    cases.extend(_find_roots_cases())
    cases.extend(_forward_cases())
    cases.extend(_reverse_cases())
    cases.extend(_multi_check_cases())
    cases.extend(_validate_cases())
    return cases


def _numbers(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _diff(label, expected, actual, out):
    """递归比对；浮点按**逐位相等**判（JSON 用最短可还原十进制往返，不引入误差）。
    -0.0 == 0.0 不计为差异：那是同一条数轴上的同一个点，不是语义漂移。"""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            out.append("%s: 结构不同 py=%r js=%r" % (label, expected, actual))
            return
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        if missing or extra:
            out.append("%s: 键集不同 缺=%s 多=%s" % (label, missing, extra))
            return
        for key in sorted(expected):
            _diff("%s.%s" % (label, key), expected[key], actual[key], out)
        return
    if isinstance(expected, (list, tuple)):
        if not isinstance(actual, list):
            out.append("%s: 结构不同 py=%r js=%r" % (label, expected, actual))
            return
        if len(expected) != len(actual):
            out.append("%s: 长度不同 py=%d js=%d" % (label, len(expected), len(actual)))
            return
        for i, (e, a) in enumerate(zip(expected, actual)):
            _diff("%s[%d]" % (label, i), e, a, out)
        return
    if _numbers(expected) and _numbers(actual):
        if expected != actual:
            out.append("%s: py=%r js=%r" % (label, expected, actual))
        return
    if expected != actual:
        out.append("%s: py=%r js=%r" % (label, expected, actual))


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="本机没有 Node：JS↔Python DL 等价性**未执法**（这一轮的绿证明不了 dlmath.js "
           "与 dltool.py 一致；换有 node 的机器重跑本用例）",
)
def test_dltool_matches_dlmath_js():
    assert DLMATH_JS.exists(), "找不到被比对的 dlmath.js：%s" % (DLMATH_JS,)
    assert RUNNER.exists(), "找不到 JS 侧 oracle：%s" % (RUNNER,)

    cases = _all_cases()
    payload = json.dumps([case for case, _ in cases])
    proc = subprocess.run(
        [shutil.which("node"), str(RUNNER)],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        timeout=300,
    )
    assert proc.returncode == 0, "node oracle 退出码 %s：%s" % (proc.returncode, proc.stderr)

    parsed = json.loads(proc.stdout)
    assert parsed["pairs"] == len(cases), "oracle 少跑了用例：%s != %s" % (parsed["pairs"], len(cases))
    assert len(cases) >= MIN_PAIRS, "用例从 %d 缩到 %d 条，等价性覆盖不足" % (MIN_PAIRS, len(cases))

    diffs = []
    for i, ((case, expected), actual) in enumerate(zip(cases, parsed["results"])):
        if case["fn"] == "validate" and actual is None:
            actual = ""  # 归一卡面裁定 2：JS 用 null 表合法，Python 契约用 ""
        _diff("#%d %s" % (i, case["fn"]), expected, actual, diffs)
    assert not diffs, ("%d 组输入对里 %d 处不一致（dltool.py vs dlmath.js）：\n%s"
                       % (len(cases), len(diffs), "\n".join(diffs[:40])))
