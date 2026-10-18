"""更新源校验提交与映射接口（M3-05 / INH-1890，规范 §10.2、§7.9、§5.4）。

四层用例分别证明四件事：

1. `services/updates.py::update_source` 的 §10.2 七步——**直接调用函数**，不经 HTTP：
   任务队列在卡 6，本卡的函数必须能被同步测试直接跑；
2. `storage` 新增的 `update_status.json` 三个函数（新增持久化文件，点名验证它
   **不**随机型复制）；
3. `/sources` CRUD 走真实 `MCServer` + 四角色夹具（UC-31 改名迁移、UC-32 错误页保护）；
4. `TestReadModifyWriteLocks`：本卡自己写的两处「读-改-写」（`schedule.json.last_run` 与
   `update_status.json` 的合并）必须整段扣在同一把机型锁里（§5.4），以及
   `update_status.json` 的键跟着改名/删除走的生命周期规则（见 know-how）。

下载一律打本地 `http.server`（127.0.0.1 随机端口）：卡 4 的 `fetch` 会做逐跳地址校验与
分块限流，打桩 `urlopen` 既进不去、又会把这两件事一起屏蔽（见 `docs/know-how.md` 第 4 节）。
`getproxies` 打桩成 `{}` 是 macOS 环境需要（开发机上的系统代理会把请求接走）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

import pytest

from authz_fixtures import as_role, make_server, seed_roles

NAME = "config.xml"
XML_V1 = b"<config><a>1</a></config>"
XML_V2 = b"<config><a>2</a></config>"
XML_OTHER_ROOT = b"<profile><a>1</a></profile>"
HTML_PAGE = b"<html><body><h1>Server Error</h1><p>token=SECRET</p></body></html>"
JUNK = b"plain text, neither XML nor JSON {{{"
JSON_OBJECT = b'{"a": 1}'
JSON_ARRAY = b"[1, 2]"
RECORD_BODY = b"<changes><a>1</a></changes>"
#: 超过 `MCHECKER_UPLOAD_MAX_MB=1` 一点点的正文，用于 `too_large`。
BIG_XML = b"<config><big>" + b"x" * (1024 * 1024 + 64) + b"</big></config>"

RESULT_KEYS = {"name", "status", "label", "reason", "at", "archived"}
#: `update_status.json` 里每项的字段（§10.2 第 7 步，不带 `name`——名字就是键）。
STATUS_FIELDS = {"status", "label", "reason", "at", "archived"}
#: 直接写进 `update_status.json` 的一条「最近结果」（改名/删除的生命周期用例用它当哨兵）。
SAMPLE_RESULT = {
    "status": "success",
    "label": "已更新",
    "reason": "已归档旧版本 config_20261018.xml",
    "at": "2026-10-18 10:00:00",
    "archived": "config_20261018.xml",
}
#: 中文更新源名（C26 允许「建」，N1 起改名与它同一字符集——围绕它的用例都钉这一条）。
CN = "机型配置.xml"
#: §10.2 与卡面契约允许的 status 取值。
ALLOWED_STATUSES = {
    "success",
    "unchanged",
    "rejected",
    "download_failed",
    "skipped",
    "too_large",
    "not_found",
}


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
        if "location" in route:
            self._reply(302, {"Location": route["location"]}, b"")
            return
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
    """测试用服务器句柄：只给 URL 与「服务端实际收到过哪些路径」。"""

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
    from app.core import downloader

    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    started = []

    def make(routes: Dict[str, Any]) -> _Server:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        httpd.daemon_threads = True
        httpd.routes = routes
        httpd.requests = []
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        started.append((httpd, thread))
        return _Server(httpd, httpd.server_address[1])

    yield make

    for httpd, thread in started:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


@pytest.fixture()
def sleeps(monkeypatch) -> List[float]:
    """重试退避记录器：不真睡，但可以断言「重试了几次 / 根本没重试」。"""
    from app.core import downloader

    recorded: List[float] = []
    monkeypatch.setattr(downloader, "_sleep", recorded.append)
    return recorded


@pytest.fixture()
def pid() -> str:
    from app.core import device_models

    return device_models.add_model("updates")


# ---------------------------------------------------------------------------
# 助手
# ---------------------------------------------------------------------------


def _seed(
    target_pid: str,
    *,
    name: str = NAME,
    content: Optional[bytes] = XML_V1,
    url: str = "",
    record_url: str = "",
) -> None:
    """在 `target_pid` 里备好当前文件与**单条**映射项。"""
    from app.core import storage

    with storage.use_profile(target_pid):
        if content is not None:
            storage.save_config_file(name, content)
        storage.save_config_mapping([{"name": name, "url": url, "record_url": record_url}])


def _seed_mapping(target_pid: str, entries: List[Dict[str, str]]) -> None:
    from app.core import storage

    with storage.use_profile(target_pid):
        storage.save_config_mapping(entries)


def _current(target_pid: str, name: str = NAME) -> Optional[bytes]:
    from app.core import storage

    with storage.use_profile(target_pid):
        return storage.load_config_file(name)


def _archive_names(target_pid: str, name: str = NAME) -> List[str]:
    """归档目录里的归档名（`.diff.json` 是派生摘要，不算历史版本）。"""
    from app.core import storage

    with storage.use_profile(target_pid):
        directory = os.path.join(storage.get_archive_root_dir(), name)
        if not os.path.isdir(directory):
            return []
        return sorted(f for f in os.listdir(directory) if not f.endswith(".diff.json"))


def _sha(data: Optional[bytes]) -> Optional[str]:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _status_of(target_pid: str, name: str = NAME) -> Optional[Dict[str, Any]]:
    from app.core import storage

    with storage.use_profile(target_pid):
        return storage.load_update_status().get(name)


def _parts(target_pid: str) -> Dict[str, int]:
    from app.services import revision

    return revision.snapshot(target_pid)["parts"]


def _mapping_file(target_pid: str) -> str:
    from app.core import storage

    return os.path.join(storage.get_profile_data_dir(target_pid), "config_mapping.json")


def _file_sha(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _sources_api(target_pid: str, body: Any):
    """POST /sources 的快捷入口（真实服务器，用完即关）。"""
    with make_server() as server:
        return as_role(server, "deployer").json("POST", "/api/v1/p/%s/sources" % target_pid, body)


def _source_path(target_pid: str, name: str) -> str:
    """`/sources/{name}`：前端按 C26 发 `encodeURIComponent(name)`，HTTP 层再 unquote 回来。"""
    return "/api/v1/p/%s/sources/%s" % (target_pid, urllib.parse.quote(name))


def _status_file(target_pid: str) -> Dict[str, Any]:
    from app.core import storage

    with storage.use_profile(target_pid):
        return storage.load_update_status()


#: 竞争线程完成写入的等待上限。坏实现（读在锁外）里它几毫秒就回来；好实现里锁还扣在
#: 主线程手上，它**必然**超时——两条路径都能让主线程确定性继续，不需要猜时序。
RACE_WINDOW = 0.5


def _race_after_first_load(monkeypatch, owner, attr: str, competitor) -> List[threading.Thread]:
    """把 `owner.attr`（读函数）换成「读到结果后立刻拉起 `competitor` 线程并等它」。

    §5.4「读-改-写在同一把锁内」只能被**另一个线程**证伪：同线程注入会重入同一把 per-pid
    `RLock`，锁内锁外都写得下去，看不出区别。返回拉起过的线程，调用方 join 之后再断言。
    """
    real = getattr(owner, attr)
    done = threading.Event()
    threads: List[threading.Thread] = []
    fired: List[bool] = []

    def run_competitor() -> None:
        try:
            competitor()
        finally:
            done.set()

    def hooked(*args: Any, **kwargs: Any):
        value = real(*args, **kwargs)
        if not fired:
            fired.append(True)
            thread = threading.Thread(target=run_competitor, daemon=True)
            threads.append(thread)
            thread.start()
            done.wait(RACE_WINDOW)
        return value

    monkeypatch.setattr(owner, attr, hooked)
    return threads


# ---------------------------------------------------------------------------
# 1. §10.2 七步（直接调用服务函数）
# ---------------------------------------------------------------------------


class TestUpdateSourceSevenSteps:
    def test_valid_xml_replaces_current_and_archives_old(self, pid, servers):
        from app.services import updates

        srv = servers({"/new.xml": {"body": XML_V2}})
        _seed(pid, url=srv.url("/new.xml"))
        before = _parts(pid)

        result = updates.update_source(pid, NAME, "deployer")

        assert result["status"] == "success"
        assert result["label"] == "已更新"
        assert result["archived"] is not None
        assert _current(pid) == XML_V2
        assert _archive_names(pid) == [result["archived"]]
        # R5：字节真的变了才递增 files
        assert _parts(pid)["files"] == before["files"] + 1

    def test_same_content_is_unchanged_without_archive_or_bump(self, pid, servers):
        """R5 守卫用例：内容与当前一致 → 不写、不归档、`files` 不递增。"""
        from app.services import updates

        srv = servers({"/new.xml": {"body": XML_V1}})
        _seed(pid, url=srv.url("/new.xml"))
        before = _parts(pid)

        result = updates.update_source(pid, NAME, "deployer")

        assert result["status"] == "unchanged"
        assert result["reason"] == "内容与当前版本一致，未归档"
        assert result["archived"] is None
        assert _archive_names(pid) == []
        assert _current(pid) == XML_V1
        assert _parts(pid) == before  # files 与 settings 都没动

    def test_unchanged_still_records_check_time(self, pid, servers):
        from app.services import updates

        srv = servers({"/new.xml": {"body": XML_V1}})
        _seed(pid, url=srv.url("/new.xml"))

        result = updates.update_source(pid, NAME, "deployer")

        status = _status_of(pid)
        assert status is not None and status["status"] == "unchanged"
        assert status["at"] == result["at"]

    def test_html_error_page_is_rejected_and_current_bytes_unchanged(self, pid, servers):
        """UC-32：HTTP 200 的错误页不能替换当前版本——拦住它的是第 4 步的根指纹。"""
        from app.services import updates

        srv = servers(
            {"/new.xml": {"body": HTML_PAGE, "headers": {"Content-Type": "text/html"}}}
        )
        _seed(pid, url=srv.url("/new.xml"))
        sha_before = _sha(_current(pid))
        before = _parts(pid)

        result = updates.update_source(pid, NAME, "deployer")

        assert result["status"] == "rejected"
        assert result["label"] == "已拒绝"
        assert "根节点由 config 变为 html" in result["reason"]
        assert "可能下载到了错误页面，已拒绝替换" in result["reason"]
        assert _sha(_current(pid)) == sha_before
        assert _archive_names(pid) == []
        assert _parts(pid) == before
        assert _status_of(pid)["status"] == "rejected"

    def test_other_xml_root_is_rejected(self, pid, servers):
        from app.services import updates

        srv = servers({"/new.xml": {"body": XML_OTHER_ROOT}})
        _seed(pid, url=srv.url("/new.xml"))

        result = updates.update_source(pid, NAME, "deployer")

        assert result["status"] == "rejected"
        assert "根节点由 config 变为 profile" in result["reason"]
        assert _current(pid) == XML_V1

    def test_json_root_object_to_array_is_rejected(self, pid, servers):
        from app.services import updates

        srv = servers({"/list.json": {"body": JSON_ARRAY}})
        _seed(pid, name="list.json", content=JSON_OBJECT, url=srv.url("/list.json"))

        result = updates.update_source(pid, "list.json", "deployer")

        assert result["status"] == "rejected"
        assert "根节点由 object 变为 array" in result["reason"]
        assert _current(pid, "list.json") == JSON_OBJECT

    def test_unparsable_content_is_rejected(self, pid, servers):
        from app.services import updates

        srv = servers({"/new.xml": {"body": JUNK}})
        _seed(pid, url=srv.url("/new.xml"))
        before = _parts(pid)

        result = updates.update_source(pid, NAME, "deployer")

        assert result["status"] == "rejected"
        assert result["reason"] == "下载内容不是有效的 XML/JSON"
        assert _current(pid) == XML_V1
        assert _parts(pid) == before

    def test_oversized_body_is_too_large_without_retry(self, pid, servers, monkeypatch):
        from app.services import updates

        monkeypatch.setenv("MCHECKER_UPLOAD_MAX_MB", "1")
        srv = servers({"/new.xml": {"body": BIG_XML}})
        _seed(pid, url=srv.url("/new.xml"))
        sha_before = _sha(_current(pid))

        result = updates.update_source(pid, NAME, "deployer")

        assert result["status"] == "too_large"
        assert "超过下载上限" in result["reason"]
        assert _sha(_current(pid)) == sha_before
        assert _archive_names(pid) == []
        assert srv.requests == ["/new.xml"]  # 确定性失败不重试

    def test_redirect_to_metadata_address_is_download_failed(self, pid, servers):
        from app.services import updates

        srv = servers({"/new.xml": {"location": "http://169.254.169.254/latest/meta-data/"}})
        _seed(pid, url=srv.url("/new.xml"))

        result = updates.update_source(pid, NAME, "deployer")

        assert result["status"] == "download_failed"
        assert result["label"] == "下载失败"
        # 第二跳在校验阶段就被拒：服务器只记到首跳
        assert srv.requests == ["/new.xml"]
        assert _current(pid) == XML_V1

    def test_http_error_status_is_download_failed_after_retries(self, pid, servers, sleeps):
        from app.services import updates

        srv = servers({"/new.xml": {"status": 503, "body": b"unavailable"}})
        _seed(pid, url=srv.url("/new.xml"))

        result = updates.update_source(pid, NAME, "deployer")

        assert result["status"] == "download_failed"
        assert "HTTP 状态 503" in result["reason"]
        assert sleeps == [1.0, 2.0]  # 默认 3 次尝试，退避 1 s、2 s
        assert _current(pid) == XML_V1

    def test_missing_url_is_skipped_without_downloading(self, pid, servers):
        from app.services import updates

        srv = servers({"/new.xml": {"body": XML_V2}})
        _seed(pid, url="")
        before = _parts(pid)

        result = updates.update_source(pid, NAME, "deployer")

        assert result["status"] == "skipped"
        assert result["reason"] == "无下载链接"
        assert srv.requests == []
        assert _current(pid) == XML_V1
        assert _parts(pid) == before

    def test_name_not_in_mapping_is_not_found_and_records_nothing(self, pid, servers):
        from app.services import updates

        srv = servers({"/new.xml": {"body": XML_V2}})
        _seed(pid, url="")

        result = updates.update_source(pid, "ghost.xml", "deployer")

        assert result["status"] == "not_found"
        assert _status_of(pid, "ghost.xml") is None  # 不往 update_status 里塞野名字
        assert srv.requests == []

    def test_illegal_name_is_not_found_and_never_touches_paths(self, pid):
        """§11.2：非法名（路径分隔符）先判「映射里没有」，绝不拼进 `configs/`。"""
        from app.services import updates

        result = updates.update_source(pid, "../../etc/passwd", "deployer")

        assert result["status"] == "not_found"
        assert _status_of(pid, "../../etc/passwd") is None

    def test_first_download_without_current_file_succeeds_without_archive(self, pid, servers):
        from app.services import updates

        srv = servers({"/new.xml": {"body": XML_V2}})
        _seed(pid, content=None, url=srv.url("/new.xml"))

        result = updates.update_source(pid, NAME, "deployer")

        assert result["status"] == "success"
        assert result["archived"] is None
        assert _current(pid) == XML_V2

    def test_unparsable_current_file_skips_root_check_and_is_replaced(self, pid, servers):
        """当前文件本身就坏了：没有可比的根节点，更新要能把它救回来。"""
        from app.services import updates

        srv = servers({"/new.xml": {"body": XML_V2}})
        _seed(pid, content=JUNK, url=srv.url("/new.xml"))

        result = updates.update_source(pid, NAME, "deployer")

        assert result["status"] == "success"
        assert _current(pid) == XML_V2

    def test_lock_rechecks_current_bytes_before_replacing(self, pid, servers, monkeypatch):
        """第 6 步：机型锁内重读确认未变；期间被改过就放弃替换（不写、不归档）。"""
        from app.services import updates

        srv = servers({"/new.xml": {"body": XML_V2}})
        _seed(pid, url=srv.url("/new.xml"))
        real = updates._current_bytes
        calls: List[str] = []

        def racing(name: str) -> Optional[bytes]:
            calls.append(name)
            return XML_OTHER_ROOT if len(calls) >= 2 else real(name)

        monkeypatch.setattr(updates, "_current_bytes", racing)

        result = updates.update_source(pid, NAME, "deployer")

        assert len(calls) == 2  # 锁外一次、锁内一次
        assert result["status"] == "rejected"
        assert "本次更新期间" in result["reason"]
        assert _current(pid) == XML_V1
        assert _archive_names(pid) == []

    def test_json_content_replaces_json_current(self, pid, servers):
        from app.services import updates

        srv = servers({"/list.json": {"body": b'{"a": 2}'}})
        _seed(pid, name="list.json", content=JSON_OBJECT, url=srv.url("/list.json"))

        result = updates.update_source(pid, "list.json", "deployer")

        assert result["status"] == "success"
        assert _current(pid, "list.json") == b'{"a": 2}'

    def test_result_shape_is_exactly_the_contract(self, pid, servers):
        from app.services import updates

        srv = servers({"/new.xml": {"body": XML_V2}})
        _seed(pid, url=srv.url("/new.xml"))

        result = updates.update_source(pid, NAME, "someone")

        assert set(result) == RESULT_KEYS
        assert result["name"] == NAME
        assert result["status"] in ALLOWED_STATUSES
        assert isinstance(result["archived"], (str, type(None)))
        # 落盘的 status 项不带 name（名字就是键）
        assert set(_status_of(pid)) == STATUS_FIELDS

    def test_signature_has_no_force_parameter(self):
        """卡面：不允许「强制更新」——没有任何入参能绕过第 4/5 步。"""
        import inspect

        from app.services import updates

        assert list(inspect.signature(updates.update_source).parameters) == [
            "pid",
            "name",
            "actor",
        ]
        assert list(inspect.signature(updates.update_all).parameters) == ["pid", "actor", "task"]
        assert list(inspect.signature(updates.refresh_records).parameters) == [
            "pid",
            "name",
            "actor",
        ]


# ---------------------------------------------------------------------------
# 2. update_all 与 refresh_records 骨架
# ---------------------------------------------------------------------------


class _FakeTask:
    """卡 6 的 `Task` 最小替身（§7.10 的 `progress` / `result.items`）。"""

    def __init__(self) -> None:
        self.progress = {"done": 0, "total": 0}
        self.result: Dict[str, Any] = {"items": []}


class TestUpdateAll:
    def test_iterates_every_mapped_name(self, pid, servers):
        from app.core import storage
        from app.services import updates

        srv = servers({"/a.xml": {"body": XML_V2}})
        with storage.use_profile(pid):
            storage.save_config_file("a.xml", XML_V1)
            storage.save_config_file("b.xml", XML_V1)
        _seed_mapping(
            pid,
            [
                {"name": "a.xml", "url": srv.url("/a.xml"), "record_url": ""},
                {"name": "b.xml", "url": "", "record_url": ""},
            ],
        )

        summary = updates.update_all(pid, "system")

        statuses = {item["name"]: item["status"] for item in summary["items"]}
        assert statuses == {"a.xml": "success", "b.xml": "skipped"}
        assert summary["last_run"]["success"] == 1
        assert summary["last_run"]["skipped"] == 1

    def test_writes_schedule_last_run_in_profile(self, pid, servers):
        from app.core import storage
        from app.services import updates

        srv = servers({"/new.xml": {"body": XML_V2}})
        _seed(pid, url=srv.url("/new.xml"))
        with storage.use_profile(pid):
            storage.save_schedule({"enabled": True, "interval_hours": 6})

        summary = updates.update_all(pid, "system")

        with storage.use_profile(pid):
            schedule = storage.load_schedule()
        assert schedule["enabled"] is True  # 其余字段保持不变
        assert schedule["interval_hours"] == 6
        assert schedule["last_run"] == summary["last_run"]
        assert set(schedule["last_run"]) == {"at", "success", "unchanged", "failed", "skipped"}
        assert schedule["last_run"]["success"] == 1

    def test_last_run_counts_failed_and_unchanged(self, pid, servers):
        from app.core import storage
        from app.services import updates

        srv = servers({"/same.xml": {"body": XML_V1}, "/bad.xml": {"body": JUNK}})
        with storage.use_profile(pid):
            storage.save_config_file("same.xml", XML_V1)
            storage.save_config_file("bad.xml", XML_V1)
        _seed_mapping(
            pid,
            [
                {"name": "same.xml", "url": srv.url("/same.xml"), "record_url": ""},
                {"name": "bad.xml", "url": srv.url("/bad.xml"), "record_url": ""},
            ],
        )

        summary = updates.update_all(pid, "system")

        assert summary["last_run"]["unchanged"] == 1
        assert summary["last_run"]["failed"] == 1
        assert summary["last_run"]["success"] == 0

    def test_calls_refresh_records_only_when_record_url_present(self, pid, servers):
        from app.core import storage
        from app.services import updates

        srv = servers({"/a.xml": {"body": XML_V2}, "/records.xml": {"body": RECORD_BODY}})
        with storage.use_profile(pid):
            storage.save_config_file("a.xml", XML_V1)
        _seed_mapping(
            pid,
            [
                {
                    "name": "a.xml",
                    "url": srv.url("/a.xml"),
                    "record_url": srv.url("/records.xml"),
                }
            ],
        )

        summary = updates.update_all(pid, "system")

        assert [item["name"] for item in summary["items"]] == ["a.xml", "a.xml"]
        assert summary["items"][0]["status"] == "success"
        assert summary["items"][1]["status"] == "skipped"
        # 本卡不下载修改记录：服务器只记到配置那一条
        assert srv.requests == ["/a.xml"]

    def test_task_progress_and_items_are_filled(self, pid, servers):
        from app.services import updates

        srv = servers({"/a.xml": {"body": XML_V2}})
        _seed_mapping(pid, [{"name": "a.xml", "url": srv.url("/a.xml"), "record_url": ""}])

        task = _FakeTask()
        summary = updates.update_all(pid, "system", task)

        assert task.progress == {"done": 1, "total": 1}
        assert task.result["items"] == summary["items"]

    def test_update_all_does_not_bump_settings(self, pid, servers):
        """调度副作用留给卡 6（§10.1「结束时按类型 bump files/settings」），这里不重复递增。"""
        from app.services import updates

        srv = servers({"/a.xml": {"body": XML_V2}})
        _seed(pid, url=srv.url("/a.xml"))
        before = _parts(pid)

        updates.update_all(pid, "system")

        after = _parts(pid)
        assert after["settings"] == before["settings"]
        assert after["files"] == before["files"] + 1  # 由 update_source 递增

    def test_empty_mapping_writes_zeroed_last_run(self, pid):
        from app.core import storage
        from app.services import updates

        _seed_mapping(pid, [])

        summary = updates.update_all(pid, "system")

        assert summary["items"] == []
        with storage.use_profile(pid):
            assert storage.load_schedule()["last_run"]["success"] == 0


class TestReadModifyWriteLocks:
    """§5.4「读-改-写必须在同一把锁内」：本卡两处自己写的读-改-写各有用例（评审 I1、I2）。

    两条都用 `_race_after_first_load` 在「读」与「写」之间放进一个**真线程**的竞争写——
    同线程注入会重入同一把 per-pid `RLock`，锁内锁外都改得动，测不出丢更新。
    """

    def test_update_all_last_run_does_not_clobber_a_concurrent_schedule_edit(
        self, pid, servers, monkeypatch
    ):
        """I1：写 `schedule.json.last_run` 期间部署者改 `interval_hours` 不能丢（§5.4、§7.9）。"""
        from app.core import storage
        from app.services import updates

        srv = servers({"/new.xml": {"body": XML_V2}})
        _seed(pid, url=srv.url("/new.xml"))
        with storage.use_profile(pid):
            storage.save_schedule({"enabled": True, "interval_hours": 24})

        def deployer_changes_interval() -> None:
            # 卡 7 的 `PUT /schedule` 形状：读整份 → 改一个字段 → 写整份。
            with storage.use_profile(pid), storage.profile_write(pid):
                schedule = storage.load_schedule()
                schedule["interval_hours"] = 99
                storage.save_schedule(schedule)

        rivals = _race_after_first_load(
            monkeypatch, storage, "load_schedule", deployer_changes_interval
        )
        summary = updates.update_all(pid, "system")
        for thread in rivals:
            thread.join(timeout=5)

        with storage.use_profile(pid):
            schedule = storage.load_schedule()
        assert schedule["interval_hours"] == 99  # 部署者的新值没被这次读到的旧副本盖掉
        assert schedule["last_run"] == summary["last_run"]  # last_run 也照常写入
        assert schedule["enabled"] is True

    def test_two_sources_finishing_concurrently_both_keep_their_results(self, pid, monkeypatch):
        """I2：`update_status.json` 的合并在锁外读就会互相丢项（卡 6 放开并发后必踩）。"""
        from app.core import storage
        from app.services import updates

        _seed_mapping(
            pid,
            [
                {"name": "a.xml", "url": "", "record_url": ""},
                {"name": "b.xml", "url": "", "record_url": ""},
            ],
        )

        def other_source_finishes() -> None:
            updates.update_source(pid, "b.xml", "tester")

        rivals = _race_after_first_load(
            monkeypatch, storage, "load_update_status", other_source_finishes
        )
        first = updates.update_source(pid, "a.xml", "tester")
        for thread in rivals:
            thread.join(timeout=5)

        assert first["status"] == "skipped"
        assert _status_of(pid, "a.xml") is not None
        # 不是「后写覆盖前写」：两条结果都要在最终文件里
        assert _status_of(pid, "b.xml") is not None
        assert set(_status_file(pid)) == {"a.xml", "b.xml"}


class TestRefreshRecordsSkeleton:
    def test_missing_mapping_is_not_found(self, pid):
        from app.services import updates

        result = updates.refresh_records(pid, "ghost.xml", "deployer")

        assert set(result) == RESULT_KEYS
        assert result["status"] == "not_found"

    def test_without_record_url_is_skipped(self, pid):
        from app.services import updates

        _seed(pid, url="http://127.0.0.1:1/nope.xml")

        result = updates.refresh_records(pid, NAME, "deployer")

        assert result["status"] == "skipped"
        assert result["reason"] == "未配置修改记录链接"

    def test_with_record_url_is_skipped_and_downloads_nothing(self, pid, servers):
        """主体属卡 9：签名与骨架先就位，但一条字节都不下载。"""
        from app.services import updates

        srv = servers({"/records.xml": {"body": RECORD_BODY}})
        _seed(pid, url="", record_url=srv.url("/records.xml"))

        result = updates.refresh_records(pid, NAME, "deployer")

        assert result["status"] == "skipped"
        assert "M3-09" in result["reason"]
        assert srv.requests == []

    def test_does_not_write_update_status(self, pid):
        """`update_status.json` 装的是「更新源最近结果」，记录拉取不往里写。"""
        from app.services import updates

        _seed(pid, url="", record_url="http://127.0.0.1:1/records.xml")

        updates.refresh_records(pid, NAME, "deployer")

        assert _status_of(pid) is None


# ---------------------------------------------------------------------------
# 3. storage：新增持久化文件 update_status.json
# ---------------------------------------------------------------------------


class TestUpdateStatusStorage:
    def test_path_is_resolved_per_profile_at_call_time(self, pid):
        from app.core import storage

        path = storage.get_update_status_file(pid)
        assert os.path.basename(path) == "update_status.json"
        assert os.path.dirname(path) == storage.get_profile_data_dir(pid)
        assert path != storage.get_update_status_file("default")

    def test_path_follows_active_profile_when_arg_omitted(self, pid):
        from app.core import storage

        with storage.use_profile(pid):
            assert storage.get_update_status_file() == storage.get_update_status_file(pid)

    def test_no_module_level_constant_for_the_new_file(self):
        """卡面要求：路径在调用期求值，不新增模块级常量（否则夹具搬不走）。"""
        from app.core import storage

        stale = [
            name
            for name, value in vars(storage).items()
            if isinstance(value, str) and "update_status" in value
        ]
        assert stale == []

    def test_load_defaults_to_empty_dict(self, pid):
        from app.core import storage

        with storage.use_profile(pid):
            assert storage.load_update_status() == {}

    def test_save_then_load_round_trip_is_atomic(self, pid):
        from app.core import storage

        with storage.use_profile(pid):
            storage.save_update_status({NAME: {"status": "success", "at": "2026-10-18 00:00:00"}})
            loaded = storage.load_update_status()
            directory = storage.get_profile_data_dir(pid)
            leftovers = [f for f in os.listdir(directory) if f.endswith(".tmp")]

        assert loaded[NAME]["status"] == "success"
        assert leftovers == []  # 原子写不留临时文件

    def test_non_dict_file_is_corrupt_not_silently_emptied(self, pid):
        from app.core import storage
        from app.core.errors import CorruptDataError

        with storage.use_profile(pid):
            path = storage.get_update_status_file()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[]")
            with pytest.raises(CorruptDataError):
                storage.load_update_status()

    def test_is_not_copied_when_copying_profile(self, pid):
        """§7.2：运行期数据不随机型复制（新增持久化文件必须点名验证这条）。"""
        from app.core import device_models, storage

        assert "update_status.json" not in storage.COPY_PROFILE_FILES

        _seed(pid, url="")
        with storage.use_profile(pid):
            storage.save_update_status({NAME: {"status": "success"}})
        target = device_models.add_model("copy-target", copy_from=pid)

        assert os.path.exists(storage.get_update_status_file(target)) is False
        assert os.path.exists(storage.get_update_status_file(pid)) is True
        # 映射仍然随机型复制（对照：证明这条断言不是「什么都没复制」）
        with storage.use_profile(target):
            assert storage.load_config_mapping() == [{"name": NAME, "url": "", "record_url": ""}]


# ---------------------------------------------------------------------------
# 4. /sources CRUD（UC-31、UC-32；四角色）
# ---------------------------------------------------------------------------


class TestSourcesApi:
    def test_get_is_readable_by_every_role(self):
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        with make_server() as server:
            from app.core import storage

            with storage.use_profile(target):
                storage.save_update_status({NAME: {"status": "success", "label": "已更新"}})
            for role in ("visitor", "member", "admin", "deployer"):
                status, _, body = as_role(server, role).json(
                    "GET", "/api/v1/p/%s/sources" % target
                )
                assert status == 200, role
                item = body["items"][0]
                assert set(item) == {
                    "name",
                    "url",
                    "record_url",
                    "local_updated_at",
                    "last_result",
                }
                assert item["url"] == "http://example.invalid/a.xml"
                assert item["record_url"] == ""
                assert item["local_updated_at"] is not None
                assert item["last_result"]["status"] == "success"

    def test_get_reports_null_when_never_downloaded(self):
        target = seed_roles("p1")
        _seed(target, content=None, url="")
        with make_server() as server:
            _, _, body = as_role(server, "visitor").json("GET", "/api/v1/p/%s/sources" % target)
            assert body["items"][0]["local_updated_at"] is None
            assert body["items"][0]["last_result"] is None

    def test_read_only_get_does_not_bump_any_part(self):
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        before = _parts(target)
        with make_server() as server:
            as_role(server, "visitor").json("GET", "/api/v1/p/%s/sources" % target)
        assert _parts(target) == before

    def test_create_by_deployer_returns_201_and_bumps_settings(self):
        target = seed_roles("p1")
        before = _parts(target)
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "POST",
                "/api/v1/p/%s/sources" % target,
                {"name": "new.xml", "url": "http://example.invalid/new.xml", "record_url": ""},
            )
        assert status == 201
        assert body["item"] == {
            "name": "new.xml",
            "url": "http://example.invalid/new.xml",
            "record_url": "",
        }
        from app.core import storage

        with storage.use_profile(target):
            assert storage.load_config_mapping() == [body["item"]]
        assert _parts(target)["settings"] == before["settings"] + 1

    def test_create_allows_chinese_filename(self):
        """C26：`name` 走 `validate_filename`，允许中文（旧的 ASCII 白名单已废止）。"""
        target = seed_roles("p1")
        status, _, body = _sources_api(
            target, {"name": "机型配置.xml", "url": "http://example.invalid/x.xml"}
        )
        assert status == 201
        assert body["item"]["name"] == "机型配置.xml"

    @pytest.mark.parametrize(
        "payload,code,field",
        [
            ({"name": "", "url": "http://a/x.xml"}, "INVALID_INPUT", "name"),
            ({"name": "sub/a.xml", "url": "http://a/x.xml"}, "INVALID_INPUT", "name"),
            ({"name": "a.xml"}, "INVALID_INPUT", "url"),
            ({"name": "a.xml", "url": "ftp://a/x.xml"}, "INVALID_INPUT", "url"),
            ({"name": "a.xml", "url": "javascript:alert(1)"}, "INVALID_INPUT", "url"),
            (
                {"name": "a.xml", "url": "http://a/x.xml", "record_url": "ftp://a/r.xml"},
                "INVALID_INPUT",
                "record_url",
            ),
        ],
    )
    def test_create_field_errors(self, payload, code, field):
        target = seed_roles("p1")
        status, _, body = _sources_api(target, payload)
        assert status == 400
        assert body["error"]["code"] == code
        assert body["error"]["detail"]["field"] == field

    def test_create_by_non_deployers_is_403_and_mapping_bytes_unchanged(self):
        """卡面验收：非部署者 POST → 403 且 `config_mapping.json` sha 不变（四角色夹具）。"""
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        path = _mapping_file(target)
        sha_before = _file_sha(path)
        before = _parts(target)
        with make_server() as server:
            for role in ("visitor", "member", "admin"):
                status, _, body = as_role(server, role).json(
                    "POST",
                    "/api/v1/p/%s/sources" % target,
                    {"name": "evil.xml", "url": "http://evil.invalid/x.xml"},
                )
                assert status == 403, role
                assert body["error"]["code"] == "FORBIDDEN"
        assert _file_sha(path) == sha_before
        assert _parts(target) == before  # 失败写不递增修订号

    def test_create_without_csrf_header_is_403(self):
        target = seed_roles("p1")
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "POST",
                "/api/v1/p/%s/sources" % target,
                {"name": "a.xml", "url": "http://example.invalid/a.xml"},
                with_csrf=False,
            )
        assert status == 403
        assert body["error"]["code"] == "FORBIDDEN"

    def test_create_duplicate_is_409(self):
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        path = _mapping_file(target)
        sha_before = _file_sha(path)
        before = _parts(target)
        status, _, body = _sources_api(
            target, {"name": NAME, "url": "http://example.invalid/b.xml"}
        )
        assert status == 409
        assert body["error"]["code"] == "FILE_EXISTS"
        assert body["error"]["detail"]["field"] == "name"
        assert _file_sha(path) == sha_before
        assert _parts(target) == before

    def test_rename_migrates_every_reference(self):
        """UC-31：改名 + `migrate_files` 后，5 份 JSON 与历史/记录目录里的文件名全部跟随。"""
        target = seed_roles("p1")
        _seed(
            target,
            url="http://example.invalid/a.xml",
            record_url="http://example.invalid/r.xml",
        )
        from app.core import dltool, storage

        with storage.use_profile(target):
            storage.save_config_file(NAME, XML_V2)  # 第一次保存产生一个归档
            storage.add_favorite("/config/a", "a", "1", NAME)
            storage.add_binding("g1", [{"file": NAME, "path": "/config/a"}])
            storage.add_edit_remark(
                source_file=NAME,
                node_key="0.0",
                node_path="/config/a",
                node_label="a",
                original_value="1",
                proposed_value="2",
                node_type="string",
                tree_type="xml",
                actor_ip="10.0.0.1",
                actor_name="张三",
            )
            storage.save_record_file(NAME, RECORD_BODY, source_url="http://example.invalid/r.xml")
            config = dltool.load_config()
            config["items"]["pp_energy"]["binding"] = {
                "source_file": NAME,
                "variable_path": "/config/a",
                "field_map": {},
            }
            dltool.save_config(config)
            archive_before = sorted(
                f
                for f in os.listdir(os.path.join(storage.get_archive_root_dir(), NAME))
                if not f.endswith(".diff.json")
            )
            record_before = sorted(os.listdir(os.path.join(storage.get_records_root_dir(), NAME)))

        before = _parts(target)
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "PATCH",
                "/api/v1/p/%s/sources/%s" % (target, NAME),
                {
                    "name": "renamed.xml",
                    "url": "http://example.invalid/new.xml",
                    "record_url": "http://example.invalid/record.xml",
                    "migrate_files": True,
                },
            )
        assert status == 200
        assert body["item"]["name"] == "renamed.xml"
        assert body["item"]["record_url"] == "http://example.invalid/record.xml"
        assert _parts(target)["settings"] == before["settings"] + 1

        with storage.use_profile(target):
            # 五份 JSON：映射、收藏、绑定、建议、DL 绑定
            assert [m["name"] for m in storage.load_config_mapping()] == ["renamed.xml"]
            assert [f["source_file"] for f in storage.load_favorites()] == ["renamed.xml"]
            assert [
                v["file"] for b in storage.load_bindings() for v in b["variables"]
            ] == ["renamed.xml"]
            assert [i["source_file"] for i in storage.load_edit_remarks()] == ["renamed.xml"]
            assert (
                dltool.load_config()["items"]["pp_energy"]["binding"]["source_file"]
                == "renamed.xml"
            )
            # 历史（归档）与修改记录目录跟着走，旧名字彻底消失
            assert sorted(
                f
                for f in os.listdir(os.path.join(storage.get_archive_root_dir(), "renamed.xml"))
                if not f.endswith(".diff.json")
            ) == archive_before
            assert sorted(
                os.listdir(os.path.join(storage.get_records_root_dir(), "renamed.xml"))
            ) == record_before
            assert os.path.isdir(os.path.join(storage.get_archive_root_dir(), NAME)) is False
            assert os.path.isdir(os.path.join(storage.get_records_root_dir(), NAME)) is False
            assert _sha(storage.load_config_file("renamed.xml")) == _sha(XML_V2)
            assert storage.load_config_file(NAME) is None

    @pytest.mark.parametrize("migrate_files", [True, False])
    def test_rename_moves_last_result_to_the_new_name(self, migrate_files):
        """I3：`update_status.json` 的键也要跟着改名走。

        不搬有三重后果：UC-31 的「状态列」在改名那一刻恒为 `null`、旧键成为永久孤儿，
        而删掉同名重建时新源会继承上一个同名的历史。`migrate_files=False` 只搬本地文件，
        **映射名照样改了**，所以状态键两种情况下都必须跟随。
        """
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        from app.core import storage

        with storage.use_profile(target):
            storage.save_update_status({NAME: SAMPLE_RESULT})

        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "PATCH",
                _source_path(target, NAME),
                {
                    "name": "renamed.xml",
                    "url": "http://example.invalid/new.xml",
                    "migrate_files": migrate_files,
                },
            )
            assert status == 200, body
            _, _, got = as_role(server, "visitor").json("GET", "/api/v1/p/%s/sources" % target)

        assert [item["name"] for item in got["items"]] == ["renamed.xml"]
        assert got["items"][0]["last_result"] == SAMPLE_RESULT  # 改名后状态列不失真
        # 旧键不留下：文件只增不减的话，孤儿键迟早会被同名重建捡走
        assert _status_file(target) == {"renamed.xml": SAMPLE_RESULT}

    def test_rename_by_admin_is_403(self):
        """改名也是部署者的事：管理员（非部署者）同样 403（§4.2）。"""
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        path = _mapping_file(target)
        sha_before = _file_sha(path)
        with make_server() as server:
            status, _, body = as_role(server, "admin").json(
                "PATCH",
                "/api/v1/p/%s/sources/%s" % (target, NAME),
                {"name": "renamed.xml", "url": "http://example.invalid/x.xml"},
            )
        assert status == 403
        assert body["error"]["code"] == "FORBIDDEN"
        assert _file_sha(path) == sha_before

    def test_rename_without_migrate_files_keeps_local_file(self):
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "PATCH",
                "/api/v1/p/%s/sources/%s" % (target, NAME),
                {
                    "name": "renamed.xml",
                    "url": "http://example.invalid/new.xml",
                    "migrate_files": False,
                },
            )
        assert status == 200
        assert body["item"]["name"] == "renamed.xml"
        assert _current(target) == XML_V1  # 文件留在旧名字上
        assert _current(target, "renamed.xml") is None

    def test_rename_migrates_when_migrate_files_omitted(self):
        """`migrate_files` 缺省按 True（与 `storage.update_config_mapping` 同默认）：
        改名把本地文件与历史留在旧名下，是更难发现的数据孤儿。"""
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "PATCH",
                "/api/v1/p/%s/sources/%s" % (target, NAME),
                {"name": "renamed.xml", "url": "http://example.invalid/new.xml"},
            )
        assert status == 200
        assert _current(target) is None
        assert _current(target, "renamed.xml") == XML_V1

    def test_rename_to_existing_name_is_409(self):
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        from app.core import storage

        with storage.use_profile(target):
            storage.save_config_file("taken.xml", XML_OTHER_ROOT)
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "PATCH",
                "/api/v1/p/%s/sources/%s" % (target, NAME),
                {
                    "name": "taken.xml",
                    "url": "http://example.invalid/x.xml",
                    "migrate_files": True,
                },
            )
        assert status == 409
        assert body["error"]["code"] == "FILE_EXISTS"
        assert body["error"]["detail"]["field"] == "name"
        # 什么都没动：映射与两侧文件都保持原样
        assert _current(target) == XML_V1
        assert _current(target, "taken.xml") == XML_OTHER_ROOT
        with storage.use_profile(target):
            assert [m["name"] for m in storage.load_config_mapping()] == [NAME]

    def test_rename_rolls_back_when_migration_fails(self):
        """迁移中途失败 → 共用的 `update_config_mapping` 自带 `_rollback` 把已移动的搬回来。"""
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        from app.core import storage
        from app.services import updates

        with storage.use_profile(target):
            storage.add_favorite("/config/a", "a", "1", NAME)

        def boom(*args, **kwargs):
            raise RuntimeError("模拟迁移失败")

        original = storage._migrate_config_references
        storage._migrate_config_references = boom
        try:
            with pytest.raises(RuntimeError):
                updates.rename_source(
                    target, NAME, "renamed.xml", "http://example.invalid/new.xml", "", True
                )
        finally:
            storage._migrate_config_references = original

        with storage.use_profile(target):
            assert [m["name"] for m in storage.load_config_mapping()] == [NAME]
            assert _sha(storage.load_config_file(NAME)) == _sha(XML_V1)
            assert storage.load_config_file("renamed.xml") is None
            assert [f["source_file"] for f in storage.load_favorites()] == [NAME]

    def test_rename_unknown_name_is_404(self):
        target = seed_roles("p1")
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "PATCH",
                "/api/v1/p/%s/sources/ghost.xml" % target,
                {"name": "ghost2.xml", "url": "http://example.invalid/x.xml"},
            )
        assert status == 404
        assert body["error"]["code"] == "NOT_FOUND"

    def test_rename_bad_url_is_400(self):
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "PATCH",
                "/api/v1/p/%s/sources/%s" % (target, NAME),
                {"name": NAME, "url": "example.invalid/a.xml"},
            )
        assert status == 400
        assert body["error"]["detail"]["field"] == "url"

    def test_rename_a_chinese_named_source_to_another_chinese_name(self):
        """N1 / INH-1912：中文源建得出来也必须改得了名（§11.2「文件名一律 `validate_filename`」）。

        改名两侧的名字都过同一把 `fsutil.validate_filename`，与 `create_source` 同一字符集；
        中文不是特例：映射项、本地产物、`update_status.json` 的键都要跟着搬。
        """
        target = seed_roles("p1")
        renamed = "机型配置新名.xml"
        _seed(target, name=CN, url="http://example.invalid/a.xml")
        from app.core import storage

        with storage.use_profile(target):
            storage.save_update_status({CN: SAMPLE_RESULT})
        before = _parts(target)
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "PATCH",
                _source_path(target, CN),
                {
                    "name": renamed,
                    "url": "http://example.invalid/new.xml",
                    "record_url": "",
                },
            )
        assert status == 200, body
        assert body["item"]["name"] == renamed
        with storage.use_profile(target):
            assert [m["name"] for m in storage.load_config_mapping()] == [renamed]
        # 本地产物跟着名字走（缺省 `migrate_files=True`）
        assert _current(target, CN) is None
        assert _current(target, renamed) == XML_V1
        # 状态键同样跟着搬，否则改名那一刻 `last_result` 恒 null
        assert _status_file(target) == {renamed: SAMPLE_RESULT}
        assert _parts(target)["settings"] == before["settings"] + 1

    def test_ascii_source_can_be_renamed_to_a_chinese_name(self):
        """改名两侧都过 `fsutil.validate_filename`（N1 收敛 C26），ASCII 源可改成中文名。"""
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "PATCH",
                "/api/v1/p/%s/sources/%s" % (target, NAME),
                {"name": CN, "url": "http://example.invalid/x.xml"},
            )
        assert status == 200, body
        assert body["item"]["name"] == CN
        assert _current(target, CN) == XML_V1
        assert _current(target) is None

    def test_rename_a_chinese_named_source_keeps_the_url_only_case_working(self):
        """中文源「名字不动、只改 url」同样是 200（N1 收敛后 M0-07 那半条边界的翻转）。

        旧实现里 `update_config_mapping` 对**新旧两个名字**都过 ASCII 白名单，所以这条路也 400；
        上面两条只钉住了「目标名是中文」与「源名是中文」，这里补上真正改名的对照：
        名字没变时映射项与修订号照常更新，本地文件仍留在原名上。
        """
        target = seed_roles("p1")
        _seed(target, name=CN, url="http://example.invalid/a.xml")
        before = _parts(target)
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "PATCH",
                _source_path(target, CN),
                {"name": CN, "url": "http://example.invalid/new.xml", "record_url": ""},
            )
        assert status == 200, body
        assert body["item"]["url"] == "http://example.invalid/new.xml"
        from app.core import storage

        with storage.use_profile(target):
            assert [m["name"] for m in storage.load_config_mapping()] == [CN]
        assert _current(target, CN) == XML_V1
        assert _parts(target)["settings"] == before["settings"] + 1

    def test_rename_target_with_path_separator_is_still_400(self):
        """收敛只统一字符集，**不放松边界**：带路径分隔符的目标名照旧 400 且不写任何东西。

        中文放行后必须还有人守着「`configs/` 之外拼不出路径」这一条，否则下一次放宽
        就是无声的越界。
        """
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        path = _mapping_file(target)
        sha_before = _file_sha(path)
        before = _parts(target)
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "PATCH",
                _source_path(target, NAME),
                {"name": "sub/a.xml", "url": "http://example.invalid/x.xml"},
            )
        assert status == 400
        assert body["error"]["code"] == "INVALID_INPUT"
        assert body["error"]["detail"]["field"] == "name"
        assert _file_sha(path) == sha_before
        assert _parts(target) == before

    def test_rename_value_error_translation_leaves_a_warning(self, caplog, monkeypatch):
        """小项：`storage` 冒出的裸 `ValueError` 仍要翻成 400，并带上原消息留一条 WARNING。

        `except ValueError` 包的是整个 `update_config_mapping` 调用；没有这条日志，净化内部
        真出别的 `ValueError` 也会被一路掩成「用户文件名写错了」，bug 就没痕迹了。
        N1 把净化收敛到 `fsutil` 之后（中文名不再抛 `ValueError`），这里改用桩触发那条
        仍然存在的裸 `ValueError`（`update_config_mapping` 的「下载链接不能为空」），
        钉的是**翻译与日志**，不是旧白名单。
        """
        from app.core import storage
        from app.core.errors import AppError, INVALID_INPUT
        from app.services import updates

        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")

        def boom(*args, **kwargs):
            raise ValueError("下载链接不能为空")

        monkeypatch.setattr(storage, "update_config_mapping", boom)
        with caplog.at_level(logging.WARNING, logger="app.services.updates"):
            with pytest.raises(AppError) as info:
                updates.rename_source(target, NAME, CN, "http://example.invalid/x.xml", "")

        assert info.value.code == INVALID_INPUT
        # 原消息带着走（不改写成一句笼统的「文件名不合法」）
        assert info.value.message == "下载链接不能为空"
        assert info.value.detail.get("field") == "name"
        assert "下载链接不能为空" in caplog.text
        assert CN in caplog.text  # 日志记到改名两侧的名字，事后能定位是哪一次调用

    def test_chinese_named_source_can_still_be_updated_and_deleted(self, servers):
        """400 处置只贴在改名那一步：中文名的 `update_source` 与 `DELETE` 不能被带坏（评审补测）。"""
        from app.services import updates

        target = seed_roles("p1")
        srv = servers({"/new.xml": {"body": XML_V2}})
        _seed(target, name=CN, url=srv.url("/new.xml"))

        result = updates.update_source(target, CN, "deployer")
        assert result["status"] == "success"
        assert _current(target, CN) == XML_V2
        assert _status_of(target, CN)["status"] == "success"

        with make_server() as server:
            status, _, body = as_role(server, "deployer").json("DELETE", _source_path(target, CN))
        assert status == 200
        assert body == {"removed": True}
        with make_server() as server:
            _, _, got = as_role(server, "visitor").json("GET", "/api/v1/p/%s/sources" % target)
        assert got["items"] == []
        assert _current(target, CN) == XML_V2  # §7.9：只删映射，不删文件

    def test_delete_removes_mapping_only(self):
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        from app.core import storage

        with storage.use_profile(target):
            storage.save_config_file(NAME, XML_V2)  # 产生一个归档
        before = _parts(target)
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "DELETE", "/api/v1/p/%s/sources/%s" % (target, NAME)
            )
        assert status == 200
        assert body == {"removed": True}
        assert _parts(target)["settings"] == before["settings"] + 1
        # 只删映射：当前文件与归档都还在
        assert _current(target) == XML_V2
        assert _archive_names(target) != []
        with storage.use_profile(target):
            assert storage.load_config_mapping() == []
            assert os.path.isdir(os.path.join(storage.get_archive_root_dir(), NAME)) is True

    def test_recreated_source_does_not_inherit_the_deleted_ones_result(self):
        """I3：删除要**清掉** `update_status.json` 的键——新同名源不该显示一条没发生过的更新。

        §7.9 只说 DELETE「只删映射，不删文件」；状态文件按名字记的是「这个源最近一次结果」，
        留着旧键的话，删掉再用同一个名字 `POST` 回来的新源会直接继承旧历史。
        """
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        from app.core import storage

        with storage.use_profile(target):
            storage.save_update_status({NAME: SAMPLE_RESULT})

        with make_server() as server:
            deployer = as_role(server, "deployer")
            assert deployer.json("DELETE", _source_path(target, NAME))[2] == {"removed": True}
            status, _, _body = deployer.json(
                "POST",
                "/api/v1/p/%s/sources" % target,
                {"name": NAME, "url": "http://example.invalid/a.xml", "record_url": ""},
            )
            assert status == 201
            _, _, got = as_role(server, "visitor").json("GET", "/api/v1/p/%s/sources" % target)

        assert [item["name"] for item in got["items"]] == [NAME]
        assert got["items"][0]["last_result"] is None
        assert _status_file(target) == {}
        assert _current(target) == XML_V1  # 删历史结果不等于删文件

    def test_delete_unknown_name_removes_nothing_and_does_not_bump(self):
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        before = _parts(target)
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "DELETE", "/api/v1/p/%s/sources/ghost.xml" % target
            )
        assert status == 200
        assert body == {"removed": False}
        assert _parts(target) == before

    def test_delete_by_non_deployer_is_403(self):
        target = seed_roles("p1")
        _seed(target, url="http://example.invalid/a.xml")
        path = _mapping_file(target)
        sha_before = _file_sha(path)
        with make_server() as server:
            status, _, _body = as_role(server, "member").request(
                "DELETE", "/api/v1/p/%s/sources/%s" % (target, NAME)
            )
        assert status == 403
        assert _file_sha(path) == sha_before


# ---------------------------------------------------------------------------
# 5. 分层与契约
# ---------------------------------------------------------------------------


class TestLayeringAndContract:
    def test_service_does_not_import_http(self):
        import app.services.updates as updates

        offenders = [
            name
            for name in dir(updates)
            if name in ("http", "RequestContext", "Response", "Router", "json_body", "authz")
        ]
        assert offenders == []

    def test_handler_delegates_to_service_and_never_touches_storage(self):
        import app.server.api_sources as api_sources

        assert "storage" not in dir(api_sources)
        assert "updates" in dir(api_sources)

    def test_routes_are_registered(self):
        from app.config import Settings
        from app.server.router import build_router

        router = build_router(Settings())
        for method, path in (
            ("GET", "/api/v1/p/default/sources"),
            ("POST", "/api/v1/p/default/sources"),
            ("PATCH", "/api/v1/p/default/sources/a.xml"),
            ("DELETE", "/api/v1/p/default/sources/a.xml"),
        ):
            handler, _params = router.match(method, path)
            assert handler is not None, path
        _, params = router.match("PATCH", "/api/v1/p/default/sources/a.xml")
        assert params == {"pid": "default", "name": "a.xml"}

    def test_part_constant_matches_contract(self):
        from app.services import updates

        assert updates.PART == "files"

    def test_mock_uses_the_same_status_and_error_vocabulary(self):
        """R19：mock 与后端契约同 PR 对齐——「同名已存在」统一 409 `FILE_EXISTS`。

        只截取 `/sources` 那一段来断言：mock 里别处（如机型重名）不属本卡契约。
        """
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "app",
            "web",
            "assets",
            "js",
            "mock",
            "mock-api.js",
        )
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        start = source.index('route("GET", "/p/{pid}/sources"')
        end = source.index('route("POST", "/p/{pid}/tasks"')
        block = source[start:end]
        assert block.count('fail(409, "FILE_EXISTS"') == 2
        assert 'fail(409, "INVALID_INPUT"' not in block
        assert "local_updated_at" in block  # GET 的字段名与后端一致
        # DELETE 的响应体与后端一致（removed，不是 deleted）
        assert "removed: removed" in block
        assert "deleted: true" not in block
        # 改名迁移跟随的引用与后端同源：绑定与 DL 绑定也要跟着走
        assert "b.variables.forEach" in block
        assert "it.binding.source_file === a.name" in block
