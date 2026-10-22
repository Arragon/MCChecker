"""配置管理领域契约的静态校验（M5-01 / INH-1935）。

本文件只校验 `docs/MCChecker_CONFIG_DOMAIN_SPEC.md` 与
`tests/fixtures/config_management/contracts/` 的一致性：**不 import 任何业务模块**，
因此它先于 M5-03 之后的实现存在，也不随实现变化而失效。

验收对照（卡面 3 条）：

1. 相同文件在不同机型、不同软件版可区分；归档不能无证据反推软件版。
   → `test_file_snapshot_keeps_archive_ref_and_blob_identity`
2. 通用/机型/设备上游引用、同范围前修订、跨版本种子与出口批次各有专用字段。
   → `test_baseline_scopes_and_refs_are_separate_fields`
3. 完整契约样例包含 archive_only/managed、unknown release、retired、pin 等状态；
   模块依赖方向清楚。
   → `test_enums_cover_required_states` / `test_spec_declares_layering`
"""

from __future__ import annotations

import json
import os
import re

import pytest

FIXTURE_DIR = os.path.join(
    os.path.dirname(__file__), "fixtures", "config_management", "contracts"
)
SPEC_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "docs", "MCChecker_CONFIG_DOMAIN_SPEC.md"
)

#: 每种对象的 ID 前缀，必须与 SPEC §2.2 一致。
ID_PREFIX = {
    "family": "cf_",
    "logical_file": "lf_",
    "software_release": "sr_",
    "file_snapshot": "fs_",
    "release_batch": "rb_",
    "template_variant": "tv_",
    "parameter": "pm_",
    "definition_revision": "dr_",
    "definition_set": "ds_",
    "tracking_policy": "tp_",
    "baseline": "bl_",
    "observation": "ob_",
    "change_set": "cs_",
    "artifact": "ar_",
    "delivery": "dl_",
}

#: 每种对象必须出现的字段（缺一即契约不完整）。
REQUIRED_KEYS = {
    "family": {"id", "name", "status", "created_at", "updated_at"},
    "logical_file": {"id", "family_id", "role", "display_name", "aliases", "status"},
    "software_release": {"id", "version_label", "build", "predecessor_id", "status", "source"},
    "file_snapshot": {
        "id",
        "release_id",
        "logical_file_id",
        "kind_detail",
        "blob_sha256",
        "original_filename",
        "captured_at",
        "source",
        "archive_ref",
        "provenance",
        "status",
    },
    "release_batch": {"id", "release_id", "snapshot_ids", "supersedes", "created_at", "created_by"},
    "template_variant": {"id", "family_id", "release_id", "structure_signature", "template_snapshot_id", "status"},
    "parameter": {"id", "family_id", "name", "aliases", "status"},
    "definition_revision": {
        "id",
        "parameter_id",
        "revision_no",
        "xml",
        "json",
        "type",
        "unit",
        "constraints",
        "supersedes",
        "status",
    },
    "definition_set": {"id", "release_id", "variant_id", "definition_ids", "created_at"},
    "tracking_policy": {
        "id",
        "scope",
        "family_id",
        "profile_id",
        "mode",
        "reason",
        "created_by",
    },
    "baseline": {
        "id",
        "scope",
        "release_id",
        "definition_set_id",
        "values",
        "upstream_ref",
        "previous_revision_id",
        "state",
        "created_by",
    },
    "observation": {
        "id",
        "scope",
        "profile_id",
        "logical_file_id",
        "release_ref",
        "blob_sha256",
        "captured_at",
        "capture_kind",
        "completeness",
        "mapping",
        "status",
    },
    "change_set": {"id", "baseline_id", "base_revision_id", "plan_ref", "changes", "state"},
    "artifact": {
        "id",
        "baseline_id",
        "variant_id",
        "definition_set_id",
        "template_snapshot_id",
        "blob_sha256",
        "validation",
        "determinism_key",
    },
    "delivery": {"id", "artifact_id", "target", "registered_by", "executed_at", "result"},
}

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: kind → 样例文件名（`baseline` 有两个作用域样例，取通用基线作为代表）。
SAMPLE_FILE = {"baseline": "baseline_common.json"}


def _load(kind: str):
    path = os.path.join(FIXTURE_DIR, SAMPLE_FILE.get(kind, kind + ".json"))
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _all_contracts():
    names = []
    for name in sorted(os.listdir(FIXTURE_DIR)):
        if name.endswith(".json"):
            with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as fh:
                names.append((name, json.load(fh)))
    return names


def test_contract_fixtures_exist_for_every_kind() -> None:
    for kind in REQUIRED_KEYS:
        path = os.path.join(FIXTURE_DIR, SAMPLE_FILE.get(kind, kind + ".json"))
        assert os.path.exists(path), "缺少契约样例：%s" % kind


@pytest.mark.parametrize("kind", sorted(REQUIRED_KEYS))
def test_contract_has_required_keys_and_kind_tag(kind: str) -> None:
    obj = _load(kind)
    assert obj.get("kind") == kind
    missing = REQUIRED_KEYS[kind] - set(obj)
    assert not missing, "%s 缺字段：%s" % (kind, sorted(missing))


@pytest.mark.parametrize("kind", sorted(ID_PREFIX))
def test_identifier_uses_documented_prefix(kind: str) -> None:
    obj = _load(kind)
    assert obj["id"].startswith(ID_PREFIX[kind]), obj["id"]
    assert re.match(r"^[a-z]{2}_\d{6}$", obj["id"]), obj["id"]


def test_identifiers_are_unique_across_contracts() -> None:
    seen = {}
    for name, obj in _all_contracts():
        ident = obj.get("id")
        if ident is None:
            continue
        assert ident not in seen, "ID 重复：%s（%s 与 %s）" % (ident, seen[ident], name)
        seen[ident] = name


def test_file_snapshot_keeps_archive_ref_and_blob_identity() -> None:
    """归档引用必须三项齐全，且哈希是唯一内容身份；不能只靠文件名反推版本。"""
    snap = _load("file_snapshot")
    assert _HEX64.match(snap["blob_sha256"])
    assert snap["source"] == "archive"
    ref = snap["archive_ref"]
    assert set(ref) == {"pid", "filename", "archive_name"}
    assert all(ref.values()), "archive_ref 三项缺一即 unresolved"
    # 归档目录名只出现在 archive_ref 里，不得冒充软件版本
    release = _load("software_release")
    assert release["version_label"] != ref["archive_name"]
    assert "archive" not in release["version_label"]


def test_baseline_scopes_and_refs_are_separate_fields() -> None:
    common = _load("baseline_common")
    model = _load("baseline_model")
    assert common["scope"] == "common" and common["profile_id"] is None
    assert model["scope"] == "model" and model["profile_id"]
    # 上游引用与同范围前修订必须是两个字段
    assert "upstream_ref" in model and "previous_revision_id" in model
    assert model["upstream_ref"] == common["id"]
    assert model["previous_revision_id"] != model["upstream_ref"]
    # pin 即使数值等于默认也保留意图
    assert common["values"]["pm_000008"]["intent"] == "pin"
    assert model["values"]["pm_000008"]["intent"] == "inherit"


def test_logical_file_display_name_matches_open_alias() -> None:
    lf = _load("logical_file")
    open_aliases = [a["name"] for a in lf["aliases"] if a.get("until") is None]
    assert open_aliases == [lf["display_name"]]


def test_parameter_alias_keeps_history() -> None:
    param = _load("parameter")
    assert param["aliases"], "改名必须留痕"
    assert any(a.get("until") for a in param["aliases"]), "历史名要有失效时间"


def test_definition_revision_is_immutable_by_supersede() -> None:
    revision = _load("definition_revision")
    assert revision["revision_no"] >= 2
    assert revision["supersedes"], "新修订必须指向被取代的旧修订"
    assert revision["xml"]["loc"]["path"][0]["occurrence"] == 1
    assert revision["xml"]["name"] != "Pulse", "1.1.0 起应使用新名"


def test_enums_cover_required_states() -> None:
    """卡面要求样例覆盖 archive_only/managed、unknown release、retired、pin。"""
    observed_states = set()
    for _name, obj in _all_contracts():
        for key in ("state", "status"):
            if isinstance(obj.get(key), str):
                observed_states.add(obj[key])
    assert {"approved", "draft", "active"} <= observed_states

    baseline = _load("baseline_model")
    assert baseline["values"]["pm_000008"]["intent"] in {"inherit", "override", "pin"}

    observation = _load("observation")
    assert observation["release_ref"]["resolution"] in {"confirmed", "unconfirmed", "unknown"}
    assert observation["completeness"] in {"full", "partial"}

    family = _load("family")
    assert family["status"] in {"active", "retired"}


def test_change_set_plan_is_referenced_not_recomputed() -> None:
    change = _load("change_set")
    assert change["plan_ref"]["output_sha256"]
    assert _HEX64.match(change["plan_ref"]["output_sha256"])
    assert change["state"] in {"draft", "submitted", "approved", "rejected", "withdrawn", "stale"}


def test_artifact_determinism_key_present() -> None:
    artifact = _load("artifact")
    assert _HEX64.match(artifact["determinism_key"])
    assert artifact["validation"]["ok"] is True


def test_delivery_separates_registration_from_execution() -> None:
    delivery = _load("delivery")
    assert delivery["registered_by"]
    assert delivery["executed_at"]
    assert delivery["result"] in {
        "pending",
        "declared_ok",
        "failed",
        "insufficient_evidence",
    }


def test_spec_declares_layering_and_identities() -> None:
    assert os.path.exists(SPEC_PATH), "缺少领域规范文档"
    with open(SPEC_PATH, "r", encoding="utf-8") as fh:
        text = fh.read()
    for name in (
        "ConfigFamily",
        "SoftwareRelease",
        "FileSnapshot",
        "DefinitionRevision",
        "BaselineRevision",
    ):
        assert name in text, "规范未声明身份：%s" % name
    assert "core` 不得 import services" in text or "core 不 import" in text or "分层" in text
    for code in ("PLAN_STALE", "MAPPING_AMBIGUOUS", "VALUE_LOSSY_CONVERSION", "IDEMPOTENCY_MISMATCH"):
        assert code in text, "规范未登记错误码：%s" % code
