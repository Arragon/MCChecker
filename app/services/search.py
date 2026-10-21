"""全局搜索（§7.6、§12；T-11 / M2-05）。

只读接口：不写权威文件，也不动修订号（§7.1 的五个计数都不变）。

数据面只有 compact 文档——每个文件走 `documents.load_document`，收藏备注按旧节点
ID 关联（§7.7）。只搜**当前版本**；历史版本与修改记录由各自的列表接口提供。

一个文件的绝大多数搜索请求其实是「这个文件里没有」，而反序列化一篇 3 万节点的文档
比在响应体字节里找一次子串贵得多（§12 实测见 `scripts/bench_search.py`）。因此在
反序列化之前先按缓存的响应体字节做一次预筛，条件见 `_may_hit`。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from ..core import fsutil, searching, storage
from ..core.errors import INVALID_INPUT, NOT_FOUND, PARSE_FAILED, AppError
from . import documents

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000

#: 关键词里出现这些字符时，它在 JSON 响应体里是转义形式，字节层比对不可靠。
_JSON_ESCAPED = re.compile(r'["\\\x00-\x1f]')


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def _clamp_offset(offset: int) -> int:
    return max(0, int(offset))


def _may_hit(
    profile_id: str,
    name: str,
    keyword: str,
    kw_bytes: bytes,
    note_map: Dict[str, str],
) -> bool:
    """返回 False 才可以整个文件跳过。只在能确定「没有」时才说没有。

    成立条件：关键词是纯 ASCII 且不含需要转义的字符；文档响应体里没有任何反斜杠
    转义（`ensure_ascii=False` 只对 `"`、`\\`、控制字符做转义）；该文件的收藏备注
    里也没有关键词。备注文本本身不在响应体里，所以备注命中必须走完整扫描。
    """
    if not keyword.isascii() or _JSON_ESCAPED.search(keyword):
        return True
    if any(keyword in note.lower() for note in note_map.values()):
        return True
    try:
        body, _etag = documents.document_bytes(profile_id, name, "current")
    except AppError:
        return True  # 读不到/解析不了交给主循环按同样规则处理
    if b"\\" in body:
        return True
    return kw_bytes in body.lower()


def search(
    profile_id: str,
    q: str,
    file: str = "",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> Dict[str, Any]:
    """`GET /p/{pid}/search?q=&file=&limit=&offset=`（§7.6）。"""
    keyword = str(q or "").strip()
    if not keyword:
        raise AppError(INVALID_INPUT, "请输入搜索关键词", field="q")
    page_limit = _clamp_limit(limit)
    page_offset = _clamp_offset(offset)
    wanted = str(file or "").strip()
    explicit = fsutil.validate_filename(wanted) if wanted else ""
    lowered = keyword.lower()
    kw_bytes = lowered.encode("utf-8")

    with storage.use_profile(profile_id):
        favorites = storage.load_favorites()
        names = [explicit] if explicit else storage.list_config_files()
        files: Dict[str, int] = {}
        hits: List[Dict[str, Any]] = []
        for name in names:
            note_map = searching.build_note_map(favorites, name)
            if not explicit and not _may_hit(profile_id, name, lowered, kw_bytes, note_map):
                continue
            try:
                doc = documents.load_document(profile_id, name, "current")
            except AppError as exc:
                # 全局扫描：读不到/解析失败的文件跳过，一个坏文件不该让整次搜索失败。
                # 指名搜索：照实抛出，免得把「文件有问题」说成「没有命中」。
                if not explicit and exc.code in (NOT_FOUND, PARSE_FAILED):
                    continue
                if explicit and exc.code == NOT_FOUND:
                    raise AppError(NOT_FOUND, "文件不存在", file=name)
                raise
            found = searching.search_compact(doc["nodes"], keyword, note_map)
            if not found:
                continue
            files[name] = len(found)
            for item in found:
                row: Dict[str, Any] = {"file": name}
                row.update(item)
                hits.append(row)

    return {
        "q": str(q or ""),
        "total": len(hits),
        "files": files,
        "items": hits[page_offset : page_offset + page_limit],
        "truncated": len(hits) > page_offset + page_limit,
    }
