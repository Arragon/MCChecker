"""定时任务管理

使用 APScheduler 实现配置文件的定时自动更新。
"""

import logging
from datetime import datetime
from typing import Dict, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import storage, downloader, parse_cache

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
    schedule_config = storage.load_schedule()
    scheduler = get_scheduler()

    if scheduler.get_job("auto_update"):
        scheduler.remove_job("auto_update")

    if schedule_config.get("enabled", False):
        interval_hours = schedule_config.get("interval_hours", 24)
        scheduler.add_job(
            run_full_update,
            trigger=IntervalTrigger(hours=interval_hours),
            id="auto_update",
            name="全量自动更新",
            replace_existing=True,
        )
        logger.info("定时更新任务已设置: 每 %d 小时", interval_hours)
    else:
        logger.info("定时更新任务未启用")


def setup_cache_cleanup():
    scheduler = get_scheduler()

    if scheduler.get_job("archive_cache_cleanup"):
        scheduler.remove_job("archive_cache_cleanup")

    scheduler.add_job(
        lambda: parse_cache.cleanup_archive_tree_cache(ttl_days=7, max_entries=500),
        trigger=IntervalTrigger(hours=24),
        id="archive_cache_cleanup",
        name="归档解析缓存清理",
        replace_existing=True,
    )


def run_full_update() -> Dict:
    """执行全量更新"""
    mapping = storage.load_config_mapping()
    results = {
        "total": len(mapping),
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "details": [],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    for item in mapping:
        name = item["name"]
        url = item["url"]

        if not url:
            results["skipped"] += 1
            results["details"].append({"name": name, "status": "skipped", "reason": "无下载链接"})
            continue

        content = downloader.download_file(url)
        if content is not None:
            try:
                storage.save_config_file(name, content)
                results["success"] += 1
                results["details"].append({"name": name, "status": "success", "size": len(content)})
            except Exception as e:
                results["failed"] += 1
                results["details"].append({"name": name, "status": "error", "reason": str(e)})
        else:
            results["failed"] += 1
            results["details"].append({"name": name, "status": "download_failed"})

    logger.info("全量更新完成: 成功 %d, 失败 %d, 跳过 %d", results["success"], results["failed"], results["skipped"])
    return results


def update_schedule_config(enabled: bool, interval_hours: float) -> Dict:
    """更新调度配置"""
    config = {"enabled": enabled, "interval_hours": interval_hours}
    storage.save_schedule(config)
    setup_scheduled_update()
    return config
