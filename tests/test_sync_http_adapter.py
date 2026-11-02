"""M16-13 普通 HTTP 文件/记录取数适配（INH-2178）。

本地 `http.server`（127.0.0.1 随机端口）验证三组事实：

1. **与旧 `services.updates` 同一输入完全一致**：XML 200、invalid XML、zip/tar、
   record 文本、keyword、unchanged 的字节/文件名/分类逐项对齐；旧入口仍然可读旧映射。
2. **失败只属于该来源**：HTTP 500、超时、超限都返回源级失败，不产生 item，不写
   current/`update_status.json`，也不存在 collector 登录路径。
3. **本适配器只取数不落盘**：变化才归档、files revision 才递增仍由旧
   `updates`/`storage` 路径负责（M16-24 在其上做提交），这里用旧路径回归钉住语义。

下载一律打本地服务器，`getproxies` 打桩成 `{}`，重试退避打桩成记录器——与
`tests/test_api_records.py` 同一套做法，避免真实网络与 1s/2s 退避拖慢用例。
所有写入都落在 conftest 的临时 data 根里（`isolated_data_root`），不碰生产 `data/`。
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
import threading
import time
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

import pytest

from app.core import downloader, storage
from app.core.errors import INVALID_INPUT, AppError
from app.core.sync_models import SyncSource, SyncTarget
from app.services import sync_http_adapter, updates

NAME = "config.xml"
XML_A = b"<config><a>1</a></config>"
XML_B = b"<config><a>2</a></config>"
INVALID_XML = b"this is not xml or json {{{"
LOGIN_PAGE = b"<!DOCTYPE html><html><body>please login</body></html>"
RECORD_TEXT = "2026-10-18 张三 改了 param_13 的值，不是 XML 也不是 JSON {{{".encode("utf-8")

ZIP_BYTES = None
TAR_BYTES = None


def _zip(members) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, body in members:
            archive.writestr(name, body)
    return buf.getvalue()


def _tar_gz(members) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for name, body in members:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(body))
    return buf.getvalue()


ZIP_BYTES = _zip(
    [
        ("dir/Default_MachineConstant_basics.xml", XML_A),
        ("Foo.json", b'{"kept": true}'),
    ]
)
TAR_BYTES = _tar_gz(
    [
        ("dir/Default_MachineConstant_basics.xml", XML_A),
        ("Foo.json", b'{"kept": true}'),
    ]
)


# ---------------------------------------------------------------------------
# 本地 HTTP 服务器：可以按路径换正文、延迟响应、返回错误状态
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
            route = {"status": 404, "body": b"not found"}
        elif isinstance(route, bytes):
            route = {"body": route}
        delay = route.get("delay", 0)
        if delay:
            time.sleep(delay)
        body = route.get("body", b"")
        self.send_response(route.get("status", 200))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/octet-stream")
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

    def url(self, path: str = "/config.xml") -> str:
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def set_body(self, path: str, body: bytes) -> None:
        self.httpd.routes[path] = {"body": body}


@pytest.fixture()
def servers(monkeypatch):
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
    recorded: List[float] = []
    monkeypatch.setattr(downloader, "_sleep", recorded.append)
    return recorded


def make_source(
    url: str,
    *,
    name: str = NAME,
    keyword: str = "",
    record_url: str = "",
    kind: str = "http_file",
    connection_id=None,
) -> SyncSource:
    """构造一个合法的 SyncSource；http_file 的 URL 约定放在 resource_ref。"""
    return SyncSource(
        source_id="src-http-test",
        workspace_id="w1",
        profile_id="default",
        revision=1,
        name=name,
        kind=kind,
        target=SyncTarget(kind="file_archive", file_id="file-1"),
        enabled=True,
        schedule_mode="inherit",
        connection_id=connection_id,
        resource_ref=url,
        display_path="/synthetic/%s" % name,
        record_source=record_url or None,
        keyword=keyword,
    )


def _policy(**overrides) -> downloader.DownloadPolicy:
    values = {"timeout_s": 5, "retries": 1, "max_bytes": 1024 * 1024}
    values.update(overrides)
    return downloader.DownloadPolicy(**values)


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@pytest.fixture()
def pid() -> str:
    from app.core import device_models

    return device_models.add_model("http-adapter")


def _seed_mapping(
    target_pid: str, url: str, *, name: str = NAME, record_url: str = "", keyword: str = ""
) -> None:
    with storage.use_profile(target_pid):
        storage.save_config_mapping(
            [{"name": name, "url": url, "record_url": record_url, "keyword": keyword}]
        )


def _parts(target_pid: str) -> Dict[str, int]:
    from app.services import revision

    return revision.snapshot(target_pid)["parts"]


def _archive_files(target_pid: str, name: str = NAME) -> List[str]:
    with storage.use_profile(target_pid):
        return [item["filename"] for item in storage.list_archived_versions(name)]


def _read_record_files(target_pid: str, name: str = NAME) -> Dict[str, bytes]:
    with storage.use_profile(target_pid):
        directory = storage.get_record_dir(name)
        if not os.path.isdir(directory):
            return {}
        out = {}
        for filename in sorted(os.listdir(directory)):
            path = os.path.join(directory, filename)
            if os.path.isfile(path):
                with open(path, "rb") as handle:
                    out[filename] = handle.read()
        return out


# ---------------------------------------------------------------------------
# 1. http_file：XML / invalid XML / zip / tar / keyword / unchanged
# ---------------------------------------------------------------------------


class TestFetchSource:
    def test_xml_200_matches_legacy_saved_bytes(self, pid, servers):
        srv = servers({"/config.xml": XML_A})
        src = make_source(srv.url("/config.xml"))

        result = sync_http_adapter.fetch_source(src)

        assert result.kind == "http_file"
        assert result.status == "succeeded"
        assert result.reason_code is None
        assert result.keyword_hit is None
        assert result.unchanged is None
        assert len(result.items) == 1
        item = result.items[0]
        assert item.basename == NAME
        assert item.content == XML_A
        assert item.size == len(XML_A)
        assert item.sha256 == _sha(XML_A)
        assert item.unchanged is None
        assert srv.requests == ["/config.xml"]

        _seed_mapping(pid, srv.url("/config.xml"))
        before = _parts(pid)
        legacy = updates.update_source(pid, NAME, "tester")

        assert legacy["status"] == "success"
        with storage.use_profile(pid):
            assert storage.load_config_file(NAME) == item.content
            assert storage.load_update_status()[NAME]["status"] == "success"
        assert _parts(pid)["files"] == before["files"] + 1
        assert _archive_files(pid) == []

    def test_invalid_xml_is_passed_through_and_legacy_rejects_it(self, pid, servers):
        """M16-24 才做内容校验；本卡必须把 invalid XML 原样交给提交层。

        旧路径对同一份字节的结论是 `rejected` 且 current 不变——适配器不解析、不改写，
        所以两边的输入字节完全相同，差别只在「谁在什么时候判定」。
        """
        srv = servers({"/config.xml": INVALID_XML})
        result = sync_http_adapter.fetch_source(make_source(srv.url("/config.xml")))

        assert result.status == "succeeded"
        assert result.items[0].content == INVALID_XML
        with storage.use_profile(pid):
            assert storage.list_config_files() == []
            assert storage.load_update_status() == {}

        with storage.use_profile(pid):
            storage.save_config_file(NAME, XML_A)
        _seed_mapping(pid, srv.url("/config.xml"))
        legacy = updates.update_source(pid, NAME, "tester")

        assert legacy["status"] == "rejected"
        assert "有效的 XML/JSON" in legacy["reason"]
        with storage.use_profile(pid):
            assert storage.load_config_file(NAME) == XML_A, "失败不得覆盖 current"
        assert _archive_files(pid) == []

    def test_login_page_200_stays_a_source_payload(self, pid, servers):
        """200 登录页只是「这个源下载到了错误内容」；不触发任何 SDA/collector 登录。"""
        srv = servers({"/config.xml": LOGIN_PAGE})
        result = sync_http_adapter.fetch_source(make_source(srv.url("/config.xml")))

        assert result.status == "succeeded"
        assert result.items[0].content == LOGIN_PAGE
        assert "登录" not in result.message
        assert not hasattr(sync_http_adapter, "ensure_session")
        with storage.use_profile(pid):
            assert storage.list_config_files() == []

    @pytest.mark.parametrize(
        "path,body",
        [("/bundle.zip", ZIP_BYTES), ("/bundle.tar.gz", TAR_BYTES)],
    )
    def test_zip_and_tar_expand_to_the_same_files_as_legacy(self, pid, servers, path, body):
        srv = servers({path: body})
        result = sync_http_adapter.fetch_source(make_source(srv.url(path), name="bundle.xml"))

        assert result.status == "succeeded"
        assert [item.basename for item in result.items] == [
            "Default_MachineConstant_basics.xml",
            "Foo.json",
        ]
        assert [item.content for item in result.items] == [XML_A, b'{"kept": true}']

        _seed_mapping(pid, srv.url(path), name="bundle.xml")
        legacy = updates.update_source(pid, "bundle.xml", "tester")

        assert legacy["status"] == "success"
        with storage.use_profile(pid):
            assert storage.list_config_files() == [item.basename for item in result.items]
            for item in result.items:
                assert storage.load_config_file(item.basename) == item.content

    def test_keyword_trims_the_same_name_as_legacy(self, pid, servers):
        srv = servers({"/bundle.zip": ZIP_BYTES})
        result = sync_http_adapter.fetch_source(
            make_source(srv.url("/bundle.zip"), name="pack.xml", keyword="MachineConstant")
        )

        assert result.status == "succeeded"
        assert result.keyword == "MachineConstant"
        assert result.keyword_hit is True
        assert [(item.basename, item.content) for item in result.items] == [
            ("MachineConstant.xml", XML_A)
        ]

        _seed_mapping(
            pid, srv.url("/bundle.zip"), name="pack.xml", keyword="MachineConstant"
        )
        legacy = updates.update_source(pid, "pack.xml", "tester")

        assert legacy["status"] == "success"
        with storage.use_profile(pid):
            assert storage.list_config_files() == ["MachineConstant.xml"]
            assert storage.load_config_file("MachineConstant.xml") == XML_A

    def test_same_and_different_content_change_unchanged_flag(self, servers):
        srv = servers({"/config.xml": XML_A})
        src = make_source(srv.url("/config.xml"))

        same = sync_http_adapter.fetch_source(src, current={NAME: XML_A})
        assert same.status == "unchanged"
        assert same.unchanged is True
        assert same.items[0].unchanged is True
        assert same.reason_code == "unchanged"

        srv.set_body("/config.xml", XML_B)
        different = sync_http_adapter.fetch_source(src, current={NAME: XML_A})
        assert different.status == "succeeded"
        assert different.unchanged is False
        assert different.items[0].unchanged is False
        assert different.items[0].sha256 == _sha(XML_B)
        assert different.items[0].sha256 != _sha(XML_A)

    def test_missing_url_skips_without_a_request(self, servers):
        srv = servers({})
        result = sync_http_adapter.fetch_source(make_source(""))

        assert result.status == "skipped"
        assert result.message == "无下载链接"
        assert result.items == ()
        assert srv.requests == []

    def test_only_http_file_sources_are_accepted(self):
        sda = make_source("file-1", kind="sda_file", connection_id="conn-1")
        with pytest.raises(AppError) as exc:
            sync_http_adapter.fetch_source(sda)
        assert exc.value.code == INVALID_INPUT

    def test_url_with_userinfo_and_query_is_masked(self, servers):
        srv = servers({"/secret/config.xml": XML_A})
        url = srv.url("/secret/config.xml").replace(
            "127.0.0.1", "user:pass@127.0.0.1"
        ) + "?token=topsecret"
        result = sync_http_adapter.fetch_source(make_source(url))

        assert result.status == "succeeded"
        assert result.url == srv.url("/secret/config.xml")
        assert "user" not in (result.url or "")
        assert "pass" not in (result.url or "")
        assert "token" not in (result.url or "")
        assert srv.requests[0].startswith("/secret/config.xml?")


# ---------------------------------------------------------------------------
# 2. 记录：文本原样、扩展名、unchanged
# ---------------------------------------------------------------------------


class TestFetchRecord:
    @pytest.mark.parametrize(
        "path,extension",
        [
            ("/records.xml", ".xml"),
            ("/changes.log", ".log"),
            ("/no-extension", ".txt"),
            ("/record.0123456789", ".txt"),
        ],
    )
    def test_record_text_and_extension_match_legacy(self, pid, servers, path, extension):
        srv = servers({path: RECORD_TEXT})
        result = sync_http_adapter.fetch_record(
            make_source("", name=NAME, record_url=srv.url(path))
        )

        assert result.kind == "http_record"
        assert result.status == "succeeded"
        assert result.unchanged is False
        assert len(result.items) == 1
        item = result.items[0]
        assert item.content == RECORD_TEXT, "记录内容不要求可解析，一个字节都不许改"
        assert item.sha256 == _sha(RECORD_TEXT)
        assert item.extension == extension
        assert item.unchanged is False

        with storage.use_profile(pid):
            storage.save_config_file(NAME, XML_A)
        _seed_mapping(pid, "", record_url=srv.url(path))
        legacy = updates.refresh_records(pid, NAME, "tester")

        assert legacy["status"] == "success"
        records = _read_record_files(pid)
        assert len(records) == 1
        filename, body = next(iter(records.items()))
        assert filename.endswith(extension), filename
        assert body == item.content

    def test_identical_record_is_unchanged_and_unchanged_pull_creates_no_file(
        self, pid, servers
    ):
        srv = servers({"/records.xml": RECORD_TEXT})
        src = make_source("", name=NAME, record_url=srv.url("/records.xml"))

        first = sync_http_adapter.fetch_record(src)
        second = sync_http_adapter.fetch_record(src, latest=RECORD_TEXT)
        assert first.status == "succeeded"
        assert second.status == "unchanged"
        assert second.unchanged is True
        assert second.items[0].unchanged is True
        assert second.reason_code == "unchanged"

        with storage.use_profile(pid):
            storage.save_config_file(NAME, XML_A)
        _seed_mapping(pid, "", record_url=srv.url("/records.xml"))
        before = _parts(pid)
        assert updates.refresh_records(pid, NAME, "tester")["status"] == "success"
        assert updates.refresh_records(pid, NAME, "tester")["status"] == "unchanged"
        assert len(_read_record_files(pid)) == 1, "相同内容不得新建记录文件"
        assert _parts(pid)["files"] == before["files"] + 1, "只有第一份真的新增才 +1"

    def test_missing_record_url_skips_without_a_request(self, servers):
        srv = servers({})
        result = sync_http_adapter.fetch_record(make_source(""))

        assert result.status == "skipped"
        assert result.message == "未配置修改记录链接"
        assert result.items == ()
        assert srv.requests == []

    def test_record_http_500_is_a_source_level_failure(self, pid, servers):
        srv = servers({"/records.xml": {"status": 500, "body": b"boom"}})
        result = sync_http_adapter.fetch_record(
            make_source("", name=NAME, record_url=srv.url("/records.xml")),
            policy=_policy(),
        )

        assert result.status == "failed"
        assert result.reason_code == "network_error"
        assert result.items == ()
        assert "500" in result.message
        assert _read_record_files(pid) == {}


# ---------------------------------------------------------------------------
# 3. 源级失败：500 / 超限 / 超时；不写 current，不碰 update_status
# ---------------------------------------------------------------------------


class TestSourceLevelFailures:
    def test_http_500_fails_the_source_without_items_or_sda_login(self, pid, servers, sleeps):
        srv = servers({"/config.xml": {"status": 500, "body": b"boom"}})
        result = sync_http_adapter.fetch_source(
            make_source(srv.url("/config.xml")), policy=_policy()
        )

        assert result.status == "failed"
        assert result.reason_code == "network_error"
        assert result.items == ()
        assert "500" in result.message
        assert "登录" not in result.message
        assert "session" not in result.message.lower()
        assert srv.requests == ["/config.xml"]
        assert not hasattr(sync_http_adapter, "ensure_session")
        with storage.use_profile(pid):
            assert storage.list_config_files() == []
            assert storage.load_update_status() == {}

    def test_too_large_is_deterministic_and_writes_nothing(self, pid, servers):
        srv = servers({"/big.xml": b"x" * 256})
        result = sync_http_adapter.fetch_source(
            make_source(srv.url("/big.xml")),
            policy=_policy(max_bytes=32),
        )

        assert result.status == "failed"
        assert result.reason_code == "too_large"
        assert result.items == ()
        assert "上限" in result.message
        assert srv.requests == ["/big.xml"], "TOO_LARGE 不重试"
        with storage.use_profile(pid):
            assert storage.list_config_files() == []

    def test_timeout_is_a_source_level_network_error(self, pid, servers):
        srv = servers({"/slow.xml": {"body": XML_A, "delay": 0.5}})
        result = sync_http_adapter.fetch_source(
            make_source(srv.url("/slow.xml")),
            policy=_policy(timeout_s=0.1),
        )

        assert result.status == "failed"
        assert result.reason_code == "network_error"
        assert result.items == ()
        assert "超时" in result.message
        with storage.use_profile(pid):
            assert storage.list_config_files() == []

    def test_policy_refusal_is_reported_as_blocked(self, pid, servers):
        srv = servers({"/config.xml": XML_A})
        result = sync_http_adapter.fetch_source(
            make_source(srv.url("/config.xml")),
            policy=_policy(allow=("10.0.0.0/8",)),
        )

        assert result.status == "failed"
        assert result.reason_code == "redirect_blocked"
        assert "MCHECKER_DOWNLOAD_ALLOW" in result.message
        assert srv.requests == [], "策略在连接之前就挡住了"
        with storage.use_profile(pid):
            assert storage.list_config_files() == []

    def test_malformed_url_fails_without_crashing(self, pid):
        """坏 URL 也要给出安全的源级结果：诊断字段不能把整个 run item 打崩。"""
        result = sync_http_adapter.fetch_source(make_source("http://[::1"))

        assert result.status == "failed"
        assert result.reason_code == "redirect_blocked"
        assert result.items == ()
        assert result.url == "<无法解析的 URL>"
        with storage.use_profile(pid):
            assert storage.list_config_files() == []


# ---------------------------------------------------------------------------
# 4. 只读边界与旧路径回归：归档/修订/update_status 仍由旧提交层负责
# ---------------------------------------------------------------------------


class TestReadOnlyAndLegacyCommit:
    def test_adapter_never_writes_data(self, pid, servers):
        srv = servers(
            {
                "/config.xml": XML_A,
                "/records.xml": RECORD_TEXT,
                "/bad.xml": {"status": 500, "body": b"boom"},
            }
        )
        with storage.use_profile(pid):
            storage.save_config_file(NAME, XML_A)
            storage.save_config_mapping(
                [{"name": NAME, "url": srv.url("/config.xml"), "record_url": "", "keyword": ""}]
            )
            storage.save_update_status(
                {NAME: {"status": "unchanged", "label": "无变化", "reason": "seed", "at": "t", "archived": None}}
            )
            before_current = storage.load_config_file(NAME)
            before_status = storage.load_update_status()
            before_mapping = storage.load_config_mapping()
            before_archive = storage.list_archived_versions(NAME)
        before_parts = _parts(pid)

        # 适配器没有 import storage，从结构上就不可能写 current/status/archive。
        assert not hasattr(sync_http_adapter, "storage")

        sync_http_adapter.fetch_source(make_source(srv.url("/config.xml")))
        sync_http_adapter.fetch_record(
            make_source("", name=NAME, record_url=srv.url("/records.xml"))
        )
        sync_http_adapter.fetch_source(
            make_source(srv.url("/bad.xml")), policy=_policy()
        )

        with storage.use_profile(pid):
            assert storage.load_config_file(NAME) == before_current
            assert storage.load_update_status() == before_status
            assert storage.load_config_mapping() == before_mapping
            assert storage.list_archived_versions(NAME) == before_archive
        assert _parts(pid) == before_parts

    def test_legacy_update_archives_only_when_content_changes(self, pid, servers):
        srv = servers({"/config.xml": XML_A})
        _seed_mapping(pid, srv.url("/config.xml"))
        before = _parts(pid)

        first = updates.update_source(pid, NAME, "tester")
        assert first["status"] == "success"
        assert _archive_files(pid) == [], "首次没有旧版本可归档"
        assert _parts(pid)["files"] == before["files"] + 1

        srv.set_body("/config.xml", XML_B)
        second = updates.update_source(pid, NAME, "tester")
        assert second["status"] == "success"
        archives = _archive_files(pid)
        assert len(archives) == 1
        with storage.use_profile(pid):
            assert storage.load_config_file(NAME) == XML_B
            assert storage.load_archived_file(NAME, archives[0]) == XML_A
            assert storage.load_update_status()[NAME]["status"] == "success"
        assert _parts(pid)["files"] == before["files"] + 2

        files_after_change = _parts(pid)["files"]
        third = updates.update_source(pid, NAME, "tester")
        assert third["status"] == "unchanged"
        assert _archive_files(pid) == archives, "未变不得新增归档"
        assert _parts(pid)["files"] == files_after_change, "unchanged 不递增 files"

    def test_legacy_failure_keeps_current_and_archives_nothing(self, pid, servers, sleeps):
        srv = servers({"/config.xml": {"status": 500, "body": b"boom"}})
        with storage.use_profile(pid):
            storage.save_config_file(NAME, XML_A)
        _seed_mapping(pid, srv.url("/config.xml"))
        before = _parts(pid)

        result = updates.update_source(pid, NAME, "tester")

        assert result["status"] == "download_failed"
        assert result["archived"] is None
        with storage.use_profile(pid):
            assert storage.load_config_file(NAME) == XML_A, "失败不得覆盖 current"
        assert _archive_files(pid) == []
        assert _parts(pid)["files"] == before["files"]
        assert sleeps, "DOWNLOAD_FAILED 仍走下载器重试路径（未被适配器绕过）"

    def test_old_entry_still_reads_old_mapping(self, pid):
        with storage.use_profile(pid):
            storage.save_config_mapping(
                [
                    {
                        "name": "legacy.xml",
                        "url": "http://example.invalid/legacy.xml",
                        "record_url": "http://example.invalid/changes.txt",
                        "keyword": "Legacy",
                    }
                ]
            )

        rows = updates.list_sources(pid)

        assert len(rows) == 1
        assert rows[0]["name"] == "legacy.xml"
        assert rows[0]["url"] == "http://example.invalid/legacy.xml"
        assert rows[0]["record_url"] == "http://example.invalid/changes.txt"
        assert rows[0]["keyword"] == "Legacy"
