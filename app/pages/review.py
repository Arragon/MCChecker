"""审阅修改页面。"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Optional

from nicegui import ui

from app.core import parser, parse_cache, reviewing, storage
from app.utils.auth import get_identity_info, is_admin


_REJECT_OPTION = "__reject__"


# ---------------------------------------------------------------------------
# ReviewDraft — 审阅选择草稿 (T17 / INH-629)
# ---------------------------------------------------------------------------

class ReviewDraft:
    """审阅选择草稿

    保存每个审阅项的选择状态（node_ref -> remark），
    并记录源文件的 content_hash 用于冲突检测。
    """

    def __init__(self, file_ref: str, source_hash: str):
        self.file_ref = file_ref
        self.source_hash = source_hash
        self.selected_items: Dict[str, Optional[str]] = {}  # selection_key -> chosen remark id
        self.remarks: Dict[str, str] = {}  # selection_key -> remark text

    def save_selection(self, selection_key: str, chosen_value: Optional[str]) -> None:
        """保存单个审阅项的选择"""
        self.selected_items[selection_key] = chosen_value

    def restore_selection(self, selection_key: str) -> Optional[str]:
        """恢复单个审阅项的选择"""
        return self.selected_items.get(selection_key)

    def check_source_changed(self, current_hash: str) -> bool:
        """检查源文件是否已变更（变更则存在冲突）"""
        return current_hash != self.source_hash

    def get_unselected_keys(self, all_keys: list[str]) -> list[str]:
        """获取未选择的审阅项 key（用于 partial submit）"""
        return [k for k in all_keys if not self.selected_items.get(k)]

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict"""
        return {
            "file_ref": self.file_ref,
            "source_hash": self.source_hash,
            "selected_items": self.selected_items,
            "remarks": self.remarks,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ReviewDraft:
        """从 dict 反序列化"""
        draft = cls(data["file_ref"], data["source_hash"])
        draft.selected_items = data.get("selected_items", {})
        draft.remarks = data.get("remarks", {})
        return draft


# ---------------------------------------------------------------------------
# 防重复提交锁 (T17 / INH-629)
# ---------------------------------------------------------------------------

_review_submit_locks: dict[str, bool] = {}
_review_submit_guard = threading.Lock()


def review_submit_with_lock(operation_key: str, submit_fn):
    """审阅防重复提交"""
    with _review_submit_guard:
        if operation_key in _review_submit_locks:
            return None
        _review_submit_locks[operation_key] = True
    try:
        return submit_fn()
    finally:
        with _review_submit_guard:
            _review_submit_locks.pop(operation_key, None)


def compute_items_hash(items: list[dict]) -> str:
    """计算审阅项列表的内容 hash（用于冲突检测）"""
    raw = json.dumps(items, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def render_review_page(session_active_tab: dict | None = None) -> None:
    ui.label("审阅修改").classes("mc-page-title q-mb-sm")

    identity = get_identity_info()
    if not is_admin():
        ui.label("仅管理员可访问该功能").classes("text-negative")
        return

    ui.label(
        f"当前审阅人: {identity['name']}  ({identity['ip']})"
    ).classes("mc-page-subtitle q-mb-md")

    review_items = storage.list_review_items()
    if not review_items:
        ui.label("暂无待审阅的修改备注").classes("text-caption text-grey")
        return

    # 计算当前审阅项的 hash（用于冲突检测）
    current_items_hash = compute_items_hash(review_items)

    # 从 session 恢复草稿（T17 / INH-629）
    draft: Optional[ReviewDraft] = None
    if session_active_tab is not None:
        draft_data = session_active_tab.get("_review_draft")
        if draft_data is not None:
            draft = ReviewDraft.from_dict(draft_data)
            # 检查源是否已变更
            if draft.check_source_changed(current_items_hash):
                ui.label("审阅源已变更，请刷新页面重新审阅").classes("text-warning q-mb-sm")
                ui.badge("冲突", color="orange").props("outline")
                # 清除旧草稿
                session_active_tab.pop("_review_draft", None)
                draft = None

    if draft is None:
        draft = ReviewDraft("review_page", current_items_hash)
        if session_active_tab is not None:
            session_active_tab["_review_draft"] = draft.to_dict()

    selection_state: dict[str, dict[str, str | None]] = {}
    # 从草稿恢复选择状态
    for item in review_items:
        key = _selection_key(item)
        restored = draft.restore_selection(key)
        selection_state[key] = {"value": restored}

    generated_container = ui.column().classes("w-full q-gutter-sm q-mt-md")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in review_items:
        grouped[item.get("source_file") or ""].append(item)

    for source_file in sorted(grouped.keys()):
        items = grouped[source_file]
        with ui.card().classes("w-full q-pa-sm"):
            with ui.row().classes("items-center q-gutter-sm q-mb-sm"):
                ui.badge(source_file, color="blue")
                ui.label(f"{len(items)} 个待审节点").classes("text-caption text-grey")

            for item in items:
                _render_review_item(item, selection_state, draft)

    def submit_review():
        # 保存草稿到 session
        if session_active_tab is not None:
            session_active_tab["_review_draft"] = draft.to_dict()

        # 收集已选择和未选择的项
        all_keys = [_selection_key(item) for item in review_items]
        selected_keys = [k for k in all_keys if selection_state.get(k, {}).get("value")]
        unselected_keys = [k for k in all_keys if not selection_state.get(k, {}).get("value")]

        # Partial submit: 未选项继续 pending
        if unselected_keys:
            ui.notify(
                f"部分提交：{len(selected_keys)} 项已选择，{len(unselected_keys)} 项继续待审",
                type="info"
            )

        if not selected_keys:
            ui.notify("请至少选择一个待审节点", type="warning")
            return

        # 仅处理已选择的项
        selected_items = [
            item for item in review_items
            if _selection_key(item) in selected_keys
        ]

        approved_ids: list[str] = []
        rejected_ids: list[str] = []
        # 被接受（通过）的备注按源文件分组，用于直接将建议值写回原始文件。
        accepted_by_file: dict[str, list[dict]] = defaultdict(list)

        for item in selected_items:
            chosen = selection_state[_selection_key(item)]["value"]
            remarks = item.get("remarks") or []
            if chosen == _REJECT_OPTION:
                # 选择「驳回」：触发驳回流程，所有备注标记为 rejected（不修改文件）。
                rejected_ids.extend(str(remark.get("id") or "") for remark in remarks)
                continue
            # 选择「接受」：将该建议值直接替换到修改项中，并写回原始文件。
            chosen_remark = next(
                (r for r in remarks if str(r.get("id") or "") == str(chosen)), None
            )
            if chosen_remark is None:
                ui.notify("审阅选择异常，请重新选择", type="negative")
                return
            approved_ids.append(str(chosen))
            rejected_ids.extend(
                str(remark.get("id") or "")
                for remark in remarks
                if str(remark.get("id") or "") != str(chosen)
            )
            accepted_by_file[item.get("source_file") or ""].append(chosen_remark)

        # 接受项：直接将建议值写回对应的原始配置文件（旧版本自动归档）。
        modified_files: list[str] = []
        for source_file, accepted_items in accepted_by_file.items():
            if not accepted_items:
                continue
            source_path = storage.get_config_path(source_file)
            tree = parse_cache.load_tree(source_path)
            if tree is None:
                tree = parser.parse_path(source_path, source_file)
            try:
                updated_tree = reviewing.apply_review_updates(tree, accepted_items)
            except ValueError as e:
                ui.notify(f"应用修改失败（{source_file}）：{e}", type="negative")
                return
            content = reviewing.serialize_tree(updated_tree, source_file)
            storage.save_config_file(source_file, content)
            parse_cache.save_tree(source_path, updated_tree)
            modified_files.append(source_file)

        result = storage.apply_review_results(
            approved_ids=approved_ids,
            rejected_ids=rejected_ids,
            reviewer_name=identity["name"],
            reviewer_ip=identity["ip"],
            generated_files=None,
        )

        # 提交成功后清除草稿
        if session_active_tab is not None:
            session_active_tab.pop("_review_draft", None)

        generated_container.clear()
        with generated_container:
            if modified_files:
                ui.label("已直接修改的原始文件").classes("mc-section-title")
                for name in sorted(modified_files):
                    ui.label(name).classes("mc-review-note-item")
            else:
                ui.label("本次审阅无接受项，未修改任何文件").classes("text-caption text-grey")

        if session_active_tab is not None:
            session_active_tab.pop("_rendered", None)

        ui.notify(
            f"审阅完成：通过 {result['approved']} 条（已写回原文件），驳回 {result['rejected']} 条",
            type="positive",
        )

    with ui.row().classes("w-full justify-end q-gutter-sm q-mt-md"):
        ui.button("提交审阅结果", icon="done_all", on_click=lambda: review_submit_with_lock(
            f"review_submit_{identity['ip']}", submit_review
        )).props("color=primary")


def _render_review_item(
    item: dict,
    selection_state: dict[str, dict[str, str | None]],
    draft: Optional[ReviewDraft] = None,
) -> None:
    remarks = item.get("remarks") or []
    selection_key = _selection_key(item)
    selection_state.setdefault(selection_key, {"value": None})

    with ui.card().classes("w-full q-mb-sm q-pa-sm mc-review-card"):
        with ui.row().classes("items-center q-gutter-sm q-mb-xs"):
            ui.label(item.get("node_label") or item.get("node_path") or "-").classes("text-subtitle2 text-weight-medium")
            ui.badge(item.get("node_type") or "-", color="teal")
        ui.label(f"节点路径: {item.get('node_path') or '-'}").classes("text-caption text-grey")
        ui.label(f"当前值: {item.get('original_value') or '-'}").classes("text-caption text-grey q-mb-sm")

        options = {
            str(remark.get("id") or ""): (
                f"{str(remark.get('actor_display') or remark.get('actor_ip') or '未知')}："
                f"{str(remark.get('proposed_value') or '')}"
            )
            for remark in remarks
        }
        options[_REJECT_OPTION] = "不通过"

        def on_selection_change(e, key=selection_key):
            selection_state[key].update(value=e.value)
            # 保存到草稿
            if draft is not None:
                draft.save_selection(key, e.value)

        ui.radio(
            options,
            value=selection_state[selection_key]["value"],
            on_change=on_selection_change,
        ).props("inline")

        with ui.column().classes("w-full q-gutter-xs q-mt-sm"):
            for remark in remarks:
                actor = str(remark.get("actor_display") or remark.get("actor_ip") or "未知")
                created_at = str(remark.get("created_at") or "-")
                remark_id = str(remark.get("id") or "")
                with ui.row().classes("items-center q-gutter-xs"):
                    ui.label(
                        f"{actor}  提交于 {created_at}  建议值: {str(remark.get('proposed_value') or '')}"
                    ).classes("mc-review-note-item")
                    ui.button(icon="delete", on_click=lambda e=None, rid=remark_id: _delete_review_remark(
                        rid,
                        session_active_tab,
                    )).props("flat round dense size=xs color=negative").classes("mc-remark-delete-btn").tooltip(
                        "删除该备注"
                    )


def _selection_key(item: dict) -> str:
    # 与 storage.build_edit_remark_map / list_review_items 保持一致：
    # 以稳定节点路径（解析树 id）作为主键，避免基于数组索引的键漂移。
    node_path = str(item.get("node_path") or item.get("node_key") or "")
    return f"{item.get('source_file') or ''}|{node_path}"


def _delete_review_remark(remark_id: str, session_active_tab: dict | None) -> None:
    """审阅界面删除单条备注：支持审阅人员直接删除指定备注。"""
    try:
        ok = storage.delete_edit_remark(remark_id)
    except Exception as e:
        ui.notify(str(e) or "删除失败", type="negative")
        return
    if not ok:
        ui.notify("未找到该备注或已被删除", type="warning")
        return
    ui.notify("备注已删除", type="positive")
    if session_active_tab is not None:
        session_active_tab.pop("_rendered", None)
