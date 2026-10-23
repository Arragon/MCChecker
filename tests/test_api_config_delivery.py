"""交付记录与回读核验的 HTTP 行为（M7-09 / M7-10）。

服务层已经证明四态与核验逻辑；这一层只钉接口契约：

* 读用 `config_read`、写用 `config_artifact_generate`（交付登记是同一件事的后半段）；
* 登记与核验都返回 **201**：每次动作新增一个对象，不覆盖旧的；
* 缺字段 → 400 落在该字段上；设备身份 → 400 `DEVICE_LAYER_DISABLED`；
* `summary` 里**永远没有** "所有设备已完成" 这种说法。
"""

from __future__ import annotations

import pytest
from authz_fixtures import as_role, make_server, seed_roles

from app.core import config_repository as cr, storage
from app.services import config_artifacts, config_gate, config_ingest, config_revision
import upgrade_fixtures as fx

PROFILE = "p1"
EARLY = "2026-10-20T09:00:00Z"
LATE = "2026-10-21T09:00:00Z"


@pytest.fixture(autouse=True)
def clean_state():
    config_revision.reset()
    yield
    config_revision.reset()


def _enable(monkeypatch, mode="on"):
    monkeypatch.setenv(config_gate.ENV_KEY, mode)


def _repo():
    return cr.ConfigRepository(storage.DATA_DIR)


def _seed():
    """世界 + 已批准通用 + 产物；返回 (repo, world, artifact)。"""
    repo = _repo()
    world = fx.build(repo)
    common = fx.approve_common(
        repo, world, values={world["pulse"]["id"]: {"value": "900", "intent": "override"}}
    )
    artifact = config_artifacts.generate_artifact(
        repo, fx.ACTOR, profile_id=PROFILE, baseline_id=common["id"]
    )["item"]
    return repo, world, artifact


def _observe(repo, world, artifact, *, captured_at=LATE, release_id=None):
    """把产物自己的字节读回来当观测——这模拟"机器上读到的就是它"。"""
    return config_ingest.record_capture(
        repo,
        fx.ACTOR,
        profile_id=PROFILE,
        content=config_artifacts.artifact_bytes(repo, artifact["id"]),
        filename="axis.xml",
        logical_file_id=world["logical"]["id"],
        release_id=release_id or world["old"]["release"]["id"],
        captured_at=captured_at,
    )


# -- 门禁与权限 -------------------------------------------------------------


def test_delivery_requires_gate_and_generate_permission(monkeypatch) -> None:
    with make_server() as server:
        seed_roles(PROFILE)
        admin = as_role(server, "admin")
        status, _h, body = admin.json("GET", "/api/v1/p/%s/config/deliveries" % PROFILE)
        assert status == 503 and body["error"]["code"] == "CONFIG_DISABLED"

    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        _repo_, _world, artifact = _seed()
        member = as_role(server, "member")
        path = "/api/v1/p/%s/config/deliveries" % PROFILE
        assert member.json("GET", path)[0] == 200
        status, _h, body = member.json(
            "POST", path, body={"artifact_id": artifact["id"], "result": "failed"}
        )
        assert status == 403
        assert body["error"]["detail"]["action"] == "config_artifact_generate"


def test_summary_needs_artifact_id(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "GET", "/api/v1/p/%s/config/deliveries/summary" % PROFILE
        )
        assert status == 400
        assert body["error"]["detail"]["field"] == "artifact_id"


# -- 交付登记 ---------------------------------------------------------------


def test_register_and_list_and_summary(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, _world, artifact = _seed()
        admin = as_role(server, "admin")
        base = "/api/v1/p/%s/config/deliveries" % PROFILE

        status, _h, body = admin.json(
            "POST",
            base,
            body={
                "artifact_id": artifact["id"],
                "result": "failed",
                "executed_at": EARLY,
                "failure_reason": "E-104 校验失败",
            },
        )
        assert status == 201, body
        first = body["item"]
        assert first["attempt"] == 1
        assert first["result_label"] == "失败"
        assert first["target"] == {"profile_id": PROFILE, "device_id": None}

        status, _h, body = admin.json(
            "POST",
            base,
            body={
                "artifact_id": artifact["id"],
                "result": "declared_ok",
                "executed_at": LATE,
                "executed_by": "现场张工",
                "evidence": {"ref": "photo://2.jpg", "note": "回读一致"},
            },
        )
        assert status == 201
        second = body["item"]
        assert second["attempt"] == 2
        assert second["previous_delivery_id"] == first["id"]

        status, _h, body = admin.json("GET", base + "?artifact_id=%s" % artifact["id"])
        assert status == 200
        assert body["total"] == 2
        assert [item["id"] for item in body["items"]] == [second["id"], first["id"]]

        status, _h, body = admin.json(
            "GET", base + "/summary?artifact_id=%s" % artifact["id"]
        )
        assert status == 200
        assert body["counts"] == {"declared_ok": 1, "failed": 1, "insufficient_evidence": 0}
        assert body["result"] == "declared_ok"
        # 这句必须存在：没有"所有设备已完成"
        assert body["can_claim_all_devices"] is False
        assert "不能断言" in body["note"]

        status, _h, body = admin.json("GET", base + "/" + first["id"])
        assert status == 200
        assert body["item"]["failure_reason"] == "E-104 校验失败"


def test_other_profile_cannot_read_by_id(monkeypatch) -> None:
    """路径里的 pid 必须与对象归属一致；换个机型直读一律当不存在。"""
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world, artifact = _seed()
        observation = _observe(repo, world, artifact)
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "POST",
            "/api/v1/p/%s/config/deliveries" % PROFILE,
            body={
                "artifact_id": artifact["id"],
                "result": "failed",
                "executed_at": EARLY,
                "failure_reason": "E-104",
            },
        )
        assert status == 201
        delivery = body["item"]
        status, _h, body = admin.json(
            "POST",
            "/api/v1/p/%s/config/artifacts/%s/verifications" % (PROFILE, artifact["id"]),
            body={"observation_id": observation["id"], "imported_after": EARLY},
        )
        assert status == 201
        verification = body["item"]

        seed_roles("p2")
        admin = as_role(server, "admin")
        base = "/api/v1/p/p2/config"
        assert admin.json("GET", base + "/deliveries")[0] == 200
        assert admin.json("GET", base + "/deliveries")[2]["total"] == 0
        assert admin.json("GET", base + "/deliveries/" + delivery["id"])[0] == 404
        assert admin.json("GET", base + "/verifications/" + verification["id"])[0] == 404
        # 通用产物本身可见，但 p1 的登记记录不能算到 p2 头上
        status, _h, body = admin.json(
            "GET", base + "/deliveries/summary?artifact_id=" + artifact["id"]
        )
        assert status == 200
        assert body["total"] == 0
        assert body["result"] == "pending"


def test_register_without_result_lands_on_the_field(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        _repo_, _world, artifact = _seed()
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "POST",
            "/api/v1/p/%s/config/deliveries" % PROFILE,
            body={"artifact_id": artifact["id"]},
        )
        assert status == 400
        assert body["error"]["detail"]["field"] == "result"


def test_device_id_is_rejected_at_the_interface(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        _repo_, _world, artifact = _seed()
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "POST",
            "/api/v1/p/%s/config/deliveries" % PROFILE,
            body={
                "artifact_id": artifact["id"],
                "result": "declared_ok",
                "device_id": "DEV-1",
                "executed_at": EARLY,
                "evidence": {"ref": "log://1"},
            },
        )
        assert status == 400
        assert body["error"]["code"] == "DEVICE_LAYER_DISABLED"


# -- 回读核验 ---------------------------------------------------------------


def test_verify_endpoint_runs_and_keeps_history(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world, artifact = _seed()
        observation = _observe(repo, world, artifact)
        admin = as_role(server, "admin")
        base = "/api/v1/p/%s/config/artifacts/%s/verifications" % (PROFILE, artifact["id"])

        status, _h, body = admin.json("GET", base)
        assert status == 200 and body["items"] == []

        status, _h, body = admin.json(
            "POST", base, body={"observation_id": observation["id"], "imported_after": EARLY}
        )
        assert status == 201, body
        report = body["item"]
        assert report["conclusion"] == "verified"
        assert report["conclusion_label"] == "核验通过"
        assert report["counts"]["matched"] >= 2
        assert report["capture_time"] == LATE
        assert report["source_fingerprint"] == observation["blob_sha256"]
        assert report["scope_complete"] is False
        assert report["unregistered_keys"], "模板里没登记的节点要列出来"

        status, _h, body = admin.json("GET", base)
        assert body["total"] == 1

        status, _h, body = admin.json(
            "GET", "/api/v1/p/%s/config/verifications/%s" % (PROFILE, report["id"])
        )
        assert status == 200
        assert body["item"]["id"] == report["id"]


def test_capture_before_import_is_incomplete(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world, artifact = _seed()
        observation = _observe(repo, world, artifact, captured_at=EARLY)
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "POST",
            "/api/v1/p/%s/config/artifacts/%s/verifications" % (PROFILE, artifact["id"]),
            body={"observation_id": observation["id"], "imported_after": LATE},
        )
        assert status == 201
        report = body["item"]
        assert report["conclusion"] == "incomplete"
        assert "capture_before_import" in report["reasons"]


def test_version_mismatch_short_circuits(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world, artifact = _seed()
        observation = _observe(repo, world, artifact, release_id=world["new"]["release"]["id"])
        admin = as_role(server, "admin")
        status, _h, body = admin.json(
            "POST",
            "/api/v1/p/%s/config/artifacts/%s/verifications" % (PROFILE, artifact["id"]),
            body={"observation_id": observation["id"], "imported_after": EARLY},
        )
        assert status == 201
        report = body["item"]
        assert report["conclusion"] == "version_mismatch"
        assert report["verification_scope"]["observation_release_id"] == world["new"]["release"]["id"]


def test_member_cannot_run_verification(monkeypatch) -> None:
    _enable(monkeypatch)
    with make_server() as server:
        seed_roles(PROFILE)
        repo, world, artifact = _seed()
        member = as_role(server, "member")
        status, _h, body = member.json(
            "POST",
            "/api/v1/p/%s/config/artifacts/%s/verifications" % (PROFILE, artifact["id"]),
            body={},
        )
        assert status == 403
        assert body["error"]["detail"]["action"] == "config_artifact_generate"
