"""M16-41 内网验收探针：配置校验、脱敏与 dry-run 行为（不联网）。"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "sda_live_check.py"


def load_module():
    spec = importlib.util.spec_from_file_location("sda_live_check_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


CHECK = load_module()


def write_config(tmp_path: Path, **overrides) -> Path:
    payload = {
        "origin": "https://sda.example.internal",
        "login_path": "/login",
        "probe_path": "/api/keepalive",
        "table_path": "/api/table/TP-1",
        "file_path": "/api/file/F-1",
        "idle_window_minutes": 30,
    }
    payload.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = CHECK.main(argv)
    return code, out.getvalue(), err.getvalue()


def test_dry_run_never_touches_network(tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("dry-run 不允许发请求")

    monkeypatch.setattr(CHECK, "_request", explode)
    path = write_config(tmp_path)
    code, out, _err = run(["--config", str(path)])
    assert code == 0
    assert "dry-run" in out
    assert "idle" in out


def test_dry_run_json_plan_is_structured(tmp_path):
    path = write_config(tmp_path)
    code, out, _err = run(["--config", str(path), "--json"])
    assert code == 0
    plan = json.loads(out)
    assert plan["origin"] == "https://sda.example.internal"
    assert [step["step"] for step in plan["steps"]] == ["login", "probe", "table", "file", "idle-recheck"]
    assert plan["steps"][0]["method"] == "POST"


def test_missing_field_is_rejected(tmp_path):
    path = write_config(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("probe_path")
    path.write_text(json.dumps(payload), encoding="utf-8")
    code, _out, err = run(["--config", str(path)])
    assert code == 2
    assert "probe_path" in err


def test_plain_http_remote_origin_rejected(tmp_path):
    path = write_config(tmp_path, origin="http://sda.example.internal")
    code, _out, err = run(["--config", str(path)])
    assert code == 2
    assert "https" in err


def test_userinfo_in_origin_rejected(tmp_path):
    path = write_config(tmp_path, origin="https://user:pass@sda.example.internal")
    code, _out, err = run(["--config", str(path)])
    assert code == 2
    assert "账号密码" in err


def test_idle_window_bounds(tmp_path):
    path = write_config(tmp_path, idle_window_minutes=0)
    code, _out, _err = run(["--config", str(path)])
    assert code == 2


def test_redact_hides_secret_like_keys_recursively():
    payload = {
        "login": {"status": 200, "Set-Cookie": "SESSION=abcdef", "nested": [{"token": "t0ken"}]},
        "probe": {"status": 200},
    }
    redacted = CHECK.redact(payload)
    assert redacted["login"]["Set-Cookie"] == "<redacted>"
    assert redacted["login"]["nested"][0]["token"] == "<redacted>"
    assert redacted["probe"]["status"] == 200


def test_execute_requires_out_and_credentials(tmp_path, monkeypatch):
    path = write_config(tmp_path)
    code, _out, err = run(["--config", str(path), "--execute"])
    assert code == 2
    assert "--out" in err

    monkeypatch.delenv("MCC_SDA_USER", raising=False)
    monkeypatch.delenv("MCC_SDA_PASSWORD", raising=False)
    code, _out, err = run(["--config", str(path), "--execute", "--out", str(tmp_path / "r.json")])
    assert code == 2
    assert "MCC_SDA_USER" in err


def test_execute_writes_redacted_result(tmp_path, monkeypatch):
    path = write_config(tmp_path, idle_window_minutes=1)

    def fake_request(url, method, body, headers, timeout=30):
        return 200, {"Set-Cookie": "SESSION=secret-value; Path=/", "Content-Type": "text/plain"}, b"payload"

    monkeypatch.setattr(CHECK, "_request", fake_request)
    monkeypatch.setenv("MCC_SDA_USER", "e2e-user")
    monkeypatch.setenv("MCC_SDA_PASSWORD", "e2e-pass")
    monkeypatch.setattr(CHECK.time, "sleep", lambda seconds: None)
    out_path = tmp_path / "result.json"
    code, _out, err = run(["--config", str(path), "--execute", "--out", str(out_path)])
    assert code == 0, err
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["login"]["status"] == 200
    assert data["probe"]["sha256"]
    assert data["idle_recheck"]["heartbeat_validated"] is True
    text = out_path.read_text(encoding="utf-8")
    assert "e2e-pass" not in text
    assert "secret-value" not in text


def test_no_credentials_in_source():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "MCC_SDA_PASSWORD" in source
    for marker in ("password = \"", "password=\"", "Cookie: SESSION="):
        assert marker not in source, "源码里不得写死凭据：%s" % marker
