"""文件与文档接口（§7.4；M1-02）。

handler 只做三件事：取路径/查询参数 → 调 `services.documents` → 组装 `Response`。
`pid` 的存在性校验与 `storage.use_profile(pid)` 由 HTTP 层统一完成（§3.4 第 5、6 步），
因此这里不再检查机型。
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional

from ..services import documents
from .router import Response


def list_files(params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]) -> Response:
    # GET /api/v1/p/{pid}/files
    return Response(payload={"items": documents.list_files(params["pid"])})


def get_document(params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]) -> Response:
    # GET /api/v1/p/{pid}/files/{name}/document?version=
    version = _param(query, "version", "current")
    payload, etag = documents.document_bytes(params["pid"], params["name"], version)
    return Response(body=payload, headers={"ETag": etag, "Cache-Control": "no-cache"})


def get_raw(params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]) -> Response:
    # GET /api/v1/p/{pid}/files/{name}/raw?version=
    version = _param(query, "version", "current")
    raw = documents.load_raw(params["pid"], params["name"], version)
    return Response(
        body=raw.data,
        content_type=raw.content_type,
        headers={
            "Content-Disposition": content_disposition(raw.filename),
            "ETag": documents.etag_for_sha(raw.sha256),
            "Cache-Control": "no-cache",
        },
    )


def content_disposition(filename: str) -> str:
    """RFC 5987 的附件头：ASCII 回退 + `filename*=UTF-8''…`（中文文件名可用）。"""
    fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_" for ch in filename
    ) or "download"
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (
        fallback,
        urllib.parse.quote(filename, safe=""),
    )


def _param(query: Dict[str, List[str]], key: str, default: str) -> str:
    """查询参数：同名多次时取最后一个（后写覆盖），缺省用 `default`。"""
    values: Any = query.get(key)
    if not values:
        return default
    value = str(values[-1]).strip()
    return value or default
