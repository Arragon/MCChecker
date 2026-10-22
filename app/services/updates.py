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

例外二：`refresh_records`（§10.3）**不写** `update_status.json`。那个文件的键是映射名、
存的是「这个源的**配置**最近一次更新结果」（§5.2、UC-31 的状态列）；`update_all` 会按
同一个映射名先跑 `update_source` 再跑 `refresh_records`，记录拉取要是也往里写，就会
**每次都用记录的结果把配置的最近结果覆盖掉**。所以记录的结果只进任务条目
（§7.10 的 `result.items`），不进状态文件。

下载策略全部来自启动配置（`downloader.default_policy()`）。`block_loopback` 保持默认
`False`：§10.2 只对**访客临时导入**额外禁止回环，那一条路径在 `services/upload.py`
（不在本卡范围，认领结论见卡报告 R31）。修改记录走**同一个** `downloader.fetch` 与同一份
策略：白名单 `MCHECKER_DOWNLOAD_ALLOW`、逐跳重定向复核、超时与重试都在这条路上生效，
本模块不写第二个取数原语。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..core import bundle, downloader, fsutil, parser, storage
from ..core.errors import (
    FILE_EXISTS,
    INVALID_INPUT,
    NOT_FOUND,
    TOO_LARGE,
    AppError,
)
from . import config_policies, revision

logger = logging.getLogger(__name__)

#: 写入了**新的权威原文字节**之后递增的修订分量（§7.3）。配置更新（§10.2 第 7 步）与
#: 修改记录（§10.3）共用它，也共用同一条判据：字节真的变了才通知别人（R5）。
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
#: §10.3 / C13：内容与最新一条记录逐字节相同 → `unchanged`，**不新建记录文件**。
RECORD_UNCHANGED_REASON = "内容与最新一条修改记录一致，未新建记录文件"
RECORD_SAVED_REASON = "已保存新的修改记录 %s"

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

        # 第 3 步起：单个 XML/JSON 仍按本条文件名提交；压缩包先解开，再按正则关键词切削文件名。
        keyword = str(entry.get("keyword") or "").strip()
        try:
            pieces = bundle.expand_update_payload(
                content,
                keyword=keyword,
                source_name=target,
                remote_name=_remote_basename(url),
                max_bytes=_policy().max_bytes,
            )
        except AppError as exc:
            status = "too_large" if exc.code == TOO_LARGE else "rejected"
            return _finish(_result(target, status, exc.message), actor)

        outcomes = [_commit_config(pid, name, body) for name, body in pieces]
        if any(item["status"] == "success" for item in outcomes):
            revision.bump(pid, PART)
        return _publish_outcomes(target, outcomes, actor)


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
    """§10.3 拉取修改记录：下载 → 与**最新一条**比 sha256 → 相同就不新建文件（C13）。

    与 §10.2 的四点区别，都要保住：

    1. **不校验可解析性、不校验根节点**：§10.3 明写「记录内容不要求可解析」，解析不了的
       在查看时走原文下载（`documents` 对 `version=record:` 已经给出 422 + `raw_available`）。
       因此记录的结果里**没有** `rejected` 这个取值。
    2. **不写 `update_status.json`**（见模块 docstring 的「例外二」）：那个键属于配置更新，
       `update_all` 按同一个映射名连着跑两者，写进来就会互相覆盖。
    3. **「变没变」的基准是 `records/<name>/` 里最新一条**，不是当前配置文件。判据是
       逐字节 sha256，与 R5 同口径；`unchanged` 不新建文件、不递增 `files`。
    4. **命名与落盘交给 `storage.save_record_file`**（§5.3：`record_<YYYYMMDD>_<HHMMSS><ext>`，
       ext 取自 `record_url` 路径且 ≤10 个字母数字，否则 `.txt`；同秒冲突追加 `_2`…），
       这里不另起一套。

    「读最新一条 → 比 sha → 写新文件」是一次读-改-写，整段扣在同一把机型锁里（§5.4）：
    分两次加锁的话，并发两次拉同一份新内容会各自读到「没有记录」，然后各写一个文件——
    机型锁只保证单次写不被撕裂，不会让两次互相覆盖丢失。网络 I/O 仍然在锁外（§3.5），
    所以两次并发下载照旧发生，只是第二次进锁后看到的是第一次写好的那份记录 ⇒ `unchanged`。
    """
    with storage.use_profile(pid):
        entry = _mapping_entry(name)
        if entry is None:
            return _result(name, "not_found", NO_MAPPING_REASON)
        target = str(entry.get("name") or "")
        record_url = str(entry.get("record_url") or "").strip()
        if not record_url:
            return _result(target, "skipped", NO_RECORD_URL_REASON)

        try:
            content = downloader.fetch(record_url, _policy())
        except AppError as exc:
            status = "too_large" if exc.code == TOO_LARGE else "download_failed"
            result = _result(target, status, exc.message)
            logger.info(
                "修改记录拉取 %s: %s（actor=%s）%s", target, status, actor, result["reason"]
            )
            return result

        with storage.profile_write(pid):
            latest = _latest_record_bytes(target)
            if latest is not None and _sha(latest) == _sha(content):
                result = _result(target, "unchanged", RECORD_UNCHANGED_REASON)
            else:
                saved = storage.save_record_file(target, content, source_url=record_url)
                result = _result(target, "success", RECORD_SAVED_REASON % os.path.basename(saved))

        logger.info(
            "修改记录拉取 %s: %s（actor=%s）%s",
            target,
            result["status"],
            actor,
            result["reason"],
        )
        if result["status"] == "success":
            # 只有真的多了一份记录文件才通知别人（R5）：`unchanged` 与失败都不递增，
            # 否则「一条记录都没新增」会把全部客户端的横幅点起来。
            revision.bump(pid, PART)
        return result


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
                    "keyword": str(entry.get("keyword") or ""),
                    "local_updated_at": _local_updated_at(name),
                    "last_result": status.get(name),
                }
            )
        return items


def create_source(
    pid: str, name: Any, url: Any, record_url: Any, keyword: Any = None
) -> Dict[str, Any]:
    """新增更新源（部署者）。`name` 走 `fsutil.validate_filename`（允许中文，C26）。

    不走 `storage.add_config_mapping`：那个函数对同名项是「覆盖 url」，而 §7.9 要的是 409。
    `keyword` 可空；非空时才写入映射，避免旧数据多一个空字段。
    """
    clean_name = _validated_name(name)
    item = {
        "name": clean_name,
        "url": _validated_url(url, "url"),
        "record_url": _validated_url(record_url, "record_url", required=False),
    }
    clean_keyword = _validated_keyword(keyword)
    if clean_keyword:
        item["keyword"] = clean_keyword
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
    keyword: Any = None,
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
    # None：请求没带 keyword，保留原值。空串：清掉关键词。
    clean_keyword = None if keyword is None else _validated_keyword(keyword)
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
            # `storage.update_config_mapping` 里剩下的裸 `ValueError` 到 HTTP 层会变成 500，
            # 所以这里翻成 400。文件名净化那一步已在 N1 收敛到 `fsutil.validate_filename`
            # （它抛 `AppError`，走上面那条分支），这条兜的是「下载链接不能为空」之类的
            # 存储层校验。上面已经把两个名字与两个 url 都校验过，正常情况下到这里就该是
            # 存储层的意外，所以留一条 WARNING：裸 `ValueError` 不加日志会被一路掩成
            # 「用户文件名写错了」，bug 就没痕迹了。
            logger.warning("更新源改名在净化步被拒（%s -> %s）：%s", original, target, exc)
            raise AppError(INVALID_INPUT, str(exc), field="name") from exc
        if clean_keyword is not None:
            mapping = storage.load_config_mapping()
            found = _entry_by_name(mapping, target)
            if found is not None:
                if clean_keyword:
                    found["keyword"] = clean_keyword
                else:
                    found.pop("keyword", None)
                storage.save_config_mapping(mapping)
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


def _commit_config(pid: str, name: str, content: bytes) -> Dict[str, Any]:
    """校验并提交一份配置。不写状态文件、不递增修订号，由调用方汇总。

    M6-06 的策略守卫在**最前面**：受管模式下未登记的文件不再直接落盘，而是回
    `blocked`（单条结果），让整批任务继续跑完、由结果汇总体现，而不是整批失败。
    """
    try:
        config_policies.guard_or_raise(
            config_policies.repository(),
            config_policies.ACTION_SCHEDULE_DOWNLOAD,
            filename=name,
            profile_id=pid,
        )
    except AppError as exc:
        return _result(name, "blocked", exc.message)

    try:
        document = parser.to_compact(content, name)
    except AppError as exc:
        reason = exc.message if exc.code == TOO_LARGE else INVALID_CONTENT_REASON
        return _result(name, "rejected", reason)

    current = _current_bytes(name)
    if current is not None:
        old_fp = _root_fingerprint(current, name)
        new_fp = _fingerprint(document, content)
        if old_fp is not None and new_fp is not None and old_fp[0] != new_fp[0]:
            return _result(name, "rejected", ROOT_CHANGED_REASON % (old_fp[1], new_fp[1]))

    with storage.profile_write(pid):
        if _sha(current) != _sha(_current_bytes(name)):
            return _result(name, "rejected", RACE_REASON)
        saved = storage.save_config_file_detailed(name, content)
        if saved.unchanged:
            return _result(name, "unchanged", UNCHANGED_REASON)
        reason = (
            "已归档旧版本 %s" % saved.archived
            if saved.archived
            else "已写入本地文件（此前没有本地版本）"
        )
        return _result(name, "success", reason, archived=saved.archived)


def _publish_outcomes(
    source_name: str, outcomes: List[Dict[str, Any]], actor: str
) -> Dict[str, Any]:
    """多文件时，每份结果记在自己的文件名下；更新源这一行记汇总。"""
    summary = _summarize(source_name, outcomes)
    for outcome in outcomes:
        if outcome["name"] != summary["name"]:
            _finish(outcome, actor)
    return _finish(summary, actor)


def _summarize(source_name: str, outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(outcomes) == 1 and outcomes[0]["name"] == source_name:
        return outcomes[0]
    statuses = [str(item.get("status") or "") for item in outcomes]
    if any(status == "success" for status in statuses):
        status = "success"
    elif statuses and all(status == "unchanged" for status in statuses):
        status = "unchanged"
    else:
        status = "rejected"
    reason = "；".join("%s：%s" % (item["name"], item["reason"]) for item in outcomes)
    archived = outcomes[0].get("archived") if len(outcomes) == 1 else None
    return _result(source_name, status, reason, archived=archived)


def _remote_basename(url: str) -> str:
    path = urllib.parse.urlsplit(str(url or "")).path
    name = path.rsplit("/", 1)[-1]
    return urllib.parse.unquote(name)


_KEYWORD_MAX = 200


def _validated_keyword(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > _KEYWORD_MAX:
        raise AppError(INVALID_INPUT, "正则关键词过长", field="keyword")
    try:
        re.compile(text)
    except re.error as exc:
        raise AppError(INVALID_INPUT, "正则关键词无法编译：%s" % exc, field="keyword")
    return text


def _policy() -> downloader.DownloadPolicy:
    """下载策略完全来自启动配置（超时、重试、体积上限、白名单都不写死在这里）。"""
    return downloader.default_policy()


def _current_bytes(name: str) -> Optional[bytes]:
    """当前版本字节。§10.2 第 4/5 步在锁外读一次、第 6 步在锁内再读一次，共两次。"""
    return storage.load_config_file(name)


def _latest_record_bytes(name: str) -> Optional[bytes]:
    """`records/<name>/` 里**最新一条**记录的字节；没有任何记录时返回 `None`。

    `None` 是「没有可比对象」而不是「内容相同」，所以第一次拉取一定走 `success`——
    这点与 §10.2 第 4 步的根指纹同口径（拿不到基准时不做拒绝、也不做去重）。

    **顺序有两个出处，它们不是一回事，这里只跟第一个**（旧 docstring 说「history.py 也用
    `list_record_versions`，两处读不出不同的最新」——那是错的，`services/history.py:84`
    走的是自己的 `_list_versions`）：

    * `storage.list_record_versions`（本函数）：排序键是 mtime **截断到秒**的字符串，
      同秒之内由文件名的**逆字典序**定先后。
    * `services/history.py::_list_versions`（`GET /files/{name}/records` 的列表，也就是
      用户看到的「最新一条」顶行）：排序键是 **float mtime**，不看文件名，另跳过
      `.diff.json`。

    所以同一秒落盘的两条（改过一次 `record_url` 的扩展名就是这个形状）会让两边各挑一个
    「最新」：基准挑了写得早的那份时，内容其实等于顶行的拉取会被判成「变了」——多写一份
    记录并 `files` +1；反向则是该新建的没新建。损伤面只有通知口径与一条多余记录，
    权威数据不受影响。本卡（M3-09 修复轮）只把这个分叉钉住、不对齐：要对齐就得改
    `storage.list_record_versions` 的排序键，而它同时被旧 NiceGUI 页面
    `app/pages/records.py` 用（H2：M3 期间不动那页），改在 `updates.py` 里则会出现第三个
    排序出处。哨兵在 `tests/test_api_records.py::test_same_second_records_are_ordered_two_different_ways`，
    将来统一两侧排序时**跟着那条一起改**，别把它改成恒真。
    """
    clean = _optional_validated_name(name)
    if clean is None:
        return None
    versions = storage.list_record_versions(clean)
    if not versions:
        return None
    filename = str(versions[0].get("filename") or "")
    if not filename:  # pragma: no cover - 目录里列出来的名字必然非空
        return None
    return storage.load_record_file(clean, filename)


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
