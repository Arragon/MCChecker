"""外部工具菜单（规范 §7.10、§8.3、§11.2；M3-15 / INH-1900）。

`tools.json` 的格式与 v1 一致：`[{"name","description","url","added_at"}]`。

写入口的裁定：

* 名称 / 说明去空白后 1–100 字符、无控制字符；说明可空（界面本来就允许只填名称和地址）。
* 名称进 `DELETE /tools/{name}` 的路径段：创建端拒 `/`、`\\` 与任何字面 `%XX`，否则
  `api.seg()` 编出的 `%2F` / `%2520` 会撞 §16.8 守卫，「加得进来、删不出去」；删除端保持
  宽松，给 v1 直接写文件的存量行留出路。
* URL 只允许 http/https 且必须有主机。用 `urllib.parse.urlparse` 判 scheme，不做字符串
  前缀匹配（`java\\nscript:` 这类绕过前缀匹配）；控制字符、空白、反斜杠与 userinfo 在
  解析前拒绝，避免浏览器与 Python 对同一串给出不同主机。
* 名称唯一：改名撞已有工具 → 409 `FILE_EXISTS`，原条目与文件字节都不动。
* 成功写之后递增一次 `settings`；读、失败写、删 0 条都不递增，删 0 条也不写文件。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ..core import storage
from ..core.errors import FILE_EXISTS, INVALID_INPUT, AppError
from . import revision

PART = "settings"

NAME_FIELD = "name"
DESCRIPTION_FIELD = "description"
URL_FIELD = "url"

MAX_TEXT = 100

BAD_NAME_MESSAGE = (
    "工具名称需为 1-%d 个字符，且不能包含 /、\\ 或任何 %%XX 编码写法（这样的工具无法删除）"
    % MAX_TEXT
)
BAD_DESCRIPTION_MESSAGE = "说明不能超过 %d 个字符，且不能包含控制字符" % MAX_TEXT
BAD_URL_MESSAGE = "访问地址必须是包含主机名的 http 或 https 链接"
DUPLICATE_NAME_MESSAGE = "已存在同名工具"

_PERCENT_ESCAPE_RE = re.compile(r"%[0-9A-Fa-f]{2}")
_FORBIDDEN_URL_CHARS_RE = re.compile(r"[\x00-\x20\x7f\\]")


def list_tools(pid: str) -> Dict[str, Any]:
    """`GET /p/{pid}/tools` → `{"items": [{"name","description","url","added_at"}]}`。

    读接口一个字节都不写、永不递增；`tools.json` 不存在时返回空列表，也不创建文件。
    """
    with storage.use_profile(pid):
        rows = storage.load_tools()
    return {"items": [_row(row) for row in rows if isinstance(row, dict)]}


def put_tool(
    pid: str, original_name: str, payload: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """`PUT /p/{pid}/tools/{name}` → `{"item": {...}}`。

    路径里的名称是**原名**，body 的 `name` 是**新名**：路径条目不存在即新增，存在即更新
    或改名。校验、撞名检查与落盘在同一个机型写锁里，避免并发写互相覆盖。
    """
    body = payload if isinstance(payload, dict) else {}
    name = _validated_name(body.get(NAME_FIELD))
    url = _validated_url(body.get(URL_FIELD))
    description = _validated_description(body.get(DESCRIPTION_FIELD))
    stamp = _now()
    with storage.use_profile(pid), storage.profile_write(pid):
        rows = storage.load_tools()
        index = _tool_index(rows, str(original_name))
        if name != str(original_name) and _tool_index(rows, name) is not None:
            raise AppError(FILE_EXISTS, DUPLICATE_NAME_MESSAGE, field=NAME_FIELD)
        if index is None:
            entry = {
                "name": name,
                "description": description,
                "url": url,
                "added_at": stamp,
            }
            rows = rows + [entry]
        else:
            current = rows[index]
            entry = {
                "name": name,
                "description": description,
                "url": url,
                "added_at": current.get("added_at") or stamp,
            }
            rows = [entry if i == index else row for i, row in enumerate(rows)]
        storage.save_tools(rows)
    revision.bump(pid, PART)
    return {"item": dict(entry)}


def delete_tool(pid: str, name: str) -> Dict[str, Any]:
    """`DELETE /p/{pid}/tools/{name}` → `{"removed": bool}`。

    没有这一条 ⇒ `removed: false`、一个字节都不写、修订号不动。名称按路径解出来的原样比较
    （不 trim、不重新校验），存量行里带空格或编码写法的条目仍有机会被清掉。
    """
    target = str(name)
    with storage.use_profile(pid), storage.profile_write(pid):
        rows = storage.load_tools()
        kept = [row for row in rows if _row_name(row) != target]
        removed = len(kept) < len(rows)
        if removed:
            storage.save_tools(kept)
    if removed:
        revision.bump(pid, PART)
    return {"removed": removed}


def _validated_name(raw: Any) -> str:
    text = str(raw if raw is not None else "").strip()
    if (
        not text
        or len(text) > MAX_TEXT
        or any(ord(char) < 32 or ord(char) == 127 for char in text)
        or "/" in text
        or "\\" in text
        or _PERCENT_ESCAPE_RE.search(text) is not None
    ):
        raise AppError(INVALID_INPUT, BAD_NAME_MESSAGE, field=NAME_FIELD)
    return text


def _validated_description(raw: Any) -> str:
    text = str(raw if raw is not None else "").strip()
    if len(text) > MAX_TEXT or any(
        ord(char) < 32 or ord(char) == 127 for char in text
    ):
        raise AppError(
            INVALID_INPUT, BAD_DESCRIPTION_MESSAGE, field=DESCRIPTION_FIELD
        )
    return text


def _validated_url(raw: Any) -> str:
    text = str(raw if raw is not None else "").strip()
    if not text or _FORBIDDEN_URL_CHARS_RE.search(text) is not None:
        raise AppError(INVALID_INPUT, BAD_URL_MESSAGE, field=URL_FIELD)
    try:
        parsed = urlparse(text)
        hostname = parsed.hostname
        parsed.port  # 端口越界（>65535）会抛 ValueError，必须在写入前拒绝
        userinfo = parsed.username is not None or parsed.password is not None
    except ValueError:
        raise AppError(INVALID_INPUT, BAD_URL_MESSAGE, field=URL_FIELD)
    if (
        parsed.scheme.lower() not in ("http", "https")
        or not hostname
        or userinfo
        or "%" in parsed.netloc
    ):
        raise AppError(INVALID_INPUT, BAD_URL_MESSAGE, field=URL_FIELD)
    return text


def _tool_index(rows: List[Any], name: str) -> Optional[int]:
    for index, row in enumerate(rows):
        if _row_name(row) == name:
            return index
    return None


def _row_name(row: Any) -> str:
    return str(row.get("name") or "") if isinstance(row, dict) else ""


def _row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": _row_name(row),
        "description": str(row.get("description") or ""),
        "url": str(row.get("url") or ""),
        "added_at": row.get("added_at"),
    }


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
