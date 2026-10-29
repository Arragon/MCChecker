"""M9-02 workspace catalog 服务测试（INH-2119）。"""

from __future__ import annotations

import json
import os

import pytest

from app.core import storage
from app.core.errors import AppError, CorruptDataError
from app.services import workspace_catalog


def test_legacy_workspace_is_synthetic_and_read_only_until_changed():
    path = workspace_catalog.catalog_path()
    items = workspace_catalog.list_workspaces()
    assert [w["id"] for w in items] == [workspace_catalog.LEGACY_WORKSPACE_ID]
    assert items[0]["name"] == workspace_catalog.LEGACY_WORKSPACE_NAME
    assert not os.path.exists(path), "首读不能在真实数据根落盘"
    assert workspace_catalog.workspace_root("default") == storage.PROFILES_DIR


def test_create_seeds_empty_default_and_profile_roots_differ():
    workspace = workspace_catalog.create_workspace("W2", "第二工作区")
    assert workspace["id"].startswith("w-")
    assert workspace["default_profile_id"] == "default"
    root = workspace_catalog.profile_root(workspace["id"], "default")
    assert os.path.isdir(root)
    assert root != workspace_catalog.profile_root("default", "default")
    assert len(workspace_catalog.list_workspaces()) == 2


def test_new_workspace_does_not_inherit_legacy_data():
    legacy = os.path.join(workspace_catalog.profile_root("default", "default"), "configs")
    os.makedirs(legacy, exist_ok=True)
    with open(os.path.join(legacy, "legacy.xml"), "w", encoding="utf-8") as handle:
        handle.write("<R/>")
    workspace = workspace_catalog.create_workspace("W2")
    fresh = os.path.join(workspace_catalog.profile_root(workspace["id"], "default"), "configs")
    assert not os.path.exists(os.path.join(fresh, "legacy.xml"))


def test_name_and_description_limits_and_duplicate():
    workspace_catalog.create_workspace("W2", "x" * 300)
    with pytest.raises(AppError) as exc:
        workspace_catalog.create_workspace("W2")
    assert exc.value.code == "FILE_EXISTS"
    assert exc.value.detail.get("field") == "name"
    with pytest.raises(AppError) as exc:
        workspace_catalog.create_workspace("   ")
    assert exc.value.code == "INVALID_INPUT"
    with pytest.raises(AppError) as exc:
        workspace_catalog.create_workspace("W3", "x" * 301)
    assert exc.value.code == "INVALID_INPUT"
    with pytest.raises(AppError) as exc:
        workspace_catalog.create_workspace("x" * 51)
    assert exc.value.code == "INVALID_INPUT"


def test_rename_keeps_id_and_root():
    workspace = workspace_catalog.create_workspace("W2")
    root = workspace_catalog.profile_root(workspace["id"], "default")
    updated = workspace_catalog.update_workspace(workspace["id"], "W2-renamed", "")
    assert updated["id"] == workspace["id"]
    assert updated["name"] == "W2-renamed"
    assert workspace_catalog.profile_root(workspace["id"], "default") == root


def test_legacy_rename_writes_override_but_keeps_legacy_root():
    updated = workspace_catalog.update_workspace("default", "默认工作区-改", "")
    assert updated["id"] == "default"
    assert updated["name"] == "默认工作区-改"
    assert workspace_catalog.workspace_root("default") == storage.PROFILES_DIR
    assert os.path.exists(workspace_catalog.catalog_path())


def test_invalid_workspace_id_and_corrupt_catalog():
    with pytest.raises(AppError) as exc:
        workspace_catalog.get_workspace("../x")
    assert exc.value.code == "NOT_FOUND"
    with open(workspace_catalog.catalog_path(), "w", encoding="utf-8") as handle:
        handle.write("{not-json")
    with pytest.raises(CorruptDataError):
        workspace_catalog.list_workspaces()


def test_storage_workspace_context_redirects_profile_root():
    workspace = workspace_catalog.create_workspace("W2")
    legacy_root = storage.get_profile_data_dir("default")
    with storage.use_workspace(workspace["id"]):
        assert storage.get_profile_data_dir("default") == workspace_catalog.profile_root(
            workspace["id"], "default"
        )
        assert storage.get_configs_dir("default").startswith(
            workspace_catalog.workspace_root(workspace["id"])
        )
    assert storage.get_profile_data_dir("default") == legacy_root
