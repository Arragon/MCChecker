"""配置仓储的存储层行为（M5-03 / INH-1937）。

覆盖卡面验收：

* 先写不可变对象、最后公布 state；state 未替换时新对象不可见；
* 内容寻址 blob 幂等，损坏可诊断，`cache/` 可删；
* 原 `data/profiles/` 不受影响（本模块只写 `data/config_management/`）；
* 损坏的 state / 引用断裂不得被当成空状态。
"""

from __future__ import annotations

import json
import os

import pytest

from app.core import config_repository as cr
from app.core.errors import AppError, CorruptDataError


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


def _commit_one(repo, kind="family", payload=None, **kwargs):
    payload = payload or {"name": "轴参数", "status": "active"}
    tx = repo.transaction({"ip": "127.0.0.1", "name": "本机"}, "测试写入", **kwargs)
    ref = tx.put(kind, payload)
    receipt = tx.commit()
    return ref, receipt


def test_initialize_creates_layout_and_is_idempotent(repo) -> None:
    for sub in ("objects", "blobs", "audit", "receipts", "cache"):
        assert os.path.isdir(os.path.join(repo.root, sub))
    before = open(repo.state_path, "rb").read()
    repo.initialize()
    assert open(repo.state_path, "rb").read() == before


def test_write_then_read_roundtrip(repo) -> None:
    ref, receipt = _commit_one(repo)
    assert receipt.seq == 1
    assert ref.id == "cf_000001"
    assert ref.revision == "r1"
    payload = repo.get("family", ref.id)
    assert payload["name"] == "轴参数"
    assert payload["revision"] == "r1"
    assert payload["kind"] == "family"


def test_revisions_are_append_only(repo) -> None:
    ref, _ = _commit_one(repo)
    tx = repo.transaction({"ip": "127.0.0.1"}, "改状态")
    second = tx.put("family", {"name": "轴参数", "status": "retired"}, object_id=ref.id)
    tx.commit()
    assert second.revision == "r2"
    assert repo.get("family", ref.id)["status"] == "retired"
    assert repo.get("family", ref.id, "r1")["status"] == "active", "旧修订不得被改写"
    assert repo.history("family", ref.id) == ["r1", "r2"]


def test_blob_is_content_addressed_and_deduped(repo) -> None:
    tx = repo.transaction({"ip": "127.0.0.1"}, "存样本")
    first = tx.blob(b"<xml>same</xml>")
    second = tx.blob(b"<xml>same</xml>")
    tx.commit()
    assert first == second
    blobs = []
    for _root, _dirs, files in os.walk(os.path.join(repo.root, "blobs")):
        blobs.extend(files)
    assert blobs == [first]
    assert repo.read_blob(first) == b"<xml>same</xml>"


def test_blob_corruption_is_diagnosed_not_zeroed(repo) -> None:
    tx = repo.transaction({"ip": "127.0.0.1"}, "存样本")
    digest = tx.blob(b"<xml>tamper</xml>")
    tx.commit()
    path = repo.blob_path(digest)
    with open(path, "wb") as fh:
        fh.write(b"<xml>changed</xml>")
    with pytest.raises(CorruptDataError):
        repo.read_blob(digest)


def test_cache_directory_is_not_authoritative(repo) -> None:
    ref, _ = _commit_one(repo)
    cache = os.path.join(repo.root, "cache")
    for name in os.listdir(cache):
        os.unlink(os.path.join(cache, name))
    assert repo.get("family", ref.id)["name"] == "轴参数"


def test_missing_state_with_objects_is_corruption(tmp_path) -> None:
    repo = cr.ConfigRepository(str(tmp_path))
    ref, _ = _commit_one(repo)
    os.unlink(repo.state_path)
    with pytest.raises(CorruptDataError):
        repo.load_state()
    with pytest.raises(CorruptDataError):
        repo.initialize()


def test_unparsable_state_is_corruption(repo) -> None:
    with open(repo.state_path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    with pytest.raises(CorruptDataError):
        repo.load_state()


def test_state_pointing_to_missing_object_is_corruption(repo) -> None:
    ref, _ = _commit_one(repo)
    os.unlink(repo.object_path("family", ref.id, "r1"))
    with pytest.raises(CorruptDataError):
        repo.get("family", ref.id)


def test_writes_do_not_touch_legacy_profiles_tree(tmp_path) -> None:
    """旧 data/profiles 的内容一个字节都不该变（D1：两个写者互不 import）。"""
    legacy = tmp_path / "profiles" / "default"
    legacy.mkdir(parents=True)
    marker = legacy / "records.json"
    marker.write_text('{"keep": true}', encoding="utf-8")
    before = marker.read_bytes()

    repo = cr.ConfigRepository(str(tmp_path))
    ref, _ = _commit_one(repo)

    assert marker.read_bytes() == before
    assert os.path.isdir(tmp_path / "config_management")
    assert not os.path.exists(os.path.join(repo.root, "profiles"))


def test_verify_reports_clean_state(repo) -> None:
    _commit_one(repo)
    report = repo.verify()
    assert report["ok"] is True
    assert report["objects"] == 1
    assert report["orphans"] == []


def test_verify_lists_orphan_objects(repo) -> None:
    ref, _ = _commit_one(repo)
    orphan = repo.object_path("family", "cf_999999", "r9")
    os.makedirs(os.path.dirname(orphan), exist_ok=True)
    with open(orphan, "w", encoding="utf-8") as fh:
        json.dump({"kind": "family", "id": "cf_999999", "revision": "r9"}, fh)
    report = repo.verify()
    assert report["ok"] is True, "孤儿不是错误，但必须可列"
    assert any("cf_999999" in path for path in report["orphans"])


def test_object_id_must_match_kind(repo) -> None:
    tx = repo.transaction({"ip": "127.0.0.1"}, "坏 ID")
    with pytest.raises(AppError) as error:
        tx.put("family", {"name": "x"}, object_id="pm_000001")
    assert error.value.code == "INVALID_INPUT"


def test_family_status_is_not_policed_by_storage(repo) -> None:
    """仓储只管结构，不管业务枚举（枚举由 services 层校验），避免两处规则漂移。"""
    ref, _ = _commit_one(repo, payload={"name": "x", "status": "whatever"})
    assert repo.get("family", ref.id)["status"] == "whatever"
