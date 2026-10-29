"""兼容旧路由（规范 §7.12；M4-01 / INH-1903）。

只做三件事，且全部在 `/api/v1` 之前匹配：

* `GET /api/file-download`：旧下载链接，等价于新版 raw 下载；`X-MC-Download-Probe: 1`
  时返回旧版 JSON `{"ok": bool, "detail"/"filename"/"kind"}`，不返回文件字节。
* `GET /record_view`：302 到 `/#/p/{profile}/f/{config}?version=record:{record}`。
* `GET /static/*`：404（旧 NiceGUI 静态资源不再提供，由 `http.py` 在路由前拦截）。

下载目标解析保持旧实现的判据：文件名/历史版本标识必须是单个路径段，`.`、`..`、含分隔符
一律 400；资源不存在 404。机型不存在 404，不回落 default。
"""

from __future__ import annotations

import mimetypes
import os
import urllib.parse
from dataclasses import dataclass
from typing import Dict, List, Optional

from ..core import device_models, fsutil, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from ..services import workspace_context
from .router import Response, Router, query_param

DOWNLOAD_KIND_CURRENT = "current"
DOWNLOAD_KIND_ARCHIVE = "archive"
DOWNLOAD_ROUTE = "/api/file-download"


class DownloadResolutionError(Exception):
    """下载目标解析失败。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class DownloadTarget:
    path: str
    download_name: str
    media_type: str
    kind: str
    filename: str
    archive_filename: Optional[str] = None


def _sanitize_segment(value: str, *, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DownloadResolutionError("%s不能为空" % label, status_code=400)
    if text in {".", ".."}:
        raise DownloadResolutionError("%s不合法" % label, status_code=400)
    if "\\" in text or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise DownloadResolutionError("%s不合法" % label, status_code=400)
    if os.path.basename(text) != text:
        raise DownloadResolutionError("%s不合法" % label, status_code=400)
    return text


#: 这几类配置文件的 Content-Type 不查操作系统注册表：Windows 会把 .xml 猜成
#: `text/xml`，同一份下载在不同平台上头部不一致，客户端与测试都没法依赖。
_DETERMINISTIC_MEDIA_TYPES = {
    ".xml": "application/xml",
    ".json": "application/json",
    ".ini": "text/plain",
    ".cfg": "text/plain",
    ".conf": "text/plain",
    ".txt": "text/plain",
}


def _guess_media_type(filename: str) -> str:
    suffix = os.path.splitext(filename)[1].lower()
    if suffix in _DETERMINISTIC_MEDIA_TYPES:
        return _DETERMINISTIC_MEDIA_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def resolve_download_target(
    kind: str,
    filename: str,
    archive_filename: Optional[str] = None,
    profile_id: Optional[str] = None,
) -> DownloadTarget:
    safe_kind = str(kind or "").strip().lower()
    safe_filename = _sanitize_segment(filename, label="文件名")

    with storage.use_profile(profile_id):
        if safe_kind == DOWNLOAD_KIND_CURRENT:
            try:
                path = fsutil.resolve_within(
                    storage.get_configs_dir(), safe_filename
                )
            except AppError as exc:
                raise DownloadResolutionError("文件名不合法", status_code=400) from exc
            if not os.path.isfile(path):
                raise DownloadResolutionError("当前文件不存在，无法下载", status_code=404)
            return DownloadTarget(
                path=path,
                download_name=safe_filename,
                media_type=_guess_media_type(safe_filename),
                kind=safe_kind,
                filename=safe_filename,
            )
        if safe_kind == DOWNLOAD_KIND_ARCHIVE:
            safe_archive = _sanitize_segment(
                archive_filename or "", label="历史版本标识"
            )
            # 不用 `get_archived_path`：它经 `get_archive_dir` 会 `makedirs`，而这是读接口
            # （M4-01 评审 Important：pristine 数据根上 probe 一个不存在的归档会长出
            # `archive/<name>/`）。符号链接防御交给 `resolve_within`。
            archive_dir = os.path.join(
                storage.get_archive_root_dir(), safe_filename
            )
            try:
                path = fsutil.resolve_within(archive_dir, safe_archive)
            except AppError as exc:
                raise DownloadResolutionError("历史版本标识不合法", status_code=400) from exc
            if not os.path.isfile(path):
                raise DownloadResolutionError("历史版本资源不存在，无法下载", status_code=404)
            return DownloadTarget(
                path=path,
                download_name=safe_archive,
                media_type=_guess_media_type(safe_archive),
                kind=safe_kind,
                filename=safe_filename,
                archive_filename=safe_archive,
            )
    raise DownloadResolutionError("不支持的下载类型", status_code=400)


def build_download_url(
    kind: str,
    filename: str,
    archive_filename: Optional[str] = None,
    profile_id: Optional[str] = None,
) -> str:
    params = {
        "kind": str(kind or "").strip().lower(),
        "filename": str(filename or "").strip(),
        "profile": profile_id or storage.get_active_profile(),
    }
    if archive_filename:
        params["archive_filename"] = str(archive_filename).strip()
    return "%s?%s" % (DOWNLOAD_ROUTE, urllib.parse.urlencode(params))


def register(router: Router) -> None:
    """在 `/api/v1` 之前注册旧路由（`build_router` 里第一个调用）。"""
    router.add("GET", DOWNLOAD_ROUTE, file_download)
    router.add("GET", "/record_view", record_view)


def file_download(
    ctx: object, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    probe = ((getattr(ctx, "headers", None) or {}).get("x-mc-download-probe") or "").strip() == "1"
    profile = query_param(query, "profile", storage.DEFAULT_PROFILE)
    workspace = getattr(ctx, "workspace_id", "") or "default"
    try:
        try:
            workspace_context.assert_profile(workspace, profile)
        except AppError:
            # 保持旧 probe 的 JSON 形状（{"ok": false}），不把新 404 包络漏给旧调用方。
            raise DownloadResolutionError("机型不存在", status_code=404)
        if not device_models.exists(profile):
            raise DownloadResolutionError("机型不存在", status_code=404)
        target = resolve_download_target(
            query_param(query, "kind"),
            query_param(query, "filename"),
            query_param(query, "archive_filename") or None,
            profile_id=profile,
        )
    except DownloadResolutionError as exc:
        if probe:
            return Response(
                status=exc.status_code,
                payload={"ok": False, "detail": exc.message},
            )
        code = INVALID_INPUT if exc.status_code == 400 else NOT_FOUND
        raise AppError(code, exc.message)

    if probe:
        return Response(
            payload={"ok": True, "filename": target.download_name, "kind": target.kind}
        )
    with open(target.path, "rb") as handle:
        data = handle.read()
    from .api_documents import content_disposition

    return Response(
        body=data,
        content_type=target.media_type,
        headers={
            "Content-Disposition": content_disposition(target.download_name),
            "Cache-Control": "no-cache",
        },
    )


def record_view(
    ctx: object, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    config = query_param(query, "config")
    record = query_param(query, "record")
    if not config or not record:
        raise AppError(INVALID_INPUT, "参数缺失：config 与 record 都必须提供")
    profile = query_param(query, "profile", storage.DEFAULT_PROFILE)
    location = "/#/p/%s/f/%s?version=record:%s" % (
        urllib.parse.quote(profile, safe=""),
        urllib.parse.quote(config, safe=""),
        urllib.parse.quote(record, safe=""),
    )
    return Response(
        status=302,
        body=b"",
        content_type="text/plain; charset=utf-8",
        headers={"Location": location},
    )
