"""标准库调度线程（规范 §10.4、§10.1；卡 8 / M3-08 / INH-1893）。

替代 APScheduler。**它只做「到点往任务队列里投一条 `update_all`」这一件事**，
下载、校验、落盘全在 `services/updates.py`，由 `services/tasks.py` 的工作线程执行。

为什么必须是这个形状（这条约束决定了本模块的全部形态，改动前先读它）：

1. **手搓线程不在本项目任何一道安全网里。** `ThreadPoolExecutor` 的工作线程从创建它的
   线程继承 daemon 位（`threading.py:828`），而 `concurrent.futures.thread._python_exit`
   会在解释器退出时把它们 join 掉——**但那只对池线程成立**。本线程不是池线程：
   `_python_exit` 不等它；`tasks.worker_threads()` 的存活判据按线程名 `mctask` 筛，也看不
   见它；`tests/conftest.py::h3_data_guard` 的第二个已证明盲区正是「最后一次快照之后才落盘
   的线程」。也就是说：**调度线程自己下载或写文件，可以在两道守卫全绿的情况下损伤权威数据。**
2. 所以循环体里**一个字节都不写权威数据**：醒来 → 算哪些机型到点 → 逐个 `submit`。投进
   runner 之后，互斥面（`tasks._CONFLICTING_TYPES`）、进度上报、`FINISH_PARTS` 的修订口径、
   停止排空、按线程名判存活全都自动继承。附带好处是定时与手工「全部更新」天然互斥（免费拿
   到 §10.1 要的 BUSY）。
3. `mcsched` 这个名字**刻意不带 `mctask` 前缀**：`worker_threads()` 的语义是「还有没有人在
   写 `data/`」，把一条不写盘的线程算进去会让「等写的人散尽」这条闸更晚更含糊。它的生命周期
   由本卡的测试自己断言（`tests/test_scheduler.py::TestThreadLifecycle`），外加
   `tests/conftest.py::isolated_scheduler` 每例拆干净——**不能指望远端守卫替我发现**。

线程模型（R11）：daemon 线程 + `Event` 唤醒。`stop()` 只置位并唤醒，**不等 join**；真正的
「等在途任务」是 `runner.shutdown()` 的职责，所以 `run.py` 的停止顺序是
`scheduler.stop()` → `runner.shutdown(30)` → `server.shutdown()`，删单实例锁仍排在
`wait_for_workers()` 之后（C1）。

周期清理（§10.4 要点 3）是循环里唯一碰文件系统的动作，且只碰**派生/临时**数据：
`data/temporary/`（过期上传的临时文件）与 `profiles/<pid>/cache/doc_v2|diff_v2/`
（解析与差异缓存，`services/documents.py`、`services/history.py` 都写着「损坏即丢弃重建」）。
删除按 mtime 年龄判定，写盘用 `fsutil.atomic_write_bytes`（同目录临时文件 + `os.replace`），
所以只可能删到「至少 7 天前的条目」，永远删不到正在写的那一份。中断的代价至多是「某个缓存
还留着、下次再清」，重跑幂等；权威配置 `configs/`、归档 `archive/`、记录 `records/` 一个
都不在候选路径里，`tests/test_scheduler.py::TestSchedulerWritesNothingAuthoritative`
用「记录每一次删除及其发起线程」把这条钉死。

时钟（`now_fn`）与等待（`sleep_fn`）都可注入：测试全在假钟上跑，没有一条为「等一小时」真睡。
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core import device_models, storage
from ..core.errors import BUSY, INVALID_INPUT, MAINTENANCE, AppError
from . import revision, tasks, temp_store
from .context import RequestContext

logger = logging.getLogger(__name__)

#: 线程名：可识别、**不带** `mctask` 前缀（理由见模块 docstring 第 3 条）。
THREAD_NAME = "mcsched"
#: 循环每轮最多睡这么久（§10.4：`Event.wait(min(剩余秒数, 60))`）。
MAX_SLEEP_SECONDS = 60.0
#: `next_run` 已经过期时的补跑延迟：「启动后 2 分钟执行」，避免启动风暴（§10.4）。
STALE_RUN_DELAY_SECONDS = 120.0
#: BUSY（有人在跑同类任务）之后的重试间隔——**不是**跳过等下一个整周期（§10.4 要点 1）。
BUSY_RETRY_SECONDS = 60.0
#: 临时区清理周期（每小时）。
TEMP_SWEEP_SECONDS = 3600.0
#: 派生缓存清理周期（每 24 小时）。
CACHE_SWEEP_SECONDS = 86400.0
#: 派生缓存保留上限：超过这个年龄的文件才会被删（7 天，与 v1 的解析缓存口径一致）。
CACHE_TTL_SECONDS = 7 * 86400.0
#: 只出现在这些子目录下的文件是清理候选（§6.5 / §6.6 的派生数据）。
DERIVED_CACHE_DIRNAMES = ("doc_v2", "diff_v2")
#: `interval_hours` 的合法区间（§7.9：1 小时 ~ 90 天）。
MIN_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 2160
DEFAULT_INTERVAL_HOURS = 24
#: 非法间隔的错误落点：与 `views/settings.js` 的 `fieldError({interval: …})` 和 mock 同口径。
INTERVAL_FIELD = "interval"
#: 定时设置属「设置」分量（§7.3）。**只有 HTTP 写路径**递增它，见 `save_schedule()` 的说明。
SCHEDULE_PART = "settings"
#: 时间字符串格式（与 `tasks.TIME_FORMAT`、`updates._now()` 同一口径）。
TIME_FORMAT = tasks.TIME_FORMAT
#: 定时任务的归属显示（复用 runner 的常量，别拼第二份字符串）。
SYSTEM_ACTOR = tasks.SYSTEM_ACTOR
#: 提交的任务类型。
TASK_TYPE = "update_all"

_INVALID_INTERVAL_MESSAGE = "间隔必须是 %d–%d 之间的整数小时" % (
    MIN_INTERVAL_HOURS,
    MAX_INTERVAL_HOURS,
)


# ---------------------------------------------------------------------------
# next_run 的算法（纯函数，§10.4 的三条播种规则）
# ---------------------------------------------------------------------------


def _clamp_interval_hours(value: Any) -> int:
    """把磁盘上的间隔收进合法区间，用于**规划**（不是校验，校验在 `save_schedule`）。

    v1 的 `schedule.json` 里可能有界面早已不允许的值；规划时越界只会把任务排到更远的将来，
    绝不该抛异常把整条循环打死。
    """
    if isinstance(value, bool):
        return DEFAULT_INTERVAL_HOURS
    try:
        hours = int(value)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_HOURS
    return max(MIN_INTERVAL_HOURS, min(MAX_INTERVAL_HOURS, hours))


def _parse_time(value: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value), TIME_FORMAT)
    except (TypeError, ValueError):
        return None


def compute_next_run(
    last_run: Any, interval_hours: Any, moment: datetime
) -> datetime:
    """一次 `next_run` 播种（§10.4）：

    * 有 `last_run.at` → `at + interval`；
    * 没有（或读不懂）→ `moment + interval`（对齐 APScheduler `IntervalTrigger`）；
    * 算出来已经过期 → `moment + 2 分钟`，避免启动风暴。

    `moment` 在启动那一刻就是启动时间；`reload()` 之后再播种，它就是「重新计划的那一刻」，
    与界面上「保存后立即按新间隔重新计划」的文案一致。纯函数：不改动 `last_run`。
    """
    interval = timedelta(hours=_clamp_interval_hours(interval_hours))
    at = _parse_time(last_run.get("at")) if isinstance(last_run, dict) else None
    candidate = (at + interval) if at is not None else (moment + interval)
    if candidate <= moment:
        candidate = moment + timedelta(seconds=STALE_RUN_DELAY_SECONDS)
    return candidate


def _interval_value(raw: Any) -> int:
    """GET 回显用的间隔：读不到整数就回落默认 24（与 `load_schedule` 的默认一致）。"""
    if isinstance(raw, bool):
        return DEFAULT_INTERVAL_HOURS
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_HOURS


def system_context(pid: str) -> RequestContext:
    """后台提交的上下文：`actor=None` ⇒ `tasks._actor_of()` 给出 `system`（§7.10）。"""
    return RequestContext(ip="", cid=None, profile_id=pid, actor=None)


# ---------------------------------------------------------------------------
# 周期清理：只碰临时区与派生缓存
# ---------------------------------------------------------------------------


def sweep_temp_area(now: Optional[datetime] = None) -> int:
    """删掉过期临时目录（每小时一次；实现就是 `temp_store` 的惰性清理入口）。"""
    removed = temp_store.purge_expired(now=now)
    if removed:
        logger.info("定时清理：临时区删除 %d 个过期目录", removed)
    return removed


def _profile_ids() -> Optional[List[str]]:
    """机型清单；注册表读坏了返回 **None**（区别于「真的一个机型都没有」）。

    `device_models.load_models()` 保证 `default` 恒在，所以正常情况下不会返回空列表；
    返回 None 时调用方要保留既有状态，不能把计划清空。
    """
    try:
        models = device_models.load_models()
    except Exception as exc:  # 注册表损坏时跳过本轮，不阻断循环（§5.4 同口径）
        logger.warning("读取机型注册表失败：%s", exc)
        return None
    return [str(model.get("id")) for model in models if str(model.get("id") or "").strip()]


def _read_schedule_cfg(pid: str) -> Dict[str, Any]:
    """读一份 `schedule.json`（只读，不加写锁）。

    `_save_json` 走原子写，所以读到的永远是完整文件；读坏损/缺文件时回落默认值，
    一条机型的读失败不许把整个计划清空。
    """
    try:
        with storage.use_profile(pid):
            cfg = storage.load_schedule()
    except Exception as exc:
        logger.warning("读取机型 %s 的定时配置失败，本轮按未启用处理：%s", pid, exc)
        return {}
    return cfg if isinstance(cfg, dict) else {}


def sweep_derived_caches(max_age_seconds: float = CACHE_TTL_SECONDS) -> int:
    """删掉各机型 `cache/doc_v2`、`cache/diff_v2` 下年龄超过 `max_age_seconds` 的文件。

    三条安全前提（都在 `tests/test_scheduler.py` 里钉着）：

    * **只有派生数据**：`documents._disk_load` 与 `history._cache_read` 读到损坏就丢弃重建，
      删了最坏是「下一次打开重新算」，不影响任何权威内容；
    * **按年龄筛**：只删 `max_age_seconds` 之前的，`fsutil.atomic_write_bytes` 那个同目录
      临时文件的年龄永远是「刚刚」，所以删不到正在写的那一份，也不可能删到半成品；
    * **不跟符号链接**：缓存目录里若被塞进一个指向别处的链接，跟着删就是越界。
    """
    cutoff = time.time() - max(0.0, float(max_age_seconds))
    ids = _profile_ids()
    if ids is None:
        return 0
    removed = 0
    for pid in ids:
        for dirname in DERIVED_CACHE_DIRNAMES:
            directory = os.path.join(storage.get_cache_root_dir(pid), dirname)
            if not os.path.isdir(directory):
                continue
            for entry in os.listdir(directory):
                path = os.path.join(directory, entry)
                try:
                    if os.path.islink(path) or not os.path.isfile(path):
                        continue
                    if os.stat(path).st_mtime > cutoff:
                        continue
                    os.remove(path)
                    removed += 1
                except OSError as exc:
                    # 别人正在删 / 刚被删掉：派生数据少删一个不叫故障，下一轮再来。
                    logger.debug("定时清理跳过 %s：%s", path, exc)
    if removed:
        logger.info("定时清理：派生缓存删除 %d 个文件", removed)
    return removed


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class _Plan:
    """一个机型的调度状态。`next_run` 只在内存里（§10.4：不持久化）。"""

    __slots__ = ("enabled", "interval_hours", "next_run")

    def __init__(self, enabled: bool, interval_hours: int, next_run: Optional[datetime]) -> None:
        self.enabled = enabled
        self.interval_hours = interval_hours
        self.next_run = next_run


class Scheduler(threading.Thread):
    """§10.4 的 `Scheduler(threading.Thread)`。

    `runner` / `now_fn` / `sleep_fn` 都能注入，是为了让测试既不必真睡、也不必把生产路径换成
    假的——`runner=None` 时每次提交现取 `tasks.get_runner()`（单例），所以 `run.py` 里
    「先 `init_runner` 再造调度器」的顺序不是隐式契约。
    """

    def __init__(
        self,
        runner: Optional[Any] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
        sleep_fn: Optional[Callable[[float], None]] = None,
    ) -> None:
        threading.Thread.__init__(self, name=THREAD_NAME, daemon=True)
        self._runner = runner
        self._now_fn = now_fn or datetime.now
        # 默认等待就是 Event：reload()/stop() 都能把它立刻叫醒（§10.4「Event 唤醒循环」）。
        self._wakeup = threading.Event()
        self._stopping = threading.Event()
        self._sleep_fn = sleep_fn or self._wakeup.wait
        self._lock = threading.Lock()
        self._plans: Dict[str, _Plan] = {}
        now = self._now_fn()
        # 清理的账从构造那一刻起算：启动那一轮**不**删任何东西。
        self._next_temp_sweep = now + timedelta(seconds=TEMP_SWEEP_SECONDS)
        self._next_cache_sweep = now + timedelta(seconds=CACHE_SWEEP_SECONDS)

    # ------------------------------------------------------------------
    # 对外（§10.4 的接口）
    # ------------------------------------------------------------------

    @property
    def runner(self) -> Any:
        """任务队列：构造时注入的，或现取进程内单例。"""
        return self._runner if self._runner is not None else tasks.get_runner()

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    def now(self) -> datetime:
        return self._now_fn()

    def reload(self) -> None:
        """读全部机型 `schedule.json`、重算 `next_run`、唤醒循环（不重启进程）。

        只**读**磁盘，一个字节都不写。`update_all` 写 `last_run` 由 worker 线程做。
        """
        plans = self._read_plans()
        if plans is None:
            return  # 注册表读坏了：保留上一轮计划，别把已有排期清空
        with self._lock:
            self._plans = plans
        self._wakeup.set()

    def next_run_at(self, pid: str) -> Optional[datetime]:
        """§7.9 的 `next_run_at`：只有启用、有排期、且**这条线程还没收摊**时才有值；不持久化。

        `stop()` 之后一律报 None：`ordered_stop` 的顺序是「调度器 → 排空队列 → 关 HTTP」，
        中间那段（最长 30 秒）HTTP 还在服务 GET，而这条线程已经不会再执行任何东西了——
        报一个未来的时刻就是撒谎（与 `schedule_view` 「没有调度器就报 None」同一条口径）。
        """
        if self._stopping.is_set():
            return None
        with self._lock:
            plan = self._plans.get(pid)
        if plan is None or not plan.enabled:
            return None
        return plan.next_run

    def stop(self) -> None:
        """置位 + 唤醒，**不等 join**（R11），幂等（信号连按两次不会重复动作）。

        之后 `tick()` 直接返回空列表——即使调用方已经不在 `run()` 里了，「停完还往队列里投」
        这条路径也被堵死，`runner.shutdown()` 的排空才有确定终点。
        """
        self._stopping.set()
        self._wakeup.set()

    def run(self) -> None:
        """循环：`tick()` → 睡 `min(剩余秒数, 60)`（§10.4）。"""
        logger.info("定时调度线程已启动（线程名 %s，daemon=%s）", self.name, self.daemon)
        while not self._stopping.is_set():
            self._wakeup.clear()
            try:
                self.tick()
            except Exception:  # 一轮坏了不能带走线程：下一轮照常
                logger.exception("定时调度本轮执行异常（已跳过，循环继续）")
            if self._stopping.is_set():
                break
            try:
                self.sleep_once()
            except Exception:  # pragma: no cover - 注入的 sleep 自己崩了
                logger.exception("定时调度等待异常（已跳过，循环继续）")
        logger.info("定时调度线程已退出")

    def sleep_once(self) -> None:
        """睡一轮（`seconds_to_wait()` 那么多秒）。独立成方法是为了让测试能不开线程驱动它。"""
        self._sleep_fn(self.seconds_to_wait())

    def seconds_to_wait(self) -> float:
        """最早的 `next_run` 还剩多久，封顶 `MAX_SLEEP_SECONDS`（§10.4）。"""
        moment = self._now_fn()
        with self._lock:
            upcoming = [
                plan.next_run
                for plan in self._plans.values()
                if plan.enabled and plan.next_run is not None
            ]
        if not upcoming:
            return MAX_SLEEP_SECONDS
        remaining = (min(upcoming) - moment).total_seconds()
        return max(0.0, min(remaining, MAX_SLEEP_SECONDS))

    # ------------------------------------------------------------------
    # 一轮工作（时钟取自 `now_fn`，测试可以直接调它）
    # ------------------------------------------------------------------

    def tick(self) -> List[str]:
        """处理所有到点的机型，返回**真的投出去了**的 pid。

        `stop()` 之后是空操作（见 `stop()` 的说明）。清理到期就顺手做，全程只删派生/临时数据。
        """
        if self._stopping.is_set():
            return []
        moment = self._now_fn()
        fired: List[str] = []
        with self._lock:
            snapshot = list(self._plans.items())
        for pid, plan in snapshot:
            if not plan.enabled or plan.next_run is None or plan.next_run > moment:
                continue
            if self._fire(pid, plan, moment):
                fired.append(pid)
        self._sweep_if_due(moment)
        return fired

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _read_plans(self) -> Optional[Dict[str, _Plan]]:
        ids = _profile_ids()
        if ids is None:
            return None  # 注册表读坏了：保留上一轮计划，别把已有排期清空
        moment = self._now_fn()
        plans: Dict[str, _Plan] = {}
        for pid in ids:
            cfg = _read_schedule_cfg(pid)
            enabled = bool(cfg.get("enabled", False))
            hours = _interval_value(cfg.get("interval_hours", DEFAULT_INTERVAL_HOURS))
            next_run = compute_next_run(cfg.get("last_run"), hours, moment) if enabled else None
            plans[pid] = _Plan(enabled=enabled, interval_hours=hours, next_run=next_run)
        return plans

    def _fire(self, pid: str, plan: _Plan, moment: datetime) -> bool:
        """投一条 `update_all`；这是循环对「执行更新」的全部参与方式。"""
        handler = tasks.handler_for(TASK_TYPE)
        if handler is None:  # pragma: no cover - tasks 模块导入期就注册好了
            logger.error("任务类型 %s 没有执行体，机型 %s 本轮跳过", TASK_TYPE, pid)
            return False
        try:
            task = self.runner.submit(system_context(pid), TASK_TYPE, None, handler)
        except AppError as exc:
            return self._reschedule(pid, plan, moment, exc)
        except Exception:
            logger.exception("定时提交失败：%s（本轮跳过，按下一周期排）", pid)
            self._set_next_run(plan, moment + timedelta(hours=plan.interval_hours))
            return False
        self._set_next_run(plan, moment + timedelta(hours=plan.interval_hours))
        logger.info(
            "定时更新已提交：机型=%s 间隔=%d 小时 id=%s actor=%s",
            pid,
            plan.interval_hours,
            task.id,
            task.actor,
        )
        return True

    def _reschedule(self, pid: str, plan: _Plan, moment: datetime, exc: AppError) -> bool:
        """提交失败的三种收口。`BUSY` **不丢这次定时**（§10.4 要点 1）。"""
        if exc.code == BUSY:
            self._set_next_run(plan, moment + timedelta(seconds=BUSY_RETRY_SECONDS))
            logger.info(
                "定时更新撞上手同类任务（机型=%s，%s），%d 秒后重试，不丢这一次",
                pid,
                exc.message,
                int(BUSY_RETRY_SECONDS),
            )
            return False
        if exc.code == MAINTENANCE:
            # 队列已经停止接收：这就是「该收摊了」的信号，重试只会每秒投一次空。
            self.stop()
            self._set_next_run(plan, None)
            logger.info("任务队列已停止接收，调度线程随之收摊（机型=%s）", pid)
            return False
        self._set_next_run(plan, moment + timedelta(hours=plan.interval_hours))
        logger.warning("定时更新提交被拒（机型=%s，%s），按下一周期排", pid, exc.message)
        return False

    def _set_next_run(self, plan: _Plan, moment: Optional[datetime]) -> None:
        with self._lock:
            plan.next_run = moment

    def _sweep_if_due(self, moment: datetime) -> None:
        """到期才清；两项各自记账，互不影响（§10.4 要点 3）。"""
        if moment >= self._next_temp_sweep:
            self._next_temp_sweep = moment + timedelta(seconds=TEMP_SWEEP_SECONDS)
            try:
                sweep_temp_area(now=moment)
            except Exception:
                logger.exception("临时区定时清理失败（下一小时再试）")
        if moment >= self._next_cache_sweep:
            self._next_cache_sweep = moment + timedelta(seconds=CACHE_SWEEP_SECONDS)
            try:
                sweep_derived_caches()
            except Exception:
                logger.exception("派生缓存定时清理失败（24 小时后再试）")


# ---------------------------------------------------------------------------
# §7.9 读写（handler 只调这两只函数，自己不碰 storage、不碰计数）
# ---------------------------------------------------------------------------


def _format(moment: Optional[datetime]) -> Optional[str]:
    return moment.strftime(TIME_FORMAT) if moment is not None else None


def schedule_view(pid: str) -> Dict[str, Any]:
    """`GET /p/{pid}/schedule` 的载荷（§7.9）。读接口，永不递增修订号。

    `next_run_at` 只由**在跑的调度器**给出：没有调度器时报 `None`，界面显示「—」。
    凭空算一个「下次执行时间」是在撒谎——那个进程里没有人会去执行它。
    """
    cfg = _read_schedule_cfg(pid)
    sched = get_scheduler()
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "interval_hours": _interval_value(cfg.get("interval_hours", DEFAULT_INTERVAL_HOURS)),
        "last_run": cfg.get("last_run"),
        "next_run_at": _format(sched.next_run_at(pid)) if sched is not None else None,
    }


def validate_interval(value: Any) -> int:
    """§7.9 的间隔校验：1–2160 的**整数**。非法即 400，调用方在落任何字节之前拿到它。

    只认 `int`：`1.5`、`"abc"`、`True` 一律拒。`views/settings.js` 发的就是 JS number
    （整值），mock 也按同一条范围校验，口径一致。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise AppError(INVALID_INPUT, _INVALID_INTERVAL_MESSAGE, field=INTERVAL_FIELD)
    if not MIN_INTERVAL_HOURS <= value <= MAX_INTERVAL_HOURS:
        raise AppError(INVALID_INPUT, _INVALID_INTERVAL_MESSAGE, field=INTERVAL_FIELD)
    return value


def save_schedule(pid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """`PUT /p/{pid}/schedule`：校验 → 锁内读-改-写 → 递增 `settings` → `reload()`。

    * **先校验后落笔**：非法间隔在碰到文件之前就抛，`schedule.json` 保持原字节。
    * **读-改-写整段扣在同一把机型锁里**（§5.4 / 卡 5 复审 I1）：分两次取锁的话，两次
      锁之间跑完的 `update_all` 会把这次的新 `enabled`/`interval_hours` 整份覆盖掉。
      `storage.save_schedule` 内部再取一次同一把 per-pid `RLock`，可重入、无死锁。
    * **成功才递增**（全局约束）；读与失败写都不递增。
    * 这里的 `revision.bump` 属**service 层的写路径**（handler 不碰计数）。调度**线程自己**
      一次都不调 `revision.bump`：它提交的 `update_all` 结束时按 `tasks.FINISH_PARTS`
      补 `settings`，那份记账在 runner 里（手递手 §3 / R39）。
    """
    enabled = bool(payload.get("enabled", False))
    hours = validate_interval(payload.get("interval_hours"))
    with storage.profile_write(pid):
        with storage.use_profile(pid):
            cfg = storage.load_schedule()
            if not isinstance(cfg, dict):
                cfg = {}
            cfg["enabled"] = enabled
            cfg["interval_hours"] = hours
            storage.save_schedule(cfg)
    revision.bump(pid, SCHEDULE_PART)
    sched = get_scheduler()
    if sched is not None:
        sched.reload()  # 不重启进程（§10.4）
    view = schedule_view(pid)
    logger.info("定时设置已保存：机型=%s 启用=%s 间隔=%d 小时 下次=%s", pid, enabled, hours, view["next_run_at"] or "—")
    return view


# ---------------------------------------------------------------------------
# 进程内单例：`run.py` 注入，`api_schedule.py` 与测试都走这里
# ---------------------------------------------------------------------------

_scheduler: Optional[Scheduler] = None
_scheduler_lock = threading.Lock()


def init_scheduler(
    runner: Optional[Any] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
    start: bool = True,
) -> Scheduler:
    """造（并默认启动）调度线程；重复调用会先干净地停掉旧的。

    顺序是**先停旧的、再起新的**：`mcsched` 没有任何安全网看得见（不是池线程、不在
    `mctask` 判据里），所以「同一进程里只有一条调度线程」只能由这里保证。反过来起会留下
    一段两条线程都在醒着的窗口，两边都往队列里投同一批 `update_all`（撞 BUSY 的那条还会
    按 1 分钟重试，等于多跑一次全量更新）。`stop()` 不等 join（R11），所以这里不会慢。
    """
    global _scheduler
    with _scheduler_lock:
        previous, _scheduler = _scheduler, Scheduler(runner=runner, now_fn=now_fn, sleep_fn=sleep_fn)
        current = _scheduler
    if previous is not None:
        previous.stop()
    current.reload()
    if start:
        current.start()
    return current


def get_scheduler() -> Optional[Scheduler]:
    """当前调度器；**没有就是 None**（读取路径要靠它区分「没人会执行」与「有排期」）。"""
    with _scheduler_lock:
        return _scheduler


def is_running() -> bool:
    """`/session` 的 `server.scheduler`（§7.2）：进程里有没有一条活着的新调度线程。"""
    sched = get_scheduler()
    return bool(sched is not None and sched.is_alive() and not sched.stopping)


def reset_scheduler(timeout: float = 5.0) -> None:
    """停掉并清空单例（`run.py` 的退出路径与 `tests/conftest.py::isolated_scheduler`）。

    `stop()` 不等 join（R11），这里的 `timeout` 是给**调用方**（测试夹具）用来确认线程真的
    散了的：等不散就返回 False，让夹具去点名，而不是静默放过。
    """
    global _scheduler
    with _scheduler_lock:
        previous, _scheduler = _scheduler, None
    if previous is None:
        return
    previous.stop()
    if previous.is_alive():
        previous.join(max(0.0, float(timeout)))
        if previous.is_alive():
            logger.warning("调度线程 %s 在 %.1f 秒内没有退出（它是 daemon，不会拖住进程退出）", previous.name, timeout)
