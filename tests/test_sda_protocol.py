"""Offline contract tests for the synthetic SDA fixtures (M16-02).

These tests never touch a network, a browser, or production data. They only
prove that the checked-in synthetic fixtures are structurally usable and do
not contain secret material. Real SDA evidence remains BLOCKED-EXTERNAL.
"""

import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parent / "fixtures" / "sda"

FORBIDDEN_KEYS = {
    "password",
    "passwd",
    "cookie",
    "set-cookie",
    "csrf",
    "authorization",
    "token",
    "secret",
    "session_id",
    "har",
}

FORBIDDEN_VALUE_MARKERS = (
    "Bearer ",
    "Basic ",
    "password=",
    "Cookie:",
    "Set-Cookie:",
)


def _load(name):
    path = ROOT / name / "cases.json"
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _walk(value, path="$"):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, path + "." + str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, path + "[" + str(index) + "]")


def _case_map(doc):
    return {case["case"]: case for case in doc["cases"]}


def test_every_fixture_is_synthetic_and_versioned():
    for name in ("catalog", "table", "file", "login", "heartbeat"):
        doc = _load(name)
        assert doc["fixture_version"] == 1, name
        assert doc["synthetic"] is True, name
        assert len(doc["cases"]) >= 3, name
        assert "not real SDA data" in doc["source"], name


def test_catalog_table_file_have_success_auth_and_business_error():
    for name in ("catalog", "table", "file"):
        cases = _case_map(_load(name))
        assert set(cases) == {"success", "auth_failed", "business_error"}, name

        success = cases["success"]
        assert success["http_status"] == 200, name
        assert success["body"]["ok"] is True, name
        assert success["body"]["resource_kind"] == name, name

        auth = cases["auth_failed"]
        assert auth["http_status"] == 500, name
        assert auth["body"] is None, name

        error = cases["business_error"]
        assert error["http_status"] == 200, name
        assert error["body"]["ok"] is False, name
        assert error["body"]["error"]["code"], name


def test_file_success_fixture_has_verifiable_content():
    case = _case_map(_load("file"))["success"]
    file_body = case["body"]["file"]
    raw = base64.b64decode(file_body["content_base64"], validate=True)
    assert len(raw) == file_body["size"]
    assert hashlib.sha256(raw).hexdigest() == file_body["content_sha256"]
    assert file_body["content_type"] == "application/xml"


def test_login_fixtures_cover_success_bad_password_and_changed_flow():
    cases = _case_map(_load("login"))
    assert set(cases) == {"login_success", "bad_credentials", "flow_changed"}

    success = cases["login_success"]["body"]
    assert success["ok"] is True
    assert success["protected_capability"] == "table_read"
    assert success["credentials_source"] == "runtime_secret_ref"

    for case_name, code in (
        ("bad_credentials", "BAD_CREDENTIALS"),
        ("flow_changed", "LOGIN_FLOW_CHANGED"),
    ):
        body = cases[case_name]["body"]
        assert body["ok"] is False
        assert body["locked"] is True
        assert body["error"]["code"] == code


def test_heartbeat_fixtures_never_claim_validation():
    cases = _case_map(_load("heartbeat"))
    assert set(cases) == {
        "heartbeat_renewed",
        "heartbeat_not_renewed",
        "heartbeat_expired",
    }

    renewed = cases["heartbeat_renewed"]["body"]
    assert renewed["renewal_observed"] is True
    assert renewed["cookie_rotated"] is True
    assert renewed["heartbeat_validated"] is False

    not_renewed = cases["heartbeat_not_renewed"]["body"]
    assert not_renewed["renewal_observed"] is False
    assert not_renewed["cookie_rotated"] is False
    assert not_renewed["heartbeat_validated"] is False

    expired = cases["heartbeat_expired"]
    assert expired["http_status"] == 500
    assert expired["body"] is None


def test_fixtures_do_not_contain_secret_shaped_material():
    for name in ("catalog", "table", "file", "login", "heartbeat"):
        doc = _load(name)
        for path, value in _walk(doc):
            if isinstance(value, dict):
                for key in value:
                    assert key not in FORBIDDEN_KEYS, (name, path, key)
            if isinstance(value, str):
                for marker in FORBIDDEN_VALUE_MARKERS:
                    assert marker not in value, (name, path, marker)
                assert "http://" not in value, (name, path)
                assert "https://" not in value, (name, path)
