"""Derived review state: writing a draft does not confirm incoming parameters.

No candidate state-machine changes or writes. Confirmation compares the incoming
values captured by a real preview with the latest saved standard of that release.
"""
from ..core import storage
from . import config_v3_baselines, config_v3_catalog, config_v3_merge, sync_store


def annotate(workspace_id, rows):
    from . import sync_candidate_plans
    previews = sync_store.read_doc(workspace_id, sync_candidate_plans.FILE, {"items": {}})
    by_candidate = {}
    for preview in (previews.get("items") or {}).values():
        key = preview.get("candidate_id")
        if key and (key not in by_candidate or preview.get("created_at", "") >= by_candidate[key].get("created_at", "")):
            by_candidate[key] = preview
    catalogs = {}
    for row in rows:
        if row["state"] in ("superseded", "dismissed"):
            continue
        row["review_state"] = "awaiting_save" if row["state"] == "applied" else "needs_review"
        preview = by_candidate.get(row["candidate_id"])
        if not preview or not preview.get("candidate"):
            continue
        pid = row["profile_id"]
        if pid not in catalogs:
            with storage.use_workspace(workspace_id), storage.use_profile(pid):
                catalogs[pid] = config_v3_catalog._load(pid)
        config = catalogs[pid]["configs"].get(preview["config_id"])
        release = (config or {}).get("releases", {}).get(str(preview.get("draft_id", ""))[3:])
        if not config or not release or not release.get("default_revision_id"):
            row["remaining_parameter_ids"] = list(preview["candidate"])
            continue
        saved = config_v3_baselines.latest_saved_baseline(config, release["release_id"])
        values = saved["values"] if saved else release["d0"].get("frozen_snapshot") or {}
        definitions = config_v3_merge._definitions(config, saved["definition_revision"] if saved else release["definition_revision"])
        current = sync_candidate_plans._current_entries({"values": values}, definitions)
        remaining = [key for key, value in preview["candidate"].items()
                     if not sync_candidate_plans._same_value(current.get(key), value)]
        row["remaining_parameter_ids"] = remaining
        blockers = [b for b in preview.get("blockers") or [] if b.get("code") not in ("d0_frozen", "base_missing")]
        if not remaining and not blockers:
            row["review_state"] = "saved"
    return rows
