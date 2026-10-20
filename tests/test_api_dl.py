"""DL 试算、提取预览与条件保存（卡 11 · M3-11 / INH-1896；规范 §7.11、UC-38、UC-39）。

口径（全部打真实 `MCServer` + 真 HTTP，不 mock 传输层）：

1. **GET /dl**：全部角色；`rev` = `dltool_config.json` 的 sha1 前 12 位（文件不存在时
   对默认配置的规范字节取哈希），三个计算项齐全；`editing` 读到后忽略；**读路径不建文件、
   不递增修订号**（`tests/data_guard.py` 另有会话级守卫，这里钉的是单次请求）。
2. **compute / extract**：全部角色、纯计算/预览。三种 mode 的响应形状逐字按 §7.11
   表格（forward 只有 `results`，reverse 带 `exhaustive: false`，multi_check 只有
   `has_multi`/`cases`）；不写文件、五个修订分量一个都不动（R20/R21 的同口径）。
3. **extract**：`variable_path` 是旧节点 ID；`missing` 不把它放进 `coeffs`、
   `invalid` 带 `raw`；两种都**不返回 0**（C15），且绝不回写配置。
4. **PUT /dl/{item}**：仅部署者；必须 `If-Match`（R17：缺失 → 409
   `REVISION_CONFLICT`，不是 400/428；`views/dltool.js:84` 永远带该头、`:94` 只处理
   `REVISION_CONFLICT`）；过期/并发 → 409 且文件一个字节不动；成功 → `{rev, item}`、
   `editing` 写 `false`、范围空值保留 `null`、字段集合不变、`settings` 只 bump 一次。
5. **`trans_coeff=0`**（M3-10 遗留的「validate 合法但 reverse/multi_check 会 ÷0」）：
   按裁定 (a) 在服务层拦成 400 `INVALID_INPUT`——三种 mode 与 save 都要 400，且在
   写盘之前；裸 `ZeroDivisionError` 不许漏到 HTTP。
6. **四条路由已注册**：路由表自省，不靠「404 长得不像 500」猜。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from copy import deepcopy

import pytest
from authz_fixtures import as_role, make_server, seed_roles, write_target

from app.config import Settings
from app.core import dltool, storage
from app.server.router import build_router
from app.services import revision

ITEMS = ("pp_energy", "rp_energy", "rp_width")
ROLES = ("visitor", "member", "admin", "deployer")

WEB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "web"
)

DL_URL = "/api/v1/p/%s/dl"
ITEM_URL = "/api/v1/p/%s/dl/%s"
COMPUTE_URL = "/api/v1/p/%s/dl/%s/compute"
EXTRACT_URL = "/api/v1/p/%s/dl/%s/extract"

SOURCE = "dl_source.xml"
SOURCE_DOC = (
    "<R>\n"
    '  <PPParams min="0" max="10">\n'
    "    <m1>2</m1>\n"
    "    <m2>bad</m2>\n"
    "    <m3>NaN</m3>\n"
    "  </PPParams>\n"
    "</R>\n"
).encode("utf-8")
SOURCE_VAR = "root/R/PPParams"

TRANS_ZERO = "传输系数不能为 0"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _parts(pid):
    """五个修订分量的快照（读接口/失败写/compute/extract 一律不许动它）。"""
    return revision.snapshot(pid)["parts"]


def _config_path(pid):
    with storage.use_profile(pid):
        return dltool._get_config_file()


def _file_state(pid):
    """`dltool_config.json` 的 (sha1, mtime_ns)；文件不存在返回 None。"""
    path = _config_path(pid)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        raw = fh.read()
    return hashlib.sha1(raw).hexdigest(), os.stat(path).st_mtime_ns


def _rev_of_file(pid):
    state = _file_state(pid)
    return None if state is None else state[0][:12]


def _default_rev():
    """文件不存在时的 rev：默认配置按 `save_config` 的同一份规范字节取 sha1。"""
    raw = json.dumps(dltool.DEFAULT_CONFIG, ensure_ascii=False, indent=2).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def _seed_config(pid, editing=False, mutate=None):
    """落一份完整配置；返回它的 rev（sha1 前 12 位）。"""
    cfg = deepcopy(dltool.DEFAULT_CONFIG)
    cfg["editing"] = editing
    if mutate is not None:
        mutate(cfg)
    with storage.use_profile(pid):
        dltool.save_config(cfg)
    return _rev_of_file(pid)


def _load_item(pid, kind="rp_energy"):
    with storage.use_profile(pid):
        return deepcopy(dltool.load_config()["items"][kind])


def _item(kind="rp_energy", coeffs=None, x_min=None, x_max=None, y_min=None,
          y_max=None, trans=1.0, power=1.0):
    """一份完整的计算项（请求里的 `params` / PUT body）。"""
    base = deepcopy(dltool.DEFAULT_CONFIG["items"][kind])
    if coeffs:
        base["coeffs"].update(coeffs)
    base.update({"x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max})
    if kind == "pp_energy":
        base.update({"trans_coeff": trans, "power_conv_coeff": power})
    return base


def _squat(kind="rp_width", **over):
    """(x-3)²：切触重根+多解，与 `tests/test_dltool_multi.py` 同一组系数。"""
    return _item(
        kind=kind,
        coeffs={"k1": -6.0, "k2": 1.0, "b": 9.0},
        x_min=-10.0,
        x_max=10.0,
        **over
    )


def _route_templates():
    settings = Settings(host="127.0.0.1", port=0, data_dir=storage.DATA_DIR, web_dir=WEB_DIR)
    out = set()
    for method, regex, _names, _handler in build_router(settings)._routes:
        template = re.sub(
            r"\(\?P<(\w+)>\[\^/\]\+\)", lambda m: "{%s}" % m.group(1), regex.pattern
        ).strip("^$")
        out.add((method, template))
    return out


# ---------------------------------------------------------------------------
# GET /dl
# ---------------------------------------------------------------------------


class TestGetState:
    def test_four_roles_get_rev_and_three_items_without_creating_the_file(self):
        pid = seed_roles("dlget")
        path = _config_path(pid)
        assert not os.path.exists(path), "前提：还没人保存过 DL 配置"
        with make_server() as server:
            for role in ROLES:
                status, _h, body = as_role(server, role).json("GET", DL_URL % pid)
                assert status == 200, (role, body)
                assert body["rev"] == _default_rev(), (role, body["rev"])
                assert set(body["items"]) == set(ITEMS), body
                assert "editing" not in body, "editing 读到后忽略（C15，不做共享编辑态）"
                pp = body["items"]["pp_energy"]
                assert set(pp) == {
                    "name",
                    "trans_coeff",
                    "power_conv_coeff",
                    "coeffs",
                    "binding",
                    "x_min",
                    "x_max",
                    "y_min",
                    "y_max",
                }, pp
                assert pp["coeffs"] == dltool.DEFAULT_CONFIG["items"]["pp_energy"]["coeffs"]
                assert pp["trans_coeff"] == 1.0 and pp["power_conv_coeff"] == 1.0
        assert not os.path.exists(path), "GET /dl 不得创建 dltool_config.json"

    def test_get_ignores_editing_and_never_bumps_or_writes(self):
        pid = seed_roles("dlget2")
        _seed_config(pid, editing=True)
        before_file = _file_state(pid)
        before_parts = _parts(pid)
        with make_server() as server:
            status, _h, body = as_role(server, "visitor").json("GET", DL_URL % pid)
            assert status == 200, body
            assert "editing" not in body
            assert body["rev"] == before_file[0][:12]
        assert _file_state(pid) == before_file, "GET 不得改写配置（含 mtime）"
        assert _parts(pid) == before_parts

    def test_rev_tracks_the_file_bytes(self):
        pid = seed_roles("dlget3")
        first = _seed_config(pid)
        assert first == _rev_of_file(pid)

        def mutate(cfg):
            cfg["items"]["rp_width"]["coeffs"]["k2"] = 3.5

        second = _seed_config(pid, mutate=mutate)
        assert second != first
        with make_server() as server:
            status, _h, body = as_role(server, "member").json("GET", DL_URL % pid)
            assert status == 200
            assert body["rev"] == second
            assert body["items"]["rp_width"]["coeffs"]["k2"] == 3.5


# ---------------------------------------------------------------------------
# POST /dl/{item}/compute
# ---------------------------------------------------------------------------


class TestCompute:
    def test_forward_keeps_out_of_range_rows_with_input_index(self):
        pid = seed_roles("dlcompute")
        params = _item(coeffs={"k1": 2.0, "b": 1.0}, x_min=0.0, x_max=10.0, y_max=15.0)
        before = _parts(pid)
        with make_server() as server:
            status, _h, body = as_role(server, "visitor").json(
                "POST",
                COMPUTE_URL % (pid, "rp_width"),
                {"mode": "forward", "values": [-1, 3, 9], "params": params},
            )
        assert status == 200, body
        assert set(body) == {"results"}, "§7.11：forward 只回 results"
        rows = body["results"]
        assert [r["input_index"] for r in rows] == [0, 1, 2]
        assert [r["status"] for r in rows] == [
            "out_of_x_range",
            "ok",
            "out_of_y_range",
        ]
        assert rows[0] == {"input_index": 0, "x": -1.0, "status": "out_of_x_range"}
        assert rows[1]["y"] == 7.0
        assert rows[2]["y_base"] == 19.0 and rows[2]["y"] == 19.0
        assert _parts(pid) == before, "compute 是纯计算，不许 bump"
        assert _file_state(pid) is None, "compute 不许创建配置"

    def test_reverse_multiple_solutions_are_all_returned_with_exhaustive_false(self):
        pid = seed_roles("dlcompute2")
        params = _squat()
        with make_server() as server:
            status, _h, body = as_role(server, "member").json(
                "POST",
                COMPUTE_URL % (pid, "rp_width"),
                {"mode": "reverse", "values": [0, 4], "params": params},
            )
        assert status == 200, body
        assert set(body) == {"results", "exhaustive"}, "§7.11：reverse 只有 results+exhaustive"
        assert body["exhaustive"] is False
        rows = body["results"]
        assert [r["input_index"] for r in rows] == [0, 1]
        assert [r["status"] for r in rows] == ["single", "multiple"]
        assert rows[0]["x_values"] == pytest.approx([3.0], abs=1e-6)
        # 多解全给、升序、不自动选最优（前端让用户逐行手选）
        assert rows[1]["x_values"] == pytest.approx([1.0, 5.0], abs=1e-6)
        assert len(rows[1]["residuals"]) == 2
        assert rows[0]["y_base"] == 0.0 and rows[1]["y_base"] == 4.0

    def test_multi_check_matches_engine_and_contract_shape(self):
        pid = seed_roles("dlcompute3")
        params = _squat()
        expected = dltool.multi_check(params, "rp_width")
        with make_server() as server:
            status, _h, body = as_role(server, "admin").json(
                "POST",
                COMPUTE_URL % (pid, "rp_width"),
                {"mode": "multi_check", "params": params},
            )
        assert status == 200, body
        assert set(body) == {"has_multi", "cases"}, "§7.11：multi_check 只有两键"
        assert body == expected
        assert body["has_multi"] is True
        assert body["cases"][0]["y"] == pytest.approx(8.45, abs=1e-12)

    def test_pp_energy_applies_transmission_and_power_conversion(self):
        pid = seed_roles("dlcompute4")
        params = _item(
            kind="pp_energy",
            coeffs={"k1": 2.0, "b": 1.0},
            trans=2.0,
            power=0.5,
        )
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").json(
                "POST",
                COMPUTE_URL % (pid, "pp_energy"),
                {"mode": "forward", "values": [1], "params": params},
            )
        assert status == 200, body
        row = body["results"][0]
        assert row["y_base"] == 3.0 and row["y"] == 12.0

    def test_all_roles_may_compute(self):
        pid = seed_roles("dlcompute5")
        params = _item(coeffs={"k1": 1.0}, x_min=-10.0, x_max=10.0)
        with make_server() as server:
            for role in ROLES:
                status, _h, body = as_role(server, role).json(
                    "POST",
                    COMPUTE_URL % (pid, "rp_width"),
                    {"mode": "forward", "values": [1], "params": params},
                )
                assert status == 200, (role, body)
                assert body["results"][0]["y"] == 1.0

    @pytest.mark.parametrize(
        "payload",
        [
            {"mode": "bogus", "values": [1], "params": _item(coeffs={"k1": 1.0})},
            {"mode": "forward", "values": ["abc"], "params": _item(coeffs={"k1": 1.0})},
            {"mode": "forward", "values": [None], "params": _item(coeffs={"k1": 1.0})},
            {"mode": "forward", "values": [True], "params": _item(coeffs={"k1": 1.0})},
            {"mode": "forward", "values": [float("nan")], "params": _item(coeffs={"k1": 1.0})},
            {"mode": "forward", "values": [1], "params": {"coeffs": {"k1": 1.0}}},
            {"mode": "forward", "values": [1], "params": {"coeffs": {"k1": None}}},
            {"mode": "forward", "values": [1], "params": _item(coeffs={"k1": 1.0}, x_min="abc")},
        ],
    )
    def test_bad_inputs_are_400_and_stay_pure(self, payload):
        pid = seed_roles("dlcompute6")
        before = _parts(pid)
        with make_server() as server:
            status, _h, body = as_role(server, "visitor").json(
                "POST", COMPUTE_URL % (pid, "rp_width"), payload
            )
        assert status == 400, body
        assert body["error"]["code"] == "INVALID_INPUT", body
        assert _parts(pid) == before
        assert _file_state(pid) is None

    def test_unknown_item_is_404(self):
        pid = seed_roles("dlcompute7")
        with make_server() as server:
            status, _h, body = as_role(server, "visitor").json(
                "POST",
                COMPUTE_URL % (pid, "nope"),
                {"mode": "forward", "values": [1], "params": _item(coeffs={"k1": 1.0})},
            )
        assert status == 404, body
        assert body["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# POST /dl/{item}/extract
# ---------------------------------------------------------------------------


class TestExtract:
    def _seed(self, pid):
        write_target(pid, SOURCE, SOURCE_DOC)
        # 原系数非零：提取失败时若被写成 0，「保持原值」这条就会红。
        _seed_config(
            pid,
            mutate=lambda cfg: cfg["items"]["rp_energy"]["coeffs"].update(
                {"k2": 7.0, "k3": 8.0, "k5": 9.0}
            ),
        )

    def test_missing_and_invalid_never_enter_coeffs_and_keep_original_values(self):
        pid = seed_roles("dlextract")
        self._seed(pid)
        before_file = _file_state(pid)
        before_parts = _parts(pid)
        binding = {
            "source_file": SOURCE,
            "variable_path": SOURCE_VAR,
            "field_map": {
                "m1": "k1",
                "m2": "k2",
                "m3": "k3",
                "@min": "k4",
                "absent": "k5",
            },
        }
        with make_server() as server:
            status, _h, body = as_role(server, "visitor").json(
                "POST", EXTRACT_URL % (pid, "rp_energy"), {"binding": binding}
            )
            assert status == 200, body
            assert set(body) == {"coeffs", "fields"}, body
            # 只有成功提取的键；missing/invalid 一律不出现，更不许有 0。
            assert body["coeffs"] == {"k1": 2.0, "k4": 0.0}, body["coeffs"]
            fields = {f["field"]: f for f in body["fields"]}
            assert list(fields) == ["m1", "m2", "m3", "@min", "absent"]
            assert fields["m1"] == {
                "field": "m1", "coeff": "k1", "status": "ok", "value": 2.0,
            }
            assert fields["m2"] == {
                "field": "m2", "coeff": "k2", "status": "invalid", "raw": "bad",
            }
            assert fields["m3"] == {
                "field": "m3", "coeff": "k3", "status": "invalid", "raw": "NaN",
            }
            assert fields["@min"] == {
                "field": "@min", "coeff": "k4", "status": "ok", "value": 0.0,
            }
            assert fields["absent"] == {
                "field": "absent", "coeff": "k5", "status": "missing",
            }

            # 预览不落盘：GET 回来仍是原来的非零系数（不是提取值、也不是 0）。
            _s, _h, state = as_role(server, "visitor").json("GET", DL_URL % pid)
            coeffs = state["items"]["rp_energy"]["coeffs"]
            assert coeffs["k2"] == 7.0 and coeffs["k3"] == 8.0 and coeffs["k5"] == 9.0
        assert _file_state(pid) == before_file, "extract 只预览，一个字节都不许写"
        assert _parts(pid) == before_parts, "extract 不许 bump"

    def test_json_keys_are_extracted_too(self):
        pid = seed_roles("dlextract2")
        write_target(pid, "dl.json", b'{"a": {"m1": 3.5, "m2": "x"}}')
        binding = {
            "source_file": "dl.json",
            "variable_path": "root.a",
            "field_map": {"m1": "k1", "m2": "k2"},
        }
        with make_server() as server:
            status, _h, body = as_role(server, "member").json(
                "POST", EXTRACT_URL % (pid, "rp_width"), {"binding": binding}
            )
        assert status == 200, body
        assert body["coeffs"] == {"k1": 3.5}
        statuses = {f["field"]: f["status"] for f in body["fields"]}
        assert statuses == {"m1": "ok", "m2": "invalid"}

    def test_missing_source_file_is_404(self):
        pid = seed_roles("dlextract3")
        binding = {
            "source_file": "nope.xml",
            "variable_path": SOURCE_VAR,
            "field_map": {"m1": "k1"},
        }
        with make_server() as server:
            status, _h, body = as_role(server, "visitor").json(
                "POST", EXTRACT_URL % (pid, "rp_width"), {"binding": binding}
            )
        assert status == 404, body
        assert body["error"]["code"] == "NOT_FOUND"

    def test_missing_variable_path_is_404_with_field(self):
        pid = seed_roles("dlextract4")
        write_target(pid, SOURCE, SOURCE_DOC)
        binding = {
            "source_file": SOURCE,
            "variable_path": "root/R/Nope",
            "field_map": {"m1": "k1"},
        }
        with make_server() as server:
            status, _h, body = as_role(server, "visitor").json(
                "POST", EXTRACT_URL % (pid, "rp_width"), {"binding": binding}
            )
        assert status == 404, body
        assert body["error"]["detail"]["field"] == "variable_path", body

    def test_unknown_item_is_404(self):
        pid = seed_roles("dlextract5")
        with make_server() as server:
            status, _h, body = as_role(server, "visitor").json(
                "POST",
                EXTRACT_URL % (pid, "nope"),
                {"binding": {"source_file": SOURCE, "variable_path": SOURCE_VAR}},
            )
        assert status == 404, body


# ---------------------------------------------------------------------------
# PUT /dl/{item}
# ---------------------------------------------------------------------------


def _valid_payload(pid, kind="rp_energy", **coeffs):
    payload = _load_item(pid, kind)
    payload["coeffs"].update(coeffs)
    return payload


class TestSaveItem:
    def test_only_the_deployer_may_save_and_non_deployers_touch_nothing(self):
        pid = seed_roles("dlsave")
        rev = _seed_config(pid)
        before_file = _file_state(pid)
        before_parts = _parts(pid)
        payload = _valid_payload(pid, k1=5.0)
        with make_server() as server:
            for role in ("visitor", "member", "admin"):
                status, _h, body = as_role(server, role).json(
                    "PUT",
                    ITEM_URL % (pid, "rp_energy"),
                    payload,
                    headers={"If-Match": rev},
                )
                assert status == 403, (role, body)
                assert body["error"]["detail"]["action"] == "settings_write", body
        assert _file_state(pid) == before_file, "403 之后配置一字节都不许动"
        assert _parts(pid) == before_parts, "失败写不递增"

    def test_missing_if_match_is_409_revision_conflict(self):
        """R17：缺 `If-Match` → 409（`dltool.js:84` 永远带头、`:94` 只处理
        `REVISION_CONFLICT`；不用 400/428）。"""
        pid = seed_roles("dlsave2")
        _seed_config(pid)
        before_file = _file_state(pid)
        before_parts = _parts(pid)
        payload = _valid_payload(pid, k1=5.0)
        with make_server() as server:
            for headers in ({}, {"If-Match": ""}):
                status, _h, body = as_role(server, "deployer").json(
                    "PUT", ITEM_URL % (pid, "rp_energy"), payload, headers=headers
                )
                assert status == 409, (headers, body)
                assert body["error"]["code"] == "REVISION_CONFLICT", body
                assert body["error"]["detail"]["header"] == "If-Match", body
        assert _file_state(pid) == before_file
        assert _parts(pid) == before_parts

    def test_stale_rev_is_409_and_writes_nothing(self):
        pid = seed_roles("dlsave3")
        _seed_config(pid)
        before_file = _file_state(pid)
        before_parts = _parts(pid)
        payload = _valid_payload(pid, k1=5.0)
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").json(
                "PUT",
                ITEM_URL % (pid, "rp_energy"),
                payload,
                headers={"If-Match": "000000000000"},
            )
        assert status == 409, body
        assert body["error"]["code"] == "REVISION_CONFLICT"
        assert _file_state(pid) == before_file
        assert _parts(pid) == before_parts

    def test_success_returns_new_rev_sets_editing_false_and_bumps_settings_once(self):
        pid = seed_roles("dlsave4")
        old_rev = _seed_config(pid, editing=True)
        before_parts = _parts(pid)
        before = _load_item(pid)
        payload = deepcopy(before)
        payload["coeffs"]["k1"] = 5.0
        payload["coeffs"]["b"] = -2.0
        payload["x_min"] = None
        payload["x_max"] = None
        payload["y_min"] = None
        payload["y_max"] = None
        assert "editing" not in payload
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").json(
                "PUT",
                ITEM_URL % (pid, "rp_energy"),
                payload,
                headers={"If-Match": old_rev},
            )
            assert status == 200, body
            assert set(body) == {"rev", "item"}, body
            assert body["item"]["coeffs"]["k1"] == 5.0
            assert body["item"]["coeffs"]["b"] == -2.0
            assert body["item"]["x_min"] is None
            assert body["item"]["x_max"] is None
            assert body["rev"] != old_rev
            assert body["rev"] == _rev_of_file(pid)

            _s, _h, state = as_role(server, "visitor").json("GET", DL_URL % pid)
            assert state["rev"] == body["rev"]
            assert state["items"]["rp_energy"] == body["item"]
            assert "editing" not in state

        after = _load_item(pid)
        assert set(after) == set(before), "item 字段集合不变（只改值）"
        assert set(after["coeffs"]) == set(before["coeffs"]), "coeffs 字段集合不变"
        assert after["x_min"] is None and after["x_max"] is None
        assert after["x_min"] != 0 and after["x_max"] != ""
        raw = json.loads(open(_config_path(pid), "r", encoding="utf-8").read())
        assert raw["editing"] is False, "保存后 editing 必须写 false"

        after_parts = _parts(pid)
        assert after_parts["settings"] == before_parts["settings"] + 1
        for part in ("files", "favorites", "remarks", "tasks"):
            assert after_parts[part] == before_parts[part], (part, after_parts)

    def test_blank_range_is_stored_as_null_not_zero_or_empty_string(self):
        pid = seed_roles("dlsave9")
        rev = _seed_config(
            pid,
            mutate=lambda cfg: cfg["items"]["rp_energy"].update(
                {"x_min": 1.0, "x_max": 2.0}
            ),
        )
        payload = _load_item(pid)
        payload["x_min"] = ""
        payload["x_max"] = "   "
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").json(
                "PUT",
                ITEM_URL % (pid, "rp_energy"),
                payload,
                headers={"If-Match": rev},
            )
        assert status == 200, body
        saved = _load_item(pid)
        assert saved["x_min"] is None and saved["x_max"] is None
        raw = json.loads(open(_config_path(pid), "r", encoding="utf-8").read())
        assert raw["items"]["rp_energy"]["x_min"] is None
        assert raw["items"]["rp_energy"]["x_max"] is None

    def test_unknown_keys_are_not_persisted(self):
        pid = seed_roles("dlsave5")
        rev = _seed_config(pid)
        before = _load_item(pid)
        payload = deepcopy(before)
        payload["extra_top"] = "x"
        payload["coeffs"]["k9"] = 99.0
        payload["binding"]["extra_binding"] = True
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").json(
                "PUT",
                ITEM_URL % (pid, "rp_energy"),
                payload,
                headers={"If-Match": rev},
            )
        assert status == 200, body
        after = _load_item(pid)
        assert set(after) == set(before)
        assert set(after["coeffs"]) == set(before["coeffs"])
        assert "k9" not in after["coeffs"]
        assert "extra_binding" not in after["binding"]

    @pytest.mark.parametrize(
        "kind, mutate",
        [
            ("rp_energy", lambda p: p["coeffs"].__setitem__("k1", "abc")),
            ("rp_energy", lambda p: p["coeffs"].__setitem__("k1", None)),
            ("rp_energy", lambda p: p.__setitem__("x_min", "abc")),
            ("rp_energy", lambda p: p.__setitem__("x_min", 5.0) or p.__setitem__("x_max", 1.0)),
            ("rp_energy", lambda p: p.__setitem__("x_min", float("nan"))),
            ("pp_energy", lambda p: p.__setitem__("power_conv_coeff", 0.0)),
            ("pp_energy", lambda p: p.__setitem__("trans_coeff", 0.0)),
        ],
    )
    def test_invalid_values_are_400_without_touching_the_file(self, kind, mutate):
        pid = seed_roles("dlsave6")
        rev = _seed_config(pid)
        before_file = _file_state(pid)
        before_parts = _parts(pid)
        payload = _load_item(pid, kind)
        mutate(payload)
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").json(
                "PUT",
                ITEM_URL % (pid, kind),
                payload,
                headers={"If-Match": rev},
            )
        assert status == 400, (kind, body)
        assert body["error"]["code"] == "INVALID_INPUT", body
        assert _file_state(pid) == before_file, "非法值不许写到磁盘"
        assert _parts(pid) == before_parts

    def test_unknown_item_is_404(self):
        pid = seed_roles("dlsave7")
        rev = _seed_config(pid)
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").json(
                "PUT",
                ITEM_URL % (pid, "nope"),
                _load_item(pid),
                headers={"If-Match": rev},
            )
        assert status == 404, body

    def test_two_concurrent_savers_only_the_first_writes(self):
        """UC-38：两人拿着同一个 rev 并发保存 → 一个 200、一个 409；文件只被先保存者
        改一次（内容=胜者、settings 只 +1、mtime 变化且落盘值不是败者的值）。"""
        pid = seed_roles("dlsave8")
        rev = _seed_config(pid)
        before_file = _file_state(pid)
        before_parts = _parts(pid)
        payload_a = _valid_payload(pid, k1=11.0)
        payload_b = _valid_payload(pid, k1=22.0)
        results = []
        barrier = threading.Barrier(2)

        with make_server() as server:
            def worker(value, payload):
                caller = as_role(server, "deployer")
                barrier.wait(timeout=10)
                status, _h, body = caller.json(
                    "PUT",
                    ITEM_URL % (pid, "rp_energy"),
                    payload,
                    headers={"If-Match": rev},
                )
                results.append((value, status, body))

            threads = [
                threading.Thread(target=worker, args=(11.0, payload_a)),
                threading.Thread(target=worker, args=(22.0, payload_b)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
            assert all(not thread.is_alive() for thread in threads)

        assert len(results) == 2, results
        statuses = sorted(status for _value, status, _body in results)
        assert statuses == [200, 409], results
        winner_value, winner_status, winner_body = next(r for r in results if r[1] == 200)
        loser_value, loser_status, loser_body = next(r for r in results if r[1] == 409)
        assert winner_status == 200 and loser_body["error"]["code"] == "REVISION_CONFLICT"
        assert winner_value != loser_value

        after_file = _file_state(pid)
        assert after_file is not None and after_file[0] != before_file[0]
        assert after_file[1] >= before_file[1]
        saved = _load_item(pid)
        assert saved["coeffs"]["k1"] == winner_value, "落盘的必须是胜者的值"
        assert saved["coeffs"]["k1"] != loser_value
        assert winner_body["item"]["coeffs"]["k1"] == winner_value

        after_parts = _parts(pid)
        assert after_parts["settings"] == before_parts["settings"] + 1, "只允许成功那次 bump"
        for part in ("files", "favorites", "remarks", "tasks"):
            assert after_parts[part] == before_parts[part], (part, after_parts)


# ---------------------------------------------------------------------------
# trans_coeff = 0：M3-10 遗留的「validate 合法但 ÷0」在本卡正面解决（裁定 a）
# ---------------------------------------------------------------------------


class TestTransCoeffZero:
    def _zero_trans(self):
        return _item(
            kind="pp_energy",
            coeffs={"k1": -6.0, "k2": 1.0, "b": 9.0},
            x_min=-10.0,
            x_max=10.0,
            y_min=0.0,
            y_max=100.0,
            trans=0.0,
            power=1.0,
        )

    def test_validate_still_accepts_it(self):
        """M3-10 的哨兵前提不变：`dltool.validate` 只拒绝 power_conv_coeff=0。"""
        assert dltool.validate(self._zero_trans(), "pp_energy") == ""

    @pytest.mark.parametrize("mode", ["forward", "reverse", "multi_check"])
    def test_compute_returns_400_instead_of_raising(self, mode):
        pid = seed_roles("dlzero")
        params = self._zero_trans()
        before = _parts(pid)
        with make_server() as server:
            status, _h, body = as_role(server, "visitor").json(
                "POST",
                COMPUTE_URL % (pid, "pp_energy"),
                {"mode": mode, "values": [4.0], "params": params},
            )
        assert status == 400, (mode, body)
        assert body["error"]["code"] == "INVALID_INPUT", body
        assert body["error"]["message"] == TRANS_ZERO
        assert "Traceback" not in json.dumps(body)
        assert _parts(pid) == before
        assert _file_state(pid) is None

    def test_save_returns_400_before_touching_disk(self):
        pid = seed_roles("dlzero2")
        rev = _seed_config(pid)
        before_file = _file_state(pid)
        before_parts = _parts(pid)
        payload = _load_item(pid, "pp_energy")
        payload["trans_coeff"] = 0.0
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").json(
                "PUT",
                ITEM_URL % (pid, "pp_energy"),
                payload,
                headers={"If-Match": rev},
            )
        assert status == 400, body
        assert body["error"]["code"] == "INVALID_INPUT"
        assert body["error"]["message"] == TRANS_ZERO
        assert _file_state(pid) == before_file
        assert _parts(pid) == before_parts


# ---------------------------------------------------------------------------
# 路由注册
# ---------------------------------------------------------------------------


class TestRoutesRegistered:
    def test_all_four_dl_routes_are_registered(self):
        templates = _route_templates()
        assert ("GET", "/api/v1/p/{pid}/dl") in templates
        assert ("POST", "/api/v1/p/{pid}/dl/{item}/compute") in templates
        assert ("POST", "/api/v1/p/{pid}/dl/{item}/extract") in templates
        assert ("PUT", "/api/v1/p/{pid}/dl/{item}") in templates
