import math

import pytest

from app.core import dltool


class TestMultiSolutionDetection:
    def test_detects_multi_solutions(self):
        coeffs = dict(dltool.DEFAULT_COEFFS)
        coeffs["k3"] = 1.0
        coeffs["k1"] = -1.0

        res = dltool.detect_multi_solutions(
            coeffs=coeffs,
            x_range=(-2.0, 2.0),
            y_range=(-0.5, 0.5),
            search_range=(-2.0, 2.0),
            y_samples=11,
            root_samples=2000,
        )
        assert res["has_multi"] is True
        assert any(len(c.get("x_values") or []) > 1 for c in (res.get("cases") or []))

    def test_no_multi_for_monotonic(self):
        coeffs = dict(dltool.DEFAULT_COEFFS)
        coeffs["k1"] = 1.0
        res = dltool.detect_multi_solutions(
            coeffs=coeffs,
            x_range=(-10.0, 10.0),
            y_range=(-10.0, 10.0),
            search_range=(-10.0, 10.0),
            y_samples=9,
            root_samples=1000,
        )
        assert res["has_multi"] is False


# ---------------------------------------------------------------------------
# §7.11 对齐用例（M3-10 / INH-1895）
#
# 前三条 test_reverse / test_forward / test_validate 用例的输入与期望逐字抄自
# app/web/tests/tests.js 的三条 DL 用例（模板模式下由 dlmath.js 计算并已验收），
# 这里让 app/core/dltool.py 对同一输入产出同一结果，防止 JS↔Python 漂移。
# 其余用例钉死卡面边界：切触重根、no_range、端点根、null≠0、
# 多解检测采样参数、validate 全部文案。容差 1e-6 与前端用例写法一致。
# ---------------------------------------------------------------------------


def _empty_coeffs():
    return dict(dltool.DEFAULT_COEFFS)


# tests.js「DL：反向求解发现切触重根与多解」：(x-3)^2 = k1=-6, k2=1, b=9
SQUAT_COEFFS = {"k1": -6.0, "k2": 1.0, "k3": 0.0, "k4": 0.0,
                "k5": 0.0, "k6": 0.0, "k7": 0.0, "k8": 0.0, "b": 9.0}


def _sq_item(**over):
    item = {"coeffs": dict(SQUAT_COEFFS), "x_min": -10.0, "x_max": 10.0,
            "y_min": None, "y_max": None}
    item.update(over)
    return item


class TestFindRoots:
    def test_multiple_roots_sorted_ascending_with_residuals(self):
        """范围内多解：全部返回、升序、**不自动选绝对值最小**（卡面 + 横幅「系统不会自动替你挑选」）。"""
        res = dltool.find_roots(4.0, SQUAT_COEFFS, -10.0, 10.0)
        assert res["status"] == "ok"
        assert [r["x"] for r in res["roots"]] == [1.0, 5.0]
        for r in res["roots"]:
            assert set(r) == {"x", "residual"}
            assert r["residual"] < 1e-9

    def test_no_root_in_range(self):
        """(x-3)^2 = -1 在 [-10,10] 无实根 → status none、roots 空。"""
        res = dltool.find_roots(-1.0, SQUAT_COEFFS, -10.0, 10.0)
        assert res["status"] == "none"
        assert res["roots"] == []

    def test_tangent_double_root_detected(self):
        """切触重根：(x-3)^2 = 0 变号二分检不出，必须靠极值检测路（卡面要点 2）。"""
        res = dltool.find_roots(0.0, SQUAT_COEFFS, -10.0, 10.0)
        assert res["status"] == "ok"
        assert len(res["roots"]) == 1
        assert res["roots"][0]["x"] == pytest.approx(3.0, abs=1e-6)

    def test_tangent_double_root_when_grid_misses_it(self):
        """samples=97 时网格取不到 x=3、函数又不变号 → 只有 minAbs 极值路能检出。"""
        res = dltool.find_roots(0.0, SQUAT_COEFFS, -10.0, 10.0, samples=97)
        assert res["status"] == "ok"
        assert len(res["roots"]) == 1
        assert res["roots"][0]["x"] == pytest.approx(3.0, abs=1e-6)

    def test_endpoint_root_included(self):
        """端点根：f(x)=x+5 的根恰落在 lo=-5 上，必须收录（卡面「端点」）。"""
        coeffs = _empty_coeffs()
        coeffs["k1"] = 1.0
        coeffs["b"] = 5.0
        res = dltool.find_roots(0.0, coeffs, -5.0, 5.0)
        assert res["status"] == "ok"
        assert res["roots"][0]["x"] == -5.0
        assert res["roots"][0]["residual"] == 0.0

    def test_identity_when_constant_matches(self):
        """常数函数且 target == b → status identity（不是 ok、不是空列表静默）。"""
        coeffs = _empty_coeffs()
        coeffs["b"] = 2.0
        res = dltool.find_roots(2.0, coeffs, -5.0, 5.0)
        assert res["status"] == "identity"
        assert res["roots"] == []

    def test_constant_when_never_matches(self):
        """常数函数且 target != b → status constant（卡面要点 3：不静默）。"""
        coeffs = _empty_coeffs()
        coeffs["b"] = 2.0
        res = dltool.find_roots(3.0, coeffs, -5.0, 5.0)
        assert res["status"] == "constant"
        assert res["roots"] == []

    def test_no_range_when_bound_is_null(self):
        """lo/hi 为 None → no_range + 空 roots；空范围是 null，不是 0（C15）。"""
        coeffs = _empty_coeffs()
        coeffs["k1"] = 1.0
        assert dltool.find_roots(4.0, coeffs, None, 10.0) == {"status": "no_range", "roots": []}
        assert dltool.find_roots(4.0, coeffs, 0.0, None) == {"status": "no_range", "roots": []}
        assert dltool.find_roots(4.0, coeffs, None, None) == {"status": "no_range", "roots": []}

    def test_no_range_precedes_constant_and_identity(self):
        """分支顺序即语义（M3-10 修复轮 / Minor 2）：no_range 判在 constant/identity **之前**，
        所以「常数系数 + 范围缺失」返回 no_range，而不是 identity / constant。
        把 find_roots 里两个 if 换个个儿，本用例就红（实测）。
        注意上面那条用的是非常数多项式，钉不住这条顺序。"""
        const = _empty_coeffs()
        const["b"] = 2.0
        # target == b：范围齐备时才是 identity，缺范围时是 no_range
        assert dltool.find_roots(2.0, const, None, None) == {"status": "no_range", "roots": []}
        assert dltool.find_roots(2.0, const, None, 5.0) == {"status": "no_range", "roots": []}
        assert dltool.find_roots(2.0, const, -5.0, None) == {"status": "no_range", "roots": []}
        # target != b：同理，no_range 优先于 constant
        assert dltool.find_roots(3.0, const, None, None) == {"status": "no_range", "roots": []}
        # 对照：范围齐备时才是 constant / identity
        assert dltool.find_roots(3.0, const, -5.0, 5.0)["status"] == "constant"
        assert dltool.find_roots(2.0, const, -5.0, 5.0)["status"] == "identity"

    def test_zero_bound_is_real_not_null(self):
        """lo=0.0 是真实下界：f(x)=x 的根 -5 被排除（若 0 被当成 None 就会搜到 [-100,100]）。"""
        coeffs = _empty_coeffs()
        coeffs["k1"] = 1.0
        assert dltool.find_roots(-5.0, coeffs, 0.0, 10.0) == {"status": "none", "roots": []}
        res = dltool.find_roots(5.0, coeffs, 0.0, 5.0)
        assert [r["x"] for r in res["roots"]] == [5.0]

    def test_all_eight_coefficients_contribute(self):
        """k1..k8 全部参与：f=x^8+x^7+…+x-0.5 只靠首项非零时检不出根。"""
        coeffs = _empty_coeffs()
        for i in range(1, 9):
            coeffs[f"k{i}"] = 1.0
        res = dltool.find_roots(0.5, coeffs, 0.0, 1.0)
        assert res["status"] == "ok"
        assert len(res["roots"]) == 1
        assert res["roots"][0]["residual"] < 1e-9


class TestRoundTieDirection:
    """_round 的半值方向必须是 JS 的 Math.round（= floor(t+0.5)，往 +∞ 破平），
    不是 Python round() 的银行家舍入（往偶数破平）。M3-10 修复轮 / Important 1。

    期望值按 JS 语义手算并经 node 实测复核（Math.round(x*1e10)/1e10）：
      0.00000000035 → t=3.5  → floor(4.0)=4 → 0.0000000004
      -0.00000000025 → t=-2.5 → floor(-2.0)=-2 → -0.0000000002（朝 +∞，不是 -0.0000000003）
      0.00000000025 → t=2.5  → floor(3.0)=3 → 0.0000000003
    回归成 round(x, 10) 会怎么红：-0.00000000025 变成 -0.0000000003、
    0.00000000035 变成 0.0000000003（两条断言当场失败；实测见修复报告）。
    反过来说 0.00000000025 **不是**判据——距它最近的双精度落在平局点上方，
    round(·, 10) 看到的不是平局，两种写法都给 0.0000000003。这条留在这里只是把
    卡面写明的正半值期望钉住，真正执法的是上面两条同号反号各一的判据。
    """

    def test_round_breaks_ties_toward_positive_infinity(self):
        assert dltool._round(0.00000000035) == 0.0000000004
        assert dltool._round(-0.00000000025) == -0.0000000002
        assert dltool._round(0.00000000025) == 0.0000000003
        # 更多 JS 实测同值的半值刻度（floor(t+0.5) 与银行家舍入在这些点上分道）
        assert dltool._round(0.00000000015) == 0.0000000002
        assert dltool._round(0.00000000045) == 0.0000000005
        assert dltool._round(-0.00000000035) == -0.0000000003
        assert dltool._round(-0.00000000015) == -0.0000000001

    def test_round_is_identity_like_on_non_ties(self):
        """非平局值仍然是「四舍五入到 10 位」，两条语义只在 .5 上分岔。"""
        assert dltool._round(0.123456789012345) == 0.123456789
        assert dltool._round(1.00000000004) == 1.0
        assert dltool._round(-2.0000000006) == -2.0000000006
        assert dltool._round(0.0) == 0.0


class TestForward:
    def test_forward_page_case_input_index_and_annotations(self):
        """tests.js「DL：正向计算保留 input_index，越界行标注而非丢弃」逐字对照。"""
        coeffs = _empty_coeffs()
        coeffs["k1"] = 2.0
        coeffs["b"] = 1.0
        item = {"coeffs": coeffs, "x_min": 0, "x_max": 10, "y_min": None, "y_max": 15}
        r = dltool.forward(item, "rp_width", [-1, 3, 9])
        assert [row["input_index"] for row in r] == [0, 1, 2]
        assert [row["status"] for row in r] == ["out_of_x_range", "ok", "out_of_y_range"]
        assert r[1]["y"] == 7
        # 越界行保留但不产出计算字段（与 dlmath.js 形状一致）
        assert set(r[0]) == {"input_index", "x", "status"}
        assert set(r[2]) == {"input_index", "x", "y_base", "y", "status"}
        assert r[2]["y_base"] == 19 and r[2]["y"] == 19

    def test_forward_pp_energy_scale(self):
        """PP = f(x) × 传输系数 ÷ 功率转换系数（§9.8.7）：y_base 是 f(x)，y 是换算值。"""
        coeffs = _empty_coeffs()
        coeffs["k1"] = 2.0
        coeffs["b"] = 1.0
        item = {"coeffs": coeffs, "x_min": None, "x_max": None, "y_min": None, "y_max": None,
                "trans_coeff": 2.0, "power_conv_coeff": 0.5}
        r = dltool.forward(item, "pp_energy", [1.0])
        assert r[0]["y_base"] == 3.0
        assert r[0]["y"] == 12.0
        assert r[0]["status"] == "ok"

    def test_forward_zero_y_bound_is_real(self):
        """y_min=0.0 是真实下界而非「不限制」：y=-1 标注越界、y=0 正常。"""
        coeffs = _empty_coeffs()
        coeffs["k1"] = 1.0
        item = {"coeffs": coeffs, "x_min": -10.0, "x_max": 10.0, "y_min": 0.0, "y_max": None}
        r = dltool.forward(item, "rp_width", [-1.0, 0.0])
        assert [row["status"] for row in r] == ["out_of_y_range", "ok"]
        assert len(r) == 2


class TestReverse:
    def test_reverse_page_case_tangent_and_multiple(self):
        """tests.js「DL：反向求解发现切触重根与多解」逐字对照（容差同款 1e-6）。"""
        rows, exhaustive = dltool.reverse(_sq_item(), "rp_width", [0, 4])
        assert exhaustive is False
        assert [row["input_index"] for row in rows] == [0, 1]
        assert rows[0]["status"] == "single"
        assert abs(rows[0]["x_values"][0] - 3) < 1e-6
        assert rows[1]["status"] == "multiple"
        assert [round(v) for v in rows[1]["x_values"]] == [1, 5]
        assert len(rows[1]["residuals"]) == 2
        assert rows[0]["y_base"] == 0.0 and rows[1]["y_base"] == 4.0

    def test_reverse_page_case_constant_identity_and_none(self):
        """tests.js「DL：常数函数返回 identity / none」逐字对照（x 范围全 null）。"""
        coeffs = _empty_coeffs()
        coeffs["b"] = 2.0
        c = {"coeffs": coeffs, "x_min": None, "x_max": None, "y_min": None, "y_max": None}
        rows, _ = dltool.reverse(c, "rp_width", [2])
        assert rows[0]["status"] == "identity"
        assert rows[0]["x_values"] == []
        rows, _ = dltool.reverse(c, "rp_width", [3])
        assert rows[0]["status"] == "none"

    def test_reverse_marks_out_of_y_range_without_dropping(self):
        """越界行标注不丢弃；该行没有 y_base（dlmath.js 形状），y_min=0 是真实边界。"""
        rows, _ = dltool.reverse(_sq_item(y_min=0.0, y_max=10.0), "rp_width", [-1, 0])
        assert len(rows) == 2
        assert rows[0]["status"] == "out_of_y_range"
        assert rows[0]["input_index"] == 0
        assert "y_base" not in rows[0]
        assert rows[0]["x_values"] == []
        assert rows[1]["status"] == "single"

    def test_reverse_pp_energy_divides_by_scale(self):
        """PP 反向：先 y ÷ 换算值得 y_base，再对 y_base 求根。"""
        coeffs = _empty_coeffs()
        coeffs["k1"] = 2.0
        coeffs["b"] = 1.0
        item = {"coeffs": coeffs, "x_min": None, "x_max": None, "y_min": None, "y_max": None,
                "trans_coeff": 2.0, "power_conv_coeff": 0.5}
        rows, _ = dltool.reverse(item, "pp_energy", [12.0])
        assert rows[0]["y_base"] == 3.0
        assert rows[0]["x_values"] == pytest.approx([1.0], abs=1e-9)
        assert rows[0]["status"] == "single"

    def test_reverse_null_x_range_falls_back_to_search_window(self):
        """x 范围 null → 在 dlmath.js 的 ±100 搜索窗内求根（若把 null 当 0 会退化成 [0,0] 检不到根）。"""
        rows, _ = dltool.reverse(_sq_item(x_min=None, x_max=None), "rp_width", [4])
        assert rows[0]["status"] == "multiple"
        assert sorted(round(v) for v in rows[0]["x_values"]) == [1, 5]

    def test_reverse_multiple_rows_keep_order(self):
        """多行输入逐行返回、顺序对应（UC-37「多解给出选择」的数据前提）。"""
        rows, _ = dltool.reverse(_sq_item(), "rp_width", [4, 0, -1])
        assert [row["input_index"] for row in rows] == [0, 1, 2]
        assert [row["status"] for row in rows] == ["multiple", "single", "none"]


class TestMultiCheck:
    def test_multi_check_parabola_cases_match_sampling_contract(self):
        """采样参数逐字对齐 dlmath.js multiCheck（y 轴 21 点、上限 8 例、findRoots 2000 样本）：
        (x-3)^2 在 [-10,10]、观测域 [0,169] 下只有 y=8.45×1…×5 有双根，第 6 点起正根越上界。"""
        res = dltool.multi_check(_sq_item(), "rp_width")
        assert res["has_multi"] is True
        assert len(res["cases"]) == 5
        assert [set(c) for c in res["cases"]] == [{"y", "x_values"}] * 5
        assert res["cases"][0]["y"] == pytest.approx(8.45, abs=1e-12)
        assert res["cases"][0]["x_values"] == pytest.approx(
            [3 - math.sqrt(8.45), 3 + math.sqrt(8.45)], abs=1e-9)

    def test_multi_check_monotonic_has_no_cases(self):
        coeffs = _empty_coeffs()
        coeffs["k1"] = 1.0
        item = {"coeffs": coeffs, "x_min": -10.0, "x_max": 10.0, "y_min": None, "y_max": None}
        res = dltool.multi_check(item, "rp_energy")
        assert res == {"has_multi": False, "cases": []}

    def test_multi_check_null_vs_zero_x_range(self):
        """同一 f(x)=x²：x 范围 null → ±100 窗内 ±√y 双根；x_min=0.0 → 只剩单根。0 不是 null。"""
        coeffs = _empty_coeffs()
        coeffs["k2"] = 1.0
        null_item = {"coeffs": coeffs, "x_min": None, "x_max": None, "y_min": None, "y_max": None}
        assert dltool.multi_check(null_item, "rp_energy")["has_multi"] is True
        zero_item = {"coeffs": coeffs, "x_min": 0.0, "x_max": None, "y_min": None, "y_max": None}
        assert dltool.multi_check(zero_item, "rp_energy")["has_multi"] is False

    def test_multi_check_pp_energy_maps_case_y_back_to_output(self):
        """PP 的多解行：判定在 base 域（y ÷ s），cases[].y 换算回输出域 round(yb × s)。"""
        item = _sq_item(y_max=20.0, trans_coeff=2.0, power_conv_coeff=0.5)
        res = dltool.multi_check(item, "pp_energy")
        assert res["has_multi"] is True
        assert len(res["cases"]) == 8  # 与 dlmath.js 的 cases.length < 8 上限一致
        # base 域试探点 = 0 + (20÷4)×i/20 = 0.25i；首案 i=1：yb=0.25 → y=round(0.25×4)=1.0，
        # (x-3)²=0.25 → x = 3±0.5
        assert res["cases"][0]["y"] == 1.0
        assert res["cases"][0]["x_values"] == pytest.approx([2.5, 3.5], abs=1e-9)


class TestTransCoeffZeroDividesByS:
    """trans_coeff == 0.0 是 validate **放行**的配置，而 reverse/multi_check 会 ÷ s 崩
    （s = trans_coeff / power_conv_coeff = 0.0）。M3-10 修复轮 / Important 2 + Minor 3。

    本卡只钉现状，不改判：把 ZeroDivisionError 翻成 400（或出 spec 让 validate 拒绝
    trans_coeff=0）归 M3-11（app/services/dl.py）。M3-11 改了行为，这三条就该同步改，
    届时它们是提示「有一处契约变化需要跟着落测」的哨兵，而不是回归失败。
    JS 侧对照：dlmath.js 同样不拒绝 trans_coeff=0，但它算出 Infinity/NaN 继续跑，
    Python 侧按 _scale_of 的既有裁定选择响亮失败。
    """

    def _zero_trans_item(self, **over):
        coeffs = _empty_coeffs()
        coeffs["k1"] = -6.0
        coeffs["k2"] = 1.0
        coeffs["b"] = 9.0
        item = {"coeffs": coeffs, "x_min": -10.0, "x_max": 10.0,
                "y_min": 0.0, "y_max": 100.0, "trans_coeff": 0.0, "power_conv_coeff": 1.0}
        item.update(over)
        return item

    def test_validate_accepts_zero_trans_coeff(self):
        """validate 只拒绝 power_conv_coeff == 0.0，从不看 trans_coeff。"""
        assert dltool.validate(self._zero_trans_item(), "pp_energy") == ""

    def test_reverse_raises_zero_division(self):
        item = self._zero_trans_item()
        with pytest.raises(ZeroDivisionError):
            dltool.reverse(item, "pp_energy", [4.0])
        # y=0.0 也一样崩：Python 的浮点除零不产出 inf/nan，而是直接抛
        with pytest.raises(ZeroDivisionError):
            dltool.reverse(item, "pp_energy", [0.0])

    def test_multi_check_raises_zero_division_when_y_range_is_set(self):
        """y_min/y_max 任一非 None 就走到 ÷ s；两者都为 None 时用观测域，不崩。"""
        with pytest.raises(ZeroDivisionError):
            dltool.multi_check(self._zero_trans_item(), "pp_energy")
        with pytest.raises(ZeroDivisionError):
            dltool.multi_check(self._zero_trans_item(y_min=None), "pp_energy")
        assert dltool.multi_check(self._zero_trans_item(y_min=None, y_max=None),
                                 "pp_energy")["has_multi"] is True

    def test_forward_survives_zero_trans_coeff(self):
        """forward 是乘 s，不是除 s —— 同一份合法配置下它只有 0 输出，不抛。"""
        rows = dltool.forward(self._zero_trans_item(), "pp_energy", [1.0])
        assert rows[0]["y_base"] == 4.0
        assert rows[0]["y"] == 0.0


class TestValidate:
    def _pp_item(self):
        return {"coeffs": _empty_coeffs(), "x_min": None, "x_max": None,
                "y_min": None, "y_max": None, "trans_coeff": 1.0, "power_conv_coeff": 1.0}

    def test_validate_rejects_zero_power_conversion_coeff(self):
        """卡面要点 5：功率转换系数为 0 → 拒绝（除零），文案与 dlmath.js 逐字一致。"""
        item = self._pp_item()
        item["power_conv_coeff"] = 0.0
        assert dltool.validate(item, "pp_energy") == "功率转换系数不能为 0"

    def test_validate_rejects_non_finite_coefficients(self):
        coeffs = _empty_coeffs()
        coeffs["k3"] = float("nan")
        item = {"coeffs": coeffs, "x_min": None, "x_max": None, "y_min": None, "y_max": None}
        assert dltool.validate(item, "rp_width") == "系数必须是有限数值"
        coeffs["k3"] = float("inf")
        assert dltool.validate(item, "rp_width") == "系数必须是有限数值"

    def test_validate_rejects_non_finite_pp_coeffs(self):
        item = self._pp_item()
        item["trans_coeff"] = float("inf")
        assert dltool.validate(item, "pp_energy") == "传输系数与功率转换系数必须是有限数值"
        item = self._pp_item()
        item["power_conv_coeff"] = None
        assert dltool.validate(item, "pp_energy") == "传输系数与功率转换系数必须是有限数值"

    def test_validate_requires_min_less_than_max(self):
        """x_min >= x_max 拒绝（等号也拒绝）；null 端点跳过检查。"""
        item = self._pp_item()
        item["x_min"] = 5.0
        item["x_max"] = 3.0
        assert dltool.validate(item, "pp_energy") == "x 范围下限必须小于上限"
        item["x_min"] = 3.0
        assert dltool.validate(item, "pp_energy") == "x 范围下限必须小于上限"
        item["x_min"] = None
        item["y_min"] = 2.0
        item["y_max"] = 2.0
        assert dltool.validate(item, "pp_energy") == "y 范围下限必须小于上限"

    def test_validate_empty_for_valid_item(self):
        assert dltool.validate(self._pp_item(), "pp_energy") == ""
        rp = {"coeffs": _empty_coeffs(), "x_min": None, "x_max": None,
              "y_min": None, "y_max": None}
        assert dltool.validate(rp, "rp_energy") == ""

    def test_keys_contract(self):
        """M3-11 依赖的常量命名与内容。"""
        assert dltool.KEYS == ("k1", "k2", "k3", "k4", "k5", "k6", "k7", "k8")

