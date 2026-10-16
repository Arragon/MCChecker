"""文件写入可靠性与路径安全（规范 §5.4、§11.2）。

* `atomic_write_*`——同目录临时文件 + fsync + `os.replace`，写入中途失败时
  目标文件字节不变，也不留下半截断的临时文件。
* `read_json`——区分"文件不存在"与"JSON 损坏"。损坏必须抛 `CorruptDataError`，
  不能退回空列表，否则下一次保存会把权威数据清空。
* `validate_filename` / `resolve_within`——文件名与路径越界校验。只用于新建、
  改名与请求路径；读取已存在的旧文件不受影响（C26 的兼容要求）。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from copy import deepcopy
from typing import Any, Optional

from .errors import FORBIDDEN, INVALID_INPUT, AppError, CorruptDataError

#: 文件名长度上限（字符数，规范 §5.4）。超过即拒绝。
MAX_FILENAME_LENGTH = 180

#: Windows 文件名非法字符。路径分隔符在 POSIX 上合法，但同样会破坏 data 目录
#: 结构与跨平台可移植性，因此一并列为非法。
ILLEGAL_CHARS = frozenset('\\/:*?"<>|')

#: Windows 保留设备名。比较时忽略大小写与扩展名。
RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

#: os.replace 在 Windows 上可能因杀毒软件/索引服务短暂占用目标而失败。
_REPLACE_RETRIES = 5
_REPLACE_RETRY_DELAY = 0.05


def _fsync_dir(directory: str) -> None:
    """让目录项落盘，避免崩溃后丢失 rename。Windows 上打开目录会失败，忽略即可。"""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _replace(src: str, dst: str) -> None:
    last_error: Optional[BaseException] = None
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt == _REPLACE_RETRIES - 1:
                break
            time.sleep(_REPLACE_RETRY_DELAY)
    assert last_error is not None
    raise last_error


def atomic_write_bytes(path: str, data: bytes) -> None:
    """把 `data` 原子写入 `path`。

    临时文件与目标同目录（保证 `os.replace` 不跨文件系统），写入后 flush +
    fsync 再替换；任一步失败都删除临时文件并原样抛出，目标文件保持旧内容。
    """
    target = os.path.abspath(path)
    directory = os.path.dirname(target)
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix="." + os.path.basename(target) + ".",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    _fsync_dir(directory)


def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: str, obj: Any) -> None:
    """按现有格式写 JSON：`ensure_ascii=False`、`indent=2`，不带尾随换行。"""
    data = json.dumps(obj, ensure_ascii=False, indent=2)
    atomic_write_bytes(path, data.encode("utf-8"))


def read_json(path: str, default: Any = None) -> Any:
    """读取 JSON。

    文件不存在 → 返回 `default` 的深拷贝；存在但无法解析（JSON 语法错误或
    编码错误）→ 抛 `CorruptDataError`。"不存在"与"损坏"必须可区分。
    """
    if not os.path.exists(path):
        return deepcopy(default)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CorruptDataError(path, cause=exc) from exc


# --------------------------------------------------------------------------
# 文件名与路径校验
# --------------------------------------------------------------------------


def validate_filename(name: Any) -> str:
    """校验并返回规范化后的文件名。

    允许中文等 Unicode 字符；拒绝空串、超长（> 180 字符）、路径分隔符、
    Windows 非法字符、控制字符、`.` / `..`、保留设备名、以及以点或空格结尾。
    不合法抛 `AppError(INVALID_INPUT)`。
    """
    if not isinstance(name, str):
        raise AppError(INVALID_INPUT, "文件名不能为空")
    cleaned = name.strip()
    if not cleaned:
        raise AppError(INVALID_INPUT, "文件名不能为空")
    if len(cleaned) > MAX_FILENAME_LENGTH:
        raise AppError(INVALID_INPUT, "文件名过长（最多 %d 个字符）" % MAX_FILENAME_LENGTH)
    if any(ch in ILLEGAL_CHARS for ch in cleaned):
        raise AppError(INVALID_INPUT, '文件名不能包含 \\ / : * ? " < > |')
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in cleaned):
        raise AppError(INVALID_INPUT, "文件名不能包含控制字符")
    if cleaned in (".", ".."):
        raise AppError(INVALID_INPUT, "文件名不能是 . 或 ..")
    if cleaned.endswith(".") or cleaned.endswith(" "):
        raise AppError(INVALID_INPUT, "文件名不能以点或空格结尾")
    if cleaned.split(".")[0].upper() in RESERVED_NAMES:
        raise AppError(INVALID_INPUT, "文件名不能使用系统保留名")
    return cleaned


def resolve_within(root: str, *parts: str) -> str:
    """把 `parts` 拼到 `root` 下，并确认结果没有越出 `root`。

    先 `realpath` 再比较前缀，因此指向 root 之外的符号链接同样被拒绝。
    越界抛 `AppError(FORBIDDEN)`。
    """
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, *parts))
    if target != root_real and not target.startswith(root_real + os.sep):
        raise AppError(FORBIDDEN, "路径超出允许范围")
    return target
