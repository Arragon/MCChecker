"""从已批准基线生成目标 XML（M6-13 / INH-1958）。

卡面验收逐条对应：

* 结果可重新解析、且**恰只在批准位置变化** → `test_only_approved_locations_change`
* 保留 namespace / 注释 / PI → `test_namespace_comments_and_pi_are_kept`
* no-op 直接拷模板 → `test_noop_is_byte_copy_of_template`
* 模板不兼容 / 需改空元素 / 不安全语义显式拒绝 → `test_missing_template_is_rejected` /
  `test_missing_location_is_reported` / `test_null_value_is_rejected`
* 不宣称逐字节保真 → `validation.warnings` 里必须有这句话
"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.errors import ARTIFACT_TEMPLATE_INCOMPATIBLE, INVALID_INPUT, AppError
from app.services import (
    config_baselines as bl,
    config_catalog,
    config_export,
    config_ingest,
)

ACTOR = {"ip": "10.0.0.11", "name": "管理员丁"}

TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<!-- 出口模板：注释必须保留 -->\n'
    '<R xmlns="urn:test:axis">\n'
    '  <PulseWidth unit="ms">650</PulseWidth>\n'
    '  <MaxSpeed unit="mm/s">100</MaxSpeed>\n'
    '  <!-- 段内注释 -->\n'
    '  <Axis>\n'
    '    <Name>X</Name>\n'
    '    <Limit>10</Limit>\n'
    '  </Axis>\n'
    '  <Axis>\n'
    '    <Name>Y</Name>\n'
    '    <Limit>20</Limit>\n'
    '  </Axis>\n'
    '</R>\n'
).encode("utf-8")


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


@pytest.fixture()
def world(repo):
    release = config_catalog.register_release(
        repo, ACTOR, version_label="1.0.0", build="20260801"
    )
    family = config_catalog.create_family(repo, ACTOR, name="轴参数", code="AXIS_PARAM")
    xml_file = config_catalog.create_logical_file(
        repo, ACTOR, family_id=family["id"], role="xml_export", display_name="轴参数.xml"
    )
    pulse = config_catalog.create_parameter(
        repo, ACTOR, family_id=family["id"], name="脉冲宽度", category="时序"
    )
    speed = config_catalog.create_parameter(
        repo, ACTOR, family_id=family["id"], name="最大速度", category="运动"
    )
    limit = config_catalog.create_parameter(
        repo, ACTOR, family_id=family["id"], name="Y 轴限位", category="运动"
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
        xml_loc={"path": [{"name": "MaxSpeed", "occurrence": 1}], "attribute": "unit"},
        value_type="text",
    )
    limit_def = config_catalog.define_parameter(
        repo,
        ACTOR,
        parameter_id=limit["id"],
        xml_name="Limit",
        xml_loc={
            "path": [{"name": "Axis", "occurrence": 2}, {"name": "Limit", "occurrence": 1}],
            "attribute": None,
        },
        value_type="decimal",
    )
    snapshot = config_catalog.register_snapshot(
        repo,
        ACTOR,
        release_id=release["id"],
        logical_file_id=xml_file["id"],
        data=TEMPLATE,
        original_filename="axis.xml",
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
        definition_ids=[pulse_def["id"], speed_def["id"], limit_def["id"]],
        variant_id=variant["id"],
    )
    baseline = bl.create_baseline(
        repo,
        ACTOR,
        scope="common",
        release_id=release["id"],
        definition_set_id=definition_set["id"],
        values={
            pulse["id"]: {"value": "650"},
            speed["id"]: {"value": "mm/s"},
            limit["id"]: {"value": "20"},
        },
        reason="初始基线",
    )
    return {
        "release": release,
        "family": family,
        "xml_file": xml_file,
        "pulse": pulse,
        "speed": speed,
        "limit": limit,
        "snapshot": snapshot,
        "variant": variant,
        "definition_set": definition_set,
        "baseline": baseline,
    }


def _approve(repo, baseline, values=None):
    """把基线推到 approved（必要时先改值）。

    走 `normalize_values` 而不是手工塞字符串：测试要造的是**服务端真实形状**
    （`{value:{type,text,unit}, intent, source_ref}`），手工拼容易拼出一个
    生产里不可能出现的载荷，然后测出一个假结论。
    """
    payload = dict(baseline)
    payload.pop("id", None)
    payload.pop("revision", None)
    if values:
        merged = dict(payload.get("values") or {})
        merged.update(
            bl.normalize_values(
                repo, values, definition_set_id=payload.get("definition_set_id")
            )
        )
        payload["values"] = merged
    payload.update({"state": "approved", "decided_by": "管理员丁"})
    tx = repo.transaction(ACTOR, "测试用批准", action="baseline.approve")
    ref = tx.put("baseline", payload, object_id=baseline["id"])
    tx.commit()
    return bl.get_baseline(repo, str(ref.id))


# -- 主路径 -----------------------------------------------------------------


def test_only_approved_locations_change(repo, world) -> None:
    approved = _approve(
        repo,
        world["baseline"],
        values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
    )
    result = config_export.generate(repo, baseline_id=approved["id"])
    produced = result["content"]

    assert produced != TEMPLATE
    assert result["filename"].endswith(".xml")
    assert len(result["edits"]) == 1, "只改了脉冲宽度一个位置"
    # loc 用的是**原始标签**（带命名空间花括号），与 `parser.to_compact` 同一口径：
    # `apply_edits` 认的就是这个字符串，换成去前缀写法反而会对不上。
    assert result["edits"][0]["loc"] == "/{urn:test:axis}R[1]/{urn:test:axis}PulseWidth[1]"
    assert result["edits"][0]["from"] == "650"
    assert result["edits"][0]["to"] == "700"

    # 重新解析：改的位置是新值，别的位置一个字没动
    root = config_ingest.parse_document(produced, "out.xml")["root"]
    from app.core import config_mapping

    ns = config_mapping.default_namespace(root)
    pulse_loc = config_mapping.to_xml_loc({"path": [{"name": "PulseWidth", "occurrence": 1}]})
    assert config_mapping.read_xml_value(root, pulse_loc, ns) == "700"
    axis2 = config_mapping.to_xml_loc(
        {"path": [{"name": "Axis", "occurrence": 2}, {"name": "Limit", "occurrence": 1}]}
    )
    assert config_mapping.read_xml_value(root, axis2, ns) == "20", "没批准的位置不许动"

    assert result["validation"]["ok"] is True
    assert "edits_applied" in result["validation"]["checks"]
    assert "structure_unchanged" in result["validation"]["checks"]
    assert any("逐字节保真" in text for text in result["validation"]["warnings"])


def test_namespace_comments_and_pi_are_kept(repo, world) -> None:
    approved = _approve(
        repo,
        world["baseline"],
        values={world["pulse"]["id"]: {"value": "800", "intent": "override"}},
    )
    produced = config_export.generate(repo, baseline_id=approved["id"])["content"]
    text = produced.decode("utf-8")
    assert "urn:test:axis" in text, "默认命名空间必须还在"
    assert "<!-- 出口模板：注释必须保留 -->" in text
    assert "<!-- 段内注释 -->" in text
    assert 'unit="ms"' in text, "没改的属性原样保留"


def test_attribute_location_is_written(repo, world) -> None:
    """定位到属性的参数（这里把 `MaxSpeed@unit` 改成 `m/s`）只改属性，不动元素文本。"""
    approved = _approve(
        repo,
        world["baseline"],
        values={world["speed"]["id"]: {"value": "m/s", "intent": "override"}},
    )
    result = config_export.generate(repo, baseline_id=approved["id"])
    edit = [item for item in result["edits"] if item["parameter_id"] == world["speed"]["id"]][0]
    assert edit["kind"] == "attribute"
    assert edit["loc"].endswith("MaxSpeed[1]/@unit")
    text = result["content"].decode("utf-8")
    assert '<MaxSpeed unit="m/s">100</MaxSpeed>' in text, "元素文本没被碰"


def test_noop_is_byte_copy_of_template(repo, world) -> None:
    approved = _approve(repo, world["baseline"])
    result = config_export.generate(repo, baseline_id=approved["id"])
    assert result["edits"] == []
    assert result["content"] == TEMPLATE, "没有改动就是模板的字节副本"
    assert any("字节副本" in text for text in result["validation"]["warnings"])


# -- 阻断 -------------------------------------------------------------------


def test_draft_baseline_cannot_generate(repo, world) -> None:
    with pytest.raises(AppError) as err:
        config_export.generate(repo, baseline_id=world["baseline"]["id"])
    assert err.value.code == INVALID_INPUT
    assert err.value.detail["state"] == "draft"


def test_missing_template_is_rejected(repo, world) -> None:
    """没有变体/模板的基线生成不了：骨架缺失不能靠"从值重建"绕过。

    造一个**全新版本**：没有默认批次、没有变体、没有定义集合——这样基线里的
    `variant_id` 才会真的为空（在 1.0.0 上建基线会自动挂到最新定义集合的变体）。
    """
    lonely_release = config_catalog.register_release(
        repo, ACTOR, version_label="2.0.0", build="20260901"
    )
    bare = bl.create_baseline(
        repo,
        ACTOR,
        scope="common",
        release_id=lonely_release["id"],
        values={world["pulse"]["id"]: {"value": "700"}},
        reason="无模板",
    )
    assert bare["variant_id"] is None
    approved = _approve(repo, bare)
    with pytest.raises(AppError) as err:
        config_export.generate(repo, baseline_id=approved["id"])
    assert err.value.code == ARTIFACT_TEMPLATE_INCOMPATIBLE
    codes = [item["code"] for item in err.value.detail["blockers"]]
    assert "template_missing" in codes
    assert all(item.get("message") for item in err.value.detail["blockers"]), "阻断项要带人话"


def test_missing_location_is_reported(repo, world) -> None:
    """定义声明的定位在模板里不存在（模板与定义集合不匹配）不能生成。"""
    ghost = config_catalog.create_parameter(
        repo, ACTOR, family_id=world["family"]["id"], name="幽灵参数", category="其它"
    )
    ghost_def = config_catalog.define_parameter(
        repo,
        ACTOR,
        parameter_id=ghost["id"],
        xml_name="Ghost",
        xml_loc={"path": [{"name": "Ghost", "occurrence": 1}], "attribute": None},
        value_type="decimal",
    )
    definition_set = config_catalog.create_definition_set(
        repo,
        ACTOR,
        release_id=world["release"]["id"],
        definition_ids=[ghost_def["id"]],
        variant_id=world["variant"]["id"],
    )
    with_ghost = bl.create_baseline(
        repo,
        ACTOR,
        scope="common",
        release_id=world["release"]["id"],
        definition_set_id=definition_set["id"],
        values={ghost["id"]: {"value": "1"}},
        reason="模板里没有这个节点",
    )
    approved = _approve(repo, with_ghost)
    with pytest.raises(AppError) as err:
        config_export.generate(repo, baseline_id=approved["id"])
    blockers = err.value.detail["blockers"]
    assert [item["code"] for item in blockers] == ["location_missing"]
    assert blockers[0]["parameter_id"] == ghost["id"]


def test_null_value_is_rejected(repo, world) -> None:
    """`null` 要写成空元素，属于改结构：显式拒绝，不偷偷做。"""
    approved = _approve(
        repo,
        world["baseline"],
        values={world["pulse"]["id"]: {"value": {"type": "null", "text": "", "unit": None},
                                       "intent": "override"}},
    )
    with pytest.raises(AppError) as err:
        config_export.generate(repo, baseline_id=approved["id"])
    assert err.value.code == ARTIFACT_TEMPLATE_INCOMPATIBLE
    assert [item["code"] for item in err.value.detail["blockers"]] == ["value_unsupported"]


# -- plan（只算不改） --------------------------------------------------------


def test_plan_reports_same_and_edits_without_touching_bytes(repo, world) -> None:
    approved = _approve(
        repo,
        world["baseline"],
        values={world["pulse"]["id"]: {"value": "700", "intent": "override"}},
    )
    planned = config_export.plan(repo, approved)
    assert planned["blockers"] == []
    assert [item["parameter_id"] for item in planned["edits"]] == [world["pulse"]["id"]]
    assert {item["parameter_id"] for item in planned["same"]} == {
        world["speed"]["id"],
        world["limit"]["id"],
    }
    assert planned["template"]["format"] == "xml"
    assert len(planned["template"]["sha256"]) == 64
