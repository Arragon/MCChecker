# M16 更新管理真实浏览器 E2E 记录（M16-40）

> 2026-11-07 更新：最新本地交互 38/38、旧路由可达 28/28、API 1/1；实际 collector + Chromium + TLS 模拟 SDA 另有 4 项端到端及 113.4 秒闲置验证，见 [当前 R6 对照和证据范围](M16_R6_UI_AUDIT.md)。下面 2026-11-02 的 token 与 13 项记录保留作历史，不代表当前授权规则；当前只区分管理员/访客。真实 SDA/目标平台未验收；不再等待现场输入阻塞模拟测试。

> 状态：2026-11-02。**由可复跑脚本驱动**：`scripts/e2e_browser.py`（stdlib CDP）+ `scripts/e2e_scenarios.py`（场景清单），
> 每次运行产出 JSON 报告与逐场景截图。最近一次全量：**interactive 13/13 通过、reachable 28/28 通过**。
> 环境：macOS + Google Chrome 156 headless（CDP）、临时数据根（`~/.cache/mcc-e2e-40-*`）、Python 3.9.11 主应用。未使用公司内网、未接触真实 SDA。

## 0. 怎么复跑

```bash
# 前端单测页（HTTP keep-alive + 只认 ALL PASSED）
./.venv39/bin/python scripts/check_web_ui.py

# 真实服务 + 真实 API 的浏览器 E2E（自动起服务、造数据、截图、写 report.json）
./.venv39/bin/python scripts/e2e_browser.py --only all
./.venv39/bin/python scripts/e2e_browser.py --only updates   # 只跑更新管理
```

脚本会：建临时数据根 → 播种（管理员身份、`A.xml`、一条启用 + 一条停用的 HTTP 来源）→ 起 `run.py --no-reload`
→ Chrome CDP 逐场景点击/输入/断言/截图 → 写 `report.json`（在临时数据根，不入库）→ 截图落到 `docs/evidence/m16-40/`。

## 1. 测试数据与身份

- 临时工作区 `default`（legacy），机型 `default`；文件 `A.xml`（`<R><x>1</x><y>2</y><z>3</z></R>`）。
- 来源：`E2E HTTP 来源`（启用，custom 6h）、`E2E 停用来源`（停用），target=`file_archive` 绑到 `A.xml` 的 FileResource。
- 身份：`X-Forwarded-For: 10.9.9.1 → E2E管理员`（admin）、`10.9.9.2 → 成员`（read-only）；
  服务以 `MCHECKER_TRUSTED_PROXIES=127.0.0.1` 启动才会采信该头。**换身份必须整页重载**（SPA 的 session 启动时拉取）。
- 运维 token：`MCHECKER_OPERATOR_TOKEN` 由脚本注入，只出现在服务进程环境里，不进仓库、不进截图。

## 2. 更新管理交互（interactive，13 项全通过）

| 场景 | 断言要点 | 截图 |
|---|---|---|
| `updates-sources-list` | 来源行、新建按钮、批量工具条齐备 | `updates-sources-list.png` |
| `updates-source-create` | 抽屉含 kind / 连接 / 目标绑定三组控件；取消可关 | `updates-source-create.png` |
| `updates-source-focus` | 抽屉内初始焦点、Esc **不**关闭未保存草稿抽屉、取消后焦点回到触发按钮 | 同上 |
| `updates-plan` | 全量预览入口与保存按钮存在 | `updates-plan.png` |
| `updates-connections` | 新建连接入口 → 运维 token 输入 → 提交按钮 | `updates-connections.png` |
| `updates-runs` | 运行记录页渲染 | `updates-runs.png` |
| `updates-candidates` | 状态/机型筛选与批量工具条 | `updates-candidates.png` |
| `roles-admin-member` | 管理员有 `[data-sync-edit]`；成员重载后无编辑入口 | `updates-sources-1366-admin.png` / `-member.png` |
| `viewport-1366 / 1920 / 768 / 375` | `scrollWidth - clientWidth <= 1`（四档全 0） | `updates-sources-{1366,1920,768,375}-admin.png` |
| `operator-token` | 无 token 403、错 token 403 且不回显、正确 token 201 | `updates-connections-operator-token.png` |

## 3. 旧 V3 F01–F28（reachable，28/28 通过）

逐项导航到对应路由，断言：视图挂载（`.view`）、无 `.form-error`、页面文本命中该面板关键词。

| F | 面板 | F | 面板 |
|---|---|---|---|
| F01/F02 | 导入/链接导入入口 | F15/F16 | 旧更新源/定时入口（已收口到更新管理） |
| F03/F04 | 文件列表/结构树 | F17/F18 | 人员/管理员 |
| F05 | 文件内搜索 | F19 | 变量绑定 |
| F06/F07 | 收藏/速览 | F20 | 外部工具 |
| F08 | 修改建议 remarks | F21 | DL 计算 |
| F09 | 审阅 | F22 | 多机型 |
| F10 | 全局搜索 | F23/F24 | 标签页/身份 |
| F11/F12 | 历史/对比 | F25 | 删除文件 |
| F13/F14 | 修改记录/下载 | F26–F28 | 迁移/解析缓存/启动端口（**服务层**，浏览器只验证不崩） |

> 口径：`reachable` 只证明「路由可达、视图渲染、无错误块、关键词命中」，**不等于**每项交互都已回归；
> 交互级证据目前覆盖更新管理（上表）与文件页（F03/F04 的树与列表渲染）。

## 4. 与早期手工记录的差异

早期（同日早些时候）是**一次性 CDP 会话**留下的手工截图：1280 五 tab、成员只读、375/768、编辑抽屉初始焦点与 409 草稿保留
（`updates-sources-1280-admin.png`、`updates-sources-375-member.png`、`updates-sources-409-draft-retained.png` 等）。
那些结论仍然有效，但不可复跑；本轮把它们改写成脚本化场景，并补上：焦点恢复、Esc 行为、运维 token 三段式、四档视口、
F01–F28 逐项可达矩阵。

## 5. 仍未覆盖（因此 M16-40 的边界要写清）

1. 真实 SDA 数据下的候选生成与决议 E2E：依赖 M16-02/41 的 `BLOCKED-EXTERNAL`，当前候选页只验证空态与筛选控件。
2. 文件页的深度交互（收藏、remarks 提交、参数编辑、历史对比）只到「渲染 + 关键词」级别，未逐项点击断言。
3. Windows/Linux 实机浏览器、真实窄屏设备、真实内网 SSO：归 M16-42 的 `BLOCKED-EXTERNAL`。
4. 跨工作区同名来源的浏览器级切换断言沿用早期手工证据（`W1/W2` 两张截图），未纳入当前脚本场景。
