"""mock↔后端 DL 请求校验 / 提取取值契约（M3-11 / INH-1896 首轮评审 I2 与 Minor 1/2/3）。

`mock-api.js` 在这里是被**执行**的（`tests/utils/mock_dl_crosscheck.js` 起 node 载入
真身、逐条跑 `MC.mock.handle`），不是被 grep 的：把 `dlNumber` 换回裸 `Number()`、
把 PUT 的 rev 比较挪回 body 校验之前，源码里的中文文案可以一个字没动，而模板模式的
判决已经完全不一样——只有执行看得见（R45 / know-how 第 4 节的两个 cross-check 先例）。

每一类判决在后端一侧都有对应用例（`tests/test_api_dl.py`），本文件把同一批输入的
mock 输出钉成与后端一致的「收 / 拒 + 错误码 + 文案 + detail.field」：

* I2：extract 的 `""` / `" "` / `"0x10"` 是 `invalid`，绝不变成 0 / 16；
* Minor 1：compute 的数值只认 number 或 Python float 语法的非空字符串，`[]`/`[5]`/
  `"0x10"` 400（后端 `float()` 的判决）；
* Minor 2：extract 的 `binding` 非对象 → 400、`field_map` 非对象 → 400；
* Minor 3：PUT 先校验 body 再比 rev；缺 `If-Match` → 409 且 `detail.header="If-Match"`。

只打 JSON 源（Node 沙箱没有 DOMParser）；用例串行执行，因为 PUT 会改 mock 的内存配置。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MOCK_ORACLE = REPO_ROOT / "tests" / "utils" / "mock_dl_crosscheck.js"
NODE = shutil.which("node")

FULL_COEFFS = {k: 0 for k in ("k1", "k2", "k3", "k4", "k5", "k6", "k7", "k8", "b")}
RANGES = {"x_min": 0, "x_max": 80, "y_min": None, "y_max": None}
EXTRACT_FILE = {
    "name": "dl_contract.json",
    "text": json.dumps(
        {"dev": {"m1": "2.5", "m2": "", "m3": " ", "m4": "0x10"}}, ensure_ascii=False
    ),
}
EXTRACT_BINDING = {
    "source_file": "dl_contract.json",
    "variable_path": "root.dev",
    "field_map": {"m1": "k1", "m2": "k2", "m3": "k3", "m4": "k4"},
}


def _params(**coeffs):
    merged = dict(FULL_COEFFS)
    merged.update(coeffs)
    return {"coeffs": merged}


def _item(**coeffs):
    body = {"coeffs": dict(FULL_COEFFS)}
    body["coeffs"].update(coeffs)
    body.update(RANGES)
    return body


def _mock(cases):
    assert MOCK_ORACLE.is_file(), "oracle 不见了：{}".format(MOCK_ORACLE)
    proc = subprocess.run(
        [NODE, str(MOCK_ORACLE)],
        input=json.dumps(cases, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert "error" not in out, out.get("error")
    assert out["count"] == len(cases), out
    return {r["name"]: r for r in out["results"]}


pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="本机没有 Node：mock↔后端 DL 契约**未执法**（这一轮的绿证明不了 mock-api.js "
           "与 services/dl.py 同判；换有 node 的机器重跑本文件）",
)


def test_extract_blank_and_hex_values_are_invalid_and_never_zero():
    """I2 / C15：`""`、`" "`、`"0x10"` 都是 invalid（带 raw），不进 coeffs。

    后端对应用例：`test_api_dl.py::TestExtract::test_blank_and_hex_strings_are_invalid_and_never_become_zero`。
    修复前 mock 用 `Number(children[fld])`：`""`/`" "` 变 0、`"0x10"` 变 16，三项都标 ok。
    """
    results = _mock(
        [
            {
                "name": "extract",
                "kind": "extract",
                "route": "/dl/rp_width/extract",
                "file": EXTRACT_FILE,
                "body": {"binding": EXTRACT_BINDING},
            }
        ]
    )
    result = results["extract"]
    assert result["status"] == 200, result
    body = result["body"]
    assert body["coeffs"] == {"k1": 2.5}, body["coeffs"]
    fields = {f["field"]: f for f in body["fields"]}
    assert fields["m1"] == {"field": "m1", "coeff": "k1", "status": "ok", "value": 2.5}
    for field, coeff, raw in (("m2", "k2", ""), ("m3", "k3", " "), ("m4", "k4", "0x10")):
        assert fields[field] == {
            "field": field,
            "coeff": coeff,
            "status": "invalid",
            "raw": raw,
        }, fields[field]


def test_extract_rejects_non_object_binding_and_field_map_like_the_backend():
    """Minor 2：`binding` 缺失/null/非对象 → 400；`field_map` 非对象 → 400。

    后端对应用例：`TestExtract::test_binding_must_be_a_json_object` /
    `test_field_map_must_be_a_json_object`。修复前 mock 对缺 binding 回落 `{}` 再 404，
    对数组 `field_map` 走 `Object.keys` 得到 200。
    """
    cases = [
        {"name": "no-binding", "kind": "extract", "route": "/dl/rp_width/extract", "body": {}},
        {
            "name": "null-binding",
            "kind": "extract",
            "route": "/dl/rp_width/extract",
            "body": {"binding": None},
        },
        {
            "name": "field-map-array",
            "kind": "extract",
            "route": "/dl/rp_width/extract",
            "file": EXTRACT_FILE,
            "body": {
                "binding": dict(EXTRACT_BINDING, field_map=[]),
            },
        },
        {
            "name": "field-map-string",
            "kind": "extract",
            "route": "/dl/rp_width/extract",
            "file": EXTRACT_FILE,
            "body": {
                "binding": dict(EXTRACT_BINDING, field_map="m1=k1"),
            },
        },
    ]
    results = _mock(cases)
    for name in ("no-binding", "null-binding"):
        error = results[name]["body"]["error"]
        assert results[name]["status"] == 400, results[name]
        assert error["code"] == "INVALID_INPUT", error
        assert error["message"] == "binding 必须是对象", error
        assert error["detail"]["field"] == "binding", error
    for name in ("field-map-array", "field-map-string"):
        error = results[name]["body"]["error"]
        assert results[name]["status"] == 400, results[name]
        assert error["code"] == "INVALID_INPUT", error
        assert error["message"] == "field_map 必须是对象", error
        assert error["detail"]["field"] == "binding", error


def test_compute_numbers_reject_arrays_and_hex_strings_like_the_backend():
    """Minor 1：`float([])`、`float([5])`、`float("0x10")` 后端都是 400。

    修复前 `isFinite(Number(v))` 让它们变成 0 / 5 / 16。对照组是字符串 `"2.5"`
    （Python float 语法）与 number，必须照旧放行（mock 不许收得比后端更紧）。
    """
    def compute(name, values, params):
        return {
            "name": name,
            "kind": "compute",
            "route": "/dl/rp_width/compute",
            "body": {"mode": "forward", "values": values, "params": params},
        }

    cases = [
        compute("coeff-array", [1], _params(k1=[])),
        compute("coeff-number-array", [1], _params(k1=[5])),
        compute("coeff-hex-string", [1], _params(k1="0x10")),
        compute("value-array", [[]], _params(k1=1)),
        compute("value-number-array", [[5]], _params(k1=1)),
        compute("value-hex-string", ["0x10"], _params(k1=1)),
        compute("coeff-float-string", [1], _params(k1="2.5")),
        compute("value-number", [2], _params(k1=1, b=1)),
    ]
    results = _mock(cases)
    for name in (
        "coeff-array",
        "coeff-number-array",
        "coeff-hex-string",
    ):
        error = results[name]["body"]["error"]
        assert results[name]["status"] == 400, (name, results[name])
        assert error["message"] == "系数必须是有限数值", error
    for name in ("value-array", "value-number-array", "value-hex-string"):
        error = results[name]["body"]["error"]
        assert results[name]["status"] == 400, (name, results[name])
        assert error["message"] == "输入值必须是有限数值", error
    assert results["coeff-float-string"]["status"] == 200, results["coeff-float-string"]
    assert results["coeff-float-string"]["body"]["results"][0]["y"] == 2.5
    assert results["value-number"]["status"] == 200, results["value-number"]
    assert results["value-number"]["body"]["results"][0]["y"] == 3


def test_put_validates_body_before_rev_and_reports_the_missing_header():
    """Minor 3：与后端同序——缺头 409（带 detail.header）→ body 400 → rev 409。

    修复前 mock 先比 rev：rev 过期 + 系数非法会报 409，后端报 400；缺头还没有
    `detail.header`（前端/测试无法区分「没带」与「过期」）。
    """
    valid = _item(k1=4)
    valid["name"] = "计算RP能量"
    valid["binding"] = {"source_file": "", "variable_path": "", "field_map": {}}
    no_range = dict(valid)
    del no_range["x_min"]

    cases = [
        {
            "name": "no-header",
            "kind": "put",
            "route": "/dl/rp_energy",
            "body": _item(k1="abc"),
        },
        {
            "name": "stale-header-bad-coeff",
            "kind": "put",
            "route": "/dl/rp_energy",
            "headers": {"If-Match": "bogus"},
            "body": _item(k1="abc"),
        },
        {
            "name": "stale-header-missing-range",
            "kind": "put",
            "route": "/dl/rp_energy",
            "headers": {"If-Match": "bogus"},
            "body": no_range,
        },
        {
            "name": "current-rev-missing-range",
            "kind": "put",
            "route": "/dl/rp_energy",
            "withRev": True,
            "body": no_range,
        },
        {
            "name": "stale-header-valid-body",
            "kind": "put",
            "route": "/dl/rp_energy",
            "headers": {"If-Match": "bogus"},
            "body": valid,
        },
        {
            "name": "current-rev-valid-body",
            "kind": "put",
            "route": "/dl/rp_energy",
            "withRev": True,
            "body": valid,
        },
    ]
    results = _mock(cases)

    missing = results["no-header"]
    assert missing["status"] == 409, missing
    assert missing["body"]["error"]["code"] == "REVISION_CONFLICT", missing
    assert missing["body"]["error"]["detail"]["header"] == "If-Match", missing
    assert missing["body"]["error"]["message"] == "缺少 If-Match 头：请先 GET /dl 取得当前 rev"

    for name in ("stale-header-bad-coeff",):
        error = results[name]["body"]["error"]
        assert results[name]["status"] == 400, (name, results[name])
        assert error["code"] == "INVALID_INPUT", error
        assert error["message"] == "系数必须是有限数值", error

    for name in ("stale-header-missing-range", "current-rev-missing-range"):
        error = results[name]["body"]["error"]
        assert results[name]["status"] == 400, (name, results[name])
        assert error["code"] == "INVALID_INPUT", error
        assert error["message"].startswith("保存时必须提供完整的范围字段"), error
        assert error["detail"]["field"] == "x_min", error

    stale = results["stale-header-valid-body"]
    assert stale["status"] == 409, stale
    assert stale["body"]["error"]["code"] == "REVISION_CONFLICT", stale
    assert "header" not in (stale["body"]["error"]["detail"] or {}), stale

    ok = results["current-rev-valid-body"]
    assert ok["status"] == 200, ok
    assert ok["body"]["item"]["coeffs"]["k1"] == 4
