"""设备层的开关与"为什么关着"（M7-07 / M7-08 延期声明）。

**设备层当前功能关闭**。这不是"还没写"，是一个**有依据的决定**：业务侧没有
设备级需求（不存在真实设备身份：序列号、单台标定差异），《M5–M8 计划》里
M7-07/M7-08 的延期判据正是"业务侧无设备级需求 → 整体标注功能关闭，并在
M8-07 文档中显式声明，验收不虚报"。

这个模块是那份声明的**唯一出处**：

* `ENABLED` 是代码里的唯一开关。散落的 `if device...` 判断迟早各自走形，
  所以要关就关在一个地方，接口、状态页、上提入口全部读它。
* `describe()` 同时说清"关了什么"和"什么是开着的"——只说前一半，读的人会
  以为整个配置域都不可用；只说后一半，则会把"没有设备例外"当成"设备都一样"。
* `require_enabled()` 只负责**明确报错**。设备层关闭时必须给
  `DEVICE_LAYER_DISABLED`，而不是 404（让人以为路径写错）或假装成功。

启用顺序（`CHECKLIST`，缺一条都不算启用）：

1. 业务侧确认存在真实设备身份，且设备 ≠ 机型（profile）；
2. 设备目录与机型归属表落地，`device_id` **不得**由 IP 或"某机型 JSON"反推；
3. 设备例外只写 `device` scope，上提机型另建审批提案（不能直接改机型基线）；
4. 设备退役后历史可读、新写禁用；
5. M8-07 文档显式解除"功能关闭"声明，并把本模块 `ENABLED` 置 `True`。
"""

from __future__ import annotations

from typing import Any, Dict

from ..core.errors import AppError

#: 设备层唯一开关。**关闭**是当前的产品决定，不是临时代码。
ENABLED = False

#: 关闭时对外暴露的状态词，与错误码同名，省得对不上号。
DEVICE_LAYER_DISABLED = "DEVICE_LAYER_DISABLED"

#: 决定"关着"的两张卡与文档出口。
DEFERRED_CARDS = ("M7-07", "M7-08")
DECLARED_IN = "M8-07"

#: 关闭期间**仍然完整可用**的能力。写出来是为了避免误读成"配置域只有一半能用"。
AVAILABLE = (
    "全局/机型级配置目录（版本、模板、参数、定义、定义集合）",
    "通用 / 机型基线、变更单、批准发布",
    "目标文件生成与交付产物",
    "跨版本升级、同版本刷新",
    "经验上提（机型 → 通用）、历史回滚、跨变体迁移、出口补发",
)

#: 关闭期间不可用的能力。
UNAVAILABLE = (
    "设备目录（稳定设备 ID、机型归属、退役）",
    "设备例外（DeviceOverride / DeviceBaseline 派生）",
    "device_rebase（设备层同版本刷新）",
    "设备 → 机型上提",
    "按设备区分的生成 XML（不得出现「这台设备不一样」的产物）",
)

#: 启用前必须逐条满足的条件（顺序即依赖顺序）。
CHECKLIST = (
    "业务侧确认存在真实设备身份（序列号 / 单台标定差异），且设备 ≠ 机型",
    "设备目录与机型归属表落地；device_id 不得由 IP 或某机型 JSON 反推",
    "设备例外只写 device scope；上提到机型必须另建审批提案",
    "设备退役后历史可读、新写禁用",
    "M8-07 文档显式解除关闭声明，并把 config_device_layer.ENABLED 置 True",
)


def enabled() -> bool:
    return bool(ENABLED)


def describe() -> Dict[str, Any]:
    """给状态页/前端/验收读的一段话：关了、关了什么、开着什么、怎么开。"""
    return {
        "enabled": enabled(),
        "status": "enabled" if enabled() else "disabled",
        "reason_code": None if enabled() else DEVICE_LAYER_DISABLED,
        "deferred_cards": list(DEFERRED_CARDS),
        "declared_in": DECLARED_IN,
        "reason": "业务侧无设备级需求（不存在真实设备身份），按计划整体延期",
        "available": list(AVAILABLE),
        "unavailable": list(UNAVAILABLE),
        "enable_checklist": list(CHECKLIST),
    }


def require_enabled(*, field: str = "device_id", **detail: Any) -> None:
    """设备层关闭时抛 `DEVICE_LAYER_DISABLED`（HTTP 400）。

    刻意不用 404：路径是对的，只是这个功能没开。也不返回"空成功"——那会让调用方
    以为"设备没有例外"，而真实情况是"根本没有设备这个概念"。
    """
    if enabled():
        return
    payload: Dict[str, Any] = {
        "field": field,
        "deferred_cards": list(DEFERRED_CARDS),
        "declared_in": DECLARED_IN,
        "note": "机型级配置完整可用；需要设备级例外时先按 enable_checklist 启用设备目录",
    }
    payload.update(detail)
    raise AppError(
        DEVICE_LAYER_DISABLED,
        "设备层未启用（M7-07/08 按计划延期），无法处理设备级配置",
        **payload
    )