"""T17 草稿保留与防重复提交测试 (INH-629)

覆盖验收标准:
- tab switch 后 draft 恢复
- 两客户端编辑互不影响
- Cancel 后 config hash 不变
- partial review 不改变未选
- source change 有 conflict
- disconnect/reconnect 后任务结果可解释
- repeated click 不重复业务提交
"""

import threading
import time

import pytest

from app.core.task_status import (
    TaskState,
    TaskStatus,
    register_task,
    get_task,
    update_task_state,
    clear_terminal_tasks,
    list_active_tasks,
    TERMINAL_STATES,
)
from app.pages.dltool import DLDraft, submit_with_lock, _submit_locks
from app.pages.review import (
    ReviewDraft,
    review_submit_with_lock,
    compute_items_hash,
    _review_submit_locks,
)


# ---------------------------------------------------------------------------
# DLDraft 测试
# ---------------------------------------------------------------------------


class TestDLDraft:
    """DL 草稿测试"""

    def _make_base_config(self):
        """构建测试用基础配置"""
        return {
            "editing": False,
            "items": {
                "pp_energy": {
                    "name": "PP Energy",
                    "coeffs": {"k1": 1.0, "k2": 0.5, "b": 0.0},
                    "x_min": -10, "x_max": 10,
                    "y_min": -100, "y_max": 100,
                },
                "rp_energy": {
                    "name": "RP Energy",
                    "coeffs": {"k1": 2.0, "b": 1.0},
                },
            },
        }

    def test_dl_draft_tab_switch_restore(self):
        """tab switch 后 draft 恢复"""
        cfg = self._make_base_config()
        draft = DLDraft(cfg)

        # 模拟用户输入
        draft.inputs["items.pp_energy.coeffs.k1"] = 3.14
        draft.inputs["items.pp_energy.coeffs.k2"] = 2.71
        draft.editing_state["some_state"] = "value"

        # 模拟 tab switch：序列化再反序列化
        draft_dict = {
            "inputs": draft.inputs.copy(),
            "editing_state": draft.editing_state.copy(),
            "base_config_hash": draft.base_config_hash,
        }

        # 恢复草稿
        restored = DLDraft(cfg)
        restored.inputs = draft_dict["inputs"]
        restored.editing_state = draft_dict["editing_state"]

        assert restored.inputs["items.pp_energy.coeffs.k1"] == 3.14
        assert restored.inputs["items.pp_energy.coeffs.k2"] == 2.71
        assert restored.editing_state["some_state"] == "value"

    def test_dl_draft_two_clients_no_interference(self):
        """两客户端编辑互不影响"""
        cfg1 = self._make_base_config()
        cfg2 = self._make_base_config()

        draft1 = DLDraft(cfg1)
        draft2 = DLDraft(cfg2)

        # 客户端 1 修改 k1
        draft1.inputs["items.pp_energy.coeffs.k1"] = 10.0
        # 客户端 2 修改 k2
        draft2.inputs["items.pp_energy.coeffs.k2"] = 20.0

        # 各自的草稿互不影响
        assert draft1.inputs.get("items.pp_energy.coeffs.k1") == 10.0
        assert "items.pp_energy.coeffs.k1" not in draft2.inputs
        assert draft2.inputs.get("items.pp_energy.coeffs.k2") == 20.0
        assert "items.pp_energy.coeffs.k2" not in draft1.inputs

    def test_dl_cancel_config_unchanged(self):
        """Cancel 后 config hash 不变"""
        cfg = self._make_base_config()
        original_hash = DLDraft._compute_config_hash(cfg)

        draft = DLDraft(cfg)
        draft.inputs["items.pp_energy.coeffs.k1"] = 999.0
        draft.temp_bindings["pp_energy"] = {"source_file": "test.xml"}

        # 取消草稿
        draft.cancel()

        # config 本身未被修改（draft 只修改自己的副本）
        assert DLDraft._compute_config_hash(cfg) == original_hash
        assert draft.inputs == {}
        assert draft.temp_bindings == {}

    def test_dl_draft_commit_success(self):
        """草稿提交成功：base config 未变"""
        cfg = self._make_base_config()
        draft = DLDraft(cfg)
        draft.inputs["items.pp_energy.coeffs.k1"] = 5.0
        draft.temp_bindings["pp_energy"] = {"source_file": "bound.xml"}

        ok = draft.commit(cfg)

        assert ok is True
        assert cfg["items"]["pp_energy"]["coeffs"]["k1"] == 5.0
        assert cfg["items"]["pp_energy"]["binding"]["source_file"] == "bound.xml"

    def test_dl_draft_commit_conflict(self):
        """草稿提交失败：base config 已被其他客户端修改"""
        cfg = self._make_base_config()
        draft = DLDraft(cfg)
        draft.inputs["items.pp_energy.coeffs.k1"] = 5.0

        # 模拟另一个客户端修改了 config
        cfg["items"]["rp_energy"]["coeffs"]["k1"] = 999.0

        ok = draft.commit(cfg)

        assert ok is False
        # 原始 cfg 不应被草稿部分写入
        assert cfg["items"]["pp_energy"]["coeffs"]["k1"] == 1.0  # 保持原值


# ---------------------------------------------------------------------------
# ReviewDraft 测试
# ---------------------------------------------------------------------------


class TestReviewDraft:
    """审阅草稿测试"""

    def _make_review_items(self):
        """构建测试用审阅项"""
        return [
            {
                "source_file": "config1.xml",
                "node_key": "node1",
                "node_path": "/root/param1",
                "remarks": [
                    {"id": "r1", "proposed_value": "100", "actor_display": "Alice"},
                    {"id": "r2", "proposed_value": "200", "actor_display": "Bob"},
                ],
            },
            {
                "source_file": "config1.xml",
                "node_key": "node2",
                "node_path": "/root/param2",
                "remarks": [
                    {"id": "r3", "proposed_value": "300", "actor_display": "Charlie"},
                ],
            },
        ]

    def test_review_draft_save_restore(self):
        """审阅选择保存与恢复"""
        items = self._make_review_items()
        source_hash = compute_items_hash(items)
        draft = ReviewDraft("review_page", source_hash)

        # 保存选择
        draft.save_selection("config1.xml|node1", "r1")
        draft.save_selection("config1.xml|node2", "__reject__")

        # 恢复选择
        assert draft.restore_selection("config1.xml|node1") == "r1"
        assert draft.restore_selection("config1.xml|node2") == "__reject__"
        assert draft.restore_selection("config1.xml|nonexistent") is None

    def test_review_partial_submit(self):
        """partial review 不改变未选"""
        items = self._make_review_items()
        source_hash = compute_items_hash(items)
        draft = ReviewDraft("review_page", source_hash)

        # 只选择第一个节点
        draft.save_selection("config1.xml|node1", "r1")
        # node2 未选择

        all_keys = ["config1.xml|node1", "config1.xml|node2"]
        unselected = draft.get_unselected_keys(all_keys)

        assert "config1.xml|node2" in unselected
        assert "config1.xml|node1" not in unselected

    def test_review_source_change_conflict(self):
        """source change 有 conflict"""
        items = self._make_review_items()
        original_hash = compute_items_hash(items)
        draft = ReviewDraft("review_page", original_hash)

        # 源未变更
        assert draft.check_source_changed(original_hash) is False

        # 源已变更（模拟新的审阅项）
        new_items = items + [{"source_file": "config2.xml", "node_key": "node3"}]
        new_hash = compute_items_hash(new_items)

        assert draft.check_source_changed(new_hash) is True

    def test_review_draft_serialization(self):
        """审阅草稿序列化与反序列化"""
        draft = ReviewDraft("review_page", "abc123")
        draft.save_selection("key1", "value1")
        draft.remarks["key1"] = "some remark"

        # 序列化
        data = draft.to_dict()
        assert data["file_ref"] == "review_page"
        assert data["source_hash"] == "abc123"
        assert data["selected_items"]["key1"] == "value1"

        # 反序列化
        restored = ReviewDraft.from_dict(data)
        assert restored.file_ref == "review_page"
        assert restored.source_hash == "abc123"
        assert restored.restore_selection("key1") == "value1"


# ---------------------------------------------------------------------------
# TaskStatus 测试
# ---------------------------------------------------------------------------


class TestTaskStatus:
    """任务状态测试"""

    def setup_method(self):
        """每个测试前清理注册表"""
        clear_terminal_tasks()

    def test_task_status_lifecycle(self):
        """任务状态生命周期"""
        status = TaskStatus(
            task_id="task-001",
            profile_id="default",
            operation="update",
            state=TaskState.QUEUED,
        )
        register_task(status)

        # 初始状态
        assert get_task("task-001").state == TaskState.QUEUED
        assert not status.is_terminal

        # 运行中
        update_task_state("task-001", TaskState.RUNNING)
        assert get_task("task-001").state == TaskState.RUNNING
        assert not status.is_terminal

        # 成功
        update_task_state("task-001", TaskState.SUCCEEDED, result={"files": 3})
        assert get_task("task-001").state == TaskState.SUCCEEDED
        assert status.is_terminal
        assert status.result == {"files": 3}

    def test_task_status_disconnect_reconnect(self):
        """disconnect/reconnect 后任务结果可解释"""
        # 注册一个失败的任务
        status = TaskStatus(
            task_id="task-002",
            profile_id="default",
            operation="review",
            state=TaskState.FAILED,
            error_code="SOURCE_CHANGED",
            inputs={"file": "config.xml", "node": "/root/param"},
            retry_condition="源文件已更新，请刷新后重试",
            message="审阅失败：源文件已变更",
        )
        register_task(status)

        # 模拟 reconnect：查询任务状态
        restored = get_task("task-002")
        assert restored is not None
        assert restored.state == TaskState.FAILED
        assert restored.error_code == "SOURCE_CHANGED"
        assert restored.inputs["file"] == "config.xml"
        assert restored.retry_condition == "源文件已更新，请刷新后重试"
        assert restored.is_terminal
        assert restored.is_retryable

    def test_task_status_serialization(self):
        """任务状态序列化与反序列化"""
        status = TaskStatus(
            task_id="task-003",
            profile_id="profile1",
            operation="dl_commit",
            state=TaskState.CONFLICT,
            error_code="REVIEW_CONFLICT",
            message="配置冲突",
        )

        data = status.to_dict()
        assert data["state"] == "conflict"
        assert data["error_code"] == "REVIEW_CONFLICT"

        restored = TaskStatus.from_dict(data)
        assert restored.task_id == "task-003"
        assert restored.state == TaskState.CONFLICT
        assert restored.error_code == "REVIEW_CONFLICT"

    def test_clear_terminal_tasks(self):
        """清理终态任务"""
        register_task(TaskStatus("t1", "p1", "op1", TaskState.SUCCEEDED))
        register_task(TaskStatus("t2", "p1", "op2", TaskState.RUNNING))
        register_task(TaskStatus("t3", "p1", "op3", TaskState.FAILED))

        active = list_active_tasks()
        assert len(active) == 1  # 只有 RUNNING 是活跃状态

        clear_terminal_tasks()

        assert get_task("t1") is None  # SUCCEEDED 已清理
        assert get_task("t2") is not None  # RUNNING 保留
        assert get_task("t3") is None  # FAILED 已清理


# ---------------------------------------------------------------------------
# 防重复提交测试
# ---------------------------------------------------------------------------


class TestSubmitLock:
    """防重复提交测试"""

    def test_repeated_click_no_duplicate(self):
        """repeated click 不重复业务提交"""
        call_count = 0

        def business_logic():
            nonlocal call_count
            call_count += 1
            time.sleep(0.1)  # 模拟耗时操作
            return "done"

        # 模拟快速双击
        results = []
        t1 = threading.Thread(
            target=lambda: results.append(submit_with_lock("test_op", business_logic))
        )
        t2 = threading.Thread(
            target=lambda: results.append(submit_with_lock("test_op", business_logic))
        )

        t1.start()
        time.sleep(0.01)  # 确保 t1 先获取锁
        t2.start()

        t1.join()
        t2.join()

        # 只有一次成功执行
        assert call_count == 1
        assert results.count("done") == 1
        assert results.count(None) == 1

    def test_different_ops_no_blocking(self):
        """不同操作互不阻塞"""
        results = {}

        def op_a():
            time.sleep(0.05)
            results["a"] = "done_a"

        def op_b():
            time.sleep(0.05)
            results["b"] = "done_b"

        t1 = threading.Thread(target=lambda: submit_with_lock("op_a", op_a))
        t2 = threading.Thread(target=lambda: submit_with_lock("op_b", op_b))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results.get("a") == "done_a"
        assert results.get("b") == "done_b"

    def test_review_submit_lock(self):
        """审阅提交防重复（同步场景）"""
        call_count = 0

        def review_submit():
            nonlocal call_count
            call_count += 1
            return "submitted"

        # 同步调用：每次调用完成后锁会释放，所以两次都会成功
        r1 = review_submit_with_lock("review_test", review_submit)
        r2 = review_submit_with_lock("review_test", review_submit)

        assert r1 == "submitted"
        assert r2 == "submitted"  # 同步场景下锁已释放
        assert call_count == 2

    def test_review_submit_lock_concurrent(self):
        """审阅提交并发防重复"""
        call_count = 0

        def review_submit():
            nonlocal call_count
            call_count += 1
            time.sleep(0.1)
            return "submitted"

        results = []
        t1 = threading.Thread(
            target=lambda: results.append(review_submit_with_lock("review_concurrent", review_submit))
        )
        t2 = threading.Thread(
            target=lambda: results.append(review_submit_with_lock("review_concurrent", review_submit))
        )

        t1.start()
        time.sleep(0.01)
        t2.start()

        t1.join()
        t2.join()

        assert call_count == 1
        assert results.count("submitted") == 1
        assert results.count(None) == 1
