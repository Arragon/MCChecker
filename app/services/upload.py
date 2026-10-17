"""上传保存与链接导入（§7.4、§5.3、§5.4；T-14 / M2-10）。

两条落地路径共用同一份校验：**先解析校验**（解析不了就 422，绝不让坏字节进 `configs/`），
**再原子写入**（`storage.save_config_file_detailed` 负责归档、内容相同则不写不归档）。
不覆盖同名文件时 409 `FILE_EXISTS`（§7.4）。

链接导入的 `persist=false` 落到临时区（M2-10 的另一半），`persist=true` 走与上传完全
相同的落盘路径，因此权限也一致（`upload_persist` = 部署者）。

**SSRF 白名单不在本卡**：§14 把 `settings.download_allow` 的放行判断排在 M3-04，本卡只做
`http/https` 前缀校验，并在 PR 里点名这一处遗留。
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any, Dict, NamedTuple, Optional

from ..core import downloader, fsutil, parser, storage
from ..core.errors import (
    DOWNLOAD_FAILED,
    FILE_EXISTS,
    INVALID_INPUT,
    TOO_LARGE,
    AppError,
)
from . import revision, temp_store

PART = "files"
DEFAULT_IMPORT_NAME = "downloaded.xml"


class SaveOutcome(NamedTuple):
    """§7.4 的响应体与状态码：新建 201、覆盖或内容未变 200。"""

    payload: Dict[str, Any]
    status: int


def save_bytes(
    profile_id: str, name: str, data: bytes, overwrite: bool, max_bytes: int
) -> SaveOutcome:
    """`PUT /p/{pid}/files/{name}?overwrite=1`。"""
    clean_name = fsutil.validate_filename(name)
    _check_size(data, max_bytes)
    _must_parse(data, clean_name)

    with storage.use_profile(profile_id):
        exists = os.path.isfile(storage.get_config_path(clean_name))
        if exists and not overwrite:
            raise AppError(FILE_EXISTS, "已存在同名文件 %s" % clean_name, name=clean_name)
        result = storage.save_config_file_detailed(clean_name, data)
        if not result.unchanged:
            revision.bump(profile_id, PART)
    payload = {
        "file": {"name": clean_name},
        "archived": result.archived,
        "unchanged": result.unchanged,
    }
    return SaveOutcome(payload=payload, status=200 if exists else 201)


def import_url(
    profile_id: str,
    cid: Optional[str],
    ip: str,
    url: str,
    name: str,
    persist: bool,
    overwrite: bool,
    settings,
) -> Dict[str, Any]:
    """`POST /p/{pid}/import-url`：下载到内存后按 `persist` 落盘或进临时区。"""
    target = str(url or "").strip()
    if not target.lower().startswith(("http://", "https://")):
        raise AppError(INVALID_INPUT, "仅支持 http/https 链接", field="url")

    data = downloader.download_file(target)
    if data is None:
        raise AppError(DOWNLOAD_FAILED, "下载失败：连接超时或目标不可达，已重试")

    clean_name = fsutil.validate_filename(str(name or "").strip() or _name_from_url(target))
    _check_size(data, settings.upload_max_bytes)

    if persist:
        outcome = save_bytes(profile_id, clean_name, data, overwrite, settings.upload_max_bytes)
        result = {"persisted": True}
        result.update(outcome.payload)
        return result

    meta = temp_store.create(
        profile_id,
        cid,
        ip,
        clean_name,
        data,
        settings.temp_ttl_hours,
        settings.upload_max_bytes,
    )
    return {"persisted": False, "token": meta["token"], "name": meta["name"]}


def create_temp(
    profile_id: str, cid: Optional[str], ip: str, name: str, data: bytes, settings
) -> Dict[str, Any]:
    """`POST /p/{pid}/temp`：只进临时区，必须先能解析（§7.4）。"""
    clean_name = fsutil.validate_filename(name)
    _check_size(data, settings.upload_max_bytes)
    _must_parse(data, clean_name)
    meta = temp_store.create(
        profile_id, cid, ip, clean_name, data, settings.temp_ttl_hours, settings.upload_max_bytes
    )
    return {
        "token": meta["token"],
        "name": meta["name"],
        "format": _format_of(meta["name"]),
        "size": meta["size"],
        "expires_at": meta["expires_at"],
    }


def _name_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path or ""
    tail = os.path.basename(path)
    try:
        return fsutil.validate_filename(tail) if tail else DEFAULT_IMPORT_NAME
    except AppError:
        return DEFAULT_IMPORT_NAME


def _check_size(data: bytes, max_bytes: int) -> None:
    if len(data) > max_bytes:
        raise AppError(
            TOO_LARGE,
            "文件超过单次上传上限 %d MB" % (max_bytes // 1024 // 1024),
            limit_bytes=max_bytes,
        )


def _must_parse(data: bytes, name: str) -> None:
    try:
        parser.to_compact(data, name)
    except AppError as exc:
        exc.detail["raw_available"] = True
        raise


def _format_of(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return {"xml": "xml", "json": "json"}.get(ext, "other")
