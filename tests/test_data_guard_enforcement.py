"""H3 守卫**执法路径**的永久证明（INH-1914 复审 I3）。

上一版的「接线已生效」只有一次性手工实验（往真实 `data/` 写一个探针再删），
仓库里没有任何东西保证它一直成立：把 `h3_data_guard` 改成 `print`，交付的
纯函数用例仍然全绿。这里改用 pytest 自带的 `pytester` 夹具 +
`runpytest_subprocess`，在**独立进程**里跑一次真正的 pytest 会话，四类差异
（新建 / 删除 / 内容变更 / 改名）各自必须拿到「退出码 1 + 会话末 teardown ERROR +
相对路径点名」，另加两条口径：干净树必须绿且公告「执法中」，数据根缺失必须绿但
公告**未执法**（C1：「没数据可保护」和「保护完且干净」不许长成同一个观测结果）。

硬约束：**全程不碰仓库真实的 `data/`**。内部会话的数据根是 `tmp_path` 下临时造的树，
真实 conftest 的 `REAL_DATA_DIR` 只在**子进程**里被改指到那棵树，主进程一行不动。

需要 `pytest.ini` 的 `addopts = -p pytester` 才能拿到 `pytester` 夹具；那行被删掉时
这里不会静默跳过，而是整组用例一起报 `fixture 'pytester' not found`。
"""

from __future__ import annotations

import os
from typing import Dict

import pytest

#: 本文件所在目录——内部会话靠它 import 仓库**真实**的 `tests/conftest.py`。
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

#: 内部会话的基线树：相对路径 -> 内容。刻意用带层级的路径，点名时必须原样出现。
BASELINE_FILES: Dict[str, str] = {
    "profiles/default/bindings.json": '{"v": 1}\n',
    "records/gone.json": '{"gone": true}\n',
}

#: 公告的判据子串（正文在 `data_guard.py`，这里只认前缀，别把文案钉死两遍）。
NOT_ENFORCING_MARK = "H3 守卫未执法："
ENFORCING_MARK = "H3 守卫执法中："

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

#: 四类差异各自的破坏动作与判据：报告里的中文标签、必须被点名的相对路径。
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
        "body": '    src = os.path.join(DATA_ROOT, "records", "gone.json")\n'
        '    dst = os.path.join(DATA_ROOT, "archive", "moved.json")\n'
        "    os.makedirs(os.path.dirname(dst), exist_ok=True)  # 目录不进快照，只留改名这一条差异\n"
        "    os.replace(src, dst)\n"
        "    assert os.path.isfile(dst) and not os.path.exists(src)",
        "label": "改名 (renamed)",
        # 一整段「旧路径 -> 新路径」都要出现：只点名一边就等于没说清是谁动的手。
        "needle": "records/gone.json -> archive/moved.json",
    },
}

#: 数据根原本不存在、会话中途凭空长出一棵树（C1 的另一半：未执法≠放行）。
CREATE_FROM_NOTHING_BODY = (
    '    os.makedirs(os.path.join(DATA_ROOT, "profiles"), exist_ok=True)\n'
    '    with open(os.path.join(DATA_ROOT, "profiles", "zz_born.tmp"), "w", encoding="utf-8") as fh:\n'
    '        fh.write("born")'
)


def _make_tree(root: str, files: Dict[str, str]) -> None:
    """在 `root` 下按 `{相对路径: 内容}` 造一棵树（内部会话开始前的世界）。"""
    for rel, content in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)


def _text(result) -> str:
    """pytest 7.4 的 `RunResult` 没有 `.str()`，stdout/stderr 是 LineMatcher。"""
    return result.stdout.str() + "\n" + result.stderr.str()


def _run_guard_session(pytester, data_root: str, body: str = "    assert True", seed: bool = True):
    """起一次独立进程的 pytest 会话：真实守卫接线 + `data_root` 作数据根。

    `seed=False` 时一个文件都不建（用来复现「干净检出没有 data/」）；`body` 是
    内部用例的函数体，默认不破坏任何东西，用作对照。
    """
    if seed:
        _make_tree(data_root, BASELINE_FILES)
    pytester.makeconftest(INNER_CONFTEST.format(tests_dir=TESTS_DIR, data_root=data_root))
    name = "test_h3_inner"
    pytester.makefile(
        ".py", **{name: INNER_TEST.format(data_root=data_root, verb="mutation", body=body)}
    )
    return pytester.runpytest_subprocess("-q", name + ".py")


# ---------------------------------------------------------------------------
# 四类差异：各自都要「退出码 1 + teardown ERROR + 点名相对路径」
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", sorted(CATEGORIES))
def test_each_damage_category_fails_the_session(pytester, tmp_path, category) -> None:
    """执法路径的四类各证一遍：不是只证 `created`，也不是只证纯函数会分类。"""
    spec = CATEGORIES[category]
    result = _run_guard_session(pytester, str(tmp_path / "h3_root"), body=spec["body"])
    out = _text(result)

    assert result.ret == 1, out
    assert "ERROR at teardown" in out, out
    assert spec["label"] in out, out
    assert spec["needle"] in out, out
    # 红之前得先确认它真的在执法，否则「红」可能来自别处的报错。
    assert ENFORCING_MARK in out, out


def test_untouched_temp_root_is_green_and_announces_enforcing(pytester, tmp_path) -> None:
    """对照：干净临时树绿，且公告「执法中」——绿要有执法者背书，不是没人说话。"""
    result = _run_guard_session(pytester, str(tmp_path / "h3_root"))
    out = _text(result)

    assert result.ret == 0, out
    assert "1 passed" in out, out
    assert "ERROR" not in out, out
    assert ENFORCING_MARK in out, out
    assert NOT_ENFORCING_MARK not in out, out
    assert "基线快照 {} 个文件".format(len(BASELINE_FILES)) in out, out


def test_missing_data_root_is_green_but_announces_non_enforcement(pytester, tmp_path) -> None:
    """C1：数据根不存在（干净检出、CI、别人的 clone）时不许冒充「已保护」。

    断言的是**两个分支的观测结果不同**：这一轮的绿必须带「未执法」公告，
    且不得出现「执法中」——上面那条的绿才是另一回事。
    """
    absent_root = str(tmp_path / "data_absent")
    assert not os.path.exists(absent_root)

    result = _run_guard_session(pytester, absent_root, seed=False)
    out = _text(result)

    assert result.ret == 0, out
    assert "1 passed" in out, out
    assert NOT_ENFORCING_MARK in out, out
    assert "没有保护任何真实数据" in out, out
    assert ENFORCING_MARK not in out, out


def test_root_appearing_from_nothing_still_fails(pytester, tmp_path) -> None:
    """未执法**不等于放行**：基线为空（根不存在）时长出来的文件照样点名判红。"""
    absent_root = str(tmp_path / "data_absent")

    result = _run_guard_session(pytester, absent_root, body=CREATE_FROM_NOTHING_BODY, seed=False)
    out = _text(result)

    assert result.ret == 1, out
    assert "ERROR at teardown" in out, out
    assert CATEGORIES["created"]["label"] in out, out
    assert "profiles/zz_born.tmp" in out, out
