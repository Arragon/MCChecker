"""定时任务管理

使用 APScheduler 实现配置文件的定时自动更新。
"""

import logging
from datetime import datetime
from typing import Dict, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import storage, downloader, parse_cache, device_models

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None


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
            r = run_single_update(item.get("name"), effective_profile, mapping_item=item)
            results["details"].append(r)
            status = r.get("status")
            if status == "success":
                results["success"] += 1
            elif status == "skipped":
                results["skipped"] += 1
            else:
                results["failed"] += 1
            try:
                run_single_record_update(item.get("name"), effective_profile, mapping_item=item)
            except Exception:
                pass

    logger.info("全量更新完成(%s): 成功 %d, 失败 %d, 跳过 %d", effective_profile, results["success"], results["failed"], results["skipped"])
    return results


def run_single_update(name: str, profile_id: Optional[str] = None, *, mapping_item: Optional[Dict] = None) -> Dict:
    effective_profile = profile_id if profile_id is not None else storage.get_active_profile()
    with storage.use_profile(effective_profile):
        if not name:
            return {"name": name, "status": "error", "reason": "缺少文件名"}

        item = mapping_item
        if item is None:
            mapping = storage.load_config_mapping()
            item = next((m for m in mapping if m.get("name") == name), None)
        if not item:
            return {"name": name, "status": "not_found"}

        url = item.get("url")
        if not url:
            return {"name": name, "status": "skipped", "reason": "无下载链接"}

        content = downloader.download_file(url)
        if content is None:
            return {"name": name, "status": "download_failed"}

        try:
            storage.save_config_file(name, content)
            return {"name": name, "status": "success", "size": len(content)}
        except Exception as e:
            return {"name": name, "status": "error", "reason": str(e)}


def run_single_record_update(name: str, profile_id: Optional[str] = None, *, mapping_item: Optional[Dict] = None) -> Dict:
    effective_profile = profile_id if profile_id is not None else storage.get_active_profile()
    with storage.use_profile(effective_profile):
        if not name:
            return {"name": name, "status": "error", "reason": "缺少文件名"}

        item = mapping_item
        if item is None:
            mapping = storage.load_config_mapping()
            item = next((m for m in mapping if m.get("name") == name), None)
        if not item:
            return {"name": name, "status": "not_found"}

        record_url = (item.get("record_url") or "").strip()
        if not record_url:
            return {"name": name, "status": "skipped", "reason": "无修改记录链接"}

        content = downloader.download_file(record_url)
        if content is None:
            return {"name": name, "status": "download_failed"}

        try:
            path = storage.save_record_file(name, content, source_url=record_url)
            return {"name": name, "status": "success", "path": path, "size": len(content)}
        except Exception as e:
            return {"name": name, "status": "error", "reason": str(e)}


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
