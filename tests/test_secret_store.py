"""M16-18 SecretStore tests (test-only cipher; production uses Fernet)."""

import base64
import hashlib
import os

import pytest

from collector.secret_store import (
    EnvKeyProvider,
    SecretStore,
    SecretStoreError,
)

KEY = b"k" * 32


class TestCipher:
    """Only for store semantics; not a production cipher."""

    def encrypt(self, key, plaintext, aad):
        digest = hashlib.sha256(key + aad).digest()
        body = bytes(byte ^ 0x5A for byte in plaintext)
        return b"TEST1" + digest + body

    def decrypt(self, key, ciphertext, aad):
        digest = hashlib.sha256(key + aad).digest()
        if not ciphertext.startswith(b"TEST1") or ciphertext[5:37] != digest:
            raise SecretStoreError("secret_corrupt", "test cipher auth failed")
        return bytes(byte ^ 0x5A for byte in ciphertext[37:])


def make_store(tmp_path):
    return SecretStore(str(tmp_path / "secrets"), lambda: KEY, TestCipher())


def test_put_get_metadata_without_plaintext(tmp_path):
    store = make_store(tmp_path)
    plaintext = b"password=super-secret-value"
    meta = store.put("w-1", "conn-1", 1, plaintext)
    assert meta["secret_id"].startswith("sec-")
    assert meta["workspace_id"] == "w-1"
    assert meta["connection_id"] == "conn-1"
    assert meta["generation"] == 1
    assert "password" not in str(meta).lower()

    path = tmp_path / "secrets" / (meta["secret_id"] + ".enc")
    blob = path.read_bytes()
    assert plaintext not in blob
    assert b"super-secret-value" not in blob

    assert store.get("w-1", "conn-1", 1) == plaintext
    listed = store.list_metadata()
    assert len(listed) == 1
    assert listed[0]["secret_id"] == meta["secret_id"]


def test_scope_generation_tamper_delete_and_restart(tmp_path):
    store = make_store(tmp_path)
    plaintext = b"token-value"
    meta = store.put("w-1", "conn-1", 1, plaintext)

    with pytest.raises(SecretStoreError) as exc:
        store.get("w-2", "conn-1", 1)
    assert exc.value.reason_code == "secret_missing"

    with pytest.raises(SecretStoreError):
        store.get("w-1", "conn-1", 2)

    path = tmp_path / "secrets" / (meta["secret_id"] + ".enc")
    blob = bytearray(path.read_bytes())
    blob[-1] ^= 0x01
    path.write_bytes(bytes(blob))
    with pytest.raises(SecretStoreError) as exc:
        store.get("w-1", "conn-1", 1)
    assert exc.value.reason_code == "secret_corrupt"

    store2 = make_store(tmp_path)
    assert store2.delete("w-1", "conn-1", 1) is True
    assert store2.delete("w-1", "conn-1", 1) is False
    with pytest.raises(SecretStoreError):
        store2.get("w-1", "conn-1", 1)


def test_missing_key_writes_nothing(tmp_path):
    store = SecretStore(str(tmp_path / "secrets"), EnvKeyProvider("M16_TEST_MISSING_KEY"), TestCipher())
    with pytest.raises(SecretStoreError) as exc:
        store.put("w-1", "conn-1", 1, b"secret")
    assert exc.value.reason_code == "key_missing"
    assert not any(name.endswith(".enc") for name in os.listdir(tmp_path / "secrets"))


def test_file_permissions_and_atomic_temp_cleanup(tmp_path):
    store = make_store(tmp_path)
    meta = store.put("w-1", "conn-1", 1, b"secret")
    path = tmp_path / "secrets" / (meta["secret_id"] + ".enc")
    if os.name != "nt":
        assert (path.stat().st_mode & 0o777) == 0o600
    assert not any(name.startswith(".tmp-secret-") for name in os.listdir(tmp_path / "secrets"))


def test_env_key_provider_base64_and_hex(monkeypatch):
    key = b"z" * 32
    monkeypatch.setenv("M16_TEST_KEY_B64", base64.urlsafe_b64encode(key).decode("ascii"))
    assert EnvKeyProvider("M16_TEST_KEY_B64")() == key

    monkeypatch.setenv("M16_TEST_KEY_HEX", key.hex())
    assert EnvKeyProvider("M16_TEST_KEY_HEX")() == key

    monkeypatch.setenv("M16_TEST_KEY_SHORT", base64.urlsafe_b64encode(b"short").decode("ascii"))
    with pytest.raises(SecretStoreError) as exc:
        EnvKeyProvider("M16_TEST_KEY_SHORT")()
    assert exc.value.reason_code == "key_invalid"
