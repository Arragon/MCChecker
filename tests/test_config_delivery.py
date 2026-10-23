"""交付记录四态（M7-09 / INH-1968）。

四条验收在这里落成可失败的断言：

* **下载不自动标"已导入"** → `test_download_does_not_claim_imported`
* **失败/撤回/重试逐次留记录** → `test_each_attempt_is_its_own_record`
* **缺设备身份不得伪造"所有设备已完成"** → `test_summary_never_claims_all_devices`
* **四态分离、待导入是推导值** → `test_pending_is_derived_not_stored`

还有三条"不许糊"的边界：成功必须有证据、失败必须写原因、证据不足必须写缺什么。
"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.errors import DEVICE_LAYER_DISABLED, INVALID_INPUT, NOT_FOUND, AppError
from app.services import config_artifacts, config_baselines as bl, config_delivery

from tests.test_config_export import ACTOR, _approve, world  # noqa: F401 - 复用同一份世界


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


def _reg(repo, artifact, **kwargs):
    """登记一次（默认落在 p1：通用产物的交付必须点名机型）。"""
    kwargs.setdefault("profile_id", "p1")
    return config_delivery.register(repo, ACTOR, artifact_id=artifact["id"], **kwargs)


@pytest.fixture()
def artifact(repo, world):  # noqa: F811 - world 是导入的夹具
    approved = _approve(
        repo,
        world["baseline"],
        values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
    )
    return config_artifacts.generate_artifact(
        repo, ACTOR, profile_id="p1", baseline_id=approved["id"]
    )["item"]


# -- 下载不算导入 -----------------------------------------------------------


def test_download_does_not_claim_imported(repo, artifact) -> None:
    before = config_delivery.list_deliveries(repo)
    # 下载就是读 blob：这条路径不许有任何写动作
    config_artifacts.artifact_bytes(repo, artifact["id"])
    after = config_delivery.list_deliveries(repo)
    assert before == after == []
    state = config_delivery.effective_result(repo, artifact["id"])
    assert state["result"] == config_delivery.RESULT_PENDING
    assert state["label"] == "待导入"
    assert state["attempts"] == 0


def test_pending_is_derived_not_stored(repo, artifact) -> None:
    with pytest.raises(AppError) as caught:
        _reg(repo, artifact, result="pending"
        )
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["field"] == "result"
    # `pending` 不在允许列表里：它不是登记出来的，是"还没登记"的推导结果
    assert "pending" not in caught.value.detail["allowed"]
    assert config_delivery.list_deliveries(repo) == []


# -- 逐次留记录 -------------------------------------------------------------


def test_each_attempt_is_its_own_record(repo, artifact) -> None:
    first = _reg(repo, artifact, result=config_delivery.RESULT_FAILED,
        executed_at="2026-10-20T09:00:00Z",
        failure_reason="导入报 E-104 校验失败",
        note="第一次尝试",
    )
    assert first["attempt"] == 1
    assert first["result_label"] == "失败"
    assert first["previous_delivery_id"] is None
    assert first["target"] == {"profile_id": "p1", "device_id": None}

    second = _reg(repo, artifact, result=config_delivery.RESULT_DECLARED_OK,
        executed_at="2026-10-20T11:00:00Z",
        executed_by="现场张工",
        evidence={"note": "导入后回读一致", "ref": "photo://2026-10-20/1.jpg"},
    )
    assert second["attempt"] == 2
    assert second["previous_delivery_id"] == first["id"]

    # 两条都在：失败没有被人"覆盖"掉
    history = config_delivery.history(repo, artifact_id=artifact["id"])
    assert [item["result"] for item in history] == ["declared_ok", "failed"]
    assert history[0]["attempt"] == 2 and history[1]["attempt"] == 1
    # 当前状态取最近一次
    state = config_delivery.effective_result(repo, artifact["id"])
    assert state["result"] == config_delivery.RESULT_DECLARED_OK
    assert state["attempts"] == 2


def test_retry_after_failure_reuses_the_same_artifact(repo, artifact) -> None:
    """重试不是重新生成产物：产物不可变，记录挂在同一条产物上。"""
    before = len(config_artifacts.list_artifacts(repo))
    for index in range(3):
        _reg(repo, artifact, result=config_delivery.RESULT_FAILED,
            executed_at="2026-10-2%dT09:00:00Z" % index,
            failure_reason="第 %d 次仍然失败" % (index + 1),
        )
    assert len(config_artifacts.list_artifacts(repo)) == before
    assert config_delivery.effective_result(repo, artifact["id"])["attempts"] == 3


# -- 三条"不许糊" -----------------------------------------------------------


def test_declared_ok_without_evidence_is_rejected(repo, artifact) -> None:
    with pytest.raises(AppError) as caught:
        _reg(repo, artifact, result="declared_ok",
            executed_at="2026-10-20T09:00:00Z",
        )
    assert caught.value.code == INVALID_INPUT
    assert caught.value.detail["field"] == "evidence"
    assert "证据不足" in caught.value.detail["hint"]


def test_failed_without_reason_is_rejected(repo, artifact) -> None:
    with pytest.raises(AppError) as caught:
        _reg(repo, artifact, result="failed",
            executed_at="2026-10-20T09:00:00Z",
        )
    assert caught.value.detail["field"] == "failure_reason"


def test_insufficient_evidence_must_say_what_is_missing(repo, artifact) -> None:
    with pytest.raises(AppError) as caught:
        _reg(repo, artifact, result="insufficient_evidence"
        )
    assert caught.value.detail["field"] == "missing_evidence"

    item = _reg(repo, artifact, result=config_delivery.RESULT_INSUFFICIENT,
        missing_evidence=["没有导入日志", "现场没拍照"],
    )
    assert item["missing_evidence"] == ["没有导入日志", "现场没拍照"]
    assert item["result_label"] == "证据不足"


def test_executed_at_required_for_declared_states(repo, artifact) -> None:
    with pytest.raises(AppError) as caught:
        _reg(repo, artifact, result="declared_ok",
            evidence={"note": "说成功了"},
        )
    assert caught.value.detail["field"] == "executed_at"


def test_common_artifact_needs_explicit_profile(repo, artifact) -> None:
    """通用产物没有机型归属：不点名就不知道该登记到哪台机器上。"""
    with pytest.raises(AppError) as caught:
        config_delivery.register(
            repo, ACTOR, artifact_id=artifact["id"], result="failed",
            executed_at="2026-10-20T09:00:00Z", failure_reason="x",
        )
    assert caught.value.detail["field"] == "profile_id"


def test_artifact_of_another_profile_is_rejected(repo, world) -> None:  # noqa: F811
    common = _approve(repo, world["baseline"])
    model = bl.create_baseline(
        repo,
        ACTOR,
        scope="model",
        release_id=world["release"]["id"],
        profile_id="p1",
        upstream_baseline_id=common["id"],
        values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
    )
    model = _approve(repo, model)
    produced = config_artifacts.generate_artifact(
        repo, ACTOR, profile_id="p1", baseline_id=model["id"]
    )["item"]

    with pytest.raises(AppError) as caught:
        config_delivery.register(
            repo, ACTOR, artifact_id=produced["id"], result="failed",
            profile_id="p2", executed_at="2026-10-20T09:00:00Z", failure_reason="x",
        )
    assert caught.value.code == NOT_FOUND


# -- 设备身份 ---------------------------------------------------------------


def test_device_id_is_rejected_while_layer_closed(repo, artifact) -> None:
    with pytest.raises(AppError) as caught:
        _reg(repo, artifact, result="declared_ok",
            device_id="DEV-1", executed_at="2026-10-20T09:00:00Z",
            evidence={"ref": "log://1"},
        )
    assert caught.value.code == DEVICE_LAYER_DISABLED
    assert caught.value.detail["device_id"] == "DEV-1"


def test_summary_never_claims_all_devices(repo, artifact) -> None:
    _reg(repo, artifact, result="declared_ok",
        executed_at="2026-10-20T09:00:00Z", evidence={"ref": "log://1"},
    )
    summary = config_delivery.summary(repo, artifact_id=artifact["id"])
    assert summary["counts"]["declared_ok"] == 1
    assert summary["total"] == 1
    assert summary["devices_known"] is False
    assert summary["can_claim_all_devices"] is False
    assert "不能断言" in summary["note"]
    assert summary["profiles"] == ["p1"]


def test_summary_without_records_says_pending(repo, artifact) -> None:
    summary = config_delivery.summary(repo, artifact_id=artifact["id"])
    assert summary["result"] == "pending"
    assert summary["label"] == "待导入"
    assert summary["total"] == 0
    assert summary["counts"] == {"declared_ok": 0, "failed": 0, "insufficient_evidence": 0}


def test_missing_artifact_is_not_found(repo) -> None:
    with pytest.raises(AppError) as caught:
        config_delivery.summary(repo, artifact_id="ar_999999")
    assert caught.value.code == NOT_FOUND
