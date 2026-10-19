"""数据持久化层

管理所有持久化数据，包括配置映射、收藏、变量绑定、工具配置等。
服务端数据与客户端会话数据隔离。

本模块是“兼容门面”（T-03、§5.4）：对外函数签名保持不变，内部统一走
`core.fsutil` 的原子写与 `core.locks` 的机型锁。三条硬规则：

* 权威 JSON 损坏 → `CorruptDataError` 向上抛，绝不退回空列表后再覆盖；
* 任何“读-改-写”都在 `profile_lock` 内一气呵成，且经过写闸门；
* 新建文件（归档、修改记录）先用独占创建拿到唯一名，再原子写入。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import contextvars
import urllib.parse
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, Iterable, Iterator, List, NamedTuple, Optional, Tuple

from . import fsutil
from .errors import FILE_EXISTS, NOT_FOUND, STORAGE_FAILURE, AppError, CorruptDataError
from .locks import profile_lock, write_gate

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")

CONFIGS_DIR = os.path.join(DATA_DIR, "configs")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
PARSE_CACHE_DIR = os.path.join(CACHE_DIR, "parse_tree")

DEFAULT_PROFILE = "default"
#: `secrets.token_urlsafe` 的字符集；同时用来挡住把 token 当路径用的穿越尝试。
_TEMP_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_profile_var: contextvars.ContextVar[str] = contextvars.ContextVar("mc_profile", default=DEFAULT_PROFILE)

#: 机型 id 的合法字符（规范 §5.2 `[A-Za-z0-9_-]+`）。
_PROFILE_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


def _normalize_profile_id(profile_id: Optional[str]) -> str:
    """规范化机型 id（规范 §5.2、C26）。

    `None` 或纯空白表示"未指定"，落到 `default`——这是内部"用当前上下文"的
    常规写法，不算非法。

    显式传入的非空 id 必须匹配 `[A-Za-z0-9_-]+` 且不是 `.` / `..`。非法时抛
    `AppError(NOT_FOUND)`，**不再**静默回退 `default`：否则 `/p/<非法>/files`
    会读到 default 的数据，等于越权。调用方（HTTP 层）应先校验 exists；只有
    `GET /session` 允许回落 default（那个例外在 M1-03 实现）。
    """
    if profile_id is None:
        return DEFAULT_PROFILE
    pid = str(profile_id).strip()
    if not pid:
        return DEFAULT_PROFILE
    if pid in (".", "..") or any(ch not in _PROFILE_ID_CHARS for ch in pid):
        raise AppError(NOT_FOUND, "机型不存在", profile=pid)
    return pid


def set_active_profile(profile_id: Optional[str]) -> None:
    _profile_var.set(_normalize_profile_id(profile_id))


def get_active_profile() -> str:
    """当前机型（配置空间）。

    只认 `set_active_profile()` / `use_profile()` 写入的上下文变量。原先会
    回落到 NiceGUI 的 `app.storage.user["device_model"]`，那是把服务端数据与
    浏览器会话绑在一起的隐式耦合（C28），已删除：调用方必须显式设置。
    """
    return _normalize_profile_id(_profile_var.get())


@contextmanager
def use_profile(profile_id: Optional[str]):
    token = _profile_var.set(_normalize_profile_id(profile_id))
    try:
        yield
    finally:
        _profile_var.reset(token)


def configure_data_dir(data_dir: str) -> str:
    """把数据根切到 `data_dir`（§3.6 的 `--data-dir` / `MCHECKER_DATA_DIR`）。

    必须在导入 `dltool`、`device_models` 等「按数据根派生路径」的模块**之前**
    调用（`run.py` 的初始化顺序保证了这一点），否则它们已固化的常量不会跟着走。

    实现与 `tests/conftest.py` 的隔离夹具一致：整体改写所有以旧数据根开头的
    模块级路径常量，今后新增 `XXX_FILE` 无需回来维护清单。返回新的数据根。
    """
    global DATA_DIR

    new_root = os.path.abspath(os.path.expanduser(data_dir))
    old_root = DATA_DIR
    if new_root == old_root:
        return DATA_DIR

    for name, value in list(globals().items()):
        if not isinstance(value, str):
            continue
        if value == old_root:
            globals()[name] = new_root
        elif value.startswith(old_root + os.sep):
            globals()[name] = new_root + value[len(old_root):]
    DATA_DIR = new_root
    return DATA_DIR


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


def get_temporary_dir(token: Optional[str] = None) -> str:
    """临时文件区 `data/temporary/<token>/`（§7.4）。

    **运行期取 `DATA_DIR`**，不做 import 期常量：测试夹具靠重绑定 `DATA_DIR` 隔离数据根，
    常量会绕开它写到真实目录；临时区也不属于权威数据，不参与机型目录布局。
    """
    path = os.path.join(DATA_DIR, "temporary")
    if token is not None:
        path = os.path.join(path, validate_temp_token(token))
    return path


def get_recovery_root_dir() -> str:
    """恢复区根目录 `data/recovery/`（§5.6）。

    同样**运行期取 `DATA_DIR`**。恢复区不分机型——条目目录名里带 pid 与操作名，
    只做移入、不做自动清理，也不参与机型布局与备份。
    """
    return os.path.join(DATA_DIR, "recovery")


def validate_temp_token(token: Any) -> str:
    """token 只允许 `secrets.token_urlsafe` 的字符集，防止路径穿越。"""
    text = str(token or "")
    if not _TEMP_TOKEN_RE.match(text):
        raise ValueError("临时文件标识不合法")
    return text


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
    """确保数据目录存在。

    **只在写路径调用**（B10.2）：它做的是 `makedirs`，读路径调它就是在改用户的
    数据根。现网的分工是——

    * 机型根目录（`profiles/`、`profiles/default/`）由启动流程
      `ensure_profile_layout()`（`run.py` 在启动时调用）与复制机型时的
      `_copy_profile_data_locked()` 建；
    * 业务子目录（`configs/`、`archive/`、`records/`、`cache/parse_tree/`）由
      **第一次需要它们的写**建，也就是本函数的四个调用点：改名迁移
      （`_rename_config_entry_locked`）、存修改记录（`save_record_file`）、存配置
      （`save_config_file_detailed`）、存审阅生成文件
      （`save_review_generated_file`，它走 `_reserve_file` 的裸 `os.open(O_EXCL)`，
      目录不在就先炸，属于「写确实需要」而不是防御性调用）。

    读路径一律不调它：目录缺失对读来说就是「还没有数据」，返回空结果即可
    （见 `list_config_files`）。「只读接口不改数据目录」现在由 H3 守卫的目录集合
    快照执法，见 `tests/data_guard.py`。
    """
    os.makedirs(PROFILES_DIR, exist_ok=True)
    os.makedirs(get_configs_dir(), exist_ok=True)
    os.makedirs(get_archive_root_dir(), exist_ok=True)
    os.makedirs(get_records_root_dir(), exist_ok=True)
    os.makedirs(get_parse_cache_dir(), exist_ok=True)


def _load_json(filepath: str, default: Any = None) -> Any:
    """加载 JSON 文件。

    文件不存在 → 返回 `default`；存在但无法解析 → `CorruptDataError` 向上抛。
    这里**不能**把损坏文件退化成空列表：那会让下一次保存把权威数据清空。
    调用方若处理的是可重建的派生数据（如归档 diff 缓存），自行捕获。
    """
    return fsutil.read_json(filepath, default)


def _save_json(filepath: str, data: Any) -> None:
    """保存 JSON 文件（原子写）。"""
    fsutil.atomic_write_json(filepath, data)


@contextmanager
def _profile_write(profile_id: Optional[str] = None) -> Iterator[None]:
    """进入写闸门并持有 `profile_id` 的机型锁（规范 §3.5、§5.4）。

    所有“读-改-写”都必须包在这个上下文里，保证同一机型的并发修改串行化；
    维护态（备份/恢复）期间 `write_gate` 直接抛 `AppError(MAINTENANCE)`。
    锁内禁止做任何网络 I/O。
    """
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    with write_gate.write():
        with profile_lock(pid):
            yield


@contextmanager
def profile_write(profile_id: Optional[str] = None) -> Iterator[None]:
    """`_profile_write` 的公开入口。

    service 层需要**跨多次存储调用保持同一把机型锁**时才用它（§5.6 的删除：先把原文
    移进恢复区、再改四份 JSON，中间不能插入别的写）。单份读-改-写请继续用各 `save_*`，
    它们自带锁。
    """
    with _profile_write(profile_id):
        yield


@contextmanager
def _reserve_file(directory: str, candidates: Iterable[str]) -> Iterator[str]:
    """按 §5.3 的顺序用独占创建（`O_CREAT | O_EXCL`）拿到第一个未被占用的名字。

    先占位再写入：占位成功即代表这个名字归本次调用所有，后续原子写只会替换
    占位文件。写入失败时删除占位，避免留下 0 字节的幽灵条目。
    """
    for candidate in candidates:
        target = os.path.join(directory, candidate)
        try:
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        os.close(fd)
        try:
            yield target
        except BaseException:
            try:
                os.unlink(target)
            except OSError:
                pass
            raise
        return
    raise AppError(STORAGE_FAILURE, "无法为文件生成唯一名")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ===================== 全量配置映射表 =====================

MAPPING_FILE = os.path.join(DATA_DIR, "config_mapping.json")
LEGACY_MAPPING_FILE = MAPPING_FILE


def ensure_profile_layout(migrate_legacy: bool = True) -> None:
    with _profile_write(DEFAULT_PROFILE):
        _ensure_profile_layout_locked(migrate_legacy)


def _ensure_profile_layout_locked(migrate_legacy: bool) -> None:
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
        (os.path.join(DATA_DIR, "ip_mapping.json"), os.path.join(default_base, "ip_mapping.json")),
        (os.path.join(DATA_DIR, "admin_users.json"), os.path.join(default_base, "admin_users.json")),
        (os.path.join(DATA_DIR, "edit_remarks.json"), os.path.join(default_base, "edit_remarks.json")),
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
    with _profile_write(profile):
        _save_json(_get_profile_json_file("config_mapping.json", profile), mapping)


def add_config_mapping(name: str, url: str) -> bool:
    """添加一条映射"""
    with _profile_write():
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
    with _profile_write():
        mapping = load_config_mapping()
        new_mapping = [item for item in mapping if item["name"] != name]
        save_config_mapping(new_mapping)
        return len(new_mapping) < len(mapping)


def _sanitize_config_filename(name: str) -> str:
    """文件名校验**一律委托 `fsutil.validate_filename`**（§11.2、C26）。

    M0-07 落这层时用的是本地实现的 `A-Za-z0-9-_.` ASCII 白名单，而「新建更新源」走的是
    `fsutil`（允许中文），两套字符集让中文源「建得出来却永远改不了名」（N1 / INH-1912）。
    收敛只统一字符集，不放松边界：`fsutil.ILLEGAL_CHARS`（路径分隔符与 Windows 非法字符）、
    控制字符、`.` / `..`、保留设备名与超长名仍然非法，所以 `configs/` 与 `records/`
    下的拼法照旧安全。不合法抛 `AppError(INVALID_INPUT)`（不再是裸 `ValueError`），
    HTTP 层照旧翻 400。
    """
    return fsutil.validate_filename(name)


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

    with _profile_write():
        return _update_config_mapping_locked(
            original,
            target,
            url,
            new_record_url=new_record_url,
            migrate_files=migrate_files,
        )


def _update_config_mapping_locked(
    original: str,
    target: str,
    url: str,
    *,
    new_record_url: Optional[str],
    migrate_files: bool,
) -> bool:
    """改名的实际执行体，调用方已持有机型锁。

    改名涉及多处数据与文件：映射表、当前文件、归档目录、修改记录目录、以及
    收藏/绑定/建议/DL 绑定里的文件名引用。任一步失败都要**整体回滚**——半改
    名状态比改名失败难修得多。回滚用逆序 undo 栈实现。
    """
    mapping = load_config_mapping()
    found = None
    for item in mapping:
        if item.get("name") == original:
            found = item
            break
    if found is None:
        return False

    if target != original and any(item.get("name") == target for item in mapping):
        raise AppError(FILE_EXISTS, "目标文件名已存在", name=target)

    undo: List[Any] = []
    try:
        original_mapping = deepcopy(mapping)
        if new_record_url is not None:
            found["record_url"] = (new_record_url or "").strip()

        found["name"] = target
        found["url"] = url
        save_config_mapping(mapping)
        undo.append(("json", save_config_mapping, original_mapping))

        if migrate_files and target != original:
            _ensure_dirs()
            _migrate_config_artifacts(original, target, undo)
            _migrate_config_references(original, target, undo)

        return True
    except BaseException:
        _rollback(undo)
        raise


def _rollback(undo: List[Any]) -> None:
    """逆序撤销已完成的步骤。回滚本身出错只记日志，不能盖掉原始异常。"""
    for entry in reversed(undo):
        try:
            if entry[0] == "move":
                _, src, dst = entry
                if os.path.exists(src) and not os.path.exists(dst):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
            elif entry[0] == "json":
                _, saver, original_data = entry
                saver(deepcopy(original_data))
        except Exception as exc:
            logger.error("改名回滚失败 %r: %s", entry, exc)


def _migrate_config_artifacts(original: str, target: str, undo: List[Any]) -> None:
    """搬走当前文件与两个随文件名走的目录；目标已存在则失败。"""
    old_file = get_config_path(original)
    new_file = get_config_path(target)
    if os.path.exists(new_file):
        raise AppError(FILE_EXISTS, "目标文件已存在", name=target)
    if os.path.exists(old_file):
        os.makedirs(os.path.dirname(new_file), exist_ok=True)
        shutil.move(old_file, new_file)
        undo.append(("move", new_file, old_file))

    for label, old_dir, new_dir in (
        ("归档目录", os.path.join(get_archive_root_dir(), original), os.path.join(get_archive_root_dir(), target)),
        ("修改记录目录", _records_dir_path(original), _records_dir_path(target)),
    ):
        if os.path.exists(new_dir):
            raise AppError(FILE_EXISTS, f"目标{label}已存在", name=target)
        if os.path.exists(old_dir):
            os.makedirs(os.path.dirname(new_dir), exist_ok=True)
            shutil.move(old_dir, new_dir)
            undo.append(("move", new_dir, old_dir))

    try:
        from . import parse_cache

        parse_cache.invalidate(old_file)
        parse_cache.invalidate(new_file)
    except Exception:
        pass


def _migrate_config_references(original: str, target: str, undo: List[Any]) -> None:
    """把收藏、绑定、建议、DL 绑定里的文件名引用从 `original` 改成 `target`。"""
    favorites = load_favorites()
    if any(fav.get("source_file") == original for fav in favorites):
        undo.append(("json", save_favorites, deepcopy(favorites)))
        for fav in favorites:
            if fav.get("source_file") == original:
                fav["source_file"] = target
        save_favorites(favorites)

    bindings = load_bindings()
    if any(v.get("file") == original for b in bindings for v in b.get("variables", [])):
        undo.append(("json", save_bindings, deepcopy(bindings)))
        for b in bindings:
            for v in b.get("variables", []):
                if v.get("file") == original:
                    v["file"] = target
        save_bindings(bindings)

    remarks = load_edit_remarks()
    if any(item.get("source_file") == original for item in remarks):
        undo.append(("json", save_edit_remarks, deepcopy(remarks)))
        for item in remarks:
            if item.get("source_file") == original:
                item["source_file"] = target
        save_edit_remarks(remarks)

    _migrate_dltool_references(original, target, undo)


def _migrate_dltool_references(original: str, target: str, undo: List[Any]) -> None:
    """DL 计算项绑定的 `source_file` 也要跟着改名，否则改名后计算取不到值。"""
    try:
        from . import dltool
    except Exception:
        return

    config = dltool.load_config()
    changed = False
    for item in (config.get("items") or {}).values():
        if not isinstance(item, dict):
            continue
        binding = item.get("binding") or {}
        if binding.get("source_file") == original:
            binding["source_file"] = target
            changed = True
    if changed:
        undo.append(("json", dltool.save_config, deepcopy(config)))
        dltool.save_config(config)


def update_config_record_url(name: str, record_url: str) -> bool:
    target = _sanitize_config_filename(name)
    with _profile_write():
        mapping = load_config_mapping()
        for item in mapping:
            if item.get("name") == target:
                item["record_url"] = (record_url or "").strip()
                save_config_mapping(mapping)
                return True
        return False


def delete_config_mapping(name: str, *, delete_files: bool = False) -> bool:
    target = _sanitize_config_filename(name)
    with _profile_write():
        removed = remove_config_mapping(target)
        if not delete_files:
            return removed
        _delete_config_artifacts(target)
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


def save_record_file(name: str, content: bytes, *, source_url: Optional[str] = None) -> str:
    """保存一份修改记录原文，返回写入路径。

    命名按 §5.3：`record_<YYYYMMDD>_<HHMMSS><ext>`，扩展名取自 `source_url`
    的路径（≤10 个字母数字），取不到则 `.txt`。同名时独占创建重试，依次追加
    `_2`、`_3`…，避免同一秒内的两条记录互相覆盖。
    """
    fsutil.validate_filename(name)
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
    base = f"record_{ts}"
    candidates = [f"{base}{ext}"] + [f"{base}_{index}{ext}" for index in range(2, 1000)]

    with _profile_write():
        with _reserve_file(get_record_dir(target), candidates) as record_path:
            fsutil.atomic_write_bytes(record_path, content)
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
    """读取归档 diff 摘要。

    这是可从归档原文重算的派生数据，因此损坏时按“缺失”处理（只记日志），
    不像权威 JSON 那样向上抛。
    """
    path = _get_archive_diff_path(name, archive_filename)
    if not os.path.exists(path):
        return None
    try:
        return _load_json(path, None)
    except CorruptDataError:
        logger.warning("归档 diff 缓存损坏，按缺失处理: %s", path)
        return None


def _save_archive_diff(name: str, archive_filename: str, data: Dict[str, Any]) -> None:
    _save_json(_get_archive_diff_path(name, archive_filename), data)


def ensure_archive_diffs(name: str) -> None:
    with _profile_write():
        _ensure_archive_diffs_locked(name)


def _ensure_archive_diffs_locked(name: str) -> None:
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


class SaveResult(NamedTuple):
    """`save_config_file_detailed` 的返回值。

    * `path`——当前文件（`configs/<name>`）的绝对路径；
    * `archived`——本次产生的归档文件名，`None` 表示没有归档；
    * `unchanged`——内容与磁盘上的当前文件完全一致，未做任何写入。
    """

    path: str
    archived: Optional[str]
    unchanged: bool


def _archive_candidates(name: str, now: datetime) -> Iterator[str]:
    """归档名的候选序列（规范 §5.3）。

    先 `<stem>_<YYYYMMDD><.ext>`；已存在 → `<stem>_<YYYYMMDD>_<HHMMSS><.ext>`；
    仍存在 → 依次追加 `_2`、`_3`… 旧实现在同一秒内第二次保存会直接覆盖
    前一个归档，历史版本就此丢失，这里用独占创建彻底堵住。
    """
    stamp = now.strftime("%Y%m%d")
    precise = now.strftime("%Y%m%d_%H%M%S")
    yield _add_date_suffix(name, stamp)
    yield _add_date_suffix(name, precise)
    for index in range(2, 1000):
        yield _add_date_suffix(name, f"{precise}_{index}")


def save_config_file(name: str, content: bytes) -> str:
    """保存配置文件，返回保存路径。

    详见 `save_config_file_detailed`——本函数只保留历史签名与返回值。
    """
    return save_config_file_detailed(name, content).path


def save_config_file_detailed(name: str, content: bytes) -> SaveResult:
    """保存配置文件（规范 §5.4 第 2~5 步）。

    流程全部在机型锁内完成：

    1. `validate_filename(name)`；
    2. 当前文件存在且 sha256 相同 → **不归档、不写**，返回 `unchanged=True`；
    3. 内容不同 → 先以**复制**方式把旧内容原子写入归档，成功后再原子替换
       当前文件。归档在前，任一步失败当前文件都保持旧内容；
    4. 计算并写入 diff 摘要缓存（派生数据，失败只记日志）；
    5. 失效该文件的解析缓存。
    """
    fsutil.validate_filename(name)
    _ensure_dirs()
    filepath = get_config_path(name)

    with _profile_write():
        old_content: Optional[bytes] = None
        if os.path.exists(filepath):
            with open(filepath, "rb") as f:
                old_content = f.read()
            if _sha256(old_content) == _sha256(content):
                logger.info("保存配置文件: %s（内容未变化，跳过写入与归档）", name)
                return SaveResult(path=filepath, archived=None, unchanged=True)

        archived: Optional[str] = None
        if old_content is not None:
            archive_dir = get_archive_dir(name)
            with _reserve_file(archive_dir, _archive_candidates(name, datetime.now())) as archive_path:
                fsutil.atomic_write_bytes(archive_path, old_content)
                archived = os.path.basename(archive_path)
            logger.info("归档旧版本: %s -> %s", name, os.path.join(archive_dir, archived or ""))

        fsutil.atomic_write_bytes(filepath, content)
        logger.info("保存配置文件: %s", name)

        if archived is not None:
            # diff 摘要是派生数据：原文已经落盘，它写失败不能让整个保存失败。
            try:
                _write_archive_diff(name, archived, old_content or b"", content)
            except Exception as exc:
                logger.warning("写入归档 diff 失败 %s/%s: %s", name, archived, exc)

        try:
            from . import parse_cache

            parse_cache.invalidate(filepath)
        except Exception:
            pass

    return SaveResult(path=filepath, archived=archived, unchanged=False)


def _write_archive_diff(
    name: str,
    archive_name: str,
    old_content: bytes,
    new_content: bytes,
) -> None:
    """计算并写入归档的 diff 摘要缓存。失败只记日志——它不影响原文。"""
    try:
        from . import differ

        result = differ.compare_versions(old_content, new_content, archive_name, name)
    except Exception as exc:
        logger.warning("计算归档 diff 失败 %s/%s: %s", name, archive_name, exc)
        return

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
    """列出所有已存储的配置文件名。

    **只读**（B10.2）：不再调 `_ensure_dirs()`。`GET /api/v1/p/{pid}/files` 每请求
    都会走到这里，原先它在真实数据根里长出 `archive/ records/ cache/ cache/parse_tree/`
    四个目录——「用只读接口验证数据目录没被动过」于是变成机制上不可验证的事。
    目录不存在就是还没有配置文件，返回空列表，而不是把树长出来。
    目录的创建交给写路径与启动流程，理由见 `_ensure_dirs` 的 docstring。
    """
    configs_dir = get_configs_dir()
    if not os.path.isdir(configs_dir):
        return []
    files = []
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


def _records_dir_path(name: str) -> str:
    target = _sanitize_config_filename(name)
    return os.path.join(get_records_root_dir(), target)


def _delete_config_artifacts(target: str) -> bool:
    changed = False

    file_path = get_config_path(target)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            changed = True
        except OSError:
            pass

    archive_dir = os.path.join(get_archive_root_dir(), target)
    if os.path.exists(archive_dir):
        try:
            shutil.rmtree(archive_dir, ignore_errors=True)
            changed = True
        except Exception:
            pass

    record_dir = _records_dir_path(target)
    if os.path.exists(record_dir):
        try:
            shutil.rmtree(record_dir, ignore_errors=True)
            changed = True
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
        changed = True

    bindings = load_bindings()
    b_changed = False
    for b in bindings:
        before = len(b.get("variables", []))
        b["variables"] = [v for v in b.get("variables", []) if v.get("file") != target]
        if len(b.get("variables", [])) != before:
            b_changed = True
    bindings = [b for b in bindings if b.get("variables")]
    if b_changed:
        save_bindings(bindings)
        changed = True

    remarks = load_edit_remarks()
    new_remarks = [item for item in remarks if item.get("source_file") != target]
    if len(new_remarks) != len(remarks):
        save_edit_remarks(new_remarks)
        changed = True

    return changed


def delete_config_file(name: str, *, remove_mapping: bool = True) -> bool:
    target = _sanitize_config_filename(name)
    with _profile_write():
        removed = remove_config_mapping(target) if remove_mapping else False
        deleted = _delete_config_artifacts(target)
        return removed or deleted


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
    profile = get_active_profile()
    with _profile_write(profile):
        _save_json(_get_profile_json_file("favorites.json", profile), favorites)


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
    with _profile_write():
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
    with _profile_write():
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
    with _profile_write():
        favorites = load_favorites()
        for fav in favorites:
            if fav["path"] == variable_path and fav["source_file"] == source_file:
                fav["note"] = note
                save_favorites(favorites)
                return True
        return False


# ===================== IP 对应表 =====================

IP_MAPPING_FILE = os.path.join(DATA_DIR, "ip_mapping.json")
LEGACY_IP_MAPPING_FILE = IP_MAPPING_FILE


def get_ip_mapping_file(profile_id: Optional[str] = None) -> str:
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    return _get_profile_json_file("ip_mapping.json", pid)


def load_ip_mapping() -> List[Dict[str, str]]:
    profile = get_active_profile()
    path = _get_profile_json_file("ip_mapping.json", profile)
    if not os.path.exists(path) and profile == DEFAULT_PROFILE and os.path.exists(LEGACY_IP_MAPPING_FILE):
        return _load_json(LEGACY_IP_MAPPING_FILE, [])
    return _load_json(path, [])


def save_ip_mapping(mapping: List[Dict[str, str]]) -> None:
    profile = get_active_profile()
    with _profile_write(profile):
        _save_json(_get_profile_json_file("ip_mapping.json", profile), mapping)


def upsert_ip_mapping(ip: str, name: str) -> bool:
    ip_text = str(ip or "").strip()
    name_text = str(name or "").strip()
    if not ip_text or not name_text:
        raise ValueError("IP 和姓名不能为空")

    with _profile_write():
        mapping = load_ip_mapping()
        for item in mapping:
            if (item.get("ip") or "").strip() == ip_text:
                item["name"] = name_text
                item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_ip_mapping(mapping)
                return True

        mapping.append(
            {
                "ip": ip_text,
                "name": name_text,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        save_ip_mapping(mapping)
        return True


def remove_ip_mapping(ip: str) -> bool:
    ip_text = str(ip or "").strip()
    with _profile_write():
        mapping = load_ip_mapping()
        new_mapping = [item for item in mapping if (item.get("ip") or "").strip() != ip_text]
        save_ip_mapping(new_mapping)
        return len(new_mapping) < len(mapping)


def resolve_person_by_ip(ip: str) -> str:
    ip_text = str(ip or "").strip()
    if not ip_text:
        return ""
    for item in load_ip_mapping():
        if (item.get("ip") or "").strip() == ip_text:
            return str(item.get("name") or "").strip()
    return ""


# ===================== 管理员列表 =====================

ADMIN_USERS_FILE = os.path.join(DATA_DIR, "admin_users.json")
LEGACY_ADMIN_USERS_FILE = ADMIN_USERS_FILE


def get_admin_users_file(profile_id: Optional[str] = None) -> str:
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    return _get_profile_json_file("admin_users.json", pid)


def load_admin_users() -> List[Dict[str, str]]:
    profile = get_active_profile()
    path = _get_profile_json_file("admin_users.json", profile)
    if not os.path.exists(path) and profile == DEFAULT_PROFILE and os.path.exists(LEGACY_ADMIN_USERS_FILE):
        return _load_json(LEGACY_ADMIN_USERS_FILE, [])
    return _load_json(path, [])


def save_admin_users(users: List[Dict[str, str]]) -> None:
    profile = get_active_profile()
    with _profile_write(profile):
        _save_json(_get_profile_json_file("admin_users.json", profile), users)


def upsert_admin_user(name: str) -> bool:
    name_text = str(name or "").strip()
    if not name_text:
        raise ValueError("管理员姓名不能为空")

    with _profile_write():
        users = load_admin_users()
        for item in users:
            if (item.get("name") or "").strip() == name_text:
                item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_admin_users(users)
                return True

        users.append(
            {
                "name": name_text,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        save_admin_users(users)
        return True


def remove_admin_user(name: str) -> bool:
    name_text = str(name or "").strip()
    with _profile_write():
        users = load_admin_users()
        new_users = [item for item in users if (item.get("name") or "").strip() != name_text]
        save_admin_users(new_users)
        return len(new_users) < len(users)


def is_admin_user(name: str) -> bool:
    name_text = str(name or "").strip()
    if not name_text:
        return False
    return any((item.get("name") or "").strip() == name_text for item in load_admin_users())


# ===================== 审阅备注 =====================

EDIT_REMARKS_FILE = os.path.join(DATA_DIR, "edit_remarks.json")
LEGACY_EDIT_REMARKS_FILE = EDIT_REMARKS_FILE


def get_edit_remarks_file(profile_id: Optional[str] = None) -> str:
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    return _get_profile_json_file("edit_remarks.json", pid)


def load_edit_remarks() -> List[Dict[str, Any]]:
    profile = get_active_profile()
    path = _get_profile_json_file("edit_remarks.json", profile)
    if not os.path.exists(path) and profile == DEFAULT_PROFILE and os.path.exists(LEGACY_EDIT_REMARKS_FILE):
        return _load_json(LEGACY_EDIT_REMARKS_FILE, [])
    return _load_json(path, [])


def save_edit_remarks(items: List[Dict[str, Any]]) -> None:
    profile = get_active_profile()
    with _profile_write(profile):
        _save_json(_get_profile_json_file("edit_remarks.json", profile), items)


def add_edit_remark(
    *,
    source_file: str,
    node_key: str,
    node_path: str,
    node_label: str,
    original_value: str,
    proposed_value: str,
    node_type: str,
    tree_type: str,
    actor_ip: str,
    actor_name: str,
    locator: str = "",
    source_sha256: str = "",
    schema_version: int = 1,
) -> str:
    source_name = _sanitize_config_filename(source_file)
    proposed_text = str(proposed_value or "").strip()
    # v2 建议以 `locator` 唯一定位，`node_key` 允许为空（§6.7）；两者全空才是坏数据。
    if not node_key.strip() and not locator.strip():
        raise ValueError("节点标识不能为空")
    if proposed_text == "":
        raise ValueError("修改值不能为空")

    actor_ip_text = str(actor_ip or "").strip() or "unknown"
    actor_name_text = str(actor_name or "").strip()
    actor_display = actor_name_text or actor_ip_text

    with _profile_write():
        items = load_edit_remarks()
        item_id = uuid.uuid4().hex
        items.append(_build_edit_remark_item(
            item_id=item_id,
            source_name=source_name,
            node_key=node_key,
            node_path=node_path,
            node_label=node_label,
            original_value=original_value,
            proposed_text=proposed_text,
            node_type=node_type,
            tree_type=tree_type,
            actor_ip_text=actor_ip_text,
            actor_name_text=actor_name_text,
            actor_display=actor_display,
            locator=locator,
            source_sha256=source_sha256,
            schema_version=schema_version,
        ))
        save_edit_remarks(items)
    return item_id


def _build_edit_remark_item(
    *,
    item_id: str,
    source_name: str,
    node_key: str,
    node_path: str,
    node_label: str,
    original_value: str,
    proposed_text: str,
    node_type: str,
    tree_type: str,
    actor_ip_text: str,
    actor_name_text: str,
    actor_display: str,
    locator: str = "",
    source_sha256: str = "",
    schema_version: int = 1,
) -> Dict[str, Any]:
    return {
        "id": item_id,
        "source_file": source_name,
        "node_key": str(node_key),
        "node_path": str(node_path or ""),
        "node_label": str(node_label or ""),
        "original_value": str(original_value or ""),
        "proposed_value": proposed_text,
        "node_type": str(node_type or ""),
        "tree_type": str(tree_type or ""),
        "actor_ip": actor_ip_text,
        "actor_name": actor_name_text,
        "actor_display": actor_display,
        "status": "pending",
        "reviewed_by": "",
        "reviewer_ip": "",
        "reviewed_at": "",
        "generated_file": "",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # v2 新增的可选字段（§6.7）：旧记录没有这些键也仍能列出。
        "locator": str(locator or ""),
        "source_sha256": str(source_sha256 or ""),
        "schema_version": int(schema_version),
    }


def build_edit_remark_map(
    source_file: str,
    *,
    statuses: Optional[set[str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    source_name = _sanitize_config_filename(source_file)
    result: Dict[str, List[Dict[str, Any]]] = {}
    for item in load_edit_remarks():
        if item.get("source_file") != source_name:
            continue
        status = str(item.get("status") or "pending")
        if statuses is not None and status not in statuses:
            continue
        key = str(item.get("node_key") or item.get("node_path") or "")
        if not key:
            continue
        result.setdefault(key, []).append(item)

    for values in result.values():
        values.sort(key=lambda entry: (entry.get("created_at") or "", entry.get("id") or ""))
    return result


def list_review_items(source_file: Optional[str] = None) -> List[Dict[str, Any]]:
    target_file = _sanitize_config_filename(source_file) if source_file else None
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for item in load_edit_remarks():
        if item.get("status") != "pending":
            continue
        if target_file and item.get("source_file") != target_file:
            continue

        key = (str(item.get("source_file") or ""), str(item.get("node_key") or item.get("node_path") or ""))
        if key not in grouped:
            grouped[key] = {
                "source_file": item.get("source_file") or "",
                "node_key": item.get("node_key") or "",
                "node_path": item.get("node_path") or "",
                "node_label": item.get("node_label") or "",
                "original_value": item.get("original_value") or "",
                "node_type": item.get("node_type") or "",
                "tree_type": item.get("tree_type") or "",
                "remarks": [],
            }
        grouped[key]["remarks"].append(item)

    items = list(grouped.values())
    for entry in items:
        entry["remarks"].sort(key=lambda remark: (remark.get("created_at") or "", remark.get("id") or ""))
    items.sort(key=lambda entry: (entry.get("source_file") or "", entry.get("node_path") or "", entry.get("node_key") or ""))
    return items


def apply_review_results(
    *,
    approved_ids: List[str],
    rejected_ids: List[str],
    reviewer_name: str,
    reviewer_ip: str,
    generated_files: Optional[Dict[str, str]] = None,
) -> Dict[str, int]:
    approved = {str(item_id) for item_id in approved_ids}
    rejected = {str(item_id) for item_id in rejected_ids}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    reviewer_name_text = str(reviewer_name or "").strip()
    reviewer_ip_text = str(reviewer_ip or "").strip()
    generated_map = generated_files or {}

    with _profile_write():
        items = load_edit_remarks()
        approved_count = 0
        rejected_count = 0
        changed = False

        for item in items:
            item_id = str(item.get("id") or "")
            if item_id in approved:
                item["status"] = "approved"
                item["reviewed_by"] = reviewer_name_text
                item["reviewer_ip"] = reviewer_ip_text
                item["reviewed_at"] = now
                item["generated_file"] = generated_map.get(item.get("source_file") or "", "")
                approved_count += 1
                changed = True
            elif item_id in rejected:
                item["status"] = "rejected"
                item["reviewed_by"] = reviewer_name_text
                item["reviewer_ip"] = reviewer_ip_text
                item["reviewed_at"] = now
                item["generated_file"] = ""
                rejected_count += 1
                changed = True

        if changed:
            save_edit_remarks(items)

    return {"approved": approved_count, "rejected": rejected_count}


# ============ 审阅提交：幂等表与生成文件（§6.7 第 1/7 步；M3-02） ============


def load_review_idempotency() -> Dict[str, Any]:
    """`request_id → {"at": 服务器 epoch 秒, "result": {…}}`（§6.7 第 1 步）。

    落在机型目录下的**独立文件** `review_idempotency.json`：卡面要求「进程重启后重复
    `request_id` 仍不得生成第二份文件」，所以必须落盘；而把它塞进 `edit_remarks.json`
    顶层会破坏 §5.2「现有字段不变」。路径在**调用期**由 `_get_profile_json_file` 求值，
    不留模块级常量（交付手册决策 1 的 import 期派生路径坑）。文件被手改成非对象时按空表
    处理——它是运行期数据，重写一次即可，不该让审阅永久报错；真正的损坏（JSON 读不出来）
    仍由 `_load_json` 抛 `CorruptDataError`（§5.4）。
    """
    profile = get_active_profile()
    data = _load_json(_get_profile_json_file("review_idempotency.json", profile), {})
    return data if isinstance(data, dict) else {}


def save_review_idempotency(table: Dict[str, Any]) -> None:
    profile = get_active_profile()
    with _profile_write(profile):
        _save_json(_get_profile_json_file("review_idempotency.json", profile), table)


def _review_generated_candidates(name: str, now: datetime) -> Iterator[str]:
    """审阅生成名的候选序列（§5.3「审阅生成」行）。

    `<stem>_<YYYYMMDD>_<HHMMSS><.ext>`；已被占用则依次追加 `_2`、`_3`…（与归档同一条
    独占创建的路子，同一秒第二次提交不会覆盖第一份）。
    """
    precise = now.strftime("%Y%m%d_%H%M%S")
    yield _add_date_suffix(name, precise)
    for index in range(2, 1000):
        yield _add_date_suffix(name, "%s_%d" % (precise, index))


def save_review_generated_file(name: str, content: bytes) -> str:
    """把审阅写回的结果作为**新**文件存进 `configs/`（§6.7 第 7 步），原文件一字节不动。

    与 `save_config_file_detailed` 的分工：那个函数维护「当前文件 + 归档」的关系，写之前
    会把磁盘上的旧内容归档；这里的产物本来就是新文件，按 §5.3 先用独占创建拿到唯一名再
    原子写入。返回生成后的文件名。
    """
    fsutil.validate_filename(name)
    _ensure_dirs()
    with _profile_write():
        with _reserve_file(
            get_configs_dir(), _review_generated_candidates(name, datetime.now())
        ) as filepath:
            fsutil.atomic_write_bytes(filepath, content)
            return os.path.basename(filepath)


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
    profile = get_active_profile()
    with _profile_write(profile):
        _save_json(_get_profile_json_file("bindings.json", profile), bindings)


def add_binding(group_name: str, variables: List[Dict[str, str]]) -> bool:
    """添加或更新变量绑定组

    variables: [{"file": str, "path": str}]
    """
    with _profile_write():
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
    with _profile_write():
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
    profile = get_active_profile()
    with _profile_write(profile):
        _save_json(_get_profile_json_file("tools.json", profile), tools)


def add_tool(name: str, description: str, url: str) -> bool:
    """添加工具"""
    with _profile_write():
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
    with _profile_write():
        tools = load_tools()
        new_tools = [t for t in tools if t["name"] != name]
        save_tools(new_tools)
        return len(new_tools) < len(tools)


def update_tool(original_name: str, name: str, description: str, url: str) -> bool:
    """更新工具配置"""
    with _profile_write():
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


#: `schedule.json` 缺文件时的默认值（规范 §5 的 schema）。`last_run` 是 v2 新增的**可选**
#: 字段，读不出来就是「从没跑过」（`None`），界面上显示「尚未执行」。写它的人是
#: `services/updates.py::update_all`（在机型锁内读-改-写）。
_SCHEDULE_DEFAULT = {"enabled": False, "interval_hours": 24, "last_run": None}


def load_schedule() -> Dict[str, Any]:
    """加载调度配置（缺文件/缺 `last_run` 都按默认值，见 `_SCHEDULE_DEFAULT`）。"""
    profile = get_active_profile()
    path = _get_profile_json_file("schedule.json", profile)
    if not os.path.exists(path) and profile == DEFAULT_PROFILE and os.path.exists(LEGACY_SCHEDULE_FILE):
        return _load_json(LEGACY_SCHEDULE_FILE, dict(_SCHEDULE_DEFAULT))
    return _load_json(path, dict(_SCHEDULE_DEFAULT))


def save_schedule(schedule: Dict[str, Any]):
    """保存调度配置"""
    profile = get_active_profile()
    with _profile_write(profile):
        _save_json(_get_profile_json_file("schedule.json", profile), schedule)


# ===================== 更新源最近结果 =====================
#
# `profiles/<pid>/update_status.json`（规范 §10.2 第 7 步、§7.9 的 `last_result`）：
# `{文件名: {"status","label","reason","at","archived"}}`。
#
# 三条约束（这张卡新增的持久化文件，容易踩的地方都写在这）：
# * **路径只在调用期由 `_get_profile_json_file` 求值**，不落地成模块级常量——测试夹具靠
#   重绑定 `DATA_DIR` 隔离数据根，常量会绕开它写到真实目录（见 `configure_data_dir` 与
#   `docs/know-how.md` 第 7 节），所以文件名字面量只出现在 `get_update_status_file` 里；
# * 属**运行期数据**，因此**不进** `COPY_PROFILE_FILES`（§7.2：不随机型复制）；
# * v1 没有这个文件，所以没有 `LEGACY_*` 回退：不存在就是「从没检查过」。


def get_update_status_file(profile_id: Optional[str] = None) -> str:
    pid = _normalize_profile_id(profile_id) if profile_id is not None else get_active_profile()
    return _get_profile_json_file("update_status.json", pid)


def load_update_status() -> Dict[str, Any]:
    """加载更新源最近结果；文件不存在时返回 `{}`（缺省视为全 null）。"""
    path = get_update_status_file()
    status = _load_json(path, {})
    if not isinstance(status, dict):
        # 形状不对（例如被改成数组）等同损坏：返回空表会让下一次保存丢掉全部结果。
        raise CorruptDataError(path)
    return status


def save_update_status(status: Dict[str, Any]) -> None:
    """整体保存更新源最近结果（原子写，机型锁内）。"""
    profile = get_active_profile()
    with _profile_write(profile):
        _save_json(get_update_status_file(profile), status)


#: 复制机型时带上的目录（规范 §7.2、C27）。
#: `cache/` 可重建；`records/`、`edit_remarks.json`、`review_idempotency.json`、
#: `ip_mapping.json`、`admin_users.json`、`update_status.json` 属于运行期数据，
#: 不随机型复制。
COPY_PROFILE_DIRS = ("configs", "archive")

#: 复制机型时带上的 JSON（规范 §7.2）。
COPY_PROFILE_FILES = (
    "config_mapping.json",
    "favorites.json",
    "bindings.json",
    "tools.json",
    "schedule.json",
    "dltool_config.json",
)

#: `default` 机型在新位置缺文件时按旧位置回退（§5.5 旧布局）。
_LEGACY_PROFILE_FILES = {
    "config_mapping.json": LEGACY_MAPPING_FILE,
    "favorites.json": LEGACY_FAVORITES_FILE,
    "bindings.json": LEGACY_BINDINGS_FILE,
    "tools.json": LEGACY_TOOLS_FILE,
    "schedule.json": LEGACY_SCHEDULE_FILE,
}

#: 复制机型时带上的目录 → 取目录的函数（§7.2）。
_COPY_DIR_GETTERS = {"configs": get_configs_dir, "archive": get_archive_root_dir}


def copy_profile_data(source_profile: str, target_profile: str) -> None:
    src = _normalize_profile_id(source_profile)
    dst = _normalize_profile_id(target_profile)
    if dst == DEFAULT_PROFILE:
        raise ValueError("target_profile cannot be default")

    # 复制是一次跨机型的读改写：两个机型锁都拿住，按 dst→src 的固定顺序取，
    # 避免与别的跨机型操作互相等待。
    with _profile_write(dst):
        with _profile_write(src):
            _copy_profile_data_locked(src, dst)


def _copy_profile_data_locked(src: str, dst: str) -> None:
    dst_base = _profile_base_dir(dst)
    os.makedirs(dst_base, exist_ok=True)

    for basename in COPY_PROFILE_FILES:
        src_path = _get_profile_json_file(basename, src)
        if src == DEFAULT_PROFILE and not os.path.exists(src_path):
            legacy = _LEGACY_PROFILE_FILES.get(basename)
            if legacy and os.path.exists(legacy):
                src_path = legacy
        if not os.path.exists(src_path):
            continue
        dst_path = os.path.join(dst_base, basename)
        if basename == "schedule.json":
            # 调度周期照抄，但"上次执行结果"属于源机型的历史，落到新机型上会
            # 让人误以为它跑过（规范 §7.2）。
            schedule = _load_json(src_path, {"enabled": False, "interval_hours": 24})
            schedule["last_run"] = None
            _save_json(dst_path, schedule)
            continue
        shutil.copy2(src_path, dst_path)

    for name in COPY_PROFILE_DIRS:
        src_dir = _COPY_DIR_GETTERS[name](src)
        if os.path.exists(src_dir):
            shutil.copytree(src_dir, os.path.join(dst_base, name), dirs_exist_ok=True)


def delete_profile_data(profile_id: str) -> None:
    pid = _normalize_profile_id(profile_id)
    if pid == DEFAULT_PROFILE:
        raise ValueError("cannot delete default profile data")
    base = _profile_base_dir(pid)
    with _profile_write(pid):
        if os.path.exists(base):
            shutil.rmtree(base, ignore_errors=True)
