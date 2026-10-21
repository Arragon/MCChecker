"""更新源回读体：单个 XML/JSON，或压缩包里的多个 XML/JSON。

压缩包只认标准库能打开的 zip、gzip、tar / tar.gz。成员不落盘，只取出
xml/json 的文件名和字节。正则关键词用来切削文件名：匹配到的那段文字加上
原扩展名就是要写入文件列表的名字。例如关键词 ``MachineConstant``，包内
``Default_MachineConstant_basics.xml`` 保存为 ``MachineConstant.xml``。
"""

from __future__ import annotations

import gzip
import io
import os
import re
import tarfile
import zipfile
from typing import List, Optional, Tuple

from .errors import INVALID_INPUT, TOO_LARGE, AppError
from .fsutil import validate_filename

MAX_MEMBERS = 64
_CONFIG_EXTS = (".xml", ".json")
_GZIP_MAGIC = b"\x1f\x8b"
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

Payload = Tuple[str, bytes]


class BundleError(AppError):
    """回读体打不开或没有可用的配置文件。code 用 INVALID_INPUT，调用方记为拒绝。"""

    def __init__(self, message: str) -> None:
        super().__init__(INVALID_INPUT, message)


def expand_update_payload(
    content: bytes,
    *,
    keyword: str,
    source_name: str,
    remote_name: str,
    max_bytes: int,
) -> List[Payload]:
    """把一次下载拆成要写入的 ``(文件名, 字节)``。

    没有关键词时：单个 XML/JSON 仍用更新源自己的文件名；压缩包则按包内
    原文件名逐个加入。有关键词时：只保留文件名能匹配的那些，并切削成
    「匹配文本 + 扩展名」。
    """
    pattern = _compile_keyword(keyword)
    content, members = _prepare(content, max_bytes)
    if members is None:
        hint = remote_name or source_name
        if hint.lower().endswith(".gz"):
            hint = hint[:-3]
        if pattern is None:
            return [(source_name, content)]
        renamed = _trim_name(hint, pattern) or _trim_name(source_name, pattern)
        if renamed is None:
            raise BundleError("文件名未匹配正则关键词")
        return [(renamed, content)]

    chosen: List[Payload] = []
    seen = {}
    for base, body in members:
        if pattern is None:
            renamed = base
        else:
            renamed = _trim_name(base, pattern)
            if renamed is None:
                continue
        try:
            renamed = validate_filename(renamed)
        except AppError as exc:
            raise BundleError("切削后的文件名不合法：%s" % exc.message)
        if renamed in seen:
            raise BundleError("多个文件都切削为 %s" % renamed)
        seen[renamed] = True
        chosen.append((renamed, body))
    if pattern is not None and not chosen:
        raise BundleError("压缩包里没有匹配正则关键词的 XML/JSON")
    if not chosen:
        raise BundleError("压缩包里没有 XML/JSON")
    return chosen


def _compile_keyword(keyword: str) -> Optional[re.Pattern]:
    text = str(keyword or "").strip()
    if not text:
        return None
    try:
        return re.compile(text)
    except re.error as exc:
        raise BundleError("正则关键词无法编译：%s" % exc)


def _trim_name(filename: str, pattern: re.Pattern) -> Optional[str]:
    """文件名（不含目录）里搜关键词，命中则返回切削后的名字。"""
    base = _basename(filename)
    stem, ext = os.path.splitext(base)
    if ext.lower() not in _CONFIG_EXTS or not stem:
        return None
    match = pattern.search(stem)
    if match is None:
        return None
    piece = match.group(1) if match.lastindex else match.group(0)
    piece = str(piece or "").strip()
    if piece.lower().endswith(ext.lower()):
        piece = piece[: -len(ext)]
    piece = piece.strip(" ._")
    if not piece:
        return None
    return piece + ext.lower()


def _prepare(content: bytes, max_bytes: int) -> Tuple[bytes, Optional[List[Payload]]]:
    """``members is None`` 表示这是单个文件（可能已从 gzip 解开）。"""
    if content.startswith(_ZIP_MAGICS):
        return content, _read_zip(content, max_bytes)
    if content.startswith(_GZIP_MAGIC):
        raw = _gunzip(content, max_bytes)
        if _is_tar(raw):
            return raw, _read_tar(raw, max_bytes)
        return raw, None
    if _is_tar(content):
        return content, _read_tar(content, max_bytes)
    return content, None


def _read_zip(content: bytes, max_bytes: int) -> List[Payload]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise BundleError("压缩包无法打开：%s" % exc)
    out: List[Payload] = []
    total = 0
    count = 0
    for info in archive.infolist():
        if info.is_dir() or info.flag_bits & 0x1:
            if info.flag_bits & 0x1:
                raise BundleError("不支持加密压缩包")
            continue
        base = _basename(info.filename)
        if not _is_config_name(base):
            continue
        count += 1
        if count > MAX_MEMBERS:
            raise AppError(TOO_LARGE, "压缩包内配置文件超过 %d 个" % MAX_MEMBERS)
        total += int(info.file_size)
        if total > max_bytes:
            raise AppError(TOO_LARGE, "解压后的配置超过下载上限")
        out.append((base, archive.read(info)))
    return out


def _read_tar(content: bytes, max_bytes: int) -> List[Payload]:
    try:
        archive = tarfile.open(fileobj=io.BytesIO(content), mode="r:*")
    except tarfile.TarError as exc:
        raise BundleError("压缩包无法打开：%s" % exc)
    out: List[Payload] = []
    total = 0
    count = 0
    for member in archive.getmembers():
        if not member.isfile() or member.issym() or member.islnk():
            continue
        base = _basename(member.name)
        if not _is_config_name(base):
            continue
        count += 1
        if count > MAX_MEMBERS:
            raise AppError(TOO_LARGE, "压缩包内配置文件超过 %d 个" % MAX_MEMBERS)
        size = int(member.size)
        total += size
        if total > max_bytes:
            raise AppError(TOO_LARGE, "解压后的配置超过下载上限")
        extracted = archive.extractfile(member)
        if extracted is None:
            continue
        body = extracted.read(size + 1)
        if len(body) > size:
            raise AppError(TOO_LARGE, "解压后的配置超过下载上限")
        out.append((base, body))
    return out


def _gunzip(content: bytes, max_bytes: int) -> bytes:
    chunks = []
    total = 0
    try:
        handle = gzip.GzipFile(fileobj=io.BytesIO(content))
    except OSError as exc:
        raise BundleError("压缩包无法打开：%s" % exc)
    try:
        while True:
            block = handle.read(65536)
            if not block:
                break
            total += len(block)
            if total > max_bytes:
                raise AppError(TOO_LARGE, "解压后的配置超过下载上限")
            chunks.append(block)
    except OSError as exc:
        raise BundleError("压缩包无法打开：%s" % exc)
    finally:
        handle.close()
    return b"".join(chunks)


def _is_tar(content: bytes) -> bool:
    return len(content) > 262 and content[257:262] == b"ustar"


def _basename(name: str) -> str:
    text = str(name or "").replace("\\", "/").rstrip("/")
    base = text.rsplit("/", 1)[-1]
    if base in (".", "..") or base.startswith("._") or not base:
        return ""
    return base


def _is_config_name(name: str) -> bool:
    return bool(name) and os.path.splitext(name)[1].lower() in _CONFIG_EXTS
