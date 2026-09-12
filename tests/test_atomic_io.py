"""原子持久化、并发保护与 WriteGate 测试"""

import json
import os
import shutil
import tempfile
import threading
import time
from unittest.mock import patch

import pytest


class TestAtomicWriteJson:
    """原子写入测试"""

    def test_atomic_write_success(self, tmp_path):
        """正常写入成功，数据完整"""
        from app.core.storage import atomic_write_json

        target = str(tmp_path / "test.json")
        data = {"key": "value", "list": [1, 2, 3]}
        atomic_write_json(target, data)

        with open(target) as f:
            loaded = json.load(f)
        assert loaded == data

    def test_atomic_write_creates_parent_dirs(self, tmp_path):
        """写入时自动创建父目录"""
        from app.core.storage import atomic_write_json

        target = str(tmp_path / "sub" / "dir" / "test.json")
        atomic_write_json(target, {"a": 1})
        assert os.path.exists(target)

    def test_atomic_write_enospc_preserves_old_data(self, tmp_path):
        """磁盘满（或写入失败）时旧数据不变"""
        from app.core.storage import atomic_write_json

        target = str(tmp_path / "test.json")
        old_data = {"original": "data", "version": 1}
        atomic_write_json(target, old_data)

        # 模拟写入失败
        def failing_fdopen(fd, *args, **kwargs):
            try:
                os.close(fd)
            except OSError:
                pass
            raise OSError(28, "No space left on device")

        with patch("os.fdopen", side_effect=failing_fdopen):
            with pytest.raises(OSError, match="No space left"):
                atomic_write_json(target, {"new": "data"})

        # 旧数据必须完整保留
        with open(target) as f:
            loaded = json.load(f)
        assert loaded == old_data

    def test_atomic_write_no_temp_file_left_on_failure(self, tmp_path):
        """写入失败后不留临时文件"""
        from app.core.storage import atomic_write_json

        target = str(tmp_path / "test.json")
        atomic_write_json(target, {"ok": True})

        def failing_fdopen(fd, *args, **kwargs):
            try:
                os.close(fd)
            except OSError:
                pass
            raise OSError(28, "No space left on device")

        with patch("os.fdopen", side_effect=failing_fdopen):
            with pytest.raises(OSError):
                atomic_write_json(target, {"ok": False})

        # 目录中不应有 .tmp 文件残留
        remaining = [f for f in os.listdir(str(tmp_path)) if f.endswith(".tmp")]
        assert remaining == []

    def test_atomic_write_replaces_atomically(self, tmp_path):
        """os.replace 保证原子性，新数据完整替换"""
        from app.core.storage import atomic_write_json

        target = str(tmp_path / "test.json")
        for i in range(10):
            atomic_write_json(target, {"iteration": i})

        with open(target) as f:
            loaded = json.load(f)
        assert loaded["iteration"] == 9


class TestCorruptJsonProtection:
    """corrupt JSON 保护测试"""

    def test_corrupt_json_raises_error(self, tmp_path):
        """损坏 JSON 抛出 CorruptDataError，不返回空列表"""
        from app.core.storage import _load_json, CorruptDataError

        corrupt_file = str(tmp_path / "corrupt.json")
        with open(corrupt_file, "w") as f:
            f.write("{invalid json content")

        with pytest.raises(CorruptDataError, match="JSON corrupt"):
            _load_json(corrupt_file, [])

    def test_missing_json_returns_default(self, tmp_path):
        """不存在的文件返回 default，不抛异常"""
        from app.core.storage import _load_json

        missing = str(tmp_path / "nonexistent.json")
        result = _load_json(missing, [])
        assert result == []

        result2 = _load_json(missing, {"default": True})
        assert result2 == {"default": True}

    def test_corrupt_json_does_not_silent_overwrite(self, tmp_path):
        """corrupt JSON 不返回空列表后导致覆盖"""
        from app.core.storage import _load_json, _save_json, CorruptDataError

        target = str(tmp_path / "data.json")
        # 先写入正常数据
        _save_json(target, [{"id": 1}, {"id": 2}])

        # 模拟文件损坏
        with open(target, "w") as f:
            f.write("corrupt{{{")

        # 加载应该抛异常，而不是返回 []
        with pytest.raises(CorruptDataError):
            _load_json(target, [])

        # 原始损坏文件不应被修改
        with open(target) as f:
            content = f.read()
        assert content == "corrupt{{{"

    def test_load_json_safe_same_behavior(self, tmp_path):
        """load_json_safe 与 _load_json 行为一致"""
        from app.core.storage import load_json_safe, CorruptDataError

        # missing → default
        assert load_json_safe(str(tmp_path / "missing.json"), [1, 2]) == [1, 2]

        # corrupt → raise
        corrupt = str(tmp_path / "bad.json")
        with open(corrupt, "w") as f:
            f.write("not json")
        with pytest.raises(CorruptDataError):
            load_json_safe(corrupt, [])


class TestConcurrentProtection:
    """并发保护测试"""

    def test_profile_lock_basic(self):
        """profile 锁存在且可重入"""
        from app.core.storage import get_profile_lock

        lock1 = get_profile_lock("test_profile")
        lock2 = get_profile_lock("test_profile")
        assert lock1 is lock2

        # RLock 可重入
        lock1.acquire()
        lock1.acquire()
        lock1.release()
        lock1.release()

    def test_different_profiles_different_locks(self):
        """不同 profile 有不同锁"""
        from app.core.storage import get_profile_lock

        lock_a = get_profile_lock("profile_a")
        lock_b = get_profile_lock("profile_b")
        assert lock_a is not lock_b

    def test_concurrent_append_both_preserved(self, isolated_data_env):
        """两个用户同时追加不同备注都保留"""
        storage = isolated_data_env
        from app.core.storage import (
            add_edit_remark,
            load_edit_remarks,
            get_profile_lock,
            get_active_profile,
        )

        profile = get_active_profile()
        lock = get_profile_lock(profile)
        results = {"errors": []}

        def add_remark(actor_name, node_key):
            try:
                with lock:
                    add_edit_remark(
                        source_file="test.xml",
                        node_key=node_key,
                        node_path=f"/root/{node_key}",
                        node_label=node_key,
                        original_value="1",
                        proposed_value="2",
                        node_type="str",
                        tree_type="xml",
                        actor_ip="10.0.0.1",
                        actor_name=actor_name,
                    )
            except Exception as e:
                results["errors"].append(e)

        t1 = threading.Thread(target=add_remark, args=("张三", "0.1"))
        t2 = threading.Thread(target=add_remark, args=("李四", "0.2"))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert results["errors"] == []
        items = load_edit_remarks()
        actors = {item["actor_name"] for item in items}
        assert "张三" in actors
        assert "李四" in actors
        assert len(items) == 2


class TestWriteGate:
    """WriteGate 测试"""

    def test_open_state_allows_write(self):
        """OPEN 状态允许写入"""
        from app.core.storage import WriteGate

        gate = WriteGate()
        assert gate.state == WriteGate.OPEN
        gate.acquire_write()
        gate.release_write()

    def test_maintenance_rejects_new_writes(self):
        """maintenance 时新写被拒绝"""
        from app.core.storage import WriteGate, WriteBlockedError

        gate = WriteGate()
        assert gate.enter_maintenance(timeout=1) is True
        assert gate.state == WriteGate.MAINTENANCE

        with pytest.raises(WriteBlockedError, match="maintenance"):
            gate.acquire_write()

    def test_draining_rejects_new_writes(self):
        """draining 状态也拒绝新写入"""
        from app.core.storage import WriteGate, WriteBlockedError

        gate = WriteGate()
        gate.acquire_write()

        def release_later():
            time.sleep(0.1)
            gate.release_write()

        t = threading.Thread(target=release_later)
        t.start()

        result = gate.enter_maintenance(timeout=2)
        assert result is True
        t.join()

        # 现在在 maintenance 状态
        with pytest.raises(WriteBlockedError):
            gate.acquire_write()

        gate.exit_maintenance()

    def test_exit_maintenance_restores_open(self):
        """退出维护模式后恢复写入"""
        from app.core.storage import WriteGate

        gate = WriteGate()
        gate.enter_maintenance(timeout=1)
        gate.exit_maintenance()
        assert gate.state == WriteGate.OPEN

        gate.acquire_write()
        gate.release_write()

    def test_write_scope_context_manager(self):
        """write_scope 上下文管理器自动 acquire/release"""
        from app.core.storage import WriteGate

        gate = WriteGate()
        with gate.write_scope():
            assert gate._in_flight_count == 1
        assert gate._in_flight_count == 0

    def test_enter_maintenance_timeout_restores_open(self):
        """enter_maintenance 超时后恢复 OPEN"""
        from app.core.storage import WriteGate

        gate = WriteGate()
        gate.acquire_write()

        result = gate.enter_maintenance(timeout=0.1)
        assert result is False
        assert gate.state == WriteGate.OPEN

        gate.release_write()

    def test_global_write_gate_exists(self):
        """全局 WriteGate 实例存在"""
        from app.core.storage import global_write_gate, WriteGate

        assert isinstance(global_write_gate, WriteGate)
        assert global_write_gate.state == WriteGate.OPEN


class TestSameSecondArchive:
    """同秒归档防碰撞测试"""

    def test_unique_archive_name_format(self):
        """唯一归档名包含时间戳和 UUID"""
        from app.core.storage import unique_archive_name
        from datetime import datetime

        dt = datetime(2026, 9, 12, 14, 30, 45)
        name = unique_archive_name("config.xml", dt=dt)
        assert "20260912_143045" in name
        # UUID 8 位 hex
        parts = name.rsplit("_", 1)
        assert len(parts[-1]) == 8

    def test_unique_archive_names_differ(self):
        """多次调用生成不同名称"""
        from app.core.storage import unique_archive_name

        names = {unique_archive_name("test.xml") for _ in range(100)}
        assert len(names) == 100

    def test_same_second_record_files_dont_collide(self, isolated_data_env):
        """同秒保存记录文件不覆盖"""
        from app.core.storage import save_record_file, list_record_versions

        p1 = save_record_file("test.xml", b"record_v1")
        p2 = save_record_file("test.xml", b"record_v2")

        assert os.path.exists(p1)
        assert os.path.exists(p2)
        with open(p1, "rb") as f:
            assert f.read() == b"record_v1"
        with open(p2, "rb") as f:
            assert f.read() == b"record_v2"

        versions = list_record_versions("test.xml")
        assert len(versions) >= 2
