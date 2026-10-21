"""H3 守卫**执法路径**的永久证明（INH-1914 复审 I3）。

上一版的「接线已生效」只有一次性手工实验（往真实 `data/` 写一个探针再删），
仓库里没有任何东西保证它一直成立：把 `h3_data_guard` 改成 `print`，交付的
纯函数用例仍然全绿。这里改用 pytest 自带的 `pytester` 夹具 +
`runpytest_subprocess`，在**独立进程**里跑一次真正的 pytest 会话，每一类差异
（新建 / 删除 / 内容变更 / 改名，外加 B10.2 之后目录的新建与删除）各自必须拿到
「退出码 1 + 会话末 teardown ERROR + 相对路径点名」，另加两条口径：干净树必须绿且
公告「执法中」，数据根缺失必须绿但公告**未执法**（C1：「没数据可保护」和「保护完且
干净」不许长成同一个观测结果）。

硬约束：**全程不碰仓库真实的 `data/`**。内部会话的数据根是 `tmp_path` 下临时造的树，
真实 conftest 的 `REAL_DATA_DIR` 只在**子进程**里被改指到那棵树，主进程一行不动。

需要 `pytest.ini` 的 `addopts = -p pytester` 才能拿到 `pytester` 夹具；那行被删掉时
这里不会静默跳过，而是整组用例一起报 `fixture 'pytester' not found`。

未执法那一态还要再证一层（复审 I1）：终端文案机器读不出来——两种绿都是退出码 0 加
同一句「N passed」，CI 日志、JUnit 消费者和卡片上贴的数字都分不清。于是加上三条：
不带 `MCCHECKER_REQUIRE_H3` 时必须以「1 warning」收尾且仍绿；带它时必须非 0 退出并
点名失败原因；执法那一态两者都没有。**三个状态的统计行必须两两不同**，这才是本卡的
落点——不是「多打一行黄字」，是「让抄数字的人抄到不同的数字」。
"""

from __future__ import annotations

import os
import re
from typing import Dict

import pytest

#: 本文件所在目录——内部会话靠它 import 仓库**真实**的 `tests/conftest.py`。
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

#: 内部会话的基线树：相对路径 -> 内容。刻意用带层级的路径，点名时必须原样出现。
BASELINE_FILES: Dict[str, str] = {
    "profiles/default/bindings.json": '{"v": 1}\n',
    "records/gone.json": '{"gone": true}\n',
}

#: 基线里**空的**目录：让「删掉一个目录」这一类破坏有东西可删。
#: 硬写在这里而不是从 `BASELINE_FILES` 的父目录推导——推导出来的期望集会跟着种子一起变，
#: 目录那半边快照一旦被写回「不进快照」，这条对照就自己失效了（同 docs/know-how.md 的白名单教训）。
BASELINE_DIRS = ("archive",)

#: 公告的判据子串（正文在 `data_guard.py`，这里只认前缀，别把文案钉死两遍）。
NOT_ENFORCING_MARK = "H3 守卫未执法："
ENFORCING_MARK = "H3 守卫执法中："
#: 第三态（硬要求）的判据：警告类别名、开关名与失败文案的前缀。
NON_ENFORCEMENT_WARNING = "H3NonEnforcementWarning"
REQUIRE_ENV_VAR = "MCCHECKER_REQUIRE_H3"
REQUIRE_FAIL_MARK = "H3 守卫执法要求未满足"

#: pytest 自己写结果的区段起点（报错节 / 失败节 / 简短摘要节）。
_SUMMARY_REGION = re.compile(
    r"^\s*=+\s*(?:ERRORS|FAILURES|short test summary info)", re.MULTILINE
)

# ---------------------------------------------------------------------------
# 生成内部会话用的两份模板
# ---------------------------------------------------------------------------

#: 内部 conftest：**不复制任何守卫逻辑**，import 仓库真实的 tests/conftest.py，
#: 按它自己声明的 `H3_GUARD_PLUGIN_MEMBERS` 清单原样重导出全部接线。
#: 于是「夹具改成 print」「比对结果不判失败」「公告钩子被删」在这里一律立刻红。
INNER_CONFTEST = '''"""由 tests/test_data_guard_enforcement.py 生成的一次性 conftest（在 tmp 下，不入库）。"""
import importlib.util
import os
import sys

_TESTS_DIR = {tests_dir!r}
_DATA_ROOT = {data_root!r}

if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

_spec = importlib.util.spec_from_file_location(
    "_h3_real_conftest", os.path.join(_TESTS_DIR, "conftest.py")
)
_real = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real)

# 真实 conftest 在 import 期把 REAL_DATA_DIR 绑到仓库 data/；本进程改指临时树。
# 主进程的 REAL_DATA_DIR 不受影响（是另一个进程），真实 data/ 一个字节都不动。
_real.REAL_DATA_DIR = _DATA_ROOT
for _name in _real.H3_GUARD_PLUGIN_MEMBERS:
    globals()[_name] = getattr(_real, _name)
'''

#: 内部测试：只做一次破坏动作，且不恢复——会话末的守卫必须看见。
INNER_TEST = '''"""由 tests/test_data_guard_enforcement.py 生成的一次性用例（在 tmp 下，不入库）。"""
import os

DATA_ROOT = {data_root!r}


def test_{verb}() -> None:
{body}
'''

#: 各类差异各自的破坏动作与判据：报告里的中文标签、必须被点名的相对路径。
#: `dir_created` / `dir_removed` 是 B10.2 新加的两条——它们**一个字节都不写**，
#: 旧快照（只哈希常规文件）对此完全沉默，所以只有这两条能让「读路径长出空目录」
#: 这类缺陷永远红得起来。
CATEGORIES: Dict[str, Dict[str, str]] = {
    "created": {
        "body": '    with open(os.path.join(DATA_ROOT, "profiles", "default", "zz_created.tmp"), "w", encoding="utf-8") as fh:\n'
        '        fh.write("leak")\n'
        "    assert os.path.isfile(os.path.join(DATA_ROOT, 'profiles', 'default', 'zz_created.tmp'))",
        "label": "新建 (created)",
        "needle": "profiles/default/zz_created.tmp",
    },
    "deleted": {
        "body": '    os.remove(os.path.join(DATA_ROOT, "records", "gone.json"))\n'
        "    assert not os.path.exists(os.path.join(DATA_ROOT, 'records', 'gone.json'))",
        "label": "删除 (deleted)",
        "needle": "records/gone.json",
    },
    "content_changed": {
        "body": '    path = os.path.join(DATA_ROOT, "profiles", "default", "bindings.json")\n'
        '    with open(path, "w", encoding="utf-8") as fh:\n'
        '        fh.write(\'{"v": 2}\\n\')\n'
        "    assert open(path, encoding='utf-8').read() == '{\"v\": 2}\\n'",
        "label": "内容变更 (content-changed)",
        "needle": "profiles/default/bindings.json",
    },
    "renamed": {
        # 目标目录一律用基线里就存在的（`profiles/default/`）：新建目录本身现在是一类
        # 破坏，借在这里就等于给这条用例塞进第二条差异，「只报改名」的形状就验不出来了。
        "body": '    src = os.path.join(DATA_ROOT, "records", "gone.json")\n'
        '    dst = os.path.join(DATA_ROOT, "profiles", "default", "moved.json")\n'
        "    os.replace(src, dst)\n"
        "    assert os.path.isfile(dst) and not os.path.exists(src)",
        "label": "改名 (renamed)",
        # 一整段「旧路径 -> 新路径」都要出现：只点名一边就等于没说清是谁动的手。
        "needle": "records/gone.json -> profiles/default/moved.json",
    },
    "dir_created": {
        # 与 B10.2 实测的形态一致：只读路径把 `cache/` 与 `cache/parse_tree/` 长出来，
        # 一个文件都不写。
        "body": '    os.makedirs(os.path.join(DATA_ROOT, "cache", "parse_tree"), exist_ok=True)\n'
        "    assert os.path.isdir(os.path.join(DATA_ROOT, 'cache', 'parse_tree'))",
        "label": "新建 (created)",
        "needle": "cache/parse_tree/",
    },
    "dir_removed": {
        "body": '    os.rmdir(os.path.join(DATA_ROOT, "archive"))\n'
        "    assert not os.path.exists(os.path.join(DATA_ROOT, 'archive'))",
        "label": "删除 (deleted)",
        "needle": "archive/",
    },
}

#: 数据根原本不存在、会话中途凭空长出一棵树（C1 的另一半：未执法≠放行）。
CREATE_FROM_NOTHING_BODY = (
    '    os.makedirs(os.path.join(DATA_ROOT, "profiles"), exist_ok=True)\n'
    '    with open(os.path.join(DATA_ROOT, "profiles", "zz_born.tmp"), "w", encoding="utf-8") as fh:\n'
    '        fh.write("born")'
)


def _make_tree(root: str, files: Dict[str, str]) -> None:
    """在 `root` 下按 `{相对路径: 内容}` 造一棵树，外加 `BASELINE_DIRS` 里的空目录。

    这是内部会话开始前的世界。空目录要单独建：文件父目录长出来的那几个都是**非空**的，
    「删掉一个目录」那一类破坏需要一个空的当靶子。
    """
    for rel, content in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    for name in BASELINE_DIRS:
        os.makedirs(os.path.join(root, name), exist_ok=True)


def _text(result) -> str:
    """pytest 7.4 的 `RunResult` 没有 `.str()`，stdout/stderr 是 LineMatcher。"""
    return result.stdout.str() + "\n" + result.stderr.str()


def _stats_line(result) -> str:
    """内部会话最后一行结果统计，例：`1 passed, 1 warning in 0.4s`。

    三态可区分的**判据**就在这一行：它是 CI、JUnit 消费者和卡片抄数字的人唯一会看
    的东西，所以断言只认这一行，不认上面的黄字。
    """
    lines = [line for line in _text(result).splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _result_region(out: str) -> str:
    """只看 pytest 报结果的区段：报错/失败节一直到结尾（含最后那行统计）。

    整段 stdout+stderr 上断言「ERROR 不出现」会被任何将来自动加载的插件顺手打的
    ERROR 字样判红，理由还跟守卫无关（复审 Minor）。守卫真红时这一节一定存在
    （`=== ERRORS ===` + `ERROR at teardown`）并且统计行带 error 计数，跑不掉。
    """
    match = _SUMMARY_REGION.search(out)
    if match is not None:
        return out[match.start() :]
    lines = [line for line in out.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _run_guard_session(
    pytester,
    monkeypatch,
    data_root: str,
    body: str = "    assert True",
    seed: bool = True,
    require: bool = False,
):
    """起一次独立进程的 pytest 会话：真实守卫接线 + `data_root` 作数据根。

    `seed=False` 时一个文件都不建（用来复现「干净检出没有 data/」）；`body` 是
    内部用例的函数体，默认不破坏任何东西，用作对照。

    `require` 显式决定内部会话有没有 `MCCHECKER_REQUIRE_H3`，**并且一律先覆盖外部环境
    变量**（`pytester.runpytest_subprocess` 是继承 `os.environ` 的）。不这么做的话，在这台
    权威开发机上带着 `MCCHECKER_REQUIRE_H3=1` 跑全量时，每个内部会话都会继承开关，
    「未执法必须是绿 + 警告」那两条对照当场变红——假红的守卫训练人把开关关掉，
    正是本卡要躲开的坑（复审裁定 C1 的同一口径）。
    """
    if require:
        monkeypatch.setenv(REQUIRE_ENV_VAR, "1")
    else:
        monkeypatch.delenv(REQUIRE_ENV_VAR, raising=False)
    if seed:
        _make_tree(data_root, BASELINE_FILES)
    pytester.makeconftest(INNER_CONFTEST.format(tests_dir=TESTS_DIR, data_root=data_root))
    name = "test_h3_inner"
    pytester.makefile(
        ".py", **{name: INNER_TEST.format(data_root=data_root, verb="mutation", body=body)}
    )
    return pytester.runpytest_subprocess("-q", name + ".py")


# ---------------------------------------------------------------------------
# 每一类差异：各自都要「退出码 1 + teardown ERROR + 点名相对路径」
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", sorted(CATEGORIES))
def test_each_damage_category_fails_the_session(pytester, tmp_path, monkeypatch, category) -> None:
    """执法路径的每一类各证一遍：不是只证 `created`，也不是只证纯函数会分类。"""
    spec = CATEGORIES[category]
    result = _run_guard_session(pytester, monkeypatch, str(tmp_path / "h3_root"), body=spec["body"])
    out = _text(result)

    assert result.ret == 1, out
    assert "ERROR at teardown" in out, out
    assert spec["label"] in out, out
    assert spec["needle"] in out, out
    # 红之前得先确认它真的在执法，否则「红」可能来自别处的报错。
    assert ENFORCING_MARK in out, out


def test_untouched_temp_root_is_green_and_announces_enforcing(pytester, tmp_path, monkeypatch) -> None:
    """对照：干净临时树绿，且公告「执法中」——绿要有执法者背书，不是没人说话。"""
    result = _run_guard_session(pytester, monkeypatch, str(tmp_path / "h3_root"))
    out = _text(result)

    assert result.ret == 0, out
    assert "1 passed" in out, out
    # 守卫红一定长成「结果区里有 ERROR」；只在这段里查，别被无关插件的 ERROR 字样冤枉
    # （复审 Minor）。teardown 那条标记是全输出级的，守卫的假绿在这里跑不掉。
    assert "ERROR" not in _result_region(out), _result_region(out)
    assert "ERROR at teardown" not in out, out
    assert ENFORCING_MARK in out, out
    assert NOT_ENFORCING_MARK not in out, out
    # 文件数与目录数分开报（B10.2）：种子树是 2 个文件、4 个目录
    # （`profiles/`、`profiles/default/`、`records/` 由文件父目录长出来，`archive/` 是空靶子）。
    assert "基线快照 {} 个文件".format(len(BASELINE_FILES)) in out, out
    assert "基线快照 2 个文件、4 个目录" in out, out


def test_non_enforcement_changes_the_summary_line_not_only_the_prose(
    pytester, tmp_path, monkeypatch
) -> None:
    """I1：未执法不许只有文案。默认（不设开关）依然是绿——干净检出与 CI 不许因此变红
    （那是本守卫当初那条 Critical）——但统计行必须自己承认「1 warning」，
    并且警告类别点名 `H3NonEnforcementWarning`，机器按名就能 grep。
    """
    absent_root = str(tmp_path / "data_absent")
    assert not os.path.exists(absent_root)

    result = _run_guard_session(pytester, monkeypatch, absent_root, seed=False)
    out = _text(result)

    assert result.ret == 0, out
    assert NOT_ENFORCING_MARK in out, out
    assert NON_ENFORCEMENT_WARNING in out, out
    assert "warning" in _stats_line(result), _stats_line(result)
    assert "error" not in _stats_line(result) and "failed" not in _stats_line(result)
    assert REQUIRE_FAIL_MARK not in out, out
    assert ENFORCING_MARK not in out, out


def test_require_h3_env_var_turns_non_enforcement_into_a_failure(pytester, tmp_path, monkeypatch) -> None:
    """I1 的另一半：`MCCHECKER_REQUIRE_H3=1` 时「没在执法」必须红，而且要能被点名。

    红走的是已证明的执法路径（session 级夹具 teardown），所以形状和四类破坏一致：
    非 0 退出 + teardown ERROR + 文案里带开关名，读者不用猜是谁判的红。
    开关不在这里裸 `setenv`，而是交给 `_run_guard_session(require=...)` 统一覆盖外部
    环境（理由见它的 docstring）。
    """
    absent_root = str(tmp_path / "data_absent")

    result = _run_guard_session(pytester, monkeypatch, absent_root, seed=False, require=True)
    out = _text(result)

    assert result.ret != 0, out
    assert result.ret == 1, out
    assert REQUIRE_FAIL_MARK in out, out
    assert REQUIRE_ENV_VAR in out, out
    assert "ERROR at teardown" in out, out
    # 文案与警告两条通道都还在（红不是把它们换掉，而是叠上去）。
    assert NOT_ENFORCING_MARK in out, out
    assert NON_ENFORCEMENT_WARNING in out, out


def test_enforcing_session_with_the_switch_on_stays_green(pytester, tmp_path, monkeypatch) -> None:
    """开关只治「没执法」，不许把执法的绿一起判红：否则第二天就有人把它关掉。"""
    result = _run_guard_session(pytester, monkeypatch, str(tmp_path / "h3_root"), require=True)
    out = _text(result)

    assert result.ret == 0, out
    assert ENFORCING_MARK in out, out
    assert REQUIRE_FAIL_MARK not in out, out
    assert "warning" not in _stats_line(result), _stats_line(result)


def test_the_three_states_are_distinguishable_from_the_summary_line(pytester, tmp_path, monkeypatch) -> None:
    """本卡的落点：三个状态在**输出**上两两不同，不是同一句「1 passed」加一行黄字。

    三态各起一次独立进程，只比统计行与标记词——抄数字的人只会抄统计行，
    那一行不同才算真的分开：
    - 未执法 + 默认：`1 passed, 1 warning`（ret 0）
    - 未执法 + 硬要求：`1 passed, 1 error`（ret 1）
    - 执法中且干净：`1 passed`（ret 0，既无警告也无失败）
    """
    plain = _run_guard_session(pytester, monkeypatch, str(tmp_path / "absent_plain"), seed=False)
    required = _run_guard_session(
        pytester, monkeypatch, str(tmp_path / "absent_required"), seed=False, require=True
    )
    enforcing = _run_guard_session(pytester, monkeypatch, str(tmp_path / "seeded_root"))

    stats = [_stats_line(plain), _stats_line(required), _stats_line(enforcing)]
    assert len(set(stats)) == 3, stats
    assert "passed" in stats[0] and "warning" in stats[0] and "error" not in stats[0], stats
    assert "error" in stats[1], stats
    assert "warning" not in stats[2] and "error" not in stats[2], stats
    assert (plain.ret, required.ret, enforcing.ret) == (0, 1, 0), stats

    plain_out, required_out, enforcing_out = _text(plain), _text(required), _text(enforcing)
    assert ENFORCING_MARK not in plain_out and ENFORCING_MARK not in required_out, stats
    assert ENFORCING_MARK in enforcing_out, enforcing_out
    assert NOT_ENFORCING_MARK in plain_out and NOT_ENFORCING_MARK in required_out, stats
    assert NOT_ENFORCING_MARK not in enforcing_out, enforcing_out
    assert REQUIRE_FAIL_MARK not in plain_out and REQUIRE_FAIL_MARK not in enforcing_out, stats
    assert REQUIRE_FAIL_MARK in required_out, required_out
    # 执法那一态必须当场报出自己的价格（字节 + 秒），否则「代价可见」只是文档里的话。
    assert "字节" in enforcing_out and "秒" in enforcing_out, enforcing_out
    assert NON_ENFORCEMENT_WARNING not in enforcing_out, enforcing_out


def test_missing_data_root_is_green_but_announces_non_enforcement(
    pytester, tmp_path, monkeypatch
) -> None:
    """C1：数据根不存在（干净检出、CI、别人的 clone）时不许冒充「已保护」。

    断言的是**两个分支的观测结果不同**：这一轮的绿必须带「未执法」公告，
    且不得出现「执法中」——上面那条的绿才是另一回事。
    """
    absent_root = str(tmp_path / "data_absent")
    assert not os.path.exists(absent_root)

    result = _run_guard_session(pytester, monkeypatch, absent_root, seed=False)
    out = _text(result)

    assert result.ret == 0, out
    assert "1 passed" in out, out
    assert NOT_ENFORCING_MARK in out, out
    assert "没有保护任何真实数据" in out, out
    assert ENFORCING_MARK not in out, out


def test_root_appearing_from_nothing_still_fails(pytester, tmp_path, monkeypatch) -> None:
    """未执法**不等于放行**：基线为空（根不存在）时长出来的文件与目录照样点名判红。"""
    absent_root = str(tmp_path / "data_absent")

    result = _run_guard_session(
        pytester, monkeypatch, absent_root, body=CREATE_FROM_NOTHING_BODY, seed=False
    )
    out = _text(result)

    assert result.ret == 1, out
    assert "ERROR at teardown" in out, out
    assert CATEGORIES["created"]["label"] in out, out
    assert "profiles/zz_born.tmp" in out, out
    # 长出来的**目录**也要单独被点名：条目排序是 `profiles/` 在 `profiles/zz_born.tmp`
    # 之前，所以这一句同时钉住「目录条目带结尾 `/`」和「它没被文件那条淹没」。
    assert "profiles/, profiles/zz_born.tmp" in out, out
