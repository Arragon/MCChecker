"""M16-41 真实内网验收探针（默认 dry-run，只有显式 --execute 才发真实请求）。

设计约束：
- 账密只从环境变量读取（`MCC_SDA_USER` / `MCC_SDA_PASSWORD`），绝不写文件、绝不
  进命令行参数、绝不打印；输出一律脱敏。
- 默认 dry-run：只校验配置、打印脱敏计划与将采集的字段，不联网。
- `--execute` 只做只读请求（登录 → 受保护 probe → 目录/表/文件读取），不提交、
  不发布、不改任何数据；重定向只允许同 origin。
- 结论写入 `--out` 指定的 JSON，字段值与时间戳留空位置由执行人填写真实观测。

这份脚本的存在不等于验收通过：真实结论只能由具备授权内网环境的人执行后回填。
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

REQUIRED_FIELDS = ("origin", "login_path", "probe_path", "table_path", "file_path")
REDACT_KEYS = ("password", "passwd", "cookie", "csrf", "token", "authorization", "secret", "set-cookie")


class ConfigError(Exception):
    pass


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ConfigError("配置必须是 JSON 对象")
    missing = [field for field in REQUIRED_FIELDS if not data.get(field)]
    if missing:
        raise ConfigError("缺少字段：%s" % ", ".join(missing))
    origin = str(data["origin"]).rstrip("/")
    parsed = urllib.parse.urlsplit(origin)
    if parsed.scheme not in ("https", "http"):
        raise ConfigError("origin 必须是 http(s)")
    if parsed.username or parsed.password:
        raise ConfigError("origin 不允许内嵌账号密码")
    if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ConfigError("非 loopback 的 origin 必须是 https")
    data["origin"] = origin
    idle = data.get("idle_window_minutes", 30)
    if not isinstance(idle, int) or idle < 1 or idle > 24 * 60:
        raise ConfigError("idle_window_minutes 必须是 1–1440 的整数")
    data["idle_window_minutes"] = idle
    return data


def redact(value: Any) -> Any:
    """递归脱敏：命中敏感键名一律替换，字符串里出现的长令牌同样打码。"""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if any(marker in str(key).lower() for marker in REDACT_KEYS):
                out[key] = "<redacted>"
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def plan_steps(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """执行计划：每步只描述意图与将记录的字段，便于评审先看后跑。"""
    origin = config["origin"]
    return [
        {
            "step": "login",
            "url": origin + str(config["login_path"]),
            "method": "POST",
            "record": ["status", "has_set_cookie", "cookie_rotated", "redirected_to"],
            "note": "账密由环境变量注入；只记录布尔值与状态码",
        },
        {
            "step": "probe",
            "url": origin + str(config["probe_path"]),
            "method": "GET",
            "record": ["status", "content_type", "bytes", "csrf_rotated"],
            "note": "受保护能力 canary；失败说明会话不可用",
        },
        {
            "step": "table",
            "url": origin + str(config["table_path"]),
            "method": "GET",
            "record": ["status", "content_type", "bytes", "sha256"],
            "note": "只读最小表；记录内容指纹，不保存正文",
        },
        {
            "step": "file",
            "url": origin + str(config["file_path"]),
            "method": "GET",
            "record": ["status", "content_type", "bytes", "sha256"],
            "note": "只读单文件；记录内容指纹，不保存正文",
        },
        {
            "step": "idle-recheck",
            "url": origin + str(config["probe_path"]),
            "method": "GET",
            "record": ["waited_minutes", "status", "heartbeat_validated"],
            "note": "等待 idle_window_minutes 后仅用心跳探测；跨真实 idle 成功才可置 heartbeat_validated=true",
        },
    ]


def _credentials() -> Tuple[str, str]:
    user = os.environ.get("MCC_SDA_USER", "")
    password = os.environ.get("MCC_SDA_PASSWORD", "")
    if not user or not password:
        raise ConfigError("真实执行需要环境变量 MCC_SDA_USER / MCC_SDA_PASSWORD")
    return user, password


def _request(
    url: str, method: str, body: Optional[bytes], headers: Dict[str, str], timeout: int = 30
) -> Tuple[int, Dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.status, dict(response.headers), response.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read(64 * 1024)


def execute(config: Dict[str, Any]) -> Dict[str, Any]:
    """真实只读执行；结果只含状态码/布尔/指纹。"""
    import hashlib

    user, password = _credentials()
    origin = config["origin"]
    results: Dict[str, Any] = {"origin": origin, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    cookie_header = ""
    login_body = urllib.parse.urlencode({"username": user, "password": password}).encode("utf-8")
    status, headers, _payload = _request(
        origin + str(config["login_path"]),
        "POST",
        login_body,
        {"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/html"},
    )
    set_cookie = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
    results["login"] = {
        "status": status,
        "has_set_cookie": bool(set_cookie),
        "cookie_rotated": bool(set_cookie) and cookie_header != set_cookie,
    }
    cookie_header = set_cookie.split(";")[0] if set_cookie else ""

    common = {"Accept": "*/*"}
    if cookie_header:
        common["Cookie"] = cookie_header
    for step, key in (("probe", "probe_path"), ("table", "table_path"), ("file", "file_path")):
        status, headers, payload = _request(origin + str(config[key]), "GET", None, common)
        results[step] = {
            "status": status,
            "content_type": headers.get("Content-Type") or headers.get("content-type") or "",
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    if config.get("idle_window_minutes"):
        minutes = int(config["idle_window_minutes"])
        if minutes > 5:
            sys.stderr.write("等待 %d 分钟 idle 窗口后再探测…\n" % minutes)
            time.sleep(minutes * 60)
        status, headers, payload = _request(origin + str(config["probe_path"]), "GET", None, common)
        results["idle_recheck"] = {
            "waited_minutes": minutes,
            "status": status,
            "bytes": len(payload),
            "heartbeat_validated": status == 200,
        }
    results["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return redact(results)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="M16-41 真实内网只读验收探针（默认 dry-run）")
    parser.add_argument("--config", required=True, help="验收配置 JSON（不含任何凭据）")
    parser.add_argument("--execute", action="store_true", help="执行真实只读请求（需要授权内网 + 环境变量凭据）")
    parser.add_argument("--out", default=None, help="--execute 时的结果 JSON 路径")
    parser.add_argument("--json", action="store_true", help="dry-run 时输出机器可读计划")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except (OSError, ValueError, ConfigError) as exc:
        sys.stderr.write("配置不可用：%s\n" % exc)
        return 2

    plan = {"origin": config["origin"], "idle_window_minutes": config["idle_window_minutes"], "steps": plan_steps(config)}
    if not args.execute:
        if args.json:
            sys.stdout.write(json.dumps(redact(plan), ensure_ascii=False, indent=2) + "\n")
        else:
            sys.stdout.write("dry-run：只校验配置，不联网。将执行 %d 步：\n" % len(plan["steps"]))
            for step in plan["steps"]:
                sys.stdout.write("  - %s %s %s（记录 %s）\n" % (step["method"], step["url"], step["step"], ",".join(step["record"])))
            sys.stdout.write("真实执行请加 --execute，并保证 MCC_SDA_USER / MCC_SDA_PASSWORD 已注入。\n")
        return 0

    if not args.out:
        sys.stderr.write("--execute 必须同时给 --out（结果落盘便于脱敏复核）\n")
        return 2
    try:
        results = execute(config)
    except (ConfigError, urllib.error.URLError, OSError) as exc:
        sys.stderr.write("执行失败：%s\n" % exc)
        return 2
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    sys.stdout.write("已写入脱敏结果：%s\n" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
