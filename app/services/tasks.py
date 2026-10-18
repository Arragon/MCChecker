"""后台任务队列（规范 §10.1、§7.10；卡 6 / M3-07 / INH-1892）。

分层（§3.3）：本模块是 service 层，只用标准库 + `core.errors` + `services.revision`
+ `services.updates`。它**不 import HTTP、也不碰 `storage`**——下载、校验、落盘全部
在 `services/updates.py` 里；队列只负责「排队、跑、记账、清理」。卡 7（更新源设置页）、
卡 8（调度器）、卡 9（修改记录）都往这里提交任务。

三条不显然的约束：

1. **工作线程不是 daemon**（R9）。`ThreadPoolExecutor` 默认如此，因此「进程能退出」
   等价于「在途任务已结束」。停止序列必须是先 `shutdown()` 等任务、再关 HTTP，
   见 `run.ordered_stop`；顺序反了 `serve_forever` 会先退出、主线程走完而任务还在跑。
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
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
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
#: 工作线程名前缀：测试用它筛「有没有残留线程」。
THREAD_NAME_PREFIX = "mctask"

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

        互斥规则：key = `(pid, ttype, target or '*')`，同 key 已有 queued/running →
        `AppError(BUSY, task_id=第一次的 id)`；`update_all` 与同机型**任意**
        `update_source` 互斥（同一机型同时只允许一个「更新类」任务改同一批文件）。
        """
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
            self._pool.submit(self._run, task, fn)
        except RuntimeError as exc:  # 池刚好在并发的 shutdown 里关掉了
            with self._lock:
                self._active.pop(key, None)
            self._finish(task, FAILED, error="服务器正在停止，任务未启动")
            raise AppError(MAINTENANCE, "服务器正在停止，不再接收新任务") from exc
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
        超时参数；超时后不再等，但工作线程是非 daemon 的，进程退出前仍会把它们跑完。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            in_flight = [task.id for task in self._active.values()]
        waiter = threading.Thread(
            target=self._pool.shutdown,
            kwargs={"wait": True},
            name="%s-shutdown" % THREAD_NAME_PREFIX,
            daemon=True,
        )
        waiter.start()
        waiter.join(max(0.0, float(timeout)))
        if waiter.is_alive():
            logger.warning(
                "后台任务在 %.1f 秒内没有结束，停止时不再等待（进行中：%s）", timeout, ", ".join(in_flight)
            )
        else:
            logger.info("任务队列已停止（等待了 %d 个在途任务）", len(in_flight))

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _conflict_locked(
        self, pid: str, ttype: str, key: Tuple[str, str, str]
    ) -> Optional[Task]:
        same = self._active.get(key)
        if same is not None:
            return same
        if ttype == "update_all":
            blocked = ("update_source",)  # 同 key 的 update_all 已经被上面挡住了
        elif ttype == "update_source":
            blocked = ("update_all",)
        else:  # refresh_records 不在 §10.1 的互斥面里（动的是 records/）
            return None
        for (other_pid, other_type, _), task in self._active.items():
            if other_pid == pid and other_type in blocked:
                return task
        return None

    def _run(self, task: Task, fn: Callable[[Task], None]) -> None:
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

    工作线程是非 daemon 的，不 reset 就会带着上一条用例的任务泄漏进下一条；这里给一个
    有上限的等待，避免测试里忘了放行的假任务把整轮 pytest 卡死。
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
