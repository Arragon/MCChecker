"""后台任务队列与任务接口（卡 6 · M3-07 / INH-1892；规范 §10.1、§7.10、D4）。

四层用例分别证明四件事：

1. `services/tasks.py` 的 TaskRunner —— **直接调用**（阻塞的假任务只存在于本文件）：
   状态机、进度递增、BUSY 互斥、内存保留、停止语义（等待在途任务 / 幂等 / 线程形态）；
2. `/tasks` 三条接口走真实 `MCServer` + 四角色夹具（202 / 403 / 400 / 404 / 409），
   并证明「下载与机型锁都在工作线程里，HTTP 线程不持锁等下载」；
3. `run.py` 的**有序停止**（R9：先 `runner.shutdown` 再 `server.shutdown`）——
   `test_ordered_stop_waits_for_in_flight_task_before_stopping_http` 是变异验证的靶子：
   把那两行顺序反过来它一定转红；
4. D4 前端门控与 mock 契约（这几处是「不发请求」的静态条件，用源码文本断言）。

下载一律打本地 `http.server`（127.0.0.1 随机端口），`getproxies` 打桩成 `{}`
（macOS 系统代理会把请求接走），重试退避打桩成记录器——与 `tests/test_api_sources.py`
同一套做法（见 `docs/know-how.md` 第 4 节）。
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

import pytest

from authz_fixtures import as_role, make_server, seed_roles

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NAME = "config.xml"
XML_V1 = b"<config><a>1</a></config>"
XML_V2 = b"<config><a>2</a></config>"

#: §7.10 规定的 `task` 字段，一个不多一个不少。
TASK_FIELDS = {
    "id",
    "profile",
    "type",
    "target",
    "state",
    "progress",
    "result",
    "error",
    "actor",
    "created_at",
    "finished_at",
}
ACTIVE_STATES = ("queued", "running")
FINISHED_STATES = ("succeeded", "partial", "failed")
#: runner 的工作线程名前缀（断言「停止后不残留非 daemon 线程」用它筛）。
THREAD_PREFIX = "mctask"


# ---------------------------------------------------------------------------
# 本地下载服务器（与卡 5 同一形态，多了「卡住不回」的路由）
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - http.server 的命名约定
        try:
            self._handle()
        except (BrokenPipeError, ConnectionResetError):  # pragma: no cover - 客户端先断
            pass

    def _handle(self) -> None:
        self.server.requests.append(self.path)
        route = self.server.routes.get(self.path.split("?", 1)[0])
        if route is None:
            self._reply(404, {}, b"not found")
            return
        gate = route.get("wait")
        if gate is not None:
            gate.wait(20)  # 「handler 不得同步下载」那条用：挂着直到测试放行（超时兜底防卡死）
        self._reply(route.get("status", 200), route.get("headers", {}), route.get("body", b""))

    def _reply(self, status: int, headers: dict, body: bytes) -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, *args):  # 访问日志会淹掉 pytest 输出
        pass


class _Server:
    def __init__(self, httpd, port: int) -> None:
        self.httpd = httpd
        self.port = port

    @property
    def requests(self) -> List[str]:
        return self.httpd.requests

    def url(self, path: str = "/new.xml") -> str:
        return "http://127.0.0.1:%d%s" % (self.port, path)


@pytest.fixture()
def servers(monkeypatch):
    """按需起本地服务器，用例结束统一关掉。"""
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    started: List[Any] = []

    def make(routes: Dict[str, Any]) -> _Server:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        httpd.daemon_threads = True
        httpd.routes = routes
        httpd.requests = []
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return _Server(httpd, httpd.server_address[1])

    yield make
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture()
def sleeps(monkeypatch) -> List[float]:
    """重试退避记录器：不真睡（否则 404 那一条要白等 1 s + 2 s）。"""
    from app.core import downloader

    recorded: List[float] = []
    monkeypatch.setattr(downloader, "_sleep", recorded.append)
    return recorded


@pytest.fixture()
def pid() -> str:
    from app.core import device_models

    return device_models.add_model("tasks")


# ---------------------------------------------------------------------------
# 助手
# ---------------------------------------------------------------------------


def _runner():
    from app.services import tasks

    return tasks.get_runner()


def _ctx(target_pid: str, name: str = "部署者甲"):
    """直接驱动 runner 时的最小请求上下文（API 用例走真实身份解析，不用它）。"""
    from app.services.auth import Actor
    from app.services.context import RequestContext

    actor = Actor(ip="127.0.0.1", name=name, registered=True, is_deployer=True, is_admin=False)
    return RequestContext(ip="127.0.0.1", cid=None, profile_id=target_pid, actor=actor)


def _ok_item(name: str = NAME) -> Dict[str, Any]:
    return {
        "name": name,
        "status": "success",
        "label": "已更新",
        "reason": "测试",
        "at": "2026-10-18 00:00:00",
    }


def _noop(task) -> None:
    """最简假任务：写一条成功结果并收口进度。"""
    task.result["items"].append(_ok_item())
    task.progress["total"] = 1
    task.progress["done"] = 1


def _blocking(release: threading.Event, entered: Optional[threading.Event] = None):
    """阻塞的假任务（只存在于测试）：卡面要求用它验 BUSY 与停止等待。"""

    def fn(task):
        if entered is not None:
            entered.set()
        release.wait(20)
        task.result["items"].append(_ok_item())

    return fn


def tasks_handler(ttype: str):
    """取本卡注册的默认 handler（卡 8 / 卡 9 也用同一入口）。"""
    from app.services import tasks

    handler = tasks.handler_for(ttype)
    assert handler is not None, ttype
    return handler


def _wait_task(task, states: tuple = FINISHED_STATES, timeout: float = 10.0) -> Dict[str, Any]:
    """按 **Task 对象**等待（清理会立刻把已结束任务从表里摘掉，只能拿引用等）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if task.state in states:
            return task.to_payload()
        time.sleep(0.01)
    raise AssertionError(
        "任务 %s 未在 %.1f 秒内进入 %s（当前 %s）" % (task.id, timeout, states, task.state)
    )


def _wait_state(
    target_pid: str, task_id: str, states: tuple = FINISHED_STATES, timeout: float = 10.0
) -> Dict[str, Any]:
    """轮询到任务进入给定状态并返回 JSON 快照（工作线程异步，必须等）。"""
    runner = _runner()
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = runner.get(task_id)
        if task is not None and task.state in states:
            return task.to_payload()
        time.sleep(0.01)
    current = runner.get(task_id)
    raise AssertionError(
        "任务 %s 未在 %.1f 秒内进入 %s（当前 %s）" % (task_id, timeout, states, current and current.state)
    )


def _seed_mapping(target_pid: str, entries: List[Dict[str, str]]) -> None:
    from app.core import storage

    with storage.use_profile(target_pid):
        storage.save_config_mapping(entries)


def _parts(target_pid: str) -> Dict[str, int]:
    from app.services import revision

    return revision.snapshot(target_pid)["parts"]


def _worker_threads() -> List[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith(THREAD_PREFIX)]


def _js(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), "r", encoding="utf-8") as fh:
        return fh.read()


def _block(source: str, marker: str, length: int = 1800) -> str:
    start = source.index(marker)
    return source[start : start + length]


# ---------------------------------------------------------------------------
# 1. TaskRunner：结构与状态机
# ---------------------------------------------------------------------------


class TestTaskContract:
    def test_task_fields_are_exactly_the_section_7_10_shape(self, pid):
        release = threading.Event()
        entered = threading.Event()
        task = _runner().submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
        entered.wait(5)
        payload = task.to_payload()

        assert set(payload) == TASK_FIELDS
        assert set(payload["progress"]) == {"done", "total"}
        assert set(payload["result"]) == {"items"}
        assert payload["profile"] == pid
        assert payload["type"] == "update_source"
        assert payload["target"] == NAME
        assert payload["actor"] == "部署者甲"
        assert payload["error"] is None
        assert payload["finished_at"] is None  # 还在跑，没有结束时间
        assert payload["state"] in ACTIVE_STATES
        assert payload["created_at"]
        # 必须能直接序列化给 HTTP（§7.10 的响应体就是这个形状）
        assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload

        release.set()
        done = _wait_task(task)
        assert done["finished_at"] is not None

    def test_successful_task_ends_succeeded_with_finished_at(self, pid):
        task = _wait_state(pid, _runner().submit(_ctx(pid), "update_source", NAME, _noop).id)

        assert task["state"] == "succeeded"
        assert task["error"] is None
        assert task["finished_at"] is not None
        assert task["progress"] == {"done": 1, "total": 1}
        assert task["result"]["items"][0]["status"] == "success"

    def test_partial_when_only_some_items_failed(self, pid):
        def fn(task):
            task.result["items"].append(_ok_item())
            task.result["items"].append(
                {"name": "b.xml", "status": "download_failed", "label": "下载失败", "reason": "连接失败", "at": "x"}
            )
            task.progress["total"] = 2
            task.progress["done"] = 2

        task = _wait_state(pid, _runner().submit(_ctx(pid), "update_all", None, fn).id)

        assert task["state"] == "partial"
        assert task["target"] is None
        assert task["error"] is None

    def test_failed_when_every_item_failed(self, pid):
        def fn(task):
            task.result["items"].append(
                {"name": "a.xml", "status": "rejected", "label": "已拒绝", "reason": "根节点变了", "at": "x"}
            )

        task = _wait_state(pid, _runner().submit(_ctx(pid), "update_all", None, fn).id)
        assert task["state"] == "failed"

    def test_skipped_and_not_found_items_are_not_failures(self, pid):
        """与 `updates._last_run` 同一口径：skipped / not_found 算「没做」而不是「失败」。"""

        def fn(task):
            task.result["items"].append({"name": "a.xml", "status": "skipped", "label": "已跳过", "reason": "无下载链接", "at": "x"})
            task.result["items"].append({"name": "b.xml", "status": "not_found", "label": "未找到", "reason": "映射里没有", "at": "x"})

        task = _wait_state(pid, _runner().submit(_ctx(pid), "update_all", None, fn).id)
        assert task["state"] == "succeeded"

    def test_no_items_is_succeeded_not_failed(self, pid):
        """空映射的全量更新：一个都没做，不能算失败（与 mock 的 `failed === 0` 同口径）。"""

        def fn(task):
            task.progress["total"] = 0

        task = _wait_state(pid, _runner().submit(_ctx(pid), "update_all", None, fn).id)
        assert task["state"] == "succeeded"

    def test_exception_marks_failed_with_readable_error_and_logs_stack(self, pid, caplog):
        def boom(task):
            raise RuntimeError("含底层细节的原始异常")

        with caplog.at_level(logging.ERROR, logger="app.services.tasks"):
            task = _wait_state(pid, _runner().submit(_ctx(pid), "update_source", NAME, boom).id)

        assert task["state"] == "failed"
        assert task["finished_at"] is not None
        assert "RuntimeError" in task["error"]
        assert "含底层细节的原始异常" not in task["error"]  # 原始异常文本只进日志
        assert "Traceback" in caplog.text  # §10.1：日志留堆栈
        assert "含底层细节的原始异常" in caplog.text

    def test_app_error_message_is_used_verbatim_as_task_error(self, pid):
        from app.core.errors import TOO_LARGE, AppError

        def boom(task):
            raise AppError(TOO_LARGE, "文件超过上限，已放弃")

        task = _wait_state(pid, _runner().submit(_ctx(pid), "update_source", NAME, boom).id)

        assert task["state"] == "failed"
        assert task["error"] == "文件超过上限，已放弃"

    def test_list_filters_by_profile_and_active_only(self, pid):
        from app.core import device_models

        other = device_models.add_model("tasks-other")
        release = threading.Event()
        entered = threading.Event()
        running = _runner().submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
        done = _runner().submit(_ctx(pid), "update_source", "done.xml", _noop)
        other_task = _runner().submit(_ctx(other), "update_all", None, _noop)
        _wait_state(pid, done.id)
        _wait_state(other, other_task.id)
        entered.wait(5)

        assert [t.id for t in _runner().list(pid, True)] == [running.id]
        assert sorted(t.id for t in _runner().list(pid, False)) == sorted([running.id, done.id])
        assert [t.id for t in _runner().list(other, False)] == [other_task.id]
        assert _runner().get("no-such-task") is None

        release.set()
        _wait_state(pid, running.id)

    def test_worker_threads_are_not_daemon(self, pid):
        """R9 的形态前提：工作线程非 daemon，进程退出前一定会把在途任务跑完。"""
        release = threading.Event()
        entered = threading.Event()
        task = _runner().submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
        entered.wait(5)

        workers = _worker_threads()
        assert workers and all(not t.daemon for t in workers)

        release.set()
        _wait_state(pid, task.id)


# ---------------------------------------------------------------------------
# 2. BUSY 与互斥（§10.1：同 key 进行中；update_all 与同机型任意 update_source 互斥）
# ---------------------------------------------------------------------------


class TestBusyAndMutex:
    def _busy(self, target_pid: str, ttype: str, target: Optional[str]):
        from app.core.errors import AppError

        with pytest.raises(AppError) as info:
            _runner().submit(_ctx(target_pid), ttype, target, _noop)
        return info.value

    def test_same_key_second_submit_is_busy_and_points_to_first(self, pid):
        from app.core.errors import BUSY

        release = threading.Event()
        entered = threading.Event()
        first = _runner().submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
        entered.wait(5)

        error = self._busy(pid, "update_source", NAME)
        assert error.code == BUSY
        assert error.http_status == 409
        assert error.detail["task_id"] == first.id
        assert "部署者甲" in error.message  # 告诉用户是谁占着

        release.set()
        _wait_state(pid, first.id)

    def test_update_all_and_update_source_are_mutually_exclusive(self, pid):
        release = threading.Event()
        entered = threading.Event()
        source = _runner().submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
        entered.wait(5)
        assert self._busy(pid, "update_all", None).detail["task_id"] == source.id
        release.set()
        _wait_state(pid, source.id)

        release2 = threading.Event()
        entered2 = threading.Event()
        bulk = _runner().submit(_ctx(pid), "update_all", None, _blocking(release2, entered2))
        entered2.wait(5)
        # 反方向同样互斥，且报的是那个正在跑的全量任务
        assert self._busy(pid, "update_source", NAME).detail["task_id"] == bulk.id
        release2.set()
        _wait_state(pid, bulk.id)

    def test_update_all_blocks_every_source_of_the_same_profile(self, pid):
        """互斥轴是「机型」，不是「文件名」：同一机型同时只允许一个更新类任务改同一批文件。"""
        release = threading.Event()
        entered = threading.Event()
        first = _runner().submit(_ctx(pid), "update_all", None, _blocking(release, entered))
        entered.wait(5)

        assert self._busy(pid, "update_source", "别的.xml").detail["task_id"] == first.id

        release.set()
        _wait_state(pid, first.id)

    def test_other_target_profile_or_type_is_allowed(self, pid):
        from app.core import device_models

        other = device_models.add_model("tasks-other")
        release = threading.Event()
        entered = threading.Event()
        first = _runner().submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
        entered.wait(5)

        second = _runner().submit(_ctx(pid), "update_source", "other.xml", _noop)
        third = _runner().submit(_ctx(other), "update_source", NAME, _noop)
        fourth = _runner().submit(_ctx(pid), "refresh_records", NAME, _noop)
        assert len({first.id, second.id, third.id, fourth.id}) == 4

        release.set()
        for task in (first, second, third, fourth):
            _wait_state(task.profile, task.id)

    def test_refresh_records_does_not_block_update_source(self, pid):
        """§10.1 只规定 `update_all` 与 `update_source` 互斥；拉记录动的是 records/，不在互斥面里。"""
        release = threading.Event()
        entered = threading.Event()
        records = _runner().submit(_ctx(pid), "refresh_records", NAME, _blocking(release, entered))
        entered.wait(5)

        source = _runner().submit(_ctx(pid), "update_source", NAME, _noop)
        assert source.id != records.id

        release.set()
        _wait_state(pid, records.id)
        _wait_state(pid, source.id)

    def test_finished_task_frees_the_key(self, pid):
        first = _runner().submit(_ctx(pid), "update_source", NAME, _noop)
        _wait_state(pid, first.id)

        second = _runner().submit(_ctx(pid), "update_source", NAME, _noop)
        assert second.id != first.id
        _wait_state(pid, second.id)


# ---------------------------------------------------------------------------
# 3. 内存保留（§7.10：1 小时 / 最近 200 条，惰性清理）
# ---------------------------------------------------------------------------


class TestRetention:
    def test_retention_constants_match_spec(self):
        from app.services import tasks

        assert tasks.RETAIN_SECONDS == 3600
        assert tasks.RETAIN_MAX == 200

    def _finished(self, target_pid: str, count: int) -> List[str]:
        ids = []
        for index in range(count):
            task = _runner().submit(_ctx(target_pid), "update_source", "f%d.xml" % index, _noop)
            _wait_task(task)  # 用引用等：RETAIN_MAX 被改小时，`get()` 已经查不到它了
            ids.append(task.id)
        return ids

    def test_finished_tasks_beyond_retain_max_are_dropped_lazily(self, pid, monkeypatch):
        from app.services import tasks

        monkeypatch.setattr(tasks, "RETAIN_MAX", 2)
        ids = self._finished(pid, 4)
        alive = [t.id for t in _runner().list(pid, False)]

        assert ids[0] not in alive and ids[1] not in alive  # 最久的两条被清掉
        assert set(ids[2:]) <= set(alive)
        assert len(alive) <= 2

    def test_finished_tasks_older_than_retain_seconds_are_dropped(self, pid, monkeypatch):
        from app.services import tasks

        ids = self._finished(pid, 2)
        from datetime import datetime, timedelta

        # 只把「现在」拨到保留期之外，不清空表：证明过期判定用的是 finished_at + RETAIN_SECONDS
        clock = {"shift": 0}
        real_now = tasks.now_string

        def shifted():
            moment = datetime.now() + timedelta(seconds=clock["shift"])
            return moment.strftime("%Y-%m-%d %H:%M:%S")

        monkeypatch.setattr(tasks, "now_string", shifted)
        clock["shift"] = tasks.RETAIN_SECONDS + 5
        assert [t.id for t in _runner().list(pid, False)] == []
        assert ids[0] not in [t.id for t in _runner().list(pid, False)]

    def test_active_tasks_are_never_purged(self, pid, monkeypatch):
        from app.services import tasks

        monkeypatch.setattr(tasks, "RETAIN_MAX", 0)
        release = threading.Event()
        entered = threading.Event()
        running = _runner().submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
        entered.wait(5)
        finished_ids = self._finished(pid, 3)

        assert [t.id for t in _runner().list(pid, True)] == [running.id]
        assert [t.id for t in _runner().list(pid, False)] == [running.id]

        release.set()
        assert _wait_task(running)["state"] == "succeeded"  # 用引用等：RETAIN_MAX=0 会当场把它清掉
        assert running.id not in [t.id for t in _runner().list(pid, True)]
        assert finished_ids

    def test_get_drops_out_the_expired_task(self, pid, monkeypatch):
        """清理是惰性的：`get` 也要有机会把它摘掉，而不是只等下一次 submit。"""
        from app.services import tasks

        ids = self._finished(pid, 1)
        from datetime import datetime, timedelta

        monkeypatch.setattr(
            tasks,
            "now_string",
            lambda: (datetime.now() + timedelta(seconds=3601)).strftime("%Y-%m-%d %H:%M:%S"),
        )
        assert _runner().get(ids[0]) is None


# ---------------------------------------------------------------------------
# 4. 停止语义（R9 / R10）
# ---------------------------------------------------------------------------


class TestShutdown:
    def test_shutdown_waits_for_the_running_task(self, pid):
        release = threading.Event()
        entered = threading.Event()
        task = _runner().submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
        entered.wait(5)
        threading.Timer(0.2, release.set).start()

        _runner().shutdown(10)

        assert task.state == "succeeded"
        assert task.finished_at is not None

    def test_shutdown_is_idempotent_and_never_rejoins(self, pid):
        """R10：信号可能连按两次，第二次不得抛、不得重复 join。"""
        release = threading.Event()
        entered = threading.Event()
        task = _runner().submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
        entered.wait(5)

        _runner().shutdown(0.01)  # 超时：不再等待，但不抛
        started = time.time()
        _runner().shutdown(30)  # 第二次必须立即返回（不能被进行中任务卡住）
        assert time.time() - started < 0.5

        release.set()
        _wait_state(pid, task.id)
        _runner().shutdown(10)  # 第三次同样安全（幂等，不重复 join）
        # 第一次调用只等了 0.01 秒就返回，join 由那个 daemon 等待线程在后台完成，
        # 所以这里给它一个上限轮询——最终一定没有残留工作线程。
        deadline = time.time() + 5
        while _worker_threads() and time.time() < deadline:
            time.sleep(0.02)
        assert _worker_threads() == []

    def test_shutdown_leaves_no_worker_threads(self, pid):
        ids = [_runner().submit(_ctx(pid), "update_source", "s%d.xml" % i, _noop).id for i in range(6)]
        for task_id in ids:
            _wait_state(pid, task_id)
        assert _worker_threads()

        _runner().shutdown(10)

        assert _worker_threads() == []

    def test_submit_after_shutdown_is_refused(self, pid):
        from app.core.errors import MAINTENANCE

        _runner().shutdown(5)

        with pytest.raises(Exception) as info:
            _runner().submit(_ctx(pid), "update_source", NAME, _noop)
        assert info.value.code == MAINTENANCE
        assert info.value.http_status == 503

    def test_get_runner_is_a_process_singleton_and_init_replaces_it(self):
        from app.services import tasks

        first = tasks.get_runner()
        assert tasks.get_runner() is first
        assert first.max_workers == tasks.DEFAULT_MAX_WORKERS == 4

        second = tasks.init_runner(2)
        assert second is not first
        assert second.max_workers == 2
        assert tasks.get_runner() is second

    def test_public_helpers_are_importable_by_cards_8_and_9(self):
        """卡面契约：类型清单、注册入口、单例入口都在这儿，卡 8 / 卡 9 照此调用。"""
        from app.services import tasks

        assert tasks.TASK_TYPES == ("update_source", "update_all", "refresh_records")
        assert callable(tasks.register_handler)
        assert callable(tasks.get_runner)
        assert tasks.handler_for("update_all") is not None
        assert tasks.handler_for("refresh_records") is not None
        assert tasks.handler_for("不存在的类型") is None


# ---------------------------------------------------------------------------
# 5. 修订号（§10.1：进度变化 bump tasks；结束时按类型 bump files/settings）
# ---------------------------------------------------------------------------


class TestRevisionBumps:
    def test_submit_bumps_tasks_part(self, pid):
        before = _parts(pid)["tasks"]
        task = _runner().submit(_ctx(pid), "update_source", NAME, _noop)

        # queued 入队就通知。工作线程可能已经抢先把状态推到 running（那是一次额外的
        # 递增），所以这里断「增加了」而不是「恰好 +1」。
        assert _parts(pid)["tasks"] > before
        _wait_state(pid, task.id)

    def test_progress_change_bumps_tasks_while_running(self, pid):
        """§10.1「每次进度变化递增 tasks」——必须在任务还在跑的时候就能看见。

        两个读数都取在**工作线程内**（同一个线程里读写才有确定的先后关系）。"""
        hold = threading.Event()
        seen = threading.Event()
        box: Dict[str, int] = {}

        def fn(task):
            box["before"] = _parts(pid)["tasks"]
            task.progress["total"] = 3
            task.progress["done"] = 1
            task.result["items"].append(_ok_item())
            box["mid"] = _parts(pid)["tasks"]
            seen.set()
            hold.wait(20)

        task = _runner().submit(_ctx(pid), "update_source", NAME, fn)
        seen.wait(5)
        hold.set()
        final = _wait_state(pid, task.id)

        assert final["state"] == "succeeded"
        assert box["mid"] > box["before"]  # 进行中就已经递增过
        assert _parts(pid)["tasks"] > box["mid"]  # 结束时再来一次

    def test_repeated_identical_progress_write_bumps_once(self, pid):
        """同一个值写第二次不是「变化」，否则刷新提示会被无意义的递增刷屏。"""
        box: Dict[str, int] = {}

        def fn(task):
            task.progress["total"] = 5
            task.progress["done"] = 2
            box["before"] = _parts(pid)["tasks"]
            task.progress["done"] = 2
            task.progress["total"] = 5
            box["after"] = _parts(pid)["tasks"]

        task = _runner().submit(_ctx(pid), "update_source", NAME, fn)
        _wait_state(pid, task.id)

        assert box["after"] == box["before"]

    def test_update_all_finish_bumps_settings_but_not_files_twice(self, pid, servers, sleeps):
        """卡 5 裁定 `update_all` 不动 settings ⇒ 由 runner 补上；
        `files` 只由服务层按真实变更递增，runner 不重复（R5 同口径）。"""
        from app.core import storage

        srv = servers({"/new.xml": {"body": XML_V2}})
        _seed_mapping(pid, [{"name": NAME, "url": srv.url("/new.xml"), "record_url": ""}])
        with storage.use_profile(pid):
            storage.save_config_file(NAME, XML_V1)
        before = _parts(pid)

        task = _wait_state(
            pid, _runner().submit(_ctx(pid), "update_all", None, tasks_handler("update_all")).id
        )
        after = _parts(pid)

        assert task["state"] == "succeeded"
        assert after["files"] == before["files"] + 1
        assert after["settings"] == before["settings"] + 1
        assert after["tasks"] > before["tasks"]

    def test_update_source_task_does_not_bump_settings(self, pid, servers, sleeps):
        from app.core import storage

        srv = servers({"/new.xml": {"body": XML_V2}})
        _seed_mapping(pid, [{"name": NAME, "url": srv.url("/new.xml"), "record_url": ""}])
        with storage.use_profile(pid):
            storage.save_config_file(NAME, XML_V1)
        before = _parts(pid)

        _wait_state(pid, _runner().submit(_ctx(pid), "update_source", NAME, tasks_handler("update_source")).id)
        after = _parts(pid)

        assert after["files"] == before["files"] + 1
        assert after["settings"] == before["settings"]

    def test_failed_task_does_not_bump_files_or_settings(self, pid):
        """失败写不递增（全局约束）；但 tasks 要递增，否则别人的界面停在「进行中」。"""

        def boom(task):
            raise RuntimeError("下载器炸了")

        before = _parts(pid)
        _wait_state(pid, _runner().submit(_ctx(pid), "update_all", None, boom).id)
        after = _parts(pid)

        assert after["settings"] == before["settings"]
        assert after["files"] == before["files"]
        assert after["tasks"] > before["tasks"]


# ---------------------------------------------------------------------------
# 6. /tasks 接口（真实服务器 + 四角色）
# ---------------------------------------------------------------------------


class TestTasksApi:
    def test_deployer_post_returns_202_and_task_payload(self, pid):
        _seed_mapping(pid, [])
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "POST", "/api/v1/p/%s/tasks" % pid, {"type": "update_all"}
            )
            assert status == 202, body
            task = body["task"]
            assert set(task) == TASK_FIELDS
            assert task["profile"] == pid
            assert task["type"] == "update_all"
            assert task["target"] is None
            assert task["state"] in ACTIVE_STATES
            assert task["actor"] == "127.0.0.1"  # 部署者未登记 ⇒ 身份名就是 IP
            assert task["error"] is None
            _wait_state(pid, task["id"])

    @pytest.mark.parametrize("role", ["visitor", "member", "admin"])
    def test_non_deployer_post_is_403_and_creates_nothing(self, pid, role):
        """卡面验收：非部署者 POST → 403（四角色夹具）。"""
        _seed_mapping(pid, [{"name": NAME, "url": "http://example.invalid/a.xml", "record_url": ""}])
        before = _parts(pid)
        with make_server() as server:
            status, _, body = as_role(server, role).json(
                "POST", "/api/v1/p/%s/tasks" % pid, {"type": "update_source", "name": NAME}
            )
            assert status == 403, role
            assert body["error"]["code"] == "FORBIDDEN"
            assert body["error"]["detail"]["action"] == "tasks_run"
        assert _runner().list(pid, False) == []
        assert _parts(pid) == before  # 失败写不递增修订号

    def test_post_without_csrf_header_is_403(self, pid):
        """CSRF 硬条件由 `http.py` 统一检查，本卡不重复实现也不放宽（§11.4）。"""
        _seed_mapping(pid, [])
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "POST", "/api/v1/p/%s/tasks" % pid, {"type": "update_all"}, with_csrf=False
            )
        assert status == 403
        assert body["error"]["code"] == "FORBIDDEN"

    def test_reads_are_available_to_every_role(self, pid):
        _seed_mapping(pid, [])
        with make_server() as server:
            _, _, created = as_role(server, "deployer").json(
                "POST", "/api/v1/p/%s/tasks" % pid, {"type": "update_all"}
            )
            task_id = created["task"]["id"]
            _wait_state(pid, task_id)
            for role in ("visitor", "member", "admin", "deployer"):
                status, _, body = as_role(server, role).json("GET", "/api/v1/p/%s/tasks" % pid)
                assert status == 200, role
                assert set(body) == {"items"}
                assert [t["id"] for t in body["items"]] == [task_id]
                assert set(body["items"][0]) == TASK_FIELDS

                status, _, body = as_role(server, role).json("GET", "/api/v1/tasks/%s" % task_id)
                assert status == 200, role
                assert body["task"]["id"] == task_id
                assert body["task"]["state"] == "succeeded"

    def test_active_query_param_filters_finished_tasks(self, pid):
        _seed_mapping(pid, [])
        with make_server() as server:
            _, _, created = as_role(server, "deployer").json(
                "POST", "/api/v1/p/%s/tasks" % pid, {"type": "update_all"}
            )
            task_id = created["task"]["id"]
            _wait_state(pid, task_id)

            status, _, body = as_role(server, "visitor").json("GET", "/api/v1/p/%s/tasks?active=1" % pid)
            assert status == 200
            assert body["items"] == []

            status, _, body = as_role(server, "visitor").json("GET", "/api/v1/p/%s/tasks" % pid)
            assert [t["id"] for t in body["items"]] == [task_id]

    def test_unknown_task_id_is_404(self, pid):
        with make_server() as server:
            status, _, body = as_role(server, "visitor").json("GET", "/api/v1/tasks/ghost")
        assert status == 404
        assert body["error"]["code"] == "NOT_FOUND"

    def test_unknown_profile_is_404(self, pid):
        """机型存在性由 `http.py` 统一处理：不能因为路径是 tasks 就回落到 default。"""
        with make_server() as server:
            status, _headers, _body = as_role(server, "visitor").request("GET", "/api/v1/p/ghost/tasks")
        assert status == 404

    @pytest.mark.parametrize(
        "payload,code,field",
        [
            ({"type": "delete_everything"}, "INVALID_INPUT", "type"),
            ({}, "INVALID_INPUT", "type"),
            ({"type": "update_source"}, "INVALID_INPUT", "name"),
            ({"type": "update_source", "name": ""}, "INVALID_INPUT", "name"),
            ({"type": "update_source", "name": "sub/a.xml"}, "INVALID_INPUT", "name"),
            ({"type": "refresh_records", "name": "  "}, "INVALID_INPUT", "name"),
        ],
    )
    def test_bad_requests_are_400_without_touching_the_queue(self, pid, payload, code, field):
        _seed_mapping(pid, [])
        before = _parts(pid)
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "POST", "/api/v1/p/%s/tasks" % pid, payload
            )
        assert status == 400
        assert body["error"]["code"] == code
        assert body["error"]["detail"]["field"] == field
        assert _runner().list(pid, False) == []
        assert _parts(pid) == before

    def test_name_missing_from_mapping_is_404(self, pid):
        """与 mock 同口径：映射里没有这个名字就不该排一个「成功但什么都没做」的任务。"""
        _seed_mapping(pid, [])
        before = _parts(pid)
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "POST", "/api/v1/p/%s/tasks" % pid, {"type": "update_source", "name": "ghost.xml"}
            )
        assert status == 404
        assert body["error"]["code"] == "NOT_FOUND"
        assert _runner().list(pid, False) == []
        assert _parts(pid) == before

    def test_second_post_while_first_runs_is_409_with_first_task_id(self, pid, monkeypatch):
        """卡面验收：并发两次相同任务，第二次 409 且 `detail.task_id` 指向第一次。"""
        from app.services import updates

        release = threading.Event()
        entered = threading.Event()

        def slow(pid_, name, actor):  # 阻塞的假任务：真实 handler 会调它
            entered.set()
            release.wait(20)
            return updates.refresh_records(pid_, name, actor)

        monkeypatch.setattr(updates, "update_source", slow)
        _seed_mapping(pid, [{"name": NAME, "url": "http://example.invalid/a.xml", "record_url": ""}])
        with make_server() as server:
            caller = as_role(server, "deployer")
            first_status, _, first = caller.json(
                "POST", "/api/v1/p/%s/tasks" % pid, {"type": "update_source", "name": NAME}
            )
            assert first_status == 202
            assert entered.wait(5)

            second_status, _, second = caller.json(
                "POST", "/api/v1/p/%s/tasks" % pid, {"type": "update_source", "name": NAME}
            )
            assert second_status == 409
            assert second["error"]["code"] == "BUSY"
            assert second["error"]["detail"]["task_id"] == first["task"]["id"]

            release.set()
            _wait_state(pid, first["task"]["id"])

    def test_post_returns_immediately_and_never_downloads_in_the_http_thread(self, pid, servers):
        """卡面：handler 不得同步跑超过 2 秒的下载——下载只发生在工作线程里。"""
        gate = threading.Event()
        srv = servers({"/new.xml": {"body": XML_V2, "wait": gate}})
        _seed_mapping(pid, [{"name": NAME, "url": srv.url("/new.xml"), "record_url": ""}])
        with make_server() as server:
            started = time.time()
            status, _, body = as_role(server, "deployer").json(
                "POST", "/api/v1/p/%s/tasks" % pid, {"type": "update_source", "name": NAME}
            )
            elapsed = time.time() - started

            assert status == 202
            assert elapsed < 2.0, "POST 在 HTTP 线程里同步等下载（%.2f s）" % elapsed
            assert _runner().get(body["task"]["id"]).state in ACTIVE_STATES

            gate.set()
            task = _wait_state(pid, body["task"]["id"])
        assert task["state"] == "succeeded"
        assert srv.requests == ["/new.xml"]

    def test_worker_takes_the_profile_lock_http_thread_does_not(self, pid, servers):
        """卡面：任务内部自己加机型锁，不在 HTTP 线程持锁等下载。
        主线程持有机型写锁时 POST 仍立刻返回 202，任务停在进行中直到锁被放出。"""
        from app.core import storage

        srv = servers({"/new.xml": {"body": XML_V2}})
        _seed_mapping(pid, [{"name": NAME, "url": srv.url("/new.xml"), "record_url": ""}])
        with storage.use_profile(pid):
            storage.save_config_file(NAME, XML_V1)

        with make_server() as server:
            with storage.profile_write(pid):
                started = time.time()
                status, _, body = as_role(server, "deployer").json(
                    "POST", "/api/v1/p/%s/tasks" % pid, {"type": "update_source", "name": NAME}
                )
                assert status == 202
                assert time.time() - started < 2.0  # HTTP 线程没有排队等锁
                assert _runner().get(body["task"]["id"]).state in ACTIVE_STATES

            task = _wait_state(pid, body["task"]["id"])
        assert task["state"] == "succeeded"
        assert task["result"]["items"][0]["status"] == "success"

    def test_partial_state_for_update_all_with_one_dead_source(self, pid, servers, sleeps):
        """卡面验收：3 项、1 项 404 ⇒ state 是 partial 而不是 failed。"""
        from app.core import storage

        srv = servers({"/a.xml": {"body": XML_V2}, "/b.xml": {"body": XML_V2}})
        _seed_mapping(
            pid,
            [
                {"name": "a.xml", "url": srv.url("/a.xml"), "record_url": ""},
                {"name": "b.xml", "url": srv.url("/b.xml"), "record_url": ""},
                {"name": "c.xml", "url": srv.url("/missing.xml"), "record_url": ""},
            ],
        )
        with storage.use_profile(pid):
            for name in ("a.xml", "b.xml", "c.xml"):
                storage.save_config_file(name, XML_V1)
        before = _parts(pid)

        with make_server() as server:
            _, _, body = as_role(server, "deployer").json(
                "POST", "/api/v1/p/%s/tasks" % pid, {"type": "update_all"}
            )
            task = _wait_state(pid, body["task"]["id"])

        assert task["state"] == "partial"
        assert task["error"] is None
        assert task["progress"] == {"done": 3, "total": 3}
        statuses = {item["name"]: item["status"] for item in task["result"]["items"]}
        assert statuses == {"a.xml": "success", "b.xml": "success", "c.xml": "download_failed"}
        # 部分失败仍然算「写成功」：last_run 落盘了 ⇒ settings 递增（§10.1）
        assert _parts(pid)["settings"] == before["settings"] + 1

    def test_refresh_records_task_runs_the_card_5_skeleton(self, pid):
        _seed_mapping(pid, [{"name": NAME, "url": "", "record_url": "http://example.invalid/r.xml"}])
        with make_server() as server:
            _, _, body = as_role(server, "deployer").json(
                "POST", "/api/v1/p/%s/tasks" % pid, {"type": "refresh_records", "name": NAME}
            )
            task = _wait_state(pid, body["task"]["id"])

        assert task["type"] == "refresh_records"
        assert task["result"]["items"][0]["status"] == "skipped"
        assert "M3-09" in task["result"]["items"][0]["reason"]

    def test_read_endpoints_do_not_bump_any_part(self, pid):
        _seed_mapping(pid, [])
        with make_server() as server:
            _, _, body = as_role(server, "deployer").json(
                "POST", "/api/v1/p/%s/tasks" % pid, {"type": "update_all"}
            )
            task_id = body["task"]["id"]
            _wait_state(pid, task_id)
            before = _parts(pid)
            as_role(server, "visitor").json("GET", "/api/v1/p/%s/tasks" % pid)
            as_role(server, "visitor").json("GET", "/api/v1/tasks/%s" % task_id)
            assert _parts(pid) == before

    def test_unknown_profile_for_post_is_404(self, pid):
        with make_server() as server:
            status, _headers, _body = as_role(server, "deployer").request(
                "POST", "/api/v1/p/ghost/tasks", body={"type": "update_all"}
            )
        assert status == 404


# ---------------------------------------------------------------------------
# 7. 有序停止（R9：runner 先、server 后；R10：幂等）
# ---------------------------------------------------------------------------


class _StubRunner:
    def __init__(self, log: List[str]) -> None:
        self.log = log
        self.timeouts: List[float] = []

    def shutdown(self, timeout: float = 30) -> None:
        self.timeouts.append(timeout)
        self.log.append("runner")


class _StubServer:
    """假 HTTP 服务器：只记录「自己被关掉的瞬间还有几个进行中任务」。"""

    def __init__(self, log: List[str], observe=None) -> None:
        self.log = log
        self.observe = observe
        self.active_at_stop: Any = "unset"

    def shutdown(self) -> None:
        if self.observe is not None:
            self.active_at_stop = self.observe()
        self.log.append("server")


class TestOrderedStop:
    def test_ordered_stop_shuts_runner_before_server(self):
        """变异靶子之一：把 `run.ordered_stop` 里两行互换，本用例立刻红。"""
        import run

        log: List[str] = []
        runner, server = _StubRunner(log), _StubServer(log)
        run.ordered_stop(runner, server)

        assert log == ["runner", "server"]
        assert runner.timeouts == [30]  # 默认等 30 秒（§10.1 签名）

    def test_ordered_stop_passes_the_configured_timeout(self):
        import run

        log: List[str] = []
        runner = _StubRunner(log)
        run.ordered_stop(runner, _StubServer(log), timeout=5)
        assert runner.timeouts == [5]

    def test_ordered_stop_waits_for_in_flight_task_before_stopping_http(self, pid):
        """变异靶子之二（行为面）：顺序反了就会带着进行中任务去关 HTTP。"""
        import run

        release = threading.Event()
        entered = threading.Event()
        runner = _runner()
        task = runner.submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
        entered.wait(5)
        threading.Timer(0.2, release.set).start()
        server = _StubServer([], observe=lambda: len(runner.list(pid, True)))

        run.ordered_stop(runner, server, timeout=10)

        # 关键断言：HTTP 被关掉的**那一刻**在途任务已经排空（顺序反了这里就是 1）
        assert server.active_at_stop == 0
        assert task.state == "succeeded"
        assert _worker_threads() == []

    def test_mcserver_shutdown_drains_the_runner_first(self, pid, tmp_path):
        from app.config import Settings
        from app.server.http import MCServer
        from app.server.router import build_router

        settings = Settings(
            host="127.0.0.1",
            port=0,
            data_dir=str(tmp_path / "data"),
            web_dir=os.path.join(ROOT, "app", "web"),
        )
        server = MCServer(settings, build_router(settings), runner=_runner())
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            release = threading.Event()
            entered = threading.Event()
            task = _runner().submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
            entered.wait(5)
            threading.Timer(0.2, release.set).start()

            server.shutdown()  # MCServer 自己保证「先排空任务、再关 HTTP」

            assert task.state == "succeeded"
            thread.join(timeout=5)
            assert not thread.is_alive()
        finally:
            server.server_close()

    def test_mcserver_without_runner_keeps_the_two_argument_contract(self, tmp_path):
        """既有调用方（`authz_fixtures.make_server`）只传两个参数，行为不变。"""
        from app.config import Settings
        from app.server.http import MCServer
        from app.server.router import build_router

        settings = Settings(
            host="127.0.0.1",
            port=0,
            data_dir=str(tmp_path / "data"),
            web_dir=os.path.join(ROOT, "app", "web"),
        )
        server = MCServer(settings, build_router(settings))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        assert not thread.is_alive()

    def test_run_py_uses_the_sequence_in_both_exit_paths(self):
        """R9：`_handle_signal` 与正常退出路径都要走同一条有序停止，不能只改一处。"""
        src = _js("run.py")
        assert "settings.max_workers" in src  # max_workers 来自 Settings（app/config.py:61）
        assert src.count("ordered_stop(runner, server)") == 2, "两条退出路径都要有序停止"
        assert "runner.shutdown" in src and "server.shutdown" in src

    def test_http_worker_threads_stay_daemon(self):
        """R9 的边界：`daemon_threads = True` 只覆盖 HTTP 处理线程，本卡不动它。"""
        from app.server.http import MCServer

        assert MCServer.daemon_threads is True


# ---------------------------------------------------------------------------
# 8. 分层、契约与 D4 前端门控
# ---------------------------------------------------------------------------


class TestLayeringAndContract:
    def test_service_does_not_import_http(self):
        import app.services.tasks as tasks

        offenders = [
            name for name in dir(tasks) if name in ("http", "Response", "Router", "json_body", "authz")
        ]
        assert offenders == []

    def test_tasks_does_not_touch_storage_directly(self):
        """§3.3：持久化只走 storage——队列本身不读写业务文件（下载与落盘都在 `updates` 里）。"""
        import app.services.tasks as tasks

        assert "storage" not in dir(tasks)

    def test_handler_delegates_to_service_and_never_touches_storage(self):
        import app.server.api_tasks as api_tasks

        assert "storage" not in dir(api_tasks)
        assert "tasks" in dir(api_tasks)
        assert "updates" in dir(api_tasks)

    def test_signatures_match_the_interface_contract(self):
        from app.services import tasks

        assert list(inspect.signature(tasks.TaskRunner.__init__).parameters) == ["self", "max_workers"]
        assert list(inspect.signature(tasks.TaskRunner.submit).parameters) == [
            "self",
            "ctx",
            "ttype",
            "target",
            "fn",
        ]
        assert list(inspect.signature(tasks.TaskRunner.get).parameters) == ["self", "task_id"]
        assert list(inspect.signature(tasks.TaskRunner.list).parameters) == ["self", "pid", "active_only"]
        shutdown_params = inspect.signature(tasks.TaskRunner.shutdown).parameters
        assert list(shutdown_params) == ["self", "timeout"]
        assert shutdown_params["timeout"].default == 30  # §10.1：至多等 30 秒
        init_params = inspect.signature(tasks.TaskRunner.__init__).parameters
        assert init_params["max_workers"].default == 4  # 卡面契约：默认 4

    def test_routes_are_registered(self):
        from app.config import Settings
        from app.server.router import build_router

        router = build_router(Settings())
        for method, path in (
            ("POST", "/api/v1/p/default/tasks"),
            ("GET", "/api/v1/p/default/tasks"),
            ("GET", "/api/v1/tasks/abc123"),
        ):
            handler, _params = router.match(method, path)
            assert handler is not None, path
        _, params = router.match("GET", "/api/v1/tasks/abc123")
        assert params == {"id": "abc123"}

    def test_tasks_part_is_constant_and_first_used_by_the_runner(self):
        """`tasks` 这个修订分量在卡 5 之前没有任何调用点，本卡是第一个。"""
        from app.services import revision, tasks

        assert "tasks" in revision.PARTS
        assert tasks.TASK_PART == "tasks"

    def test_mock_task_contract_matches_the_backend(self):
        """R19：mock 与后端同 PR 对齐——payload 不带内部键，失败判定用同一套词汇。"""
        from app.services import tasks as tasks_service

        source = _js("app", "web", "assets", "js", "mock", "mock-api.js")
        block = _block(source, "function runTask(", 2600)

        assert "key: key" not in block  # `key` 是内部去重键，不进 §7.10 的 payload
        assert "taskKeys[t.id]" in block
        statuses = re.findall(
            r'"([a-z_]+)"', _block(source, "var TASK_FAILED_STATUSES = [", 200).split("];")[0]
        )
        assert set(statuses) == set(tasks_service.FAILED_ITEM_STATUSES)
        assert 'return ok({ task: t }, 202)' in block


class TestD4FrontendGating:
    def test_fileview_no_longer_borrows_the_tools_key(self):
        """卡面 grep：`tools` 键从此只代表外部工具。"""
        source = _js("app", "web", "assets", "js", "views", "fileview.js")

        assert "MC.caps.tools" not in source
        assert "MC.caps.tasks" in source
        assert "拉取修改记录需要任务接口（M3-07）" in source
        assert "属 M3-08" not in source  # 注释里写错的卡号已纠正

    def test_api_js_declares_and_wires_the_tasks_key(self):
        source = _js("app", "web", "assets", "js", "core", "api.js")
        caps = re.findall(
            r'"([A-Za-z_]+)"', _block(source, "var CAP_KEYS = [", 400).split("];")[0]
        )
        wired = re.findall(r'"([A-Za-z_]+)"', _block(source, "var WIRED = [", 400).split("];")[0])
        unwired = _block(source, "MC.unwired = {", 1200).split("};")[0]

        assert caps == [
            "favorites", "remarks", "schedule", "search", "review", "dl", "settings", "import",
            "delete", "history", "compare", "records", "tools", "revision", "temp", "tasks",
        ]
        assert len(caps) == 16
        assert "tasks" in wired
        assert set(wired) <= set(caps)  # 与前端测试页同一条不变量
        assert re.search(r'tasks:\s*"[^"]*M3-07[^"]*"', unwired), "每项能力都要有未接入说明"

    def test_shell_does_not_request_tasks_when_unwired(self):
        source = _js("app", "web", "assets", "js", "views", "shell.js")
        for name in ("pollTasks", "startTask"):
            block = _block(source, "App.prototype.%s = function" % name, 700)
            assert "if (!MC.caps.tasks)" in block, name
            assert block.index("if (!MC.caps.tasks)") < block.index("api."), name

    def test_settings_update_buttons_are_gated_on_the_tasks_key(self):
        """D4 第 4 点：两个更新按钮都按 `MC.caps.tasks` 决定 disabled + title。

        实现里把开关算成一个局部 `tasksOff` 供两个按钮共用（比在两处各写一遍
        `!MC.caps.tasks` 更难写错），所以断言落在「两个按钮的 disabled 表达式」上。
        """
        source = _js("app", "web", "assets", "js", "views", "settings.js")

        assert "MC.caps.tasks" in source
        assert "MC.unwired.tasks" in source
        for marker in ("disabled: running || !!tasksOff", "disabled: ro() || !!tasksOff"):
            assert marker in source, marker  # 全部更新 / 单条更新，一个都不能漏
        assert source.count("tasksOff") >= 5

    def test_shell_task_entry_lists_active_tasks(self):
        """D4 第 3 点：顶栏任务入口要能列出 active 任务（进度面板在 shell.js）。"""
        source = _js("app", "web", "assets", "js", "views", "shell.js")
        block = _block(source, "App.prototype.renderTasks = function", 900)

        assert 't.state === "running" || t.state === "queued"' in block
        assert "进行中的任务" in block
