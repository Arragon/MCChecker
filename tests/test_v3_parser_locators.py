"""M11-03 精确 XML/JSON 定位与安全解析（INH-2132）。"""

from __future__ import annotations

import json

import pytest

from authz_fixtures import as_role, make_server, seed_roles

from app.core.errors import PARSE_FAILED, TOO_LARGE, AppError
from app.services import config_v3_parse as parse


def _rows(items):
    return {(row["display_path"], row["value"]) for row in items}


def test_xml_prefixes_with_the_same_uri_match_the_same_locator():
    a = b'<a:R xmlns:a="urn:cfg"><a:item>1</a:item></a:R>'
    b = b'<b:R xmlns:b="urn:cfg"><b:item>2</b:item></b:R>'
    ra, rb = parse.xml_locators(a), parse.xml_locators(b)
    assert [(r["display_path"], parse.locator_key(r)) for r in ra] == [
        (r["display_path"], parse.locator_key(r)) for r in rb
    ]
    assert ra[0]["display_path"] == "/R[1]/item[1]"


def test_xml_same_name_siblings_use_occurrence_and_mixed_text_stays_separate():
    rows = parse.xml_locators(
        b'<R><item>first</item><item>second</item><mixed>before<a>1</a></mixed></R>'
    )
    items = [r for r in rows if r["display_path"] in ("/R[1]/item[1]", "/R[1]/item[2]")]
    assert [r["value"] for r in items] == ["first", "second"]
    mixed = [r for r in rows if r["display_path"].startswith("/R[1]/mixed[1]")]
    assert [(r["display_path"], r["locator"]["kind"], r["value"]) for r in mixed] == [
        ("/R[1]/mixed[1]/a[1]", "element", "1"),
        ("/R[1]/mixed[1]/#text", "element_text", "before"),
    ]


def test_xml_attributes_and_dtd_are_handled():
    rows = parse.xml_locators(b'<R><p min="0" max="10">5</p></R>')
    by_path = {r["display_path"]: r for r in rows}
    assert by_path["/R[1]/p[1]/@min"]["value"] == "0"
    assert by_path["/R[1]/p[1]"]["value"] == "5"
    with pytest.raises(AppError) as exc:
        parse.xml_locators(b'<!DOCTYPE R [<!ENTITY x "y">]><R>&x;</R>')
    assert exc.value.code == PARSE_FAILED


def test_xml_depth_limit():
    body = "".join("<n>" for _ in range(parse.MAX_DEPTH + 1))
    body += "0" + "".join("</n>" for _ in range(parse.MAX_DEPTH + 1))
    with pytest.raises(AppError) as exc:
        parse.xml_locators(("<R>" + body + "</R>").encode())
    assert exc.value.code == TOO_LARGE


def test_json_rfc6901_escapes_and_typed_values():
    data = {"a/b": {"m~n": 0, "false": False, "empty": "", "null": None}}
    rows = parse.json_locators(json.dumps(data).encode())
    by_ptr = {r["locator"]["pointer"]: r["value"] for r in rows}
    assert by_ptr == {
        "/a~1b/m~0n": 0,
        "/a~1b/false": False,
        "/a~1b/empty": "",
        "/a~1b/null": None,
    }
    assert parse.locator_key(rows[0]).startswith("json:/a~1b/")


def test_json_rejects_duplicates_nan_and_depth():
    for raw, code in (
        (b'{"a":1,"a":2}', PARSE_FAILED),
        (b'{"a":NaN}', PARSE_FAILED),
        (b'{"a":Infinity}', PARSE_FAILED),
    ):
        with pytest.raises(AppError) as exc:
            parse.json_locators(raw)
        assert exc.value.code == code
    nested = b"[" * (parse.MAX_DEPTH + 2) + b"0" + b"]" * (parse.MAX_DEPTH + 2)
    with pytest.raises(AppError) as exc:
        parse.json_locators(nested)
    assert exc.value.code == TOO_LARGE


def test_page_is_lazy_and_limited():
    items = [{"n": i} for i in range(10)]
    assert [r["n"] for r in parse.page(items, 3, 2)] == [3, 4]
    assert parse.page(iter(items), 0, 100) == items


def test_unsafe_upload_is_rejected_without_writing():
    seed_roles("parse")
    with make_server() as server:
        for name, data, content_type in (
            ("dup.json", b'{"a":1,"a":2}', "application/json"),
            ("nan.json", b'{"a":NaN}', "application/json"),
            ("ent.xml", b'<!DOCTYPE R [<!ENTITY x "y">]><R>&x;</R>', "application/xml"),
        ):
            status, _h, raw = as_role(server, "admin").request(
                "PUT", "/api/v1/p/default/files/%s" % name, body=data,
                headers={"X-Workspace-Id": "default", "Content-Type": content_type},
            )
            assert status == 422, (name, raw)
            assert b"PARSE_FAILED" in raw
        status, _h, body = as_role(server, "visitor").json(
            "GET", "/api/v1/p/default/files", headers={"X-Workspace-Id": "default"}
        )
        assert status == 200 and body["items"] == [], "解析失败不得留下文件"
