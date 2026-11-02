"""M16-07 来源目标绑定：FileResource/FileVersion 与 V3 配置版本。

只解析/校验目标身份，不下载、不改文件、不发布。file_archive 以
`file_id` 绑定当前 FileVersion；default_candidate 绑定
configuration_id + release_id（可再带 file_id）；machine_observation
必须有可信 device_id，不能按文件名/IP 猜机台。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core import storage
from app.core.errors import INVALID_INPUT, NOT_FOUND, SOURCE_CHANGED, AppError
from app.core.sync_models import (
    SyncTarget,
    validate_id,
    validate_profile_id,
    validate_workspace_id,
)

from . import config_v3_catalog, config_v3_provenance, file_versions, workspace_context


def _target(value: Any) -> SyncTarget:
    if isinstance(value, SyncTarget):
        return value
    if isinstance(value, dict):
        return SyncTarget.from_dict(value)
    raise AppError(INVALID_INPUT, "target 必须是对象", field="target")


def _file_ref(workspace_id: str, profile_id: str, file_id: str) -> Dict[str, Any]:
    fid = validate_id(file_id, "file_id")
    with storage.use_workspace(workspace_id), storage.use_profile(profile_id):
        for row in file_versions.list_resources(profile_id):
            if row.get("file_id") == fid:
                return config_v3_provenance.make_ref(profile_id, row["name"], "current")
    raise AppError(NOT_FOUND, "目标 FileResource 不存在或已删除", field="file_id")


def assert_catalog_match(
    catalog: Optional[Dict[str, str]],
    resource_ref: Optional[str],
    display_path: Optional[str],
) -> None:
    if not resource_ref and not display_path:
        return
    if not resource_ref or not display_path:
        raise AppError(
            INVALID_INPUT,
            "resource_ref 与 display_path 必须同时提供",
            field="resource_ref",
        )
    ref = validate_id(resource_ref, "resource_ref")
    path = str(display_path).strip()
    if not path:
        raise AppError(INVALID_INPUT, "display_path 不能为空", field="display_path")
    if catalog is not None:
        expected = catalog.get(ref)
        if expected is None or str(expected) != path:
            raise AppError(
                SOURCE_CHANGED,
                "catalog 资源引用与显示路径不一致",
                field="resource_ref",
            )


def resolve_binding(
    workspace_id: str,
    profile_id: str,
    target: Any,
    catalog: Optional[Dict[str, str]] = None,
    resource_ref: Optional[str] = None,
    display_path: Optional[str] = None,
) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    pid = validate_profile_id(profile_id)
    workspace_context.assert_profile(wid, pid)
    binding_target = _target(target)
    assert_catalog_match(catalog, resource_ref, display_path)

    if binding_target.kind == "file_archive":
        ref = _file_ref(wid, pid, binding_target.file_id or "")
        return {
            "target": binding_target.to_dict(),
            "ref": ref,
            "file_id": ref["file_id"],
            "version_id": ref["version_id"],
            "blob_sha256": ref["blob_sha256"],
        }

    if binding_target.kind == "default_candidate":
        with storage.use_workspace(wid), storage.use_profile(pid):
            config = config_v3_catalog.get_config(pid, binding_target.configuration_id or "")
            releases = config_v3_catalog.list_releases(pid, binding_target.configuration_id or "")
        release = next(
            (row for row in releases if row.get("release_id") == binding_target.release_id),
            None,
        )
        if release is None:
            raise AppError(NOT_FOUND, "目标软件版本不存在", field="release_id")
        result = {
            "target": binding_target.to_dict(),
            "configuration_id": config["config_id"],
            "configuration_code": config.get("code"),
            "release_id": release["release_id"],
            "release_version": release.get("version"),
        }
        if binding_target.file_id:
            ref = _file_ref(wid, pid, binding_target.file_id)
            result.update(
                {
                    "ref": ref,
                    "file_id": ref["file_id"],
                    "version_id": ref["version_id"],
                    "blob_sha256": ref["blob_sha256"],
                }
            )
        return result

    if binding_target.kind == "machine_observation":
        return {
            "target": binding_target.to_dict(),
            "device_id": binding_target.device_id,
            "profile_id": pid,
            "release_id": binding_target.release_id,
        }

    raise AppError(INVALID_INPUT, "未知目标类型", field="target.kind")


def assert_binding_current(workspace_id: str, profile_id: str, binding: Dict[str, Any]) -> Dict[str, Any]:
    wid = validate_workspace_id(workspace_id)
    pid = validate_profile_id(profile_id)
    if not isinstance(binding, dict):
        raise AppError(INVALID_INPUT, "binding 必须是对象", field="binding")
    target = _target(binding.get("target"))
    if target.kind == "file_archive":
        ref = binding.get("ref")
        if not isinstance(ref, dict):
            raise AppError(INVALID_INPUT, "file binding 缺少 ref", field="ref")
        verified = config_v3_provenance.verify_ref(ref)
        if verified["file_id"] != binding.get("file_id"):
            raise AppError(SOURCE_CHANGED, "目标文件身份已变化", field="file_id")
        return dict(binding, ref=ref, **verified)
    if target.kind == "default_candidate":
        return resolve_binding(wid, pid, target)
    if target.kind == "machine_observation":
        return dict(binding)
    raise AppError(INVALID_INPUT, "未知目标类型", field="target.kind")
