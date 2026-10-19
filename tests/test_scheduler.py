"""标准库调度器（卡 8 · M3-08 / INH-1893；规范 §10.4、§10.1、§16.2 UC-33）。

本文件证明的四件事，按重要性排：

1. **调度线程自己不下载、不写、不删权威数据**（卡 8 手递手 §1）。它醒来只做一件事——
   往 `TaskRunner` 投一个 `update_all`，于是互斥、进度、修订口径、排空、按线程名判存活
   全部继承 runner 的。`TestSchedulerWritesNothingAuthoritative` 用「记录每一次文件系统
   删除并核对发起线程」把这条钉死，而不是靠人读代码相信它；允许目录那一份名单是**硬编码**的
   （`("doc_v2", "diff_v2")`），不从被测的 `DERIVED_CACHE_DIRNAMES` 推导——否则往那个元组里
   多加一项就会连带放宽它本该防的东西，而那正是这条用例存在的理由。
2. **时钟可注入**：全部用例都在假钟上跑，没有一条为「等一小时」真睡。
3. **BUSY 不丢这次定时**：手工 `update_all` 正在跑时投出 BUSY，调度器改 1 分钟后重试，
   而不是「跳过、等下一个整周期」。这是与「跳过」真实不同的产品行为，单独一条用例钉住。
4. **线程生命周期可断言**：线程名 `mcsched`、daemon、`stop()` 幂等且不 join（R11），
   停完之后既不再提交也不会留残线程。`mcsched` **故意**不带 `mctask` 前缀——runner 的
   存活判据只该管「写 data/ 的人」，把调度线程塞进去是错的（见
   `TestThreadLifecycle::test_scheduler_is_invisible_to_the_runner_liveness_predicate`）。

数据根由 `tests/conftest.py::isolated_data_root` 重定向到临时目录，
调度器单例与残线程由 `tests/conftest.py::isolated_scheduler` 每例拆干净（手递手 §2）。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.services import scheduler as scheduler_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
BASE = datetime(2026, 10, 18, 12, 0, 0)
#: `tasks.ACTIVE_STATES` 的同值别名（避免把「任务还没结束」写成对内部常量的依赖）。
ACTIVE_STATES = ("queued", "running")
#: 等旧调度线程真的收摊的**有界**预算（INH-1916 / `docs/MCChecker_BLOCKERS.md` B11）。
#: 实测口径：`stop()` 被调到该线程从 `threading.enumerate()` 里消失的耗时，
#: 12 核机器、每轮 400 次同形状重采样、共 22 轮 8800 次——p50 0.049–0.115 毫秒、
#: p99 ≤ 8.7 毫秒，CPU 被纯 Python 死循环占满（负载均值 21）时最坏一次 47 毫秒；
#: `join()` 返回后线程表还列着它的情况 0/8800（CPython 先 `_delete(ident)`
#: 再放开 tstate 锁，所以等完就是看得见没了）。取 2 秒：是最大观测值的 40 倍以上，
#: 又明显短于 `tests/conftest.py::isolated_scheduler` 拆卸时的
#: `SCHEDULER_DRAIN_TIMEOUT`（5 秒），所以线程真的收不了摊时，是本用例先点名，
#: 而不是夹具兜底。等不到就判失败——不许把预算越调越长。
OLD_SCHEDULER_EXIT_TIMEOUT = 2.0


# ---------------------------------------------------------------------------
# 假钟：调度器唯一的时钟出处
# ---------------------------------------------------------------------------


class FakeClock:
    """可推进的虚拟时钟；`advance()` 之后 `now()` 读到新值（线程安全）。"""

    def __init__(self, start: datetime = BASE) -> None:
        self._now = start
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now = self._now + timedelta(seconds=seconds)

    def set(self, moment: datetime) -> None:
        with self._lock:
            self._now = moment


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def pid() -> str:
    from app.core import device_models

    return device_models.add_model("sched")


def _write_schedule(target_pid: str, payload: Dict[str, Any]) -> str:
    """直接落一份 `schedule.json`（绕开被测代码，让初值完全由用例掌握）。"""
    from app.core import storage

    path = storage.get_schedule_file(target_pid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return path


def _read_schedule(target_pid: str) -> Dict[str, Any]:
    from app.core import storage

    with open(storage.get_schedule_file(target_pid), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _make(clock: FakeClock, runner=None, sleep_fn=None, **kwargs):
    from app.services import tasks

    return scheduler_module.Scheduler(
        runner=runner if runner is not None else tasks.get_runner(),
        now_fn=clock.now,
        sleep_fn=sleep_fn if sleep_fn is not None else (lambda _seconds: None),
        **kwargs,
    )


def _wait_finished(task, timeout: float = 10.0) -> Dict[str, Any]:
    """按 **Task 对象引用**等（惰性清理会把刚结束的任务从表里摘掉，不能靠 `get`）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if task.state not in ACTIVE_STATES:
            return task.to_payload()
        time.sleep(0.01)
    raise AssertionError("任务未在 %.1f 秒内结束（当前 %s）" % (timeout, task.state))


def _blocking(release: threading.Event, entered: Optional[threading.Event] = None):
    def fn(task):
        if entered is not None:
            entered.set()
        release.wait(20)

    return fn


# ---------------------------------------------------------------------------
# 1. next_run 的三条播种规则（§10.4）
# ---------------------------------------------------------------------------


class TestSeedNextRun:
    def test_last_run_plus_interval(self, clock):
        last = {"at": (BASE - timedelta(hours=6)).strftime(TIME_FORMAT)}
        got = scheduler_module.compute_next_run(last, 24, clock.now())
        assert got == BASE + timedelta(hours=18)

    def test_no_last_run_uses_reference_plus_interval(self, clock):
        # 卡面验收：interval_hours=1 ⇒ next_run == 基准 + 1 小时
        assert scheduler_module.compute_next_run(None, 1, clock.now()) == BASE + timedelta(hours=1)

    def test_expired_seed_runs_two_minutes_later_not_in_a_storm(self, clock):
        last = {"at": (BASE - timedelta(days=3)).strftime(TIME_FORMAT)}
        got = scheduler_module.compute_next_run(last, 48, clock.now())
        assert got == BASE + timedelta(minutes=2)

    def test_unparseable_last_run_is_treated_as_missing(self, clock):
        for bad in ({"at": "昨天"}, {"at": None}, "不是字典", {}, None):
            assert scheduler_module.compute_next_run(bad, 1, clock.now()) == BASE + timedelta(hours=1)

    def test_out_of_range_interval_is_clamped_for_planning(self, clock):
        # v1 文件里可能有界面早已不允许的值：规划时收进 1–2160，绝不抛、绝不排成 0 间隔
        assert scheduler_module.compute_next_run(None, 0, clock.now()) == BASE + timedelta(hours=1)
        assert scheduler_module.compute_next_run(None, 99999, clock.now()) == BASE + timedelta(hours=2160)

    def test_seed_is_pure_and_never_mutates_last_run(self, clock):
        last = {"at": BASE.strftime(TIME_FORMAT), "success": 1}
        scheduler_module.compute_next_run(last, 24, clock.now())
        assert last == {"at": BASE.strftime(TIME_FORMAT), "success": 1}


# ---------------------------------------------------------------------------
# 2. reload：读全部机型、重算 next_run
# ---------------------------------------------------------------------------


class TestReload:
    def test_only_enabled_profiles_get_a_next_run(self, pid, clock):
        _write_schedule(pid, {"enabled": False, "interval_hours": 24})
        sched = _make(clock)
        sched.reload()
        assert sched.next_run_at(pid) is None
        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        sched.reload()
        assert sched.next_run_at(pid) == BASE + timedelta(hours=1)

    def test_covers_every_profile_in_the_registry(self, pid, clock):
        from app.core import device_models

        other = device_models.add_model("sched2")
        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        _write_schedule(other, {"enabled": True, "interval_hours": 48})
        sched = _make(clock)
        sched.reload()
        assert sched.next_run_at(pid) == BASE + timedelta(hours=1)
        assert sched.next_run_at(other) == BASE + timedelta(hours=48)

    def test_unknown_profile_has_no_next_run(self, clock):
        assert _make(clock).next_run_at("不存在") is None

    def test_reload_recomputes_after_interval_change_without_restarting(self, pid, clock):
        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        sched = _make(clock)
        sched.reload()
        assert sched.next_run_at(pid) == BASE + timedelta(hours=1)
        _write_schedule(pid, {"enabled": True, "interval_hours": 48})
        sched.reload()
        assert sched.next_run_at(pid) == BASE + timedelta(hours=48)

    def test_next_run_is_never_persisted(self, pid, clock):
        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        sched = _make(clock)
        sched.reload()
        assert sched.next_run_at(pid) is not None
        assert "next_run" not in _read_schedule(pid)
        assert "next_run_at" not in _read_schedule(pid)

    def test_reload_survives_a_broken_registry_and_keeps_other_plans(self, pid, clock, monkeypatch):
        from app.core import device_models

        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        sched = _make(clock)
        sched.reload()
        monkeypatch.setattr(
            device_models, "load_models", lambda: (_ for _ in ()).throw(RuntimeError("注册表损坏"))
        )
        sched.reload()  # 不许抛：读不到就保留上一次的计划
        assert sched.next_run_at(pid) == BASE + timedelta(hours=1)


# ---------------------------------------------------------------------------
# 3. tick：到点只提交任务，别的什么都不做
# ---------------------------------------------------------------------------


class TestTickSubmits:
    def test_due_profile_lands_an_update_all_task_with_system_actor(self, pid, clock):
        from app.services import tasks

        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        runner = tasks.get_runner()
        sched = _make(clock, runner=runner)
        sched.reload()
        clock.advance(3600)  # 到点
        fired = sched.tick()
        assert fired == [pid]
        queued = runner.list(pid, False)
        assert len(queued) == 1, "到点必须真的往队列里投一条"
        payload = _wait_finished(queued[0])
        assert payload["type"] == "update_all"
        assert payload["actor"] == scheduler_module.SYSTEM_ACTOR == "system"
        assert payload["profile"] == pid
        assert payload["target"] is None

    def test_not_due_submits_nothing(self, pid, clock):
        from app.services import tasks

        _write_schedule(pid, {"enabled": True, "interval_hours": 24})
        runner = tasks.get_runner()
        sched = _make(clock, runner=runner)
        sched.reload()
        clock.advance(3600)  # 24 小时间隔，1 小时后还不该跑
        assert sched.tick() == []
        assert runner.list(pid, False) == []

    def test_next_run_moves_one_interval_forward_after_a_submit(self, pid, clock):
        from app.services import tasks

        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        sched = _make(clock, runner=tasks.get_runner())
        sched.reload()
        clock.advance(3600)
        sched.tick()
        assert sched.next_run_at(pid) == BASE + timedelta(hours=2)

    def test_disabled_profile_is_never_submitted(self, pid, clock):
        from app.services import tasks

        _write_schedule(pid, {"enabled": False, "interval_hours": 1})
        runner = tasks.get_runner()
        sched = _make(clock, runner=runner)
        sched.reload()
        clock.advance(86400)
        assert sched.tick() == []
        assert runner.list(pid, False) == []

    def test_second_tick_in_the_same_window_does_not_double_submit(self, pid, clock):
        from app.services import tasks

        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        runner = tasks.get_runner()
        sched = _make(clock, runner=runner)
        sched.reload()
        clock.advance(3600)
        sched.tick()
        assert sched.tick() == [], "同一次到点只投一条：next_run 已经推走了"
        assert len(runner.list(pid, False)) == 1

    def test_two_profiles_both_fire(self, pid, clock):
        from app.core import device_models
        from app.services import tasks

        other = device_models.add_model("sched3")
        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        _write_schedule(other, {"enabled": True, "interval_hours": 1})
        runner = tasks.get_runner()
        sched = _make(clock, runner=runner)
        sched.reload()
        clock.advance(3600)
        assert sorted(sched.tick()) == sorted([pid, other])
        for target in (pid, other):
            assert len(runner.list(target, False)) == 1

    def test_submit_failure_never_escapes_tick(self, pid, clock, monkeypatch):
        """投不出去（比如意外的 AppError）也不能把循环炸掉——只记日志、按下一周期排。"""
        from app.services import tasks

        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        sched = _make(clock, runner=tasks.get_runner())
        sched.reload()
        clock.advance(3600)
        monkeypatch.setattr(
            sched.runner,
            "submit",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("队列崩了")),
        )
        assert sched.tick() == []
        assert sched.next_run_at(pid) == BASE + timedelta(hours=2)


# ---------------------------------------------------------------------------
# 4. BUSY 重试（真实行为差异，不许退化成「跳过等下个周期」）
# ---------------------------------------------------------------------------


class TestBusyRetry:
    def test_busy_defers_one_minute_and_still_fires(self, pid, clock, gates):
        from app.services import tasks

        _write_schedule(pid, {"enabled": True, "interval_hours": 24})
        runner = tasks.get_runner()
        release = gates()
        entered = threading.Event()
        manual = runner.submit(
            _http_ctx(pid), "update_all", None, _blocking(release, entered)
        )
        assert entered.wait(5), "手工任务没起来"

        sched = _make(clock, runner=runner)
        sched.reload()
        clock.advance(86400)  # 到点，但有人在跑
        assert sched.tick() == [], "BUSY 时不算提交成功"
        assert runner.list(pid, False) == [manual], "不能排出第二条"
        assert sched.next_run_at(pid) == BASE + timedelta(hours=24) + timedelta(minutes=1), (
            "BUSY 之后要 1 分钟重试，而不是等下一个 24 小时周期"
        )

        # 手工任务结束，重试到点 —— 这一次定时**没有被丢掉**
        release.set()
        _wait_finished(manual)
        clock.advance(60)
        assert sched.tick() == [pid]
        assert len(runner.list(pid, False)) == 2

    def test_busy_retry_keeps_the_original_interval_after_firing(self, pid, clock, gates):
        from app.services import tasks

        _write_schedule(pid, {"enabled": True, "interval_hours": 24})
        runner = tasks.get_runner()
        release = gates()
        entered = threading.Event()
        manual = runner.submit(_http_ctx(pid), "update_all", None, _blocking(release, entered))
        entered.wait(5)
        sched = _make(clock, runner=runner)
        sched.reload()
        clock.advance(86400)
        sched.tick()
        release.set()
        _wait_finished(manual)
        clock.advance(60)
        sched.tick()
        # 投出去之后按**原间隔**排下一次，不是按 1 分钟前那个重试点排
        assert sched.next_run_at(pid) == BASE + timedelta(hours=24) + timedelta(minutes=1) + timedelta(hours=24)

    def test_closed_runner_stops_the_scheduler_instead_of_spinning(self, pid, clock):
        from app.services import tasks

        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        runner = tasks.get_runner()
        runner.shutdown(1)  # 停止序列里 runner 先收：MAINTENANCE 不该被当成「等一分钟」
        sched = _make(clock, runner=runner)
        sched.reload()
        clock.advance(3600)
        assert sched.tick() == []
        assert sched.next_run_at(pid) is None
        assert sched.stopping is True, "队列已经关了就该收摊，不是每秒重试一次"


# ---------------------------------------------------------------------------
# 5. 周期清理：只碰临时区与派生缓存（卡面要点 3）
# ---------------------------------------------------------------------------


class TestPeriodicCleanup:
    def test_no_sweep_before_the_period_elapses(self, clock, monkeypatch):
        calls: List[str] = []
        monkeypatch.setattr(
            scheduler_module, "sweep_temp_area", lambda now=None: calls.append("temp") or 0
        )
        monkeypatch.setattr(
            scheduler_module,
            "sweep_derived_caches",
            lambda now=None: calls.append("cache") or 0,
        )
        sched = _make(clock)
        sched.tick()
        assert calls == [], "启动那一轮什么都不清（每小时/每 24 小时的账从构造那刻起算）"
        clock.advance(scheduler_module.TEMP_SWEEP_SECONDS - 1)
        sched.tick()
        assert calls == [], "差一秒也不许提前清"

    def test_temp_sweep_runs_every_hour(self, clock, monkeypatch):
        calls: List[int] = []
        monkeypatch.setattr(
            scheduler_module, "sweep_temp_area", lambda now=None: calls.append(1) or 0
        )
        monkeypatch.setattr(scheduler_module, "sweep_derived_caches", lambda now=None: 0)
        sched = _make(clock)
        clock.advance(scheduler_module.TEMP_SWEEP_SECONDS)
        sched.tick()
        assert len(calls) == 1
        sched.tick()
        assert len(calls) == 1, "同一个小时只清一次"
        clock.advance(scheduler_module.TEMP_SWEEP_SECONDS)
        sched.tick()
        assert len(calls) == 2

    def test_cache_sweep_runs_every_24_hours(self, clock, monkeypatch):
        temps: List[int] = []
        caches: List[int] = []
        monkeypatch.setattr(
            scheduler_module, "sweep_temp_area", lambda now=None: temps.append(1) or 0
        )
        monkeypatch.setattr(
            scheduler_module, "sweep_derived_caches", lambda now=None: caches.append(1) or 0
        )
        sched = _make(clock)
        clock.advance(scheduler_module.TEMP_SWEEP_SECONDS)
        sched.tick()
        assert (len(temps), len(caches)) == (1, 0), "24 小时没到就不许动派生缓存"
        clock.advance(scheduler_module.CACHE_SWEEP_SECONDS - scheduler_module.TEMP_SWEEP_SECONDS)
        sched.tick()
        assert (len(temps), len(caches)) == (2, 1)

    def test_a_failing_sweep_cannot_kill_the_loop(self, clock, monkeypatch):
        monkeypatch.setattr(
            scheduler_module,
            "sweep_temp_area",
            lambda now=None: (_ for _ in ()).throw(RuntimeError("磁盘忙")),
        )
        sched = _make(clock)
        clock.advance(scheduler_module.TEMP_SWEEP_SECONDS)
        sched.tick()  # 不许抛
        clock.advance(scheduler_module.TEMP_SWEEP_SECONDS)
        sched.tick()

    def test_both_sweeps_answer_to_the_injected_clock_not_two_clocks(self, clock, monkeypatch):
        """`_sweep_if_due` 只听一只钟（修复轮 Minor 4）：两项清理都收到 `tick()` 那个时刻。

        旧形状是「临时区用注入的 `moment`、派生缓存自己读 `time.time()`」——一只函数两只钟，
        假钟下的用例既决定「到没到期」又决定不了「哪些条目算旧」。现在 `now` 一路传到底，
        这里直接看传下去的是不是同一个时刻（`None` 就意味着回落真实钟）。
        """
        seen: List[Tuple[str, Any]] = []
        monkeypatch.setattr(
            scheduler_module, "sweep_temp_area", lambda now=None: seen.append(("temp", now)) or 0
        )
        monkeypatch.setattr(
            scheduler_module,
            "sweep_derived_caches",
            lambda max_age_seconds=None, now=None: seen.append(("cache", now)) or 0,
        )
        sched = _make(clock)
        clock.advance(scheduler_module.CACHE_SWEEP_SECONDS)  # 两项都到期
        sched.tick()
        assert [name for name, _ in seen] == ["temp", "cache"]
        assert [moment for _, moment in seen] == [clock.now(), clock.now()], (
            "两项清理拿到的必须是同一个注入时刻：%s" % (seen,)
        )

    def test_derived_cache_sweep_honours_the_injected_moment(self, pid, clock):
        """同一件事在被测函数那一侧：给了 `now` 就按 `now` 算年龄，不读真实墙上时间。

        判据是「按假钟算很旧、按真实钟算很新」的那一份文件——只有注入的钟生效时它才被删。
        真实钟那一档由本文件其余用例覆盖（它们不传 `now`，走 `datetime.now()` 回落）。
        """
        from app.core import storage

        doc = os.path.join(storage.get_cache_root_dir(pid), "doc_v2")
        os.makedirs(doc, exist_ok=True)
        path = os.path.join(doc, "a" * 64 + ".json.gz")
        with open(path, "wb") as fh:
            fh.write(b"x")
        # 磁盘上是「刚刚」，但假钟被拨到两年之后 ⇒ 按注入的钟它已经老过 TTL。
        far_future = clock.now() + timedelta(days=365 * 2)
        assert scheduler_module.sweep_derived_caches(now=far_future) == 1
        assert not os.path.exists(path), "`now` 没生效：年龄仍然按真实墙上时间算"

    def test_expired_temp_directories_are_removed(self, pid, clock):
        from app.core import storage

        root = storage.get_temporary_dir()
        os.makedirs(root, exist_ok=True)
        gone = os.path.join(root, "expiredtoken")
        keep = os.path.join(root, "livetoken")
        for one in (gone, keep):
            os.makedirs(one, exist_ok=True)
            with open(os.path.join(one, "content"), "wb") as fh:
                fh.write(b"<a>1</a>")
        _write_meta(gone, clock.now() - timedelta(minutes=1))
        _write_meta(keep, clock.now() + timedelta(hours=1))
        assert scheduler_module.sweep_temp_area(now=clock.now()) == 1
        assert not os.path.exists(gone) and os.path.isdir(keep)

    def test_a_temp_directory_without_meta_json_is_not_swept(self, pid, clock):
        """还没有 `meta.json` 的临时目录**不许**被后台清理删掉——那正是某次上传正在写的那一半。

        `temp_store.create()` 的落盘顺序是 `makedirs` → 原文 → `meta.json` 最后，所以旧语义
        「读不到 meta 就删」= 「删正在写的」。本卡把这条清理从「机会式」升级成了独立的小时级
        daemon 线程定时器，窗口也从「同一毫秒内的另一次上传」变成「任何时候」，所以这一档必须收
        （M3-08 修复轮 Important 3）。
        """
        from app.core import storage
        from app.services import temp_store

        root = storage.get_temporary_dir()
        os.makedirs(root, exist_ok=True)
        mid_upload = os.path.join(root, "uploadingtoken")
        os.makedirs(mid_upload, exist_ok=True)
        half = os.path.join(mid_upload, temp_store.CONTENT_FILE)
        with open(half, "wb") as fh:
            fh.write(b"<a>1</a>")

        assert scheduler_module.sweep_temp_area(now=clock.now()) == 0
        assert os.path.isdir(mid_upload), "半成品被抹掉 ⇒ 上传中的用户拿到一次 FileNotFoundError"
        assert os.path.isfile(half), "原文也不许跟着没"

    def test_a_metaless_directory_past_the_grace_is_swept_by_the_timer(self, pid, clock):
        """宽限期外的孤儿目录由**定时器**回收——修复轮的洞：`skip_incomplete` 之后，
        写一半崩掉的目录只剩「下一趟上传开头那次默认档清理」一个出口，没人再上传就永远留着。

        年龄用 `os.utime` 相对**注入时刻**摆位（宽限期 + 60 秒），不睡任何真实时间；
        假钟之外真实 mtime 是「刚刚」，所以只有年龄判定真的生效才会删。
        """
        from app.core import storage
        from app.services import temp_store

        root = storage.get_temporary_dir()
        os.makedirs(root, exist_ok=True)
        dead = os.path.join(root, "deaduploadtoken")
        os.makedirs(dead, exist_ok=True)
        with open(os.path.join(dead, temp_store.CONTENT_FILE), "wb") as fh:
            fh.write(b"<a>1</a>")
        aged = clock.now().timestamp() - temp_store.INCOMPLETE_GRACE_SECONDS - 60
        os.utime(dead, (aged, aged))

        assert scheduler_module.sweep_temp_area(now=clock.now()) == 1
        assert not os.path.exists(dead), "过了宽限期的孤儿必须被定时器收掉，不能只等下一趟上传"

    def test_a_metaless_directory_inside_the_grace_is_still_spared(self, pid, clock):
        """宽限期**之内**的无 meta 目录照旧不碰——年龄闸不许被放宽成「无 meta 一律过期就删」。

        同一份形状、只把 mtime 摆进宽限期里（差 1 小时届满）：如果实现把宽限期写成恒真，
        这一条与上一条不可能同时绿。
        """
        from app.core import storage
        from app.services import temp_store

        root = storage.get_temporary_dir()
        os.makedirs(root, exist_ok=True)
        recent = os.path.join(root, "recenttoken")
        os.makedirs(recent, exist_ok=True)
        half = os.path.join(recent, temp_store.CONTENT_FILE)
        with open(half, "wb") as fh:
            fh.write(b"<a>1</a>")
        aged = clock.now().timestamp() - temp_store.INCOMPLETE_GRACE_SECONDS + 3600
        os.utime(recent, (aged, aged))

        assert scheduler_module.sweep_temp_area(now=clock.now()) == 0
        assert os.path.isdir(recent), "宽限期内的目录可能就是某趟上传正在写的那一半"
        assert os.path.isfile(half)

    def test_create_reclaims_a_metaless_orphan_on_the_real_upload_path(self, pid, clock):
        """走**真上传入口** `create()`：它开头那次清理必须是默认档，孤儿目录才被回收。

        修复轮复审实测：把 `create()` 改成 `purge_expired(skip_incomplete=True)` 后，
        直接调 `purge_expired` 的既有用例（`test_the_upload_path_keeps_reclaiming_metaless_directories`）
        全绿——「唯一回收孤儿的通路」当时一条测试都没有。这条从 `create()` 本身进，
        谁改动那一档谁当场红。默认档不看年龄，所以孤儿不带 meta、mtime 是「刚刚」也照收。
        """
        from app.core import storage
        from app.services import temp_store

        root = storage.get_temporary_dir()
        os.makedirs(root, exist_ok=True)
        orphan = os.path.join(root, "orphanbeforecreate")
        os.makedirs(orphan, exist_ok=True)
        with open(os.path.join(orphan, temp_store.CONTENT_FILE), "wb") as fh:
            fh.write(b"<a>stale</a>")

        meta = temp_store.create(pid, "cid-new", "10.0.0.9", "fresh.xml", b"<a>2</a>", 24, 1024 * 1024)

        assert not os.path.isdir(orphan), "create() 的默认档清理丢了 ⇒ 孤儿只能等 24 小时宽限期，配额继续被吃"
        assert os.path.isdir(os.path.join(storage.get_temporary_dir(), meta["token"]))

    def test_a_temp_directory_with_unreadable_meta_is_still_swept(self, pid, clock):
        """闸只挡「meta 文件还不存在」。meta **在但读不懂**说明这次上传已经死了，照旧回收
        ——否则坏目录永远留在 `data/temporary/`，§7.4 的配额会被孤儿一点点吃光。"""
        from app.core import storage
        from app.services import temp_store

        root = storage.get_temporary_dir()
        os.makedirs(root, exist_ok=True)
        broken = os.path.join(root, "brokentoken")
        os.makedirs(broken, exist_ok=True)
        with open(os.path.join(broken, temp_store.META_FILE), "w", encoding="utf-8") as fh:
            fh.write("{不是 json")

        assert scheduler_module.sweep_temp_area(now=clock.now()) == 1
        assert not os.path.exists(broken)

    def test_the_upload_path_keeps_reclaiming_metaless_directories(self, pid, clock):
        """边界声明：`purge_expired` 的**默认**语义没被顺手改掉。

        不带 `skip_incomplete` 时照旧回收没有 meta 的目录——那是 `temp_store.create()` 自己在
        `makedirs` **之前**跑的那一次（它看不到自己的半成品），也是孤儿目录**最早**的回收入口
        （过了宽限期的孤儿另有定时器兜底，见上面两条宽限期用例）。
        新增的那一档只属于后台调度器；共享服务的既有行为不是本卡的改动面。
        """
        from app.core import storage
        from app.services import temp_store

        root = storage.get_temporary_dir()
        os.makedirs(root, exist_ok=True)
        orphan = os.path.join(root, "orphantoken")
        os.makedirs(orphan, exist_ok=True)

        assert temp_store.purge_expired(now=clock.now()) == 1
        assert not os.path.exists(orphan)

    def test_stale_derived_cache_files_are_removed_and_fresh_kept(self, pid, clock):
        from app.core import storage

        doc = os.path.join(storage.get_cache_root_dir(pid), "doc_v2")
        diff = os.path.join(storage.get_cache_root_dir(pid), "diff_v2")
        for one in (doc, diff):
            os.makedirs(one, exist_ok=True)
        stale_doc = os.path.join(doc, "a" * 64 + ".json.gz")
        fresh_doc = os.path.join(doc, "b" * 64 + ".json.gz")
        stale_diff = os.path.join(diff, "c" * 64 + ".json")
        for path in (stale_doc, stale_diff):
            with open(path, "wb") as fh:
                fh.write(b"x")
            old = time.time() - scheduler_module.CACHE_TTL_SECONDS - 60
            os.utime(path, (old, old))
        with open(fresh_doc, "wb") as fh:
            fh.write(b"y")
        # 权威配置同机型的 configs/ 下：清理绝不许碰它
        cfg_dir = storage.get_configs_dir(pid)
        os.makedirs(cfg_dir, exist_ok=True)
        authoritative = os.path.join(cfg_dir, "keep.xml")
        with open(authoritative, "wb") as fh:
            fh.write(b"<config><a>1</a></config>")
        old = time.time() - scheduler_module.CACHE_TTL_SECONDS - 60
        os.utime(authoritative, (old, old))

        assert scheduler_module.sweep_derived_caches() == 2
        assert not os.path.exists(stale_doc) and not os.path.exists(stale_diff)
        assert os.path.isfile(fresh_doc), "新鲜缓存不许删"
        assert os.path.isfile(authoritative), "清理越界到 configs/ 就是损伤权威数据"

    def test_sweeps_tolerate_missing_directories_and_are_idempotent(self, pid, clock):
        assert scheduler_module.sweep_temp_area(now=clock.now()) == 0
        assert scheduler_module.sweep_derived_caches() == 0
        from app.core import storage

        doc = os.path.join(storage.get_cache_root_dir(pid), "doc_v2")
        os.makedirs(doc, exist_ok=True)
        path = os.path.join(doc, "d" * 64 + ".json.gz")
        with open(path, "wb") as fh:
            fh.write(b"x")
        old = time.time() - scheduler_module.CACHE_TTL_SECONDS - 60
        os.utime(path, (old, old))
        assert scheduler_module.sweep_derived_caches() == 1
        assert scheduler_module.sweep_derived_caches() == 0, "第二次扫到空目录：幂等"
        shutil.rmtree(doc)
        assert scheduler_module.sweep_derived_caches() == 0

    def test_sweep_skips_symlinks_instead_of_following_them(self, pid, clock, tmp_path):
        from app.core import storage

        doc = os.path.join(storage.get_cache_root_dir(pid), "doc_v2")
        os.makedirs(doc, exist_ok=True)
        outside = tmp_path / "precious.xml"
        with open(outside, "wb") as fh:
            fh.write(b"<config/>")
        link = os.path.join(doc, "e" * 64 + ".json.gz")
        os.symlink(str(outside), link)
        scheduler_module.sweep_derived_caches()
        assert os.path.isfile(str(outside)), "跟着符号链接删出去就是越界"


# ---------------------------------------------------------------------------
# 6. 调度线程一个权威字节都不写（本卡最重要的一条）
# ---------------------------------------------------------------------------


class TestSchedulerWritesNothingAuthoritative:
    def test_the_mcsched_thread_deletes_only_temp_and_derived_paths(self, pid, clock, monkeypatch):
        """记录每一次删除与其**发起线程**：`mcsched` 只许出现在临时区与派生缓存里。

        这一条跑在真线程上（不是主线程直接调 `tick()`），断的才是「调度线程自己碰得到哪些
        路径」。观测点两个：`shutil.rmtree`（临时区，`temp_store._remove_tree` 的唯一出口）
        与 `os.remove`（派生缓存）。不去桩 `os.unlink`——`shutil` 内部会带 `dir_fd=` 调它，
        桩进去只会把被测代码搞崩，换不来任何覆盖面。
        """
        from app.core import device_models, storage
        from app.services import tasks

        events: List[Tuple[str, str]] = []
        real_remove = os.remove
        real_rmtree = shutil.rmtree

        def watch_remove(path, *args, **kwargs):
            events.append((threading.current_thread().name, str(path)))
            return real_remove(path, *args, **kwargs)

        def watch_rmtree(path, *args, **kwargs):
            events.append((threading.current_thread().name, str(path)))
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(os, "remove", watch_remove)
        monkeypatch.setattr(shutil, "rmtree", watch_rmtree)

        # 造料：一条过期临时目录 + 一份过期派生缓存 + 一条到期该跑的定时任务
        temp_root = storage.get_temporary_dir()
        os.makedirs(temp_root, exist_ok=True)
        expired = os.path.join(temp_root, "expiredtoken")
        os.makedirs(expired, exist_ok=True)
        with open(os.path.join(expired, "content"), "wb") as fh:
            fh.write(b"<a>1</a>")
        _write_meta(expired, clock.now() - timedelta(minutes=1))
        doc = os.path.join(storage.get_cache_root_dir(pid), "doc_v2")
        os.makedirs(doc, exist_ok=True)
        stale = os.path.join(doc, "f" * 64 + ".json.gz")
        with open(stale, "wb") as fh:
            fh.write(b"x")
        old = time.time() - scheduler_module.CACHE_TTL_SECONDS - 60
        os.utime(stale, (old, old))

        cfg_dir = storage.get_configs_dir(pid)
        os.makedirs(cfg_dir, exist_ok=True)
        authoritative = os.path.join(cfg_dir, "keep.xml")
        with open(authoritative, "wb") as fh:
            fh.write(b"<config><a>1</a></config>")
        # **把它老化**（修复轮 Important 2，对照同文件 `test_stale_derived_cache_files_are_removed_and_fresh_kept`）：
        # 一份「刚刚才写」的权威文件会被 mtime 过滤器顺手保住，于是「把 configs/ 列进清理候选」这种
        # 越界根本撞不到这条用例——上一版就是这样。老到超过 TTL 之后，只有代码本身不把它列进候选
        # 才保得住它。
        os.utime(authoritative, (old, old))
        _write_schedule(pid, {"enabled": True, "interval_hours": 1})

        # 真线程：睡的是真 Event（reload/stop 都能立刻叫醒），所以不会空转。
        sched = scheduler_module.Scheduler(runner=tasks.get_runner(), now_fn=clock.now)
        sched.reload()
        events.clear()
        clock.advance(scheduler_module.CACHE_SWEEP_SECONDS)  # 两类清理都到期
        sched.start()
        deadline = time.time() + 5
        while time.time() < deadline and not _mine(events):
            time.sleep(0.01)
        for task in tasks.get_runner().list(pid, False):
            _wait_finished(task)
        sched.stop()
        sched.join(5)
        assert not sched.is_alive()

        mine = _mine(events)
        assert mine, "本用例没能在调度线程上观测到任何删除，等于没测到"
        assert not os.path.exists(expired) and not os.path.exists(stale), "清理要真的做掉事"
        temp_real = os.path.realpath(temp_root)
        # 允许目录**硬编码**，绝不从 `scheduler_module.DERIVED_CACHE_DIRNAMES` 推导（修复轮 Important 2）：
        # 那个元组是「被测代码自己宣布它能删哪些目录」，从它推导白名单就会跟着它一起放宽——
        # 往里面加一项 `"../configs"` 时上一版仍然全绿，正是这条用例本该抓的那个回归。
        # 机型清单仍然取自注册表：那是环境（有哪些 profile），不是被断言的权限面。
        allowed_cache_dirnames = ("doc_v2", "diff_v2")
        derived = [
            os.path.realpath(os.path.join(storage.get_cache_root_dir(one), name))
            for one in [str(model.get("id")) for model in device_models.load_models()]
            for name in allowed_cache_dirnames
        ]
        for path in mine:
            real = os.path.realpath(path)
            assert _under_any(real, [temp_real] + derived), (
                "调度线程删了临时区/派生缓存之外的东西：%s（全部观测：%s）" % (path, events)
            )
        assert os.path.isfile(authoritative), "权威配置必须原样在"

    def test_tick_itself_performs_no_filesystem_write_at_all(self, pid, clock, monkeypatch):
        """没到清理点时，调度线程连一个字节都不写：真正的下载落盘只发生在 mctask 线程里。

        观测点选 `os.replace`——`fsutil.atomic_write_bytes` 的**提交动作**就是它，本仓库
        每一次权威 JSON 落盘都经过这一句，比桩 `builtins.open` 覆盖面准。
        """
        from app.services import tasks

        commits: List[Tuple[str, str]] = []
        real_replace = os.replace

        def watch_replace(src, dst):
            commits.append((threading.current_thread().name, str(dst)))
            return real_replace(src, dst)

        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        monkeypatch.setattr(os, "replace", watch_replace)
        commits.clear()  # 上面那次造数据不算

        sched = _make(clock, runner=tasks.get_runner())
        sched.reload()
        clock.advance(3600)
        assert sched.tick() == [pid]
        for task in tasks.get_runner().list(pid, False):
            _wait_finished(task)

        assert commits, "一条落盘都没观测到 = 桩没生效，本用例等于没跑"
        # 这一句就是本用例的全部判据（修复轮 Minor 1）：`tick()` 由**主线程**直接驱动，这里根本
        # 没有 `mcsched` 线程存在，所以旧版旁边那句「把提交按 `THREAD_NAME` 筛出来应当为空」
        # 恒真、看着像卡面头条保证而实际一条都不执法。真线程上的观测归
        # `test_the_mcsched_thread_deletes_only_temp_and_derived_paths`；而主线程要是自己写了盘，
        # 线程名会是 `MainThread`，下面这句照样红。
        assert all(
            name.startswith("mctask") for name, _ in commits
        ), "权威写入只许出现在 runner 的工作线程上：%s" % commits


# ---------------------------------------------------------------------------
# 7. 线程生命周期
# ---------------------------------------------------------------------------


class TestThreadLifecycle:
    def test_thread_is_named_and_daemon(self, clock):
        sched = scheduler_module.Scheduler(runner=None, now_fn=clock.now)
        assert sched.name == scheduler_module.THREAD_NAME == "mcsched"
        assert sched.daemon is True, "调度线程是 daemon：它不该拖住进程退出（它一个字节都不写）"

    def test_started_thread_exits_on_stop_and_leaves_no_survivor(self, clock):
        sched = scheduler_module.Scheduler(runner=None, now_fn=clock.now)
        sched.start()
        assert sched.is_alive()
        assert _sched_threads(), "线程真的在 enumerate() 里存在过（不是自己造出来的断言）"
        sched.stop()
        sched.join(5)
        assert not sched.is_alive(), "stop() 之后必须退出"
        assert _sched_threads() == []

    def test_next_run_at_is_none_once_stopped(self, pid, clock):
        """`stop()` 之后 `next_run_at` 必须报 None——那条线程永远不会再去执行它。

        `ordered_stop` 的顺序是「调度器 → 排空队列 → 关 HTTP」（手递手 §5），于是中间有一段
        最长 30 秒的窗口：调度器已经停了、HTTP 还在服务 GET。这时候报一个未来的时刻，等于让
        界面撒谎（和「没有调度器就报 None」是同一条口径，见 `schedule_view` 的说明）。
        """
        _write_schedule(pid, {"enabled": True, "interval_hours": 24})
        sched = _make(clock)
        sched.reload()
        assert sched.next_run_at(pid) == BASE + timedelta(hours=24), "先证明停之前有值"
        sched.stop()
        assert sched.next_run_at(pid) is None, "已经收摊的调度器不许再报一个到不了的时间"
        assert scheduler_module.schedule_view(pid)["next_run_at"] is None

    def test_stop_is_idempotent(self, clock):
        sched = scheduler_module.Scheduler(runner=None, now_fn=clock.now)
        sched.stop()
        sched.stop()
        assert sched.stopping is True

    def test_tick_after_stop_submits_nothing(self, pid, clock):
        from app.services import tasks

        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        runner = tasks.get_runner()
        sched = _make(clock, runner=runner)
        sched.reload()
        sched.stop()
        clock.advance(86400)
        assert sched.tick() == [], "停掉之后还在提交 = 停止序列没有终点"
        assert runner.list(pid, False) == []

    def test_scheduler_is_invisible_to_the_runner_liveness_predicate(self, clock):
        """`mctask` 判据只管「写 data/ 的人」：调度线程故意不在里面。

        这条不是回归、是**边界声明**：把 `mcsched` 塞进 `tasks.worker_threads()` 会让
        「等写的人散尽」把一条不写盘的线程也算进去，删锁反而更晚更含糊。
        """
        from app.services import tasks

        assert not tasks.THREAD_NAME_PREFIX.startswith(scheduler_module.THREAD_NAME)
        assert not scheduler_module.THREAD_NAME.startswith(tasks.THREAD_NAME_PREFIX)
        sched = scheduler_module.Scheduler(runner=None, now_fn=clock.now)
        sched.start()
        try:
            assert tasks.worker_threads() == [], "调度线程不算写盘线程"
        finally:
            sched.stop()
            sched.join(5)

    def test_loop_stays_alive_across_reload_and_dies_on_stop(self, pid, clock):
        """`reload()` 唤醒循环但**不**重启进程；`stop()` 才终止它。"""
        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        sched = scheduler_module.Scheduler(runner=None, now_fn=clock.now)
        sched.reload()
        sched.start()
        assert sched.is_alive()
        _write_schedule(pid, {"enabled": True, "interval_hours": 48})
        sched.reload()
        assert sched.is_alive(), "reload 不该把循环弄死"
        assert sched.next_run_at(pid) == BASE + timedelta(hours=48)
        sched.stop()
        sched.join(5)
        assert not sched.is_alive()


# ---------------------------------------------------------------------------
# 8. 睡眠长度（§10.4：min(剩余秒数, 60)）
# ---------------------------------------------------------------------------


class TestSleepBudget:
    def test_capped_at_sixty_seconds(self, pid, clock):
        _write_schedule(pid, {"enabled": True, "interval_hours": 2160})
        sched = _make(clock)
        sched.reload()
        assert sched.seconds_to_wait() == scheduler_module.MAX_SLEEP_SECONDS

    def test_a_distant_run_still_wakes_every_minute(self, pid, clock):
        """§10.4 的 `min(剩余, 60)`：剩余 100 秒也只睡 60，好让 reload 后的重排尽快生效。"""
        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        sched = _make(clock)
        sched.reload()
        clock.advance(3500)  # 还剩 100 秒
        assert sched.seconds_to_wait() == scheduler_module.MAX_SLEEP_SECONDS
        clock.advance(70)  # 还剩 30 秒：这时按剩余来，不再被上限盖住
        assert sched.seconds_to_wait() == pytest.approx(30, abs=1)

    def test_no_plans_still_sleeps_the_cap(self, clock):
        assert _make(clock).seconds_to_wait() == scheduler_module.MAX_SLEEP_SECONDS

    def test_past_due_never_becomes_a_negative_sleep(self, pid, clock):
        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        sched = _make(clock)
        sched.reload()
        clock.advance(7200)  # 早过了：tick 会把它推走，这里只验不出现负数
        assert sched.seconds_to_wait() == 0.0

    def test_recorded_sleep_lengths(self, pid, clock):
        slept: List[float] = []
        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        sched = _make(clock, sleep_fn=lambda seconds: slept.append(seconds))
        sched.reload()
        sched.sleep_once()
        assert slept == [scheduler_module.MAX_SLEEP_SECONDS]


# ---------------------------------------------------------------------------
# 9. 停止顺序（run.py：调度器 → 排空 runner → 关 HTTP → 才谈删锁）
# ---------------------------------------------------------------------------


class _StopStubServer:
    """只记录自己被关掉的时刻，以及那一刻调度器是不是已经停了。"""

    def __init__(self, log: List[str], observe=None) -> None:
        self.log = log
        self.observe = observe
        self.observed: Any = "unset"
        self.closed = 0

    def shutdown(self) -> None:
        if self.observe is not None:
            self.observed = self.observe()
        self.log.append("server")

    def server_close(self) -> None:
        self.closed += 1


class TestStopOrder:
    def test_scheduler_is_stopped_before_the_runner_and_the_server(self, clock, monkeypatch):
        import run

        log: List[str] = []
        marker: List[str] = []
        sched = scheduler_module.Scheduler(runner=None, now_fn=clock.now)
        original_stop = sched.stop

        def stop_then_mark() -> None:
            marker.append("scheduler")
            return original_stop()

        sched.stop = stop_then_mark  # type: ignore[method-assign]
        monkeypatch.setattr(scheduler_module, "_scheduler", sched)
        runner = _OrderStubRunner(log, marker)
        server = _StopStubServer(log, observe=lambda: marker[:])

        run.ordered_stop(runner, server, timeout=1)

        assert log == ["runner", "server"]
        assert marker == ["scheduler"], "调度器必须在关 HTTP 之前就被叫停"
        assert server.observed == ["scheduler"], "关 HTTP 的那一刻调度器已经停了"

    def test_ordered_stop_kills_the_live_scheduler_thread(self, clock):
        """行为断言（手递手 §5：别只断调用序列）——真的线程真的没了。"""
        import run
        from app.services import tasks

        sched = scheduler_module.init_scheduler(tasks.get_runner(), now_fn=clock.now)
        assert sched.is_alive()
        runner = tasks.get_runner()
        run.ordered_stop(runner, _StopStubServer([], observe=None), timeout=10)
        sched.join(5)
        assert not sched.is_alive(), "ordered_stop 之后调度线程必须已经退出"
        assert _sched_threads() == []

    def test_a_stopped_scheduler_cannot_add_another_task(self, pid, clock):
        """停完之后再把虚拟时间往前推一周，任务表一条都不许多。

        「到点」由用例代跑 `tick()`（循环体调的就是同一只方法、同一份计划），因为
        `compute_next_run` 的播种规则**保证**首跑至少在两分钟之后（避免启动风暴），而
        循环真睡的是 `Event.wait(min(剩余, 60))`——真线程在 5 秒内等不到那一次到期。
        """
        import run
        from app.services import tasks

        _write_schedule(pid, {"enabled": True, "interval_hours": 1})
        runner = tasks.get_runner()
        sched = scheduler_module.init_scheduler(runner, now_fn=clock.now)
        clock.advance(3600)
        assert sched.tick() == [pid], "先证明『没停之前确实会投』"
        before = len(runner.list(pid, False))
        assert before == 1

        run.ordered_stop(runner, _StopStubServer([], observe=None), timeout=10)
        sched.join(5)
        assert not sched.is_alive()

        clock.advance(86400 * 7)  # 往前跑一周
        assert sched.tick() == [], "停止位之后 tick() 必须是空操作"
        # 上一行才是本用例真正执法的那一句（修复轮 Minor 3）。旧版这里还跟了一句
        # `time.sleep(0.2)`，注释写「给任何还活着的循环一轮机会」——那是把话说大了：线程在上面
        # 已经 join 散（`run.ordered_stop` + `sched.join(5)`），而真循环一轮最长睡
        # `MAX_SLEEP_SECONDS` = 60 秒，0.2 秒连一次醒来都观察不到，它既抓不到 stray 提交、
        # 也不会让这条用例更慢或更稳。所以直接删掉那句睡，判据留在行为上。
        assert len(runner.list(pid, False)) == before, "停完之后一条都不许再投"

    def test_lock_release_still_waits_for_mctask_writers(self, pid, clock, gates):
        """本卡不许把「删锁过早」这个 bug 带回来（C1）。

        断的是**时刻**：`lock.release()` 被调用的那一瞬间，`tasks.worker_threads()` 必须已经
        空了；同时调度线程也已经停。这条与 `tests/test_api_tasks.py::TestLockOutlivesWriters`
        同口径，只是把「本卡新加的调度器」放进被测路径里。
        """
        import run
        from app.services import tasks

        release = gates()
        entered = threading.Event()
        runner = tasks.get_runner()
        task = runner.submit(_http_ctx(pid), "update_all", None, _blocking(release, entered))
        assert entered.wait(5)
        threading.Timer(0.3, release.set).start()

        sched = scheduler_module.init_scheduler(runner, now_fn=clock.now)
        lock = _StubLock()
        assert run.stop_and_release(runner, _StopStubServer([], observe=None), lock) is True
        _wait_finished(task)

        assert lock.released == 1
        assert lock.writers_at_release == [], "release 时还有人在写 data/（C1 的老 bug）"
        # 修复轮 Minor 2：旧写法是 `assert not sched.is_alive() or sched.stopping`，而后一支是
        # `stop()` 自己置的位、必然为真 ⇒ 前一支要抓的「线程没退」被自己吞了，整条恒绿。
        # `ordered_stop` 按 R11 故意不等 join，所以**由用例自己等**再断言行为（手递手 §5：
        # 断「线程真的没了」，不断调用序列）。等 5 秒还不散就是真回归。
        sched.join(5)
        assert not sched.is_alive(), "stop_and_release 之后调度线程必须已经退出"
        assert _sched_threads() == [], "停止路径上不许留下调度线程"

    def test_ordered_stop_without_a_scheduler_is_unchanged(self, clock):
        """早退路径（`MCServer` 构造失败、或没走 `run.py`）：单例是 None 也不能抛。"""
        import run

        assert scheduler_module.get_scheduler() is None
        log: List[str] = []
        run.ordered_stop(
            _OrderStubRunner(log, [], expect_scheduler_stopped=False), _StopStubServer(log), timeout=1
        )
        assert log == ["runner", "server"]


class _OrderStubRunner:
    """`shutdown` 里可以顺带检查「调度器是不是已经先停了」（`expect_scheduler_stopped`）。"""

    def __init__(self, log: List[str], marker: List[str], expect_scheduler_stopped: bool = True) -> None:
        self.log = log
        self.marker = marker
        self.expect = expect_scheduler_stopped

    def shutdown(self, timeout: float = 30) -> None:
        if self.expect:
            assert self.marker == ["scheduler"], "runner 排空前调度器必须已经停了"
        self.log.append("runner")

    def wait_for_workers(self, timeout: float = 300.0) -> bool:
        return True


class _StubLock:
    """记录 `release()` 那一刻**还有几个写盘线程**——C1 的回归靶子。"""

    path = "/tmp/stub.lock"

    def __init__(self) -> None:
        self.released = 0
        self.writers_at_release: List[str] = []

    def release(self) -> None:
        from app.services import tasks

        self.released += 1
        self.writers_at_release = [thread.name for thread in tasks.worker_threads()]


# ---------------------------------------------------------------------------
# 10. 单例与启动路径
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_init_registers_and_reset_clears(self, clock):
        from app.services import tasks

        assert scheduler_module.get_scheduler() is None
        sched = scheduler_module.init_scheduler(tasks.get_runner(), now_fn=clock.now)
        assert scheduler_module.get_scheduler() is sched
        assert sched.is_alive()
        scheduler_module.reset_scheduler()
        assert scheduler_module.get_scheduler() is None
        assert _sched_threads() == []

    def test_reinit_stops_the_previous_thread_before_starting_the_next(self, clock, monkeypatch):
        """重复 `init_scheduler` 时**先停旧的再起新的**：两条都活着的那一段会投出双份定时任务。

        `mcsched` 是手搓线程、没有任何安全网看得见它，所以「同一进程里同时只有一条调度线程」
        这条不变量只能由启动函数自己保证。顺序断法：在 `start()` 被调的那一刻回看旧单例的
        停止位——好实现里它已经置位，坏实现（先 start 再 stop）读到的是 False。
        「旧线程已经消失」那一步靠**有界等待**兑现（`OLD_SCHEDULER_EXIT_TIMEOUT`）：它是异步
        结果，瞬时断言会在机器忙的时候假红（B11 / INH-1916），等满预算仍然不退则是真坏。
        """
        from app.services import tasks

        first = scheduler_module.init_scheduler(tasks.get_runner(), now_fn=clock.now)
        assert first.is_alive()
        seen: List[bool] = []

        def spy_start(self):
            seen.append(first.stopping)
            return threading.Thread.start(self)

        monkeypatch.setattr(scheduler_module.Scheduler, "start", spy_start)
        second = scheduler_module.init_scheduler(tasks.get_runner(), now_fn=clock.now)
        assert seen == [True], "新线程起来的时候旧线程还没停：%s" % (seen,)
        assert first.stopping is True
        assert scheduler_module.get_scheduler() is second
        # 到这里为止的三条断言都是**同步**的，也正是本卡要保护的不变量（先停旧再起新）。
        # 「旧线程已经从线程表里消失」不是同步事实：`stop()` 只置位并唤醒，线程要自己
        # 从睡眠里醒过来才收摊（R11 刻意不等 join），机器一忙 OS 就来不及——B11 实测
        # 3/20，本地 8800 次同形状采样里 66 次（0.75%，单轮最高 3.5%）在断言那一刻还看得见
        # 两条线程。所以这里改成**有界等待**：等不到就是失败，绝不因为「再等等总会好的」
        # 而放过，也不许把预算调到看起来永远够。
        first.join(OLD_SCHEDULER_EXIT_TIMEOUT)
        assert not first.is_alive(), (
            "旧调度线程在 %.1f 秒内没有退出（当前线程表 %s）：先停再起这条顺序没有兑现，"
            "或 stop()/循环不再让线程收摊。实测退出耗时 p50 ≈ 0.1 毫秒、最坏 47 毫秒，"
            "等满预算说明它不是慢，是不退。"
            % (OLD_SCHEDULER_EXIT_TIMEOUT, ", ".join(_sched_threads()) or "空")
        )
        assert _sched_threads() == [scheduler_module.THREAD_NAME], "同一进程只许留一条调度线程"

    def test_reset_without_a_scheduler_is_harmless(self):
        scheduler_module.reset_scheduler()
        scheduler_module.reset_scheduler()
        assert scheduler_module.get_scheduler() is None

    def test_new_startup_path_never_imports_apscheduler(self):
        """H2 / T-30 的中间态：新代码零 apscheduler（旧 `app/core/scheduler.py` 与
        `app/pages/` 仍在，M4-07 才删，所以只检查新启动路径）。"""
        import subprocess
        import sys

        hit = subprocess.run(
            ["grep", "-rn", "apscheduler", "run.py", "app/services", "app/server"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert hit.stdout.strip() == "", "新启动路径里出现了 apscheduler：\n%s" % hit.stdout
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys\n"
                "import run  # 新启动入口\n"
                "from app.server.router import build_router  # 新 HTTP 面\n"
                "from app.services import scheduler  # 本卡的新调度器\n"
                "print('apscheduler' in sys.modules)",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "False", "新启动路径把 apscheduler 拖进来了：%s" % proc.stdout


# ---------------------------------------------------------------------------
# 帮助函数
# ---------------------------------------------------------------------------


def _sched_threads() -> List[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith(scheduler_module.THREAD_NAME)]


def _mine(events: List[Tuple[str, str]]) -> List[str]:
    """调度线程（`mcsched`）自己发起的那些删除。"""
    return [path for name, path in events if name == scheduler_module.THREAD_NAME]


def _under_any(real_path: str, roots: List[str]) -> bool:
    for root in roots:
        if real_path == root or real_path.startswith(root + os.sep):
            return True
    return False


def _write_meta(directory: str, expires_at: datetime) -> None:
    with open(os.path.join(directory, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "token": os.path.basename(directory),
                "expires_at": expires_at.strftime(TIME_FORMAT),
                "owner_cid": "cid",
                "owner_ip": "10.0.0.1",
                "profile": "default",
                "name": "a.xml",
                "size": 7,
                "created_at": (expires_at - timedelta(hours=2)).strftime(TIME_FORMAT),
            },
            fh,
        )


def _http_ctx(target_pid: str):
    from app.services.auth import Actor
    from app.services.context import RequestContext

    actor = Actor(ip="127.0.0.1", name="部署者甲", registered=True, is_deployer=True, is_admin=False)
    return RequestContext(ip="127.0.0.1", cid=None, profile_id=target_pid, actor=actor)
