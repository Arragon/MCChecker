"""产物与下载的 HTTP 行为（M6-14 / INH-1959）。

钉住四条容易退化的性质：

* **不重生成**：`/raw` 读的是落盘 blob，重复下载必须字节一致（`X-Content-Sha256` 相同）；
* **同输入同产物**：同一基线修订重复生成返回 200 + 同一 id，不新建对象；
* **修订变了才有新产物**：基线改一版值再生成，必须是新 id、新哈希；
* **生成不是随便谁都能做**：`config_artifact_generate` 与提交/批准分开授权。

已知缺口 D6：没有真实系统的 XML 样本，"真实导入并记录操作者/环境/结果"做不了，
产物一律带 `delivery_status=unverified`——这里也把这个标记钉住，免得有人"顺手"改成 verified。
"""

from __future__ import annotations

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.services import config_export, config_gate, config_revision

XML_DOC = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<R><PulseWidth unit="ms">650</PulseWidth><MaxSpeed unit="mm/s">100</MaxSpeed></R>'
).encode("utf-8")

OCTET = {"Content-Type": "application/octet-stream"}


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.delenv(config_gate.ENV_KEY, raising=False)
    config_revision.reset()
    yield
    config_revision.reset()


def _enable(monkeypatch, mode="on"):
    monkeypatch.setenv(config_gate.ENV_KEY, mode)


def _seed(caller):
    """版本 + 类别 + 两参数 + 定义集合 + 默认批次 + 通用基线（草稿）。"""
    release = caller.json(
        "POST", "/api/v1/config/releases", body={"version_label": "1.0.0", "build": "20260801"}
    )[2]["item"]
    family = caller.json(
        "POST", "/api/v1/config/families", body={"name": "轴参数", "code": "AXIS_PARAM"}
    )[2]["item"]
    logical = caller.json(
        "POST",
        "/api/v1/config/families/%s/files" % family["id"],
        body={"role": "xml_export", "display_name": "轴参数.xml"},
    )[2]["item"]
    pulse = caller.json(
        "POST",
        "/api/v1/config/parameters",
        body={"family_id": family["id"], "name": "脉冲宽度", "category": "时序"},
    )[2]["item"]
    speed = caller.json(
        "POST",
        "/api/v1/config/parameters",
        body={"family_id": family["id"], "name": "最大速度", "category": "运动"},
    )[2]["item"]
    pulse_def = caller.json(
        "POST",
        "/api/v1/config/parameters/%s/definitions" % pulse["id"],
        body={
            "xml_name": "PulseWidth",
            "xml_loc": {"path": [{"name": "PulseWidth", "occurrence": 1}], "attribute": None},
            "type": "decimal",
            "unit": "ms",
            "reason": "首次登记",
        },
    )[2]["item"]
    speed_def = caller.json(
        "POST",
        "/api/v1/config/parameters/%s/definitions" % speed["id"],
        body={
            "xml_name": "MaxSpeed",
            "xml_loc": {"path": [{"name": "MaxSpeed", "occurrence": 1}], "attribute": None},
            "type": "decimal",
            "unit": "mm/s",
            "reason": "首次登记",
        },
    )[2]["item"]
    snapshot = caller.json(
        "POST",
        "/api/v1/config/snapshots?release_id=%s&logical_file_id=%s&source=upload"
        % (release["id"], logical["id"]),
        body=XML_DOC,
        headers={**OCTET, "X-Filename": "axis_param.xml"},
    )[2]["item"]
    variant = caller.json(
        "POST",
        "/api/v1/config/variants",
        body={
            "family_id": family["id"],
            "release_id": release["id"],
            "template_snapshot_id": snapshot["id"],
        },
    )[2]["item"]
    caller.json(
        "POST",
        "/api/v1/config/releases/%s/default" % release["id"],
        body={"snapshot_ids": [snapshot["id"]]},
    )
    definition_set = caller.json(
        "POST",
        "/api/v1/config/definition-sets",
        body={
            "release_id": release["id"],
            "definition_ids": [pulse_def["id"], speed_def["id"]],
            "variant_id": variant["id"],
        },
    )[2]["item"]
    return {
        "release": release,
        "family": family,
        "logical": logical,
        "pulse": pulse,
        "speed": speed,
        "snapshot": snapshot,
        "variant": variant,
        "definition_set": definition_set,
    }


def _common(caller, world, **overrides):
    payload = {"scope": "common", "release_id": world["release"]["id"]}
    payload.update(overrides)
    status, _h, body = caller.json("POST", "/api/v1/config/baselines", body=payload)
    assert status == 201, body
    return body["item"]


def _repo():
    from app.core import storage
    from app.core.config_repository import ConfigRepository

    return ConfigRepository(storage.DATA_DIR)


def _approve(baseline, values=None):
    """推仓储把基线改成 approved（必要时先改值）。

    刻意不复用"变更单 → 批准"那条生产路径：本文件测的是产物，
    不该因为改动单状态机出问题而连带失败。对象形状与真实批准一致。
    """
    from app.services import config_baselines as bl

    repo = _repo()
    payload = dict(baseline)
    payload.pop("id", None)
    payload.pop("revision", None)
    if values:
        merged = dict(payload.get("values") or {})
        merged.update(
            bl.normalize_values(
                repo, values, definition_set_id=payload.get("definition_set_id")
            )
        )
        payload["values"] = merged
    payload.update({"state": "approved", "decided_by": "管理员乙"})
    tx = repo.transaction({"ip": "127.0.0.1", "name": "管理员乙"}, "测试用批准")
    ref = tx.put("baseline", payload, object_id=baseline["id"])
    tx.commit()
    return bl.get_baseline(repo, str(ref.id))


def _gen(caller, pid, baseline_id, **kwargs):
    return caller.json(
        "POST", "/api/v1/p/%s/config/artifacts" % pid, body={"baseline_id": baseline_id}, **kwargs
    )


# -- 门禁与权限 -------------------------------------------------------------


def test_artifacts_require_gate_and_generate_permission(monkeypatch) -> None:
    with make_server() as server:
        pid = seed_roles("p1")
        admin = as_role(server, "admin")
        status, _h, body = admin.json("GET", "/api/v1/p/%s/config/artifacts" % pid)
        assert status == 503 and body["error"]["code"] == "CONFIG_DISABLED"

    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        admin = as_role(server, "admin")
        world = _seed(admin)
        approved = _approve(
            _common(admin, world),
            values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
        )
        member = as_role(server, "member")
        # 读：成员可以看列表
        assert member.json("GET", "/api/v1/p/%s/config/artifacts" % pid)[0] == 200
        # 写：成员不能生成，且错误里点名缺哪个权限
        status, _h, body = _gen(member, pid, approved["id"])
        assert status == 403
        assert body["error"]["detail"]["action"] == "config_artifact_generate"


# -- 生成：同输入同产物 ------------------------------------------------------


def test_generate_is_idempotent_for_same_input(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        approved = _approve(
            _common(caller, world),
            values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
        )

        status, _h, body = _gen(caller, pid, approved["id"])
        assert status == 201, body
        first = body["item"]
        assert body["reused"] is False
        assert first["baseline_id"] == approved["id"]
        assert first["baseline_revision"] == approved["revision"]
        # D6 未解除前恒为 unverified
        assert first["delivery_status"] == "unverified"
        assert first["byte_size"] > 0
        assert first["filename"].endswith(".xml")

        # 第二次：不新建，200 + 同 id
        status, _h, body = _gen(caller, pid, approved["id"])
        assert status == 200, body
        assert body["reused"] is True
        assert body["item"]["id"] == first["id"]
        assert body["item"]["blob_sha256"] == first["blob_sha256"]

        # 列表里只有一份
        items = caller.json("GET", "/api/v1/p/%s/config/artifacts" % pid)[2]["items"]
        assert len(items) == 1


def test_new_baseline_revision_yields_new_artifact(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        approved = _approve(
            _common(caller, world),
            values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
        )
        first = _gen(caller, pid, approved["id"])[2]["item"]

        # 基线再改一版值 → 修订号变了 → 必须产出新产物
        revised = _approve(
            approved, values={world["pulse"]["id"]: {"value": "800", "intent": "override"}}
        )
        assert revised["revision"] != approved["revision"]
        status, _h, body = _gen(caller, pid, revised["id"])
        assert status == 201, body
        assert body["reused"] is False
        assert body["item"]["id"] != first["id"]
        assert body["item"]["blob_sha256"] != first["blob_sha256"]
        assert body["item"]["baseline_revision"] == revised["revision"]

        # 旧产物仍在、仍可下载：产物是历史留痕，不随基线改动而消失
        assert caller.json("GET", "/api/v1/p/%s/config/artifacts/%s" % (pid, first["id"]))[0] == 200


# -- 下载 -------------------------------------------------------------------


def test_raw_download_is_byte_identical(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        approved = _approve(
            _common(caller, world),
            values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
        )
        item = _gen(caller, pid, approved["id"])[2]["item"]

        status, headers, first = caller.request(
            "GET", "/api/v1/p/%s/config/artifacts/%s/raw" % (pid, item["id"])
        )
        assert status == 200
        assert headers["X-Content-Sha256"] == item["blob_sha256"]
        assert headers["ETag"] == '"%s"' % item["blob_sha256"]
        assert "attachment" in headers["Content-Disposition"]
        assert item["filename"] in headers["Content-Disposition"]
        assert headers["Content-Type"].startswith("application/xml")
        assert len(first) == item["byte_size"]

        # 重复下载：同一串字节（不重生成）
        status, headers2, again = caller.request(
            "GET", "/api/v1/p/%s/config/artifacts/%s/raw" % (pid, item["id"])
        )
        assert status == 200
        assert again == first
        assert headers2["X-Content-Sha256"] == headers["X-Content-Sha256"]

        # 落盘内容确实是被批准的那个值
        assert b"700" in first
        assert b"100" in first


def test_blob_is_read_not_regenerated(monkeypatch) -> None:
    """把 blob 替换成哨兵字节后再下载：拿到的必须是哨兵，证明没有重生成。"""
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        approved = _approve(
            _common(caller, world),
            values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
        )
        item = _gen(caller, pid, approved["id"])[2]["item"]

        repo = _repo()
        from app.core.config_repository import sha256_bytes

        # blob 是内容寻址的：写哨兵会落到另一个哈希上，所以先算出哨兵的哈希，
        # 再把产物对象指向它——只有"读 blob"才会看到哨兵。
        sentinel = b"<!-- sentinel -->"
        repo.put_blob(sentinel)
        sentinel_sha = sha256_bytes(sentinel)
        payload = dict(item)
        payload.pop("id", None)
        tx = repo.transaction({"ip": "127.0.0.1", "name": "管理员乙"}, "改指向")
        tx.put("artifact", {**payload, "blob_sha256": sentinel_sha}, object_id=item["id"])
        tx.commit()

        status, headers, raw = caller.request(
            "GET", "/api/v1/p/%s/config/artifacts/%s/raw" % (pid, item["id"])
        )
        assert status == 200
        assert raw == sentinel
        assert headers["X-Content-Sha256"] == sentinel_sha


# -- 拒绝与可见性 -----------------------------------------------------------


def test_draft_baseline_cannot_generate(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        draft = _common(caller, world)
        status, _h, body = _gen(caller, pid, draft["id"])
        assert status == 400
        assert body["error"]["detail"]["field"] == "baseline_id"
        assert body["error"]["detail"]["state"] == "draft"


def test_missing_template_is_reported(monkeypatch) -> None:
    """基线指向的变体不存在时，生成要显式拒绝，而不是产出空文件或空产物。"""
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        # 基线本身能建（变体存在），批准后再把变体指针改成不存在的对象：
        # 模拟"模板快照被人工清理掉"这种真实会发生的坏状态。
        approved = _approve(
            _common(caller, world),
            values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
        )
        repo = _repo()
        payload = dict(approved)
        payload.pop("id", None)
        payload.pop("revision", None)
        payload["variant_id"] = "vr_999999"
        tx = repo.transaction({"ip": "127.0.0.1", "name": "管理员乙"}, "指向不存在的变体")
        tx.put("baseline", payload, object_id=approved["id"])
        tx.commit()

        status, _h, body = _gen(caller, pid, approved["id"])
        assert status == 404, body
        assert body["error"]["message"] == "模板变体不存在"
        assert body["error"]["detail"]["variant_id"] == "vr_999999"
        # 没留下半成品产物
        assert caller.json("GET", "/api/v1/p/%s/config/artifacts" % pid)[2]["total"] == 0


def test_unknown_artifact_is_404(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        caller = as_role(server, "admin")
        assert caller.json("GET", "/api/v1/p/%s/config/artifacts/ar_000001" % pid)[0] == 404
        assert caller.request("GET", "/api/v1/p/%s/config/artifacts/ar_000001/raw" % pid)[0] == 404


def test_detail_lists_edits_and_validation(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        approved = _approve(
            _common(caller, world),
            values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
        )
        item = _gen(caller, pid, approved["id"])[2]["item"]

        status, _h, body = caller.json("GET", "/api/v1/p/%s/config/artifacts/%s" % (pid, item["id"]))
        assert status == 200
        assert body["delivery_status"] == "unverified"
        assert body["validation"]["ok"] is True
        assert any("逐字节保真" in text for text in body["validation"]["warnings"])
        assert len(body["edits"]) == 1
        assert body["edits"][0]["to"] == "700"
        assert body["edits"][0]["parameter_id"] == world["pulse"]["id"]
        # 模板骨架必须与生成时一致（快照的 blob 哈希即模板字节哈希）
        assert body["item"]["template_sha256"] == world["snapshot"]["blob_sha256"]


def test_list_filters_by_baseline(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        common = _approve(
            _common(caller, world),
            values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
        )
        first = _gen(caller, pid, common["id"])[2]["item"]

        # 另一个基线（同版本通用基线，值不同）→ 第二份产物
        other = _approve(
            _common(caller, world),
            values={world["pulse"]["id"]: {"value": "900", "intent": "override"}},
        )
        second = _gen(caller, pid, other["id"])[2]["item"]
        assert second["id"] != first["id"]

        items = caller.json("GET", "/api/v1/p/%s/config/artifacts" % pid)[2]["items"]
        assert len(items) == 2
        only = caller.json(
            "GET", "/api/v1/p/%s/config/artifacts?baseline_id=%s" % (pid, other["id"])
        )[2]
        assert only["total"] == 1
        assert only["items"][0]["id"] == second["id"]

        # 空结果也是 200 + total=0，不是 404
        empty = caller.json(
            "GET", "/api/v1/p/%s/config/artifacts?baseline_id=bl_999999" % pid
        )[2]
        assert empty == {"items": [], "total": 0}


def test_model_artifact_is_scoped_to_its_profile(monkeypatch) -> None:
    """机型基线产出的产物只在它的机型下可见；换了机型就是 404。"""
    _enable(monkeypatch)
    with make_server() as server:
        pid = seed_roles("p1")
        caller = as_role(server, "admin")
        world = _seed(caller)
        common = _approve(
            _common(caller, world),
            values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
        )
        status, _h, body = caller.json(
            "POST",
            "/api/v1/config/baselines",
            body={
                "scope": "model",
                "release_id": world["release"]["id"],
                "profile_id": pid,
                "upstream_baseline_id": common["id"],
            },
        )
        assert status == 201, body
        model = _approve(
            body["item"], values={world["pulse"]["id"]: {"value": "710", "intent": "override"}}
        )

        status, _h, body = _gen(caller, pid, model["id"])
        assert status == 201, body
        item = body["item"]
        assert item["profile_id"] == pid

        # 新增一个机型 p2，从 p2 的路径下去看 p1 的产物 → 404
        from app.core import storage

        storage.set_active_profile("default")
        p2 = seed_roles("p2")
        peek = as_role(server, "admin")
        assert peek.json("GET", "/api/v1/p/%s/config/artifacts/%s" % (p2, item["id"]))[0] == 404
        assert (
            peek.request("GET", "/api/v1/p/%s/config/artifacts/%s/raw" % (p2, item["id"]))[0]
            == 404
        )
        # 在 p2 的坐标下也不能拿 p1 的基线生成
        status, _h, body = _gen(peek, p2, model["id"])
        assert status == 404
