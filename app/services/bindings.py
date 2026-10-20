"""变量绑定（规范 §7.5、§8.3、§9.8.7、§11.2；M3-14 / INH-1899）。

`bindings.json` 的格式与 v1 一致：`[{"group_name": str, "variables": [{"file","path"}]}]`。
`path` 仍是 `<文件名>/<标签>/…`，对比时由 `core/differ` 的 `bound_group` 标注（M2-08 已实现，
本卡不重做）。

写入口的裁定：

* 至少 2 个**有效**变量（文件存在且路径非空）。无效行（空行、文件已被删）被丢弃；剩下的
  不足 2 个 → 400 `detail.field="variables"`，且在碰磁盘之前。
* 组名进 `DELETE /bindings/{group}` 的路径段。`api.seg()` 会把 `/`、`\\` 与 `%` 编成
  `%2F` / `%5C` / `%25…`，撞上 §16.8 的全站守卫后「加得进来、删不出去」；所以创建端拒绝
  `/`、`\\` 与任何字面 `%XX`。删除端保持宽松，v1 直接写文件的存量行仍可由服务层/运维清理。
* 改名撞已有组 → 409 `FILE_EXISTS`，原组与文件字节都不动。
* 成功写之后递增一次 `settings`；读、失败写、删 0 条都不递增，删 0 条也不写文件。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..core import storage
from ..core.errors import FILE_EXISTS, INVALID_INPUT, AppError
from . import revision

PART = "settings"

GROUP_FIELD = "group_name"
VARIABLES_FIELD = "variables"

MAX_GROUP_NAME = 100

BAD_GROUP_NAME_MESSAGE = (
    "绑定组名称需为 1-%d 个字符，且不能包含 /、\\ 或任何 %%XX 编码写法（这样的组无法删除）"
    % MAX_GROUP_NAME
)
BAD_VARIABLES_MESSAGE = "绑定组至少需要 2 个有效变量（文件存在且路径非空）"
DUPLICATE_GROUP_MESSAGE = "已存在同名绑定组"

#: 与 `people._PERCENT_ESCAPE_RE` 同一条判据：路径会被解码两次，字面 `%XX` 在删除端会变成
#: 另一个名字（`超时%20组` 会撤掉 `超时 组`），所以创建端一律拒。`%zz`、裸 `%` 不在此族。
_PERCENT_ESCAPE_RE = re.compile(r"%[0-9A-Fa-f]{2}")


def list_bindings(pid: str) -> Dict[str, Any]:
    """`GET /p/{pid}/bindings` → `{"items": [{"group_name","variables"}]}`。

    读接口一个字节都不写、永不递增；`bindings.json` 不存在时返回空列表，也不创建文件。
    """
    with storage.use_profile(pid):
        rows = storage.load_bindings()
    return {"items": [_row(row) for row in rows if isinstance(row, dict)]}


def put_binding(
    pid: str, original_group: str, payload: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """`PUT /p/{pid}/bindings/{group}` → `{"item": {...}}`。

    路径里的组名是**原名**，body 的 `group_name` 是**新名**：路径组不存在即新增，存在即更新
    或改名。校验、改名冲突检查与落盘在同一个机型写锁里完成，避免两个写者互相覆盖。
    """
    body = payload if isinstance(payload, dict) else {}
    name = _validated_group_name(body.get(GROUP_FIELD))
    with storage.use_profile(pid), storage.profile_write(pid):
        existing_files = set(storage.list_config_files())
        variables = _validated_variables(body.get(VARIABLES_FIELD), existing_files)
        rows = storage.load_bindings()
        index = _group_index(rows, str(original_group))
        if name != str(original_group) and _group_index(rows, name) is not None:
            raise AppError(
                FILE_EXISTS, DUPLICATE_GROUP_MESSAGE, field=GROUP_FIELD
            )
        entry = {"group_name": name, "variables": variables}
        if index is None:
            rows = rows + [entry]
        else:
            rows = [entry if i == index else row for i, row in enumerate(rows)]
        storage.save_bindings(rows)
    revision.bump(pid, PART)
    return {"item": _clone_item(entry)}


def delete_binding(pid: str, group: str) -> Dict[str, Any]:
    """`DELETE /p/{pid}/bindings/{group}` → `{"removed": bool}`。

    没有这一组 ⇒ `removed: false`、一个字节都不写、修订号不动。组名按路径解出来的原样比较
    （不 trim、不重新校验），因为存量行可能带着 v1 写下的空格或编码写法；删不掉它们只会
    缩小出路。
    """
    target = str(group)
    with storage.use_profile(pid), storage.profile_write(pid):
        rows = storage.load_bindings()
        kept = [row for row in rows if _row_name(row) != target]
        removed = len(kept) < len(rows)
        if removed:
            storage.save_bindings(kept)
    if removed:
        revision.bump(pid, PART)
    return {"removed": removed}


def _validated_group_name(raw: Any) -> str:
    text = str(raw if raw is not None else "").strip()
    if (
        not text
        or len(text) > MAX_GROUP_NAME
        or any(ord(char) < 32 or ord(char) == 127 for char in text)
        or "/" in text
        or "\\" in text
        or _PERCENT_ESCAPE_RE.search(text) is not None
    ):
        raise AppError(INVALID_INPUT, BAD_GROUP_NAME_MESSAGE, field=GROUP_FIELD)
    return text


def _validated_variables(raw: Any, existing_files: set) -> List[Dict[str, str]]:
    if not isinstance(raw, list):
        raise AppError(INVALID_INPUT, BAD_VARIABLES_MESSAGE, field=VARIABLES_FIELD)
    valid: List[Dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        file_name = str(entry.get("file") or "").strip()
        path = str(entry.get("path") or "").strip()
        if file_name and path and file_name in existing_files:
            valid.append({"file": file_name, "path": path})
    if len(valid) < 2:
        raise AppError(INVALID_INPUT, BAD_VARIABLES_MESSAGE, field=VARIABLES_FIELD)
    return valid


def _group_index(rows: List[Any], name: str) -> Optional[int]:
    for index, row in enumerate(rows):
        if _row_name(row) == name:
            return index
    return None


def _row_name(row: Any) -> str:
    return str(row.get("group_name") or "") if isinstance(row, dict) else ""


def _row(row: Dict[str, Any]) -> Dict[str, Any]:
    variables = []
    for entry in row.get("variables") or []:
        if isinstance(entry, dict):
            variables.append(
                {
                    "file": str(entry.get("file") or ""),
                    "path": str(entry.get("path") or ""),
                }
            )
    return {"group_name": _row_name(row), "variables": variables}


def _clone_item(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "group_name": entry["group_name"],
        "variables": [dict(variable) for variable in entry["variables"]],
    }
