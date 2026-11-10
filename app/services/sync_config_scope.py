"""Read-only file/configuration ownership for update projections.

Source registrations can point to a configuration or its unique file. Historical
candidate/run ownership comes from frozen targets, never today's source binding.
"""
from ..core import storage
from . import config_v3_catalog


def file_id(workspace_id, profile_id, config_id):
    if not profile_id or not config_id:
        return None
    with storage.use_workspace(workspace_id), storage.use_profile(profile_id):
        config = config_v3_catalog._load(profile_id)["configs"].get(config_id)
    return config.get("file_id") if config else None


def matches(target, config_id, owned_file):
    return (target.get("configuration_id") == config_id or
            bool(owned_file and target.get("kind") == "file_archive" and target.get("file_id") == owned_file))


def candidate_matches(candidate, config_id):
    return str(candidate.get("target_scope") or "").startswith("default_candidate:" + config_id + ":")
