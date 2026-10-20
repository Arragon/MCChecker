"""修订号全量审计（M3-17 / INH-1902；§7.3、UC-40）。

两件事：

1. **路由表 × 修订行为对账**：`build_router()` 里的每一条写路由都必须在
   `WRITE_ROUTE_EXPECTATIONS` 里登记（新增写接口不登记直接红）；声明表里也不许多出
   已消失的路由。
2. **失败/读/纯计算不递增，成功写只递增对应分量**：真实 HTTP 打一遍。异步任务、
   import-url 的持久化分支、DL 条件保存、文件删除与机型删除的完整递增语义由各自的
   卡内测试覆盖（见文件末尾的覆盖映射），本文件只补「跨卡对账」与未覆盖的 no-bump 面。

`/revision` 的已知边界（M4-05）：计数逻辑本身零磁盘 I/O，但带 `{pid}` 的路由在分发层
仍要读 `device_models.json` 与两张身份表（R13 禁止缓存）；本文件只对
`revision.snapshot` 做「不碰 storage」的断言，不对整个端点谎称零 I/O。
"""

from __future__ import annotations

import re
from urllib.parse import quote

import pytest
from authz_fixtures import as_role, make_server, seed_roles, write_target

from app.config import Settings
from app.core import storage
from app.server.router import build_router
from app.services import revision

FILE_A = "a.xml"
XML = "<R><params><item>1</item><param_01 min=\"0\" max=\"10\">5</param_01></params></R>"

#: 每条写路由的修订语义。`None` = 成功也不递增（纯计算/临时区/对比）。
WRITE_ROUTE_EXPECTATIONS = {
    ("POST", "/api/v1/p/{pid}/favorites"): "favorites",
    ("PATCH", "/api/v1/p/{pid}/favorites"): "favorites",
    ("DELETE", "/api/v1/p/{pid}/favorites"): "favorites",
    ("POST", "/api/v1/p/{pid}/remarks"): "remarks",
    ("POST", "/api/v1/p/{pid}/review/submit"): "remarks",
    ("POST", "/api/v1/p/{pid}/compare"): None,
    ("POST", "/api/v1/p/{pid}/temp"): None,
    ("PUT", "/api/v1/p/{pid}/files/{name}"): "files",
    ("POST", "/api/v1/p/{pid}/import-url"): "files",
    ("DELETE", "/api/v1/p/{pid}/files/{name}"): "files",
    ("POST", "/api/v1/p/{pid}/sources"): "settings",
    ("PATCH", "/api/v1/p/{pid}/sources/{name}"): "settings",
    ("DELETE", "/api/v1/p/{pid}/sources/{name}"): "settings",
    ("POST", "/api/v1/p/{pid}/tasks"): "tasks",
    ("PUT", "/api/v1/p/{pid}/schedule"): "settings",
    ("PUT", "/api/v1/p/{pid}/ip-mapping/{ip}"): "settings",
    ("DELETE", "/api/v1/p/{pid}/ip-mapping/{ip}"): "settings",
    ("POST", "/api/v1/p/{pid}/admins"): "settings",
    ("DELETE", "/api/v1/p/{pid}/admins/{name}"): "settings",
    ("POST", "/api/v1/p/{pid}/dl/{item}/compute"): None,
    ("POST", "/api/v1/p/{pid}/dl/{item}/extract"): None,
    ("PUT", "/api/v1/p/{pid}/dl/{item}"): "settings",
    ("PUT", "/api/v1/p/{pid}/bindings/{group}"): "settings",
    ("DELETE", "/api/v1/p/{pid}/bindings/{group}"): "settings",
    ("PUT", "/api/v1/p/{pid}/tools/{name}"): "settings",
    ("DELETE", "/api/v1/p/{pid}/tools/{name}"): "settings",
    ("POST", "/api/v1/profiles"): "settings (default)",
    ("PATCH", "/api/v1/profiles/{pid}"): "settings (default)",
    ("DELETE", "/api/v1/profiles/{pid}"): "settings (default)",
}

_PARAM_RE = re.compile(r"\(\?P<([A-Za-z_][A-Za-z0-9_]*)>\[\^/\][^)]*\)")


def _declared_routes():
    router = build_router(
        Settings(host="127.0.0.1", port=0, data_dir="/tmp/rev", web_dir="/tmp", trusted_proxies=())
    )
    found = set()
    for method, regex, _names, _handler in router._routes:
        if method not in ("POST", "PUT", "PATCH", "DELETE"):
            continue
        pattern = _PARAM_RE.sub(r"{\1}", regex.pattern.strip("^$"))
        found.add((method, pattern))
    return found


def _parts(pid):
    return revision.snapshot(pid)["parts"]


def _doc_sha_and_index(caller, pid, name):
    status, _h, body = caller.json(
        "GET", "/api/v1/p/%s/files/%s/document" % (pid, name)
    )
    assert status == 200, body
    nodes = body["nodes"]
    index = {loc: nodes["id"][i] for i, loc in enumerate(nodes["loc"])}
    return body["file"]["sha256"], index


class TestRouteTable:
    def test_route_table_and_expectation_table_cover_each_other(self):
        """新增写接口不在声明表 → 红；声明表残留已消失的路由 → 也红。"""
        discovered = _declared_routes()
        assert discovered == set(WRITE_ROUTE_EXPECTATIONS), (
            "未登记：%s；已消失：%s"
            % (
                sorted(discovered - set(WRITE_ROUTE_EXPECTATIONS)),
                sorted(set(WRITE_ROUTE_EXPECTATIONS) - discovered),
            )
        )

    def test_every_non_null_expectation_names_a_real_part(self):
        for route, part in WRITE_ROUTE_EXPECTATIONS.items():
            if part is None:
                continue
            assert part.split(" ")[0] in revision.PARTS, (route, part)


class TestNoBump:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("revno")
        write_target(self.pid, FILE_A, XML.encode("utf-8"))

    def test_reads_do_not_bump(self):
        before = _parts(self.pid)
        with make_server() as server:
            caller = as_role(server, "visitor")
            assert caller.json("GET", "/api/v1/p/%s/revision" % self.pid)[0] == 200
            assert caller.json("GET", "/api/v1/p/%s/files" % self.pid)[0] == 200
            assert caller.json("GET", "/api/v1/p/%s/favorites" % self.pid)[0] == 200
        assert _parts(self.pid) == before

    def test_compare_and_temp_do_not_bump(self):
        before = _parts(self.pid)
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = caller.json(
                "POST",
                "/api/v1/p/%s/compare" % self.pid,
                {
                    "base": {"file": FILE_A, "version": "current"},
                    "target": {"file": FILE_A, "version": "current"},
                },
            )
            assert status == 200, body
            status, _h, body = caller.request(
                "POST",
                "/api/v1/p/%s/temp" % self.pid,
                body=b"<R/>",
                headers={"X-Filename": "t.xml"},
            )
            assert status == 201, body
        assert _parts(self.pid) == before

    def test_delete_misses_do_not_bump(self):
        before = _parts(self.pid)
        with make_server() as server:
            caller = as_role(server, "deployer")
            for path in (
                "/api/v1/p/%s/favorites?file=%s&path=root" % (self.pid, FILE_A),
                "/api/v1/p/%s/bindings/nope" % self.pid,
                "/api/v1/p/%s/tools/nope" % self.pid,
                "/api/v1/p/%s/ip-mapping/10.9.9.9" % self.pid,
                "/api/v1/p/%s/admins/nope" % self.pid,
            ):
                status, _h, body = caller.json("DELETE", path)
                assert status in (200, 404), (path, body)
        assert _parts(self.pid) == before

    @pytest.mark.parametrize("role", ["visitor", "member", "admin"])
    def test_forbidden_writes_do_not_bump(self, role):
        before = _parts(self.pid)
        with make_server() as server:
            status, _h, _body = as_role(server, role).json(
                "PUT",
                "/api/v1/p/%s/tools/x" % self.pid,
                {"name": "x", "description": "", "url": "https://example.com"},
            )
            assert status == 403
        assert _parts(self.pid) == before

    def test_validation_failure_does_not_bump(self):
        before = _parts(self.pid)
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").json(
                "PUT",
                "/api/v1/p/%s/bindings/g" % self.pid,
                {"group_name": "g", "variables": [{"file": FILE_A, "path": ""}]},
            )
            assert status == 400, body
        assert _parts(self.pid) == before


class TestBumpOnSuccess:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("revyes")
        write_target(self.pid, FILE_A, XML.encode("utf-8"))

    def _bump(self, action):
        before = _parts(self.pid)
        with make_server() as server:
            action(as_role(server, "deployer"), self.pid)
        after = _parts(self.pid)
        changed = [part for part in revision.PARTS if after[part] != before[part]]
        assert len(changed) == 1, (changed, before, after)
        return changed[0]

    def test_favorites(self):
        def action(caller, pid):
            _sha, index = _doc_sha_and_index(caller, pid, FILE_A)
            node = index["/R[1]/params[1]/item[1]"]
            status, _h, body = caller.json(
                "POST", "/api/v1/p/%s/favorites" % pid, {"file": FILE_A, "id": node}
            )
            assert status in (200, 201), body

        assert self._bump(action) == "favorites"

    def test_remarks(self):
        def action(caller, pid):
            sha, _index = _doc_sha_and_index(caller, pid, FILE_A)
            status, _h, body = caller.json(
                "POST",
                "/api/v1/p/%s/remarks" % pid,
                {
                    "file": FILE_A,
                    "sha256": sha,
                    "loc": "/R[1]/params[1]/param_01[1]",
                    "proposed_value": "6",
                },
            )
            assert status == 201, body

        assert self._bump(action) == "remarks"

    def test_sources(self):
        def action(caller, pid):
            status, _h, body = caller.json(
                "POST",
                "/api/v1/p/%s/sources" % pid,
                {"name": "s.xml", "url": "http://127.0.0.1:9/a.xml", "record_url": ""},
            )
            assert status == 201, body

        assert self._bump(action) == "settings"

    def test_schedule(self):
        def action(caller, pid):
            status, _h, body = caller.json(
                "PUT", "/api/v1/p/%s/schedule" % pid,
                {"enabled": False, "interval_hours": 24},
            )
            assert status == 200, body

        assert self._bump(action) == "settings"

    def test_ip_mapping(self):
        def action(caller, pid):
            status, _h, body = caller.json(
                "PUT", "/api/v1/p/%s/ip-mapping/10.7.7.7" % pid,
                {"ip": "10.7.7.7", "name": "甲"},
            )
            assert status == 200, body

        assert self._bump(action) == "settings"

    def test_admins(self):
        def action(caller, pid):
            status, _h, body = caller.json(
                "POST", "/api/v1/p/%s/admins" % pid, {"name": "甲"}
            )
            assert status == 201, body

        assert self._bump(action) == "settings"

    def test_bindings(self):
        def action(caller, pid):
            status, _h, body = caller.json(
                "PUT",
                "/api/v1/p/%s/bindings/%s" % (pid, quote("组", safe="")),
                {
                    "group_name": "组",
                    "variables": [
                        {"file": FILE_A, "path": FILE_A + "/R"},
                        {"file": FILE_A, "path": FILE_A + "/R/params"},
                    ],
                },
            )
            assert status == 200, body

        assert self._bump(action) == "settings"

    def test_tools(self):
        def action(caller, pid):
            status, _h, body = caller.json(
                "PUT",
                "/api/v1/p/%s/tools/%s" % (pid, quote("工具", safe="")),
                {"name": "工具", "description": "", "url": "https://example.com"},
            )
            assert status == 200, body

        assert self._bump(action) == "settings"

    def test_files(self):
        def action(caller, pid):
            status, _h, body = caller.request(
                "PUT",
                "/api/v1/p/%s/files/%s?overwrite=1" % (pid, FILE_A),
                body=XML.replace("<item>1</item>", "<item>2</item>").encode("utf-8"),
            )
            assert status in (200, 201), body

        assert self._bump(action) == "files"

    def test_profile_create_bumps_default(self):
        before = _parts(storage.DEFAULT_PROFILE)
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").json(
                "POST", "/api/v1/profiles", {"name": "修订机型", "copy_from": None}
            )
            assert status == 201, body
        after = _parts(storage.DEFAULT_PROFILE)
        changed = [part for part in revision.PARTS if after[part] != before[part]]
        assert changed == ["settings"], (changed, before, after)


class TestUC40SecondClient:
    def test_another_client_sees_the_new_counter(self):
        """UC-40 的自动化部分：B 写成功后，A 的下一次 `/revision` 必须看到新计数。

        10 秒内的体感属跨设备项，仍记「未验证 · 待人工确认」；这里只钉住轮询拿到的值。
        """
        revision.reset()
        pid = seed_roles("rev40")
        write_target(pid, FILE_A, XML.encode("utf-8"))
        with make_server() as server:
            writer = as_role(server, "member")
            reader = as_role(server, "visitor")
            status, _h, before = reader.json(
                "GET", "/api/v1/p/%s/revision" % pid
            )
            assert status == 200, before
            sha, index = _doc_sha_and_index(writer, pid, FILE_A)
            node = index["/R[1]/params[1]/item[1]"]
            status, _h, body = writer.json(
                "POST",
                "/api/v1/p/%s/favorites" % pid,
                {"file": FILE_A, "id": node},
            )
            assert status in (200, 201), body
            status, _h, after = reader.json(
                "GET", "/api/v1/p/%s/revision" % pid
            )
        assert status == 200, after
        assert after["parts"]["favorites"] == before["parts"]["favorites"] + 1
        assert after["rev"] != before["rev"]
        assert after["rev"] == ".".join(
            str(after["parts"][part]) for part in revision.PARTS
        )


class TestRevisionCounterIsPureMemory:
    def test_snapshot_does_not_touch_the_filesystem(self, monkeypatch):
        import builtins

        revision.reset()
        revision.bump("pure", "files")
        original = builtins.open

        def boom(*_args, **_kwargs):
            raise AssertionError("revision.snapshot 不得读文件")

        monkeypatch.setattr(builtins, "open", boom)
        try:
            assert revision.snapshot("pure")["parts"]["files"] == 2
        finally:
            monkeypatch.setattr(builtins, "open", original)

    def test_snapshot_is_cheap(self):
        import time

        revision.reset()
        started = time.perf_counter()
        for _ in range(2000):
            revision.snapshot("fast")
        elapsed = time.perf_counter() - started
        assert elapsed < 0.2, "2000 次 snapshot 用了 %.3f s，明显超出 5 ms/次的量级" % elapsed


# 覆盖映射（本文件不重复造轮子）：
# - `POST /import-url` 的 persist=false 不递增、persist=true 递增 files：`tests/test_api_import.py`
# - `POST /tasks` 的入队/进度/收口递增：`tests/test_api_tasks.py::TestRevisionBumps`
# - `PUT /dl/{item}`、compute/extract：`tests/test_api_dl.py`
# - `DELETE /files/{name}` 及引用分量：`tests/test_api_delete.py`
# - `PATCH/DELETE /profiles/{pid}`：`tests/test_api_profiles.py`
# - 前端 10 秒轮询与黄横幅（UC-40 的体感部分仍记「未验证 · 待人工确认」）：
#   `app/web/tests/tests.js` 的 revision/横幅用例。
