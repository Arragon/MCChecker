"""TaskStatus UI 状态模型 (T17 / INH-629)

为 UpdatePipeline 及前台操作提供统一的任务状态枚举与数据载体，
支持 disconnect/reconnect 后结果可解释（保留 inputs、error_code、retry_condition）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class TaskState(Enum):
    """任务生命周期状态"""
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_FAILED = "partially_failed"
    FAILED = "failed"
    CONFLICT = "conflict"


# 终态集合（disconnect 后若处于终态，重连时直接展示结果）
TERMINAL_STATES = frozenset({
    TaskState.SUCCEEDED,
    TaskState.PARTIALLY_FAILED,
    TaskState.FAILED,
    TaskState.CONFLICT,
})


@dataclass
class TaskStatus:
    """任务状态数据载体

    Attributes:
        task_id: 唯一任务标识（uuid 或 pipeline 生成的 key）
        profile_id: 目标 profile
        operation: 操作类型（如 "update", "review", "dl_commit"）
        state: 当前状态
        error_code: 失败时的错误码（来自 ErrorCode）
        inputs: 失败时保留原始输入，方便重试
        retry_condition: 安全重试条件描述（人类可读）
        result: 成功时的结果数据
        message: 人类可读状态描述
    """
    task_id: str
    profile_id: str
    operation: str
    state: TaskState = TaskState.QUEUED
    error_code: Optional[str] = None
    inputs: Optional[Dict[str, Any]] = None
    retry_condition: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    message: str = ""

    @property
    def is_terminal(self) -> bool:
        """是否处于终态"""
        return self.state in TERMINAL_STATES

    @property
    def is_retryable(self) -> bool:
        """是否可安全重试"""
        return self.state in (TaskState.FAILED, TaskState.CONFLICT) and self.retry_condition is not None

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict（用于 session storage / JSON 传输）"""
        return {
            "task_id": self.task_id,
            "profile_id": self.profile_id,
            "operation": self.operation,
            "state": self.state.value,
            "error_code": self.error_code,
            "inputs": self.inputs,
            "retry_condition": self.retry_condition,
            "result": self.result,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskStatus:
        """从 dict 反序列化"""
        return cls(
            task_id=data["task_id"],
            profile_id=data["profile_id"],
            operation=data["operation"],
            state=TaskState(data["state"]),
            error_code=data.get("error_code"),
            inputs=data.get("inputs"),
            retry_condition=data.get("retry_condition"),
            result=data.get("result"),
            message=data.get("message", ""),
        )


# ---------------------------------------------------------------------------
# 内存任务状态注册表（session-scoped，支持 disconnect/reconnect 查询）
# ---------------------------------------------------------------------------

_task_registry: Dict[str, TaskStatus] = {}


def register_task(status: TaskStatus) -> None:
    """注册任务状态"""
    _task_registry[status.task_id] = status


def get_task(task_id: str) -> Optional[TaskStatus]:
    """查询任务状态（reconnect 后调用）"""
    return _task_registry.get(task_id)


def update_task_state(task_id: str, state: TaskState, **kwargs) -> Optional[TaskStatus]:
    """更新任务状态"""
    status = _task_registry.get(task_id)
    if status is None:
        return None
    status.state = state
    for k, v in kwargs.items():
        if hasattr(status, k):
            setattr(status, k, v)
    return status


def clear_terminal_tasks() -> None:
    """清理所有终态任务（释放内存）"""
    to_remove = [tid for tid, s in _task_registry.items() if s.is_terminal]
    for tid in to_remove:
        del _task_registry[tid]


def list_active_tasks() -> list[TaskStatus]:
    """列出所有非终态任务"""
    return [s for s in _task_registry.values() if not s.is_terminal]


# ---------------------------------------------------------------------------
# UI 渲染辅助
# ---------------------------------------------------------------------------

_STATE_DISPLAY = {
    TaskState.QUEUED: ("待处理", "grey", "hourglass_empty"),
    TaskState.RUNNING: ("执行中", "blue", "sync"),
    TaskState.SUCCEEDED: ("成功", "positive", "check_circle"),
    TaskState.PARTIALLY_FAILED: ("部分失败", "warning", "warning"),
    TaskState.FAILED: ("失败", "negative", "error"),
    TaskState.CONFLICT: ("冲突", "orange", "conflict"),
}


def render_task_status(status: TaskStatus) -> None:
    """渲染任务状态 UI 组件（NiceGUI）

    根据状态显示 badge + 描述 + 重试条件。
    失败时保留 inputs 展示，方便用户确认重试。
    """
    from nicegui import ui

    label, color, icon = _STATE_DISPLAY.get(
        status.state, ("未知", "grey", "help")
    )

    with ui.row().classes("items-center q-gutter-sm"):
        ui.badge(label, color=color).props(f"outline")
        ui.icon(icon, color=color).classes("text-caption")
        if status.message:
            ui.label(status.message).classes("text-caption text-grey")

    # 失败时展示错误码和重试条件
    if status.state in (TaskState.FAILED, TaskState.CONFLICT):
        with ui.column().classes("q-ml-md q-gutter-xs"):
            if status.error_code:
                ui.label(f"错误码: {status.error_code}").classes(
                    "text-caption text-negative"
                )
            if status.retry_condition:
                ui.label(f"重试条件: {status.retry_condition}").classes(
                    "text-caption text-orange"
                )
            if status.inputs:
                ui.label("保留输入:").classes("text-caption text-grey")
                for k, v in status.inputs.items():
                    ui.label(f"  {k}: {v}").classes("text-caption font-mono")
