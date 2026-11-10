"""Discard an exact active draft; immutable records and files are never removed."""
import copy

from ..core import locks, storage
from ..core.errors import IDEMPOTENCY_MISMATCH, INVALID_INPUT, NOT_FOUND, REVISION_CONFLICT, AppError
from . import config_v3_baselines, config_v3_catalog, sync_sources, sync_store


def discard(profile_id, config_id, payload):
    did, request_id, expected = payload.get("draft_id"), payload.get("request_id"), payload.get("expected_revision")
    if not isinstance(did, str) or not did or not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 100:
        raise AppError(INVALID_INPUT, "请选择草稿并提供本次操作标识", field="draft_id")
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        raise AppError(INVALID_INPUT, "请提供核对时的草稿版本", field="expected_revision")
    wid = storage.get_active_workspace()
    # Sync candidate writes already use workspace -> profile order. Holding the
    # same workspace lock prevents a registered source being orphaned mid-delete.
    with sync_store.workspace_lock(wid), locks.write_gate.write(), storage.use_profile(profile_id), storage.profile_write(profile_id):
        doc = config_v3_catalog._load(profile_id)
        config = doc["configs"].get(config_id)
        if config is None:
            raise AppError(NOT_FOUND, "配置不存在", config_id=config_id)
        receipts = config.setdefault("discard_receipts", {})
        receipt = receipts.get(request_id)
        if receipt:
            if receipt["draft_id"] != did or receipt["expected_revision"] != expected:
                raise AppError(IDEMPOTENCY_MISMATCH, "本次操作已用于另一份草稿", field="request_id")
            return dict(copy.deepcopy(receipt["result"]), replayed=True)
        release, draft = config_v3_catalog.configuration_record(config, did)
        if did.startswith("d0-"):
            draft = release["d0"]
            if draft.get("frozen"):
                raise AppError(REVISION_CONFLICT, "已保存配置不能作为草稿放弃")
            kind, machine = "version", None
        elif did == (release.get("baseline_draft") or {}).get("draft_id") and draft.get("status") == "draft":
            kind, machine = "standard", None
        else:
            machine = next((m for m in (config.get("machines") or {}).values()
                            if (m.get("draft") or {}).get("draft_id") == did and m["draft"].get("status") == "draft"), None)
            if machine is None:
                raise AppError(NOT_FOUND, "没有这份可放弃的草稿", draft_id=did)
            draft, kind = machine["draft"], "machine"
        if int(draft.get("revision", 0)) != expected:
            raise AppError(REVISION_CONFLICT, "草稿已变化，请重新核对后放弃", current=draft.get("revision", 0))
        rid = release["release_id"]
        result = {"draft_id": did, "kind": kind, "release_id": rid, "removed_object": False, "return_revision_id": None}
        if kind == "version":
            if any(r.get("predecessor_id") == rid for r in config["releases"].values()):
                raise AppError(REVISION_CONFLICT, "还有软件版本引用此版本，请先调整版本关系")
            if any(m.get("release_id") == rid for m in (config.get("machines") or {}).values()):
                raise AppError(REVISION_CONFLICT, "还有机台使用此版本，不能移除")
            if sync_sources.list_sources(wid, profile_id=profile_id, configuration_id=config_id, release_id=rid, limit=1)["total"]:
                raise AppError(REVISION_CONFLICT, "此版本仍有更新来源，请先移除对应来源")
            del config["releases"][rid]
            result["removed_object"] = True
        elif kind == "standard":
            release["baseline_draft"] = None
            saved = config_v3_baselines.latest_saved_baseline(config, rid)
            result["return_revision_id"] = saved["baseline_id"] if saved else release.get("default_revision_id")
        else:
            result["machine_id"] = machine["machine_id"]
            head = (machine.get("revisions") or {}).get(machine.get("head_id"))
            if head:
                machine["draft"] = None
                machine["release_id"] = head["release_id"]
                result["return_revision_id"] = head["revision_id"]
            elif machine.get("revisions"):
                raise AppError(REVISION_CONFLICT, "机台已有保存记录，需要先核对当前配置")
            else:
                del config["machines"][machine["machine_id"]]
                result["removed_object"] = True
        receipts[request_id] = {"draft_id": did, "expected_revision": expected, "result": copy.deepcopy(result)}
        config["updated_at"] = config_v3_catalog._now()
        config_v3_catalog._save(profile_id, doc)
        return result
