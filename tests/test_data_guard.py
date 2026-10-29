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

复审 I1 + 四项 Minor 之后，本文件另外钉住这些口径（都在纯函数层，起不了 pytest 会话）：
接线清单 `H3_GUARD_PLUGIN_MEMBERS` 的**完备性**（AST 扫 conftest）、警告通道的条目与类别
（`session_notice_warnings`）、`MCCHECKER_REQUIRE_H3` 开关的默认值与解析、快照的**代价**
（字节/秒，`SnapshotStats`），以及**读不到/中途消失**的文件如何归类。带进程的三态证明在
`tests/test_data_guard_enforcement.py`。
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import os
import posixpath
import re
from typing import Dict

import pytest

import data_guard
from data_guard import (
    DIFF_CATEGORIES,
    DIFF_LABELS,
    DIRECTORY_ENTRY_MARKER,
    H3NonEnforcementWarning,
    H3UncomparedWarning,
    NOT_ENFORCING_NOTICE,
    REQUIRE_H3_ENV_VAR,
    SnapshotStats,
    VANISHED_REASON,
    compare_snapshots,
    format_diff_report,
    h3_enforcement_required,
    has_differences,
    is_directory_entry,
    is_enforcing,
    requirement_failure_notice,
    session_notice_lines,
    session_notice_warnings,
    snapshot_data_tree,
    split_entries,
    unhashed_notice_lines,
)

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")

#: 守卫接线的命名口径（`tests/conftest.py::H3_GUARD_PLUGIN_MEMBERS` 的注释写了这条约定）：
#: conftest 里凡是模块级 `pytest_*` 钩子或 `h3_*` 夹具，都属于守卫接线，必须出现在元组里。
CONFTEST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conftest.py")
_GUARD_MEMBER_NAME = re.compile(r"\A(?:pytest_|h3_)[A-Za-z0-9_]+\Z")
#: 元组里此刻应当存在的三条接线（清单本身不许靠删空来「变得完备」）。
KNOWN_GUARD_MEMBERS = {"pytest_sessionstart", "pytest_terminal_summary", "h3_data_guard"}


def _sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _conftest_guard_members():
    """用 AST 读 `tests/conftest.py`，不 import 它（那会把 app/nicegui 整套再装一遍）。

    返回 `(模块级定义的守卫接线名, H3_GUARD_PLUGIN_MEMBERS 里声明的名字)`。
    """
    with open(CONFTEST_PATH, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=CONFTEST_PATH)
    defined = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _GUARD_MEMBER_NAME.match(node.name)
    }
    declared = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "H3_GUARD_PLUGIN_MEMBERS" for t in node.targets):
            continue
        declared = frozenset(elt.value for elt in node.value.elts)
    assert declared is not None, "tests/conftest.py 里找不到 H3_GUARD_PLUGIN_MEMBERS 的赋值"
    return defined, declared


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
    """快照的键值形状（文件与目录两类条目分开认）。

    - 文件：值是 64 位十六进制 sha256。
    - 目录（键以 `/` 结尾，B10.2 起进快照）：值恒等于 `DIRECTORY_ENTRY_MARKER`，
      而且**不可能**是 sha256——这条同时钉住「两类条目不会互相冒充」。
    - 两者：都是相对数据根的归一化 posix 路径。
    """
    for rel, value in snapshot.items():
        if is_directory_entry(rel):
            assert value == DIRECTORY_ENTRY_MARKER, "目录条目的值不是标记: {!r}".format(rel)
            assert _HEX64.match(value) is None, rel
            core = rel[:-1]
        else:
            assert _HEX64.match(value), "值不是 64 位十六进制 sha256: {!r}".format(value)
            core = rel
        assert not rel.startswith("./"), rel
        assert not os.path.isabs(rel), rel
        assert not rel.startswith("data/"), rel
        assert "\\" not in rel and core == posixpath.normpath(core), rel


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

    期望的目录条目**逐个硬写**，不从 `files` 推导：推导出来的期望集会跟着实现一起
    放宽（同一个仓库里已经踩过两次这个味道，见 docs/know-how.md 的 M3-08 那条）。
    """
    root = tmp_path / "tree"
    files = {
        "top.txt": "top\n",
        "profiles/default/bindings.json": '{"v": 1}',
        "profiles/default/configs/probe.xml": "<a>1</a>",
        "records/deep/nested/leaf.json": "{}",
    }
    expected_dirs = {
        "profiles/",
        "profiles/default/",
        "profiles/default/configs/",
        "records/",
        "records/deep/",
        "records/deep/nested/",
    }
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # 用 bytes 写：Windows 的文本模式会把 \n 翻成 \r\n，而下面硬写的
        # sha256 对应的是原始 UTF-8 字节，不能被平台的换行转换改写。
        path.write_bytes(content.encode("utf-8"))

    snapshot = snapshot_data_tree(str(root))
    assert snapshot, "我自己造的树不许快照为空"
    assert set(snapshot) == set(files) | expected_dirs, sorted(snapshot)
    _assert_key_shape(snapshot)
    # 防「假哈希」：逐文件独立重算一遍 sha256，必须与快照一致。
    for rel in files:
        on_disk = (root / rel).read_bytes()
        assert snapshot[rel] == _sha256_of(on_disk), rel
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
    目录不再和锁文件一起豁免：B10.2 之后目录是**要**进快照的，那条单独由
    `test_snapshot_records_created_and_removed_directories` 钉住。
    """
    root = tmp_path / "tree"
    (root / "profiles" / "default").mkdir(parents=True)
    (root / "x.txt").write_text("x", encoding="utf-8")
    before = snapshot_data_tree(str(root))

    (root / ".mcchecker.lock").write_text("pid", encoding="utf-8")
    after = snapshot_data_tree(str(root))
    assert before == after, "根一级的锁不算数据变化"

    (root / "profiles" / "default" / ".mcchecker.lock").write_text("pid", encoding="utf-8")
    nested = snapshot_data_tree(str(root))
    assert "profiles/default/.mcchecker.lock" in nested, sorted(nested)


def test_snapshot_records_created_and_removed_directories(tmp_path) -> None:
    """B10.2 的正面证明：**空目录**的新建与删除本身就是一种可检出的变化。

    这条存在的原因是实测过的：`GET /files` 一路由读路径 `_ensure_dirs()` 长出
    `archive/`、`records/`、`cache/`、`cache/parse_tree/`，权威文件哈希逐条未变，
    于是旧守卫（只哈希常规文件）对此完全沉默——「只读接口没改数据」这句话
    在机制上无法验证。目录进快照之后它变成了可验证、可判红的命题。
    """
    root = tmp_path / "tree"
    (root / "profiles").mkdir(parents=True)
    (root / "a.txt").write_text("a", encoding="utf-8")
    before = snapshot_data_tree(str(root))
    assert set(before) == {"a.txt", "profiles/"}, sorted(before)

    # 新建一个空目录：一个字节都没写，但数据根确实被改了。
    (root / "archive").mkdir()
    created = compare_snapshots(before, snapshot_data_tree(str(root)))
    assert created["created"] == ["archive/"], created
    assert created["deleted"] == [] and has_differences(created) is True

    # 删掉它（目录本身没了，文件一个没动）：同样必须红。
    (root / "archive").rmdir()
    removed = compare_snapshots(before, snapshot_data_tree(str(root)))
    assert removed["created"] == [] and removed["deleted"] == []

    os.rmdir(os.path.join(str(root), "profiles"))
    gone = compare_snapshots(before, snapshot_data_tree(str(root)))
    assert gone["deleted"] == ["profiles/"], gone
    assert gone["created"] == [] and has_differences(gone) is True


def test_empty_directory_churn_is_created_plus_deleted_never_renamed(tmp_path) -> None:
    """删一个空目录 + 建另一个空目录 必须报成 新建+删除，不许配对成「改名」。

    目录条目的值是同一个常量标记，一旦混进改名的 sha256 配对，任何「读路径长出
    新目录、同时旧目录没了」都会被写成一句假的「archive/ -> records/（改名）」，
    把方向说反（这正是 `compare_snapshots` docstring 里那条空文件精度上限的形状，
    目录不能也踩一遍）。
    """
    root = tmp_path / "tree"
    root.mkdir()
    (root / "archive").mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    before = snapshot_data_tree(str(root))

    (root / "records").mkdir()
    (root / "archive").rmdir()
    diffs = compare_snapshots(before, snapshot_data_tree(str(root)))
    assert diffs["renamed"] == [], diffs
    assert diffs["created"] == ["records/"], diffs
    assert diffs["deleted"] == ["archive/"], diffs


def test_existing_empty_root_is_still_not_enforcing(tmp_path) -> None:
    """「快照为空 ≠ 安全」这条诚实话在目录进快照之后必须还成立。

    根存在但里面什么都没有（干净检出刚 `mkdir` 出一个空 data/，或服务只建了根）
    依旧是非执法态，公告与警告两条通道都要说破，不许读成「保护完且干净」。
    """
    empty_root = tmp_path / "tree"
    empty_root.mkdir()
    snapshot = snapshot_data_tree(str(empty_root))
    assert snapshot == {}
    assert is_enforcing(snapshot) is False
    lines = "\n".join(session_notice_lines(snapshot))
    assert "H3 守卫未执法" in lines and "H3 守卫执法中" not in lines, lines
    assert [cat for _msg, cat in session_notice_warnings(snapshot, None)] == [
        H3NonEnforcementWarning
    ]


def test_enforcing_notice_reports_files_and_dirs_separately(tmp_path) -> None:
    """公告里的「N 个文件」只能是文件数：目录进快照后拿 `len(baseline)` 当文件数就是撒谎。

    一树空目录（`data/` 被某个读路径长出来的那种形状）必须报成「0 个文件、4 个目录」
    而不是「4 个文件」，否则读的人会把「没哈希到任何字节」当成「保护了 4 个东西」。
    """
    root = tmp_path / "tree"
    for name in ("archive", "records", "cache", "cache/parse_tree"):
        os.makedirs(os.path.join(str(root), *name.split("/")))
    snapshot = snapshot_data_tree(str(root))
    files, dirs = split_entries(snapshot)
    assert files == [] and dirs == ["archive/", "cache/", "cache/parse_tree/", "records/"], snapshot

    lines = "\n".join(session_notice_lines(snapshot))
    assert "基线快照 0 个文件、4 个目录" in lines, lines
    assert "1 个文件" not in lines, lines
    assert "H3 守卫执法中" in lines, lines

    (root / "a.txt").write_text("a", encoding="utf-8")
    mixed = "\n".join(session_notice_lines(snapshot_data_tree(str(root))))
    assert "基线快照 1 个文件、4 个目录" in mixed, mixed


# ---------------------------------------------------------------------------
# 端到端烟测（临时树）：快照 + 比对两个纯函数串起来可用
# ---------------------------------------------------------------------------


def test_snapshot_and_compare_end_to_end_on_temp_tree(tmp_path) -> None:
    """快照 + 比对对一棵小临时树端到端可用（不依赖 pytest 生命周期）。"""
    snap = _snapshot_a(tmp_path)
    assert set(snap) == {
        "top.txt",
        "profiles/",
        "profiles/default/",
        "profiles/default/bindings.json",
    }, sorted(snap)
    root = tmp_path / "tree"
    (root / "zz.tmp").write_text("leak", encoding="utf-8")
    diffs = compare_snapshots(snap, snapshot_data_tree(str(root)))
    assert diffs["created"] == ["zz.tmp"]
    assert has_differences(diffs) is True


# ---------------------------------------------------------------------------
# 接线清单的完备性（复审 Minor：「忘加就红」只对被人断言到的钩子成立）
# ---------------------------------------------------------------------------


def test_every_conftest_guard_hook_is_listed_in_the_plugin_tuple() -> None:
    """钉住 `H3_GUARD_PLUGIN_MEMBERS` 的**完备性**，而不只是「已列出的那些被证明」。

    内部会话只重导出元组里的名字，所以一条纯增量、没人断言的新钩子漏进元组时，
    永久证明照样全绿——那句「漏了就会红」只对内部用例真的观察到的钩子成立。这里直接
    扫 conftest 的模块级定义：`pytest_*` 钩子与 `h3_*` 夹具一个都不许漏；反向也不许留
    过期名字（改名已被内部 conftest 的 `getattr` 钉住，这里连「清单里有条目但函数没了」
    一起钉，并当场说出是哪个名字）。清单本身还必须有那三条接线，防止「删空即完备」。
    """
    defined, declared = _conftest_guard_members()
    missing = sorted(defined - declared)
    stale = sorted(declared - defined)
    assert not missing, "tests/conftest.py 里这些守卫接线没进 H3_GUARD_PLUGIN_MEMBERS：%s" % (
        ", ".join(missing)
    )
    assert not stale, "H3_GUARD_PLUGIN_MEMBERS 里这些名字在 tests/conftest.py 已不存在：%s" % (
        ", ".join(stale)
    )
    assert KNOWN_GUARD_MEMBERS <= declared, sorted(declared)


# ---------------------------------------------------------------------------
# 警告通道与硬要求开关（复审 I1：两种绿不许都只是终端文案）
# ---------------------------------------------------------------------------


def test_non_enforcement_and_uncompared_bytes_are_warnings_not_only_prose() -> None:
    """「没执法」与「有字节没比较」都必须进警告通道；执法且全树可读时必须为空。

    类别名会印在警告行上，机器可以按名 grep，不必读中文段落里的某一句。执法态返回空
    列表正是三态可区分的那一半（另一个状态各带一条）。
    """
    items = session_notice_warnings({}, None)
    assert [cat for _msg, cat in items] == [H3NonEnforcementWarning], items
    assert [msg for msg, _cat in items] == [NOT_ENFORCING_NOTICE], items
    assert session_notice_warnings(None, None) == [(NOT_ENFORCING_NOTICE, H3NonEnforcementWarning)]

    readable = SnapshotStats(files=2, total_bytes=8, seconds=0.01)
    assert session_notice_warnings({"a.txt": _sha256_of(b"a")}, readable) == []

    stats = SnapshotStats(unhashed=[("secret.txt", "读取失败 PermissionError")])
    warned = session_notice_warnings({"a.txt": _sha256_of(b"a")}, stats)
    assert [cat for _msg, cat in warned] == [H3UncomparedWarning], warned
    assert "secret.txt" in warned[0][0], warned


def test_h3_enforcement_required_switch_defaults_off_and_names_the_var(monkeypatch) -> None:
    """开关口径：`MCCHECKER_REQUIRE_H3=1` 开、默认关；只有空串与 `0` 才算关。

    写 `=true`/`=yes` 的人想要的是执法，绝不能静默拿到「不执法也绿」——那恰好是 H3
    要消灭的那类失效，所以非空非 `0` 一律按打开处理。
    """
    assert REQUIRE_H3_ENV_VAR == "MCCHECKER_REQUIRE_H3"
    monkeypatch.delenv(REQUIRE_H3_ENV_VAR, raising=False)
    assert h3_enforcement_required() is False, "默认必须关：干净检出与 CI 没有 data/"
    assert h3_enforcement_required({}) is False
    assert h3_enforcement_required({REQUIRE_H3_ENV_VAR: ""}) is False
    assert h3_enforcement_required({REQUIRE_H3_ENV_VAR: "0"}) is False
    monkeypatch.setenv(REQUIRE_H3_ENV_VAR, "1")
    assert h3_enforcement_required() is True
    for spelling in ("true", "yes", " 1 ", "2"):
        monkeypatch.setenv(REQUIRE_H3_ENV_VAR, spelling)
        assert h3_enforcement_required() is True, spelling
    # 硬失败必须把开关与原因点名出来，光有退出码没法 triage。
    notice = requirement_failure_notice()
    assert REQUIRE_H3_ENV_VAR in notice and "未执法" in notice, notice


# ---------------------------------------------------------------------------
# 守卫的代价与盲区（复审两条 Minor：价格要看得见、读不到不许算成没变化）
# ---------------------------------------------------------------------------


def test_snapshot_stats_accumulate_the_price_of_both_passes(tmp_path) -> None:
    """两轮快照的字节数/秒数都要记账，而且是**累加**在同一个 stats 上。

    守卫每会话把整棵树哈希两遍（会话开头一遍、teardown 一遍），代价随真实数据线性
    增长；第二遍跑在 teardown 里本来毫无声响，几 GB 的树看起来就像 pytest 卡死。
    """
    root = tmp_path / "tree"
    root.mkdir()
    payload = 256 * 1024
    (root / "a.txt").write_bytes(b"abc")
    (root / "sub").mkdir()
    (root / "sub" / "big.bin").write_bytes(b"x" * payload)

    stats = SnapshotStats()
    first = snapshot_data_tree(str(root), stats)
    # 目录条目进快照，但**不**计入 `files`/`total_bytes`——那两项是「哈希了多少字节」的价格。
    assert set(first) == {"a.txt", "sub/", "sub/big.bin"}, sorted(first)
    assert (stats.files, stats.total_bytes) == (2, 3 + payload), stats
    assert stats.seconds > 0.0, "耗时必须真的测出来，不是写死的 0"
    assert stats.unhashed == []

    snapshot_data_tree(str(root), stats)  # 会话末那第二遍
    assert (stats.files, stats.total_bytes) == (4, 2 * (3 + payload)), stats

    # 不传 stats 时口径完全不变：返回值仍然只是 {路径: sha256}。
    assert snapshot_data_tree(str(root)) == first


def test_enforcing_notice_prints_the_price_next_to_the_file_count() -> None:
    """执法那行公告必须带上字节与秒，同时原有的文件数/盲区文案一条不许被挤掉。"""
    cost = SnapshotStats(files=6, total_bytes=4096, seconds=1.5)
    priced = "\n".join(session_notice_lines({"a.txt": _sha256_of(b"a")}, cost))
    assert "4096 字节" in priced and "1.50 秒" in priced, priced
    assert "基线快照 1 个文件" in priced, priced
    assert "不证明期间无人写入" in priced, priced
    # 没记账时不许出现半截价格（老的单参调用口径不变）。
    assert "字节" not in "\n".join(session_notice_lines({"a.txt": _sha256_of(b"a")})), priced


@pytest.mark.skipif(os.name == "nt", reason="Windows 不支持 POSIX 权限位，chmod(0) 拦不住读取")
def test_unreadable_file_is_named_as_uncompared_instead_of_blaming_the_guard(tmp_path) -> None:
    """读不到的文件不许在 teardown 里裸抛 traceback（那是把机器的权限问题算成守卫炸了），
    也不许当成「没变化」——它本轮**根本没被比较**，必须逐条点名并走警告通道。
    """
    root = tmp_path / "tree"
    root.mkdir()
    (root / "ok.txt").write_text("ok", encoding="utf-8")
    secret = root / "secret.txt"
    secret.write_text("nope", encoding="utf-8")
    secret.chmod(0)

    stats = SnapshotStats()
    snapshot = snapshot_data_tree(str(root), stats)  # 关键：这一句不许抛
    secret.chmod(0o600)

    assert set(snapshot) == {"ok.txt"}, sorted(snapshot)
    assert [rel for rel, _reason in stats.unhashed] == ["secret.txt"], stats.unhashed
    assert "读取失败 PermissionError" in stats.unhashed[0][1], stats.unhashed
    assert stats.files == 1 and stats.total_bytes == 2, stats

    notice = "\n".join(unhashed_notice_lines(stats))
    assert "没有被比较" in notice and "secret.txt" in notice, notice
    assert "数据完好" in notice, notice
    # 两轮都读不到时比对是「没差异」，所以唯一的响动就是这条警告——它必须响。
    assert [cat for _msg, cat in session_notice_warnings(snapshot, stats)] == [H3UncomparedWarning]


@pytest.mark.skipif(os.name == "nt", reason="Windows 不支持 POSIX 权限位，chmod(0) 拦不住遍历")
def test_unreadable_directory_is_named_instead_of_silently_vanishing(tmp_path) -> None:
    """`os.walk` 默认把 scandir 的异常咽掉：一个读不了的目录会让**整棵子树**静默变成
    「没有差异」，比单个文件更危险，所以同样点名（尾部带 `/` 表明是目录）。
    """
    root = tmp_path / "tree"
    locked = root / "locked"
    locked.mkdir(parents=True)
    (locked / "inside.txt").write_text("x", encoding="utf-8")
    (root / "ok.txt").write_text("ok", encoding="utf-8")
    locked.chmod(0)
    try:
        stats = SnapshotStats()
        snapshot = snapshot_data_tree(str(root), stats)
    finally:
        locked.chmod(0o755)

    assert set(snapshot) == {"ok.txt", "locked/"}, sorted(snapshot)
    # `locked/` 本身进快照是事实（列这个条目只需要读父目录），读不进去的是它**里面**
    # 的东西——那部分才是不许沉默的部分，所以它进 `unhashed` 而不是消失。
    assert [rel for rel, _reason in stats.unhashed] == ["locked/"], stats.unhashed
    assert "目录无法遍历 PermissionError" in stats.unhashed[0][1], stats.unhashed
    assert [cat for _msg, cat in session_notice_warnings(snapshot, stats)] == [H3UncomparedWarning]


def test_file_that_vanishes_mid_snapshot_is_compared_as_absent_and_explained(
    tmp_path, monkeypatch
) -> None:
    """`os.walk` 列出之后、`open` 之前文件没了（真实服务正在 churn `data/` 时很现实）。

    诚实的分类是「这些字节已经不在这棵树里」：让它**不进快照**，比对就按事实把它归到
    删除（基线有、末尾没）或新建（基线没、末尾有）——响得起来，又不会把权限/并发问题
    渲染成守卫自己炸了。同时登记真实原因，免得读者以为是某个用例动的手。
    这里窄窄地替换 `data_guard.open`，只为把那 1 毫秒的竞态窗口做成确定性的。
    """
    root = tmp_path / "tree"
    root.mkdir()
    gone = root / "gone.txt"
    gone.write_text("bye", encoding="utf-8")
    before = snapshot_data_tree(str(root))
    assert set(before) == {"gone.txt"}, sorted(before)

    real_open = builtins.open

    def _vanishing_open(file, *args, **kwargs):
        if os.path.abspath(str(file)) == os.path.abspath(str(gone)):
            raise FileNotFoundError(2, "No such file or directory", str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(data_guard, "open", _vanishing_open, raising=False)
    stats = SnapshotStats()
    after = snapshot_data_tree(str(root), stats)  # 不许抛

    assert after == {}, sorted(after)
    assert stats.unhashed == [("gone.txt", VANISHED_REASON)], stats.unhashed
    assert stats.files == 0 and stats.total_bytes == 0, stats
    diffs = compare_snapshots(before, after)
    assert diffs["deleted"] == ["gone.txt"], diffs
    assert has_differences(diffs) is True
    # 消失不是「没发生」：这一条差异要在点名报告里出现。
    assert DIFF_LABELS["deleted"] in format_diff_report(diffs)
