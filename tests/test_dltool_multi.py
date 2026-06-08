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

