"""IP 对应表与管理员名单（卡 13 · M3-13 / INH-1898；规范 §7.9、§4.2、§11.1、§16.2 UC-34、UC-44）。

本卡改的是**谁能写**，所以判据一律落在字节与真实身份上，不落在状态码上：

1. **权限**（§4.2 / §16.3 / UC-44）：读=全部角色；四个写接口=部署者。非部署者打进来 403，
   并且 `ip_mapping.json` / `admin_users.json` 的 **sha256 与 mtime 都不许动**、修订号不涨
   ——这一条是本卡的安全底线，所以每个角色 × 每个写接口各打一遍真 HTTP 请求。
2. **非法输入在碰磁盘之前**（§7.9 / §11.2）：`999.1.1.1`、`abc`、空串、`10.0.0.1/24` →
   400 `INVALID_INPUT` + `detail.field="ip"`；姓名空/超长/含控制字符 → 400 `field="name"`；
   管理员姓名含 `/`、`\\`，或**字面** `%2f` / `%5c` / `%00`（任意大小写）→ 同样 400
   `field="name"`；要落盘的 IP 键含同一族编码分隔符（IPv6 scope 写法进得来）→ 400
   `field="ip"`（M3-13 两轮裁定：这些值进得了 `POST`/`PUT` 就永远进不了
   `DELETE /admins/{name}`、`DELETE /ip-mapping/{ip}` 的路径段，两个入口必须达成一致，
   而**删除端保持宽松**好让 v1 存量行仍有出路）；重名管理员 → 409 `FILE_EXISTS`
   （**不是** 400，§7.1 的状态码表）。三类的字节与计数都不变。
   mock 一侧的同一判据靠**执行** `mock-api.js` 核对，见本文件末尾的 `TestMockPeopleContract`。
3. **改 IP = 同一次人员锁里先删后写**（§7.9）：`PUT /ip-mapping/10.0.0.1` + body `10.0.0.2`
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
import shutil
import subprocess
import threading
import urllib.parse
from datetime import datetime
from pathlib import Path
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
from app.services import auth, people, revision


@pytest.fixture(autouse=True)
def loopback_writer_is_admin(monkeypatch):
    """本文件用 127.0.0.1 当写请求的调用方。本机不再自动有权限，测试里单独放行这个地址，不写进名单。"""
    real = storage.is_admin_ip

    def wrapped(ip: str) -> bool:
        if str(ip or "").strip() == "127.0.0.1":
            return True
        return real(ip)

    monkeypatch.setattr(storage, "is_admin_ip", wrapped)

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
#: M3-13 裁定（INH-1898）：管理员姓名进的是 `DELETE /admins/{name}` 那个**路径段**，
#: `api.seg()` 必然把 `/`、`\` 编成 `%2F` / `%5C`，撞上 §16.8 第 3 条的全站守卫，于是
#: 「加得进来、删不出去」。两个入口必须达成一致 ⇒ 这一族在**创建端**就 400。
SEPARATOR_ADMIN_NAMES = ["王/强", "王\\强", "/打头", "结尾/", "a/b\\c"]
#: 必须**放行**的合法姓名，用来钉「别把限制收紧过头」：中文、含空格、含标点/`%`/`#`/`+`
#: 都不在守卫那三只 token（`%2f` / `%5c` / `%00`）里，所以加得进来就一定删得出去。
#: 最后两条是本轮加的**过宽哨兵**：`100%达标` 的 `%` 后面不是十六进制、`值班 50%` 干脆以
#: `%` 结尾，两份解码都干净 ⇒ 必须照旧放行并且真的删得掉（删除走真 HTTP，不是断言）。
#: **不要**往这一族里加「含 `%XX`（X 为十六进制）」的名字——那种名字在创建端已经
#: 400（`PERCENT_ESCAPE_ADMIN_NAMES`）；这里只放 `%` 后不是两位十六进制的合法写法。
ROUNDTRIP_ADMIN_NAMES = [
    "李四",
    "王 五",
    "  赵六  ",
    "陈·七",
    "O'Brien",
    "Smith, John",
    "部署者 #7",
    "100%达标",
    "值班+备份",
    "值班 50%",
    "完成度%zz",
    "50%",
]
#: 第二轮裁定（同一条死路的第二层）：姓名里**字面**写着守卫那三只 token（任意大小写）。
#: `api.seg()` 会把 `%` 自己编成 `%25`，路径段于是长成 `%252f` —— 躲过守卫的 raw 那一份，
#: 却在**解码后**那一份里重新长出 `%2f`，而 `_reject_encoded_separators` 把两份一起看。
#: 结果和上面那条一模一样：加得进来、删不出去 ⇒ 创建端 400 `detail.field="name"`。
ENCODED_SEPARATOR_ADMIN_NAMES = [
    "王%2f强",
    "王%2F强",
    "备份%5c甲",
    "备份%5C甲",
    "王%00强",
    "%2f打头",
    "结尾%5c",
]
#: 第三族（评审 F1）：姓名里任何字面「% + 两位十六进制」都拒。路径被解码两次
#: （`http.py` 一次、`router.py` 又一次），存进去的名字在删除端会变成另一个名字；
#: `测试%20甲` 甚至会把名单里的 `测试 甲` 撤掉。`%` 后面不是两位十六进制的写法
#: （`100%达标`、`完成度%zz`、裸 `%`）不在这一族，必须继续放行。
PERCENT_ESCAPE_ADMIN_NAMES = [
    "测试%20甲",
    "测试%2e甲",
    "测试%2f甲",
    "测试%252f甲",
    "测试%2F甲",
    "备份#%5d",
]
#: 含 `/` 的 IP：`ipaddress` 一律解析失败，所以这张表**到不了**「加得进来删不出去」的状态
#: （见 `test_a_cidr_style_ip_with_a_separator_never_reaches_the_table`）。
SEPARATOR_IP_KEYS = ["10.0.0.1/24", "fd00::/8", "192.168.1.1/"]
#: 含**字面**编码写法的 IP 键：`%2f`/`%5c`/`%00` 以及任意 `%XX`（`fe80::1%20` 是 UI
#: 编辑流程可达的 scope id；`ipaddress` 会逐字回显）。全部在创建端 400 `field="ip"`。
ENCODED_SEPARATOR_IP_KEYS = ["fe80::1%2feth0", "fe80::1%2F", "fe80::1%5c", "fe80::1%00", "fe80::1%20"]
#: 上面这一族里**真正落盘**的一个样本，用来摆 v1 式存量行（删除侧必须仍然宽松）。
LEGACY_ENCODED_IP_KEY = "fe80::1%2feth0"


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

    @pytest.mark.parametrize("bad", SEPARATOR_IP_KEYS)
    def test_a_cidr_style_ip_with_a_separator_never_reaches_the_table(self, seeded, bad):
        """IP 那一侧**到不了**「加得进来、删不出去」，所以 M3-13 的分隔符限制不许套到它头上。

        裁定要求「只在同一个不可管理状态真的可达时才收紧」。这里不可达：键是 IP，
        `auth.normalize_ip` 用的是 `ipaddress.ip_address`，`/` 不是地址字符 ⇒ 含 `/` 的键在
        `_validated_ip` 就变成 400 `detail.field="ip"`，一个字节都不写，表里永远不会有这样一行。
        路径一律用干净的 IP，好让这一只 400 明确来自 **IP 校验**（带字段）而不是 §16.8 的守卫。
        """
        before = _state(seeded)
        before_parts = _parts(seeded)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "PUT",
                _url(seeded, "/ip-mapping/%s" % _seg("10.0.0.1")),
                {"ip": bad, "name": "网段不是地址"},
            )
        assert status == 400, "%r 必须 400，实际 %s：%r" % (bad, status, payload)
        assert payload["error"]["detail"]["field"] == "ip", (
            "拦它的必须是 IP 校验（带字段），不是路径守卫那只无字段的 400"
        )
        assert _state(seeded) == before
        assert _parts(seeded) == before_parts
        assert all(bad not in row["ip"] for row in _rows(seeded)), "含分隔符的键根本进不了表"

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

    def test_an_ip_mapping_name_may_contain_a_separator_because_the_key_is_the_ip(self, seeded):
        """`ip_mapping` 的 `name` **不进路径段**，所以 M3-13 的分隔符限制不碰它——而且要能管得动。

        这条是「别凭空收紧」的判据：同一串 `王/强` 当管理员姓名要 400（删除按名字寻址），
        当 IP 行的姓名则是 200，并且那一行照常按 IP 删得掉。两个入口各自的字符集只受
        **自己那条删除路径**约束，不是抄来的统一禁令。
        """
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _, added = caller.json(
                "PUT",
                _url(seeded, "/ip-mapping/%s" % _seg("10.0.0.9")),
                {"ip": "10.0.0.9", "name": "王/强"},
            )
            assert status == 200, added
            assert added["item"]["name"] == "王/强"
            assert any(row["name"] == "王/强" for row in _rows(seeded))

            status, _, removed = caller.json(
                "DELETE", _url(seeded, "/ip-mapping/%s" % _seg("10.0.0.9"))
            )
            assert status == 200 and removed["removed"] is True, (
                "删除按 IP 寻址，姓名里的 `/` 一点也挡不住它"
            )
        assert all(row["name"] != "王/强" for row in _rows(seeded))

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

    @pytest.mark.parametrize("name", SEPARATOR_ADMIN_NAMES)
    def test_admin_name_containing_a_separator_is_rejected_at_creation(self, seeded, name):
        """含 `/` 或反斜杠的管理员姓名在**创建端**就 400，带 `detail.field="name"`，不写一个字节。

        前身用例钉的是「加得进来、删不出去」这个现状；控制方裁定让两个入口达成一致（对照
        INH-1912 的「可创建但不可管理」同一修法），所以**期望随之改变**，不是放宽断言。

        拦它的必须是姓名校验而不是路径守卫：守卫那只 400（`http.py::_reject_encoded_separators`，
        全站共用、早于路由）**不带** `detail.field`，前端只能显示成全局错误。同一只 URL 的
        `DELETE` 仍然撞守卫——这条留在判据里，因为它就是「必须挡在创建端」的理由本身。
        """
        before = _state(seeded)
        before_parts = _parts(seeded)
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _, payload = caller.json("POST", _url(seeded, "/admins"), {"name": name})
            assert status == 400, payload
            assert payload["error"]["code"] == "INVALID_INPUT"
            assert payload["error"]["detail"]["field"] == "name", (
                "必须是姓名校验的 400（带字段，界面才指得到输入框）"
            )
            blocked = caller.json("DELETE", _url(seeded, "/admins/%s" % _seg(name)))
            assert blocked[0] == 400 and not blocked[2]["error"]["detail"].get("field"), (
                "删除这一侧仍然只能撞到路径守卫（无字段）——正是创建端必须收紧的原因"
            )
        assert _state(seeded) == before, "拒绝必须发生在碰磁盘之前"
        assert _parts(seeded) == before_parts, "失败的写不许递增修订号"
        assert all(row["name"] != name for row in _rows(seeded, admins=True)), (
            "名单里不该出现一个撤不掉的条目"
        )

    @pytest.mark.parametrize("name", ENCODED_SEPARATOR_ADMIN_NAMES)
    def test_admin_name_with_a_literal_encoded_separator_is_rejected_at_creation(self, seeded, name):
        """同一条死路的**第二层**（第二轮裁定）：姓名里字面写着 `%2f` / `%5c` / `%00` 也拒。

        上一轮只挡了裸 `/` 与 `\\`，漏了这一族：`api.seg()`（= `encodeURIComponent`）先把名字里
        的 `%` 编成 `%25`，于是 `王%2f强` 的路径段是 `王%252f强`——守卫的 raw 那一份里查不到
        token，但 `path = unquote(split.path)` 之后**解码后那一份**又变回 `王%2f强`，
        `_reject_encoded_separators` 看的是这两份，于是删除照样 400（下面那半条断言就是它）。
        可达性一样、后果一样 ⇒ 判据一样：400 + `detail.field="name"`、字节不动、修订号不动。

        这一族**不是**「含 `%` 就拒」：`100%达标`、`完成度%zz` 这些 `%` 后不是两位
        十六进制的写法仍在 `ROUNDTRIP_ADMIN_NAMES` 里走完 POST→GET→DELETE，是这条
        规则的过宽哨兵；`%XX` 那一族由 `PERCENT_ESCAPE_ADMIN_NAMES` 另行钉住。
        """
        before = _state(seeded)
        before_parts = _parts(seeded)
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _, payload = caller.json("POST", _url(seeded, "/admins"), {"name": name})
            assert status == 400, payload
            assert payload["error"]["code"] == "INVALID_INPUT"
            assert payload["error"]["detail"]["field"] == "name", (
                "必须是姓名校验的 400（带字段）；守卫那只不带字段，前端指不到输入框"
            )
            assert payload["error"]["message"] == people._BAD_ADMIN_NAME_MESSAGE, (
                "报的是「不可管理」这一档，不是 §11.2 的通用姓名档"
            )
            blocked = caller.json("DELETE", _url(seeded, "/admins/%s" % _seg(name)))
            assert blocked[0] == 400 and not blocked[2]["error"]["detail"].get("field"), (
                "删除这一侧仍然只撞到路径守卫（无字段）——这正是必须挡在创建端的理由"
            )
        assert _state(seeded) == before, "拒绝必须发生在碰磁盘之前"
        assert _parts(seeded) == before_parts, "失败的写不许递增修订号"
        assert all(row["name"] != name for row in _rows(seeded, admins=True))

    @pytest.mark.parametrize("name", PERCENT_ESCAPE_ADMIN_NAMES)
    def test_admin_name_with_any_literal_percent_escape_is_rejected_at_creation(self, seeded, name):
        """第三族（评审 F1）：任何字面 `%XX` 都在创建端 400，字节与修订号不动。

        机制是路径被解码两次；只挡 `%2f/%5c/%00` 不够，`%20` 这种会把删除端解成另一个
        名字。`%` 后不是两位十六进制的写法仍走 `ROUNDTRIP_ADMIN_NAMES`。
        """
        before = _state(seeded)
        before_parts = _parts(seeded)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "POST", _url(seeded, "/admins"), {"name": name}
            )
        assert status == 400, payload
        assert payload["error"]["code"] == "INVALID_INPUT"
        assert payload["error"]["detail"]["field"] == "name"
        assert payload["error"]["message"] == people._BAD_ADMIN_NAME_MESSAGE, (
            "报的是「不可管理」这一档，不是 §11.2 的通用姓名档"
        )
        assert _state(seeded) == before, "拒绝必须发生在碰磁盘之前"
        assert _parts(seeded) == before_parts, "失败的写不许递增修订号"
        assert all(row["name"] != name for row in _rows(seeded, admins=True))

    def test_rejecting_a_percent_escape_does_not_touch_its_unescaped_lookalike(self, seeded):
        """正反用例：先有 `测试 甲`，再建 `测试%20甲` 必须 400；删 `测试 甲` 只删它自己。

        这条钉住「创建端拒绝不会误伤同名的合法管理员」；真正会撤错人的存量行场景在
        下一条用例。
        """
        legitimate = "测试 甲"
        colliding = "测试%20甲"
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _, added = caller.json("POST", _url(seeded, "/admins"), {"name": legitimate})
            assert status == 201, added
            status, _, payload = caller.json("POST", _url(seeded, "/admins"), {"name": colliding})
            assert status == 400, payload
            status, _, removed = caller.json(
                "DELETE", _url(seeded, "/admins/%s" % _seg(legitimate))
            )
            assert status == 200 and removed == {"removed": True}
        names = [row["name"] for row in _rows(seeded, admins=True)]
        assert legitimate not in names and colliding not in names

    def test_http_delete_of_a_legacy_percent_escape_row_cannot_revoke_another_admin(self, seeded):
        """存量行场景：名单里同时有 `测试 甲` 与 `测试%20甲`，HTTP 删后者不许动前者。

        新数据在创建端已经被挡住；v1 直接写文件留下的存量行仍可能在表里。HTTP 边界
        必须把这种二次编码路径整段拒掉（400，不带字段），只留服务层/运维这条出路。
        """
        legitimate = "测试 甲"
        colliding = "测试%20甲"
        _seed(
            seeded,
            admins=[
                {"name": legitimate, "updated_at": "2026-10-17 09:00:00"},
                {"name": colliding, "updated_at": "2026-10-17 09:00:00"},
            ],
        )
        before = _state(seeded)
        before_parts = _parts(seeded)
        encoded_forms = [
            _seg(colliding),
            _seg(colliding).replace("%2520", "%25%32%30"),
            _seg(colliding).replace("%2520", "%25%32%35%32%30"),
        ]
        with make_server() as server:
            caller = as_role(server, "deployer")
            for encoded in encoded_forms:
                status, _, payload = caller.json(
                    "DELETE", _url(seeded, "/admins/%s" % encoded)
                )
                assert status == 400, (encoded, payload)
                assert not payload["error"]["detail"].get("field"), (
                    encoded,
                    "守卫那只 400 不带字段",
                )
                assert _state(seeded) == before and _parts(seeded) == before_parts, encoded
                assert [row["name"] for row in _rows(seeded, admins=True)] == [
                    legitimate,
                    colliding,
                ], encoded
        assert people.remove_admin(seeded, colliding) == {"removed": True}
        assert [row["name"] for row in _rows(seeded, admins=True)] == [legitimate]

    def test_the_removal_side_stays_permissive_for_a_legacy_row(self, seeded):
        """限制**只加在创建端**：v1 是直接写文件的，存量名单里可能有含 `/` 的行。

        把同一档校验也压到 `remove_admin` 上，只会让那些行在本层多一道拒绝（HTTP 层本来就到
        不了它们），等于缩小出路而不是打开出路。这一条同时钉住「被拒的是不可管理的**入口组合**，
        不是 `/` 这个字符本身」。
        """
        legacy = "王/强"
        _seed(
            seeded,
            admins=[
                {"name": "张三", "updated_at": "2026-10-17 09:00:00"},
                {"name": legacy, "updated_at": "2026-10-17 09:00:00"},
            ],
        )
        assert any(row["name"] == legacy for row in _rows(seeded, admins=True))
        assert people.remove_admin(seeded, legacy) == {"removed": True}
        assert all(row["name"] != legacy for row in _rows(seeded, admins=True))

    def test_the_removal_side_stays_permissive_for_a_legacy_encoded_separator_row(self, seeded):
        """第二层也一样：**限制只压创建端**。存量行里那种字面 `%2f` 的姓名照样撤得掉。

        这条就是「别把不对称清理掉」的执法者——把 `_validated_admin_name` 塞进 `remove_admin`
        的当下它就会红。HTTP 那一侧到不了这些行（守卫拦着，见上面两条创建用例），所以服务层
        是唯一还留着出路的入口，不许再收紧。
        """
        legacy = "备份%2f甲"
        _seed(
            seeded,
            admins=[
                {"name": "张三", "updated_at": "2026-10-17 09:00:00"},
                {"name": legacy, "updated_at": "2026-10-17 09:00:00"},
            ],
        )
        assert people.remove_admin(seeded, legacy) == {"removed": True}
        assert [row["name"] for row in _rows(seeded, admins=True)] == ["张三"]

    @pytest.mark.parametrize("name", ROUNDTRIP_ADMIN_NAMES)
    def test_a_legitimate_name_adds_and_still_deletes_through_the_same_pair(self, seeded, name):
        """两个入口一致 = 也**不许多拦**：中文、带空格、带标点的姓名都要走完 POST→GET→DELETE。

        这个项目已经被「两个入口对字符集各执一词」咬过一回（`docs/know-how.md` 第 2 节
        文件名字符集那条，N1 / INH-1912：新建允许中文、改名永远 400），所以这一组既是放行
        判据，也是新校验的**过宽**哨兵。删除用的是 `POST` 回显的名字（已去空白），把
        「写入端存的写法」和「删除端寻址的写法」也钉成同一个。
        """
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _, added = caller.json("POST", _url(seeded, "/admins"), {"name": name})
            assert status == 201, added
            stored = added["item"]["name"]
            assert any(row["name"] == stored for row in _rows(seeded, admins=True)), stored

            status, _, listed = caller.json("GET", _url(seeded, "/admins"))
            assert status == 200 and stored in [item["name"] for item in listed["items"]]

            status, _, removed = caller.json(
                "DELETE", _url(seeded, "/admins/%s" % _seg(stored))
            )
            assert status == 200 and removed["removed"] is True, (
                "加得进来就必须删得出去——这条 200 就是本次裁定的全部内容"
            )
        assert all(row["name"] != stored for row in _rows(seeded, admins=True))

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

    def test_the_two_lists_are_shared_by_every_profile(self, pid):
        """人员表不是机型数据：在 A 上加的管理员，换到 B 仍是同一个管理员。"""
        other = device_models.add_model("另一个机型")
        _seed(pid, mapping=[{"ip": XFF_MEMBER, "name": MEMBER_NAME, "updated_at": "2026-10-17 09:00:00"}])
        with make_server() as server:
            status, _, added = as_role(server, "deployer").json(
                "POST", _url(pid, "/admins"), {"name": MEMBER_NAME}
            )
            assert status == 201, added
            for target in (pid, other):
                _, _, session = as_role(server, "member").json(
                    "GET", "/api/v1/session?profile=%s" % _seg(target)
                )
                assert session["identity"]["is_admin"] is True, target
                assert session["identity"]["name"] == MEMBER_NAME, target
            _, _, admins_elsewhere = as_role(server, "deployer").json("GET", _url(other, "/admins"))
            assert [row["name"] for row in admins_elsewhere["items"]] == [MEMBER_NAME]


# ---------------------------------------------------------------------------
# 6. 四角色（§4.2 / §16.3 / UC-44）——安全底线
# ---------------------------------------------------------------------------


class TestRoles:
    @pytest.mark.parametrize("method,rest,body", WRITE_ROUTES)
    @pytest.mark.parametrize("role", ["member"])
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

    def test_admin_can_write_the_tables(self):
        """管理员是最高权限，人员表的写接口不再另设部署者档。"""
        role_pid = seed_roles("管理员写人员表")
        with make_server() as server:
            admin = as_role(server, "admin")
            assert admin.json("GET", _url(role_pid, "/review"))[0] == 200
            for method, rest, body in WRITE_ROUTES:
                status, _, payload = admin.json(method, _write_url(role_pid, rest), body)
                assert status in (200, 201), (method, rest, status, payload)

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
            with storage.people_write():
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
                assert not thread.is_alive(), "竞争线程还扣在锁上：人员锁没释放"

        ips = [row["ip"] for row in _rows(seeded)]
        assert "10.0.0.1" not in ips, "改 IP 的「删旧」没生效"
        assert "10.0.0.9" in ips, "改 IP 的「写新」没生效"
        assert "10.6.6.6" in ips, "并发写进来的那一行被这次 PUT 用旧副本覆盖掉了——读-改-写没在同一把锁里"


# ---------------------------------------------------------------------------
# 9. 接线与契约
# ---------------------------------------------------------------------------


class TestSharedAcrossProfiles:
    def test_a_write_on_one_profile_is_the_same_table_everywhere(self, pid):
        other = device_models.add_model("另一机型")
        before = revision.snapshot(other)["parts"]["settings"]
        people.put_ip_mapping(pid, "10.4.4.4", {"ip": "10.4.4.4", "name": "共用"})
        people.add_admin(pid, "共用")
        assert [row["name"] for row in people.list_ip_mapping(other)["items"]] == ["共用"]
        assert [row["name"] for row in people.list_admins(other)["items"]] == ["共用"]
        assert revision.snapshot(other)["parts"]["settings"] == before + 2
        assert os.path.isfile(os.path.join(storage.DATA_DIR, "ip_mapping.json"))
        assert os.path.isfile(os.path.join(storage.DATA_DIR, "admin_users.json"))
        assert not os.path.exists(os.path.join(storage.get_profile_data_dir(pid), "ip_mapping.json"))
        assert not os.path.exists(os.path.join(storage.get_profile_data_dir(other), "admin_users.json"))


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


# ---------------------------------------------------------------------------
# 10. 「值要当 URL 路径段用」这条不变量（M3-13 / INH-1898 两轮裁定）
# ---------------------------------------------------------------------------


class TestUnmanageableValue:
    """创建端那一档与 §16.8 守卫、以及 IP 键那一族的**可达性**判据。

    可复用的规则不是「姓名字符集」，而是：凡是「写进来的值日后要当作 URL 路径段去寻址」的
    接口，创建端必须一次满足删除端那条路径的全部约束；而**只在同一个不可管理状态真的可达时
    才收紧**（这是裁定原文）。下面几条把「可达」与「不可达」各钉一半，另一头的过宽哨兵是
    `ROUNDTRIP_ADMIN_NAMES`（`%` 后不是两位十六进制的姓名照样走完 POST→GET→DELETE；
    `%XX` 那一族已在创建端拒绝，见 `PERCENT_ESCAPE_ADMIN_NAMES`）。
    """

    def test_the_creation_rule_tracks_the_guard_token_list(self):
        """创建端手抄的 token 清单必须与全站守卫的 `_ENCODED_UNSAFE` 同一份。

        `services` 不许反向 import `server`（§3.3），所以 people.py 里是手抄的；手抄就会漂移，
        而漂移的两个方向都咬人：守卫加了新 token 而创建端没跟上 ⇒ 又长出撤不掉的行；
        创建端自己多拦一棵 ⇒ 平白拒掉删得出去的名字。只有测试两边都读才看得见这件事，
        断的是**常量值**而不是源码文本（R45）。
        """
        from app.server import http

        assert set(people._UNMANAGEABLE_PATH_TOKENS) == set(http._ENCODED_UNSAFE), (
            "创建端拒的编码 token 必须与守卫拒的完全一致：%r vs %r"
            % (people._UNMANAGEABLE_PATH_TOKENS, http._ENCODED_UNSAFE)
        )
        # 裸分隔符那一层来自 `validate_filename` 的同一口径（`/`、`\\` 本就是路径分隔符）。
        assert set(people._UNMANAGEABLE_PATH_CHARS) == {"/", "\\"}

    @pytest.mark.parametrize("bad", ENCODED_SEPARATOR_IP_KEYS)
    def test_ip_key_with_a_literal_encoded_separator_is_rejected_at_creation(self, seeded, bad):
        """IP 键也要挡（含 UI 编辑流程：路径是旧 IP、body 是新 IP）。

        路径给干净 IP、候选值放 body，好让这只 400 明确来自校验而不是守卫。
        `detail.field="ip"` 而且报的是**专门那一档**（不是 `ipaddress` 的「请输入合法的
        IPv4/IPv6 地址」）——这条 message 断言就是「新规则真的在执法、不是被旧校验顺手挡了」。
        """
        before = _state(seeded)
        before_parts = _parts(seeded)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "PUT",
                _url(seeded, "/ip-mapping/%s" % _seg("10.0.0.1")),
                {"ip": bad, "name": "带编码分隔符的键"},
            )
        assert status == 400, "%r 必须 400，实际 %s：%r" % (bad, status, payload)
        assert payload["error"]["code"] == "INVALID_INPUT"
        assert payload["error"]["detail"]["field"] == "ip", "错误要落在 IP 字段上（§7.1）"
        assert payload["error"]["message"] == people._UNMANAGEABLE_IP_MESSAGE, (
            "必须是「不可管理」那一档拒绝，说明 ipaddress 放过它、这一档才拦得住"
        )
        assert _state(seeded) == before, "非法键必须一个字节都不动"
        assert _parts(seeded) == before_parts, "失败写不递增修订号"
        assert all(bad.lower() not in str(row["ip"]).lower() for row in _rows(seeded))

    def test_editing_an_existing_ip_to_a_scope_id_with_percent_escape_is_rejected(self, seeded):
        """UI 编辑已有行：路径=旧 IP、body=新 IP；`fe80::1%20` 可达，必须 400 且不搬走旧行。"""
        before = _state(seeded)
        before_parts = _parts(seeded)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "PUT",
                _url(seeded, "/ip-mapping/%s" % _seg("10.0.0.1")),
                {"ip": "fe80::1%20", "name": "坏键"},
            )
        assert status == 400, payload
        assert payload["error"]["code"] == "INVALID_INPUT"
        assert payload["error"]["detail"]["field"] == "ip"
        assert payload["error"]["message"] == people._UNMANAGEABLE_IP_MESSAGE
        assert _state(seeded) == before and _parts(seeded) == before_parts
        assert all(row["ip"] != "fe80::1%20" for row in _rows(seeded))

    def test_an_encoded_separator_inside_an_ipv6_scope_id_reaches_the_unmanageable_state(
        self, seeded
    ):
        """**可达性实测**（IP 键这一族的裁定依据；答案是「到得了」，所以创建端必须挡）。

        裸 `/` 到不了：`ipaddress` 直接判非法（`SEPARATOR_IP_KEYS` 那条用例钉着）。但 3.9 的
        `ipaddress` 认 IPv6 scope id，scope 段只限字符集、不限含义，于是 `fe80::1%2feth0`
        被 `normalize_ip` **逐字回显**并原样落盘。这一行进了表之后：
        * 按 HTTP 删不出去——`api.seg()` 把 `%` 编成 `%25`，解码后又长出 `%2f`，撞守卫，
          而且那只 400 **不带** `detail.field`（下面实测的就是这两件事）；
        * 按服务层仍然删得掉——`delete_ip_mapping` 保持宽松，这是存量行的唯一出路。

        两半都要钉：前一半证明「创建端不挡就会长出撤不掉的行」，后一半证明「限制不许被
        顺手压到删除端」。
        """
        key = LEGACY_ENCODED_IP_KEY
        assert auth.normalize_ip(key) == key, (
            "ipaddress 认这个 scope 写法（含字面 %2f）⇒ 不可管理状态确实可达"
        )
        assert auth.normalize_ip("fe80::1/24") == "", "裸斜杠仍然到不了表，这一半不许跟着变松"

        _seed(seeded, mapping=[{"ip": key, "name": "存量行", "updated_at": "2026-10-17 09:00:00"}])
        assert [row["ip"] for row in _rows(seeded)] == [key], "存量行是按原写法躺在磁盘上的"
        before = _state(seeded)
        before_parts = _parts(seeded)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "DELETE", _url(seeded, "/ip-mapping/%s" % _seg(key))
            )
        assert status == 400, "删除路径确实被守卫拦死（这就是「加得进来、删不出去」）"
        assert not payload["error"]["detail"].get("field"), "守卫那只 400 不带字段，前端报不到 IP 框"
        assert _state(seeded) == before and _parts(seeded) == before_parts, "守卫早于路由，字节不动"

        assert people.delete_ip_mapping(seeded, key) == {"removed": True}, (
            "服务层这一侧仍然宽松——存量行只剩这条出路，别把它也收紧掉"
        )
        assert _rows(seeded) == []

    def test_changing_a_legacy_encoded_ip_key_to_a_clean_one_is_still_allowed(self, seeded):
        """`put_ip_mapping` 的**路径那一半**（被删掉的旧键）也不过这一档。

        只约束要落盘的那个键：否则「把存量行改成干净 IP」这条出路会被自己堵死
        （HTTP 那一侧到不了这个形状，所以它由服务层直接执法）。
        """
        _seed(
            seeded,
            mapping=[
                {"ip": LEGACY_ENCODED_IP_KEY, "name": "存量行", "updated_at": "2026-10-17 09:00:00"},
                {"ip": "10.0.0.1", "name": "张三", "updated_at": "2026-10-17 09:30:00"},
            ],
        )
        moved = people.put_ip_mapping(seeded, LEGACY_ENCODED_IP_KEY, {"ip": "10.8.8.8", "name": "搬走"})
        assert moved["item"]["ip"] == "10.8.8.8"
        assert [row["ip"] for row in _rows(seeded)] == ["10.0.0.1", "10.8.8.8"], (
            "旧键被搬走、没留残行，其余行不受影响"
        )

    def test_a_literal_percent_escape_is_rejected_at_creation_and_a_legacy_row_is_service_only(
        self, seeded
    ):
        """第三族的收口：新数据创建端 400；存量行 HTTP 删不动，但服务层仍有出路。

        旧版本这条用例钉的是「200 + removed:false，甚至撤错人」的现状；评审 F1
        把它升级为阻断项后，期望值随裁定改变：`备份#%5d` 不能再创建；若它已经在
        v1 的存量名单里，HTTP 的二次编码路径必须被整段 400 拒掉（不带字段），
        只有服务层/运维能把它撤掉，而且只撤它自己。
        """
        unaddressable = "备份#%5d"
        collision = "备份#]"
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _, payload = caller.json(
                "POST", _url(seeded, "/admins"), {"name": unaddressable}
            )
            assert status == 400, payload
            assert payload["error"]["detail"]["field"] == "name"

        _seed(
            seeded,
            admins=[
                {"name": collision, "updated_at": "2026-10-17 09:00:00"},
                {"name": unaddressable, "updated_at": "2026-10-17 09:00:00"},
            ],
        )
        before = _state(seeded)
        before_parts = _parts(seeded)
        with make_server() as server:
            status, _, payload = as_role(server, "deployer").json(
                "DELETE", _url(seeded, "/admins/%s" % _seg(unaddressable))
            )
        assert status == 400, payload
        assert not payload["error"]["detail"].get("field"), "守卫那只 400 不带字段"
        assert _state(seeded) == before and _parts(seeded) == before_parts
        assert [row["name"] for row in _rows(seeded, admins=True)] == [collision, unaddressable], (
            "HTTP 删除既不能删掉自己，更不能误删解出来的 `备份#]`"
        )

        assert people.remove_admin(seeded, unaddressable) == {"removed": True}
        assert [row["name"] for row in _rows(seeded, admins=True)] == [collision], (
            "服务层只能撤掉点名的那一行；`备份#]` 原样保留"
        )

    def test_the_ip_mapping_name_is_not_tightened_by_the_ip_key_rule(self, seeded):
        """`ip_mapping` 的 `name` 仍然可以吃下带分隔符的写法：它不进路径段，删除永远按 IP。

        这条与 `TestDeleteIpMapping::test_an_ip_mapping_name_may_contain_a_separator_because_the_key_is_the_ip`
        同族，只是换成第二层那一族值——收紧它属于凭空加规则（裁定第 ① 条边界）。
        """
        for name in ("王%2f强", "备份%5c甲", "王%00强"):
            with make_server() as server:
                caller = as_role(server, "deployer")
                status, _, added = caller.json(
                    "PUT",
                    _url(seeded, "/ip-mapping/%s" % _seg("10.3.3.3")),
                    {"ip": "10.3.3.3", "name": name},
                )
                assert status == 200, "%r 当 IP 行的姓名必须放行：%r" % (name, added)
                assert added["item"]["name"] == name
                status, _, removed = caller.json(
                    "DELETE", _url(seeded, "/ip-mapping/%s" % _seg("10.3.3.3"))
                )
                assert status == 200 and removed["removed"] is True
        assert [row["name"] for row in _rows(seeded)] == ["张三", "李四"], (
            "三轮写删都只碰 10.3.3.3 那一行，预置两行原样还在"
        )


# ---------------------------------------------------------------------------
# 11. mock ↔ 后端同口径（执行 mock-api.js，不是 grep 它）
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
MOCK_ORACLE = REPO_ROOT / "tests" / "utils" / "mock_people_crosscheck.js"
NODE = shutil.which("node")

#: mock 比后端**更严**的唯一一处，已裁定留着不补（`docs/know-how.md` 第 2 节 `normalizeIp`
#: 那条：mock 不实现 IPv6 scope 语法，因为带 scope 的地址永远匹配不上任何客户端地址）。
#: 名单之外再冒出「只有 mock 拒」的值，就是新的漂移，必须当场说清。
KNOWN_MOCK_ONLY_IP_REJECTIONS = {"fe80::1%eth0"}


@pytest.mark.skipif(NODE is None, reason="本机没有 node ⇒ mock↔后端契约**未执法**（不是通过，是没测）")
class TestMockPeopleContract:
    """模板模式（mock）与真后端对**同一批值**必须给出同一个判决。

    `mock-api.js` 在这里是被**执行**的（`tests/utils/mock_people_crosscheck.js` 起 node 载入
    真身，跑 `MC.mock.handle`），不是被 grep 的：把 `validPersonName` 的斜杠那一档删掉、
    或把 token 名单从 `%2f` 改成别的写法，源码里的中文字面串可以一个字没动，而模板模式的
    判决已经完全变了——只有执行看得见（R45 / know-how 第 4 节 dlmath cross-check 的同款教训）。
    """

    def _mock(self, cases):
        assert MOCK_ORACLE.is_file(), "oracle 不见了：{}".format(MOCK_ORACLE)
        proc = subprocess.run(
            [NODE, str(MOCK_ORACLE)],
            input=json.dumps(cases, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(REPO_ROOT),
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        out = json.loads(proc.stdout)
        assert "error" not in out, out.get("error")
        assert out["count"] == len(cases), out
        return {(r["kind"], r["value"]): r for r in out["results"]}

    def test_mock_and_backend_reject_the_same_admin_names(self, seeded):
        """姓名这一族两侧**必须完全一致**：同判向、同错误码、同 `detail.field`、同文案。

        后端一侧走真 HTTP（`POST /admins`），顺带钉住「拒绝那只 400 带 `detail.field="name"`」
        ——这正是整条裁定的落点（守卫那只不带字段，所以字段级报错只在创建端做得到）。
        """
        candidates = (
            SEPARATOR_ADMIN_NAMES
            + ENCODED_SEPARATOR_ADMIN_NAMES
            + PERCENT_ESCAPE_ADMIN_NAMES
            + ROUNDTRIP_ADMIN_NAMES
            + ["新管理员", "张三"]  # 末位是重名 409：它不是「被拒」，两侧都必须算放行
        )
        backend: Dict[str, Any] = {}
        with make_server() as server:
            caller = as_role(server, "deployer")
            for name in candidates:
                status, _, payload = caller.json("POST", _url(seeded, "/admins"), {"name": name})
                backend[name] = (status, payload)
                if status == 400:
                    assert payload["error"]["code"] == "INVALID_INPUT", (name, payload)
                    assert payload["error"]["detail"]["field"] == "name", (
                        "后端那只 400 必须带字段，界面才指得到输入框：%r %r" % (name, payload)
                    )
        mock = self._mock([{"kind": "admin", "value": name} for name in candidates])
        disagreed = []
        for name in candidates:
            mine = mock[("admin", name)]
            backend_rejected = backend[name][0] == 400
            mock_rejected = mine["status"] == 400
            if backend_rejected:
                assert mine["code"] == "INVALID_INPUT", (name, mine)
                assert mine["field"] == "name", (name, mine)
                assert mine["message"] == backend[name][1]["error"]["message"], (
                    "拒绝的文案也要逐字一致（mock 不许换一套说法）：%r 后端=%r mock=%r"
                    % (name, backend[name][1]["error"]["message"], mine["message"])
                )
            if backend_rejected != mock_rejected:
                disagreed.append((name, backend[name][0], mine["status"]))
        assert not disagreed, "mock 与后端对姓名判向不一致：%r" % (disagreed,)

    def test_mock_never_allows_an_ip_key_the_backend_refuses(self, seeded):
        """IP 键这一族：**后端拒的 mock 必须也拒**（反方向只许那一支已裁定的 scope 名单）。

        为什么两边不许按「完全一致」核：know-how 第 2 节裁定 mock 不实现 IPv6 scope 语法，
        所以 mock 对**一切**带 `%` 的写法都比后端严（`fe80::1%eth0` 就是那一支）。
        被禁的方向恰恰是本卡要修的那个——mock 放行、后端拒绝，模板模式里加得进来而真后端不收。
        本轮新增的 `%2f` 一族落在这个交集里：两侧都拒，且后端的 400 带 `field="ip"`。
        """
        candidates = (
            list(BAD_IPS)
            + SEPARATOR_IP_KEYS
            + ENCODED_SEPARATOR_IP_KEYS
            + ["10.9.9.9", "fe80::1%eth0", "::ffff:10.1.2.3", "FE80::1"]
        )
        backend: Dict[str, Any] = {}
        with make_server() as server:
            caller = as_role(server, "deployer")
            for value in candidates:
                status, _, payload = caller.json(
                    "PUT",
                    _url(seeded, "/ip-mapping/%s" % _seg("10.0.0.1")),
                    {"ip": value, "name": "对照"},
                )
                backend[value] = (status, payload)
                if status == 400:
                    assert payload["error"]["detail"]["field"] == "ip", (value, payload)
        mock = self._mock([{"kind": "ip", "value": value} for value in candidates])
        too_lenient = []
        for value in candidates:
            mine = mock[("ip", value)]
            if backend[value][0] == 400 and mine["status"] != 400:
                too_lenient.append((value, mine["status"]))
        assert not too_lenient, (
            "mock 放行了后端会拒的 IP 键（就是「mock 发明了后端没有的行为」）：%r" % (too_lenient,)
        )
        mock_only = {
            value
            for value in candidates
            if mock[("ip", value)]["status"] == 400 and backend[value][0] != 400
        }
        assert mock_only == KNOWN_MOCK_ONLY_IP_REJECTIONS, (
            "只有 mock 拒、后端放行"
            "的名单变了（已裁定的那一支是 IPv6 scope 语法，新增的必须单独裁定）：%r" % (mock_only,)
        )

    def test_mock_still_allows_the_names_the_backend_allows(self, seeded):
        """反向哨兵：mock 也不许在姓名上**多拦**（多拦 = 模板模式里做不到的事后端做得到）。

        姓名这一族两侧字符集完全同源，所以这一侧可以按精确相等核——它挡的是
        「有人把守卫那三只 token 记成 `%2` 之类的前缀」（那会连 `100%达标` 一起拒掉）。
        名单取 `ROUNDTRIP_ADMIN_NAMES`，也就是 **`%` 后不是两位十六进制**、今天真的删得出去
        的那些；含 `%XX` 的名字在 `PERCENT_ESCAPE_ADMIN_NAMES` 里，创建端两侧都必须拒。
        """
        candidates = list(ROUNDTRIP_ADMIN_NAMES)
        mock = self._mock([{"kind": "admin", "value": name} for name in candidates])
        with make_server() as server:
            caller = as_role(server, "deployer")
            for name in candidates:
                status, _, payload = caller.json("POST", _url(seeded, "/admins"), {"name": name})
                assert status in (201, 409), (name, payload)
                assert mock[("admin", name)]["status"] == status, (
                    "两侧对同一个合法姓名的判决必须一致：%r 后端=%s mock=%s"
                    % (name, status, mock[("admin", name)]["status"])
                )
