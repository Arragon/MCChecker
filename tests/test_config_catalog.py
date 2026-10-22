"""配置类别、结构变体、稳定逻辑文件与来源关联（M6-02 / INH-1947）。

卡面验收逐条对应：

* `pid + filename + kind + version` 精确解析到 `FileSnapshot` →
  `test_resolve_is_exact_per_role` / `test_same_filename_in_two_profiles_does_not_collide`
* XML/JSON 是同一业务配置的不同表示，一个名称不得同时充当两种 →
  `test_same_name_cannot_be_both_representations`
* 改名保留稳定 ID 与历史别名 → `test_rename_keeps_id_and_history_alias`
* 旧归档关联到不可变原文而非 current → `test_snapshot_is_immutable_and_hash_addressed`
* 结构变体按 fingerprint 判定 → `test_value_edit_keeps_signature` /
  `test_structure_change_makes_new_variant`
* 退役/改名后按旧关联可查历史 → `test_retired_file_keeps_history_by_old_name`
"""

from __future__ import annotations

import os

import pytest

from app.core import config_repository as cr
from app.core.errors import AppError
from app.services import config_catalog

ACTOR = {"ip": "127.0.0.1", "name": "测试管理员"}

SAMPLE_DIR = os.path.join(
    os.path.dirname(__file__), "fixtures", "config_management", "samples"
)


def _sample(name: str) -> bytes:
    with open(os.path.join(SAMPLE_DIR, name), "rb") as handle:
        return handle.read()


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


@pytest.fixture()
def setup(repo):
    """一个版本 + 一个配置族 + 一个 XML 逻辑文件。"""
    release = config_catalog.register_release(repo, ACTOR, version_label="1.1.0", build="20260901")
    family = config_catalog.create_family(repo, ACTOR, name="轴参数", code="AXIS_PARAM")
    logical_file = config_catalog.create_logical_file(
        repo, ACTOR, family_id=family["id"], role="xml_export", display_name="轴参数.xml"
    )
    return {"release": release, "family": family, "logical_file": logical_file}


# -- 配置族 -----------------------------------------------------------------


def test_family_code_is_uppercased_and_unique(repo) -> None:
    family = config_catalog.create_family(repo, ACTOR, name="轴参数", code="axis_param")
    assert family["code"] == "AXIS_PARAM"
    assert family["status"] == "active"
    with pytest.raises(AppError) as caught:
        config_catalog.create_family(repo, ACTOR, name="重复", code="AXIS_PARAM")
    assert caught.value.code == "DEFINITION_CONFLICT"
    assert caught.value.detail["family_id"] == family["id"]


def test_invalid_family_code_is_rejected(repo) -> None:
    with pytest.raises(AppError) as caught:
        config_catalog.create_family(repo, ACTOR, name="轴参数", code="1AXIS")
    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.detail["field"] == "code"


# -- 逻辑文件：稳定 ID、别名与表示 ---------------------------


def test_rename_keeps_id_and_history_alias(repo, setup) -> None:
    original = setup["logical_file"]
    renamed = config_catalog.rename_logical_file(
        repo, ACTOR, file_id=original["id"], new_name="轴参数表.xml", reason="对齐新命名"
    )
    assert renamed["id"] == original["id"]
    assert renamed["display_name"] == "轴参数表.xml"
    open_aliases = [item["name"] for item in renamed["aliases"] if item.get("until") in (None, "")]
    assert open_aliases == ["轴参数表.xml"]
    closed = [item for item in renamed["aliases"] if item.get("until")]
    assert [item["name"] for item in closed] == ["轴参数.xml"]
    assert repo.history("logical_file", original["id"]) == ["r1", "r2"]

    # 旧名仍解析到同一稳定 ID
    found = config_catalog.resolve_logical_file(repo, setup["family"]["id"], "xml_export", "轴参数.xml")
    assert found is not None and found["id"] == original["id"]
    found_new = config_catalog.resolve_logical_file(
        repo, setup["family"]["id"], "xml_export", "轴参数表.xml"
    )
    assert found_new["id"] == original["id"]


def test_rename_to_existing_name_is_conflict(repo, setup) -> None:
    other = config_catalog.create_logical_file(
        repo, ACTOR, family_id=setup["family"]["id"], role="xml_export", display_name="另一份.xml"
    )
    with pytest.raises(AppError) as caught:
        config_catalog.rename_logical_file(
            repo, ACTOR, file_id=other["id"], new_name="轴参数.xml"
        )
    assert caught.value.code == "DEFINITION_CONFLICT"
    with pytest.raises(AppError) as same:
        config_catalog.rename_logical_file(
            repo, ACTOR, file_id=other["id"], new_name="另一份.xml"
        )
    assert same.value.code == "INVALID_INPUT"


def test_same_name_cannot_be_both_representations(repo, setup) -> None:
    with pytest.raises(AppError) as caught:
        config_catalog.create_logical_file(
            repo,
            ACTOR,
            family_id=setup["family"]["id"],
            role="field_json",
            display_name="轴参数.xml",
        )
    assert caught.value.code == "DEFINITION_CONFLICT"
    assert caught.value.detail["conflicting_role"] == "xml_export"

    # 换个名字（同一业务配置的另一种表示）就能登记
    json_file = config_catalog.create_logical_file(
        repo, ACTOR, family_id=setup["family"]["id"], role="field_json", display_name="轴参数.json"
    )
    assert json_file["role"] == "field_json"

    # 改名也不能绕过：JSON 文件改成 XML 的名字一样被拦
    with pytest.raises(AppError) as renamed:
        config_catalog.rename_logical_file(
            repo, ACTOR, file_id=json_file["id"], new_name="轴参数.xml"
        )
    assert renamed.value.code == "DEFINITION_CONFLICT"


def test_retired_file_keeps_history_by_old_name(repo, setup) -> None:
    logical_file = setup["logical_file"]
    snapshot = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=setup["release"]["id"],
        logical_file_id=logical_file["id"],
        data=_sample("v1_1_A.xml"),
        original_filename="轴参数.xml",
        source="archive",
        archive_ref={"pid": "A", "filename": "轴参数.xml", "archive_name": "20260901.xml"},
    )
    config_catalog.rename_logical_file(
        repo, ACTOR, file_id=logical_file["id"], new_name="轴参数表.xml"
    )
    retired = config_catalog.retire_logical_file(
        repo, ACTOR, file_id=logical_file["id"], reason="合并到新类别"
    )
    assert retired["status"] == "retired"

    # 按旧名仍能找到对象，并且历史快照照旧可查
    found = config_catalog.resolve_logical_file(
        repo, setup["family"]["id"], "xml_export", "轴参数.xml"
    )
    assert found["id"] == logical_file["id"]
    assert found["status"] == "retired"
    history = config_catalog.list_snapshots(repo, logical_file_id=logical_file["id"])
    assert [item["id"] for item in history] == [snapshot["id"]]
    assert config_catalog.snapshot_bytes(repo, snapshot["id"]) == _sample("v1_1_A.xml")


def test_resolve_is_exact_per_role(repo, setup) -> None:
    json_file = config_catalog.create_logical_file(
        repo, ACTOR, family_id=setup["family"]["id"], role="field_json", display_name="轴参数.json"
    )
    assert (
        config_catalog.resolve_logical_file(
            repo, setup["family"]["id"], "field_json", "轴参数.xml"
        )
        is None
    )
    assert (
        config_catalog.resolve_logical_file(
            repo, setup["family"]["id"], "xml_export", "轴参数.json"
        )
        is None
    )
    assert (
        config_catalog.resolve_logical_file(repo, setup["family"]["id"], "xml_export", "")
        is None
    )
    assert (
        config_catalog.resolve_logical_file(
            repo, setup["family"]["id"], "field_json", "轴参数.json"
        )["id"]
        == json_file["id"]
    )


def test_invalid_role_is_rejected(repo, setup) -> None:
    with pytest.raises(AppError) as caught:
        config_catalog.create_logical_file(
            repo, ACTOR, family_id=setup["family"]["id"], role="binary", display_name="x.bin"
        )
    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.detail["field"] == "role"


# -- 文件快照：按机型不串、内容寻址、不可变 ---------------------------------


def test_same_filename_in_two_profiles_does_not_collide(repo, setup) -> None:
    snapshot_a = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=setup["release"]["id"],
        logical_file_id=setup["logical_file"]["id"],
        data=_sample("v1_0_A.xml"),
        original_filename="轴参数.xml",
        source="archive",
        archive_ref={"pid": "A", "filename": "轴参数.xml", "archive_name": "20260901_A.xml"},
    )
    snapshot_b = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=setup["release"]["id"],
        logical_file_id=setup["logical_file"]["id"],
        data=_sample("v1_0_B.xml"),
        original_filename="轴参数.xml",
        source="archive",
        archive_ref={"pid": "B", "filename": "轴参数.xml", "archive_name": "20260901_B.xml"},
    )
    assert snapshot_a["id"] != snapshot_b["id"]
    assert snapshot_a["blob_sha256"] != snapshot_b["blob_sha256"]
    assert snapshot_a["archive_ref"]["pid"] == "A"
    assert snapshot_b["archive_ref"]["pid"] == "B"
    # 版本相同也不合并：同一个版本里 A/B 机的同名文件是两份证据
    listed = config_catalog.list_snapshots(repo, release_id=setup["release"]["id"])
    assert len(listed) == 2


def test_snapshot_dedupes_identical_content(repo, setup) -> None:
    first = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=setup["release"]["id"],
        logical_file_id=setup["logical_file"]["id"],
        data=_sample("v1_1_A.xml"),
        original_filename="轴参数.xml",
        source="archive",
        archive_ref={"pid": "A", "filename": "轴参数.xml", "archive_name": "20260901.xml"},
    )
    again = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=setup["release"]["id"],
        logical_file_id=setup["logical_file"]["id"],
        data=_sample("v1_1_A.xml"),
        original_filename="轴参数.xml",
        source="archive",
        archive_ref={"pid": "A", "filename": "轴参数.xml", "archive_name": "20260902.xml"},
    )
    assert again["id"] == first["id"]
    assert repo.history("file_snapshot", first["id"]) == ["r1"]
    assert len(config_catalog.list_snapshots(repo)) == 1


def test_snapshot_is_immutable_and_hash_addressed(repo, setup) -> None:
    data = _sample("v1_1_A.xml")
    snapshot = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=setup["release"]["id"],
        logical_file_id=setup["logical_file"]["id"],
        data=data,
        original_filename="轴参数.xml",
        source="upload",
    )
    assert snapshot["byte_size"] == len(data)
    assert snapshot["kind_detail"] == "xml_export"
    assert config_catalog.snapshot_bytes(repo, snapshot["id"]) == data
    assert repo.blob_path(snapshot["blob_sha256"]).endswith(snapshot["blob_sha256"])
    # 归档关联指向的原文就是不可变 blob，不会随 current 漂移
    assert cr.sha256_bytes(config_catalog.snapshot_bytes(repo, snapshot["id"])) == snapshot[
        "blob_sha256"
    ]


def test_empty_payload_is_rejected(repo, setup) -> None:
    with pytest.raises(AppError) as caught:
        config_catalog.register_snapshot(
            repo,
            ACTOR,
            release_id=setup["release"]["id"],
            logical_file_id=setup["logical_file"]["id"],
            data=b"",
            original_filename="轴参数.xml",
        )
    assert caught.value.code == "INVALID_INPUT"


# -- 结构变体 ---------------------------------------------------------------


def test_value_edit_keeps_signature(repo, setup) -> None:
    template = _sample("v1_0_A.xml")
    edited = template.replace(b">120.5<", b">130.0<").replace(b">true<", b">false<")
    assert edited != template

    first = config_catalog.ensure_variant(
        repo,
        ACTOR,
        family_id=setup["family"]["id"],
        release_id=setup["release"]["id"],
        template_snapshot_id=config_catalog.register_snapshot(
            repo,
            ACTOR,
            release_id=setup["release"]["id"],
            logical_file_id=setup["logical_file"]["id"],
            data=template,
            original_filename="轴参数.xml",
            source="upload",
        )["id"],
    )
    second = config_catalog.ensure_variant(
        repo,
        ACTOR,
        family_id=setup["family"]["id"],
        release_id=setup["release"]["id"],
        template_snapshot_id=config_catalog.register_snapshot(
            repo,
            ACTOR,
            release_id=setup["release"]["id"],
            logical_file_id=setup["logical_file"]["id"],
            data=edited,
            original_filename="轴参数.xml",
            source="upload",
        )["id"],
    )
    assert first["id"] == second["id"], "只改取值的模板仍然是同一个变体"
    assert len(config_catalog.list_variants(repo)) == 1


def test_structure_change_makes_new_variant(repo, setup) -> None:
    old = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=setup["release"]["id"],
        logical_file_id=setup["logical_file"]["id"],
        data=_sample("v1_0_A.xml"),
        original_filename="轴参数.xml",
        source="upload",
    )
    new = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=setup["release"]["id"],
        logical_file_id=setup["logical_file"]["id"],
        data=_sample("v1_1_A.xml"),
        original_filename="轴参数.xml",
        source="upload",
    )
    first = config_catalog.ensure_variant(
        repo,
        ACTOR,
        family_id=setup["family"]["id"],
        release_id=setup["release"]["id"],
        template_snapshot_id=old["id"],
    )
    second = config_catalog.ensure_variant(
        repo,
        ACTOR,
        family_id=setup["family"]["id"],
        release_id=setup["release"]["id"],
        template_snapshot_id=new["id"],
    )
    assert first["id"] != second["id"]
    assert first["structure_signature"] != second["structure_signature"]
    assert first["template_snapshot_id"] == old["id"]
    assert second["template_snapshot_id"] == new["id"]
    assert len(config_catalog.list_variants(repo, family_id=setup["family"]["id"])) == 2


def test_variant_requires_existing_template(repo, setup) -> None:
    with pytest.raises(AppError) as caught:
        config_catalog.ensure_variant(
            repo,
            ACTOR,
            family_id=setup["family"]["id"],
            release_id=setup["release"]["id"],
            template_snapshot_id="fs_999999",
        )
    assert caught.value.code == "NOT_FOUND"


def test_broken_xml_is_not_a_template(repo, setup) -> None:
    snapshot = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=setup["release"]["id"],
        logical_file_id=setup["logical_file"]["id"],
        data=b"<MachineConfig><Axis></MachineConfig>",
        original_filename="轴参数.xml",
        source="upload",
    )
    with pytest.raises(AppError) as caught:
        config_catalog.ensure_variant(
            repo,
            ACTOR,
            family_id=setup["family"]["id"],
            release_id=setup["release"]["id"],
            template_snapshot_id=snapshot["id"],
        )
    assert caught.value.code == "PARSE_FAILED"
    assert config_catalog.list_variants(repo) == []


def test_catalog_summary_counts_head_objects(repo, setup) -> None:
    config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=setup["release"]["id"],
        logical_file_id=setup["logical_file"]["id"],
        data=_sample("v1_0_A.xml"),
        original_filename="轴参数.xml",
        source="upload",
    )
    summary = config_catalog.catalog_summary(repo)
    assert summary["releases"] == 1
    assert summary["families"] == 1
    assert summary["logical_files"] == 1
    assert summary["snapshots"] == 1
    assert summary["variants"] == 0
    assert summary["latest_release"]["id"] == setup["release"]["id"]
