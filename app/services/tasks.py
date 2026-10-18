"""后台任务队列（规范 §10.1、§7.10；卡 6 / M3-07 / INH-1892）。

分层（§3.3）：本模块是 service 层，只用标准库 + `core.errors` + `services.revision`
+ `services.updates`。它**不 import HTTP、也不碰 `storage`**——下载、校验、落盘全部
在 `services/updates.py` 里；队列只负责「排队、跑、记账、清理」。卡 7（更新源设置页）、
卡 8（调度器）、卡 9（修改记录）都往这里提交任务。

三条不显然的约束：

1. **「进程真正退出」确实要等在途任务结束，但别拿 `daemon` 位当判据**（R9 / C1）。
   `ThreadPoolExecutor` 的工作线程从**创建它的线程**继承 daemon 位（`threading.py:828`）：
   测试里从主线程提交 ⇒ 非 daemon；真实服务器里 `submit` 由 HTTP 处理线程调、
   `MCServer.daemon_threads = True` ⇒ 那几条工作线程就是 daemon。两种形态下解释器最后都
   会等它们（非 daemon 由 `threading._shutdown` join，daemon 由 `concurrent.futures.thread`
   注册的 `_python_exit` join），**但单实例锁是在那之前被删的**——所以删锁前必须自己
   数线程，判据是线程名，见 `worker_threads()` / `wait_for_workers()` / `run.stop_and_release`。
   停止序列本身仍然是先 `shutdown()` 等任务、再关 HTTP，见 `run.ordered_stop`；顺序反了
   `serve_forever` 会先退出、主线程走完而任务还在跑。
   `shutdown()` 的超时**只是「这一层不等了」**（没有 `cancel_futures`），所以删锁之前还要
   `wait_for_workers()` 再等一次、等不散就不删（C1）。
2. **`fn` 跑在工作线程里，机型锁由 `fn` 自己加**（§10.1）。HTTP 线程只做「解析 →
   授权 → 入队 → 202」，绝不持锁等下载（有回归用例用真实 `MCServer` 钉住这点）。
   因此 handler 侧不允许出现任何超过 2 秒的同步下载。
3. **进度变化要能被别的客户端看到**（§10.1「每次进度变化递增 `tasks`」）。卡 5 的
   `updates._publish()` 是直接写 `task.progress[...]` 与 `task.result["items"].append()`
   的，所以这两个容器是带通知的浅子类；去重键是 `(done, total, 条数)`——同一个值
   写第二次不算变化，否则无意义的递增会把别人的刷新提示刷成噪声。

修订号口径（§10.1 与卡 5 裁定的拼接处）：

* 入队、状态切换、每次进度变化 → `revision.bump(pid, "tasks")`。**`tasks` 这个分量在
  本卡之前没有任何调用点**，这里是第一个。
* 结束 → 按类型 `FINISH_PARTS`。里面只有 `update_all → settings`（它写
  `schedule.json.last_run`，卡 5 已裁定自己不递增 `settings`，由 runner 补）。
  `files` **不在**这里：`updates.update_source` 只在字节真的变了时递增一次（R5），
  runner 无从判断「有没有变」，再补一次就是虚假通知。
* 抛异常的失败任务不递增 `files`/`settings`（失败写不递增，全局约束），但一定递增
  `tasks`——否则别人的界面会永远停在「进行中」。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import DEFAULT_MAX_WORKERS
from ..core.errors import BUSY, INVALID_INPUT, MAINTENANCE, AppError
from . import revision, updates
from .context import RequestContext

logger = logging.getLogger(__name__)

#: 任务类型清单（§7.10）。卡 8 提交 `update_all`、卡 9 填 `refresh_records` 主体。
TASK_TYPES = ("update_source", "update_all", "refresh_records")
#: 已结束任务在内存里的保留窗口（§7.10：1 小时 / 最近 200 条）。
RETAIN_SECONDS = 3600
RETAIN_MAX = 200
#: 修订分量名（§7.3 的五段之一）。
TASK_PART = "tasks"
#: `shutdown()` 的默认等待上限（§10.1 签名，也是停止序列要求的 30 秒）。
DEFAULT_SHUTDOWN_TIMEOUT = 30.0
#: 工作线程名前缀：`worker_threads()` 用它判「还有没有人在写 data/」，测试也用它筛残留线程。
THREAD_NAME_PREFIX = "mctask"

#: `shutdown()` 自己那条等待线程的名字：同样带前缀，但它只跑 `pool.shutdown`，一个字节都不写。
SHUTDOWN_WAITER_NAME = "%s-shutdown" % THREAD_NAME_PREFIX

#: **删单实例锁之前**还要再等多久（C1，取值见下面的推导）。
#:
#: `shutdown()` 那 30 秒只是「HTTP 侧的停止要多快」，不是「写的人多久会散」：
#: `app/config.py` 给的是 `DEFAULT_DOWNLOAD_TIMEOUT_S = 30` × `DEFAULT_DOWNLOAD_RETRIES = 3`
#: 再加退避 `downloader._RETRY_DELAYS = (1.0, 2.0)` ⇒ **一个卡死的源最坏 ≈ 93 秒**，
#: 而 `updates.update_all` 是**串行**遍历映射项的，所以「批量更新进行中收到 SIGTERM」
#: 几乎必然撞穿 30 秒的排空预算。既然超时是常态，删锁前必须再等一次，且预算要**明显**
#: 大于排空预算：300 秒 = 排空预算的 10 倍，也盖得住串行撞三个死源（3 × 93 ≈ 279 秒）。
#: 再往上加没意义——`wait_for_workers` 等不散时**不删锁**并说明原因，而残留锁是可以被
#: 下一个实例按 pid 接管的（`SingleInstanceLock.acquire` → `_process_alive`，
#: `http.py:139-152`），「删了锁但写的人还在」却不可恢复。取值本身有回归用例守着
#: （`tests/test_api_tasks.py::TestLockOutlivesWriters::test_exit_budget_is_far_larger_than_the_drain_budget`）。
EXIT_WORKER_TIMEOUT = 300.0

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
PARTIAL = "partial"
FAILED = "failed"
ACTIVE_STATES = (QUEUED, RUNNING)
FINISHED_STATES = (SUCCEEDED, PARTIAL, FAILED)

#: `result.items[].status` 里算「这一项失败」的取值，与 `updates._last_run` 的
#: 失败口径一致（success/unchanged/skipped/not_found 都不算失败）。
FAILED_ITEM_STATUSES = frozenset({"download_failed", "rejected", "too_large", "error"})

#: 结束时按类型递增的修订分量（§10.1）。`refresh_records` 留给卡 9：记录属于文件域，
#: 等它把主体实现填进来再决定是否补 `files`。
FINISH_PARTS: Dict[str, Tuple[str, ...]] = {
    "update_source": (),
    "update_all": ("settings",),
    "refresh_records": (),
}

#: 互斥面（§10.1 + 卡 6 复审 F2）：`ttype` -> 同一机型内要挡掉的在途类型。
#:
#: 三类任务都算「更新类」，规则不是全对成机型级串行，只有 `refresh_records` 例外地
#: **不看 target**。理由：`updates.update_all` 自己对每个有 `record_url` 的映射项跑一次
#: `refresh_records`（`updates.py:164`），而 `refresh_records` 是对 `records/<name>` 的
#: 读-改-写。单独 `POST {"type":"refresh_records","name":X}` 与 `update_all`（或任何
#: `update_source`）并发，就是把同一个 RMW 在同一机型、同一 target 上跑两遍——机型锁只
#: 保证单次读写不被撕裂，不会让两次互相覆盖丢失，所以这一条必须按**机型**整块挡掉；
#: `update_all` 会碰全部映射项，因此按 target 判也挡不住。
#:
#: `update_source ↔ update_source` 仍按 target 细分（不细分就是机型级串行，卡 5 的多源
#: 并行更新会退化成排队）：不同源写不同的 `configs/<name>`，而共享文件
#: （`update_status.json`、`schedule.json`）的读-改-写已经整段扣在机型锁里
#: （`updates._finish` / `update_all`），并行不会丢东西。
_CONFLICTING_TYPES: Dict[str, Tuple[str, ...]] = {
    "update_source": ("update_all", "refresh_records"),
    "update_all": ("update_source", "refresh_records"),
    "refresh_records": ("update_source", "update_all"),
}

#: §7.1 的本地时间字符串格式（与 `updates._now()` 同一口径）。
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

#: 调度器（卡 8）用 `actor=None` 的上下文提交任务时的归属显示。
SYSTEM_ACTOR = "system"

#: 通用「互斥键」里 target 缺位时的占位（§10.1：`target or '*'`）。
ANY_TARGET = "*"


def now_string() -> str:
    """服务器本地时间字符串。

    保留期判定也走这只「钟」（清理时把它和 `finished_at` 都解析成 datetime），
    这样测试只要 monkeypatch 本函数就能模拟「一小时之后」，不必真等。
    """
    return datetime.now().strftime(TIME_FORMAT)


def _key(pid: str, ttype: str, target: Optional[str]) -> Tuple[str, str, str]:
    return (pid, ttype, target or ANY_TARGET)


def _state_from_items(items: List[Dict[str, Any]]) -> str:
    """任务状态由结果条目推出：全成 `succeeded`，全败 `failed`，部分 `partial`。"""
    failed = [item for item in items if str(item.get("status") or "") in FAILED_ITEM_STATUSES]
    if not items or not failed:
        return SUCCEEDED
    return FAILED if len(failed) == len(items) else PARTIAL


def worker_threads() -> List[threading.Thread]:
    """还没散去的 `mctask` 工作线程——「现在还有人写 `data/` 吗」的唯一可信读数。

    判据只能用**线程名**，不能用 `daemon` 位：CPython 的 `Thread.__init__`
    （`threading.py:828`）把 daemon 从创建者继承下来，而真实服务器里 `submit` 是 HTTP
    处理线程调的、`MCServer.daemon_threads = True`，所以池里那几条工作线程**就是 daemon**。
    第一版按 `not t.daemon` 过滤，手工 SIGTERM 验证时 30 秒就把锁删了而下载还在跑，
    等于没修（C1 就栽在这上面，见 `docs/know-how.md`）。

    唯一要摘掉的是 `shutdown()` 自己那条 `mctask-shutdown` 等待线程：它不写盘，而且会
    一直活到排空结束，留着它判「写的人散没散」就永远散不了。
    """
    return [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith(THREAD_NAME_PREFIX) and thread.name != SHUTDOWN_WAITER_NAME
    ]


class _ObservedDict(dict):
    """写入时通知宿主 `Task` 的 dict（§10.1 的 `task.progress`）。"""

    def __init__(self, payload: Dict[str, int], host: "Task") -> None:
        super().__init__(payload)
        self._host = host

    def __setitem__(self, key: str, value: int) -> None:  # type: ignore[override]
        super().__setitem__(key, value)
        self._host.notify()


class _ObservedList(list):
    """append 时通知宿主 `Task` 的 list（§7.10 的 `result["items"]`）。"""

    def __init__(self, values: List[Dict[str, Any]], host: "Task") -> None:
        super().__init__(values)
        self._host = host

    def append(self, value: Any) -> None:  # type: ignore[override]
        super().append(value)
        self._host.notify()


class Task:
    """一次后台任务的内存记录，字段严格同 §7.10。

    写的一方是工作线程（`fn` 改 `progress`/`result`，runner 改 `state`），读的一方是
    HTTP 线程（`to_payload`），所以两边都过同一把 RLock；`to_payload` 返回的是深一层的
    纯 dict/list 快照，序列化出去的就是 §7.10 的形状。
    """

    def __init__(self, profile: str, ttype: str, target: Optional[str], actor: str) -> None:
        self.id = uuid.uuid4().hex[:16]
        self.profile = profile
        self.type = ttype
        self.target: Optional[str] = target or None
        self.state = QUEUED
        self.error: Optional[str] = None
        self.actor = actor
        self.created_at = now_string()
        self.finished_at: Optional[str] = None
        self._lock = threading.RLock()
        self._observer: Optional[Callable[[Task], None]] = None
        self._signature = (0, 0, 0)
        self._progress = _ObservedDict({"done": 0, "total": 0}, self)
        self._result: Dict[str, Any] = {"items": _ObservedList([], self)}

    def __repr__(self) -> str:
        return "Task(%s, %s, %s, %s)" % (self.id, self.type, self.target or "-", self.state)

    @property
    def progress(self) -> Dict[str, int]:
        return self._progress

    @property
    def result(self) -> Dict[str, Any]:
        return self._result

    def observe(self, callback: Callable[[Task], None]) -> None:
        """注册「进度变化」回调（runner 用它递增 `tasks`）。"""
        with self._lock:
            self._observer = callback

    def notify(self) -> None:
        signature = (
            int(self._progress.get("done") or 0),
            int(self._progress.get("total") or 0),
            len(self._result["items"]),
        )
        with self._lock:
            if signature == self._signature:
                return
            self._signature = signature
            observer = self._observer
        if observer is not None:
            observer(self)

    def to_payload(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "profile": self.profile,
                "type": self.type,
                "target": self.target,
                "state": self.state,
                "progress": {
                    "done": int(self._progress.get("done") or 0),
                    "total": int(self._progress.get("total") or 0),
                },
                "result": {"items": [dict(item) for item in self._result["items"]]},
                "error": self.error,
                "actor": self.actor,
                "created_at": self.created_at,
                "finished_at": self.finished_at,
            }

    # 状态只由 runner 写（§10.1：`fn` 只写 progress/result），锁在这里收口。

    def mark_running(self) -> None:
        with self._lock:
            self.state = RUNNING

    def mark_finished(self, state: str, error: Optional[str] = None) -> None:
        with self._lock:
            self.state = state
            self.error = error
            self.finished_at = now_string()


class TaskRunner:
    """`ThreadPoolExecutor` + 内存任务表（§10.1）。

    任务记录只在内存里（§7.10「已结束任务在内存保留 1 小时 / 最近 200 条」），
    不新增持久化文件；清理是惰性的，在每次 `submit`/`list`/`get` 里顺手做，
    与 `services/temp_store.py` 同一写法（决策 4：M3 不引入定时器）。
    """

    def __init__(self, max_workers: int = DEFAULT_MAX_WORKERS) -> None:
        self._max_workers = max(1, int(max_workers))
        self._pool = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix=THREAD_NAME_PREFIX
        )
        self._lock = threading.Lock()
        self._tasks: "OrderedDict[str, Task]" = OrderedDict()
        self._active: Dict[Tuple[str, str, str], Task] = {}
        self._closed = False

    # ------------------------------------------------------------------
    # 提交与查询
    # ------------------------------------------------------------------

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def submit(
        self,
        ctx: RequestContext,
        ttype: str,
        target: Optional[str],
        fn: Callable[[Task], None],
    ) -> Task:
        """入队并立即返回；`fn` 在工作线程里跑（§10.1）。

        `ttype` 必须在 `TASK_TYPES` 里，否则 `INVALID_INPUT`——见 `_run` 上方与
        `_CONFLICTING_TYPES` 的说明（卡 6 复审 F4）。

        互斥规则：key = `(pid, ttype, target or '*')`，同 key 已有 queued/running →
        `AppError(BUSY, task_id=第一次的 id)`；同机型内 `update_all` 与**任意**
        `update_source` 互斥（同一机型同时只允许一个「更新类」任务改同一批文件），
        而只要有一方是 `refresh_records`，就按**机型**互斥、不看 target——判据全在
        `_CONFLICTING_TYPES` 那张表里（卡 6 复审 F2）。
        """
        if ttype not in TASK_TYPES:
            # 类型清单是 key 语义的一部分：`_CONFLICTING_TYPES` 与 `FINISH_PARTS` 都按它取值。
            # HTTP handler 那层（`api_tasks.py`）确实已经挡过一次，但那道门只有前端会走——
            # 卡 8 / 卡 9 直接调 `submit`，拼错的类型进队列后既不参与互斥、结束时的修订分量
            # 口径也不存在，只会留下一条永远撞不上的任务。
            raise AppError(
                INVALID_INPUT,
                "未知任务类型：%s（可选 %s）" % (ttype, "/".join(TASK_TYPES)),
                field="type",
                wanted=ttype,
            )
        pid = _profile_of(ctx)
        clean_target = target or None
        key = _key(pid, ttype, clean_target)
        with self._lock:
            if self._closed:
                raise AppError(MAINTENANCE, "服务器正在停止，不再接收新任务")
            self._purge_locked()
            conflict = self._conflict_locked(pid, ttype, key)
            if conflict is not None:
                raise AppError(
                    BUSY,
                    "同类任务正在执行（由 %s 发起）" % conflict.actor,
                    task_id=conflict.id,
                )
            task = Task(profile=pid, ttype=ttype, target=clean_target, actor=_actor_of(ctx))
            task.observe(self._on_progress)
            self._tasks[task.id] = task
            self._active[key] = task

        revision.bump(pid, TASK_PART)  # queued 入队也要让人看见
        try:
            future = self._pool.submit(self._run, task, fn)
        except RuntimeError as exc:  # 池刚好在并发的 shutdown 里关掉了
            with self._lock:
                self._active.pop(key, None)
            self._finish(task, FAILED, error="服务器正在停止，任务未启动")
            raise AppError(MAINTENANCE, "服务器正在停止，不再接收新任务") from exc
        # I3：`_run` 收口之后仍然可能有异常逸出（`_finish` 自己再崩一次、`finally` 里
        # 又抛、……），而原先这只 Future 没人 inspect ⇒ 异常直接消失。挂个 done-callback
        # 兜最后一道记录；`add_done_callback` 对已完成的 Future 会立刻回调，没有竞态。
        future.add_done_callback(_log_escaping_error)
        logger.info("任务入队: %s %s（id=%s, 机型=%s, actor=%s）", ttype, clean_target or "-", task.id, pid, task.actor)
        return task

    def get(self, task_id: str) -> Optional[Task]:
        with self._lock:
            self._purge_locked()
            return self._tasks.get(task_id)

    def list(self, pid: str, active_only: bool) -> List[Task]:
        """按机型列任务，入队顺序（§7.10 的 `{"items": [task]}`）。"""
        with self._lock:
            self._purge_locked()
            return [
                task
                for task in self._tasks.values()
                if task.profile == pid and (not active_only or task.state in ACTIVE_STATES)
            ]

    # ------------------------------------------------------------------
    # 停止（R9 / R10）
    # ------------------------------------------------------------------

    def shutdown(self, timeout: float = 30) -> None:
        """停止接收新任务，等待运行中任务至多 `timeout` 秒（§10.1）。

        **幂等**（R10）：信号可能连按两次，第二次直接返回，绝不重复 join、绝不抛。
        等待本身放在一个 daemon 线程里做，因为 `ThreadPoolExecutor.shutdown()` 没有
        超时参数；`cancel_futures` 也**没有**传——预算到期只是「这一层不等了」，队列里
        还没启动的条目照样会被启动，而工作线程会一直跑到自己结束为止。

        所以超时**不等于**这些写入消失了，只是把等待交到了退出前：调用方在删单实例锁
        之前必须再 `wait_for_workers()` 一次（C1）。这条 WARNING 是给运维看的，措辞要
        说清「等待挪到哪去了」，别让人以为进程马上就退了。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            in_flight = [task.id for task in self._active.values()]
        waiter = threading.Thread(
            target=self._pool.shutdown,
            kwargs={"wait": True},
            name=SHUTDOWN_WAITER_NAME,
            daemon=True,
        )
        waiter.start()
        waiter.join(max(0.0, float(timeout)))
        if waiter.is_alive():
            logger.warning(
                "后台任务在 %.1f 秒内没有结束：这一层停止等待，等待挪到退出前——工作线程还在写 "
                "data/，进程要等它们结束才会删单实例锁（见 run.stop_and_release），等不散就把锁"
                "留在原地。进行中：%s",
                timeout,
                ", ".join(in_flight),
            )
        else:
            logger.info("任务队列已停止（等待了 %d 个在途任务）", len(in_flight))

    def wait_for_workers(self, timeout: float = EXIT_WORKER_TIMEOUT) -> bool:
        """等到 `mctask` 工作线程归零；返回 True 表示「已经没有人在写 `data/` 了」。

        这是删单实例锁前那道闸（C1）：`shutdown()` 的预算管「HTTP 侧停得多快」，这份
        明显更大的预算管「写的人散没散」（推导见 `EXIT_WORKER_TIMEOUT` 的注释）。
        排空成功时工作线程已经被 `pool.shutdown(wait=True)` join 完，这里立刻返回 True，
        正常停止一点都不会被拖慢。

        返回 False 时调用方**不要**删锁：残留锁能被下一个实例按 pid 接管
        （`SingleInstanceLock.acquire` → `_process_alive`），删错的锁不能。

        总耗时以 `timeout` **封顶**（修复轮 3 / Item 1）：`remaining` 必须在每次
        `join` 前重算——旧实现每轮只算一次、再逐个交给每条卡死的线程去消费，一轮就是
        N × remaining（实测预算 1.0 秒 + 4 条卡死线程 ⇒ 4.03 秒），照 300 × 4 推下去
        停止路径能坐到约 20 分钟，与常量注释和 `docs/know-how.md` 承诺的 300 秒不符。
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            alive = worker_threads()
            if not alive:
                return True
            for thread in alive:  # join 而不是轮询：线程一散就立刻返回
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                thread.join(remaining)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _conflict_locked(
        self, pid: str, ttype: str, key: Tuple[str, str, str]
    ) -> Optional[Task]:
        same = self._active.get(key)
        if same is not None:
            return same  # 同 (机型, 类型, target) 的快路径；`update_all` 之间也是靠它挡住
        blocked = _CONFLICTING_TYPES.get(ttype, ())
        for (other_pid, other_type, _), task in self._active.items():
            if other_pid == pid and other_type in blocked:
                return task
        return None

    def _run(self, task: Task, fn: Callable[[Task], None]) -> None:
        # 「收口」要覆盖 `_run` 的**每一**行。原先 `mark_running()` 与第一次 `revision.bump()`
        # 落在 `try` 之外、成功路径的 `_finish()` 落在 `try` 之后：那三处任意一处抛异常，异常
        # 都只进那只没人 inspect 的 `Future`，而 `_active[key]` 永远被占住——这个
        # `(机型, 类型, target)` 从此只回 409 BUSY，任务还停在「进行中」，惰性清理又永远
        # 跳过 active 任务，于是连内存里那条记录也不会消失（卡 6 复审 F4）。
        # 状态与键在这里兜住之后，**异常本身**仍然可能继续往外跑（`_finish` 自己在状态
        # 落终态之后崩一次，`finally` 就管不着了）——那一份由 `submit` 挂的 done-callback
        # 记录，见 `_log_escaping_error`（I3）。
        try:
            task.mark_running()
            revision.bump(task.profile, TASK_PART)
            try:
                fn(task)
            except AppError as exc:
                logger.exception(
                    "后台任务失败（%s）：%s %s（id=%s）", exc.code, task.type, task.target or "-", task.id
                )
                self._finish(task, FAILED, error=exc.message)
                return
            except Exception as exc:
                # 原始异常文本可能含路径、URL、令牌——那些只进日志，不进 `task.error`（§11.5）。
                logger.exception(
                    "后台任务未预期异常：%s %s（id=%s）", task.type, task.target or "-", task.id
                )
                self._finish(task, FAILED, error="任务执行失败（%s）" % type(exc).__name__)
                return
            self._finish(task, _state_from_items(list(task.result["items"])))
        finally:
            if task.state in ACTIVE_STATES:
                # 只有「`_finish` 一次都没跑到」才会落到这里。`_finish` 做的第一件事就是归还
                # 互斥键，所以键与状态在这里一起收口；`error` 不为 None ⇒ 一个业务分量都不补
                # （§10.1「失败写不递增」），但 `tasks` 照补，别人的界面才不会永远「进行中」。
                logger.exception(
                    "后台任务在任务体之外崩了：%s %s（id=%s）", task.type, task.target or "-", task.id
                )
                self._finish(task, FAILED, error="任务执行失败（队列内部错误）")

    def _finish(self, task: Task, state: str, error: Optional[str] = None) -> None:
        """收口状态 + `finished_at`，递增 `tasks`，并按类型补 `files`/`settings`。

        `error` 不为 None 就是「抛异常失败」，那种情况一个业务分量都不递增（失败写
        不递增），但 `tasks` 必须递增，否则别人的界面永远停在「进行中」。
        """
        with self._lock:
            self._active.pop(_key(task.profile, task.type, task.target), None)
        task.mark_finished(state, error=error)
        revision.bump(task.profile, TASK_PART)
        if error is None:
            for part in FINISH_PARTS.get(task.type, ()):
                revision.bump(task.profile, part)
        logger.info("任务结束: %s %s -> %s（id=%s）", task.type, task.target or "-", state, task.id)

    def _on_progress(self, task: Task) -> None:
        revision.bump(task.profile, TASK_PART)

    def _purge_locked(self, moment: Optional[str] = None) -> int:
        """惰性清理已结束任务（§7.10 的 1 小时 / 最近 200 条）。"""
        now_text = moment or now_string()
        dropped = 0
        for task_id in list(self._tasks):
            task = self._tasks[task_id]
            if task.state in ACTIVE_STATES:
                continue  # 进行中的任务永远不清
            if _expired(task, now_text):
                del self._tasks[task_id]
                dropped += 1
        finished = [tid for tid, task in self._tasks.items() if task.state not in ACTIVE_STATES]
        overflow = len(finished) - max(0, int(RETAIN_MAX))
        for task_id in finished[: max(0, overflow)]:  # 只从**最旧的**已结束任务开始丢
            del self._tasks[task_id]
            dropped += 1
        if dropped:
            logger.debug("清理已结束任务 %d 条（保留 %d 小时 / %d 条）", dropped, RETAIN_SECONDS // 3600, RETAIN_MAX)
        return dropped


def _log_escaping_error(future: Future) -> None:
    """最后一道记录：`_run` 没接住的异常原先会**无声消失**（I3）。

    `submit` 拿到的是 `ThreadPoolExecutor` 的 Future，从来没人 `inspect()`/`result()`，
    所以任何逃出 `_run` 的异常——包括 `_finish` 自己在状态落终态之后崩一次这种
    `finally` 也管不着的路径——连一行日志都不留。回调只做一件事：把它记下来。
    回调本身不许再抛（它跑在工作线程里，`_invoke_callbacks` 虽然会兜住，但那只会
    多一条没人认领的堆栈）。
    """
    try:
        error = future.exception()
    except BaseException:  # pragma: no cover - 已取消 / 池被打破等极端形态
        return
    if error is None:
        return
    logger.error(
        "异常逸出了 `_run`：队列的收口没能接住它（I3 的最后一道记录）。异常：%r", error, exc_info=error
    )


def _expired(task: Task, now_text: str) -> bool:
    try:
        done = datetime.strptime(task.finished_at or task.created_at, TIME_FORMAT)
        now = datetime.strptime(now_text, TIME_FORMAT)
    except ValueError:
        return True  # 时间戳读不懂就丢掉：宁可少留一条记录，也不要撑爆内存
    return (now - done) >= timedelta(seconds=RETAIN_SECONDS)


def _profile_of(ctx: RequestContext) -> str:
    pid = str(getattr(ctx, "profile_id", "") or "").strip()
    if not pid:
        # 机型是任务归属与互斥的轴，拿不到就不该默默落到 default（§3.4 同口径）。
        raise AppError(INVALID_INPUT, "任务必须指定机型")
    return pid


def _actor_of(ctx: RequestContext) -> str:
    """任务归属：请求来的就是身份名，后台（卡 8 的调度线程）就是 `system`。"""
    actor = getattr(ctx, "actor", None)
    if actor is None:
        return SYSTEM_ACTOR
    return str(getattr(actor, "name", "") or getattr(actor, "ip", "") or SYSTEM_ACTOR)


# ---------------------------------------------------------------------------
# 进程内单例：`run.py` 注入，卡 7 / 卡 8 / 卡 9 与 HTTP handler 都用 `get_runner()`
# ---------------------------------------------------------------------------

#: ttype -> 任务函数（在工作线程里执行）。
_HANDLERS: Dict[str, Callable[[Task], None]] = {}
_runner: Optional[TaskRunner] = None
_runner_lock = threading.Lock()


def register_handler(ttype: str, fn: Callable[..., None]) -> None:
    """登记任务类型的执行体（卡 9 用它换掉 `refresh_records` 的骨架实现）。"""
    if ttype not in TASK_TYPES:
        raise ValueError("未知任务类型：%s（可选 %s）" % (ttype, "/".join(TASK_TYPES)))
    _HANDLERS[ttype] = fn


def handler_for(ttype: str) -> Optional[Callable[[Task], None]]:
    return _HANDLERS.get(ttype)


def init_runner(max_workers: int = DEFAULT_MAX_WORKERS) -> TaskRunner:
    """启动期注入单例（`run.py` 用 `settings.max_workers` 调它）。"""
    global _runner
    with _runner_lock:
        previous = _runner
        _runner = TaskRunner(max_workers=max_workers)
    if previous is not None:
        previous.shutdown(DEFAULT_SHUTDOWN_TIMEOUT)
    return _runner


def get_runner() -> TaskRunner:
    """单例；还没注入时按默认 4 个 worker 懒建（未走 `run.py` 的入口与测试）。"""
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = TaskRunner(max_workers=DEFAULT_MAX_WORKERS)
        return _runner


def reset_runner(timeout: float = 5.0) -> None:
    """关掉当前单例并清空（`tests/conftest.py` 的 autouse 夹具用）。

    工作线程不 reset 就会带着上一条用例的任务泄漏进下一条；这里给一个有上限的等待，
    避免测试里忘了放行的假任务把整轮 pytest 卡死。

    调用方传的预算必须**长过**测试里闸门自己的兜底（`conftest.RUNNER_DRAIN_TIMEOUT` >
    `conftest.GATE_TIMEOUT`），否则那条线程会活到 monkeypatch 撤销之后、往仓库真实的
    `data/` 里写（I2 / H3）。
    """
    global _runner
    with _runner_lock:
        previous, _runner = _runner, None
    if previous is not None:
        previous.shutdown(timeout)


# ---------------------------------------------------------------------------
# 本卡认领的三个任务类型：都只是「调 `services/updates.py`」，落盘与锁都在里面
# ---------------------------------------------------------------------------


def _publish_single(task: Task, result: Dict[str, Any]) -> None:
    """单项任务把结果与进度回填给 §7.10 的形状。"""
    task.result["items"].append(result)
    task.progress["total"] = 1
    task.progress["done"] = 1


def _run_update_source(task: Task) -> None:
    # §10.2 七步。机型锁由 `updates` 自己在写的那一步加，网络 I/O 在锁外。
    _publish_single(task, updates.update_source(task.profile, task.target or "", task.actor))


def _run_update_all(task: Task) -> None:
    # 传 `task` 进去：`updates` 会逐项回填 `progress` 与 `result["items"]`。
    # 它自己不递增 `settings`（卡 5 裁定），那份由 `_finish` 按 FINISH_PARTS 补。
    updates.update_all(task.profile, task.actor, task)


def _run_refresh_records(task: Task) -> None:
    # 主体属卡 9（INH-1894）：届时只换 `updates.refresh_records` 的实现，签名不变。
    _publish_single(task, updates.refresh_records(task.profile, task.target or "", task.actor))


register_handler("update_source", _run_update_source)
register_handler("update_all", _run_update_all)
register_handler("refresh_records", _run_refresh_records)
