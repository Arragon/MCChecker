"""配置事务：CAS、持久幂等、审计与故障注入（M5-04 / INH-1938）。

卡面验收逐条对应：

* 一次提交同批公布"实体修订 + 审计事件 + 回执 + 指针" → `test_one_commit_publishes_all`;
* 锁内重取 state 并核验 `expected_revision` → `test_cas_rejects_stale_writer`;
* 同幂等键+同正文回执重放，异体 `IDEMPOTENCY_MISMATCH` → 两个 idempotency 用例；
* 磁盘失败/中断/重复请求/并发/孤儿 → 故障注入组；
* 维护态写入被拒 → `test_maintenance_gate_blocks_commit`。
"""

from __future__ import annotations

import json
import os
import threading

import pytest

from app.core import config_repository as cr
from app.core import fsutil
from app.core.errors import AppError, CorruptDataError
from app.core.locks import write_gate


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


def _actor():
    return {"ip": "127.0.0.1", "name": "本机"}


def test_one_commit_publishes_object_pointer_audit_and_receipt(repo) -> None:
    tx = repo.transaction(
        _actor(), "初始化默认批次", action="release_default.publish", idempotency_key="k-1"
    )
    batch = tx.put("release_batch", {"release_id": "sr_000001", "snapshot_ids": ["fs_000002"]})
    tx.pointer("release_default", "sr_000001", batch)
    receipt = tx.commit()

    assert receipt.seq == 1
    assert repo.pointer("release_default", "sr_000001") == batch.id
    assert repo.get("release_batch", batch.id)["snapshot_ids"] == ["fs_000002"]

    events = repo.audit_events()
    assert len(events["items"]) == 1
    event = events["items"][0]
    assert event["seq"] == 1
    assert event["action"] == "release_default.publish"
    assert event["objects"] == [{"kind": "release_batch", "id": batch.id, "revision": "r1"}]
    assert os.path.exists(repo.receipt_path("k-1"))


def test_seq_is_monotonic_and_head_tracks_latest(repo) -> None:
    first = repo.transaction(_actor(), "一")
    ref = first.put("family", {"name": "f1"})
    assert first.commit().seq == 1

    second = repo.transaction(_actor(), "二")
    second.put("family", {"name": "f1", "status": "retired"}, object_id=ref.id)
    assert second.commit().seq == 2
    assert repo.head_revision("family", ref.id) == "r2"
    assert repo.load_state()["seq"] == 2


def test_cas_rejects_stale_writer(repo) -> None:
    tx = repo.transaction(_actor(), "创建")
    ref = tx.put("family", {"name": "f1"})
    tx.commit()

    stale = repo.transaction(_actor(), "基于旧修订", expected={"family:%s" % ref.id: "r0"})
    stale.put("family", {"name": "f1", "status": "retired"}, object_id=ref.id)
    with pytest.raises(AppError) as error:
        stale.commit()
    assert error.value.code == "REVISION_CONFLICT"
    assert repo.head_revision("family", ref.id) == "r1", "冲突不得写入"
    assert len(repo.audit_events()["items"]) == 1


def test_cas_on_global_seq(repo) -> None:
    tx = repo.transaction(_actor(), "创建", expected={"seq": 0})
    tx.put("family", {"name": "f1"})
    tx.commit()

    again = repo.transaction(_actor(), "又一次", expected={"seq": 0})
    again.put("family", {"name": "f2"})
    with pytest.raises(AppError) as error:
        again.commit()
    assert error.value.code == "REVISION_CONFLICT"
    assert error.value.detail["actual_revision"] == 1


def test_idempotent_replay_returns_same_seq_and_ids(repo) -> None:
    def build():
        tx = repo.transaction(_actor(), "写样本", idempotency_key="idem-1")
        ref = tx.put("family", {"name": "f1"})
        return tx, ref

    first_tx, first_ref = build()
    first = first_tx.commit()

    second_tx, second_ref = build()
    second = second_tx.commit()

    assert second.replayed is True
    assert second.seq == first.seq == 1
    assert second_ref.id == first_ref.id
    assert second_ref.revision == first_ref.revision == "r1"
    assert len(repo.audit_events()["items"]) == 1, "重放不产生第二条审计"
    assert repo.list_objects("family")[0]["revision"] == "r1"


def test_same_key_different_body_is_rejected(repo) -> None:
    tx = repo.transaction(_actor(), "写样本", idempotency_key="idem-2")
    tx.put("family", {"name": "f1"})
    tx.commit()

    conflicting = repo.transaction(_actor(), "写样本", idempotency_key="idem-2")
    conflicting.put("family", {"name": "另一个名字"})
    with pytest.raises(AppError) as error:
        conflicting.commit()
    assert error.value.code == "IDEMPOTENCY_MISMATCH"


def test_state_write_failure_leaves_new_objects_invisible(repo, monkeypatch) -> None:
    """关键：state 替换失败时不许半发布。"""
    original = fsutil.atomic_write_json

    def boom(path, obj):
        if os.path.basename(path) == "state.json":
            raise OSError("模拟磁盘失败")
        return original(path, obj)

    monkeypatch.setattr(fsutil, "atomic_write_json", boom)

    tx = repo.transaction(_actor(), "会失败的一次")
    ref = tx.put("family", {"name": "半发布"})
    with pytest.raises(AppError) as error:
        tx.commit()
    assert error.value.code == "STORAGE_FAILURE"

    # 对象文件可能已落盘，但 state 未公布 ⇒ 不可见
    assert repo.head_revision("family", ref.id) is None
    assert repo.get("family", ref.id) is None
    assert repo.load_state()["seq"] == 0
    assert repo.audit_events()["items"] == []

    monkeypatch.setattr(fsutil, "atomic_write_json", original)
    report = repo.verify()
    assert report["ok"] is True
    assert any("family" in orphan for orphan in report["orphans"]), "失败写入应可被一致性检查列出"


def test_object_write_failure_does_not_advance_state(repo, monkeypatch) -> None:
    original = fsutil.atomic_write_json

    def boom(path, obj):
        if os.sep + "objects" + os.sep in path:
            raise OSError("模拟只读目录")
        return original(path, obj)

    monkeypatch.setattr(fsutil, "atomic_write_json", boom)
    tx = repo.transaction(_actor(), "写不进去")
    tx.put("family", {"name": "x"})
    with pytest.raises(AppError) as error:
        tx.commit()
    assert error.value.code == "STORAGE_FAILURE"
    assert repo.load_state()["seq"] == 0


def test_concurrent_writers_with_same_expectation_only_one_wins(repo) -> None:
    barrier = threading.Barrier(2)
    results = []

    def worker(index):
        barrier.wait()
        tx = repo.transaction(_actor(), "并发 %d" % index, expected={"seq": 0})
        tx.put("family", {"name": "f%d" % index})
        try:
            results.append(("ok", tx.commit().seq))
        except AppError as exc:
            results.append(("conflict", exc.code))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive(), "并发提交不得死锁"

    assert sorted(outcome for outcome, _ in results) == ["conflict", "ok"]
    assert repo.load_state()["seq"] == 1


def test_parallel_commits_without_cas_do_not_lose_updates(repo) -> None:
    errors = []

    def worker(index):
        try:
            tx = repo.transaction(_actor(), "并行 %d" % index)
            tx.put("family", {"name": "f%d" % index})
            tx.commit()
        except Exception as exc:  # noqa: BLE001 - 测试要把任何异常带回主线程
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert errors == []
    state = repo.load_state()
    assert state["seq"] == 8
    assert len(repo.list_objects("family")) == 8
    ids = [obj["id"] for obj in repo.list_objects("family")]
    assert len(set(ids)) == 8, "ID 分配不得撞号"


def test_maintenance_gate_blocks_commit(repo) -> None:
    tx = repo.transaction(_actor(), "维护期写入")
    tx.put("family", {"name": "x"})
    write_gate.enter_maintenance()
    try:
        with pytest.raises(AppError) as error:
            tx.commit()
        assert error.value.code == "MAINTENANCE"
    finally:
        write_gate.exit_maintenance()
    assert repo.load_state()["seq"] == 0

    retry = repo.transaction(_actor(), "维护结束后写入")
    retry.put("family", {"name": "x"})
    assert retry.commit().seq == 1


def test_audit_filters_and_pagination(repo) -> None:
    for index in range(5):
        tx = repo.transaction(_actor(), "第 %d 次" % index, action="baseline.approve")
        tx.put("baseline", {"scope": "common", "state": "approved"})
        tx.commit()

    page = repo.audit_events(limit=2)
    assert [item["seq"] for item in page["items"]] == [5, 4]
    assert page["next_before"] == 4

    page2 = repo.audit_events(limit=2, before_seq=page["next_before"])
    assert [item["seq"] for item in page2["items"]] == [3, 2]

    filtered = repo.audit_events(action="baseline.approve", limit=10)
    assert len(filtered["items"]) == 5
    assert repo.audit_events(action="nope")["items"] == []


def test_audit_filters_by_object_id(repo) -> None:
    tx = repo.transaction(_actor(), "创建基线")
    ref = tx.put("baseline", {"scope": "common"})
    tx.commit()
    other = repo.transaction(_actor(), "别的对象")
    other.put("family", {"name": "f"})
    other.commit()

    matched = repo.audit_events(object_id=ref.id)
    assert len(matched["items"]) == 1
    assert matched["items"][0]["reason"] == "创建基线"


def test_receipt_survives_process_restart(tmp_path) -> None:
    first = cr.ConfigRepository(str(tmp_path))
    tx = first.transaction(_actor(), "写一次", idempotency_key="restart-key")
    ref = tx.put("family", {"name": "f1"})
    tx.commit()

    second = cr.ConfigRepository(str(tmp_path))
    replay_tx = second.transaction(_actor(), "写一次", idempotency_key="restart-key")
    replay_ref = replay_tx.put("family", {"name": "f1"})
    receipt = replay_tx.commit()
    assert receipt.replayed is True
    assert replay_ref.id == ref.id
    assert second.load_state()["seq"] == 1


def test_audit_file_is_plain_json(repo) -> None:
    tx = repo.transaction(_actor(), "检查落盘格式")
    tx.put("family", {"name": "f1"})
    seq = tx.commit().seq
    with open(repo.audit_path(seq), "r", encoding="utf-8") as fh:
        event = json.load(fh)
    assert event["seq"] == seq
    assert event["actor"]["ip"] == "127.0.0.1"


def test_pointer_updates_are_isolated_per_name(repo) -> None:
    tx = repo.transaction(_actor(), "写指针")
    tx.pointer("release_default", "sr_1", "rb_000001")
    tx.pointer("current_baseline", "A", "bl_000002")
    tx.commit()
    assert repo.pointer("release_default", "sr_1") == "rb_000001"
    assert repo.pointer("current_baseline", "A") == "bl_000002"
    assert repo.pointer("missing", "x", "fallback") == "fallback"


def test_corrupt_state_blocks_further_commits(repo) -> None:
    with open(repo.state_path, "w", encoding="utf-8") as fh:
        fh.write("{broken")
    tx = repo.transaction(_actor(), "坏状态")
    tx.put("family", {"name": "x"})
    with pytest.raises(CorruptDataError):
        tx.commit()
