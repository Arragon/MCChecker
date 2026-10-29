"""临时文件（§7.4；T-14 / M2-10）。

上传或链接导入的原文先落在这里，只给**上传者本人**看：所有者 = 同一个 `mc_cid`
且同一个客户端 IP（两个要素都不满足即 404，不承认 token 存在）。区域是 `data/temporary/<token>/`，`meta.json` 记所有者、机型、
原名、大小与到期时间；它不属于权威数据，因此不参与机型目录布局、也不进备份。

**惰性清理**（决策 4）：过期目录在每次创建/访问/列举时机会式删除；M3-08 起
`services/scheduler.py` 每小时再兜一次（`purge_expired(skip_incomplete=True)`），
把「之后再没人碰过」的过期目录也收掉。过期 token 一律 404「临时文件已过期，请重新上传」，并且
**绝不回退去读同名服务器文件**（这是 F01 的老缺陷）。

**「没有 `meta.json`」不等于「可以删」**：`create()` 的落盘顺序是 `makedirs` → 原文 →
`meta.json` 最后，所以一个还没有 meta 的目录可能就是某次上传正在写的那一半。后台清理走
`skip_incomplete=True` + 宽限期（见 `purge_expired` 与 `INCOMPLETE_GRACE_SECONDS`）：年龄没超过
宽限期的半成品不碰，超过的只会是中途死掉的孤儿，照收——否则孤儿要等「有人再上传」才没了。
**这一档挡不住全部**：上传路径自己在 `create()` 开头跑的是默认档，两次上传恰好并发时，它仍可能
删掉另一趟正在写的半成品目录——那是本卡之前就存在的毫秒级竞态，比小时级定时器的窗口小得多，
目前作为已知残留保留，不在这里改 `create()` 的语义。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..core import fsutil, parser, storage
from ..core.errors import NOT_FOUND, PARSE_FAILED, TOO_LARGE, AppError

CONTENT_FILE = "content"
META_FILE = "meta.json"
#: §7.4 的上限：同一所有者 ≤ 20 个，全局总量 ≤ 1 GB。
MAX_PER_OWNER = 20
GLOBAL_LIMIT_BYTES = 1024 * 1024 * 1024
EXPIRED_MESSAGE = "临时文件已过期，请重新上传"
#: 后台清理对「没有 meta 的目录」的宽限期：按目录 mtime 超过这个年龄的孤儿才被定时档删掉。
#: 取 24 小时：一趟上传从 `makedirs` 到写完 `meta.json` 是连续的一次性动作，≤ 50 MB 的原文
#: 在慢链路上也是分钟级写完；24 小时与 `DEFAULT_TEMP_TTL_HOURS` 同量级，是人尺度上对
#: 「还在写」最宽松的合理解释——过了宽限期还没有 meta，只可能是中途被杀死的孤儿。
INCOMPLETE_GRACE_SECONDS = 24 * 3600

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _now() -> datetime:
    return datetime.now()


def new_token() -> str:
    return secrets.token_urlsafe(18)


def create(
    profile_id: str,
    cid: Optional[str],
    ip: str,
    name: str,
    data: bytes,
    ttl_hours: int,
    max_bytes: int,
) -> Dict[str, Any]:
    """写入一个临时文件并返回它的 meta（含 `token`）。"""
    clean_name = fsutil.validate_filename(name)
    if len(data) > max_bytes:
        raise AppError(
            TOO_LARGE,
            "文件超过单次上传上限 %d MB" % (max_bytes // 1024 // 1024),
            limit_bytes=max_bytes,
        )
    purge_expired()
    _enforce_quota(cid, ip, len(data))

    token = new_token()
    created = _now()
    expires = created + timedelta(hours=max(1, int(ttl_hours)))
    directory = storage.get_temporary_dir(token)
    meta = {
        "token": token,
        "owner_cid": cid or "",
        "owner_ip": ip or "",
        "profile": profile_id,
        "name": clean_name,
        "size": len(data),
        "created_at": created.strftime(_TIME_FORMAT),
        "expires_at": expires.strftime(_TIME_FORMAT),
    }
    os.makedirs(directory, exist_ok=True)
    fsutil.atomic_write_bytes(os.path.join(directory, CONTENT_FILE), data)
    fsutil.atomic_write_bytes(
        os.path.join(directory, META_FILE),
        json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return meta


def take(profile_id: str, token: str, cid: Optional[str], ip: str) -> Tuple[Dict[str, Any], bytes]:
    """按 token 取回 `(meta, 原文)`；本人校验不过或已过期时报错。

    不在这里做全局清理：那样会把「已过期」压成「不存在」，§7.4 要求的过期提示就丢了。
    过期目录在 `_read_meta`（就地删除被访问的这一个）和 `create`（全局顺手清）两处回收。
    """
    directory, meta = _read_meta(token)
    if meta.get("profile") != profile_id:
        raise AppError(NOT_FOUND, "临时文件不存在或已过期", token=token)
    if (meta.get("owner_cid") or "") != (cid or "") or (meta.get("owner_ip") or "") != (ip or ""):
        # 卡面要求「其他人 404」：状态码不承认 token 存在，消息仍说清原因，便于 UI 提示。
        raise AppError(NOT_FOUND, "临时文件只对上传者可见", token=token)
    path = os.path.join(directory, CONTENT_FILE)
    if not os.path.isfile(path):
        raise AppError(NOT_FOUND, EXPIRED_MESSAGE, token=token)
    with open(path, "rb") as fh:
        return meta, fh.read()


def document(profile_id: str, token: str, cid: Optional[str], ip: str) -> Dict[str, Any]:
    """临时文件的 §6.3 文档响应（`file.kind = "temp"`，带 `token` 与 `expires_at`）。"""
    meta, data = take(profile_id, token, cid, ip)
    ext = meta["name"].rsplit(".", 1)[-1].lower() if "." in meta["name"] else ""
    if ext in ("ini", "cfg", "conf", "txt"):
        raise AppError(
            PARSE_FAILED,
            "该格式不支持结构树，仅提供原文查看与下载",
            raw_available=True,
            unsupported=True,
        )
    try:
        compact = parser.to_compact(data, meta["name"])
    except AppError as exc:
        if exc.code == "PARSE_FAILED":
            exc.detail["raw_available"] = True
        raise
    nodes = compact["nodes"]
    return {
        "file": {
            "profile": profile_id,
            "name": meta["name"],
            "kind": "temp",
            "version": "temp",
            "version_label": "临时文件（仅自己可见）",
            "sha256": hashlib.sha256(data).hexdigest(),
            "format": compact["format"],
            "size": len(data),
            "updated_at": meta["created_at"],
            "parser_version": parser.PARSER_VERSION,
            "node_count": len(nodes["p"]) - 1,
            "read_only": True,
            "warnings": list(compact.get("warnings") or []),
            "expires_at": meta["expires_at"],
            "token": token,
        },
        "nodes": nodes,
    }


def raw(profile_id: str, token: str, cid: Optional[str], ip: str) -> Tuple[bytes, str]:
    """临时文件原文（下载名 = 上传时的原名）。"""
    meta, data = take(profile_id, token, cid, ip)
    return data, meta["name"]


def purge_expired(now: Optional[datetime] = None, skip_incomplete: bool = False) -> int:
    """删掉已过期的临时目录，返回删除数（惰性清理的唯一入口）。

    `skip_incomplete=True` 是**后台定时清理那一档**：连 `meta.json` 都还没写出来的目录，
    只有年龄（目录 mtime）没超过 `INCOMPLETE_GRACE_SECONDS` 的才跳过；超过宽限期的照收——
    否则进程被杀留下的孤儿要等「下一次有人上传」才可能被清掉，后台定时器永远不收它们。
    `create()` 的顺序是 `makedirs` → 原文 → meta 最后，所以「读不到 meta」恰恰是「某次上传正在
    写这一半」的形状，把它删掉就是删别人正在写的东西（M3-08 修复轮 / Important 3）。默认 `False`
    保住上传路径自己的既有语义（那里在 `makedirs` 之前跑，看不到自己的半成品，孤儿目录照收；
    但**并发另一趟上传**的半成品它看得到——那毫秒级竞态是既有残留，这一档不覆盖，见模块 docstring）。
    meta 文件**存在但读不懂**的目录两档都照删——那是真损坏，不是半成品。
    """
    root = storage.get_temporary_dir()
    if not os.path.isdir(root):
        return 0
    moment = now or _now()
    removed = 0
    for entry in os.listdir(root):
        directory = os.path.join(root, entry)
        if not os.path.isdir(directory):
            continue
        meta_path = os.path.join(directory, META_FILE)
        if skip_incomplete and not os.path.isfile(meta_path) and not _aged_past_grace(directory, moment):
            continue
        meta = _read_meta_file(meta_path)
        if meta is None or _expired(meta, moment):
            _remove_tree(directory)
            removed += 1
    return removed


def total_bytes() -> int:
    root = storage.get_temporary_dir()
    if not os.path.isdir(root):
        return 0
    size = 0
    for entry in os.listdir(root):
        path = os.path.join(root, entry, CONTENT_FILE)
        if os.path.isfile(path):
            size += os.path.getsize(path)
    return size


def _enforce_quota(cid: Optional[str], ip: str, incoming: int) -> None:
    root = storage.get_temporary_dir()
    if os.path.isdir(root):
        mine = 0
        for entry in os.listdir(root):
            meta = _read_meta_file(os.path.join(root, entry, META_FILE))
            if meta and (meta.get("owner_cid") or "") == (cid or "") and (meta.get("owner_ip") or "") == (ip or ""):
                mine += 1
        if mine >= MAX_PER_OWNER:
            raise AppError(TOO_LARGE, "你已经有 %d 个临时文件，请先删除一部分" % MAX_PER_OWNER)
    if total_bytes() + incoming > GLOBAL_LIMIT_BYTES:
        raise AppError(TOO_LARGE, "临时文件总量已达 1 GB 上限，请稍后再试")


def _read_meta(token: str) -> Tuple[str, Dict[str, Any]]:
    try:
        safe = storage.validate_temp_token(token)
    except ValueError:
        raise AppError(NOT_FOUND, "临时文件不存在或已过期", token=token)
    directory = storage.get_temporary_dir(safe)
    meta = _read_meta_file(os.path.join(directory, META_FILE))
    if meta is None:
        raise AppError(NOT_FOUND, "临时文件不存在或已过期", token=safe)
    if _expired(meta, _now()):
        _remove_tree(directory)
        raise AppError(NOT_FOUND, EXPIRED_MESSAGE, token=safe)
    return directory, meta


def _read_meta_file(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as fh:
            payload = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _expired(meta: Dict[str, Any], moment: datetime) -> bool:
    raw_value = str(meta.get("expires_at") or "")
    try:
        return datetime.strptime(raw_value, _TIME_FORMAT) <= moment
    except ValueError:
        return True


def _aged_past_grace(directory: str, moment: datetime) -> bool:
    """没有 meta 的目录是否已超过 `INCOMPLETE_GRACE_SECONDS`（年龄只按注入的 `moment` 算）。

    判据是目录自身的 mtime：`os.makedirs`、`atomic_write_bytes` 建同目录临时文件和
    `os.replace` 落位都会刷新它，所以「最后一次往里写东西」不会晚于 mtime；过完宽限期
    还缺 meta，就不可能还有活着的写者。stat 失败保守返回 False——本轮跳过，下轮再看。
    """
    try:
        age = moment.timestamp() - os.stat(directory).st_mtime
    except OSError:
        return False
    return age >= INCOMPLETE_GRACE_SECONDS


def _remove_tree(directory: str) -> None:
    shutil.rmtree(directory, ignore_errors=True)
