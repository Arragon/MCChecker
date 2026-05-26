"""通用工具函数"""

import os
import hashlib
from datetime import datetime
from typing import Any, Dict, List


def generate_id(*args) -> str:
    """基于参数生成唯一 ID"""
    content = "|".join(str(a) for a in args)
    return hashlib.md5(content.encode()).hexdigest()[:12]


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def get_file_extension(filename: str) -> str:
    """获取文件扩展名"""
    if "." in filename:
        return filename.rsplit(".", 1)[-1].lower()
    return ""


def safe_filename(filename: str) -> str:
    """清理文件名，移除不安全字符"""
    unsafe_chars = '<>:"/\\|?*'
    for ch in unsafe_chars:
        filename = filename.replace(ch, "_")
    return filename


def timestamp_str() -> str:
    """当前时间戳字符串"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
