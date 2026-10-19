"""IP 对应表与管理员名单（卡 13 · M3-13 / INH-1898；规范 §7.9、§4.2、§11.1、§16.2 UC-34、UC-44）。

本卡改的是**谁能写**，所以判据一律落在字节与真实身份上，不落在状态码上：

1. **权限**（§4.2 / §16.3 / UC-44）：读=全部角色；四个写接口=部署者。非部署者打进来 403，
   并且 `ip_mapping.json` / `admin_users.json` 的 **sha256 与 mtime 都不许动**、修订号不涨
   ——这一条是本卡的安全底线，所以每个角色 × 每个写接口各打一遍真 HTTP 请求。
2. **非法输入在碰磁盘之前**（§7.9 / §11.2）：`999.1.1.1`、`abc`、空串、`10.0.0.1/24` →
   400 `INVALID_INPUT` + `detail.field="ip"`；姓名空/超长/含控制字符 → 400 `field="name"`；
   重名管理员 → 409 `FILE_EXISTS`（**不是** 400，§7.1 的状态码表）。三类的字节与计数都不变。
3. **改 IP = 同一次机型锁里先删后写**（§7.9）：`PUT /ip-mapping/10.0.0.1` + body `10.0.0.2`
   ⇒ 旧行消失、新行存在、总数不变。`TestNoLostUpdate` 用「竞争方必须是另一个线程」的既定
   写法（`docs/know-how.md` 第 2 / 4 节）证明它不是一次「锁外读 + 锁内写」。
4. **身份实时生效**（§4.1 / 不许加缓存）：UC-34 端到端——登记当前访问者的 IP 并加为管理员
   之后，下一次 `/session` 就是管理员；移除之后下一次 `GET /review` 立刻 403。附带钉住
   「IP 按 `ipaddress` 规范化后才落盘」：登记表里的写法与 `auth.normalize_ip` 算出来的客户端
   地址必须同一个口径，否则「登记了、加了管理员、但不生效」这种最难查的失败会静悄悄发生。
5. **修订号**（全局约束 / 决策 16）：成功写补一次 `settings`；读、失败写、以及「删了 0 条」
   都不补，且后者一个字节都不写。
6. **§11.1 的「IP 识别不是强认证，只适用于可信内网」这句提示要在界面上出现**，不许只在文档里。
   它在真实 DOM 上执法（`app/web/tests/tests.js` 的 M3-13 用例）；本文件**不**用 grep 前端源码
   的方式执法（R45 / INH-1915：字面串断言既挡不住文案搬家，也证明不了它真的显示出来）。

响应形状取**后端既有先例**：`DELETE /sources/{name}` ⇒ `{removed: bool}`
（`app/server/api_sources.py:86`）、`POST /sources` ⇒ 201 `{item}`。mock 原先在这两条
尚不存在的路由上写的是 `{deleted: true}` / `{ok: true}`，那是**未定契约**（know-how 第 2 节），
本卡同 PR 一并对齐，见 `app/web/assets/js/mock/mock-api.js`。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest
from authz_fixtures import (
    ADMIN_NAME,
    MEMBER_NAME,
    XFF_ADMIN,
    XFF_MEMBER,
    as_role,
    make_server,
    seed_roles,
)

from app.core import device_models, storage
from app.services import people, revision

#: `updated_at` 的格式（与 `storage` 自己写的逐字一致，§7.1 服务器本地时间字符串）。
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

#: 四个写接口（§7.9：全部只给部署者）。`rest` 里的 IP/姓名先按前端 `api.seg()` 的口径编码。
WRITE_ROUTES = [
    ("PUT", "/ip-mapping/{ip}", {"ip": "10.7.7.7", "name": "张三"}),
    ("DELETE", "/ip-mapping/{ip}", None),
    ("POST", "/admins", {"name": "新管理员"}),
    ("DELETE", "/admins/{name}", None),
]
#: 上面三条 DELETE/PUT 用的路径值（`{ip}` / `{name}` 在 `_write_url` 里替换）。
WRITE_IP = "10.7.7.7"
WRITE_ADMIN_NAME = "管理员已存在的那位"

#: §7.9 明写的非法 IP（含空串与网段写法；`010.0.0.1` 的前导零 3.9 也拒）。
BAD_IPS = ["999.1.1.1", "abc", "", "10.0.0.1/24", "10.1.1", "1.2.3.4.5", "010.0.0.1", "  "]
#: 可以安全出现在**路径段**里的那几枚非法值（空格按前端 `api.seg()` 的口径编码成 `%20`）。
#: 不含 `10.0.0.1/24`：路径里的编码分隔符（`%2F`）在 `_dispatch` 就被 §16.8 第 3 条拦成
#: 「请求路径含编码的分隔符或空字节」，那只 400 不带 `detail.field`，与 IP 校验无关，
#: 已由 `tests/test_api_security.py` 的穿越一族执法；网段写法作为 **body** 值仍在 BAD_IPS 里。
BAD_PATH_IPS = ["999.1.1.1", "abc", "010.0.0.1", "%20%20"]
#: §11.2：姓名 = 去空白后 1–100 字符、无控制字符。
BAD_NAMES = ["", "   ", "a" * 101, "张\x01三", "李\n四", "王\x7f"]


# ---------------------------------------------------------------------------
# 帮助函数
# ---------------------------------------------------------------------------


def _file(pid: str, admins: bool = False) -> str:
    return storage.get_admin_users_file(pid) if admins else storage.get_ip_mapping_file(pid)


def _state(pid: str) -> Dict[str, Any]:
    """两份人员文件的 (内容 sha256, mtime_ns)。

    只看内容 hash 挡不住「同长度原地改写」，所以和 `test_api_security.schedule_state`
    一样把 mtime 一起取；文件不存在就是 `None`（「GET 不许把文件建出来」也靠它）。
    """
    out: Dict[str, Any] = {}
    for path in (_file(pid), _file(pid, admins=True)):
        if not os.path.isfile(path):
            out[path] = None
            continue
        with open(path, "rb") as fh:
            raw = fh.read()
        out[path] = (hashlib.sha256(raw).hexdigest(), os.stat(path).st_mtime_ns)
    return out


def _rows(pid: str, admins: bool = False) -> Optional[List[Any]]:
    path = _file(pid, admins)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def _seed(pid: str, mapping: Optional[List[Any]] = None, admins: Optional[List[Any]] = None) -> None:
    with storage.use_profile(pid):
        if mapping is not None:
            storage.save_ip_mapping(mapping)
        if admins is not None:
            storage.save_admin_users(admins)


def _parts(pid: str) -> Dict[str, int]:
    return revision.snapshot(pid)["parts"]


def _url(pid: str, rest: str) -> str:
    return "/api/v1/p/%s%s" % (pid, rest)


def _seg(value: str) -> str:
    """与前端 `api.seg()`（`encodeURIComponent`）同一口径编码路径段。"""
    return urllib.parse.quote(value, safe="")


def _write_url(pid: str, rest: str) -> str:
    return _url(pid, rest.replace("{ip}", _seg(WRITE_IP)).replace("{name}", _seg(WRITE_ADMIN_NAME)))


def _is_timestamp(value: Any) -> bool:
    try:
        datetime.strptime(str(value), TIME_FORMAT)
    except (TypeError, ValueError):
        return False
    return True


@pytest.fixture()
def pid() -> str:
    """一个干净机型：人员表与管理员名单都由用例自己摆。"""
    return device_models.add_model("人员机型")


@pytest.fixture()
def seeded(pid: str) -> str:
    """预置 10.0.0.1→张三、10.0.0.2→李四 与管理员「张三」，两份文件都真实存在。"""
    _seed(
        pid,
        mapping=[
            {"ip": "10.0.0.1", "name": "张三", "updated_at": "2026-10-17 09:00:00"},
            {"ip": "10.0.0.2", "name": "李四", "updated_at": "2026-10-17 09:30:00"},
        ],
        admins=[{"name": "张三", "updated_at": "2026-10-17 09:00:00"}],
    )
    return pid


# ---------------------------------------------------------------------------
# 1. GET（§7.9：全部角色可读）
# ---------------------------------------------------------------------------


class TestGet:
    @pytest.mark.parametrize("role", ["visitor", "member", "admin", "deployer"])
    def test_every_role_can_read_both_lists(self, seeded, role):
        with make_server() as server:
            caller = as_role(server, role)
            ip_status, _, ips = caller.json("GET", _url(seeded, "/ip-mapping"))
            ad_status, _, admins = caller.json("GET", _url(seeded, "/admins"))
        assert ip_status == 200 and ad_status == 200, "%s 应当读得到（§7.9 读=全部）" % role
        assert [row["ip"] for row in ips["items"]] == ["10.0.0.1", "10.0.0.2"]
        assert [row["name"] for row in ips["items"]] == ["张三", "李四"]
        assert admins["items"] == [{"name": "张三", "updated_at": "2026-10-17 09:00:00"}]

    def test_payload_has_exactly_the_spec_keys(self, seeded):
        """§5.2 的字段形状：`{ip,name,updated_at}` / `{name,updated_at}`，一个不多一个不少。"""
        with make_server() as server:
            _, _, ips = as_role(server, "deployer").json("GET", _url(seeded, "/ip-mapping"))
            _, _, admins = as_role(server, "deployer").json("GET", _url(seeded, "/admins"))
        assert set(ips["items"][0]) == {"ip", "name", "updated_at"}
        assert set(admins["items"][0]) == {"name", "updated_at"}

    def test_missing_files_read_as_empty_lists_without_creating_them(self, pid):
        """空表是 200 `{"items": []}`，而且 GET **不许**把两份文件建出来（读不写盘）。"""
        before = _state(pid)
        assert before[_file(pid)] is None, "夹具没给这个机型预置人员文件"
        with make_server() as server:
            _, _, ips = as_role(server, "visitor").json("GET", _url(pid, "/ip-mapping"))
            _, _, admins = as_role(server, "visitor").json("GET", _url(pid, "/admins"))
        assert ips == {"items": []} and admins == {"items": []}
        assert _state(pid) == before

    def test_read_never_bumps_revision(self, seeded):
        before = _parts(seeded)
        with make_server() as server:
            caller = as_role(server, "deployer")
            for _ in range(3):
                caller.json("GET", _url(seeded, "/ip-mapping"))
                caller.json("GET", _url(seeded, "/admins"))
        assert _parts(seeded) == before, "读接口不许递增修订号（全局约束）"

    def test_a_malformed_row_is_skipped_instead_of_crashing_the_read(self, pid):
        """旧文件里可能有非字典条目（v1 界面没校验过）：读取路径跳过它，而不是把它当一个人回显。

        **这条只能在 service 层钉，HTTP 面上到不了这里**（实测）：同一份文件下
        `storage.resolve_person_by_ip`（`app/core/storage.py:1217`）会对每个请求抛
        `AttributeError: 'str' object has no attribute 'get'` ⇒ 该机型任何接口都是 500，
        handler 根本轮不到。那是 `storage` 的既有健壮性问题（`app/pages/management.py` 与
        `app/utils/auth.py` 走同一只函数，M4-07 才删那两处），**本卡不动它**；
        这里的过滤只是「别让读路径比身份解析更脆」。已在卡报告里点名。
        """
        _seed(pid, mapping=[{"ip": "10.0.0.1", "name": "张三", "updated_at": "2026-10-17 09:00:00"}, "垃圾行"])
        assert people.list_ip_mapping(pid) == {
            "items": [{"ip": "10.0.0.1", "name": "张三", "updated_at": "2026-10-17 09:00:00"}]
        }
        # 写路径同样不许被垃圾行带跑：改 IP 只动它该动的那一行，旧条目原样留着。
        people.put_ip_mapping(pid, "10.0.0.1", {"ip": "10.0.0.1", "name": "张三丰"})
        rows = _rows(pid)
        assert [row for row in rows if isinstance(row, dict)][0]["name"] == "张三丰"
        assert "垃圾行" in rows, "不是自己写的行不该被这次写悄悄抹掉"


# ---------------------------------------------------------------------------
# 2. UC-34：登记当前 IP + 加为管理员 ⇒ 身份实时生效
# ---------------------------------------------------------------------------


class TestUc34IdentityTakesEffect:
    def test_registering_current_ip_then_adding_admin_makes_the_next_request_admin(self, pid):
        """端到端（真实后端）：未登记访客 → 部署者登记其 IP、加为管理员 → 下一次请求即管理员。

        这是 UC-34 的服务端那一半；「不整页刷新」的前端那一半由
        `app/web/tests/tests.js` 的 M3-13 用例执法（保存后重新 `GET /session` 并重画顶栏与侧栏）。
        """
        with make_server() as server:
            member = as_role(server, "member")
            deployer = as_role(server, "deployer")
            session_url = "/api/v1/session?profile=%s" % _seg(pid)

            _, _, before = member.json("GET", session_url)
            assert before["identity"]["registered"] is False
            assert before["identity"]["is_admin"] is False
            assert before["identity"]["name"] == XFF_MEMBER, "未登记时姓名回落为 IP（§4.1）"

            status, _, put = deployer.json(
                "PUT",
                _url(pid, "/ip-mapping/%s" % _seg(XFF_MEMBER)),
                {"ip": XFF_MEMBER, "name": MEMBER_NAME},
            )
            assert status == 200, put
            status, _, added = deployer.json("POST", _url(pid, "/admins"), {"name": MEMBER_NAME})
            assert status == 201, added

            # 没有任何缓存要失效：下一次请求就是新身份。
            _, _, after = member.json("GET", session_url)
            assert after["identity"]["registered"] is True
            assert after["identity"]["name"] == MEMBER_NAME
            assert after["identity"]["is_admin"] is True
            assert after["identity"]["role_label"] == "管理员"

            # 管理员身份立刻换来审阅接口的可用性（§4.2）。
            status, _, _ = member.json("GET", _url(pid, "/review"))
            assert status == 200, "登记并加为管理员之后 GET /review 必须可用（UC-34）"

    def test_an_ipv4_mapped_spelling_still_resolves_after_being_registered(self, pid):
        """登记 `::ffff:10.0.0.2` 也命中 10.0.0.2 —— 落盘前按 `ipaddress` 规范化。

        不规范化就会出现「部署者照一种写法登记了、也加了管理员、身份却不生效」这种静默失败：
        客户端地址走 `auth.normalize_ip`（`::ffff:a.b.c.d` → `a.b.c.d`），而
        `storage.resolve_person_by_ip` 是**逐字**比较。
        """
        mapped = "::ffff:%s" % XFF_MEMBER
        with make_server() as server:
            deployer = as_role(server, "deployer")
            status, _, payload = deployer.json(
                "PUT",
                _url(pid, "/ip-mapping/%s" % _seg(mapped)),
                {"ip": mapped, "name": MEMBER_NAME},
            )
            assert status == 200, payload
            assert payload["item"]["ip"] == XFF_MEMBER, "回显与落盘都得是规范化写法"
            assert [row["ip"] for row in _rows(pid)] == [XFF_MEMBER]
            _, _, session = as_role(server, "member").json(
                "GET", "/api/v1/session?profile=%s" % _seg(pid)
            )
            assert session["identity"]["name"] == MEMBER_NAME
            assert session["identity"]["registered"] is True

    def test_an_uppercase_ipv6_is_stored_in_its_canonical_form(self, pid):
        item = people.put_ip_mapping(pid, "FE80::1", {"ip": "FE80::1", "name": "无线甲"})["item"]
        assert item["ip"] == "fe80::1"
        assert _rows(pid)[0]["ip"] == "fe80::1"

    def test_editing_a_legacy_row_does_not_leave_a_duplicate(self, pid):
        """旧写法 `FE80::1` 那一行被编辑时**就地替换**，不是并排多出一条同一台机器的地址。

        「总数不变」是 §7.9 改 IP 用例的判据，也是这里唯一的防线——只按逐字比较找行会写成
        两行，同一个人在表里出现两次，而 `resolve_person_by_ip` 取先出现的那一条。
        """
        _seed(pid, mapping=[{"ip": "FE80::1", "name": "旧写法", "updated_at": "2026-10-01 08:00:00"}])
        item = people.put_ip_mapping(pid, "FE80::1", {"ip": "FE80::1", "name": "新姓名"})["item"]
        rows = _rows(pid)
        assert len(rows) == 1, "编辑旧写法不能把一条变成两条：%r" % rows
        assert rows[0]["ip"] == "fe80::1" and rows[0]["name"] == "新姓名"
        assert item["name"] == "新姓名"

    def test_delete_matches_whether_the_client_sends_the_stored_or_the_canonical_spelling(self, pid):
        """删除也按规范化匹配：界面回显的是磁盘原写法，而部署者可能自己敲规范形式。"""
        _seed(pid, mapping=[{"ip": "FE80::1", "name": "旧写法", "updated_at": "2026-10-01 08:00:00"}])
        assert people.delete_ip_mapping(pid, "fe80::1") == {"removed": True}
        assert _rows(pid) == []


# ---------------------------------------------------------------------------
# 3. PUT /ip-mapping/{ip}
# ---------------------------------------------------------------------------


class TestPutIpMapping:
    def test_changing_the_ip_removes_the_old_row_and_keeps_the_count(self, seeded):
        """§7.9 / 卡面验收：路径 IP ≠ body IP ⇒ 旧 IP 消失、新 IP 存在、总数不变。"""
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "PUT",
                _url(seeded, "/ip-mapping/%s" % _seg("10.0.0.1")),
                {"ip": "10.0.0.2", "name": "张三"},
            )
        assert status == 200, payload
        rows = _rows(seeded)
        assert [row["ip"] for row in rows] == ["10.0.0.2"], "旧 IP 必须消失、总数不变"
        assert rows[0]["name"] == "张三"
        assert payload == {"item": {"ip": "10.0.0.2", "name": "张三", "updated_at": rows[0]["updated_at"]}}

    def test_same_path_and_body_ip_only_renames_the_row(self, seeded):
        with make_server() as server:
            as_role(server, "deployer").json(
                "PUT",
                _url(seeded, "/ip-mapping/%s" % _seg("10.0.0.1")),
                {"ip": "10.0.0.1", "name": "张三丰"},
            )
        rows = _rows(seeded)
        assert len(rows) == 2
        assert rows[0] == {"ip": "10.0.0.1", "name": "张三丰", "updated_at": rows[0]["updated_at"]}

    def test_a_new_ip_is_appended_and_updated_at_is_a_local_timestamp(self, seeded):
        with make_server() as server:
            _, _, payload = as_role(server, "deployer").json(
                "PUT",
                _url(seeded, "/ip-mapping/%s" % _seg("10.9.9.9")),
                {"ip": "10.9.9.9", "name": "王五"},
            )
        rows = _rows(seeded)
        assert len(rows) == 3
        assert rows[-1]["ip"] == "10.9.9.9" and rows[-1]["name"] == "王五"
        assert payload["item"]["updated_at"] == rows[-1]["updated_at"]
        assert _is_timestamp(payload["item"]["updated_at"]), "§7.1：时间是服务器本地时间字符串"

    def test_body_without_ip_renames_the_path_entry(self, seeded):
        """`{"name": …}` 缺 `ip` ⇒ 取路径那一个（与 mock 同口径：只改姓名不必重复 IP）。"""
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "PUT", _url(seeded, "/ip-mapping/%s" % _seg("10.0.0.2")), {"name": "李四改"}
            )
        assert status == 200
        assert payload["item"]["ip"] == "10.0.0.2"
        assert [row["name"] for row in _rows(seeded)] == ["张三", "李四改"]

    @pytest.mark.parametrize("bad", BAD_IPS)
    def test_invalid_ip_in_the_body_is_400_on_the_ip_field_and_changes_nothing(self, seeded, bad):
        """§7.9 的校验在落任何字节之前：400 + `detail.field="ip"`，文件与计数都不动。"""
        before = _state(seeded)
        before_parts = _parts(seeded)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "PUT",
                _url(seeded, "/ip-mapping/%s" % _seg("10.0.0.1")),
                {"ip": bad, "name": "张三"},
            )
        assert status == 400, "%r 必须 400，实际 %s" % (bad, status)
        assert payload["error"]["code"] == "INVALID_INPUT"
        assert payload["error"]["detail"]["field"] == "ip", "错误要落在 IP 字段上（§7.1）"
        assert _state(seeded) == before, "非法 IP 必须一个字节都不动"
        assert _parts(seeded) == before_parts, "失败写不递增修订号"

    @pytest.mark.parametrize("bad", BAD_PATH_IPS)
    def test_invalid_ip_in_the_path_is_400_too(self, seeded, bad):
        """路径段里的 IP 也要过同一道校验：坏 IP 不该被当成「一个不存在的旧 IP」而静默新增。"""
        before = _state(seeded)
        raw = bad if "%" in bad else _seg(bad)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "PUT", _url(seeded, "/ip-mapping/%s" % raw), {"ip": "10.5.5.5", "name": "张三"}
            )
        assert status == 400, "%r 必须 400，实际 %s：%r" % (bad, status, payload)
        assert payload["error"]["detail"]["field"] == "ip"
        assert _state(seeded) == before

    @pytest.mark.parametrize("bad", BAD_NAMES)
    def test_invalid_name_is_400_on_the_name_field(self, seeded, bad):
        before = _state(seeded)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "PUT",
                _url(seeded, "/ip-mapping/%s" % _seg("10.0.0.1")),
                {"ip": "10.0.0.1", "name": bad},
            )
        assert status == 400, "%r 必须 400，实际 %s" % (bad, status)
        assert payload["error"]["detail"]["field"] == "name"
        assert _state(seeded) == before

    def test_whitespace_is_trimmed_before_storing(self, seeded):
        """§11.2「去空白后」：两端空白不落盘（否则 `is_admin_user` 逐字比较认不出这个人）。"""
        with make_server() as server:
            _, _, payload = as_role(server, "deployer").json(
                "PUT",
                _url(seeded, "/ip-mapping/%s" % _seg("10.0.0.1")),
                {"ip": "10.0.0.1", "name": "  张三  "},
            )
        assert payload["item"]["name"] == "张三"

    def test_non_json_body_is_400(self, seeded):
        before = _state(seeded)
        with make_server() as server:
            status, _, _ = as_role(server, "deployer").request(
                "PUT", _url(seeded, "/ip-mapping/%s" % _seg("10.0.0.1")), body=b"{not json"
            )
        assert status == 400
        assert _state(seeded) == before

    def test_unknown_profile_is_404(self):
        """`{pid}` 接口一律 404，不回落 default（§7.2 / 决策 6）。"""
        with make_server() as server:
            status, _, _ = as_role(server, "deployer").json(
                "GET", "/api/v1/p/%s/ip-mapping" % _seg("没有这个机型")
            )
        assert status == 404

    def test_route_shape_that_is_not_registered_is_404(self, seeded):
        """`POST /ip-mapping` 与 `PUT /admins/{name}` 都不存在（§7.9 只给这几条）。"""
        with make_server() as server:
            caller = as_role(server, "deployer")
            assert caller.json("POST", _url(seeded, "/ip-mapping"), {"ip": "10.0.0.9", "name": "张三"})[0] == 404
            assert caller.json("PUT", _url(seeded, "/admins/%s" % _seg("张三")), {"name": "张三"})[0] == 404


# ---------------------------------------------------------------------------
# 4. DELETE /ip-mapping/{ip}
# ---------------------------------------------------------------------------


class TestDeleteIpMapping:
    def test_removes_the_row_and_reports_it_with_the_backend_precedent_shape(self, seeded):
        """返回 `{removed: bool}`——照 `DELETE /sources/{name}`，不沿用 mock 的 `{deleted: true}`。"""
        before = _parts(seeded)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "DELETE", _url(seeded, "/ip-mapping/%s" % _seg("10.0.0.1"))
            )
        assert status == 200
        assert payload == {"removed": True}
        assert [row["ip"] for row in _rows(seeded)] == ["10.0.0.2"]
        assert _parts(seeded)["settings"] == before["settings"] + 1

    def test_deleting_an_absent_ip_reports_false_and_writes_nothing(self, seeded):
        """「删了 0 条」既不是错误、也不许写盘或递增（全局约束 / 决策 16）。"""
        before = _state(seeded)
        before_parts = _parts(seeded)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "DELETE", _url(seeded, "/ip-mapping/%s" % _seg("10.8.8.8"))
            )
        assert status == 200
        assert payload == {"removed": False}
        assert _state(seeded) == before, "删不到东西时一个字节都不该动"
        assert _parts(seeded) == before_parts

    def test_invalid_ip_is_400_on_the_ip_field(self, seeded):
        before = _state(seeded)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "DELETE", _url(seeded, "/ip-mapping/%s" % _seg("999.1.1.1"))
            )
        assert status == 400
        assert payload["error"]["detail"]["field"] == "ip"
        assert _state(seeded) == before

    def test_deleting_the_last_row_leaves_an_empty_list_not_a_missing_file(self, pid):
        _seed(pid, mapping=[{"ip": "10.0.0.1", "name": "张三", "updated_at": "2026-10-17 09:00:00"}])
        with make_server() as server:
            as_role(server, "deployer").json(
                "DELETE", _url(pid, "/ip-mapping/%s" % _seg("10.0.0.1"))
            )
        assert _rows(pid) == [], "清空表要写回 `[]`，§5.2 的形状不能变成 null 或缺文件"


# ---------------------------------------------------------------------------
# 5. /admins
# ---------------------------------------------------------------------------


class TestAdmins:
    def test_add_returns_201_with_the_new_item(self, seeded):
        before = _parts(seeded)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "POST", _url(seeded, "/admins"), {"name": "  李四  "}
            )
        assert status == 201
        assert payload["item"]["name"] == "李四"
        assert _is_timestamp(payload["item"]["updated_at"])
        assert _rows(seeded, admins=True)[-1]["name"] == "李四"
        assert _parts(seeded)["settings"] == before["settings"] + 1

    def test_duplicate_name_is_409_file_exists_and_changes_nothing(self, seeded):
        """409 的错误码是 `FILE_EXISTS`：§7.1 把 `INVALID_INPUT` 定成 400，mock 原先写错了。"""
        before = _state(seeded)
        before_parts = _parts(seeded)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "POST", _url(seeded, "/admins"), {"name": "张三"}
            )
        assert status == 409
        assert payload["error"]["code"] == "FILE_EXISTS"
        assert payload["error"]["detail"]["field"] == "name"
        assert _state(seeded) == before
        assert _parts(seeded) == before_parts

    def test_removing_the_admin_revokes_review_on_the_very_next_request(self):
        """卡面验收第 5 条：删除管理员后立刻以该人身份调 `GET /review` → 403。"""
        role_pid = seed_roles("撤权机型")
        with make_server() as server:
            admin = as_role(server, "admin")
            deployer = as_role(server, "deployer")
            assert admin.json("GET", _url(role_pid, "/review"))[0] == 200, "先证明他真的是管理员"
            status, _, payload = deployer.json(
                "DELETE", _url(role_pid, "/admins/%s" % _seg(ADMIN_NAME))
            )
            assert status == 200 and payload == {"removed": True}
            status, _, denied = admin.json("GET", _url(role_pid, "/review"))
            assert status == 403, "撤权必须在下一次请求生效（§4.1 实时解析，不许缓存身份）"
            assert denied["error"]["detail"]["action"] == "review"

    def test_removing_the_only_admin_leaves_an_empty_list(self, seeded):
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "DELETE", _url(seeded, "/admins/%s" % _seg("张三"))
            )
        assert status == 200 and payload == {"removed": True}
        assert _rows(seeded, admins=True) == []

    def test_an_admin_name_containing_a_separator_can_be_added_but_not_removed(self, seeded):
        """**已知边界，本用例只钉现状不改行为**（留给控制方裁定，见卡报告）。

        §11.2 给姓名的规则是「去空白后 1–100 字符、无控制字符」，里面**没有**禁止 `/`；
        而 §7.9 把删除设计成 `DELETE /admins/{name}`（名字在路径段里）。两条一撞就出现
        「加得进来、删不出去」：`POST {"name":"王/强"}` 是 201，但 `api.seg()` 必然把它
        编成 `%2F`，于是被 §16.8 第 3 条的编码分隔符守卫（`http.py::_reject_encoded_separators`，
        全站共用、早于路由）拦成 400 —— 而且那只 400 **不带 `detail.field`**，前端只能显示成
        全局错误而不是字段错误。后果是**权限面**的：这个管理员此后无法撤回。

        为什么本卡不动它：要么收紧 §11.2 的姓名字符集（改的是**规范**，且 M3-14 的 `{group}`、
        M3-15 的 `{tool}` 是同一条路同一个形状，该由一次裁定统一三张卡），要么把删除改成
        查询串/请求体（改 §7.9 的接口形状 + mock + 前端三处）。两张卡各自偷偷收紧一半，
        只会让三张设置卡的姓名字符集互不相同。
        """
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _, payload = caller.json("POST", _url(seeded, "/admins"), {"name": "王/强"})
            assert status == 201, "§11.2 没禁止 `/`，所以写入端现在放行"
            assert payload["item"]["name"] == "王/强"
            assert any(row["name"] == "王/强" for row in _rows(seeded, admins=True))

            blocked = caller.json("DELETE", _url(seeded, "/admins/%s" % _seg("王/强")))
            assert blocked[0] == 400, blocked
            assert "detail" in blocked[2]["error"] and not blocked[2]["error"]["detail"].get("field"), (
                "拦它的必须是路径守卫（无字段），不是姓名校验"
            )
            assert any(row["name"] == "王/强" for row in _rows(seeded, admins=True)), (
                "这一条 400 撤不掉人——正是本用例要钉住的已知边界"
            )

    def test_removing_an_absent_admin_reports_false_and_writes_nothing(self, seeded):
        before = _state(seeded)
        before_parts = _parts(seeded)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "DELETE", _url(seeded, "/admins/%s" % _seg("没有这个人"))
            )
        assert status == 200
        assert payload == {"removed": False}
        assert _state(seeded) == before
        assert _parts(seeded) == before_parts

    @pytest.mark.parametrize("bad", BAD_NAMES)
    def test_invalid_admin_name_is_400_on_the_name_field(self, seeded, bad):
        before = _state(seeded)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "POST", _url(seeded, "/admins"), {"name": bad}
            )
        assert status == 400, "%r 必须 400" % bad
        assert payload["error"]["detail"]["field"] == "name"
        assert _state(seeded) == before

    def test_the_two_lists_are_per_profile(self, pid):
        """机型是数据空间（§5.1 / §9.8.10）：在 A 上加的管理员不该让 B 里同名的人也变成管理员。"""
        other = device_models.add_model("另一个机型")
        for target in (pid, other):
            _seed(target, mapping=[{"ip": XFF_MEMBER, "name": MEMBER_NAME, "updated_at": "2026-10-17 09:00:00"}])
        with make_server() as server:
            as_role(server, "deployer").json("POST", _url(pid, "/admins"), {"name": MEMBER_NAME})
            for target, expected in ((pid, True), (other, False)):
                _, _, session = as_role(server, "member").json(
                    "GET", "/api/v1/session?profile=%s" % _seg(target)
                )
                assert session["identity"]["is_admin"] is expected, target
            _, _, admins_elsewhere = as_role(server, "deployer").json("GET", _url(other, "/admins"))
            assert admins_elsewhere == {"items": []}


# ---------------------------------------------------------------------------
# 6. 四角色（§4.2 / §16.3 / UC-44）——安全底线
# ---------------------------------------------------------------------------


class TestRoles:
    @pytest.mark.parametrize("method,rest,body", WRITE_ROUTES)
    @pytest.mark.parametrize("role", ["visitor", "member", "admin"])
    def test_non_deployer_write_is_403_and_changes_not_a_byte(self, seeded, method, rest, body, role):
        """改这张表等于改谁能写：非部署者的写请求必须 403 **且 sha 不变**（本卡的安全底线）。"""
        before = _state(seeded)
        before_parts = _parts(seeded)
        url = _write_url(seeded, rest)
        with make_server() as server:
            status, _, payload = as_role(server, role).json(method, url, body)
        assert status == 403, "%s %s 被 %s 放过了" % (method, rest, role)
        assert payload["error"]["code"] == "FORBIDDEN"
        assert payload["error"]["detail"]["action"] == "settings_write", "拒它的必须是权限矩阵"
        assert _state(seeded) == before, "%s 的 403 写动了人员文件" % role
        assert _parts(seeded) == before_parts, "403 不递增"

    @pytest.mark.parametrize("method,rest,body", WRITE_ROUTES)
    def test_deployer_write_is_allowed(self, seeded, method, rest, body):
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                method, _write_url(seeded, rest), body
            )
        assert status in (200, 201), "%s %s ⇒ %s %r" % (method, rest, status, payload)

    def test_an_admin_without_deployer_rights_cannot_touch_the_tables(self):
        """§4.2：管理员档不等于设置写——`admin` 能审阅，但动不了人员表（这才是 UC-44 的另一半）。

        先证明他**确实**是管理员（`GET /review` 200），否则「写被 403 拒」可能只是因为他
        连管理员都不是，用例就成了恒真。
        """
        role_pid = seed_roles("管理员非部署者")
        with make_server() as server:
            admin = as_role(server, "admin")
            assert admin.json("GET", _url(role_pid, "/review"))[0] == 200, "正向对照：他是管理员"
            ips_before = [row["ip"] for row in _rows(role_pid)]
            admins_before = [row["name"] for row in _rows(role_pid, admins=True)]
            for method, rest, body in WRITE_ROUTES:
                status, _, payload = admin.json(method, _write_url(role_pid, rest), body)
                assert status == 403, (method, rest, status, payload)
            assert [row["ip"] for row in _rows(role_pid)] == ips_before
            assert [row["name"] for row in _rows(role_pid, admins=True)] == admins_before

    def test_deployer_admin_role_writes_and_reads(self):
        """部署者本人登记为管理员时同样有设置写权限（`deployer` 档，与审阅无关）。"""
        role_pid = seed_roles("本机管理员机型", local_admin=True)
        with make_server() as server:
            caller = as_role(server, "deployer_admin")
            assert caller.json("GET", _url(role_pid, "/ip-mapping"))[0] == 200
            status, _, payload = caller.json(
                "PUT",
                _url(role_pid, "/ip-mapping/%s" % _seg("10.4.4.4")),
                {"ip": "10.4.4.4", "name": "赵六"},
            )
            assert status == 200, payload
        assert any(row["ip"] == "10.4.4.4" for row in _rows(role_pid))


# ---------------------------------------------------------------------------
# 7. 修订号账目（全局约束 / 决策 16）
# ---------------------------------------------------------------------------


class TestRevisionAccounting:
    def test_each_successful_write_bumps_settings_and_only_settings(self, seeded):
        with make_server() as server:
            caller = as_role(server, "deployer")
            for method, rest, body in WRITE_ROUTES:
                before = _parts(seeded)
                status, _, payload = caller.json(method, _write_url(seeded, rest), body)
                assert status in (200, 201), (method, rest, status, payload)
                after = _parts(seeded)
                if payload == {"removed": False}:
                    assert after == before, "%s %s 删了 0 条却递增了" % (method, rest)
                    continue
                assert after["settings"] == before["settings"] + 1, (method, rest, before, after)
                assert {k: v for k, v in after.items() if k != "settings"} == {
                    k: v for k, v in before.items() if k != "settings"
                }, "%s %s 只该动 settings" % (method, rest)

    def test_failed_writes_bump_nothing(self, seeded):
        before = _parts(seeded)
        with make_server() as server:
            caller = as_role(server, "deployer")
            bad_ip = _url(seeded, "/ip-mapping/%s" % _seg("10.0.0.1"))
            caller.json("PUT", bad_ip, {"ip": "999.1.1.1", "name": "张三"})
            caller.json("PUT", bad_ip, {"ip": "10.0.0.1", "name": ""})
            caller.json("POST", _url(seeded, "/admins"), {"name": "张三"})
            caller.json("DELETE", _url(seeded, "/admins/%s" % _seg("没有这个人")))
            caller.json("DELETE", _url(seeded, "/ip-mapping/%s" % _seg("10.8.8.8")))
            caller.json("GET", _url(seeded, "/ip-mapping"))
            caller.json("GET", _url(seeded, "/admins"))
            as_role(server, "member").json("POST", _url(seeded, "/admins"), {"name": "别人"})
            as_role(server, "member").json("PUT", bad_ip, {"ip": "10.0.0.1", "name": "别人"})
        assert _parts(seeded) == before, "400/403/409、「删了 0 条」与读都不许递增"


# ---------------------------------------------------------------------------
# 8. 读-改-写整段扣在同一把机型锁里（§5.4；know-how 的「锁外读 + 锁内写」教训）
# ---------------------------------------------------------------------------


class TestNoLostUpdate:
    def test_ip_change_is_atomic_against_a_concurrent_writer(self, seeded, monkeypatch):
        """**竞争方必须是另一个线程**（`docs/know-how.md` 第 4 节那条教训）。

        坏实现（先在锁外读一份、再分两次取锁做「删旧 + 写新」）里，竞争线程几毫秒就写完自己
        那一行，主线程随后拿旧副本整份覆盖 ⇒ 竞争方那一行消失，用例确定性地红。好实现里机型锁
        还扣在主线程手上，那 0.5 秒必然等不到；主线程写完释放，竞争线程才轮到写它自己那一行。
        同线程重入同一把 per-pid `RLock` 抓不到这件事，所以注入一定在另一个线程里写。
        """
        real_load = storage.load_ip_mapping
        racer: List[threading.Thread] = []

        def other() -> None:
            with storage.profile_write(seeded):
                with storage.use_profile(seeded):
                    fresh = real_load()
                    fresh.append(
                        {"ip": "10.6.6.6", "name": "并发写的人", "updated_at": "2026-10-18 11:00:00"}
                    )
                    storage.save_ip_mapping(fresh)

        def load_then_race():
            rows = real_load()
            if not racer:
                thread = threading.Thread(target=other, name="people-racer")
                thread.start()
                racer.append(thread)
                thread.join(0.5)  # 坏实现里这里就返回了；好实现里必然超时
            return rows

        monkeypatch.setattr(storage, "load_ip_mapping", load_then_race)
        try:
            people.put_ip_mapping(seeded, "10.0.0.1", {"ip": "10.0.0.9", "name": "张三"})
        finally:
            for thread in racer:
                thread.join(10)
                assert not thread.is_alive(), "竞争线程还扣在锁上：机型锁没释放"

        ips = [row["ip"] for row in _rows(seeded)]
        assert "10.0.0.1" not in ips, "改 IP 的「删旧」没生效"
        assert "10.0.0.9" in ips, "改 IP 的「写新」没生效"
        assert "10.6.6.6" in ips, "并发写进来的那一行被这次 PUT 用旧副本覆盖掉了——读-改-写没在同一把锁里"


# ---------------------------------------------------------------------------
# 9. 接线与契约
# ---------------------------------------------------------------------------


class TestWiring:
    def test_register_mounts_all_six_routes(self):
        from app.server import api_people
        from app.server.router import Router

        router = Router()
        api_people.register(router)
        cases = [
            ("GET", "/api/v1/p/p1/ip-mapping", api_people.list_ip_mapping, {"pid": "p1"}),
            ("PUT", "/api/v1/p/p1/ip-mapping/10.0.0.1", api_people.put_ip_mapping, {"pid": "p1", "ip": "10.0.0.1"}),
            ("DELETE", "/api/v1/p/p1/ip-mapping/::1", api_people.delete_ip_mapping, {"pid": "p1", "ip": "::1"}),
            ("GET", "/api/v1/p/p1/admins", api_people.list_admins, {"pid": "p1"}),
            ("POST", "/api/v1/p/p1/admins", api_people.add_admin, {"pid": "p1"}),
            (
                "DELETE",
                "/api/v1/p/p1/admins/%E5%BC%A0%E4%B8%89",
                api_people.remove_admin,
                {"pid": "p1", "name": "张三"},
            ),
        ]
        for method, path, handler, expected in cases:
            found, params = router.match(method, path)
            assert found is handler, "%s %s 没注册到 %s" % (method, path, handler.__name__)
            assert params == expected, (method, path, params)

    def test_build_router_mounts_the_people_endpoints(self, tmp_path):
        from app.config import Settings
        from app.server.router import build_router

        router = build_router(Settings(host="127.0.0.1", port=0, data_dir=str(tmp_path / "data")))
        for method, path in (
            ("GET", "/api/v1/p/default/ip-mapping"),
            ("PUT", "/api/v1/p/default/ip-mapping/10.0.0.1"),
            ("DELETE", "/api/v1/p/default/ip-mapping/10.0.0.1"),
            ("GET", "/api/v1/p/default/admins"),
            ("POST", "/api/v1/p/default/admins"),
            ("DELETE", "/api/v1/p/default/admins/x"),
        ):
            handler, _ = router.match(method, path)
            assert handler is not None, "%s %s 没挂进 build_router" % (method, path)

    def test_service_layer_needs_no_http_context(self, pid):
        """分层（§3.3）：service 只吃 pid，不经 HTTP 也能直接调用（handler 只做解析与授权）。"""
        people.put_ip_mapping(pid, "10.0.0.1", {"ip": "10.0.0.1", "name": "张三"})
        assert people.list_ip_mapping(pid) == {
            "items": [{"ip": "10.0.0.1", "name": "张三", "updated_at": _rows(pid)[0]["updated_at"]}]
        }
        assert people.add_admin(pid, "张三")["item"]["name"] == "张三"
        assert people.remove_admin(pid, "张三") == {"removed": True}
        assert people.delete_ip_mapping(pid, "10.0.0.1") == {"removed": True}
