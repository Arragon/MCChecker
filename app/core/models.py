"""核心数据模型：FileRef, SourceSnapshot, NodeRef, ParsedDocument

为解析引擎提供不可变的源版本引用和节点定位能力。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from pathlib import Path


class FileKind(Enum):
    """文件来源类型"""
    CURRENT = "current"
    ARCHIVE = "archive"
    RECORD = "record"
    TEMP = "temp"


@dataclass(frozen=True)
class FileRef:
    """文件引用：标识文件来源和版本"""
    profile_id: str
    kind: FileKind
    name: str
    version_or_token: Optional[str] = None

    def __str__(self) -> str:
        v = f"@{self.version_or_token}" if self.version_or_token else ""
        return f"{self.kind.value}:{self.name}{v}"


@dataclass(frozen=True)
class SourceSnapshot:
    """不可变源版本引用

    content_hash 为 SHA-256，用于唯一标识一份源内容。
    source_handle 可以是文件路径或 token，不强制持有 bytes（大文件友好）。
    """
    file_ref: FileRef
    content_hash: str  # SHA-256
    format: str  # "xml" or "json"
    parser_version: str
    source_handle: str  # path or token

    @property
    def is_large_file(self) -> bool:
        """大文件不强制持有 bytes（预留扩展点）"""
        return False


@dataclass(frozen=True)
class NodeRef:
    """节点引用：在固定 source hash 内唯一标识节点

    locator 格式:
    - JSON: RFC 6901 JSON Pointer，如 "$/config/name"
    - XML: "xml:<tag>[<occurrence>]" 如 "xml:{http://ns}server[1]"
    """
    content_hash: str  # 所属 source 的 hash
    locator: str  # JSON Pointer 或 XML locator
    value_type: str  # "string", "number", "object", "array", "element", "attribute", "text", "tail"
    original_value: Any

    def matches(self, other: 'NodeRef') -> bool:
        """检查是否指向同一节点（同 hash + 同 locator）"""
        return (self.content_hash == other.content_hash and
                self.locator == other.locator)


@dataclass
class ParsedDocument:
    """单一完整解析模型

    持有 SourceSnapshot 引用和解析后的树根节点。
    不强制在 SourceSnapshot 之外额外复制 bytes。
    """
    source: SourceSnapshot
    root: Any  # 树根节点（dict 结构）
    format: str  # "xml" or "json"

    def get_node(self, ref: NodeRef) -> Optional[Any]:
        """根据 NodeRef 在树中查找节点

        对 JSON 使用 JSON Pointer 遍历；
        对 XML 使用 locator 匹配。
        """
        if ref.content_hash != self.source.content_hash:
            return None
        if self.format == "json":
            return self._resolve_json_pointer(self.root, ref.locator)
        elif self.format == "xml":
            return self._find_xml_node(self.root, ref.locator)
        return None

    @staticmethod
    def _resolve_json_pointer(node: Any, pointer: str) -> Optional[Any]:
        """RFC 6901 JSON Pointer 解析（$ 开头）

        支持 wrapper 结构（root wrapper 的 children 是 list，
        内部 object 节点的 children 是 dict）。
        """
        if pointer == "$":
            # 如果是 wrapper，自动 unwrap 到数据根节点
            if isinstance(node, dict) and node.get("attrs", {}).get("type") in ("json", "xml"):
                children = node.get("children", [])
                if isinstance(children, list) and len(children) == 1:
                    return children[0]
            return node
        if not pointer.startswith("$"):
            return None
        # 如果 node 是 wrapper（attrs.type == "json"），先跳到数据根节点
        if isinstance(node, dict) and node.get("attrs", {}).get("type") == "json":
            children = node.get("children", [])
            if isinstance(children, list) and len(children) == 1:
                node = children[0]
            elif isinstance(children, list) and len(children) == 0:
                return None
        # 去掉 "$" 前缀，按 "/" 分割
        parts = pointer[1:].split("/")
        if parts and parts[0] == "":
            parts = parts[1:]
        # 反转 escaping: ~1 -> /, ~0 -> ~
        unescaped = [p.replace("~1", "/").replace("~0", "~") for p in parts]
        current = node
        for part in unescaped:
            if current is None:
                return None
            if isinstance(current, dict):
                if current.get("type") == "object":
                    children = current.get("children")
                    if isinstance(children, dict):
                        current = children.get(part)
                    else:
                        return None
                elif current.get("type") == "array":
                    items = current.get("children", [])
                    if isinstance(items, list):
                        try:
                            idx = int(part)
                            current = items[idx] if 0 <= idx < len(items) else None
                        except (ValueError, IndexError):
                            return None
                    else:
                        return None
                else:
                    return None
            else:
                return None
        return current

    @staticmethod
    def _find_xml_node(node: Any, locator: str) -> Optional[Any]:
        """在 XML 树中按 locator 查找节点

        locator 格式: "xml:<tag>[<occ>]"
        """
        if not locator.startswith("xml:"):
            return None
        target = locator[4:]  # 去掉 "xml:"
        # 解析 tag[occ]
        bracket = target.rfind("[")
        if bracket < 0:
            return None
        tag = target[:bracket]
        occ_str = target[bracket + 1:-1]
        try:
            target_occ = int(occ_str)
        except ValueError:
            return None

        # 在树中 DFS 查找
        return ParsedDocument._xml_dfs(node, tag, target_occ, {tag: 0})

    @staticmethod
    def _xml_dfs(node: Any, tag: str, target_occ: int, counter: dict) -> Optional[Any]:
        """DFS 查找第 target_occ 个匹配 tag 的 XML 节点"""
        if isinstance(node, dict):
            node_type = node.get("attrs", {}).get("type") or node.get("type")
            if node_type == "element" and node.get("tag") == tag:
                counter[tag] = counter.get(tag, 0) + 1
                if counter[tag] == target_occ:
                    return node
            for child in node.get("children", []):
                result = ParsedDocument._xml_dfs(child, tag, target_occ, counter)
                if result is not None:
                    return result
        return None
