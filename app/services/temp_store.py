"""临时文件（§7.4；T-14 / M2-10）。

上传或链接导入的原文先落在这里，只给**上传者本人**看：所有者 = 同一个 `mc_cid`
且同一个客户端 IP。区域是 `data/temporary/<token>/`，`meta.json` 记所有者、机型、
原名、大小与到期时间；它不属于权威数据，因此不参与机型目录布局、也不进备份。

**惰性清理**（决策 4）：M2 阶段不引入定时器或后台线程——过期目录在每次创建/访问/
列举时机会式删除。过期 token 一律 404「临时文件已过期，请重新上传」，并且
**绝不回退去读同名服务器文件**（这是 F01 的老缺陷）。
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
from ..core.errors import FORBIDDEN, NOT_FOUND, TOO_LARGE, AppError

CONTENT_FILE = "content"
META_FILE = "meta.json"
#: §7.4 的上限：同一所有者 ≤ 20 个，全局总量 ≤ 1 GB。
MAX_PER_OWNER = 20
GLOBAL_LIMIT_BYTES = 1024 * 1024 * 1024
EXPIRED_MESSAGE = "临时文件已过期，请重新上传"

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
        raise AppError(FORBIDDEN, "临时文件只对上传者可见")
    path = os.path.join(directory, CONTENT_FILE)
    if not os.path.isfile(path):
        raise AppError(NOT_FOUND, EXPIRED_MESSAGE, token=token)
    with open(path, "rb") as fh:
        return meta, fh.read()


def document(profile_id: str, token: str, cid: Optional[str], ip: str) -> Dict[str, Any]:
    """临时文件的 §6.3 文档响应（`file.kind = "temp"`，带 `token` 与 `expires_at`）。"""
    meta, data = take(profile_id, token, cid, ip)
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


def purge_expired(now: Optional[datetime] = None) -> int:
    """删掉已过期的临时目录，返回删除数（惰性清理的唯一入口）。"""
    root = storage.get_temporary_dir()
    if not os.path.isdir(root):
        return 0
    moment = now or _now()
    removed = 0
    for entry in os.listdir(root):
        directory = os.path.join(root, entry)
        if not os.path.isdir(directory):
            continue
        meta = _read_meta_file(os.path.join(directory, META_FILE))
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


def _remove_tree(directory: str) -> None:
    shutil.rmtree(directory, ignore_errors=True)
