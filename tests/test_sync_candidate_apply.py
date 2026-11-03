"""M16-28 候选决议单事务写入与幂等回执。"""

from __future__ import annotations

import uuid

import pytest

from app.core import storage
from app.core.errors import INVALID_INPUT, REVISION_CONFLICT, AppError
from app.services import (
    config_v3_catalog,
    config_v3_drafts,
    sync_candidate_apply,
    sync_candidate_plans,
    sync_candidates,
    workspace_catalog,
)


def typed(kind, text):
    return {"type": kind, "text": text, "unit": None}


def make_config(count=10):
    wid = workspace_catalog.create_workspace("W-apply-" + uuid.uuid4().hex[:8])["id"]
    pids = []
    with storage.use_workspace(wid):
        config = config_v3_catalog.create_config("default", {"code": "C1", "name": "Config"})
        cid = config["config_id"]
        for index in range(count):
            pids.append(
                config_v3_catalog.create_parameter(
                    "default", cid, {"name": "P%d" % index, "directory": ["G"], "type": "int"}
                )["parameter_id"]
            )
        release = config_v3_catalog.create_release("default", cid, {"version": "1"})
    return wid, cid, "d0-" + release["release_id"], pids


def seed_current(wid, cid, did, pids, value="1"):
    items = [
        {
            "parameter_id": pid,
            "membership": "included",
            "value_state": "present",
            "value": value,
        }
        for pid in pids
    ]
    with storage.use_workspace(wid):
        config_v3_drafts.patch_values(
            "default", cid, did, {"request_id": "seed", "items": items}, if_match='"0"'
        )


def values(pids, text="2"):
    return {pid: typed("int", text) for pid in pids}


def make_preview(wid, cid, did, pids, current_text="1", base_text="1", candidate_text="2"):
    seed_current(wid, cid, did, pids, current_text)
    return sync_candidate_plans.create_preview(
        wid,
        "default",
        cid,
        did,
        "cand-apply",
        values(pids, base_text),
        values(pids, candidate_text),
    )


def test_ten_params_apply_in_one_transaction_and_keep_head():
    wid, cid, did, pids = make_config(10)
    preview = make_preview(wid, cid, did, pids)
    with storage.use_workspace(wid):
        before = config_v3_drafts.get_draft("default", cid, did)
        head_before = (
            config_v3_catalog._load("default")["configs"][cid].get("main_branch") or {}
        ).get("head_id")

    result = sync_candidate_apply.apply_preview(
        wid, "default", preview["preview_id"], "apply-1", {}
    )
    assert result["status"] == "applied"
    assert len(result["changed"]) == 10
    assert result["draft_revision"] == before["revision"] + 1

    with storage.use_workspace(wid):
        after = config_v3_drafts.get_draft("default", cid, did)
        head_after = (
            config_v3_catalog._load("default")["configs"][cid].get("main_branch") or {}
        ).get("head_id")
    assert after["revision"] == before["revision"] + 1
    assert all(after["values"][pid]["value"] == "2" for pid in pids)
    assert head_after == head_before

    again = sync_candidate_apply.apply_preview(
        wid, "default", preview["preview_id"], "apply-1", {}
    )
    assert again["replayed"] is True
    assert again["draft_revision"] == result["draft_revision"]
    assert sync_candidate_apply.get_apply(wid, preview["preview_id"], "apply-1")["changed"] == result["changed"]


def test_apply_marks_candidate_applied_and_replay_keeps_it_stable():
    """M16-36：写入草稿后候选置 applied；幂等重放不重复写、不抖动回执时间。"""
    wid, cid, did, pids = make_config(2)
    seed_current(wid, cid, did, pids)
    candidate = sync_candidates.create_candidate(
        wid, "default", "src-apply", 1, "f" * 64, "default:%s" % cid
    )
    assert candidate["state"] == "pending"
    preview = sync_candidate_plans.create_preview(
        wid,
        "default",
        cid,
        did,
        candidate["candidate_id"],
        values(pids, "1"),
        values(pids, "2"),
    )

    result = sync_candidate_apply.apply_preview(wid, "default", preview["preview_id"], "apply-mark", {})
    marked = sync_candidates.get_candidate(wid, candidate["candidate_id"])
    assert marked["state"] == "applied"
    assert marked["applied_revision"] == result["draft_revision"]
    assert marked["applied_at"] == result["applied_at"]

    again = sync_candidate_apply.apply_preview(wid, "default", preview["preview_id"], "apply-mark", {})
    assert again["replayed"] is True
    still = sync_candidates.get_candidate(wid, candidate["candidate_id"])
    assert still["state"] == "applied"
    assert still["applied_at"] == marked["applied_at"]
    with storage.use_workspace(wid):
        draft = config_v3_drafts.get_draft("default", cid, did)
    assert draft["revision"] == result["draft_revision"]


def test_apply_without_candidate_record_still_writes_draft():
    """预览可能引用尚未落库的候选 id：写入草稿不能被状态回写拖失败。"""
    wid, cid, did, pids = make_config(2)
    preview = make_preview(wid, cid, did, pids)
    result = sync_candidate_apply.apply_preview(wid, "default", preview["preview_id"], "apply-nocand", {})
    assert result["status"] == "applied"
    assert result["changed"]


def test_invalid_type_rejects_all_writes():
    wid, cid, did, pids = make_config(10)
    seed_current(wid, cid, did, pids)
    candidate = values(pids)
    candidate[pids[-1]] = typed("string", "not-int")
    preview = sync_candidate_plans.create_preview(
        wid, "default", cid, did, "cand-bad", values(pids, "1"), candidate
    )
    assert preview["can_apply"] is False
    with pytest.raises(AppError) as exc:
        sync_candidate_apply.apply_preview(wid, "default", preview["preview_id"], "bad", {})
    assert exc.value.code == INVALID_INPUT
    with storage.use_workspace(wid):
        draft = config_v3_drafts.get_draft("default", cid, did)
    assert draft["revision"] == 1
    assert all(draft["values"][pid]["value"] == "1" for pid in pids)


def test_conflict_decision_and_different_body_same_key():
    wid, cid, did, pids = make_config(1)
    seed_current(wid, cid, did, pids, "2")
    preview = sync_candidate_plans.create_preview(
        wid,
        "default",
        cid,
        did,
        "cand-conflict",
        values(pids, "1"),
        values(pids, "3"),
    )
    assert any(item["action"] == "conflict" for item in preview["items"])
    with pytest.raises(AppError):
        sync_candidate_apply.apply_preview(wid, "default", preview["preview_id"], "k1", {})
    result = sync_candidate_apply.apply_preview(
        wid, "default", preview["preview_id"], "k1", {pids[0]: "use_new"}
    )
    assert result["changed"] == [pids[0]]
    with storage.use_workspace(wid):
        draft = config_v3_drafts.get_draft("default", cid, did)
    assert draft["values"][pids[0]]["value"] == "3"

    with pytest.raises(AppError) as exc:
        sync_candidate_apply.apply_preview(
            wid, "default", preview["preview_id"], "k1", {pids[0]: "inherit_old"}
        )
    assert exc.value.code == REVISION_CONFLICT


def test_head_change_and_frozen_reject_before_write():
    wid, cid, did, pids = make_config(1)
    preview = make_preview(wid, cid, did, pids)
    with storage.use_workspace(wid):
        doc = config_v3_catalog._load("default")
        config = doc["configs"][cid]
        config.setdefault("main_branch", {})["head_id"] = "bl-changed"
        config_v3_catalog._save("default", doc)
    with pytest.raises(AppError) as exc:
        sync_candidate_apply.apply_preview(wid, "default", preview["preview_id"], "head", {})
    assert exc.value.code == REVISION_CONFLICT

    wid2, cid2, did2, pids2 = make_config(1)
    preview2 = make_preview(wid2, cid2, did2, pids2)
    with storage.use_workspace(wid2):
        doc = config_v3_catalog._load("default")
        release = doc["configs"][cid2]["releases"][did2[3:]]
        release.setdefault("d0", {"values": {}, "frozen": False, "revision": 0})["frozen"] = True
        config_v3_catalog._save("default", doc)
    with pytest.raises(AppError) as exc:
        sync_candidate_apply.apply_preview(wid2, "default", preview2["preview_id"], "frozen", {})
    assert exc.value.code == REVISION_CONFLICT
    with storage.use_workspace(wid2):
        draft = config_v3_drafts.get_draft("default", cid2, did2)
    assert draft["revision"] == 1


def test_no_op_decisions_do_not_create_a_draft_revision():
    wid, cid, did, pids = make_config(1)
    seed_current(wid, cid, did, pids, "1")
    preview = sync_candidate_plans.create_preview(
        wid, "default", cid, did, "cand-keep", values(pids, "1"), values(pids, "1")
    )
    result = sync_candidate_apply.apply_preview(
        wid, "default", preview["preview_id"], "noop", {}
    )
    assert result["changed"] == []
    with storage.use_workspace(wid):
        assert config_v3_drafts.get_draft("default", cid, did)["revision"] == 1


def test_frozen_d0_apply_creates_baseline_draft_and_leaves_d0():
    wid, cid, did, pids = make_config(1)
    seed_current(wid, cid, did, pids, "1")
    with storage.use_workspace(wid):
        config_v3_drafts.freeze_default("default", cid, did, {"request_id": "freeze-1"})
        before = config_v3_drafts.get_draft("default", cid, did)
    preview = sync_candidate_plans.create_preview(
        wid, "default", cid, did, "cand-frozen-apply",
        values(pids, "1"), values(pids, "2"),
    )
    assert preview["d0_frozen"] is True
    result = sync_candidate_apply.apply_preview(
        wid, "default", preview["preview_id"], "baseline-1", {}, create_baseline=True
    )
    assert result["target"] == "baseline_draft"
    assert result["changed"]
    with storage.use_workspace(wid):
        after = config_v3_drafts.get_draft("default", cid, did)
        doc = config_v3_catalog._load("default")
        release = next(iter(doc["configs"][cid]["releases"].values()))
        draft = release["baseline_draft"]
    assert after["revision"] == before["revision"]
    assert after.get("frozen") is True or release["d0"]["frozen"] is True
    member = draft["values"][pids[0]]
    assert member["value"] == "2"
    assert release["d0"]["values"][pids[0]]["value"] == "1"
