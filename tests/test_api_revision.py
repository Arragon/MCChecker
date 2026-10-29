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
from app.core import downloader, storage
from app.server.router import build_router
from app.services import revision

FILE_A = "a.xml"
XML = "<R><params><item>1</item><param_01 min=\"0\" max=\"10\">5</param_01></params></R>"

#: 每条写路由的修订语义。`None` = 成功也不递增（纯计算/临时区/对比）。
WRITE_ROUTE_EXPECTATIONS = {
    ("POST", "/api/v1/p/{pid}/favorites"): "favorites",
    ("PATCH", "/api/v1/p/{pid}/favorites"): "favorites",
    ("DELETE", "/api/v1/p/{pid}/favorites"): "favorites (only if removed)",
    ("POST", "/api/v1/p/{pid}/remarks"): "remarks",
    ("POST", "/api/v1/p/{pid}/review/submit"): "remarks",
    ("POST", "/api/v1/p/{pid}/compare"): None,
    ("POST", "/api/v1/p/{pid}/temp"): None,
    ("PUT", "/api/v1/p/{pid}/files/{name}"): "files (unchanged -> none)",
    ("POST", "/api/v1/p/{pid}/import-url"): "files (persist=true only)",
    ("DELETE", "/api/v1/p/{pid}/files/{name}"): "files (+favorites/remarks/settings when refs removed)",
    ("POST", "/api/v1/p/{pid}/sources"): "settings",
    ("PATCH", "/api/v1/p/{pid}/sources/{name}"): "settings",
    ("DELETE", "/api/v1/p/{pid}/sources/{name}"): "settings (only if removed)",
    ("POST", "/api/v1/p/{pid}/tasks"): "tasks (finish also files/settings)",
    ("PUT", "/api/v1/p/{pid}/schedule"): "settings",
    ("PUT", "/api/v1/p/{pid}/ip-mapping/{ip}"): "settings",
    ("DELETE", "/api/v1/p/{pid}/ip-mapping/{ip}"): "settings (only if removed)",
    ("POST", "/api/v1/p/{pid}/admins"): "settings",
    ("DELETE", "/api/v1/p/{pid}/admins/{name}"): "settings (only if removed)",
    ("POST", "/api/v1/p/{pid}/dl/{item}/compute"): None,
    ("POST", "/api/v1/p/{pid}/dl/{item}/extract"): None,
    ("PUT", "/api/v1/p/{pid}/dl/{item}"): "settings",
    ("POST", "/api/v1/p/{pid}/dl/{item}/coeffs"): "settings (only if changed)",
    ("POST", "/api/v1/p/{pid}/dl/{item}/coeff-history/{hid}/apply"): "settings (only if changed)",
    ("DELETE", "/api/v1/p/{pid}/dl/{item}/coeff-history/{hid}"): None,
    ("PUT", "/api/v1/p/{pid}/bindings/{group}"): "settings",
    ("DELETE", "/api/v1/p/{pid}/bindings/{group}"): "settings (only if removed)",
    ("PUT", "/api/v1/p/{pid}/tools/{name}"): "settings",
    ("DELETE", "/api/v1/p/{pid}/tools/{name}"): "settings (only if removed)",
    ("POST", "/api/v1/profiles"): "settings (default)",
    ("PATCH", "/api/v1/profiles/{pid}"): "settings (default)",
    ("DELETE", "/api/v1/profiles/{pid}"): "settings (default)",
    ("POST", "/api/v1/profiles/{pid}/retire"): "settings (default)",
    ("POST", "/api/v1/profiles/{pid}/restore"): "settings (default)",
    # M9-02：workspace 元数据不递增旧 5 段。
    ("POST", "/api/v3/workspaces"): None,
    ("PATCH", "/api/v3/workspaces/{wid}"): None,
    ("POST", "/api/v3/workspaces/{wid}/profiles"): None,
    ("PATCH", "/api/v3/workspaces/{wid}/profiles/{pid}"): None,
    ("DELETE", "/api/v3/workspaces/{wid}/profiles/{pid}"): None,
    # --- 配置管理域（M6-01/M6-02）：**不递增**旧 5 段，改用全局 `config_revision`（决策 D3）。
    # 配置域的对象、审计与回执都在 `data/config_management/` 里，与机型文件的修订语义解耦；
    # 因此这里逐条登记为 None，等于声明"它们绝不碰 models.rev.*"。
    ("POST", "/api/v1/config/releases"): None,
    ("POST", "/api/v1/config/releases/{rid}/default"): None,
    ("POST", "/api/v1/config/releases/{rid}/retire"): None,
    ("POST", "/api/v1/config/families"): None,
    ("POST", "/api/v1/config/families/{fid}/files"): None,
    ("POST", "/api/v1/config/families/{fid}/retire"): None,
    ("POST", "/api/v1/config/families/{fid}/reactivate"): None,
    ("PATCH", "/api/v1/config/files/{lid}"): None,
    ("POST", "/api/v1/config/files/{lid}/retire"): None,
    ("POST", "/api/v1/config/snapshots"): None,
    ("POST", "/api/v1/config/variants"): None,
    # M6-03/M6-04：参数登记同样只递增全局 `config_revision`，不碰机型 5 段。
    ("POST", "/api/v1/config/parameters"): None,
    ("PATCH", "/api/v1/config/parameters/{pmid}"): None,
    ("POST", "/api/v1/config/parameters/{pmid}/definitions"): None,
    ("POST", "/api/v1/config/definitions/{did}/retire"): None,
    ("POST", "/api/v1/config/definitions/{did}/verify"): None,
    ("POST", "/api/v1/config/definition-sets"): None,
    # M6-05：追踪策略同样只递增全局 config_revision。
    ("POST", "/api/v1/config/tracking"): None,
    # 采集与观测（M6-07/M6-08）：观测不是基线，写它不动 5 段修订号
    ("POST", "/api/v1/config/observations"): None,
    ("POST", "/api/v1/config/observations/{oid}/remap"): None,
    # 基线草稿（M6-09）：基线是配置域自己的对象，不动旧 5 段修订号
    ("POST", "/api/v1/config/baselines"): None,
    ("POST", "/api/v1/config/baselines/{bid}/values"): None,
    ("POST", "/api/v1/config/baselines/{bid}/intent"): None,
    # 变更单（M6-11）与批准发布（M6-12）：发布写的是配置域基线，仍不动旧 5 段
    ("POST", "/api/v1/p/{pid}/config/changes"): None,
    ("POST", "/api/v1/p/{pid}/config/changes/{cid}"): None,
    ("POST", "/api/v1/p/{pid}/config/changes/{cid}/submit"): None,
    ("POST", "/api/v1/p/{pid}/config/changes/{cid}/withdraw"): None,
    ("POST", "/api/v1/p/{pid}/config/changes/{cid}/reopen"): None,
    ("POST", "/api/v1/p/{pid}/config/changes/{cid}/approve"): None,
    ("POST", "/api/v1/p/{pid}/config/changes/{cid}/reject"): None,
    # 产物（M6-14）：写 blob + 建对象，仍不动旧 5 段
    ("POST", "/api/v1/p/{pid}/config/artifacts"): None,
    # 升级落地建的是**新对象**（草稿），没有"改哪个已有对象"的 CAS 目标
    ("POST", "/api/v1/config/upgrade/common"): None,
    ("POST", "/api/v1/p/{pid}/config/upgrade/model"): None,
    ("POST", "/api/v1/p/{pid}/config/upgrade/rebase"): None,
    # 上提建变更单、回滚/迁移/刷新建新对象：都不改已有对象
    ("POST", "/api/v1/p/{pid}/config/promote/common"): None,
    ("POST", "/api/v1/p/{pid}/config/promote/model"): None,
    ("POST", "/api/v1/config/baselines/{bid}/restore"): None,
    ("POST", "/api/v1/config/baselines/{bid}/migrate"): None,
    ("POST", "/api/v1/config/baselines/{bid}/reissue"): None,
    # 交付登记与核验报告都是新增对象，不改已有对象
    ("POST", "/api/v1/p/{pid}/config/deliveries"): None,
    ("POST", "/api/v1/p/{pid}/config/artifacts/{aid}/verifications"): None,
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

    @pytest.mark.parametrize("role", ["visitor", "member"])
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
        assert after[changed[0]] == before[changed[0]] + 1, (changed, before, after)
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

    def test_favorites_patch_and_delete(self):
        with make_server() as server:
            caller = as_role(server, "member")
            _sha, index = _doc_sha_and_index(caller, self.pid, FILE_A)
            node = index["/R[1]/params[1]/item[1]"]
            base = "/api/v1/p/%s/favorites" % self.pid

            def expect(action):
                before = _parts(self.pid)
                status, _h, body = action()
                assert status in (200, 201), body
                after = _parts(self.pid)
                assert after["favorites"] == before["favorites"] + 1, (before, after)

            expect(lambda: caller.json("POST", base, {"file": FILE_A, "id": node}))
            expect(lambda: caller.json(
                "PATCH", base, {"file": FILE_A, "path": node, "note": "n"}
            ))
            expect(lambda: caller.json(
                "DELETE", base + "?file=%s&path=%s" % (FILE_A, node)
            ))

    def test_sources_patch_and_delete(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            base = "/api/v1/p/%s/sources" % self.pid

            def expect(action):
                before = _parts(self.pid)
                status, _h, body = action()
                assert status in (200, 201), body
                after = _parts(self.pid)
                assert after["settings"] == before["settings"] + 1, (before, after)

            expect(lambda: caller.json(
                "POST", base,
                {"name": "s.xml", "url": "http://127.0.0.1:9/a.xml", "record_url": ""},
            ))
            expect(lambda: caller.json(
                "PATCH", base + "/s.xml",
                {"name": "s.xml", "url": "http://127.0.0.1:9/b.xml"},
            ))
            expect(lambda: caller.json("DELETE", base + "/s.xml"))

    def test_import_url_bumps_only_when_persisted(self, monkeypatch):
        monkeypatch.setattr(
            downloader, "fetch", lambda url, policy: XML.replace("<item>1</item>", "<item>7</item>").encode("utf-8")
        )
        with make_server() as server:
            visitor = as_role(server, "member")
            before = _parts(self.pid)
            status, _h, body = visitor.json(
                "POST",
                "/api/v1/p/%s/import-url" % self.pid,
                {"url": "http://example.invalid/x.xml", "name": "tmp.xml"},
            )
            assert status == 200 and body["persisted"] is False, body
            assert _parts(self.pid) == before, "临时导入不递增"

            deployer = as_role(server, "deployer")
            status, _h, body = deployer.json(
                "POST",
                "/api/v1/p/%s/import-url" % self.pid,
                {"url": "http://example.invalid/y.xml", "name": "persisted.xml", "persist": True},
            )
            assert status == 200 and body["persisted"] is True, body
            after = _parts(self.pid)
            assert after["files"] == before["files"] + 1, (before, after)

    def test_bindings_and_tools_delete_bump(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            bindings = "/api/v1/p/%s/bindings/%s" % (self.pid, quote("组", safe=""))
            status, _h, body = caller.json(
                "PUT", bindings,
                {"group_name": "组", "variables": [
                    {"file": FILE_A, "path": FILE_A + "/R"},
                    {"file": FILE_A, "path": FILE_A + "/R/params"},
                ]},
            )
            assert status == 200, body
            before = _parts(self.pid)
            status, _h, body = caller.json("DELETE", bindings)
            assert status == 200 and body == {"removed": True}, body
            assert _parts(self.pid)["settings"] == before["settings"] + 1

            tools = "/api/v1/p/%s/tools/%s" % (self.pid, quote("工具", safe=""))
            status, _h, body = caller.json(
                "PUT", tools,
                {"name": "工具", "description": "", "url": "https://example.com"},
            )
            assert status == 200, body
            before = _parts(self.pid)
            status, _h, body = caller.json("DELETE", tools)
            assert status == 200 and body == {"removed": True}, body
            assert _parts(self.pid)["settings"] == before["settings"] + 1

    def test_people_deletes_bump(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            admins = "/api/v1/p/%s/admins" % self.pid
            assert caller.json("POST", admins, {"name": "甲"})[0] == 201
            before = _parts(self.pid)
            status, _h, body = caller.json("DELETE", admins + "/" + quote("甲", safe=""))
            assert status == 200 and body == {"removed": True}, body
            assert _parts(self.pid)["settings"] == before["settings"] + 1

            ip_url = "/api/v1/p/%s/ip-mapping/10.7.7.7" % self.pid
            assert caller.json("PUT", ip_url, {"ip": "10.7.7.7", "name": "甲"})[0] == 200
            before = _parts(self.pid)
            status, _h, body = caller.json("DELETE", ip_url)
            assert status == 200 and body == {"removed": True}, body
            assert _parts(self.pid)["settings"] == before["settings"] + 1

    def test_profile_rename_bumps_default(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _h, body = caller.json(
                "POST", "/api/v1/profiles", {"name": "改名机型", "copy_from": None}
            )
            assert status == 201, body
            model_id = body["profile"]["id"]
            before = _parts(storage.DEFAULT_PROFILE)
            status, _h, body = caller.json(
                "PATCH", "/api/v1/profiles/%s" % model_id, {"name": "改名之后"}
            )
            assert status == 200, body
            after = _parts(storage.DEFAULT_PROFILE)
            assert after["settings"] == before["settings"] + 1, (before, after)

    def test_conflict_and_404_do_not_bump(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            admins = "/api/v1/p/%s/admins" % self.pid
            assert caller.json("POST", admins, {"name": "甲"})[0] == 201
            before = _parts(self.pid)
            status, _h, body = caller.json("POST", admins, {"name": "甲"})
            assert status == 409, body
            assert _parts(self.pid) == before

            default_before = _parts(storage.DEFAULT_PROFILE)
            status, _h, _body = caller.json(
                "PATCH", "/api/v1/profiles/nope", {"name": "x"}
            )
            assert status == 404
            assert _parts(storage.DEFAULT_PROFILE) == default_before


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
            revision.bump("pure", "files")
            assert revision.snapshot("pure")["parts"]["files"] == 3
        finally:
            monkeypatch.setattr(builtins, "open", original)

    def test_snapshot_is_cheap(self):
        """本地近似：真实 P99 由 M4-05 的 `bench_api.py` 在真实服务上测，这里只防退化。"""
        import time

        revision.reset()
        started = time.perf_counter()
        for _ in range(2000):
            revision.snapshot("fast")
        elapsed = time.perf_counter() - started
        assert elapsed < 0.2, "2000 次 snapshot 用了 %.3f s，明显超出 5 ms/次的量级" % elapsed


# 覆盖映射（本文件不重复造轮子）：
# - `POST /import-url` 的持久化分支：本文件 `test_import_url_bumps_only_when_persisted`，
#   `tests/test_api_import.py` 另有行为用例。
# - `POST /tasks` 的入队/进度/收口递增：`tests/test_api_tasks.py::TestRevisionBumps`
# - `PUT /dl/{item}`、compute/extract：`tests/test_api_dl.py`
# - `DELETE /files/{name}` 及引用分量：`tests/test_api_delete.py`
# - `PATCH/DELETE /profiles/{pid}`：`tests/test_api_profiles.py`（本文件也覆盖成功 +1）
# - UC-40 的 10 秒体感属跨设备项，仍记「未验证 · 待人工确认」；本文件只钉住「B 写后 A 的
#   下一次 `/revision` 拿到新计数」，不声称前端黄横幅已被自动化覆盖。
