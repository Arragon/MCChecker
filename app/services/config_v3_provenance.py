"""精确来源引用（M11-04 / INH-2133）。

引用形状固定为 `workspace_id + profile_id + file_id + version_id + sha256`；
选 current 时另记 `expected_current_version_id`，提交前可检测 current 已变。
文件删除后 tombstone 命中，旧引用返回 `NOT_FOUND(missing=True)`，不会因为
同名重新上传而复活；同名的另一个 W 也无关。
"""

from __future__ import annotations

from typing import Any, Dict

from ..core import storage
from ..core.errors import NOT_FOUND, SOURCE_CHANGED, AppError
from . import documents, file_versions


def make_ref(profile_id: str, name: str, version: str = "current") -> Dict[str, Any]:
    ident = file_versions.identity(profile_id, name, version)
    ref = {
        "workspace_id": ident["workspace_id"],
        "profile_id": profile_id,
        "name": name,
        "file_id": ident["file_id"],
        "version_id": ident["version_id"],
        "blob_sha256": ident["blob_sha256"],
        "ref": ident["ref"],
        "version_label": ident["version_label"],
        "source_kind": ident["source_kind"],
    }
    if ident["source_kind"] == "current":
        ref["expected_current_version_id"] = ident["version_id"]
    return ref


def verify_ref(ref: Dict[str, Any]) -> Dict[str, Any]:
    """按引用重新读取来源；任何漂移都抛错，绝不按同名回填。"""
    wid = str(ref.get("workspace_id") or "default")
    pid = str(ref.get("profile_id") or "")
    name = str(ref.get("name") or "")
    version_id = str(ref.get("version_id") or "")
    file_id_value = str(ref.get("file_id") or "")
    with storage.use_workspace(wid), storage.use_profile(pid):
        if file_versions.is_missing(pid, file_id_value, version_id):
            raise AppError(NOT_FOUND, "来源文件已删除", missing=True, file_id=file_id_value)
        current = file_versions.current_identity(pid, name)
        if current["file_id"] != file_id_value:
            raise AppError(NOT_FOUND, "来源文件已删除或已更换", missing=True, file_id=file_id_value)
        ref_version = str(ref.get("ref") or "current")
        if ref_version == "current":
            expected = str(ref.get("expected_current_version_id") or version_id)
            if current["version_id"] != expected:
                raise AppError(SOURCE_CHANGED, "来源文件当前版本已变化", expected=expected)
        raw = documents.load_raw(pid, name, ref_version)
        if str(ref.get("blob_sha256") or raw.sha256) != raw.sha256:
            raise AppError(SOURCE_CHANGED, "来源文件内容指纹已变化", expected=ref.get("blob_sha256"))
        return {
            "workspace_id": wid,
            "profile_id": pid,
            "file_id": file_id_value,
            "version_id": version_id,
            "blob_sha256": raw.sha256,
            "source_kind": ref.get("source_kind"),
            "version_label": ref.get("version_label"),
        }


def assert_usable(ref: Dict[str, Any]) -> Dict[str, Any]:
    """extract/import/export 的入口门禁；语义与 verify_ref 相同。"""
    return verify_ref(ref)
