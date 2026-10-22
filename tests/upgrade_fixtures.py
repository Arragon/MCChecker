"""升级夹具：两个版本、同族模板、可复用的定义集合与批准辅助。

单独放一个模块而不是各测试各写一遍：M7-02 与 M7-03 的差别在"三方是谁"，
不在"怎么造数据"；把这层抽出来后，两个测试文件只关心各自的判据。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.services import (
    config_baselines as bl,
    config_catalog,
)

ACTOR = {"ip": "10.0.0.11", "name": "管理员丁"}

TEMPLATE_OLD = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<R xmlns="urn:test:axis">\n'
    '  <PulseWidth unit="ms">650</PulseWidth>\n'
    '  <MaxSpeed unit="mm/s">100</MaxSpeed>\n'
    '</R>\n'
).encode("utf-8")

TEMPLATE_NEW = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<R xmlns="urn:test:axis">\n'
    '  <PulseWidth unit="ms">700</PulseWidth>\n'
    '  <MaxSpeed unit="mm/s">120</MaxSpeed>\n'
    '</R>\n'
).encode("utf-8")

TEMPLATE_OTHER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<R xmlns="urn:test:io">\n'
    '  <Poll>10</Poll>\n'
    '</R>\n'
).encode("utf-8")


def _release(repo, *, version: str, template: bytes, family, logical, definition_ids) -> Dict[str, Any]:
    """一个版本：版本 + 快照 + 变体 + 默认批次 + 定义集合。"""
    release = config_catalog.register_release(
        repo, ACTOR, version_label=version, build="2026" + version.replace(".", "")
    )
    snapshot = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=release["id"],
        logical_file_id=logical["id"],
        data=template,
        original_filename="axis_%s.xml" % version,
    )
    variant = config_catalog.ensure_variant(
        repo,
        ACTOR,
        family_id=family["id"],
        release_id=release["id"],
        template_snapshot_id=snapshot["id"],
    )
    config_catalog.publish_default_batch(
        repo, ACTOR, release_id=release["id"], snapshot_ids=[snapshot["id"]]
    )
    definition_set = config_catalog.create_definition_set(
        repo,
        ACTOR,
        release_id=release["id"],
        definition_ids=definition_ids,
        variant_id=variant["id"],
    )
    return {
        "release": release,
        "snapshot": snapshot,
        "variant": variant,
        "definition_set": definition_set,
    }


def build(repo, *, versions=("1.0.0", "2.0.0")) -> Dict[str, Any]:
    """建族 + 两个参数 + 两个版本；返回全部句柄。"""
    family = config_catalog.create_family(repo, ACTOR, name="轴参数", code="AXIS_PARAM")
    logical = config_catalog.create_logical_file(
        repo, ACTOR, family_id=family["id"], role="xml_export", display_name="轴参数.xml"
    )
    pulse = config_catalog.create_parameter(
        repo, ACTOR, family_id=family["id"], name="脉冲宽度", category="时序"
    )
    speed = config_catalog.create_parameter(
        repo, ACTOR, family_id=family["id"], name="最大速度", category="运动"
    )
    pulse_def = config_catalog.define_parameter(
        repo,
        ACTOR,
        parameter_id=pulse["id"],
        xml_name="PulseWidth",
        xml_loc={"path": [{"name": "PulseWidth", "occurrence": 1}], "attribute": None},
        value_type="decimal",
        unit="ms",
    )
    speed_def = config_catalog.define_parameter(
        repo,
        ACTOR,
        parameter_id=speed["id"],
        xml_name="MaxSpeed",
        xml_loc={"path": [{"name": "MaxSpeed", "occurrence": 1}], "attribute": None},
        value_type="decimal",
        unit="mm/s",
    )
    definition_ids = [pulse_def["id"], speed_def["id"]]
    releases = {}
    for index, version in enumerate(versions):
        template = TEMPLATE_OLD if index == 0 else TEMPLATE_NEW
        releases[version] = _release(
            repo,
            version=version,
            template=template,
            family=family,
            logical=logical,
            definition_ids=definition_ids,
        )
    result = {
        "family": family,
        "logical": logical,
        "pulse": pulse,
        "speed": speed,
        "pulse_def": pulse_def,
        "speed_def": speed_def,
        "releases": releases,
    }
    result["old"] = releases[versions[0]]
    result["new"] = releases[versions[-1]]
    return result


def push(repo, baseline: Dict[str, Any], **overrides) -> Dict[str, Any]:
    """追加一条基线修订（可改状态/值），返回新修订。

    走仓储而不是走接口：造"已批准的旧基线"与"通用层又出了 r5"这两个前置条件，
    不应该依赖变更单状态机——否则状态机一改，升级测试就跟着红。
    """
    import copy

    payload = copy.deepcopy(baseline)
    payload.pop("id", None)
    payload.pop("revision", None)
    for key, value in overrides.items():
        payload[key] = value
    tx = repo.transaction(ACTOR, "测试用修订")
    ref = tx.put("baseline", payload, object_id=baseline["id"])
    tx.commit()
    return bl.get_baseline(repo, str(ref.id), ref.revision)


def approve_common(
    repo,
    world,
    *,
    values: Optional[Dict[str, Any]] = None,
    release: Optional[Dict[str, Any]] = None,
    approved: bool = True,
) -> Dict[str, Any]:
    """建一条已批准的旧版本通用基线（`values` 是覆盖项）。

    `release` 可换成 `world["new"]`，用来造"通用层在新版本、机型还在旧版本"
    这类需要版本错位的前置条件。
    """
    chosen = release or world["old"]
    baseline = bl.create_baseline(
        repo,
        ACTOR,
        scope="common",
        release_id=chosen["release"]["id"],
        definition_set_id=chosen["definition_set"]["id"],
        values=values or {},
        reason="旧版本通用基线",
    )
    if not approved:
        return baseline
    return push(repo, baseline, state=bl.STATE_APPROVED, decided_by=ACTOR["name"])


def approve_model(
    repo,
    world,
    *,
    profile_id: str,
    upstream: Dict[str, Any],
    values=None,
    release: Optional[Dict[str, Any]] = None,
    approved: bool = True,
):
    chosen = release or world["old"]
    baseline = bl.create_baseline(
        repo,
        ACTOR,
        scope="model",
        release_id=chosen["release"]["id"],
        definition_set_id=chosen["definition_set"]["id"],
        profile_id=profile_id,
        upstream_baseline_id=upstream["id"],
        values=values or {},
        reason="旧版本机型基线",
    )
    if not approved:
        return baseline
    return push(repo, baseline, state=bl.STATE_APPROVED, decided_by=ACTOR["name"])
