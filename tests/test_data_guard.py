"""H3 数据守卫自身的测试（INH-1914 / chore h3-data-guard）。

守卫的三半：
- `data_guard.snapshot_data_tree` / `compare_snapshots` 是纯函数，这里直接喂构造
  输入做单测，全程不碰真实 `data/`；
- `tests/conftest.py` 的 session 级 autouse 夹具 + `pytest_sessionstart` +
  `pytest_terminal_summary` 把纯函数接进 pytest 生命周期；
- 接线**本身**由 `tests/test_data_guard_enforcement.py` 用 `pytester` 起独立进程
  永久证明（四类破坏各一次 + 干净树对照 + 数据根缺失对照），不在本文件的职责里。

关于真实 `data/`（C1）：它是 gitignored 的，干净检出 / CI / 别人的 clone 里**根本没有**
这棵树。所以本文件一律不对真实树断言「里面有文件」——那等于把这台机器的测试残留
当成全项目的前提，还会让「快照为空 = 没人执法」那种假绿重新变得看不出来。
执法与否改由守卫自己在会话摘要里公告（`session_notice_lines`），它的两个分支由
`pytester` 用例钉住；本文件只钉公告文案本身的口径。
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Dict

from data_guard import (
    DIFF_CATEGORIES,
    DIFF_LABELS,
    compare_snapshots,
    format_diff_report,
    has_differences,
    is_enforcing,
    session_notice_lines,
    snapshot_data_tree,
)

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")


def _sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _snapshot_a(tmp_path) -> Dict[str, str]:
    """构造一棵与真实树无关的小数据根。

    名字刻意不叫 `data`：autouse 的 `isolated_data_root` 已经在 `tmp_path/data`
    下建好了业务目录树，撞上去的正是下面这两条本该证明的事。
    """
    root = tmp_path / "tree"
    (root / "profiles" / "default").mkdir(parents=True)
    (root / "top.txt").write_text("top\n", encoding="utf-8")
    (root / "profiles" / "default" / "bindings.json").write_text("{}", encoding="utf-8")
    return snapshot_data_tree(str(root))


def _assert_key_shape(snapshot: Dict[str, str]) -> None:
    """快照的键值形状：64 位十六进制 sha256 + 相对数据根的归一化 posix 路径。"""
    for rel, sha in snapshot.items():
        assert _HEX64.match(sha), "值不是 64 位十六进制 sha256: {!r}".format(sha)
        assert not rel.startswith("./"), rel
        assert not os.path.isabs(rel), rel
        assert not rel.startswith("data/"), rel
        assert "\\" not in rel and rel == os.path.normpath(rel), rel


# ---------------------------------------------------------------------------
# 纯比对函数：四类判定，各一条
# ---------------------------------------------------------------------------


def test_compare_identical_snapshots_reports_no_differences() -> None:
    before = {"a.txt": _sha256_of(b"a"), "b/c.txt": _sha256_of(b"c")}
    assert compare_snapshots(before, dict(before)) == {
        "created": [],
        "deleted": [],
        "content_changed": [],
        "renamed": [],
    }


def test_compare_detects_created() -> None:
    sha = _sha256_of(b"new")
    diffs = compare_snapshots({}, {"leak.txt": sha})
    assert diffs["created"] == ["leak.txt"]
    assert diffs["deleted"] == []
    assert diffs["content_changed"] == []
    assert diffs["renamed"] == []


def test_compare_detects_deleted() -> None:
    sha = _sha256_of(b"old")
    diffs = compare_snapshots({"gone.txt": sha}, {})
    assert diffs["deleted"] == ["gone.txt"]
    assert diffs["created"] == []
    assert diffs["content_changed"] == []
    assert diffs["renamed"] == []


def test_compare_detects_content_changed() -> None:
    old = _sha256_of(b"old")
    new = _sha256_of(b"new")
    diffs = compare_snapshots({"x.xml": old}, {"x.xml": new})
    assert diffs["content_changed"] == [("x.xml", old, new)]
    assert diffs["created"] == []
    assert diffs["deleted"] == []
    assert diffs["renamed"] == []


def test_compare_detects_renamed_not_created_plus_deleted() -> None:
    """同内容换路径 = 改名：sha256 同时出现在删除集与新建集里，必须归成 renamed。"""
    sha = _sha256_of(b"payload")
    diffs = compare_snapshots({"configs/old.xml": sha}, {"configs/new.xml": sha})
    assert diffs["renamed"] == [("configs/old.xml", "configs/new.xml", sha)]
    assert diffs["created"] == []
    assert diffs["deleted"] == []
    assert diffs["content_changed"] == []


def test_diff_categories_have_a_single_source_of_truth() -> None:
    """类别键只许有一份（复审 Minor）：比对结果、判定、报告三处必须同源。"""
    assert DIFF_CATEGORIES == tuple(DIFF_LABELS)
    diffs = compare_snapshots({"a": _sha256_of(b"a")}, {"b": _sha256_of(b"b")})
    assert tuple(diffs) == DIFF_CATEGORIES
    assert has_differences(diffs) is True
    assert has_differences(compare_snapshots({}, {})) is False
    # 报告按同一份键集出：有差异的类别一定被点名，不许悄悄漏排一类。
    report = format_diff_report(diffs)
    for category in DIFF_CATEGORIES:
        if diffs[category]:
            assert DIFF_LABELS[category] in report, report


def test_report_lists_every_difference_by_category() -> None:
    """失败消息必须分类列出差异文件，不许只报一句「哈希不匹配」把活踢给下一个人。"""
    old, new, gone, born = (_sha256_of(s) for s in (b"old", b"new", b"g", b"b"))
    diffs = compare_snapshots(
        {"changed.xml": old, "removed.xml": gone, "moved.xml": _sha256_of(b"m")},
        {
            "changed.xml": new,
            "added.xml": born,
            "renamed.xml": _sha256_of(b"m"),
        },
    )
    report = format_diff_report(diffs)
    for needle in ("created", "deleted", "content-changed", "renamed",
                   "added.xml", "removed.xml", "changed.xml",
                   "moved.xml", "renamed.xml"):
        assert needle in report, report


def test_report_states_what_green_proves_and_the_run_py_triage() -> None:
    """I2 + 复审 Minor：红消息里要写清「绿只证明末态」，并给出应用同占 data/ 的排查步。

    只有测试会红的说法是错的：这台机器上 `run.py` 起的服务和测试共用同一个数据根，
    第一嫌疑常常是那个进程，不是某个用例。
    """
    diffs = compare_snapshots({"a": _sha256_of(b"a")}, {})
    report = format_diff_report(diffs)
    assert "run.py" in report, report
    assert "绿只证明" in report and "末态" in report, report
    # 两个盲区必须是被说出来的，不是埋在文档里等有心人去翻。
    assert "isolated_task_runner" in report, report


# ---------------------------------------------------------------------------
# 快照函数：结构 + 归一化 + 排除规则
# ---------------------------------------------------------------------------


def test_snapshot_of_a_controlled_tree_is_non_empty_and_normalized(tmp_path) -> None:
    """钉住快照的结构与真实哈希——防一个返回空快照/假哈希的守卫。

    C1 之前这条断言的是「对**真实 data/** 的快照非空」，而 `data/` 是 gitignored 的
    本机残留：干净检出里它是 `{}`，整套交付用例红一条，且在这台机器上「绿」恰恰
    意味着没执法。换法不是放弃反空快照，而是把「非空」建在**我能控制的树**上
    （键形状 + 逐文件独立重算 sha256 一条没减），真实树的执法与否交给守卫自己
    公告（`test_session_notice_...` 与 `tests/test_data_guard_enforcement.py`）。
    """
    root = tmp_path / "tree"
    files = {
        "top.txt": "top\n",
        "profiles/default/bindings.json": '{"v": 1}',
        "profiles/default/configs/probe.xml": "<a>1</a>",
        "records/deep/nested/leaf.json": "{}",
    }
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    snapshot = snapshot_data_tree(str(root))
    assert snapshot, "我自己造的树不许快照为空"
    assert set(snapshot) == set(files)
    _assert_key_shape(snapshot)
    # 防「假哈希」：逐文件独立重算一遍 sha256，必须与快照一致。
    for rel, sha in snapshot.items():
        on_disk = (root / rel).read_bytes()
        assert sha == _sha256_of(on_disk), rel
    assert snapshot["top.txt"] == _sha256_of(b"top\n")


def test_snapshot_of_missing_root_is_empty_but_not_clean(tmp_path) -> None:
    """根不存在 -> 空快照；这是「没东西可保护」的入口，绝不允许被读成「保护好了」。"""
    absent = str(tmp_path / "nope")
    snapshot = snapshot_data_tree(absent)
    assert snapshot == {}
    assert is_enforcing(snapshot) is False
    assert is_enforcing({"a": _sha256_of(b"a")}) is True


def test_session_notice_keeps_nothing_to_protect_and_protected_apart() -> None:
    """C1 的核心口径：两种绿必须有不同文案，且未执法那条要说破「没保护任何真实数据」。"""
    empty_lines = "\n".join(session_notice_lines({}))
    none_lines = "\n".join(session_notice_lines(None))  # 钩子没跑也不能装成执法
    enforcing_lines = "\n".join(session_notice_lines({"a.txt": _sha256_of(b"a")}))

    assert "H3 守卫未执法" in empty_lines, empty_lines
    assert "data/ 不存在或为空" in empty_lines, empty_lines
    assert "没有保护任何真实数据" in empty_lines, empty_lines
    assert "H3 守卫执法中" not in empty_lines, empty_lines
    assert empty_lines == none_lines
    assert "H3 守卫执法中" in enforcing_lines, enforcing_lines
    assert "基线快照 1 个文件" in enforcing_lines, enforcing_lines
    assert "H3 守卫未执法" not in enforcing_lines, enforcing_lines
    # 执法分支也要当场说清绿的含义（I2），不许只报个数。
    assert "不证明期间无人写入" in enforcing_lines, enforcing_lines


def test_snapshot_is_identical_across_root_spellings(tmp_path, monkeypatch) -> None:
    """`data` / `./data` / `data/` / 绝对路径必须得到同一份快照。

    历史上聚合基线号会漂移，机制之一就是同一棵树换一种拼法写出不同的哈希；
    这条把它钉死。
    """
    root = tmp_path / "tree"
    (root / "profiles" / "default").mkdir(parents=True)
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "profiles" / "b.xml").write_text("<b/>", encoding="utf-8")

    absolute = str(root)
    spellings = [
        absolute,
        absolute + os.sep,
        os.path.join(absolute, "."),
    ]
    monkeypatch.chdir(tmp_path)
    spellings += ["tree", "./tree", "tree/"]

    expected = snapshot_data_tree(absolute)
    assert expected, "临时树不该快照为空"
    _assert_key_shape(expected)
    for spelling in spellings:
        assert snapshot_data_tree(spelling) == expected, spelling


def test_snapshot_skips_lock_file_at_root_only(tmp_path) -> None:
    """.mcchecker.lock 只在**根一级**豁免；深层同名文件是未知文件，必须进快照。

    磁盘锁的唯一出处是 `app/server/http.py::SingleInstanceLock`，它只写在数据根，
    所以豁免收窄不会误伤合法产物；反过来说 `data/profiles/default/.mcchecker.lock`
    这种文件以前按 basename 全树豁免是**看不见的洞**（复审 Minor）。
    空目录不是数据，与锁文件一起都不许进快照。
    """
    root = tmp_path / "tree"
    (root / "profiles" / "default").mkdir(parents=True)
    (root / "x.txt").write_text("x", encoding="utf-8")
    before = snapshot_data_tree(str(root))

    (root / ".mcchecker.lock").write_text("pid", encoding="utf-8")
    (root / "profiles" / "newdir").mkdir()
    after = snapshot_data_tree(str(root))
    assert before == after, "根一级的锁与空目录都不算数据变化"

    (root / "profiles" / "default" / ".mcchecker.lock").write_text("pid", encoding="utf-8")
    nested = snapshot_data_tree(str(root))
    assert "profiles/default/.mcchecker.lock" in nested, sorted(nested)


# ---------------------------------------------------------------------------
# 端到端烟测（临时树）：快照 + 比对两个纯函数串起来可用
# ---------------------------------------------------------------------------


def test_snapshot_and_compare_end_to_end_on_temp_tree(tmp_path) -> None:
    """快照 + 比对对一棵小临时树端到端可用（不依赖 pytest 生命周期）。"""
    snap = _snapshot_a(tmp_path)
    assert set(snap) == {"top.txt", "profiles/default/bindings.json"}
    root = tmp_path / "tree"
    (root / "zz.tmp").write_text("leak", encoding="utf-8")
    diffs = compare_snapshots(snap, snapshot_data_tree(str(root)))
    assert diffs["created"] == ["zz.tmp"]
    assert has_differences(diffs) is True
