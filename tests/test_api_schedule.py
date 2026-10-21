"""`/schedule` 读写接口（卡 8 · M3-08 / INH-1893；规范 §7.9、§10.4、§16.2 UC-33）。

四件必须钉死的事：

1. **权限**：`PUT` 只给部署者（`settings_write`），四角色各打一遍；403 时文件字节不变、
   修订号不涨（§16.3 的口径）。
2. **非法间隔 = 400 且原文件一字节都不动**（0、2161、`"abc"`、`1.5`），且**不递增**。
   「先校验后落笔」由 `services/scheduler.save_schedule()` 保证，本文件从 HTTP 面钉住它。
3. **修订号**（R39 / 全局约束）：成功写补一次 `settings`；读永远不补；失败写不补。
4. **读-改-写整段扣在同一把机型锁里**（§5.4 / 卡 5 复审 I1）：`TestNoLostUpdate` 用
   「竞争方必须是另一个线程」的既定写法证明——同线程重入同一把 `RLock` 是抓不到丢失更新的。
5. **PUT 是整份替换**：载荷里缺 `enabled` 就是写 `False`（把定时关掉），不是「保留原值」。
   本卡不改这条契约，只钉住它并记进 `docs/know-how.md`，免得 M3-09 的设置页踩上去。

`next_run_at` 由调度器算、绝不持久化，所以「有没有在跑的调度器」会直接改变响应：
没有就是 `None`（那个进程里没人会去执行它，凭空报一个时间是撒谎）。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest

from app.services import scheduler as scheduler_module
from authz_fixtures import as_role, make_server, seed_roles

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

#: §7.9 规定的载荷字段，一个不多一个不少。
VIEW_FIELDS = {"enabled", "interval_hours", "last_run", "next_run_at"}


# ---------------------------------------------------------------------------
# 帮助函数
# ---------------------------------------------------------------------------


def _path(pid: str) -> str:
    from app.core import storage

    return storage.get_schedule_file(pid)


def _raw(pid: str) -> bytes:
    path = _path(pid)
    if not os.path.exists(path):
        return b""
    with open(path, "rb") as fh:
        return fh.read()


def _sha(pid: str) -> str:
    return hashlib.sha256(_raw(pid)).hexdigest()


def _seed(pid: str, payload: Dict[str, Any]) -> None:
    path = _path(pid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)


def _parts(pid: str) -> Dict[str, int]:
    from app.services import revision

    return revision.snapshot(pid)["parts"]


class Clock:
    """假钟：GET/PUT 的 `next_run_at` 与播种基准都由它给出。"""

    def __init__(self, start: Optional[datetime] = None) -> None:
        self._now = start or datetime(2026, 10, 18, 12, 0, 0)

    def now(self) -> datetime:
        return self._now

    def at(self, **kwargs) -> datetime:
        return self._now + timedelta(**kwargs)

    def text(self, **kwargs) -> str:
        return self.at(**kwargs).strftime(TIME_FORMAT)


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def target_pid() -> str:
    from app.core import device_models

    return device_models.add_model("定时机型")


@pytest.fixture()
def running_scheduler(target_pid, clock):
    """一个**在跑的**调度器单例：只有它存在时 `next_run_at` 才有值。"""
    from app.services import tasks

    sched = scheduler_module.init_scheduler(tasks.get_runner(), now_fn=clock.now)
    yield sched
    scheduler_module.reset_scheduler()


# ---------------------------------------------------------------------------
# 1. GET
# ---------------------------------------------------------------------------


class TestGetSchedule:
    def test_shape_matches_spec_7_9(self, target_pid, running_scheduler, clock):
        _seed(target_pid, {"enabled": True, "interval_hours": 12, "last_run": None})
        running_scheduler.reload()
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "GET", "/api/v1/p/%s/schedule" % target_pid
            )
        assert status == 200
        assert set(body) == VIEW_FIELDS, "§7.9 的四个字段一个不能多、一个不能少"
        assert body["enabled"] is True
        assert body["interval_hours"] == 12
        assert body["last_run"] is None
        assert body["next_run_at"] == clock.text(hours=12)

    def test_default_file_without_last_run_reads_as_none(self, target_pid, running_scheduler):
        # 机型刚建好：根本没有 schedule.json，GET 也要给出一份完整形状而不是 404
        with make_server() as server:
            status, _, body = as_role(server, "visitor").json(
                "GET", "/api/v1/p/%s/schedule" % target_pid
            )
        assert status == 200
        assert body["enabled"] is False
        assert body["interval_hours"] == 24
        assert body["last_run"] is None
        assert body["next_run_at"] is None, "未启用就没有排期"

    def test_last_run_is_passed_through_verbatim(self, target_pid, running_scheduler, clock):
        last = {"at": clock.text(), "success": 3, "unchanged": 1, "failed": 2, "skipped": 0}
        _seed(target_pid, {"enabled": False, "interval_hours": 24, "last_run": last})
        running_scheduler.reload()
        with make_server() as server:
            _, _, body = as_role(server, "member").json("GET", "/api/v1/p/%s/schedule" % target_pid)
        assert body["last_run"] == last

    def test_next_run_at_is_none_when_no_scheduler_is_running(self, target_pid, clock):
        """没有调度器就没有排期——`reset_scheduler()` 之后单例是 None，读路径不许编一个时间。"""
        assert scheduler_module.get_scheduler() is None
        _seed(target_pid, {"enabled": True, "interval_hours": 1})
        with make_server() as server:
            _, _, body = as_role(server, "deployer").json("GET", "/api/v1/p/%s/schedule" % target_pid)
        assert body["next_run_at"] is None

    def test_get_never_bumps_revision(self, target_pid, running_scheduler, clock):
        _seed(target_pid, {"enabled": True, "interval_hours": 6, "last_run": None})
        running_scheduler.reload()
        before = _parts(target_pid)
        with make_server() as server:
            for _ in range(3):
                as_role(server, "deployer").json("GET", "/api/v1/p/%s/schedule" % target_pid)
        assert _parts(target_pid) == before, "读接口不许递增修订号（全局约束）"

    def test_next_run_is_not_persisted_by_a_read(self, target_pid, running_scheduler, clock):
        _seed(target_pid, {"enabled": True, "interval_hours": 6, "last_run": None})
        running_scheduler.reload()
        before = _sha(target_pid)
        with make_server() as server:
            as_role(server, "deployer").json("GET", "/api/v1/p/%s/schedule" % target_pid)
        assert _sha(target_pid) == before, "GET 一个字节都不许写"


# ---------------------------------------------------------------------------
# 2. PUT：UC-33
# ---------------------------------------------------------------------------


class TestPutSchedule:
    def test_two_days_saves_as_48_and_reports_the_right_next_run(self, target_pid, running_scheduler, clock):
        # UC-33：设为 2 天 ⇒ interval_hours=48、下次执行时间正确
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "PUT", "/api/v1/p/%s/schedule" % target_pid, body={"enabled": True, "interval_hours": 48}
            )
        assert status == 200
        assert body["interval_hours"] == 48
        assert body["enabled"] is True
        assert body["next_run_at"] == clock.text(hours=48)
        assert json.loads(_raw(target_pid).decode("utf-8"))["interval_hours"] == 48

    def test_seeding_follows_the_last_run_when_the_interval_still_covers_it(self, target_pid, running_scheduler, clock):
        _seed(target_pid, {"enabled": False, "interval_hours": 24, "last_run": {"at": clock.text(hours=-6)}})
        with make_server() as server:
            _, _, body = as_role(server, "deployer").json(
                "PUT", "/api/v1/p/%s/schedule" % target_pid, body={"enabled": True, "interval_hours": 24}
            )
        assert body["next_run_at"] == clock.text(hours=18), "上次 6 小时前 + 间隔 24 小时"

    def test_a_stale_last_run_defers_two_minutes_instead_of_firing_now(self, target_pid, running_scheduler, clock):
        _seed(target_pid, {"enabled": True, "interval_hours": 24, "last_run": {"at": clock.text(days=-30)}})
        with make_server() as server:
            _, _, body = as_role(server, "deployer").json(
                "PUT", "/api/v1/p/%s/schedule" % target_pid, body={"enabled": True, "interval_hours": 48}
            )
        assert body["next_run_at"] == clock.text(minutes=2)

    def test_reload_happens_without_restarting_the_process(self, target_pid, running_scheduler, clock):
        _seed(target_pid, {"enabled": True, "interval_hours": 1, "last_run": None})
        running_scheduler.reload()
        assert running_scheduler.next_run_at(target_pid) == clock.at(hours=1)
        with make_server() as server:
            as_role(server, "deployer").json(
                "PUT", "/api/v1/p/%s/schedule" % target_pid, body={"enabled": True, "interval_hours": 48}
            )
        assert running_scheduler.next_run_at(target_pid) == clock.at(hours=48), "PUT 之后现网排期立刻换"
        assert running_scheduler.is_alive(), "PUT 之后调度线程还活着（reload 不是重启）"

    def test_disable_keeps_the_interval_and_clears_the_plan(self, target_pid, running_scheduler, clock):
        _seed(target_pid, {"enabled": True, "interval_hours": 12, "last_run": None})
        running_scheduler.reload()
        with make_server() as server:
            _, _, body = as_role(server, "deployer").json(
                "PUT", "/api/v1/p/%s/schedule" % target_pid, body={"enabled": False, "interval_hours": 12}
            )
        assert body == {"enabled": False, "interval_hours": 12, "last_run": None, "next_run_at": None}

    def test_a_put_that_omits_enabled_disables_it_replace_not_patch(self, target_pid, running_scheduler, clock):
        """PUT 是**整份替换**，不是补丁：载荷里缺 `enabled` ⇒ 写入 `False`，定时被关掉。

        本条只是**钉住现有契约**（`save_schedule` 的 `payload.get("enabled", False)`，mock 的
        `!!req.body.enabled` 同口径），不是新的产品决定。M3-09 与设置页每次 PUT 都必须把当前
        `enabled` 一起带上，否则「只改间隔」会把定时悄悄关掉——所以这里连磁盘、回显和排期三处
        一起断，任何一处变成「保留原值」都会红（并已记进 `docs/know-how.md`）。
        """
        _seed(target_pid, {"enabled": True, "interval_hours": 12, "last_run": None})
        running_scheduler.reload()
        assert running_scheduler.next_run_at(target_pid) == clock.at(hours=12), "先证明停之前是开着的"
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "PUT", "/api/v1/p/%s/schedule" % target_pid, body={"interval_hours": 48}
            )
        assert status == 200, "缺 enabled 不叫非法载荷，服务端不报错、只是按缺省写 False"
        assert body["enabled"] is False, "省略 enabled ⇒ False（整份替换语义）"
        assert body["interval_hours"] == 48, "另一个字段照常按载荷写入"
        saved = json.loads(_raw(target_pid).decode("utf-8"))
        assert saved["enabled"] is False and saved["interval_hours"] == 48
        assert running_scheduler.next_run_at(target_pid) is None, "关掉之后不再有排期"

    def test_success_bumps_settings_once_and_only_settings(self, target_pid, running_scheduler, clock):
        _seed(target_pid, {"enabled": True, "interval_hours": 1, "last_run": None})
        running_scheduler.reload()
        before = _parts(target_pid)
        with make_server() as server:
            as_role(server, "deployer").json(
                "PUT", "/api/v1/p/%s/schedule" % target_pid, body={"enabled": True, "interval_hours": 24}
            )
        after = _parts(target_pid)
        assert after["settings"] == before["settings"] + 1
        assert {k: v for k, v in after.items() if k != "settings"} == {
            k: v for k, v in before.items() if k != "settings"
        }, "PUT /schedule 只该动 settings"

    @pytest.mark.parametrize("bad", [0, -1, 2161, "abc", 1.5, 0.5, None, True, [], {}, "24"])
    def test_invalid_interval_is_400_with_unchanged_bytes(self, target_pid, running_scheduler, bad):
        original = {"enabled": True, "interval_hours": 12, "last_run": {"at": "2026-10-18 10:00:00"}}
        _seed(target_pid, original)
        running_scheduler.reload()
        before_raw = _raw(target_pid)
        before_sha = _sha(target_pid)
        before_parts = _parts(target_pid)
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "PUT", "/api/v1/p/%s/schedule" % target_pid, body={"enabled": False, "interval_hours": bad}
            )
        assert status == 400, "%r 必须 400，实际 %s" % (bad, status)
        assert _raw(target_pid) == before_raw, "非法间隔必须让 schedule.json 一字节都不动"
        assert _sha(target_pid) == before_sha
        assert _parts(target_pid) == before_parts, "失败写不递增修订号"
        assert body["error"]["code"] == "INVALID_INPUT"
        assert body["error"]["detail"]["field"] == "interval", "错误要落在间隔字段上（settings.js 按它显示）"

    @pytest.mark.parametrize("good", [1, 24, 48, 2160])
    def test_boundary_intervals_are_accepted(self, target_pid, running_scheduler, good):
        _seed(target_pid, {"enabled": False, "interval_hours": 24, "last_run": None})
        with make_server() as server:
            status, _, body = as_role(server, "deployer").json(
                "PUT", "/api/v1/p/%s/schedule" % target_pid, body={"enabled": True, "interval_hours": good}
            )
        assert status == 200, "%d 在合法区间内" % good
        assert body["interval_hours"] == good

    def test_missing_body_or_field_is_400(self, target_pid, running_scheduler):
        _seed(target_pid, {"enabled": False, "interval_hours": 24, "last_run": None})
        before = _raw(target_pid)
        with make_server() as server:
            caller = as_role(server, "deployer")
            empty = caller.json("PUT", "/api/v1/p/%s/schedule" % target_pid, body={})
            partial = caller.json("PUT", "/api/v1/p/%s/schedule" % target_pid, body={"enabled": True})
        assert empty[0] == 400 and partial[0] == 400
        assert _raw(target_pid) == before

    def test_non_json_body_is_400(self, target_pid, running_scheduler):
        _seed(target_pid, {"enabled": False, "interval_hours": 24, "last_run": None})
        before = _raw(target_pid)
        with make_server() as server:
            status, _, _ = as_role(server, "deployer").request(
                "PUT", "/api/v1/p/%s/schedule" % target_pid, body=b"{not json"
            )
        assert status == 400
        assert _raw(target_pid) == before

    def test_put_without_the_csrf_header_is_rejected(self, target_pid, running_scheduler):
        """CSRF 由 `http.py` 统一挡（§11.4），本卡不许在 handler 里重复实现或放宽。"""
        before = _raw(target_pid)
        with make_server() as server:
            status, _, _ = as_role(server, "deployer").request(
                "PUT",
                "/api/v1/p/%s/schedule" % target_pid,
                body={"enabled": True, "interval_hours": 48},
                with_csrf=False,
            )
        assert status in (400, 403)
        assert _raw(target_pid) == before


# ---------------------------------------------------------------------------
# 3. 四角色（§4.2 / §16.3）
# ---------------------------------------------------------------------------


class TestRoles:
    @pytest.mark.parametrize("role", ["visitor", "member"])
    def test_non_deployer_put_is_403_and_changes_nothing(self, target_pid, running_scheduler, role):
        _seed(target_pid, {"enabled": False, "interval_hours": 24, "last_run": None})
        before = _raw(target_pid)
        before_parts = _parts(target_pid)
        with make_server() as server:
            status, _, body = as_role(server, role).json(
                "PUT", "/api/v1/p/%s/schedule" % target_pid, body={"enabled": True, "interval_hours": 48}
            )
        assert status == 403, "%s 不许改定时设置" % role
        assert body["error"]["code"] == "FORBIDDEN"
        assert _raw(target_pid) == before, "403 也必须一字节不动"
        assert _parts(target_pid) == before_parts, "403 不递增"

    @pytest.mark.parametrize("role", ["visitor", "member", "admin", "deployer"])
    def test_every_role_can_read(self, target_pid, running_scheduler, role):
        _seed(target_pid, {"enabled": True, "interval_hours": 6, "last_run": None})
        running_scheduler.reload()
        with make_server() as server:
            status, _, body = as_role(server, role).json("GET", "/api/v1/p/%s/schedule" % target_pid)
        assert status == 200
        assert set(body) == VIEW_FIELDS

    def test_deployer_put_is_allowed(self, target_pid, running_scheduler):
        _seed(target_pid, {"enabled": False, "interval_hours": 24, "last_run": None})
        with make_server() as server:
            status, _, _ = as_role(server, "deployer").json(
                "PUT", "/api/v1/p/%s/schedule" % target_pid, body={"enabled": True, "interval_hours": 1}
            )
        assert status == 200


# ---------------------------------------------------------------------------
# 4. 读-改-写必须在同一把机型锁里（卡 5 复审 I1 的教训）
# ---------------------------------------------------------------------------


class TestNoLostUpdate:
    def test_concurrent_writer_inside_the_rmw_window_is_not_lost(self, target_pid, monkeypatch):
        """竞争方**必须是另一个线程**（`docs/know-how.md` 第 4 节）。

        同线程在 load 与 save 之间注入一次修改会重入同一把 per-pid `RLock`，锁内锁外都改得动，
        坏实现照样绿。这里的形状是：打桩 `storage.load_schedule`，读到的一瞬间拉起竞争线程
        并等 0.5 秒——坏实现（分两次取锁）里竞争方几毫秒就写完，主线程随后拿旧副本整份覆盖，
        用例确定性地红；好实现里锁还扣在主线程手上，那 0.5 秒必然等不到，主线程写完释放后
        竞争线程才轮到。最后**一定要 join 竞争线程**再读文件，否则断言读的是不确定的中间态。
        """
        from app.core import storage

        _seed(target_pid, {"enabled": False, "interval_hours": 24, "last_run": None})
        real_load = storage.load_schedule
        racer: List[threading.Thread] = []

        def other() -> None:
            with storage.profile_write(target_pid):
                with storage.use_profile(target_pid):
                    fresh = real_load()
                    fresh["last_run"] = {"at": "2026-10-18 11:00:00", "success": 1}
                    storage.save_schedule(fresh)

        def load_then_race():
            cfg = real_load()
            if not racer:
                thread = threading.Thread(target=other, name="racer")
                thread.start()
                racer.append(thread)
                thread.join(0.5)  # 坏实现里这里就返回了；好实现里必然超时
            return cfg

        monkeypatch.setattr(storage, "load_schedule", load_then_race)
        try:
            view = scheduler_module.save_schedule(target_pid, {"enabled": True, "interval_hours": 48})
        finally:
            for thread in racer:
                thread.join(10)
                assert not thread.is_alive(), "竞争线程还扣在锁上：机型锁没释放"

        assert view["interval_hours"] == 48
        saved = json.loads(_raw(target_pid).decode("utf-8"))
        assert saved["enabled"] is True, "PUT 自己的字段不能丢"
        assert saved["interval_hours"] == 48
        assert saved["last_run"] == {"at": "2026-10-18 11:00:00", "success": 1}, (
            "并发写进来的 last_run 被这次 PUT 用旧副本覆盖掉了——读-改-写没有整段扣在同一把锁里"
        )

    def test_two_concurrent_puts_both_survive_their_own_lock(self, target_pid, clock):
        """两个 PUT 并发：后写的那份决定最终值，但不许出现半份写（文件必须仍是合法 JSON）。"""
        _seed(target_pid, {"enabled": False, "interval_hours": 24, "last_run": None})
        results: List[Dict[str, Any]] = []
        barrier = threading.Barrier(2)

        def put(hours: int) -> None:
            barrier.wait(5)
            results.append(
                scheduler_module.save_schedule(target_pid, {"enabled": True, "interval_hours": hours})
            )

        threads = [threading.Thread(target=put, args=(hours,)) for hours in (12, 48)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        assert len(results) == 2
        saved = json.loads(_raw(target_pid).decode("utf-8"))
        assert saved["interval_hours"] in (12, 48)
        assert saved["enabled"] is True


# ---------------------------------------------------------------------------
# 5. 接线与契约
# ---------------------------------------------------------------------------


class TestWiring:
    def test_router_registers_both_routes(self):
        from app.server import api_schedule
        from app.server.router import Router

        router = Router()
        api_schedule.register(router)
        handler, params = router.match("GET", "/api/v1/p/p1/schedule")
        assert handler is api_schedule.get_schedule and params == {"pid": "p1"}
        handler, _ = router.match("PUT", "/api/v1/p/p1/schedule")
        assert handler is api_schedule.put_schedule

    def test_build_router_mounts_schedule_endpoints(self, tmp_path):
        from app.config import Settings
        from app.server.router import build_router

        settings = Settings(host="127.0.0.1", port=0, data_dir=str(tmp_path / "data"))
        router = build_router(settings)
        for method in ("GET", "PUT"):
            handler, _ = router.match(method, "/api/v1/p/default/schedule")
            assert handler is not None, "%s /schedule 没挂进 build_router" % method


# ---------------------------------------------------------------------------
# 为什么这里没有「grep 源码文本」的用例（M3-08 修复轮 / Important 1，R45）
#
# 上一版有三条按字面串读源码的断言：`api_schedule.py` 的 import 里不许出现 `storage`、
# `mock-api.js` 里要能找到 `24 * 90` 与四个键名、`views/settings.js` 里要有一句特定的
# `fieldError({…})` 标记。它们都是穿了测试衣服的 lint：契约真漂移时照样绿（把变量改个名、
# 上限写成 `48 * 45`、把错误写进另一个节点，三条都看不见），而任何一次格式化排版都会把它们
# 弄红。要保护的行为由两条**语义**用例执法：
#   * 本文件 `TestPutSchedule::test_invalid_interval_is_400_with_unchanged_bytes`——服务端
#     错误落在 `detail.field == "interval"` 上（前后端字段名的真实契约）。
#   * `app/web/tests/tests.js` 的 M3-08 UC-33 用例——跑真实视图代码：字段错误可见、控件标红、
#     不退化成全局 toast、未接入时一个请求都不发。
# 分层红线（handler 不碰 `storage`、不碰计数）在这里的可观察形式是「GET 一字节都不写、读不
# 递增、失败写不递增、成功只补一次 settings」，那四条用例已经各自钉住；grep 反而挡不住别名
# 导入。卡面只要求 mock **同步更新**，从没要求它被 grep。
