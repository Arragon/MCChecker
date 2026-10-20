"""IP 对应表与管理员名单（规范 §7.9、§9.8.10、§11.1；M3-13 / INH-1898）。

分层（§3.3）：`server/api_people.py` 只做「解析 → 授权 → 调这里 → 序列化」，持久化一律
走 `storage`，修订号只在**写成功之后**由本层递增（全局约束 / 决策 16）。

两份文件的格式与 v1 逐字一致（§5.2）：`ip_mapping.json` = `[{"ip","name","updated_at"}]`、
`admin_users.json` = `[{"name","updated_at"}]`。

**这张表就是「谁能写」，按高风险改**：

* 身份在**每个请求**重新解析（§4.1、`http.py` 第 4 步 → `auth.resolve_actor` 里实时调
  `storage.resolve_person_by_ip` / `is_admin_user`），所以这里**不许加任何缓存**——删掉一条
  IP 行或一个管理员之后，那个人**下一次**审阅请求就是 403（卡 4 的
  `tests/test_api_authz.py::TestRevocation` 已经在执法这条，本卡只是被它删的那一方）。
* IP 写入前一律归一成 `ipaddress` 的规范写法（复用 `auth.normalize_ip`，不自己写第二份）：
  客户端地址是规范化过的，而 `resolve_person_by_ip` 按**字符串逐字**比较，所以人写的
  `FE80::1`、`::ffff:10.1.2.3` 存进去就永远匹配不上——登记了 IP、加了管理员，身份却不生效。
  只在**写入那一次**归一，读取路径原样回显磁盘上的旧写法（§5.2「现有字段不变」）。
* 「改 IP」（路径 IP ≠ body IP）必须**在同一次机型锁里先删后写**：分两次取锁的话，两次锁
  之间跑完的另一次写会被这里的旧副本覆盖（§5.4 的丢失更新，`docs/know-how.md` 第 2 节那条
  「锁外读 + 锁内写」的同族错法），最坏结果是表里同时留下新旧两条或旧条目丢失。
* 「删了 0 条」不递增修订号，也不写一个字节（与 `updates.delete_source` 同口径）。
* 管理员姓名与 IP 键**都不许含让「删除路径」走不通的东西**（M3-13 / INH-1898 三轮裁定）：
  §7.9 的删除把姓名 / IP 放进路径段，而 §16.8 第 3 条的全站守卫会把编码过的分隔符拦成一只
  不带 `detail.field` 的 400——`api.seg()`（= `encodeURIComponent`）必然产出的 `%2F` / `%5C`
  是第一层，值里**字面**写着 `%2f` / `%5c` / `%00` 的是第二层，而**任何**字面 `%XX` 会在
  删除端被二次解码成另一个名字（第三层，评审 F1；`测试%20甲` 会撤掉 `测试 甲`）。放行到
  创建端就是「加得进来、删不出去」甚至「撤错人」。两个入口必须达成一致，所以限制落在
  **创建端**（400 + `detail.field`）：管理员姓名走 `_validated_admin_name` 的
  `_PERCENT_ESCAPE_RE`；IP 键仍只走 `_validated_ip_key` 的三只 token，其他 `%XX` 的可达性
  尚未裁定。删除端在服务层保持宽松，HTTP 边界则由守卫把二次编码路径整段拒掉
  （存量行只能走服务层/运维）。
* IP 识别不是强认证（§11.1）：只适用于可信内网。这条口径必须出现在设置页文案里，
  见 `app/web/assets/js/views/settings.js` 的人员分段。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core import storage
from ..core.errors import FILE_EXISTS, INVALID_INPUT, AppError
from . import auth, revision

logger = logging.getLogger(__name__)

#: 两份文件都属于「设置」分量（§7.3；与 `updates.SETTINGS_PART`、`scheduler.SCHEDULE_PART` 同值）。
PART = "settings"

#: §11.2：姓名 = 去空白后 1–100 字符、无控制字符。
MAX_NAME_LENGTH = 100
#: `detail.field` 的两个落点，与 `views/settings.js` 的 `personDialog`/`adminDialog` 字段名一致
#: （前后端靠这两个键把错误显示在对应输入框下方，§7.1）。
IP_FIELD = "ip"
NAME_FIELD = "name"

#: 值进 URL 路径段之后会让「删除」这条出路消失的**字面字符**（M3-13 / INH-1898 第一轮裁定：
#: 在写入端拒）。`DELETE /admins/{name}` 与 `DELETE /ip-mapping/{ip}` 都要把值塞进路径段，
#: `api.seg()`（= `encodeURIComponent`）必然把它们编成 `%2F` / `%5C`，撞上 §16.8 第 3 条的
#: 全站守卫 `http.py::_reject_encoded_separators`——那只 400 **不带** `detail.field`，
#: 人撤不掉、界面还只能报全局错误。放行到创建端就是「加得进来、删不出去」的权限面死路。
_UNMANAGEABLE_PATH_CHARS = ("/", "\\")

#: 同一条死路的**第二层**（M3-13 / INH-1898 第二轮裁定）：值里**字面**写着这三只 token（大小写
#: 任一）。这一族咬人的方式和上面那条不一样——`api.seg()` 会先把 `%` 自己编成 `%25`，于是
#: `王%2f强` 的路径段是 `王%252f强`，躲过守卫的 raw 那一份，却在**解码后**那一份里重新长出
#: `%2f`；而 `_reject_encoded_separators` 把两份一起看（`haystacks`），结果还是删不出去。
#: 这三只 token 与守卫的 `_ENCODED_UNSAFE` 同源；此处手抄而不 import，是因为 §3.3 不许
#: `services` 反向依赖 `server` 层。两枚常量不许各走各的：常量集合相等由
#: `tests/test_api_people.py::TestUnmanageableValue::test_the_creation_rule_tracks_the_guard_token_list`
#: 钉，「被这一档拒掉的每一串在删除端确实只得到一只不带字段的 400」由
#: `TestAdmins::test_admin_name_with_a_literal_encoded_separator_is_rejected_at_creation`
#: 与 `TestUnmanageableValue::test_an_encoded_separator_inside_an_ipv6_scope_id_reaches_the_unmanageable_state`
#: 打真 HTTP 钉。
#: 注意真正的 NUL 字符到不了这里——§11.2 的控制字符那一档先拒；这三只 token 挡的是**字面写法**。
_UNMANAGEABLE_PATH_TOKENS = ("%2f", "%5c", "%00")

#: 第三族（评审 F1）：任何字面 `%` + 两位十六进制。路径被解码两次，存进去的名字在删除端
#: 会变成另一个名字（`测试%20甲` 会撤掉 `测试 甲`）；创建端一律拒。`%` 后不是两位十六进制的
#: 写法（`100%达标`、`完成度%zz`、裸 `%`）不在这一族，必须继续放行。
_PERCENT_ESCAPE_RE = re.compile(r"%[0-9A-Fa-f]{2}")

_BAD_IP_MESSAGE = "请输入合法的 IPv4/IPv6 地址"
_BAD_NAME_MESSAGE = "姓名需为 1-%d 个字符，且不含控制字符" % MAX_NAME_LENGTH
#: 文案与 mock 的 `BAD_ADMIN_NAME_MESSAGE`（`app/web/assets/js/mock/mock-api.js`）**逐字一致**，
#: 由 `TestMockPeopleContract` 逐值比对——模板模式许出一条后端会拒的姓名，就是「mock 发明了
#: 后端没有的行为」（R19）。IP 那一档不需要一份对应文案：mock 的 `normalizeIp` 拒绝**一切**带
#: `%` 的写法（know-how 第 2 节已裁定「mock 只比后端严、不补 IPv6 scope 语法」），所以在 mock
#: 里这一族早就在 `_BAD_IP_MESSAGE` 那一步返回了，同向、更早。
_BAD_ADMIN_NAME_MESSAGE = (
    "管理员姓名不能包含 / 、\\ 或任何 %XX 编码写法（这样的人员无法从名单里删除）"
)
_UNMANAGEABLE_IP_MESSAGE = (
    "IP 地址不能包含 / 、\\ 或 %2f / %5c / %00 这类编码分隔符（这样的条目无法从对应表里删除）"
)


# ---------------------------------------------------------------------------
# 读（§7.9：全部角色可读）
# ---------------------------------------------------------------------------


def list_ip_mapping(pid: str) -> Dict[str, Any]:
    """`GET /p/{pid}/ip-mapping` → `{"items": [{"ip","name","updated_at"}]}`。

    磁盘上是什么写法就回显什么（旧行可能是非规范拼写；归一只在写入那一次发生）。
    读接口：一个字节都不写、永不递增。
    """
    with storage.use_profile(pid):
        rows = storage.load_ip_mapping()
    return {"items": [_ip_row(row) for row in rows if isinstance(row, dict)]}


def list_admins(pid: str) -> Dict[str, Any]:
    """`GET /p/{pid}/admins` → `{"items": [{"name","updated_at"}]}`。"""
    with storage.use_profile(pid):
        rows = storage.load_admin_users()
    return {"items": [_admin_row(row) for row in rows if isinstance(row, dict)]}


# ---------------------------------------------------------------------------
# 写（§7.9：只有部署者；授权在 handler，落盘在这里）
# ---------------------------------------------------------------------------


def put_ip_mapping(pid: str, path_ip: Any, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`PUT /p/{pid}/ip-mapping/{ip}`：登记/改名，或（路径 ≠ body）改 IP。

    载荷 `{"ip","name"}`：`ip` 缺省取路径那一个（只想改姓名时不必重复 IP），与 mock 同口径；
    显式给了非法值（空串、`999.1.1.1`、`10.0.0.1/24`）就是 400 `detail.field="ip"`，
    **在碰磁盘之前**抛出，文件字节不变。
    要落盘的那个键另外还要过 `_validated_ip_key`（含编码分隔符 ⇒ 同样 400 `field="ip"`）。
    **路径上的旧键不过这一档**：它是「被删掉的那一半」，与 `delete_ip_mapping` 同口径保持宽松，
    否则 v1 直接写文件留下的存量行会连「改成干净 IP」这条出路也被堵死。
    这里的 `name` 只走 §11.2 的通用姓名校验，**不**带管理员那条分隔符限制——它不进路径段，
    删除永远按 IP 走，收紧它属于凭空加规则。
    """
    body = payload if isinstance(payload, dict) else {}
    old_ip = _validated_ip(path_ip)
    new_ip = _validated_ip_key(_validated_ip(body.get(IP_FIELD, path_ip)))
    name = _validated_name(body.get(NAME_FIELD))
    stamp = _now()
    # 一次锁内完成「删旧 + 写新」：`save_ip_mapping` 内部再取同一把 per-pid RLock 只是重入。
    with storage.use_profile(pid), storage.profile_write(pid):
        rows = storage.load_ip_mapping()
        if new_ip != old_ip:
            rows = [row for row in rows if not _row_ip_matches(row, old_ip)]
        index = _ip_index(rows, new_ip)
        entry = {"ip": new_ip, "name": name, "updated_at": stamp}
        if index is None:
            rows = rows + [entry]
        else:
            rows = [entry if i == index else row for i, row in enumerate(rows)]
        storage.save_ip_mapping(rows)
    revision.bump(pid, PART)
    logger.info("IP 对应表已保存：机型=%s %s → %s（%s）", pid, old_ip, new_ip, name)
    return {"item": dict(entry)}


def delete_ip_mapping(pid: str, ip: Any) -> Dict[str, Any]:
    """`DELETE /p/{pid}/ip-mapping/{ip}` → `{"removed": bool}`。

    返回形状取**后端既有先例** `DELETE /sources/{name}` ⇒ `{removed: bool}`
    （`app/server/api_sources.py:86`），不沿用 mock 那几个「路由还不存在」的
    `{deleted: true}`（`docs/know-how.md` 第 2 节把它记为**未定契约**）。
    没有这一行 ⇒ `removed: false`、一个字节都不写、修订号不动。

    这里**故意不做** `_validated_ip_key` 那一档：存量行（v1 的界面没做过 IP 校验，直接把文件
    写出去过）可能长着编码分隔符，把创建端的限制也压到删除端只会**缩小**出路。别把它当疏漏
    「顺手统一掉」——两个入口不对称是裁定，不是漂移。
    """
    target = _validated_ip(ip)
    with storage.use_profile(pid), storage.profile_write(pid):
        rows = storage.load_ip_mapping()
        kept = [row for row in rows if not _row_ip_matches(row, target)]
        removed = len(kept) < len(rows)
        if removed:
            storage.save_ip_mapping(kept)
    if removed:
        revision.bump(pid, PART)
        logger.info("IP 对应表已删除条目：机型=%s %s", pid, target)
    return {"removed": removed}


def add_admin(pid: str, name: Any) -> Dict[str, Any]:
    """`POST /p/{pid}/admins` → 201 `{"item": {...}}`；重名 → 409 `FILE_EXISTS`。

    409 的错误码用 `FILE_EXISTS`（§7.1 表里唯一映射 409 的「同名已存在」，与
    `updates.create_source` 同一只码），不是 mock 原先的 `INVALID_INPUT`（那只码按 §7.1
    只能配 400）；本卡同 PR 把 mock 一起改过来。

    姓名走 `_validated_admin_name`（含 `/`、`\\`，或字面的 `%2f` / `%5c` / `%00` ⇒ 400
    `detail.field="name"`，**在碰磁盘之前**）。
    """
    clean = _validated_admin_name(name)
    stamp = _now()
    with storage.use_profile(pid), storage.profile_write(pid):
        rows = storage.load_admin_users()
        if _name_index(rows, clean) is not None:
            raise AppError(FILE_EXISTS, "该管理员已存在", name=clean, field=NAME_FIELD)
        entry = {"name": clean, "updated_at": stamp}
        storage.save_admin_users(rows + [entry])
    revision.bump(pid, PART)
    logger.info("管理员名单已新增：机型=%s %s", pid, clean)
    return {"item": dict(entry)}


def remove_admin(pid: str, name: Any) -> Dict[str, Any]:
    """`DELETE /p/{pid}/admins/{name}` → `{"removed": bool}`。

    移除后此人**下一次**审阅请求即 403（身份实时解析，§4.1），不需要任何「踢下线」动作。

    这里**故意只用** `_validated_name`、不加 `_validated_admin_name` 那两层限制：新数据已经被
    创建端挡住，而 v1 直接写文件的旧名单里可能已经有含 `/`、`\\` 或字面 `%2f` / `%5c` / `%00`
    的行。把限制也加到删除端只会让那些存量行在本层再多一道拒绝，**缩小**而不是打开出路。
    这一条不是「还没清理干净的不对称」，是裁定，执法者是这三条用例：
    `TestAdmins::test_the_removal_side_stays_permissive_for_a_legacy_row`、
    `TestAdmins::test_the_removal_side_stays_permissive_for_a_legacy_encoded_separator_row`、
    `TestUnmanageableValue::test_an_encoded_separator_inside_an_ipv6_scope_id_reaches_the_unmanageable_state`
    （最后那条同时钉住「HTTP 那一侧到不了它们」，所以服务层是唯一还留着出路的入口）。
    看到这段想「统一两个入口」的人，请先让这三条红一遍再决定。
    """
    clean = _validated_name(name)
    with storage.use_profile(pid), storage.profile_write(pid):
        rows = storage.load_admin_users()
        kept = [row for row in rows if _row_name(row) != clean]
        removed = len(kept) < len(rows)
        if removed:
            storage.save_admin_users(kept)
    if removed:
        revision.bump(pid, PART)
        logger.info("管理员名单已移除：机型=%s %s", pid, clean)
    return {"removed": removed}


# ---------------------------------------------------------------------------
# 校验与内部工具
# ---------------------------------------------------------------------------


def _validated_ip(raw: Any) -> str:
    """规范化 IP；非法即 400（`detail.field="ip"`）。

    解析与规范化只此一处出处——`auth.normalize_ip` 就是身份解析用来比对客户端地址的那一只
    （`::ffff:10.1.2.3` → `10.1.2.3`、大写转小写、去方括号），所以「登记时怎么写的」与
    「请求来了怎么比的」保证是同一套规则。它解析失败返回空串，不抛异常。
    """
    canonical = auth.normalize_ip(raw)
    if not canonical:
        raise AppError(INVALID_INPUT, _BAD_IP_MESSAGE, field=IP_FIELD)
    return canonical


def _validated_name(raw: Any) -> str:
    """姓名校验（§11.2）：去空白后 1–100 字符、无控制字符；非法即 400（`detail.field="name"`）。

    必须先于落盘：`storage.upsert_ip_mapping` / `upsert_admin_user` 对空值抛的是**裸
    `ValueError`**，透到 HTTP 层就是 500（`R18` 那一类）。
    """
    text = str(raw if raw is not None else "").strip()
    if not text or len(text) > MAX_NAME_LENGTH:
        raise AppError(INVALID_INPUT, _BAD_NAME_MESSAGE, field=NAME_FIELD)
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise AppError(INVALID_INPUT, _BAD_NAME_MESSAGE, field=NAME_FIELD)
    return text


def _unmanageable_path_segment(text: str) -> Optional[str]:
    """这个值将来要当 URL 路径段用；返回第一串让它「删不出去」的子串，`None` 表示走得通。

    判据不是字符集洁癖，而是「写入端与删除端必须对同一个值达成一致」：`api.seg()` 就是
    `encodeURIComponent`，所以
    ① 含 `/` 或 `\\` ⇒ 路径段里出现 `%2F` / `%5C`，撞守卫的 raw 那一份；
    ② 含字面 `%2f` / `%5c` / `%00`（任意大小写）⇒ `%` 先被编成 `%25`（raw 里没有这三只 token），
       但解码后又重新长出它们，撞守卫的 decoded 那一份。
    两种都是 400 且**不带** `detail.field`（守卫全站共用、早于路由，永远别指望它给你带字段），
    于是记录加得进来、撤不出去。真正的 NUL 字符到不了这里——§11.2 的控制字符那一档先拒。

    这一档只负责斜杠、反斜杠与守卫那三只 token；**整族**的字面 `%XX`（`%20`、`%5d` 等）
    由 `_validated_admin_name` 的 `_PERCENT_ESCAPE_RE` 在管理员姓名入口统一拒绝。
    IP 键仍只用本函数：`ipaddress` 对 `%20` 之类 scope id 的可达性尚未裁定，保持现状。
    """
    for char in _UNMANAGEABLE_PATH_CHARS:
        if char in text:
            return char
    lowered = text.lower()
    for token in _UNMANAGEABLE_PATH_TOKENS:
        if token in lowered:
            return token
    return None


def _validated_ip_key(canonical: str) -> str:
    """**只在写入端**给 IP 键加这一档（M3-13 / INH-1898 第二轮裁定）。

    IP 这一侧的可达性和管理员姓名不一样，两条都要记住：
    * 裸 `/`、`\\` 到不了表里——`auth.normalize_ip` 用 `ipaddress.ip_address`，含斜杠的写法
      一律解析失败（`10.0.0.1/24` 是空串 ⇒ 400 `field="ip"`）。这一档由
      `test_a_cidr_style_ip_with_a_separator_never_reaches_the_table` 钉着。
    * 但 `%2f` / `%5c` / `%00` **到得了**：3.9 的 `ipaddress` 认 IPv6 scope id，而 scope 段
      只限字符集不限含义，所以 `fe80::1%2feth0` 会被原样规范化并原样落盘（实测
      `normalize_ip` 把它逐字回显）。键一旦长这样，`DELETE /ip-mapping/{ip}` 的路径段就必然
      解码回 `%2f` ⇒ 撞守卫 ⇒ 那一行永远撤不掉。所以这张表也**必须**在创建端挡，
      判据与管理员姓名同一条（可达性自己证，见
      `test_an_encoded_separator_inside_an_ipv6_scope_id_reaches_the_unmanageable_state`）。

    入参必须是**已规范化**的那个值（要落盘的键），不是原始输入：落盘的是什么，删除端就要寻什么。
    """
    blocked = _unmanageable_path_segment(canonical)
    if blocked is not None:
        raise AppError(INVALID_INPUT, _UNMANAGEABLE_IP_MESSAGE, field=IP_FIELD)
    return canonical


def _validated_admin_name(raw: Any) -> str:
    """管理员姓名 = §11.2 的通用姓名校验 + 「删除路径必须走得通」这一条。

    两个入口（`POST /admins` 与 `DELETE /admins/{name}`）对字符集必须**一致**，否则就是
    「加得进来、删不出去」；这条边界以前只由 §16.8 的守卫在删除端单方面挡，现在挪到创建端，
    于是 400 带得上 `detail.field="name"`，界面能指到输入框。

    **只作用于管理员名单**：`ip_mapping` 的 `name` 不进路径段（删除按 IP 寻址），给它加同样
    的限制就是凭空收紧。
    """
    text = _validated_name(raw)
    if _PERCENT_ESCAPE_RE.search(text) is not None:
        raise AppError(INVALID_INPUT, _BAD_ADMIN_NAME_MESSAGE, field=NAME_FIELD)
    if _unmanageable_path_segment(text) is not None:
        raise AppError(INVALID_INPUT, _BAD_ADMIN_NAME_MESSAGE, field=NAME_FIELD)
    return text


def _same_ip(stored: Any, canonical: str) -> bool:
    """逐字相等，或**规范化之后**相等（旧行可能是 `FE80::1` 这种人写的写法）。

    规范化不了的原值（v1 界面没做过 IP 校验，表里可能有非地址字符串）只按逐字比较，
    所以它既不会被误删，也永远不会匹配上某个真实客户端。
    """
    left = str(stored or "").strip()
    if left == canonical:
        return True
    return bool(left) and auth.normalize_ip(left) == canonical


def _row_ip_matches(row: Any, canonical: str) -> bool:
    if not isinstance(row, dict):
        return False
    return _same_ip(row.get("ip"), canonical)


def _ip_index(rows: List[Any], canonical: str) -> Optional[int]:
    for index, row in enumerate(rows):
        if isinstance(row, dict) and _same_ip(row.get("ip"), canonical):
            return index
    return None


def _name_index(rows: List[Any], name: str) -> Optional[int]:
    for index, row in enumerate(rows):
        if isinstance(row, dict) and _row_name(row) == name:
            return index
    return None


def _row_name(row: Any) -> str:
    return str(row.get("name") or "").strip() if isinstance(row, dict) else ""


def _ip_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ip": str(row.get("ip") or ""),
        "name": _row_name(row),
        "updated_at": row.get("updated_at"),
    }


def _admin_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": _row_name(row), "updated_at": row.get("updated_at")}


def _now() -> str:
    """服务器本地时间字符串（§7.1），格式与 `storage` 自己写的 `updated_at` 逐字一致。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
