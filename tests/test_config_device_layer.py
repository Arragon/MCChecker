"""设备层功能关闭（M7-07 / M7-08 延期声明 / INH-1966、INH-1967）。

这两张卡按计划**整体延期**。延期不是"忘了做"，所以必须留下可验证的痕迹：

* 开关只有一个出处（`config_device_layer.ENABLED`），接口、状态页、上提入口读同一份
  （`test_single_switch_is_the_only_source_of_truth`）；
* 关闭时明确 `400 DEVICE_LAYER_DISABLED`，不是 404、更不是"空成功"
  （`test_absent_device_layer_is_reported_not_faked`）；
* **没有**设备目录/设备例外/device_rebase 的接口——不能让人以为能调
  （`test_no_device_endpoints_exist`）；
* 机型级配置不受影响（`test_model_level_stays_fully_usable`）；
* "关了什么/开着什么/怎么开"三件事一起给（`test_declaration_lists_all_three_sides`），
  这样 M8-07 文档可以直接引用它，验收也不会把"没有设备例外"说成"设备都一样"。
"""

from __future__ import annotations

import pytest

from app.core import config_repository as cr
from app.core.errors import DEVICE_LAYER_DISABLED, AppError
from app.server.router import build_router
from app.services import config_baselines as bl
from app.services import config_device_layer, config_promote
import upgrade_fixtures as fx

PROFILE = "MDL-A"


@pytest.fixture()
def repo(tmp_path):
    instance = cr.ConfigRepository(str(tmp_path))
    instance.initialize()
    return instance


def test_switch_is_off_and_never_silently_on() -> None:
    assert config_device_layer.ENABLED is False
    assert config_device_layer.enabled() is False
    # 别名与错误码同源：两处各写一个字符串，迟早对不上
    assert (
        config_promote.DEVICE_LAYER_DISABLED
        == config_device_layer.DEVICE_LAYER_DISABLED
        == DEVICE_LAYER_DISABLED
    )


def test_declaration_lists_all_three_sides() -> None:
    info = config_device_layer.describe()
    assert info["enabled"] is False
    assert info["status"] == "disabled"
    assert info["reason_code"] == "DEVICE_LAYER_DISABLED"
    assert info["deferred_cards"] == ["M7-07", "M7-08"]
    assert info["declared_in"] == "M8-07"
    # 只说"关了"会被读成"配置域只有一半能用"；只说"开着"会把没有例外当成都一样
    assert info["unavailable"], "要说清关了什么"
    assert any("经验上提" in text for text in info["available"]), "要说清机型级仍可用"
    assert len(info["enable_checklist"]) >= 5, "启用条件要逐条列出"


def test_require_enabled_raises_400_not_404() -> None:
    with pytest.raises(AppError) as caught:
        config_device_layer.require_enabled(device_id="DEV-1", device_baselines=2)
    assert caught.value.code == "DEVICE_LAYER_DISABLED"
    assert caught.value.http_status == 400
    assert caught.value.detail["field"] == "device_id"
    assert caught.value.detail["device_id"] == "DEV-1"
    assert caught.value.detail["device_baselines"] == 2
    assert caught.value.detail["declared_in"] == "M8-07"


def test_absent_device_layer_is_reported_not_faked(repo) -> None:
    """没有设备层时不许返回"设备没有例外"这种空成功。"""
    world = fx.build(repo)
    common = fx.approve_common(repo, world)
    fx.approve_model(repo, world, profile_id=PROFILE, upstream=common)
    with pytest.raises(AppError) as caught:
        config_promote.plan_promote_to_model(repo, profile_id=PROFILE, device_id="DEV-9")
    assert caught.value.code == "DEVICE_LAYER_DISABLED"
    assert caught.value.detail["device_id"] == "DEV-9"
    assert "机型级配置完整可用" in caught.value.detail["note"]


def test_no_device_endpoints_exist() -> None:
    """设备目录/设备例外/device_rebase 一律没有入口——不能让人以为能调。"""
    import re

    from app.config import Settings

    router = build_router(
        Settings(host="127.0.0.1", port=0, data_dir="/tmp/dev", web_dir="/tmp", trusted_proxies=())
    )
    param = re.compile(r"\(\?P<([A-Za-z_][A-Za-z0-9_]*)>\[\^/\][^)]*\)")
    paths = [param.sub(r"{\1}", regex.pattern.strip("^$")) for _m, regex, _n, _h in router._routes]
    assert paths, "路由表不该是空的"
    leaked = [path for path in paths if "/config/devices" in path or "device_rebase" in path]
    assert leaked == [], "设备层关闭期间不该有设备接口：%s" % leaked
    # 但"设备 → 机型"的入口留着：留着才能回答"为什么不能做"
    assert any("/config/promote/model" in path for path in paths)


def test_model_level_stays_fully_usable(repo) -> None:
    """关掉设备层不影响机型级：建基线、批准、上提计划都正常。"""
    world = fx.build(repo)
    common = fx.approve_common(repo, world)
    model = fx.approve_model(
        repo,
        world,
        profile_id=PROFILE,
        upstream=common,
        values={world["pulse"]["id"]: {"value": "900", "intent": "override"}},
    )
    assert model["scope"] == bl.SCOPE_MODEL
    plan = config_promote.plan_promote_to_common(repo, profile_id=PROFILE)
    assert plan["can_apply"] is True
    assert plan["eligible"] == [world["pulse"]["id"]]


def test_status_endpoint_exposes_the_switch() -> None:
    """状态页与代码读同一份：前端不必自己写死"设备层关着"。"""
    from authz_fixtures import as_role, make_server, seed_roles

    from app.services import config_gate

    import os

    previous = os.environ.get(config_gate.ENV_KEY)
    os.environ[config_gate.ENV_KEY] = "on"
    try:
        with make_server() as server:
            seed_roles("p1")
            admin = as_role(server, "admin")
            status, _h, body = admin.json("GET", "/api/v1/config/status")
            assert status == 200, body
            layer = body["device_layer"]
            assert layer["enabled"] is False
            assert layer["status"] == "disabled"
            assert layer["declared_in"] == "M8-07"
    finally:
        if previous is None:
            os.environ.pop(config_gate.ENV_KEY, None)
        else:
            os.environ[config_gate.ENV_KEY] = previous
