"""DL 试算、提取预览与条件保存（规范 §7.11、UC-37/38/39；M3-11 / INH-1896）。

分层（§3.3 / 全局约束）：本模块不 import HTTP，handler 只做「解析 → 授权 → 调这里 →
序列化」；持久化只走 `storage`（`use_profile` / `profile_write`）与 `core.dltool` 现有的
`load_config` / `save_config`；修订号只在**保存成功之后**由本层递增。

契约要点（逐条对应卡面）：

* `PART="settings"`、`ITEMS` 与 `current_rev` / `get_state` / `compute` / `extract` /
  `save_item` 是 M3-12 前端要消费的接口。`rev` = `dltool_config.json` 的 sha1 前 12 位；
  文件不存在时对**默认配置的规范字节**（与 `save_config` 同一份 `json.dumps(...,
  ensure_ascii=False, indent=2)`）取哈希，且**不创建文件**。
* `GET` / `compute` / `extract` 永不写权威数据、永不递增；`params` / `binding` 只在本
  请求内使用。
* `compute` 的响应形状严格按 §7.11：forward `{"results"}`、reverse
  `{"results", "exhaustive": false}`、multi_check `{"has_multi", "cases"}`。
* `extract` 的 `variable_path` 是旧节点 ID；只从 compact 文档的直接子节点取值
  （XML 子元素/属性标签、JSON 键）。`missing` 与 `invalid` **都不进 `coeffs`**，
  更不返回 0（C15）——前端据此保留草稿里的原系数。
* `PUT` 必须 `If-Match`。**缺失 → 409 `REVISION_CONFLICT`**：R17 已按
  `views/dltool.js:84`（永远带该头）与 `:94`（只处理 `REVISION_CONFLICT`）裁定，
  不走卡面里「前端未区分才回落 400」的分支。rev 比较与写入在**同一次机型锁**内完成，
  并发第二个写者只能拿到 409，文件只会被先保存者改一次。
* 保存时 `editing` 写 `false`；范围空值保留 `null`（不并成 0 / ""）；非法数值 400 且
  不落盘；`dltool_config.json` 的字段集合不变——新 item 从**磁盘上现有的 item** 深拷贝，
  只覆盖已知键，payload 里的未知键（含 `coeffs.k9`）一律忽略。PUT 的 body 按 §7.11 是
  **完整 item**：四个范围键缺失 → 400（不再静默清空磁盘值；要清空请显式传 `null`）。
* **极端但有限的数值**：`float()` 自身就溢出的超大整数（如 400 位 JSON 整数）在参数
  校验里直接 400；`1e308` 这类**能通过校验**的值在计算中途溢出成 `inf`/`nan` 时，
  `compute` 把 `OverflowError`/`ValueError` 翻成 400（独立文案，不复用除零那条），
  不改 `app/core/dltool.py`。
* **`trans_coeff=0`（M3-10 的 Important 2）**：`dltool.validate` 只拒绝
  `power_conv_coeff=0`，而 `reverse` / `multi_check` 会 `÷ s`（s=0）抛
  `ZeroDivisionError`。按卡面裁定 **(a)** 在本层拦成 400 `INVALID_INPUT`：三种 mode
  与 save 都在碰磁盘之前拒绝，另在纯计算调用外兜一层 `ZeroDivisionError → 400`，
  不让裸异常漏到 HTTP。**没有改 `app/core/dltool.py`**，M3-10 的
  `tests/test_dltool_multi.py::TestTransCoeffZeroDividesByS` 哨兵继续成立。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from copy import deepcopy
from typing import Any, Dict, List, Optional

from ..core import dltool, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError
from . import documents, revision

logger = logging.getLogger(__name__)

#: DL 配置属设置域（M3-17 会审这个归属）。
PART = "settings"

ITEMS = ("pp_energy", "rp_energy", "rp_width")
MODES = ("forward", "reverse", "multi_check")

_COEFF_FIELDS = dltool.KEYS + ("b",)
_RANGE_FIELDS = ("x_min", "x_max", "y_min", "y_max")

#: 文案与 `mock-api.js` 的 `TRANS_ZERO_MESSAGE` 逐字一致（R19：mock 不发明后端没有的行为）。
TRANS_ZERO_MESSAGE = "传输系数不能为 0"
#: 有限输入在计算中途溢出（`inf`/`NaN`）时的独立文案：不复用除零那条，前端能区分。
OVERFLOW_MESSAGE = "数值超出可计算范围"

_IF_MATCH = "If-Match"


# ---------------------------------------------------------------------------
# 读（§7.11：全部角色）
# ---------------------------------------------------------------------------


def current_rev(pid: str) -> str:
    """当前配置的修订标识：`dltool_config.json` 的 sha1 前 12 位。

    文件不存在时对默认配置的规范字节取哈希（**不建文件**）：这样「还没保存过」也有
    一个稳定 rev，第一次 `PUT` 用 GET 拿到的 rev 就能通过。文件存在时按**原始字节**
    哈希——格式化差异也是内容差异，不能假装同一个 rev。
    """
    with storage.use_profile(pid):
        return _current_rev_in_profile()


def get_state(pid: str) -> Dict[str, Any]:
    """`GET /p/{pid}/dl` → `{"rev", "items": {pp_energy, rp_energy, rp_width}}`。

    `dltool_config.json` 里的 `editing`（v1 的共享编辑态）**读到后忽略**：v2 不做共享
    编辑态（C15），草稿只存在于浏览器内存。

    `cfg` 与 `rev` 必须在**同一把机型锁**内读：`ThreadingHTTPServer` 下 GET 与 PUT
    并发，若两次读之间插进一次保存，就会返回「旧 items + 新 rev」，客户端拿新 rev
    再 PUT 会把对方的保存覆盖掉。锁与 `save_item` 的 `profile_write` 是同一把
    （`storage.profile_lock`，无需另造一套）。
    """
    with storage.use_profile(pid):
        with storage.profile_lock(pid):
            cfg = dltool.load_config()
            rev = _current_rev_in_profile()
            items = {item: deepcopy(cfg["items"][item]) for item in ITEMS}
    return {"rev": rev, "items": items}


# ---------------------------------------------------------------------------
# 试算（§7.11：全部角色；纯计算，不写文件、不递增）
# ---------------------------------------------------------------------------


def compute(
    pid: str,
    item: str,
    mode: str,
    values: Optional[List[Any]],
    params: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """`POST /p/{pid}/dl/{item}/compute`：按**请求里的 params** 计算。

    `pid` 只保留在签名里（M3-12 依赖的接口形状），计算本身不读盘——这也保证
    「用未保存的草稿试算」不会在服务端留下任何痕迹。
    """
    del pid  # 纯计算：不读、不写该机型的任何权威数据
    _require_item(item)
    clean_mode = str(mode or "").strip()
    if clean_mode not in MODES:
        raise AppError(
            INVALID_INPUT,
            "mode 只允许 forward、reverse、multi_check",
            field="mode",
        )
    clean = _validated_params(params, item)
    inputs = _validated_values(values)
    try:
        if clean_mode == "forward":
            return {"results": dltool.forward(clean, item, inputs)}
        if clean_mode == "reverse":
            rows, exhaustive = dltool.reverse(clean, item, inputs)
            return {"results": rows, "exhaustive": exhaustive}
        return dltool.multi_check(clean, item)
    except ZeroDivisionError:
        # 防御层：_validated_params 已在碰磁盘之前拒绝 trans_coeff=0；这里只兜住
        # 任何残留的 ÷0（例如将来 core 新增的除法路径），不让它变成 500。
        raise AppError(INVALID_INPUT, TRANS_ZERO_MESSAGE, field="trans_coeff")
    except (OverflowError, ValueError):
        # 输入本身有限（1e308 之类），但中间量溢出成 inf / nan：`dltool._round` 的
        # `math.floor(inf)` 抛 OverflowError、`math.floor(nan)` 抛 ValueError。
        # 不改 `core/dltool.py`（那里按 JS 语义冻结），服务层统一翻成 400。
        raise AppError(INVALID_INPUT, OVERFLOW_MESSAGE)


# ---------------------------------------------------------------------------
# 提取预览（§7.11：全部角色；只预览，不保存）
# ---------------------------------------------------------------------------


def extract(pid: str, item: str, binding: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`POST /p/{pid}/dl/{item}/extract`：从绑定源文件的 compact 文档取系数。

    `variable_path` 是旧节点 ID（与 v1 绑定配置一致）。只取该节点的**直接子节点**：
    XML 子元素的标签 / 属性的 `@name` 标签、JSON 键。字段缺失（或子节点没有值）→
    `missing`；存在但转不成有限数 → `invalid` + `raw`。两种都**不进 `coeffs`**，
    于是前端保留草稿里的原系数，绝不会被 0 覆盖（C15）。
    """
    _require_item(item)
    if not isinstance(binding, dict):
        raise AppError(INVALID_INPUT, "binding 必须是对象", field="binding")
    source_file = _clean_text(binding.get("source_file"))
    if not source_file:
        raise AppError(NOT_FOUND, "绑定的配置文件不存在", field="source_file")
    variable_path = _clean_text(binding.get("variable_path"))

    try:
        document = documents.load_document(pid, source_file)
    except AppError as exc:
        if exc.code == NOT_FOUND:
            raise AppError(NOT_FOUND, "绑定的配置文件不存在", field="source_file")
        raise

    nodes = document.get("nodes") or {}
    ids = nodes.get("id") or []
    if variable_path not in ids:
        raise AppError(
            NOT_FOUND,
            "在 %s 中未找到 %s" % (source_file, variable_path),
            field="variable_path",
        )
    index = ids.index(variable_path)  # 同名兄弟取第一个，与 v1 / mock 一致
    children = _direct_children(nodes, index)

    field_map = binding.get("field_map")
    if field_map is None:
        field_map = {}
    if not isinstance(field_map, dict):
        raise AppError(INVALID_INPUT, "field_map 必须是对象", field="binding")

    fields: List[Dict[str, Any]] = []
    coeffs: Dict[str, float] = {}
    for raw_field, raw_coeff in field_map.items():
        field = str(raw_field)
        coeff = str(raw_coeff)
        entry: Dict[str, Any] = {"field": field, "coeff": coeff}
        if field not in children or children[field] is None:
            entry["status"] = "missing"
            fields.append(entry)
            continue
        raw_value = children[field]
        try:
            number = float(raw_value)
        except (TypeError, ValueError):
            entry["status"] = "invalid"
            entry["raw"] = raw_value
            fields.append(entry)
            continue
        if not math.isfinite(number):
            entry["status"] = "invalid"
            entry["raw"] = raw_value
            fields.append(entry)
            continue
        entry["status"] = "ok"
        entry["value"] = number
        coeffs[coeff] = number
        fields.append(entry)
    return {"coeffs": coeffs, "fields": fields}


# ---------------------------------------------------------------------------
# 条件保存（§7.11：仅部署者；If-Match）
# ---------------------------------------------------------------------------


def save_item(
    pid: str,
    item: str,
    payload: Optional[Dict[str, Any]],
    if_match: Optional[str],
) -> Dict[str, Any]:
    """`PUT /p/{pid}/dl/{item}` → `{"rev", "item"}`。

    `If-Match` 缺失/空 → 409 `REVISION_CONFLICT`（R17）。载荷的数值校验（含四个范围
    键的齐全性）在**碰磁盘之前**完成；`rev` 比较、读配置、写配置都在同一次
    `profile_write` 内，所以并发第二个写者只能 409，文件只会被先保存者改一次。
    """
    _require_item(item)
    header = str(if_match or "").strip()
    if not header:
        # R17：`views/dltool.js:84` 永远带该头，`:94` 只处理 REVISION_CONFLICT。
        raise AppError(
            REVISION_CONFLICT,
            "缺少 If-Match 头：请先 GET /dl 取得当前 rev",
            header=_IF_MATCH,
        )
    if not isinstance(payload, dict):
        raise AppError(INVALID_INPUT, "请求体必须是完整 item 对象")

    # 范围键必须齐全（§7.11：body 为完整 item）：`_validated_params` 把「缺失」与
    # 「显式 null」都归一成 None，若放行缺失键，部分 body 会**静默清空**磁盘上的四个
    # 范围；而 name/binding 缺失是保留磁盘值——两种语义混在一起最容易丢数据。
    # 要清空请显式传 null。
    missing_ranges = [field for field in _RANGE_FIELDS if field not in payload]
    if missing_ranges:
        raise AppError(
            INVALID_INPUT,
            "保存时必须提供完整的范围字段（缺少：%s）；不需要限制时请显式传 null"
            % "、".join(missing_ranges),
            field=missing_ranges[0],
        )

    # 数值校验先于任何磁盘访问（trans_coeff=0 / 非法值都必须在落盘前 400）。
    clean = _validated_params(payload, item)
    name = _validated_name(payload)
    binding = _validated_binding(payload)

    with storage.use_profile(pid), storage.profile_write(pid):
        if header != _current_rev_in_profile():
            raise AppError(
                REVISION_CONFLICT,
                "计算参数已被他人修改，请刷新后再保存",
            )
        cfg = dltool.load_config()
        cfg["editing"] = False  # v2 不做共享编辑态（C15）
        items = cfg.get("items")
        if not isinstance(items, dict) or not isinstance(items.get(item), dict):
            # `_ensure_defaults` 保证三件都在；走到这里说明文件形状被破坏。
            raise AppError(NOT_FOUND, "计算项不存在")
        new_item = _merged_item(items[item], clean, name, binding)
        items[item] = new_item
        dltool.save_config(cfg)
        new_rev = _current_rev_in_profile()

    revision.bump(pid, PART)
    logger.info("DL 配置已保存：机型=%s 计算项=%s", pid, item)
    return {"rev": new_rev, "item": deepcopy(new_item)}


# ---------------------------------------------------------------------------
# 校验与内部工具
# ---------------------------------------------------------------------------


def _require_item(item: Any) -> str:
    clean = str(item or "")
    if clean not in ITEMS:
        raise AppError(NOT_FOUND, "计算项不存在", item=clean)
    return clean


def _current_rev_in_profile() -> str:
    """调用方必须已在 `storage.use_profile(pid)` 上下文里。"""
    path = dltool._get_config_file()  # 与 load_config/save_config 同一路径口径
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            raw = fh.read()
        return hashlib.sha1(raw).hexdigest()[:12]
    # 文件不存在：对默认配置的规范字节取哈希，不创建文件。
    raw = json.dumps(dltool.load_config(), ensure_ascii=False, indent=2).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _validated_params(raw: Optional[Dict[str, Any]], item: str) -> Dict[str, Any]:
    """把请求里的 params/item 归一成一份只含数值字段的完整 item。

    非数值、非有限数、缺系数、范围倒置、功率转换系数 0 → 400；`trans_coeff=0`
    也在**这里**拒绝（反向/多解检测会 ÷ s）。返回的 dict 不含 name/binding 等
    非数值字段，调用方按需再合并。
    """
    if not isinstance(raw, dict):
        raise AppError(INVALID_INPUT, "参数必须是对象", field="params")
    raw_coeffs = raw.get("coeffs")
    if not isinstance(raw_coeffs, dict):
        raise AppError(INVALID_INPUT, "系数必须是有限数值", field="coeffs")

    coeffs: Dict[str, float] = {}
    for key in _COEFF_FIELDS:
        coeffs[key] = _as_finite_number(
            raw_coeffs.get(key),
            field="coeffs.%s" % key,
            message="系数必须是有限数值",
        )
    clean: Dict[str, Any] = {"coeffs": coeffs}
    for field in _RANGE_FIELDS:
        clean[field] = _as_optional_number(raw.get(field), field=field)

    if item == "pp_energy":
        for field in ("trans_coeff", "power_conv_coeff"):
            clean[field] = _as_finite_number(
                raw.get(field),
                field=field,
                message="传输系数与功率转换系数必须是有限数值",
            )

    error = dltool.validate(clean, item)
    if error:
        # 文案与 `dltool.validate` / `dlmath.js validate` 逐字一致（含「功率转换系数不能为 0」、
        # 「x/y 范围下限必须小于上限」）。
        raise AppError(INVALID_INPUT, error)
    if item == "pp_energy" and clean["trans_coeff"] == 0.0:
        # 裁定 (a)：validate 放行，但 reverse/multi_check 会 ÷ 0；服务层在碰磁盘前拒绝。
        raise AppError(INVALID_INPUT, TRANS_ZERO_MESSAGE, field="trans_coeff")
    return clean


def _as_finite_number(value: Any, *, field: str, message: str) -> float:
    if isinstance(value, bool) or value is None:
        raise AppError(INVALID_INPUT, message, field=field)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError：JSON 里的超大整数（如 400 位）`float()` 直接溢出。
        raise AppError(INVALID_INPUT, message, field=field)
    if not math.isfinite(number):
        raise AppError(INVALID_INPUT, message, field=field)
    return number


def _as_optional_number(value: Any, *, field: str) -> Optional[float]:
    """范围字段：`null` 与空串都保存为 `null`（不是 0、不是 ""；§9.8.7）。"""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _as_finite_number(value, field=field, message="范围必须是有限数值")


def _validated_values(raw: Any) -> List[float]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AppError(INVALID_INPUT, "输入值必须是数组", field="values")
    values: List[float] = []
    for index, value in enumerate(raw):
        if isinstance(value, bool) or value is None:
            raise AppError(
                INVALID_INPUT, "输入值必须是有限数值", field="values[%d]" % index
            )
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            # OverflowError 同 `_as_finite_number`：超大 JSON 整数不许漏成 500。
            raise AppError(
                INVALID_INPUT, "输入值必须是有限数值", field="values[%d]" % index
            )
        if not math.isfinite(number):
            raise AppError(
                INVALID_INPUT, "输入值必须是有限数值", field="values[%d]" % index
            )
        values.append(number)
    return values


def _validated_name(payload: Dict[str, Any]) -> Optional[str]:
    """保存时名称是可选覆盖：给了就必须是字符串；没给就保留磁盘上的值。"""
    if "name" not in payload:
        return None
    value = payload.get("name")
    if not isinstance(value, str) or not value.strip():
        raise AppError(INVALID_INPUT, "名称必须是非空字符串", field="name")
    return value


def _validated_binding(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """校验并返回 binding 的已知字段；没给返回 None（保留磁盘上的原值）。

    只认 `source_file` / `variable_path` / `field_map` 三个键；`field_map` 是
    「字段名 → 系数名」的字符串映射。未知键不落盘（字段集合不变）。
    """
    if "binding" not in payload:
        return None
    raw = payload.get("binding")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise AppError(INVALID_INPUT, "binding 必须是对象", field="binding")
    clean: Dict[str, Any] = {}
    for key in ("source_file", "variable_path"):
        if key not in raw:
            continue
        value = raw.get(key)
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise AppError(INVALID_INPUT, "binding.%s 必须是字符串" % key, field="binding")
        clean[key] = value
    if "field_map" in raw:
        field_map = raw.get("field_map")
        if field_map is None:
            field_map = {}
        if not isinstance(field_map, dict):
            raise AppError(INVALID_INPUT, "binding.field_map 必须是对象", field="binding")
        pairs: Dict[str, str] = {}
        for field, coeff in field_map.items():
            if not isinstance(field, str) or not isinstance(coeff, str):
                raise AppError(
                    INVALID_INPUT, "binding.field_map 必须是字符串映射", field="binding"
                )
            pairs[field] = coeff
        clean["field_map"] = pairs
    return clean


def _merged_item(
    existing: Dict[str, Any],
    clean: Dict[str, Any],
    name: Optional[str],
    binding: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """从磁盘上的现有 item 深拷贝，只覆盖已知键 —— 字段集合不变（只改值）。"""
    new_item = deepcopy(existing)
    coeffs = new_item.get("coeffs")
    if not isinstance(coeffs, dict):
        coeffs = {}
        new_item["coeffs"] = coeffs
    for key in _COEFF_FIELDS:
        coeffs[key] = clean["coeffs"][key]
    for field in _RANGE_FIELDS:
        new_item[field] = clean[field]
    if "trans_coeff" in clean:
        new_item["trans_coeff"] = clean["trans_coeff"]
    if "power_conv_coeff" in clean:
        new_item["power_conv_coeff"] = clean["power_conv_coeff"]
    if name is not None:
        new_item["name"] = name
    if binding is not None:
        merged = new_item.get("binding")
        if not isinstance(merged, dict):
            merged = {}
        for key, value in binding.items():
            merged[key] = deepcopy(value)
        new_item["binding"] = merged
    return new_item


def _direct_children(nodes: Dict[str, List[Any]], parent: int) -> Dict[str, Any]:
    """compact 文档里 `parent` 的直接子节点：`label → value`（同名后者覆盖，同 v1）。"""
    parents = nodes.get("p") or []
    labels = nodes.get("l") or []
    values = nodes.get("v") or []
    children: Dict[str, Any] = {}
    for index in range(parent + 1, len(parents)):
        if parents[index] == parent:
            children[labels[index]] = values[index]
    return children
