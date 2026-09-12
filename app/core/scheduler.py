"""定时任务管理

使用 APScheduler 实现配置文件的定时自动更新。
包含统一更新管线 UpdatePipeline (T09 / INH-621)。
"""

import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import storage, downloader, parse_cache, device_models
from .parser import compute_content_hash

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None


# ---------------------------------------------------------------------------
# 防重入锁 (T09 / INH-621)
# ---------------------------------------------------------------------------

_update_locks: Dict[str, threading.Lock] = {}
_update_locks_guard = threading.Lock()


def get_update_lock(profile_id: str, resource: str) -> threading.Lock:
    """按 profile+resource 防重入

    同一 profile 同一资源（config name）同时只允许一个更新操作。
    不同 profile 或不同资源之间互不阻塞。
    """
    key = f"{profile_id}:{resource}"
    with _update_locks_guard:
        if key not in _update_locks:
            _update_locks[key] = threading.Lock()
        return _update_locks[key]


# ---------------------------------------------------------------------------
# 内容验证辅助 (T09 / INH-621)
# ---------------------------------------------------------------------------

_HTML_CONTENT_TYPES = frozenset({
    b'<!doctype html',
    b'<html',
    b'<head',
    b'<body',
})


def _looks_like_html_error(content: bytes) -> bool:
    """检测下载内容是否为 HTML 错误页面（200 状态码但返回 HTML 错误）"""
    stripped = content.lstrip()[:200].lower()
    return any(marker in stripped for marker in _HTML_CONTENT_TYPES)


def _detect_format(filename: str, content: bytes) -> str:
    """根据文件名和内容推断格式: xml / json / unknown"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "xml":
        return "xml"
    if ext == "json":
        return "json"
    # 根据内容推断
    stripped = content.lstrip()[:1]
    if stripped.startswith(b"<"):
        return "xml"
    if stripped.startswith(b"{") or stripped.startswith(b"["):
        return "json"
    return "unknown"


def _extract_root_tag(content: bytes) -> Optional[str]:
    """提取 XML 根元素 tag（展开命名空间）"""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(content)
        return root.tag
    except ET.ParseError:
        return None


def _extract_json_root_type(content: bytes) -> Optional[str]:
    """提取 JSON 根类型: object / array / scalar"""
    import json
    try:
        text = content.decode("utf-8-sig")
        data = json.loads(text)
        if isinstance(data, dict):
            return "object"
        if isinstance(data, list):
            return "array"
        return "scalar"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 统一更新管线 (T09 / INH-621)
# ---------------------------------------------------------------------------


class UpdatePipeline:
    """统一更新管线

    将 manual update、scheduler update、record refresh 的执行方式统一为:
    1. download to staging
    2. content validation (HTML error / malformed / too large)
    3. parse
    4. structural fingerprint check
    5. content hash comparison (skip duplicate archive)
    6. atomic commit (archive + current)
    7. derive diff/cache

    使用 T02 safe_download、T04 atomic_write_json、T01 authorize。
    """

    def execute(
        self,
        source_url: str,
        target_name: str,
        profile_id: str,
        *,
        is_scheduled: bool = False,
        is_record: bool = False,
        actor=None,
    ) -> dict:
        """执行统一更新流程。

        Args:
            source_url: 下载 URL
            target_name: 目标文件名（config name）
            profile_id: 目标 profile
            is_scheduled: 是否为定时任务触发
            is_record: 是否为修改记录更新
            actor: 操作者身份 (Actor)，None 时默认 SYSTEM_ACTOR

        Returns:
            dict: {status, errors, new_hash, ...}
        """
        from app.utils.auth import authorize, SYSTEM_ACTOR

        result: Dict = {"status": "pending", "errors": [], "name": target_name}

        if actor is None:
            actor = SYSTEM_ACTOR

        # T01: 授权检查
        try:
            authorize(actor, "write", profile_id)
        except Exception as e:
            result["status"] = "unauthorized"
            result["errors"].append(str(e))
            return result

        # 防重入
        lock = get_update_lock(profile_id, target_name)
        acquired = lock.acquire(blocking=False)
        if not acquired:
            result["status"] = "busy"
            result["errors"].append("Another update is in progress for this resource")
            return result

        try:
            with storage.use_profile(profile_id):
                return self._do_update(
                    source_url, target_name, profile_id,
                    is_scheduled=is_scheduled,
                    is_record=is_record,
                    result=result,
                )
        finally:
            lock.release()

    def _do_update(
        self,
        source_url: str,
        target_name: str,
        profile_id: str,
        *,
        is_scheduled: bool,
        is_record: bool,
        result: dict,
    ) -> dict:
        """内部更新实现（在 profile context 和锁内执行）"""

        # 1. Download to staging
        try:
            staging_path = self._download_to_staging(source_url, target_name)
        except Exception as e:
            result["status"] = "download_failed"
            result["errors"].append(f"Download failed: {e}")
            return result

        try:
            # 读取 staging 内容
            staging_content = staging_path.read_bytes()

            # 2. Validate: HTML error page
            if _looks_like_html_error(staging_content):
                result["status"] = "rejected"
                result["errors"].append("Downloaded content appears to be an HTML error page")
                return result

            # 2b. Validate: empty content
            if not staging_content:
                result["status"] = "rejected"
                result["errors"].append("Downloaded content is empty")
                return result

            # 3. Parse check (仅对 config 文件，record 文件为纯文本跳过)
            if not is_record:
                new_format = _detect_format(target_name, staging_content)
                try:
                    if new_format == "xml":
                        import xml.etree.ElementTree as ET
                        import io
                        ET.parse(io.BytesIO(staging_content))
                    elif new_format == "json":
                        import json
                        json.loads(staging_content.decode("utf-8-sig"))
                except Exception as e:
                    result["status"] = "rejected"
                    result["errors"].append(f"Parse validation failed: {e}")
                    return result
            else:
                new_format = "text"

            # 4. Structural fingerprint check
            if not is_record:
                fp_ok, fp_info = self._check_fingerprint(
                    staging_content, target_name, new_format
                )
                if not fp_ok:
                    if is_scheduled:
                        result["status"] = "rejected"
                        result["errors"].append(
                            f"Structural fingerprint mismatch: {fp_info}"
                        )
                        return result
                    else:
                        result["status"] = "needs_confirmation"
                        result["fingerprint_diff"] = fp_info
                        return result

            # 5. Content hash comparison
            new_hash = compute_content_hash(staging_content)

            if not is_record:
                current_hash = self._get_current_hash(target_name)
                if current_hash and new_hash == current_hash:
                    result["status"] = "unchanged"
                    result["new_hash"] = new_hash
                    return result

            # 6. Atomic commit
            if is_record:
                path = storage.save_record_file(
                    target_name, staging_content, source_url=source_url
                )
                result["path"] = path
            else:
                storage.save_config_file(target_name, staging_content)

            result["status"] = "success"
            result["new_hash"] = new_hash
            result["size"] = len(staging_content)

        finally:
            # 清理 staging
            try:
                staging_path.unlink(missing_ok=True)
            except OSError:
                pass

        return result

    def _download_to_staging(self, url: str, target_name: str) -> Path:
        """使用 T02 safe_download 下载到 staging 区域"""
        configs_dir = Path(storage.get_configs_dir())
        staging_dir = configs_dir.parent / ".staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        staging_path = staging_dir / f"update_{uuid.uuid4().hex[:8]}_{target_name}"

        downloader.safe_download(url, staging_path)
        return staging_path

    def _get_current_hash(self, target_name: str) -> Optional[str]:
        """获取当前文件的 content hash"""
        content = storage.load_config_file(target_name)
        if content is None:
            return None
        return compute_content_hash(content)

    def _check_fingerprint(
        self,
        new_content: bytes,
        target_name: str,
        new_format: str,
    ) -> tuple:
        """结构指纹校验。

        比较:
        - 文件格式 (xml/json)
        - XML 根元素 tag
        - JSON 根类型 (object/array)

        Returns:
            (ok: bool, info: str)
        """
        current_content = storage.load_config_file(target_name)
        if current_content is None:
            return True, "new file"

        current_format = _detect_format(target_name, current_content)

        if new_format != current_format:
            return False, f"format changed: {current_format} -> {new_format}"

        if new_format == "xml":
            old_tag = _extract_root_tag(current_content)
            new_tag = _extract_root_tag(new_content)
            if old_tag and new_tag and old_tag != new_tag:
                return False, f"XML root tag changed: {old_tag} -> {new_tag}"

        if new_format == "json":
            old_type = _extract_json_root_type(current_content)
            new_type = _extract_json_root_type(new_content)
            if old_type and new_type and old_type != new_type:
                return False, f"JSON root type changed: {old_type} -> {new_type}"

        return True, "fingerprint match"


# 全局 pipeline 实例
update_pipeline = UpdatePipeline()


# ---------------------------------------------------------------------------
# 调度器
# ---------------------------------------------------------------------------


def get_scheduler() -> BackgroundScheduler:
    """获取全局调度器实例"""
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
        _scheduler.start()
        logger.info("定时调度器已启动")
    return _scheduler


def setup_scheduled_update():
    """根据持久化配置设置定时更新任务"""
    scheduler = get_scheduler()

    for job in scheduler.get_jobs():
        if job.id.startswith("auto_update:"):
            scheduler.remove_job(job.id)

    for m in device_models.load_models():
        mid = m.get("id")
        if not mid:
            continue
        with storage.use_profile(mid):
            schedule_config = storage.load_schedule()
        if not schedule_config.get("enabled", False):
            continue
        interval_hours = schedule_config.get("interval_hours", 24)
        scheduler.add_job(
            lambda profile_id=mid: run_full_update(profile_id),
            trigger=IntervalTrigger(hours=interval_hours),
            id=f"auto_update:{mid}",
            name=f"全量自动更新({mid})",
            replace_existing=True,
        )
        logger.info("定时更新任务已设置: %s 每 %d 小时", mid, interval_hours)


def setup_cache_cleanup():
    scheduler = get_scheduler()

    if scheduler.get_job("archive_cache_cleanup"):
        scheduler.remove_job("archive_cache_cleanup")

    scheduler.add_job(
        cleanup_all_profiles_cache,
        trigger=IntervalTrigger(hours=24),
        id="archive_cache_cleanup",
        name="归档解析缓存清理",
        replace_existing=True,
    )


def cleanup_all_profiles_cache() -> Dict[str, Dict[str, int]]:
    stats: Dict[str, Dict[str, int]] = {}
    for m in device_models.load_models():
        mid = m.get("id")
        if not mid:
            continue
        with storage.use_profile(mid):
            stats[mid] = parse_cache.cleanup_archive_tree_cache(ttl_days=7, max_entries=500)
    return stats


def run_full_update(profile_id: Optional[str] = None) -> Dict:
    """执行全量更新"""
    effective_profile = profile_id if profile_id is not None else storage.get_active_profile()
    with storage.use_profile(effective_profile):
        mapping = storage.load_config_mapping()
        results = {
            "profile": effective_profile,
            "total": len(mapping),
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "details": [],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        for item in mapping:
            r = run_single_update(item.get("name"), effective_profile, mapping_item=item, is_scheduled=True)
            results["details"].append(r)
            status = r.get("status")
            if status == "success":
                results["success"] += 1
            elif status in ("skipped", "unchanged"):
                results["skipped"] += 1
            else:
                results["failed"] += 1
            try:
                run_single_record_update(item.get("name"), effective_profile, mapping_item=item, is_scheduled=True)
            except Exception:
                pass

    logger.info("全量更新完成(%s): 成功 %d, 失败 %d, 跳过 %d", effective_profile, results["success"], results["failed"], results["skipped"])
    return results


def run_single_update(name: str, profile_id: Optional[str] = None, *, mapping_item: Optional[Dict] = None, is_scheduled: bool = False, actor=None) -> Dict:
    """执行单个配置文件更新（通过 UpdatePipeline）"""
    effective_profile = profile_id if profile_id is not None else storage.get_active_profile()

    if not name:
        return {"name": name, "status": "error", "reason": "缺少文件名"}

    item = mapping_item
    if item is None:
        with storage.use_profile(effective_profile):
            mapping = storage.load_config_mapping()
            item = next((m for m in mapping if m.get("name") == name), None)
    if not item:
        return {"name": name, "status": "not_found"}

    url = item.get("url")
    if not url:
        return {"name": name, "status": "skipped", "reason": "无下载链接"}

    return update_pipeline.execute(
        source_url=url,
        target_name=name,
        profile_id=effective_profile,
        is_scheduled=is_scheduled,
        is_record=False,
        actor=actor,
    )


def run_single_record_update(name: str, profile_id: Optional[str] = None, *, mapping_item: Optional[Dict] = None, is_scheduled: bool = False, actor=None) -> Dict:
    """执行单个修改记录更新（通过 UpdatePipeline）"""
    effective_profile = profile_id if profile_id is not None else storage.get_active_profile()

    if not name:
        return {"name": name, "status": "error", "reason": "缺少文件名"}

    item = mapping_item
    if item is None:
        with storage.use_profile(effective_profile):
            mapping = storage.load_config_mapping()
            item = next((m for m in mapping if m.get("name") == name), None)
    if not item:
        return {"name": name, "status": "not_found"}

    record_url = (item.get("record_url") or "").strip()
    if not record_url:
        return {"name": name, "status": "skipped", "reason": "无修改记录链接"}

    return update_pipeline.execute(
        source_url=record_url,
        target_name=name,
        profile_id=effective_profile,
        is_scheduled=is_scheduled,
        is_record=True,
        actor=actor,
    )


def update_schedule_config(enabled: bool, interval_hours: float) -> Dict:
    """更新调度配置"""
    config = {"enabled": enabled, "interval_hours": interval_hours}
    storage.save_schedule(config)
    setup_scheduled_update()
    return config


def get_auto_refresh_interval_seconds(profile_id: Optional[str] = None, *, min_seconds: float = 60.0) -> Optional[float]:
    effective_profile = profile_id if profile_id is not None else storage.get_active_profile()
    with storage.use_profile(effective_profile):
        cfg = storage.load_schedule()
    if not cfg.get("enabled", False):
        return None
    hours = cfg.get("interval_hours", 24) or 24
    try:
        secs = float(hours) * 3600.0
    except Exception:
        secs = 24.0 * 3600.0
    try:
        ms = float(min_seconds or 0.0)
    except Exception:
        ms = 60.0
    return max(secs, ms)
