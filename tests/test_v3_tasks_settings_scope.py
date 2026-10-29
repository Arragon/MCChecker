"""M10-05 任务/调度/工具/设置按工作区隔离（INH-2128）。"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from authz_fixtures import as_role, make_server, seed_roles

from app.core import storage
from app.core.errors import BUSY, AppError
from app.services import scheduler, tasks, workspace_catalog


def _ctx(wid, pid="default"):
    from app.services.context import RequestContext

    return RequestContext(ip="", cid=None, profile_id=pid, workspace_id=wid, actor=None)


def _write_schedule(wid, cfg):
    with storage.use_workspace(wid), storage.use_profile("default"):
        storage.save_schedule(cfg)


class _FakeTask:
    def __init__(self, task_id):
        self.id = task_id
        self.actor = "system"


class _FakeRunner:
    def __init__(self):
        self.calls = []

    def submit(self, ctx, ttype, target, fn):
        self.calls.append((ctx.workspace_id, ctx.profile_id, ttype, target))
        return _FakeTask("t-%d" % len(self.calls))


def test_task_key_and_active_list_are_workspace_scoped():
    runner = tasks.TaskRunner(max_workers=2)
    gate = threading.Event()

    def hold(task):
        gate.wait(5)

    try:
        first = runner.submit(_ctx("w1"), "update_all", None, hold)
        second = runner.submit(_ctx("w2"), "update_all", None, hold)
        assert first.workspace == "w1" and second.workspace == "w2"
        assert [t.id for t in runner.list("default", True, "w1")] == [first.id]
        assert [t.id for t in runner.list("default", True, "w2")] == [second.id]
        try:
            runner.submit(_ctx("w1"), "update_all", None, hold)
        except AppError as exc:
            assert exc.code == BUSY
        else:  # pragma: no cover
            raise AssertionError("同一 W/P 的同类任务必须 409 BUSY")
    finally:
        gate.set()
        runner.shutdown(timeout=5)


def test_scheduler_fires_each_workspace_with_its_own_context():
    w1 = workspace_catalog.create_workspace("Sched W1")["id"]
    w2 = workspace_catalog.create_workspace("Sched W2")["id"]
    base = datetime(2026, 1, 1, 12, 0, 0)
    expired = (base - timedelta(hours=25)).strftime(scheduler.TIME_FORMAT)
    for wid in (w1, w2):
        _write_schedule(
            wid,
            {"enabled": True, "interval_hours": 24, "last_run": {"at": expired}},
        )

    now = [base]
    runner = _FakeRunner()
    sched = scheduler.Scheduler(runner=runner, now_fn=lambda: now[0])
    sched.reload()
    assert sched.next_run_at("default", w1) == base + timedelta(minutes=2)
    assert sched.next_run_at("default", w2) == base + timedelta(minutes=2)
    assert sched.next_run_at("default") is None, "legacy 没有 schedule，不该被新 W 牵连"

    storage.set_active_workspace(w1)
    view1 = scheduler.schedule_view("default")
    storage.set_active_workspace(w2)
    view2 = scheduler.schedule_view("default")
    storage.set_active_workspace("")
    assert view1["enabled"] and view2["enabled"]
    assert view1["interval_hours"] == view2["interval_hours"] == 24

    now[0] = base + timedelta(minutes=3)
    fired = sched.tick()
    assert fired == ["default", "default"]
    assert sorted(runner.calls) == sorted(
        [(w1, "default", "update_all", None), (w2, "default", "update_all", None)]
    )


def test_revision_counters_are_workspace_scoped():
    from app.services import revision

    revision.reset()
    try:
        with storage.use_workspace("w1"):
            revision.bump("default", "files")
            revision.bump("default", "files")
        with storage.use_workspace("w2"):
            assert revision.snapshot("default")["parts"]["files"] == 1, (
                "W1 的写不得让 W2 的轮询看到变化"
            )
            revision.bump("default", "files")
        with storage.use_workspace("w1"):
            assert revision.snapshot("default")["parts"]["files"] == 3
    finally:
        revision.reset()


def test_tools_schedule_and_admins_scope():
    seed_roles("m10settings")
    with make_server() as server:
        admin = as_role(server, "admin")
        w1 = admin.json("POST", "/api/v3/workspaces", {"name": "W1"})[2]["workspace"]["id"]
        w2 = admin.json("POST", "/api/v3/workspaces", {"name": "W2"})[2]["workspace"]["id"]
        deployer = as_role(server, "deployer")

        status, _h, body = deployer.json(
            "PUT", "/api/v1/p/default/tools/foo",
            {"name": "foo", "description": "W1 工具", "url": "http://example.com"},
            headers={"X-Workspace-Id": w1},
        )
        assert status == 200, body
        tools1 = deployer.json("GET", "/api/v1/p/default/tools", headers={"X-Workspace-Id": w1})[2]
        tools2 = deployer.json("GET", "/api/v1/p/default/tools", headers={"X-Workspace-Id": w2})[2]
        assert [t["name"] for t in tools1["items"]] == ["foo"]
        assert tools2["items"] == []

        status, _h, body = deployer.json(
            "POST", "/api/v1/p/default/sources",
            {"name": "same.xml", "url": "http://example.invalid/w1.xml"},
            headers={"X-Workspace-Id": w1},
        )
        assert status == 201, body
        status, _h, body = deployer.json(
            "POST", "/api/v1/p/default/sources",
            {"name": "same.xml", "url": "http://example.invalid/w2.xml"},
            headers={"X-Workspace-Id": w2},
        )
        assert status == 201, body
        src1 = deployer.json("GET", "/api/v1/p/default/sources", headers={"X-Workspace-Id": w1})[2]
        src2 = deployer.json("GET", "/api/v1/p/default/sources", headers={"X-Workspace-Id": w2})[2]
        assert src1["items"][0]["url"] == "http://example.invalid/w1.xml"
        assert src2["items"][0]["url"] == "http://example.invalid/w2.xml"

        status, _h, body = deployer.json(
            "PUT", "/api/v1/p/default/schedule",
            {"enabled": True, "interval_hours": 5},
            headers={"X-Workspace-Id": w1},
        )
        assert status == 200, body
        s1 = deployer.json("GET", "/api/v1/p/default/schedule", headers={"X-Workspace-Id": w1})[2]
        s2 = deployer.json("GET", "/api/v1/p/default/schedule", headers={"X-Workspace-Id": w2})[2]
        assert s1["enabled"] is True and s1["interval_hours"] == 5
        assert s2["enabled"] is False and s2["interval_hours"] == 24

        status, _h, body = deployer.json(
            "POST", "/api/v1/p/default/admins", {"name": "全局管理员甲"},
            headers={"X-Workspace-Id": w1},
        )
        assert status == 201, body
        a1 = deployer.json("GET", "/api/v1/p/default/admins", headers={"X-Workspace-Id": w1})[2]
        a2 = deployer.json("GET", "/api/v1/p/default/admins", headers={"X-Workspace-Id": w2})[2]
        assert a1 == a2 and any(row["name"] == "全局管理员甲" for row in a2["items"])
