"""配置管理仓储：不可变对象、内容寻址 blob、state 指针与事务（M5-03 / M5-04）。

设计见 `docs/MCChecker_CONFIG_DOMAIN_SPEC.md` §5。要点：

* **单写者**：所有写入在 `write_gate.write()` + `management_lock` 内完成，锁序固定；
* **先写对象、最后公布 state**：`state.json` 是唯一权威指针，它没被替换成功时
  新对象一律不可见（不存在"半发布"）；
* **内容寻址**：blob 以 SHA-256 命名，重复写入幂等；对象按 `r1`、`r2` 递增，旧修订不改写；
* **CAS**：调用方给 `expected`（`{"seq": 12}` 或 `{"baseline:bl_000016": "r2"}`），
  不匹配即 `REVISION_CONFLICT`；
* **持久幂等**：同一 `Idempotency-Key` + 同一请求体返回旧回执；同键异体即
  `IDEMPOTENCY_MISMATCH`；
* **损坏不掩盖**：`state.json` 不可解析、引用对象缺失、blob 哈希不符 → `CorruptDataError`，
  进入只读保护，禁止当作空状态继续写。

本模块与旧 `storage` **互不 import**（决策 D1）：旧 `data/profiles/*` 与新
`data/config_management/` 各自只有一个写者。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from . import fsutil
from .errors import (
    CORRUPT_DATA,
    IDEMPOTENCY_MISMATCH,
    INVALID_INPUT,
    REVISION_CONFLICT,
    STORAGE_FAILURE,
    AppError,
    CorruptDataError,
)
from .locks import management_lock, write_gate

#: 持久化 schema 版本。改结构必须同步 `docs/MCChecker_CONFIG_DOMAIN_SPEC.md`。
SCHEMA_VERSION = 1

#: 允许的对象类型与 ID 前缀（SPEC §2.2）。
KIND_PREFIX = {
    "family": "cf",
    "logical_file": "lf",
    "software_release": "sr",
    "file_snapshot": "fs",
    "release_batch": "rb",
    "template_variant": "tv",
    "parameter": "pm",
    "definition_revision": "dr",
    "definition_set": "ds",
    "tracking_policy": "tp",
    "baseline": "bl",
    "observation": "ob",
    "capture_event": "ce",
    "change_set": "cs",
    "artifact": "ar",
    "delivery": "dl",
}

#: 承接不可变 blob 引用的字段名（一致性检查用）。
BLOB_FIELDS = ("blob_sha256",)

#: **服务端自动填充的时间戳**：不参与请求指纹。
#:
#: 幂等重放要求"同一请求"算出同一个指纹，而时间戳每次提交都会变；若让它们参与，
#: 客户端重试同一请求会被误判成 `IDEMPOTENCY_MISMATCH`。语义上它们也不是输入
#: （采集时间 `captured_at` 缺省时同样是服务端时钟），因此统一排除。
VOLATILE_KEYS = frozenset(("created_at", "updated_at", "captured_at", "retired_at"))

_HEX = set("0123456789abcdef")


def now_iso() -> str:
    """UTC 秒级 ISO-8601，全域统一（与契约样例一致）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Ref:
    """事务里的一次写入句柄。

    `id` / `revision` 在 `commit()` 之前是 `None`，提交后回填；幂等重放时同样回填，
    因此调用方无需区分"新建"与"重放"。
    """

    __slots__ = ("kind", "id", "revision", "_payload")

    def __init__(self, kind: str, object_id: Optional[str], payload: Dict[str, Any]) -> None:
        self.kind = kind
        self.id = object_id
        self.revision: Optional[str] = None
        self._payload = payload

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return "Ref(%s, %s, %s)" % (self.kind, self.id, self.revision)


class Receipt(NamedTuple):
    """一次提交的回执（也是幂等重放的返回体）。"""

    seq: int
    refs: Tuple[Ref, ...]
    request_hash: str
    idempotency_key: Optional[str] = None
    replayed: bool = False


class Transaction:
    """暂存写入，最后由 `commit()` 一次性公布。"""

    def __init__(
        self,
        repo: "ConfigRepository",
        actor: Dict[str, Any],
        reason: str,
        action: str,
        idempotency_key: Optional[str],
        expected: Optional[Dict[str, Any]],
    ) -> None:
        self._repo = repo
        self.actor = dict(actor)
        self.reason = reason
        self.action = action
        self.idempotency_key = idempotency_key
        self.expected = dict(expected or {})
        self._refs: List[Ref] = []
        self._pointers: List[Tuple[str, str, Any]] = []
        self._blobs: List[Tuple[str, bytes]] = []
        self.committed = False

    # -- 暂存 ---------------------------------------------------------------

    def blob(self, data: bytes) -> str:
        """登记一个 blob，返回 SHA-256；真正落盘在 commit 时（内容寻址，幂等）。"""
        if not isinstance(data, (bytes, bytearray)):
            raise AppError(INVALID_INPUT, "blob 必须是字节串")
        payload = bytes(data)
        digest = sha256_bytes(payload)
        self._blobs.append((digest, payload))
        return digest

    def put(
        self,
        kind: str,
        payload: Dict[str, Any],
        object_id: Optional[str] = None,
    ) -> Ref:
        """新建或追加修订。`object_id` 为空表示新建（ID 由仓储分配）。"""
        if kind not in KIND_PREFIX:
            raise AppError(INVALID_INPUT, "未知对象类型：%s" % kind)
        if not isinstance(payload, dict):
            raise AppError(INVALID_INPUT, "对象载荷必须是 JSON 对象")
        if object_id is not None:
            self._repo._check_object_id(kind, object_id)
            if payload.get("id") not in (None, object_id):
                raise AppError(INVALID_INPUT, "载荷 id 与目标对象不一致")
        elif payload.get("id") is not None:
            raise AppError(INVALID_INPUT, "新建对象不能自带 id（由仓储分配）")
        ref = Ref(kind, object_id, dict(payload))
        self._refs.append(ref)
        return ref

    def pointer(self, name: str, key: str, value: Any) -> None:
        """登记一次指针更新（如 `release_default` → 批次 ID）。state 公布时生效。

        `value` 允许直接传 `put()` 返回的 `Ref`，落盘时取它的 ID。
        """
        if not name or not key:
            raise AppError(INVALID_INPUT, "指针名与键都不能为空")
        # `Ref` 在提交后才拿到 ID，因此这里只登记，落盘时再解析
        self._pointers.append((str(name), str(key), value))

    # -- 提交 ---------------------------------------------------------------

    def commit(self) -> Receipt:
        if self.committed:
            raise AppError(INVALID_INPUT, "该事务已提交，不能重复提交")
        receipt = self._repo._commit(self)
        self.committed = True
        return receipt


class ConfigRepository:
    """`data/config_management/` 的唯一读写入口。"""

    def __init__(self, data_root: str) -> None:
        self.data_root = os.path.abspath(data_root)
        self.root = os.path.join(self.data_root, "config_management")
        #: 事务内分配 ID 时的进程内互斥（磁盘态仍以 state 为准）。
        self._id_guard = threading.Lock()

    # -- 路径 ---------------------------------------------------------------

    @property
    def state_path(self) -> str:
        return os.path.join(self.root, "state.json")

    def object_path(self, kind: str, object_id: str, revision: str) -> str:
        return os.path.join(self.root, "objects", kind, object_id, revision + ".json")

    def blob_path(self, digest: str) -> str:
        if len(digest) < 4 or any(ch not in _HEX for ch in digest):
            raise AppError(INVALID_INPUT, "非法 blob 哈希")
        return os.path.join(self.root, "blobs", digest[:2], digest)

    def audit_path(self, seq: int) -> str:
        return os.path.join(self.root, "audit", "%010d.json" % seq)

    def receipt_path(self, key: str) -> str:
        digest = sha256_bytes(key.encode("utf-8"))
        return os.path.join(self.root, "receipts", digest[:2], digest + ".json")

    # -- 初始化与状态 -------------------------------------------------------

    def initialize(self) -> None:
        """建立目录与空 state；已存在则原样保留（幂等）。"""
        for sub in ("objects", "blobs", "audit", "receipts", "cache"):
            os.makedirs(os.path.join(self.root, sub), exist_ok=True)
        if os.path.exists(self.state_path):
            return
        if self._has_any_object():
            # 有对象却没有 state：指针丢失，不能悄悄当成空状态。
            raise CorruptDataError(self.state_path, cause="state.json 缺失但对象目录非空")
        state = self._default_state()
        fsutil.atomic_write_json(self.state_path, state)

    def _has_any_object(self) -> bool:
        objects = os.path.join(self.root, "objects")
        if not os.path.isdir(objects):
            return False
        for _kind, _dirs, files in os.walk(objects):
            if files:
                return True
        return False

    @staticmethod
    def _default_state() -> Dict[str, Any]:
        stamp = now_iso()
        return {
            "schema": SCHEMA_VERSION,
            "seq": 0,
            "id_seq": 0,
            "heads": {},
            "pointers": {},
            "created_at": stamp,
            "updated_at": stamp,
        }

    def load_state(self) -> Dict[str, Any]:
        """读 state；缺失返回空状态，损坏抛 `CorruptDataError`。"""
        state = fsutil.read_json(self.state_path, None)
        if state is None:
            if self._has_any_object():
                raise CorruptDataError(self.state_path, cause="state.json 缺失但对象目录非空")
            return self._default_state()
        if not isinstance(state, dict):
            raise CorruptDataError(self.state_path, cause="state.json 顶层不是对象")
        if state.get("schema") != SCHEMA_VERSION:
            raise CorruptDataError(
                self.state_path, cause="schema %r 与期望 %r 不符" % (state.get("schema"), SCHEMA_VERSION)
            )
        for field in ("seq", "id_seq", "heads", "pointers"):
            if field not in state:
                raise CorruptDataError(self.state_path, cause="state.json 缺字段 %s" % field)
        return state

    # -- blob ---------------------------------------------------------------

    def put_blob(self, data: bytes) -> str:
        """写入（或确认已存在）一个 blob，返回哈希。内容寻址，重复写入幂等。"""
        digest = sha256_bytes(bytes(data))
        path = self.blob_path(digest)
        if os.path.exists(path):
            # 已存在则只校验（内容寻址要求同哈希同内容），不重写
            self._verify_blob_file(path, digest)
            return digest
        with write_gate.write(), management_lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fsutil.atomic_write_bytes(path, bytes(data))
        return digest

    def read_blob(self, digest: str) -> bytes:
        path = self.blob_path(digest)
        if not os.path.exists(path):
            raise CorruptDataError(path, cause="blob 缺失")
        with open(path, "rb") as fh:
            data = fh.read()
        if sha256_bytes(data) != digest:
            raise CorruptDataError(path, cause="blob 哈希不符（内容被改写）")
        return data

    @staticmethod
    def _verify_blob_file(path: str, digest: str) -> None:
        with open(path, "rb") as fh:
            if sha256_bytes(fh.read()) != digest:
                raise CorruptDataError(path, cause="blob 哈希不符（内容被改写）")

    # -- 对象读取 -----------------------------------------------------------

    @staticmethod
    def _check_object_id(kind: str, object_id: str) -> None:
        prefix = KIND_PREFIX.get(kind)
        if prefix is None:
            raise AppError(INVALID_INPUT, "未知对象类型：%s" % kind)
        text = str(object_id)
        head, _, tail = text.partition("_")
        if head != prefix or len(tail) != 6 or not tail.isdigit():
            raise AppError(INVALID_INPUT, "对象 ID 与类型不匹配：%s" % text)

    def head_revision(self, kind: str, object_id: str) -> Optional[str]:
        state = self.load_state()
        return (state["heads"].get(kind) or {}).get(object_id)

    def get(self, kind: str, object_id: str, revision: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """读某修订；`revision` 为空取当前 head。不存在返回 None（不抛 404）。"""
        rev = revision or self.head_revision(kind, object_id)
        if not rev:
            return None
        path = self.object_path(kind, object_id, rev)
        if not os.path.exists(path):
            raise CorruptDataError(path, cause="state 指向的对象文件缺失")
        payload = fsutil.read_json(path, None)
        if not isinstance(payload, dict):
            raise CorruptDataError(path, cause="对象载荷不是 JSON 对象")
        if payload.get("id") != object_id or payload.get("revision") != rev:
            raise CorruptDataError(path, cause="对象载荷与路径不一致")
        return payload

    def history(self, kind: str, object_id: str) -> List[str]:
        """某对象的全部修订号，按 `r<n>` 数字升序。"""
        directory = os.path.join(self.root, "objects", kind, object_id)
        if not os.path.isdir(directory):
            return []
        revisions = []
        for name in os.listdir(directory):
            if name.startswith("r") and name.endswith(".json"):
                try:
                    revisions.append(int(name[1:-5]))
                except ValueError:
                    continue
        return ["r%d" % n for n in sorted(revisions)]

    def list_objects(self, kind: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """按 head 列某类型的当前修订（默认全部；`limit` 截断）。"""
        state = self.load_state()
        heads = state["heads"].get(kind) or {}
        items: List[Dict[str, Any]] = []
        for object_id in sorted(heads):
            payload = self.get(kind, object_id)
            if payload is not None:
                items.append(payload)
            if limit is not None and len(items) >= limit:
                break
        return items

    def pointer(self, name: str, key: str, default: Any = None) -> Any:
        state = self.load_state()
        return (state["pointers"].get(name) or {}).get(key, default)

    # -- 审计 ---------------------------------------------------------------

    def audit_events(
        self,
        *,
        limit: int = 50,
        before_seq: Optional[int] = None,
        action: Optional[str] = None,
        object_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按 seq 倒序返回审计事件；`before_seq` 用于翻页（不含该 seq）。"""
        directory = os.path.join(self.root, "audit")
        if not os.path.isdir(directory):
            return {"items": [], "next_before": None}
        seqs: List[int] = []
        for name in os.listdir(directory):
            stem, _, ext = name.partition(".")
            if ext == "json" and stem.isdigit():
                seq = int(stem)
                if before_seq is not None and seq >= before_seq:
                    continue
                seqs.append(seq)
        seqs.sort(reverse=True)
        items: List[Dict[str, Any]] = []
        next_before: Optional[int] = None
        for seq in seqs:
            event = fsutil.read_json(self.audit_path(seq), None)
            if not isinstance(event, dict):
                continue
            if action and event.get("action") != action:
                continue
            if object_id:
                matched = any(
                    (obj or {}).get("id") == object_id for obj in event.get("objects") or []
                )
                if not matched:
                    continue
            if len(items) >= limit:
                next_before = seq + 1
                break
            items.append(event)
        return {"items": items, "next_before": next_before}

    # -- 事务 ---------------------------------------------------------------

    def transaction(
        self,
        actor: Any,
        reason: str = "",
        *,
        action: str = "commit",
        idempotency_key: Optional[str] = None,
        expected: Optional[Dict[str, Any]] = None,
    ) -> Transaction:
        """开一个事务。`actor` 是 `Actor` 或 `{ip, name}` 字典。"""
        actor_map = self._actor_map(actor)
        return Transaction(self, actor_map, reason, action, idempotency_key, expected)

    @staticmethod
    def _actor_map(actor: Any) -> Dict[str, str]:
        if actor is None:
            return {"ip": "", "name": ""}
        if isinstance(actor, dict):
            return {"ip": str(actor.get("ip", "")), "name": str(actor.get("name", ""))}
        return {
            "ip": str(getattr(actor, "ip", "")),
            "name": str(getattr(actor, "name", "")),
        }

    def _request_hash(self, tx: Transaction) -> str:
        """请求体指纹：只覆盖语义输入，不含时间与自增序号。

        指针里若引用本次新建对象，用它在本次事务中的序号参与指纹（此时还没有 ID）；
        服务端自动填充的时间戳（`VOLATILE_KEYS`）一律剔除，否则同一次重试会算出
        不同指纹、被误判成 `IDEMPOTENCY_MISMATCH`。
        """

        def normalize(value: Any) -> Any:
            if isinstance(value, Ref):
                return {"$ref": tx._refs.index(value)}
            if isinstance(value, dict):
                return {
                    key: normalize(item)
                    for key, item in value.items()
                    if key not in VOLATILE_KEYS
                }
            if isinstance(value, list):
                return [normalize(item) for item in value]
            return value

        payload = {
            "actor": tx.actor,
            "reason": tx.reason,
            "action": tx.action,
            "ops": [
                {"kind": ref.kind, "id": ref.id, "payload": normalize(ref._payload)}
                for ref in tx._refs
            ],
            "pointers": [(name, key, normalize(value)) for name, key, value in tx._pointers],
            "blobs": [digest for digest, _ in tx._blobs],
            "expected": tx.expected,
        }
        return sha256_bytes(_canonical(payload).encode("utf-8"))

    def _next_id(self, kind: str, state: Dict[str, Any]) -> str:
        with self._id_guard:
            state["id_seq"] = int(state["id_seq"]) + 1
            return "%s_%06d" % (KIND_PREFIX[kind], state["id_seq"])

    def _check_expected(self, state: Dict[str, Any], expected: Dict[str, Any]) -> None:
        for key, value in expected.items():
            if key == "seq":
                if int(state["seq"]) != int(value):
                    raise AppError(
                        REVISION_CONFLICT,
                        "配置域已变化，请刷新后重试",
                        expected_revision=value,
                        actual_revision=int(state["seq"]),
                    )
                continue
            kind, _, object_id = key.partition(":")
            if not kind or not object_id:
                raise AppError(INVALID_INPUT, "expected 键格式应为 seq 或 kind:id")
            actual = (state["heads"].get(kind) or {}).get(object_id)
            if actual != value:
                raise AppError(
                    REVISION_CONFLICT,
                    "对象已被修改，请刷新后重试",
                    object_id=object_id,
                    expected_revision=value,
                    actual_revision=actual,
                )

    def _commit(self, tx: Transaction) -> Receipt:
        request_hash = self._request_hash(tx)

        if tx.idempotency_key:
            replay = self._load_receipt(tx.idempotency_key)
            if replay is not None:
                if replay.get("request_hash") != request_hash:
                    raise AppError(
                        IDEMPOTENCY_MISMATCH,
                        "同一 Idempotency-Key 被用于不同请求",
                        idempotency_key=tx.idempotency_key,
                    )
                self._fill_refs(tx, replay.get("result") or [])
                return Receipt(
                    seq=int(replay["seq"]),
                    refs=tuple(tx._refs),
                    request_hash=request_hash,
                    idempotency_key=tx.idempotency_key,
                    replayed=True,
                )

        with write_gate.write(), management_lock:
            self.initialize()
            state = self.load_state()
            self._check_expected(state, tx.expected)

            # 1) blob（内容寻址，先落盘；孤儿无害且可复用）
            for digest, data in tx._blobs:
                path = self.blob_path(digest)
                if os.path.exists(path):
                    self._verify_blob_file(path, digest)
                else:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    fsutil.atomic_write_bytes(path, data)

            # 2) 对象修订（不可变）。先把本次事务里所有对象的 ID 与修订号**定下来**
            #    再统一落盘——同一事务内的对象因此可以互相引用（例如采集事件指向
            #    刚建的观测），引用在落盘时解析成对方真实 ID，不需要第二次提交。
            plan: List[Dict[str, Any]] = []
            stamp = now_iso()
            for ref in tx._refs:
                object_id = ref.id or self._next_id(ref.kind, state)
                self._check_object_id(ref.kind, object_id)
                previous = (state["heads"].get(ref.kind) or {}).get(object_id)
                number = int(previous[1:]) + 1 if previous else 1
                ref.id = object_id
                ref.revision = "r%d" % number
                plan.append(ref)

            result: List[Dict[str, str]] = []
            for ref in plan:
                object_id = str(ref.id)
                revision = str(ref.revision)
                payload = self._resolve_refs(dict(ref._payload), tx)
                payload.update(
                    {
                        "kind": ref.kind,
                        "id": object_id,
                        "revision": revision,
                        "created_at": payload.get("created_at") or stamp,
                    }
                )
                try:
                    fsutil.atomic_write_json(
                        self.object_path(ref.kind, object_id, revision), payload
                    )
                except OSError as exc:  # 磁盘满 / 权限 / 只读
                    raise AppError(STORAGE_FAILURE, "写入配置对象失败", cause=str(exc))

                state["heads"].setdefault(ref.kind, {})[object_id] = revision
                result.append({"kind": ref.kind, "id": object_id, "revision": revision})

            # 3) 指针（`Ref` 已在上一步拿到 ID）
            for name, key, value in tx._pointers:
                if isinstance(value, Ref):
                    if value.id is None:
                        raise AppError(INVALID_INPUT, "指针引用了本次未提交的对象")
                    value = value.id
                state["pointers"].setdefault(name, {})[key] = value

            # 4) 审计 + 回执 + 公布 state
            state["seq"] = int(state["seq"]) + 1
            state["updated_at"] = stamp
            seq = int(state["seq"])
            event = {
                "seq": seq,
                "at": stamp,
                "actor": tx.actor,
                "action": tx.action,
                "objects": result,
                "before_sha": None,
                "after_sha": None,
                "reason": tx.reason,
                "idempotency_key": tx.idempotency_key,
                "detail": {"request_hash": request_hash},
            }
            try:
                fsutil.atomic_write_json(self.audit_path(seq), event)
                if tx.idempotency_key:
                    fsutil.atomic_write_json(
                        self.receipt_path(tx.idempotency_key),
                        {
                            "idempotency_key": tx.idempotency_key,
                            "request_hash": request_hash,
                            "seq": seq,
                            "result": result,
                            "at": stamp,
                        },
                    )
                # 最后一步：state 替换成功才对读者可见
                fsutil.atomic_write_json(self.state_path, state)
            except BaseException as exc:
                # 补偿：撤回刚写的审计/回执，避免出现"审计有、状态无"的假记录。
                # state 若已替换成功则不会进这里，因此不会误删已生效的证据。
                self._discard(self.audit_path(seq))
                if tx.idempotency_key:
                    self._discard(self.receipt_path(tx.idempotency_key))
                if isinstance(exc, AppError):
                    raise
                raise AppError(STORAGE_FAILURE, "公布配置状态失败", cause=str(exc))

        return Receipt(
            seq=seq,
            refs=tuple(tx._refs),
            request_hash=request_hash,
            idempotency_key=tx.idempotency_key,
            replayed=False,
        )

    @staticmethod
    def _resolve_refs(value: Any, tx: Transaction) -> Any:
        """把载荷里的 `Ref` 换成对方的真实 ID（同一事务内的对象互相引用）。

        ID 在写盘前已全部定好，因此这里不会遇到"引用了还没编号的对象"。
        """
        if isinstance(value, Ref):
            if value.id is None:
                raise AppError(INVALID_INPUT, "对象引用了本次未分配 ID 的对象")
            return value.id
        if isinstance(value, dict):
            return {
                key: ConfigRepository._resolve_refs(item, tx)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [ConfigRepository._resolve_refs(item, tx) for item in value]
        return value

    def _load_receipt(self, key: str) -> Optional[Dict[str, Any]]:
        path = self.receipt_path(key)
        if not os.path.exists(path):
            return None
        data = fsutil.read_json(path, None)
        return data if isinstance(data, dict) else None

    def receipt(self, key: str) -> Optional[Dict[str, Any]]:
        """查持久回执（只读）。service 层用它区分"重试"与"重复登记"。

        有回执说明这个幂等键已经提交过：此时不应再做"业务唯一性"检查，
        直接让事务走到重放分支（同键异体仍会被 `IDEMPOTENCY_MISMATCH` 拦下）。
        """
        if not key:
            return None
        return self._load_receipt(str(key))

    @staticmethod
    def _discard(path: str) -> None:
        """补偿删除：只用于本次提交刚写下、尚未被 state 承认的文件。"""
        try:
            os.unlink(path)
        except OSError:
            pass

    @staticmethod
    def _fill_refs(tx: Transaction, result: List[Dict[str, str]]) -> None:
        if len(result) != len(tx._refs):
            raise AppError(IDEMPOTENCY_MISMATCH, "回执与请求的对象数量不一致")
        for ref, item in zip(tx._refs, result):
            ref.id = item["id"]
            ref.revision = item["revision"]

    # -- 一致性检查 ---------------------------------------------------------

    def verify(self) -> Dict[str, Any]:
        """只读一致性检查：state 可解析、head 对象可读、blob 哈希对得上、孤儿可列。"""
        issues: List[Dict[str, str]] = []
        orphans: List[str] = []
        objects = 0
        blobs_checked = 0
        try:
            state = self.load_state()
        except CorruptDataError as exc:
            return {
                "ok": False,
                "issues": [{"kind": "state", "detail": str(exc)}],
                "orphans": [],
                "objects": 0,
                "blobs": 0,
            }
        reachable: set = set()
        for kind, heads in (state["heads"] or {}).items():
            for object_id, revision in (heads or {}).items():
                reachable.add(os.path.join("objects", kind, object_id, revision + ".json"))
                try:
                    payload = self.get(kind, object_id, revision)
                except CorruptDataError as exc:
                    issues.append({"kind": "object", "id": object_id, "detail": str(exc)})
                    continue
                if payload is None:
                    issues.append({"kind": "object", "id": object_id, "detail": "head 指向的修订不存在"})
                    continue
                objects += 1
                for field in BLOB_FIELDS:
                    digest = payload.get(field)
                    if not digest:
                        continue
                    blobs_checked += 1
                    try:
                        self.read_blob(digest)
                    except CorruptDataError as exc:
                        issues.append({"kind": "blob", "id": object_id, "detail": str(exc)})
        objects_root = os.path.join(self.root, "objects")
        if os.path.isdir(objects_root):
            for current, _dirs, files in os.walk(objects_root):
                for name in files:
                    relative = os.path.relpath(os.path.join(current, name), self.root)
                    if relative not in reachable:
                        orphans.append(relative)
        return {
            "ok": not issues,
            "issues": issues,
            "orphans": sorted(orphans),
            "objects": objects,
            "blobs": blobs_checked,
        }
