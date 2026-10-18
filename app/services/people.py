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
* IP 识别不是强认证（§11.1）：只适用于可信内网。这条口径必须出现在设置页文案里，
  见 `app/web/assets/js/views/settings.js` 的人员分段。
"""

from __future__ import annotations

import logging
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

_BAD_IP_MESSAGE = "请输入合法的 IPv4/IPv6 地址"
_BAD_NAME_MESSAGE = "姓名需为 1-%d 个字符，且不含控制字符" % MAX_NAME_LENGTH


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
    """
    body = payload if isinstance(payload, dict) else {}
    old_ip = _validated_ip(path_ip)
    new_ip = _validated_ip(body.get(IP_FIELD, path_ip))
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
    """
    clean = _validated_name(name)
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
