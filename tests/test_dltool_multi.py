import pytest
import math
from app.core import dltool
from app.core.dltool import (
    validate_number, validate_interval,
    extract_coefficient, find_polynomial_roots,
    CoefficientResult, DLResult,
)


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
# T11 新增：validate_number
# ---------------------------------------------------------------------------

class TestValidateNumber:
    def test_none_rejected(self):
        with pytest.raises(ValueError, match="required"):
            validate_number(None, "test")

    def test_nan_rejected(self):
        with pytest.raises(ValueError, match="finite"):
            validate_number(float('nan'), "test")

    def test_inf_rejected(self):
        with pytest.raises(ValueError, match="finite"):
            validate_number(float('inf'), "test")

    def test_neg_inf_rejected(self):
        with pytest.raises(ValueError, match="finite"):
            validate_number(float('-inf'), "test")

    def test_zero_accepted(self):
        assert validate_number(0, "test") == 0.0

    def test_negative_accepted(self):
        assert validate_number(-1.5, "test") == -1.5

    def test_string_rejected(self):
        with pytest.raises(TypeError, match="numeric"):
            validate_number("abc", "test")

    def test_int_accepted(self):
        assert validate_number(42, "test") == 42.0


# ---------------------------------------------------------------------------
# T11 新增：validate_interval
# ---------------------------------------------------------------------------

class TestValidateInterval:
    def test_valid_interval(self):
        assert validate_interval(0, 10) == (0.0, 10.0)

    def test_invalid_interval(self):
        with pytest.raises(ValueError, match="Invalid"):
            validate_interval(10, 0)

    def test_equal_bounds(self):
        with pytest.raises(ValueError, match="Invalid"):
            validate_interval(5, 5)

    def test_none_low(self):
        with pytest.raises(ValueError, match="required"):
            validate_interval(None, 10)

    def test_none_high(self):
        with pytest.raises(ValueError, match="required"):
            validate_interval(0, None)

    def test_negative_interval(self):
        assert validate_interval(-10, -1) == (-10.0, -1.0)


# ---------------------------------------------------------------------------
# T11 新增：extract_coefficient
# ---------------------------------------------------------------------------

class TestExtractCoefficient:
    def test_success(self):
        data = [[1, 2], [3, 4]]
        result = extract_coefficient(data, 0, 1)
        assert result.status == "success"
        assert result.value == 2.0

    def test_missing_data(self):
        result = extract_coefficient(None, 0, 0)
        assert result.status == "missing"

    def test_missing_row(self):
        data = [[1, 2]]
        result = extract_coefficient(data, 5, 0)
        assert result.status == "missing"

    def test_missing_column(self):
        data = [[1, 2]]
        result = extract_coefficient(data, 0, 5)
        assert result.status == "missing"

    def test_none_value(self):
        data = [[1, None]]
        result = extract_coefficient(data, 0, 1)
        assert result.status == "missing"

    def test_nan_value_invalid(self):
        data = [[float('nan')]]
        result = extract_coefficient(data, 0, 0)
        assert result.status == "invalid"

    def test_string_value_invalid(self):
        data = [["abc"]]
        result = extract_coefficient(data, 0, 0)
        assert result.status == "invalid"

    def test_zero_value_success(self):
        """真正的 0 应该被接受，不能被替换"""
        data = [[0]]
        result = extract_coefficient(data, 0, 0)
        assert result.status == "success"
        assert result.value == 0.0


# ---------------------------------------------------------------------------
# T11 新增：find_polynomial_roots
# ---------------------------------------------------------------------------

class TestPolynomialRoots:
    def test_simple_linear_root(self):
        # x - 2 = 0, root at x=2
        coeffs = [1, -2]
        results = find_polynomial_roots(coeffs, (0, 10))
        assert any(r.status == "success" and abs(r.value - 2.0) < 1e-6 for r in results)

    def test_endpoint_root_low(self):
        # x = 0 at endpoint low=0
        coeffs = [1, 0]
        results = find_polynomial_roots(coeffs, (0, 10))
        assert any(r.status == "success" and abs(r.value) < 1e-6 for r in results)

    def test_no_real_root(self):
        # x^2 + 1 = 0, no real root
        coeffs = [1, 0, 1]
        results = find_polynomial_roots(coeffs, (-10, 10))
        assert all(r.status != "success" for r in results)

    def test_zero_polynomial(self):
        coeffs = [0]
        results = find_polynomial_roots(coeffs, (-10, 10))
        assert any(r.status == "invalid" and "zero polynomial" in r.message for r in results)

    def test_constant_polynomial_nonzero(self):
        coeffs = [5]
        results = find_polynomial_roots(coeffs, (-10, 10))
        assert any(r.status == "no_real_root" for r in results)

    def test_even_multiplicity_tangent(self):
        # (x-2)^2 = x^2 - 4x + 4, tangent root at x=2
        coeffs = [1, -4, 4]
        results = find_polynomial_roots(coeffs, (0, 10))
        # 应能找到或标记
        assert len(results) > 0

    def test_multiple_roots(self):
        # x^2 - 1 = 0, roots at x=-1 and x=1
        coeffs = [1, 0, -1]
        results = find_polynomial_roots(coeffs, (-5, 5))
        success_vals = sorted([r.value for r in results if r.status == "success"])
        assert len(success_vals) >= 2
        assert abs(success_vals[0] - (-1.0)) < 1e-6
        assert abs(success_vals[-1] - 1.0) < 1e-6

    def test_invalid_interval_rejected(self):
        coeffs = [1, -1]
        with pytest.raises(ValueError, match="Invalid"):
            find_polynomial_roots(coeffs, (10, 0))

    def test_residual_reported(self):
        # root with residual info
        coeffs = [1, -3]  # x - 3 = 0
        results = find_polynomial_roots(coeffs, (0, 10))
        success = [r for r in results if r.status == "success"]
        assert len(success) >= 1
        assert success[0].residual is not None
        assert success[0].residual < 1e-8

