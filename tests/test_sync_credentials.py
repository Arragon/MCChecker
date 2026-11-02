"""M16-19 候选账密/激活/撤销/auth_epoch 竞态测试。

密文由假 vault 持有，metadata 落在临时 data 根的 sync/credentials.json；测试
钉住：晚到 probe 拒绝、错密码不换 active、旧 epoch 不覆盖、撤销失败不谎称删除、
明文不落 data。
"""

from __future__ import annotations

import json
import os

import pytest

from app.core import storage
from app.core.errors import REVISION_CONFLICT, SOURCE_CHANGED, STORAGE_FAILURE, AppError
from app.services import (
    sync_connections,
    sync_credentials,
    sync_paths,
    sync_store,
    workspace_catalog,
)


class VaultError(Exception):
    pass


class FakeVault:
    def __init__(self, fail_delete=False):
        self.items = {}
        self.puts = []
        self.deletes = []
        self.fail_delete = fail_delete

    def put(self, workspace_id, connection_id, generation, plaintext):
        key = (workspace_id, connection_id, generation)
        self.items[key] = bytes(plaintext)
        self.puts.append(key)
        return {"generation": generation}

    def delete(self, workspace_id, connection_id, generation):
        key = (workspace_id, connection_id, generation)
        self.deletes.append(key)
        if self.fail_delete and key in self.items:
            raise VaultError("disk full")
        return self.items.pop(key, None) is not None


def make_connection():
    wid = workspace_catalog.create_workspace("W-cred")["id"]
    connection = sync_connections.create_connection(wid, "SDA", "https://sda.invalid")
    return wid, connection["connection_id"]


def state(wid, cid):
    return sync_credentials.get_credential_state(wid, cid)


def metadata_text(wid):
    path = sync_paths.sync_file(wid, "credentials.json")
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def test_begin_activate_rotates_epoch_and_generation():
    wid, cid = make_connection()
    vault = FakeVault()
    before = state(wid, cid)
    assert before["state"] == "unconfigured"
    assert before["auth_epoch"] == 0
    assert not os.path.exists(sync_paths.sync_file(wid, "credentials.json"))

    pending = sync_credentials.begin_candidate(wid, cid, "operator", "old-password", vault)
    assert pending["candidate"]["state"] == "pending"
    assert pending["candidate"]["generation"] == 1
    assert pending["active_generation"] is None
    assert pending["auth_epoch"] == 0
    assert vault.items[(wid, cid, 1)]

    active = sync_credentials.activate_candidate(
        wid, cid, pending["candidate"]["candidate_id"], 0, vault
    )
    assert active["state"] == "valid"
    assert active["active_generation"] == 1
    assert active["auth_epoch"] == 1
    assert active["candidate"] is None
    assert active["last_candidate_id"] == pending["candidate"]["candidate_id"]
    assert active["last_activated_at"]
    assert sync_credentials.assert_epoch(wid, cid, 1)["active_generation"] == 1

    second = sync_credentials.begin_candidate(wid, cid, "operator", "new-password", vault)
    assert second["candidate"]["generation"] == 2
    rotated = sync_credentials.activate_candidate(
        wid, cid, second["candidate"]["candidate_id"], 1, vault
    )
    assert rotated["state"] == "valid"
    assert rotated["active_generation"] == 2
    assert rotated["auth_epoch"] == 2
    assert (wid, cid, 1) not in vault.items
    assert vault.items[(wid, cid, 2)]

    with pytest.raises(AppError) as exc:
        sync_credentials.activate_candidate(
            wid, cid, second["candidate"]["candidate_id"], 1, vault
        )
    assert exc.value.code in ("NOT_FOUND", "REVISION_CONFLICT")

    text = metadata_text(wid)
    assert "old-password" not in text
    assert "new-password" not in text
    assert "operator" not in text
    assert "new-password" not in repr(rotated)


def test_wrong_password_locks_but_keeps_active_ciphertext():
    wid, cid = make_connection()
    vault = FakeVault()
    first = sync_credentials.begin_candidate(wid, cid, "operator", "good-password", vault)
    active = sync_credentials.activate_candidate(
        wid, cid, first["candidate"]["candidate_id"], 0, vault
    )
    old_secret = vault.items[(wid, cid, 1)]

    candidate = sync_credentials.begin_candidate(wid, cid, "operator", "wrong-password", vault)
    failed = sync_credentials.fail_candidate(
        wid, cid, candidate["candidate"]["candidate_id"], 1, "bad_credentials", vault
    )

    assert failed["state"] == "locked"
    assert failed["last_error"] == "bad_credentials"
    assert failed["active_generation"] == active["active_generation"]
    assert failed["auth_epoch"] == 2
    assert vault.items[(wid, cid, 1)] == old_secret
    assert (wid, cid, 2) not in vault.items
    assert failed["candidate"]["state"] == "failed"

    with pytest.raises(AppError) as exc:
        sync_credentials.assert_epoch(wid, cid, 1)
    assert exc.value.code == SOURCE_CHANGED


def test_late_probe_cannot_activate_after_epoch_moved():
    wid, cid = make_connection()
    vault = FakeVault()
    candidate = sync_credentials.begin_candidate(wid, cid, "operator", "pw", vault)
    candidate_id = candidate["candidate"]["candidate_id"]

    # 模拟并发撤销/轮换：仅推进 epoch，候选仍 pending。
    def bump(doc):
        record = doc["items"][cid]
        record["auth_epoch"] = int(record["auth_epoch"]) + 1
        record["revision"] = int(record["revision"]) + 1
        return doc

    sync_store.update_doc(wid, "credentials.json", bump, {"items": {}})
    with pytest.raises(AppError) as exc:
        sync_credentials.activate_candidate(wid, cid, candidate_id, 0, vault)
    assert exc.value.code == SOURCE_CHANGED
    assert state(wid, cid)["active_generation"] is None
    assert state(wid, cid)["candidate"]["state"] == "pending"
    assert (wid, cid, 1) in vault.items


def test_revoke_failure_keeps_active_and_does_not_claim_deleted():
    wid, cid = make_connection()
    vault = FakeVault()
    candidate = sync_credentials.begin_candidate(wid, cid, "operator", "pw", vault)
    active = sync_credentials.activate_candidate(
        wid, cid, candidate["candidate"]["candidate_id"], 0, vault
    )

    vault.fail_delete = True
    with pytest.raises(AppError) as exc:
        sync_credentials.revoke_credential(wid, cid, active["revision"], vault)
    assert exc.value.code == STORAGE_FAILURE
    current = state(wid, cid)
    assert current["state"] == "valid"
    assert current["active_generation"] == active["active_generation"]
    assert current["auth_epoch"] == active["auth_epoch"]
    assert vault.items[(wid, cid, active["active_generation"])]

    vault.fail_delete = False
    revoked = sync_credentials.revoke_credential(wid, cid, current["revision"], vault)
    assert revoked["state"] == "unconfigured"
    assert revoked["active_generation"] is None
    assert revoked["auth_epoch"] == active["auth_epoch"] + 1
    assert revoked["last_revoked_at"]
    assert (wid, cid, active["active_generation"]) not in vault.items

    again = sync_credentials.revoke_credential(wid, cid, revoked["revision"], vault)
    assert again["state"] == "unconfigured"


def test_revoke_and_begin_cas_and_workspace_isolation():
    wid, cid = make_connection()
    vault = FakeVault()
    pending = sync_credentials.begin_candidate(wid, cid, "operator", "pw", vault)

    with pytest.raises(AppError) as exc:
        sync_credentials.begin_candidate(
            wid, cid, "operator", "other", vault, expected_revision=99
        )
    assert exc.value.code == REVISION_CONFLICT
    assert (wid, cid, 2) not in vault.items

    with pytest.raises(AppError) as exc:
        sync_credentials.revoke_credential(wid, cid, 99, vault)
    assert exc.value.code == REVISION_CONFLICT
    assert (wid, cid, 1) in vault.items

    other_wid = workspace_catalog.create_workspace("W-cred-2")["id"]
    other = sync_connections.create_connection(other_wid, "SDA", "https://sda.invalid")
    assert other["connection_id"] != cid
    assert state(other_wid, other["connection_id"])["state"] == "unconfigured"
    assert state(other_wid, other["connection_id"])["revision"] == 0

    # 另一工作区不会看到本区的候选
    assert state(wid, cid)["candidate"]["candidate_id"] == pending["candidate"]["candidate_id"]
    assert json.loads(metadata_text(wid))["items"][cid]["candidate"]["state"] == "pending"
