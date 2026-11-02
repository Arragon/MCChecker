"""M16-16 synthetic SDA adapter tests."""

import json
from pathlib import Path

import pytest

from collector import contracts
from collector.sda_adapter import SdaAdapter, SdaResponse

ROOT = Path(__file__).parent / "fixtures" / "sda"
ORIGIN = "https://sda.example.invalid"


def fixture(kind, name):
    with (ROOT / kind / "cases.json").open("r", encoding="utf-8") as handle:
        doc = json.load(handle)
    return next(case for case in doc["cases"] if case["case"] == name)


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, headers=None, body=None, allow_redirects=False):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": body,
                "allow_redirects": allow_redirects,
            }
        )
        return self.response


def make_response(kind, name, final_url=None):
    case = fixture(kind, name)
    body = b"" if case.get("body") is None else json.dumps(case["body"]).encode("utf-8")
    return SdaResponse(
        status=case["http_status"],
        headers=case.get("headers") or {},
        body=body,
        final_url=final_url or ORIGIN + contracts.PATHS[kind],
    )


def adapter_for(kind, name, final_url=None):
    session = FakeSession(make_response(kind, name, final_url=final_url))
    return SdaAdapter(ORIGIN, session), session


def test_success_catalog_table_file_and_session_validity():
    catalog, catalog_session = adapter_for("catalog", "success")
    result = catalog.read_catalog()
    assert result.status == "succeeded"
    assert result.session_valid is False
    assert result.data["items"][0]["resource_ref"] == "res-catalog-001"
    assert catalog_session.calls[0]["method"] == "GET"
    assert catalog_session.calls[0]["url"] == ORIGIN + "/synthetic/catalog"

    table, table_session = adapter_for("table", "success")
    result = table.read_table("res-1")
    assert result.status == "succeeded"
    assert result.session_valid is True
    assert result.data["table_id"] == "tbl-001"
    assert table_session.calls[0]["method"] == "POST"
    assert table_session.calls[0]["body"] == {"resource_ref": "res-1"}
    assert table_session.calls[0]["url"] == ORIGIN + "/synthetic/table"

    file_adapter, file_session = adapter_for("file", "success")
    result = file_adapter.read_file("file-001")
    assert result.status == "succeeded"
    assert result.session_valid is True
    assert result.data["resource_ref"] == "file-001"
    assert file_session.calls[0]["url"] == ORIGIN + "/synthetic/file"


def test_auth_and_business_errors():
    auth, _ = adapter_for("table", "auth_failed")
    result = auth.read_table("res-1")
    assert result.status == "failed"
    assert result.reason_code == contracts.REASON_AUTH_FAILED

    business, _ = adapter_for("table", "business_error")
    result = business.read_table("res-1")
    assert result.status == "failed"
    assert result.reason_code == contracts.REASON_BUSINESS_ERROR
    assert result.message == "TABLE_NOT_FOUND"


def test_login_html_wrong_kind_and_redirect_rejected():
    session = FakeSession(
        SdaResponse(
            200,
            {"content-type": "text/html"},
            b"<html>login</html>",
            ORIGIN + "/synthetic/table",
        )
    )
    result = SdaAdapter(ORIGIN, session).read_table("res-1")
    assert result.reason_code == contracts.REASON_INVALID_PAYLOAD

    wrong = {
        "ok": True,
        "resource_kind": "table",
        "table": {"table_id": "tbl-1"},
    }
    session = FakeSession(
        SdaResponse(200, {"content-type": "application/json"}, json.dumps(wrong).encode(), ORIGIN + "/synthetic/catalog")
    )
    result = SdaAdapter(ORIGIN, session).read_catalog()
    assert result.reason_code == contracts.REASON_INVALID_PAYLOAD

    redirect, _ = adapter_for("table", "success", final_url="https://evil.example.com/synthetic/table")
    result = redirect.read_table("res-1")
    assert result.reason_code == contracts.REASON_REDIRECT_BLOCKED


def test_invalid_resource_ref_and_unknown_capability():
    adapter, _ = adapter_for("table", "success")
    result = adapter.read_table("http://evil.example/x")
    assert result.reason_code == contracts.REASON_INVALID_PAYLOAD

    result = adapter.probe("run_arbitrary_js")
    assert result.reason_code == contracts.REASON_PROTOCOL_MISMATCH

    result = adapter.probe("table_read")
    assert result.status == "succeeded"


def test_origin_validation():
    for bad in (
        "http://sda.example.invalid",
        "https://user:pass@sda.example.invalid",
        "https://sda.example.invalid/path",
        "https://sda.example.invalid:8443",
    ):
        with pytest.raises(ValueError):
            SdaAdapter(bad, FakeSession(make_response("catalog", "success")))
