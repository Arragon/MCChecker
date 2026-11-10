"""Read-only configuration indexes for the V3.2 file/version workspace."""

from typing import Any, Dict

from ..core import storage
from ..core.errors import INVALID_INPUT, AppError
from . import config_v3_baselines, config_v3_catalog


def _draft(row, release_id):
    if not row or row.get("status") != "draft":
        return None
    return {
        "draft_id": row["draft_id"],
        "revision": row.get("revision", 0),
        "release_id": release_id,
        "source_revision_id": row.get("source_revision_id") or row.get("base_revision_id"),
    }


def list_index(profile_id: str, kind: str, filters: Dict[str, Any]) -> Dict[str, Any]:
    try:
        offset, limit = int(filters.get("offset") or 0), int(filters.get("limit") or 50)
    except (TypeError, ValueError):
        raise AppError(INVALID_INPUT, "分页参数需为整数", field="limit")
    if offset < 0 or not 1 <= limit <= 100:
        raise AppError(INVALID_INPUT, "每页需为 1–100 项，起始位置不能小于零", field="limit")
    state = filters.get("status") or "all"
    if state not in {"all", "draft", "saved", "unsaved"}:
        raise AppError(INVALID_INPUT, "不支持的配置状态", field="status")
    with storage.use_profile(profile_id):
        catalog = config_v3_catalog._load(profile_id)
    items = []
    for config in catalog["configs"].values():
        if filters.get("config_id") and config["config_id"] != filters["config_id"]:
            continue
        identity = {"config_id": config["config_id"], "config_name": config["name"]}
        releases = config.get("releases") or {}
        if kind == "standards":
            for release in releases.values():
                saved = config_v3_baselines.latest_saved_baseline(config, release["release_id"])
                revision_id = saved["baseline_id"] if saved else release.get("default_revision_id")
                draft = _draft(release.get("baseline_draft"), release["release_id"])
                if not release.get("default_revision_id"):
                    draft = {"draft_id": "d0-" + release["release_id"],
                             "release_id": release["release_id"],
                             "revision": release["d0"].get("revision", 0),
                             "source_revision_id": None}
                items.append(dict(identity, release_id=release["release_id"], version=release["version"],
                    order=release["order"], initial_revision_id=config_v3_catalog.initial_revision_id(release),
                    saved={"revision_id": revision_id,
                           "kind": "baseline" if saved else "default",
                           "published_at": saved["published_at"] if saved else release.get("default_frozen_at")}
                          if revision_id else None,
                    draft=draft))
        else:
            for machine in (config.get("machines") or {}).values():
                saved = (machine.get("revisions") or {}).get(machine.get("head_id"))
                release_id = saved.get("release_id") if saved else None
                release = releases.get(release_id) or {}
                items.append(dict(identity, machine_id=machine["machine_id"], machine_no=machine["machine_no"],
                    release_id=release_id, version=release.get("version"),
                    saved={"revision_id": saved["revision_id"], "published_at": saved["published_at"],
                           "source_revision_id": saved.get("base_revision_id")} if saved else None,
                    draft=_draft(machine.get("draft"), machine["release_id"])))
    query = str(filters.get("q") or "").strip().casefold()
    items = [item for item in items
             if (not filters.get("release_id") or item["release_id"] == filters["release_id"])
             and (not query or query in " ".join(str(item.get(key) or "")
                  for key in ("config_name", "version", "machine_no")).casefold())
             and (state == "all" or state == "draft" and item["draft"]
                  or state == "saved" and item["saved"] or state == "unsaved" and not item["saved"])]
    items.sort(key=lambda item: (item["config_id"], item.get("order", 0), item.get("machine_no", ""),
                                item.get("machine_id") or item["release_id"]))
    return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit}
