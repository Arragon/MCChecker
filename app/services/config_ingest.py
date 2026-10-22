"""现场采集与不可变 Observation（M6-07 / M6-08）。

两条主线：

* **M6-07 采集**：把已有的手动/定时下载结果接进配置域。原始字节进内容寻址 blob，
  `Observation` 与 `CaptureEvent` 在**同一次事务**里公布——采集这件事与"它被投影成
  哪个观测"要么一起成立，要么都不成立，不存在"有事件没观测"的中间态。
* **M6-08 严格解析**：按 `LogicalFile.role` 选定位（XML 出口用 `xml.loc`，现场 JSON
  用 `json.pointer`），逐参数给出四类可观测状态，外加文件里没被任何参数认领的键。

几条不可退让的规则：

1. 原始字节**永不改写**；重新映射是给同一个 `Observation` 追加 `r<n>` 修订，
   `r1` 始终是最初那次采集的结果（`repo.get("observation", id, "r1")`）。
2. 同内容重复下载**不产生第二条观测**，但仍写一条采集事件——"这个时间点它还是
   这个样子"本身就是要留下的证据。
3. 旧任务晚到**不覆盖**新内容：`captured_at` 更早的采集照常入库（历史不丢），
   但不移动"当前观测"指针。
4. `missing` 不等于删除：节点不存在只是"这次没看到值"，不是"该删掉这个参数"。
5. 解析失败也入库。失败的观测不可能成为基线，但"现场给了 200 错误页"这件事本身
   必须可追溯。
"""

from __future__ import annotations

import threading
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..core import config_mapping, config_values
from ..core.config_repository import ConfigRepository, now_iso, sha256_bytes
from ..core.errors import (
    BUSY,
    INVALID_INPUT,
    NOT_FOUND,
    OBSERVATION_UNKNOWN_RELEASE,
    PARSE_FAILED,
    AppError,
)
from . import config_catalog, config_gate, config_policies

# -- 采集种类 ---------------------------------------------------------------

CAPTURE_SCHEDULED = "scheduled"
CAPTURE_MANUAL = "manual"
CAPTURE_MANUAL_IMPORT = "manual_import"
CAPTURE_KINDS = (CAPTURE_SCHEDULED, CAPTURE_MANUAL, CAPTURE_MANUAL_IMPORT)

# -- 观测状态 ---------------------------------------------------------------

STATUS_PARSED = "parsed"
STATUS_PARSE_FAILED = "parse_failed"

# -- 版本归属三态 -----------------------------------------------------------

RESOLUTION_CONFIRMED = "confirmed"
RESOLUTION_UNCONFIRMED = "unconfirmed"
RESOLUTION_UNKNOWN = "unknown"
RESOLUTIONS = (RESOLUTION_CONFIRMED, RESOLUTION_UNCONFIRMED, RESOLUTION_UNKNOWN)

# -- 完整度 -----------------------------------------------------------------

COMPLETENESS_FULL = "full"
COMPLETENESS_PARTIAL = "partial"

# -- 逐项映射状态 -----------------------------------------------------------

ENTRY_MAPPED = "mapped"
ENTRY_UNMAPPED = "unmapped"
ENTRY_AMBIGUOUS = "ambiguous"
ENTRY_MISSING = "missing"
ENTRY_INVALID = "invalid"
ENTRY_STATUSES = (
    ENTRY_MAPPED,
    ENTRY_UNMAPPED,
    ENTRY_AMBIGUOUS,
    ENTRY_MISSING,
    ENTRY_INVALID,
)

#: 让完整度降级的状态：只要有一条不是 `mapped`，这次观测就是 `partial`。
_DEGRADING = frozenset((ENTRY_UNMAPPED, ENTRY_AMBIGUOUS, ENTRY_MISSING, ENTRY_INVALID))

#: 参数登记的值类型（`config_catalog.VALUE_TYPES`）与严格值模型（`config_values.KINDS`）
#: 之间的唯一差异：登记侧叫 `text`，值模型侧叫 `string`。翻译放在这一层，
#: 两边各自的词汇表都不用为了对方改名。
_TYPE_ALIASES = {"text": "string"}

#: 枚举键时最多收集多少个键，防止一个超大的现场文件把观测撑爆。
MAX_COLLECTED_KEYS = 1000


# -- 小工具 -----------------------------------------------------------------


def _text(value: Any, field: str) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise AppError(INVALID_INPUT, "%s 不能为空" % field, field=field)
    return text


def _optional_text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _label(actor: Any) -> str:
    if actor is None:
        return ""
    if isinstance(actor, dict):
        return str(actor.get("name") or actor.get("ip") or "")
    return str(getattr(actor, "name", "") or getattr(actor, "ip", "") or "")


def _qualify(name: str, namespace: Optional[str]) -> str:
    if name.startswith("{") or not namespace:
        return name
    return "{%s}%s" % (namespace, name)


def _format_of(filename: str, content: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".json"):
        return "json"
    if name.endswith(".xml"):
        return "xml"
    head = content.lstrip()[:1]
    if head in (b"{", b"["):
        return "json"
    if head == b"<":
        return "xml"
    raise AppError(
        PARSE_FAILED,
        "无法判断文件格式（既不是 JSON 也不是 XML）",
        filename=filename,
    )


# -- 解析 -------------------------------------------------------------------


def parse_document(content: bytes, filename: str) -> Dict[str, Any]:
    """严格解析原始字节。失败抛 `PARSE_FAILED`，调用方据此落一条失败观测。"""
    if not isinstance(content, (bytes, bytearray)):
        raise AppError(INVALID_INPUT, "采集内容必须是字节串")
    raw = bytes(content)
    if not raw.strip():
        raise AppError(PARSE_FAILED, "采集内容是空的", filename=filename)
    fmt = _format_of(filename, raw)
    if fmt == "json":
        try:
            document = config_values.loads_strict(raw.decode("utf-8-sig"))
        except AppError as exc:
            # 重复键 / NaN / Infinity 都会走到这里：不静默取后者，直接判失败。
            raise AppError(
                PARSE_FAILED,
                "JSON 内容不合法：%s" % exc.message,
                filename=filename,
                detail=exc.detail,
            )
        except (UnicodeDecodeError, ValueError) as exc:
            raise AppError(
                PARSE_FAILED, "JSON 内容不合法：%s" % exc, filename=filename
            )
        return {
            "format": "json",
            "document": document,
            "root": None,
            "signature": None,
        }
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise AppError(PARSE_FAILED, "XML 内容不合法：%s" % exc, filename=filename)
    return {
        "format": "xml",
        "document": None,
        "root": root,
        "signature": config_mapping.structure_signature(raw),
    }


# -- 定位 -------------------------------------------------------------------


def _locate_xml(
    root: ET.Element, loc: config_mapping.XmlLoc
) -> Tuple[Optional[ET.Element], Optional[str]]:
    """返回 `(节点, 缺席原因)`；节点存在时原因为 None。

    缺席原因区分三种情况，便于现场判断是"模板变了"还是"这次没采到"：

    * `parent_missing` —— 路径中间某级就不存在（模板不匹配）；
    * `node_missing` —— 末级元素不存在；
    * `occurrence_missing` —— **同名序号越界**（数组错位）；
    * `attribute_missing` —— 元素在、属性没有。
    """
    namespace = config_mapping.default_namespace(root)
    node = root
    for depth, (name, occurrence) in enumerate(loc.steps):
        wanted = _qualify(name, namespace)
        matches = [child for child in list(node) if child.tag == wanted]
        if not matches:
            return None, ("parent_missing" if depth else "node_missing")
        if len(matches) < occurrence:
            return None, "occurrence_missing"
        node = matches[occurrence - 1]
    if loc.attribute and loc.attribute not in node.attrib:
        return None, "attribute_missing"
    return node, None


def _json_reach(document: Any, pointer: str) -> Tuple[Any, bool, Optional[str]]:
    """按指针取值，返回 `(值, 是否存在, 缺席原因)`。

    与 `config_mapping.resolve_pointer` 的区别：这里要**区分 `null` 与不存在**，
    并且索引越界不能被当成"取到了隔壁的元素"（数组错位）。
    """
    node = document
    parts = config_mapping.parse_pointer(pointer)
    for part in parts:
        if isinstance(node, dict):
            if part not in node:
                return None, False, "key_missing"
            node = node[part]
        elif isinstance(node, list):
            try:
                index = int(part)
            except (TypeError, ValueError):
                return None, False, "not_an_index"
            if index < 0 or index >= len(node):
                return None, False, "index_out_of_range"
            node = node[index]
        else:
            return None, False, "path_blocked"
    return node, True, None


def _collect_json_keys(document: Any, limit: int = MAX_COLLECTED_KEYS) -> Tuple[List[str], bool]:
    """枚举文件里出现的所有叶子指针，返回 `(指针列表, 是否被截断)`。"""
    found: List[str] = []
    state = {"truncated": False}

    def walk(parts: List[str], node: Any) -> None:
        if len(found) >= limit:
            state["truncated"] = True
            return
        if isinstance(node, dict):
            for key, item in node.items():
                walk(parts + [str(key)], item)
            return
        if isinstance(node, list):
            for index, item in enumerate(node):
                walk(parts + [str(index)], item)
            return
        found.append(config_mapping.encode_pointer(parts))

    walk([], document)
    return found, state["truncated"]


def _collect_xml_keys(
    root: ET.Element, limit: int = MAX_COLLECTED_KEYS
) -> Tuple[List[str], bool]:
    """枚举 XML 里所有"可定位的叶子"：无子元素的元素 + 它们自带的属性。"""
    found: List[str] = []
    truncated = False

    def walk(node: ET.Element, path: List[Tuple[str, int]], seen: Dict[str, int]) -> None:
        nonlocal truncated
        for child in list(node):
            if len(found) >= limit:
                truncated = True
                return
            tag = child.tag or ""
            if tag.startswith("{"):
                tag = tag.split("}", 1)[1]
            seen[tag] = seen.get(tag, 0) + 1
            steps = path + [(tag, seen[tag])]
            loc = config_mapping.XmlLoc(steps)
            if len(child):
                walk(child, steps, {})
                continue
            if (child.text or "").strip():
                found.append(loc.canonical())
            elif not child.attrib:
                found.append(loc.canonical())
            for attribute in child.attrib:
                found.append(config_mapping.XmlLoc(steps, attribute).canonical())

    walk(root, [], {})
    return found, truncated


# -- 定义与映射 -------------------------------------------------------------


def _family_parameters(
    repo: ConfigRepository, family_id: str
) -> Dict[str, Dict[str, Any]]:
    return {
        item["id"]: item
        for item in config_catalog.list_parameters(repo, family_id, include_retired=False)
    }


def _definitions_for(
    repo: ConfigRepository,
    family_id: str,
    release_id: Optional[str],
    variant_id: Optional[str],
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """优先用该版本的**冻结定义集合**；没有就退到当前生效定义。

    退回是必要的：现场文件往往比"冻结集合"先到（新装机、新版本刚上线），必须
    先能解析再说。观测里会注明 `definition_set_id=None`，后续对账时一眼能看出
    这次映射用的是"当时的当前定义"而不是某个冻结版本。
    """
    parameters = _family_parameters(repo, family_id)
    definition_set = None
    if release_id:
        definition_set = config_catalog.latest_definition_set(
            repo, release_id, variant_id
        )
    ids: List[str] = []
    if definition_set is not None:
        ids = [
            definition_id
            for definition_id in definition_set.get("definition_ids") or []
            if config_catalog.get_definition(repo, definition_id).get("parameter_id")
            in parameters
        ]
    else:
        for parameter in parameters.values():
            current = config_catalog.current_definition(repo, parameter["id"])
            if current is not None:
                ids.append(current["id"])
    return definition_set, [config_catalog.get_definition(repo, i) for i in ids]


def _definition_target(definition: Dict[str, Any], role: Optional[str]) -> Tuple[str, Any]:
    """按逻辑文件的角色挑定位：现场 JSON 用 json 指针，其余用 xml 路径。"""
    if role == config_catalog.ROLE_FIELD_JSON:
        block = definition.get("json") or {}
        return "json", (block or {}).get("pointer")
    block = definition.get("xml") or {}
    return "xml", (block or {}).get("loc")


def _typed(value: Any, definition: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """把一个原始值转成 `TypedValue` 的传输形态；失败返回 `(None, 原因)`。"""
    kind = str(definition.get("type") or "decimal")
    try:
        parsed = config_values.parse_typed(
            value,
            _TYPE_ALIASES.get(kind, kind),
            definition.get("unit") or None,
        )
    except AppError as exc:
        return None, exc.message
    return parsed.to_json(), None


def build_mapping(
    repo: ConfigRepository,
    logical_file: Dict[str, Any],
    definitions: Iterable[Dict[str, Any]],
    parsed: Dict[str, Any],
) -> Dict[str, Any]:
    """逐参数产出映射结论 + 文件里没人认领的键。

    同一目标被多个参数占用时不抛错也不任取其一：**全部标成 `ambiguous`**，
    让现场看到"这两个参数抢同一个节点"，而不是悄悄选中一个继续往下走。
    """
    role = logical_file.get("role")
    entries: List[Dict[str, Any]] = []
    claimed: Dict[str, List[str]] = {}

    for definition in definitions:
        kind, target = _definition_target(definition, role)
        if not target:
            continue
        parameter_id = str(definition.get("parameter_id") or "")
        if kind == "json":
            raw_key = str(target)
            claimed.setdefault("json:" + raw_key, []).append(parameter_id)
            value, present, reason = _json_reach(parsed["document"], raw_key)
            if not present:
                entries.append(
                    {
                        "parameter_id": parameter_id,
                        "raw_key": raw_key,
                        "status": ENTRY_MISSING,
                        "value_ref": None,
                        "reason": reason,
                    }
                )
                continue
            reference, error = _typed(value, definition)
            entries.append(
                {
                    "parameter_id": parameter_id,
                    "raw_key": raw_key,
                    "status": ENTRY_INVALID if error else ENTRY_MAPPED,
                    "value_ref": reference,
                    "reason": error,
                }
            )
            continue

        loc = config_mapping.to_xml_loc(target)
        raw_key = loc.canonical()
        claimed.setdefault("xml:" + raw_key, []).append(parameter_id)
        node, reason = _locate_xml(parsed["root"], loc)
        if node is None:
            entries.append(
                {
                    "parameter_id": parameter_id,
                    "raw_key": raw_key,
                    "status": ENTRY_MISSING,
                    "value_ref": None,
                    "reason": reason,
                }
            )
            continue
        raw_text = (node.get(loc.attribute) if loc.attribute else node.text) or ""
        if not raw_text.strip():
            entries.append(
                {
                    "parameter_id": parameter_id,
                    "raw_key": raw_key,
                    "status": ENTRY_MISSING,
                    "value_ref": None,
                    "reason": "empty_value",
                }
            )
            continue
        reference, error = _typed(raw_text.strip(), definition)
        entries.append(
            {
                "parameter_id": parameter_id,
                "raw_key": raw_key,
                "status": ENTRY_INVALID if error else ENTRY_MAPPED,
                "value_ref": reference,
                "reason": error,
            }
        )

    # 同一目标被多个参数占用 → 全部标 ambiguous（不静默取一个）
    for target, owners in claimed.items():
        if len(owners) < 2:
            continue
        for entry in entries:
            if (
                entry["parameter_id"] in owners
                and ("%s:%s" % ("json" if target.startswith("json:") else "xml", entry["raw_key"]))
                == target
            ):
                entry["status"] = ENTRY_AMBIGUOUS
                entry["value_ref"] = None
                entry["reason"] = "同一目标被多个参数占用"

    # 文件里有、但没有任何参数认领的键
    if parsed["format"] == "json":
        present_keys, truncated = _collect_json_keys(parsed["document"])
        prefix = "json:"
    else:
        present_keys, truncated = _collect_xml_keys(parsed["root"])
        prefix = "xml:"
    unresolved = [key for key in present_keys if prefix + key not in claimed]
    for key in unresolved:
        entries.append(
            {
                "parameter_id": None,
                "raw_key": key,
                "status": ENTRY_UNMAPPED,
                "value_ref": None,
                "reason": "文件中存在但未登记为参数",
            }
        )

    mapping = {
        "entries": entries,
        "unresolved_keys": unresolved,
        "truncated": truncated,
        "definition_set_id": None,
    }
    return mapping


def completeness_of(mapping: Dict[str, Any]) -> str:
    """有任何一条不是 `mapped` 就是 `partial`；空映射也是 `partial`。"""
    entries = mapping.get("entries") or []
    if not entries:
        return COMPLETENESS_PARTIAL
    for entry in entries:
        if entry.get("status") in _DEGRADING:
            return COMPLETENESS_PARTIAL
    return COMPLETENESS_FULL


# -- 版本归属 ---------------------------------------------------------------


def resolve_release_ref(
    repo: ConfigRepository,
    logical_file_id: str,
    digest: str,
    release_id: Optional[str],
) -> Dict[str, Any]:
    """`confirmed` / `unconfirmed` / `unknown` 三态的判定。

    * 显式给了版本，且内容与该版本登记的出口快照**逐字节一致** → `confirmed`；
    * 显式给了版本，但内容与任何快照都不一样 → `unconfirmed`（"说是这个版本，
      但没有证据"）；
    * 没给版本，但内容能对上某个已登记快照 → `confirmed`（按内容反查得到）；
    * 都对不上 → `unknown`。
    """
    snapshots = [
        item
        for item in repo.list_objects("file_snapshot")
        if str(item.get("logical_file_id")) == str(logical_file_id)
    ]
    if release_id:
        release = config_catalog.get_release(repo, release_id)
        matched = any(
            item.get("blob_sha256") == digest
            and str(item.get("release_id")) == str(release["id"])
            for item in snapshots
        )
        return {
            "release_id": release["id"],
            "resolution": RESOLUTION_CONFIRMED if matched else RESOLUTION_UNCONFIRMED,
        }
    for item in snapshots:
        if item.get("blob_sha256") == digest:
            return {
                "release_id": item.get("release_id"),
                "resolution": RESOLUTION_CONFIRMED,
            }
    return {"release_id": None, "resolution": RESOLUTION_UNKNOWN}


# -- 任务互斥（入队防重复）---------------------------------------------------

_INFLIGHT: Dict[str, int] = {}
_INFLIGHT_LOCK = threading.Lock()


def capture_slot(profile_id: str, source_name: str) -> str:
    key = "%s|%s" % (_optional_text(profile_id), _optional_text(source_name))
    return key


@contextmanager
def capture_guard(profile_id: str, source_name: str):
    """同一个 `机型 + 来源` 同时只允许一个采集在跑。

    定时任务与手动刷新可能撞在一起（两个调度都认为该拉一次），第二个直接拿
    `BUSY` 退出，而不是两条线程同时往同一个逻辑文件上写。
    """
    key = capture_slot(profile_id, source_name)
    with _INFLIGHT_LOCK:
        if _INFLIGHT.get(key):
            raise AppError(
                BUSY,
                "该来源正在采集中，请稍后重试",
                profile_id=profile_id,
                source_name=source_name,
            )
        _INFLIGHT[key] = 1
    try:
        yield key
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(key, None)


def reset_inflight() -> None:
    """测试用：清空互斥表。"""
    with _INFLIGHT_LOCK:
        _INFLIGHT.clear()


# -- 读取 -------------------------------------------------------------------


def get_observation(
    repo: ConfigRepository, observation_id: str, revision: Optional[str] = None
) -> Dict[str, Any]:
    payload = repo.get("observation", _optional_text(observation_id), revision)
    if payload is None:
        raise AppError(NOT_FOUND, "观测不存在", observation_id=observation_id)
    return payload


def list_observations(
    repo: ConfigRepository,
    *,
    profile_id: Optional[str] = None,
    logical_file_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    items = repo.list_objects("observation")
    if profile_id is not None:
        items = [i for i in items if str(i.get("profile_id")) == str(profile_id)]
    if logical_file_id is not None:
        items = [
            i for i in items if str(i.get("logical_file_id")) == str(logical_file_id)
        ]
    if status is not None:
        items = [i for i in items if i.get("status") == status]
    items.sort(key=lambda i: (str(i.get("captured_at") or ""), str(i.get("id") or "")))
    if limit is not None:
        items = items[-int(limit):]
    return items


def current_observation(
    repo: ConfigRepository, profile_id: str, logical_file_id: str
) -> Optional[Dict[str, Any]]:
    object_id = repo.pointer(
        "observation_current", "%s:%s" % (profile_id, logical_file_id)
    )
    if not object_id:
        return None
    return repo.get("observation", str(object_id))


def observation_bytes(repo: ConfigRepository, observation_id: str) -> bytes:
    payload = get_observation(repo, observation_id)
    return repo.read_blob(str(payload.get("blob_sha256")))


def _replay_capture(
    repo: ConfigRepository,
    idempotency_key: Optional[str],
    *,
    profile_id: str,
    logical_file_id: str,
    digest: str,
) -> Optional[Dict[str, Any]]:
    """同一个 `Idempotency-Key` 的重复请求：直接回第一次的结果。

    必须在"去重判定"**之前**做。否则第二次请求会走"发现已有同内容观测"的分支，
    事务形状与第一次不同（这次没有观测对象），事务层会把它算成另一个请求、
    报 `IDEMPOTENCY_MISMATCH`——语义上明明是同一个请求重发。

    同时校验内容指纹：同键换内容仍是错用，抛 `IDEMPOTENCY_MISMATCH`。
    """
    if not idempotency_key:
        return None
    receipt = repo.receipt(str(idempotency_key))
    if receipt is None:
        return None
    result = receipt.get("result") or []
    observation = next((item for item in result if item.get("kind") == "observation"), None)
    if observation is None:
        return None
    payload = dict(get_observation(repo, str(observation["id"]), observation.get("revision")))
    if (
        str(payload.get("profile_id")) != profile_id
        or str(payload.get("logical_file_id")) != logical_file_id
        or payload.get("blob_sha256") != digest
    ):
        raise AppError(
            "IDEMPOTENCY_MISMATCH",
            "同一 Idempotency-Key 被用于不同的采集内容",
            idempotency_key=str(idempotency_key),
        )
    event = next((item for item in result if item.get("kind") == "capture_event"), None)
    payload.update(
        {
            "deduplicated": False,
            "stale_capture": bool(payload.get("stale_capture")),
            "event_id": str(event["id"]) if event else None,
            "replayed": True,
        }
    )
    return payload


def _find_duplicate(
    repo: ConfigRepository,
    *,
    profile_id: str,
    logical_file_id: str,
    digest: str,
    release_id: Optional[str],
    status: str,
) -> Optional[Dict[str, Any]]:
    """同机型 + 同逻辑文件 + 同字节 + 同版本归属 + 同解析结论 → 视为同一条观测。"""
    for item in repo.list_objects("observation"):
        if str(item.get("profile_id")) != str(profile_id):
            continue
        if str(item.get("logical_file_id")) != str(logical_file_id):
            continue
        if item.get("blob_sha256") != digest:
            continue
        if (item.get("release_ref") or {}).get("release_id") != release_id:
            continue
        if item.get("status") != status:
            continue
        return item
    return None


# -- 写入：采集 -------------------------------------------------------------


def record_capture(
    repo: ConfigRepository,
    actor: Any,
    *,
    profile_id: Any,
    content: bytes,
    capture_kind: str = CAPTURE_MANUAL,
    filename: Any = None,
    logical_file_id: Any = None,
    release_id: Any = None,
    variant_id: Any = None,
    device_id: Any = None,
    source_name: Any = "",
    captured_at: Any = None,
    role: Any = None,
    require_release: bool = False,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """把一次采集落成不可变观测 + 一条采集事件（同一事务）。

    返回观测本体，外加 `deduplicated` / `stale_capture` / `event_id` / `replayed`
    四个**响应侧**字段（它们不进观测对象本身：一条观测是不是"重复来的"取决于
    何时问它，不是它的属性）。
    """
    pid = _text(profile_id, "profile_id")
    if capture_kind not in CAPTURE_KINDS:
        raise AppError(
            INVALID_INPUT,
            "采集种类非法",
            field="capture_kind",
            allowed=list(CAPTURE_KINDS),
        )

    # 1) 找到稳定逻辑文件：优先按 ID，否则按文件名回查
    if _optional_text(logical_file_id):
        logical_file = config_catalog.get_logical_file(repo, _optional_text(logical_file_id))
    else:
        classified = config_policies.classify_file(repo, filename, role=role)
        if classified is None:
            raise AppError(
                NOT_FOUND,
                "该文件还没有登记为逻辑文件，无法建立观测",
                filename=_optional_text(filename),
                hint="先在配置类别里登记这个文件的逻辑文件（含 role），再采集",
            )
        logical_file = classified["logical_file"]

    raw = bytes(content or b"")
    digest = sha256_bytes(raw)
    stamp = _optional_text(captured_at) or now_iso()
    source = _optional_text(source_name) or _optional_text(filename)
    release = _optional_text(release_id) or None
    variant = _optional_text(variant_id) or None
    device = _optional_text(device_id) or None

    # 2) 版本归属
    release_ref = resolve_release_ref(repo, logical_file["id"], digest, release)
    if require_release and release_ref["resolution"] != RESOLUTION_CONFIRMED:
        raise AppError(
            OBSERVATION_UNKNOWN_RELEASE,
            "这次采集的软件版本无法确认，不能用于需要版本归属的操作",
            resolution=release_ref["resolution"],
            release_id=release_ref["release_id"],
        )

    # 3) 严格解析 + 映射（失败也入库，失败观测不可能成为基线）
    definitions: List[Dict[str, Any]] = []
    definition_set = None
    mapping: Dict[str, Any]
    status = STATUS_PARSED
    reason = ""
    signature = None
    fmt = ""
    try:
        parsed = parse_document(raw, _optional_text(filename))
        fmt = parsed["format"]
        signature = parsed["signature"]
        definition_set, definitions = _definitions_for(
            repo, str(logical_file.get("family_id")), release_ref["release_id"], variant
        )
        mapping = build_mapping(repo, logical_file, definitions, parsed)
        mapping["definition_set_id"] = (
            definition_set["id"] if definition_set is not None else None
        )
    except AppError as exc:
        status = STATUS_PARSE_FAILED
        reason = exc.message
        mapping = {
            "entries": [],
            "unresolved_keys": [],
            "truncated": False,
            "definition_set_id": None,
        }

    # 3.5) 幂等重放：同一个 key 的重发必须在去重判定之前短路
    replayed = _replay_capture(
        repo,
        idempotency_key,
        profile_id=pid,
        logical_file_id=str(logical_file["id"]),
        digest=digest,
    )
    if replayed is not None:
        return replayed

    # 4) 同内容重复采集：不新增观测，但仍要留下"这个时刻"的事件
    duplicate = _find_duplicate(
        repo,
        profile_id=pid,
        logical_file_id=logical_file["id"],
        digest=digest,
        release_id=release_ref["release_id"],
        status=status,
    )

    # 5) 旧任务晚到：入库存史，但不移动"当前观测"指针
    previous = current_observation(repo, pid, logical_file["id"])
    stale = bool(
        previous is not None
        and str(previous.get("captured_at") or "") > stamp
    )

    tx = repo.transaction(
        actor,
        "采集 %s（%s）" % (logical_file.get("display_name"), source),
        action="observation.capture",
        idempotency_key=idempotency_key,
    )
    observation_ref = None
    if duplicate is None:
        tx.blob(raw)
        observation_ref = tx.put(
            "observation",
            {
                "scope": "profile",
                "profile_id": pid,
                "logical_file_id": logical_file["id"],
                "family_id": logical_file.get("family_id"),
                "role": logical_file.get("role"),
                "release_ref": release_ref,
                "variant_id": variant,
                "device_id": device,
                "blob_sha256": digest,
                "byte_size": len(raw),
                "format": fmt,
                "structure_signature": signature,
                "captured_at": stamp,
                "captured_by": _label(actor),
                "capture_kind": capture_kind,
                "source_name": source,
                "filename": _optional_text(filename),
                "completeness": completeness_of(mapping),
                "mapping": mapping,
                "mapping_revision": 1,
                "definition_set_id": mapping.get("definition_set_id"),
                "status": status,
                "reason": reason,
                "stale_capture": stale,
            },
        )
        target = observation_ref
    else:
        target = duplicate["id"]

    if duplicate is None and not stale:
        # 采集事件与"当前观测"指针在同一次提交里落地：不存在"指针指过去却没有观测"
        tx.pointer(
            "observation_current",
            "%s:%s" % (pid, logical_file["id"]),
            target,
        )

    event_ref = tx.put(
        "capture_event",
        {
            # 观测对象与本事件同一事务创建，仓储在落盘时把 Ref 换成真实 ID
            "observation_id": target,
            "profile_id": pid,
            "logical_file_id": logical_file["id"],
            "blob_sha256": digest,
            "captured_at": stamp,
            "captured_by": _label(actor),
            "capture_kind": capture_kind,
            "source_name": source,
            "filename": _optional_text(filename),
            "deduplicated": duplicate is not None,
            "stale_capture": stale,
            "status": status,
            "reason": reason,
        },
    )
    receipt = tx.commit()

    if receipt.replayed:
        for ref in receipt.refs:
            if ref.kind == "observation":
                payload = dict(get_observation(repo, str(ref.id), ref.revision))
                payload.update({"deduplicated": False, "stale_capture": stale,
                                "event_id": None, "replayed": True})
                return payload

    if observation_ref is not None:
        payload = dict(get_observation(repo, str(observation_ref.id), observation_ref.revision))
    else:
        payload = dict(duplicate)
    payload.update(
        {
            "deduplicated": duplicate is not None,
            "stale_capture": stale,
            "event_id": str(event_ref.id),
            "replayed": False,
        }
    )
    return payload


# -- 写入：重新映射 ---------------------------------------------------------


def remap_observation(
    repo: ConfigRepository,
    actor: Any,
    *,
    observation_id: Any,
    release_id: Any = None,
    variant_id: Any = None,
    expected_revision: Any = None,
    reason: Any = "",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """用当前定义重算映射，产出**新修订**；原始字节与上次结果都保留。

    `r1` 永远是采集那一刻的结论。重新映射只是"以今天的定义再看一遍同一份字节"，
    所以它不碰 blob，也不删旧修订——历史基线可能还指着旧修订里的值。
    """
    object_id = _text(observation_id, "observation_id")
    current = get_observation(repo, object_id)
    head = repo.head_revision("observation", object_id)
    wanted = _optional_text(expected_revision)
    if wanted and wanted != head:
        raise AppError(
            "REVISION_CONFLICT",
            "观测已被重新映射过，请刷新后重试",
            expected_revision=wanted,
            actual_revision=head,
        )

    logical_file = config_catalog.get_logical_file(repo, str(current.get("logical_file_id")))
    raw = repo.read_blob(str(current.get("blob_sha256")))
    release = _optional_text(release_id) or (current.get("release_ref") or {}).get("release_id")
    variant = (
        _optional_text(variant_id)
        if variant_id is not None
        else current.get("variant_id")
    )
    release_ref = resolve_release_ref(repo, logical_file["id"], str(current.get("blob_sha256")), release)

    status = STATUS_PARSED
    mapping: Dict[str, Any]
    error = ""
    try:
        parsed = parse_document(raw, str(current.get("filename") or ""))
        definition_set, definitions = _definitions_for(
            repo, str(logical_file.get("family_id")), release_ref["release_id"], variant
        )
        mapping = build_mapping(repo, logical_file, definitions, parsed)
        mapping["definition_set_id"] = (
            definition_set["id"] if definition_set is not None else None
        )
    except AppError as exc:
        status = STATUS_PARSE_FAILED
        error = exc.message
        mapping = {
            "entries": [],
            "unresolved_keys": [],
            "truncated": False,
            "definition_set_id": None,
        }

    revision_no = int(current.get("mapping_revision") or 1) + 1
    tx = repo.transaction(
        actor,
        _optional_text(reason) or "重新映射观测 %s" % object_id,
        action="observation.remap",
        expected={"observation:%s" % object_id: head},
        idempotency_key=idempotency_key,
    )
    payload = dict(current)
    payload.pop("id", None)
    payload.update(
        {
            "release_ref": release_ref,
            "variant_id": _optional_text(variant) or None,
            "completeness": completeness_of(mapping),
            "mapping": mapping,
            "mapping_revision": revision_no,
            "definition_set_id": mapping.get("definition_set_id"),
            "remapped_at": now_iso(),
            "remapped_by": _label(actor),
            "status": status,
            "reason": error or "重新映射（原采集结论保留在 r1）",
        }
    )
    ref = tx.put("observation", payload, object_id=object_id)
    tx.commit()
    return get_observation(repo, object_id, ref.revision)


# -- 摘要 -------------------------------------------------------------------


def ingest_summary(repo: ConfigRepository, profile_id: Optional[str] = None) -> Dict[str, Any]:
    """采集概览：按状态/完整性/版本归属计数，附最近一次采集。"""
    items = list_observations(repo, profile_id=profile_id)
    by_status: Dict[str, int] = {}
    by_completeness: Dict[str, int] = {}
    by_resolution: Dict[str, int] = {}
    for item in items:
        by_status[item.get("status") or ""] = by_status.get(item.get("status") or "", 0) + 1
        by_completeness[item.get("completeness") or ""] = (
            by_completeness.get(item.get("completeness") or "", 0) + 1
        )
        resolution = (item.get("release_ref") or {}).get("resolution") or ""
        by_resolution[resolution] = by_resolution.get(resolution, 0) + 1
    return {
        "total": len(items),
        "by_status": by_status,
        "by_completeness": by_completeness,
        "by_resolution": by_resolution,
        "latest": items[-1] if items else None,
        "capture_kinds": list(CAPTURE_KINDS),
        "resolutions": list(RESOLUTIONS),
    }


def gate_error() -> None:  # pragma: no cover - 由 server 层调用
    """占位：门禁检查在 server 层做（`config_gate.require_write`）。"""
    config_gate.require_write()
