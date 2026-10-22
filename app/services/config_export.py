"""从已批准基线生成目标 XML（M6-13 / INH-1958）。

一条硬规矩：**以真实模板为骨架**。目标文件是在那份出口 XML 的**原始字节**上只改
批准过的位置，不是从精简 JSON 重建——重建会丢掉命名空间、注释、处理指令、属性顺序
和缩进，而这些东西在现场是有意义的。

因此这里做四件事：

1. 找骨架：基线 → `variant_id` → 模板快照 → 原始字节。找不到就是 `template_missing`。
2. 算落点：定义集合里每条定义给出 `xml` 定位，翻成 compact 文档的 loc 字符串
   （`/R[1]/A[2]/@unit`），并核对模板里确实存在该节点。
3. 只改变了的位置：值没动的参数**不产生编辑**——不写等于不破坏。
4. 生成后自证：重新解析输出，确认①每个被改的位置现在读到的是新值、②结构指纹与
   模板一致（没动结构）。任何一条不成立就整体失败。

**不宣称逐字节保真**：标准库会统一属性引号与空元素写法。业务值不变、结构不变，
写法可能变——这句话要写在返回的 `validation.warnings` 里，而不是让人自己发现。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..core import config_mapping, reviewing
from ..core.config_repository import ConfigRepository, sha256_bytes
from ..core.errors import (
    ARTIFACT_TEMPLATE_INCOMPATIBLE,
    INVALID_INPUT,
    NOT_FOUND,
    AppError,
)
from . import config_baselines, config_catalog, config_ingest

#: 阻断生成的缺口（每一条都对应"这个文件现在不能发"）。
BLOCK_TEMPLATE_MISSING = "template_missing"
BLOCK_TEMPLATE_NOT_XML = "template_not_xml"
BLOCK_DEFINITION_MISSING = "definition_missing"
BLOCK_LOCATION_MISSING = "location_missing"
BLOCK_VALUE_UNSUPPORTED = "value_unsupported"

BLOCKER_MESSAGES = {
    BLOCK_TEMPLATE_MISSING: "基线没有绑定模板变体/模板快照，无法确定骨架",
    BLOCK_TEMPLATE_NOT_XML: "模板不是 XML 出口文件",
    BLOCK_DEFINITION_MISSING: "参数在当前定义集合里没有定义，不知道该写到哪",
    BLOCK_LOCATION_MISSING: "模板里找不到定义声明的定位（模板与定义集合不匹配）",
    BLOCK_VALUE_UNSUPPORTED: "该值类型需要改写结构（例如 null 要写成空元素），未实现",
}


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _template_of(
    repo: ConfigRepository, baseline: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], Optional[bytes]]:
    """基线 → (变体, 模板字节)。缺任何一环都返回 (None, None)。"""
    variant_id = _text(baseline.get("variant_id"))
    if not variant_id:
        return None, None
    variant = config_catalog.get_variant(repo, variant_id)
    snapshot_id = _text(variant.get("template_snapshot_id"))
    if not snapshot_id:
        return None, None
    return variant, config_catalog.snapshot_bytes(repo, snapshot_id)


def plan(repo: ConfigRepository, baseline: Dict[str, Any]) -> Dict[str, Any]:
    """算出"要改哪些位置"，**不生成字节**。

    返回 `{template, edits, same, skipped, blockers}`：`edits` 里每一项都带
    `parameter_id` 与 compact loc，方便前端逐条展示"会改哪一行"。
    """
    variant, template_bytes = _template_of(repo, baseline)
    blockers: List[Dict[str, Any]] = []
    if variant is None or template_bytes is None:
        blockers.append({"code": BLOCK_TEMPLATE_MISSING})
        return {"template": None, "edits": [], "same": [], "skipped": [], "blockers": blockers}

    template_sha = sha256_bytes(template_bytes)
    try:
        parsed = config_ingest.parse_document(template_bytes, "template.xml")
    except AppError as err:
        blockers.append(
            {"code": BLOCK_TEMPLATE_NOT_XML, "detail": err.message}
        )
        return {
            "template": {"variant_id": variant["id"], "snapshot_id": variant.get("template_snapshot_id"),
                         "sha256": template_sha, "format": "unknown"},
            "edits": [],
            "same": [],
            "skipped": [],
            "blockers": blockers,
        }

    if parsed["format"] != "xml":
        blockers.append({"code": BLOCK_TEMPLATE_NOT_XML, "detail": "模板格式是 %s" % parsed["format"]})
        return {
            "template": {"variant_id": variant["id"], "snapshot_id": variant.get("template_snapshot_id"),
                         "sha256": template_sha, "format": parsed["format"]},
            "edits": [],
            "same": [],
            "skipped": [],
            "blockers": blockers,
        }

    root = parsed["root"]
    definition_set_id = _text(baseline.get("definition_set_id")) or None
    definitions = config_baselines.definitions_by_parameter(repo, definition_set_id)

    edits: List[Dict[str, Any]] = []
    same: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for parameter_id, item in (baseline.get("values") or {}).items():
        value = item.get("value") or {}
        definition = definitions.get(parameter_id)
        if definition is None:
            blockers.append({"code": BLOCK_DEFINITION_MISSING, "parameter_id": parameter_id})
            continue
        xml_block = definition.get("xml") or {}
        loc = config_mapping.to_xml_loc(xml_block.get("loc"))
        if loc is None:
            skipped.append({"parameter_id": parameter_id, "reason": "定义没有 XML 定位"})
            continue
        path = config_mapping.compact_path(root, loc)
        if path is None:
            blockers.append(
                {
                    "code": BLOCK_LOCATION_MISSING,
                    "parameter_id": parameter_id,
                    "loc": str(loc),
                }
            )
            continue

        kind = "attribute" if loc.attribute else "element"
        wanted = _text(value.get("text"))
        if _text(value.get("type")) == "null":
            # 写空元素会改结构（`<A/>` 与 `<A></A>`、以及"删掉才算清空"的语义问题）
            blockers.append({"code": BLOCK_VALUE_UNSUPPORTED, "parameter_id": parameter_id})
            continue

        current = config_mapping.read_xml_value(root, loc)
        if (current or "") == wanted:
            same.append({"parameter_id": parameter_id, "loc": path, "value": wanted})
            continue
        edits.append(
            {
                "parameter_id": parameter_id,
                "loc": path,
                "kind": kind,
                "xml_loc": str(loc),
                "from": current or "",
                "to": wanted,
            }
        )

    return {
        "template": {
            "variant_id": variant["id"],
            "snapshot_id": _text(variant.get("template_snapshot_id")),
            "sha256": template_sha,
            "format": parsed["format"],
            "definition_set_id": definition_set_id,
        },
        "edits": edits,
        "same": same,
        "skipped": skipped,
        "blockers": blockers,
    }


def _verify(
    produced: bytes,
    template: bytes,
    edits: List[Dict[str, Any]],
    filename: str,
) -> Dict[str, Any]:
    """生成后自证：每个落点读到新值 + 结构指纹不变。"""
    checks: List[str] = ["reparsed"]
    warnings: List[str] = []
    root = config_ingest.parse_document(produced, filename)["root"]

    if config_mapping.structure_signature(produced) != config_mapping.structure_signature(template):
        raise AppError(
            ARTIFACT_TEMPLATE_INCOMPATIBLE,
            "生成结果改变了文件结构",
            check="structure_changed",
        )
    checks.append("structure_unchanged")

    for edit in edits:
        node = _read_path(root, edit["loc"])
        if node is None:
            raise AppError(
                ARTIFACT_TEMPLATE_INCOMPATIBLE,
                "生成结果里找不到被改的位置",
                check="edit_target_missing",
                loc=edit["loc"],
            )
        if node != edit["to"]:
            raise AppError(
                ARTIFACT_TEMPLATE_INCOMPATIBLE,
                "生成结果里该位置的值与批准值不一致",
                check="edit_value_mismatch",
                loc=edit["loc"],
                expected=edit["to"],
                actual=node,
            )
    if edits:
        checks.append("edits_applied")
    if produced != template:
        warnings.append("标准库重写 XML 时可能统一属性引号、空元素与实体写法；业务值与结构不变，不宣称逐字节保真。")
    if not edits:
        warnings.append("没有任何值变化，产物是模板的字节副本。")
    return {"ok": True, "checks": checks, "warnings": warnings}


def _read_path(root: Any, path: str) -> Optional[str]:
    """按 compact loc 字符串读回一个值（`/R[1]/A[2]/@unit` 或 `/R[1]/B[1]`）。

    第一段是**根元素自己**（`/R[1]`），不是它的子节点——跳过它，从根的孩子开始走。
    """
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return None
    node = root
    for segment in segments[1:]:
        if segment.startswith("@"):
            return node.get(segment[1:])
        name, _, index = segment.partition("[")
        occurrence = int(index.rstrip("]") or "1")
        matches = [child for child in list(node) if child.tag == name]
        if len(matches) < occurrence:
            return None
        node = matches[occurrence - 1]
    return (node.text or "").strip()


def generate(
    repo: ConfigRepository,
    *,
    baseline_id: Any,
    filename: Optional[str] = None,
) -> Dict[str, Any]:
    """生成目标 XML 字节。基线必须已批准——草稿不生成产物。"""
    baseline = config_baselines.get_baseline(repo, _text(baseline_id))
    if baseline.get("state") != config_baselines.STATE_APPROVED:
        raise AppError(
            INVALID_INPUT,
            "只有已批准的基线才能生成目标文件（当前 %s）" % baseline.get("state"),
            field="baseline_id",
            state=baseline.get("state"),
        )

    planned = plan(repo, baseline)
    if planned["blockers"]:
        raise AppError(
            ARTIFACT_TEMPLATE_INCOMPATIBLE,
            "不能生成目标文件：存在阻断项",
            blockers=[
                dict(item, message=BLOCKER_MESSAGES.get(item["code"], item["code"]))
                for item in planned["blockers"]
            ],
        )

    template = config_catalog.snapshot_bytes(repo, planned["template"]["snapshot_id"])
    edits = planned["edits"]
    name = _text(filename) or _default_filename(repo, baseline, planned["template"]["variant_id"])
    if edits:
        produced = reviewing.apply_edits(
            template, "xml", [(item["loc"], item["to"], item["kind"]) for item in edits]
        )
    else:
        produced = template
    validation = _verify(produced, template, edits, name)
    return {
        "content": produced,
        "filename": name,
        "format": "xml",
        "template": planned["template"],
        "edits": edits,
        "same": planned["same"],
        "skipped": planned["skipped"],
        "validation": validation,
        "baseline_revision": baseline.get("revision"),
    }


def _default_filename(
    repo: ConfigRepository, baseline: Dict[str, Any], variant_id: Any
) -> str:
    """`<类别代码>_<版本>_<基线修订>.xml`——稳定、可检索、不含路径分隔符。

    类别代码从**模板变体**取：基线本身不存 `family_id`，而变体一定挂在某个类别下。
    """
    family_code = "config"
    release_label = "unknown"
    try:
        vid = _text(variant_id)
        if vid:
            family_id = _text(config_catalog.get_variant(repo, vid).get("family_id"))
            if family_id:
                family_code = _text(config_catalog.get_family(repo, family_id).get("code")) or family_code
        release_id = _text(baseline.get("release_id"))
        if release_id:
            release_label = _text(
                config_catalog.get_release(repo, release_id).get("version_label")
            ) or release_label
    except AppError as err:
        if err.code != NOT_FOUND:
            raise
    safe_code = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in family_code)
    safe_label = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in release_label)
    return "%s_%s_%s.xml" % (
        safe_code or "config",
        safe_label or "unknown",
        _text(baseline.get("revision")) or "r1",
    )
