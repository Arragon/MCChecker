"""M16-27 候选三方差异计划/类型校验/草稿 CAS 预览。"""

from __future__ import annotations

import pytest

from app.core import storage
from app.core.errors import REVISION_CONFLICT, AppError
from app.services import (
    config_v3_catalog,
    config_v3_drafts,
    sync_candidate_plans,
    workspace_catalog,
)


def make_config():
    wid = workspace_catalog.create_workspace("W-plan")["id"]
    with storage.use_workspace(wid):
        config = config_v3_catalog.create_config(
            "default", {"code": "C1", "name": "Config"}
        )
        cid = config["config_id"]
        p1 = config_v3_catalog.create_parameter(
            "default", cid, {"name": "P1", "directory": ["G"], "type": "int"}
        )["parameter_id"]
        p2 = config_v3_catalog.create_parameter(
            "default", cid, {"name": "P2", "directory": ["G"], "type": "int"}
        )["parameter_id"]
        p3 = config_v3_catalog.create_parameter(
            "default", cid, {"name": "P3", "directory": ["G"], "type": "int"}
        )["parameter_id"]
        p4 = config_v3_catalog.create_parameter(
            "default", cid, {"name": "P4", "directory": ["G"], "type": "int"}
        )["parameter_id"]
        p_bool = config_v3_catalog.create_parameter(
            "default", cid, {"name": "Enabled", "directory": ["G"], "type": "bool"}
        )["parameter_id"]
        p_str = config_v3_catalog.create_parameter(
            "default", cid, {"name": "Label", "directory": ["G"], "type": "string"}
        )["parameter_id"]
        release = config_v3_catalog.create_release("default", cid, {"version": "1"})
    return wid, cid, "d0-" + release["release_id"], {
        "p1": p1,
        "p2": p2,
        "p3": p3,
        "p4": p4,
        "p_bool": p_bool,
        "p_str": p_str,
    }


def typed(kind, text, unit=None):
    return {"type": kind, "text": text, "unit": unit}


def patch_current(wid, cid, did, values, request_id, revision=0):
    items = [
        {
            "parameter_id": pid,
            "membership": "included",
            "value_state": "present",
            "value": value,
        }
        for pid, value in values.items()
    ]
    with storage.use_workspace(wid):
        return config_v3_drafts.patch_values(
            "default",
            cid,
            did,
            {"request_id": request_id, "items": items},
            if_match='"%d"' % revision,
        )


def test_three_way_actions_and_type_validation():
    wid, cid, did, ids = make_config()
    patch_current(
        wid,
        cid,
        did,
        {ids["p1"]: "1", ids["p2"]: "2", ids["p3"]: "1", ids["p4"]: "2"},
        "r1",
    )
    base = {
        ids["p1"]: typed("int", "1"),
        ids["p2"]: typed("int", "1"),
        ids["p3"]: typed("int", "2"),
        ids["p4"]: typed("int", "1"),
    }
    candidate = {
        ids["p1"]: typed("int", "2"),
        ids["p2"]: typed("int", "2"),
        ids["p3"]: typed("int", "2"),
        ids["p4"]: typed("int", "3"),
    }
    preview = sync_candidate_plans.create_preview(
        wid, "default", cid, did, "cand-1", base, candidate
    )
    actions = {item["parameter_id"]: item["action"] for item in preview["items"]}
    assert actions[ids["p1"]] == "from_new"
    assert actions[ids["p2"]] == "keep"
    assert actions[ids["p3"]] == "keep"
    assert actions[ids["p4"]] == "conflict"
    assert preview["needs_decision"] == [ids["p4"]]
    assert preview["can_apply"] is False
    assert preview["allowed_decisions"]

    assert sync_candidate_plans.get_preview(wid, preview["preview_id"])["preview_id"] == preview["preview_id"]
    assert sync_candidate_plans.list_previews(wid) == [preview]

    bad = dict(candidate)
    bad[ids["p1"]] = typed("string", "2")
    blocked = sync_candidate_plans.create_preview(
        wid, "default", cid, did, "cand-2", base, bad
    )
    assert blocked["can_apply"] is False
    assert any(row["code"] == "type_invalid" for row in blocked["blockers"])


def test_null_zero_false_are_distinct():
    wid, cid, did, ids = make_config()
    with storage.use_workspace(wid):
        config_v3_drafts.patch_values(
            "default",
            cid,
            did,
            {
                "request_id": "r1",
                "items": [
                    {
                        "parameter_id": ids["p1"],
                        "membership": "included",
                        "value_state": "present",
                        "value": "0",
                    },
                    {
                        "parameter_id": ids["p_bool"],
                        "membership": "included",
                        "value_state": "present",
                        "value": False,
                    },
                    {
                        "parameter_id": ids["p_str"],
                        "membership": "included",
                        "value_state": "null",
                    },
                ],
            },
            if_match='"0"',
        )
    values = {
        ids["p1"]: typed("int", "0"),
        ids["p_bool"]: typed("bool", "false"),
        ids["p_str"]: typed("null", "null"),
    }
    preview = sync_candidate_plans.create_preview(
        wid, "default", cid, did, "cand-kinds", values, values
    )
    stored = preview["candidate"]
    assert stored[ids["p1"]]["value"]["type"] == "int"
    assert stored[ids["p1"]]["value"]["text"] == "0"
    assert stored[ids["p_bool"]]["value"]["type"] == "bool"
    assert stored[ids["p_bool"]]["value"]["text"] == "false"
    assert stored[ids["p_str"]]["value"]["type"] == "null"


def test_preview_after_draft_change_is_409():
    wid, cid, did, ids = make_config()
    patch_current(wid, cid, did, {ids["p1"]: "1"}, "r1")
    preview = sync_candidate_plans.create_preview(
        wid,
        "default",
        cid,
        did,
        "cand-cas",
        {ids["p1"]: typed("int", "1")},
        {ids["p1"]: typed("int", "2")},
    )
    patch_current(wid, cid, did, {ids["p1"]: "9"}, "r2", revision=1)
    with pytest.raises(AppError) as exc:
        sync_candidate_plans.get_preview(wid, preview["preview_id"])
    assert exc.value.code == REVISION_CONFLICT
    assert "draft_revision" in exc.value.detail["changed"]


def test_frozen_d0_has_no_override_decisions():
    wid, cid, did, ids = make_config()
    patch_current(wid, cid, did, {ids["p1"]: "1"}, "r1")
    with storage.use_workspace(wid):
        doc = config_v3_catalog._load("default")
        release = doc["configs"][cid]["releases"][did[3:]]
        release.setdefault("d0", {"values": {}, "frozen": False, "revision": 0})["frozen"] = True
        config_v3_catalog._save("default", doc)

    preview = sync_candidate_plans.create_preview(
        wid,
        "default",
        cid,
        did,
        "cand-frozen",
        {ids["p1"]: typed("int", "1")},
        {ids["p1"]: typed("int", "2")},
    )
    assert preview["d0_frozen"] is True
    assert preview["allowed_decisions"] == []
    assert preview["can_apply"] is False
    assert any(row["code"] == "d0_frozen" for row in preview["blockers"])
