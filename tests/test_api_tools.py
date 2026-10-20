"""外部工具菜单 CRUD（M3-15 / INH-1900；规范 §7.10、§8.3、§11.2、UC-36）。

判据：

1. 读=全部角色；PUT/DELETE=部署者。403 时 `tools.json` 的 sha 与 `settings` 修订号都不动。
2. 名称 1–100 字符、无控制字符；名称进 `DELETE /tools/{name}` 的路径段，创建端拒 `/`、`\`
   与任何字面 `%XX`（否则加得进来、删不出去）；删除端宽松，给 v1 存量行留出路。
3. 说明可空；给出时去空白后 ≤100 字符、无控制字符。
4. URL 只允许 http/https，且必须有主机：`urllib.parse.urlparse` 判 scheme，不做前缀匹配；
   `javascript:`、`ftp://`、`data:`、空串、无 scheme、`http://`、内部空白/控制字符全 400。
5. 名称唯一：改名撞已有工具 → 409 `FILE_EXISTS`，原条目与字节不动。
6. 成功写补一次 `settings`；读、失败写、删 0 条都不补，删 0 条也不写文件。
"""

from __future__ import annotations

import hashlib
import os
import threading
import urllib.parse

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage
from app.services import revision

NAME = "构建工具"
URL = "https://example.com/build?x=1"
DESCRIPTION = "生成固件的脚本"

BAD_NAMES = [
    "",
    "   ",
    "a" * 101,
    "工具\x01名",
    "工具/名",
    "工具\\名",
    "工具%20名",
    "工具%2f名",
    "工具%2F名",
    "工具%00名",
]

BAD_URLS = [
    "",
    "   ",
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "ftp://example.com/x",
    "data:text/html,<script>alert(1)</script>",
    "example.com/x",
    "//example.com/x",
    "http://",
    "https:example.com",
    "http://exa mple.com/x",
    "http://example.com/\npath",
    "java\nscript:alert(1)",
    "http://example.com/\x01",
]


def _url(pid, name=None):
    base = "/api/v1/p/%s/tools" % pid
    if name is None:
        return base
    return base + "/" + urllib.parse.quote(str(name), safe="")


def _path(pid):
    return storage.get_tools_file(pid)


def _digest(pid):
    path = _path(pid)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _settings_rev(pid):
    return revision.snapshot(pid)["parts"]["settings"]


def _put(caller, pid, path_name, name=NAME, url=URL, description=DESCRIPTION):
    return caller.json(
        "PUT",
        _url(pid, path_name),
        {"name": name, "description": description, "url": url},
    )


class Base:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("tools")


class TestRead(Base):
    def test_every_role_may_read(self):
        with make_server() as server:
            status, _h, body = _put(as_role(server, "deployer"), self.pid, NAME)
            assert status == 200, body
            for role in ("visitor", "member", "admin", "deployer"):
                status, _h, listed = as_role(server, role).json("GET", _url(self.pid))
                assert status == 200, (role, listed)
                assert [item["name"] for item in listed["items"]] == [NAME], (role, listed)
                assert listed["items"][0]["url"] == URL, (role, listed)

    def test_read_does_not_create_the_file_or_bump(self):
        assert not os.path.exists(_path(self.pid))
        before = _settings_rev(self.pid)
        with make_server() as server:
            status, _h, body = as_role(server, "visitor").json("GET", _url(self.pid))
        assert status == 200 and body == {"items": []}, body
        assert not os.path.exists(_path(self.pid)), "读接口不得创建 tools.json"
        assert _settings_rev(self.pid) == before


class TestPermissions(Base):
    def setup_method(self):
        super().setup_method()
        with make_server() as server:
            status, _h, body = _put(as_role(server, "deployer"), self.pid, NAME)
            assert status == 200, body

    @pytest.mark.parametrize("role", ["visitor", "member", "admin"])
    def test_non_deployers_cannot_write(self, role):
        sha_before = _digest(self.pid)
        rev_before = _settings_rev(self.pid)
        with make_server() as server:
            caller = as_role(server, role)
            status, _h, body = _put(caller, self.pid, NAME, name="改名")
            assert status == 403, (role, body)
            status, _h, body = caller.json("DELETE", _url(self.pid, NAME))
            assert status == 403, (role, body)
        assert _digest(self.pid) == sha_before
        assert _settings_rev(self.pid) == rev_before

    def test_deployer_can_create_update_and_delete(self):
        rev_before = _settings_rev(self.pid)
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _h, body = _put(
                caller, self.pid, "新工具", name="新工具", url="http://127.0.0.1:8080/a"
            )
            assert status == 200, body
            assert body["item"]["name"] == "新工具", body
            assert body["item"]["added_at"], body
            assert _settings_rev(self.pid) == rev_before + 1

            status, _h, body = _put(
                caller, self.pid, "新工具", name="新工具", description="改过"
            )
            assert status == 200, body
            assert body["item"]["description"] == "改过", body
            status, _h, listed = as_role(server, "visitor").json("GET", _url(self.pid))
            rows = [item for item in listed["items"] if item["name"] == "新工具"]
            assert len(rows) == 1, listed
            assert rows[0]["description"] == "改过", listed

            status, _h, body = caller.json("DELETE", _url(self.pid, "新工具"))
            assert status == 200 and body == {"removed": True}, body
            assert _settings_rev(self.pid) == rev_before + 3


class TestValidation(Base):
    @pytest.mark.parametrize("name", BAD_NAMES)
    def test_bad_names_are_rejected_before_disk(self, name):
        sha_before = _digest(self.pid)
        rev_before = _settings_rev(self.pid)
        with make_server() as server:
            status, _h, body = _put(
                as_role(server, "deployer"), self.pid, "_", name=name
            )
        assert status == 400, (name, body)
        assert body["error"]["code"] == "INVALID_INPUT", (name, body)
        assert body["error"]["detail"]["field"] == "name", (name, body)
        assert _digest(self.pid) == sha_before
        assert _settings_rev(self.pid) == rev_before

    @pytest.mark.parametrize("name", ["100%达标", "完成度%zz", "值班 50%", "O'Brien"])
    def test_names_that_can_still_be_deleted_roundtrip(self, name):
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _h, body = _put(caller, self.pid, name, name=name)
            assert status == 200, (name, body)
            status, _h, body = caller.json("DELETE", _url(self.pid, name))
            assert status == 200 and body == {"removed": True}, (name, body)

    @pytest.mark.parametrize("url", BAD_URLS)
    def test_bad_urls_are_rejected_before_disk(self, url):
        sha_before = _digest(self.pid)
        rev_before = _settings_rev(self.pid)
        with make_server() as server:
            status, _h, body = _put(
                as_role(server, "deployer"), self.pid, "_", url=url
            )
        assert status == 400, (url, body)
        assert body["error"]["code"] == "INVALID_INPUT", (url, body)
        assert body["error"]["detail"]["field"] == "url", (url, body)
        assert _digest(self.pid) == sha_before
        assert _settings_rev(self.pid) == rev_before

    @pytest.mark.parametrize("url", ["https://example.com/x", "http://127.0.0.1:8080/a"])
    def test_http_and_https_urls_are_accepted(self, url):
        with make_server() as server:
            status, _h, body = _put(
                as_role(server, "deployer"), self.pid, NAME, url=url
            )
        assert status == 200, (url, body)
        assert body["item"]["url"] == url, (url, body)

    def test_description_is_optional_but_bounded(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _h, body = _put(caller, self.pid, NAME, description="")
            assert status == 200, body
            assert body["item"]["description"] == "", body
            for description in ("a" * 101, "说明\x01"):
                status, _h, body = _put(
                    caller, self.pid, NAME, description=description
                )
                assert status == 400, (description, body)
                assert body["error"]["detail"]["field"] == "description", body

    def test_duplicate_name_is_a_conflict_and_keeps_the_original(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            assert _put(caller, self.pid, NAME)[0] == 200
            assert _put(caller, self.pid, "另一个", name="另一个")[0] == 200
            sha_before = _digest(self.pid)
            rev_before = _settings_rev(self.pid)
            status, _h, body = _put(caller, self.pid, NAME, name="另一个")
            assert status == 409, body
            assert body["error"]["code"] == "FILE_EXISTS", body
            assert body["error"]["detail"]["field"] == "name", body
            assert _digest(self.pid) == sha_before
            assert _settings_rev(self.pid) == rev_before
            status, _h, listed = as_role(server, "visitor").json("GET", _url(self.pid))
        assert sorted(item["name"] for item in listed["items"]) == sorted([NAME, "另一个"])


class TestDelete(Base):
    def test_delete_miss_does_not_write_or_bump(self):
        with make_server() as server:
            caller = as_role(server, "deployer")
            assert _put(caller, self.pid, NAME)[0] == 200
            status, _h, body = caller.json("DELETE", _url(self.pid, NAME))
            assert status == 200 and body == {"removed": True}, body
            sha_before = _digest(self.pid)
            rev_before = _settings_rev(self.pid)
            status, _h, body = caller.json("DELETE", _url(self.pid, NAME))
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

        def worker(name):
            with make_server() as server:
                barrier.wait()
                status, _h, body = _put(
                    as_role(server, "deployer"), self.pid, name, name=name
                )
                with lock:
                    outcomes.append((name, status, body))

        threads = [threading.Thread(target=worker, args=(name,)) for name in ("甲", "乙")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert [status for _name, status, _body in outcomes] == [200, 200], outcomes
        storage.set_active_profile(self.pid)
        names = sorted(item["name"] for item in storage.load_tools())
        assert names == ["乙", "甲"], names
