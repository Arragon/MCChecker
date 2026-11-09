"""Actual managed collector + Chromium + loopback TLS SDA; no mocked network/session/vault."""
import base64
import json
import os
import threading
import time
from pathlib import Path

import pytest

from authz_fixtures import as_role, make_server, seed_roles
from app.core import storage
from app.server import api_connections, api_sync_heartbeat
from app.services import file_versions, sync_catalog, sync_collector, sync_tasks, sync_heartbeat, sync_status

from app.services import collector_manager
from app.services.collector_client import CollectorClientError
from scripts.sda_simulator import CHROME, ORIGIN, SdaSimulator

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def connected_simulator(tmp_path, monkeypatch):
    python = os.environ.get("MCC_SDA_INTEGRATION_PYTHON", str(ROOT / ".venv-collector/bin/python"))
    if not Path(python).is_file() or not Path(CHROME).is_file():
        pytest.skip("requires separate collector Python/Playwright/cryptography and Chromium")
    with SdaSimulator(tmp_path) as sim:
        for name, value in {"MCHECKER_COLLECTOR_CONFIG": str(sim.config), "MCHECKER_SECRET_ROOT": str(tmp_path / "vault"),
                            "MCHECKER_SECRET_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
                            "MCHECKER_COLLECTOR_CHROMIUM": CHROME, "NODE_EXTRA_CA_CERTS": str(sim.cert)}.items():
            monkeypatch.setenv(name, value)
        manager = collector_manager.CollectorManager(python=python)
        monkeypatch.setattr(collector_manager, "_manager", manager)
        for module, field in ((api_connections, "_VAULT"), (api_connections, "_PROBE"), (api_sync_heartbeat, "_PROBE"),
                              (sync_catalog, "_READER"), (sync_tasks, "_SDA_READER"), (sync_collector, "_BRIDGE")):
            monkeypatch.setattr(module, field, None)
        bridge = sync_collector.configure_from_environment()
        assert bridge.thread is None, "deployment configuration must not start threads before HTTP binds"
        bridge.start_heartbeats()
        try:
            yield sim, bridge
        finally:
            bridge.stop()
            manager.stop()


def _json(caller, method, path, payload=None, expected=200, **kwargs):
    status, _, result = caller.json(method, path, payload, **kwargs)
    assert status == expected, (status, result)
    return result


def _activate(admin, base, password="sim-password"):
    state = _json(admin, "POST", base + "/credentials/candidates", {"username": "sim-admin", "password": password}, 201)
    candidate = state["candidate"]
    probe = _json(admin, "POST", base + "/credentials/probe", {"candidate_id": candidate["candidate_id"]}, 202)["operation"]
    if probe["state"] != "succeeded":
        return probe
    activated = _json(admin, "POST", base + "/credentials/candidates/" + candidate["candidate_id"] + "/activate", {"probe_epoch": candidate["auth_epoch"]}, 202)["operation"]
    assert activated["state"] == "succeeded", activated
    return activated


def test_application_api_drives_real_collector_and_commits_file(connected_simulator):
    sim, bridge = connected_simulator
    seed_roles("sda-runtime")
    with make_server() as server:
        admin, visitor = as_role(server, "admin"), as_role(server, "visitor")
        connection = _json(admin, "POST", "/api/v3/workspaces/default/connections", {"name": "Simulation SDA", "origin": ORIGIN}, 201)
        cid = connection["connection_id"]
        base = "/api/v3/workspaces/default/connections/" + cid
        assert visitor.json("POST", base + "/credentials/candidates", {"username": "sim-admin", "password": "sim-password"})[0] == 403
        state = _json(admin, "POST", base + "/credentials/candidates", {"username": "sim-admin", "password": "sim-password"}, 201)
        candidate = state["candidate"]
        unverified = _json(admin, "POST", base + "/credentials/candidates/" + candidate["candidate_id"] + "/activate", {"probe_epoch": 0}, 202)
        assert unverified["operation"]["state"] == "failed"
        assert _json(admin, "GET", base + "/credentials")["active_generation"] is None
        checked = _json(admin, "POST", base + "/credentials/probe", {"candidate_id": candidate["candidate_id"]}, 202)
        assert checked["operation"]["state"] == "succeeded", checked
        active = _json(admin, "POST", base + "/credentials/candidates/" + candidate["candidate_id"] + "/activate", {"probe_epoch": 0}, 202)
        assert active["operation"]["state"] == "succeeded", active
        catalog = _json(admin, "GET", base + "/catalog")
        assert len(catalog["items"]) == 2
        with storage.use_profile("default"):
            storage.save_config_file("Target.xml", b"<config/>")
            fid = file_versions.current_identity("default", "Target.xml")["file_id"]
        payload = {"profile_id": "default", "name": "Display name.xml", "kind": "sda_file", "connection_id": cid,
                   "resource_ref": "file-1", "display_path": "/simulation/MachineConstant.xml", "target": {"kind": "file_archive", "file_id": fid}}
        checked = _json(admin, "POST", "/api/v3/workspaces/default/sync/source-tests", payload)
        assert checked["status"] == "succeeded", checked
        with storage.use_profile("default"):
            assert storage.load_config_file("Target.xml") == b"<config/>"
        source = _json(admin, "POST", "/api/v3/workspaces/default/sync/sources", payload, 201)
        assert sync_status.get_status("default", "default", source["source_id"])["last_verified_at"] is None
        queued = _json(admin, "POST", "/api/v3/workspaces/default/sync/runs", {"scope": "selected_sources", "source_ids": [source["source_id"]]}, 202)
        run = queued["runs"][0]
        from test_sync_executor import wait_run
        finished = wait_run("default", run["run"]["run_id"])
        assert finished["run"]["state"] == "succeeded", finished
        with storage.use_profile("default"):
            assert storage.load_config_file("Target.xml") == sim.content
        assert sync_status.get_status("default", "default", source["source_id"])["last_verified_at"]
        assert sim.logins == 1
        sim.expire()
        assert bridge.heartbeat("default", cid)["status"] == "succeeded"
        assert sim.logins == 2
        heartbeat = _json(admin, "PUT", base + "/heartbeat", {"enabled": True, "interval_minutes": 1}, headers={"If-Match": "0"})
        assert heartbeat["next_due_at"]
        deadline = time.monotonic() + 4
        while not sync_heartbeat.get_state("default", cid)["last_heartbeat_at"] and time.monotonic() < deadline:
            time.sleep(.1)
        assert sync_heartbeat.get_state("default", cid)["last_result"] == "success"
        failed_rotation = _activate(admin, base, "wrong-simulation-password")
        assert failed_rotation["state"] == "failed", failed_rotation
        locked = _json(admin, "GET", base + "/credentials")
        assert locked["state"] == "locked" and locked["active_generation"] == 1
        attempts = sim.logins
        assert bridge.heartbeat("default", cid)["reason_code"] == "locked"
        assert sim.logins == attempts
        restored = _activate(admin, base)
        assert restored["state"] == "succeeded", restored
        current = _json(admin, "GET", base + "/credentials")
        revoked = _json(admin, "POST", base + "/credentials/revoke", {}, headers={"If-Match": str(current["revision"])})
        assert revoked["state"] == "unconfigured" and revoked["active_generation"] is None
        assert bridge.heartbeat("default", cid)["status"] == "failed"
        assert "sim-password" not in json.dumps(finished)


def test_managed_collector_uses_real_login_cookie_vault_and_frames(connected_simulator, tmp_path):
    sim, bridge = connected_simulator
    client = bridge.manager.client()
    def call(operation, params):
        raw = client.request(operation, "default", "conn-sim", params, timeout=15)
        if operation == "read_resource":
            return dict(client.last_meta["result"], content=raw)
        return json.loads(raw)
    call("store_credentials", {"generation": 1, "credentials": {"username": "sim-admin", "password": "sim-password"}})
    call("configure_connection", {"origin": ORIGIN, "policy_revision": 1, "auth_epoch": 0, "generation": 1, "auto_relogin": False})
    checked = call("verify_credentials", {"generation": 1})
    assert checked["ok"] and checked["session_valid"]
    call("configure_connection", {"origin": ORIGIN, "policy_revision": 1, "auth_epoch": 0, "generation": 1, "auto_relogin": True})
    assert sim.logins == 1 and sim.protected_reads >= 1
    call("activate_credentials", {"generation": 1})
    result = call("read_resource", {"kind": "file", "resource_ref": "file-1"})
    assert result["content"] == sim.content
    sim.delay = .4
    timer = threading.Timer(.1, lambda: client.cancel(client.last_request_id))
    timer.start()
    try:
        with pytest.raises(CollectorClientError) as canceled:
            client.request("read_resource", "default", "conn-sim", {"kind": "file", "resource_ref": "file-1"}, timeout=10)
        assert canceled.value.reason_code == "canceled"
    finally:
        timer.join(2)
        sim.delay = 0
    sim.expire()
    assert call("keepalive", {})["status"] == "succeeded"
    assert sim.logins == 2
    assert call("keepalive", {})["renewal_observed"] is True
    assert sim.logins == 2
    with pytest.raises(CollectorClientError) as stale:
        client.request("keepalive", "default", "conn-sim", {}, expected_auth_epoch=99)
    assert stale.value.reason_code == "source_changed"
    assert b"sim-password" not in b"".join(p.read_bytes() for p in (tmp_path / "vault").glob("*.enc"))
    assert call("delete_credentials", {"generation": 1})["deleted"] is True
    assert call("delete_credentials", {"generation": 1})["deleted"] is False
    assert json.loads(client.request("store_credentials", "workspace:1", "connection:1", {"generation": 1, "credentials": {"username": "sim-admin", "password": "sim-password"}}))["stored"]
    assert json.loads(client.request("delete_credentials", "workspace:1", "connection:1", {"generation": 1}))["deleted"]


def test_real_network_faults_cancellation_and_concurrent_local_edit(connected_simulator):
    sim, bridge = connected_simulator
    seed_roles("sda-network-faults")
    with make_server() as server:
        admin = as_role(server, "admin")
        connection = _json(admin, "POST", "/api/v3/workspaces/default/connections", {"name": "SDA faults", "origin": ORIGIN}, 201)
        cid = connection["connection_id"]
        base = "/api/v3/workspaces/default/connections/" + cid
        assert _activate(admin, base)["state"] == "succeeded"
        with storage.use_profile("default"):
            storage.save_config_file("Target.xml", b"<config/>")
            fid = file_versions.current_identity("default", "Target.xml")["file_id"]
        payload = {"profile_id": "default", "name": "Target.xml", "kind": "sda_file", "connection_id": cid,
                   "resource_ref": "file-1", "display_path": "/simulation/MachineConstant.xml", "target": {"kind": "file_archive", "file_id": fid}}
        source = _json(admin, "POST", "/api/v3/workspaces/default/sync/sources", payload, 201)
        for fault in ("bad_hash", "login_html"):
            sim.fault = fault
            check = _json(admin, "POST", "/api/v3/workspaces/default/sync/source-tests", payload)
            assert check["status"] == "failed", check
            assert sync_status.get_status("default", "default", source["source_id"])["last_verified_at"] is None
        sim.fault = None
        sim.content = b"<config><param_1>" + b"7" * (300 * 1024) + b"</param_1></config>"
        check = _json(admin, "POST", "/api/v3/workspaces/default/sync/source-tests", payload)
        assert check["status"] == "succeeded" and check["items"][0]["size"] == len(sim.content)
        sim.delay = .5
        reads = sim.protected_reads
        queued = _json(admin, "POST", "/api/v3/workspaces/default/sync/runs", {"scope": "selected_sources", "source_ids": [source["source_id"]]}, 202)
        run_id = queued["runs"][0]["run"]["run_id"]
        deadline = time.monotonic() + 3
        while sim.protected_reads == reads and time.monotonic() < deadline:
            time.sleep(.01)
        assert sim.protected_reads > reads
        local = b"<config><param_1>99</param_1></config>"
        with storage.use_profile("default"):
            storage.save_config_file("Target.xml", local)
        from test_sync_executor import wait_run
        conflict = wait_run("default", run_id)
        assert conflict["run"]["state"] != "succeeded"
        with storage.use_profile("default"):
            assert storage.load_config_file("Target.xml") == local
        reads = sim.protected_reads
        queued = _json(admin, "POST", "/api/v3/workspaces/default/sync/runs", {"scope": "selected_sources", "source_ids": [source["source_id"]]}, 202)
        run_id = queued["runs"][0]["run"]["run_id"]
        deadline = time.monotonic() + 3
        while sim.protected_reads == reads and time.monotonic() < deadline:
            time.sleep(.01)
        assert sim.protected_reads > reads
        _json(admin, "POST", "/api/v3/workspaces/default/sync/runs/" + run_id + "/cancel", {}, 202)
        assert wait_run("default", run_id)["run"]["state"] == "canceled"
        with storage.use_profile("default"):
            assert storage.load_config_file("Target.xml") == local
        reads = sim.protected_reads
        queued = _json(admin, "POST", "/api/v3/workspaces/default/sync/runs", {"scope": "selected_sources", "source_ids": [source["source_id"]]}, 202)
        run_id = queued["runs"][0]["run"]["run_id"]
        deadline = time.monotonic() + 3
        while sim.protected_reads == reads and time.monotonic() < deadline:
            time.sleep(.01)
        assert sim.protected_reads > reads
        _json(admin, "PATCH", base, {"name": "Changed during read"}, headers={"If-Match": str(connection["policy_revision"])})
        assert wait_run("default", run_id)["run"]["state"] != "succeeded"
        with storage.use_profile("default"):
            assert storage.load_config_file("Target.xml") == local
        other = _json(admin, "POST", "/api/v3/workspaces", {"name": "Other simulation workspace"}, 201)["workspace"]["id"]
        assert admin.json("GET", "/api/v3/workspaces/" + other + "/connections/" + cid + "/catalog")[0] == 404


def test_network_table_snapshot_can_be_compared_and_applied_to_draft(connected_simulator):
    from app.services import config_v3_catalog, config_v3_drafts, sync_candidates
    from test_sync_executor import wait_run
    sim, bridge = connected_simulator
    seed_roles("sda-table-runtime")
    with make_server() as server:
        admin = as_role(server, "admin")
        connection = _json(admin, "POST", "/api/v3/workspaces/default/connections", {"name": "SDA tables", "origin": ORIGIN}, 201)
        cid = connection["connection_id"]
        assert _activate(admin, "/api/v3/workspaces/default/connections/" + cid)["state"] == "succeeded"
        with storage.use_profile("default"):
            storage.save_config_file("Parameters.json", b'{"parameters":{"param_1":1}}')
            fid = file_versions.current_identity("default", "Parameters.json")["file_id"]
            config = config_v3_catalog.create_config("default", {"code": "TABLE", "name": "Simulation table"})
            config_id = config["config_id"]
            parameter = config_v3_catalog.create_parameter("default", config_id, {"name": "param_1", "directory": [], "type": "int"})
            release = config_v3_catalog.create_release("default", config_id, {"version": "1"})
            config_v3_catalog.create_binding("default", config_id, release["release_id"], {"parameter_id": parameter["parameter_id"], "locator": "/parameters/param_1", "file_id": fid, "confirmed": True})
        source = _json(admin, "POST", "/api/v3/workspaces/default/sync/sources", {"profile_id": "default", "name": "Parameters.json", "kind": "sda_table", "connection_id": cid, "resource_ref": "table-1", "display_path": "/simulation/Parameters.json", "target": {"kind": "default_candidate", "file_id": fid, "configuration_id": config_id, "release_id": release["release_id"]}}, 201)
        queued = _json(admin, "POST", "/api/v3/workspaces/default/sync/runs", {"scope": "selected_sources", "source_ids": [source["source_id"]]}, 202)
        finished = wait_run("default", queued["runs"][0]["run"]["run_id"])
        assert finished["run"]["state"] == "succeeded", finished
        assert finished["run"]["counts"]["candidate_created"] == 1
        candidate = sync_candidates.list_candidates("default")["items"][0]
        assert candidate["file_version_id"]
        url = "/api/v3/workspaces/default/sync/candidates/" + candidate["candidate_id"]
        did = "d0-" + release["release_id"]
        preview = _json(admin, "POST", url + "/apply-preview", {"config_id": config_id, "draft_id": did}, 201)
        assert preview["blockers"] == [], preview
        decisions = {item["parameter_id"]: "use_new" for item in preview["items"] if item["action"] == "conflict"}
        _json(admin, "POST", url + "/apply", {"preview_id": preview["preview_id"], "request_id": "simulation-table-apply", "decisions": decisions})
        with storage.use_profile("default"):
            assert str(config_v3_drafts.get_draft("default", config_id, did)["values"][parameter["parameter_id"]]["value"]) == "7"
            assert config_v3_catalog.get_config("default", config_id)["releases"][0]["default_revision_id"] is None
