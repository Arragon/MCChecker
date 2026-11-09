"""M16-17 普通账密登录与受保护能力核验（E2 现场字段待补）。

本模块只做**会话状态机**，不 import Playwright：

1. 每次登录向调用方给的 `browser` 要一个全新的非持久化 context（连接隔离）；
2. 正常业务入口 → 账密表单 → 提交 → 只接受配置允许的成功业务页；
3. 登录后必须逐个 probe 当前启用来源要求的**受保护能力**；catalog 匿名成功
   不算会话有效；
4. 密码错误、账号锁定、表单结构变化、非法跳转、网络故障分别返回可区分
   reason_code；失败一律关闭 context。

真实选择器、SSO 域和成功路径必须由 E2 授权会话提供并写进部署配置；本模块
不猜上游字段，也不把密码/Cookie/CSRF 放进结果、日志或异常文本。真实 Playwright
适配在 `playwright_session.py`，其证据保持 BLOCKED-EXTERNAL。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlsplit

from . import contracts
from .sda_adapter import SdaAdapter, _validate_origin

LOGIN_SUCCEEDED = "succeeded"
LOGIN_FAILED = "failed"

REASON_BAD_CREDENTIALS = "bad_credentials"
REASON_LOCKED = "locked"
REASON_FLOW_CHANGED = "login_flow_changed"
REASON_REDIRECT_BLOCKED = "redirect_blocked"
REASON_NETWORK = "network_error"
REASON_AUTH_FAILED = "auth_failed"
REASON_PROTOCOL = "protocol_mismatch"

_REASON_CODES = frozenset(
    {
        REASON_BAD_CREDENTIALS,
        REASON_LOCKED,
        REASON_FLOW_CHANGED,
        REASON_REDIRECT_BLOCKED,
        REASON_NETWORK,
        REASON_AUTH_FAILED,
        REASON_PROTOCOL,
        "permission_denied",
        "business_error",
        "invalid_payload",
        "too_large",
        "source_changed",
        "collector_offline",
    }
)


def _text(value: Any, name: str, limit: int = 300) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or "\x00" in text or "\n" in text:
        raise ValueError("%s invalid" % name)
    return text


def _path(value: Any, name: str) -> str:
    text = _text(value, name, 500)
    if not text.startswith("/") or text.startswith("//"):
        raise ValueError("%s must start with /" % name)
    parts = urlsplit(text)
    if parts.scheme or parts.netloc or parts.fragment:
        raise ValueError("%s must be a path" % name)
    return text


def _origin(value: Any, name: str) -> str:
    try:
        return _validate_origin(str(value or ""))
    except ValueError as exc:
        raise ValueError("%s invalid: %s" % (name, exc))


@dataclass(frozen=True)
class Credentials:
    """账密只在运行时出现；repr/异常里都不带值。"""

    username: str = field(repr=False)
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        for name, value, limit in (
            ("username", self.username, 256),
            ("password", self.password, 1024),
        ):
            if not isinstance(value, str) or not value or len(value) > limit:
                raise ValueError("%s invalid" % name)
            if "\x00" in value:
                raise ValueError("%s invalid" % name)


@dataclass(frozen=True)
class LoginConfig:
    """部署侧版本化登录配置；所有 URL/选择器都必须显式给出。"""

    origin: str
    login_path: str
    success_paths: Tuple[str, ...]
    username_selector: str
    password_selector: str
    submit_selector: str
    identity_origins: Tuple[str, ...] = ()
    error_selectors: Tuple[str, ...] = ()
    lock_selectors: Tuple[str, ...] = ()
    required_capabilities: Tuple[str, ...] = ("table_read",)
    timeout_ms: int = 15000

    def __post_init__(self) -> None:
        origin = _origin(self.origin, "origin")
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "login_path", _path(self.login_path, "login_path"))
        successes = tuple(_path(item, "success_path") for item in self.success_paths)
        if not successes:
            raise ValueError("success_paths must not be empty")
        object.__setattr__(self, "success_paths", successes)
        identities = tuple(_origin(item, "identity_origin") for item in self.identity_origins)
        object.__setattr__(self, "identity_origins", identities)
        for name, value in (
            ("username_selector", self.username_selector),
            ("password_selector", self.password_selector),
            ("submit_selector", self.submit_selector),
        ):
            object.__setattr__(self, name, _text(value, name))
        for name in ("error_selectors", "lock_selectors"):
            object.__setattr__(
                self,
                name,
                tuple(_text(item, name) for item in getattr(self, name)),
            )
        capabilities = tuple(self.required_capabilities)
        unknown = [item for item in capabilities if item not in contracts.CAPABILITIES]
        if unknown:
            raise ValueError("unknown required capability: %s" % unknown[0])
        if not capabilities:
            raise ValueError("required_capabilities must not be empty")
        protected = [item for item in capabilities if item in ("table_read", "file_read")]
        if not protected:
            # catalog 匿名成功不能证明已登录（M16-02 §2/§3）。
            raise ValueError("at least one protected capability is required")
        object.__setattr__(self, "required_capabilities", capabilities)
        if isinstance(self.timeout_ms, bool) or not isinstance(self.timeout_ms, int):
            raise ValueError("timeout_ms invalid")
        if self.timeout_ms < 1000 or self.timeout_ms > 120000:
            raise ValueError("timeout_ms must be 1000-120000")

    @property
    def login_url(self) -> str:
        return self.origin + self.login_path

    @property
    def success_prefixes(self) -> Tuple[str, ...]:
        return tuple(self.origin + path for path in self.success_paths)


@dataclass
class AuthenticatedSession:
    """登录成功后的受控会话；Cookie 只存在于 context，不复制到结果里。"""

    context: Any = field(repr=False)
    origin: str
    timeout_ms: int = 15000
    capabilities: Dict[str, str] = field(default_factory=dict)
    closed: bool = False

    def request_session(self) -> "_ContextRequest":
        if self.closed:
            raise RuntimeError("session closed")
        return _ContextRequest(self.context, self.timeout_ms)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.context.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        return "AuthenticatedSession(origin=%r, closed=%r)" % (self.origin, self.closed)


@dataclass
class LoginResult:
    status: str
    reason_code: Optional[str] = None
    message: str = ""
    session_valid: bool = False
    capabilities: Dict[str, str] = field(default_factory=dict)
    session: Optional[AuthenticatedSession] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.status not in (LOGIN_SUCCEEDED, LOGIN_FAILED):
            raise ValueError("unknown login status")
        if self.reason_code is not None and self.reason_code not in _REASON_CODES:
            raise ValueError("unknown login reason_code")

    def __repr__(self) -> str:
        return "LoginResult(status=%r, reason_code=%r, session_valid=%r)" % (
            self.status,
            self.reason_code,
            self.session_valid,
        )


class _ContextRequest:
    """把 BrowserContext.request 收敛成 SdaAdapter 需要的 `SdaResponse`。"""

    def __init__(self, context: Any, timeout_ms: int) -> None:
        self._context = context
        self._timeout_ms = timeout_ms

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[Dict[str, Any]] = None,
        allow_redirects: bool = False,
    ):
        from .sda_adapter import SdaResponse

        data = json.dumps(body, ensure_ascii=False) if body is not None else None
        request_headers = dict(headers or {})
        if data is not None:
            request_headers.setdefault("content-type", "application/json")
        response = self._context.request.fetch(
            method=method.upper(),
            url=url,
            headers=request_headers,
            data=data,
            max_redirects=20 if allow_redirects else 0,
            timeout=self._timeout_ms,
        )
        raw_headers = dict(getattr(response, "headers", {}) or {})
        safe_headers = {
            str(key).lower(): str(value)
            for key, value in raw_headers.items()
            if str(key).lower() in contracts.SAFE_RESPONSE_HEADERS
        }
        from .ipc import MAX_CONTENT_BYTES
        from .stream_reader import StreamError
        try:
            envelope_limit = MAX_CONTENT_BYTES * 4 // 3 + 32768
            declared = safe_headers.get("content-length")
            if declared is not None and int(declared) > envelope_limit:
                raise StreamError("too_large")
            raw = bytes(response.body() or b"")
            if len(raw) > envelope_limit:
                raise StreamError("too_large")
            return SdaResponse(
                status=int(getattr(response, "status", 0) or 0), headers=safe_headers, body=raw,
                final_url=str(getattr(response, "url", "") or url),
                renewal_observed=any(str(key).lower() == "set-cookie" for key in raw_headers),
            )
        finally:
            if hasattr(response, "dispose"):
                response.dispose()


class SdaLogin:
    """一次登录 = 一个新 context + 一次表单提交 + 一组受保护 probe。"""

    def __init__(self, browser: Any, config: LoginConfig) -> None:
        if not isinstance(config, LoginConfig):
            raise ValueError("config must be LoginConfig")
        self.browser = browser
        self.config = config

    def authenticate(self, credentials: Credentials) -> LoginResult:
        if not isinstance(credentials, Credentials):
            raise ValueError("credentials must be Credentials")
        context = None
        page = None
        try:
            try:
                context = self.browser.new_context()
                page = context.new_page()
            except Exception:
                return self._fail(REASON_NETWORK, "无法建立登录上下文")
            try:
                page.goto(self.config.login_url, timeout_ms=self.config.timeout_ms)
            except Exception:
                return self._fail(REASON_NETWORK, "打开登录入口失败")

            current = str(getattr(page, "url", "") or "")
            if not self._allowed_navigation(current):
                return self._fail(REASON_REDIRECT_BLOCKED, "登录入口跳转到未授权域")
            if self._any_text(page, self.config.lock_selectors):
                return self._fail(REASON_LOCKED, "账号已锁定")
            if not self._form_present(page):
                return self._fail(REASON_FLOW_CHANGED, "登录表单结构已变化")

            try:
                page.fill(self.config.username_selector, credentials.username)
                page.fill(self.config.password_selector, credentials.password)
                page.click(self.config.submit_selector)
            except Exception:
                return self._fail(REASON_FLOW_CHANGED, "填写或提交登录表单失败")

            try:
                landed = page.wait_for_url(
                    self.config.success_prefixes, timeout_ms=self.config.timeout_ms
                )
            except Exception:
                return self._fail(REASON_NETWORK, "等待登录结果失败")
            final_url = str(getattr(page, "url", "") or "")
            if self._any_text(page, self.config.lock_selectors):
                return self._fail(REASON_LOCKED, "账号已锁定")
            if not landed:
                if self._any_text(page, self.config.error_selectors):
                    return self._fail(REASON_BAD_CREDENTIALS, "用户名或密码错误")
                if not self._allowed_navigation(final_url):
                    return self._fail(REASON_REDIRECT_BLOCKED, "登录跳转到未授权域")
                return self._fail(REASON_AUTH_FAILED, "登录未到达允许的业务页")
            if not self._is_success_url(final_url):
                if not self._allowed_navigation(final_url):
                    return self._fail(REASON_REDIRECT_BLOCKED, "登录跳转到未授权域")
                return self._fail(REASON_AUTH_FAILED, "登录未到达允许的业务页")

            try:
                page.close()
            except Exception:
                pass
            page = None
            try:
                result = self._probe(context)
            except Exception:
                return self._fail(REASON_NETWORK, "受保护能力核验请求失败")
            if result.status == LOGIN_SUCCEEDED:
                return result
            return result
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
            if context is not None and (
                "result" not in locals() or result.status != LOGIN_SUCCEEDED
            ):
                try:
                    context.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _fail(self, reason: str, message: str) -> LoginResult:
        return LoginResult(LOGIN_FAILED, reason, _safe_message(message))

    def _form_present(self, page: Any) -> bool:
        return all(
            bool(page.find(selector))
            for selector in (
                self.config.username_selector,
                self.config.password_selector,
                self.config.submit_selector,
            )
        )

    def _any_text(self, page: Any, selectors: Iterable[str]) -> bool:
        for selector in selectors:
            try:
                if page.find(selector) and str(page.text(selector) or "").strip():
                    return True
            except Exception:
                continue
        return False

    def _allowed_navigation(self, url: str) -> bool:
        origin = _url_origin(url)
        allowed = {self.config.origin} | set(self.config.identity_origins)
        return origin in allowed

    def _is_success_url(self, url: str) -> bool:
        if _url_origin(url) != self.config.origin:
            return False
        return any(str(url).startswith(prefix) for prefix in self.config.success_prefixes)

    def _probe(self, context: Any) -> LoginResult:
        session = AuthenticatedSession(
            context=context,
            origin=self.config.origin,
            timeout_ms=self.config.timeout_ms,
        )
        adapter = SdaAdapter(self.config.origin, session.request_session())
        capabilities: Dict[str, str] = {}
        failure = None
        protected = False
        for capability in self.config.required_capabilities:
            result = adapter.probe(capability)
            if result.status == "succeeded":
                capabilities[capability] = "succeeded"
                if result.session_valid:
                    protected = True
            else:
                capabilities[capability] = result.reason_code or "business_error"
                if failure is None:
                    failure = result
        if failure is not None:
            return LoginResult(
                LOGIN_FAILED,
                failure.reason_code if failure.reason_code in _REASON_CODES else "business_error",
                _safe_message(failure.message or "受保护能力核验失败"),
                session_valid=False,
                capabilities=capabilities,
            )
        if not protected:
            return LoginResult(
                LOGIN_FAILED,
                REASON_PROTOCOL,
                "没有受保护能力被实际验证（catalog 匿名成功不算登录）",
                session_valid=False,
                capabilities=capabilities,
            )
        return LoginResult(
            LOGIN_SUCCEEDED,
            None,
            "",
            session_valid=True,
            capabilities=capabilities,
            session=session,
        )


def _url_origin(url: str) -> str:
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return ""
    if parts.scheme != "https" or not parts.hostname:
        return ""
    if parts.username or parts.password:
        return ""
    port = parts.port or 443
    if port != 443:
        return ""
    return "https://" + parts.hostname


def _safe_message(text: Any, limit: int = 200) -> str:
    value = str(text or "").strip()
    return value[:limit]
