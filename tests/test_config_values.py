"""严格值模型（M5-05 / INH-1939）。

卡面验收：missing/null/零/false/精确 Decimal、重复 JSON 键、映射歧义。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core import config_values as cv
from app.core.errors import AppError


def test_large_integer_is_not_rounded() -> None:
    """JS 的 Number 在 2^53 以上丢精度；本模型必须逐位保留。"""
    parsed = cv.loads_strict('{"n": 10000000000000001}')
    assert parsed["n"] == 10000000000000001
    assert isinstance(parsed["n"], int)


def test_decimal_keeps_exact_scale() -> None:
    parsed = cv.loads_strict('{"v": 0.1}')
    assert parsed["v"] == Decimal("0.1")
    assert format(parsed["v"], "f") == "0.1"


def test_duplicate_json_keys_are_rejected() -> None:
    with pytest.raises(AppError) as error:
        cv.loads_strict('{"PULSE": "650", "PULSE": "700"}')
    assert error.value.code == "INVALID_INPUT"


def test_nan_and_infinity_are_rejected() -> None:
    for text in ('{"v": NaN}', '{"v": Infinity}', '{"v": -Infinity}'):
        with pytest.raises(AppError):
            cv.loads_strict(text)


def test_invalid_utf8_is_rejected() -> None:
    with pytest.raises(AppError):
        cv.loads_strict(b'{"v": "\xff\xfe"}')


def test_missing_null_empty_zero_false_are_all_distinct() -> None:
    missing = cv.MISSING
    null = cv.TypedValue.null()
    empty = cv.TypedValue("string", "")
    zero = cv.TypedValue("int", "0")
    false = cv.TypedValue("bool", "false")

    assert cv.compare(missing, null) == "missing"
    assert cv.compare(null, empty) == "different"
    assert cv.compare(null, zero) == "different"
    assert cv.compare(zero, false) == "different"
    assert cv.compare(empty, zero) == "different"
    assert bool(missing) is False, "missing 不能被当成真值"


def test_bool_is_not_coerced_to_int() -> None:
    value = cv.TypedValue.of(True)
    assert value.kind == "bool"
    assert value.decimal() is None
    assert cv.compare(value, cv.TypedValue("int", "1")) == "different"


def test_typed_value_roundtrip_json() -> None:
    value = cv.TypedValue("decimal", "0.650", "s")
    restored = cv.TypedValue.from_json(value.to_json())
    assert restored == value
    assert restored.to_json() == {"type": "decimal", "text": "0.650", "unit": "s"}


def test_typed_value_rejects_bad_shape() -> None:
    for payload in ({}, {"type": "decimal"}, {"type": "decimal", "text": 1}):
        with pytest.raises(AppError):
            cv.TypedValue.from_json(payload)


def test_numeric_equality_ignores_scale_but_not_type_family() -> None:
    assert cv.compare(cv.TypedValue("decimal", "0.650"), cv.TypedValue("decimal", "0.65")) == "equal"
    assert cv.compare(cv.TypedValue("int", "3"), cv.TypedValue("decimal", "3.0")) == "equal"
    assert cv.compare(cv.TypedValue("decimal", "0.65", "s"), cv.TypedValue("decimal", "650", "ms")) == "equal"
    assert cv.compare(cv.TypedValue("decimal", "0.65", "s"), cv.TypedValue("decimal", "0.65", "mm")) == "incomparable"


def test_millisecond_conversion_is_lossless() -> None:
    value = cv.TypedValue("decimal", "650", "ms")
    converted = cv.convert(value, "s")
    assert converted.text == "0.65"
    assert converted.unit == "s"
    assert cv.convert(converted, "ms").text == "650"


def test_lossy_conversion_is_blocked() -> None:
    """度/弧度换算在十进制下不可逆，必须明确阻断而不是悄悄取整。"""
    value = cv.TypedValue("decimal", "1", "rad")
    with pytest.raises(AppError) as error:
        cv.convert(value, "deg")
    assert error.value.code == "VALUE_LOSSY_CONVERSION"


def test_large_millisecond_value_converts_exactly() -> None:
    value = cv.TypedValue("decimal", "123456789012345", "ms")
    assert cv.convert(value, "s").text == "123456789012.345"


def test_unit_dimension_mismatch_is_rejected() -> None:
    with pytest.raises(AppError) as error:
        cv.convert(cv.TypedValue("decimal", "1", "s"), "mm")
    assert error.value.code == "INVALID_INPUT"


def test_constraints_are_enforced() -> None:
    constraints = {"min": "0.0", "max": "10.0"}
    cv.TypedValue("decimal", "0.65").validate(constraints)
    with pytest.raises(AppError) as error:
        cv.TypedValue("decimal", "10.5").validate(constraints)
    assert error.value.code == "VALUE_OUT_OF_RANGE"

    cv.TypedValue("enum", "manual").validate({"enum": ["auto", "manual"]})


def test_enum_membership() -> None:
    cv.TypedValue("enum", "auto").validate({"enum": ["auto"]})
    with pytest.raises(AppError) as error:
        cv.TypedValue("enum", "other").validate({"enum": ["auto"]})
    assert error.value.code == "VALUE_OUT_OF_RANGE"


def test_string_max_length() -> None:
    with pytest.raises(AppError):
        cv.TypedValue("string", "abcdef").validate({"max_length": 3})


def test_unknown_unit_is_rejected() -> None:
    with pytest.raises(AppError):
        cv.TypedValue("decimal", "1", "furlong").validate()


def test_parse_typed_rejects_float_and_mismatched_kind() -> None:
    with pytest.raises(AppError):
        cv.parse_typed(1.5, "decimal")
    with pytest.raises(AppError):
        cv.parse_typed("abc", "decimal")
    with pytest.raises(AppError):
        cv.parse_typed("abc", "bool")
    assert cv.parse_typed("0.65", "decimal", "s").text == "0.65"


def test_yield_changes_reports_only_real_differences() -> None:
    old = {"pm_1": cv.TypedValue("decimal", "0.650")}
    new = {"pm_1": cv.TypedValue("decimal", "0.65"), "pm_2": cv.TypedValue("int", "1")}
    changes = list(cv.yield_changes(old, new))
    assert [item[0] for item in changes] == ["pm_2"]
    assert changes[0][1] is cv.MISSING


def test_declared_unit_outside_table_is_kept_but_not_convertible() -> None:
    """`mm/s` 这类派生单位照原样保留；能不能换算与"能不能存在"是两件事。"""
    value = cv.parse_with_unit("120", "decimal", "mm/s")
    assert value.to_json() == {"type": "decimal", "text": "120", "unit": "mm/s"}
    # 换算表里没有它，因此换算仍然拒绝（而不是悄悄当同单位处理）
    with pytest.raises(AppError):
        cv.convert(value, "mm")

    # 表内的单位照旧走严格解析
    assert cv.parse_with_unit("0.65", "decimal", "s").unit == "s"
    with pytest.raises(AppError):
        cv.parse_typed("1", "decimal", "mm/s")
