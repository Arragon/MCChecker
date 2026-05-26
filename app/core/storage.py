"""数据持久化层

管理所有持久化数据，包括配置映射、收藏、变量绑定、工具配置等。
服务端数据与客户端会话数据隔离。
"""

import json
import os
import shutil
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
CONFIGS_DIR = os.path.join(DATA_DIR, "configs")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
PARSE_CACHE_DIR = os.path.join(CACHE_DIR, "parse_tree")


def _ensure_dirs():
    """确保数据目录存在"""
    os.makedirs(CONFIGS_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    os.makedirs(PARSE_CACHE_DIR, exist_ok=True)


def _load_json(filepath: str, default: Any = None) -> Any:
    """加载 JSON 文件"""
    if not os.path.exists(filepath):
        return default if default is not None else {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error("加载 %s 失败: %s", filepath, e)
        return default if default is not None else {}


def _save_json(filepath: str, data: Any):
    """保存 JSON 文件"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ===================== 全量配置映射表 =====================

MAPPING_FILE = os.path.join(DATA_DIR, "config_mapping.json")


def load_config_mapping() -> List[Dict[str, str]]:
    """加载配置映射表"""
    return _load_json(MAPPING_FILE, [])


def save_config_mapping(mapping: List[Dict[str, str]]):
    """保存配置映射表"""
    _save_json(MAPPING_FILE, mapping)


def add_config_mapping(name: str, url: str) -> bool:
    """添加一条映射"""
    mapping = load_config_mapping()
    for item in mapping:
        if item["name"] == name:
            item["url"] = url
            save_config_mapping(mapping)
            return True
    mapping.append({"name": name, "url": url})
    save_config_mapping(mapping)
    return True


def remove_config_mapping(name: str) -> bool:
    """删除一条映射"""
    mapping = load_config_mapping()
    new_mapping = [item for item in mapping if item["name"] != name]
    save_config_mapping(new_mapping)
    return len(new_mapping) < len(mapping)


# ===================== 配置文件存储 =====================


def get_config_path(name: str) -> str:
    """获取配置文件路径"""
    return os.path.join(CONFIGS_DIR, name)


def get_archive_dir(name: str) -> str:
    """获取某个配置文件的归档目录"""
    d = os.path.join(ARCHIVE_DIR, name)
    os.makedirs(d, exist_ok=True)
    return d


def save_config_file(name: str, content: bytes) -> str:
    """保存配置文件，返回保存路径

    如果已有同名文件，将旧版移至归档目录
    """
    _ensure_dirs()
    filepath = get_config_path(name)

    # 归档旧版本
    if os.path.exists(filepath):
        timestamp = datetime.now().strftime("%Y%m%d")
        archive_name = _add_date_suffix(name, timestamp)
        archive_path = os.path.join(get_archive_dir(name), archive_name)
        # 如果同名归档已存在，添加时间戳精确到秒
        if os.path.exists(archive_path):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_name = _add_date_suffix(name, timestamp)
            archive_path = os.path.join(get_archive_dir(name), archive_name)
        shutil.move(filepath, archive_path)
        logger.info("归档旧版本: %s -> %s", name, archive_path)

    with open(filepath, "wb") as f:
        f.write(content)
    logger.info("保存配置文件: %s", name)
    try:
        from . import parse_cache
        parse_cache.invalidate(filepath)
    except Exception:
        pass
    return filepath


def _add_date_suffix(filename: str, date_str: str) -> str:
    """为文件名添加日期后缀

    例: config.xml -> config_20260524.xml
    """
    if "." in filename:
        name, ext = filename.rsplit(".", 1)
        return f"{name}_{date_str}.{ext}"
    return f"{filename}_{date_str}"


def load_config_file(name: str) -> Optional[bytes]:
    """加载配置文件内容"""
    filepath = get_config_path(name)
    if not os.path.exists(filepath):
        return None
    with open(filepath, "rb") as f:
        return f.read()


def list_config_files() -> List[str]:
    """列出所有已存储的配置文件名"""
    _ensure_dirs()
    files = []
    for f in os.listdir(CONFIGS_DIR):
        if os.path.isfile(os.path.join(CONFIGS_DIR, f)):
            files.append(f)
    return sorted(files)


def list_archived_versions(name: str) -> List[Dict[str, Any]]:
    """列出某个配置文件的所有归档版本"""
    archive_dir = get_archive_dir(name)
    versions = []
    for f in sorted(os.listdir(archive_dir), reverse=True):
        fpath = os.path.join(archive_dir, f)
        if os.path.isfile(fpath):
            mtime = os.path.getmtime(fpath)
            versions.append(
                {
                    "filename": f,
                    "date": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "size": os.path.getsize(fpath),
                }
            )
    return versions


def load_archived_file(name: str, archive_filename: str) -> Optional[bytes]:
    """加载归档文件内容"""
    archive_path = os.path.join(get_archive_dir(name), archive_filename)
    if not os.path.exists(archive_path):
        return None
    with open(archive_path, "rb") as f:
        return f.read()


def get_archived_path(name: str, archive_filename: str) -> str:
    return os.path.join(get_archive_dir(name), archive_filename)


def get_file_update_date(name: str) -> Optional[str]:
    """获取文件最后更新日期"""
    filepath = get_config_path(name)
    if not os.path.exists(filepath):
        return None
    mtime = os.path.getmtime(filepath)
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")


# ===================== 收藏变量 =====================

FAVORITES_FILE = os.path.join(DATA_DIR, "favorites.json")


def load_favorites() -> List[Dict[str, Any]]:
    """加载收藏变量列表"""
    return _load_json(FAVORITES_FILE, [])


def save_favorites(favorites: List[Dict[str, Any]]):
    """保存收藏变量列表"""
    _save_json(FAVORITES_FILE, favorites)


def add_favorite(
    variable_path: str,
    variable_label: str,
    variable_value: str,
    source_file: str,
    note: str = "",
    children: List[Dict[str, Any]] = None,
) -> bool:
    """添加收藏变量。

    如果 children 非空，表示该收藏项是一个打包了子变量的父级节点，
    主页将以单个卡片展示，卡片内可展开查看层级结构。
    """
    favorites = load_favorites()
    for fav in favorites:
        if fav["path"] == variable_path and fav["source_file"] == source_file:
            fav["note"] = note
            if children is not None:
                fav["children"] = children
            save_favorites(favorites)
            return True
    favorites.append(
        {
            "path": variable_path,
            "label": variable_label,
            "value": variable_value,
            "source_file": source_file,
            "note": note,
            "children": children or [],
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    save_favorites(favorites)
    return True


def _collect_fav_paths(entry: dict) -> set:
    """收集一个收藏条目覆盖的所有路径（含自身和子节点）"""
    paths = {entry["path"]}
    for child in entry.get("children", []):
        paths |= _collect_fav_paths(child)
    return paths


def find_parent_favorite(path: str, source_file: str) -> Optional[str]:
    """查找覆盖该路径的父级收藏的路径（用于判断节点是否被父级收藏包含）"""
    favorites = load_favorites()
    for fav in favorites:
        if fav["source_file"] != source_file:
            continue
        if fav["path"] == path:
            continue  # 自身不算
        covered = _collect_fav_paths(fav)
        if path in covered:
            return fav["path"]
    return None


def remove_favorite(variable_path: str, source_file: str) -> bool:
    """移除收藏变量"""
    favorites = load_favorites()
    new_favs = [
        f
        for f in favorites
        if not (f["path"] == variable_path and f["source_file"] == source_file)
    ]
    save_favorites(new_favs)
    return len(new_favs) < len(favorites)


def update_favorite_note(variable_path: str, source_file: str, note: str) -> bool:
    """更新收藏变量备注"""
    favorites = load_favorites()
    for fav in favorites:
        if fav["path"] == variable_path and fav["source_file"] == source_file:
            fav["note"] = note
            save_favorites(favorites)
            return True
    return False


# ===================== 变量绑定关系 =====================

BINDINGS_FILE = os.path.join(DATA_DIR, "bindings.json")


def load_bindings() -> List[Dict[str, Any]]:
    """加载变量绑定关系"""
    return _load_json(BINDINGS_FILE, [])


def save_bindings(bindings: List[Dict[str, Any]]):
    """保存变量绑定关系"""
    _save_json(BINDINGS_FILE, bindings)


def add_binding(group_name: str, variables: List[Dict[str, str]]) -> bool:
    """添加或更新变量绑定组

    variables: [{"file": str, "path": str}]
    """
    bindings = load_bindings()
    for b in bindings:
        if b["group_name"] == group_name:
            b["variables"] = variables
            save_bindings(bindings)
            return True
    bindings.append({"group_name": group_name, "variables": variables})
    save_bindings(bindings)
    return True


def remove_binding(group_name: str) -> bool:
    """删除变量绑定组"""
    bindings = load_bindings()
    new_bindings = [b for b in bindings if b["group_name"] != group_name]
    save_bindings(new_bindings)
    return len(new_bindings) < len(bindings)


def get_bound_paths() -> Dict[str, str]:
    """获取所有绑定路径到组名的映射，用于对比时标注

    Returns: {file_path: group_name}
    """
    bindings = load_bindings()
    result = {}
    for b in bindings:
        for v in b["variables"]:
            key = f"{v.get('file', '')}:{v.get('path', '')}"
            result[key] = b["group_name"]
    return result


# ===================== 工具菜单配置 =====================

TOOLS_FILE = os.path.join(DATA_DIR, "tools.json")


def load_tools() -> List[Dict[str, str]]:
    """加载工具菜单配置"""
    return _load_json(TOOLS_FILE, [])


def save_tools(tools: List[Dict[str, str]]):
    """保存工具菜单配置"""
    _save_json(TOOLS_FILE, tools)


def add_tool(name: str, description: str, url: str) -> bool:
    """添加工具"""
    tools = load_tools()
    tools.append(
        {
            "name": name,
            "description": description,
            "url": url,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    save_tools(tools)
    return True


def remove_tool(name: str) -> bool:
    """删除工具"""
    tools = load_tools()
    new_tools = [t for t in tools if t["name"] != name]
    save_tools(new_tools)
    return len(new_tools) < len(tools)


def update_tool(original_name: str, name: str, description: str, url: str) -> bool:
    """更新工具配置"""
    tools = load_tools()
    for t in tools:
        if t["name"] == original_name:
            t["name"] = name
            t["description"] = description
            t["url"] = url
            save_tools(tools)
            return True
    return False


# ===================== 调度配置 =====================

SCHEDULE_FILE = os.path.join(DATA_DIR, "schedule.json")


def load_schedule() -> Dict[str, Any]:
    """加载调度配置"""
    return _load_json(SCHEDULE_FILE, {"enabled": False, "interval_hours": 24})


def save_schedule(schedule: Dict[str, Any]):
    """保存调度配置"""
    _save_json(SCHEDULE_FILE, schedule)
