"""M16-40 浏览器 E2E 的场景清单（纯数据 + 纯函数，便于单测）。

三级证据：
- `interactive`：真的点击/输入/断言 DOM 变化（更新管理、焦点、409、运维 token）。
- `reachable`：路由可达、视图挂载、无错误块、标题快照一致（旧 V3 的 F 面板）。
- `api`：真实服务接口交互，不证明用户界面能发出相同请求。

级别写进报告，不能用 `reachable` 冒充 `interactive`。
"""

from __future__ import annotations

from typing import Dict, List, Optional

VIEWPORTS = (
    {"id": "desktop-1366", "width": 1366, "height": 768},
    {"id": "desktop-1920", "width": 1920, "height": 1080},
    {"id": "tablet-768", "width": 768, "height": 1024},
    {"id": "phone-375", "width": 375, "height": 812},
)

#: 旧 V3 的 F01–F28：路由 + 期望可见的关键词（任一命中即视为该面板已渲染）。
#: 关键词取自各视图的实际标题/按钮文案；F26–F28 是服务/CLI 层能力，浏览器侧只验证不崩。
LEGACY_ROUTES: List[Dict[str, object]] = [
    {"f": "F01", "label": "本地上传/导入入口", "path": "settings/import", "expect": ["导入", "上传", "设置"]},
    {"f": "F02", "label": "链接导入入口", "path": "settings/import", "expect": ["链接", "导入", "设置"]},
    {"f": "F03", "label": "文件列表", "path": "f/A.xml", "expect": ["A.xml", "参数", "结构"]},
    {"f": "F04", "label": "结构树", "path": "f/A.xml?view=tree", "expect": ["A.xml", "树", "结构", "参数"]},
    {"f": "F05", "label": "文件内搜索", "path": "search?q=x", "expect": ["搜索", "结果", "x"]},
    {"f": "F06", "label": "收藏", "path": "overview", "expect": ["收藏", "速览"]},
    {"f": "F07", "label": "速览", "path": "overview", "expect": ["速览"]},
    {"f": "F08", "label": "修改建议 remarks", "path": "f/A.xml?view=tree", "expect": ["建议", "修改", "A.xml"]},
    {"f": "F09", "label": "审阅", "path": "review", "expect": ["审阅"]},
    {"f": "F10", "label": "全局搜索", "path": "search", "expect": ["搜索"]},
    {"f": "F11", "label": "历史版本", "path": "f/A.xml?view=history", "expect": ["历史", "版本", "A.xml"]},
    {"f": "F12", "label": "对比", "path": "f/A.xml?view=compare", "expect": ["对比", "差异", "A.xml"]},
    {"f": "F13", "label": "修改记录", "path": "f/A.xml?view=records", "expect": ["记录", "A.xml"]},
    {"f": "F14", "label": "下载", "path": "f/A.xml?view=tree", "expect": ["下载", "A.xml"]},
    {"f": "F15", "label": "旧更新源入口（收口到更新管理）", "path": "settings/sources", "expect": ["更新源", "更新管理"]},
    {"f": "F16", "label": "旧定时更新入口（收口到更新管理）", "path": "settings/schedule", "expect": ["计划", "更新"]},
    {"f": "F17", "label": "人员", "path": "settings/people", "expect": ["人员", "访问"]},
    {"f": "F18", "label": "管理员", "path": "settings/people", "expect": ["管理员", "人员"]},
    {"f": "F19", "label": "变量绑定", "path": "settings/bindings", "expect": ["绑定", "变量"]},
    {"f": "F20", "label": "外部工具", "path": "tools", "expect": ["工具"]},
    {"f": "F21", "label": "DL 计算", "path": "dl", "expect": ["DL"]},
    {"f": "F22", "label": "多机型", "path": "settings/profiles", "expect": ["机型"]},
    {"f": "F23", "label": "标签页", "path": "overview", "expect": ["速览"]},
    {"f": "F24", "label": "身份", "path": "overview", "expect": ["速览"]},
    {"f": "F25", "label": "删除文件", "path": "f/A.xml?view=tree", "expect": ["删除", "A.xml"]},
    {"f": "F26", "label": "旧布局迁移（服务层）", "path": "settings/profiles", "expect": ["机型"]},
    {"f": "F27", "label": "解析缓存（服务层）", "path": "f/A.xml", "expect": ["A.xml", "参数", "结构"]},
    {"f": "F28", "label": "启动与端口（服务层）", "path": "overview", "expect": ["速览"]},
]

#: 更新管理的交互场景（真实 API，不加载 mock）。步骤语法见 e2e_browser.py。
UPDATE_SCENARIOS: List[Dict[str, object]] = [
    {
        "id": "updates-sources-list",
        "tab": "sources",
        "title": "来源列表/筛选/新建与批量工具条",
        "steps": ["wait:[data-sync-source]", "assert:[data-sync-new]", "assert:[data-sync-batch]", "assert-disabled-source", "shot"],
    },
    {
        "id": "updates-source-create",
        "tab": "sources",
        "title": "新建来源抽屉（类型/连接/目标绑定）",
        "steps": [
            "click:[data-sync-new]",
            "wait:[data-sync-kind]",
            "assert:[data-sync-connection]",
            "assert:[data-sync-target-kind]",
            "shot",
            "click:[data-sync-cancel]",
            "wait-close",
        ],
    },
    {
        "id": "updates-source-focus",
        "tab": "sources",
        "title": "抽屉初始焦点、Esc 不关闭未保存抽屉与焦点恢复",
        "steps": [
            "remember-focus",
            "click:[data-sync-new]",
            "wait:[data-sync-kind]",
            "assert-focus-in:.modal",
            "assert-source-tab-wrap",
            "key:Escape",
            "wait-close",
            "click:[data-sync-new]",
            "wait:[data-sync-kind]",
            "click:[data-sync-cancel]",
            "wait-close",
            "assert-focus-restored",
        ],
    },
    {
        "id": "updates-plan",
        "tab": "plan",
        "title": "更新计划与全量预览入口",
        "steps": ["wait:[data-plan-preview]", "assert:[data-plan-save]", "shot"],
    },
    {
        "id": "updates-connections",
        "tab": "connections",
        "title": "连接设置与运维 token 入口",
        "steps": [
            "wait:[data-conn-create]",
            "click:[data-conn-create]",
            "wait:[data-conn-token]",
            "assert:[data-conn-create-submit]",
            "validate-connection-form",
            "shot",
            "key:Escape",
            "assert:[data-sync-sheet-close]",
            "key:Escape",
            "wait-close",
        ],
    },
    {
        "id": "updates-runs",
        "tab": "runs",
        "title": "运行记录页渲染",
        "steps": ["assert-text:运行记录", "shot"],
    },
    {
        "id": "updates-candidates",
        "tab": "candidates",
        "title": "候选列表筛选与批量工具条",
        "steps": ["wait:[data-sync-candidate-state]", "assert:[data-sync-candidate-profile]", "shot"],
    },
]

#: 运维 token 的三段式验证：无 token / 错 token / 正确 token。
OPERATOR_CASES = (
    {"id": "operator-missing", "token": "", "expect_status": "403"},
    {"id": "operator-wrong", "token": "wrong-token-value", "expect_status": "403"},
    {"id": "operator-right", "token": "PROVISIONED", "expect_status": "其它"},
)


def route_path(kind: str, arg: str = "", query: Optional[str] = None, pid: str = "default") -> str:
    path = "#/p/%s/%s" % (pid, kind)
    if arg:
        path += "/" + arg
    if query:
        path += "?" + query
    return path


def updates_hash(tab: str, workspace: str) -> str:
    return route_path("updates", tab, "workspace=%s" % workspace)


def legacy_hash(item: Dict[str, object], workspace: str) -> str:
    raw = str(item.get("path") or "overview")
    base, _, query = raw.partition("?")
    kind, _, arg = base.partition("/")
    extra = query + ("&" if query else "") + "workspace=%s" % workspace
    return route_path(kind, arg, extra)


def summarize(results: List[Dict[str, object]]) -> Dict[str, int]:
    """交互、路由和接口证据分别计数。"""
    summary = {"interactive_pass": 0, "interactive_fail": 0, "reachable_pass": 0, "reachable_fail": 0, "api_pass": 0, "api_fail": 0}
    for row in results:
        level = row.get("level")
        ok = bool(row.get("ok"))
        if level == "interactive":
            summary["interactive_pass" if ok else "interactive_fail"] += 1
        elif level == "api":
            summary["api_pass" if ok else "api_fail"] += 1
        else:
            summary["reachable_pass" if ok else "reachable_fail"] += 1
    return summary
