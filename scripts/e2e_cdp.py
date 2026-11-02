"""M16-40 极简 Chrome DevTools Protocol 客户端（标准库，零第三方依赖）。

用途：真实浏览器 E2E 需要「设置视口 / 注入请求头 / 发送键盘事件 / 截图」，
而 `--dump-dom` 只能拿到静态 DOM。这里用 stdlib 实现 WebSocket 客户端 + CDP
调用，避免把 Playwright/selenium 塞进核心环境（Python 3.9、零三方依赖）。

只覆盖 E2E 需要的几个域：Page / Runtime / Emulation / Network / Input / Log。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from typing import Any, Dict, List, Optional

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)


class CdpError(RuntimeError):
    pass


def find_chrome(explicit: Optional[str] = None) -> str:
    candidates = ([explicit] if explicit else []) + [os.environ.get("CHROME_BIN")] + list(CHROME_CANDIDATES)
    for path in candidates:
        if path and os.path.exists(path):
            return path
    found = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chrome")
    if found:
        return found
    raise CdpError("找不到 Chrome/Chromium；用 --chrome 指定可执行文件")


class WebSocket:
    """最小客户端：文本帧收发，支持分片与 ping/pong。"""

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        assert url.startswith("ws://"), url
        rest = url[len("ws://") :]
        host_port, _, path = rest.partition("/")
        host, _, port = host_port.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            "GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, host_port, key)
        )
        self.sock.sendall(request.encode())
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CdpError("WebSocket 握手被关闭")
            header += chunk
        if b"101" not in header.split(b"\r\n")[0]:
            raise CdpError("WebSocket 握手失败：%s" % header.split(b"\r\n")[0].decode("latin-1"))
        self._buffer = b""

    def _recv_exact(self, size: int) -> bytes:
        while len(self._buffer) < size:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CdpError("连接被关闭")
            self._buffer += chunk
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    def send(self, text: str) -> None:
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        header += mask
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv(self) -> str:
        frames = []
        while True:
            first, second = self._recv_exact(2)
            fin = first & 0x80
            opcode = first & 0x0F
            masked = second & 0x80
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length)
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x9:  # ping
                self._pong(payload)
                continue
            if opcode == 0x8:  # close
                raise CdpError("服务端关闭了 WebSocket")
            frames.append(payload)
            if fin:
                return b"".join(frames).decode("utf-8")

    def _pong(self, payload: bytes) -> None:
        frame = bytearray([0x8A, 0x80 | len(payload)])
        mask = os.urandom(4)
        frame += mask
        frame += bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(frame))

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def default_profile_dir() -> str:
    """不依赖 tempfile：某些受限环境里 `tempfile.mkdtemp()` 会被沙箱拒绝。"""
    base = os.environ.get("MCC_E2E_CHROME_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "mcc-e2e-chrome"
    )
    path = "%s-%d-%d" % (base, os.getpid(), int(time.time() * 1000) % 100000)
    os.makedirs(path, exist_ok=True)
    return path


class Chrome:
    """启动 headless Chrome 并暴露 CDP 便捷方法。"""

    def __init__(
        self,
        chrome: Optional[str] = None,
        profile_dir: Optional[str] = None,
        headless: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.binary = find_chrome(chrome)
        self.profile_dir = profile_dir or default_profile_dir()
        args = [
            self.binary,
            "--remote-debugging-port=0",
            "--user-data-dir=%s" % self.profile_dir,
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-features=Translate,MediaRouter",
            "about:blank",
        ]
        if headless:
            args.insert(1, "--headless=new")
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.timeout = timeout
        self.ws_url = self._wait_for_ws()
        port = int(self.ws_url.rsplit(":", 1)[1].split("/")[0])
        self.port = port
        page_ws = self._pick_page_target(port)
        self.ws = WebSocket(page_ws, timeout=timeout)
        self._id = 0
        self.events: List[Dict[str, Any]] = []
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Log.enable")
        self.call("Network.enable")

    def _pick_page_target(self, port: int, deadline_seconds: float = 15.0) -> str:
        """浏览器级端点不能直接调 Page.*，必须连到 page target。"""
        deadline = time.time() + deadline_seconds
        last = ""
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=3) as response:
                    targets = json.loads(response.read().decode("utf-8"))
                for target in targets:
                    if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                        return target["webSocketDebuggerUrl"]
                last = "没有 page target"
            except (OSError, ValueError) as exc:  # pragma: no cover - 环境相关
                last = str(exc)
            time.sleep(0.1)
        raise CdpError("拿不到 page target：%s" % last)

    def _wait_for_ws(self, deadline_seconds: float = 20.0) -> str:
        deadline = time.time() + deadline_seconds
        while time.time() < deadline:
            line = self.proc.stderr.readline().decode("utf-8", "replace") if self.proc.stderr else ""
            if "DevTools listening on" in line:
                return line.split("DevTools listening on")[1].strip()
            if not line and self.proc.poll() is not None:
                raise CdpError("Chrome 提前退出：%s" % (self.proc.returncode,))
            time.sleep(0.05)
        raise CdpError("等待 DevTools 端点超时")

    def call(self, method: str, **params: Any) -> Dict[str, Any]:
        self._id += 1
        request_id = self._id
        self.ws.send(json.dumps({"id": request_id, "method": method, "params": params}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == request_id:
                if "error" in message:
                    raise CdpError("%s 失败：%s" % (method, message["error"]))
                return message.get("result") or {}
            if message.get("method"):
                self.events.append(message)

    def reload(self, timeout: Optional[float] = None) -> None:
        """整页重载：换身份后必须重载，SPA 的 session 是启动时拉取的。"""
        self.call("Page.reload", ignoreCache=False)
        deadline = time.time() + (timeout or 5.0)
        self.ws.sock.settimeout(0.5)
        try:
            while time.time() < deadline:
                try:
                    message = json.loads(self.ws.recv())
                except (socket.timeout, CdpError):
                    return
                if message.get("method") == "Page.loadEventFired":
                    return
        finally:
            self.ws.sock.settimeout(self.timeout)

    def navigate(self, url: str, timeout: Optional[float] = None) -> None:
        """导航。hash-only 跳转不会触发 Page.loadEventFired，所以这里只做短等待，
        真正的就绪判断交给调用方的 `wait_for`。"""
        self.call("Page.navigate", url=url)
        deadline = time.time() + (timeout or 3.0)
        self.ws.sock.settimeout(0.5)
        try:
            while time.time() < deadline:
                try:
                    message = json.loads(self.ws.recv())
                except (socket.timeout, CdpError):
                    return
                if message.get("method") == "Page.loadEventFired":
                    return
                if message.get("method"):
                    self.events.append(message)
        finally:
            self.ws.sock.settimeout(self.timeout)

    def eval(self, expression: str, await_promise: bool = True) -> Any:
        result = self.call(
            "Runtime.evaluate",
            expression=expression,
            returnByValue=True,
            awaitPromise=await_promise,
            userGesture=True,
        )
        if result.get("exceptionDetails"):
            raise CdpError("JS 异常：%s" % json.dumps(result["exceptionDetails"], ensure_ascii=False)[:500])
        return (result.get("result") or {}).get("value")

    def wait_for(self, expression: str, timeout: Optional[float] = None, interval: float = 0.15) -> bool:
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            try:
                if self.eval("!!(%s)" % expression):
                    return True
            except CdpError:
                pass
            time.sleep(interval)
        return False

    def viewport(self, width: int, height: int, scale: int = 1) -> None:
        self.call(
            "Emulation.setDeviceMetricsOverride",
            width=int(width),
            height=int(height),
            deviceScaleFactor=scale,
            mobile=False,
        )

    def headers(self, extra: Dict[str, str]) -> None:
        self.call("Network.setExtraHTTPHeaders", headers=extra)

    def press(self, key: str, code: Optional[str] = None) -> None:
        mapping = {
            "Escape": (27, "Escape"),
            "Tab": (9, "Tab"),
            "Enter": (13, "Enter"),
            "ArrowDown": (40, "ArrowDown"),
        }
        key_code, default_code = mapping.get(key, (0, key))
        for kind in ("rawKeyDown", "keyUp"):
            self.call(
                "Input.dispatchKeyEvent",
                type=kind,
                key=key,
                code=code or default_code,
                windowsVirtualKeyCode=key_code,
                nativeVirtualKeyCode=key_code,
            )

    def screenshot(self, path: str, full_page: bool = False) -> None:
        params: Dict[str, Any] = {"format": "png"}
        if full_page:
            params["captureBeyondViewport"] = True
        data = self.call("Page.captureScreenshot", **params)["data"]
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(data))

    def close(self) -> None:
        try:
            self.ws.close()
        finally:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=10)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self.proc.kill()
                except OSError:
                    pass


if __name__ == "__main__":  # 手工自检：开一个页面并打印标题
    target_url = sys.argv[1] if len(sys.argv) > 1 else "about:blank"
    browser = Chrome()
    try:
        browser.navigate(target_url)
        print(browser.eval("document.title"))
    finally:
        browser.close()
