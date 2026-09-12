"""文件下载器

从 URL 下载配置文件，支持重试。
包含 URL 安全验证、SSRF 防护与下载大小限制 (T02 / INH-613)。
"""

import logging
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Set

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
TIMEOUT_SECONDS = 30

# ---------------------------------------------------------------------------
# URL 安全配置 (T02 / INH-613)
# ---------------------------------------------------------------------------

MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
CHUNK_SIZE = 8192

BLOCKED_HOSTS: frozenset = frozenset({
    'localhost',
    '127.0.0.1',
    '::1',
    '0.0.0.0',
    '169.254.169.254',          # AWS / 通用云 metadata
    'metadata.google.internal',  # GCP metadata
    'metadata.goog',             # GCP metadata (短域名)
    '100.100.100.200',           # 阿里云 metadata
})


def validate_download_url(url: str, allowed_hosts: Optional[Set[str]] = None) -> bool:
    """验证下载 URL 安全性。

    规则:
    - 只允许 ``http`` / ``https`` 方案
    - 拒绝包含 userinfo（凭据）的 URL
    - 拒绝 loopback / link-local / 云 metadata 端点
      （除非显式列入 *allowed_hosts*）

    Raises:
        ValueError: URL 不合法
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        raise ValueError(f"Invalid URL: {exc}") from exc

    # 方案检查
    if parsed.scheme not in ('http', 'https'):
        raise ValueError(f"Unsupported scheme: {parsed.scheme!r}")

    # 拒绝凭据
    if parsed.username or parsed.password:
        raise ValueError("URL contains credentials")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Missing hostname")

    # SSRF 防护：检查拒绝列表
    allowed = allowed_hosts or set()
    if hostname in BLOCKED_HOSTS and hostname not in allowed:
        raise ValueError(f"Blocked host: {hostname}")

    return True


def sanitize_url_for_log(url: str) -> str:
    """脱敏 URL 中的 userinfo 和敏感 query 参数，用于安全日志记录。"""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return '[INVALID URL]'

    # 重建 netloc（去除 userinfo）
    netloc = parsed.hostname or ''
    if parsed.port:
        netloc += f":{parsed.port}"

    # 脱敏敏感 query 参数
    query = parsed.query
    sensitive_keys = {'token', 'key', 'secret', 'password', 'apikey', 'access_token'}
    if query:
        params = urllib.parse.parse_qs(query, keep_blank_values=True)
        for param_key in params:
            if param_key.lower() in sensitive_keys:
                query = '[REDACTED]'
                break

    return urllib.parse.urlunparse((
        parsed.scheme, netloc, parsed.path,
        parsed.params, query, '',
    ))


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """在每次重定向时重新验证目标 URL 的 HTTPRedirectHandler。"""

    def __init__(self, allowed_hosts: Optional[Set[str]] = None):
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_download_url(newurl, self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_download(
    url: str,
    dest: Path,
    allowed_hosts: Optional[Set[str]] = None,
    max_size: int = MAX_DOWNLOAD_SIZE,
    timeout: int = TIMEOUT_SECONDS,
) -> None:
    """安全下载文件到本地。

    特性:
    - URL 安全验证（方案、凭据、SSRF）
    - 每次重定向重新验证目标 URL
    - 分块读取，总字节限制
    - 超时控制
    - 失败时自动清理不完整文件

    Raises:
        ValueError: URL 不合法或超出大小限制
        urllib.error.URLError: 网络错误
    """
    validate_download_url(url, allowed_hosts)

    opener = urllib.request.build_opener(
        _SafeRedirectHandler(allowed_hosts),
    )

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "MCChecker/1.0"}
        )
        with opener.open(request, timeout=timeout) as response:
            # 预检 Content-Length
            content_length = response.getheader('Content-Length')
            if content_length is not None and int(content_length) > max_size:
                raise ValueError(
                    f"Content too large: {content_length} bytes (limit: {max_size})"
                )

            total_size = 0
            with open(dest, 'wb') as fh:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > max_size:
                        raise ValueError(
                            f"Download exceeds size limit ({max_size} bytes)"
                        )
                    fh.write(chunk)

        logger.info(
            "安全下载完成: %s -> %s (%d bytes)",
            sanitize_url_for_log(url), dest, total_size,
        )

    except BaseException:
        # 清理不完整文件
        try:
            dest.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# 原有 API（保持向后兼容）
# ---------------------------------------------------------------------------

def download_file(url: str, retries: int = MAX_RETRIES) -> Optional[bytes]:
    """从 URL 下载文件内容

    Args:
        url: 文件下载链接
        retries: 最大重试次数

    Returns:
        文件内容字节，失败返回 None
    """
    # 对日志中的 URL 进行脱敏
    safe_url = sanitize_url_for_log(url)

    for attempt in range(1, retries + 1):
        try:
            logger.info("下载文件: %s (尝试 %d/%d)", safe_url, attempt, retries)
            req = urllib.request.Request(url, headers={"User-Agent": "MCChecker/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                content = resp.read()
                logger.info("下载成功: %s, 大小: %d bytes", safe_url, len(content))
                return content
        except urllib.error.HTTPError as e:
            logger.warning("下载失败 (HTTP %d): %s - 尝试 %d/%d", e.code, safe_url, attempt, retries)
        except urllib.error.URLError as e:
            logger.warning("下载失败 (URL错误): %s - %s - 尝试 %d/%d", safe_url, e.reason, attempt, retries)
        except Exception as e:
            logger.warning("下载失败: %s - %s - 尝试 %d/%d", safe_url, str(e), attempt, retries)

    logger.error("下载最终失败: %s, 已重试 %d 次", safe_url, retries)
    return None
