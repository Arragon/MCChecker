"""M16-18 collector 独立 SecretStore（密文根在 data/ 之外）。

设计约束：
- 密文文件只在部署指定的 secret root；不在业务 data/、备份或发布包。
- 密钥由部署端单独注入（`MCHECKER_SECRET_KEY`，base64url 或 hex，≥32 字节）；
  密钥与密文分离，没有密钥时不回退明文。
- AAD 绑定 workspace + connection + generation；成熟认证加密（Fernet）把 AAD
  放进认证明文内；测试用可替换 Cipher 验证语义，生产默认 Fernet。
- 文件权限在 POSIX 下 0600；原子写；删除失败不谎称成功。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

MAGIC = b"M16SEC1\n"
DEFAULT_KEY_ENV = "MCHECKER_SECRET_KEY"
MIN_KEY_BYTES = 32


class SecretStoreError(Exception):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.message = message or reason_code


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _aad(workspace_id: str, connection_id: str, generation: int) -> bytes:
    return ("%s\x1f%s\x1f%d" % (workspace_id, connection_id, generation)).encode("utf-8")


def _validate_scope(workspace_id: str, connection_id: str, generation: int) -> None:
    for label, value in (("workspace_id", workspace_id), ("connection_id", connection_id)):
        text = str(value or "").strip()
        if not text or len(text) > 80 or any(ch in text for ch in "/\\\x00"):
            raise SecretStoreError("invalid_scope", "%s invalid" % label)
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise SecretStoreError("invalid_scope", "generation must be >= 1")


class EnvKeyProvider:
    def __init__(self, env_var: str = DEFAULT_KEY_ENV) -> None:
        self.env_var = env_var

    def __call__(self) -> bytes:
        raw = str(os.environ.get(self.env_var) or "").strip()
        if not raw:
            raise SecretStoreError("key_missing", "secret key is not configured")
        key: Optional[bytes] = None
        if re.fullmatch(r"[0-9a-fA-F]+", raw) and len(raw) % 2 == 0:
            try:
                key = bytes.fromhex(raw)
            except ValueError:
                raise SecretStoreError("key_invalid", "secret key is not base64url/hex")
        else:
            try:
                padded = raw + "=" * (-len(raw) % 4)
                key = base64.urlsafe_b64decode(padded.encode("ascii"))
            except (ValueError, binascii.Error, UnicodeEncodeError):
                raise SecretStoreError("key_invalid", "secret key is not base64url/hex")
        if key is None or len(key) < MIN_KEY_BYTES:
            raise SecretStoreError("key_invalid", "secret key must be at least 32 bytes")
        return key


class FernetCipher:
    """生产默认：cryptography Fernet；AAD 放在认证明文内。"""

    def __init__(self) -> None:
        try:
            from cryptography.fernet import Fernet, InvalidToken  # type: ignore
        except ImportError:
            raise SecretStoreError("cipher_unavailable", "cryptography is not installed")
        self._fernet_cls = Fernet
        self._invalid = InvalidToken

    def _fernet(self, key: bytes):
        return self._fernet_cls(base64.urlsafe_b64encode(key))

    def encrypt(self, key: bytes, plaintext: bytes, aad: bytes) -> bytes:
        return self._fernet(key).encrypt(b"AAD:" + aad + b"\n" + plaintext)

    def decrypt(self, key: bytes, ciphertext: bytes, aad: bytes) -> bytes:
        try:
            raw = self._fernet(key).decrypt(ciphertext)
        except self._invalid:
            raise SecretStoreError("secret_corrupt", "ciphertext authentication failed")
        prefix = b"AAD:" + aad + b"\n"
        if not raw.startswith(prefix):
            raise SecretStoreError("secret_corrupt", "AAD binding mismatch")
        return raw[len(prefix) :]


class SecretStore:
    def __init__(self, root: str, key_provider: Callable[[], bytes], cipher=None) -> None:
        self.root = os.path.abspath(str(root))
        self.key_provider = key_provider
        self.cipher = cipher or FernetCipher()
        os.makedirs(self.root, mode=0o700, exist_ok=True)

    def _path(self, secret_id: str) -> str:
        if not secret_id.startswith("sec-") or any(ch in secret_id for ch in "/\\\x00"):
            raise SecretStoreError("invalid_scope", "secret_id invalid")
        return os.path.join(self.root, secret_id + ".enc")

    def _secret_id(self, workspace_id: str, connection_id: str, generation: int) -> str:
        payload = "%s\x1f%s\x1f%d" % (workspace_id, connection_id, generation)
        return "sec-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]

    def put(self, workspace_id: str, connection_id: str, generation: int, plaintext: bytes) -> Dict[str, Any]:
        _validate_scope(workspace_id, connection_id, generation)
        if not isinstance(plaintext, (bytes, bytearray)) or not plaintext:
            raise SecretStoreError("invalid_secret", "secret must be non-empty bytes")
        secret_id = self._secret_id(workspace_id, connection_id, generation)
        aad = _aad(workspace_id, connection_id, generation)
        key = self.key_provider()
        ciphertext = self.cipher.encrypt(key, bytes(plaintext), aad)
        header = {
            "secret_id": secret_id,
            "workspace_id": workspace_id,
            "connection_id": connection_id,
            "generation": generation,
            "created_at": _now(),
            "size": len(plaintext),
            "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
        }
        body = MAGIC + json.dumps(header, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n" + ciphertext
        self._atomic_write(self._path(secret_id), body)
        return self.metadata(secret_id)

    def _read_header(self, secret_id: str):
        path = self._path(secret_id)
        if not os.path.exists(path):
            raise SecretStoreError("secret_missing", "secret not found")
        with open(path, "rb") as handle:
            raw = handle.read()
        if not raw.startswith(MAGIC):
            raise SecretStoreError("secret_corrupt", "secret file magic mismatch")
        rest = raw[len(MAGIC) :]
        if b"\n" not in rest:
            raise SecretStoreError("secret_corrupt", "secret file header missing")
        header_raw, ciphertext = rest.split(b"\n", 1)
        try:
            header = json.loads(header_raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise SecretStoreError("secret_corrupt", "secret header invalid")
        if not isinstance(header, dict) or header.get("secret_id") != secret_id:
            raise SecretStoreError("secret_corrupt", "secret header identity mismatch")
        if hashlib.sha256(ciphertext).hexdigest() != header.get("ciphertext_sha256"):
            raise SecretStoreError("secret_corrupt", "ciphertext hash mismatch")
        return header, ciphertext

    def get(self, workspace_id: str, connection_id: str, generation: int) -> bytes:
        _validate_scope(workspace_id, connection_id, generation)
        secret_id = self._secret_id(workspace_id, connection_id, generation)
        header, ciphertext = self._read_header(secret_id)
        if (
            header.get("workspace_id") != workspace_id
            or header.get("connection_id") != connection_id
            or header.get("generation") != generation
        ):
            raise SecretStoreError("secret_scope_mismatch", "secret scope mismatch")
        key = self.key_provider()
        plaintext = self.cipher.decrypt(key, ciphertext, _aad(workspace_id, connection_id, generation))
        if len(plaintext) != header.get("size"):
            raise SecretStoreError("secret_corrupt", "plaintext size mismatch")
        return plaintext

    def metadata(self, secret_id: str) -> Dict[str, Any]:
        header, _ciphertext = self._read_header(secret_id)
        return dict(header)

    def list_metadata(self) -> List[Dict[str, Any]]:
        result = []
        for name in sorted(os.listdir(self.root)):
            if not name.endswith(".enc"):
                continue
            try:
                result.append(self.metadata(name[:-4]))
            except SecretStoreError:
                continue
        return result

    def delete(self, workspace_id: str, connection_id: str, generation: int) -> bool:
        _validate_scope(workspace_id, connection_id, generation)
        path = self._path(self._secret_id(workspace_id, connection_id, generation))
        if not os.path.exists(path):
            return False
        try:
            os.remove(path)
        except OSError as exc:
            raise SecretStoreError("delete_failed", str(exc))
        return True

    def _atomic_write(self, path: str, body: bytes) -> None:
        fd, temp_path = tempfile.mkstemp(prefix=".tmp-secret-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                os.chmod(temp_path, 0o600)
            os.replace(temp_path, path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
