"""变量绑定 CRUD（M3-14 / INH-1899；规范 §7.5、§8.3、§11.2、UC-35）。

判据落在字节、修订号与真实对比结果上：

1. 读=全部角色；PUT/DELETE=部署者。403 时 `bindings.json` 的 sha 与 `settings` 修订号都不动。
2. 至少 2 个**有效**变量（文件存在且路径非空）；不足 → 400 `INVALID_INPUT` +
   `detail.field="variables"`，且在碰磁盘之前。空行/失效行被丢弃，但只剩 <2 个有效行仍拒。
3. 组名进 `DELETE /bindings/{group}` 的路径段：`/`、`\\` 与任何字面 `%XX` 在创建端拒
   （前端 `api.seg()` 会编成 `%2F`/`%2520`，撞 §16.8 守卫后「加得进来、删不出去」）；
   删除端保持宽松，给 v1 存量行留出路。
4. 改名撞已有组 → 409 `FILE_EXISTS`，两组都在、字节不动、修订号不动。
5. 成功写补一次 `settings`；读、失败写、删 0 条都不补，且删 0 条不写文件。
6. UC-35：两个变量的组在真实 `POST /compare` 结果里带 `bound_group`。
"""

from __future__ import annotations

import hashlib
import os
import threading
import urllib.parse

import pytest
from authz_fixtures import as_role, make_server, seed_roles, write_target

from app.core import storage
from app.services import revision

FILE_A = "scenario_a.xml"
FILE_B = "scenario_b.xml"

V1 = (
    "<R>\n"
    "  <params>\n"
    "    <item>1</item>\n"
    "    <item>2</item>\n"
    "    <timeout>10</timeout>\n"
    "  </params>\n"
    "</R>\n"
)
V2 = (
    "<R>\n"
    "  <params>\n"
    "    <item>9</item>\n"
    "    <item>8</item>\n"
    "    <timeout>30</timeout>\n"
    "  </params>\n"
    "</R>\n"
)

GROUP = "超时组"
VARIABLES = [
    {"file": FILE_A, "path": FILE_A + "/R/params/item"},
    {"file": FILE_A, "path": FILE_A + "/R/params/timeout"},
]


def _url(pid, group=None):
    base = "/api/v1/p/%s/bindings" % pid
    if group is None:
        return base
    return base + "/" + urllib.parse.quote(str(group), safe="")


def _path(pid):
    return storage.get_bindings_file(pid)


def _digest(pid):
    path = _path(pid)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _settings_rev(pid):
    return revision.snapshot(pid)["parts"]["settings"]


def _put(caller, pid, path_group, name, variables):
    return caller.json(
        "PUT",
        _url(pid, path_group),
        {"group_name": name, "variables": variables},
    )


def _create(caller, pid, name=GROUP, variables=None):
    return _put(caller, pid, name, name, VARIABLES if variables is None else variables)


class Base:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("bind")
        write_target(self.pid, FILE_A, V1.encode("utf-8"))
        write_target(self.pid, FILE_B, b"<B/>\n")


class TestRead(Base):
    def test_every_role_may_read(self):
        with make_server() as server:
            status, _h, body = _create(as_role(server, "deployer"), self.pid)
            assert status == 200, body
            for role in ("visitor", "member", "admin", "deployer"):
                status, _h, body = as_role(server, role).json("GET", _url(self.pid))
                assert status == 200, (role, body)
                assert body["items"] == [
                    {"group_name": GROUP, "variables": VARIABLES}
                ], (role, body)

    def test_read_does_not_create_the_file_or_bump(self):
        assert not os.path.exists(_path(self.pid))
        before = _settings_rev(self.pid)
        with make_server() as server:
            status, _h, body = as_role(server, "visitor").json("GET", _url(self.pid))
        assert status == 200 and body == {"items": []}, body
        assert not os.path.exists(_path(self.pid)), "读接口不得创建 bindings.json"
        assert _settings_rev(self.pid) == before

    def test_read_only_projects_known_keys(self):
        storage.set_active_profile(self.pid)
        storage.save_bindings(
            [
                {
                    "group_name": GROUP,
                    "variables": VARIABLES,
                    "updated_at": "2026-01-01 00:00:00",
                }
            ]
        )
        with make_server() as server:
            status, _h, body = as_role(server, "member").json("GET", _url(self.pid))
        assert status == 200, body
        assert body["items"] == [{"group_name": GROUP, "variables": VARIABLES}], body


class TestPermissions(Base):
    def setup_method(self):
        super().setup_method()
        with make_server() as server:
            status, _h, body = _create(as_role(server, "deployer"), self.pid)
            assert status == 200, body

    @pytest.mark.parametrize("role", ["visitor", "member", "admin"])
    def test_non_deployers_cannot_write(self, role):
        sha_before = _digest(self.pid)
        rev_before = _settings_rev(self.pid)
        with make_server() as server:
            caller = as_role(server, role)
            status, _h, body = _put(caller, self.pid, GROUP, "改名", VARIABLES)
            assert status == 403, (role, body)
            status, _h, body = caller.json("DELETE", _url(self.pid, GROUP))
            assert status == 403, (role, body)
        assert _digest(self.pid) == sha_before
        assert _settings_rev(self.pid) == rev_before

    def test_deployer_can_rename_and_delete(self):
        rev_before = _settings_rev(self.pid)
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _h, body = _put(caller, self.pid, GROUP, "新名字", VARIABLES)
            assert status == 200, body
            assert body["item"]["group_name"] == "新名字"
            assert _settings_rev(self.pid) == rev_before + 1
            status, _h, body = caller.json("DELETE", _url(self.pid, "新名字"))
            assert status == 200, body
            assert body == {"removed": True}
            assert _settings_rev(self.pid) == rev_before + 2


class TestValidation(Base):
    @pytest.mark.parametrize(
        "variables",
        [
            [],
            [VARIABLES[0]],
            [VARIABLES[0], {"file": FILE_A, "path": "   "}],
            [VARIABLES[0], {"file": "missing.xml", "path": "missing.xml/R"}],
            [{"file": FILE_B, "path": ""}, {"file": "missing.xml", "path": "x"}],
            "not-a-list",
        ],
    )
    def test_fewer_than_two_valid_variables_is_rejected_before_disk(self, variables):
        sha_before = _digest(self.pid)
        rev_before = _settings_rev(self.pid)
        with make_server() as server:
            status, _h, body = _put(
                as_role(server, "deployer"), self.pid, GROUP, GROUP, variables
            )
        assert status == 400, body
        assert body["error"]["code"] == "INVALID_INPUT", body
        assert body["error"]["detail"]["field"] == "variables", body
        assert _digest(self.pid) == sha_before
        assert _settings_rev(self.pid) == rev_before

    def test_invalid_rows_are_dropped_and_two_valid_rows_survive(self):
        variables = [
            VARIABLES[0],
            {"file": FILE_A, "path": ""},
            VARIABLES[1],
            {"file": "missing.xml", "path": "x"},
        ]
        with make_server() as server:
            status, _h, body = _put(
                as_role(server, "deployer"), self.pid, GROUP, GROUP, variables
            )
            assert status == 200, body
            status, _h, listed = as_role(server, "visitor").json("GET", _url(self.pid))
        assert listed["items"][0]["variables"] == VARIABLES, listed

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "   ",
            "a" * 101,
            "组\x01名",
            "组/名",
            "组\\名",
            "组%20名",
            "组%2f名",
            "组%2F名",
            "组%00名",
        ],
    )
    def test_bad_group_names_are_rejected_in_the_body(self, name):
        sha_before = _digest(self.pid)
        rev_before = _settings_rev(self.pid)
        with make_server() as server:
            status, _h, body = _put(
                as_role(server, "deployer"), self.pid, "_", name, VARIABLES
            )
        assert status == 400, (name, body)
        assert body["error"]["code"] == "INVALID_INPUT", (name, body)
        assert body["error"]["detail"]["field"] == "group_name", (name, body)
        assert _digest(self.pid) == sha_before
        assert _settings_rev(self.pid) == rev_before

    @pytest.mark.parametrize(
        "name", ["100%达标", "完成度%zz", "值班 50%", "50%", "O'Brien"]
    )
    def test_names_that_can_still_be_deleted_roundtrip(self, name):
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _h, body = _create(caller, self.pid, name)
            assert status == 200, (name, body)
            assert body["item"]["group_name"] == name, (name, body)
            status, _h, body = caller.json("DELETE", _url(self.pid, name))
            assert status == 200, (name, body)
            assert body == {"removed": True}, (name, body)

    def test_group_name_is_trimmed(self):
        with make_server() as server:
            status, _h, body = _create(
                as_role(server, "deployer"), self.pid, "  超时组  "
            )
        assert status == 200, body
        assert body["item"]["group_name"] == GROUP, body


class TestCreateRenameDelete(Base):
    def test_create_and_update_keep_one_group(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _h, body = _create(caller, self.pid)
            assert status == 200, body
            assert body["item"] == {"group_name": GROUP, "variables": VARIABLES}, body

            changed = [VARIABLES[0], {"file": FILE_B, "path": FILE_B + "/B"}]
            status, _h, body = _put(caller, self.pid, GROUP, GROUP, changed)
            assert status == 200, body
            status, _h, listed = as_role(server, "visitor").json("GET", _url(self.pid))
        assert listed["items"] == [{"group_name": GROUP, "variables": changed}], listed

    def test_rename_moves_the_group(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            assert _create(caller, self.pid)[0] == 200
            status, _h, body = _put(caller, self.pid, GROUP, "新组", VARIABLES)
            assert status == 200, body
            status, _h, listed = as_role(server, "visitor").json("GET", _url(self.pid))
        assert [item["group_name"] for item in listed["items"]] == ["新组"], listed

    def test_rename_onto_an_existing_group_conflicts_and_keeps_both(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            assert _create(caller, self.pid, GROUP)[0] == 200
            assert _create(caller, self.pid, "另一组")[0] == 200
            sha_before = _digest(self.pid)
            rev_before = _settings_rev(self.pid)
            status, _h, body = _put(caller, self.pid, GROUP, "另一组", VARIABLES)
            assert status == 409, body
            assert body["error"]["code"] == "FILE_EXISTS", body
            assert body["error"]["detail"]["field"] == "group_name", body
            assert _digest(self.pid) == sha_before
            assert _settings_rev(self.pid) == rev_before
            status, _h, listed = as_role(server, "visitor").json("GET", _url(self.pid))
        assert sorted(item["group_name"] for item in listed["items"]) == sorted(
            [GROUP, "另一组"]
        ), listed

    def test_delete_reports_removed_and_does_not_bump_or_write_on_miss(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            assert _create(caller, self.pid)[0] == 200
            rev_before = _settings_rev(self.pid)
            status, _h, body = caller.json("DELETE", _url(self.pid, GROUP))
            assert status == 200 and body == {"removed": True}, body
            assert _settings_rev(self.pid) == rev_before + 1

            sha_before = _digest(self.pid)
            rev_before = _settings_rev(self.pid)
            status, _h, body = caller.json("DELETE", _url(self.pid, GROUP))
            assert status == 200 and body == {"removed": False}, body
            assert _digest(self.pid) == sha_before
            assert _settings_rev(self.pid) == rev_before

    def test_delete_on_a_missing_file_does_not_create_it(self):
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").json(
                "DELETE", _url(self.pid, "不存在")
            )
        assert status == 200 and body == {"removed": False}, body
        assert not os.path.exists(_path(self.pid))

    def test_two_concurrent_creates_both_survive(self):
        barrier = threading.Barrier(2)
        outcomes = []
        lock = threading.Lock()

        def worker(group):
            with make_server() as server:
                barrier.wait()
                status, _h, body = _create(as_role(server, "deployer"), self.pid, group)
                with lock:
                    outcomes.append((group, status, body))

        threads = [threading.Thread(target=worker, args=(name,)) for name in ("甲", "乙")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert [status for _name, status, _body in outcomes] == [200, 200], outcomes
        storage.set_active_profile(self.pid)
        groups = sorted(item["group_name"] for item in storage.load_bindings())
        assert groups == ["乙", "甲"], groups


class TestCompareUC35(Base):
    def test_two_variables_carry_the_purple_group_name(self):
        storage.set_active_profile(self.pid)
        storage.save_config_file(FILE_A, V2.encode("utf-8"))
        directory = os.path.join(storage.get_archive_root_dir(self.pid), FILE_A)
        archives = sorted(
            name for name in os.listdir(directory) if not name.endswith(".diff.json")
        )
        assert archives, "历史版本没有落盘，测试前提不成立"
        archive = archives[0]

        with make_server() as server:
            status, _h, body = _create(as_role(server, "deployer"), self.pid)
            assert status == 200, body
            status, _h, result = as_role(server, "member").json(
                "POST",
                "/api/v1/p/%s/compare" % self.pid,
                {
                    "base": {"file": FILE_A, "version": "archive:%s" % archive},
                    "target": {"file": FILE_A, "version": "current"},
                },
            )
        assert status == 200, result
        items = result["structural"]["items"]
        timeout = [item for item in items if item["loc"].endswith("timeout[1]")]
        first_item = [item for item in items if item["loc"] == "/R[1]/params[1]/item[1]"]
        assert timeout and first_item, items
        assert timeout[0]["bound_group"] == GROUP, items
        assert first_item[0]["bound_group"] == GROUP, items
