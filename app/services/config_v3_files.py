"""Project the existing file library and explicitly associated configurations."""

from typing import Any, Dict

from ..core import storage
from ..core.errors import INVALID_INPUT, AppError
from . import config_v3_catalog, file_versions, sync_candidates


def list_files(profile_id: str, query: str = "", offset: str = "0", limit: str = "50") -> Dict[str, Any]:
    try:
        start, size = int(offset or 0), int(limit or 50)
    except (TypeError, ValueError):
        raise AppError(INVALID_INPUT, "分页参数需为整数", field="limit")
    if start < 0 or not 1 <= size <= 100:
        raise AppError(INVALID_INPUT, "每页需为 1–100 项，起始位置不能小于零", field="limit")
    with storage.use_profile(profile_id), storage.profile_write(profile_id):
        configs = config_v3_catalog.list_configs(profile_id)
        resources = file_versions.list_resources(profile_id)
    by_file = {item["file_id"]: item for item in configs if item.get("file_id")}
    pending = {}
    cursor = "0"
    while cursor is not None:
        # Legacy storage uses an empty scope; Sync uses the explicit default ID.
        page = sync_candidates.list_candidates(storage.get_active_workspace() or "default", profile_id=profile_id, pending_only=True, limit=200, cursor=cursor)
        for candidate in page["items"]:
            parts = candidate["target_scope"].split(":")
            if len(parts) == 3 and parts[0] == "default_candidate":
                pending[parts[1]] = pending.get(parts[1], 0) + 1
        cursor = page["next_cursor"]
    for config in configs:
        config["pending_updates"] = pending.get(config["config_id"], 0)
    needle = str(query or "").strip().casefold()
    rows = []
    for resource in resources:
        config = by_file.get(resource["file_id"])
        if needle and needle not in (resource["name"] + " " + (config or {}).get("name", "")).casefold():
            continue
        rows.append(dict(resource, configuration=config))
    rows.sort(key=lambda item: (item["name"].casefold(), item["file_id"]))
    return {"items": rows[start:start + size], "total": len(rows), "offset": start, "limit": size}
