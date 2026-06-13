"""统一文件下载辅助。

为当前配置文件与历史版本文件提供：
- 服务端下载目标解析与受控路由
- 浏览器原生下载触发
- 失败提示与重试入口
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from nicegui import app as nice_app, ui

from app.core import storage

DOWNLOAD_KIND_CURRENT = "current"
DOWNLOAD_KIND_ARCHIVE = "archive"
DOWNLOAD_ROUTE = "/api/file-download"

_ROUTES_REGISTERED = False


class DownloadResolutionError(Exception):
    """下载目标解析失败。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class DownloadTarget:
    """已解析的下载目标。"""

    path: str
    download_name: str
    media_type: str
    kind: str
    filename: str
    archive_filename: Optional[str] = None


def _sanitize_segment(value: str, *, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DownloadResolutionError(f"{label}不能为空", status_code=400)
    if text in {".", ".."}:
        raise DownloadResolutionError(f"{label}不合法", status_code=400)
    if os.path.basename(text) != text:
        raise DownloadResolutionError(f"{label}不合法", status_code=400)
    return text


def _guess_media_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def resolve_download_target(
    kind: str,
    filename: str,
    archive_filename: Optional[str] = None,
    profile_id: Optional[str] = None,
) -> DownloadTarget:
    """解析下载目标并校验资源可用性。"""

    safe_kind = str(kind or "").strip().lower()
    safe_filename = _sanitize_segment(filename, label="文件名")

    with storage.use_profile(profile_id):
        if safe_kind == DOWNLOAD_KIND_CURRENT:
            path = storage.get_config_path(safe_filename)
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
            safe_archive_filename = _sanitize_segment(archive_filename or "", label="历史版本标识")
            path = storage.get_archived_path(safe_filename, safe_archive_filename)
            if not os.path.isfile(path):
                raise DownloadResolutionError("历史版本资源不存在，无法下载", status_code=404)
            return DownloadTarget(
                path=path,
                download_name=safe_archive_filename,
                media_type=_guess_media_type(safe_archive_filename),
                kind=safe_kind,
                filename=safe_filename,
                archive_filename=safe_archive_filename,
            )

    raise DownloadResolutionError("不支持的下载类型", status_code=400)


def build_download_url(
    kind: str,
    filename: str,
    archive_filename: Optional[str] = None,
    profile_id: Optional[str] = None,
) -> str:
    """构建受控下载链接。"""

    params = {
        "kind": str(kind or "").strip().lower(),
        "filename": str(filename or "").strip(),
        "profile": profile_id or storage.get_active_profile(),
    }
    if archive_filename:
        params["archive_filename"] = str(archive_filename).strip()
    return f"{DOWNLOAD_ROUTE}?{urllib.parse.urlencode(params)}"


def register_download_routes() -> None:
    """注册下载路由。"""

    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return

    @nice_app.get(DOWNLOAD_ROUTE)
    def file_download(
        request: Request,
        kind: str,
        filename: str,
        archive_filename: Optional[str] = None,
        profile: Optional[str] = None,
    ):
        is_probe = request.headers.get("x-mc-download-probe") == "1"
        try:
            target = resolve_download_target(
                kind=kind,
                filename=filename,
                archive_filename=archive_filename,
                profile_id=profile,
            )
        except DownloadResolutionError as exc:
            if is_probe:
                return JSONResponse(
                    {
                        "ok": False,
                        "detail": exc.message,
                    },
                    status_code=exc.status_code,
                )
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

        if is_probe:
            return JSONResponse(
                {
                    "ok": True,
                    "filename": target.download_name,
                    "kind": target.kind,
                }
            )

        return FileResponse(
            path=target.path,
            media_type=target.media_type,
            filename=target.download_name,
        )

    _ROUTES_REGISTERED = True


def make_download_handler(
    kind: str,
    filename: str,
    archive_filename: Optional[str] = None,
):
    """为 NiceGUI 按钮生成异步下载处理器。"""

    async def _handler(_event=None):
        await trigger_browser_download(
            kind=kind,
            filename=filename,
            archive_filename=archive_filename,
        )

    return _handler


async def trigger_browser_download(
    kind: str,
    filename: str,
    archive_filename: Optional[str] = None,
) -> bool:
    """校验后触发浏览器原生下载。"""

    try:
        target = resolve_download_target(
            kind=kind,
            filename=filename,
            archive_filename=archive_filename,
        )
    except DownloadResolutionError as exc:
        _show_retry_dialog(
            exc.message,
            make_download_handler(kind, filename, archive_filename),
        )
        return False

    download_url = build_download_url(
        kind=kind,
        filename=filename,
        archive_filename=archive_filename,
    )

    js_result = await ui.run_javascript(
        f"""
        (async () => {{
          try {{
            const downloadUrl = {json.dumps(download_url)};
            const downloadName = {json.dumps(target.download_name)};
            const probe = await fetch(downloadUrl, {{
              method: 'GET',
              credentials: 'same-origin',
              headers: {{ 'X-MC-Download-Probe': '1' }},
            }});
            if (!probe.ok) {{
              let message = '下载失败，请稍后重试';
              try {{
                const data = await probe.json();
                if (data && typeof data.detail === 'string' && data.detail.trim()) {{
                  message = data.detail.trim();
                }}
              }} catch (_probeError) {{
              }}
              return JSON.stringify({{ ok: false, message }});
            }}

            const link = document.createElement('a');
            link.href = downloadUrl;
            link.download = downloadName;
            link.rel = 'noopener';
            document.body.appendChild(link);
            link.click();
            link.remove();
            return JSON.stringify({{ ok: true }});
          }} catch (error) {{
            return JSON.stringify({{
              ok: false,
              message: error?.message || '网络异常，无法下载文件',
            }});
          }}
        }})()
        """,
        timeout=30.0,
    )

    try:
        payload = json.loads(js_result or "{}")
    except json.JSONDecodeError:
        payload = {"ok": False, "message": "浏览器返回异常，无法确认下载结果"}

    if payload.get("ok"):
        ui.notify(f"开始下载: {target.download_name}", type="positive")
        return True

    message = str(payload.get("message") or "下载失败，请稍后重试")
    _show_retry_dialog(
        message,
        make_download_handler(kind, filename, archive_filename),
    )
    return False


def _show_retry_dialog(message: str, retry_handler) -> None:
    """弹出下载失败提示，并提供重试入口。"""

    dialog = ui.dialog().props("persistent")
    with dialog, ui.card().classes("w-[28rem] max-w-full q-pa-md"):
        ui.label("下载失败").classes("mc-section-title")
        ui.label(message).classes("mc-page-subtitle")
        with ui.row().classes("justify-end items-center w-full q-gutter-sm q-mt-md"):
            ui.button("关闭", on_click=dialog.close).props("flat")

            async def _retry(_event=None):
                dialog.close()
                await retry_handler()

            ui.button("重试下载", icon="refresh", on_click=_retry).props("unelevated color=primary")

    dialog.open()
