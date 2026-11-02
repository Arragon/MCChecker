"""M16-33 workspace sync run API tests."""

import json

from authz_fixtures import as_role, make_server, seed_roles

from app.core.errors import BUSY, AppError
from app.services import sync_connections, sync_sources, sync_tasks


def make_workspace(caller, name):
    status, _headers, body = caller.json("POST", "/api/v3/workspaces", {"name": name})
    assert status == 201, body
    return body["workspace"]["id"]


def add_profile(caller, wid, name="second"):
    status, _headers, body = caller.json(
        "POST", "/api/v3/workspaces/%s/profiles" % wid, {"name": name}
    )
    assert status == 201, body
    return body["profile"]["id"]


def make_connection(wid):
    return sync_connections.create_connection(wid, "SDA main", "https://sda.example.invalid")[
        "connection_id"
    ]


def make_source(wid, cid, profile_id, name, enabled=True, mode="inherit"):
    return sync_sources.create_source(
        wid,
        {
            "profile_id": profile_id,
            "name": name,
            "kind": "sda_catalog",
            "target": None,
            "enabled": enabled,
            "schedule_mode": mode,
            "interval_hours": None,
            "connection_id": cid,
            "resource_ref": "res-%s" % name,
            "display_path": "/synthetic/%s" % name,
            "record_source": None,
            "keyword": "",
        },
    )


def runs_url(wid):
    return "/api/v3/workspaces/%s/sync/runs" % wid


def test_workspace_all_run_spans_profiles_and_keeps_manual_skips_disabled():
    seed_roles("sync-runs-all")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "W-runs")
        p2 = add_profile(admin, wid)
        cid = make_connection(wid)
        manual = make_source(wid, cid, "default", "A-manual.xml", mode="manual")
        p2_source = make_source(wid, cid, p2, "B-second.xml")
        disabled = make_source(wid, cid, "default", "Z-disabled.xml", enabled=False)

        status, _headers, body = admin.json(
            "POST", runs_url(wid), {"scope": "workspace_all"}
        )
        assert status == 202, body
        preview = body["preview"]
        assert preview["total"] == 2
        assert preview["excluded"] == []
        assert preview["read_set_sha256"]
        assert {row["profile_id"] for row in body["runs"]} == {"default", p2}
        for row in body["runs"]:
            assert row["run"]["trigger"] == "manual"
            assert row["task"]["type"] == "sync_run"

        status, _headers, frozen = admin.json(
            "GET",
            "/api/v3/workspaces/%s/sync/previews/%s"
            % (wid, preview["preview_id"]),
        )
        assert status == 200, frozen
        ids = {item["source_id"] for item in frozen["items"]}
        assert ids == {manual["source_id"], p2_source["source_id"]}
        assert disabled["source_id"] not in ids
        manual_item = next(
            item for item in frozen["items"] if item["source_id"] == manual["source_id"]
        )
        assert manual_item["schedule_mode"] == "manual"


def test_run_requires_tasks_run_and_is_workspace_scoped():
    seed_roles("sync-runs-roles")
    with make_server() as server:
        admin = as_role(server, "admin")
        visitor = as_role(server, "visitor")
        member = as_role(server, "member")
        wid = make_workspace(admin, "W-runs-roles")
        cid = make_connection(wid)
        make_source(wid, cid, "default", "A.xml")

        for caller in (visitor, member):
            status, _headers, denied = caller.json(
                "POST", runs_url(wid), {"scope": "workspace_all"}
            )
            assert status == 403, denied
            assert denied["error"]["detail"]["action"] == "tasks_run"

        status, _headers, created = admin.json(
            "POST", "/api/v3/workspaces/%s/sync/previews" % wid, {"scope": "workspace_all"}
        )
        assert status == 201, created
        other = make_workspace(admin, "W-other-runs")
        status, _headers, misplaced = admin.json(
            "POST", runs_url(other), {"preview_id": created["preview_id"]}
        )
        assert status == 404, misplaced

        status, _headers, missing = admin.json(
            "POST", "/api/v3/workspaces/w-nothere/sync/runs", {"scope": "workspace_all"}
        )
        assert status == 404, missing


def test_policy_change_after_preview_returns_409_without_running():
    seed_roles("sync-runs-conflict")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "W-conflict")
        cid = make_connection(wid)
        make_source(wid, cid, "default", "A.xml")

        status, _headers, preview = admin.json(
            "POST", "/api/v3/workspaces/%s/sync/previews" % wid, {"scope": "workspace_all"}
        )
        assert status == 201, preview

        status, _headers, policy = admin.json(
            "PUT",
            "/api/v3/workspaces/%s/sync/policy" % wid,
            {"enabled": True, "default_interval_hours": 8},
            headers={"If-Match": "0"},
        )
        assert status == 200, policy
        assert policy["revision"] == 1

        status, _headers, conflict = admin.json(
            "POST",
            runs_url(wid),
            {"scope": "workspace_all", "preview_id": preview["preview_id"]},
        )
        assert status == 409, conflict
        assert conflict["error"]["code"] == "REVISION_CONFLICT"
        assert conflict["error"]["detail"]["field"] == "policy_revision"


def test_paused_policy_still_allows_manual_workspace_run():
    seed_roles("sync-runs-paused")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "W-paused")
        cid = make_connection(wid)
        make_source(wid, cid, "default", "A.xml")

        status, _headers, policy = admin.json(
            "PUT",
            "/api/v3/workspaces/%s/sync/policy" % wid,
            {"enabled": False},
            headers={"If-Match": "0"},
        )
        assert status == 200, policy
        assert policy["enabled"] is False

        status, _headers, accepted = admin.json(
            "POST", runs_url(wid), {"scope": "workspace_all"}
        )
        assert status == 202, accepted
        assert len(accepted["runs"]) == 1
        assert accepted["runs"][0]["run"]["trigger"] == "manual"


def test_run_with_preview_id_uses_frozen_scope_only():
    seed_roles("sync-runs-frozen")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "W-frozen")
        p2 = add_profile(admin, wid)
        cid = make_connection(wid)
        first = make_source(wid, cid, "default", "A.xml")
        make_source(wid, cid, p2, "B.xml")

        status, _headers, preview = admin.json(
            "POST",
            "/api/v3/workspaces/%s/sync/previews" % wid,
            {"scope": "current_profile", "profile_id": "default"},
        )
        assert status == 201, preview
        assert preview["total"] == 1

        status, _headers, accepted = admin.json(
            "POST", runs_url(wid), {"preview_id": preview["preview_id"]}
        )
        assert status == 202, accepted
        assert [row["profile_id"] for row in accepted["runs"]] == ["default"]
        assert accepted["preview"]["total"] == 1
        assert accepted["preview"]["preview_id"] == preview["preview_id"]

        status, _headers, frozen = admin.json(
            "GET",
            "/api/v3/workspaces/%s/sync/previews/%s"
            % (wid, preview["preview_id"]),
        )
        assert status == 200
        assert [item["source_id"] for item in frozen["items"]] == [first["source_id"]]


def test_partial_busy_returns_409_with_safe_detail(monkeypatch):
    seed_roles("sync-runs-busy")
    with make_server() as server:
        admin = as_role(server, "admin")
        wid = make_workspace(admin, "W-busy")
        p2 = add_profile(admin, wid)
        cid = make_connection(wid)
        make_source(wid, cid, "default", "A.xml")
        make_source(wid, cid, p2, "B.xml")

        seen = []

        def fake_submit(ctx, sources, trigger="manual", **kwargs):
            seen.append((ctx.workspace_id, ctx.profile_id, trigger))
            if ctx.profile_id == p2:
                raise AppError(
                    BUSY, "同类任务正在执行（由 管理员乙 发起）", task_id="task-secret"
                )
            return {
                "run": {"run_id": "run-safe", "state": "queued", "trigger": trigger},
                "task": {"id": "task-safe", "type": "sync_run", "state": "queued"},
            }

        monkeypatch.setattr(sync_tasks, "submit_sync_run", fake_submit)

        status, _headers, body = admin.json(
            "POST", runs_url(wid), {"scope": "workspace_all"}
        )
        assert status == 409, body
        assert body["error"]["code"] == BUSY
        detail = body["error"]["detail"]
        assert detail["failed_profile_id"] == p2
        assert detail["started_profiles"] == ["default"]
        assert detail["started_run_ids"] == ["run-safe"]
        raw = json.dumps(body, ensure_ascii=False)
        assert "task-secret" not in raw
        assert "sda.example.invalid" not in raw
        assert seen == [(wid, "default", "manual"), (wid, p2, "manual")]
