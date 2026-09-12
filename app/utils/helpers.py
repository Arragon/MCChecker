"""通用工具函数"""

import os
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse


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


# ---------------------------------------------------------------------------
# 输入边界验证 (T02 / INH-613)
# ---------------------------------------------------------------------------

_WINDOWS_RESERVED = frozenset({
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
})


def validate_filename(name: str) -> str:
    """验证文件名安全。

    规则:
    - 拒绝空名、``.``、``..``
    - 拒绝路径分隔符 (``/``、``\\``)、冒号、NUL 字节
    - 拒绝 Windows 保留设备名 (CON, NUL …)
    - 拒绝 ADS (Alternate Data Streams) 和尾部点号
    - 允许安全 Unicode 文件名

    Returns:
        原始 *name*（验证通过）

    Raises:
        ValueError: 文件名不合法
    """
    if not name or name in (".", ".."):
        raise ValueError(f"Invalid filename: {name!r}")

    # 拒绝路径分隔符、冒号（盘符 / ADS）和 NUL
    if any(c in name for c in ('/', '\\', ':', '\x00')):
        raise ValueError(f"Filename contains forbidden characters: {name!r}")

    # 拒绝尾部点号
    if name.endswith('.'):
        raise ValueError(f"Filename must not end with '.': {name!r}")

    # 拒绝 Windows 保留设备名
    base_name = name.split('.')[0].upper()
    if base_name in _WINDOWS_RESERVED:
        raise ValueError(f"Windows reserved name: {name!r}")

    return name


def resolve_within(root: Path, relative: str) -> Path:
    """解析 *relative* 并验证结果仍在 *root* 内。

    处理 ``..``、symlink 等越界尝试。

    Returns:
        解析后的绝对 :class:`~pathlib.Path`

    Raises:
        ValueError: 路径越界
    """
    root = Path(root).resolve()
    target = (root / relative).resolve()

    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError(f"Path escape attempt: {relative!r}")

    return target


def safe_external_url(url: str) -> bool:
    """验证 URL scheme，只允许 http/https/mailto。

    用于工具链接、外部跳转等场景，防止 javascript:/data: 等危险协议。
    """
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in ('http', 'https', 'mailto')
