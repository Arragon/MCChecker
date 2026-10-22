"""严格配置数值模型（M5-05 / INH-1939）。

旧链路（`core/parser.py` 的 `to_compact`）把值交给前端 JS 处理，`Number` 在
2^53 以上会丢精度，`0`/`false`/`""`/`null` 也容易混。本模块提供**不依赖 JS**
的一套值模型，供配置域独立使用；旧解析链路一行不改。

三条硬规则：

1. 数值一律 `decimal.Decimal`，从文本构造，不经过 `float`；
2. `missing`（键不存在）与 `null`（显式空）是两个不同的东西，`""`/`0`/`false`
   各自是合法值；
3. 单位换算必须可逆；不可逆即 `VALUE_LOSSY_CONVERSION`，绝不静默取整。
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .errors import (
    INVALID_INPUT,
    VALUE_LOSSY_CONVERSION,
    VALUE_OUT_OF_RANGE,
    AppError,
)

#: 值类型。`null` 是**显式空**，与 `MISSING` 不同。
KINDS = ("string", "bool", "int", "decimal", "enum", "null", "raw")

#: 单位表：`单位 -> (量纲, 换算到基准单位的系数)`。基准：s、mm、deg。
UNITS: Dict[str, Tuple[str, str]] = {
    "s": ("duration", "1"),
    "ms": ("duration", "0.001"),
    "us": ("duration", "0.000001"),
    "mm": ("length", "1"),
    "um": ("length", "0.001"),
    "m": ("length", "1000"),
    "deg": ("angle", "1"),
    "rad": ("angle", "57.295779513082320876798154814105"),
}


class _Missing:
    """`missing` 单例：键不存在。`repr` 保持可读，避免误当 None。"""

    _instance: Optional["_Missing"] = None

    def __new__(cls) -> "_Missing":
        if cls._instance is None:
            cls._instance = super(_Missing, cls).__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<missing>"

    def __bool__(self) -> bool:
        return False


MISSING = _Missing()


class TypedValue:
    """一个带类型的值：`kind` + 规范化文本 + 可选单位。

    ``to_json()`` 是**对外传输形态**（JS 侧按 `type` 解析 `text`，不碰 `Number`）。
    """

    __slots__ = ("kind", "text", "unit")

    def __init__(self, kind: str, text: str, unit: Optional[str] = None) -> None:
        if kind not in KINDS:
            raise AppError(INVALID_INPUT, "未知值类型：%s" % kind)
        self.kind = kind
        self.text = text
        self.unit = unit

    # -- 构造 ---------------------------------------------------------------

    @classmethod
    def null(cls) -> "TypedValue":
        return cls("null", "null")

    @classmethod
    def of(cls, raw: Any, unit: Optional[str] = None) -> "TypedValue":
        """从 Python 原生值构造（测试与内部使用；布尔必须先于数字判断）。"""
        if raw is None:
            return cls.null()
        if isinstance(raw, bool):
            return cls("bool", "true" if raw else "false")
        if isinstance(raw, int):
            return cls("int", str(raw))
        if isinstance(raw, Decimal):
            return cls("decimal", format(raw, "f"), unit)
        if isinstance(raw, str):
            return cls("string", raw)
        raise AppError(INVALID_INPUT, "不支持的值类型：%s" % type(raw).__name__)

    @classmethod
    def from_json(cls, payload: Any) -> "TypedValue":
        """反序列化 `to_json()` 的产物；形状不对即拒绝。"""
        if not isinstance(payload, dict):
            raise AppError(INVALID_INPUT, "类型化值必须是对象")
        kind = payload.get("type")
        text = payload.get("text")
        if not isinstance(kind, str) or not isinstance(text, str):
            raise AppError(INVALID_INPUT, "类型化值缺 type/text")
        unit = payload.get("unit")
        value = cls(kind, text, unit if isinstance(unit, str) else None)
        value.validate()
        return value

    def to_json(self) -> Dict[str, Any]:
        return {"type": self.kind, "text": self.text, "unit": self.unit}

    # -- 校验 ---------------------------------------------------------------

    def validate(self, constraints: Optional[Dict[str, Any]] = None) -> None:
        """按**类型与约束**校验自身；**不校验单位能否换算**。

        单位分两件事：`mm/s`、`rpm`、`%` 这类派生单位是合法声明，只是换不了；
        `s`/`ms`/`deg` 这类才在换算表里。把"换不了"当成"不合法"，会让整份
        带派生单位的配置无法保存——所以单位成员资格由 `convert()` 在**真的
        需要换算时**判定，这里只管类型。
        """
        if self.kind == "bool" and self.text not in ("true", "false"):
            raise AppError(INVALID_INPUT, "布尔值只能是 true/false")
        if self.kind == "int":
            try:
                int(self.text)
            except ValueError:
                raise AppError(INVALID_INPUT, "整数格式不合法：%s" % self.text)
        if self.kind == "decimal":
            number = self.decimal()
            if number is None:  # pragma: no cover - decimal() 已校验
                raise AppError(INVALID_INPUT, "数值格式不合法：%s" % self.text)
        if constraints:
            self._validate_constraints(constraints)

    def check_unit(self) -> None:
        """需要换算的场合显式调这个：表外单位在这里才被判非法。"""
        if self.unit is not None and self.unit not in UNITS:
            raise AppError(INVALID_INPUT, "未知单位：%s" % self.unit)


    def _validate_constraints(self, constraints: Dict[str, Any]) -> None:
        if self.kind in ("int", "decimal"):
            number = self.decimal()
            low = constraints.get("min")
            high = constraints.get("max")
            if low is not None and number < Decimal(str(low)):
                raise AppError(
                    VALUE_OUT_OF_RANGE, "低于下限", value=self.text, min=str(low), field=None
                )
            if high is not None and number > Decimal(str(high)):
                raise AppError(
                    VALUE_OUT_OF_RANGE, "高于上限", value=self.text, max=str(high), field=None
                )
        enum = constraints.get("enum") or []
        if enum and self.text not in enum:
            raise AppError(VALUE_OUT_OF_RANGE, "不在允许的枚举内", value=self.text, enum=enum)
        max_length = constraints.get("max_length")
        if max_length is not None and self.kind == "string" and len(self.text) > int(max_length):
            raise AppError(VALUE_OUT_OF_RANGE, "字符串超长", length=len(self.text))

    # -- 取值 ---------------------------------------------------------------

    def decimal(self) -> Optional[Decimal]:
        """数值类型的 `Decimal`；非数值返回 None。拒绝 NaN/Infinity。"""
        if self.kind not in ("int", "decimal"):
            return None
        try:
            number = Decimal(self.text)
        except (InvalidOperation, ValueError):
            raise AppError(INVALID_INPUT, "数值格式不合法：%s" % self.text)
        if not number.is_finite():
            raise AppError(INVALID_INPUT, "不接受 NaN/Infinity")
        return number

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return "TypedValue(%s, %r, %r)" % (self.kind, self.text, self.unit)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TypedValue):
            return NotImplemented
        return (self.kind, self.text, self.unit) == (other.kind, other.text, other.unit)

    def __hash__(self) -> int:
        return hash((self.kind, self.text, self.unit))


# ---------------------------------------------------------------------------
# JSON 严格解析
# ---------------------------------------------------------------------------


def _no_duplicate_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    seen = set()
    for key, _value in pairs:
        if key in seen:
            raise AppError(INVALID_INPUT, "JSON 含重复键：%s" % key)
        seen.add(key)
    return dict(pairs)


def _reject_constant(name: str) -> Any:
    raise AppError(INVALID_INPUT, "JSON 不接受 %s" % name)


def loads_strict(text: str) -> Any:
    """严格 JSON 解析：拒绝重复键、NaN/Infinity，数字用 `Decimal`。

    重复键在普通 `json.loads` 里是"后者覆盖前者"，对配置文件是静默数据丢失。
    """
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError(INVALID_INPUT, "JSON 不是合法 UTF-8：%s" % exc)
    if not isinstance(text, str):
        raise AppError(INVALID_INPUT, "JSON 输入必须是文本")
    try:
        return json.loads(
            text,
            object_pairs_hook=_no_duplicate_keys,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_constant,
        )
    except AppError:
        raise
    except (ValueError, UnicodeDecodeError) as exc:
        raise AppError(INVALID_INPUT, "JSON 解析失败：%s" % exc)


def parse_with_unit(raw: Any, kind: str, unit: Optional[str] = None) -> TypedValue:
    """按**定义里声明的单位**解析：换算表之外的单位照原样保留。

    `mm/s`、`rpm`、`%` 这类派生单位在现场文件里到处都是。换算表只负责一件事——
    "这两个单位之间能不能无损换算"；它不负责"单位必须出现在这张表里"。
    因此这里与 `parse_typed` 现已同义（校验分工见 `TypedValue.validate`），
    保留这个名字是因为调用点想表达的是"我知道这个单位是定义里声明的"。
    """
    return parse_typed(raw, kind, unit)


def parse_typed(raw: Any, kind: str, unit: Optional[str] = None) -> TypedValue:
    """把 JSON 原生值转成 `TypedValue`，并校验类型一致性。"""
    if raw is None:
        value = TypedValue.null()
    elif isinstance(raw, bool):
        value = TypedValue("bool", "true" if raw else "false")
    elif isinstance(raw, int):
        # 整数也要带上单位：JSON 里 `650` 与 `0.65` 都可能是同一个带单位参数，
        # 单位丢了会让后续"同值不同单位"的判等与展示全错。
        value = TypedValue("int", str(raw), unit if kind in ("int", "decimal") else None)
    elif isinstance(raw, Decimal):
        value = TypedValue("decimal", format(raw, "f"), unit)
    elif isinstance(raw, float):  # 只可能来自非严格解析，明确拒绝
        raise AppError(INVALID_INPUT, "浮点数必须按文本传入，避免精度丢失")
    elif isinstance(raw, str):
        value = TypedValue(kind if kind != "string" else "string", raw, unit)
        value.validate()
    else:
        raise AppError(INVALID_INPUT, "不支持的值：%s" % type(raw).__name__)

    if kind == "null" and value.kind != "null":
        raise AppError(INVALID_INPUT, "期望空值")
    if kind == "bool" and value.kind != "bool":
        raise AppError(INVALID_INPUT, "期望布尔值")
    if kind in ("int", "decimal") and value.kind not in ("int", "decimal"):
        raise AppError(INVALID_INPUT, "期望数值，实际是 %s" % value.kind)
    if kind == "enum" and value.kind not in ("enum", "string"):
        raise AppError(INVALID_INPUT, "期望枚举文本")
    return value


# ---------------------------------------------------------------------------
# 单位换算与比较
# ---------------------------------------------------------------------------


def _format_decimal(number: Decimal) -> str:
    """去掉无意义的尾随零（`0.650` → `0.65`、`650.000` → `650`），不引入指数。"""
    return format(number.normalize(), "f")


def convert(value: TypedValue, to_unit: str) -> TypedValue:
    """单位换算。不可逆（换回去不等于原值）即抛 `VALUE_LOSSY_CONVERSION`。"""
    if value.kind == "null" or value.unit is None:
        raise AppError(INVALID_INPUT, "该值没有单位，无法换算")
    if to_unit not in UNITS:
        raise AppError(INVALID_INPUT, "未知目标单位：%s" % to_unit)
    if value.unit not in UNITS:
        # 声明单位不在换算表里是合法状态（`mm/s` 之类），但**不能**换出去——
        # 这里必须抛 AppError 而不是让 `UNITS[...]` 抛 KeyError 冒成 500。
        raise AppError(
            INVALID_INPUT,
            "源单位不在换算表内，无法换算：%s" % value.unit,
            from_unit=value.unit,
        )
    from_kind, from_factor = UNITS[value.unit]
    to_kind, to_factor = UNITS[to_unit]
    if from_kind != to_kind:
        raise AppError(
            INVALID_INPUT,
            "量纲不同，不能换算：%s → %s" % (value.unit, to_unit),
        )
    number = value.decimal()
    if number is None:
        raise AppError(INVALID_INPUT, "只有数值可以换算")
    converted = number * Decimal(from_factor) / Decimal(to_factor)
    back = converted * Decimal(to_factor) / Decimal(from_factor)
    if back != number:
        raise AppError(
            VALUE_LOSSY_CONVERSION,
            "换算不可逆，已阻断",
            value=value.text,
            from_unit=value.unit,
            to_unit=to_unit,
        )
    return TypedValue(
        "decimal" if value.kind == "decimal" else "int",
        _format_decimal(converted),
        to_unit,
    )


def compare(left: Any, right: Any) -> str:
    """比较两个值（含 `MISSING`）——返回 `equal`/`different`/`incomparable`/`missing`。

    * 两侧都 missing → `missing`；
    * 一侧 missing → `missing`（缺失不等于删除，更不等于 0）；
    * 数值跨 int/decimal 按十进制比较（`0.650` 与 `0.65` 相等）；
    * 单位不同先换算，换算不可逆则 `incomparable`。
    """
    if left is MISSING or right is MISSING:
        return "missing"
    if not isinstance(left, TypedValue) or not isinstance(right, TypedValue):
        raise AppError(INVALID_INPUT, "compare 只接受 TypedValue 或 MISSING")

    if left.kind == "null" or right.kind == "null":
        return "equal" if left.kind == right.kind else "different"

    if left.kind in ("int", "decimal") and right.kind in ("int", "decimal"):
        if left.unit != right.unit:
            if left.unit is None or right.unit is None:
                return "incomparable"
            try:
                right = convert(right, left.unit)
            except AppError:
                return "incomparable"
        return "equal" if left.decimal() == right.decimal() else "different"

    # 类型不同（如 `0` 与 `false`）就是"变了"：值语义不同，不能判等也不能判不可比
    if left.kind != right.kind:
        return "different"
    return "equal" if left.text == right.text else "different"


def yield_changes(
    old: Dict[str, Any], new: Dict[str, Any]
) -> Iterable[Tuple[str, Any, Any]]:
    """逐参数对比两个 `{param_id: TypedValue}` 映射，产出差异三元组。"""
    for key in sorted(set(old) | set(new)):
        left = old.get(key, MISSING)
        right = new.get(key, MISSING)
        if compare(left, right) not in ("equal",):
            yield key, left, right
