"""Public DL coefficient configuration uses effective intervals, not nearest dates."""

from copy import deepcopy
from datetime import datetime
from urllib.parse import urlencode

import pytest

from app.core import dltool, fsutil, storage
from app.services import dl_history
from authz_fixtures import as_role, make_server, seed_roles


def _clock(monkeypatch, at):
    monkeypatch.setattr(dl_history, "_effective_now", lambda: datetime.fromisoformat(at), raising=False)


def _save(caller, pid, module, value, wid="default"):
    path = "/api/v1/p/%s/dl" % pid
    state = caller.json("GET", path, headers={"X-Workspace-Id": wid})[2]
    config = deepcopy(state["items"][module])
    config["coeffs"]["k1"] = value
    if module == "pp_energy":
        config["trans_coeff"], config["power_conv_coeff"] = value + 1, 2
    if module == "rp_width":
        config["aom_sum"] = 100
        config["aom_width"]["coeffs"]["k1"] = value + 10
    status, _, saved = caller.json("POST", path + "/" + module + "/coeffs", config,
                                   headers={"If-Match": state["rev"], "X-Workspace-Id": wid})
    assert status == 200, saved
    return saved


def _query(caller, pid, at, module="pp_energy", wid="default"):
    path = "/api/v3/workspaces/%s/profiles/%s/dl/%s/coefficients" % (wid, pid, module)
    return caller.json("GET", path + "?" + urlencode({"at": at}))


@pytest.mark.parametrize("module", ["pp_energy", "rp_energy", "rp_width", "ppz"])
def test_asof_returns_latest_whole_configuration_at_or_before_requested_time(monkeypatch, module):
    pid = seed_roles("dl-asof")
    with make_server() as server:
        admin, visitor = as_role(server, "admin"), as_role(server, "visitor")
        _clock(monkeypatch, "2026-10-08T01:00:00+00:00")
        _save(admin, pid, module, 8)
        _clock(monkeypatch, "2026-10-11T01:00:00+00:00")
        _save(admin, pid, module, 11)
        status, _, found = _query(visitor, pid, "2026-10-09T09:00:00+08:00", module)
        assert status == 200, found
        assert found["configuration"]["coeffs"]["k1"] == 8
        assert found["effective_at"] == "2026-10-08T01:00:00.000000Z"
        assert found["valid_until"] == "2026-10-11T01:00:00.000000Z"
        assert found["module_id"] == module and found["profile_id"] == pid
        if module == "pp_energy":
            assert found["configuration"]["trans_coeff"] == 9
            assert found["configuration"]["power_conv_coeff"] == 2
        if module == "rp_width":
            assert found["configuration"]["aom_width"]["coeffs"]["k1"] == 18
            assert found["configuration"]["aom_sum"] == 100
        assert _query(visitor, pid, "2026-10-11T01:00:00Z", module)[2]["configuration"]["coeffs"]["k1"] == 11
        assert _query(visitor, pid, "2026-10-08T00:59:59Z", module)[0] == 404
        assert _query(visitor, pid, "2026-10-12T00:00:00Z", module)[2]["valid_until"] is None


def test_apply_and_deleting_display_history_do_not_rewrite_temporal_intervals(monkeypatch):
    pid = seed_roles("dl-asof-restore")
    monkeypatch.setattr(dl_history, "KEEP_PER_SERIES", 1)
    with make_server() as server:
        admin = as_role(server, "admin")
        _clock(monkeypatch, "2026-10-08T00:00:00+00:00")
        _save(admin, pid, "pp_energy", 8)
        _clock(monkeypatch, "2026-10-11T00:00:00+00:00")
        latest = _save(admin, pid, "pp_energy", 11)
        _clock(monkeypatch, "2026-10-12T00:00:00+00:00")
        # Retention drops the old display row, but must not drop its effective interval.
        assert _query(admin, pid, "2026-10-09T00:00:00Z")[2]["configuration"]["coeffs"]["k1"] == 8
        third = _save(admin, pid, "pp_energy", 12)
        path = "/api/v1/p/%s/dl/pp_energy/coeff-history" % pid
        # Keep two for the restore action; the prior time series remains independent.
        monkeypatch.setattr(dl_history, "KEEP_PER_SERIES", 2)
        _clock(monkeypatch, "2026-10-13T00:00:00+00:00")
        fourth = _save(admin, pid, "pp_energy", 13)
        _clock(monkeypatch, "2026-10-14T00:00:00+00:00")
        hid = third["entry"]["id"]
        status, _, applied = admin.json("POST", path + "/" + hid + "/apply", {}, headers={"If-Match": fourth["rev"]})
        assert status == 200, applied
        assert _query(admin, pid, "2026-10-13T12:00:00Z")[2]["configuration"]["coeffs"]["k1"] == 13
        assert _query(admin, pid, "2026-10-14T00:00:00Z")[2]["configuration"]["coeffs"]["k1"] == 12
        assert admin.json("DELETE", path + "/" + hid)[0] == 200
        assert _query(admin, pid, "2026-10-12T12:00:00Z")[2]["configuration"]["coeffs"]["k1"] == 12
        assert _query(admin, pid, "2026-10-11T12:00:00Z")[2]["configuration"]["coeffs"]["k1"] == 11


def test_invalid_or_missing_time_is_explicit_and_get_does_not_create_files(monkeypatch):
    pid = seed_roles("dl-asof-empty")
    with make_server() as server:
        visitor = as_role(server, "visitor")
        for at in ("", "2026-10-09", "2026-10-09T10:00:00", "bad", "2026-02-30T00:00:00Z", "2026-10-09T10:00:00+00:99"):
            assert _query(visitor, pid, at)[0] == 400
        assert _query(visitor, pid, "2026-10-09T00:00:00Z")[0] == 404
        assert _query(visitor, pid, "2026-10-09T00:00:00Z", "missing")[0] == 404
        with storage.use_profile(pid):
            import os
            assert not os.path.exists(dl_history.history_path())
            assert not os.path.exists(dltool._get_config_file())


def test_legacy_coverage_is_explicit_and_untracked_changes_are_not_fabricated(monkeypatch):
    pid = seed_roles("dl-asof-legacy")
    with make_server() as server:
        admin = as_role(server, "admin")
        _clock(monkeypatch, "2026-10-08T00:00:00+00:00")
        _save(admin, pid, "pp_energy", 8)
        with storage.use_profile(pid):
            book = fsutil.read_json(dl_history.history_path(), None)
            legacy = {key: book[key] for key in ("next", "entries")}
            legacy["entries"][0]["created_at"] = "2026-10-08T00:00:00+00:00"
            fsutil.atomic_write_json(dl_history.history_path(), legacy)
        found = _query(admin, pid, "2026-10-09T00:00:00Z")[2]
        assert found["coverage"] == "legacy_history" and found["interval_verified"] is False
        _clock(monkeypatch, "2026-10-11T00:00:00+00:00")
        _save(admin, pid, "pp_energy", 11)
        with storage.use_profile(pid):
            config = dltool.load_config()
            config["items"]["pp_energy"]["coeffs"]["k1"] = 99
            dltool.save_config(config)
        assert _query(admin, pid, "2026-10-12T00:00:00Z")[0] == 409
        assert _query(admin, pid, "2026-10-09T00:00:00Z")[2]["configuration"]["coeffs"]["k1"] == 8


def test_temporal_api_is_scoped_and_includes_full_item_saves(monkeypatch):
    seed_roles("dl-asof-scope")
    with make_server() as server:
        admin = as_role(server, "admin")
        w1 = admin.json("POST", "/api/v3/workspaces", {"name": "DL A"})[2]["workspace"]["id"]
        w2 = admin.json("POST", "/api/v3/workspaces", {"name": "DL B"})[2]["workspace"]["id"]
        _clock(monkeypatch, "2026-10-08T00:00:00+00:00")
        _save(admin, "default", "pp_energy", 8, w1)
        assert _query(admin, "default", "2026-10-09T00:00:00Z", wid=w2)[0] == 404
        assert _query(admin, "default", "2026-10-09T00:00:00Z", wid="absent")[0] == 404
        _clock(monkeypatch, "2026-10-11T00:00:00+00:00")
        state = admin.json("GET", "/api/v1/p/default/dl", headers={"X-Workspace-Id": w1})[2]
        config = deepcopy(state["items"]["pp_energy"])
        config["coeffs"]["k1"] = 15
        status, _, result = admin.json("PUT", "/api/v1/p/default/dl/pp_energy", config,
                                       headers={"If-Match": state["rev"], "X-Workspace-Id": w1})
        assert status == 200, result
        assert _query(admin, "default", "2026-10-12T00:00:00Z", wid=w1)[2]["configuration"]["coeffs"]["k1"] == 15


def test_timeline_write_failure_does_not_leave_changed_live_coefficients(monkeypatch):
    pid = seed_roles("dl-asof-write-failure")
    with make_server() as server:
        admin = as_role(server, "admin")
        _clock(monkeypatch, "2026-10-08T00:00:00+00:00")
        _save(admin, pid, "pp_energy", 8)
        state = admin.json("GET", "/api/v1/p/%s/dl" % pid)[2]
        write = fsutil.atomic_write_json

        def fail_history(path, value):
            if path.endswith(dl_history.HISTORY_FILE):
                raise OSError("injected history write failure")
            return write(path, value)

        monkeypatch.setattr(fsutil, "atomic_write_json", fail_history)
        config = deepcopy(state["items"]["pp_energy"])
        config["coeffs"]["k1"] = 11
        status, _, _ = admin.json("POST", "/api/v1/p/%s/dl/pp_energy/coeffs" % pid, config, headers={"If-Match": state["rev"]})
        assert status == 500
        after = admin.json("GET", "/api/v1/p/%s/dl" % pid)[2]
        assert after == state
        assert _query(admin, pid, "2026-10-12T00:00:00Z")[2]["configuration"]["coeffs"]["k1"] == 8
