"""数据持久化层

管理所有持久化数据，包括配置映射、收藏、变量绑定、工具配置等。
服务端数据与客户端会话数据隔离。
"""

import json
import os
import shutil
import logging
import contextvars
import urllib.parse
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")

CONFIGS_DIR = os.path.join(DATA_DIR, "configs")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
PARSE_CACHE_DIR = os.path.join(CACHE_DIR, "parse_tree")

DEFAULT_PROFILE = "default"
_profile_var: contextvars.ContextVar[str] = contextvars.ContextVar("mc_profile", default=DEFAULT_PROFILE)


def _normalize_profile_id(profile_id: Optional[str]) -> str:
    if not profile_id:
        return DEFAULT_PROFILE
    pid = str(profile_id).strip()
    if not pid:
        return DEFAULT_PROFILE
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if any(ch not in allowed for ch in pid):
        return DEFAULT_PROFILE
    if pid in (".", ".."):
        return DEFAULT_PROFILE
    return pid


def set_active_profile(profile_id: Optional[str]) -> None:
    _profile_var.set(_normalize_profile_id(profile_id))


def get_active_profile() -> str:
    pid_ctx = _normalize_profile_id(_profile_var.get())
    if pid_ctx != DEFAULT_PROFILE:
        return pid_ctx
    try:
        from nicegui import app
        pid = app.storage.user.get("device_model")
        if pid:
            return _normalize_profile_id(pid)
    except Exception:
        pass
    return pid_ctx


@contextmanager
def use_profile(profile_id: Optional[str]):
    token = _profile_var.set(_normalize_profile_id(profile_id))
    try:
        yield
    finally:
        _profile_var.reset(token)


def _profile_base_dir(profile_id: Optional[str] = None) -> str:
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    return os.path.join(PROFILES_DIR, pid)


def get_configs_dir(profile_id: Optional[str] = None) -> str:
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    target = os.path.join(_profile_base_dir(pid), "configs")
    if pid == DEFAULT_PROFILE and not os.path.exists(target) and os.path.exists(CONFIGS_DIR):
        return CONFIGS_DIR
    return target


def get_archive_root_dir(profile_id: Optional[str] = None) -> str:
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    target = os.path.join(_profile_base_dir(pid), "archive")
    if pid == DEFAULT_PROFILE and not os.path.exists(target) and os.path.exists(ARCHIVE_DIR):
        return ARCHIVE_DIR
    return target


def get_cache_root_dir(profile_id: Optional[str] = None) -> str:
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    target = os.path.join(_profile_base_dir(pid), "cache")
    if pid == DEFAULT_PROFILE and not os.path.exists(target) and os.path.exists(CACHE_DIR):
        return CACHE_DIR
    return target


def get_parse_cache_dir(profile_id: Optional[str] = None) -> str:
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    target = os.path.join(get_cache_root_dir(pid), "parse_tree")
    if pid == DEFAULT_PROFILE and not os.path.exists(target) and os.path.exists(PARSE_CACHE_DIR):
        return PARSE_CACHE_DIR
    return target


def _get_profile_json_file(basename: str, profile_id: Optional[str] = None) -> str:
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    return os.path.join(_profile_base_dir(pid), basename)


def get_profile_data_dir(profile_id: Optional[str] = None) -> str:
    return _profile_base_dir(profile_id)


def _ensure_dirs():
    """确保数据目录存在"""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    os.makedirs(get_configs_dir(), exist_ok=True)
    os.makedirs(get_archive_root_dir(), exist_ok=True)
    os.makedirs(get_records_root_dir(), exist_ok=True)
    os.makedirs(get_parse_cache_dir(), exist_ok=True)


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
LEGACY_MAPPING_FILE = MAPPING_FILE


def ensure_profile_layout(migrate_legacy: bool = True) -> None:
    os.makedirs(PROFILES_DIR, exist_ok=True)
    default_base = _profile_base_dir(DEFAULT_PROFILE)
    os.makedirs(default_base, exist_ok=True)
    if not migrate_legacy:
        return

    legacy_to_new = [
        (os.path.join(DATA_DIR, "config_mapping.json"), os.path.join(default_base, "config_mapping.json")),
        (os.path.join(DATA_DIR, "favorites.json"), os.path.join(default_base, "favorites.json")),
        (os.path.join(DATA_DIR, "bindings.json"), os.path.join(default_base, "bindings.json")),
        (os.path.join(DATA_DIR, "tools.json"), os.path.join(default_base, "tools.json")),
        (os.path.join(DATA_DIR, "schedule.json"), os.path.join(default_base, "schedule.json")),
        (os.path.join(DATA_DIR, "dltool_config.json"), os.path.join(default_base, "dltool_config.json")),
        (os.path.join(DATA_DIR, "configs"), os.path.join(default_base, "configs")),
        (os.path.join(DATA_DIR, "archive"), os.path.join(default_base, "archive")),
        (os.path.join(DATA_DIR, "cache"), os.path.join(default_base, "cache")),
    ]

    for legacy_path, new_path in legacy_to_new:
        if not os.path.exists(legacy_path):
            continue
        if os.path.exists(new_path):
            continue
        try:
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            shutil.move(legacy_path, new_path)
        except Exception as e:
            logger.warning("迁移默认机型数据失败 %s -> %s: %s", legacy_path, new_path, e)


def load_config_mapping() -> List[Dict[str, str]]:
    """加载配置映射表"""
    profile = get_active_profile()
    path = _get_profile_json_file("config_mapping.json", profile)
    if not os.path.exists(path) and profile == DEFAULT_PROFILE and os.path.exists(LEGACY_MAPPING_FILE):
        return _load_json(LEGACY_MAPPING_FILE, [])
    return _load_json(path, [])


def save_config_mapping(mapping: List[Dict[str, str]]):
    """保存配置映射表"""
    profile = get_active_profile()
    _save_json(_get_profile_json_file("config_mapping.json", profile), mapping)


def add_config_mapping(name: str, url: str) -> bool:
    """添加一条映射"""
    mapping = load_config_mapping()
    for item in mapping:
        if item["name"] == name:
            item["url"] = url
            save_config_mapping(mapping)
            return True
    mapping.append({"name": name, "url": url, "record_url": ""})
    save_config_mapping(mapping)
    return True


def remove_config_mapping(name: str) -> bool:
    """删除一条映射"""
    mapping = load_config_mapping()
    new_mapping = [item for item in mapping if item["name"] != name]
    save_config_mapping(new_mapping)
    return len(new_mapping) < len(mapping)


def _sanitize_config_filename(name: str) -> str:
    n = (name or "").strip()
    if not n:
        raise ValueError("文件名不能为空")
    if any(sep in n for sep in ("/", "\\", ":", "\0")):
        raise ValueError("文件名不合法")
    if n in (".", ".."):
        raise ValueError("文件名不合法")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if any(ch not in allowed for ch in n):
        raise ValueError("文件名包含非法字符")
    return n


def update_config_mapping(
    original_name: str,
    new_name: str,
    new_url: str,
    *,
    migrate_files: bool = True,
    new_record_url: Optional[str] = None,
) -> bool:
    original = _sanitize_config_filename(original_name)
    target = _sanitize_config_filename(new_name)
    url = (new_url or "").strip()
    if not url:
        raise ValueError("下载链接不能为空")

    mapping = load_config_mapping()
    found = None
    for item in mapping:
        if item.get("name") == original:
            found = item
            break
    if found is None:
        return False

    if target != original and any(item.get("name") == target for item in mapping):
        raise ValueError("目标文件名已存在")

    if new_record_url is not None:
        found["record_url"] = (new_record_url or "").strip()

    found["name"] = target
    found["url"] = url
    save_config_mapping(mapping)

    if migrate_files and target != original:
        _ensure_dirs()

        old_file = get_config_path(original)
        new_file = get_config_path(target)
        if os.path.exists(old_file) and not os.path.exists(new_file):
            os.makedirs(os.path.dirname(new_file), exist_ok=True)
            shutil.move(old_file, new_file)

        old_archive_dir = os.path.join(get_archive_root_dir(), original)
        new_archive_dir = os.path.join(get_archive_root_dir(), target)
        if os.path.exists(old_archive_dir) and not os.path.exists(new_archive_dir):
            os.makedirs(os.path.dirname(new_archive_dir), exist_ok=True)
            shutil.move(old_archive_dir, new_archive_dir)

        try:
            from . import parse_cache
            parse_cache.invalidate(old_file)
            parse_cache.invalidate(new_file)
        except Exception:
            pass

        favorites = load_favorites()
        changed = False
        for fav in favorites:
            if fav.get("source_file") == original:
                fav["source_file"] = target
                changed = True
        if changed:
            save_favorites(favorites)

        bindings = load_bindings()
        b_changed = False
        for b in bindings:
            for v in b.get("variables", []):
                if v.get("file") == original:
                    v["file"] = target
                    b_changed = True
        if b_changed:
            save_bindings(bindings)

    return True


def update_config_record_url(name: str, record_url: str) -> bool:
    target = _sanitize_config_filename(name)
    mapping = load_config_mapping()
    for item in mapping:
        if item.get("name") == target:
            item["record_url"] = (record_url or "").strip()
            save_config_mapping(mapping)
            return True
    return False


def delete_config_mapping(name: str, *, delete_files: bool = False) -> bool:
    target = _sanitize_config_filename(name)
    removed = remove_config_mapping(target)
    if not delete_files:
        return removed

    _ensure_dirs()
    file_path = get_config_path(target)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError:
            pass

    archive_dir = os.path.join(get_archive_root_dir(), target)
    if os.path.exists(archive_dir):
        try:
            shutil.rmtree(archive_dir, ignore_errors=True)
        except Exception:
            pass

    try:
        from . import parse_cache
        parse_cache.invalidate(file_path)
    except Exception:
        pass

    favorites = load_favorites()
    new_favs = [f for f in favorites if f.get("source_file") != target]
    if len(new_favs) != len(favorites):
        save_favorites(new_favs)

    bindings = load_bindings()
    for b in bindings:
        b["variables"] = [v for v in b.get("variables", []) if v.get("file") != target]
    bindings = [b for b in bindings if b.get("variables")]
    save_bindings(bindings)

    return removed


# ===================== 修改记录文件存储 =====================


def get_records_root_dir(profile_id: Optional[str] = None) -> str:
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    return os.path.join(_profile_base_dir(pid), "records")


def get_record_dir(name: str) -> str:
    target = _sanitize_config_filename(name)
    d = os.path.join(get_records_root_dir(), target)
    os.makedirs(d, exist_ok=True)
    return d


def get_record_path(name: str, record_filename: str) -> str:
    target = _sanitize_config_filename(name)
    rf = _sanitize_config_filename(record_filename)
    return os.path.join(get_record_dir(target), rf)


def save_record_file(name: str, content: bytes, *, source_url: str | None = None) -> str:
    _ensure_dirs()
    target = _sanitize_config_filename(name)
    ext = ".txt"
    if source_url:
        try:
            p = urllib.parse.urlparse(source_url)
            ext2 = os.path.splitext(p.path or "")[1]
            if ext2 and len(ext2) <= 10 and all(ch.isalnum() or ch == "." for ch in ext2):
                ext = ext2
        except Exception:
            pass
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    record_filename = f"record_{ts}{ext}"
    record_path = os.path.join(get_record_dir(target), record_filename)
    with open(record_path, "wb") as f:
        f.write(content)
    logger.info("保存修改记录文件: %s -> %s", target, record_path)
    return record_path


def list_record_versions(name: str) -> List[Dict[str, Any]]:
    target = _sanitize_config_filename(name)
    record_dir = get_record_dir(target)
    if not os.path.exists(record_dir):
        return []
    versions = []
    for f in sorted(os.listdir(record_dir), reverse=True):
        fpath = os.path.join(record_dir, f)
        if os.path.isfile(fpath):
            mtime = os.path.getmtime(fpath)
            versions.append(
                {
                    "filename": f,
                    "date": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "size": os.path.getsize(fpath),
                }
            )
    versions.sort(key=lambda x: x.get("date") or "", reverse=True)
    return versions


def load_record_file(name: str, record_filename: str) -> Optional[bytes]:
    path = get_record_path(name, record_filename)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


# ===================== 配置文件存储 =====================


def get_config_path(name: str) -> str:
    """获取配置文件路径"""
    return os.path.join(get_configs_dir(), name)


def get_archive_dir(name: str) -> str:
    """获取某个配置文件的归档目录"""
    d = os.path.join(get_archive_root_dir(), name)
    os.makedirs(d, exist_ok=True)
    return d


def _get_archive_diff_path(name: str, archive_filename: str) -> str:
    return os.path.join(get_archive_dir(name), f"{archive_filename}.diff.json")


def load_archive_diff(name: str, archive_filename: str) -> Optional[Dict[str, Any]]:
    path = _get_archive_diff_path(name, archive_filename)
    if not os.path.exists(path):
        return None
    return _load_json(path, None)


def _save_archive_diff(name: str, archive_filename: str, data: Dict[str, Any]) -> None:
    _save_json(_get_archive_diff_path(name, archive_filename), data)


def ensure_archive_diffs(name: str) -> None:
    archive_dir = get_archive_dir(name)
    if not os.path.exists(archive_dir):
        return

    archives: List[Tuple[str, str, float]] = []
    for f in os.listdir(archive_dir):
        if f.endswith(".diff.json"):
            continue
        fpath = os.path.join(archive_dir, f)
        if os.path.isfile(fpath):
            archives.append((f, fpath, os.path.getmtime(fpath)))

    if not archives:
        return

    archives.sort(key=lambda x: x[2], reverse=True)

    current_content = load_config_file(name)
    if current_content is None:
        return

    try:
        from . import differ
    except Exception:
        return

    for idx, (archive_filename, archive_path, _) in enumerate(archives):
        meta_path = _get_archive_diff_path(name, archive_filename)
        if os.path.exists(meta_path):
            continue

        try:
            with open(archive_path, "rb") as f:
                old_content = f.read()
        except OSError:
            continue

        if idx == 0:
            new_content = current_content
            compared_to = name
        else:
            prev_archive_path = archives[idx - 1][1]
            try:
                with open(prev_archive_path, "rb") as f:
                    new_content = f.read()
            except OSError:
                continue
            compared_to = archives[idx - 1][0]

        try:
            result = differ.compare_versions(old_content, new_content, archive_filename, compared_to)
        except Exception:
            continue

        struct = result.get("structural_diff") or {}
        summary = (struct.get("summary") or {}) if isinstance(struct, dict) else {}
        _save_archive_diff(
            name,
            archive_filename,
            {
                "has_changes": bool(result.get("has_changes")),
                "compared_to": compared_to,
                "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "structural_type": struct.get("type"),
                "summary": {
                    "added_count": int(summary.get("added_count", 0) or 0),
                    "removed_count": int(summary.get("removed_count", 0) or 0),
                    "modified_count": int(summary.get("modified_count", 0) or 0),
                    "bound_count": int(summary.get("bound_count", 0) or 0),
                },
            },
        )


def save_config_file(name: str, content: bytes) -> str:
    """保存配置文件，返回保存路径

    如果已有同名文件，将旧版移至归档目录
    """
    _ensure_dirs()
    filepath = get_config_path(name)

    # 归档旧版本
    if os.path.exists(filepath):
        try:
            with open(filepath, "rb") as f:
                old_content = f.read()
        except OSError:
            old_content = None

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

    if os.path.exists(filepath) and "old_content" in locals() and old_content is not None:
        try:
            from . import differ
            result = differ.compare_versions(old_content, content, archive_name, name)
            struct = result.get("structural_diff") or {}
            summary = (struct.get("summary") or {}) if isinstance(struct, dict) else {}
            _save_archive_diff(
                name,
                archive_name,
                {
                    "has_changes": bool(result.get("has_changes")),
                    "compared_to": name,
                    "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "structural_type": struct.get("type"),
                    "summary": {
                        "added_count": int(summary.get("added_count", 0) or 0),
                        "removed_count": int(summary.get("removed_count", 0) or 0),
                        "modified_count": int(summary.get("modified_count", 0) or 0),
                        "bound_count": int(summary.get("bound_count", 0) or 0),
                    },
                },
            )
        except Exception:
            pass

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
    configs_dir = get_configs_dir()
    for f in os.listdir(configs_dir):
        if os.path.isfile(os.path.join(configs_dir, f)):
            files.append(f)
    return sorted(files)


def list_archived_versions(name: str) -> List[Dict[str, Any]]:
    """列出某个配置文件的所有归档版本"""
    archive_dir = get_archive_dir(name)
    versions = []
    for f in sorted(os.listdir(archive_dir), reverse=True):
        if f.endswith(".diff.json"):
            continue
        fpath = os.path.join(archive_dir, f)
        if os.path.isfile(fpath):
            mtime = os.path.getmtime(fpath)
            diff = load_archive_diff(name, f)
            versions.append(
                {
                    "filename": f,
                    "date": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "size": os.path.getsize(fpath),
                    "diff": diff,
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
LEGACY_FAVORITES_FILE = FAVORITES_FILE


def get_favorites_file(profile_id: Optional[str] = None) -> str:
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    return _get_profile_json_file("favorites.json", pid)


def load_favorites() -> List[Dict[str, Any]]:
    """加载收藏变量列表"""
    profile = get_active_profile()
    path = _get_profile_json_file("favorites.json", profile)
    if not os.path.exists(path) and profile == DEFAULT_PROFILE and os.path.exists(LEGACY_FAVORITES_FILE):
        return _load_json(LEGACY_FAVORITES_FILE, [])
    return _load_json(path, [])


def save_favorites(favorites: List[Dict[str, Any]]):
    """保存收藏变量列表"""
    _save_json(_get_profile_json_file("favorites.json", get_active_profile()), favorites)


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
LEGACY_BINDINGS_FILE = BINDINGS_FILE


def get_bindings_file(profile_id: Optional[str] = None) -> str:
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    return _get_profile_json_file("bindings.json", pid)


def load_bindings() -> List[Dict[str, Any]]:
    """加载变量绑定关系"""
    profile = get_active_profile()
    path = _get_profile_json_file("bindings.json", profile)
    if not os.path.exists(path) and profile == DEFAULT_PROFILE and os.path.exists(LEGACY_BINDINGS_FILE):
        return _load_json(LEGACY_BINDINGS_FILE, [])
    return _load_json(path, [])


def save_bindings(bindings: List[Dict[str, Any]]):
    """保存变量绑定关系"""
    _save_json(_get_profile_json_file("bindings.json", get_active_profile()), bindings)


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
LEGACY_TOOLS_FILE = TOOLS_FILE


def get_tools_file(profile_id: Optional[str] = None) -> str:
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    return _get_profile_json_file("tools.json", pid)


def load_tools() -> List[Dict[str, str]]:
    """加载工具菜单配置"""
    profile = get_active_profile()
    path = _get_profile_json_file("tools.json", profile)
    if not os.path.exists(path) and profile == DEFAULT_PROFILE and os.path.exists(LEGACY_TOOLS_FILE):
        return _load_json(LEGACY_TOOLS_FILE, [])
    return _load_json(path, [])


def save_tools(tools: List[Dict[str, str]]):
    """保存工具菜单配置"""
    _save_json(_get_profile_json_file("tools.json", get_active_profile()), tools)


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
LEGACY_SCHEDULE_FILE = SCHEDULE_FILE


def get_schedule_file(profile_id: Optional[str] = None) -> str:
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    return _get_profile_json_file("schedule.json", pid)


def load_schedule() -> Dict[str, Any]:
    """加载调度配置"""
    profile = get_active_profile()
    path = _get_profile_json_file("schedule.json", profile)
    if not os.path.exists(path) and profile == DEFAULT_PROFILE and os.path.exists(LEGACY_SCHEDULE_FILE):
        return _load_json(LEGACY_SCHEDULE_FILE, {"enabled": False, "interval_hours": 24})
    return _load_json(path, {"enabled": False, "interval_hours": 24})


def save_schedule(schedule: Dict[str, Any]):
    """保存调度配置"""
    _save_json(_get_profile_json_file("schedule.json", get_active_profile()), schedule)


def copy_profile_data(source_profile: str, target_profile: str) -> None:
    src = _normalize_profile_id(source_profile)
    dst = _normalize_profile_id(target_profile)
    if dst == DEFAULT_PROFILE:
        raise ValueError("target_profile cannot be default")

    dst_base = _profile_base_dir(dst)
    os.makedirs(dst_base, exist_ok=True)

    src_mapping = _get_profile_json_file("config_mapping.json", src)
    if src == DEFAULT_PROFILE and not os.path.exists(src_mapping) and os.path.exists(LEGACY_MAPPING_FILE):
        src_mapping = LEGACY_MAPPING_FILE

    src_favs = _get_profile_json_file("favorites.json", src)
    if src == DEFAULT_PROFILE and not os.path.exists(src_favs) and os.path.exists(LEGACY_FAVORITES_FILE):
        src_favs = LEGACY_FAVORITES_FILE

    src_bindings = _get_profile_json_file("bindings.json", src)
    if src == DEFAULT_PROFILE and not os.path.exists(src_bindings) and os.path.exists(LEGACY_BINDINGS_FILE):
        src_bindings = LEGACY_BINDINGS_FILE

    src_tools = _get_profile_json_file("tools.json", src)
    if src == DEFAULT_PROFILE and not os.path.exists(src_tools) and os.path.exists(LEGACY_TOOLS_FILE):
        src_tools = LEGACY_TOOLS_FILE

    src_schedule = _get_profile_json_file("schedule.json", src)
    if src == DEFAULT_PROFILE and not os.path.exists(src_schedule) and os.path.exists(LEGACY_SCHEDULE_FILE):
        src_schedule = LEGACY_SCHEDULE_FILE

    src_files = {
        "config_mapping.json": src_mapping,
        "favorites.json": src_favs,
        "bindings.json": src_bindings,
        "tools.json": src_tools,
        "schedule.json": src_schedule,
        "dltool_config.json": os.path.join(_profile_base_dir(src), "dltool_config.json"),
    }

    for basename, src_path in src_files.items():
        if os.path.exists(src_path):
            shutil.copy2(src_path, os.path.join(dst_base, basename))

    src_dirs = {
        "configs": get_configs_dir(src),
        "archive": get_archive_root_dir(src),
        "cache": get_cache_root_dir(src),
    }
    for name, src_dir in src_dirs.items():
        if os.path.exists(src_dir):
            shutil.copytree(src_dir, os.path.join(dst_base, name), dirs_exist_ok=True)


def delete_profile_data(profile_id: str) -> None:
    pid = _normalize_profile_id(profile_id)
    if pid == DEFAULT_PROFILE:
        raise ValueError("cannot delete default profile data")
    base = _profile_base_dir(pid)
    if os.path.exists(base):
        shutil.rmtree(base, ignore_errors=True)
