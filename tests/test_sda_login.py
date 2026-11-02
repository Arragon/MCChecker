"""M16-17 普通账密登录与受保护能力核验（离线 fake browser）。

真实 Playwright/Chromium 与企业 SSO 证据没有授权内网，保持 BLOCKED-EXTERNAL；
这里用假 BrowserContext/Page 钉住状态机：连接隔离、错误可区分、catalog 不算
登录、失败关闭 context、密码/Cookie 不进结果。
"""

from __future__ import annotations

import json

import pytest

from collector import login_flow
from collector.login_flow import Credentials, LoginConfig, SdaLogin

ORIGIN = "https://sda.example"
LOGIN_URL = ORIGIN + "/login"
SUCCESS_URL = ORIGIN + "/app/home"

TABLE_BODY = json.dumps(
    {"ok": True, "resource_kind": "table", "table": {"table_id": "t1", "rows": []}}
).encode("utf-8")
FILE_BODY = json.dumps(
    {
        "ok": True,
        "resource_kind": "file",
        "file": {"resource_ref": "res-1", "size": 0, "content_sha256": "0" * 64},
    }
).encode("utf-8")


def make_config(**overrides):
    values = {
        "origin": ORIGIN,
        "login_path": "/login",
        "success_paths": ("/app/",),
        "identity_origins": ("https://sso.example",),
        "username_selector": "#username",
        "password_selector": "#password",
        "submit_selector": "#submit",
        "error_selectors": ("#error",),
        "lock_selectors": ("#locked",),
        "required_capabilities": ("table_read", "file_read"),
        "timeout_ms": 5000,
    }
    values.update(overrides)
    return LoginConfig(**values)


class FakeResponse:
    def __init__(self, status, body, url, headers=None):
        self.status = status
        self._body = body
        self.url = url
        self.headers = headers or {"content-type": "application/json"}

    def body(self):
        return self._body


class FakeRequest:
    def __init__(self, context):
        self.context = context

    def fetch(self, method, url, headers=None, data=None, max_redirects=0, timeout=0):
        self.context.requests.append(
            {"method": method, "url": url, "headers": dict(headers or {}), "data": data}
        )
        path = url[len(self.context.origin) :] if url.startswith(self.context.origin) else url
        route = self.context.responses.get(path)
        if route is None:
            return FakeResponse(404, b"", url)
        status = route.get("status", 200)
        body = route.get("body", b"")
        final = route.get("url", url)
        return FakeResponse(status, body, final, route.get("headers"))


class FakePage:
    def __init__(self, context, spec):
        self.context = context
        self.spec = spec
        self.origin = context.origin
        self.current_url = ""
        self.closed = False
        self.fills = []
        self.clicks = 0
        self.texts = {}
        self.selectors = {"#username", "#password", "#submit", "#error", "#locked"}
        self.selectors -= set(spec.get("missing") or ())

    @property
    def url(self):
        return self.current_url

    def goto(self, url, timeout_ms=15000):
        if self.spec.get("goto_raises"):
            raise RuntimeError("network down")
        self.current_url = self.spec.get("goto_final", url)

    def find(self, selector):
        return selector in self.selectors

    def fill(self, selector, value):
        if selector not in self.selectors:
            raise RuntimeError("selector missing")
        self.fills.append((selector, value))

    def click(self, selector):
        if selector not in self.selectors:
            raise RuntimeError("selector missing")
        self.clicks += 1
        behavior = self.spec.get("click", "success")
        if behavior == "success":
            self.current_url = self.spec.get("success_url", SUCCESS_URL)
            self.context.cookies["session"] = "cookie-" + self.context.name
        elif behavior == "bad":
            self.texts["#error"] = "用户名或密码错误"
        elif behavior == "locked":
            self.texts["#locked"] = "账号已锁定"
        elif behavior == "redirect":
            self.current_url = "https://evil.invalid/app/home"

    def text(self, selector):
        return self.texts.get(selector, "")

    def wait_for_url(self, prefixes, timeout_ms=15000):
        return any(self.current_url.startswith(prefix) for prefix in prefixes)

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, browser, name, spec):
        self.browser = browser
        self.name = name
        self.spec = spec
        self.origin = browser.origin
        self.cookies = {}
        self.closed = False
        self.requests = []
        self.requests_facade = FakeRequest(self)
        self.pages = []
        self.responses = dict(browser.responses)

    def new_page(self):
        page = FakePage(self, self.spec)
        self.pages.append(page)
        return page

    @property
    def request(self):
        return self.requests_facade

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, specs=(), responses=None):
        self.specs = list(specs)
        self.responses = dict(responses or {})
        self.origin = ORIGIN
        self.contexts = []

    def new_context(self):
        index = len(self.contexts)
        spec = self.specs[index] if index < len(self.specs) else {}
        context = FakeContext(self, "c%d" % index, spec)
        self.contexts.append(context)
        return context


def ok_responses():
    return {
        "/synthetic/table": {"status": 200, "body": TABLE_BODY},
        "/synthetic/file": {"status": 200, "body": FILE_BODY},
    }


def credentials():
    return Credentials("operator", "very-secret-password")


def test_success_requires_protected_capability_and_keeps_isolated_context():
    browser = FakeBrowser([{}], ok_responses())
    result = SdaLogin(browser, make_config()).authenticate(credentials())

    assert result.status == "succeeded"
    assert result.reason_code is None
    assert result.session_valid is True
    assert result.capabilities == {"table_read": "succeeded", "file_read": "succeeded"}
    assert result.session is not None
    assert len(browser.contexts) == 1
    context = browser.contexts[0]
    assert context.closed is False
    assert context.pages[0].closed is True
    assert context.cookies == {"session": "cookie-c0"}
    assert all("cookie" not in row["headers"] for row in context.requests)
    assert all("very-secret-password" not in str(row["data"]) for row in context.requests)
    assert "very-secret-password" not in repr(result)
    assert "very-secret-password" not in repr(credentials())

    result.session.close()
    assert context.closed is True


def test_bad_credentials_are_distinct_from_form_change():
    bad = FakeBrowser([{"click": "bad"}], ok_responses())
    bad_result = SdaLogin(bad, make_config()).authenticate(credentials())
    assert bad_result.status == "failed"
    assert bad_result.reason_code == "bad_credentials"
    assert bad_result.session is None
    assert bad.contexts[0].closed is True

    changed = FakeBrowser([{"missing": ("#password",)}], ok_responses())
    changed_result = SdaLogin(changed, make_config()).authenticate(credentials())
    assert changed_result.reason_code == "login_flow_changed"
    assert changed.contexts[0].closed is True
    assert changed.contexts[0].pages[0].clicks == 0


def test_locked_and_illegal_redirect_are_distinct():
    locked = FakeBrowser([{"click": "locked"}], ok_responses())
    locked_result = SdaLogin(locked, make_config()).authenticate(credentials())
    assert locked_result.reason_code == "locked"
    assert locked.contexts[0].closed is True

    redirected = FakeBrowser([{"click": "redirect"}], ok_responses())
    redirect_result = SdaLogin(redirected, make_config()).authenticate(credentials())
    assert redirect_result.reason_code == "redirect_blocked"
    assert redirected.contexts[0].closed is True
    assert redirected.contexts[0].requests == []

    goto_redirected = FakeBrowser([{"goto_final": "https://evil.invalid/login"}], ok_responses())
    goto_result = SdaLogin(goto_redirected, make_config()).authenticate(credentials())
    assert goto_result.reason_code == "redirect_blocked"


def test_two_connections_get_separate_contexts_and_cookie_jars():
    browser = FakeBrowser([{}, {"success_url": SUCCESS_URL}], ok_responses())
    login = SdaLogin(browser, make_config())
    first = login.authenticate(Credentials("one", "pw-one"))
    second = login.authenticate(Credentials("two", "pw-two"))

    assert first.status == second.status == "succeeded"
    assert len(browser.contexts) == 2
    one, two = browser.contexts
    assert one is not two
    assert one.cookies is not two.cookies
    assert one.cookies == {"session": "cookie-c0"}
    assert two.cookies == {"session": "cookie-c1"}
    assert "cookie-c1" not in one.cookies.values()
    first.session.close()
    second.session.close()


def test_capability_probe_failure_is_not_login_success():
    responses = ok_responses()
    responses["/synthetic/file"] = {"status": 403, "body": b""}
    browser = FakeBrowser([{}], responses)
    result = SdaLogin(browser, make_config()).authenticate(credentials())

    assert result.status == "failed"
    assert result.reason_code == "permission_denied"
    assert result.session_valid is False
    assert result.session is None
    assert result.capabilities == {
        "table_read": "succeeded",
        "file_read": "permission_denied",
    }
    assert browser.contexts[0].closed is True


def test_catalog_only_config_is_rejected_and_network_error_does_not_leak():
    with pytest.raises(ValueError):
        make_config(required_capabilities=("catalog_read",))

    browser = FakeBrowser([{"goto_raises": True}], ok_responses())
    result = SdaLogin(browser, make_config()).authenticate(credentials())
    assert result.status == "failed"
    assert result.reason_code == "network_error"
    assert browser.contexts[0].closed is True
    assert browser.contexts[0].requests == []
    assert "very-secret-password" not in str(result)

    with pytest.raises(ValueError):
        LoginConfig(
            origin="http://sda.example",
            login_path="/login",
            success_paths=("/app/",),
            username_selector="#u",
            password_selector="#p",
            submit_selector="#s",
        )


def test_session_after_close_refuses_new_requests():
    browser = FakeBrowser([{}], ok_responses())
    result = SdaLogin(browser, make_config()).authenticate(credentials())
    result.session.close()
    with pytest.raises(RuntimeError):
        result.session.request_session()
