"""Incoming updates compare the actual existing standard draft, with CAS."""
import pytest
from app.core import storage
from app.core.errors import AppError, REVISION_CONFLICT
from app.services import config_v3_baselines, config_v3_drafts, sync_candidate_apply, sync_candidate_plans, sync_candidates
from test_sync_candidate_apply import make_config, seed_current, values


def test_frozen_standard_candidate_previews_existing_draft_and_cannot_overwrite_a_later_edit():
    wid, cid, did, pids = make_config(1)
    seed_current(wid, cid, did, pids, "1")
    with storage.use_workspace(wid):
        config_v3_drafts.freeze_default("default", cid, did, {"expected_revision": 1, "request_id": "freeze"})
        draft = config_v3_baselines.create_baseline_draft("default", cid, did[3:])
        def write(value, revision, key):
            return config_v3_baselines.patch_baseline_values("default", cid, draft["draft_id"], {"expected_revision": revision, "request_id": key, "items": [{"parameter_id": pids[0], "membership": "included", "value_state": "present", "value": value}]}, None)
        write("9", 0, "first")
        preview = sync_candidate_plans.create_preview(wid, "default", cid, did, "candidate-existing-draft", values(pids, "1"), values(pids, "2"))
        assert preview["current"][pids[0]]["value"]["text"] == "9"
        assert preview["saved"][pids[0]]["value"]["text"] == "1"
        assert preview["needs_decision"] == pids and preview["editing_draft_id"] == draft["draft_id"]
        write("11", 1, "later")
        with pytest.raises(AppError) as stale:
            sync_candidate_apply.apply_preview(wid, "default", preview["preview_id"], "stale", {pids[0]: "use_new"}, True)
        assert stale.value.code == REVISION_CONFLICT
        preview = sync_candidate_plans.create_preview(wid, "default", cid, did, "candidate-existing-draft", values(pids, "1"), values(pids, "2"))
        result = sync_candidate_apply.apply_preview(wid, "default", preview["preview_id"], "keep", {pids[0]: "inherit_old"}, True)
        assert result["draft_id"] == draft["draft_id"]
        assert config_v3_baselines.get_baseline_draft("default", cid, draft["draft_id"])["values"][pids[0]]["value"] == "11"
        assert config_v3_drafts.get_draft("default", cid, did)["values"][pids[0]]["value"] == "1"


def test_candidate_stays_pending_until_formally_saved_values_match_the_incoming_content():
    wid, cid, did, pids = make_config(1)
    seed_current(wid, cid, did, pids, "1")
    candidate = sync_candidates.create_candidate(wid, "default", "src-one", 1, "a" * 64, "default_candidate:%s:%s" % (cid, did[3:]))
    with storage.use_workspace(wid):
        config_v3_drafts.freeze_default("default", cid, did, {"expected_revision": 1, "request_id": "freeze"})
        preview = sync_candidate_plans.create_preview(wid, "default", cid, did, candidate["candidate_id"], values(pids, "1"), values(pids, "2"))
        result = sync_candidate_apply.apply_preview(wid, "default", preview["preview_id"], "accept", {}, True)
        pending = sync_candidates.list_candidates(wid, profile_id="default", configuration_id=cid, pending_only=True)
        assert pending["total"] == 1 and pending["items"][0]["review_state"] == "awaiting_save"
        assert pending["items"][0]["remaining_parameter_ids"] == pids
        config_v3_baselines.publish_baseline("default", cid, result["draft_id"], {"expected_revision": result["draft_revision"], "request_id": "save-incoming"}, None)
        assert sync_candidates.list_candidates(wid, profile_id="default", configuration_id=cid, pending_only=True)["total"] == 0
        all_rows = sync_candidates.list_candidates(wid, profile_id="default", configuration_id=cid)
        assert all_rows["items"][0]["state"] == "applied" and all_rows["items"][0]["review_state"] == "saved"
