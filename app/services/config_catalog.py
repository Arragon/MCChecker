"""配置目录：软件版本、出口批次、配置族、逻辑文件与模板变体（M6-01 / M6-02）。

本模块只做**业务校验与事务编排**，落盘一律交给 `core.config_repository`；
不 import `storage`（决策 D1），也不触碰 HTTP（分层：server → services → core）。

## 三条硬规则（卡面验收的落点）

1. **版本必须显式给出**：`register_snapshot` 要求调用方明确 `release_id`，
   本模块**没有**"按文件名/目录名/mtime 猜版本"的入口。归档目录名只作为
   `archive_ref` 的证据出现，永远不参与版本判定（`FileSnapshot.archive_ref`）。
2. **同版本补发是新增批次，不是覆盖**：`publish_default_batch` 每次都新建一个
   `release_batch` 对象（新 ID、`supersedes` 指向上一个），并把 state 指针
   `release_default[release_id]` 指向它。旧批次对象与其快照的 blob 一个字节都不改，
   因此"补发后旧默认 XML 哈希与下载内容不变"是结构上成立的，不依赖实现纪律。
3. **同一名称不得同时充当默认 XML 与现场 JSON**：`role` 在
   `REPRESENTATION_ROLES` 内时，同一配置族里同名但不同表示直接
   `DEFINITION_CONFLICT`；改名走 `rename_logical_file`，稳定 ID 不变、旧名进入
   `aliases` 的闭合记录（`until` 非空），历史仍可按旧名解析。

## 逻辑文件与快照的关系

`logical_file` 是"这是哪个业务文件"（稳定身份，改名换表示都不换 ID）；
`file_snapshot` 是"某个软件版本里它长什么样"（内容寻址、不可变）。
A/B 两个机型可能出现同名的同名文件，因此 `file_snapshot` 的身份是
`(release_id, logical_file_id, blob_sha256)`——同名不同内容各自成对象，
在 `archive_ref.pid` 里各自留痕，不会互相覆盖。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET

from ..core import config_mapping, config_values
from ..core.config_repository import ConfigRepository, now_iso, sha256_bytes
from ..core.errors import (
    CORRUPT_DATA,
    DEFINITION_CONFLICT,
    INVALID_INPUT,
    NOT_FOUND,
    PARSE_FAILED,
    REVISION_CONFLICT,
    AppError,
)

# -- 枚举 -------------------------------------------------------------------

RELEASE_ACTIVE = "active"
RELEASE_RETIRED = "retired"
RELEASE_STATUSES = (RELEASE_ACTIVE, RELEASE_RETIRED)

SOURCE_MANUAL = "manual"
SOURCE_ARCHIVE = "archive"
SOURCE_UPLOAD = "upload"
SOURCES = (SOURCE_MANUAL, SOURCE_ARCHIVE, SOURCE_UPLOAD)

ROLE_XML_EXPORT = "xml_export"
ROLE_FIELD_JSON = "field_json"
ROLE_READBACK = "readback"
ROLES = (ROLE_XML_EXPORT, ROLE_FIELD_JSON, ROLE_READBACK)

#: 同一业务配置的两种表示。一个逻辑文件只能取其一，同名也不能两边都登记。
REPRESENTATION_ROLES = (ROLE_XML_EXPORT, ROLE_FIELD_JSON)

SNAPSHOT_CONFIRMED = "confirmed"
SNAPSHOT_UNCONFIRMED = "unconfirmed"

LIFECYCLE_ACTIVE = "active"
LIFECYCLE_RETIRED = "retired"

#: 默认批次的指针名（`state.json` 的 `pointers.release_default[release_id]`）。
POINTER_RELEASE_DEFAULT = "release_default"

_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
_VERSION_SPLIT = re.compile(r"(\d+)")


# -- 小工具 -----------------------------------------------------------------


def _text(value: Any, field: str) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise AppError(INVALID_INPUT, "%s不能为空" % field, field=field)
    return text


def _optional_text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _label(actor: Any) -> str:
    """操作者标签：优先姓名，其次 IP（与审计事件里的 actor 同源）。"""
    if actor is None:
        return ""
    if isinstance(actor, dict):
        return str(actor.get("name") or actor.get("ip") or "")
    return str(getattr(actor, "name", "") or getattr(actor, "ip", "") or "")


def version_key(label: str) -> Tuple[Tuple[int, Any], ...]:
    """版本号排序键：`1.10.0` 排在 `1.9.0` 之后（纯字符串排序会排反）。"""
    parts: List[Tuple[int, Any]] = []
    for chunk in _VERSION_SPLIT.split(str(label or "")):
        if not chunk:
            continue
        if chunk.isdigit():
            parts.append((0, int(chunk)))
        else:
            parts.append((1, chunk.lower()))
    return tuple(parts)


def _id_list(value: Any, field: str) -> List[str]:
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise AppError(INVALID_INPUT, "%s必须是字符串数组" % field, field=field)
    ids: List[str] = []
    for item in value:
        text = _optional_text(item)
        if not text:
            raise AppError(INVALID_INPUT, "%s里不能有空值" % field, field=field)
        if text not in ids:  # 同批去重，保留调用方顺序
            ids.append(text)
    return ids


# -- 软件版本（M6-01）-------------------------------------------------------


def _replayed(repo: ConfigRepository, idempotency_key: Optional[str]) -> bool:
    """该幂等键已经提交过 → 这是重试，不该再报"已存在"。

    业务唯一性（版本/编码/文件名重复）与幂等重放是两件事：重试必须返回原回执，
    重复登记才该 409。交给事务层判定：同键同体 → 重放，同键异体 → `IDEMPOTENCY_MISMATCH`。
    """
    return bool(idempotency_key) and repo.receipt(str(idempotency_key)) is not None


def list_releases(repo: ConfigRepository) -> List[Dict[str, Any]]:
    """全部软件版本，按版本号从新到旧（同版本按构建号）。"""
    items = repo.list_objects("software_release")
    return sorted(
        items,
        key=lambda item: (
            version_key(item.get("version_label") or ""),
            _optional_text(item.get("build")),
            item.get("id") or "",
        ),
        reverse=True,
    )


def get_release(repo: ConfigRepository, release_id: str) -> Dict[str, Any]:
    payload = repo.get("software_release", _optional_text(release_id))
    if payload is None:
        raise AppError(NOT_FOUND, "软件版本不存在", release_id=release_id)
    return payload


def _find_release(
    repo: ConfigRepository, version_label: str, build: str
) -> Optional[Dict[str, Any]]:
    for item in list_releases(repo):
        if (item.get("version_label") or "") != version_label:
            continue
        if _optional_text(item.get("build")) != build:
            continue
        return item
    return None


def register_release(
    repo: ConfigRepository,
    actor: Any,
    *,
    version_label: Any,
    build: Any = None,
    predecessor_id: Optional[str] = None,
    source: str = SOURCE_MANUAL,
    notes: Any = "",
    status: str = RELEASE_ACTIVE,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """登记一个软件版本（`SoftwareRelease`）。

    * `version_label` 与 `build` 的组合唯一：重复登记是 `DEFINITION_CONFLICT`，
      并带回已存在的 `release_id`（前端可直接跳转，不用再查一次）；
    * `predecessor_id` 必须显式给出且已存在——**不从版本号大小推断前驱**；
    * `source` 只说明这条登记的来源，不含任何"从文件名推测"的路径。
    """
    label = _text(version_label, "版本号")
    build_text = _optional_text(build)
    if status not in RELEASE_STATUSES:
        raise AppError(
            INVALID_INPUT, "版本状态非法", field="status", allowed=list(RELEASE_STATUSES)
        )
    if source not in SOURCES:
        raise AppError(INVALID_INPUT, "版本来源非法", field="source", allowed=list(SOURCES))

    predecessor = _optional_text(predecessor_id) or None
    if predecessor is not None:
        get_release(repo, predecessor)  # 显式前驱必须存在，否则整条链是假的

    existing = _find_release(repo, label, build_text)
    if existing is not None and not _replayed(repo, idempotency_key):
        raise AppError(
            DEFINITION_CONFLICT,
            "同一版本号与构建号已登记",
            version_label=label,
            build=build_text,
            release_id=existing["id"],
        )

    stamp = now_iso()
    tx = repo.transaction(
        actor,
        "登记软件版本 %s%s" % (label, ("（%s）" % build_text) if build_text else ""),
        action="release.register",
        idempotency_key=idempotency_key,
    )
    ref = tx.put(
        "software_release",
        {
            "version_label": label,
            "build": build_text,
            "predecessor_id": predecessor,
            "status": status,
            "source": source,
            "notes": _optional_text(notes),
            "updated_at": stamp,
        },
    )
    tx.commit()
    return get_release(repo, str(ref.id))


def retire_release(
    repo: ConfigRepository,
    actor: Any,
    *,
    release_id: str,
    reason: Any = "",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """退役一个版本（只改状态，历史对象一个不动）。"""
    release = get_release(repo, release_id)
    if release.get("status") == RELEASE_RETIRED:
        return release
    stamp = now_iso()
    payload = dict(release)
    payload.pop("kind", None)
    payload.pop("revision", None)
    payload["status"] = RELEASE_RETIRED
    payload["updated_at"] = stamp
    payload["retired_at"] = stamp
    tx = repo.transaction(
        actor,
        _optional_text(reason) or "退役软件版本 %s" % release.get("version_label"),
        action="release.retire",
        expected={"software_release:%s" % release["id"]: release.get("revision")},
        idempotency_key=idempotency_key,
    )
    tx.put("software_release", payload, object_id=release["id"])
    tx.commit()
    return get_release(repo, release["id"])


# -- 出口批次（M6-01）-------------------------------------------------------


def default_batch(repo: ConfigRepository, release_id: str) -> Optional[Dict[str, Any]]:
    """当前默认出口批次；没有发布过则为 None（不是空批次）。"""
    batch_id = repo.pointer(POINTER_RELEASE_DEFAULT, str(release_id))
    if not batch_id:
        return None
    return repo.get("release_batch", str(batch_id))


def get_snapshot(repo: ConfigRepository, snapshot_id: str) -> Dict[str, Any]:
    payload = repo.get("file_snapshot", _optional_text(snapshot_id))
    if payload is None:
        raise AppError(NOT_FOUND, "文件快照不存在", snapshot_id=snapshot_id)
    return payload


def batch_snapshots(repo: ConfigRepository, batch: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按批次记录的顺序取快照；引用的快照缺失即数据损坏，不当空列表。"""
    items: List[Dict[str, Any]] = []
    for snapshot_id in batch.get("snapshot_ids") or []:
        payload = repo.get("file_snapshot", str(snapshot_id))
        if payload is None:
            raise AppError(
                CORRUPT_DATA,
                "出口批次引用的文件快照缺失",
                batch_id=batch.get("id"),
                snapshot_id=snapshot_id,
            )
        items.append(payload)
    return items


def publish_default_batch(
    repo: ConfigRepository,
    actor: Any,
    *,
    release_id: str,
    snapshot_ids: Any,
    note: Any = "",
    supersedes: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    expected: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """发布（或补发）默认出口批次——**新增**批次对象，绝不改写旧批次。

    `supersedes` 缺省取当前默认批次，于是补发天然形成可追溯的链；
    显式传 `None` 只能通过"当前没有默认批次"达成。
    """
    release = get_release(repo, release_id)
    ids = _id_list(snapshot_ids, "snapshot_ids")
    if not ids:
        raise AppError(INVALID_INPUT, "出口批次至少要包含一个文件快照", field="snapshot_ids")

    for snapshot_id in ids:
        snapshot = get_snapshot(repo, snapshot_id)
        if str(snapshot.get("release_id")) != str(release["id"]):
            raise AppError(
                INVALID_INPUT,
                "文件快照不属于该软件版本",
                snapshot_id=snapshot_id,
                release_id=release["id"],
                snapshot_release_id=snapshot.get("release_id"),
            )

    previous = default_batch(repo, release["id"])
    supersede_id = _optional_text(supersedes) or None
    if supersede_id is None and previous is not None:
        supersede_id = previous["id"]
    elif supersede_id is not None and repo.get("release_batch", supersede_id) is None:
        raise AppError(NOT_FOUND, "被取代的批次不存在", batch_id=supersede_id)

    stamp = now_iso()
    tx = repo.transaction(
        actor,
        _optional_text(note) or "发布 %s 默认出口批次" % release.get("version_label"),
        action="release_default.publish",
        idempotency_key=idempotency_key,
        expected=expected,
    )
    batch = tx.put(
        "release_batch",
        {
            "release_id": release["id"],
            "snapshot_ids": ids,
            "supersedes": supersede_id,
            "created_by": _label(actor),
            "note": _optional_text(note),
            "updated_at": stamp,
        },
    )
    tx.pointer(POINTER_RELEASE_DEFAULT, release["id"], batch)
    tx.commit()
    return repo.get("release_batch", str(batch.id)) or {}


def release_detail(repo: ConfigRepository, release_id: str) -> Dict[str, Any]:
    """版本详情：版本本身 + 默认批次 + 该批次里的快照（只读，供 API 直接用）。"""
    release = get_release(repo, release_id)
    batch = default_batch(repo, release["id"])
    snapshots = batch_snapshots(repo, batch) if batch else []
    return {
        "release": release,
        "default_batch": batch,
        "snapshots": snapshots,
        "batch_count": len(_batches_of(repo, release["id"])),
    }


def _batches_of(repo: ConfigRepository, release_id: str) -> List[Dict[str, Any]]:
    return [
        item
        for item in repo.list_objects("release_batch")
        if str(item.get("release_id")) == str(release_id)
    ]


def release_batches(repo: ConfigRepository, release_id: str) -> List[Dict[str, Any]]:
    """某版本的全部出口批次，按创建时间/ID 倒序（补发链一目了然）。"""
    items = _batches_of(repo, release_id)
    return sorted(
        items, key=lambda item: (item.get("created_at") or "", item.get("id") or ""), reverse=True
    )


# -- 配置族（M6-02）---------------------------------------------------------


def list_families(repo: ConfigRepository, *, include_retired: bool = True) -> List[Dict[str, Any]]:
    items = repo.list_objects("family")
    if not include_retired:
        items = [item for item in items if item.get("status") != LIFECYCLE_RETIRED]
    return sorted(items, key=lambda item: (item.get("code") or "", item.get("id") or ""))


def get_family(repo: ConfigRepository, family_id: str) -> Dict[str, Any]:
    payload = repo.get("family", _optional_text(family_id))
    if payload is None:
        raise AppError(NOT_FOUND, "配置类别不存在", family_id=family_id)
    return payload


def create_family(
    repo: ConfigRepository,
    actor: Any,
    *,
    name: Any,
    code: Any,
    notes: Any = "",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """新建配置类别；`code` 是全大写稳定键，唯一且不再改动。"""
    family_name = _text(name, "配置类别名称")
    family_code = _text(code, "配置类别编码").upper()
    if not _CODE_RE.match(family_code):
        raise AppError(
            INVALID_INPUT,
            "配置类别编码只能是大写字母/数字/下划线，且以字母开头",
            field="code",
            value=family_code,
        )
    for item in list_families(repo):
        if (item.get("code") or "").upper() == family_code:
            if _replayed(repo, idempotency_key):
                break
            raise AppError(
                DEFINITION_CONFLICT,
                "配置类别编码已存在",
                family_code=family_code,
                family_id=item["id"],
            )
    stamp = now_iso()
    tx = repo.transaction(
        actor,
        "新建配置类别 %s" % family_name,
        action="family.create",
        idempotency_key=idempotency_key,
    )
    ref = tx.put(
        "family",
        {
            "name": family_name,
            "code": family_code,
            "notes": _optional_text(notes),
            "status": LIFECYCLE_ACTIVE,
            "updated_at": stamp,
        },
    )
    tx.commit()
    return get_family(repo, str(ref.id))


# -- 逻辑文件（M6-02）-------------------------------------------------------


def list_logical_files(
    repo: ConfigRepository,
    family_id: Optional[str] = None,
    *,
    role: Optional[str] = None,
    include_retired: bool = True,
) -> List[Dict[str, Any]]:
    items = repo.list_objects("logical_file")
    if family_id is not None:
        items = [item for item in items if str(item.get("family_id")) == str(family_id)]
    if role is not None:
        items = [item for item in items if item.get("role") == role]
    if not include_retired:
        items = [item for item in items if item.get("status") != LIFECYCLE_RETIRED]
    return sorted(items, key=lambda item: (item.get("display_name") or "", item.get("id") or ""))


def get_logical_file(repo: ConfigRepository, file_id: str) -> Dict[str, Any]:
    payload = repo.get("logical_file", _optional_text(file_id))
    if payload is None:
        raise AppError(NOT_FOUND, "逻辑文件不存在", logical_file_id=file_id)
    return payload


def _open_alias(logical_file: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for alias in logical_file.get("aliases") or []:
        if alias.get("until") in (None, ""):
            return alias
    return None


def _find_logical_file_by_name(
    repo: ConfigRepository,
    family_id: str,
    role: str,
    display_name: str,
    *,
    include_retired: bool = True,
) -> Optional[Dict[str, Any]]:
    for item in list_logical_files(
        repo, family_id, role=role, include_retired=include_retired
    ):
        if item.get("display_name") == display_name:
            return item
    return None


def _assert_no_representation_conflict(
    repo: ConfigRepository, family_id: str, role: str, display_name: str
) -> None:
    """同一名称不得同时充当默认 XML 与现场 JSON（卡面硬规则）。"""
    if role not in REPRESENTATION_ROLES:
        return
    for other_role in REPRESENTATION_ROLES:
        if other_role == role:
            continue
        clash = _find_logical_file_by_name(repo, family_id, other_role, display_name)
        if clash is not None:
            raise AppError(
                DEFINITION_CONFLICT,
                "同一名称不能同时作为默认 XML 与现场 JSON 的表示",
                display_name=display_name,
                role=role,
                conflicting_role=other_role,
                conflicting_id=clash["id"],
            )


def create_logical_file(
    repo: ConfigRepository,
    actor: Any,
    *,
    family_id: str,
    role: str,
    display_name: Any,
    notes: Any = "",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """登记一个逻辑文件；`(family_id, role, display_name)` 唯一。"""
    family = get_family(repo, family_id)
    if role not in ROLES:
        raise AppError(INVALID_INPUT, "文件角色非法", field="role", allowed=list(ROLES))
    name = _text(display_name, "文件名")
    existing = _find_logical_file_by_name(repo, family["id"], role, name)
    if existing is not None and not _replayed(repo, idempotency_key):
        raise AppError(
            DEFINITION_CONFLICT,
            "该配置类别下已有同角色同名的逻辑文件",
            display_name=name,
            role=role,
            logical_file_id=existing["id"],
        )
    _assert_no_representation_conflict(repo, family["id"], role, name)

    stamp = now_iso()
    tx = repo.transaction(
        actor,
        "登记逻辑文件 %s" % name,
        action="logical_file.create",
        idempotency_key=idempotency_key,
    )
    ref = tx.put(
        "logical_file",
        {
            "family_id": family["id"],
            "role": role,
            "display_name": name,
            "aliases": [{"name": name, "since": stamp, "until": None}],
            "notes": _optional_text(notes),
            "status": LIFECYCLE_ACTIVE,
            "updated_at": stamp,
        },
    )
    tx.commit()
    return get_logical_file(repo, str(ref.id))


def _rewrite_logical_file(
    repo: ConfigRepository,
    actor: Any,
    logical_file: Dict[str, Any],
    mutations: Dict[str, Any],
    *,
    action: str,
    reason: str,
    idempotency_key: Optional[str],
) -> Dict[str, Any]:
    payload = {
        "family_id": logical_file.get("family_id"),
        "role": logical_file.get("role"),
        "display_name": logical_file.get("display_name"),
        "aliases": [dict(item) for item in (logical_file.get("aliases") or [])],
        "notes": logical_file.get("notes") or "",
        "status": logical_file.get("status"),
    }
    payload.update(mutations)
    payload["updated_at"] = now_iso()
    tx = repo.transaction(
        actor,
        reason,
        action=action,
        expected={"logical_file:%s" % logical_file["id"]: logical_file.get("revision")},
        idempotency_key=idempotency_key,
    )
    tx.put("logical_file", payload, object_id=logical_file["id"])
    tx.commit()
    return get_logical_file(repo, logical_file["id"])


def rename_logical_file(
    repo: ConfigRepository,
    actor: Any,
    *,
    file_id: str,
    new_name: Any,
    reason: Any = "",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """改名：稳定 ID 不变，旧名闭合成历史别名，按旧名仍能查回同一对象。"""
    logical_file = get_logical_file(repo, file_id)
    name = _text(new_name, "文件名")
    if name == logical_file.get("display_name"):
        raise AppError(INVALID_INPUT, "新旧文件名相同", display_name=name)
    family_id = str(logical_file.get("family_id"))
    clash = _find_logical_file_by_name(repo, family_id, logical_file.get("role"), name)
    if clash is not None and clash["id"] != logical_file["id"]:
        raise AppError(
            DEFINITION_CONFLICT,
            "该配置类别下已有同角色同名的逻辑文件",
            display_name=name,
            logical_file_id=clash["id"],
        )
    _assert_no_representation_conflict(repo, family_id, logical_file.get("role"), name)

    stamp = now_iso()
    aliases: List[Dict[str, Any]] = []
    for alias in logical_file.get("aliases") or []:
        item = dict(alias)
        if item.get("until") in (None, ""):
            item["until"] = stamp
        aliases.append(item)
    aliases.append({"name": name, "since": stamp, "until": None})

    return _rewrite_logical_file(
        repo,
        actor,
        logical_file,
        {"display_name": name, "aliases": aliases},
        action="logical_file.rename",
        reason=_optional_text(reason) or "重命名逻辑文件 %s → %s" % (logical_file.get("display_name"), name),
        idempotency_key=idempotency_key,
    )


def retire_logical_file(
    repo: ConfigRepository,
    actor: Any,
    *,
    file_id: str,
    reason: Any = "",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """退役逻辑文件：只改状态，历史快照/别名/审计全部保留、仍可查。"""
    logical_file = get_logical_file(repo, file_id)
    if logical_file.get("status") == LIFECYCLE_RETIRED:
        return logical_file
    stamp = now_iso()
    return _rewrite_logical_file(
        repo,
        actor,
        logical_file,
        {"status": LIFECYCLE_RETIRED, "retired_at": stamp},
        action="logical_file.retire",
        reason=_optional_text(reason) or "退役逻辑文件 %s" % logical_file.get("display_name"),
        idempotency_key=idempotency_key,
    )


def resolve_logical_file(
    repo: ConfigRepository,
    family_id: str,
    role: str,
    filename: Any,
    *,
    include_retired: bool = True,
) -> Optional[Dict[str, Any]]:
    """把"机型 + 文件名 + 角色"精确解析到稳定 `LogicalFile`。

    先按当前名匹配，再按历史别名匹配；因此改名、退役之后按旧名仍能查回同一 ID
    （卡面"退役/改名后按旧关联可查历史"）。
    """
    target = _optional_text(filename)
    if not target:
        return None
    candidates = list_logical_files(
        repo, family_id, role=role, include_retired=include_retired
    )
    for item in candidates:
        if item.get("display_name") == target:
            return item
    for item in candidates:
        for alias in item.get("aliases") or []:
            if alias.get("name") == target:
                return item
    return None


# -- 文件快照（M6-02）-------------------------------------------------------


def _normalize_archive_ref(raw: Any) -> Tuple[Optional[Dict[str, str]], bool]:
    """归档引用三项（pid / filename / archive_name）齐全才算证据完整。"""
    if raw in (None, ""):
        return None, False
    if not isinstance(raw, dict):
        raise AppError(INVALID_INPUT, "archive_ref 必须是对象", field="archive_ref")
    ref = {
        "pid": _optional_text(raw.get("pid")),
        "filename": _optional_text(raw.get("filename")),
        "archive_name": _optional_text(raw.get("archive_name")),
    }
    complete = all(ref.values())
    return ref, complete


def _provenance(
    source: str, archive_ref: Optional[Dict[str, str]], complete: bool, actor: Any
) -> Dict[str, Any]:
    if complete:
        return {"resolution": "exact", "note": "归档引用三项齐全"}
    if source == SOURCE_UPLOAD:
        return {"resolution": "exact", "note": "由 %s 直接上传" % (_label(actor) or "未知操作者")}
    if source == SOURCE_MANUAL:
        return {"resolution": "exact", "note": "手工登记"}
    return {"resolution": "unconfirmed", "note": "归档引用不完整，待确认"}


def _find_snapshot_by_content(
    repo: ConfigRepository, release_id: str, logical_file_id: str, digest: str
) -> Optional[Dict[str, Any]]:
    """同内容重复采集不生成重复修订（保留首采时间，也是采集事件的证据）。"""
    for item in repo.list_objects("file_snapshot"):
        if str(item.get("release_id")) != str(release_id):
            continue
        if str(item.get("logical_file_id")) != str(logical_file_id):
            continue
        if item.get("blob_sha256") == digest:
            return item
    return None


def register_snapshot(
    repo: ConfigRepository,
    actor: Any,
    *,
    release_id: str,
    logical_file_id: str,
    data: bytes,
    original_filename: Any,
    source: str = SOURCE_ARCHIVE,
    archive_ref: Any = None,
    variant_id: Optional[str] = None,
    captured_at: Optional[str] = None,
    batch_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """把一段真实文件字节登记成不可变 `FileSnapshot`。

    * 版本必须由调用方**显式**指定（`release_id`），缺了就报错——不存在
      "按文件名推断版本"的分支；
    * 内容寻址去重：同版本同逻辑文件同哈希直接返回既有快照，不产生新修订；
    * 归档引用不完整时状态是 `unconfirmed`（待确认），不是默认可信。
    """
    release = get_release(repo, release_id)
    logical_file = get_logical_file(repo, logical_file_id)
    if source not in SOURCES:
        raise AppError(INVALID_INPUT, "来源非法", field="source", allowed=list(SOURCES))
    name = _text(original_filename, "原始文件名")
    if not isinstance(data, (bytes, bytearray)):
        raise AppError(INVALID_INPUT, "文件内容必须是字节串", field="data")
    payload_bytes = bytes(data)
    if not payload_bytes:
        raise AppError(INVALID_INPUT, "文件内容为空", field="data")

    digest = sha256_bytes(payload_bytes)
    duplicate = _find_snapshot_by_content(repo, release["id"], logical_file["id"], digest)
    if duplicate is not None:
        return duplicate

    ref, complete = _normalize_archive_ref(archive_ref)
    provenance = _provenance(source, ref, complete, actor)
    status = SNAPSHOT_CONFIRMED if provenance["resolution"] == "exact" else SNAPSHOT_UNCONFIRMED
    stamp = now_iso()

    tx = repo.transaction(
        actor,
        "登记文件快照 %s" % name,
        action="file_snapshot.register",
        idempotency_key=idempotency_key,
    )
    tx.blob(payload_bytes)
    created = tx.put(
        "file_snapshot",
        {
            "release_id": release["id"],
            "logical_file_id": logical_file["id"],
            "kind_detail": logical_file.get("role"),
            "variant_id": _optional_text(variant_id) or None,
            "blob_sha256": digest,
            "original_filename": name,
            "byte_size": len(payload_bytes),
            "captured_at": _optional_text(captured_at) or stamp,
            "source": source,
            "archive_ref": ref,
            "batch_id": _optional_text(batch_id) or None,
            "provenance": provenance,
            "status": status,
            "updated_at": stamp,
        },
    )
    tx.commit()
    return get_snapshot(repo, str(created.id))


def list_snapshots(
    repo: ConfigRepository,
    *,
    release_id: Optional[str] = None,
    logical_file_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    items = repo.list_objects("file_snapshot")
    if release_id is not None:
        items = [item for item in items if str(item.get("release_id")) == str(release_id)]
    if logical_file_id is not None:
        items = [
            item for item in items if str(item.get("logical_file_id")) == str(logical_file_id)
        ]
    if status is not None:
        items = [item for item in items if item.get("status") == status]
    return sorted(items, key=lambda item: (item.get("captured_at") or "", item.get("id") or ""))


def snapshot_bytes(repo: ConfigRepository, snapshot_id: str) -> bytes:
    """取快照原文（哈希不符会抛 `CorruptDataError`，由仓储保证）。"""
    snapshot = get_snapshot(repo, snapshot_id)
    return repo.read_blob(str(snapshot.get("blob_sha256")))


# -- 模板变体（M6-02）-------------------------------------------------------


def _find_variant(
    repo: ConfigRepository, family_id: str, release_id: str, signature: str
) -> Optional[Dict[str, Any]]:
    for item in repo.list_objects("template_variant"):
        if str(item.get("family_id")) != str(family_id):
            continue
        if str(item.get("release_id")) != str(release_id):
            continue
        if item.get("structure_signature") == signature:
            return item
    return None


def ensure_variant(
    repo: ConfigRepository,
    actor: Any,
    *,
    family_id: str,
    release_id: str,
    template_snapshot_id: str,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """按结构指纹登记/复用模板变体。

    * 指纹只由元素路径 + 同名序号 + 命名空间决定（`structure_signature`），
      改一个参数值不会产生新变体，加/删/移动节点才会；
    * 只有 XML 出口才能当模板：JSON 没有结构指纹，直接 `PARSE_FAILED`；
    * 同族同版本同指纹已存在则复用，不重复造对象。
    """
    family = get_family(repo, family_id)
    release = get_release(repo, release_id)
    snapshot = get_snapshot(repo, template_snapshot_id)
    if snapshot.get("kind_detail") != ROLE_XML_EXPORT:
        raise AppError(
            PARSE_FAILED,
            "只有 XML 出口才能作为模板变体",
            snapshot_id=snapshot["id"],
            kind_detail=snapshot.get("kind_detail"),
        )
    try:
        signature = config_mapping.structure_signature(snapshot_bytes(repo, snapshot["id"]))
    except AppError:
        raise
    except Exception as exc:  # XML 不合法
        raise AppError(
            PARSE_FAILED,
            "模板不是合法 XML，无法计算结构指纹",
            snapshot_id=snapshot["id"],
            cause=str(exc),
        )

    existing = _find_variant(repo, family["id"], release["id"], signature)
    if existing is not None:
        return existing

    stamp = now_iso()
    tx = repo.transaction(
        actor,
        "登记模板变体 %s" % (family.get("code") or family["id"]),
        action="template_variant.ensure",
        idempotency_key=idempotency_key,
    )
    ref = tx.put(
        "template_variant",
        {
            "family_id": family["id"],
            "release_id": release["id"],
            "structure_signature": signature,
            "template_snapshot_id": snapshot["id"],
            "status": LIFECYCLE_ACTIVE,
            "updated_at": stamp,
        },
    )
    tx.commit()
    return repo.get("template_variant", str(ref.id)) or {}


def list_variants(
    repo: ConfigRepository,
    *,
    family_id: Optional[str] = None,
    release_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    items = repo.list_objects("template_variant")
    if family_id is not None:
        items = [item for item in items if str(item.get("family_id")) == str(family_id)]
    if release_id is not None:
        items = [item for item in items if str(item.get("release_id")) == str(release_id)]
    return sorted(items, key=lambda item: (item.get("created_at") or "", item.get("id") or ""))


def catalog_summary(repo: ConfigRepository) -> Dict[str, Any]:
    """目录规模摘要（前端首屏用；只读、不建目录）。"""
    releases = list_releases(repo)
    families = list_families(repo)
    return {
        "releases": len(releases),
        "families": len(families),
        "logical_files": len(list_logical_files(repo)),
        "snapshots": len(list_snapshots(repo)),
        "variants": len(list_variants(repo)),
        "parameters": len(list_parameters(repo)),
        "definition_sets": len(list_definition_sets(repo)),
        "latest_release": releases[0] if releases else None,
    }


# -- 参数主档（M6-03）-------------------------------------------------------

#: 参数值类型。`enum` 必须同时给 `constraints.enum`。
VALUE_TYPES = ("decimal", "int", "bool", "text", "enum")

CONSTRAINT_KEYS = ("min", "max", "enum", "pattern", "max_length")


def _normalize_constraints(raw: Any, value_type: str) -> Dict[str, Any]:
    """约束一律补齐五个键，避免"有的参数有 min、有的没有"这种半结构化数据。"""
    payload = raw if isinstance(raw, dict) else {}
    unknown = set(payload) - set(CONSTRAINT_KEYS)
    if unknown:
        raise AppError(
            INVALID_INPUT,
            "未知的约束字段",
            field="constraints",
            unknown=sorted(unknown),
            allowed=list(CONSTRAINT_KEYS),
        )
    result: Dict[str, Any] = {
        "min": None,
        "max": None,
        "enum": [],
        "pattern": None,
        "max_length": None,
    }
    for key in ("min", "max"):
        if payload.get(key) in (None, ""):
            continue
        text = _optional_text(payload[key])
        if value_type not in ("decimal", "int"):
            raise AppError(
                INVALID_INPUT, "%s 只对数值类型有意义" % key, field="constraints", type=value_type
            )
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise AppError(
                INVALID_INPUT, "%s 必须是十进制数" % key, field="constraints", value=text
            )
        result[key] = _format_decimal(number)
    if result["min"] is not None and result["max"] is not None:
        if Decimal(result["min"]) > Decimal(result["max"]):
            raise AppError(
                INVALID_INPUT,
                "约束下界大于上界",
                field="constraints",
                min=result["min"],
                max=result["max"],
            )

    enum_values = payload.get("enum") or []
    if isinstance(enum_values, str) or not isinstance(enum_values, Iterable):
        raise AppError(INVALID_INPUT, "constraints.enum 必须是字符串数组", field="constraints")
    result["enum"] = [_optional_text(item) for item in enum_values if _optional_text(item)]
    if value_type == "enum" and not result["enum"]:
        raise AppError(INVALID_INPUT, "enum 类型必须给出可选值", field="constraints")

    if payload.get("pattern") not in (None, ""):
        pattern = _optional_text(payload["pattern"])
        try:
            re.compile(pattern)
        except re.error as exc:
            raise AppError(
                INVALID_INPUT, "正则不合法", field="constraints", cause=str(exc)
            )
        result["pattern"] = pattern
    if payload.get("max_length") not in (None, ""):
        try:
            length = int(payload["max_length"])
        except (TypeError, ValueError):
            raise AppError(INVALID_INPUT, "max_length 必须是整数", field="constraints")
        if length <= 0:
            raise AppError(INVALID_INPUT, "max_length 必须大于 0", field="constraints")
        result["max_length"] = length
    return result


def _format_decimal(number: "Decimal") -> str:
    """去掉尾零但不进科学计数法（与 `config_values` 的文本风格一致）。"""
    normalized = number.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def list_parameters(
    repo: ConfigRepository,
    family_id: Optional[str] = None,
    *,
    include_retired: bool = True,
) -> List[Dict[str, Any]]:
    items = repo.list_objects("parameter")
    if family_id is not None:
        items = [item for item in items if str(item.get("family_id")) == str(family_id)]
    if not include_retired:
        items = [item for item in items if item.get("status") != LIFECYCLE_RETIRED]
    return sorted(items, key=lambda item: (item.get("name") or "", item.get("id") or ""))


def get_parameter(repo: ConfigRepository, parameter_id: str) -> Dict[str, Any]:
    payload = repo.get("parameter", _optional_text(parameter_id))
    if payload is None:
        raise AppError(NOT_FOUND, "参数不存在", parameter_id=parameter_id)
    return payload


def _find_parameter_by_name(
    repo: ConfigRepository, family_id: str, name: str
) -> Optional[Dict[str, Any]]:
    for item in list_parameters(repo, family_id):
        if item.get("name") == name:
            return item
    return None


def create_parameter(
    repo: ConfigRepository,
    actor: Any,
    *,
    family_id: str,
    name: Any,
    category: Any = "",
    notes: Any = "",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """新建参数主档。`name` 是**用户名称**，不是 XML/JSON 里的键。"""
    family = get_family(repo, family_id)
    parameter_name = _text(name, "参数名称")
    existing = _find_parameter_by_name(repo, family["id"], parameter_name)
    if existing is not None and not _replayed(repo, idempotency_key):
        raise AppError(
            DEFINITION_CONFLICT,
            "该配置类别下已有同名参数",
            name=parameter_name,
            parameter_id=existing["id"],
        )
    stamp = now_iso()
    tx = repo.transaction(
        actor,
        "新建参数 %s" % parameter_name,
        action="parameter.create",
        idempotency_key=idempotency_key,
    )
    ref = tx.put(
        "parameter",
        {
            "family_id": family["id"],
            "name": parameter_name,
            "category": _optional_text(category),
            "notes": _optional_text(notes),
            "aliases": [],
            "status": LIFECYCLE_ACTIVE,
            "updated_at": stamp,
        },
    )
    tx.commit()
    return get_parameter(repo, str(ref.id))


def _parameter_expected(parameter: Dict[str, Any], expected_revision: Optional[str]) -> Dict[str, str]:
    """CAS 期望值：客户端给了就用它（真正的"读到哪版改哪版"），否则用刚读到的 revision。

    只传服务器刚读到的 revision 等价于"永远成立"，那是弱 CAS；因此 API 层会把
    表单里的 `expected_revision` 一路带下来，过期即 409 且草稿保留。
    """
    return {
        "parameter:%s" % parameter["id"]: _optional_text(expected_revision)
        or parameter.get("revision")
    }


def _rewrite_parameter(
    repo: ConfigRepository,
    actor: Any,
    parameter: Dict[str, Any],
    mutations: Dict[str, Any],
    *,
    action: str,
    reason: str,
    idempotency_key: Optional[str],
    expected_revision: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {
        "family_id": parameter.get("family_id"),
        "name": parameter.get("name"),
        "category": parameter.get("category") or "",
        "notes": parameter.get("notes") or "",
        "aliases": [dict(item) for item in (parameter.get("aliases") or [])],
        "status": parameter.get("status"),
    }
    payload.update(mutations)
    payload["updated_at"] = now_iso()
    tx = repo.transaction(
        actor,
        reason,
        action=action,
        expected=_parameter_expected(parameter, expected_revision),
        idempotency_key=idempotency_key,
    )
    tx.put("parameter", payload, object_id=parameter["id"])
    tx.commit()
    return get_parameter(repo, parameter["id"])


def rename_parameter(
    repo: ConfigRepository,
    actor: Any,
    *,
    parameter_id: str,
    new_name: Any,
    reason: Any = "",
    idempotency_key: Optional[str] = None,
    expected_revision: Optional[str] = None,
) -> Dict[str, Any]:
    """改名：稳定 ID 不变，旧名进入 `aliases`（带 `until`），旧名检索仍回同一 ID。"""
    parameter = get_parameter(repo, parameter_id)
    name = _text(new_name, "参数名称")
    if name == parameter.get("name"):
        raise AppError(INVALID_INPUT, "新旧名称相同", name=name)
    clash = _find_parameter_by_name(repo, str(parameter.get("family_id")), name)
    if clash is not None and clash["id"] != parameter["id"]:
        raise AppError(
            DEFINITION_CONFLICT,
            "该配置类别下已有同名参数",
            name=name,
            parameter_id=clash["id"],
        )
    stamp = now_iso()
    aliases = [dict(item) for item in (parameter.get("aliases") or [])]
    aliases.append({"name": parameter.get("name"), "until": stamp})
    return _rewrite_parameter(
        repo,
        actor,
        parameter,
        {"name": name, "aliases": aliases},
        action="parameter.rename",
        reason=_optional_text(reason) or "重命名参数 %s → %s" % (parameter.get("name"), name),
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
    )


def set_parameter_status(
    repo: ConfigRepository,
    actor: Any,
    *,
    parameter_id: str,
    status: str,
    reason: Any = "",
    idempotency_key: Optional[str] = None,
    expected_revision: Optional[str] = None,
) -> Dict[str, Any]:
    """退役 / 重新启用（`retired` / `active`）。历史修订与基线都不动。"""
    if status not in (LIFECYCLE_ACTIVE, LIFECYCLE_RETIRED):
        raise AppError(INVALID_INPUT, "参数状态非法", field="status")
    parameter = get_parameter(repo, parameter_id)
    if parameter.get("status") == status:
        return parameter
    return _rewrite_parameter(
        repo,
        actor,
        parameter,
        {"status": status},
        action="parameter.retire" if status == LIFECYCLE_RETIRED else "parameter.reactivate",
        reason=_optional_text(reason)
        or ("%s参数 %s" % ("退役" if status == LIFECYCLE_RETIRED else "启用", parameter.get("name"))),
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
    )


def search_parameters(
    repo: ConfigRepository,
    text: Any = "",
    *,
    family_id: Optional[str] = None,
    include_retired: bool = True,
) -> List[Dict[str, Any]]:
    """四种检索维度合一：用户名称 / 旧名（别名）/ XML 键 / JSON 键。

    命中任一维度就返回该参数，并带回 `matched` 说明命中的是哪一维，
    免得界面上"搜到了但看不出为什么"。
    """
    needle = _optional_text(text).lower()
    results: List[Dict[str, Any]] = []
    for parameter in list_parameters(repo, family_id, include_retired=include_retired):
        dimension = []
        if needle and needle in (parameter.get("name") or "").lower():
            dimension.append("name")
        for alias in parameter.get("aliases") or []:
            if needle and needle in (alias.get("name") or "").lower():
                dimension.append("alias")
                break
        definition = current_definition(repo, parameter["id"])
        if definition is not None:
            if needle and needle in (definition["xml"].get("name") or "").lower():
                dimension.append("xml")
            json_block = definition.get("json") or {}
            if needle and needle in (json_block.get("key") or "").lower():
                dimension.append("json")
        if needle and not dimension:
            continue
        item = dict(parameter)
        item["matched"] = dimension
        item["current_definition_id"] = definition["id"] if definition else None
        # 把命中用的键一并回传：前端做二次过滤时不必再猜 XML/JSON 名，
        # 也不用为了一行数据额外拉一次详情（避免 N+1）。
        keys = [parameter.get("name") or ""]
        keys.extend(alias.get("name") or "" for alias in parameter.get("aliases") or [])
        if definition is not None:
            keys.append(definition["xml"].get("name") or "")
            keys.append((definition.get("json") or {}).get("key") or "")
        item["search_keys"] = [key for key in keys if key]
        results.append(item)
    return results


# -- 定义修订（M6-03）-------------------------------------------------------

DEFINITION_ACTIVE = "active"
DEFINITION_RETIRED = "retired"


def list_definitions(
    repo: ConfigRepository, parameter_id: str, *, include_retired: bool = True
) -> List[Dict[str, Any]]:
    """某参数的全部定义修订，按 `revision_no` 从新到旧（历史不可覆盖，只能新增）。"""
    items = [
        item
        for item in repo.list_objects("definition_revision")
        if str(item.get("parameter_id")) == str(parameter_id)
    ]
    if not include_retired:
        items = [item for item in items if item.get("status") == DEFINITION_ACTIVE]
    return sorted(items, key=lambda item: (int(item.get("revision_no") or 0), item.get("id") or ""), reverse=True)


def get_definition(repo: ConfigRepository, definition_id: str) -> Dict[str, Any]:
    payload = repo.get("definition_revision", _optional_text(definition_id))
    if payload is None:
        raise AppError(NOT_FOUND, "定义修订不存在", definition_id=definition_id)
    return payload


def current_definition(
    repo: ConfigRepository, parameter_id: str
) -> Optional[Dict[str, Any]]:
    """当前生效的定义修订（`revision_no` 最大的 active 那条）。"""
    items = list_definitions(repo, parameter_id, include_retired=False)
    return items[0] if items else None


def _normalize_xml_block(xml_name: Any, xml_loc: Any) -> Dict[str, Any]:
    name = _text(xml_name, "XML 名称")
    loc = config_mapping.to_xml_loc(xml_loc)
    if loc is None:
        raise AppError(INVALID_INPUT, "缺少 XML 定位（loc）", field="loc")
    return {"name": name, "loc": loc.to_json()}


def _normalize_json_block(json_key: Any, json_pointer: Any) -> Optional[Dict[str, Any]]:
    key = _optional_text(json_key)
    pointer = _optional_text(json_pointer)
    if not key and not pointer:
        return None
    if not pointer:
        pointer = config_mapping.encode_pointer([key])
    parts = config_mapping.parse_pointer(pointer)
    if key and parts and parts[-1] != key:
        raise AppError(
            INVALID_INPUT,
            "JSON 键与指针末段不一致",
            field="json",
            key=key,
            pointer=pointer,
        )
    return {"key": key or (parts[-1] if parts else ""), "pointer": pointer}


def define_parameter(
    repo: ConfigRepository,
    actor: Any,
    *,
    parameter_id: str,
    xml_name: Any,
    xml_loc: Any,
    json_key: Any = None,
    json_pointer: Any = None,
    value_type: str = "decimal",
    unit: Any = "",
    constraints: Any = None,
    remarks: Any = "",
    idempotency_key: Optional[str] = None,
    expected_definition_id: Optional[str] = None,
) -> Dict[str, Any]:
    """追加一条定义修订（**永不原地覆盖**）。

    * `revision_no` 自增，`supersedes` 指向上一条，历史基线仍指向旧修订；
    * XML 定位必须可解析，JSON 指针必须合法且末段与键一致；
    * 类型/单位/约束在这里定型，发布前的"与 XML 元信息是否一致"由
      `verify_definition_against_template` 判定。
    """
    parameter = get_parameter(repo, parameter_id)
    if value_type not in VALUE_TYPES:
        raise AppError(INVALID_INPUT, "参数类型非法", field="type", allowed=list(VALUE_TYPES))
    xml_block = _normalize_xml_block(xml_name, xml_loc)
    json_block = _normalize_json_block(json_key, json_pointer)
    normalized = _normalize_constraints(constraints, value_type)
    unit_text = _optional_text(unit)
    if unit_text and value_type not in ("decimal", "int"):
        raise AppError(INVALID_INPUT, "只有数值类型才有单位", field="unit")

    # `revision_no` 取**全部历史**的最大值（退役的也算），否则会出现两条 r1；
    # `supersedes` 只指向当前仍生效的那条——退役之后没有可承接的前驱。
    history = list_definitions(repo, parameter["id"])
    revision_no = max((int(item.get("revision_no") or 0) for item in history), default=0) + 1
    previous = current_definition(repo, parameter["id"])
    # 追加定义时，表单的 CAS 目标是**当前定义修订**（`expected_definition_id`）：
    # 每条定义修订都是独立对象、版本号各自从 r1 起，拿 r1 比对没有意义。
    # `expected_definition_id=""` 表示"我看到的时候还没有定义"，非空表示
    # "我看到的是这一条"；两个并发请求都拿着同一条来追加，第二个必须 409。
    if expected_definition_id is not None:
        actual_id = previous["id"] if previous else None
        wanted = _optional_text(expected_definition_id) or None
        if wanted != actual_id:
            raise AppError(
                REVISION_CONFLICT,
                "定义已被修改，请刷新后重试",
                expected_definition_id=wanted,
                actual_definition_id=actual_id,
                parameter_id=parameter["id"],
            )
    stamp = now_iso()
    tx = repo.transaction(
        actor,
        "追加定义修订 %s r%d" % (parameter.get("name"), revision_no),
        action="definition.create",
        expected=_parameter_expected(parameter, None),
        idempotency_key=idempotency_key,
    )
    ref = tx.put(
        "definition_revision",
        {
            "parameter_id": parameter["id"],
            "revision_no": revision_no,
            "xml": xml_block,
            "json": json_block,
            "type": value_type,
            "unit": unit_text,
            "constraints": normalized,
            "remarks": _optional_text(remarks),
            "definition_set_id": None,
            "created_by": _label(actor),
            "supersedes": previous["id"] if previous else None,
            "status": DEFINITION_ACTIVE,
            "updated_at": stamp,
        },
    )
    tx.commit()
    return get_definition(repo, str(ref.id))


def retire_definition(
    repo: ConfigRepository,
    actor: Any,
    *,
    definition_id: str,
    reason: Any = "",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """退役一条定义修订（历史集合里它仍在，只是不再作为"当前定义"）。"""
    definition = get_definition(repo, definition_id)
    if definition.get("status") == DEFINITION_RETIRED:
        return definition
    payload = {
        "parameter_id": definition.get("parameter_id"),
        "revision_no": definition.get("revision_no"),
        "xml": definition.get("xml"),
        "json": definition.get("json"),
        "type": definition.get("type"),
        "unit": definition.get("unit"),
        "constraints": definition.get("constraints"),
        "remarks": definition.get("remarks") or "",
        "definition_set_id": definition.get("definition_set_id"),
        "created_by": definition.get("created_by"),
        "supersedes": definition.get("supersedes"),
        "status": DEFINITION_RETIRED,
        "updated_at": now_iso(),
    }
    tx = repo.transaction(
        actor,
        _optional_text(reason) or "退役定义修订 %s" % definition["id"],
        action="definition.retire",
        expected={"definition_revision:%s" % definition["id"]: definition.get("revision")},
        idempotency_key=idempotency_key,
    )
    tx.put("definition_revision", payload, object_id=definition["id"])
    tx.commit()
    return get_definition(repo, definition["id"])


def definition_entries(repo: ConfigRepository, definition_ids: Iterable[str]) -> List[Any]:
    """把定义修订转成 `MappingEntry`（供 `MappingTable` 做冲突检查）。"""
    entries = []
    for definition_id in definition_ids:
        definition = get_definition(repo, definition_id)
        xml_block = definition.get("xml") or {}
        json_block = definition.get("json") or {}
        entries.append(
            config_mapping.MappingEntry(
                str(definition.get("parameter_id")),
                xml=config_mapping.to_xml_loc(xml_block.get("loc")),
                json_pointer=(json_block or {}).get("pointer"),
                json_key=(json_block or {}).get("key"),
                unit=definition.get("unit") or None,
                value_type=definition.get("type"),
            )
        )
    return entries


def check_definition_locations(
    repo: ConfigRepository, definition_ids: Iterable[str]
) -> None:
    """一批定义里同一个 XML 节点/JSON 指针只能被一个参数占用，否则 `MAPPING_AMBIGUOUS`。"""
    table = config_mapping.MappingTable(definition_entries(repo, definition_ids))
    table.assert_unique()


def verify_definition_against_template(
    repo: ConfigRepository, definition: Dict[str, Any], snapshot_id: str
) -> Dict[str, Any]:
    """把定义与真实模板对一遍：节点是否存在、类型是否匹配、单位是否可无损换算。

    发布前的闸门之一（M6-12 会再调一次）。这里只做判定，不写盘。
    """
    snapshot = get_snapshot(repo, snapshot_id)
    data = snapshot_bytes(repo, snapshot["id"])
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise AppError(
            PARSE_FAILED, "模板不是合法 XML", snapshot_id=snapshot["id"], cause=str(exc)
        )
    namespace = config_mapping.default_namespace(root)
    loc = config_mapping.to_xml_loc((definition.get("xml") or {}).get("loc"))
    node = config_mapping.resolve_xml(root, loc, namespace) if loc else None
    if node is None:
        raise AppError(
            DEFINITION_CONFLICT,
            "模板里找不到定义声明的定位",
            definition_id=definition.get("id"),
            loc=str(loc),
            snapshot_id=snapshot["id"],
        )
    raw = node.attrib.get(loc.attribute, "") if loc.attribute else (node.text or "")
    template_unit = node.attrib.get("unit")
    declared_unit = definition.get("unit") or None
    converted = None
    if template_unit and declared_unit and template_unit != declared_unit:
        try:
            converted = config_values.convert(
                config_values.TypedValue("decimal", raw.strip(), template_unit), declared_unit
            ).text
        except AppError as exc:
            if exc.code == "VALUE_LOSSY_CONVERSION":
                raise AppError(
                    exc.code,
                    "单位换算不可逆，阻断发布",
                    definition_id=definition.get("id"),
                    template_unit=template_unit,
                    declared_unit=declared_unit,
                    value=raw,
                )
            raise
    return {
        "snapshot_id": snapshot["id"],
        "loc": str(loc),
        "raw": raw,
        "template_unit": template_unit,
        "declared_unit": declared_unit,
        "converted": converted,
        "value_type": definition.get("type"),
    }


# -- 定义集合（M6-03）-------------------------------------------------------

def list_definition_sets(
    repo: ConfigRepository,
    *,
    release_id: Optional[str] = None,
    variant_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    items = repo.list_objects("definition_set")
    if release_id is not None:
        items = [item for item in items if str(item.get("release_id")) == str(release_id)]
    if variant_id is not None:
        items = [item for item in items if str(item.get("variant_id") or "") == str(variant_id)]
    return sorted(items, key=lambda item: (item.get("created_at") or "", item.get("id") or ""))


def get_definition_set(repo: ConfigRepository, set_id: str) -> Dict[str, Any]:
    payload = repo.get("definition_set", _optional_text(set_id))
    if payload is None:
        raise AppError(NOT_FOUND, "定义集合不存在", definition_set_id=set_id)
    return payload


def latest_definition_set(
    repo: ConfigRepository, release_id: str, variant_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """该版本（该变体）当前的定义集合。集合一旦创建就不可变：
    "冻结"由**新集合 = 新对象**实现，已发布基线始终引用它当初那个 ID。"""
    items = list_definition_sets(repo, release_id=release_id, variant_id=variant_id)
    return items[-1] if items else None


def create_definition_set(
    repo: ConfigRepository,
    actor: Any,
    *,
    release_id: str,
    definition_ids: Any,
    variant_id: Optional[str] = None,
    note: Any = "",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """为「版本 + 变体」冻结一份定义集合。

    * 每个参数只能出现一次（两条定义指同一参数即 `DEFINITION_CONFLICT`）；
    * 同一 XML 节点/JSON 指针只能被一个参数占用（`MAPPING_AMBIGUOUS`）；
    * 只接受 `active` 的定义修订，退役修订不能进新集合。
    """
    release = get_release(repo, release_id)
    variant = _optional_text(variant_id) or None
    if variant is not None:
        if repo.get("template_variant", variant) is None:
            raise AppError(NOT_FOUND, "模板变体不存在", variant_id=variant)
    ids = _id_list(definition_ids, "definition_ids")
    if not ids:
        raise AppError(INVALID_INPUT, "定义集合不能为空", field="definition_ids")

    seen: Dict[str, str] = {}
    for definition_id in ids:
        definition = get_definition(repo, definition_id)
        if definition.get("status") != DEFINITION_ACTIVE:
            raise AppError(
                DEFINITION_CONFLICT,
                "退役的定义修订不能进入新集合",
                definition_id=definition_id,
            )
        parameter_id = str(definition.get("parameter_id"))
        if parameter_id in seen:
            raise AppError(
                DEFINITION_CONFLICT,
                "同一参数在同一集合里出现了两条定义",
                parameter_id=parameter_id,
                definition_ids=[seen[parameter_id], definition_id],
            )
        seen[parameter_id] = definition_id
    check_definition_locations(repo, ids)

    stamp = now_iso()
    tx = repo.transaction(
        actor,
        _optional_text(note) or "冻结定义集合 %s" % release.get("version_label"),
        action="definition_set.create",
        idempotency_key=idempotency_key,
    )
    ref = tx.put(
        "definition_set",
        {
            "release_id": release["id"],
            "variant_id": variant,
            "definition_ids": ids,
            "created_by": _label(actor),
            "note": _optional_text(note),
            "updated_at": stamp,
        },
    )
    tx.commit()
    return get_definition_set(repo, str(ref.id))


def definition_set_detail(repo: ConfigRepository, set_id: str) -> Dict[str, Any]:
    """集合 + 展开后的定义（前端一次拿全，避免 N+1 请求）。"""
    definition_set = get_definition_set(repo, set_id)
    definitions = [get_definition(repo, item) for item in definition_set.get("definition_ids") or []]
    return {"definition_set": definition_set, "definitions": definitions}
