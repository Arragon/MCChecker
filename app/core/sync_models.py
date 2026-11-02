"""M16/V3.1 Sync 领域模型、校验与纯函数状态聚合。

本模块只定义**值对象与不变量**，不读写文件、不碰 HTTP、不启动线程。后续
`app/services/sync_*.py` 负责持久化与编排；`SyncRun`/`SyncItem` 的落盘、
幂等和锁内提交由 M16-11/12/23 实现。

边界：
- 所有模型只含安全字段；没有 password/Cookie/CSRF/token/密钥字段。
- ID 是稳定、可校验的字符串；不接受路径分隔符/空白。
- 状态聚合是纯函数，输入 item 状态，输出 run 终态；不依赖时间或 I/O。
- Scope 至少带 workspace；profile 在需要时必填。
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from .errors import INVALID_INPUT, AppError

# ---------------------------------------------------------------------------
# 枚举 / 常量
# ---------------------------------------------------------------------------

SOURCE_KINDS: Tuple[str, ...] = ("http_file", "sda_catalog", "sda_table", "sda_file")
SOURCE_SCHEDULE_MODES: Tuple[str, ...] = ("inherit", "custom", "manual")
TARGET_KINDS: Tuple[str, ...] = ("file_archive", "default_candidate", "machine_observation")
CONNECTION_PROTOCOLS: Tuple[str, ...] = ("sda_v1",)

RUN_TRIGGERS: Tuple[str, ...] = ("manual", "schedule", "retry", "collector")
RUN_STATES: Tuple[str, ...] = (
    "queued",
    "running",
    "partial",
    "succeeded",
    "failed",
    "canceled",
    "interrupted",
)
ITEM_STATES: Tuple[str, ...] = (
    "queued",
    "running",
    "succeeded",
    "unchanged",
    "skipped",
    "blocked",
    "failed",
    "canceled",
    "interrupted",
)
ITEM_STAGES: Tuple[str, ...] = ("waiting", "collecting", "validating", "committing", "finished")
CANDIDATE_STATES: Tuple[str, ...] = (
    "pending",
    "applied",
    "dismissed",
    "superseded",
    "conflicted",
    "failed",
)
AUTH_STATES: Tuple[str, ...] = (
    "unconfigured",
    "valid",
    "expired",
    "locked",
    "disabled",
    "unknown",
)
FRESHNESS_ORIGINS: Tuple[str, ...] = ("remote", "manual", "unknown")
CONNECTION_EVENT_TYPES: Tuple[str, ...] = (
    "configured",
    "enabled",
    "disabled",
    "policy_changed",
    "credential_activated",
    "credential_revoked",
    "login_succeeded",
    "login_failed",
    "session_expired",
    "keepalive_renewed",
    "keepalive_failed",
)

REASON_CODES = frozenset(
    {
        "auth_failed",
        "business_error",
        "network_error",
        "redirect_blocked",
        "protocol_mismatch",
        "login_flow_changed",
        "bad_credentials",
        "locked",
        "collector_offline",
        "invalid_payload",
        "source_changed",
        "revision_conflict",
        "permission_denied",
        "too_large",
        "parse_failed",
        "not_found",
        "disabled",
        "canceled",
        "interrupted",
        "unknown",
        "unchanged",
        "validated",
        "keepalive_not_renewed",
        "heartbeat_pending",
        "candidate_conflict",
        "type_invalid",
        "target_missing",
        "device_missing",
        "storage_failure",
    }
)

SUCCESS_ITEM_STATES = frozenset({"succeeded", "unchanged"})
FAIL_ITEM_STATES = frozenset({"failed", "blocked", "interrupted"})
ACTIVE_ITEM_STATES = frozenset({"queued", "running"})
CANCELED_ITEM_STATES = frozenset({"canceled"})
NEUTRAL_ITEM_STATES = frozenset({"skipped"})
TERMINAL_RUN_STATES = frozenset({"partial", "succeeded", "failed", "canceled", "interrupted"})

FORBIDDEN_SECRET_KEYS = frozenset(
    {
        "password",
        "passwd",
        "cookie",
        "set-cookie",
        "csrf",
        "authorization",
        "token",
        "secret",
        "session_id",
        "har",
        "private_key",
        "api_key",
    }
)
FORBIDDEN_VALUE_MARKERS: Tuple[str, ...] = (
    "Bearer ",
    "Basic ",
    "password=",
    "Cookie:",
    "Set-Cookie:",
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _fail(message: str, field_name: str) -> None:
    raise AppError(INVALID_INPUT, message, field=field_name)


def _text(value: Any, field_name: str, max_len: int = 120) -> str:
    if not isinstance(value, str):
        _fail("%s 必须是字符串" % field_name, field_name)
    text = value.strip()
    if not text:
        _fail("%s 不能为空" % field_name, field_name)
    if len(text) > max_len:
        _fail("%s 过长（最多 %d）" % (field_name, max_len), field_name)
    if any(ord(ch) < 32 for ch in text):
        _fail("%s 含控制字符" % field_name, field_name)
    return text


def validate_id(value: Any, field_name: str = "id") -> str:
    text = _text(value, field_name, 80)
    if not _ID_RE.match(text):
        _fail("%s 含非法字符" % field_name, field_name)
    return text


def validate_workspace_id(value: Any) -> str:
    return validate_id(value, "workspace_id")


def validate_profile_id(value: Any) -> str:
    return validate_id(value, "profile_id")


def _optional_id(value: Any, field_name: str) -> Optional[str]:
    if value is None or value == "":
        return None
    return validate_id(value, field_name)


def validate_revision(value: Any, field_name: str = "revision") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("%s 必须是整数" % field_name, field_name)
    if value < 0:
        _fail("%s 不能为负" % field_name, field_name)
    return value


def validate_sha256(value: Any, field_name: str = "sha256", allow_none: bool = False) -> Optional[str]:
    if value is None or value == "":
        if allow_none:
            return None
        _fail("%s 不能为空" % field_name, field_name)
    text = _text(value, field_name, 64)
    if not _SHA_RE.match(text):
        _fail("%s 必须是 64 位小写十六进制" % field_name, field_name)
    return text


def validate_timestamp(value: Any, field_name: str = "timestamp", allow_none: bool = True) -> Optional[str]:
    if value is None or value == "":
        if allow_none:
            return None
        _fail("%s 不能为空" % field_name, field_name)
    text = _text(value, field_name, 40)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _fail("%s 必须是 ISO-8601 时间" % field_name, field_name)
    return text


def validate_enum(value: Any, allowed: Sequence[str], field_name: str) -> str:
    text = _text(value, field_name, 80)
    if text not in allowed:
        _fail("%s 不在允许值内" % field_name, field_name)
    return text


def validate_reason_code(value: Any, field_name: str = "reason_code") -> Optional[str]:
    if value is None or value == "":
        return None
    text = _text(value, field_name, 64)
    if text not in REASON_CODES:
        _fail("%s 不在允许值内" % field_name, field_name)
    return text


def validate_interval_hours(value: Any, field_name: str = "interval_hours") -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("%s 必须是整数" % field_name, field_name)
    if value < 1 or value > 2160:
        _fail("%s 必须在 1–2160 之间" % field_name, field_name)
    return value


def validate_origin(value: Any, field_name: str = "origin") -> str:
    text = _text(value, field_name, 200)
    parts = urlsplit(text)
    if parts.scheme != "https" or not parts.hostname:
        _fail("%s 必须是 https:// 绝对地址" % field_name, field_name)
    if parts.username or parts.password:
        _fail("%s 不能包含 userinfo" % field_name, field_name)
    if parts.query or parts.fragment:
        _fail("%s 不能包含 query/fragment" % field_name, field_name)
    if parts.path not in ("", "/"):
        _fail("%s 只能是 origin，不能带路径" % field_name, field_name)
    return text.rstrip("/")


def validate_safe_details(value: Any, field_name: str = "details") -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        _fail("%s 必须是对象" % field_name, field_name)
    _check_safe(value, field_name, 0)
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        _fail("%s 必须是可 JSON 序列化对象" % field_name, field_name)
    if len(encoded) > 8192:
        _fail("%s 过大" % field_name, field_name)
    return dict(value)


def _check_safe(value: Any, path: str, depth: int) -> None:
    if depth > 4:
        _fail("%s 嵌套过深" % path, path)
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                _fail("%s 的键必须是字符串" % path, path)
            if key.lower() in FORBIDDEN_SECRET_KEYS:
                _fail("%s 不允许包含秘密字段 %s" % (path, key), path)
            _check_safe(child, path + "." + key, depth + 1)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _check_safe(child, "%s[%d]" % (path, index), depth + 1)
    elif isinstance(value, str):
        for marker in FORBIDDEN_VALUE_MARKERS:
            if marker in value:
                _fail("%s 含疑似秘密内容" % path, path)
    elif value is not None and not isinstance(value, (bool, int, float)):
        _fail("%s 含不支持的类型" % path, path)


# ---------------------------------------------------------------------------
# 稳定身份
# ---------------------------------------------------------------------------


def _stable(prefix: str, *parts: Any) -> str:
    payload = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return "%s-%s" % (prefix, digest)


def make_connection_id(workspace_id: str, name: str) -> str:
    return _stable("conn", validate_workspace_id(workspace_id), _text(name, "name", 80))


def make_source_id(workspace_id: str, profile_id: str, name: str) -> str:
    return _stable(
        "src",
        validate_workspace_id(workspace_id),
        validate_profile_id(profile_id),
        _text(name, "name", 120),
    )


def make_candidate_id(source_id: str, binding_revision: int, content_hash: str, target_scope: str) -> str:
    return _stable(
        "cand",
        validate_id(source_id, "source_id"),
        validate_revision(binding_revision, "binding_revision"),
        validate_sha256(content_hash, "content_hash"),
        _text(target_scope, "target_scope", 160),
    )


def make_run_id(now: Optional[str] = None, nonce: Optional[str] = None) -> str:
    stamp = now or datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    stamp = re.sub(r"[^0-9A-Za-z]", "", stamp)[:20] or "run"
    token = nonce or secrets.token_hex(4)
    return "run-%s-%s" % (stamp, _text(token, "nonce", 32))


def make_item_id(run_id: str, source_id: str, profile_id: str) -> str:
    return _stable(
        "item",
        validate_id(run_id, "run_id"),
        validate_id(source_id, "source_id"),
        validate_profile_id(profile_id),
    )


def make_event_id(connection_id: str, at: str, sequence: int) -> str:
    return _stable(
        "evt",
        validate_id(connection_id, "connection_id"),
        validate_timestamp(at, "at", allow_none=False),
        validate_revision(sequence, "sequence"),
    )


# ---------------------------------------------------------------------------
# 值对象
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncTarget:
    kind: str
    file_id: Optional[str] = None
    configuration_id: Optional[str] = None
    release_id: Optional[str] = None
    device_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", validate_enum(self.kind, TARGET_KINDS, "target.kind"))
        object.__setattr__(self, "file_id", _optional_id(self.file_id, "target.file_id"))
        object.__setattr__(self, "configuration_id", _optional_id(self.configuration_id, "target.configuration_id"))
        object.__setattr__(self, "release_id", _optional_id(self.release_id, "target.release_id"))
        object.__setattr__(self, "device_id", _optional_id(self.device_id, "target.device_id"))
        if self.kind == "file_archive" and not self.file_id:
            _fail("file_archive 目标必须绑定 file_id", "target.file_id")
        if self.kind == "default_candidate" and not (self.configuration_id and self.release_id):
            _fail("default_candidate 必须绑定 configuration_id/release_id", "target.configuration_id")
        if self.kind == "machine_observation" and not self.device_id:
            _fail("machine_observation 必须绑定 device_id", "target.device_id")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "file_id": self.file_id,
            "configuration_id": self.configuration_id,
            "release_id": self.release_id,
            "device_id": self.device_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SyncTarget":
        return cls(
            kind=data.get("kind", ""),
            file_id=data.get("file_id"),
            configuration_id=data.get("configuration_id"),
            release_id=data.get("release_id"),
            device_id=data.get("device_id"),
        )


@dataclass(frozen=True)
class SdaConnection:
    connection_id: str
    workspace_id: str
    name: str
    origin: str
    protocol: str = "sda_v1"
    policy_revision: int = 0
    enabled: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "connection_id", validate_id(self.connection_id, "connection_id"))
        object.__setattr__(self, "workspace_id", validate_workspace_id(self.workspace_id))
        object.__setattr__(self, "name", _text(self.name, "name", 80))
        object.__setattr__(self, "origin", validate_origin(self.origin))
        object.__setattr__(self, "protocol", validate_enum(self.protocol, CONNECTION_PROTOCOLS, "protocol"))
        object.__setattr__(self, "policy_revision", validate_revision(self.policy_revision, "policy_revision"))
        if not isinstance(self.enabled, bool):
            _fail("enabled 必须是布尔值", "enabled")
        object.__setattr__(self, "created_at", validate_timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", validate_timestamp(self.updated_at, "updated_at"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "connection_id": self.connection_id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "origin": self.origin,
            "protocol": self.protocol,
            "policy_revision": self.policy_revision,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SdaConnection":
        return cls(
            connection_id=data.get("connection_id", ""),
            workspace_id=data.get("workspace_id", ""),
            name=data.get("name", ""),
            origin=data.get("origin", ""),
            protocol=data.get("protocol", "sda_v1"),
            policy_revision=data.get("policy_revision", 0),
            enabled=data.get("enabled", True),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )


@dataclass(frozen=True)
class SyncSource:
    source_id: str
    workspace_id: str
    profile_id: str
    revision: int
    name: str
    kind: str
    target: Optional[SyncTarget] = None
    enabled: bool = True
    schedule_mode: str = "inherit"
    interval_hours: Optional[int] = None
    connection_id: Optional[str] = None
    resource_ref: Optional[str] = None
    display_path: Optional[str] = None
    record_source: Optional[str] = None
    keyword: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", validate_id(self.source_id, "source_id"))
        object.__setattr__(self, "workspace_id", validate_workspace_id(self.workspace_id))
        object.__setattr__(self, "profile_id", validate_profile_id(self.profile_id))
        object.__setattr__(self, "revision", validate_revision(self.revision, "revision"))
        object.__setattr__(self, "name", _text(self.name, "name", 120))
        object.__setattr__(self, "kind", validate_enum(self.kind, SOURCE_KINDS, "kind"))
        if self.target is not None and not isinstance(self.target, SyncTarget):
            _fail("target 必须是 SyncTarget", "target")
        if self.kind != "sda_catalog" and self.target is None:
            _fail("非 catalog 来源必须绑定 target", "target")
        object.__setattr__(self, "schedule_mode", validate_enum(self.schedule_mode, SOURCE_SCHEDULE_MODES, "schedule_mode"))
        object.__setattr__(self, "interval_hours", validate_interval_hours(self.interval_hours))
        if self.schedule_mode == "custom" and self.interval_hours is None:
            _fail("custom 计划必须给 interval_hours", "interval_hours")
        if self.schedule_mode != "custom" and self.interval_hours is not None:
            _fail("inherit/manual 不能带 interval_hours", "interval_hours")
        if not isinstance(self.enabled, bool):
            _fail("enabled 必须是布尔值", "enabled")
        object.__setattr__(self, "connection_id", _optional_id(self.connection_id, "connection_id"))
        object.__setattr__(self, "resource_ref", _text(self.resource_ref, "resource_ref", 160) if self.resource_ref else None)
        object.__setattr__(self, "display_path", _text(self.display_path, "display_path", 240) if self.display_path else None)
        object.__setattr__(self, "record_source", _text(self.record_source, "record_source", 240) if self.record_source else None)
        if not isinstance(self.keyword, str):
            _fail("keyword 必须是字符串", "keyword")
        object.__setattr__(self, "keyword", self.keyword.strip())
        object.__setattr__(self, "created_at", validate_timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", validate_timestamp(self.updated_at, "updated_at"))
        if self.kind.startswith("sda_") and not (self.connection_id and self.resource_ref):
            _fail("SDA 来源必须绑定 connection_id/resource_ref", "resource_ref")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "workspace_id": self.workspace_id,
            "profile_id": self.profile_id,
            "revision": self.revision,
            "name": self.name,
            "kind": self.kind,
            "target": self.target.to_dict() if self.target else None,
            "enabled": self.enabled,
            "schedule_mode": self.schedule_mode,
            "interval_hours": self.interval_hours,
            "connection_id": self.connection_id,
            "resource_ref": self.resource_ref,
            "display_path": self.display_path,
            "record_source": self.record_source,
            "keyword": self.keyword,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SyncSource":
        target = data.get("target")
        return cls(
            source_id=data.get("source_id", ""),
            workspace_id=data.get("workspace_id", ""),
            profile_id=data.get("profile_id", ""),
            revision=data.get("revision", 0),
            name=data.get("name", ""),
            kind=data.get("kind", ""),
            target=SyncTarget.from_dict(target) if target else None,
            enabled=data.get("enabled", True),
            schedule_mode=data.get("schedule_mode", "inherit"),
            interval_hours=data.get("interval_hours"),
            connection_id=data.get("connection_id"),
            resource_ref=data.get("resource_ref"),
            display_path=data.get("display_path"),
            record_source=data.get("record_source"),
            keyword=data.get("keyword", ""),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )


@dataclass(frozen=True)
class SyncRun:
    run_id: str
    workspace_id: str
    trigger: str
    state: str = "queued"
    counts: Dict[str, int] = field(default_factory=dict)
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    parent_run_id: Optional[str] = None
    plan_id: Optional[str] = None
    request_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        object.__setattr__(self, "workspace_id", validate_workspace_id(self.workspace_id))
        object.__setattr__(self, "trigger", validate_enum(self.trigger, RUN_TRIGGERS, "trigger"))
        object.__setattr__(self, "state", validate_enum(self.state, RUN_STATES, "state"))
        object.__setattr__(self, "counts", _validate_counts(self.counts))
        object.__setattr__(self, "created_at", validate_timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "started_at", validate_timestamp(self.started_at, "started_at"))
        object.__setattr__(self, "finished_at", validate_timestamp(self.finished_at, "finished_at"))
        object.__setattr__(self, "parent_run_id", _optional_id(self.parent_run_id, "parent_run_id"))
        object.__setattr__(self, "plan_id", _optional_id(self.plan_id, "plan_id"))
        object.__setattr__(self, "request_id", _optional_id(self.request_id, "request_id"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "trigger": self.trigger,
            "state": self.state,
            "counts": dict(self.counts),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "parent_run_id": self.parent_run_id,
            "plan_id": self.plan_id,
            "request_id": self.request_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SyncRun":
        return cls(
            run_id=data.get("run_id", ""),
            workspace_id=data.get("workspace_id", ""),
            trigger=data.get("trigger", ""),
            state=data.get("state", "queued"),
            counts=data.get("counts") or {},
            created_at=data.get("created_at"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            parent_run_id=data.get("parent_run_id"),
            plan_id=data.get("plan_id"),
            request_id=data.get("request_id"),
        )


@dataclass(frozen=True)
class SyncItem:
    item_id: str
    run_id: str
    source_id: str
    workspace_id: str
    profile_id: str
    state: str = "queued"
    stage: str = "waiting"
    reason_code: Optional[str] = None
    using_last_good: bool = False
    last_verified_at: Optional[str] = None
    candidate_id: Optional[str] = None
    observation_id: Optional[str] = None
    file_version_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", validate_id(self.item_id, "item_id"))
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        object.__setattr__(self, "source_id", validate_id(self.source_id, "source_id"))
        object.__setattr__(self, "workspace_id", validate_workspace_id(self.workspace_id))
        object.__setattr__(self, "profile_id", validate_profile_id(self.profile_id))
        object.__setattr__(self, "state", validate_enum(self.state, ITEM_STATES, "state"))
        object.__setattr__(self, "stage", validate_enum(self.stage, ITEM_STAGES, "stage"))
        object.__setattr__(self, "reason_code", validate_reason_code(self.reason_code))
        if not isinstance(self.using_last_good, bool):
            _fail("using_last_good 必须是布尔值", "using_last_good")
        object.__setattr__(self, "last_verified_at", validate_timestamp(self.last_verified_at, "last_verified_at"))
        object.__setattr__(self, "candidate_id", _optional_id(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "observation_id", _optional_id(self.observation_id, "observation_id"))
        object.__setattr__(self, "file_version_id", _optional_id(self.file_version_id, "file_version_id"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "run_id": self.run_id,
            "source_id": self.source_id,
            "workspace_id": self.workspace_id,
            "profile_id": self.profile_id,
            "state": self.state,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "using_last_good": self.using_last_good,
            "last_verified_at": self.last_verified_at,
            "candidate_id": self.candidate_id,
            "observation_id": self.observation_id,
            "file_version_id": self.file_version_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SyncItem":
        return cls(
            item_id=data.get("item_id", ""),
            run_id=data.get("run_id", ""),
            source_id=data.get("source_id", ""),
            workspace_id=data.get("workspace_id", ""),
            profile_id=data.get("profile_id", ""),
            state=data.get("state", "queued"),
            stage=data.get("stage", "waiting"),
            reason_code=data.get("reason_code"),
            using_last_good=data.get("using_last_good", False),
            last_verified_at=data.get("last_verified_at"),
            candidate_id=data.get("candidate_id"),
            observation_id=data.get("observation_id"),
            file_version_id=data.get("file_version_id"),
        )


@dataclass(frozen=True)
class SyncCandidate:
    candidate_id: str
    workspace_id: str
    profile_id: str
    source_id: str
    binding_revision: int
    content_hash: str
    target_scope: str
    state: str = "pending"
    created_at: Optional[str] = None
    superseded_by: Optional[str] = None
    file_version_id: Optional[str] = None
    plan_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", validate_id(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "workspace_id", validate_workspace_id(self.workspace_id))
        object.__setattr__(self, "profile_id", validate_profile_id(self.profile_id))
        object.__setattr__(self, "source_id", validate_id(self.source_id, "source_id"))
        object.__setattr__(self, "binding_revision", validate_revision(self.binding_revision, "binding_revision"))
        object.__setattr__(self, "content_hash", validate_sha256(self.content_hash, "content_hash"))
        object.__setattr__(self, "target_scope", _text(self.target_scope, "target_scope", 160))
        object.__setattr__(self, "state", validate_enum(self.state, CANDIDATE_STATES, "state"))
        object.__setattr__(self, "created_at", validate_timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "superseded_by", _optional_id(self.superseded_by, "superseded_by"))
        object.__setattr__(self, "file_version_id", _optional_id(self.file_version_id, "file_version_id"))
        object.__setattr__(self, "plan_id", _optional_id(self.plan_id, "plan_id"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "workspace_id": self.workspace_id,
            "profile_id": self.profile_id,
            "source_id": self.source_id,
            "binding_revision": self.binding_revision,
            "content_hash": self.content_hash,
            "target_scope": self.target_scope,
            "state": self.state,
            "created_at": self.created_at,
            "superseded_by": self.superseded_by,
            "file_version_id": self.file_version_id,
            "plan_id": self.plan_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SyncCandidate":
        return cls(
            candidate_id=data.get("candidate_id", ""),
            workspace_id=data.get("workspace_id", ""),
            profile_id=data.get("profile_id", ""),
            source_id=data.get("source_id", ""),
            binding_revision=data.get("binding_revision", 0),
            content_hash=data.get("content_hash", ""),
            target_scope=data.get("target_scope", ""),
            state=data.get("state", "pending"),
            created_at=data.get("created_at"),
            superseded_by=data.get("superseded_by"),
            file_version_id=data.get("file_version_id"),
            plan_id=data.get("plan_id"),
        )


@dataclass(frozen=True)
class SourceFreshness:
    workspace_id: str
    profile_id: str
    source_id: str
    last_attempt_at: Optional[str] = None
    last_verified_at: Optional[str] = None
    last_commit_at: Optional[str] = None
    last_good_sha: Optional[str] = None
    last_good_version_id: Optional[str] = None
    source_version: Optional[str] = None
    current_origin: str = "unknown"
    reason_code: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_id", validate_workspace_id(self.workspace_id))
        object.__setattr__(self, "profile_id", validate_profile_id(self.profile_id))
        object.__setattr__(self, "source_id", validate_id(self.source_id, "source_id"))
        object.__setattr__(self, "last_attempt_at", validate_timestamp(self.last_attempt_at, "last_attempt_at"))
        object.__setattr__(self, "last_verified_at", validate_timestamp(self.last_verified_at, "last_verified_at"))
        object.__setattr__(self, "last_commit_at", validate_timestamp(self.last_commit_at, "last_commit_at"))
        object.__setattr__(self, "last_good_sha", validate_sha256(self.last_good_sha, "last_good_sha", allow_none=True))
        object.__setattr__(self, "last_good_version_id", _optional_id(self.last_good_version_id, "last_good_version_id"))
        object.__setattr__(self, "source_version", _text(self.source_version, "source_version", 120) if self.source_version else None)
        object.__setattr__(self, "current_origin", validate_enum(self.current_origin, FRESHNESS_ORIGINS, "current_origin"))
        object.__setattr__(self, "reason_code", validate_reason_code(self.reason_code))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "profile_id": self.profile_id,
            "source_id": self.source_id,
            "last_attempt_at": self.last_attempt_at,
            "last_verified_at": self.last_verified_at,
            "last_commit_at": self.last_commit_at,
            "last_good_sha": self.last_good_sha,
            "last_good_version_id": self.last_good_version_id,
            "source_version": self.source_version,
            "current_origin": self.current_origin,
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SourceFreshness":
        return cls(
            workspace_id=data.get("workspace_id", ""),
            profile_id=data.get("profile_id", ""),
            source_id=data.get("source_id", ""),
            last_attempt_at=data.get("last_attempt_at"),
            last_verified_at=data.get("last_verified_at"),
            last_commit_at=data.get("last_commit_at"),
            last_good_sha=data.get("last_good_sha"),
            last_good_version_id=data.get("last_good_version_id"),
            source_version=data.get("source_version"),
            current_origin=data.get("current_origin", "unknown"),
            reason_code=data.get("reason_code"),
        )


@dataclass(frozen=True)
class ConnectionEvent:
    event_id: str
    workspace_id: str
    connection_id: str
    event_type: str
    at: str
    reason_code: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", validate_id(self.event_id, "event_id"))
        object.__setattr__(self, "workspace_id", validate_workspace_id(self.workspace_id))
        object.__setattr__(self, "connection_id", validate_id(self.connection_id, "connection_id"))
        object.__setattr__(self, "event_type", validate_enum(self.event_type, CONNECTION_EVENT_TYPES, "event_type"))
        object.__setattr__(self, "at", validate_timestamp(self.at, "at", allow_none=False))
        object.__setattr__(self, "reason_code", validate_reason_code(self.reason_code))
        object.__setattr__(self, "details", validate_safe_details(self.details))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "workspace_id": self.workspace_id,
            "connection_id": self.connection_id,
            "event_type": self.event_type,
            "at": self.at,
            "reason_code": self.reason_code,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConnectionEvent":
        return cls(
            event_id=data.get("event_id", ""),
            workspace_id=data.get("workspace_id", ""),
            connection_id=data.get("connection_id", ""),
            event_type=data.get("event_type", ""),
            at=data.get("at", ""),
            reason_code=data.get("reason_code"),
            details=data.get("details") or {},
        )


# ---------------------------------------------------------------------------
# 纯函数状态聚合
# ---------------------------------------------------------------------------


def _state_list(items: Iterable[Any]) -> List[str]:
    states: List[str] = []
    for item in items:
        if isinstance(item, SyncItem):
            state = item.state
        elif isinstance(item, str):
            state = item
        else:
            raise AppError(INVALID_INPUT, "聚合输入必须是 SyncItem 或状态字符串", field="items")
        if state not in ITEM_STATES:
            raise AppError(INVALID_INPUT, "未知 item 状态", field="items", state=state)
        states.append(state)
    return states


def aggregate_counts(items: Iterable[Any]) -> Dict[str, int]:
    states = _state_list(items)
    counts: Dict[str, int] = {"total": len(states)}
    for state in ITEM_STATES:
        counts[state] = 0
    for state in states:
        counts[state] += 1
    return counts


def aggregate_run_state(items: Iterable[Any]) -> str:
    """把子项状态聚合为 run 终态。

    - 空/仍有 active -> queued/running
    - 全 success/unchanged/skipped -> succeeded
    - 全 failed/blocked/interrupted -> failed
    - 全 canceled/skipped -> canceled
    - 其余混合 -> partial
    """
    states = _state_list(items)
    if not states:
        return "queued"
    if any(state in ACTIVE_ITEM_STATES for state in states):
        return "running"
    if all(state in CANCELED_ITEM_STATES or state in NEUTRAL_ITEM_STATES for state in states):
        return "canceled" if any(state == "canceled" for state in states) else "succeeded"
    if all(state in FAIL_ITEM_STATES for state in states):
        return "failed"
    if all(state in SUCCESS_ITEM_STATES or state in NEUTRAL_ITEM_STATES for state in states):
        return "succeeded"
    return "partial"


def is_terminal_run_state(state: str) -> bool:
    if state not in RUN_STATES:
        raise AppError(INVALID_INPUT, "未知 run 状态", field="state", state=state)
    return state in TERMINAL_RUN_STATES


def can_cancel_run(state: str) -> bool:
    if state not in RUN_STATES:
        raise AppError(INVALID_INPUT, "未知 run 状态", field="state", state=state)
    return state in ("queued", "running")


def _validate_counts(value: Any) -> Dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AppError(INVALID_INPUT, "counts 必须是对象", field="counts")
    allowed = {
        "total",
        "queued",
        "running",
        "succeeded",
        "unchanged",
        "skipped",
        "blocked",
        "failed",
        "canceled",
        "interrupted",
        "candidate_created",
        "observation_created",
        "file_updated",
    }
    result: Dict[str, int] = {}
    for key, raw in value.items():
        if key not in allowed:
            raise AppError(INVALID_INPUT, "未知 counts 键", field="counts", key=key)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise AppError(INVALID_INPUT, "counts 值必须是非负整数", field="counts", key=key)
        result[key] = raw
    return result


def to_json(value: Any) -> str:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
