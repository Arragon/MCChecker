"""上传保存与链接导入（§7.4、§5.3、§5.4；T-14 / M2-10）。

两条落地路径共用同一份校验：**先解析校验**（解析不了就 422，绝不让坏字节进 `configs/`），
**再原子写入**（`storage.save_config_file_detailed` 负责归档、内容相同则不写不归档）。
不覆盖同名文件时 409 `FILE_EXISTS`（§7.4）。

链接导入的 `persist=false` 落到临时区（M2-10 的另一半），`persist=true` 走与上传完全
相同的落盘路径，因此权限也一致（`upload_persist` = 部署者）。

**地址策略按授权分支挑选（N2 / INH-1913）**：`persist=false` 的字节进的是访客可见的临时区，
所以这一条必须额外 `block_loopback=True`；`persist=true` 是部署者写权威配置的通道，内网更新
源照旧可用，沿用 `default_policy` 的 `False`（卡 4 的裁定：默认值不动，opt-in 放在调用方的
授权边界上）。见 `_download`。`settings.download_allow` 的白名单判断在 `core.downloader`
（M3-04），本层只剩入口那句给出 `field="url"` 的 http/https 前缀校验，为的是把错误显示在
对应输入框下方（§7.1）。
"""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
from dataclasses import replace
from typing import Any, Dict, NamedTuple, Optional

from ..core import downloader, fsutil, log_context, parser, storage
from ..core.errors import (
    DOWNLOAD_FAILED,
    FILE_EXISTS,
    INVALID_INPUT,
    TOO_LARGE,
    AppError,
)
from . import revision, temp_store

logger = logging.getLogger(__name__)

PART = "files"
DEFAULT_IMPORT_NAME = "downloaded.xml"


class SaveOutcome(NamedTuple):
    """§7.4 的响应体与状态码：新建 201、覆盖或内容未变 200。"""

    payload: Dict[str, Any]
    status: int


def save_bytes(
    profile_id: str, name: str, data: bytes, overwrite: bool, max_bytes: int,
    actor: str = "",
) -> SaveOutcome:
    """`PUT /p/{pid}/files/{name}?overwrite=1`。"""
    started = time.perf_counter()
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
    log_context.business(
        "file_save",
        profile=profile_id,
        actor=actor,
        target=clean_name,
        result="unchanged" if result.unchanged else ("created" if not exists else "updated"),
        duration_ms=(time.perf_counter() - started) * 1000,
    )
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
    actor: str = "",
) -> Dict[str, Any]:
    """`POST /p/{pid}/import-url`：下载到内存后按 `persist` 落盘或进临时区。"""
    target = str(url or "").strip()
    if not target.lower().startswith(("http://", "https://")):
        raise AppError(INVALID_INPUT, "仅支持 http/https 链接", field="url")

    data = _download(target, persist, settings)
    clean_name = fsutil.validate_filename(str(name or "").strip() or _name_from_url(target))
    _check_size(data, settings.upload_max_bytes)

    if persist:
        outcome = save_bytes(
            profile_id, clean_name, data, overwrite, settings.upload_max_bytes, actor=actor
        )
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


def _download(target: str, persist: bool, settings) -> bytes:
    """链接导入唯一的下载入口：**用哪个地址策略由 `persist` 在这里一次性决定**。

    `persist=false` 的字节进访客可见的临时区，所以额外打开 `block_loopback`，关掉「访客把
    导入指到服务器本机回环上的服务、再从临时文件查看器把正文读走」这条 SSRF（N2 / INH-1913）。
    `persist=true` 走部署者的权威落盘，内网更新源照旧可用，沿用 `default_policy` 的 `False`。

    协议/地址段/白名单的判断一律留在 `core.downloader`，超时与重试一律来自 `Settings`
    （经 `default_policy`），本函数不重复实现其中任何一件。
    """
    policy = downloader.default_policy(settings)
    if not persist:
        policy = replace(policy, block_loopback=True)
    try:
        return downloader.fetch(target, policy)
    except AppError as exc:
        if exc.code != DOWNLOAD_FAILED:
            # `TOO_LARGE` 及其余一律原样上抛：超限在服务边界是 413，不许塌成 502。
            raise
        # 下载类失败一律收成同一句话（`download_file` 的旧口径），逐字节相同，访客据此分不出
        # 「端口开着还是关着」——那正是 SSRF 探测要读的差别。具体原因只进日志，且下载器已经把
        # URL 脱敏成 `scheme://host/path`（§11.5）。
        logger.error("链接导入下载失败: %s", exc.message)
        raise AppError(DOWNLOAD_FAILED, "下载失败：连接超时或目标不可达，已重试") from exc


def _name_from_url(url: str) -> str:
    """从链接推断文件名，优先保留 `.xml` / `.json` 后缀。

    路径末段、以及 `filename` / `file` / `name` 查询参数都会看。百分号编码先解开。
    有配置后缀的候选优先于没有后缀的（`/download?file=cfg.json` 得到 `cfg.json`，
    而不是 `download`）。都没有合法名字时才用 `downloaded.xml`。
    """
    parts = urllib.parse.urlsplit(str(url or ""))
    candidates = []
    tail = os.path.basename(urllib.parse.unquote(parts.path or "")).strip()
    if tail:
        candidates.append(tail)
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=False)
    for key in ("filename", "file", "name"):
        for raw in query.get(key) or []:
            name = os.path.basename(urllib.parse.unquote(str(raw)).replace("\\", "/")).strip()
            if name:
                candidates.append(name)

    valid = []
    for candidate in candidates:
        try:
            valid.append(fsutil.validate_filename(candidate))
        except AppError:
            continue
    for name in valid:
        if os.path.splitext(name)[1].lower() in (".xml", ".json", ".ini", ".cfg", ".conf", ".txt"):
            return name
    for name in valid:
        if "." in name:
            return name
    if valid:
        return valid[0]
    return DEFAULT_IMPORT_NAME


def _check_size(data: bytes, max_bytes: int) -> None:
    if len(data) > max_bytes:
        raise AppError(
            TOO_LARGE,
            "文件超过单次上传上限 %d MB" % (max_bytes // 1024 // 1024),
            limit_bytes=max_bytes,
        )


def _must_parse(data: bytes, name: str) -> None:
    if _format_of(name) == "text":
        return  # 文本格式只做原文管理，不冒充 XML/JSON 结构树
    try:
        parser.to_compact(data, name)
    except AppError as exc:
        exc.detail["raw_available"] = True
        raise


def _format_of(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in ("xml", "json"):
        return ext
    if ext in ("ini", "cfg", "conf", "txt"):
        return "text"
    return "other"
