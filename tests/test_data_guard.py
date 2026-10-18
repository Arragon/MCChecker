"""H3 数据守卫自身的测试（INH-1914 / chore h3-data-guard）。

守卫的两半：
- `data_guard.snapshot_data_tree` / `compare_snapshots` 是纯函数，这里直接喂构造
  输入做单测，全程不碰真实 `data/`；
- `tests/conftest.py` 的 session 级 autouse 夹具把纯函数接进 pytest 生命周期。
  夹具接线本身由一次「往真实 data/ 造一个 `zz_h3_selftest.tmp` 再删掉」的
  红/绿实验证明（过程与逐字输出见 `.superpowers/sdd/MCChecker_M3_M4_PLAN/h3-guard-report.md`），
  不能只靠下面的纯函数用例宣称「守卫已生效」——一个永远返回空快照的假守卫
  比没有守卫更糟，因为它看起来像有执法。所以另有
  `test_snapshot_of_real_data_tree_is_non_empty_and_normalized` 直接钉结构。
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Dict

import pytest

from conftest import REAL_DATA_DIR
from data_guard import compare_snapshots, snapshot_data_tree

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


# ---------------------------------------------------------------------------
# 纯比对函数：五类判定，各一条
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


def test_report_lists_every_difference_by_category() -> None:
    """失败消息必须分类列出差异文件，不许只报一句「哈希不匹配」把活踢给下一个人。"""
    from data_guard import format_diff_report

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


# ---------------------------------------------------------------------------
# 快照函数：结构 + 归一化 + 排除规则
# ---------------------------------------------------------------------------


def test_snapshot_of_real_data_tree_is_non_empty_and_normalized() -> None:
    """直接钉住「对真实 data/ 的快照」的结构——防一个返回空快照的假守卫。"""
    snapshot = snapshot_data_tree(REAL_DATA_DIR)
    assert snapshot, "对真实 data/ 的快照为空：守卫形同虚设"
    for rel, sha in snapshot.items():
        assert _HEX64.match(sha), f"{rel} 的值不是 64 位十六进制 sha256: {sha!r}"
        assert not rel.startswith("./"), rel
        assert not os.path.isabs(rel), rel
        assert not rel.startswith("data/"), rel
        assert "\\" not in rel and rel == os.path.normpath(rel), rel
    # 防「假哈希」：逐文件独立重算一遍 sha256，必须与快照一致。
    for rel, sha in snapshot.items():
        with open(os.path.join(REAL_DATA_DIR, *rel.split("/")), "rb") as fh:
            assert sha == _sha256_of(fh.read()), rel


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
    for spelling in spellings:
        assert snapshot_data_tree(spelling) == expected, spelling


def test_snapshot_skips_lock_file_and_directories_only_changes(tmp_path) -> None:
    """.mcchecker.lock 是运行时产物、空目录不是数据——两者都不许进快照。"""
    root = tmp_path / "tree"
    (root / "profiles").mkdir(parents=True)
    (root / "x.txt").write_text("x", encoding="utf-8")
    before = snapshot_data_tree(str(root))

    (root / ".mcchecker.lock").write_text("pid", encoding="utf-8")
    (root / "profiles" / "newdir").mkdir()
    after = snapshot_data_tree(str(root))
    assert before == after


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
