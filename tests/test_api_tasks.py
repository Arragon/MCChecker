"""后台任务队列与任务接口（卡 6 · M3-07 / INH-1892；规范 §10.1、§7.10、D4）。

四层用例分别证明四件事：

1. `services/tasks.py` 的 TaskRunner —— **直接调用**（阻塞的假任务只存在于本文件）：
   状态机、进度递增、BUSY 互斥、内存保留、停止语义（等待在途任务 / 幂等 / 线程形态）；
2. `/tasks` 三条接口走真实 `MCServer` + 四角色夹具（202 / 403 / 400 / 404 / 409），
   并证明「下载与机型锁都在工作线程里，HTTP 线程不持锁等下载」；
3. `run.py` 的**有序停止**（R9：先 `runner.shutdown` 再 `server.shutdown`）——
   `test_ordered_stop_waits_for_in_flight_task_before_stopping_http` 是变异验证的靶子：
   把那两行顺序反过来它一定转红；
4. D4 前端门控与 mock 契约：门控行为（未接入 ⇒ 禁用 + title + 不发请求）由
   `app/web/tests/tests.js` 的浏览器用例执法，本文件只保留能由配置数据解析出的
   mock 失败状态集不再与后端逐字对比，缺口见文件末尾说明。

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
from conftest import GATE_TIMEOUT
from app.services import tasks

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
#: 「还有没有人在写 data/」的判据只有一个出处：`tasks.worker_threads()`——按 `mctask`
#: **线程名**筛、摘掉不写盘的 `mctask-shutdown` 等待线程（daemon 位从提交方继承，
#: 现网就是 daemon，不可用，见 `docs/know-how.md` M3-07 / C1）。本文件不再自带前缀常量、
#: 也不再自己按裸前缀筛（修复轮 3 / Item 5）。


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
            gate.wait(GATE_TIMEOUT)  # 「handler 不得同步下载」那条用：挂着直到测试放行（超时兜底防卡死）
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
        release.wait(GATE_TIMEOUT)
        task.result["items"].append(_ok_item())

    return fn


def _submit_like_http(runner, target_pid: str, ttype: str, target, fn):
    """按**现网的形状**提交任务：从一条 daemon 线程调 `submit`。

    `MCServer.daemon_threads = True`，而 CPython 的 `Thread.__init__` 把 daemon 位从
    **创建者**继承下去（`threading.py:828`），所以真实服务器里池那几条 `mctask_N` 工作线程
    就是 daemon。从主线程提交（本文件其余用例的形状）拿到的是非 daemon，正好让
    「按 `daemon` 过滤」这种错判据看起来成立——C1 的第一版就死在这里，是手工 SIGTERM
    验证发现的（30 秒删锁、下载还在跑），见 `docs/know-how.md`。
    """
    box: Dict[str, Any] = {}

    def go():
        box["task"] = runner.submit(_ctx(target_pid), ttype, target, fn)

    caller = threading.Thread(target=go, daemon=True, name="http-handler-stub")
    caller.start()
    caller.join(5)
    assert "task" in box, "daemon 提交线程没跑完：submit 自己卡住了"
    return box["task"]


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
    """生产谓词的别名（Item 5）：判据只认 `tasks.worker_threads()`，别再自己筛裸前缀。"""
    return tasks.worker_threads()


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
    def test_task_fields_are_exactly_the_section_7_10_shape(self, pid, gates):
        release = gates()
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

    def test_list_filters_by_profile_and_active_only(self, pid, gates):
        from app.core import device_models

        other = device_models.add_model("tasks-other")
        release = gates()
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

    def test_worker_daemonness_follows_the_submitter_not_the_pool(self, pid, gates):
        """R9 / C1 的形态事实：daemon 位从**提交方**继承，不是池子自带的——判据只能是线程名。

        CPython 3.9 `threading.py:828` 就是 `self._daemonic = current_thread().daemon`，
        两种形态本机都实测过：从主线程提交（单测的形状）⇒ 非 daemon；从 daemon 线程提交
        （现网 HTTP 处理线程的形状，`_submit_like_http`）⇒ daemon。所以 `daemon` 位
        在哪个形态下都当不了「还有没有人在写 `data/`」的判据——`tasks.worker_threads()`
        按 `mctask` 名字前缀筛，两种形态都看得见同几条线程。
        """
        # 形态一：主线程提交 ⇒ 非 daemon（这正是「按 daemon 筛」看起来成立的那个形状）。
        release = gates()
        entered = threading.Event()
        runner = tasks.TaskRunner(1)
        try:
            task = runner.submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
            assert entered.wait(5)
            workers = tasks.worker_threads()
            assert workers and all(not t.daemon for t in workers), "主线程提交出来的工作线程应是非 daemon"
            assert all(t.name.startswith(tasks.THREAD_NAME_PREFIX) for t in workers)
        finally:
            release.set()
            runner.shutdown(5)
            assert runner.wait_for_workers(10), "形态一的工作线程必须在本用例里散干净（H3）"

        # 形态二：daemon 提交线程（现网形状，`MCServer.daemon_threads = True`）⇒ 工作线程也是 daemon。
        release2 = gates()
        entered2 = threading.Event()
        runner2 = tasks.TaskRunner(1)
        try:
            task2 = _submit_like_http(runner2, pid, "update_source", NAME, _blocking(release2, entered2))
            assert entered2.wait(5)
            workers2 = tasks.worker_threads()
            assert workers2 and all(t.daemon for t in workers2), (
                "现网形状下工作线程就是 daemon——C1 的第一版按 `not t.daemon` 过滤，把「还没散」"
                "读成「已散尽」，30 秒就删了锁而下载还在跑（见 docs/know-how.md）"
            )
            assert all(t.name.startswith(tasks.THREAD_NAME_PREFIX) for t in workers2), (
                "liveness 谓词是名字：daemon 与否，worker_threads() 都看得见它们"
            )
        finally:
            release2.set()
            _wait_task(task2)
            runner2.shutdown(5)
            assert runner2.wait_for_workers(10), "形态二的工作线程必须在本用例里散干净（H3）"


# ---------------------------------------------------------------------------
# 2. BUSY 与互斥（§10.1：同 key 进行中；update_all 与同机型任意 update_source 互斥；
#    卡 6 复审 F2 把 refresh_records 也拉进同一机型的互斥面，见下面两条用例）
# ---------------------------------------------------------------------------


class TestBusyAndMutex:
    def _busy(self, target_pid: str, ttype: str, target: Optional[str]):
        from app.core.errors import AppError

        with pytest.raises(AppError) as info:
            _runner().submit(_ctx(target_pid), ttype, target, _noop)
        return info.value

    def test_same_key_second_submit_is_busy_and_points_to_first(self, pid, gates):
        from app.core.errors import BUSY

        release = gates()
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

    def test_update_all_and_update_source_are_mutually_exclusive(self, pid, gates):
        release = gates()
        entered = threading.Event()
        source = _runner().submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
        entered.wait(5)
        assert self._busy(pid, "update_all", None).detail["task_id"] == source.id
        release.set()
        _wait_state(pid, source.id)

        release2 = gates()
        entered2 = threading.Event()
        bulk = _runner().submit(_ctx(pid), "update_all", None, _blocking(release2, entered2))
        entered2.wait(5)
        # 反方向同样互斥，且报的是那个正在跑的全量任务
        assert self._busy(pid, "update_source", NAME).detail["task_id"] == bulk.id
        release2.set()
        _wait_state(pid, bulk.id)

    def test_update_all_blocks_every_source_of_the_same_profile(self, pid, gates):
        """互斥轴是「机型」，不是「文件名」：同一机型同时只允许一个更新类任务改同一批文件。"""
        release = gates()
        entered = threading.Event()
        first = _runner().submit(_ctx(pid), "update_all", None, _blocking(release, entered))
        entered.wait(5)

        assert self._busy(pid, "update_source", "别的.xml").detail["task_id"] == first.id

        release.set()
        _wait_state(pid, first.id)

    def test_other_target_or_profile_is_allowed(self, pid, gates):
        """互斥面**之内**仍然并行的只有「同一类型的不同 target」与「别的机型」。

        卡 6 复审 F2 之后，「同机型的别的类型」这一轴已经不在这里成立了：三类更新任务
        只要沾上 `refresh_records` 就要互相挡（见下面两条）。所以本用例的第四提交换到
        第三个机型上——它证明的是原本要证明的那件事（机型是互斥轴），不是放宽。
        """
        from app.core import device_models

        other = device_models.add_model("tasks-other")
        another = device_models.add_model("tasks-another")
        release = gates()
        entered = threading.Event()
        first = _runner().submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
        entered.wait(5)

        second = _runner().submit(_ctx(pid), "update_source", "other.xml", _noop)
        third = _runner().submit(_ctx(other), "update_source", NAME, _noop)
        fourth = _runner().submit(_ctx(another), "refresh_records", NAME, _noop)
        assert len({first.id, second.id, third.id, fourth.id}) == 4

        release.set()
        for task in (first, second, third, fourth):
            _wait_state(task.profile, task.id)

    def test_refresh_records_and_update_all_are_mutually_exclusive(self, pid, gates):
        """卡 6 复审 F2：`update_all` 自己就逐项跑 `refresh_records`（`updates.py:164`）。

        单独 POST 一条 `refresh_records` 与 `update_all` 并发，就是在同一机型、同一
        target 上把 `records/<name>` 的读-改-写同时跑两遍——正是丢更新。两个方向都要挡，
        而且**不看 target**（`update_all` 会碰所有映射项，`refresh_records` 的 target 换
        一个也照样撞进同一批文件）。
        """
        release = gates()
        entered = threading.Event()
        records = _runner().submit(_ctx(pid), "refresh_records", NAME, _blocking(release, entered))
        entered.wait(5)
        assert self._busy(pid, "update_all", None).detail["task_id"] == records.id
        release.set()
        _wait_state(pid, records.id)

        release2 = gates()
        entered2 = threading.Event()
        bulk = _runner().submit(_ctx(pid), "update_all", None, _blocking(release2, entered2))
        entered2.wait(5)
        # 反方向同样互斥；target 不同也一样挡
        assert self._busy(pid, "refresh_records", "别的.xml").detail["task_id"] == bulk.id
        release2.set()
        _wait_state(pid, bulk.id)

    def test_refresh_records_and_update_source_are_mutually_exclusive(self, pid, gates):
        """同一条记录面也会被 `update_source` 撞上：两个方向都要挡，同样不看 target。"""
        release = gates()
        entered = threading.Event()
        records = _runner().submit(_ctx(pid), "refresh_records", NAME, _blocking(release, entered))
        entered.wait(5)
        assert self._busy(pid, "update_source", NAME).detail["task_id"] == records.id
        release.set()
        _wait_state(pid, records.id)

        release2 = gates()
        entered2 = threading.Event()
        source = _runner().submit(_ctx(pid), "update_source", NAME, _blocking(release2, entered2))
        entered2.wait(5)
        assert self._busy(pid, "refresh_records", "别的.xml").detail["task_id"] == source.id
        release2.set()
        _wait_state(pid, source.id)

    def test_finished_task_frees_the_key(self, pid):
        first = _runner().submit(_ctx(pid), "update_source", NAME, _noop)
        _wait_state(pid, first.id)

        second = _runner().submit(_ctx(pid), "update_source", NAME, _noop)
        assert second.id != first.id
        _wait_state(pid, second.id)

    def test_failure_before_the_task_body_still_frees_the_key(self, pid, monkeypatch):
        """卡 6 复审 F4：`mark_running` 与第一次 `revision.bump` 抛异常时，键必须照样归还。

        这两行原先落在 `try` 之外：异常只会进那只没人 inspect 的 `Future`，而
        `_active[key]` 永远被占住——那个 `(机型, 类型, target)` 从此只回 409 BUSY，
        任务还停在「进行中」被惰性清理永远跳过。打桩点选工作线程里的第一次 `bump`
        本身，不动生产代码的形状。
        """
        from app.services import revision, tasks

        real_bump = revision.bump
        fired = threading.Event()

        def blow_up_in_the_worker(target_pid: str, part: str) -> None:
            # 只炸工作线程里的第一次调用：`submit` 自己那次发生在主线程，必须照常。
            if not fired.is_set() and threading.current_thread().name.startswith(
                tasks.THREAD_NAME_PREFIX
            ):
                fired.set()
                raise RuntimeError("修订号服务在任务体开始前就崩了")
            real_bump(target_pid, part)

        monkeypatch.setattr(revision, "bump", blow_up_in_the_worker)
        broken = _runner().submit(_ctx(pid), "update_source", NAME, _noop)
        payload = _wait_task(broken, FINISHED_STATES, timeout=5.0)

        assert payload["state"] == "failed"
        assert payload["error"], "收口了的失败任务必须留下一条能看的错误"

        # 键确实归还了：同一个 `(机型, 类型, target)` 现在还能再排一条并正常跑完。
        second = _runner().submit(_ctx(pid), "update_source", NAME, _noop)
        assert _wait_task(second, FINISHED_STATES, timeout=5.0)["state"] == "succeeded"

    def test_base_exception_from_the_task_body_still_frees_the_key(self, pid, caplog):
        """I3：`except Exception` 接不住 `BaseException`——`finally` 必须照样收口并留下日志。

        键被占住的后果是永久的：`_active` 里那条永远停在 running，惰性清理**跳过** active
        任务，于是这个 `(机型, 类型, target)` 从此只回 409 BUSY，直到进程重启。
        打桩点选任务体自己抛，不动生产代码的形状。
        """

        class Fatal(BaseException):
            """不是 `Exception` 的子类：`_run` 里那两个 `except` 都不会接住它。"""

        def fn(task):
            raise Fatal("工作线程里逸出的致命错误")

        broken = _runner().submit(_ctx(pid), "update_source", NAME, fn)
        with caplog.at_level(logging.ERROR, logger="app.services.tasks"):
            payload = _wait_task(broken, FINISHED_STATES, timeout=5.0)
            second = _runner().submit(_ctx(pid), "update_source", NAME, _noop)
            done = _wait_task(second, FINISHED_STATES, timeout=5.0)

        assert payload["state"] == "failed"
        assert payload["error"], "BaseException 逸出也要留下一条能看的错误"
        assert done["state"] == "succeeded", "键没归还：同一条 key 又被占住了"
        # 异常本身必须在日志里（`logger.exception` 带的堆栈），不能只进那只没人看的 Future。
        assert "工作线程里逸出的致命错误" in caplog.text

    def test_exception_escaping_the_runner_is_logged_instead_of_vanishing(self, pid, caplog, monkeypatch):
        """I3：`self._pool.submit(self._run, task, fn)` 原先把 Future 丢掉——逸出的异常无声消失。

        造一条「收口之后又崩一次」的**第二次**故障：`fn` 先失败，`_finish()` 已经把状态
        落到终态、互斥键也已经归还，紧接着它自己那次 `revision.bump` 再抛。这条异常既不在
        `_run` 的两个 `except` 里，也不会触发 `finally`（状态已是终态），只能由 Future 的
        done-callback 记下来；没有回调时它就消失在没人 inspect 的 Future 里。
        """
        from app.services import revision, tasks

        real_bump = revision.bump
        watched: Dict[str, Any] = {}

        def bump_after_the_finish(target_pid: str, part: str) -> None:
            task = watched.get("task")
            if (
                task is not None
                and threading.current_thread().name.startswith(tasks.THREAD_NAME_PREFIX)
                and task.state in tasks.FINISHED_STATES
            ):
                raise RuntimeError("收口之后递增修订号又崩了一次")
            real_bump(target_pid, part)

        def boom(task):
            watched["task"] = task  # 在工作线程里登记，保证 `_finish` 之前一定看得见
            raise ValueError("任务体先失败")

        monkeypatch.setattr(revision, "bump", bump_after_the_finish)
        with caplog.at_level(logging.ERROR, logger="app.services.tasks"):
            broken = _runner().submit(_ctx(pid), "update_source", NAME, boom)
            payload = _wait_task(broken, FINISHED_STATES, timeout=5.0)
            # 撤掉第二次故障，证明互斥键真的回来了。
            watched["task"] = None
            second = _runner().submit(_ctx(pid), "update_source", NAME, _noop)
            done = _wait_task(second, FINISHED_STATES, timeout=5.0)

        assert payload["state"] == "failed"
        assert done["state"] == "succeeded"
        assert "收口之后递增修订号又崩了一次" in caplog.text, (
            "逸出 `_run` 的异常必须被记进日志，不能留在没人 inspect 的 Future 里"
        )

    def test_submit_rejects_an_unknown_task_type(self, pid):
        """卡 6 复审 F4：类型清单是 key 语义的一部分，校验要落在 `submit` 上。

        拼错的类型绕过 HTTP handler（`api_tasks.py` 那层只有前端会走）直接进队列后，
        既不参与互斥（`_CONFLICTING_TYPES` 查不到）、也没有 `FINISH_PARTS` 口径，
        只会留下一条永远撞不上的任务。卡 8 / 卡 9 是直接调 `submit` 的。
        """
        from app.core.errors import AppError, INVALID_INPUT

        before = _parts(pid)
        with pytest.raises(AppError) as info:
            _runner().submit(_ctx(pid), "update_sourse", NAME, _noop)
        error = info.value

        assert error.code == INVALID_INPUT
        assert error.http_status == 400
        assert error.detail["field"] == "type"
        assert "update_source/update_all/refresh_records" in error.message  # 给出可选清单
        assert _runner().list(pid, False) == []  # 队列里什么都没留下
        assert _parts(pid) == before  # 连 `tasks` 分量都不该递增

        # 未知类型也不能占用互斥键：同机型的正常提交照样进得去。
        task = _runner().submit(_ctx(pid), "update_source", NAME, _noop)
        assert _wait_task(task, FINISHED_STATES, timeout=5.0)["state"] == "succeeded"


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

    def test_active_tasks_are_never_purged(self, pid, monkeypatch, gates):
        from app.services import tasks

        monkeypatch.setattr(tasks, "RETAIN_MAX", 0)
        release = gates()
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
    def test_shutdown_waits_for_the_running_task(self, pid, gates):
        release = gates()
        entered = threading.Event()
        task = _runner().submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
        entered.wait(5)
        threading.Timer(0.2, release.set).start()

        _runner().shutdown(10)

        assert task.state == "succeeded"
        assert task.finished_at is not None

    def test_gate_and_fixture_budgets_are_aligned(self):
        """I2 的另一半：夹具等 runner 的预算必须**长过**闸门自己的兜底。

        夹具会无条件放行闸门（`conftest.gates`），这条断言守的是那个「万一」：
        有人绕过 `gates()` 自己 `threading.Event()` 卡住工作线程时，夹具的等待也要比
        闸门醒来得晚，否则线程会在 monkeypatch 撤销之后落到真实的 `data/` 里写（H3）。
        """
        from conftest import GATE_TIMEOUT, RUNNER_DRAIN_TIMEOUT

        assert GATE_TIMEOUT == 20.0
        assert RUNNER_DRAIN_TIMEOUT == 30.0
        assert RUNNER_DRAIN_TIMEOUT > GATE_TIMEOUT, (
            "夹具比闸门先放弃 = 孤儿线程活到 monkeypatch 撤销之后（H3）"
        )

    def test_shutdown_timeout_warning_moves_the_wait_to_exit(self, pid, caplog, gates):
        """C1：旧文案写「停止时不再等待」，是句反话——进程会一直活到工作线程跑完。

        读这条日志的是看 systemd 的运维，必须让他知道等待**挪到了退出前**、
        以及锁文件在这种情况下不会被删。
        """
        release = gates()
        entered = threading.Event()
        task = _runner().submit(_ctx(pid), "update_source", NAME, _blocking(release, entered))
        assert entered.wait(5)
        try:
            with caplog.at_level(logging.WARNING, logger="app.services.tasks"):
                _runner().shutdown(0.01)  # 排空预算到期
            text = caplog.text
            assert "没有结束" in text
            assert "退出" in text, "超时警告要说清等待挪到了退出前"
            assert "不再等待" not in text, "「停止时不再等待」是反话：这一刻进程还没退"
        finally:
            release.set()
            _wait_task(task)

    def test_shutdown_is_idempotent_and_never_rejoins(self, pid, gates):
        """R10：信号可能连按两次，第二次不得抛、不得重复 join。"""
        release = gates()
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

    def test_progress_change_bumps_tasks_while_running(self, pid, gates):
        """§10.1「每次进度变化递增 tasks」——必须在任务还在跑的时候就能看见。

        两个读数都取在**工作线程内**（同一个线程里读写才有确定的先后关系）。"""
        hold = gates()
        seen = threading.Event()
        box: Dict[str, int] = {}

        def fn(task):
            box["before"] = _parts(pid)["tasks"]
            task.progress["total"] = 3
            task.progress["done"] = 1
            task.result["items"].append(_ok_item())
            box["mid"] = _parts(pid)["tasks"]
            seen.set()
            hold.wait(GATE_TIMEOUT)

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

    @pytest.mark.parametrize("role", ["visitor", "member"])
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

    def test_second_post_while_first_runs_is_409_with_first_task_id(self, pid, monkeypatch, gates):
        """卡面验收：并发两次相同任务，第二次 409 且 `detail.task_id` 指向第一次。"""
        from app.services import updates

        release = gates()
        entered = threading.Event()

        def slow(pid_, name, actor):  # 阻塞的假任务：真实 handler 会调它
            entered.set()
            release.wait(GATE_TIMEOUT)
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

    def test_post_returns_immediately_and_never_downloads_in_the_http_thread(self, pid, servers, gates):
        """卡面：handler 不得同步跑超过 2 秒的下载——下载只发生在工作线程里。"""
        gate = gates()
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

    def test_refresh_records_task_downloads_and_reports_the_item(self, pid, servers):
        """卡 9（M3-09）之后 `refresh_records` 任务真的会拉：202 → 工作线程下载 → 条目 `success`。

        原名 `test_refresh_records_task_runs_the_card_5_skeleton`，断的是「条目 = skipped
        且 reason 含 M3-09」——那正是本卡要替换掉的骨架行为，期望随之改变（去重与落盘的
        完整用例见 `tests/test_api_records.py`，这里只钉任务链路：类型、状态、条目回填）。
        """
        from app.core import storage

        srv = servers({"/r.xml": {"body": b"<changes><a>1</a></changes>"}})
        _seed_mapping(pid, [{"name": NAME, "url": "", "record_url": srv.url("/r.xml")}])
        with make_server() as server:
            _, _, body = as_role(server, "deployer").json(
                "POST", "/api/v1/p/%s/tasks" % pid, {"type": "refresh_records", "name": NAME}
            )
            task = _wait_state(pid, body["task"]["id"])

        assert task["type"] == "refresh_records"
        assert task["state"] == "succeeded"
        assert task["result"]["items"][0]["status"] == "success"
        assert task["progress"] == {"done": 1, "total": 1}
        assert srv.requests == ["/r.xml"]
        with storage.use_profile(pid):
            assert len(storage.list_record_versions(NAME)) == 1

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

    def test_ordered_stop_waits_for_in_flight_task_before_stopping_http(self, pid, gates):
        """变异靶子之二（行为面）：顺序反了就会带着进行中任务去关 HTTP。"""
        import run

        release = gates()
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

    def test_mcserver_shutdown_drains_the_runner_first(self, pid, tmp_path, gates):
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
            release = gates()
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

    @pytest.mark.parametrize("drained", [True, False])
    def test_main_orders_the_exit_wait_before_releasing_the_lock(
        self, tmp_path, monkeypatch, drained
    ):
        """`main()` 的两条退出路径都走同一套有序停止，锁只在工作线程散尽后才删。

        旧断言 grep `run.py` 的标识符拼写（R45）：把 `lock.release()` 挪到
        `wait_for_workers` 之前、或把 `stop_and_release(...)` 整段删掉再留一句同名
        注释，源码文本不变、旧断言照样绿。这里注入假 settings / runner / server /
        lock 直接跑 `main()`：信号路径与 `finally` 路径各产生一次
        scheduler → runner → server，`server_close` 与 `wait_for_workers` 之后才
        允许删锁；没排空时锁文件必须还在。
        """
        import run
        import signal
        import types

        from app.config import Settings
        from app.server import http as http_module
        from app.server import router as router_module
        from app.services import scheduler as scheduler_service
        from app.services import tasks as tasks_service

        log: List[str] = []
        lock_path = tmp_path / "data" / ".mcchecker.lock"
        signal_path_done = threading.Event()

        class _FakeRunner:
            max_workers = 3

            def __init__(self) -> None:
                self.timeouts: List[float] = []

            def shutdown(self, timeout: float = 30) -> None:
                self.timeouts.append(timeout)
                log.append("runner")

            def wait_for_workers(self, timeout: float) -> bool:
                log.append("wait")
                return drained

        class _FakeServer:
            def serve_forever(self, poll_interval: float = 0.2) -> None:
                handlers[signal.SIGTERM](signal.SIGTERM, None)
                assert signal_path_done.wait(5), "信号退出路径没有走到 server.shutdown"

            def shutdown(self) -> None:
                log.append("server")
                signal_path_done.set()

            def server_close(self) -> None:
                log.append("close")

        class _FakeScheduler:
            def stop(self) -> None:
                log.append("scheduler")

            def is_alive(self) -> bool:
                return False

        class _FakeLock:
            path = str(lock_path)

            def acquire(self) -> None:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text("{}", encoding="utf-8")

            def release(self) -> None:
                log.append("release")
                lock_path.unlink()

        runner = _FakeRunner()
        server = _FakeServer()
        scheduler = _FakeScheduler()
        lock = _FakeLock()
        handlers: Dict[int, Any] = {}
        settings = Settings(
            host="127.0.0.1",
            port=0,
            data_dir=str(tmp_path / "data"),
            web_dir=os.path.join(ROOT, "app", "web"),
            max_workers=7,
            # 这个用例直接跑 main() 里的服务循环。默认热更新会改走父进程监视，
            # 假 server 就进不去了。
            reload=False,
        )

        def init_runner(max_workers: int) -> _FakeRunner:
            log.append("init:%d" % max_workers)
            return runner

        def fake_signal(signum, handler):
            handlers[signum] = handler

        monkeypatch.setattr(run, "load_settings", lambda argv=None: settings)
        monkeypatch.setattr(run, "configure_logging", lambda level, data_dir=None: None)
        monkeypatch.setattr(run.storage, "configure_data_dir", lambda path: None)
        monkeypatch.setattr(
            run.storage, "ensure_profile_layout", lambda migrate_legacy=True: None
        )
        monkeypatch.setattr(run, "_count_profiles", lambda: 0)
        monkeypatch.setattr(
            run,
            "signal",
            types.SimpleNamespace(
                SIGINT=signal.SIGINT, SIGTERM=signal.SIGTERM, signal=fake_signal
            ),
        )
        monkeypatch.setattr(http_module, "SingleInstanceLock", lambda data_dir, port: lock)
        monkeypatch.setattr(
            http_module, "MCServer", lambda cfg, router, runner=None: server
        )
        monkeypatch.setattr(router_module, "build_router", lambda cfg: object())
        monkeypatch.setattr(tasks_service, "init_runner", init_runner)
        monkeypatch.setattr(scheduler_service, "init_scheduler", lambda r: scheduler)
        monkeypatch.setattr(scheduler_service, "get_scheduler", lambda: scheduler)

        assert run.main([]) == 0

        assert log[0] == "init:7"  # worker 数来自 Settings，不是写死的常量
        assert runner.timeouts == [tasks_service.DEFAULT_SHUTDOWN_TIMEOUT] * 2
        expected = [
            "init:7",
            "scheduler",
            "runner",
            "server",  # 信号路径
            "scheduler",
            "runner",
            "server",  # 正常退出路径
            "close",
            "wait",
        ]
        if drained:
            expected.append("release")
            assert not lock_path.exists()
        else:
            assert lock_path.exists(), (
                "工作线程没排空却把锁删了：端口已释放，第二个实例会进来同写 data/"
            )
        assert log == expected

    def test_http_worker_threads_stay_daemon(self):
        """R9 的边界：`daemon_threads = True` 管的是 HTTP 处理线程本身，本卡不动它。

        任务工作线程不从这里拿 daemon 位——`submit` 正是这批 HTTP 线程调的，daemon 位
        沿创建链继承下去（`threading.py:828`）；「还有没有人在写」两种形状下都只按
        线程名判（`tasks.worker_threads()`，见
        `test_worker_daemonness_follows_the_submitter_not_the_pool`）。
        """
        from app.server.http import MCServer

        assert MCServer.daemon_threads is True


# ---------------------------------------------------------------------------
# 7b. 单实例锁必须活得比写的人久（C1 / H3；卡 6 修复轮 2）
# ---------------------------------------------------------------------------


class _ExitStubServer:
    """退出序列里的假 HTTP 服务器：只记录「关 HTTP」这一步发生的先后。"""

    def __init__(self, log: List[str]) -> None:
        self.log = log

    def shutdown(self) -> None:
        self.log.append("http")

    def server_close(self) -> None:
        self.log.append("close")


class _SpyLock:
    """真 `SingleInstanceLock` + 「`release()` 那一刻还有几个工作线程」的录像。"""

    def __init__(self, data_dir: str) -> None:
        from app.server.http import SingleInstanceLock

        self._lock = SingleInstanceLock(data_dir, 0)
        self.workers_at_release: Any = "release 没有被调用"

    @property
    def path(self) -> str:
        return self._lock.path

    def acquire(self) -> None:
        self._lock.acquire()

    def release(self) -> None:
        from app.services import tasks

        self.workers_at_release = sorted(t.name for t in tasks.worker_threads())
        self._lock.release()


class TestLockOutlivesWriters:
    """`lock.release()` 是无条件 `os.unlink`，而它挡的是**跨进程**唯一那道写锁。

    `shutdown()` 的 30 秒排空预算**小于**一个卡死下载的最坏耗时（30 s × 3 次 + 退避，
    而且 `updates.update_all` 串行跑多个源），所以「排空超时」是常态不是意外。超时后
    原先照样删锁：此刻端口已释放、工作线程还在写 `data/profiles/<pid>/…`，第二个实例
    （systemd 自动重启 / `docker restart`）进来就是两个进程同写一份权威数据
    ——`locks.profile_lock` 是 `threading.RLock`、`write_gate` 只在进程内，跨进程一点
    都挡不住。这几条用例把新次序钉住：**先等写的人散，再删锁；等不散就不删**。
    """

    def _held_lock(self, tmp_path) -> _SpyLock:
        lock = _SpyLock(str(tmp_path / "data"))
        lock.acquire()
        return lock

    def test_exit_keeps_the_lock_while_workers_are_still_writing(
        self, pid, tmp_path, caplog, monkeypatch, gates
    ):
        import run
        from app.services import tasks

        # 排空预算给到几乎为零：这就是「批量更新进行中收到 SIGTERM」那条真实路径。
        monkeypatch.setattr(tasks, "DEFAULT_SHUTDOWN_TIMEOUT", 0.01)
        lock = self._held_lock(tmp_path)
        runner = tasks.TaskRunner(1)
        release = gates()
        entered = threading.Event()
        try:
            _submit_like_http(runner, pid, "update_source", NAME, _blocking(release, entered))
            assert entered.wait(5), "假任务没有进入工作线程"
            # 现网形状（daemon 继承）下也必须看得见这条线程，否则「等不散就不删」形同虚设。
            assert tasks.worker_threads(), (
                "任务正在跑却被判成「没有工作线程」：`worker_threads()` 又拿 daemon 位当判据了（C1）"
            )

            with caplog.at_level(logging.ERROR, logger="mcchecker"):
                released = run.stop_and_release(
                    runner, _ExitStubServer([]), lock, exit_timeout=0.1
                )

            assert released is False
            assert os.path.exists(lock.path), (
                "工作线程还在写 data/profiles/，锁文件却已经被 unlink：此刻端口也已释放，"
                "第二个实例会进来同写同一批文件（H3）。退出路径必须等不散就不删。"
            )
            assert lock.workers_at_release == "release 没有被调用"
            assert "锁文件保留" in caplog.text  # 运维要在日志里看见「为什么没删」
            # 保留 ≠ 卡死：写的人还活着的时候第二个实例就该被挡住（等它真的散了以后
            # 由 `acquire()` 认 pid 接管，那条既有路径见
            # tests/test_api_http.py::test_stale_lock_is_taken_over）。
            from app.server.http import SingleInstanceLock

            second = SingleInstanceLock(str(tmp_path / "data"), 0)
            with pytest.raises(RuntimeError) as info:
                second.acquire()
            assert "已在运行" in str(info.value)
        finally:
            release.set()
            runner.shutdown(5)  # 幂等；中途红也要把池关掉，别把空闲线程留给解释器退出
            assert runner.wait_for_workers(10), "拆用例前必须把工作线程等散（H3）"
            lock.release()

    def test_exit_releases_the_lock_only_after_the_last_worker_exits(self, pid, tmp_path):
        import run
        from app.services import tasks

        lock = self._held_lock(tmp_path)
        runner = tasks.TaskRunner(2)
        log: List[str] = []
        try:
            for index in range(4):
                _wait_task(
                    _submit_like_http(runner, pid, "update_source", "s%d.xml" % index, _noop)
                )

            started = time.time()
            released = run.stop_and_release(runner, _ExitStubServer(log), lock, exit_timeout=10)

            assert released is True
            assert lock.workers_at_release == []  # 删锁的那一刻已经没有人还在写
            assert not os.path.exists(lock.path)
            assert log == ["http", "close"]  # 先停 HTTP，再收口锁
            assert time.time() - started < 2.0, "排空了的 runner 也等满了退出预算：正常停止必须立刻走完"
        finally:
            runner.shutdown(5)  # 断言中途红也不能把自建 runner 的工作线程留给下一个用例
            assert not tasks.worker_threads(), "自建 runner 必须在本用例里散干净（H3）"
            lock.release()

    def test_exit_budget_is_far_larger_than_the_drain_budget(self):
        """退出预算的取值依据（`EXIT_WORKER_TIMEOUT` 的注释里逐条写了出处）。"""
        from app.config import DEFAULT_DOWNLOAD_RETRIES, DEFAULT_DOWNLOAD_TIMEOUT_S
        from app.core import downloader
        from app.services import tasks

        # 一个卡死的源最坏多久：30 s 超时 × 3 次尝试 + 退避 (1 s, 2 s)。
        hung_source = DEFAULT_DOWNLOAD_TIMEOUT_S * DEFAULT_DOWNLOAD_RETRIES + sum(
            downloader._RETRY_DELAYS
        )
        assert tasks.DEFAULT_SHUTDOWN_TIMEOUT == 30.0  # §10.1：HTTP 侧只等 30 秒
        assert tasks.EXIT_WORKER_TIMEOUT == 300.0
        assert tasks.EXIT_WORKER_TIMEOUT > 3 * hung_source  # 串行撞三个死源也覆盖得住
        assert tasks.EXIT_WORKER_TIMEOUT > 5 * tasks.DEFAULT_SHUTDOWN_TIMEOUT


# ---------------------------------------------------------------------------
# 7c. `wait_for_workers` 的预算要真的封顶（修复轮 3 / Item 1）
# ---------------------------------------------------------------------------


class _StubExitRunner:
    """`wait_for_workers` 耗时**不等于**配置预算的 runner 桩，专门用来验日志里的秒数。

    `run.stop_and_release` 给运维的那句「已等 %.1f 秒」必须是实测等待时长而不是
    `exit_timeout` 常量：桩在预算 0.05 秒时等 0.3 秒再返回 False，旧实现会印 0.1。
    """

    def __init__(self, wait_seconds: float) -> None:
        self._wait_seconds = wait_seconds

    def shutdown(self, timeout: float = 30) -> None:  # ordered_stop 会先调它
        pass

    def wait_for_workers(self, timeout: float) -> bool:
        time.sleep(self._wait_seconds)
        return False


class TestExitWaitBudget:
    def test_wait_for_workers_total_is_bounded_by_the_budget(self, pid, gates):
        """预算按**整次调用**封顶，不按每条线程各发一份 `remaining`。

        旧实现每轮只算一次 `deadline - now`，然后把它交给每一个还卡着的线程逐个
        `join(remaining)`：本轮代价 = N × remaining。本机用循环副本实测过——预算
        1.0 秒 + 4 条卡死线程 ⇒ 4.03 秒才返回 False；照 `EXIT_WORKER_TIMEOUT=300` ×
        `DEFAULT_MAX_WORKERS=4` 推下去，停止路径能坐到约 20 分钟，而常量注释和文档
        承诺的都是 300 秒。0.9 秒的容差 = 预算的 3 倍（新实现只超几毫秒），仍明显
        小于 4 × 0.3 = 1.2 秒（旧实现的下界）。
        """
        from app.config import DEFAULT_MAX_WORKERS

        runner = tasks.TaskRunner(DEFAULT_MAX_WORKERS)
        release = gates()
        entered = threading.Event()
        try:
            for index in range(DEFAULT_MAX_WORKERS):
                _submit_like_http(
                    runner, pid, "update_source", "hung%d.xml" % index, _blocking(release, entered)
                )
            assert entered.wait(5)
            deadline = time.time() + 5
            while len(tasks.worker_threads()) < DEFAULT_MAX_WORKERS and time.time() < deadline:
                time.sleep(0.01)
            assert len(tasks.worker_threads()) == DEFAULT_MAX_WORKERS, "全部 worker 都应卡在闸门里"

            started = time.monotonic()
            drained = runner.wait_for_workers(0.3)
            elapsed = time.monotonic() - started

            assert drained is False
            assert elapsed < 0.9, (
                "wait_for_workers 超支：每条卡死线程都吃满同一份 remaining——实测 %.2f 秒，"
                "预算只有 0.3 秒（修复轮 3 Item 1：remaining 必须每次 join 前重算）" % elapsed
            )
        finally:
            release.set()
            runner.shutdown(5)
            assert runner.wait_for_workers(10), "本用例的工作线程必须散干净（H3）"

    def test_stop_and_release_logs_the_measured_elapsed_seconds(self, tmp_path, caplog):
        """运维看见的「已等 N 秒」必须是实测值：桩只等 0.3 秒，配置预算是 0.05 秒。

        旧实现无论实际等多久都印 `exit_timeout`——它是这条路径唯一的操作员可见信号，
        印错等于把「等了 20 分钟」报成「等了 300 秒」。
        """
        import run

        lock = _SpyLock(str(tmp_path / "data"))
        lock.acquire()
        try:
            with caplog.at_level(logging.ERROR, logger="mcchecker"):
                released = run.stop_and_release(
                    _StubExitRunner(0.3), _ExitStubServer([]), lock, exit_timeout=0.05
                )
            assert released is False
            assert os.path.exists(lock.path), "等不散就不能删锁（C1）"
            match = re.search(r"等待 (\d+(?:\.\d+)?) 秒", caplog.text)
            assert match is not None, caplog.text
            logged = float(match.group(1))
            assert 0.2 <= logged <= 1.5, (
                "日志印的是配置值（%.1f 秒）而不是实测等待时长（桩等了 0.3 秒）——Item 1："
                "stop_and_release 必须计时后把实际秒数写进 ERROR" % logged
            )
        finally:
            lock.release()


# ---------------------------------------------------------------------------
# 7d. liveness 谓词只有一个出处 + 夹具放行环有哨兵（修复轮 3 / Items 4、5）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def gate_release_sentinel():
    """盯住 `isolated_task_runner` 拆夹具时的**无条件放行环**（Item 4 的哨兵状态）。

    放行环删掉后全部既有用例照样绿——闸门自己有 `GATE_TIMEOUT` 兜底会把孤儿线程接住
    （只是白等 20 秒），这正是「没人盯的基础设施会被下一个人简化掉」的形状。本夹具是
    module 作用域：它的拆（teardown）发生在**本模块最后一条用例的 function 级夹具**
    （含 `isolated_task_runner`）拆完之后，所以它来得及看一眼就只答得出来的那个问题：
    哨兵闸门是被夹具 `set()` 放开的，还是自己到点散的？
    """
    state: Dict[str, Any] = {"armed": False, "released": None}
    done = threading.Event()
    yield state, done
    if state["armed"]:
        assert done.wait(GATE_TIMEOUT + 10), "哨兵线程既没被夹具放行、也没到自己的兜底：放行环和 GATE_TIMEOUT 同时失效"
        assert state["released"] is True, (
            "哨兵闸门最后是自己 20 秒到点散的，不是被 `isolated_task_runner` 拆夹具时的无条件放行"
            "放开的——那两行 `gate.set()` 被删或没跑到（I2 / H3：用例断言中途红时孤儿线程会"
            "活到 monkeypatch 撤销之后，往真实的 data/ 里写）。"
        )


class TestFixtureOwnsEveryGate:
    def test_registered_gate_is_released_by_the_fixture_even_if_the_test_never_sets_it(
        self, gates, isolated_task_runner, gate_release_sentinel
    ):
        """故意在 happy path 不放行闸门：这条 Event 只有夹具的放行环能开。

        用例体内断言「登记归夹具所有」；「放行真的发生」由 module 级哨兵夹具在本夹具
        拆完之后核对——被 `set()` 放开 ⇒ 绿，靠 `GATE_TIMEOUT` 到点 ⇒ 红。删掉
        `conftest.isolated_task_runner` 里那两行 `gate.set()` 本用例必红（变异验证见
        修复轮 3 报告）。
        """
        state, done = gate_release_sentinel
        gate = gates()
        assert gate in isolated_task_runner, (
            "`gates` 工厂没把闸门登记进 `isolated_task_runner` 的放行名单：用例中途红时就没人"
            "放行，孤儿线程会活到 monkeypatch 撤销之后（I2 / H3）"
        )

        def holder() -> None:
            gate.wait(GATE_TIMEOUT)
            state["released"] = gate.is_set()  # 被 set 放开 ⇒ 夹具干的；到点散 ⇒ 放行环没了
            done.set()

        state["armed"] = True
        threading.Thread(target=holder, name="gate-release-sentinel").start()
        # 到这里绝不 gate.set()：这条用例要证明的就是「用例绿、没人放行」时夹具兜底。


class TestLivenessPredicateSingleSource:
    def test_mctask_shutdown_waiter_is_never_counted_as_a_writer(self):
        """谓词单一出处（Item 5）：生产、`_worker_threads()`、夹具 survivors 三处都必须是 `tasks.worker_threads()`。

        裸前缀筛法会把 `mctask-shutdown`（只跑 `pool.shutdown`、一个字节都不写）当成
        「还有人写 data/」：旧夹具在排空恰好踩满预算时会把用例判红成「任务队列已经拆完，
        但仍有工作线程活着：mctask-shutdown」。这里挂一条同名哨兵线程睡 0.5 秒，
        覆盖三层——① 本用例体内直接调 `tasks.worker_threads()`；② 本文件的
        `_worker_threads()`；③ 拆夹具时的 survivors 检查（拆点在哨兵还睡着时到达：
        夹具若退回裸前缀筛，会在自己的 teardown 里把本用例判失败）。
        """
        waiter = threading.Thread(
            target=time.sleep, args=(0.5,), name=tasks.SHUTDOWN_WAITER_NAME, daemon=True
        )
        waiter.start()
        assert tasks.worker_threads() == [], (
            "mctask-shutdown 等待线程一个字节都不写，不该被算进「还有人在写 data/」"
        )
        assert _worker_threads() == [], (
            "`_worker_threads()` 又自己筛裸前缀了：必须调用 tasks.worker_threads()，谓词单一出处"
        )
        # ③ 不在这里断言：本用例一返回夹具就拆，survivors 检查落在 0.5 秒哨兵的睡眠窗口里。


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
        assert list(inspect.signature(tasks.TaskRunner.list).parameters) == [
            "self",
            "pid",
            "active_only",
            "workspace",
        ]  # M10-05：任务按 W/P 归属
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

# R19 的 mock `/tasks` 行为由 `app/web/tests/tests.js` 的执行用例覆盖；
# `function runTask(` 的 2600 字节窗口与 `TASK_FAILED_STATUSES` 源码 grep 已删除（INH-1915）。
# 覆盖缺口：JS 的失败状态列表不再与 `tasks_service.FAILED_ITEM_STATUSES` 逐字对比，
# 如需恢复应做成执行 mock 真身的 cross-check，而不是再 grep 源码。


