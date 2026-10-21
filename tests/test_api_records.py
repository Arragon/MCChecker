"""修改记录拉取与文件内查看（卡 9 · M3-09 / INH-1894；规范 §10.3、§5.3、§7.4、§9.8.6、UC-30）。

三层用例各证一件事：

1. `services/updates.py::refresh_records` 的主体（**直接调函数**）：下载 → 与**最新一条**
   记录比 sha256 → 相同就 `unchanged` 且**不新建文件**（C13）；不同才按 §5.3 命名保存。
   「不新建」一律用**文件数 + 字节 sha256** 证明，不用状态码证明——200 的 `unchanged`
   和「偷偷写了一份再删掉」在这种断言下长得一模一样。
2. 修订号口径（R5 / 卡 8 手递手 §3）：只有**真的多了一份记录文件**才 `files` +1；
   `unchanged`、下载失败、被策略拒绝、超限都不递增，`tasks`/`settings` 一分不涨。
   `FINISH_PARTS` 里 `refresh_records` 必须是空元组（递增在 service 层）。钉它的是三条，
   各管一层，缺一条就漏一种改法：钉**表值**的那条只是值比较，改表的人会顺手改它；
   只有 `test_runner_finish_bumps_no_business_part_on_an_identical_pull` 走得到 `_finish`，
   所以它是「顺手把 `files` 加进表里」唯一会红的行为判据，别当重复用例删。
3. 真实 `MCServer` + 四角色（UC-30 端到端）：`POST /tasks` 起拉取（只有部署者），
   拉完 `GET /records` 列表增加、「查看」用 `version=record:<名称>` 在文件标签内打开、
   「与上一条对比」两端都走 `record:`；**打开页面不产生任何对记录服务器的请求**。

下载一律打本地 `http.server`（127.0.0.1 随机端口），`getproxies` 打桩成 `{}`，重试退避
打桩成记录器——与卡 5 / 卡 6 同一套做法（`docs/know-how.md` 第 4 节：打桩 `urlopen`
进不去，还会把逐跳重定向复核与分块限流一起屏蔽）。
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

import pytest

from authz_fixtures import as_role, make_server, seed_roles
from test_api_sources import RACE_WINDOW, _race_after_first_load

NAME = "config.xml"
CONFIG_BYTES = b"<config><a>1</a></config>"
RECORD_A = b"<changes><e>1</e></changes>"
RECORD_B = b"<changes><e>2</e></changes>"
#: §10.3：记录内容**不要求可解析**，所以这种字节也必须能存下来、能在查看时走原文下载。
RECORD_JUNK = "2026-10-18 张三 改了 param_13 的值，不是 XML 也不是 JSON {{{".encode("utf-8")
#: 超过 `MCHECKER_UPLOAD_MAX_MB=1` 一点点的记录，用于 `too_large`。
BIG_RECORD = b"<changes>" + b"x" * (1024 * 1024 + 64) + b"</changes>"

RESULT_KEYS = {"name", "status", "label", "reason", "at", "archived"}
#: §5.3 的记录名：`record_<YYYYMMDD>_<HHMMSS><ext>`（同秒冲突再追加 `_2`…）。
RECORD_NAME_RE = re.compile(r"^record_\d{8}_\d{6}(_\d+)?\.([A-Za-z0-9]{1,9})$")


# ---------------------------------------------------------------------------
# 本地记录服务器（正文可以在用例中途换掉：UC-30 要的正是「远端改了内容」）
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
        if isinstance(route, bytes):
            route = {"body": route}
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
    """测试用服务器句柄：给 URL、「服务端收到过哪些路径」，以及「把远端内容换掉」。"""

    def __init__(self, httpd, port: int) -> None:
        self.httpd = httpd
        self.port = port

    @property
    def requests(self) -> List[str]:
        return self.httpd.requests

    def url(self, path: str = "/records.xml") -> str:
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def set_body(self, path: str, body: bytes) -> None:
        """换掉某个路径的正文（= 远端在这两次拉取之间改了内容）。"""
        self.httpd.routes[path] = {"body": body}


@pytest.fixture()
def servers(monkeypatch):
    """按需起本地服务器，用例结束统一关掉。"""
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

    return device_models.add_model("records")


# ---------------------------------------------------------------------------
# 助手
# ---------------------------------------------------------------------------


def _seed(target_pid: str, *, record_url: str = "", url: str = "") -> None:
    """备好当前配置文件与**单条**映射项（`GET /records` 要求 `configs/<name>` 存在）。"""
    from app.core import storage

    with storage.use_profile(target_pid):
        storage.save_config_file(NAME, CONFIG_BYTES)
        storage.save_config_mapping([{"name": NAME, "url": url, "record_url": record_url}])


def _records_root(target_pid: str, name: str = NAME) -> str:
    """记录目录路径，**不**创建它（`storage.get_record_dir` 会 makedirs）。"""
    from app.core import storage

    return os.path.join(storage.get_records_root_dir(target_pid), name)


def _record_files(target_pid: str, name: str = NAME) -> List[str]:
    """目录里的记录文件名清单——「没新建文件」只认这个，不认状态码。"""
    directory = _records_root(target_pid, name)
    if not os.path.isdir(directory):
        return []
    return sorted(f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f)))


def _record_blobs(target_pid: str, name: str = NAME) -> Dict[str, str]:
    """每个记录文件的 sha256（键是文件名）：证明「同一份字节、同一份文件」。"""
    directory = _records_root(target_pid, name)
    out: Dict[str, str] = {}
    for entry in _record_files(target_pid, name):
        with open(os.path.join(directory, entry), "rb") as fh:
            out[entry] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _parts(target_pid: str) -> Dict[str, int]:
    from app.services import revision

    return revision.snapshot(target_pid)["parts"]


def _pull(target_pid: str, actor: str = "deployer") -> Dict[str, Any]:
    from app.services import updates

    return updates.refresh_records(target_pid, NAME, actor)


# ---------------------------------------------------------------------------
# 1. §10.3 主体：拉取、去重、命名
# ---------------------------------------------------------------------------


class TestPullAndDedup:
    def test_first_pull_writes_exactly_one_file_with_the_fetched_bytes(self, pid, servers):
        from app.core import storage

        srv = servers({"/records.xml": {"body": RECORD_A}})
        _seed(pid, record_url=srv.url("/records.xml"))

        result = _pull(pid)

        assert set(result) == RESULT_KEYS
        assert result["status"] == "success"
        assert result["label"] == "已更新"  # 与 `updates.LABELS` 同口径，不再是 mock 的「已拉取」
        assert srv.requests == ["/records.xml"]
        files = _record_files(pid)
        assert len(files) == 1, "首次拉取必须且只能多一份记录文件"
        assert RECORD_NAME_RE.match(files[0]), files[0]
        # 字节就是下载到的那份，一个字符都不许多
        with storage.use_profile(pid):
            assert storage.load_record_file(NAME, files[0]) == RECORD_A
        assert result["reason"].endswith(files[0])

    def test_identical_pull_downloads_again_but_creates_no_file(self, pid, servers):
        """C13 的核心：第二次仍然**下载**（远端变了没有只能靠下载才知道），但比完 sha 相同
        就不新建文件。`files == 2` 与「一条新字节都没有」两句要同时成立：只断状态码的话，
        「下载后照样写一份」这种实现也能拿到 200/`unchanged`。"""
        srv = servers({"/records.xml": {"body": RECORD_A}})
        _seed(pid, record_url=srv.url("/records.xml"))

        first = _pull(pid)
        blobs_before = _record_blobs(pid)
        second = _pull(pid)

        assert first["status"] == "success"
        assert second["status"] == "unchanged"
        assert second["label"] == "无变化"
        assert second["archived"] is None
        assert srv.requests == ["/records.xml", "/records.xml"], "去重发生在下载**之后**"
        assert _record_files(pid) == list(blobs_before), "相同内容不得新建文件"
        assert _record_blobs(pid) == blobs_before, "也不得改写已有那份的字节"

    def test_uc30_two_changed_pulls_then_one_identical(self, pid, servers):
        """UC-30 的账：两条**不同**内容 → 2 份文件；再拉一次相同内容 → 还是 2 份。

        卡面那句「断言下载次数 = 2（不是 3）」与 §10.3 冲突：规范写的是
        「下载 → 若与最新一条记录 sha256 相同 → unchanged」，判重必须先有那份字节，
        下载器也没有条件请求（ETag/If-Modified-Since）。所以按 SPEC 落的账是
        **下载 3 次、文件 2 份**；「不是 3」落在文件数上。见卡报告的 mock/规格差异一节。
        """
        srv = servers({"/records.xml": {"body": RECORD_A}})
        _seed(pid, record_url=srv.url("/records.xml"))

        assert _pull(pid)["status"] == "success"
        srv.set_body("/records.xml", RECORD_B)
        assert _pull(pid)["status"] == "success"
        files_after_two = _record_files(pid)
        assert _pull(pid)["status"] == "unchanged"

        assert len(srv.requests) == 3, "三次拉取都真的发了请求"
        assert len(files_after_two) == 2, "远端变过两次 ⇒ 两份记录"
        assert _record_files(pid) == files_after_two, "相同内容那次没有把条数变成 3"

    def test_two_changed_pulls_never_overwrite_each_other(self, pid, servers):
        """同秒两次不同内容：`storage.save_record_file` 的独占创建给出 `_2`，
        两份都在。不断具体后缀（取决于跑没跨过秒），只断「名字互不相同 + 条数 2」。"""
        srv = servers({"/records.xml": {"body": RECORD_A}})
        _seed(pid, record_url=srv.url("/records.xml"))

        _pull(pid)
        srv.set_body("/records.xml", RECORD_B)
        _pull(pid)

        files = _record_files(pid)
        assert len(files) == 2 and len(set(files)) == 2
        assert len(set(_record_blobs(pid).values())) == 2, "两份记录字节必须互不相同"

    def test_unparsable_record_content_is_still_saved(self, pid, servers):
        """§10.3「记录内容不要求可解析」：解析不了的照样落盘（查看时走原文下载）。

        与 §10.2 第 3/4 步的区别就在这里——配置更新会因为「不是有效 XML/JSON」或
        「根节点变了」而 `rejected`，记录拉取**没有** `rejected` 这个取值。
        """
        srv = servers({"/changes.txt": {"body": RECORD_JUNK}})
        _seed(pid, record_url=srv.url("/changes.txt"))

        result = _pull(pid)

        assert result["status"] == "success"
        assert _record_files(pid) and all(RECORD_NAME_RE.match(f) for f in _record_files(pid))

    def test_missing_mapping_is_not_found_without_a_request(self, pid, servers):
        srv = servers({"/records.xml": {"body": RECORD_A}})
        from app.services import updates

        result = updates.refresh_records(pid, "ghost.xml", "deployer")

        assert result["status"] == "not_found"
        assert srv.requests == []
        assert _record_files(pid) == []

    def test_same_second_records_are_ordered_two_different_ways(self, pid, servers):
        """现状哨兵（**不是**裁定）：去重基准与记录列表对「最新一条」的排序在同秒内会分叉。

        两个排序各有出处，别再按「只有一处」读它们（`_latest_record_bytes` 的旧 docstring
        就是这么写错的）：

        * 去重基准 `storage.list_record_versions`：排序键是 **mtime 截断到秒**的字符串，
          同秒之内由「文件名的逆字典序」定先后（`sorted(listdir, reverse=True)` 之后
          再做一次稳定排序留下的次序）。
        * 界面「最新一条」= `GET /files/{name}/records` 走的 `services/history.py`
          `_list_versions`：排序键是 **float mtime**，不看文件名，还额外跳过 `.diff.json`。

        于是同一秒落盘的 `record_….xml` 与 `record_….log`（改过一次 `record_url` 的扩展名
        就是这个形状）会各挑各的「最新」：名字里 `x` > `l`，所以基准拿 `.xml`，而 mtime
        更晚的 `.log` 才是列表顶行。后果不是数据损坏，是**通知口径**——远端内容其实等于
        列表顶行那次，去重基准看到的是另一份，于是判成「变了」：多写一份记录并 `files` +1，
        给全部客户端弹一次「文件已更新」。反向同理（等于 `.xml` 而顶行是 `.log` 时该新建的
        没新建）。

        为什么本卡只钉现状不修：要对齐就得把 `storage.list_record_versions` 的排序键换成
        float mtime（它同时被旧 NiceGUI 页面 `app/pages/records.py` 用，M3 期间那页不动），
        那是 `core/storage` 的行为改动，超出 M3-09 的文件面；把排序抄进 `updates.py` 则会
        造出**第三个**排序出处。两条都比「留着这条已知的、已钉住的、只影响通知的分叉」更贵。
        真要统一（含 `documents._count_files` 对 `records/` 不跳 `.diff.json` 的同一族问题）
        得单开一张卡，判据用这条用例的两侧顺序一起改。

        名字里的时间戳跟着 mtime 一起造，所以这条不依赖本机时区，也不依赖
        `save_record_file` 的挂钟（那两份是手工铺的「同秒历史」，模拟的是记录目录里
        已经存在这种字节的情形）。
        """
        from datetime import datetime

        from app.core import fsutil, storage
        from app.services import history, updates

        # 远端正文等于「界面顶行」那份（RECORD_B），去重基准却是 RECORD_A。
        # 先 `_seed`：`configs/<name>` 存在 `history.records` 才肯列（§7.4 的 404 判据）。
        srv = servers({"/records.log": {"body": RECORD_B}})
        _seed(pid, record_url=srv.url("/records.log"))

        # 同一秒的两个 mtime：`.xml` 写得早、`.log` 写得晚。
        moment = 1_700_000_000.0
        stamp = datetime.fromtimestamp(moment).strftime("record_%Y%m%d_%H%M%S")
        baseline_name = "%s.xml" % stamp  # 名字逆字典序在前 ⇒ 去重基准挑中它
        newest_name = "%s.log" % stamp  # mtime 更晚 ⇒ 界面列表顶行是它
        with storage.use_profile(pid):
            directory = storage.get_record_dir(NAME)
            fsutil.atomic_write_bytes(os.path.join(directory, baseline_name), RECORD_A)
            fsutil.atomic_write_bytes(os.path.join(directory, newest_name), RECORD_B)
            os.utime(os.path.join(directory, baseline_name), (moment + 0.2, moment + 0.2))
            os.utime(os.path.join(directory, newest_name), (moment + 0.8, moment + 0.8))

        # 1. 两处排序读出的「最新一条」确实不同（这条就是那句被写错的 docstring 的反证）。
        with storage.use_profile(pid):
            assert storage.list_record_versions(NAME)[0]["filename"] == baseline_name
            assert updates._latest_record_bytes(NAME) == RECORD_A
        assert history.records(pid, NAME)["items"][0]["filename"] == newest_name

        # 2. 后果就落在这两份字节上。
        before = _parts(pid)

        result = _pull(pid)

        assert result["status"] == "success", "同秒分叉的可见代价：本该 unchanged 的一判成变了"
        assert len(_record_files(pid)) == 3, "多写了第三份记录文件"
        assert _parts(pid)["files"] == before["files"] + 1, "跟着多发了一次「文件已更新」"


class TestNaming:
    """§5.3：扩展名取自 `record_url` 的**路径**（含点在内 ≤10 字符），否则 `.txt`。

    判据只有一个出处：`storage.save_record_file`。本卡不另起一套命名，也不许在这里
    写第二份「怎么取扩展名」的逻辑——所以下面每种 URL 都只断**落盘的文件名**。
    """

    @pytest.mark.parametrize(
        "path,expected_suffix",
        [
            ("/records.xml", ".xml"),
            ("/records.changes.xml", ".xml"),  # splitext 取**最后**一段
            ("/records.log", ".log"),
            ("/changes.json", ".json"),
            ("/no-extension", ".txt"),
            ("/records?", ".txt"),  # 叶子以 `.` 开头之外的情形：没有扩展名
            ("/record.0123456789", ".txt"),  # 含点 12 字符 > 10 ⇒ 回落
            ("/record.xml?rev=7", ".xml"),  # query 不参与
        ],
    )
    def test_extension_follows_the_record_url(self, pid, servers, path, expected_suffix):
        # 服务器按「不含 query 的路径」记账（`_Handler` 就是这么查的），所以 routes 的键
        # 要剥掉 query，URL 本身保留 query 才能证明「扩展名只看路径」。
        srv = servers({path.split("?")[0]: {"body": RECORD_A}})
        _seed(pid, record_url=srv.url(path))

        assert _pull(pid)["status"] == "success"

        files = _record_files(pid)
        assert len(files) == 1
        assert files[0].endswith(expected_suffix), files[0]

    def test_record_url_with_userinfo_does_not_leak_into_the_filename(self, pid, servers):
        """带凭据的 `record_url` 照样取到同一个路径，而落盘名里只有时间戳与扩展名（§11.5）。

        三条 `"…" not in name` 单独摆在这里**永远不会红**——名字由
        `storage.save_record_file` 用挂钟 + 路径扩展名拼出来，凭据进不去是构造保证，不是
        这条用例的证明。真正的判据是下面这几条，它们都会红：

        * **形状**：`RECORD_NAME_RE` 只接受 `record_<8 位>_<6 位>(_<n>)?.<≤9 个字母数字>`，
          所以「把 URL 的某一段拼进文件名」的实现（哪怕只是多一个点、一段目录名）当场红；
          `.xml` 后缀单独钉一次，证明扩展名取自**路径叶子**而不是 authority。
        * **请求路径**：`srv.requests` 必须是 `/secret/records.xml`——凭据写进 authority
          之后 URL 仍然被原样取用，这条同时挡住「为了去凭据把 URL 改坏」。
        * 外加 `reason` 只带落盘名：它是任务条目里给别的客户端看的文案。
        """
        srv = servers({"/secret/records.xml": {"body": RECORD_A}})
        url = srv.url("/secret/records.xml").replace("127.0.0.1", "user:pass@127.0.0.1")
        _seed(pid, record_url=url)

        result = _pull(pid)

        assert result["status"] == "success"
        assert srv.requests == ["/secret/records.xml"], "带 userinfo 的 URL 仍按原路径取数"
        files = _record_files(pid)
        assert len(files) == 1, files
        name = files[0]
        assert RECORD_NAME_RE.match(name), "落盘名只能有时间戳与扩展名：%s" % name
        assert name.endswith(".xml"), "扩展名来自路径叶子，不是 authority"
        assert result["reason"].endswith(name), result["reason"]
        assert "user:pass" not in result["reason"] and "pass" not in name


# ---------------------------------------------------------------------------
# 2. 修订号与「没变就不叫醒别人」
# ---------------------------------------------------------------------------


class TestRevisionAccounting:
    def test_changed_pull_bumps_files_once(self, pid, servers):
        srv = servers({"/records.xml": {"body": RECORD_A}})
        _seed(pid, record_url=srv.url("/records.xml"))
        before = _parts(pid)

        _pull(pid)

        parts = _parts(pid)
        assert parts["files"] == before["files"] + 1, "多了一份记录文件才通知一次"
        assert parts["settings"] == before["settings"], "记录属于文件域，不动 settings"

    def test_identical_pull_bumps_nothing(self, pid, servers):
        """R5 的同一笔账：拉到 0 条新记录**不许**叫醒全部客户端。"""
        srv = servers({"/records.xml": {"body": RECORD_A}})
        _seed(pid, record_url=srv.url("/records.xml"))
        _pull(pid)
        before = _parts(pid)

        assert _pull(pid)["status"] == "unchanged"

        assert _parts(pid) == before, "unchanged 一个分量都不递增"

    def test_http_404_pull_bumps_nothing_and_creates_no_file(self, pid, servers, sleeps):
        """失败写不递增（全局约束）：远端 404 ⇒ `download_failed`、零文件、零递增。"""
        srv = servers({})
        _seed(pid, record_url=srv.url("/records.xml"))
        before = _parts(pid)

        result = _pull(pid)

        assert result["status"] == "download_failed"
        assert result["archived"] is None
        assert _record_files(pid) == []
        assert _parts(pid) == before
        assert sleeps, "DOWNLOAD_FAILED 走下载器的重试路径（本用例只断它没被绕过）"

    def test_over_limit_record_is_too_large_and_writes_nothing(self, pid, servers, monkeypatch):
        """`TOO_LARGE` 与 `DOWNLOAD_FAILED` 是两种状态（前者是确定性失败、不重试），
        但都不许落半个文件、都不递增。"""
        monkeypatch.setenv("MCHECKER_UPLOAD_MAX_MB", "1")  # 与卡 5 的 too_large 用例同一口径
        srv = servers({"/big.xml": {"body": BIG_RECORD}})
        _seed(pid, record_url=srv.url("/big.xml"))
        before = _parts(pid)

        result = _pull(pid)

        assert result["status"] == "too_large"
        assert result["label"] == "超过上限"
        assert _record_files(pid) == []
        assert _parts(pid) == before

    def test_finish_parts_table_leaves_files_to_the_service_layer(self, pid):
        """`FINISH_PARTS["refresh_records"]` 必须是空元组（卡 8 手递手 §3 / R5）。

        这张表是「任务成功结束就无条件递增」，而绝大多数拉取是 `unchanged`；把 `files`
        放进去就等于每次拉取都发一次「文件已更新」横幅。递增的真实位置在
        `updates.refresh_records`（只有真的写了文件才 +1），上面几条用例钉的就是那一条。
        本条只钉**表本身**（一个值比较），行为由下一条 `test_runner_...` 钉——只有那条
        走得到 `_finish`，别把两条并成一条。
        """
        from app.services import tasks

        assert tasks.FINISH_PARTS["refresh_records"] == ()
        assert tasks.FINISH_PARTS["update_all"] == ("settings",)
        # 互斥面一个字没动：`refresh_records` 仍然按**机型**与另两类互斥（卡 6 复审 F2）。
        assert tasks._CONFLICTING_TYPES["refresh_records"] == ("update_source", "update_all")

    def test_runner_finish_bumps_no_business_part_on_an_identical_pull(self, servers):
        """走 **runner** 的 `unchanged` 拉取：除 `tasks` 之外，五个修订分量一个都不许多。

        为什么要单独有这条：本类其余用例都直接调 `updates.refresh_records`，而
        `FINISH_PARTS` 只在 `TaskRunner._finish` 里生效——直调 service 的那几条**碰不到
        这张表**。修复轮实测过：往表里塞 `"refresh_records": ("files",)`（变异 D），
        1098 条里只红了一条 `test_finish_parts_table_leaves_files_to_the_service_layer`，
        而它是个**值比较**，下一个把表改回去的人会顺手把它的期望一起改掉。行为必须由这条
        从 `POST /tasks` 一路跑到 `_finish` 的用例来钉，所以别把这条当重复用例删掉。

        表为什么是错的落点（三条都在 `_finish` 的语义里，改之前先读完）：
        ① 表是「任务结束就**无条件**递增」，而 `_finish` 的 `error` 只在任务体抛异常时才
           非 None；条目状态是 `download_failed` / `skipped` / `not_found` 的任务照样按
           `_state_from_items` 收口并**照走** `FINISH_PARTS` ⇒ 失败与跳过也 +1。
        ② §10.3 的绝大多数拉取是 `unchanged`（远端根本没变），每次点「立即拉取」都给全部
           客户端弹一次「文件已更新」，正是卡 5 判过的虚假通知（R5）。
        ③ 规范已经把这一格定在 service 层：§10.1 让 `update_source` 只在字节真的变了时由
           服务递增 `files`，`refresh_records` 的同口径实现就是
           `updates.refresh_records` 末尾那句「多写了一份记录文件才 `bump(pid, PART)`」。
        要「修」的时候把 bump 留在 `updates.py`，**不要**加进 `FINISH_PARTS`。
        `tasks` 是 runner 自己的进度分量（§10.1），它必须动，见下方单独一条断言。
        """
        from app.services import revision

        target = seed_roles("p1")
        srv = servers({"/records.xml": {"body": RECORD_A}})
        _seed(target, record_url=srv.url("/records.xml"))
        # 先建立「最新一条记录」，让 runner 那一次注定是 `unchanged`。
        assert _pull(target)["status"] == "success"
        assert len(_record_files(target)) == 1

        with make_server() as server:
            caller = as_role(server, "deployer")
            before = _parts(target)
            status, _, body = caller.json(
                "POST",
                "/api/v1/p/%s/tasks" % target,
                {"type": "refresh_records", "name": NAME},
            )
            assert status == 202, body
            task = _wait_task(body["task"]["id"])
            after = _parts(target)

        assert task["state"] == "succeeded", task
        assert task["result"]["items"][0]["status"] == "unchanged"
        assert len(srv.requests) == 2, "runner 那一次真的下载了（不是没跑）"
        assert len(_record_files(target)) == 1, "内容相同不得因为走的是 runner 就多写一份"

        moved = {
            part: (before[part], after[part])
            for part in revision.PARTS
            if before[part] != after[part]
        }
        assert set(after) == set(revision.PARTS), "修订分量清单变了：本用例的判据要跟着改"
        # 逐个分量比对（不是只看 `files`）：`favorites`/`remarks`/`settings`/`files` 里
        # 任何一个被这张表带起来都红，并把「从几涨到几」直接打在断言消息里。
        assert set(moved) == {"tasks"}, "除 runner 自己的 tasks 之外一个分量都不许动：%r" % (moved,)
        assert moved["tasks"][1] > moved["tasks"][0], "tasks 不涨说明任务没跑到 `_finish`"


class TestReadModifyWriteLock:
    def test_concurrent_pull_writes_exactly_one_file(self, pid, servers, monkeypatch):
        """§5.4：「读最新一条 → 比 sha → 写新文件」整段必须在同一把机型锁里。

        同线程注入会重入同一把 per-pid RLock，锁内锁外都写得下去，看不出区别，所以竞争方
        必须是**另一个线程**（`docs/know-how.md` 里卡 5 复审 I1/I2 的那条教训）。
        坏实现（读在锁外）：两条线程都读到「还没有记录」，各写一份 ⇒ 2 个文件。
        好实现：竞争线程卡在 `profile_write` 上，`RACE_WINDOW` 必然超时，主线程先写、
        竞争线程进锁后看到的是主线程那份 ⇒ `unchanged`，1 个文件。
        """
        srv = servers({"/records.xml": {"body": RECORD_A}})
        _seed(pid, record_url=srv.url("/records.xml"))
        from app.core import storage

        def rival_pulls_the_same_content() -> None:
            _pull(pid, "rival")

        threads = _race_after_first_load(
            monkeypatch, storage, "list_record_versions", rival_pulls_the_same_content
        )
        try:
            mine = _pull(pid)
            assert mine["status"] == "success"
            for thread in threads:
                thread.join(timeout=10)
                assert not thread.is_alive(), "竞争线程卡在闸门里没散：它会活到夹具撤销之后（H3）"
        finally:
            for thread in threads:
                thread.join(timeout=10)

        assert len(_record_files(pid)) == 1, "并发两次同内容只许写一份"
        assert len(srv.requests) == 2, "两次下载都发了请求（网络 I/O 不在锁内）"


# ---------------------------------------------------------------------------
# 3. 下载策略（卡 8 手递手：不许写第二个取数原语）
# ---------------------------------------------------------------------------


class TestDownloadPolicy:
    def test_allowlist_refuses_the_record_url_without_downloading(self, pid, servers, monkeypatch):
        """`MCHECKER_DOWNLOAD_ALLOW` 对记录这条路同样生效（§11.2）。

        判据是**服务器一条请求都没收到**：策略在连接之前就把目标挡掉了，
        这同时证明本卡用的是 `downloader.fetch` + `default_policy()`，而不是自己
        `urlopen` 了一份（自己取数的话白名单根本不会响）。
        """
        monkeypatch.setenv("MCHECKER_DOWNLOAD_ALLOW", "10.0.0.0/8")
        srv = servers({"/records.xml": {"body": RECORD_A}})
        _seed(pid, record_url=srv.url("/records.xml"))
        before = _parts(pid)

        result = _pull(pid)

        assert result["status"] == "download_failed"
        assert "MCHECKER_DOWNLOAD_ALLOW" in result["reason"]
        assert srv.requests == []
        assert _record_files(pid) == []
        assert _parts(pid) == before

    def test_pull_goes_through_the_shared_policy_object(self, pid, servers, monkeypatch):
        """策略来自 `downloader.default_policy()`，**逐字段相等**，不是「像一份策略就行」。

        只断 `isinstance(...)` + 两个数 `> 0` 的话，任何手搓的 `DownloadPolicy(timeout_s=1,
        retries=1, max_bytes=1)` 都能过——那正好屏蔽掉这条用例唯一要证的字段（`allow` 白名单
        与 `block_loopback` 都来自启动配置）。本卡不写第二个取数原语，判据必须是「拿到的
        那份 == `default_policy()` 现算出来的那份」，环境推导的值也算在内。
        """
        import dataclasses

        from app.core import downloader

        seen: List[Any] = []
        real_fetch = downloader.fetch

        def spy(url, policy):
            seen.append(policy)
            return real_fetch(url, policy)

        monkeypatch.setattr(downloader, "fetch", spy)
        srv = servers({"/records.xml": {"body": RECORD_A}})
        _seed(pid, record_url=srv.url("/records.xml"))

        _pull(pid)

        assert len(seen) == 1
        handed = seen[0]
        expected = downloader.default_policy()
        assert isinstance(handed, downloader.DownloadPolicy)
        # 字段清单取自 dataclass 本身：以后加一个策略字段，这里自动要一起对上。
        names = [field.name for field in dataclasses.fields(downloader.DownloadPolicy)]
        assert names, "DownloadPolicy 不再是 dataclass？判据要换写法"
        for field_name in names:
            assert getattr(handed, field_name) == getattr(expected, field_name), (
                "策略字段 %s 与 default_policy() 不一致：本卡拿到的是 %r，启动配置算出来的是 %r"
                % (
                    field_name,
                    getattr(handed, field_name),
                    getattr(expected, field_name),
                )
            )
        assert handed == expected, "逐字段都对上了还差整体相等？dataclass 比较被改坏了"


# ---------------------------------------------------------------------------
# 4. UC-30 端到端：真实服务器、四角色、文件内查看
# ---------------------------------------------------------------------------


def _wait_task(task_id: str, timeout: float = 10.0) -> Dict[str, Any]:
    """轮询到任务终态并返回 JSON 快照（任务在工作线程里跑，必须等）。

    拿的是 `Task` 对象引用而不是 `runner.get(id)` 的结果：惰性清理会在下一次
    list/get/submit 时把刚结束的任务摘掉（`docs/know-how.md`，M3-07）。
    """
    from app.services import tasks

    runner = tasks.get_runner()
    task = runner.get(task_id)
    assert task is not None, "任务连记录都没排上"
    deadline = time.monotonic() + timeout
    while task.state in ("queued", "running"):
        if time.monotonic() > deadline:
            raise AssertionError("任务未在 %.1f 秒内结束（当前 %s）" % (timeout, task.state))
        time.sleep(0.01)
    return task.to_payload()


class TestRecordsApi:
    def test_deployer_pull_then_list_view_and_compare(self, servers):
        """UC-30 一整条：拉取 → 列表增加 → 记录在同一文件标签内查看 → 与上一条对比。

        前端那一半（`version=record:` 打开、两个对比位预填、结束后刷新列表）已在合并
        过的卡里做完；这里验的是**真后端**能喂饱它：`document`/`raw`/`compare` 三个接口
        都认 `record:<名称>`，且 `GET /records` 给出相邻差异摘要。
        """
        target = seed_roles("p1")
        srv = servers({"/records.xml": {"body": RECORD_A}})
        _seed(target, record_url=srv.url("/records.xml"))

        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _, body = caller.json(
                "POST", "/api/v1/p/%s/tasks" % target, {"type": "refresh_records", "name": NAME}
            )
            assert status == 202, body
            task = _wait_task(body["task"]["id"])
            assert task["state"] == "succeeded"
            assert task["result"]["items"][0]["status"] == "success"

            # 远端改了内容，再拉一次 ⇒ 列表增加一条（同一文件标签内的两个版本）
            srv.set_body("/records.xml", RECORD_B)
            _, _, again = caller.json(
                "POST", "/api/v1/p/%s/tasks" % target, {"type": "refresh_records", "name": NAME}
            )
            second = _wait_task(again["task"]["id"])
            assert second["result"]["items"][0]["status"] == "success"

            _, _, listing = caller.json("GET", "/api/v1/p/%s/files/%s/records" % (target, NAME))
            assert listing["record_url"] == srv.url("/records.xml")
            names = [item["filename"] for item in listing["items"]]
            assert len(names) == 2, names
            assert listing["items"][0]["diff_prev"] is not None, "最新一条要能和上一条比"
            assert listing["items"][1]["diff_prev"] is None, "最早一条没有上一条"

            # 「查看」：结构视图按 record:<名称> 打开同一份文件
            doc_status, _, document = caller.json(
                "GET",
                "/api/v1/p/%s/files/%s/document?version=%s"
                % (target, NAME, "record:" + names[0]),
            )
            assert doc_status == 200
            # §6.3 的形状：`file` 块里带 kind/format，`read_only` 对非 current 版本恒为 true
            assert document["file"]["format"] == "xml"
            assert document["file"]["kind"] == "record"
            assert document["file"]["read_only"] is True
            assert document["file"]["version"] == "record:" + names[0]

            # 原文下载同样是 record 那一份字节
            raw_status, _, raw = caller.request(
                "GET", "/api/v1/p/%s/files/%s/raw?version=%s" % (target, NAME, "record:" + names[0])
            )
            assert raw_status == 200
            assert raw == RECORD_B, "下载到的必须是新拉的那份"

            # 「与上一条对比」：两端都是 record:，服务端要给出结构化 + 逐行差异
            cmp_status, _, compared = caller.json(
                "POST",
                "/api/v1/p/%s/compare" % target,
                {
                    "base": {"file": NAME, "version": "record:" + names[1]},
                    "target": {"file": NAME, "version": "record:" + names[0]},
                },
            )
            assert cmp_status == 200, compared
            assert compared["has_changes"] is True
            assert compared["base"]["name"] == names[1]
            assert compared["unified"]["lines"], "两条记录之间必须有逐行差异"

    def test_unparsable_record_opens_as_raw_only(self, servers):
        """§10.3：解析不了的记录在查看时走原文下载——422 + `raw_available`，不是 500。"""
        target = seed_roles("p1")
        srv = servers({"/changes.txt": {"body": RECORD_JUNK}})
        _seed(target, record_url=srv.url("/changes.txt"))
        _pull(target)
        name = _record_files(target)[0]

        with make_server() as server:
            caller = as_role(server, "member")
            status, _, body = caller.json(
                "GET", "/api/v1/p/%s/files/%s/document?version=%s" % (target, NAME, "record:" + name)
            )
            assert status == 422, body
            assert body["error"]["code"] == "PARSE_FAILED"
            assert body["error"]["detail"]["raw_available"] is True
            raw_status, _, raw = caller.request(
                "GET", "/api/v1/p/%s/files/%s/raw?version=%s" % (target, NAME, "record:" + name)
            )
            assert raw_status == 200
            assert raw == RECORD_JUNK

    @pytest.mark.parametrize("role", ["visitor", "member", "admin"])
    def test_non_deployer_cannot_start_a_pull(self, servers, role):
        """四角色（§4.2、§7.10：`POST /tasks` 属部署者）。

        303/403 之外还要钉三件事：**没发下载**、**没写文件**、**修订号不动**——
        「失败写不递增」在记录拉取这条新路径上同样成立。
        """
        target = seed_roles("p1")
        srv = servers({"/records.xml": {"body": RECORD_A}})
        _seed(target, record_url=srv.url("/records.xml"))
        before = _parts(target)

        with make_server() as server:
            status, _, body = as_role(server, role).json(
                "POST", "/api/v1/p/%s/tasks" % target, {"type": "refresh_records", "name": NAME}
            )

        assert status == 403, role
        assert body["error"]["code"] == "FORBIDDEN"
        assert body["error"]["detail"]["action"] == "tasks_run"
        assert srv.requests == []
        assert _record_files(target) == []
        assert _parts(target) == before

    def test_reads_of_records_are_open_to_every_role(self, servers):
        target = seed_roles("p1")
        srv = servers({"/records.xml": {"body": RECORD_A}})
        _seed(target, record_url=srv.url("/records.xml"))
        _pull(target)
        name = _record_files(target)[0]

        with make_server() as server:
            for role in ("visitor", "member", "admin", "deployer"):
                _, _, listing = as_role(server, role).json(
                    "GET", "/api/v1/p/%s/files/%s/records" % (target, NAME)
                )
                assert len(listing["items"]) == 1, role
                _, _, document = as_role(server, role).json(
                    "GET",
                    "/api/v1/p/%s/files/%s/document?version=%s" % (target, NAME, "record:" + name),
                )
                assert document["file"]["kind"] == "record", role

    def test_opening_the_record_page_never_hits_the_record_server(self, servers):
        """卡面验收：**两个浏览器同时打开记录页，服务端下载次数不随人数增加**。

        页面能发的只有 §7.4/§7.5 那几条读接口（列表、结构、原文、对比、修订号）与
        §7.3 的轮询——它们一个字节都不从远端取。旧版页面自带定时器自己拉，正是 §10.3
        要消灭的东西（「页面不再有自己的定时器；只有调度器与立即拉取会触发」）。
        """
        target = seed_roles("p1")
        srv = servers({"/records.xml": {"body": RECORD_A}})
        _seed(target, record_url=srv.url("/records.xml"))
        _pull(target)
        name = _record_files(target)[0]
        downloads = len(srv.requests)
        assert downloads == 1

        with make_server() as server:
            for role in ("member", "deployer"):  # 两台浏览器，两种身份
                caller = as_role(server, role)
                caller.json("GET", "/api/v1/p/%s/files" % target)
                caller.json("GET", "/api/v1/p/%s/files/%s/records" % (target, NAME))
                caller.json(
                    "GET",
                    "/api/v1/p/%s/files/%s/document?version=%s" % (target, NAME, "record:" + name),
                )
                caller.request(
                    "GET", "/api/v1/p/%s/files/%s/raw?version=%s" % (target, NAME, "record:" + name)
                )
                caller.json(
                    "POST",
                    "/api/v1/p/%s/compare" % target,
                    {"base": {"file": NAME, "version": "current"}, "target": {"file": NAME, "version": "record:" + name}},
                )
                caller.json("GET", "/api/v1/p/%s/revision" % target)
                caller.json("GET", "/api/v1/p/%s/tasks?active=1" % target)

        assert len(srv.requests) == downloads, "打开记录页不得触发任何下载"

    def test_update_all_pulls_records_and_dedups_on_the_second_run(self, servers):
        """§10.2 末段：`update_all`（调度器唯一会投的那条）逐项跑配置更新 + 记录拉取；
        远端没变时第二次**不新增记录文件**。"""
        target = seed_roles("p1")
        srv = servers({"/new.xml": {"body": CONFIG_BYTES}, "/records.xml": {"body": RECORD_A}})
        from app.core import storage
        from app.services import updates

        with storage.use_profile(target):
            storage.save_config_file(NAME, CONFIG_BYTES)
            storage.save_config_mapping(
                [{"name": NAME, "url": srv.url("/new.xml"), "record_url": srv.url("/records.xml")}]
            )

        first = updates.update_all(target, "system")
        files_after_first = _record_files(target)
        before = _parts(target)
        second = updates.update_all(target, "system")

        assert [item["status"] for item in first["items"]] == ["unchanged", "success"]
        assert [item["status"] for item in second["items"]] == ["unchanged", "unchanged"]
        assert _record_files(target) == files_after_first, "第二次批量更新不得多出记录文件"
        assert len(files_after_first) == 1
        # 两份 last_run 都把记录那一项算进 `unchanged`/`success`（§7.9 的 `GET /schedule` 显示它）
        assert first["last_run"]["success"] == 1 and first["last_run"]["unchanged"] == 1
        assert second["last_run"]["unchanged"] == 2
        assert _parts(target)["files"] == before["files"], "全 unchanged 的一轮不递增 files"

    def test_no_record_pull_writes_update_status(self, servers):
        """约束 4：`update_status.json` 的键是**映射名**、装的是配置更新的最近结果。
        记录拉取胜/负都不往里写——否则 `update_all` 每轮都会用记录结果覆盖 UC-31 的状态列。"""
        target = seed_roles("p1")
        srv = servers({"/records.xml": {"body": RECORD_A}})
        _seed(target, record_url=srv.url("/records.xml"))

        _pull(target)
        srv.set_body("/records.xml", b"not found at all")

        from app.core import storage

        with storage.use_profile(target):
            assert storage.load_update_status() == {}
