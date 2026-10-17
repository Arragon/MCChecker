"""文件写入可靠性（规范 §5.4）。

* `atomic_write_*`——同目录临时文件 + fsync + `os.replace`，写入中途失败时
  目标文件字节不变，也不留下半截断的临时文件。
* `read_json`——区分"文件不存在"与"JSON 损坏"。损坏必须抛 `CorruptDataError`，
  不能退回空列表，否则下一次保存会把权威数据清空。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from copy import deepcopy
from typing import Any, Optional

from .errors import CorruptDataError

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
