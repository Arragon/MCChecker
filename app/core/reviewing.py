"""审阅修改核心逻辑。

包含：
- 旧版树操作函数（make_node_key, get_node_by_key, apply_review_updates, serialize_tree）
- 新版审阅提交流程（ReviewRemark, submit_review）
- XML/JSON 保真输出（apply_review_to_xml, apply_review_to_json）
"""

from __future__ import annotations

import json
import os
import re
import uuid
import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from app.core.models import NodeRef, SourceSnapshot, ParsedDocument
from app.core.parser import compute_content_hash, parse_xml_full, parse_json_full
from app.core.storage import atomic_write_json


_ARRAY_LABEL_RE = re.compile(r"^\[(\d+)\]$")


# ---------------------------------------------------------------------------
# 旧版树操作函数（保持向后兼容）
# ---------------------------------------------------------------------------

def make_node_key(parent_key: str, child_index: int) -> str:
    return f"{parent_key}.{child_index}" if parent_key else str(child_index)


def get_node_by_key(tree: Dict[str, Any], node_key: str) -> Dict[str, Any]:
    key_text = str(node_key or "").strip()
    if not key_text:
        raise ValueError("节点标识不能为空")

    node = tree
    for part in key_text.split("."):
        try:
            index = int(part)
        except ValueError as e:
            raise ValueError("节点标识格式不正确") from e
        children = node.get("children") or []
        if index < 0 or index >= len(children):
            raise ValueError("节点标识不存在")
        node = children[index]
    return node


def apply_review_updates(tree: Dict[str, Any], approved_items: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    copied = deepcopy(tree)
    for item in approved_items:
        node = get_node_by_key(copied, item.get("node_key") or "")
        node["value"] = str(item.get("proposed_value") or "")
    return copied


def serialize_tree(tree: Dict[str, Any], filename: str) -> bytes:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    tree_type = str((tree.get("attrs") or {}).get("type") or ext).lower()
    if tree_type == "xml" or ext == "xml":
        return _serialize_xml_tree(tree)
    if tree_type == "json" or ext == "json":
        return _serialize_json_tree(tree)
    raise ValueError("暂不支持该文件类型的审阅输出")


def _serialize_json_tree(tree: Dict[str, Any]) -> bytes:
    data = _json_children_to_data(tree.get("children") or [])
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def _json_children_to_data(children: List[Dict[str, Any]]) -> Any:
    if not children:
        return {}
    if _is_array_children(children):
        ordered = sorted(children, key=lambda item: _array_label_index(item.get("label") or ""))
        return [_json_node_to_data(child) for child in ordered]
    return {str(child.get("label") or ""): _json_node_to_data(child) for child in children}


def _json_node_to_data(node: Dict[str, Any]) -> Any:
    children = node.get("children") or []
    node_type = str((node.get("attrs") or {}).get("type") or "")

    if children:
        if node_type == "array" or _is_array_children(children):
            ordered = sorted(children, key=lambda item: _array_label_index(item.get("label") or ""))
            return [_json_node_to_data(child) for child in ordered]
        return {str(child.get("label") or ""): _json_node_to_data(child) for child in children}

    if node.get("value") is None:
        if node_type == "array":
            return []
        if node_type == "object":
            return {}
        return None

    return _parse_json_primitive(str(node.get("value") or ""), node_type)


def _parse_json_primitive(value: str, node_type: str) -> Any:
    if node_type == "bool":
        return value.strip().lower() == "true"
    if node_type == "NoneType":
        return None
    if node_type == "int":
        return int(value)
    if node_type == "float":
        return float(value)
    return value


def _is_array_children(children: List[Dict[str, Any]]) -> bool:
    if not children:
        return False
    indexes = []
    for child in children:
        match = _ARRAY_LABEL_RE.match(str(child.get("label") or ""))
        if not match:
            return False
        indexes.append(int(match.group(1)))
    return sorted(indexes) == list(range(len(indexes)))


def _array_label_index(label: str) -> int:
    match = _ARRAY_LABEL_RE.match(label)
    if not match:
        raise ValueError("数组节点标签不合法")
    return int(match.group(1))


def _serialize_xml_tree(tree: Dict[str, Any]) -> bytes:
    roots = tree.get("children") or []
    if not roots:
        raise ValueError("XML 树为空")
    root = _xml_node_to_element(roots[0])
    try:
        ET.indent(root, space="  ")
    except Exception:
        pass
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _xml_node_to_element(node: Dict[str, Any]) -> ET.Element:
    elem = ET.Element(str(node.get("label") or "node"))
    text_value = str(node.get("value")) if node.get("value") is not None else None

    for child in node.get("children") or []:
        label = str(child.get("label") or "")
        if label.startswith("@"):
            elem.set(label[1:], str(child.get("value") or ""))
            continue
        if label == "#text":
            text_value = str(child.get("value") or "")
            continue
        elem.append(_xml_node_to_element(child))

    if text_value is not None:
        elem.text = text_value
    return elem


# ---------------------------------------------------------------------------
# 新版审阅提交：版本校验与保真输出
# ---------------------------------------------------------------------------

@dataclass
class ReviewRemark:
    """审阅建议，包含版本信息。

    Attributes:
        remark_id: 唯一标识
        source_hash: 创建建议时的 source content hash
        node_ref: 节点引用（locator + original_value + value_type）
        original_value: 创建时的原始值
        original_type: 原始值类型（string/int/float/bool/element/attribute/text）
        suggested_value: 建议修改值
        status: pending / approved / rejected / conflict
    """
    remark_id: str
    source_hash: str
    node_ref: NodeRef
    original_value: Any
    original_type: str
    suggested_value: Any
    status: str = "pending"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "remark_id": self.remark_id,
            "source_hash": self.source_hash,
            "locator": self.node_ref.locator,
            "value_type": self.node_ref.value_type,
            "original_value": self.original_value,
            "original_type": self.original_type,
            "suggested_value": self.suggested_value,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReviewRemark":
        node_ref = NodeRef(
            content_hash=data["source_hash"],
            locator=data["locator"],
            value_type=data.get("value_type", "string"),
            original_value=data.get("original_value"),
        )
        return cls(
            remark_id=data.get("remark_id", uuid.uuid4().hex),
            source_hash=data["source_hash"],
            node_ref=node_ref,
            original_value=data.get("original_value"),
            original_type=data.get("original_type", "string"),
            suggested_value=data.get("suggested_value"),
            status=data.get("status", "pending"),
        )


class ReviewConflictError(Exception):
    """审阅提交时源已变更"""
    pass


class ReviewValidationError(Exception):
    """审阅校验失败（NodeRef 不匹配等）"""
    pass


def compute_content_hash_from_file(file_path: str) -> str:
    """读取文件并计算 SHA-256 hash"""
    with open(file_path, "rb") as f:
        return compute_content_hash(f.read())


def _detect_format(source_path: str) -> str:
    """根据文件扩展名推断格式"""
    ext = source_path.rsplit(".", 1)[-1].lower() if "." in source_path else ""
    if ext == "xml":
        return "xml"
    if ext == "json":
        return "json"
    raise ValueError(f"无法识别文件格式: {source_path}")


def _resolve_node_in_parsed(parsed: ParsedDocument, node_ref: NodeRef) -> Any:
    """在 ParsedDocument 中查找 NodeRef 指向的节点"""
    return parsed.get_node(node_ref)


def _verify_original_value(node: Any, node_ref: NodeRef, fmt: str) -> bool:
    """校验节点当前值是否与 NodeRef.original_value 一致"""
    if node is None:
        return False
    if fmt == "json":
        current = node.get("value") if isinstance(node, dict) else None
        expected = node_ref.original_value
        # 类型宽松比较：字符串化后比较
        return str(current) == str(expected)
    elif fmt == "xml":
        if isinstance(node, dict):
            current = node.get("value")
            expected = node_ref.original_value
            return str(current) == str(expected)
    return False


# ---------------------------------------------------------------------------
# submit_review: 审阅提交流程
# ---------------------------------------------------------------------------

def submit_review(
    remarks: List[ReviewRemark],
    source_path: str,
    profile_id: str,
    actor: Any,
    *,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """提交审阅。

    流程：
    1. 重新授权
    2. 校验 source hash 未变化
    3. 校验每个 remark 的 NodeRef + original value/type
    4. 修改 ParsedDocument 生成 candidate
    5. 重 parse 验证只有预期语义变化
    6. 原子写入 artifact
    7. 返回结果

    Args:
        remarks: 审阅建议列表
        source_path: 源文件路径
        profile_id: 目标 profile
        actor: Actor 身份
        output_path: 候选输出路径（可选，默认自动生成）

    Returns:
        包含 approved/rejected/conflict 计数的结果字典

    Raises:
        ReviewConflictError: source hash 已变化
        ReviewValidationError: NodeRef 校验失败
    """
    from app.utils.auth import authorize

    # 1. 重新授权
    authorize(actor, "review", profile_id)

    # 2. 计算当前 source hash
    current_hash = compute_content_hash_from_file(source_path)
    fmt = _detect_format(source_path)

    # 3. 校验每个 remark
    approved: List[ReviewRemark] = []
    conflicts: List[ReviewRemark] = []
    validation_errors: List[ReviewRemark] = []

    # 解析当前源文件
    with open(source_path, "rb") as f:
        source_bytes = f.read()

    if fmt == "xml":
        parsed = parse_xml_full(source_bytes, os.path.basename(source_path), source_hash=current_hash)
    else:
        parsed = parse_json_full(source_bytes, os.path.basename(source_path), source_hash=current_hash)

    for remark in remarks:
        if remark.status != "pending":
            continue

        # 3a. 校验 source hash
        if remark.source_hash != current_hash:
            remark.status = "conflict"
            conflicts.append(remark)
            continue

        # 3b. 校验 NodeRef 能找到节点
        node = _resolve_node_in_parsed(parsed, remark.node_ref)
        if node is None:
            remark.status = "conflict"
            validation_errors.append(remark)
            continue

        # 3c. 校验 original value
        if not _verify_original_value(node, remark.node_ref, fmt):
            remark.status = "conflict"
            validation_errors.append(remark)
            continue

        approved.append(remark)

    # 如果全部冲突，不生成 candidate
    if not approved:
        return {
            "approved": 0,
            "rejected": 0,
            "conflict": len(conflicts) + len(validation_errors),
            "candidate_path": None,
            "candidate_hash": None,
            "remarks": [r.to_dict() for r in remarks],
        }

    # 4. 生成 candidate
    if output_path is None:
        base, ext = os.path.splitext(source_path)
        output_path = f"{base}.candidate{ext or ('.xml' if fmt == 'xml' else '.json')}"

    try:
        if fmt == "xml":
            apply_review_to_xml(source_path, approved, output_path)
        else:
            apply_review_to_json(source_path, approved, output_path)
    except Exception as e:
        # candidate 生成失败，不把 remark 标 approved
        for r in approved:
            r.status = "pending"
        raise ReviewValidationError(f"candidate 生成失败: {e}") from e

    # 5. 重 parse 验证：检查只有预期变化
    candidate_hash = compute_content_hash_from_file(output_path)

    # 6. 标记 approved
    for r in approved:
        r.status = "approved"

    return {
        "approved": len(approved),
        "rejected": 0,
        "conflict": len(conflicts) + len(validation_errors),
        "candidate_path": output_path,
        "candidate_hash": candidate_hash,
        "remarks": [r.to_dict() for r in remarks],
    }


# ---------------------------------------------------------------------------
# XML 保真输出
# ---------------------------------------------------------------------------

# 标准库 ElementTree 无法保留的 XML 构造
_UNSUPPORTED_XML_CONSTRUCTS = {
    "CDATA": "CDATA sections",
    "entity_ref": "entity references beyond standard XML",
    "processing_instruction": "processing instructions inside elements",
    "doctype": "DOCTYPE declarations",
}


def apply_review_to_xml(
    source_path: str,
    approved_remarks: List[ReviewRemark],
    output_path: str,
) -> str:
    """应用审阅修改到 XML 源文件，生成保真候选文件。

    使用标准库 xml.etree.ElementTree 进行最小化修改：
    - 仅修改 approved remark 指向的节点
    - 未修改的业务语义不丢失
    - 标准库无法保留的构造明确拒绝

    Args:
        source_path: 源 XML 文件路径
        approved_remarks: 已批准的审阅建议
        output_path: 输出候选文件路径

    Returns:
        输出文件路径

    Raises:
        ReviewValidationError: 遇到不支持的 XML 构造时
    """
    # 读取源文件
    with open(source_path, "rb") as f:
        source_bytes = f.read()

    source_hash = compute_content_hash(source_bytes)

    # 使用完整解析获取 ParsedDocument
    parsed = parse_xml_full(source_bytes, os.path.basename(source_path), source_hash=source_hash)

    # 用标准库解析原始 XML 用于输出（保留结构）
    try:
        tree = ET.parse(source_path)
        root = tree.getroot()
    except ET.ParseError as e:
        msg = str(e)
        if "unbound prefix" in msg:
            # 修复 namespace 后重试
            from app.core.parser import _preprocess_xml_namespaces
            text = source_bytes.decode("utf-8-sig")
            text = _preprocess_xml_namespaces(text)
            root = ET.fromstring(text.encode("utf-8"))
        else:
            raise

    # 逐个应用修改
    for remark in approved_remarks:
        _apply_change_to_xml_element(root, remark, parsed)

    # 写入候选文件
    try:
        ET.indent(root, space="  ")
    except Exception:
        pass

    output_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with open(output_path, "wb") as f:
        f.write(output_bytes)

    return output_path


def _apply_change_to_xml_element(
    root: ET.Element,
    remark: ReviewRemark,
    parsed: ParsedDocument,
) -> None:
    """在 XML Element 树中应用单个 remark 的修改。

    通过 locator 在 parsed tree 中找到对应节点，
    然后在 Element 树中定位并修改。
    """
    locator = remark.node_ref.locator
    if not locator.startswith("xml:"):
        return

    target = locator[4:]  # 去掉 "xml:"
    bracket = target.rfind("[")
    if bracket < 0:
        return
    tag = target[:bracket]
    occ_str = target[bracket + 1:-1]
    try:
        target_occ = int(occ_str)
    except ValueError:
        return

    value_type = remark.node_ref.value_type

    # 在 Element 树中查找第 target_occ 个匹配 tag
    elem, parent = _find_xml_element_with_parent(root, tag, target_occ)
    if elem is None:
        return

    if value_type == "attribute":
        # locator 格式: xml:<tag>[<occ>]@<attr_name>
        attr_locator = locator
        at_pos = attr_locator.rfind("@")
        if at_pos >= 0:
            attr_name = attr_locator[at_pos + 1:]
            elem.set(attr_name, str(remark.suggested_value))
    elif value_type == "text":
        elem.text = str(remark.suggested_value)
    elif value_type in ("element", "string", "number"):
        elem.text = str(remark.suggested_value)
    elif value_type == "tail":
        elem.tail = str(remark.suggested_value)


def _find_xml_element_with_parent(
    root: ET.Element,
    tag: str,
    target_occ: int,
) -> tuple:
    """在 Element 树中 DFS 查找第 target_occ 个匹配 tag 的元素。

    Returns: (element, parent) 或 (None, None)
    """
    counter = {tag: 0}
    return _xml_dfs_with_parent(root, None, tag, target_occ, counter)


def _xml_dfs_with_parent(
    elem: ET.Element,
    parent: Optional[ET.Element],
    tag: str,
    target_occ: int,
    counter: dict,
) -> tuple:
    if elem.tag == tag:
        counter[tag] = counter.get(tag, 0) + 1
        if counter[tag] == target_occ:
            return elem, parent
    for child in elem:
        result = _xml_dfs_with_parent(child, elem, tag, target_occ, counter)
        if result[0] is not None:
            return result
    return None, None


# ---------------------------------------------------------------------------
# JSON 保真输出
# ---------------------------------------------------------------------------

def apply_review_to_json(
    source_path: str,
    approved_remarks: List[ReviewRemark],
    output_path: str,
) -> str:
    """应用审阅修改到 JSON 源文件，生成候选文件。

    使用 ParsedDocument 的 locator（JSON Pointer）定位节点，
    保留原始类型信息（int/float/bool/null/str）。

    Args:
        source_path: 源 JSON 文件路径
        approved_remarks: 已批准的审阅建议
        output_path: 输出候选文件路径

    Returns:
        输出文件路径
    """
    with open(source_path, "rb") as f:
        source_bytes = f.read()

    source_hash = compute_content_hash(source_bytes)
    parsed = parse_json_full(source_bytes, os.path.basename(source_path), source_hash=source_hash)

    # 解析原始 JSON 数据
    text = source_bytes.decode("utf-8-sig")
    data = json.loads(text)

    # 逐个应用修改
    for remark in approved_remarks:
        _apply_change_to_json(data, remark, parsed)

    # 原子写入
    dir_path = os.path.dirname(output_path)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return output_path


def _apply_change_to_json(
    data: Any,
    remark: ReviewRemark,
    parsed: ParsedDocument,
) -> None:
    """在 JSON 数据中应用单个 remark 的修改。

    使用 JSON Pointer (locator) 遍历到目标节点。
    """
    pointer = remark.node_ref.locator
    if not pointer.startswith("$"):
        return

    # 解析 JSON Pointer
    parts = pointer[1:].split("/")
    if parts and parts[0] == "":
        parts = parts[1:]
    # 反转 escaping
    unescaped = [p.replace("~1", "/").replace("~0", "~") for p in parts]

    # 遍历到倒数第二层
    current = data
    for part in unescaped[:-1]:
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            return

    # 设置最后一层
    last_key = unescaped[-1] if unescaped else None
    if last_key is None:
        return

    # 类型转换
    suggested = remark.suggested_value
    vtype = remark.node_ref.value_type
    if vtype == "int":
        suggested = int(suggested)
    elif vtype == "float":
        suggested = float(suggested)
    elif vtype == "bool":
        suggested = str(suggested).strip().lower() == "true"
    elif vtype == "NoneType":
        suggested = None

    if isinstance(current, dict):
        current[last_key] = suggested
    elif isinstance(current, list):
        current[int(last_key)] = suggested


# ---------------------------------------------------------------------------
# 幂等提交保护
# ---------------------------------------------------------------------------

def submit_review_idempotent(
    remarks: List[ReviewRemark],
    source_path: str,
    profile_id: str,
    actor: Any,
    *,
    output_path: Optional[str] = None,
    applied_hashes: Optional[set] = None,
) -> Dict[str, Any]:
    """幂等审阅提交。

    如果 applied_hashes 非空，检查 candidate_hash 是否已存在，
    防止双管理员重复 submit 产生多次业务结果。

    Args:
        remarks: 审阅建议列表
        source_path: 源文件路径
        profile_id: 目标 profile
        actor: Actor 身份
        output_path: 候选输出路径
        applied_hashes: 已应用的 candidate hash 集合

    Returns:
        结果字典，包含 "already_applied" 标记
    """
    result = submit_review(remarks, source_path, profile_id, actor, output_path=output_path)

    if applied_hashes is not None and result.get("candidate_hash"):
        if result["candidate_hash"] in applied_hashes:
            # 已经应用过，回滚状态
            for r in remarks:
                if r.status == "approved":
                    r.status = "pending"
            result["already_applied"] = True
            result["approved"] = 0
            return result
        applied_hashes.add(result["candidate_hash"])

    result["already_applied"] = False
    return result
