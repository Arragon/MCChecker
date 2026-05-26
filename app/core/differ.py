"""版本对比引擎

基于 difflib 实现配置文件版本对比，支持变量绑定标注。
"""

import difflib
import json
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from . import parser
from .storage import get_bound_paths


def compare_versions(
    content_old: bytes,
    content_new: bytes,
    filename_old: str,
    filename_new: str,
    use_bindings: bool = True,
) -> Dict[str, Any]:
    """对比两个版本的配置文件

    Args:
        content_old: 旧版本文件内容
        content_new: 新版本文件内容
        filename_old: 旧版本文件名
        filename_new: 新版本文件名
        use_bindings: 是否启用变量绑定标注

    Returns:
        对比结果，包含统一差异和结构化差异
    """
    try:
        text_old = content_old.decode("utf-8-sig").splitlines(keepends=True)
    except UnicodeDecodeError:
        text_old = content_old.decode("latin-1").splitlines(keepends=True)

    try:
        text_new = content_new.decode("utf-8-sig").splitlines(keepends=True)
    except UnicodeDecodeError:
        text_new = content_new.decode("latin-1").splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            text_old, text_new,
            fromfile=filename_old, tofile=filename_new,
            lineterm="",
        )
    )

    struct_diff = _structural_diff(content_old, content_new, filename_old, filename_new, use_bindings)

    return {
        "unified_diff": diff_lines,
        "structural_diff": struct_diff,
        "has_changes": len(diff_lines) > 0,
    }


def _normalize_path(path: str) -> str:
    """归一化路径：移除文件名前缀，保留纯结构路径

    例: 'v1.json/timeout' -> '/timeout'
        'root/config/server/host' -> '/config/server/host'
    """
    # 去掉第一个段（通常是文件名或 'root'）
    parts = path.split("/")
    if len(parts) > 1:
        return "/" + "/".join(parts[1:])
    return path


def _structural_diff(
    content_old: bytes,
    content_new: bytes,
    filename_old: str,
    filename_new: str,
    use_bindings: bool,
) -> Dict[str, Any]:
    """结构化差异：键值级别对比"""
    try:
        tree_old = parser.parse_file(content_old, filename_old)
        tree_new = parser.parse_file(content_new, filename_new)
    except ValueError:
        return {"type": "unsupported", "message": "文件格式不支持结构化对比"}

    # 使用归一化路径进行比较
    values_old = parser.get_all_values(tree_old)
    values_new = parser.get_all_values(tree_new)

    # 归一化路径
    norm_old = {_normalize_path(k): (k, v) for k, v in values_old.items()}
    norm_new = {_normalize_path(k): (k, v) for k, v in values_new.items()}

    bound_paths = get_bound_paths() if use_bindings else {}

    added = []
    removed = []
    modified = []
    bound_items = []

    all_norm_paths = set(list(norm_old.keys()) + list(norm_new.keys()))

    for npath in sorted(all_norm_paths):
        old_entry = norm_old.get(npath)
        new_entry = norm_new.get(npath)

        old_val = old_entry[1] if old_entry else None
        new_val = new_entry[1] if new_entry else None
        old_orig_path = old_entry[0] if old_entry else None
        new_orig_path = new_entry[0] if new_entry else None

        # 检查绑定
        is_bound_old = f"{filename_old}:{old_orig_path}" in bound_paths if old_orig_path else False
        is_bound_new = f"{filename_new}:{new_orig_path}" in bound_paths if new_orig_path else False
        is_bound = is_bound_old or is_bound_new

        item: Dict[str, Any] = {
            "path": npath,
            "old_value": old_val,
            "new_value": new_val,
            "bound": is_bound,
        }
        if is_bound:
            group_name = bound_paths.get(f"{filename_old}:{old_orig_path}", 
                                          bound_paths.get(f"{filename_new}:{new_orig_path}", ""))
            item["bound_group"] = group_name

        if old_entry is None and new_entry is not None:
            added.append(item)
        elif old_entry is not None and new_entry is None:
            removed.append(item)
        elif old_val != new_val:
            modified.append(item)

        if is_bound:
            bound_items.append(item)

    return {
        "type": "structural",
        "added": added,
        "removed": removed,
        "modified": modified,
        "bound_items": bound_items,
        "summary": {
            "added_count": len(added),
            "removed_count": len(removed),
            "modified_count": len(modified),
            "bound_count": len(bound_items),
        },
    }


def compare_with_uploaded(
    uploaded_content: bytes,
    uploaded_filename: str,
    server_content: bytes,
    server_filename: str,
    use_bindings: bool = True,
) -> Dict[str, Any]:
    """上传文件与服务端文件对比"""
    return compare_versions(
        server_content, uploaded_content,
        server_filename, uploaded_filename,
        use_bindings,
    )
