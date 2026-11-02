"""M16-16 SDA 三端点 adapter（合成契约，真实字段待 E2）。

只允许连接配置中的固定 origin + 固定路径；不接受任意 URL、JS 或本地路径。
`session` 是受控浏览器请求的薄封装，返回 `SdaResponse`；本模块不持久化
Cookie/CSRF，也不把原始响应正文写入日志。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

from . import contracts

_RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


@dataclass(frozen=True)
class SdaResponse:
    status: int
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    final_url: str = ""


@dataclass(frozen=True)
class SdaResult:
    status: str
    reason_code: Optional[str]
    data: Optional[Dict[str, Any]] = None
    session_valid: Optional[bool] = None
    resource_ref: Optional[str] = None
    message: str = ""


def _same_origin(url: str, origin: str) -> bool:
    left = urlsplit(str(url or ""))
    right = urlsplit(str(origin or ""))
    return (
        left.scheme == right.scheme
        and left.hostname == right.hostname
        and (left.port or 443) == (right.port or 443)
    )


def _validate_origin(origin: str) -> str:
    text = str(origin or "").strip()
    parts = urlsplit(text)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("SDA origin must be https")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("SDA origin must not contain userinfo/query/fragment")
    if parts.path not in ("", "/"):
        raise ValueError("SDA origin must be an origin")
    if parts.port is not None and parts.port != 443:
        raise ValueError("SDA origin port must be 443")
    return text.rstrip("/")


def _validate_resource_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not _RESOURCE_RE.match(text):
        raise ValueError("invalid resource_ref")
    return text


class SdaAdapter:
    def __init__(self, origin: str, session) -> None:
        self.origin = _validate_origin(origin)
        self.session = session

    def _request(self, kind: str, resource_ref: Optional[str] = None) -> SdaResult:
        if kind not in contracts.ADAPTER_KINDS:
            return SdaResult("failed", contracts.REASON_PROTOCOL_MISMATCH, message="unknown kind")
        path = contracts.PATHS[kind]
        url = self.origin + path
        body: Optional[Dict[str, Any]] = None
        if contracts.RESOURCE_FIELD[kind]:
            try:
                ref = _validate_resource_ref(resource_ref)
            except ValueError:
                return SdaResult("failed", contracts.REASON_INVALID_PAYLOAD, message="invalid resource_ref")
            body = {contracts.RESOURCE_FIELD[kind]: ref}
        response = self.session.request(
            "GET" if kind == "catalog" else "POST",
            url,
            headers={"accept": "application/json"},
            body=body,
            allow_redirects=False,
        )
        if not _same_origin(response.final_url or url, self.origin):
            return SdaResult("failed", contracts.REASON_REDIRECT_BLOCKED, resource_ref=resource_ref)

        if response.status == 500 and not response.body:
            return SdaResult("failed", contracts.REASON_AUTH_FAILED, resource_ref=resource_ref)
        if response.status == 403:
            return SdaResult("failed", contracts.REASON_PERMISSION_DENIED, resource_ref=resource_ref)
        if response.status != 200:
            return SdaResult(
                "failed",
                contracts.REASON_BUSINESS_ERROR,
                resource_ref=resource_ref,
                message="http_status=%d" % response.status,
            )

        content_type = str(response.headers.get("content-type") or "").lower()
        raw = response.body or b""
        if "text/html" in content_type or raw.lstrip().startswith(b"<"):
            return SdaResult(
                "failed",
                contracts.REASON_INVALID_PAYLOAD,
                resource_ref=resource_ref,
                message="login/html response rejected",
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return SdaResult("failed", contracts.REASON_INVALID_PAYLOAD, resource_ref=resource_ref)
        if not isinstance(payload, dict):
            return SdaResult("failed", contracts.REASON_INVALID_PAYLOAD, resource_ref=resource_ref)
        if payload.get("ok") is not True:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            return SdaResult(
                "failed",
                contracts.REASON_BUSINESS_ERROR,
                resource_ref=resource_ref,
                message=str(error.get("code") or "business_error"),
            )
        if payload.get("resource_kind") != kind:
            return SdaResult(
                "failed",
                contracts.REASON_INVALID_PAYLOAD,
                resource_ref=resource_ref,
                message="resource_kind mismatch",
            )

        if kind == "catalog":
            items = payload.get("items")
            if not isinstance(items, list):
                return SdaResult("failed", contracts.REASON_INVALID_PAYLOAD, resource_ref=resource_ref)
            return SdaResult(
                "succeeded",
                None,
                data={"items": items},
                session_valid=False,
                resource_ref=resource_ref,
            )
        if kind == "table":
            table = payload.get("table")
            if not isinstance(table, dict) or not table.get("table_id"):
                return SdaResult("failed", contracts.REASON_INVALID_PAYLOAD, resource_ref=resource_ref)
            return SdaResult(
                "succeeded",
                None,
                data=table,
                session_valid=True,
                resource_ref=resource_ref,
            )
        file_body = payload.get("file")
        if (
            not isinstance(file_body, dict)
            or not file_body.get("resource_ref")
            or not isinstance(file_body.get("size"), int)
            or not isinstance(file_body.get("content_sha256"), str)
        ):
            return SdaResult("failed", contracts.REASON_INVALID_PAYLOAD, resource_ref=resource_ref)
        return SdaResult(
            "succeeded",
            None,
            data=file_body,
            session_valid=True,
            resource_ref=resource_ref,
        )

    def read_catalog(self) -> SdaResult:
        return self._request("catalog")

    def read_table(self, resource_ref: str) -> SdaResult:
        return self._request("table", resource_ref)

    def read_file(self, resource_ref: str) -> SdaResult:
        return self._request("file", resource_ref)

    def probe(self, capability: str, resource_ref: str = "res-probe") -> SdaResult:
        if capability == "table_read":
            return self.read_table(resource_ref)
        if capability == "file_read":
            return self.read_file(resource_ref)
        if capability == "catalog_read":
            return self.read_catalog()
        return SdaResult("failed", contracts.REASON_PROTOCOL_MISMATCH, message="unknown capability")
