"""不可变产物与下载（M6-14 / INH-1959）。

卡面验收逐条对应：

* 重复下载 SHA 一致 → `test_repeat_download_is_byte_identical`
* 同输入同生成版本返回已知产物 → `test_same_input_returns_known_artifact`
* 基线改一版值 → 新产物（键里带修订号） → `test_new_baseline_revision_makes_new_artifact`
* 下载不动态重生成 → `test_download_reads_blob_not_regenerate`
* 真实系统导入 → **受 D6 制约**：`delivery_status` 恒为 `unverified`，
  `test_delivery_is_unverified_until_d6`
"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.config_repository import sha256_bytes
from app.core.errors import INVALID_INPUT, NOT_FOUND, AppError
from app.services import config_artifacts, config_baselines as bl, config_export

from tests.test_config_export import ACTOR, TEMPLATE, world  # noqa: F401 - 复用同一份世界

from tests.test_config_export import _approve  # noqa: F401


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


def _approved(repo, world, values=None):
    return _approve(repo, world["baseline"], values=values)


# -- 生成与复用 -------------------------------------------------------------


def test_generate_records_validation_and_edits(repo, world) -> None:
    approved = _approved(
        repo, world, values={world["pulse"]["id"]: {"value": "700", "intent": "override"}}
    )
    result = config_artifacts.generate_artifact(
        repo, ACTOR, profile_id="p1", baseline_id=approved["id"]
    )
    assert result["reused"] is False
    item = result["item"]
    assert item["baseline_id"] == approved["id"]
    assert item["baseline_revision"] == approved["revision"]
    assert item["byte_size"] > 0
    assert item["filename"].endswith(".xml")
    assert item["validation"]["ok"] is True
    assert [edit["parameter_id"] for edit in item["edits"]] == [world["pulse"]["id"]]
    assert item["same_count"] == 2
    assert item["delivery_status"] == "unverified"


def test_same_input_returns_known_artifact(repo, world) -> None:
    approved = _approved(
        repo, world, values={world["pulse"]["id"]: {"value": "700", "intent": "override"}}
    )
    first = config_artifacts.generate_artifact(repo, ACTOR, profile_id="p1", baseline_id=approved["id"])
    again = config_artifacts.generate_artifact(repo, ACTOR, profile_id="p1", baseline_id=approved["id"])
    assert again["reused"] is True
    assert again["item"]["id"] == first["item"]["id"]
    assert len(config_artifacts.list_artifacts(repo)) == 1, "同输入不该多出一份产物"


def test_new_baseline_revision_makes_new_artifact(repo, world) -> None:
    """键里带基线修订号：同一个基线改了值就是新输入，必须重新生成。"""
    first = _approved(
        repo, world, values={world["pulse"]["id"]: {"value": "700", "intent": "override"}}
    )
    one = config_artifacts.generate_artifact(repo, ACTOR, profile_id="p1", baseline_id=first["id"])

    second = _approve(
        repo, first, values={world["pulse"]["id"]: {"value": "800", "intent": "override"}}
    )
    two = config_artifacts.generate_artifact(repo, ACTOR, profile_id="p1", baseline_id=second["id"])
    assert two["reused"] is False
    assert two["item"]["id"] != one["item"]["id"]
    assert two["item"]["determinism_key"] != one["item"]["determinism_key"]
    assert two["item"]["blob_sha256"] != one["item"]["blob_sha256"]


def test_draft_baseline_cannot_generate(repo, world) -> None:
    with pytest.raises(AppError) as err:
        config_artifacts.generate_artifact(
            repo, ACTOR, profile_id="p1", baseline_id=world["baseline"]["id"]
        )
    assert err.value.code == INVALID_INPUT
    assert err.value.detail["state"] == "draft"


def test_baseline_of_other_profile_is_not_found(repo, world) -> None:
    """机型基线不能从别的机型路径生成（通用基线不在此限）。"""
    definition_set = world["definition_set"]
    model_baseline = bl.create_baseline(
        repo,
        ACTOR,
        scope="model",
        release_id=world["release"]["id"],
        profile_id="p9",
        definition_set_id=definition_set["id"],
        upstream_baseline_id=world["baseline"]["id"],
        reason="别的机型",
    )
    approved = _approve(repo, model_baseline)
    with pytest.raises(AppError) as err:
        config_artifacts.generate_artifact(
            repo, ACTOR, profile_id="p1", baseline_id=approved["id"]
        )
    assert err.value.code == NOT_FOUND


# -- 下载 -------------------------------------------------------------------


def test_repeat_download_is_byte_identical(repo, world) -> None:
    approved = _approved(
        repo, world, values={world["pulse"]["id"]: {"value": "700", "intent": "override"}}
    )
    item = config_artifacts.generate_artifact(
        repo, ACTOR, profile_id="p1", baseline_id=approved["id"]
    )["item"]
    first = config_artifacts.artifact_bytes(repo, item["id"])
    second = config_artifacts.artifact_bytes(repo, item["id"])
    assert first == second
    assert sha256_bytes(first) == item["blob_sha256"]
    assert len(first) == item["byte_size"]


def test_download_reads_blob_not_regenerate(repo, world, monkeypatch) -> None:
    """把生成函数打成"一调就炸"，下载仍然能拿到字节——证明它读的是 blob。"""
    approved = _approved(
        repo, world, values={world["pulse"]["id"]: {"value": "700", "intent": "override"}}
    )
    item = config_artifacts.generate_artifact(
        repo, ACTOR, profile_id="p1", baseline_id=approved["id"]
    )["item"]

    def boom(*args, **kwargs):  # pragma: no cover - 调到就是回归
        raise AssertionError("下载路径不应重新生成")

    monkeypatch.setattr(config_export, "generate", boom)
    assert config_artifacts.artifact_bytes(repo, item["id"]).startswith(b"<?xml")


def test_detail_carries_validation(repo, world) -> None:
    approved = _approved(repo, world)
    item = config_artifacts.generate_artifact(
        repo, ACTOR, profile_id="p1", baseline_id=approved["id"]
    )["item"]
    detail = config_artifacts.detail(repo, item["id"])
    assert detail["validation"]["ok"] is True
    assert detail["revisions"] == ["r1"]
    assert detail["delivery_status"] == "unverified"


def test_delivery_is_unverified_until_d6(repo, world) -> None:
    """D6 未解除：产物能生成，但"真实系统导入并记录结果"这一步没有证据。"""
    approved = _approved(repo, world)
    item = config_artifacts.generate_artifact(
        repo, ACTOR, profile_id="p1", baseline_id=approved["id"]
    )["item"]
    assert item["delivery_status"] == config_artifacts.DELIVERY_UNVERIFIED
