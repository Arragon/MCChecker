"""M4-02（INH-1904）安全硬化与回归：§16.8 四条 + §11.3/11.4/11.5 + §4.2 无旁路。

口径（每张写卡复用，故写成可执行的）：

1. **§16.8 第 1 条（XSS）**：`<img src=x onerror=alert(1)>` 作标签/值/备注/工具名，
   服务端一律当**数据**送出去（`application/json`，字节原样、不 HTML 转义——转义是
   渲染层的活，在服务端做会把权威值改坏），并且全站带 §11.3 的 CSP（`script-src 'self'`
   让内联 `onerror` 即使漏进 DOM 也执行不了）。**「以文本显示、不执行」在 DOM 上的正证
   在 `app/web/tests/tests.js`**，必须按该文件顶部钉死的 HTTP 命令跑（`file://` 不算）。
2. **§16.8 第 2 条（工具 URL）**：`javascript:` 必须在**保存点**被拒。本卡基线（`cecb58a`）
   上 `/tools` 写接口**还不存在**（路由表实测无 `/tools`，属 M3-15 INH-1900 交付），
   所以「保存点校验」这条不变量落在当前唯一真实存在的 URL 型写接口
   `POST/PATCH /sources` 上（`services/updates._validated_url`）；渲染侧由
   `MC.util.safeUrl()` 兜（tests.js 覆盖）。工具接口自己的保存侧拒绝须由 M3-15 自带，
   本卡的 `test_tools_write_endpoint_is_still_absent` 是它的哨兵。
3. **§16.8 第 3 条（路径穿越）**：`..%2F..%2Fetc%2Fpasswd`、`C%3A%5Cwin.ini` → **400/403**，
   且根外文件一个字节都读不到。
4. **§16.8 第 4 条（CSRF）**：无 `X-Requested-With` 的写请求 → 403 **且不落盘**。
   每个写接口族都要有，并且必须证明「带上头就过得去」——否则无关角色的 403 会让
   用例在 CSRF 守卫被删掉后照样绿。

另执法 §11.5（错误响应不含绝对路径与堆栈）与 §4.2（**没有任何写接口能绕过角色校验**：
扫全量路由表，新增未登记的写接口直接判失败）。

风格照 `tests/test_api_authz.py`：真实 `MCServer` + 真实 HTTP，不 mock 传输层。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import urllib.parse

import pytest
from authz_fixtures import as_role, make_server, seed_roles, write_target

from app.config import Settings
from app.core import fsutil, storage
from app.core.errors import FORBIDDEN, INVALID_INPUT, AppError
from app.server.http import CSRF_HEADER, CSRF_VALUE
from app.server.router import build_router

XSS = "<img src=x onerror=alert(1)>"
FILE_A = "a.xml"
XML_V1 = b"<root><a>1</a></root>"
SENTINEL = "OUTSIDE-DATA-ROOT-SECRET"

#: 收藏/备注需要**真实存在**的节点，形状与 `tests/test_api_favorites.py` 的语料一致。
FAV_DOC = (
    '<R>\n'
    '  <params>\n'
    '    <param_01 description="宽度" min="0" max="10">5</param_01>\n'
    '    <item>first</item>\n'
    '  </params>\n'
    '</R>\n'
)
FAV_NODE_ID = "root/R/params/param_01"
#: 定位符以服务端文档为准（形状见 `tests/test_api_remarks.py`）。
FAV_NODE_LOC = "/R[1]/params[1]/param_01[1]"
#: 语料里必须写 XML 转义后的形态，文件才解析得开；解析后送出去的才是真正
#: 的 `<img …>`，也就顺带证明了服务端不会替渲染层加转义。
XSS_DOC = (
    '<R>\n'
    '  <params>\n'
    '    <param_01 description="&lt;img src=x onerror=alert(1)&gt;" min="0" max="10">'
    '&lt;img src=x onerror=alert(1)&gt;</param_01>\n'
    '    <item>first</item>\n'
    '  </params>\n'
    '</R>\n'
).encode("utf-8")

WEB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "web"
)

#: §11.3 原文规定的 CSP，逐字对齐（`default-src 'self'`，不得放宽成 `*`）。
SPEC_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
)

#: (方法, 路由模板) → 该接口的权限档（`authz.ACTIONS` 的键）；`None` = 所有角色可用（§4.2）。
WRITE_ROUTE_ACTIONS = {
    ("POST", "/api/v1/p/{pid}/temp"): None,
    ("POST", "/api/v1/p/{pid}/favorites"): None,
    ("PATCH", "/api/v1/p/{pid}/favorites"): None,
    ("DELETE", "/api/v1/p/{pid}/favorites"): None,
    ("POST", "/api/v1/p/{pid}/remarks"): None,
    ("POST", "/api/v1/p/{pid}/compare"): None,
    ("POST", "/api/v1/p/{pid}/import-url"): None,
    ("POST", "/api/v1/p/{pid}/review/submit"): "review",
    ("DELETE", "/api/v1/p/{pid}/files/{name}"): "delete_file",
    ("PUT", "/api/v1/p/{pid}/files/{name}"): "upload_persist",
    ("POST", "/api/v1/p/{pid}/sources"): "settings_write",
    ("PATCH", "/api/v1/p/{pid}/sources/{name}"): "settings_write",
    ("DELETE", "/api/v1/p/{pid}/sources/{name}"): "settings_write",
    ("PUT", "/api/v1/p/{pid}/schedule"): "settings_write",
    # M3-11（INH-1896）：DL 条件保存，仅部署者（§7.11）。If-Match 由 handler 从
    # `RequestContext.headers` 读取；缺失/过期都是 409，不改变「写接口必须表态权限档」这条对账。
    ("PUT", "/api/v1/p/{pid}/dl/{item}"): "settings_write",
    # 同为 M3-11：compute / extract 是 POST 但纯计算、只预览（R20 同口径「明确不递增」），
    # 全部角色可用，所以登记为 None——不登记会让上面的双向对账直接红。
    ("POST", "/api/v1/p/{pid}/dl/{item}/compute"): None,
    ("POST", "/api/v1/p/{pid}/dl/{item}/extract"): None,
    # 系数与整份参数的保存、应用历史对所有角色开放（含访客）。
    # 删除历史只给管理员，且不改当前正在使用的参数。
    ("POST", "/api/v1/p/{pid}/dl/{item}/coeffs"): None,
    ("POST", "/api/v1/p/{pid}/dl/{item}/coeff-history/{hid}/apply"): None,
    ("DELETE", "/api/v1/p/{pid}/dl/{item}/coeff-history/{hid}"): "settings_write",
    # M3-13（INH-1898）：人员两张表的写接口全部只有部署者（§7.9）。
    ("PUT", "/api/v1/p/{pid}/ip-mapping/{ip}"): "settings_write",
    ("DELETE", "/api/v1/p/{pid}/ip-mapping/{ip}"): "settings_write",
    ("POST", "/api/v1/p/{pid}/admins"): "settings_write",
    ("DELETE", "/api/v1/p/{pid}/admins/{name}"): "settings_write",
    # M3-14（INH-1899）：变量绑定写接口只有部署者（§8.3）。
    ("PUT", "/api/v1/p/{pid}/bindings/{group}"): "settings_write",
    ("DELETE", "/api/v1/p/{pid}/bindings/{group}"): "settings_write",
    # M3-15（INH-1900）：外部工具写接口只有部署者（§8.3）。
    ("PUT", "/api/v1/p/{pid}/tools/{name}"): "settings_write",
    ("DELETE", "/api/v1/p/{pid}/tools/{name}"): "settings_write",
    # M3-16（INH-1901）：机型注册表写接口只有部署者（§7.2）。
    ("POST", "/api/v1/profiles"): "settings_write",
    ("PATCH", "/api/v1/profiles/{pid}"): "settings_write",
    ("DELETE", "/api/v1/profiles/{pid}"): "settings_write",
    # M7-12：软退役/恢复与配置类别生命周期；退役机型仍可读。
    ("POST", "/api/v1/profiles/{pid}/retire"): "settings_write",
    ("POST", "/api/v1/profiles/{pid}/restore"): "settings_write",
    # M9-02（INH-2119）：V3 workspace 元数据写接口，仅管理员。
    ("POST", "/api/v3/workspaces"): "settings_write",
    ("PATCH", "/api/v3/workspaces/{wid}"): "settings_write",
    ("POST", "/api/v3/workspaces/{wid}/profiles"): "settings_write",
    ("PATCH", "/api/v3/workspaces/{wid}/profiles/{pid}"): "settings_write",
    ("DELETE", "/api/v3/workspaces/{wid}/profiles/{pid}"): "settings_write",
    ("POST", "/api/v3/workspaces/{wid}/profiles/{pid}/configs"): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/releases",
    ): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/parameters",
    ): "config_registry_write",
    (
        "PATCH",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/parameters/{parameter_id}",
    ): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/releases/{rid}/bindings",
    ): "config_registry_write",
    (
        "PATCH",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/drafts/{did}/values",
    ): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/drafts/{did}/freeze",
    ): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/releases/{rid}/baseline-drafts",
    ): "config_registry_write",
    (
        "PATCH",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/baseline-drafts/{did}/values",
    ): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/baseline-drafts/{did}/publish",
    ): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/machine-branches",
    ): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/machines/{mid}/drafts",
    ): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/machines/{mid}/publish",
    ): "config_registry_write",
    # M13-05：提取计划与逐行决议只改计划，不改参数，但仍是管理员写操作。
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/extraction-plans",
    ): "config_registry_write",
    (
        "PATCH",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/extraction-plans/{plan_id}/decisions",
    ): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/extraction-plans/{plan_id}/recompute",
    ): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/extraction-plans/{plan_id}/commit",
    ): "config_registry_write",
    ("POST", "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/merge-plans"): "config_registry_write",
    (
        "PATCH",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/merge-plans/{plan_id}/decisions",
    ): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/merge-plans/{plan_id}/commit",
    ): "config_registry_write",
    ("POST", "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/export-plans"): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/export-plans/{plan_id}/commit",
    ): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/restore-plans",
    ): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/restore-plans/{plan_id}/undo",
    ): "config_registry_write",
    ("POST", "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/value-import-plans"): "config_registry_write",
    (
        "PATCH",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/value-import-plans/{plan_id}/decisions",
    ): "config_registry_write",
    (
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/value-import-plans/{plan_id}/commit",
    ): "config_registry_write",
    ("POST", "/api/v1/config/families/{fid}/retire"): "config_registry_write",
    ("POST", "/api/v1/config/families/{fid}/reactivate"): "config_registry_write",
    ("POST", "/api/v1/p/{pid}/tasks"): "tasks_run",
    # M6-01/M6-02（INH-1946/1947）：配置目录的写接口只有管理员（`config_registry_write`）。
    # 这些 handler **先授权后门禁**，所以未授权身份得到的是 403（detail.action 可解释），
    # 而不是把"特性是否开启"泄露出去的 503。
    ("POST", "/api/v1/config/releases"): "config_registry_write",
    ("POST", "/api/v1/config/releases/{rid}/default"): "config_registry_write",
    ("POST", "/api/v1/config/releases/{rid}/retire"): "config_registry_write",
    ("POST", "/api/v1/config/families"): "config_registry_write",
    ("POST", "/api/v1/config/families/{fid}/files"): "config_registry_write",
    ("PATCH", "/api/v1/config/files/{lid}"): "config_registry_write",
    ("POST", "/api/v1/config/files/{lid}/retire"): "config_registry_write",
    ("POST", "/api/v1/config/snapshots"): "config_registry_write",
    ("POST", "/api/v1/config/variants"): "config_registry_write",
    # M6-03/M6-04（INH-1948/1949）：参数登记写接口，同样只有管理员。
    ("POST", "/api/v1/config/parameters"): "config_registry_write",
    ("PATCH", "/api/v1/config/parameters/{pmid}"): "config_registry_write",
    ("POST", "/api/v1/config/parameters/{pmid}/definitions"): "config_registry_write",
    ("POST", "/api/v1/config/definitions/{did}/retire"): "config_registry_write",
    ("POST", "/api/v1/config/definitions/{did}/verify"): "config_registry_write",
    ("POST", "/api/v1/config/definition-sets"): "config_registry_write",
    # M6-05（INH-1950）：追踪策略是**独立的管理动作**，不复用旧的 settings_write。
    ("POST", "/api/v1/config/tracking"): "config_tracking_admin",
    ("POST", "/api/v1/config/observations"): "config_registry_write",
    ("POST", "/api/v1/config/observations/{oid}/remap"): "config_registry_write",
    ("POST", "/api/v1/config/baselines"): "config_change_submit",
    ("POST", "/api/v1/config/baselines/{bid}/values"): "config_change_submit",
    ("POST", "/api/v1/config/baselines/{bid}/intent"): "config_change_submit",
    # M6-11：变更单的提交面用 `config_change_submit`
    ("POST", "/api/v1/p/{pid}/config/changes"): "config_change_submit",
    ("POST", "/api/v1/p/{pid}/config/changes/{cid}"): "config_change_submit",
    ("POST", "/api/v1/p/{pid}/config/changes/{cid}/submit"): "config_change_submit",
    ("POST", "/api/v1/p/{pid}/config/changes/{cid}/withdraw"): "config_change_submit",
    ("POST", "/api/v1/p/{pid}/config/changes/{cid}/reopen"): "config_change_submit",
    # M6-12：批准/驳回是**独立**的能力键，提交面拿不到
    ("POST", "/api/v1/p/{pid}/config/changes/{cid}/approve"): "config_change_approve",
    ("POST", "/api/v1/p/{pid}/config/changes/{cid}/reject"): "config_change_approve",
    # M6-14：生成产物是独立的写动作，与提交/批准分开
    ("POST", "/api/v1/p/{pid}/config/artifacts"): "config_artifact_generate",
    # M7-04：升级落地产出的是**基线草稿**，与建基线同一个能力键
    ("POST", "/api/v1/config/upgrade/common"): "config_change_submit",
    ("POST", "/api/v1/p/{pid}/config/upgrade/model"): "config_change_submit",
    ("POST", "/api/v1/p/{pid}/config/upgrade/rebase"): "config_change_submit",
    # M7-05：上提产出的是**变更单**；设备层入口虽关闭，权限口径不能松
    ("POST", "/api/v1/p/{pid}/config/promote/common"): "config_change_submit",
    ("POST", "/api/v1/p/{pid}/config/promote/model"): "config_change_submit",
    # M7-06：回滚/迁移/刷新都直接落对象
    ("POST", "/api/v1/config/baselines/{bid}/restore"): "config_change_submit",
    ("POST", "/api/v1/config/baselines/{bid}/migrate"): "config_change_submit",
    ("POST", "/api/v1/config/baselines/{bid}/reissue"): "config_change_submit",
    # M7-09/M7-10：交付登记与回读核验属于"交付这个动作"，与生成产物同一个键
    ("POST", "/api/v1/p/{pid}/config/deliveries"): "config_artifact_generate",
    ("POST", "/api/v1/p/{pid}/config/artifacts/{aid}/verifications"): "config_artifact_generate",
}

ROUTE_VALUES = {
    "pid": "p1", "name": FILE_A, "token": "tk", "id": "t1", "ip": "10.7.7.7",
    "item": "pp_energy", "group": "g1", "hid": "ch_000001",
    "wid": "w-00000001",
    # 配置域对象 ID（M6-01/02）：形状必须是 `<prefix>_<6 位>`，否则会先撞上 ID 校验
    # 而不是权限矩阵——这里要验的是"权限先于存在性"。
    "rid": "sr_000001", "fid": "cf_000001", "lid": "lf_000001",
    "pmid": "pm_000001", "did": "dr_000001", "dsid": "ds_000001",
    "oid": "ob_000001", "tpid": "tp_000001", "bid": "bl_000001",
    "cid": "cs_000001", "aid": "ar_000001", "parameter_id": "p_000001",
    "did": "d0-rel_000001", "bid": "bl_000001", "mid": "m_000001",
    "plan_id": "ep_000001",
}

#: 想读数据根之外哨兵的形态：编码分隔符、点段、Windows 绝对路径、NUL、二次编码。
TRAVERSALS = [
    "..%2F..%2Fetc%2Fpasswd",
    "..%2f..%2f..%2fsentinel",
    "%2e%2e%2f%2e%2e%2fsentinel",
    "%2E%2E%2F%2E%2E%2Fsentinel",
    "..%5C..%5Cwindows%5Cwin.ini",
    "C%3A%5Cwin.ini",
    "C%3A%2F%2Fwindows%2Fwin.ini",
    "..%2Fsentinel%00.xml",
    "%00sentinel",
    "..%252F..%252Fsentinel",
]

#: 二次编码那一条单独处理：`_dispatch` 只解一层，`%252F` 解出来是**字面** `%2F`，
#: 在 URL 路径里它不构成层级，在请求头里它是个合法字面文件名。分发层仍按 §16.8 判 400
#: （见 `_ENCODED_UNSAFE` 对解码后那份的检查），但 `X-Filename` 那条走不到分发层——
#: 它只能由服务层的 `validate_filename` 判，而那里它确实不是穿越。见下面两个用例。
DOUBLE_ENCODED = "..%252F..%252Fsentinel"
SINGLE_ENCODED_TRAVERSALS = [t for t in TRAVERSALS if t != DOUBLE_ENCODED]


def route_table():
    """生产路由表 → [((method, 模板路径), handler)]；模板由正则反推回 `{pid}` 形状。"""
    settings = Settings(
        host="127.0.0.1", port=0, data_dir=storage.DATA_DIR, web_dir=WEB_DIR
    )
    out = []
    for method, regex, _names, handler in build_router(settings)._routes:
        template = re.sub(
            r"\(\?P<(\w+)>\[\^/\]\+\)", lambda m: "{%s}" % m.group(1), regex.pattern
        ).strip("^$")
        out.append(((method, template), handler))
    return out


def url_of(template: str, **overrides) -> str:
    values = dict(ROUTE_VALUES)
    values.update(overrides)
    return template.format(**values)


def csrf_error(payload) -> bool:
    """响应是不是 **CSRF 守卫** 拦下的那个 403（角色 403 的消息不含头名，不会混）。"""
    if isinstance(payload, (bytes, str)):
        try:
            payload = json.loads(payload)
        except ValueError:
            return False
    if not isinstance(payload, dict):
        return False
    error = payload.get("error") or {}
    return error.get("code") == FORBIDDEN and CSRF_HEADER in str(error.get("message", ""))


def outside_sentinel() -> str:
    """在数据根（`tmp_path/data`）**之外**放一个哨兵；读到位即证明越界。"""
    root = storage.DATA_DIR
    outside = os.path.join(os.path.dirname(root), "sentinel")
    with open(outside, "w", encoding="utf-8") as fh:
        fh.write(SENTINEL)
    return outside


def sentinel_untouched(outside: str) -> None:
    assert os.path.isfile(outside)
    with open(outside, "r", encoding="utf-8") as fh:
        assert fh.read() == SENTINEL


def config_sha(pid: str, name: str):
    with storage.use_profile(pid):
        path = storage.get_config_path(name)
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()


def schedule_state(pid: str):
    """`schedule.json` 的 (内容 sha256, mtime_ns)——CSRF 用例要钉「一个字节都没动」，
    单看内容 hash 挡不住「同长度原地改写」，所以把 mtime 一起取。文件不存在返回 None。
    """
    path = storage.get_schedule_file(pid)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        raw = fh.read()
    return hashlib.sha256(raw).hexdigest(), os.stat(path).st_mtime_ns


def people_state(pid: str):
    """`ip_mapping.json` + `admin_users.json` 的 (内容 sha256, mtime_ns) 两份（M3-13 / §16.8 第 4 条）。

    口径与 `schedule_state` 一致：单看内容 hash 挡不住「同长度原地改写」，所以 mtime 一起取；
    文件不存在返回 None。**这两份文件决定谁能写**，所以「CSRF 403 不落盘」值得单独钉。
    """
    out = {}
    for label, path in (
        ("ip_mapping", storage.get_ip_mapping_file(pid)),
        ("admin_users", storage.get_admin_users_file(pid)),
    ):
        if not os.path.isfile(path):
            out[label] = None
            continue
        with open(path, "rb") as fh:
            raw = fh.read()
        out[label] = (hashlib.sha256(raw).hexdigest(), os.stat(path).st_mtime_ns)
    return out


# ======================================================================
# §16.8 第 1 条：注入串以文本送达、不可执行
# ======================================================================


class TestInjectedMarkupStaysData:
    def test_document_label_and_value_are_delivered_as_json_text(self):
        """§16.8 第 1 条的「标签/值」：注入串来自**上传的语料**，不是请求体。

        语料里存的是 XML 转义形态，解析后 API 必须送出未转义的原文——服务端一加转义
        就把权威值改坏了（前端要的是文本节点，见 tests.js）。
        """
        pid = seed_roles("sec1")
        write_target(pid, FILE_A, XSS_DOC)
        with make_server() as server:
            caller = as_role(server, "member")
            status, headers, body = caller.request(
                "GET", "/api/v1/p/%s/files/%s/document" % (pid, FILE_A)
            )
            assert status == 200, body
            assert headers["Content-Type"] == "application/json; charset=utf-8"
            payload = json.loads(body.decode("utf-8"))
            assert XSS in payload["nodes"]["v"], payload["nodes"]["v"]
            text = body.decode("utf-8")
            # 只解码一次：双重转义（`&amp;lt;`）或残留实体（`&lt;img`）都算改坏权威值。
            assert "&amp;lt;" not in text and "&lt;img" not in text, text[:400]

    def test_remark_proposed_value_round_trips_verbatim_as_json(self):
        pid = seed_roles("sec1")
        write_target(pid, FILE_A, FAV_DOC.encode("utf-8"))
        with make_server() as server:
            caller = as_role(server, "visitor")
            _s, _h, doc = caller.json(
                "GET", "/api/v1/p/%s/files/%s/document" % (pid, FILE_A)
            )
            status, headers, payload = caller.json(
                "POST",
                "/api/v1/p/%s/remarks" % pid,
                body={
                    "file": FILE_A,
                    "sha256": doc["file"]["sha256"],
                    "loc": FAV_NODE_LOC,
                    "proposed_value": XSS,
                },
            )
            assert status == 201, payload
            assert headers["Content-Type"].startswith("application/json")
            assert payload["item"]["proposed_value"] == XSS
            assert "&lt;" not in json.dumps(payload, ensure_ascii=False)
            status, _h, listed = caller.json("GET", "/api/v1/p/%s/remarks" % pid)
            assert status == 200
            assert XSS in json.dumps(listed, ensure_ascii=False)

    def test_favorite_note_round_trips_verbatim(self):
        """收藏的 `label` 是服务端从文档快照取的（客户端给不了），所以「工具名/备注」
        这一类自由文本在收藏侧的正主是 `note`（§7.7 的 PATCH）。"""
        pid = seed_roles("sec1")
        write_target(pid, FILE_A, FAV_DOC.encode("utf-8"))
        with make_server() as server:
            caller = as_role(server, "member")
            url = "/api/v1/p/%s/favorites" % pid
            status, _h, payload = caller.json(
                "POST", url, body={"file": FILE_A, "id": FAV_NODE_ID}
            )
            assert status == 201, payload
            status, _h, patched = caller.json(
                "PATCH", url, body={"file": FILE_A, "path": FAV_NODE_ID, "note": XSS}
            )
            assert status == 200, patched
            assert patched["item"]["note"] == XSS
            status, _h, listed = caller.json("GET", url)
            assert status == 200
            assert XSS in json.dumps(listed, ensure_ascii=False)

    def test_data_is_never_served_as_html(self):
        """§11.3：数据只走 `application/json`；`text/html` 通道只有生产页本身。"""
        pid = seed_roles("p1")
        with make_server() as server:
            for path in (
                "/api/v1/session",
                "/api/v1/p/%s/files" % pid,
                "/api/v1/p/%s/favorites" % pid,
                "/api/v1/p/%s/sources" % pid,
            ):
                status, headers, _ = as_role(server, "visitor").request("GET", path)
                assert status == 200, (path, status)
                assert headers["Content-Type"] == "application/json; charset=utf-8", path

    def test_error_envelope_is_json_too(self):
        seed_roles("sec1")
        with make_server() as server:
            status, headers, _ = as_role(server, "visitor").request("GET", "/api/v1/nope")
            assert status == 404
            assert headers["Content-Type"] == "application/json; charset=utf-8"


# ======================================================================
# §11.3：全站安全响应头（此前 http.py:52 注明「其余安全头策略……在 M4-02」）
# ======================================================================


class TestSecurityResponseHeaders:
    @pytest.mark.parametrize(
        "path",
        [
            "/",
            "/index.html",
            "/assets/js/core/util.js",
            "/assets/css/base.css",
            "/config.js",
            "/api/v1/session",
        ],
    )
    def test_headers_present_on_every_surface(self, path):
        seed_roles("sec1")
        with make_server() as server:
            status, headers, _ = as_role(server, "visitor").request("GET", path)
            assert status == 200, (path, status)
            assert headers.get("X-Content-Type-Options") == "nosniff", path
            assert headers.get("X-Frame-Options") == "DENY", path
            assert headers.get("Referrer-Policy") == "same-origin", path
            assert headers.get("Content-Security-Policy") == SPEC_CSP, path

    @pytest.mark.parametrize("method,path", [
        ("POST", "/api/v1/p/p1/favorites"),
        ("DELETE", "/api/v1/p/p1/favorites"),
        ("PUT", "/api/v1/p/p1/files/nope.xml"),
    ])
    def test_headers_present_on_error_responses(self, method, path):
        seed_roles("sec1")
        with make_server() as server:
            status, headers, _ = as_role(server, "visitor").request(
                method, path, body=b"", with_csrf=False
            )
            assert status == 403, (path, status)
            assert headers.get("Content-Security-Policy") == SPEC_CSP
            assert headers.get("X-Content-Type-Options") == "nosniff"

    def test_not_modified_still_carries_headers(self):
        """304 没有 body，是最容易漏掉头的一条响应。"""
        seed_roles("sec1")
        with make_server() as server:
            _s, headers, _b = as_role(server, "visitor").request("GET", "/index.html")
            status, got, _ = as_role(server, "visitor").request(
                "GET", "/index.html", headers={"If-None-Match": headers["ETag"]}
            )
            assert status == 304
            assert got.get("Content-Security-Policy") == SPEC_CSP
            assert got.get("X-Content-Type-Options") == "nosniff"

    def test_csp_allows_no_wildcard_and_no_inline_script(self):
        seed_roles("sec1")
        with make_server() as server:
            _s, headers, _b = as_role(server, "visitor").request("GET", "/")
            csp = headers["Content-Security-Policy"]
            assert "default-src 'self'" in csp
            assert "*" not in csp
            script = csp.split("script-src")[1].split(";")[0]
            assert script.strip() == "'self'", script

    def test_served_page_carries_nothing_that_csp_would_block(self):
        """CSP 能收紧的**前提**校验：浏览器**收到**的那份文档里没有 CSP 会拦的东西。

        断的是 HTTP 响应体（`_dispatch` 交付 `app/web/index.html` 之后客户端实际拿到的
        字节），不是磁盘上的文件（R45：拿源码文本当断言对象是穿测试衣服的 lint；而且
        「交付出去的这一份」才是浏览器要执行的那一份）。有人往生产页加内联 `<script>`
        或 `onclick="…"`，生产页会在浏览器里当场瞎掉，而本仓没有任何其它用例读这份文档
        ——所以这条必须在这里钉住。
        """
        seed_roles("sec1")
        with make_server() as server:
            status, headers, body = as_role(server, "visitor").request("GET", "/index.html")
            assert status == 200
            assert headers["Content-Security-Policy"] == SPEC_CSP
            html = body.decode("utf-8")
            tags = re.findall(r"<script[^>]*>", html, re.I)
            assert tags, "生产页至少要加载脚本"
            for tag in tags:
                assert "src=" in tag, tag
            assert not re.search(r"\son[a-z]+\s*=\s*[\"']", html, re.I)
            assert "<style" not in html.lower(), "内联样式表同样归 style-src 管"

    def test_dev_page_that_needs_inline_script_is_not_served(self):
        """§8.9 的放行条件：全站 CSP 与「测试页有内联脚本」不冲突，因为生产不交付它。

        `app/web/tests/index.html` 第 24 行是一行内联 `<script>`（mock 开关），在
        `script-src 'self'` 下必被拦（控制实验实测：给同一份副本打上 §11.3 的 CSP，
        Chrome 报 `Executing inline script violates … 'script-src 'self''`，页面 7 FAILED）。
        所以「生产页能过 CSP」这条依赖 `/tests/` 与 `/assets/js/mock/` 一直是 404；
        有人放开那道闸门，本条当场红，而不是等浏览器里才发现。
        """
        seed_roles("sec1")
        with make_server() as server:
            for path in ("/tests/index.html", "/assets/js/mock/mock-api.js", "/template.html"):
                status, _h, _b = as_role(server, "visitor").request("GET", path)
                assert status == 404, (path, status)

    def test_served_css_needs_no_webfont_and_no_remote_source(self):
        """`default-src 'self'` 没配 `font-src`：本应用的字体是**系统字体栈**。

        `tokens.css` 的 `--font-ui` / `--font-mono` 只列本地字体名（-apple-system、
        PingFang SC、Consolas…），不发网络请求，所以既不需要 `font-src`，更不需要把
        `default-src` 放宽成 `*`。有人引入 webfont 或远程图片时这条会红——正确动作是
        加一条精确的 `font-src`/`img-src` 来源，而不是动 `default-src`。
        """
        seed_roles("sec1")
        with make_server() as server:
            for name in ("base.css", "components.css", "views.css", "tokens.css"):
                status, _h, body = as_role(server, "visitor").request(
                    "GET", "/assets/css/%s" % name
                )
                assert status == 200, (name, status)
                css = body.decode("utf-8")
                assert "@font-face" not in css, name
                assert not re.search(r"url\(\s*['\"]?(https?:)?//", css, re.I), name


class TestSecurityHeadersOnTransportLevelResponses:
    """§11.3「全站」的两个旁路：不经过 `_dispatch` 的响应同样必须带头。

    501（未实现的方法）与 400（畸形请求行）由标准库 `send_error` 发出，原先绕开了
    `_send_bytes` 这个唯一出口，实测只回 `Server/Date/Content-Type: text/html` 一段
    HTML 错误页、四个安全头一个都没有。摘掉 `MCRequestHandler.send_error`，这两条必红。
    """

    def _raw(self, server, payload: bytes):
        sock = socket.create_connection(("127.0.0.1", server.server_port), timeout=8)
        try:
            sock.sendall(payload)
            sock.settimeout(8)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if sum(len(c) for c in chunks) > 65536:
                    break
            return b"".join(chunks)
        finally:
            sock.close()

    def test_unsupported_method_is_501_with_headers_and_json_envelope(self):
        seed_roles("sec1")
        with make_server() as server:
            raw = self._raw(
                server, b"OPTIONS /api/v1/session HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
            )
            head, _, body = raw.partition(b"\r\n\r\n")
            status_line = head.split(b"\r\n", 1)[0]
            assert b"501" in status_line, status_line
            lowered = head.lower()
            assert b"x-content-type-options: nosniff" in lowered, head
            assert b"x-frame-options: deny" in lowered, head
            assert b"referrer-policy: same-origin" in lowered, head
            assert SPEC_CSP.encode() in lowered, head
            # 不再是标准库那段把请求行回显进 text/html 的错误页
            assert b"application/json" in lowered, head
            assert b"<!DOCTYPE" not in body, body[:120]
            payload = json.loads(body.decode("utf-8"))
            assert payload["error"]["code"] == INVALID_INPUT, payload
            assert payload["error"]["detail"] == {}, payload

    def test_oversized_request_line_is_414_with_headers(self):
        """414 也走 `send_error` 旁路，而这一次版本可读 ⇒ 头必须带上（第二条旁路证据）。"""
        seed_roles("sec1")
        with make_server() as server:
            raw = self._raw(
                server, b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\nHost: x\r\n\r\n"
            )
            head = raw.split(b"\r\n\r\n", 1)[0]
            assert b"414" in head.split(b"\r\n", 1)[0], head[:200]
            lowered = head.lower()
            assert b"x-content-type-options: nosniff" in lowered, head[:400]
            assert SPEC_CSP.encode() in lowered, head[:400]

    def test_unparsable_version_leaves_the_response_without_headers_by_protocol(self):
        """如实记录的边界：请求行读不出 HTTP/1.x 版本时，§11.3 的头**发不出去**。

        机制（不是猜的）：`parse_request()` 把 `self.request_version` 的初值留成
        `default_request_version`（= `HTTP/0.9`），而 `send_response_only()` 对 0.9
        「按协议不写任何响应头」。所以畸形请求行（400）与不支持的版本（505）的响应里
        根本没有头的位置——那是 HTTP/0.9 没有响应头这回事，不是本卡留了旁路。
        这里能钉住的两件事：响应体已经是本仓的 JSON 封套（不再是标准库那段把请求行
        回显进 `text/html` 的错误页），且 501/505 不许挂 `STORAGE_FAILURE`
        （那个码前端按「服务器内部错误」呈现，而坏的是请求）。
        """
        seed_roles("sec1")
        with make_server() as server:
            for bad_line in (b"GARBAGE-LINE\r\n\r\n", b"GET / HTTP/9.9\r\n\r\n"):
                raw = self._raw(server, bad_line)
                # 0.9 形态：连状态行与头都没有，收到的第一个字节就是响应体
                assert raw.startswith(b"{"), raw[:120]
                assert b"<!DOCTYPE" not in raw, raw[:120]
                payload = json.loads(raw.decode("utf-8"))
                assert payload["error"]["code"] == INVALID_INPUT, payload
                assert payload["error"]["detail"] == {}, payload



# ======================================================================
# §16.8 第 2 条：URL 协议白名单在保存点执法
# ======================================================================

BAD_URLS = [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    " javascript:alert(1)",
    "java\tscript:alert(1)",
    "javascript:alert(1)//",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "vbscript:msgbox(1)",
    "ftp://example.com/x",
    "file:///etc/passwd",
    "//10.0.0.1/x",
    "/relative/path",
]


class TestUrlSchemeRejectedAtSave:
    @pytest.mark.parametrize("bad", BAD_URLS)
    def test_source_url_is_rejected_before_it_is_written(self, bad):
        """保存点拒绝：400，且 `GET /sources` 与之前逐字节一致。"""
        pid = seed_roles("sec2")
        with make_server() as server:
            caller = as_role(server, "deployer")
            _s, _h, before = caller.json("GET", "/api/v1/p/%s/sources" % pid)
            status, _h, payload = caller.json(
                "POST",
                "/api/v1/p/%s/sources" % pid,
                body={"name": "坏源", "url": bad, "record_url": ""},
            )
            assert status == 400, (bad, status, payload)
            assert payload["error"]["detail"]["field"] == "url"
            _s, _h, after = caller.json("GET", "/api/v1/p/%s/sources" % pid)
            assert after == before, "被拒的 URL 绝不能落到权威文件里"

    @pytest.mark.parametrize("bad", BAD_URLS)
    def test_source_record_url_is_rejected_too(self, bad):
        pid = seed_roles("sec2")
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _h, payload = caller.json(
                "POST",
                "/api/v1/p/%s/sources" % pid,
                body={"name": "坏源", "url": "http://10.0.0.9/x.xml", "record_url": bad},
            )
            assert status == 400, (bad, status, payload)
            assert payload["error"]["detail"]["field"] == "record_url"

    def test_http_and_https_are_accepted(self):
        """正向对照：白名单不是「一律 400」，否则上面两组断言恒真。"""
        pid = seed_roles("sec2")
        with make_server() as server:
            caller = as_role(server, "deployer")
            for index, good in enumerate(("http://10.0.0.9/a.xml", "https://example.com/b.xml")):
                status, _h, payload = caller.json(
                    "POST",
                    "/api/v1/p/%s/sources" % pid,
                    body={"name": "好源%d" % index, "url": good, "record_url": ""},
                )
                assert status == 201, (good, status, payload)

    def test_patch_cannot_smuggle_a_bad_url_into_an_existing_source(self):
        pid = seed_roles("sec2")
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _h, payload = caller.json(
                "POST",
                "/api/v1/p/%s/sources" % pid,
                body={"name": "good", "url": "http://10.0.0.9/a.xml", "record_url": ""},
            )
            assert status == 201, payload
            status, _h, payload = caller.json(
                "PATCH",
                "/api/v1/p/%s/sources/good" % pid,
                body={"name": "good", "url": "javascript:alert(1)"},
            )
            assert status == 400, payload
            _s, _h, listed = caller.json("GET", "/api/v1/p/%s/sources" % pid)
            assert listed["items"][0]["url"] == "http://10.0.0.9/a.xml"

    def test_import_url_scheme_is_rejected_without_touching_network(self):
        pid = seed_roles("sec2")
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, payload = caller.json(
                "POST",
                "/api/v1/p/%s/import-url" % pid,
                body={"url": "javascript:alert(1)"},
            )
            assert status == 400, payload

    @pytest.mark.parametrize(
        "bad",
        [
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "ftp://example.com/x",
            "",
            "example.com/x",
            "http://",
        ],
    )
    def test_tools_url_scheme_is_rejected_at_save(self, bad):
        """§16.8 第 2 条：外部工具的保存点拒绝非 http/https 与无主机 URL。

        这条取代了 M3-15 之前的「写接口还不存在」哨兵（接口已落地，哨兵按计划删除）。
        """
        pid = seed_roles("sec2")
        with make_server() as server:
            caller = as_role(server, "deployer")
            status, _h, payload = caller.json(
                "PUT",
                "/api/v1/p/%s/tools/x" % pid,
                body={"name": "x", "description": "", "url": bad},
            )
            assert status == 400, (bad, payload)
            assert payload["error"]["code"] == "INVALID_INPUT", (bad, payload)
            assert payload["error"]["detail"]["field"] == "url", (bad, payload)
        assert not os.path.exists(storage.get_tools_file(pid)), "拒绝的 URL 不得落盘"


# ======================================================================
# §16.8 第 3 条：路径穿越 → 400/403，且根外字节读不到
# ======================================================================


class TestPathContainment:
    @pytest.mark.parametrize("payload", TRAVERSALS)
    def test_file_name_parameter_is_400_or_403_and_reads_nothing_outside(self, payload):
        pid = seed_roles("sec3")
        write_target(pid, FILE_A, XML_V1)
        outside = outside_sentinel()
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = caller.request(
                "GET", "/api/v1/p/%s/files/%s/raw" % (pid, payload)
            )
            assert status in (400, 403), (payload, status, body)
            assert SENTINEL.encode() not in body, payload
            sentinel_untouched(outside)

    def test_positive_control_raw_read_inside_root_works(self):
        """正向对照：同一条 URL 换成合法文件名必须 200，否则上面的断言是恒真。"""
        pid = seed_roles("sec3")
        write_target(pid, FILE_A, XML_V1)
        with make_server() as server:
            caller = as_role(server, "member")
            status, headers, body = caller.request(
                "GET", "/api/v1/p/%s/files/%s/raw" % (pid, FILE_A)
            )
            assert status == 200
            assert body == XML_V1
            assert headers["Content-Disposition"].startswith("attachment")
            assert headers["X-Content-Type-Options"] == "nosniff"

    @pytest.mark.parametrize("payload", TRAVERSALS)
    def test_document_parameter_has_the_same_contract(self, payload):
        pid = seed_roles("sec3")
        write_target(pid, FILE_A, XML_V1)
        outside = outside_sentinel()
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = caller.request(
                "GET", "/api/v1/p/%s/files/%s/document" % (pid, payload)
            )
            assert status in (400, 403), (payload, status)
            assert SENTINEL.encode() not in body
            sentinel_untouched(outside)

    @pytest.mark.parametrize("payload", TRAVERSALS)
    def test_static_asset_traversal_is_400_or_403(self, payload):
        """`/assets/` 走 `fsutil.resolve_within`（M1-06 建的），§16.8 要求越界给 400/403。"""
        outside = outside_sentinel()
        seed_roles("sec3")
        with make_server() as server:
            status, _h, body = as_role(server, "visitor").request(
                "GET", "/assets/%s" % payload
            )
            assert status in (400, 403), (payload, status)
            assert SENTINEL.encode() not in body
            sentinel_untouched(outside)

    @pytest.mark.parametrize("payload", TRAVERSALS)
    def test_root_path_traversal_is_400_or_403(self, payload):
        outside = outside_sentinel()
        seed_roles("sec3")
        with make_server() as server:
            status, _h, body = as_role(server, "visitor").request("GET", "/%s" % payload)
            assert status in (400, 403), (payload, status)
            assert SENTINEL.encode() not in body
            sentinel_untouched(outside)

    @pytest.mark.parametrize("payload", TRAVERSALS)
    def test_temp_token_traversal_is_rejected(self, payload):
        pid = seed_roles("sec3")
        write_target(pid, FILE_A, XML_V1)
        outside = outside_sentinel()
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = caller.request(
                "GET", "/api/v1/p/%s/temp/%s/raw" % (pid, payload)
            )
            assert status in (400, 403), (payload, status)
            assert SENTINEL.encode() not in body
            sentinel_untouched(outside)

    @pytest.mark.parametrize("payload", SINGLE_ENCODED_TRAVERSALS)
    def test_version_query_traversal_is_rejected(self, payload):
        """`version=archive:<穿越>`：查询串不是 URL 路径，分发层那一条管不到它，
        执法点是服务层的 `validate_filename` + `resolve_within`（M1-06 建的）。"""
        pid = seed_roles("sec3")
        write_target(pid, FILE_A, XML_V1)
        outside = outside_sentinel()
        with make_server() as server:
            caller = as_role(server, "member")
            # 服务端看到的是解过一道之后的形状（`archive:../../x`），否则测的是
            # 「一个不存在的归档文件名」而不是穿越。
            version = urllib.parse.quote("archive:" + urllib.parse.unquote(payload), safe=":")
            status, _h, body = caller.request(
                "GET", "/api/v1/p/%s/files/%s/document?version=%s" % (pid, FILE_A, version)
            )
            # 查询串走不到分发层那条 URL 守卫，所以这里**必须**是服务层的 400
            # （`validate_filename`）；写成 `in (400, 403)` 的话把那一关摘掉也照样绿
            # （只剩 `resolve_within` 的 403 兜底），负控 3c 实测过这个区别。
            assert status == 400, (payload, status, body[:160])
            assert SENTINEL.encode() not in body
            sentinel_untouched(outside)

    def test_double_encoded_version_is_a_literal_name_and_cannot_escape(self):
        """如实记录的边界：`version=archive:..%252F..%252Fsentinel` 解一道后是**字面**
        `%2F`，不构成层级，服务层把它当一个不存在的归档文件名（404）。它出不了根，
        所以不变量成立；但 §16.8 第 3 条的 400/403 只在 URL 路径参数那条路上执法。
        """
        pid = seed_roles("sec3")
        write_target(pid, FILE_A, XML_V1)
        outside = outside_sentinel()
        with make_server() as server:
            caller = as_role(server, "member")
            version = urllib.parse.quote(
                "archive:" + urllib.parse.unquote(DOUBLE_ENCODED), safe=":"
            )
            status, _h, body = caller.request(
                "GET", "/api/v1/p/%s/files/%s/document?version=%s" % (pid, FILE_A, version)
            )
            assert status == 404, (status, body)
            assert SENTINEL.encode() not in body
            sentinel_untouched(outside)

    @pytest.mark.parametrize("payload", SINGLE_ENCODED_TRAVERSALS)
    def test_x_filename_header_traversal_is_rejected(self, payload):
        """C26：`X-Filename` 先百分号解码，合法性只能在服务层判——越界必须 400/403。

        `X-Filename` 是请求头，到不了分发层的 URL 检查，所以这一族完全依赖
        `validate_filename`；正对照（合法名 201）在下面另一条里。
        """
        pid = seed_roles("sec3")
        outside = outside_sentinel()
        with make_server() as server:
            caller = as_role(server, "visitor")
            status, _h, body = caller.request(
                "POST",
                "/api/v1/p/%s/temp" % pid,
                body=b"<r/>",
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Filename": payload,
                },
            )
            assert status in (400, 403), (payload, status)
            assert SENTINEL.encode() not in body
            sentinel_untouched(outside)

    def test_x_filename_positive_control_and_double_encoded_name_stay_inside_root(self):
        """正向对照 + 已知边界：`X-Filename` 的正常名字必须能存（否则上一条恒真），
        而二次编码的名字会**当成字面文件名**收下——它出不了临时区，但不算被拒绝。

        这是如实记录，不是放行：临时区文件名由 `temp_store` 经根内路径落盘，
        §16.8 第 3 条要的状态码只在 URL 路径参数那条路上执法（分发层）。
        """
        pid = seed_roles("sec3")
        outside = outside_sentinel()
        with make_server() as server:
            caller = as_role(server, "visitor")
            status, _h, payload = caller.request(
                "POST",
                "/api/v1/p/%s/temp" % pid,
                body=b"<r/>",
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Filename": "normal.xml",
                },
            )
            assert status == 201, (status, payload)

            status, _h, payload = caller.request(
                "POST",
                "/api/v1/p/%s/temp" % pid,
                body=b"<r/>",
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Filename": DOUBLE_ENCODED,
                },
            )
            assert status == 201, (status, payload)
            # 临时区是根下**全局**一层（`data/temporary/<token>/`，见 temp_store 头注释），
            # 不随机型走，所以这里从 storage 取它自己的路径而不是拼字符串。
            temp_root = storage.get_temporary_dir()
            hits = []
            for dirpath, _dirnames, filenames in os.walk(temp_root):
                hits.extend(os.path.join(dirpath, name) for name in filenames)
            assert hits, "临时文件没落在临时区里"
            real_root = os.path.realpath(temp_root)
            for path in hits:
                assert os.path.realpath(path).startswith(real_root), path
            sentinel_untouched(outside)

    @pytest.mark.skipif(
        os.name == "nt",
        reason="CI 的仓库在 D:、tmp_path 在 C:，ntpath.relpath 无法跨盘构造逃逸路径",
    )
    def test_literal_dot_segments_never_reach_outside_the_root(self):
        """未编码的 `../`：静态层把它当「不存在」是**刻意**的不暴露目录结构
        （`http.py:340`，M1-06 的决定，`test_api_http.py` 也钉着 404）。这里不跟它抢
        状态码，只钉不变量：根外字节一律读不到。

        路径用 `relpath` 现算，不写死 `..` 的层数——负控 3b 的教训：手写的
        `/assets/../../../../etc/passwd` 其实落在 `projects/etc/passwd` 上，**本来就不
        存在**，所以那条断言在 `resolve_within` 被摘掉后照样绿（自我感觉良好的装饰）。
        """
        outside = outside_sentinel()
        pid = seed_roles("sec3")
        write_target(pid, FILE_A, XML_V1)
        # 从 web 根里的 assets 出发，走到数据根之外那个哨兵，需要多少 `../` 算多少。
        escape = os.path.relpath(outside, os.path.join(WEB_DIR, "assets"))
        assert escape.startswith(".."), escape
        with make_server() as server:
            caller = as_role(server, "member")
            for path in (
                "/assets/" + escape,
                "/assets/" + escape + ".xml",
                "/api/v1/p/%s/files/../../sentinel/raw" % pid,
                "/api/v1/p/%s/files/..%%2F..%%2Fsentinel/raw" % pid,
            ):
                status, _h, body = caller.request("GET", path)
                assert status in (400, 403, 404), (path, status, body[:120])
                assert SENTINEL.encode() not in body, path
            sentinel_untouched(outside)

    @pytest.mark.skipif(os.name != "posix", reason="符号链接只在 POSIX 上可建（Windows 部署另计）")
    def test_symlink_inside_the_root_still_cannot_leave_it(self):
        """`fsutil.resolve_within` 的**专属**证据。

        文件名完全合法（过得了 `validate_filename`），只有 realpath 才识破它指向根外——
        摘掉 `resolve_within`（负控 3b）这一条必须红，否则前面那些 `%2F` 用例全被
        分发层的新守卫挡在前面，根包含这一关等于没测。
        """
        pid = seed_roles("sec3")
        outside = outside_sentinel()
        write_target(pid, FILE_A, XML_V1)  # 先让 configs/ 目录真的存在
        with storage.use_profile(pid):
            link = os.path.join(storage.get_configs_dir(pid), "escape.xml")
            os.symlink(outside, link)
        with make_server() as server:
            caller = as_role(server, "member")
            status, _h, body = caller.request(
                "GET", "/api/v1/p/%s/files/escape.xml/raw" % pid
            )
            assert status in (400, 403), (status, body[:160])
            assert SENTINEL.encode() not in body
        sentinel_untouched(outside)


# ======================================================================
# §16.8 第 4 条：无 X-Requested-With 的跨站写请求 → 403 且不落盘
# ======================================================================

#: (方法, 模板, **该接口本来就有权的角色**, body, 是否需要预置文件)
WRITE_FAMILIES = [
    ("POST", "/api/v1/p/{pid}/favorites", "visitor", {"file": FILE_A, "id": "1", "label": "L"}, True),
    ("PATCH", "/api/v1/p/{pid}/favorites", "visitor", {"file": FILE_A, "id": "1", "note": "n"}, True),
    ("DELETE", "/api/v1/p/{pid}/favorites?file=a.xml&path=root", "visitor", None, True),
    ("POST", "/api/v1/p/{pid}/remarks", "member", {"file": FILE_A}, True),
    ("POST", "/api/v1/p/{pid}/temp", "member", b"<r/>", False),
    ("POST", "/api/v1/p/{pid}/import-url", "member", {"url": "javascript:alert(1)"}, False),
    ("POST", "/api/v1/p/{pid}/review/submit", "member", {}, True),
    ("POST", "/api/v1/p/{pid}/sources", "deployer", {"name": "n", "url": "http://x/y"}, False),
    ("PATCH", "/api/v1/p/{pid}/sources/{name}", "deployer", {"url": "http://x/y"}, False),
    ("DELETE", "/api/v1/p/{pid}/sources/{name}", "deployer", None, False),
    ("POST", "/api/v1/p/{pid}/tasks", "deployer", {"type": "update", "target": FILE_A}, True),
    ("PUT", "/api/v1/p/{pid}/schedule", "deployer", {"enabled": True, "interval_hours": 24}, False),
    # M3-13（INH-1898）：两张人员表的写接口。有权的角色是部署者，所以「删掉 CSRF 守卫必红」
    # 在这一族上成立（不是靠别人的权限 403 蒙过去）。
    ("PUT", "/api/v1/p/{pid}/ip-mapping/{ip}", "deployer", {"ip": "10.7.7.7", "name": "跨站改的人"}, False),
    ("DELETE", "/api/v1/p/{pid}/ip-mapping/{ip}", "deployer", None, False),
    ("POST", "/api/v1/p/{pid}/admins", "deployer", {"name": "跨站加的人"}, False),
    ("DELETE", "/api/v1/p/{pid}/admins/{name}", "deployer", None, False),
    # M3-14（INH-1899）：变量绑定。body 故意给出两个有效变量，让带头的对照请求越过
    # CSRF 后落到业务校验（文件不存在也只会是 400，不是 403）。
    ("PUT", "/api/v1/p/{pid}/bindings/{group}", "deployer", {"group_name": "跨站组", "variables": [{"file": FILE_A, "path": FILE_A + "/R"}, {"file": FILE_A, "path": FILE_A + "/R/params"}]}, False),
    ("DELETE", "/api/v1/p/{pid}/bindings/{group}", "deployer", None, False),
    # M3-15（INH-1900）：外部工具。body 的 URL 合法，带头的对照请求会越过 CSRF 落到业务层。
    ("PUT", "/api/v1/p/{pid}/tools/{name}", "deployer", {"name": "跨站工具", "description": "", "url": "https://example.com/x"}, False),
    ("DELETE", "/api/v1/p/{pid}/tools/{name}", "deployer", None, False),
    # M3-16（INH-1901）：机型注册表。POST 无 pid；PATCH/DELETE 的 pid 由 seed_roles 提供。
    ("POST", "/api/v1/profiles", "deployer", {"name": "跨站机型", "copy_from": None}, False),
    ("PATCH", "/api/v1/profiles/{pid}", "deployer", {"name": "跨站改名"}, False),
    ("DELETE", "/api/v1/profiles/{pid}", "deployer", None, False),
    ("POST", "/api/v1/profiles/{pid}/retire", "deployer", {"reason": "跨站退役"}, False),
    ("POST", "/api/v1/profiles/{pid}/restore", "deployer", {}, False),
    ("POST", "/api/v1/config/families/{fid}/retire", "deployer", {"reason": "跨站退役", "acknowledge_refs": True}, False),
    ("POST", "/api/v1/config/families/{fid}/reactivate", "deployer", {}, False),
    ("PUT", "/api/v1/p/{pid}/files/{name}", "deployer", XML_V1, True),
    ("DELETE", "/api/v1/p/{pid}/files/{name}", "admin", None, True),
    # M3-11（INH-1896）：DL 条件保存。body 留空也能证明 CSRF 闸门在 handler 之前
    # （带头的对照请求得到 409/400 都不算 CSRF 403）。
    ("PUT", "/api/v1/p/{pid}/dl/{item}", "deployer", {}, False),
]


class TestCsrfOnEveryWriteFamily:
    @pytest.mark.parametrize("method,template,role,body,needs_file", WRITE_FAMILIES)
    def test_missing_header_is_403_even_for_the_privileged_role(
        self, method, template, role, body, needs_file
    ):
        """用**该接口本来就有权的角色**发请求：这样删掉 CSRF 守卫必定红，
        而不是靠无关角色的权限 403 蒙过去。带上头必须不再命中 CSRF 那条消息。"""
        pid = seed_roles("sec4")
        if needs_file:
            write_target(pid, FILE_A, XML_V1)
        with make_server() as server:
            caller = as_role(server, role)
            url = url_of(template, pid=pid)

            status, headers, raw = caller.request(method, url, body=body, with_csrf=False)
            assert status == 403, (method, url, raw)
            assert csrf_error(raw), (method, url, raw)
            assert headers["Content-Type"] == "application/json; charset=utf-8"

            # 正向对照：同一个请求补上头，CSRF 这一关必须放它过去。
            status2, _h2, payload2 = caller.request(method, url, body=body)
            assert not csrf_error(payload2), (method, url, status2, payload2)

    def test_wrong_header_value_is_403(self):
        pid = seed_roles("sec4")
        with make_server() as server:
            caller = as_role(server, "deployer")
            for bad in ("", "XMLHttpRequest", "mcchecker", CSRF_VALUE + " "):
                status, _h, raw = caller.request(
                    "POST",
                    url_of("/api/v1/p/{pid}/sources", pid=pid),
                    body={"name": "n", "url": "http://x/y"},
                    headers={CSRF_HEADER: bad},
                )
                assert status == 403, (bad, status)
                assert csrf_error(raw), bad

    def test_put_target_file_is_untouched_by_a_csrf_403(self):
        """M4-02 卡面「403 **且不写文件**」：先证明带头的同类请求确实会改数据，
        再证明不带头的那次一个字节都没动——否则「没改」是恒真。"""
        pid = seed_roles("sec4")
        before = write_target(pid, FILE_A, XML_V1)
        replacement = b"<root><a>2</a></root>"
        assert hashlib.sha256(replacement).hexdigest() != before
        with make_server() as server:
            caller = as_role(server, "deployer")
            # `overwrite=1`：同名文件已存在时 PUT 是 409，正向对照必须真的写下去。
            url = url_of("/api/v1/p/{pid}/files/{name}", pid=pid) + "?overwrite=1"
            octet = {"Content-Type": "application/octet-stream"}

            status, _h, raw = caller.request(
                "PUT", url, body=replacement, headers=octet, with_csrf=False
            )
            assert status == 403 and csrf_error(raw), raw
            assert config_sha(pid, FILE_A) == before, "CSRF 403 之后权威文件被改写了"

            status, _h, raw = caller.request("PUT", url, body=replacement, headers=octet)
            assert status in (200, 201), raw
            assert config_sha(pid, FILE_A) != before

    def test_sources_list_is_untouched_by_a_csrf_403(self):
        pid = seed_roles("sec4")
        with make_server() as server:
            caller = as_role(server, "deployer")
            url = url_of("/api/v1/p/{pid}/sources", pid=pid)
            body = {"name": "n", "url": "http://10.0.0.9/y.xml", "record_url": ""}

            _s, _h, before = caller.json("GET", url)
            status, _h, raw = caller.request("POST", url, body=body, with_csrf=False)
            assert status == 403 and csrf_error(raw), raw
            _s, _h, after = caller.json("GET", url)
            assert after == before

            status, _h, payload = caller.json("POST", url, body=body)
            assert status == 201, payload
            _s, _h, grown = caller.json("GET", url)
            assert len(grown["items"]) == len(before["items"]) + 1

    def test_favorites_write_is_untouched_by_a_csrf_403(self):
        """收藏是 §4.2 里「所有角色都能写」的那一族：它的 403 不落盘也要单独钉。"""
        pid = seed_roles("sec4")
        write_target(pid, FILE_A, FAV_DOC.encode("utf-8"))
        fav_url = url_of("/api/v1/p/{pid}/favorites", pid=pid)
        with make_server() as server:
            caller = as_role(server, "visitor")
            _s, _h, empty = caller.json("GET", fav_url)
            assert empty["groups"] == [], empty

            status, _h, raw = caller.request(
                "POST", fav_url, body={"file": FILE_A, "id": FAV_NODE_ID}, with_csrf=False
            )
            assert status == 403 and csrf_error(raw), raw
            _s, _h, still = caller.json("GET", fav_url)
            assert still == empty, "CSRF 403 之后收藏被写进去了"

            status, _h, payload = caller.json(
                "POST", fav_url, body={"file": FILE_A, "id": FAV_NODE_ID}
            )
            assert status == 201, payload
            _s, _h, grown = caller.json("GET", fav_url)
            assert grown["groups"], "带头的请求没真写出收藏，上面的「没写」断言就是恒真"

    def test_schedule_file_is_untouched_by_a_csrf_403(self):
        """M3-08 合入的 `PUT /schedule`（§7.9）：CSRF 403 必须一字节不落盘。

        形状照上面的 PUT files 与 sources 两例——先落一份基线，再证明不带头的
        请求让 `schedule.json` 的内容 hash 与 mtime 都纹丝不动，最后用**带头的
        同类请求**做正向对照；没有正向对照，「没改」可能只是恒真。
        """
        pid = seed_roles("sec4")
        url = url_of("/api/v1/p/{pid}/schedule", pid=pid)
        with make_server() as server:
            caller = as_role(server, "deployer")

            # 基线：带头写一份，文件这才存在。
            status, _h, payload = caller.json(
                "PUT", url, body={"enabled": True, "interval_hours": 24}
            )
            assert status == 200, payload
            assert payload["interval_hours"] == 24, payload
            before = schedule_state(pid)
            assert before is not None, "基线 schedule.json 没落下来，下面的「没改」是恒真"

            # 不带头再发一次：权限够（部署者），只缺 CSRF 头；载荷的两个字段都与基线相反/
            # 不同，所以它要是真写下去了，字节必变。
            replacement = {"enabled": False, "interval_hours": 48}
            status, _h, raw = caller.request("PUT", url, body=replacement, with_csrf=False)
            assert status == 403, raw
            assert csrf_error(raw), raw
            assert schedule_state(pid) == before, "CSRF 403 之后定时设置被改写了"

            # 正向对照：同一个请求补上头，必须真的把 schedule.json 改掉。
            status, _h, payload = caller.json("PUT", url, body=replacement)
            assert status == 200, payload
            after = schedule_state(pid)
            assert after is not None and after[0] != before[0], (
                "带头的请求没真写出定时设置，上面的「没改」断言就是恒真"
            )
            assert after[1] != before[1], after

    def test_people_files_are_untouched_by_a_csrf_403(self):
        """M3-13 合入的 `/ip-mapping`、`/admins`（§7.9）：CSRF 403 必须一字节不落盘。

        这两份文件是**权限面**（登记表等于改谁能写、名单决定谁能审阅），所以除了上面的
        「不带头 403」，这里还要证明「403 之后文件纹丝不动」，并且照 `test_schedule_file_…`
        的形状补上**带头的正向对照**——没有正向对照，「没改」可能只是恒真。
        """
        pid = seed_roles("sec4")
        ip_url = url_of("/api/v1/p/{pid}/ip-mapping/{ip}", pid=pid)
        admin_url = url_of("/api/v1/p/{pid}/admins", pid=pid)
        with make_server() as server:
            caller = as_role(server, "deployer")
            # 基线：带头各写一次，两份文件这才存在。
            assert caller.json("PUT", ip_url, body={"ip": "10.7.7.7", "name": "基线甲"})[0] == 200
            assert caller.json("POST", admin_url, body={"name": "基线管理员"})[0] == 201
            before = people_state(pid)
            assert before["ip_mapping"] is not None and before["admin_users"] is not None, (
                "基线没落下来，下面的「没改」是恒真"
            )

            for method, url, body in (
                ("PUT", ip_url, {"ip": "10.7.7.7", "name": "跨站改掉的名字"}),
                ("DELETE", url_of("/api/v1/p/{pid}/ip-mapping/{ip}", pid=pid), None),
                ("POST", admin_url, {"name": "跨站加的人"}),
                ("DELETE", url_of("/api/v1/p/{pid}/admins/{name}", pid=pid), None),
            ):
                status, _h, raw = caller.request(method, url, body=body, with_csrf=False)
                assert status == 403, (method, url, status, raw)
                assert csrf_error(raw), (method, url, raw)
            assert people_state(pid) == before, "CSRF 403 之后人员文件被改写了"

            # 正向对照：带头的同类请求必须真的改得动。
            assert caller.json("PUT", ip_url, body={"ip": "10.7.7.7", "name": "带头改的名字"})[0] == 200
            after = people_state(pid)
            assert after["ip_mapping"][0] != before["ip_mapping"][0], (
                "带头的 PUT 没真写出 IP 对应表，上面的「没改」断言就是恒真"
            )
            assert after["ip_mapping"][1] != before["ip_mapping"][1], after

            before_admin = people_state(pid)["admin_users"]
            assert caller.json("POST", admin_url, body={"name": "带头加的人"})[0] == 201
            assert people_state(pid)["admin_users"][0] != before_admin[0], (
                "带头的 POST 没真写出管理员名单，上面的「没改」断言就是恒真"
            )

    @pytest.mark.parametrize("path", [
        "/api/v1/session",
        "/api/v1/p/{pid}/files",
        "/api/v1/p/{pid}/favorites",
        "/api/v1/p/{pid}/sources",
        "/api/v1/p/{pid}/tasks",
        "/api/v1/p/{pid}/revision",
        "/index.html",
        "/assets/js/core/util.js",
    ])
    def test_get_is_unaffected(self, path):
        """CSRF 只拦写：GET 不带任何自定义头必须照常 200。"""
        pid = seed_roles("sec4")
        with make_server() as server:
            status, _h, raw = as_role(server, "visitor").request(
                "GET", url_of(path, pid=pid)
            )
            assert status == 200, (path, status, raw)

    def test_gate_closes_before_the_body_is_read(self):
        """`http.py` 的承诺是「未读 body 即拒绝」，且必须**在读 body 之前**。

        做法：声明一个超大 `Content-Length`，只发出请求头 + 一小段字节就停住。
        守卫开在读 body 之前 → 立刻 403；守卫若被挪到 `_read_body()` 之后 → 服务端
        在 `rfile.read()` 上等剩下的 1 MB，这里就会超时（**超时即红**，因为那正是
        「跨站表单可以先把服务器堵在读体上」的拒绝服务形状）。
        不能直接用 `http.client` 发满 1 MB 再读响应：服务器提前关闭连接会让客户端
        写入时拿到 broken pipe，那样测不到顺序。
        """
        from app.server.http import JSON_BODY_LIMIT

        pid = seed_roles("sec4")
        with make_server() as server:
            path = url_of("/api/v1/p/{pid}/favorites", pid=pid)
            head = (
                "POST %s HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n"
                "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n"
                % (path, JSON_BODY_LIMIT + 1024)
            )
            sock = socket.create_connection(("127.0.0.1", server.server_port), timeout=8)
            try:
                sock.sendall(head.encode("ascii") + b"{")
                sock.settimeout(8)
                data = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break  # 服务器拒了就该关连接（close_connection=True）
                    data += chunk
                    if len(data) > 65536:
                        break
            finally:
                sock.close()
            assert data, "服务器没有回应：守卫大概被挪到读完 body 之后了"
            status_line = data.split(b"\r\n", 1)[0]
            assert b"403" in status_line, status_line
            assert CSRF_HEADER.encode() in data, data[:400]


# ======================================================================
# §4.2：没有任何写接口能绕过角色校验（全量路由表扫描）
# ======================================================================


class TestNoWriteEndpointBypassesRoleCheck:
    def test_route_table_and_expectation_table_cover_each_other(self):
        """新增写接口而不在 `WRITE_ROUTE_ACTIONS` 登记 → 直接红。

        这条是「防旁路」的机关：它不给新接口默认放行，逼写卡的人当场表态权限档。
        """
        discovered = {key for key, _handler in route_table()}
        writes = {k for k in discovered if k[0] in ("POST", "PUT", "PATCH", "DELETE")}
        assert writes == set(WRITE_ROUTE_ACTIONS), (
            "路由表与本卡的权限期望不一致。新增未登记：%s；已消失：%s"
            % (sorted(writes - set(WRITE_ROUTE_ACTIONS)),
               sorted(set(WRITE_ROUTE_ACTIONS) - writes))
        )

    @pytest.mark.parametrize(
        "method,template,action",
        sorted((k[0], k[1], v) for k, v in WRITE_ROUTE_ACTIONS.items() if v),
    )
    def test_privileged_endpoint_denies_the_roles_it_must_deny(self, method, template, action):
        from app.services import authz

        pid = seed_roles("sec5")
        write_target(pid, FILE_A, XML_V1)
        denied = ["visitor", "member"]
        url = url_of(template, pid=pid)
        with make_server() as server:
            for role in denied:
                caller = as_role(server, role)
                body = XML_V1 if method == "PUT" else {}
                status, _h, payload = caller.json(method, url, body=body)
                assert status == 403, (role, method, template, payload)
                assert payload["error"]["detail"].get("action") == action, (
                    "%s %s 被 %s 拒了，但拒它的不是权限矩阵（detail=%s）"
                    % (method, template, role, payload["error"]["detail"])
                )

    @pytest.mark.parametrize(
        "method,template",
        sorted((k[0], k[1]) for k, v in WRITE_ROUTE_ACTIONS.items() if v is None),
    )
    def test_open_endpoints_are_not_permission_denied(self, method, template):
        """§4.2 里「所有角色可用」的写接口：访客必须过得去权限这一关（业务 400 不算）。"""
        pid = seed_roles("sec5")
        write_target(pid, FILE_A, XML_V1)
        with make_server() as server:
            caller = as_role(server, "visitor")
            body = b"<r/>" if template.endswith("/temp") else {"file": FILE_A, "id": "1"}
            status, _h, payload = caller.json(method, url_of(template, pid=pid), body=body)
            detail = (payload or {}).get("error", {}).get("detail", {})
            assert not (status == 403 and detail.get("action")), (
                method, template, status, payload
            )


# ======================================================================
# §11.5：错误响应不含绝对路径与堆栈
# ======================================================================


class TestErrorsDoNotLeak:
    def test_corrupt_data_error_hides_the_absolute_path(self):
        """权威 JSON 损坏 → 503，但客户端不能因此看到磁盘布局（§11.5）。

        `CorruptDataError` 把 `path` 放进 detail（`errors.py:80`），而
        `storage.py:1745` 传的是绝对路径——那两个文件都不在本卡可改范围，
        所以只能在响应边界上收敛。
        """
        pid = seed_roles("sec6")
        bad = storage.get_favorites_file(pid)
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        with make_server() as server:
            caller = as_role(server, "visitor")
            status, _h, payload = caller.json("GET", "/api/v1/p/%s/favorites" % pid)
            assert status == 503, payload
            assert payload["error"]["code"] == "CORRUPT_DATA"
            raw = json.dumps(payload, ensure_ascii=False)
            assert storage.DATA_DIR not in raw, raw
            assert bad not in raw, raw
            assert SENTINEL not in raw
            for value in payload["error"]["detail"].values():
                assert not (isinstance(value, str) and os.path.isabs(value)), raw
            assert "Traceback" not in raw and ".py" not in raw

    def test_unknown_exception_gives_generic_500(self):
        """未知异常：日志留堆栈，响应不含堆栈（§3.4 第 8 步）。用测试专用路由造异常。"""

        def boom(ctx, params, query, body):
            raise RuntimeError("内部信息 /var/data/secret.xml")

        def crashing_router(settings):
            router = build_router(settings)
            router.add("GET", "/api/v1/p/{pid}/boom", boom)
            return router

        seed_roles("sec6")
        with make_server(router=crashing_router) as server:
            status, headers, body = as_role(server, "visitor").request(
                "GET", "/api/v1/p/sec6/boom"
            )
            assert status == 500
            assert headers["Content-Type"] == "application/json; charset=utf-8"
            text = body.decode("utf-8")
            assert "Traceback" not in text
            assert "secret.xml" not in text
            assert "/var/data" not in text
            payload = json.loads(text)
            assert payload["error"]["code"] == "STORAGE_FAILURE"
            assert payload["error"]["detail"] == {}

    def test_containment_error_carries_no_path_at_all(self):
        """`resolve_within` 的 403 只说「超出允许范围」，不回显拼出来的目标路径。"""
        with pytest.raises(AppError) as caught:
            fsutil.resolve_within(storage.DATA_DIR, "..", "etc", "passwd")
        exc = caught.value
        assert exc.http_status == 403
        assert exc.detail == {}

    def test_error_bodies_never_contain_the_data_root_prefix(self):
        """把能踩的错误各来一遍，统一钉「不含数据根前缀」。"""
        pid = seed_roles("sec6")
        write_target(pid, FILE_A, XML_V1)
        outside = outside_sentinel()
        with make_server() as server:
            caller = as_role(server, "member")
            probes = [
                ("GET", "/api/v1/p/%s/files/nope.xml/raw" % pid, None),
                ("GET", "/api/v1/p/%s/files/..%%2Fsentinel/raw" % pid, None),
                ("GET", "/assets/..%2F..%2Fsentinel", None),
                ("POST", "/api/v1/p/%s/favorites" % pid, {"file": "nope.xml", "id": "1"}),
                ("DELETE", "/api/v1/p/%s/files/%s" % (pid, FILE_A), None),
            ]
            for method, path, body in probes:
                status, _h, raw = caller.request(method, path, body=body)
                assert status in (400, 403, 404, 409, 422), (path, status, raw)
                assert storage.DATA_DIR.encode() not in raw, (path, raw[:200])
                assert b"Traceback" not in raw, path
            sentinel_untouched(outside)
