"""软件版本登记与不可变出口批次（M6-01 / INH-1946）。

卡面验收逐条对应：

* 显式前驱 + build，缺来源置"待确认" → `test_register_release_records_explicit_predecessor`
  / `test_missing_archive_ref_marks_unconfirmed`
* 同版本补发**新增批次**不覆盖原件 → `test_reissue_creates_new_batch_and_keeps_old_bytes`
* 不接受仅凭文件名/mtime 推断版本 → `test_version_is_never_inferred_from_filename`
* 补发后旧默认 XML 哈希与下载内容不变 → `test_old_default_snapshot_bytes_are_untouched`
* 无模板可归档 JSON 但不可标"可生成" → `test_json_snapshot_has_no_structure_signature`
"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.errors import AppError
from app.services import config_catalog

ACTOR = {"ip": "127.0.0.1", "name": "测试管理员"}

XML_V1 = b'<MachineConfig xmlns="urn:mc:cfg:1" model="A"><Retry>3</Retry></MachineConfig>'
XML_V1_REISSUE = (
    b'<MachineConfig xmlns="urn:mc:cfg:1" model="A"><Retry>4</Retry></MachineConfig>'
)
JSON_FIELD = b'{"RETRY": 3}'


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


def _family(repo, code="AXIS_PARAM", name="轴参数"):
    for item in config_catalog.list_families(repo):
        if item["code"] == code:
            return item
    return config_catalog.create_family(repo, ACTOR, name=name, code=code)


def _logical_file(repo, family_id, role="xml_export", name="轴参数.xml"):
    found = config_catalog.resolve_logical_file(repo, family_id, role, name)
    if found is not None:
        return found
    return config_catalog.create_logical_file(
        repo, ACTOR, family_id=family_id, role=role, display_name=name
    )


def _snapshot(
    repo,
    release_id,
    data=XML_V1,
    *,
    pid="A",
    filename="轴参数.xml",
    role="xml_export",
    archive_name="20260901_101500.xml",
    source="archive",
):
    family = _family(repo)
    logical_file = _logical_file(repo, family["id"], role=role, name=filename)
    archive_ref = None
    if source == "archive":
        archive_ref = {"pid": pid, "filename": filename, "archive_name": archive_name}
    return config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=release_id,
        logical_file_id=logical_file["id"],
        data=data,
        original_filename=filename,
        source=source,
        archive_ref=archive_ref,
    )


# -- 版本登记 ---------------------------------------------------------------


def test_register_release_records_explicit_predecessor(repo) -> None:
    first = config_catalog.register_release(
        repo, ACTOR, version_label="1.0.0", build="20260601", source="manual"
    )
    second = config_catalog.register_release(
        repo,
        ACTOR,
        version_label="1.1.0",
        build="20260901",
        predecessor_id=first["id"],
        notes="Pulse 改名为 PulseWidth",
    )
    assert first["status"] == "active"
    assert first["predecessor_id"] is None
    assert second["predecessor_id"] == first["id"]
    assert second["build"] == "20260901"
    assert second["source"] == "manual"
    assert second["revision"] == "r1"

    listed = config_catalog.list_releases(repo)
    assert [item["id"] for item in listed] == [second["id"], first["id"]]
    assert config_catalog.get_release(repo, second["id"])["id"] == second["id"]


def test_version_order_is_numeric_not_lexicographic(repo) -> None:
    for label in ("1.9.0", "1.10.0", "1.2.0"):
        config_catalog.register_release(repo, ACTOR, version_label=label)
    labels = [item["version_label"] for item in config_catalog.list_releases(repo)]
    assert labels == ["1.10.0", "1.9.0", "1.2.0"]


def test_duplicate_version_and_build_is_conflict(repo) -> None:
    original = config_catalog.register_release(repo, ACTOR, version_label="1.0.0", build="1")
    with pytest.raises(AppError) as caught:
        config_catalog.register_release(repo, ACTOR, version_label="1.0.0", build="1")
    assert caught.value.code == "DEFINITION_CONFLICT"
    assert caught.value.detail["release_id"] == original["id"]


def test_same_label_with_different_build_is_a_new_release(repo) -> None:
    one = config_catalog.register_release(repo, ACTOR, version_label="1.0.0", build="1")
    two = config_catalog.register_release(repo, ACTOR, version_label="1.0.0", build="2")
    assert one["id"] != two["id"]


def test_unknown_predecessor_is_rejected(repo) -> None:
    with pytest.raises(AppError) as caught:
        config_catalog.register_release(
            repo, ACTOR, version_label="1.1.0", predecessor_id="sr_999999"
        )
    assert caught.value.code == "NOT_FOUND"
    assert config_catalog.list_releases(repo) == []


def test_invalid_source_and_status_are_rejected(repo) -> None:
    with pytest.raises(AppError) as source_error:
        config_catalog.register_release(repo, ACTOR, version_label="1.0.0", source="filename")
    assert source_error.value.code == "INVALID_INPUT"
    with pytest.raises(AppError) as status_error:
        config_catalog.register_release(repo, ACTOR, version_label="1.0.0", status="unknown")
    assert status_error.value.code == "INVALID_INPUT"


def test_version_is_never_inferred_from_filename(repo) -> None:
    """没有"按文件名/归档目录名猜版本"的入口——这是卡面的硬约束。"""
    for name in (
        "release_from_filename",
        "infer_release",
        "guess_release",
        "release_from_archive_name",
    ):
        assert not hasattr(config_catalog, name), "不允许存在推断版本的入口：%s" % name

    release = config_catalog.register_release(repo, ACTOR, version_label="1.0.0")
    with pytest.raises(AppError) as caught:
        config_catalog.register_snapshot(
            repo,
            ACTOR,
            release_id="sr_999999",
            logical_file_id="lf_000001",
            data=XML_V1,
            original_filename="20260901.xml",
        )
    assert caught.value.code == "NOT_FOUND"
    # 归档目录名与版本号无关：快照里两处都留存，但不互相冒充
    snapshot = _snapshot(repo, release["id"], archive_name="20260901_101500.xml")
    assert snapshot["release_id"] == release["id"]
    assert snapshot["archive_ref"]["archive_name"] == "20260901_101500.xml"
    assert "20260901" not in release["version_label"]


# -- 出口批次 ---------------------------------------------------------------


def test_default_batch_is_none_before_first_publish(repo) -> None:
    release = config_catalog.register_release(repo, ACTOR, version_label="1.0.0")
    assert config_catalog.default_batch(repo, release["id"]) is None
    detail = config_catalog.release_detail(repo, release["id"])
    assert detail["default_batch"] is None
    assert detail["snapshots"] == []
    assert detail["batch_count"] == 0


def test_publish_default_batch_points_release_default(repo) -> None:
    release = config_catalog.register_release(repo, ACTOR, version_label="1.0.0")
    snapshot = _snapshot(repo, release["id"])

    batch = config_catalog.publish_default_batch(
        repo, ACTOR, release_id=release["id"], snapshot_ids=[snapshot["id"]], note="首轮出口"
    )
    assert batch["release_id"] == release["id"]
    assert batch["snapshot_ids"] == [snapshot["id"]]
    assert batch["supersedes"] is None
    assert batch["created_by"] == "测试管理员"

    head = config_catalog.default_batch(repo, release["id"])
    assert head["id"] == batch["id"]
    assert config_catalog.batch_snapshots(repo, head)[0]["id"] == snapshot["id"]


def test_reissue_creates_new_batch_and_keeps_old_bytes(repo) -> None:
    release = config_catalog.register_release(repo, ACTOR, version_label="1.1.0")
    first_snapshot = _snapshot(repo, release["id"], XML_V1)
    first_batch = config_catalog.publish_default_batch(
        repo, ACTOR, release_id=release["id"], snapshot_ids=[first_snapshot["id"]]
    )
    old_bytes = config_catalog.snapshot_bytes(repo, first_snapshot["id"])
    old_hash = first_snapshot["blob_sha256"]

    second_snapshot = _snapshot(
        repo, release["id"], XML_V1_REISSUE, archive_name="20260905_090000.xml"
    )
    second_batch = config_catalog.publish_default_batch(
        repo, ACTOR, release_id=release["id"], snapshot_ids=[second_snapshot["id"]], note="补发"
    )

    # 新批次是**新对象**，旧批次不被改写
    assert second_batch["id"] != first_batch["id"]
    assert second_batch["supersedes"] == first_batch["id"]
    assert config_catalog.default_batch(repo, release["id"])["id"] == second_batch["id"]

    kept = repo.get("release_batch", first_batch["id"])
    assert kept["revision"] == "r1"
    assert repo.history("release_batch", first_batch["id"]) == ["r1"]
    assert kept["snapshot_ids"] == [first_snapshot["id"]]

    # 旧默认 XML 的哈希与下载内容一个字节都没变
    assert config_catalog.snapshot_bytes(repo, first_snapshot["id"]) == old_bytes
    assert config_catalog.get_snapshot(repo, first_snapshot["id"])["blob_sha256"] == old_hash
    assert repo.history("file_snapshot", first_snapshot["id"]) == ["r1"]
    assert old_bytes == XML_V1

    assert len(config_catalog.release_batches(repo, release["id"])) == 2


def test_batch_rejects_foreign_snapshot(repo) -> None:
    one = config_catalog.register_release(repo, ACTOR, version_label="1.0.0")
    two = config_catalog.register_release(repo, ACTOR, version_label="1.1.0")
    snapshot = _snapshot(repo, one["id"])
    with pytest.raises(AppError) as caught:
        config_catalog.publish_default_batch(
            repo, ACTOR, release_id=two["id"], snapshot_ids=[snapshot["id"]]
        )
    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.detail["snapshot_release_id"] == one["id"]
    assert config_catalog.default_batch(repo, two["id"]) is None


def test_batch_requires_at_least_one_snapshot(repo) -> None:
    release = config_catalog.register_release(repo, ACTOR, version_label="1.0.0")
    for payload in ([], "", None, ["  "]):
        with pytest.raises(AppError) as caught:
            config_catalog.publish_default_batch(
                repo, ACTOR, release_id=release["id"], snapshot_ids=payload
            )
        assert caught.value.code == "INVALID_INPUT"


def test_batch_deduplicates_snapshot_ids(repo) -> None:
    release = config_catalog.register_release(repo, ACTOR, version_label="1.0.0")
    snapshot = _snapshot(repo, release["id"])
    batch = config_catalog.publish_default_batch(
        repo, ACTOR, release_id=release["id"], snapshot_ids=[snapshot["id"], snapshot["id"]]
    )
    assert batch["snapshot_ids"] == [snapshot["id"]]


def test_missing_archive_ref_marks_unconfirmed(repo) -> None:
    release = config_catalog.register_release(repo, ACTOR, version_label="1.0.0")
    family = _family(repo)
    logical_file = _logical_file(repo, family["id"])
    snapshot = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=release["id"],
        logical_file_id=logical_file["id"],
        data=XML_V1,
        original_filename="轴参数.xml",
        source="archive",
        archive_ref={"pid": "A", "filename": "轴参数.xml", "archive_name": ""},
    )
    assert snapshot["status"] == "unconfirmed"
    assert snapshot["provenance"]["resolution"] == "unconfirmed"
    assert snapshot["archive_ref"] == {
        "pid": "A",
        "filename": "轴参数.xml",
        "archive_name": "",
    }

    uploaded = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=release["id"],
        logical_file_id=logical_file["id"],
        data=XML_V1_REISSUE,
        original_filename="轴参数.xml",
        source="upload",
    )
    assert uploaded["status"] == "confirmed"
    assert uploaded["archive_ref"] is None

    assert len(config_catalog.list_snapshots(repo, status="unconfirmed")) == 1


def test_json_snapshot_has_no_structure_signature(repo) -> None:
    """JSON 可以归档，但没有结构指纹，因此不能被当成"可生成"的模板。"""
    release = config_catalog.register_release(repo, ACTOR, version_label="1.0.0")
    family = _family(repo)
    json_file = _logical_file(repo, family["id"], role="field_json", name="轴参数.json")
    snapshot = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=release["id"],
        logical_file_id=json_file["id"],
        data=JSON_FIELD,
        original_filename="轴参数.json",
        source="archive",
        archive_ref={"pid": "A", "filename": "轴参数.json", "archive_name": "20260901.json"},
    )
    assert snapshot["kind_detail"] == "field_json"

    with pytest.raises(AppError) as caught:
        config_catalog.ensure_variant(
            repo,
            ACTOR,
            family_id=family["id"],
            release_id=release["id"],
            template_snapshot_id=snapshot["id"],
        )
    assert caught.value.code == "PARSE_FAILED"
    assert config_catalog.list_variants(repo) == []


def test_retire_release_keeps_history(repo) -> None:
    release = config_catalog.register_release(repo, ACTOR, version_label="1.0.0")
    retired = config_catalog.retire_release(repo, ACTOR, release_id=release["id"], reason="换型")
    assert retired["status"] == "retired"
    assert retired["retired_at"]
    assert repo.history("software_release", release["id"]) == ["r1", "r2"]
    # 退役是幂等的：再调一次不再产生新修订
    again = config_catalog.retire_release(repo, ACTOR, release_id=release["id"])
    assert again["revision"] == "r2"
    assert repo.history("software_release", release["id"]) == ["r1", "r2"]
