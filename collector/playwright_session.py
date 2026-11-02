"""M16-17 Playwright 适配层（真实浏览器路径，证据 BLOCKED-EXTERNAL）。

本模块在**独立 collector 环境**运行，顶层只 import 标准库；Playwright 在
`launch()` 内延迟导入。它只负责把 Playwright 的 Browser/BrowserContext/Page/
APIResponse 转成 `login_flow.SdaLogin` 需要的稳定接口。真实 Chromium 启动、
企业 CA/代理、登录选择器与 SSO 跳转没有授权内网时不得标 PASS。

安全边界：
- 每次 `new_context()` 都创建新的非持久化 context，不传 `storage_state`；
- 关闭浏览器时同时停止 Playwright，不遗留后台进程；
- 返回给上层的响应头已在 `login_flow._ContextRequest` 收窄到安全白名单。
"""

from __future__ import annotations

import os
from typing import Any, Iterable


class PlaywrightRequest:
    def __init__(self, request: Any) -> None:
        self._request = request

    def fetch(self, **kwargs):
        return self._request.fetch(**kwargs)


class PlaywrightPage:
    def __init__(self, page: Any, timeout_ms: int) -> None:
        self._page = page
        self._timeout = timeout_ms

    @property
    def url(self) -> str:
        return str(self._page.url or "")

    def goto(self, url: str, timeout_ms: int = 15000) -> None:
        self._page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    def find(self, selector: str) -> bool:
        return self._page.locator(selector).count() > 0

    def fill(self, selector: str, value: str) -> None:
        self._page.locator(selector).first.fill(value, timeout=self._timeout)

    def click(self, selector: str) -> None:
        self._page.locator(selector).first.click(timeout=self._timeout)

    def text(self, selector: str) -> str:
        return str(self._page.locator(selector).first.text_content(timeout=self._timeout) or "")

    def wait_for_url(self, prefixes: Iterable[str], timeout_ms: int = 15000) -> bool:
        candidates = tuple(prefixes)

        def _matches(url: Any) -> bool:
            return any(str(url).startswith(prefix) for prefix in candidates)

        try:
            self._page.wait_for_url(_matches, timeout=timeout_ms)
            return True
        except Exception as exc:  # Playwright TimeoutError 不保证可 import
            if type(exc).__name__ == "TimeoutError":
                return False
            raise

    def close(self) -> None:
        self._page.close()


class PlaywrightContext:
    def __init__(self, context: Any, timeout_ms: int) -> None:
        self._context = context
        self._timeout = timeout_ms

    def new_page(self) -> PlaywrightPage:
        return PlaywrightPage(self._context.new_page(), self._timeout)

    @property
    def request(self) -> PlaywrightRequest:
        return PlaywrightRequest(self._context.request)

    def close(self) -> None:
        self._context.close()


class PlaywrightBrowser:
    """同步 Playwright Chromium；调用方负责 `close()`。"""

    def __init__(self, playwright: Any, browser: Any, timeout_ms: int = 15000) -> None:
        self._playwright = playwright
        self._browser = browser
        self._timeout = timeout_ms

    @classmethod
    def launch(
        cls,
        headless: bool = True,
        executable_path: str = "",
        args: Iterable[str] = (),
        timeout_ms: int = 15000,
    ) -> "PlaywrightBrowser":
        from playwright.sync_api import sync_playwright  # type: ignore

        playwright = sync_playwright().start()
        try:
            launch_options = {"headless": bool(headless), "args": list(args)}
            configured = str(executable_path or "").strip()
            if configured:
                launch_options["executable_path"] = os.path.abspath(configured)
            browser = playwright.chromium.launch(**launch_options)
        except Exception:
            playwright.stop()
            raise
        return cls(playwright, browser, timeout_ms)

    def new_context(self) -> PlaywrightContext:
        return PlaywrightContext(self._browser.new_context(), self._timeout)

    def close(self) -> None:
        try:
            self._browser.close()
        finally:
            self._playwright.stop()
