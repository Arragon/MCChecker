"""删除文件与恢复区（§5.6、§7.4、T-15；M2-12）。

破坏性操作的固定顺序：**先整份复制进恢复区并逐文件比对 sha256，校验通过才删源，最后才改
JSON 引用**。中途任何失败一律逆序回滚——原文放回原位置、四份权威 JSON 回到删除前的快照，
恢复区目录保留但不留半份副本，也绝不报告成功（§5.6）。

`storage.delete_config_file` 的旧硬删语义（`rmtree(ignore_errors=True)`，删了就没有副本）
**保持不动**：v1 页面与既有测试依赖它，v2 的删除一律走本模块。

机型删除（`delete_profile`）以后复用同一套 `create_entry` / `move_in` / `write_manifest`，
它的 HTTP 入口在 M3-16。恢复动作本身是运维手工步骤（§14.6），本期不做浏览与恢复 UI。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Tuple

from ..core import fsutil, log_context, parse_cache, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, STORAGE_FAILURE, AppError
from . import revision

logger = logging.getLogger(__name__)

#: §5.6 清单里的 `op`；机型删除与旧布局迁移复用同一目录约定。
OP_DELETE_FILE = "delete_file"
OP_DELETE_PROFILE = "delete_profile"
MANIFEST_FILE = "manifest.json"

#: 条目目录名用 `%Y%m%d_%H%M%S`（不含空格与冒号，跨平台可移植）；清单里给人看的时间另用一套。
_DIR_TIME = "%Y%m%d_%H%M%S"
_DISPLAY_TIME = "%Y-%m-%d %H:%M:%S"
#: 同名同秒的极端情况：连续删同一个文件名时追加序号，最多试这么多次。
MAX_ENTRY_TRIES = 50


def delete_file(
    profile_id: str, name: str, ip: str = "", actor_name: str = ""
) -> Dict[str, Any]:
    """`DELETE /p/{pid}/files/{name}`：原文移入恢复区并清理四类引用。

    修订号只按**真正改动的权威数据**递增（决策 16）：`files` 总是动，其余三类只在确有
    移除时才动，所以「删一个光秃秃的文件」不会让别的用户看到收藏/建议分量白涨。
    """
    started = time.perf_counter()
    clean_name = fsutil.validate_filename(name)
    moment = datetime.now()
    with storage.profile_write(profile_id), storage.use_profile(profile_id):
        payload, parts = _delete_locked(profile_id, clean_name, ip, actor_name, moment)
    for part in parts:
        revision.bump(profile_id, part)
    log_context.business(
        "file_delete", profile=profile_id, actor=actor_name, target=clean_name,
        result="recovered", duration_ms=(time.perf_counter() - started) * 1000,
    )
    return payload


def _delete_locked(
    profile_id: str, clean_name: str, ip: str, actor_name: str, moment: datetime
) -> Tuple[Dict[str, Any], List[str]]:
    config_path = fsutil.resolve_within(storage.get_configs_dir(), clean_name)
    if not os.path.isfile(config_path):
        raise AppError(NOT_FOUND, "文件不存在", name=clean_name)

    # 只列真实存在的产物：读路径不能把 archive/records 的目录建回来。
    archive_dir = os.path.join(storage.get_archive_root_dir(), clean_name)
    records_dir = os.path.join(storage.get_records_root_dir(), clean_name)
    artifacts = [
        (config_path, "configs/%s" % clean_name),
        (archive_dir, "archive/%s" % clean_name),
        (records_dir, "records/%s" % clean_name),
    ]
    artifacts = [pair for pair in artifacts if os.path.exists(pair[0])]
    counts = {"archives": _count_files(archive_dir, skip_diff=True), "records": _count_files(records_dir)}

    # 引用先读一遍：既是「要摘掉哪些」的计划，也是回滚与清单用的快照。
    plan = _plan_references(
        clean_name,
        storage.load_config_mapping(),
        storage.load_favorites(),
        storage.load_bindings(),
        storage.load_edit_remarks(),
    )

    entry_path, entry = create_entry(OP_DELETE_FILE, profile_id, clean_name, moment)
    undo: List[Any] = []
    try:
        moved: List[Dict[str, str]] = []
        for source, rel in artifacts:
            moved.append(move_in(entry_path, source, rel))
            undo.append(("move", source, rel))
        _apply_references(plan, undo)
        write_manifest(entry_path, _manifest(profile_id, clean_name, ip, actor_name, moment, moved, plan))
    except BaseException:
        _undo(entry_path, undo)
        raise

    _invalidate(config_path)
    parts = ["files"]
    if plan["favorites_removed"]:
        parts.append("favorites")
    if plan["remarks_removed"]:
        parts.append("remarks")
    if plan["bindings_removed"]:
        parts.append("settings")
    payload = {
        "deleted": True,
        "removed": {
            "archives": counts["archives"],
            "records": counts["records"],
            "remarks": len(plan["remarks_removed"]),
            "favorites": len(plan["favorites_removed"]),
        },
        "recovery": entry,
    }
    return payload, parts


# ===================== 恢复区条目 =====================


def entry_name(op: str, profile_id: str, target: str, moment: datetime) -> str:
    """`<时间>_<op>_<机型>_<目标>`（§5.6）。"""
    return "%s_%s_%s_%s" % (moment.strftime(_DIR_TIME), op, profile_id, target)


def create_entry(op: str, profile_id: str, target: str, moment: datetime) -> Tuple[str, str]:
    """在恢复区下**独占创建**一个条目目录，返回 `(绝对路径, 目录名)`。

    用 `os.mkdir` 的「已存在即失败」占位，不用时间戳赌唯一：同名同秒的两次删除也必须各自
    留一份，否则第二次的副本会盖掉第一次。
    """
    return _create_unique_entry(entry_name(op, profile_id, target, moment))


def _create_unique_entry(base: str) -> Tuple[str, str]:
    root = storage.get_recovery_root_dir()
    os.makedirs(root, exist_ok=True)
    for index in range(1, MAX_ENTRY_TRIES + 1):
        candidate = base if index == 1 else "%s_%d" % (base, index)
        path = fsutil.resolve_within(root, candidate)
        try:
            os.mkdir(path)
        except FileExistsError:
            continue
        return path, candidate
    raise AppError(STORAGE_FAILURE, "无法为恢复条目生成唯一目录名", target=base)


def create_profile_entry(model_id: str, moment: datetime, workspace_id: str = "") -> Tuple[str, str]:
    """机型删除条目：`<时间>_delete_profile_<机型>`；V3 带 W 时追加 `_w<wid>`。"""
    name = "%s_%s_%s" % (moment.strftime(_DIR_TIME), OP_DELETE_PROFILE, model_id)
    if workspace_id:
        name += "_w" + str(workspace_id)
    return _create_unique_entry(name)



def plan_restore(entry: str) -> Dict[str, Any]:
    """只读恢复计划（M4-09）：manifest 的 moved 逐条给出「恢复区副本 → 原位置」。"""
    entry_path = os.path.abspath(os.path.expanduser(str(entry or "")))
    if not os.path.isdir(entry_path):
        raise AppError(NOT_FOUND, "恢复条目不存在：%s" % entry_path)
    manifest_path = os.path.join(entry_path, MANIFEST_FILE)
    if not os.path.isfile(manifest_path):
        raise AppError(NOT_FOUND, "恢复条目缺少 manifest.json：%s" % entry_path)
    manifest = fsutil.read_json(manifest_path, None)
    if not isinstance(manifest, dict):
        raise AppError(INVALID_INPUT, "manifest.json 不是 JSON 对象")
    moved = manifest.get("moved") or []
    if not isinstance(moved, list):
        raise AppError(INVALID_INPUT, "manifest.json 的 moved 必须是数组")
    removed_refs = manifest.get("removed_refs")
    if removed_refs is None:
        removed_refs = {}
    if not isinstance(removed_refs, dict):
        raise AppError(INVALID_INPUT, "manifest.json 的 removed_refs 必须是对象")
    steps = []
    for item in moved:
        if not isinstance(item, dict):
            raise AppError(INVALID_INPUT, "manifest.json 的 moved 条目必须是对象")
        try:
            source = fsutil.resolve_within(entry_path, str(item.get("to") or ""))
            destination = fsutil.resolve_within(storage.DATA_DIR, str(item.get("from") or ""))
        except AppError as exc:
            raise AppError(INVALID_INPUT, "manifest.json 的路径越界：%s" % exc.message) from exc
        steps.append(
            {
                "source": source,
                "destination": destination,
                "exists": os.path.exists(source),
            }
        )
    return {
        "entry": entry_path,
        "op": manifest.get("op"),
        "steps": steps,
        "removed_refs": removed_refs,
    }

def move_profile_to_recovery(
    model_id: str, actor_name: str = "", actor_ip: str = "", workspace_id: str = ""
) -> str:
    """把 `data/profiles/<id>/` 整个目录移入恢复区，返回条目目录名。

    顺序与文件删除一致：复制 → 逐文件 sha256 校验 → 才删源 → 写 manifest。
    manifest 写失败时把整份目录放回原位置并清掉恢复条目；回滚自身失败则保留条目
    （数据仍在恢复区）并把异常抛出，绝不报告成功。
    """
    moment = datetime.now()
    source = storage.get_profile_data_dir(model_id)
    entry_path, entry = create_profile_entry(model_id, moment, workspace_id=workspace_id)
    rel = "profiles/%s" % model_id
    manifest: Dict[str, Any] = {
        "op": OP_DELETE_PROFILE,
        "profile": model_id,
        "created_at": moment.strftime(_DISPLAY_TIME),
        "actor": {"ip": actor_ip or "", "name": actor_name or ""},
        "moved": [],
        "files": [],
    }
    if workspace_id:
        manifest["workspace"] = str(workspace_id)
    try:
        if os.path.exists(source):
            manifest["moved"].append(move_in(entry_path, source, rel))
            target = os.path.join(entry_path, rel)
            manifest["files"] = [
                {"path": path, "sha256": _sha256_file(full)}
                for path, full in sorted(_tree(target).items())
            ]
        write_manifest(entry_path, manifest)
    except BaseException:
        copied = os.path.join(entry_path, rel)
        drop_entry = False
        if os.path.exists(copied):
            if not os.path.exists(source):
                # 源已被完整移走：把完整副本放回原位，成功后才清条目。
                try:
                    os.makedirs(os.path.dirname(source), exist_ok=True)
                    shutil.move(copied, source)
                    drop_entry = True
                except Exception as exc:
                    logger.error("机型删除回滚失败，数据保留在恢复区 %s: %s", entry, exc)
            else:
                # `_remove_source` 可能只删了一部分就抛错：此时源是残的，副本才是唯一完整
                # 副本。绝不 rmtree 条目——那会把最后一份数据也删掉（M3-16 评审 Critical）。
                logger.error("机型删除时源未完整移除，保留恢复条目 %s", entry)
        else:
            # 复制根本没建立目标；源未被触碰，空条目可以清掉。
            drop_entry = True
        if drop_entry:
            shutil.rmtree(entry_path, ignore_errors=True)
        raise
    return entry


def restore_profile_entry(entry: str, model_id: str) -> bool:
    """把恢复条目里的机型目录放回原位（注册表写失败时的逆操作）。

    只处理「源目录不存在」的形态；源还在（哪怕是残的）就不覆盖，交给运维按 manifest
    处理。成功放回后清掉条目；返回是否真的恢复了。
    """
    entry_path = fsutil.resolve_within(storage.get_recovery_root_dir(), entry)
    copied = os.path.join(entry_path, "profiles", model_id)
    source = storage.get_profile_data_dir(model_id)
    if not os.path.exists(copied) or os.path.exists(source):
        return False
    try:
        os.makedirs(os.path.dirname(source), exist_ok=True)
        shutil.move(copied, source)
    except Exception as exc:
        logger.error("恢复机型目录失败 %s: %s", entry, exc)
        return False
    shutil.rmtree(entry_path, ignore_errors=True)
    return True


def move_in(entry_path: str, source: str, rel: str) -> Dict[str, str]:
    """把一份原文（文件或整个目录）复制进恢复区，**校验通过才删源**。"""
    target = os.path.join(entry_path, rel)
    if os.path.exists(target):
        raise AppError(STORAGE_FAILURE, "恢复条目里已存在同名路径", to=rel)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.isdir(source):
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)
    _verify_copy(source, target)
    _remove_source(source)
    return {"from": _data_relative(source), "to": rel}


def write_manifest(entry_path: str, manifest: Dict[str, Any]) -> None:
    fsutil.atomic_write_json(os.path.join(entry_path, MANIFEST_FILE), manifest)


# ===================== 计划与校验 =====================


def _plan_references(
    clean_name: str,
    mapping: List[Dict[str, Any]],
    favorites: List[Dict[str, Any]],
    bindings: List[Dict[str, Any]],
    remarks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """算出「删掉这个文件名之后，四份 JSON 各自该长什么样」，并留下被摘掉的原件。"""
    removed_mapping = [item for item in mapping if item.get("name") == clean_name]
    removed_favorites = [item for item in favorites if item.get("source_file") == clean_name]
    removed_remarks = [item for item in remarks if item.get("source_file") == clean_name]

    kept_bindings: List[Dict[str, Any]] = []
    removed_bindings: List[Dict[str, Any]] = []
    for group in bindings:
        variables = group.get("variables") or []
        gone = [v for v in variables if v.get("file") == clean_name]
        if not gone:
            kept_bindings.append(group)
            continue
        removed_bindings.append({"group_name": group.get("group_name"), "variables": deepcopy(gone)})
        kept = [v for v in variables if v.get("file") != clean_name]
        if kept:
            # 「空组删除」只对因本次删除而变空的组生效；本来就有其它变量的组保持原样。
            updated = dict(group)
            updated["variables"] = kept
            kept_bindings.append(updated)

    return {
        "mapping_all": mapping,
        "mapping_removed": removed_mapping,
        "mapping_after": [item for item in mapping if item.get("name") != clean_name],
        "favorites_all": favorites,
        "favorites_removed": removed_favorites,
        "favorites_after": [item for item in favorites if item.get("source_file") != clean_name],
        "bindings_all": bindings,
        "bindings_removed": removed_bindings,
        "bindings_after": kept_bindings,
        "remarks_all": remarks,
        "remarks_removed": removed_remarks,
        "remarks_after": [item for item in remarks if item.get("source_file") != clean_name],
    }


def _apply_references(plan: Dict[str, Any], undo: List[Any]) -> None:
    """写四份 JSON；每写成功一份就登记它的前像，供中途失败时退回。"""
    steps = (
        ("mapping_after", "mapping_all", storage.save_config_mapping),
        ("favorites_after", "favorites_all", storage.save_favorites),
        ("bindings_after", "bindings_all", storage.save_bindings),
        ("remarks_after", "remarks_all", storage.save_edit_remarks),
    )
    for after_key, before_key, saver in steps:
        if plan[after_key] == plan[before_key]:
            continue
        saver(plan[after_key])
        undo.append(("json", saver, plan[before_key]))


def _manifest(
    profile_id: str,
    clean_name: str,
    ip: str,
    actor_name: str,
    moment: datetime,
    moved: List[Dict[str, str]],
    plan: Dict[str, Any],
) -> Dict[str, Any]:
    """§5.6 的清单：够运维照着它手工放回原文与被移除的引用。"""
    return {
        "op": OP_DELETE_FILE,
        "profile": profile_id,
        "target": clean_name,
        "created_at": moment.strftime(_DISPLAY_TIME),
        "actor": {"ip": ip or "", "name": actor_name or ""},
        "moved": moved,
        "removed_refs": {
            "mapping": deepcopy(plan["mapping_removed"]),
            "favorites": deepcopy(plan["favorites_removed"]),
            "bindings": deepcopy(plan["bindings_removed"]),
            "remarks": deepcopy(plan["remarks_removed"]),
        },
    }


def _undo(entry_path: str, undo: List[Any]) -> None:
    """逆序退回。回滚自身出错只记日志，不能盖掉原始异常（与 `storage._rollback` 同规则）。"""
    for entry in reversed(undo):
        try:
            if entry[0] == "json":
                entry[1](deepcopy(entry[2]))
            else:
                _restore(entry_path, entry[1], entry[2])
        except Exception as exc:
            logger.error("删除回滚失败 %r: %s", entry, exc)


def _restore(entry_path: str, source: str, rel: str) -> None:
    """把副本放回原位置；原位置已有东西就不动（宁缺勿盖）。"""
    moved = os.path.join(entry_path, rel)
    if not os.path.exists(moved) or os.path.exists(source):
        return
    os.makedirs(os.path.dirname(source), exist_ok=True)
    shutil.move(moved, source)


def _verify_copy(source: str, target: str) -> None:
    """文件数与每个文件的 sha256 都要一致，否则不许删源。"""
    src = _tree(source)
    dst = _tree(target)
    if set(src) != set(dst):
        raise AppError(STORAGE_FAILURE, "恢复区副本的文件数与原位置不符", path=os.path.basename(source))
    for rel, src_path in src.items():
        if _sha256_file(src_path) != _sha256_file(dst[rel]):
            raise AppError(STORAGE_FAILURE, "恢复区副本内容与原文件不一致", file=rel)


def _remove_source(source: str) -> None:
    """删掉已校验过的源。这里**不**用 `ignore_errors`：残留必须暴露成失败。"""
    if os.path.isdir(source):
        shutil.rmtree(source)
    else:
        os.remove(source)
    if os.path.exists(source):
        raise AppError(STORAGE_FAILURE, "源路径未能移除", path=os.path.basename(source))


def _tree(root: str) -> Dict[str, str]:
    """`{相对路径: 绝对路径}`；单个文件时键就是它的文件名。"""
    if os.path.isfile(root):
        return {os.path.basename(root): root}
    out: Dict[str, str] = {}
    for base, _dirs, names in os.walk(root):
        for name in names:
            full = os.path.join(base, name)
            out[os.path.relpath(full, root)] = full
    return out


def _count_files(directory: str, skip_diff: bool = False) -> int:
    """与 `GET /files` 的 `archive_count` / `record_count` 同口径（归档数不含 `.diff.json`）。"""
    if not os.path.isdir(directory):
        return 0
    total = 0
    for entry in os.listdir(directory):
        if skip_diff and entry.endswith(".diff.json"):
            continue
        if os.path.isfile(os.path.join(directory, entry)):
            total += 1
    return total


def _data_relative(path: str) -> str:
    """清单里的 `from`：相对数据根的可读路径（运维照着它把文件放回原位）。

    两侧都取 realpath：macOS 上 `/var` 是指向 `/private/var` 的符号链接，而
    `resolve_within` 会把配置文件路径 realpath 化、归档/记录目录却不会——只 realpath
    一侧会算出 `../../../../` 开头的废路径。
    """
    return os.path.relpath(os.path.realpath(path), os.path.realpath(storage.DATA_DIR))


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _invalidate(config_path: str) -> None:
    """解析缓存是派生数据：清不掉只影响下次读取是否走缓存，不能把删除报成失败。"""
    try:
        parse_cache.invalidate(config_path)
    except Exception as exc:
        logger.warning("解析缓存失效失败 %s: %s", config_path, exc)
