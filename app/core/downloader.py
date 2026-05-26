"""文件下载器

从 URL 下载配置文件，支持重试。
"""

import logging
import urllib.request
import urllib.error
from typing import Optional

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
TIMEOUT_SECONDS = 30


def download_file(url: str, retries: int = MAX_RETRIES) -> Optional[bytes]:
    """从 URL 下载文件内容

    Args:
        url: 文件下载链接
        retries: 最大重试次数

    Returns:
        文件内容字节，失败返回 None
    """
    for attempt in range(1, retries + 1):
        try:
            logger.info("下载文件: %s (尝试 %d/%d)", url, attempt, retries)
            req = urllib.request.Request(url, headers={"User-Agent": "MCChecker/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                content = resp.read()
                logger.info("下载成功: %s, 大小: %d bytes", url, len(content))
                return content
        except urllib.error.HTTPError as e:
            logger.warning("下载失败 (HTTP %d): %s - 尝试 %d/%d", e.code, url, attempt, retries)
        except urllib.error.URLError as e:
            logger.warning("下载失败 (URL错误): %s - %s - 尝试 %d/%d", url, e.reason, attempt, retries)
        except Exception as e:
            logger.warning("下载失败: %s - %s - 尝试 %d/%d", url, str(e), attempt, retries)

    logger.error("下载最终失败: %s, 已重试 %d 次", url, retries)
    return None
