"""审阅列表与按文件提交的 API（§6.7「审阅提交」第 1–9 步、§7.8；M3-02 / INH-1887）。

覆盖（卡面验收逐条）：

* UC-20/21：生成文件**只有被批准的值**不同、原文件一字节不动、未决定的组仍 pending；
* UC-22：`source_sha` 不符 → 409 且 `configs/` 不新增文件；
* UC-23：legacy 建议恰好匹配 1 个节点才可定位，同名兄弟（旧 ID 匹配到多个）只能驳回；
* 重复 `request_id` 只产生一个新文件，且**落盘**（模拟进程重启后仍然如此）；
* 校验失败（`REVIEW_UNSAFE`）→ 422，不写任何文件、不改任何建议状态；
* 四角色与 CSRF、修订号只递增 `remarks`。

断言只针对**行为**（compact 的 `loc → v`、建议状态、文件集合），不断言标准库统一掉的
写法（引号/空元素/实体，§6.7 已知限制）。测试全部跑在 `tests/conftest.py` 重定向后的
临时数据根里。
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Optional

import pytest
from authz_fixtures import as_role, make_server, seed_roles, write_target

from app.core import parser, reviewing, storage
from app.core.errors import (
    INVALID_INPUT,
    NOT_FOUND,
    REVIEW_CONFLICT,
    REVIEW_UNSAFE,
    AppError,
)
from app.services import review as review_svc
from app.services import revision
from app.services.auth import Actor

PID = "default"
XML_NAME = "cfg.xml"
JSON_NAME = "cfg.json"

XML_DOC = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<R>\n"
    "  <params>\n"
    "    <item>first</item>\n"
    "    <item>second</item>\n"
    '    <param_01 min="0" max="10">5</param_01>\n'
    "  </params>\n"
    "</R>\n"
)
JSON_DOC = '{"a": 1, "b": "text"}\n'

ITEM_1 = "/R[1]/params[1]/item[1]"
ITEM_2 = "/R[1]/params[1]/item[2]"
PARAM = "/R[1]/params[1]/param_01[1]"
PARAM_MAX = "/R[1]/params[1]/param_01[1]/@max"
OLD_ITEM_ID = "root/R/params/item"
OLD_PARAM_ID = "root/R/params/param_01"

#: 服务层的调用方是 handler（已 authorize 过），这里直接给一个管理员身份。
ADMIN = Actor(ip="10.0.0.3", name="管理员乙", registered=True, is_deployer=False, is_admin=True)


# ===================== 小工具 =====================


def _write(name: str, content: bytes, pid: str = PID) -> None:
    with storage.use_profile(pid):
        storage.save_config_file(name, content)


def _read(name: str, pid: str = PID) -> bytes:
    with storage.use_profile(pid):
        data = storage.load_config_file(name)
    assert data is not None, name
    return data


def _sha(name: str, pid: str = PID) -> str:
    return hashlib.sha256(_read(name, pid)).hexdigest()


def _values(content: bytes, name: str) -> Dict[str, Optional[str]]:
    """有值节点的 `loc → v`（§6.2）：判断「只有被批准的值不同」的唯一口径。"""
    nodes = parser.to_compact(content, name)["nodes"]
    return {loc: v for loc, v in zip(nodes["loc"], nodes["v"]) if v is not None}


def _changed(before: bytes, after: bytes, name: str) -> Dict[str, str]:
    source, result = _values(before, name), _values(after, name)
    assert set(source) == set(result), "生成文件不得增删节点"
    return {loc: str(result[loc]) for loc in source if source[loc] != result[loc]}


def _add(
    loc: str,
    proposed: str,
    *,
    file: str = XML_NAME,
    legacy: bool = False,
    pid: str = PID,
) -> str:
    """按 §6.7 提交建议的字段口径写一条 pending 建议。

    `legacy=True` 写 v1 那种没有 `locator` 的记录（`node_key` 非空、`schema_version=1`），
    与 `storage.add_edit_remark` 对旧数据的约束一致。
    """
    with storage.use_profile(pid):
        nodes = parser.to_compact(storage.load_config_file(file), file)["nodes"]
        i = nodes["loc"].index(loc)
        return storage.add_edit_remark(
            source_file=file,
            node_key="0.0.1.3" if legacy else "",
            node_path=nodes["id"][i],
            node_label=nodes["l"][i],
            original_value=nodes["v"][i],
            proposed_value=proposed,
            node_type=nodes["t"][i],
            tree_type="json" if file.endswith(".json") else "xml",
            actor_ip="10.0.0.9",
            actor_name="张三",
            locator="" if legacy else loc,
            source_sha256=_sha(file, pid),
            schema_version=1 if legacy else 2,
        )


def _items(pid: str = PID) -> List[Dict[str, Any]]:
    with storage.use_profile(pid):
        return storage.load_edit_remarks()


def _status_by_id(pid: str = PID) -> Dict[str, str]:
    return {str(item["id"]): str(item["status"]) for item in _items(pid)}


def _config_names(pid: str = PID) -> List[str]:
    with storage.use_profile(pid):
        return storage.list_config_files()


def _last_id(pid: str = PID) -> str:
    """最近写入的一条建议 id（紧接着 `_add` 调用）。"""
    return str(_items(pid)[-1]["id"])


def _item(remark_id: str, pid: str = PID) -> Dict[str, Any]:
    for item in _items(pid):
        if str(item["id"]) == remark_id:
            return item
    raise AssertionError(remark_id)


def _generated_name(remark_id: str, pid: str = PID) -> str:
    for item in _items(pid):
        if str(item["id"]) == remark_id:
            return str(item["generated_file"])
    raise AssertionError(remark_id)


def _decision(group_key: str, choice: str) -> Dict[str, str]:
    return {"group_key": group_key, "choice": choice}


def _payload(
    decisions: List[Dict[str, str]],
    *,
    request_id: str = "req-1",
    file: str = XML_NAME,
    source_sha: Optional[str] = None,
    pid: str = PID,
) -> Dict[str, Any]:
    return {
        "request_id": request_id,
        "file": file,
        "source_sha": _sha(file, pid) if source_sha is None else source_sha,
        "decisions": decisions,
    }


def _submit(payload: Dict[str, Any], actor: Actor = ADMIN) -> Dict[str, Any]:
    return review_svc.submit_review(PID, actor, payload)


def _files(pid: str = PID) -> Dict[str, Dict[str, Any]]:
    return {f["file"]: f for f in review_svc.list_review(pid)["files"]}


def _node(name: str, group_key: str, pid: str = PID) -> Dict[str, Any]:
    for node in _files(pid)[name]["nodes"]:
        if node["group_key"] == group_key:
            return node
    raise AssertionError("分组不存在：%s / %s" % (name, group_key))


@pytest.fixture
def seeded() -> str:
    """一份 XML + 一份 JSON 的当前原文。"""
    _write(XML_NAME, XML_DOC.encode("utf-8"))
    _write(JSON_NAME, JSON_DOC.encode("utf-8"))
    return XML_NAME


# ===================== GET /review：分组与布尔字段 =====================


class TestReviewList:
    def test_groups_by_locator_not_old_id(self, seeded):
        """R1：同 locator 的两条并一组，不同 locator 另起一组。

        三条建议的 `node_path` 完全相同（同名兄弟共享旧 ID），所以「按旧 ID 分组」会把
        它们合成一组——那正是 v1 改错节点的根因。
        """
        _add(ITEM_1, "A")
        _add(ITEM_1, "B")
        _add(ITEM_2, "C")
        counts = sorted(len(n["remarks"]) for n in _files()[XML_NAME]["nodes"])
        assert counts == [1, 2], "同 loc 必须并组，且不得按旧 node_path 合并"

    def test_node_fields_match_the_spec_shape(self, seeded):
        """§7.8 的字段名与 `views/review.js` 的读法（R6/R7/R8）。"""
        rid = _add(PARAM_MAX, "42")
        node = _node(XML_NAME, PARAM_MAX)
        assert node["group_key"] == PARAM_MAX
        assert node["loc"] == PARAM_MAX
        assert node["id"] == OLD_PARAM_ID + "@max"
        assert node["label"] == "@max"
        assert node["path"] == "R › params › param_01 › @max"
        assert node["node_type"] == "attribute"
        assert node["original_value"] == "10" and node["current_value"] == "10"
        assert (node["stale"], node["legacy"], node["resolvable"]) == (False, False, True)
        assert set(node["remarks"][0]) == {
            "id",
            "actor_display",
            "actor_ip",
            "proposed_value",
            "created_at",
        }
        assert node["remarks"][0]["id"] == rid
        assert node["remarks"][0]["proposed_value"] == "42"
        assert node["remarks"][0]["actor_display"] == "张三"

    def test_file_block_reports_exists_and_current_sha(self, seeded):
        _add(PARAM, "6")
        block = _files()[XML_NAME]
        assert block["exists"] is True
        assert block["current_sha"] == _sha(XML_NAME)

    def test_missing_file_is_listed_but_not_resolvable(self, seeded):
        """当前文件被删掉（例如归档后改名）：仍然列出这一组，但只给「驳回」这条出路。

        `current_sha` 为 `null` 是有意的——指纹无从计算，提交时会在 §6.7 第 2 步落到 404，
        而不是把「文件不见了」说成「文件变了」。
        """
        _add(PARAM, "6")
        with storage.use_profile(PID):
            os.unlink(storage.get_config_path(XML_NAME))
        block = _files()[XML_NAME]
        assert block["exists"] is False and block["current_sha"] is None
        node = block["nodes"][0]
        assert node["resolvable"] is False and node["current_value"] is None
        assert node["path"] == OLD_PARAM_ID
        assert node["stale"] is False, "定位不到时不能同时报「已变化」（§7.8 的 stale 定义）"

    def test_only_pending_groups_are_listed(self, seeded):
        approved, rejected = _add(PARAM, "6"), _add(ITEM_1, "A")
        pending = _add(ITEM_2, "C")
        _submit(_payload([_decision(PARAM, approved), _decision(ITEM_1, "reject")]))
        nodes = _files()[XML_NAME]["nodes"]
        assert [n["group_key"] for n in nodes] == [ITEM_2]
        assert [r["id"] for r in nodes[0]["remarks"]] == [pending]

    def test_files_do_not_share_groups(self, seeded):
        _add(PARAM, "6")
        _add("/a", "7", file=JSON_NAME)
        listed = review_svc.list_review(PID)["files"]
        assert [f["file"] for f in listed] == [JSON_NAME, XML_NAME]
        assert [n["group_key"] for n in listed[0]["nodes"]] == ["/a"]

    def test_no_remarks_yields_no_files(self, seeded):
        assert review_svc.list_review(PID) == {"files": []}

    def test_legacy_unique_match_is_resolvable(self, seeded):
        """UC-23 的前半：旧 ID 恰好匹配 1 个节点 → 可定位，但仍打 legacy 徽标。"""
        _add(PARAM, "7", legacy=True)
        node = _node(XML_NAME, OLD_PARAM_ID)
        assert (node["legacy"], node["resolvable"], node["stale"]) == (True, True, False)
        assert node["loc"] == PARAM, "legacy 分组也要给出定位到的真实 loc"
        assert node["current_value"] == "5"

    def test_legacy_ambiguous_match_is_not_resolvable(self, seeded):
        """R7：两个同名兄弟共享旧 ID → 匹配到 2 个 → 必须 `resolvable=false`。

        否则 `review.js:103` 的 `disabled = n.stale || !n.resolvable` 会给出一个可点的
        「通过」按钮，点下去必然 409——界面看似正常但是死路。
        """
        _add(ITEM_1, "A", legacy=True)
        node = _node(XML_NAME, OLD_ITEM_ID)
        assert node["resolvable"] is False and node["legacy"] is True
        assert node["current_value"] is None and node["path"] == OLD_ITEM_ID

    def test_locator_that_no_longer_exists_is_not_resolvable(self, seeded):
        _add(PARAM, "6")
        _write(XML_NAME, b"<R><other>1</other></R>")
        node = _node(XML_NAME, PARAM)
        assert (node["resolvable"], node["stale"], node["current_value"]) == (False, False, None)
        assert node["path"] == OLD_PARAM_ID

    def test_missing_value_is_json_null_and_thus_stale(self, seeded):
        """R6：节点无值时 `current_value` 是 JSON `null`，不是空串（`review.js:137`）。"""
        _add(PARAM, "6")
        _write(XML_NAME, b'<R><params><item>first</item><param_01 min="0"/></params></R>')
        node = _node(XML_NAME, PARAM)
        assert node["current_value"] is None
        assert node["resolvable"] is True and node["stale"] is True


# ===================== POST /review/submit：冲突、部分提交与状态 =====================


class TestReviewSubmit:
    def test_generated_file_changes_exactly_the_approved_values(self, seeded):
        """UC-20：两个节点通过 → 生成文件只有这两处值不同，原文件一字节不动。"""
        before = _read(XML_NAME)
        first, second = _add(ITEM_1, "FIRST!"), _add(PARAM_MAX, "42")
        out = _submit(_payload([_decision(ITEM_1, first), _decision(PARAM_MAX, second)]))
        assert _read(XML_NAME) == before, "原文件必须一字节不动"
        assert out["generated_file"] not in (None, XML_NAME)
        assert out["generated_file"].startswith("cfg_") and out["generated_file"].endswith(".xml")
        assert _changed(before, _read(out["generated_file"]), XML_NAME) == {
            ITEM_1: "FIRST!",
            PARAM_MAX: "42",
        }
        assert out == {"generated_file": out["generated_file"], "approved": 2, "rejected": 0}

    def test_partial_submit_keeps_undecided_groups_pending(self, seeded):
        """C07：只决定一个组，另一个组保持 pending，且不出现在生成文件里。"""
        approved = _add(ITEM_1, "A")
        untouched = _add(ITEM_2, "B")
        out = _submit(_payload([_decision(ITEM_1, approved)]))
        assert _status_by_id() == {approved: "approved", untouched: "pending"}
        assert _changed(_read(XML_NAME), _read(out["generated_file"]), XML_NAME) == {ITEM_1: "A"}

    def test_json_document_is_written_back_in_its_own_format(self, seeded):
        before = _read(JSON_NAME)
        rid = _add("/a", "7", file=JSON_NAME)
        out = _submit(_payload([_decision("/a", rid)], file=JSON_NAME))
        assert _read(JSON_NAME) == before
        assert _changed(before, _read(out["generated_file"]), JSON_NAME) == {"/a": "7"}

    def test_approved_group_rejects_its_other_remarks(self, seeded):
        """§6.7 第 8 步：一个节点只能通过一条建议，同组其余条自动 rejected。"""
        chosen, other = _add(ITEM_1, "A"), _add(ITEM_1, "B")
        _submit(_payload([_decision(ITEM_1, chosen)]))
        assert _status_by_id() == {chosen: "approved", other: "rejected"}

    def test_reject_disposes_the_whole_group_and_makes_no_file(self, seeded):
        a, b = _add(ITEM_1, "A"), _add(ITEM_1, "B")
        out = _submit(_payload([_decision(ITEM_1, "reject")]))
        assert out == {"generated_file": None, "approved": 0, "rejected": 2}
        assert _status_by_id() == {a: "rejected", b: "rejected"}
        assert _config_names() == [JSON_NAME, XML_NAME], "全部驳回时不该生成任何文件"

    def test_reviewer_fields_are_written(self, seeded):
        rid = _add(ITEM_1, "A")
        out = _submit(_payload([_decision(ITEM_1, rid)]))
        item = _items()[0]
        assert item["reviewed_by"] == ADMIN.name and item["reviewer_ip"] == ADMIN.ip
        assert item["reviewed_at"] and item["generated_file"] == out["generated_file"]
        assert item["generated_file"] in _config_names()

    def test_rejected_items_do_not_keep_a_generated_file(self, seeded):
        chosen, other = _add(ITEM_1, "A"), _add(ITEM_1, "B")
        _submit(_payload([_decision(ITEM_1, chosen)]))
        by_id = {str(i["id"]): i for i in _items()}
        assert by_id[chosen]["generated_file"] and by_id[other]["generated_file"] == ""

    def test_unknown_group_key_is_ignored_not_failed(self, seeded):
        """部分提交 + 他人已决定的竞争：请求里的组可能已经不存在，只能忽略（§6.7 第 3 步）。"""
        pending = _add(ITEM_1, "A")
        out = _submit(_payload([_decision("/R[1]/gone[1]", "reject"), _decision(ITEM_1, pending)]))
        assert out["approved"] == 1 and out["rejected"] == 0
        assert _status_by_id() == {pending: "approved"}

    def test_choice_that_is_not_a_remark_of_the_group_is_400(self, seeded):
        """§7.8 的 `choice` 只允许 `<remark_id>` 或 `"reject"`；静默忽略会把「没决定」说成成功。"""
        _add(ITEM_1, "A")
        with pytest.raises(AppError) as excinfo:
            _submit(_payload([_decision(ITEM_1, "not-a-remark-id")]))
        assert excinfo.value.code == INVALID_INPUT
        assert excinfo.value.detail.get("group_key") == ITEM_1

    def test_source_sha_conflict_writes_nothing(self, seeded):
        """UC-22：`source_sha` 不符 → 409，`configs/` 文件数不变、建议状态不变。"""
        rid = _add(ITEM_1, "A")
        names, statuses = _config_names(), _status_by_id()
        with pytest.raises(AppError) as excinfo:
            _submit(_payload([_decision(ITEM_1, rid)], source_sha="0" * 64))
        assert excinfo.value.code == REVIEW_CONFLICT
        assert _config_names() == names and _status_by_id() == statuses

    def test_approving_a_stale_group_is_400(self, seeded):
        """§6.7 第 7 步：stale 的节点只能驳回，给「通过」必须是 400，不能静默驳回。"""
        rid = _add(PARAM_MAX, "42")
        _write(XML_NAME, XML_DOC.replace('max="10"', 'max="11"').encode("utf-8"))
        with pytest.raises(AppError) as excinfo:
            _submit(_payload([_decision(PARAM_MAX, rid)]))
        assert excinfo.value.code == INVALID_INPUT
        assert excinfo.value.detail.get("group_key") == PARAM_MAX
        assert _status_by_id() == {rid: "pending"}

    def test_approving_an_unresolvable_legacy_group_is_400(self, seeded):
        """R7 的后端另一半：定位不唯一的 legacy 组给「通过」→ 400（不是 409，不是静默驳回）。"""
        rid = _add(ITEM_1, "A", legacy=True)
        with pytest.raises(AppError) as excinfo:
            _submit(_payload([_decision(OLD_ITEM_ID, rid)]))
        assert excinfo.value.code == INVALID_INPUT
        assert excinfo.value.detail.get("group_key") == OLD_ITEM_ID
        assert _status_by_id() == {rid: "pending"}

    def test_rejecting_an_unresolvable_group_still_works(self, seeded):
        """定位不唯一只能驳回——这条必须是**可用**的出路（UC-23）。"""
        rid = _add(ITEM_1, "A", legacy=True)
        out = _submit(_payload([_decision(OLD_ITEM_ID, "reject")]))
        assert out == {"generated_file": None, "approved": 0, "rejected": 1}
        assert _status_by_id() == {rid: "rejected"}

    def test_two_decisions_on_the_same_group_are_400(self, seeded):
        """同一个组给两个「通过」：只能落一个值，另一个会被静默丢弃 → 400 而不是 422。"""
        a, b = _add(ITEM_1, "A"), _add(ITEM_1, "B")
        with pytest.raises(AppError) as excinfo:
            _submit(_payload([_decision(ITEM_1, a), _decision(ITEM_1, b)]))
        assert excinfo.value.code == INVALID_INPUT
        assert excinfo.value.detail.get("group_key") == ITEM_1
        assert _status_by_id() == {a: "pending", b: "pending"}

    def test_two_groups_locating_the_same_node_are_400(self, seeded):
        """locator 组与「按旧 ID 唯一命中」的 legacy 组指向同一个节点：同样是自相矛盾的请求。"""
        located, legacy = _add(PARAM, "6"), _add(PARAM, "7", legacy=True)
        assert review_svc.group_key_of(_item(located)) == PARAM
        assert review_svc.group_key_of(_item(legacy)) == OLD_PARAM_ID
        with pytest.raises(AppError) as excinfo:
            _submit(_payload([_decision(PARAM, located), _decision(OLD_PARAM_ID, legacy)]))
        assert excinfo.value.code == INVALID_INPUT
        assert excinfo.value.detail.get("other_group_key") in (PARAM, OLD_PARAM_ID)
        assert _status_by_id() == {located: "pending", legacy: "pending"}

    def test_chosen_remark_with_an_other_original_value_is_409(self, seeded):
        """§6.7 第 4 步：组没变（不 stale），但选的那条建议记录的「提交时原值」对不上 → 409。

        同组各条建议是在不同时刻写下的，中间文件可能被改过又改回来，所以组的 stale 判定
        （按第一条的原值）盖不住这一类；写回前必须再按选中那条的原值核一次。
        """
        first = _add(ITEM_1, "A")
        _write(XML_NAME, XML_DOC.replace(">first<", ">changed<").encode("utf-8"))
        second = _add(ITEM_1, "B")
        _write(XML_NAME, XML_DOC.encode("utf-8"))
        node = _node(XML_NAME, ITEM_1)
        assert node["original_value"] == "first" and node["current_value"] == "first"
        assert node["stale"] is False, "前提：按组的第一条原值判断，这个组是新鲜的"
        with pytest.raises(AppError) as excinfo:
            _submit(_payload([_decision(ITEM_1, second)]))
        assert excinfo.value.code == REVIEW_CONFLICT
        assert _status_by_id() == {first: "pending", second: "pending"}
        assert _config_names() == [JSON_NAME, XML_NAME]

    @pytest.mark.parametrize(
        "missing,field",
        [
            ("request_id", "request_id"),
            ("file", "file"),
            ("source_sha", "source_sha"),
            ("decisions", "decisions"),
        ],
    )
    def test_missing_payload_fields_are_400_with_field(self, seeded, missing, field):
        _add(ITEM_1, "A")
        payload = _payload([_decision(ITEM_1, _last_id())])
        del payload[missing]
        with pytest.raises(AppError) as excinfo:
            _submit(payload)
        assert excinfo.value.code == INVALID_INPUT
        assert excinfo.value.detail.get("field") == field

    @pytest.mark.parametrize(
        "decisions",
        [
            "not-a-list",
            [{"group_key": "", "choice": "reject"}],
            [{"group_key": PARAM}],
            [{}],
        ],
    )
    def test_malformed_decisions_are_400(self, seeded, decisions):
        _add(ITEM_1, "A")
        with pytest.raises(AppError) as excinfo:
            _submit(_payload(decisions))
        assert excinfo.value.code == INVALID_INPUT
        assert excinfo.value.detail.get("field") == "decisions"

    def test_choice_of_an_empty_string_is_400(self, seeded):
        rid = _add(ITEM_1, "A")
        with pytest.raises(AppError) as excinfo:
            _submit(_payload([{"group_key": ITEM_1, "choice": ""}]))
        assert excinfo.value.code == INVALID_INPUT
        assert excinfo.value.detail.get("group_key") == ITEM_1
        assert _status_by_id() == {rid: "pending"}

    def test_missing_source_file_is_404(self, seeded):
        with pytest.raises(AppError) as excinfo:
            _submit(_payload([], file="gone.xml", source_sha="a" * 64))
        assert excinfo.value.code == NOT_FOUND

    def test_empty_decisions_is_a_noop_success(self, seeded):
        _add(ITEM_1, "A")
        assert _submit(_payload([])) == {"generated_file": None, "approved": 0, "rejected": 0}
        assert _status_by_id() == {_last_id(): "pending"}

    def test_two_files_never_share_one_generated_file(self, seeded):
        """按文件提交：一次只处理请求里那个文件的建议。"""
        xml_remark, json_remark = _add(ITEM_1, "A"), _add("/a", "7", file=JSON_NAME)
        out = _submit(_payload([_decision(ITEM_1, xml_remark)]))
        assert _status_by_id() == {xml_remark: "approved", json_remark: "pending"}
        assert _values(_read(out["generated_file"]), XML_NAME)[ITEM_1] == "A"


# ===================== 校验失败：422 且不写任何文件、不改任何状态 =====================


class TestUnsafeWritesNothing:
    def _assert_untouched(self, names: List[str], statuses: Dict[str, str]) -> None:
        assert _config_names() == names, "校验失败不得写任何文件"
        assert _status_by_id() == statuses, "校验失败不得改任何建议状态"

    def test_apply_edits_failure_is_422(self, seeded, monkeypatch):
        rid = _add(ITEM_1, "A")
        names, statuses = _config_names(), _status_by_id()

        def boom(*_args, **_kwargs):
            raise AppError(REVIEW_UNSAFE, "写回后文档结构发生了变化，已拒绝写回")

        monkeypatch.setattr(reviewing, "apply_edits", boom)
        with pytest.raises(AppError) as excinfo:
            _submit(_payload([_decision(ITEM_1, rid)]))
        assert excinfo.value.code == REVIEW_UNSAFE
        self._assert_untouched(names, statuses)

    def test_document_with_dtd_is_422_for_real(self, seeded):
        """不是只靠 monkeypatch：DTD 是 `apply_edits` 真实拒绝的输入（§6.7 已知限制）。"""
        dtd = '<!DOCTYPE r SYSTEM "m.dtd"><R><a>1</a></R>'
        _write(XML_NAME, dtd.encode("utf-8"))
        loc = "/R[1]/a[1]"
        rid = _add(loc, "2", file=XML_NAME)
        names, statuses = _config_names(), _status_by_id()
        with pytest.raises(AppError) as excinfo:
            _submit(_payload([_decision(loc, rid)]))
        assert excinfo.value.code == REVIEW_UNSAFE
        self._assert_untouched(names, statuses)

    def test_second_group_failing_rolls_back_the_first(self, seeded, monkeypatch):
        """一组通过、另一组校验不过 → 一份文件都不写、一条状态都不改（§6.7 第 7/8 步的顺序）。"""
        good, bad = _add(ITEM_1, "A"), _add(PARAM, "6")
        names, statuses = _config_names(), _status_by_id()
        real = reviewing.apply_edits

        def flaky(src, fmt, edits):
            if any(loc == PARAM for loc, _v, _k in edits):
                raise AppError(REVIEW_UNSAFE, "模拟校验失败")
            return real(src, fmt, edits)

        monkeypatch.setattr(reviewing, "apply_edits", flaky)
        with pytest.raises(AppError):
            _submit(_payload([_decision(ITEM_1, good), _decision(PARAM, bad)]))
        self._assert_untouched(names, statuses)


# ===================== request_id 幂等 =====================


class TestIdempotency:
    def test_duplicate_request_id_makes_exactly_one_file(self, seeded):
        rid = _add(ITEM_1, "A")
        payload = _payload([_decision(ITEM_1, rid)], request_id="req-dup")
        first = _submit(payload)
        assert _submit(payload) == first
        assert _config_names().count(first["generated_file"]) == 1

    def test_duplicate_after_the_source_changed_returns_the_old_result(self, seeded):
        """幂等是第 1 步（§6.7）：重试不能因为「文件后来变了」而变成 409。"""
        rid = _add(ITEM_1, "A")
        payload = _payload([_decision(ITEM_1, rid)], request_id="req-race")
        first = _submit(payload)
        _write(XML_NAME, XML_DOC.replace("first", "FIRST").encode("utf-8"))
        assert _submit(payload) == first

    def test_different_request_id_is_a_new_submission(self, seeded):
        _add(ITEM_1, "A")
        assert _submit(_payload([_decision(ITEM_1, "reject")], request_id="req-a"))["rejected"] == 1
        # 上一条已 rejected：换个 request_id 再提交同一组，没有任何可决定的组
        assert _submit(_payload([_decision(ITEM_1, _last_id())], request_id="req-b")) == {
            "generated_file": None,
            "approved": 0,
            "rejected": 0,
        }

    def test_table_is_persisted_and_survives_a_restart(self, seeded):
        """卡面明令「进程重启后重复 `request_id` 仍不得生成第二份文件（必须落盘）」。

        幂等表落在独立 JSON 文件里，重启后读得回来；这里既核对落盘内容，也核对落盘之后
        的重复提交只产生一份文件。
        """
        rid = _add(ITEM_1, "A")
        payload = _payload([_decision(ITEM_1, rid)], request_id="req-restart")
        first = _submit(payload)
        with storage.use_profile(PID):
            table = storage.load_review_idempotency()
        assert "req-restart" in table, "幂等记录必须落盘，否则重启后会生成第二份文件"
        assert table["req-restart"]["result"] == first
        names = _config_names()
        assert _submit(payload) == first
        assert _config_names() == names

    def test_record_outside_the_ten_minute_window_is_not_reused(self, seeded):
        rid = _add(ITEM_1, "A")
        payload = _payload([_decision(ITEM_1, rid)], request_id="req-old")
        first = _submit(payload)
        with storage.use_profile(PID):
            table = storage.load_review_idempotency()
            table["req-old"]["at"] -= review_svc.IDEMPOTENCY_WINDOW_SECONDS + 1
            storage.save_review_idempotency(table)
        assert _submit(payload) == {"generated_file": None, "approved": 0, "rejected": 0}
        assert first["generated_file"] not in (None, ""), "窗口内那次确实生成了文件"

    def test_failed_submission_is_not_recorded(self, seeded):
        """失败的写不登记幂等：重试应当重新尝试，而不是永远返回上次的错误。"""
        _add(ITEM_1, "A")
        with pytest.raises(AppError):
            _submit(_payload([_decision(ITEM_1, _last_id())], source_sha="0" * 64))
        with storage.use_profile(PID):
            assert storage.load_review_idempotency() == {}

    def test_table_lives_in_its_own_file_and_is_not_copied_with_profile(self, seeded):
        """R3：独立文件 `review_idempotency.json`，不随机型复制、不塞进 edit_remarks.json。"""
        assert "review_idempotency.json" not in storage.COPY_PROFILE_FILES
        rid = _add(ITEM_1, "A")
        _submit(_payload([_decision(ITEM_1, rid)], request_id="req-path"))
        path = storage._get_profile_json_file("review_idempotency.json", PID)
        assert os.path.isfile(path)
        assert path != storage.get_edit_remarks_file(PID)
        assert set(storage.load_edit_remarks()[0]) >= {"id", "status", "source_file"}

    def test_no_module_level_path_constant(self):
        """交付手册决策 1：新增路径一律在调用期求值，不能加模块级常量（import 期派生路径坑）。"""
        assert not hasattr(storage, "REVIEW_IDEMPOTENCY_FILE")


# ===================== HTTP 层：角色、CSRF 与修订号 =====================


class TestReviewHttp:
    def setup_method(self):
        revision.reset()
        self.pid = seed_roles("rvw")
        write_target(self.pid, XML_NAME, XML_DOC.encode("utf-8"))
        self.item_remark = _add(ITEM_1, "A", pid=self.pid)
        self.param_remark = _add(PARAM_MAX, "42", pid=self.pid)

    def _payload(self, decisions: Optional[List[Dict[str, str]]] = None, **kwargs) -> Dict[str, Any]:
        if decisions is None:
            decisions = [
                _decision(ITEM_1, self.item_remark),
                _decision(PARAM_MAX, self.param_remark),
            ]
        return _payload(decisions, pid=self.pid, **kwargs)

    def _get(self, role: str, pid: Optional[str] = None):
        with make_server() as server:
            return as_role(server, role).json(
                "GET", "/api/v1/p/%s/review" % (pid or self.pid)
            )

    def _post(self, role: str, payload: Dict[str, Any], **kwargs):
        with make_server() as server:
            return as_role(server, role).json(
                "POST", "/api/v1/p/%s/review/submit" % self.pid, payload, **kwargs
            )

    def test_roles_follow_the_admin_tier_matrix(self):
        """§4.2：`review` 是管理员档——未登记进管理员名单的部署者同样 403（脚注 1）。"""
        for role, expected in (
            ("visitor", 403),
            ("member", 403),
            ("deployer", 200),
            ("admin", 200),
        ):
            status, _h, body = self._get(role)
            assert status == expected, (role, body)
        assert _config_names(self.pid) == [XML_NAME], "读取接口不得改动数据"

    def test_non_admin_submit_leaves_everything_unchanged(self):
        """§16.3：403 时 `configs/` 与建议记录一字节不变。"""
        before = _read(XML_NAME, self.pid)
        statuses = _status_by_id(self.pid)
        for role in ("visitor", "member"):
            status, _h, body = self._post(role, self._payload(request_id="req-forbid-" + role))
            assert status == 403, (role, body)
            assert body["error"]["detail"]["action"] == "review"
        assert _read(XML_NAME, self.pid) == before
        assert _status_by_id(self.pid) == statuses
        assert _config_names(self.pid) == [XML_NAME]

    def test_deployer_registered_as_admin_may_review(self):
        admin_pid = seed_roles("rvwlocal", local_admin=True)
        write_target(admin_pid, XML_NAME, XML_DOC.encode("utf-8"))
        with make_server() as server:
            status, _h, body = as_role(server, "deployer_admin").json(
                "GET", "/api/v1/p/%s/review" % admin_pid
            )
        assert status == 200, body
        assert body == {"files": []}, "没有建议时给出空列表而不是报错"

    def test_write_without_csrf_header_is_forbidden(self):
        status, _h, _b = self._post(
            "admin", self._payload(request_id="req-csrf"), with_csrf=False
        )
        assert status == 403
        assert _config_names(self.pid) == [XML_NAME]

    def test_admin_may_submit_over_http(self):
        status, _h, body = self._post("admin", self._payload(request_id="req-ok"))
        assert status == 200, body
        assert body["approved"] == 2 and body["generated_file"]

    def test_submit_end_to_end_and_generated_file_is_downloadable(self):
        """UC-20 走完整 HTTP：列表 → 提交两个节点 → 按返回的文件名下载生成文件。"""
        original = _read(XML_NAME, self.pid)
        with make_server() as server:
            caller = as_role(server, "admin")
            block = caller.json("GET", "/api/v1/p/%s/review" % self.pid)[2]["files"][0]
            assert block["current_sha"] == hashlib.sha256(original).hexdigest()
            payload = {
                "request_id": "req-e2e",
                "file": block["file"],
                "source_sha": block["current_sha"],
                "decisions": [
                    {"group_key": n["group_key"], "choice": n["remarks"][0]["id"]}
                    for n in block["nodes"]
                ],
            }
            status, _h, result = caller.json(
                "POST", "/api/v1/p/%s/review/submit" % self.pid, payload
            )
            assert status == 200, result
            assert result["approved"] == 2 and result["generated_file"]
            raw = caller.request(
                "GET", "/api/v1/p/%s/files/%s/raw" % (self.pid, result["generated_file"])
            )
        assert raw[0] == 200, raw
        assert _changed(original, raw[2], XML_NAME) == {ITEM_1: "A", PARAM_MAX: "42"}
        assert _read(XML_NAME, self.pid) == original

    def test_success_bumps_remarks_only(self):
        """审阅写回属于建议域：递增 `remarks`；原文件字节不变，`files` 不得递增。"""
        with make_server() as server:
            caller = as_role(server, "admin")
            base = caller.json("GET", "/api/v1/p/%s/revision" % self.pid)[2]["parts"]
            status, _h, _b = self._post("admin", self._payload(request_id="req-rev"))
            assert status == 200
            after = caller.json("GET", "/api/v1/p/%s/revision" % self.pid)[2]["parts"]
        assert after["remarks"] == base["remarks"] + 1
        assert after["files"] == base["files"]
        assert after["favorites"] == base["favorites"] and after["settings"] == base["settings"]

    def test_failure_and_read_do_not_bump(self):
        with make_server() as server:
            caller = as_role(server, "admin")
            base = caller.json("GET", "/api/v1/p/%s/revision" % self.pid)[2]["parts"]
            assert self._post(
                "admin", self._payload([], request_id="req-409", source_sha="0" * 64)
            )[0] == 409
            assert self._post("member", self._payload([], request_id="req-403"))[0] == 403
            assert caller.json("GET", "/api/v1/p/%s/review" % self.pid)[0] == 200
            # 「决定了一个都不在这个文件里」的 200 也没有改动任何记录，同样不递增（全局约束）。
            assert self._post("admin", self._payload([_decision("/R[1]/gone[1]", "reject")], request_id="req-noop"))[0] == 200
            after = caller.json("GET", "/api/v1/p/%s/revision" % self.pid)[2]["parts"]
        assert after == base

    def test_error_envelope_shape(self):
        status, _h, body = self._post(
            "admin", self._payload([], request_id="req-env", source_sha="f" * 64)
        )
        assert status == 409
        assert body["error"]["code"] == "REVIEW_CONFLICT"
        assert body["error"]["message"] and "detail" in body["error"]

    def test_missing_request_id_is_400_over_http(self):
        payload = self._payload(request_id="req-x")
        del payload["request_id"]
        status, _h, body = self._post("admin", payload)
        assert status == 400 and body["error"]["detail"]["field"] == "request_id"

    def test_unknown_profile_is_404(self):
        assert self._get("admin")[0] == 200
        with make_server() as server:
            assert as_role(server, "admin").json("GET", "/api/v1/p/no-such-model/review")[0] == 404
            assert as_role(server, "admin").json(
                "POST", "/api/v1/p/no-such-model/review/submit", self._payload([])
            )[0] == 404


class TestLockedSequence:
    """§6.7 第 3–8 步都在机型锁内：外层持锁时服务层的嵌套调用必须可重入（R2）。"""

    def test_service_runs_inside_the_profile_lock_without_deadlock(self, seeded):
        rid = _add(ITEM_1, "A")
        with storage.profile_write(PID):
            assert _submit(_payload([_decision(ITEM_1, rid)]))["approved"] == 1
