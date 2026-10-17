"""locks 测试：机型锁、注册表锁与 WriteGate（M0-05）。"""

from __future__ import annotations

import threading
import time

import pytest

from app.core import locks
from app.core.errors import MAINTENANCE, AppError


# ---------------------------------------------------------------------------
# profile_lock
# ---------------------------------------------------------------------------


class TestProfileLock:
    def test_same_pid_returns_same_object(self):
        assert locks.profile_lock("p1") is locks.profile_lock("p1")

    def test_different_pid_returns_different_object(self):
        assert locks.profile_lock("p-a") is not locks.profile_lock("p-b")

    def test_is_reentrant(self):
        lock = locks.profile_lock("reentrant")
        with lock:
            with lock:
                pass  # 同一线程可重入，不应死锁

    def test_reentrant_across_helper_calls(self):
        """锁内再取一次锁（业务函数互相调用的常见形态）不能死锁。"""

        def inner():
            with locks.profile_lock("nested"):
                return "ok"

        with locks.profile_lock("nested"):
            assert inner() == "ok"

    def test_twenty_threads_increment_counter_to_twenty(self):
        """验收用例：20 个线程对同一 pid 获取锁并递增计数器，结果为 20。"""
        pid = "concurrent-pid"
        counter = {"n": 0}
        barrier = threading.Barrier(20)
        errors: list = []

        def worker():
            try:
                barrier.wait()
                with locks.profile_lock(pid):
                    # 读-改-写必须在锁内：这里刻意放大竞态窗口
                    current = counter["n"]
                    time.sleep(0.001)
                    counter["n"] = current + 1
            except BaseException as exc:  # pragma: no cover - 仅用于诊断
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        assert counter["n"] == 20

    def test_mutual_exclusion_holds(self):
        """持锁期间其他线程必须被挡住。"""
        pid = "mutex-pid"
        lock = locks.profile_lock(pid)
        entered = threading.Event()
        release = threading.Event()
        observed = {"inside": False}

        def holder():
            with lock:
                entered.set()
                release.wait(timeout=5)
                observed["inside"] = True

        t = threading.Thread(target=holder)
        t.start()
        assert entered.wait(timeout=5)

        # 主线程再取同一把锁会被挡住；如果没能互斥，这里会立刻拿到锁并看到 inside=False
        release.set()
        with lock:
            assert observed["inside"] is True
        t.join(timeout=5)

    def test_concurrent_creation_under_race(self):
        """并发首次创建时同一 pid 只能拿到一把锁。"""
        pid = "race-pid"
        seen: list = []
        seen_guard = threading.Lock()
        barrier = threading.Barrier(20)

        def worker():
            barrier.wait()
            got = locks.profile_lock(pid)
            with seen_guard:
                seen.append(id(got))

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(set(seen)) == 1

    def test_count_grows_per_pid(self):
        before = locks.profile_lock_count()
        locks.profile_lock("count-pid-1")
        assert locks.profile_lock_count() == before + 1
        locks.profile_lock("count-pid-2")
        assert locks.profile_lock_count() == before + 2
        # 已存在的 pid 不新增
        locks.profile_lock("count-pid-1")
        assert locks.profile_lock_count() == before + 2


# ---------------------------------------------------------------------------
# registry_lock
# ---------------------------------------------------------------------------


class TestRegistryLock:
    def test_is_rlock(self):
        assert isinstance(locks.registry_lock, type(threading.RLock()))

    def test_reentrant(self):
        with locks.registry_lock:
            with locks.registry_lock:
                pass

    def test_shared_across_threads(self):
        order: list = []

        def worker(tag):
            with locks.registry_lock:
                order.append(tag + "-in")
                time.sleep(0.005)
                order.append(tag + "-out")

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # 两段临界区不能交错
        assert order[0].endswith("-in")
        assert order[1].endswith("-out")


# ---------------------------------------------------------------------------
# WriteGate
# ---------------------------------------------------------------------------


class TestWriteGateBasics:
    def test_write_context_counts(self):
        gate = locks.WriteGate()
        with gate.write():
            assert gate.writers == 1
        assert gate.writers == 0

    def test_nested_writes_count_twice(self):
        gate = locks.WriteGate()
        with gate.write():
            with gate.write():
                assert gate.writers == 2
            assert gate.writers == 1
        assert gate.writers == 0

    def test_exit_write_without_enter_does_not_go_negative(self):
        gate = locks.WriteGate()
        gate.exit_write()
        assert gate.writers == 0

    def test_concurrent_writers_all_counted(self):
        """10 个并发写入同时持有时计数必须为 10，全部退出后归零。"""
        gate = locks.WriteGate()
        barrier = threading.Barrier(10)
        release = threading.Event()

        def worker():
            barrier.wait()
            with gate.write():
                release.wait(timeout=5)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()

        # 等到确实有线程进入临界区（而不是靠睡固定的时间猜）
        deadline = time.monotonic() + 5
        while gate.writers < 10 and time.monotonic() < deadline:
            time.sleep(0.001)
        assert gate.writers == 10

        release.set()
        for t in threads:
            t.join(timeout=10)
        assert gate.writers == 0


class TestWriteGateMaintenance:
    def test_enter_write_rejected_during_maintenance(self):
        gate = locks.WriteGate()
        with gate.maintenance():
            assert gate.in_maintenance is True
            with pytest.raises(AppError) as exc:
                gate.enter_write()
            assert exc.value.code == MAINTENANCE
        assert gate.in_maintenance is False

    def test_write_recovers_after_maintenance(self):
        """验收用例：维护态下 enter_write 失败；退出后成功。"""
        gate = locks.WriteGate()
        with gate.maintenance():
            with pytest.raises(AppError):
                gate.enter_write()

        with gate.write():
            assert gate.writers == 1
        assert gate.writers == 0

    def test_reads_not_affected_by_maintenance(self):
        """维护只挡写入。这里用 writers 计数代表读取路径不经过闸门。"""
        gate = locks.WriteGate()
        with gate.maintenance():
            # 读取路径不调用 enter_write，因此不会抛错
            assert gate.writers == 0

    def test_maintenance_waits_for_inflight_writes(self):
        gate = locks.WriteGate()
        started = threading.Event()
        finish = threading.Event()

        def writer():
            with gate.write():
                started.set()
                finish.wait(timeout=5)

        t = threading.Thread(target=writer)
        t.start()
        assert started.wait(timeout=5)
        assert gate.writers == 1

        entered = threading.Event()

        def maintainer():
            with gate.maintenance(timeout=5):
                entered.set()
                # 进入维护态时写入必须已经结束
                assert gate.writers == 0
            entered.set()

        m = threading.Thread(target=maintainer)
        m.start()
        time.sleep(0.05)
        # 还在等写入结束，未进入维护态
        assert gate.in_maintenance is False
        finish.set()
        m.join(timeout=10)
        assert gate.in_maintenance is False
        t.join(timeout=10)

    def test_maintenance_timeout_raises(self):
        gate = locks.WriteGate()
        release = threading.Event()

        def writer():
            with gate.write():
                release.wait(timeout=5)

        t = threading.Thread(target=writer)
        t.start()
        time.sleep(0.05)
        try:
            with pytest.raises(AppError) as exc:
                gate.enter_maintenance(timeout=0.2)
            assert exc.value.code == MAINTENANCE
        finally:
            release.set()
            t.join(timeout=10)

    def test_exit_write_wakes_waiting_maintenance(self):
        gate = locks.WriteGate()
        with gate.write():
            result: dict = {}

            def maintainer():
                try:
                    with gate.maintenance(timeout=5):
                        result["entered"] = True

                except AppError:
                    result["entered"] = False

            m = threading.Thread(target=maintainer)
            m.start()
            time.sleep(0.05)
        m.join(timeout=10)
        assert result.get("entered") is True


class TestModuleWriteGate:
    def test_singleton_exists(self):
        assert isinstance(locks.write_gate, locks.WriteGate)

    def test_singleton_roundtrip(self):
        """进程级闸门同样可用；用完确保退出维护态，避免污染其他测试。"""
        gate = locks.write_gate
        try:
            with gate.maintenance(timeout=5):
                with pytest.raises(AppError):
                    gate.enter_write()
        finally:
            gate.exit_maintenance()
        with gate.write():
            assert gate.writers == 1
        assert gate.writers == 0
        assert gate.in_maintenance is False
