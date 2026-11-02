"""M16-40 场景清单的纯逻辑（不启动浏览器）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "e2e_scenarios.py"


def load_module():
    spec = importlib.util.spec_from_file_location("e2e_scenarios_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


SCEN = load_module()


def test_route_path_shapes():
    assert SCEN.route_path("overview") == "#/p/default/overview"
    assert SCEN.route_path("updates", "sources", "workspace=w1") == "#/p/default/updates/sources?workspace=w1"
    assert SCEN.route_path("f", "A.xml", "view=tree", pid="p1") == "#/p/p1/f/A.xml?view=tree"


def test_updates_hash_includes_workspace():
    assert SCEN.updates_hash("plan", "w-abc") == "#/p/default/updates/plan?workspace=w-abc"


def test_legacy_hash_appends_workspace_to_query():
    item = {"path": "f/A.xml?view=tree"}
    assert SCEN.legacy_hash(item, "w1") == "#/p/default/f/A.xml?view=tree&workspace=w1"
    bare = {"path": "review"}
    assert SCEN.legacy_hash(bare, "w1") == "#/p/default/review?workspace=w1"


def test_legacy_table_covers_f01_to_f28_once():
    ids = [row["f"] for row in SCEN.LEGACY_ROUTES]
    assert ids == ["F%02d" % n for n in range(1, 29)]
    assert len(set(ids)) == 28


def test_every_legacy_row_has_expectation():
    for row in SCEN.LEGACY_ROUTES:
        assert row.get("expect"), row
        assert row.get("path"), row


def test_update_scenarios_have_steps_and_unique_ids():
    ids = [row["id"] for row in SCEN.UPDATE_SCENARIOS]
    assert len(ids) == len(set(ids))
    for row in SCEN.UPDATE_SCENARIOS:
        assert row["steps"], row
        assert row["tab"] in ("sources", "plan", "connections", "runs", "candidates")


def test_summarize_separates_levels():
    results = [
        {"level": "interactive", "ok": True},
        {"level": "interactive", "ok": False},
        {"level": "reachable", "ok": True},
        {"level": "reachable", "ok": True},
    ]
    summary = SCEN.summarize(results)
    assert summary == {
        "interactive_pass": 1,
        "interactive_fail": 1,
        "reachable_pass": 2,
        "reachable_fail": 0,
    }


def test_viewports_cover_required_sizes():
    sizes = {(row["width"], row["height"]) for row in SCEN.VIEWPORTS}
    assert (1366, 768) in sizes
    assert (1920, 1080) in sizes
    assert (375, 812) in sizes
    assert (768, 1024) in sizes
