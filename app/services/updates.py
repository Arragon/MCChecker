"""更新源更新与更新源映射（规范 §10.2 七步、§7.9 CRUD、§5.4 写入可靠性；M3-05 / INH-1890）。

分层：本模块组合 `core`（下载、解析、存储）与 `services.revision`，**不碰 HTTP**；
`server/api_sources.py` 只做「解析请求 → 授权 → 调这里 → 序列化响应」（§3.3）。

`update_source` 是「下载之后的校验后提交」，四条不变量：

1. **校验不过就不写**：解析失败、根节点指纹不同、锁内发现文件已被别人改过，三种情况
   一律 `rejected`，当前版本字节不变、不产生归档。HTTP 200 的错误页也是错误页——
   它能不能替换现版本由根指纹决定，不由状态码决定。
2. **没有「强制更新」**：函数签名只有 `(pid, name, actor)`，没有任何入参能绕过第 4/5 步。
3. **归档与「内容相同」的判定只信 `storage.save_config_file_detailed`**（R5）：它返回
   `SaveResult(path, archived, unchanged)`，逐字节一致就不写不归档。本模块不再自己比
   sha256 来判断「有没有变」，只在机型锁内比一次 sha 确认**比较基准没被并发换掉**（§10.2 第 6 步）。
4. **`revision.bump(pid, "files")` 只在 `result.unchanged is False` 时发生**：文件字节没变
   就不该通知别人（决策 16 的「失败写与删了 0 条不递增」同口径，见 R5 裁定）。

结果一律返回 dict、**不抛业务异常**（`update_status.json` 与任务 `result.items` 都吃这个
结构）；只有存储失败才向上抛。失败与跳过同样会写 `update_status.json`——「最近结果」这一列
要的正是失败原因。例外是 `not_found`：映射里根本没有这个名字，不该往状态文件里塞野键。

下载策略全部来自启动配置（`downloader.default_policy()`）。`block_loopback` 保持默认
`False`：§10.2 只对**访客临时导入**额外禁止回环，那一条路径在 `services/upload.py`
（不在本卡范围，认领结论见卡报告 R31）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..core import downloader, fsutil, parser, storage
from ..core.errors import (
    FILE_EXISTS,
    INVALID_INPUT,
    NOT_FOUND,
    TOO_LARGE,
    AppError,
)
from . import revision

logger = logging.getLogger(__name__)

#: `update_source` 成功后递增的修订分量（§7.3）。
PART = "files"
#: 映射 CRUD 与调度配置属于「设置」分量（本模块是 `settings` 的第一个调用点，M3-17 会审）。
SETTINGS_PART = "settings"

#: 结果 `label` 与 `status` 的对应（§7.10 的 `result.items`、§7.9 的 `last_result`）。
LABELS: Dict[str, str] = {
    "success": "已更新",
    "unchanged": "无变化",
    "rejected": "已拒绝",
    "download_failed": "下载失败",
    "too_large": "超过上限",
    "skipped": "已跳过",
    "not_found": "未找到",
}

NO_URL_REASON = "无下载链接"
INVALID_CONTENT_REASON = "下载内容不是有效的 XML/JSON"
ROOT_CHANGED_REASON = "根节点由 %s 变为 %s，可能下载到了错误页面，已拒绝替换"
UNCHANGED_REASON = "内容与当前版本一致，未归档"
RACE_REASON = "当前版本在本次更新期间被其他操作修改，已放弃替换"
NO_MAPPING_REASON = "映射中不存在该更新源"
NO_RECORD_URL_REASON = "未配置修改记录链接"
RECORDS_PENDING_REASON = "修改记录拉取将在 M3-09 接入"

_URL_LABELS = {"url": "下载链接", "record_url": "修改记录链接"}


# ---------------------------------------------------------------------------
# §10.2 七步
# ---------------------------------------------------------------------------


def update_source(pid: str, name: str, actor: str) -> Dict[str, Any]:
    """按 §10.2 更新一个更新源，返回 `{"name","status","label","reason","at","archived"}`。

    `actor` 只进日志（人或 `system`）；结果结构里没有它，卡 6 的 `Task.actor` 才是归属。
    """
    with storage.use_profile(pid):
        entry = _mapping_entry(name)
        if entry is None:
            # 名字都不在映射里：不写 update_status（见模块 docstring 的例外说明）。
            return _result(name, "not_found", NO_MAPPING_REASON)
        target = str(entry.get("name") or "")

        # 第 1 步：没有下载链接就不下载、不记失败。
        url = str(entry.get("url") or "").strip()
        if not url:
            return _finish(_result(target, "skipped", NO_URL_REASON), actor)

        # 第 2 步：下载。网络 I/O 一律在机型锁之外（§3.5「锁内禁止网络 I/O」）。
        try:
            content = downloader.fetch(url, _policy())
        except AppError as exc:
            status = "too_large" if exc.code == TOO_LARGE else "download_failed"
            return _finish(_result(target, status, exc.message), actor)

        # 第 3 步：能不能解析成 XML/JSON。解析器的体积/深度上限同样算「内容不合格」，
        # 但它的原因对用户更有用，所以那种情况保留解析器自己的消息。
        try:
            document = parser.to_compact(content, target)
        except AppError as exc:
            reason = exc.message if exc.code == TOO_LARGE else INVALID_CONTENT_REASON
            return _finish(_result(target, "rejected", reason), actor)

        # 第 4 步：根节点指纹。当前文件不存在（或本身解析不了）时没有可比对象，不据此拒绝。
        current = _current_bytes(target)
        if current is not None:
            old_fp = _root_fingerprint(current, target)
            new_fp = _fingerprint(document, content)
            if old_fp is not None and new_fp is not None and old_fp[0] != new_fp[0]:
                reason = ROOT_CHANGED_REASON % (old_fp[1], new_fp[1])
                return _finish(_result(target, "rejected", reason), actor)

        # 第 5、6 步：锁内确认「刚才比过的那份当前内容」没被并发换掉，然后交给 storage
        # 决定写不写、归不归档（R5：`unchanged` 由它判定，本模块不再自己比 sha）。
        with storage.profile_write(pid):
            if _sha(current) != _sha(_current_bytes(target)):
                result = _result(target, "rejected", RACE_REASON)
            else:
                saved = storage.save_config_file_detailed(target, content)
                if saved.unchanged:
                    result = _result(target, "unchanged", UNCHANGED_REASON)
                else:
                    reason = (
                        "已归档旧版本 %s" % saved.archived
                        if saved.archived
                        else "已写入本地文件（此前没有本地版本）"
                    )
                    result = _result(target, "success", reason, archived=saved.archived)

        if result["status"] == "success":
            revision.bump(pid, PART)
        return _finish(result, actor)


def update_all(pid: str, actor: str, task: Optional[Any] = None) -> Dict[str, Any]:
    """逐项执行 `update_source`（有 `record_url` 时再跑 `refresh_records`），并写 `schedule.json.last_run`。

    返回 `{"items": [结果…], "last_run": {"at","success","unchanged","failed","skipped"}}`。
    `task` 是卡 6 的 `Task`（§7.10）：不为 None 时把每一项结果与进度回填给它。
    这里**不**递增 `settings`——任务结束时的 `files`/`settings` 递增归卡 6 的 TaskRunner（§10.1）。
    """
    with storage.use_profile(pid):
        entries = [
            item
            for item in storage.load_config_mapping()
            if str(item.get("name") or "").strip()
        ]
        items: List[Dict[str, Any]] = []
        total = len(entries)
        _publish(task, 0, total, None)
        for done, entry in enumerate(entries, start=1):
            name = str(entry.get("name") or "")
            started = len(items)
            items.append(update_source(pid, name, actor))
            if str(entry.get("record_url") or "").strip():
                items.append(refresh_records(pid, name, actor))
            for item in items[started:]:
                _publish(task, done, total, item)
        last_run = _last_run(items)
        # §5.4：`schedule.json` 是**用户配置**，读-改-写必须在同一把机型锁里完成。
        # 分两次加锁的话，两次锁之间进来的 `PUT /schedule`（卡 7 的部署者设置）会被这次
        # 读到的旧副本整份覆盖掉——正是 §5.4 要防的丢失更新。`storage.save_schedule` 内部
        # 另取的 `_profile_write()` 解析到同一把 per-pid RLock，可重入、无死锁。
        with storage.profile_write(pid):
            schedule = storage.load_schedule()
            schedule["last_run"] = last_run
            storage.save_schedule(schedule)
    return {"items": items, "last_run": last_run}


def refresh_records(pid: str, name: str, actor: str) -> Dict[str, Any]:
    """§10.3 的**骨架**：只判定映射与 `record_url`，不下载、不落盘。

    主体（下载、与最新一条记录比 sha 去重、按 §5.3 命名保存）属卡 9（INH-1894）。
    签名与返回结构与卡 9 一致，届时只替换第 3 个分支的实现。
    """
    with storage.use_profile(pid):
        entry = _mapping_entry(name)
        if entry is None:
            return _result(name, "not_found", NO_MAPPING_REASON)
        target = str(entry.get("name") or "")
        if not str(entry.get("record_url") or "").strip():
            return _result(target, "skipped", NO_RECORD_URL_REASON)
        logger.info("修改记录拉取未实现（M3-09）：%s（actor=%s）", target, actor)
        return _result(target, "skipped", RECORDS_PENDING_REASON)


# ---------------------------------------------------------------------------
# §7.9 更新源映射 CRUD（持久化一律走 storage）
# ---------------------------------------------------------------------------


def list_sources(pid: str) -> List[Dict[str, Any]]:
    """`GET /sources`：映射表 + 本地文件时间 + 最近一次更新结果。"""
    with storage.use_profile(pid):
        status = storage.load_update_status()
        items: List[Dict[str, Any]] = []
        for entry in storage.load_config_mapping():
            name = str(entry.get("name") or "")
            if not name:
                continue
            items.append(
                {
                    "name": name,
                    "url": str(entry.get("url") or ""),
                    "record_url": str(entry.get("record_url") or ""),
                    "local_updated_at": _local_updated_at(name),
                    "last_result": status.get(name),
                }
            )
        return items


def create_source(pid: str, name: Any, url: Any, record_url: Any) -> Dict[str, Any]:
    """新增更新源（部署者）。`name` 走 `fsutil.validate_filename`（允许中文，C26）。

    不走 `storage.add_config_mapping`：那个函数对同名项是「覆盖 url」，而 §7.9 要的是
    409；并且它后面那步 `update_config_record_url` 走 M0-07 的 ASCII 白名单，
    会拒掉中文名。这里在一次机型锁里读-改-写整份映射，字段仍是 `name/url/record_url`。
    """
    clean_name = _validated_name(name)
    item = {
        "name": clean_name,
        "url": _validated_url(url, "url"),
        "record_url": _validated_url(record_url, "record_url", required=False),
    }
    with storage.use_profile(pid), storage.profile_write(pid):
        mapping = storage.load_config_mapping()
        if _entry_by_name(mapping, clean_name) is not None:
            raise AppError(FILE_EXISTS, "已存在同名更新源", name=clean_name, field="name")
        mapping.append(item)
        storage.save_config_mapping(mapping)
    revision.bump(pid, SETTINGS_PART)
    logger.info("新增更新源: %s", clean_name)
    return dict(item)


def rename_source(
    pid: str,
    original_name: Any,
    name: Any,
    url: Any,
    record_url: Any,
    migrate_files: bool = True,
) -> Dict[str, Any]:
    """改 url / 改 `record_url` / 改名（部署者）。

    改名与迁移**必须共用 `storage.update_config_mapping`**（M0-07 的实现：configs、archive、
    records 三份产物 + 收藏/绑定/建议/DL 绑定四处引用 + 失败 `_rollback`），
    这里不复制一套移动逻辑。目标已存在任一项时它抛 409 `FILE_EXISTS`。

    `update_status.json` 的键不在 M0-07 那份清单里（它是本卡新增的文件），所以由这里跟着搬——
    搬它是改名的一部分，不是可选的收尾：留旧键等于让状态列当场失真。
    """
    original = _validated_name(original_name)
    target = _validated_name(name)
    clean_url = _validated_url(url, "url")
    clean_record = _validated_url(record_url, "record_url", required=False)
    # 改名是一次跨多份数据的读-改-写（映射 + 五份引用 + 三处产物 + `update_status.json` 的键），
    # 整段扣在同一把机型锁里：`update_config_mapping` 内部另取的 `_profile_write()` 解析到
    # 同一把 per-pid RLock，可重入、无死锁（与 `create_source` 同形）。
    with storage.use_profile(pid), storage.profile_write(pid):
        if _mapping_entry(original) is None:
            raise AppError(NOT_FOUND, "更新源不存在", name=original)
        try:
            storage.update_config_mapping(
                original,
                target,
                clean_url,
                migrate_files=bool(migrate_files),
                new_record_url=clean_record,
            )
        except AppError as exc:
            # 前端要把冲突显示在「文件名」输入框下方（§7.1 的 `detail.field`）。
            if exc.code == FILE_EXISTS:
                exc.detail.setdefault("field", "name")
            raise
        except ValueError as exc:
            # `storage._sanitize_config_filename`（M0-07 的改名迁移）仍是 ASCII 白名单，
            # 裸 `ValueError` 到 HTTP 层会变成 500；这里只把它翻成 400，不改那条边界本身。
            # 上面已经把「名字（`fsutil`）与两个 url」都校验过，`update_config_mapping` 里
            # 唯一还能抛 `ValueError` 的就是那步净化。留一条 WARNING：万一净化内部真出了
            # 别的 `ValueError`，400 不会把它一路掩成「用户文件名写错了」。
            logger.warning("更新源改名在净化步被拒（%s -> %s）：%s", original, target, exc)
            raise AppError(INVALID_INPUT, str(exc), field="name") from exc
        if target != original:
            # 状态键按**映射名**走，与 `migrate_files` 无关：不搬的话改名后 `last_result` 恒 null。
            _retarget_update_status(original, target)
        entry = _mapping_entry(target)
        if entry is None:
            raise AppError(NOT_FOUND, "更新源不存在", name=original)
    revision.bump(pid, SETTINGS_PART)
    logger.info("更新更新源: %s -> %s", original, target)
    return dict(entry)


def delete_source(pid: str, name: Any) -> bool:
    """删除映射项（部署者）。**只删映射，不删文件**（§7.9），但会清掉 `update_status.json` 的键。

    旧键留着等于给下一个同名源预置了一条「最近结果」——用同一个名字重建时，新源会显示
    上一个同名源的更新历史，而这条历史从未发生在它身上。见 `_retarget_update_status`。
    """
    clean = _optional_validated_name(name)
    with storage.use_profile(pid):
        removed = bool(clean) and storage.remove_config_mapping(clean)
        if removed:
            _retarget_update_status(clean, None)
    if removed:
        revision.bump(pid, SETTINGS_PART)
        logger.info("删除更新源映射: %s", clean)
    return removed


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------


def _policy() -> downloader.DownloadPolicy:
    """下载策略完全来自启动配置（超时、重试、体积上限、白名单都不写死在这里）。"""
    return downloader.default_policy()


def _current_bytes(name: str) -> Optional[bytes]:
    """当前版本字节。§10.2 第 4/5 步在锁外读一次、第 6 步在锁内再读一次，共两次。"""
    return storage.load_config_file(name)


def _mapping_entry(name: str) -> Optional[Dict[str, Any]]:
    """按名字取映射项。

    名字先过 `fsutil.validate_filename`（§11.2）：非法名（含路径分隔符）一律当作
    「映射里没有」，绝不拿它去拼 `configs/` 下的路径。
    """
    clean = _optional_validated_name(name)
    if clean is None:
        return None
    return _entry_by_name(storage.load_config_mapping(), clean)


def _entry_by_name(mapping: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    for item in mapping:
        if str(item.get("name") or "") == name:
            return item
    return None


def _optional_validated_name(name: Any) -> Optional[str]:
    try:
        return fsutil.validate_filename(name)
    except AppError:
        return None


def _validated_name(name: Any) -> str:
    try:
        return fsutil.validate_filename(name)
    except AppError as exc:
        exc.detail["field"] = "name"
        raise


def _validated_url(value: Any, field: str, required: bool = True) -> str:
    text = str(value or "").strip()
    label = _URL_LABELS.get(field, "链接")
    if not text:
        if required:
            raise AppError(INVALID_INPUT, "%s不能为空" % label, field=field)
        return ""
    if not text.lower().startswith(("http://", "https://")):
        raise AppError(INVALID_INPUT, "%s需以 http:// 或 https:// 开头" % label, field=field)
    return text


def _local_updated_at(name: str) -> Optional[str]:
    """当前文件时间；过不了 §5.4 校验的名字（旧数据里的怪名字）按「没有本地版本」显示。"""
    clean = _optional_validated_name(name)
    if clean is None:
        return None
    return storage.get_file_update_date(clean)


def _root_fingerprint(content: bytes, name: str) -> Optional[Tuple[str, str]]:
    """当前文件的根节点指纹；当前文件解析不出来时返回 `None`（不据此拒绝）。"""
    try:
        document = parser.to_compact(content, name)
    except AppError:
        return None
    return _fingerprint(document, content)


def _fingerprint(
    document: Dict[str, Any], content: bytes
) -> Optional[Tuple[str, str]]:
    """§10.2 第 4 步的结构指纹：XML 取根元素 tag，JSON 取根类型（object/array）。

    返回 `(比较用键, 提示用名字)`。拿不到指纹就返回 `None`——没有可比对象时**不做拒绝**，
    否则一个坏掉的当前文件会把这条更新源永久卡在「无法替换」上。
    """
    nodes = document.get("nodes") or {}
    fmt = document.get("format")
    if fmt == "xml":
        ids = nodes.get("id") or []
        labels = nodes.get("l") or []
        # compact 的第 0 项是合成的文档根（`id="root"`），第 1 项才是真实根元素；
        # 它的旧 ID 恒为 `"root/" + 原始 tag`（带 `{uri}` 前缀），比显示标签稳：
        # 换个前缀写法不该被误判成「下载到了别的页面」。
        if len(ids) < 2 or not ids[1].startswith("root/"):
            return None
        tag = str(ids[1][len("root/") :])
        label = str(labels[1]) if len(labels) > 1 else tag
        return ("xml:" + tag, label)
    if fmt == "json":
        try:
            data = json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError):  # pragma: no cover - to_compact 已判过
            return None
        if isinstance(data, dict):
            return ("json:object", "object")
        if isinstance(data, list):
            return ("json:array", "array")
        return ("json:scalar", type(data).__name__)
    return None  # pragma: no cover - parser 只会给 xml/json


def _sha(content: Optional[bytes]) -> Optional[str]:
    return None if content is None else hashlib.sha256(content).hexdigest()


def _result(
    name: str, status: str, reason: str, archived: Optional[str] = None
) -> Dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "label": LABELS.get(status, status),
        "reason": reason,
        "at": _now(),
        "archived": archived,
    }


def _now() -> str:
    """服务器本地时间字符串（§7.1）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _finish(result: Dict[str, Any], actor: str) -> Dict[str, Any]:
    """§10.2 第 7 步：把本次结果合进 `update_status.json`，然后原样返回。

    调用方必须已处在 `storage.use_profile(pid)` 上下文里（写的是当前机型的状态文件）。
    合并是「读整份 → 改一个键 → 写整份」，按 §5.4 整段都要扣在同一把机型锁里：
    读与写之间分开的窗口里，另一个源的结果会被这份旧副本整份盖掉。卡 6 的 TaskRunner
    一旦放开 `update_source` 与 `refresh_records` 并发就会踩到，所以这里不指望调用方持锁。
    锁取当前机型的（`load_update_status` / `save_update_status` 都按 active profile 落盘）。
    """
    name = str(result.get("name") or "")
    logger.info(
        "更新源 %s: %s（actor=%s）%s", name, result.get("status"), actor, result.get("reason")
    )
    with storage.profile_write():
        status = storage.load_update_status()
        status[name] = {key: result[key] for key in ("status", "label", "reason", "at", "archived")}
        storage.save_update_status(status)
    return result


def _retarget_update_status(original: str, target: Optional[str]) -> None:
    """把 `update_status.json` 的键跟着改名/删除走（本卡自己定义的数据生命周期）。

    §7.9 的迁移清单没点这个名字（`update_status.json` 是本卡新增的文件），但不处理有三笔账：
    改名后 `GET /sources` 的 `last_result` 恒为 `null`（UC-31 的状态列当场失真）、旧键成为
    永久孤儿（文件只增不减）、删除后用同一个名字重建的新源会显示上一个同名源的「最近结果」。
    删除选**清除**而不是保留：保留就是第三笔账。规则已记进 `docs/know-how.md`。

    同样是读-改-写，整段扣在同一把机型锁里；键本来不存在时不写文件。
    """
    with storage.profile_write():
        status = storage.load_update_status()
        if original not in status:
            return
        payload = status.pop(original)
        if target is not None:
            status[target] = payload
        storage.save_update_status(status)


def _last_run(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """`schedule.json.last_run` 的汇总（§7.9 的 `GET /schedule` 直接显示这几个数）。"""
    counts = {"success": 0, "unchanged": 0, "failed": 0, "skipped": 0}
    for item in items:
        status = str(item.get("status") or "")
        if status == "success":
            counts["success"] += 1
        elif status == "unchanged":
            counts["unchanged"] += 1
        elif status in ("skipped", "not_found"):
            counts["skipped"] += 1
        else:
            counts["failed"] += 1
    counts["at"] = _now()
    return counts


def _publish(task: Optional[Any], done: int, total: int, item: Optional[Dict[str, Any]]) -> None:
    """把进度与结果回填给卡 6 的 `Task`（§7.10 的 `progress` / `result.items`）。"""
    if task is None:
        return
    if item is not None:
        task.result["items"].append(item)
    task.progress["done"] = done
    task.progress["total"] = total
