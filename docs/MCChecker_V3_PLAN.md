# MCChecker V3.0 开发计划（M9–M15）

> **定位**：本文件是仓库内的 V3 开发计划镜像；事实源仍是 Linear 项目 `P-INH-14` 与 INH-2117 的原型 HTML/后端设计 MD。生成 2026-10-28；**计划已批准，尚未开始 M9-00 实现**。
> **事实源**：[Linear 项目 P-INH-14](https://linear.app/inhandy/project/mc-checker-v2-a9004135219e/overview)、[INH-2117 设计基线](https://linear.app/inhandy/issue/INH-2117)；执行索引与后端设计 ID 见 §1.1。
> **执行口径**：M9 卡级、M10–M15 里程碑级、逐卡 JIT；`feat/mc-v3` 集成分支、一卡一 PR；不改旧功能；不编造人日/日期/完成度。
> **本轮范围**：只落计划文档，不改业务代码、不启动 M9-00。

## Context

Linear 项目 `P-INH-14` 已在 2026-10-28 增加 V3.0 的 M9–M15 共 45 张卡，全部 Backlog；设计输入是 INH-2117 的两份原件（前端 HTML、后端设计 MD）和两份 Linear 文档（执行索引、后端设计说明书）。仓库当前 `main@c24ab23`，M0–M8 的 v2 交付已在 main 历史中，旧配置域（M5–M8）仍可运行；L9 的 4 条主线红灯已在工作区修好但尚未提交/合并。

本计划的目标不是重写设计文档，而是把 Linear 的 45 张卡收敛成可执行的仓库级计划：里程碑顺序、卡级依赖、文件面、验收证据、分支与 PR 规则、风险和首批动作。计划采用“M9 卡级 + M10–M15 里程碑级、逐卡 JIT”的粒度；默认新建长驻集成分支 `feat/mc-v3`，一卡一 PR，M15 收口后才并入 main；本轮只落计划文档，不改业务代码、不启动 M9-00。**界面与交互以 INH-2117 的 V3 原型 HTML 为严格对齐基准（见 §1.3）；服务端行为以设计 MD 为准，两者冲突先写 ADR，不允许实现卡自行选边。**

## 0. 执行口径

- 事实源：Linear 卡面与两份 V3 设计文档；仓库文档只做镜像，冲突时以 Linear 为准并回填。
- 卡级节奏：每卡开工时产出 JIT brief（范围、文件面、接口、测试、验收命令、风险），不预先给 45 张卡写逐步 TDD。
- 不编造人日、日期、完成度；用依赖、波次和相对工作量级 S/M/L 表达（S=单点改动，M=一个域内多模块，L=跨域/迁移/门禁）。Linear 的 estimate 全部为空，计划不回填。
- 一卡一 PR，base 恒为 `feat/mc-v3`；每卡必须有实现 diff、定向测试、全量回归、Python 3.9 与零依赖验证；改前端加 HTTP 前端页；动数据根/备份加 H3 与 `test_release.py`。
- M9-00/M9-01 只产出基线与契约文档，不写业务代码；HTML 原型模拟数据不算后端验收证据。
- 旧 `remarks`/`review`、速览、文件、搜索、DL、工具、设置、机型、更新源、任务与旧链接必须保留；V3 替换的是旧 config-domain 的 ChangeSet/registry/delivery/audit 编排，不是整个 v1。

## 1. 基线与事实

### 1.1 Linear

- 当前板面：**149 张 / 91 Done / 9 In Progress / 47 Backlog / 2 Canceled**。
- V3：**M9–M15 共 45 张全部 Backlog**（M9 7、M10 6、M11 6、M12 7、M13 7、M14 6、M15 6）。
- 设计输入：
  - 前端 HTML：`MCChecker_V3.0_Frontend.html`，SHA-256 `f50888b9147aeff6dbedb5976238e63f75e44d4073799864522de857f989927c`。
  - 后端 MD：`MCChecker_V3.0_Backend_Design.md`，SHA-256 `86b1f51099f4b4d0487bcd704eedfe6bd90dbda8367d68c3133725a4976a17b0`。
  - 执行索引：Linear 文档 `3ec7123e-77c9-4e0b-aa86-0f6ffb4cd48e`；后端设计全文：`7819fe3f-b5a3-4737-9411-d6aa5c0db5d9`。
  - 附件说明：INH-2117 的附件 URL 带签名，会过期；M9-00 开工当天通过 Linear 附件接口重新获取，不把签名 URL 写进仓库。**HTML 附件的直连签名 URL 实测可能返回 401，必须用 Linear `get_attachment(format=content)` 取回并校验 SHA；MD 直连可用，但仍以内容 SHA 为准。**
- 关键依赖缺口（需要在计划层兜底）：
  - `INH-2123`（M9-06 首轮门禁）的 `blocks` 为空；本计划规定 **M10-01 开工前必须贴 2123 绿证据**，否则 M10 抢跑。是否同步补 Linear relation 见 ADR-006。
  - `INH-2161` 被旧卡 `INH-1910`（M4-08，仍 In Progress）阻塞；M4-08 未关闭或未取得书面豁免前，2161 不得 Done。
  - `INH-1932`（100 文件档搜索 P50 约 2.4–2.7 s，预算 500 ms）只是 `relatedTo`，不会自动阻断 2160；2160 必须显式记录该风险，不得静默当已解决。
  - 2123 卡面写的 `scripts/check_py39.py` 与仓库实有的 `scripts/check_py39.sh` 不一致；开工时按 `.sh` 执行，并在卡面/PR 里注明是卡面笔误。

### 1.2 仓库现状

- 当前 `main@c24ab23`；v2 交付已进入 main（`feat/mc-v2-delivery@2e8031f`、M5–M8 收口 tip `df4e642`）；`origin/mc-v2/m5-m8` 为历史分支。
- L9 修复（4 条红灯）已在 `chore/fix-main-red-20261028` 工作区验证：全量 2571 passed / 0 failed，前端 HTTP `ALL PASSED (96)`，`check_py39.sh` exit 0；尚未提交/合并。启动 V3 前必须先把它作为独立 chore PR 合入 main，并记录新的 main SHA。
- 没有任何 Workspace 实现：`app/services/context.py` 只有 `RequestContext(ip,cid,profile_id,actor)`；数据根是 `data/profiles`，无 `data/workspaces`；`app/server/router.py` 只注册 v1。
- 文件身份仍是“profile + 文件名”：`app/services/documents.py::FileRef` 只认 `current`、`archive:<文件名>`、`record:<文件名>`；删除整文件进恢复区，无持久 file_id/version_id/tombstone。
- 可复用的现有能力：
  - `app/core/config_repository.py`：blob SHA-256、原子 state、CAS/expected、持久幂等回执、audit、verify；`__init__(data_root)` 已支持任意子根。
  - `app/core/config_merge.py`：O/L/N 三方纯算法。
  - `app/services/config_baselines.py`：`intent=inherit/override/pin`、`scope=common/model/device`、`values/parameter_id`。
  - `app/services/config_catalog.py`、`config_ingest.py`、`config_export.py`、`config_artifacts.py`、`config_verify.py`、`scripts/config_backup.py`。
  - 前端 `MC.api` 旧包络、`shell.js` 的 `loadSession/switchProfile/renderHeader`、`MC.router` 的 hash 路由。
  - 测试：`tests/authz_fixtures.py` 的四角色夹具、`tests/conftest.py` 的隔离数据根与 H3 守卫、`tests/test_config_transactions.py`、`test_config_merge.py`、`test_config_export.py`、`test_config_migration.py`。
- 硬约束：Python 3.9.11、标准库 HTTP、运行时零第三方依赖、原生 JS、现有视觉 tokens、离线内网、单写者；50k 参数首批 p95≤500 ms、草稿保存≤800 ms、3200 项提取首批≤3 s、原子提交≤5 s、20 并发。
- 文件面撞车（并行调度必须看）：所有后端卡都撞 `app/server/router.py`；所有前端卡都撞 `app/web/assets/js/core/api.js`、`mock-api.js`、`tests.js`；Workspace/文件/配置内核卡会撞 `storage.py`、`config_repository.py`、`tests/conftest.py`。新增 Workspace 目录必须进 `tests/conftest.py::DATA_SUBDIRS`，否则 H3、备份和夹具会假绿。

### 1.3 原型 UI/功能基准（INH-2117 HTML）

用户要求本计划与 INH-2117 的 V3 原型 HTML 严格对齐。该 HTML 是**界面与交互事实源**；后端设计 MD 是**服务端行为事实源**。两者冲突时不得由实现卡自行选边，必须先写 ADR 并同步所有下游；HTML 的模拟数据仍不算后端验收证据。

本计划在卡面和后端 MD 之外，补齐四件事：① 原型屏幕/组件清单；② 工作区切换、参数工作台/树、参数总览、提取、合并、导出、历史等交互事实；③ 每张相关卡的 UI 验收与 M9-00/01 的 UI parity matrix；④ 原型与 MD 的冲突 ADR（ADR-007–011）。

原型由两层组成：
- **宿主外壳** `assets/js/views/shell.js` + `configspace-integration`：顶栏、侧栏文件列表、标签、速览/搜索/审阅/DL/工具/设置，以及“配置管理”入口。
- **配置管理域** `MCC_CONFIG_DOMAIN_HTML`：独立的配置管理界面，通过 `MCCV3Host` 桥接宿主；正式实现应是一个应用内的原生视图（或至少保持同样的能力契约），不引入第二套站点。

**原型屏幕清单（必须逐屏映射到卡）**

| 屏幕/组件 | 关键内容 | 主要卡 |
|---|---|---|
| 宿主顶栏 | 侧栏开关、品牌、**工作区切换**、机型切换、搜索/Ctrl+K、任务指示、演示身份、身份 chip | 2119/2122/2123 |
| 宿主侧栏 | 导航（速览/全局搜索/审阅修改/DL 计算/配置管理/外部工具/设置）、唯一“配置文件”列表、上传 +、筛选、下载/历史/删除 | 2125/2126/2127/2128/2131 |
| 宿主标签栏 | 每工作区/机型的 tabs、最近页、当前页、dirty 点、关闭/关闭其他/全部、上限 15 | 2122 |
| 配置管理顶栏 | 固定配置 / 配置文件 / 配置记录；面包屑“固定配置 / <名称> <code>”；非管理员只读横幅 | 2136/2142/2155 |
| 固定配置目录 | 搜索配置名称/标识、配置数量、新建固定配置、空态“添加第一个软件版本” | 2136/2142 |
| 版本演进 | 680/690/691 版本列；D0 版本默认、Bn 主线基线、MC-001/002 机台分支；节点状态、连线、待同步提示、新建软件版本/机台分支 | 2136/2142 |
| 配置修订选择器 | 四级：管理分支（主线/机台）→ 软件版本（新到旧）→ 参数配置类型（默认/基线/机台）→ 修订（新到旧） | 2142/2151/2153 |
| 参数工作台 | 选择器路径、草稿/固化状态、基准与参数数、保存状态、导入/校验/保存/发布、继承规则、来源与历史、参数目录树 + 参数表 | 2137/2141/2142/2143 |
| 参数树视图 | 与列表共享修订/ETag/选中参数；目录展开/折叠/搜索/分页 25/50/100；复用原文件树的行高/箭头/↑↓←→/Enter/详情 | 2143 |
| 参数总览 | 软件版本/对比/基线/机台列；默认值、基线值、各机台实际 rn；仅看变化；新增/停用/改名/层级/类型/单位/约束/数值/intent 差异标签 | 2144/2145 |
| 参数历程 | 每个参数的修订序列、名称/层级/属性/值、相对前序修订 | 2145/2154 |
| 从配置文件提取参数 | 选择 JSON/XML 文件、目标版本、50 行/页、全部/新增/冲突/已有筛选、逐行新增/关联/跳过、类型选择、补齐缺失绑定、提交 | 2146/2147/2148 |
| 导入参数 | 目标修订选择器、空间文件/上传文件、映射、重复检测、写入默认草稿或生成新草稿 | 2152/2141 |
| 预览合并 | 主线跨版本/同版同步/机台跨版本徽标、目标基线选择、O/L/N 三栏、仅未决、逐行决议、生成合并草稿 | 2150/2151 |
| 模板导出 | 四步：配置修订 → 选择模板 → 核对映射 → 预览并下载；来源文件、停用字段确认、预览 token、新文件不覆盖原文件 | 2153 |
| 配置历史 | 分支/版本筛选、搜索、事件列表、前后值、操作人/IP/时间、撤销最近可逆操作、从历史恢复草稿 | 2154 |
| 配置记录 | 当前工作区事件（上传/归档/配置/空间设置）、搜索、详情 | 2154/2155 |
| 文件页桥接 | 配置域不建第二文件库；“配置文件”打开宿主左下列表；“工作区设置”打开宿主工作区管理器 | 2131/2134/2155 |
| 状态/权限 | 只读横幅、无权限禁用按钮与 tooltip、空态、加载、失败重试、保存失败、来源变化、草稿 dirty | 2123/2159 |

**原型交互事实（实现必须复现）**
- 顶栏顺序：侧栏开关 → 品牌 → **工作区** → 机型 → 搜索 → 任务 → 演示（仅 mock）→ 身份；工作区切换插在机型之前，移动端按原型断点收起 caption。
- 工作区切换：先确认所有 dirty 视图和打开的 modal；`epoch++`、清实例/任务/弹窗/待处理来源、保存 tabs/当前路由/当前 profile；切换后重建 session、恢复该工作区上次路由（带 `workspace` query）并刷新任务。
- 工作区管理：名称 1–50 字符、说明 ≤300；全局唯一；仅管理员可新建/改名；新工作区得到空 default 机型，不继承其它工作区数据；同名 409 字段级报错。
- 工作区/机型的本地键：`mc.*` 按工作区隔离（`mcc-v3-workspace:<wid>:mc.*`）；`mc.demo.identity` 保持全局；全局身份、管理员名单、IP 映射不随工作区复制。
- 配置管理域通过宿主桥接取文件：`context`、`listFiles`、`readFile`、`openFile`、`openUploader`、`openFiles`、`openWorkspaceManager`、`openGlobalSearch`、`renderParameterTree`。正式实现可原生实现这些能力，但**不得**在配置域另建上传库或工作区管理器。
- 配置域只读：非管理员隐藏/禁用编辑入口并显示“当前身份可查看配置，编辑需要管理员权限”；不得靠 UI 隐藏代替服务端 403。
- 参数工作台与参数树共享 revision/ETag/选中 parameter_id；树复用原文件查看器的键盘与详情面板。
- 提取、合并、导出、恢复的提交前都必须重新读取来源文件并核对 hash/current version；来源变化给 `SOURCE_CHANGED`/重新选择，不得静默用旧副本。
- 发布/固化只移动管理 head，UI 明确写“未下发真实机台”。

**原型与后端说明书的已知冲突（必须先 ADR）**

| 编号 | 冲突 | 建议 |
|---|---|---|
| ADR-007 | 原型 XML 引擎用索引元素路径（`/Config[1]/Parameters[1]/Key[1]`）；后端 MD 要求 namespace URI + 完整元素路径 + 同名 occurrence | 原型路径是 UI 事实，MD 是服务端事实；M11-03 必须定义同时可显示、可稳定定位的 canonical path；若两者不能同时满足，M9-00 ADR 决定改原型还是改 MD，并同步所有下游卡 |
| ADR-008 | 原型配置域“工作空间”切换文案写“权限随当前空间切换”，但代码与实际规则是全局 isAdmin/权限不变；另有“工作区/工作空间”两种叫法 | 采用全局身份/权限不变，统一叫“工作区”；修文案与测试 |
| ADR-009 | 原型配置域 `spaceStore` 只按工作区存 configs/files，未按机型存；后端 MD 要求 FixedConfig 属于 W+P | 实现必须 W+P 隔离；配置域状态/缓存/审计/文件引用都带 profile；补原型未覆盖的同 W 不同 P fixtures |
| ADR-010 | 原型配置域是 iframe + `MCCV3Host` 桥接；后端 MD 要求一个应用、无第二站点 | 推荐原生视图 + 等价宿主 API；iframe 只作为原型打包方式，不作为正式架构；若保留 iframe，必须证明无第二文件库/第二工作区管理 |
| ADR-011 | 原型提取/合并/导出有 50 行/页与本地原型数据；卡面要求 50/100、3000+/50k、真实后端 | 默认 50 行/页，100 作为可选；原型数据只做 UI 状态，不参与性能/验收数字 |

## 2. 分支、卡务与证据规则

- 集成分支：`feat/mc-v3`，从 L9 合并后的 main 创建并设保护。
- 卡分支：`mc-v3/<卡号>-<slug>`；PR base 恒为 `feat/mc-v3`；合并用 `--merge`；M15 收口后再开 `feat/mc-v3 → main` 的汇总 PR。
- 非卡改动（文档、测试基建、脚本）用 `chore/...`，同样走 PR，不直接推 main 或集成分支。
- 每卡 PR 必须写：卡号、范围、文件面、改动、验证命令+结果、已知取舍/风险、回滚方式。未跑过的检查不得写成通过。
- 每卡 Done 前至少：定向测试 + 全量 `pytest -q` + `check_py39.sh`；改 `app/web/**` 加 HTTP 前端页；动 `data/`/备份加 H3 与 `test_release.py`；动发布包加 `make_release.py --verify-only`。
- 证据格式沿用 `docs/know-how.md` 与 `MCChecker_DELIVERY_PLAN.md` 的口径：命令 + 原始输出 + 解释器/硬件/并发；不粘贴过期数字。

## 3. 里程碑路线图

| 里程碑 | 退出条件 | 卡与波次 | 串行/并行 |
|---|---|---|---|
| M9 契约冻结与全局 W 基础 | 2117/2118 文档完成；2119–2122 可用；2123 绿；v1 缺 wid 写 428；W1/W2 隔离；前端 `ALL PASSED` | 2117(W0)→2118(W1)→2119(W2)→2121(W2)→2120(W3)→2122(W4)→2123(W5) | 2119 与 2121 在 schema 冻结后局部重叠；2122 的 mock 与 2123 fixture 可提前；主线全串行 |
| M10 原功能全工作区适配 | 速览/文件/搜索/审阅/DL/源/任务/设置/工具全按 W+P；旧全局根不再读写；F01–F28 回归 | 2124(W0)→2125/2126/2127/2128(W1)→2129(W2) | 四路并行；2124、2129 串行；2123 是硬门禁 |
| M11 唯一文件与精确版本 | FileResource/FileVersion 稳定；精确来源；tombstone；唯一选择器；hash 门禁 | 2130(W0)→2131/2132(W1)→2133/2134(W2)→2135(W3) | 2132 只依赖 2118，可最早提前；2131/2132、2133/2134 并行；2130、2135 串行 |
| M12 配置管理 V3 内核 | D0 每版一次；Bn/rn 不可变；只移动 head；草稿 CAS；工作台/选择器闭环 | 2136(W0)→2137(W1)→2141(W2)→2138(W3)→2139(W4)→2140(W5)→2142(W6) | 全串行，最长段；2142 还需 2134 |
| M13 工作台/总览/提取 | 双视图、矩阵、八类差异、3200 提取、50k 参数性能、分组分页 | 2143/2144(W0) 与 2146(W0)→2145/2147(W1)→2148(W2)→2149(W3) | 投影链与提取链并行；2149 是门禁 |
| M14 合并/导入/导出/历史 | O/L/N 决议、值导入、精确模板导出、恢复/撤销、旧 config 停写、全流程集成 | 2150/2152/2153/2154(W0)→2151(W1)→2155(W2) | 2150 可与 M13 并行；2152–2154 并行；2151 等 2142+2150；2155 收口 |
| M15 迁移/总门禁/发布 | dry-run 零写、迁移/回退演练、跨 W 安全、真实浏览器/模板/离线/性能、发行包 | 2156(W0 提前)→2157(W1)→2158/2159/2160(W2)→2161(W3) | 2156 只依赖 2121/2129/2130/2136，可提前；2158–2160 并行；2161 等 1910 |

**总关键路径（压缩）**：2117→2118→2119→2121→2120→2124→2129→2135→2155→2157→2158→2161；另有 2130→2131→2134→2142→2151→2155 和 2137→2141→2138→2139→2140→2142。M12 七卡链是不可并行的最长段；2156 提前只能缩短准备期，不能缩短最终门禁。

**可提前启动的独立工作**：
- 2132（精确 XML/JSON 解析）只依赖 2118，可在 M9 期间开工。
- 2150（O/L/N 合并算法升级）只依赖 M12，可与 M13 并行。
- 2156（迁移 dry-run）只依赖 2121/2129/2130/2136，可在 M14 前启动。
- 2122 的 mock 端点与 2123 的隔离 fixtures 可在对应后端接口冻结前先搭骨架，但不得提前合并为“已完成”。

## 4. M9 卡级计划

| 卡 | 目标/接口 | 文件面 | 验收 | 风险/并行 |
|---|---|---|---|---|
| INH-2117 M9-00 | 冻结设计基线：复核附件 SHA；冻结 L9 合并后的 main SHA；逐文件差异审计；Q1–Q6/A01–A17 追踪矩阵；**导出原型屏幕/组件/状态清单（§1.3）并把每屏映射到卡**；产品未决点 ADR；不写业务代码 | `docs/MCChecker_V3_BASELINE.md`、`docs/adr/`、原型 SHA/屏幕清单 | 两份附件哈希一致；Q1–Q6/A01–A17 每项有落点；屏幕清单覆盖宿主 8 屏 + 配置域 5 tab + 文件/空间/错误态；ADR-007–011 有结论；零业务代码；HTML 模拟不算证据 | 设计与仓库漂移；main 移动；附件签名过期；根卡不可并行 |
| INH-2118 M9-01 | 固定 main commit；比较 V3 HTML/MD、M5–M8 旧域和原工具入口；旧 changes/upgrade/lifecycle/audit/delivery/registry 分 replace/retire/read-only；保留 remarks/review；旧页/token 不删；**产出 UI parity matrix（原型屏/组件→卡→验收）并标出原型有而卡没有的缺口** | `docs/MCChecker_V3_CONTRACT.md`、`docs/MCChecker_V3_UI_MATRIX.md`、`docs/architecture.md`、旧 spec 标注 legacy | 端点/模块 100% 分类；无删除动作；契约写清 v1/v3 边界；UI parity matrix 覆盖 §1.3 每屏且缺口有卡/ADR | 过早停写旧域；漏 DL/settings；UI 缺口被后端卡掩盖；串行 |
| INH-2119 M9-02 | `workspace_catalog`、Workspace CRUD、default profile 作用域、`GET /api/v3/session`、`W/context`；沿用全局 principal/role/permissions；禁 WorkspaceMembership；**工作区对话框名称 1–50、说明 ≤300、全局唯一、仅管理员可建/改名；新工作区得到空 default 且不继承其它工作区** | 新增 `app/services/workspace_catalog.py`、`app/server/api_workspaces.py`；改 `context.py`、`api_session.py`、`router.py`、`config.py`、`tests/conftest.py` | 新建 W 得空 default profile；两个 W 同名 profile 指向不同根；切 W 的 principal/capabilities/revision 不变；普通用户不能建空间；同名 409 字段级报错 | ID/路径穿越；catalog 损坏；2121 可在 schema 冻结后重叠 |
| INH-2120 M9-03 | 从 path/header/query 解析 wid；原 `/api/v1/p/{pid}` 需 `X-Workspace-Id`；无 wid 写 428、冲突 409、跨区枚举 404；raw/download/task retry 不能漏检；旧无 wid 链接只读固定 legacy W | `app/server/http.py`、新增 `app/services/workspace_context.py`、`router.py`、`legacy.py`、各 `api_*.py`、`tests/test_workspace_context.py` | 路由分类表全覆盖；无旁路；legacy 只读；428/409/404 语义正确；失败后权威 SHA 不变 | 路由覆盖缺口；后台任务/重试绕过；404 信息泄露；串行 |
| INH-2121 M9-04 | W/P 安全根；`workspace-config-state`（不透明 ID/CAS/blob/hash/幂等回执）；固定锁序；损坏只读；旧数据 dry-run 路径；W1 不能读写 W2 | 新增 `app/services/workspace_store.py`；改 `config_repository.py`、`storage.py`、`fsutil.py`、`locks.py`、`config_store.py`、`config_backup.py`、`tests/conftest.py` | 根隔离；跨区幂等 key 不冲突；中途磁盘故障不半发布；损坏 JSON/hash 只读；测试不写真实 `data/` | 锁序死锁；全局 active profile 泄漏；`DATA_SUBDIRS` 漏 workspaces；2119 后启动 |
| INH-2122 M9-05 | 顶栏 **工作区→机型** 顺序；工作区选择/管理/新建/改名；路由带 `workspace` query；各空间 tabs/最近页/机型记忆；**dirty 确认覆盖所有视图和打开的 modal**；`contextEpoch` 过滤旧 fetch/poll/toast/自动保存；导航改“配置管理”；registry 深链迁移到 configspace；**移动端按原型 1200/760/520 断点收起 caption/搜索/品牌**；切换后恢复该 W 的上次路由 | `app/web/assets/js/core/api.js`、`views/shell.js`、`app.js`、`template.html`、`index.html`、`mock-api.js`、`tests.js`、相关 views | 切换后文件/搜索/配置/DL 内容全变而身份不变；晚到响应不覆盖；草稿不丢；默认速览；旧 `mc.tabs.<pid>` 迁移为按 W 作用域；`ALL PASSED(n)` | 两套脚本清单；mock 漂移；localStorage 迁移；dirty 检查漏 modal；mock 可提前、合并在 2120 后 |
| INH-2123 M9-06 | W1/W2 同名 default/filename/configId/profile/param fixtures；旧 API + v3 + 下载 token/cookie；**双浏览器/同浏览器切 W 的 UI 隔离、旧响应不覆盖、非管理员只读横幅**；Q1-A/B/C/D、A13；失败后权威 SHA 不变；缺口单开卡不降断言 | `tests/test_v3_workspace_isolation.py`、`tests/authz_fixtures.py`、`tests/conftest.py`、`app/web/tests/`、`scripts/check_py39.sh` | 门禁绿；W1/W2 隔离；受限身份原合法临时上传/收藏/remarks/DL 不被误伤；证据入 PR | H3 fixture 漏 `DATA_SUBDIRS`；Linear 无 blocks；fixture 可提前、门禁最后 |

## 5. M10–M15 卡表（38 张）

文件面代号：P=storage/profile/context；F=files/parser/upload/temp/recovery；C=config/repo/catalog/merge/export；U=web/shell/api/mock/views；T=tests/scripts/docs；W=workspace/router/http。`blockedBy` 为 Linear 卡面的精确值（省略 `INH-` 前缀；`1910` 为 M4-08）。

### M10 原功能全工作区适配（6 张）

| 卡 | 目标 | blockedBy | 文件面 | 量 | 验收要点 |
|---|---|---|---|---|---|
| 2124 | 原 Profile 机型 CRUD/复制/切换按 W 隔离 | 2120,2121 | P | L | 两个 W 同名 profile/数据不同；复制不跨区；删机型进恢复区；旧包络不变 |
| 2125 | 速览/收藏/搜索/结构树全作用域 | 2122,2120,2124 | P/F/U | M | 缓存/索引键含 W+P；零交叉命中；Ctrl+K 保留；服务端强制 |
| 2126 | remarks 建议/管理员审阅隔离与保留 | 2120,2124 | P | M | 同名文件不串单；源 SHA 变 409；旧 admin 名单全局一致；UC-20–24 不退化 |
| 2127 | DL 计算/系数绑定/图表/历史隔离 | 2120,2124 | P | M | 数学不变；同名 DL 绑定独立；并发冲突只影响当前 W/P；普通用户能力不收紧 |
| 2128 | 更新源/定时后台任务/工具/全套设置隔离 | 2120,2124 | P | L | 任务建时捕获不可变 W/P；切换不改排队目标；全局 IP/管理员不复制 |
| 2129 | F01–F28 原功能回归与数据守卫 | 2126,2127,2125,2128 | T | L | 两空间两机型多角色回归；A01–A05 PASS；原数据 sha 不变；缺口单开修复卡 |

### M11 唯一文件与精确版本（6 张）

| 卡 | 目标 | blockedBy | 文件面 | 量 | 验收要点 |
|---|---|---|---|---|---|
| 2130 | FileResource/FileVersion 身份 + v1 兼容层 | 2121,2124,2120 | F | L | 每个 W/P/name 稳定 file_id；覆盖后 file_id 不变、version_id 变；旧 `/files`、`/archives`、`/records`、`/raw` 可用 |
| 2131 | 左下文件管理：上传/URL/临时/归档/记录/下载不回归 | 2130,2125 | F | L | **唯一宿主文件列表；配置域禁止第二上传库**；覆盖确认；相同内容不归档；临时 TTL；INI/CFG/CONF/TXT 仅原文页；全部带 W/P |
| 2132 | 精确 XML/JSON 定位与安全解析 | 2118 | F | M | XML ns URI+occurrence；JSON RFC6901；拒 DTD/entity/重复键/NaN/超深；失败零写入 |
| 2133 | 精确来源引用/current 竞争/删除 tombstone | 2132,2121,2130 | F/C | M | 引用 W/P/file_id/version_id/sha；三次覆盖后旧引用仍读同一 sha；删除阻断新引用、保冻结值 |
| 2134 | 新旧文件页桥接/唯一版本选择器 | 2131,2122,2130 | U | M | `file-selector`/`file-versions/{vid}`；**选择器显示 W/P、版本、sha、时间；临时资源先保存**；跨 profile 不可引用；旧文件页不退化 |
| 2135 | 统一文件集成/hash 安全门禁 | 2131,2133,2134,2129 | T | M | update/upload/import-url/review/delete/record 全链路 hash 一致；无半 current pointer；Q3-A/B、A03、A12 |

### M12 配置管理 V3 内核（7 张）

| 卡 | 目标 | blockedBy | 文件面 | 量 | 验收要点 |
|---|---|---|---|---|---|
| 2136 | FixedConfig/SoftwareRelease/真实版本图 | 2120,2121 | C | L | `P/configs` CRUD、`releases`、显式 predecessor/order、`graph`；同 W/P code/release 唯一；新版 Value unset |
| 2137 | 参数身份/定义修订/目录/BindingSet 快照 | 2136,2132 | C | L | stable ID 不随名称/目录变；128 层目录；同叶名异层级并存；历史定义不被最新目录改写 |
| 2141 | 值模型/草稿批量自动保存/CAS/防丢失 | 2121,2137 | C/U | M | membership/enabled/value_state 与 typed Decimal；≤100 行/ETag/幂等；0/false/'' ≠ unset/null/removed |
| 2138 | Canonical D0 唯一冻结事务 | 2141,2137 | C | M | 每版只冻结一次；并发双 freeze 一个 `DEFAULT_ALREADY_FROZEN`；同键重放同回执 |
| 2139 | Bn 草稿/固化/精确 source/不可变 head | 2138 | C | M | 从精确 default/旧基线生草稿；Bn 不可变；只移动 head；If-Match 409；未完成草稿不可导出 |
| 2140 | Machine r1..rn 连续分支/初建 head=null | 2139,2138 | C | M | 机台号配置内唯一；初建 head=null；publish 才得 r1；每台独立 parent；不暗示物理部署 |
| 2142 | 配置管理工作台/版本选择器/后端闭环 | 2134,2136,2140,2137,2141,2139,2138 | U/C/T | L | **固定配置目录 + 版本演进图 + 四级选择器 + 五 tab（版本演进/参数工作台/参数总览/模板导出/配置历史）**；空配置→690→D0→冻结→B1→MC-001→r1；非管理员只读横幅；页面写“未下发真实机台” |

### M13 参数总览/树/大批量提取（7 张）

| 卡 | 目标 | blockedBy | 文件面 | 量 | 验收要点 |
|---|---|---|---|---|---|
| 2143 | 列表/原树同款参数视图双模式共享状态 | 2142,2141 | U | M | **列表/参数树切换；目录树搜索/展开/折叠/分页 25/50/100；详情面板复用原查看器**；同 revision/draft/ETag/选中 parameter_id；键盘 ↑↓←→/Enter |
| 2144 | 参数总览：D0/Bn/各机台真实值和属性矩阵 | 2137,2140,2138,2139 | C/U | M | **默认值/基线值/各机台列；软件版本、对比、基线选择器；仅看变化**；不拿 D0 冒充缺失 Bn；跨版机台 current 不当本版 rn；每格绑 revision_id |
| 2145 | 差异比较与历史历程 | 2144,2140 | C/U | M | **差异标签：新增/停用/改名/层级路径/类型/单位/约束/数值/intent；参数历程弹窗**；D0 previous=前一发布 D0，Bn/rn previous=真实 parent；null/unset/未纳入分开 |
| 2146 | JSON/XML 全量树/增量身份匹配 | 2137,2132,2130 | C | L | **解析/身份匹配：new/existing/conflict；add/link/skip/pending；只提叶名+完整层级**；不生成释义/不导值；3000+；同名异路径/歧义/类型冲突分类；预览零写 |
| 2147 | 50/100 行预览/全计划选择/冲突 UX | 2134,2142,2146 | C/U | M | **提取 modal：50 行/页默认、100 可选；全部/新增/冲突/已有筛选；新增/关联/跳过逐行决议；计数与提交禁用态**；服务端全量计划；分页不改选择；stale 保决议 |
| 2148 | 批量原子新增/重复增量/补本版缺失绑定 | 2147,2121,2138,2146,2141 | C | L | **提交前重新读来源并核对 current/hash；includeExisting 默认关；来源变化 SOURCE_CHANGED**；单事务 CAS；三次导入不重复；已有值不动；第 2500 行故障整批零提交 |
| 2149 | 大参数性能/分页分组/契约门禁 | 2148,2144,2143,2145 | T | M | 50k 首批≤500ms、3200 预览≤3s、原子提交≤5s、20 并发；记录机器；不改旧 F01–F28 基线 |

### M14 合并/值导入/导出/恢复（6 张）

| 卡 | 目标 | blockedBy | 文件面 | 量 | 验收要点 |
|---|---|---|---|---|---|
| 2150 | O/L/N 合并算法升级为 V3 分支/成员状态合同 | 2138,2141,2137,2139,2140 | C | L | 复用 `config_merge.py`；真实 D0/Bn/rn 引用+hash+成员状态+定义快照；无共同基准禁一键合并；不改 head |
| 2151 | 合并冲突 UX/决议计划/新草稿 | 2150,2142 | U/C | M | **预览合并 modal：主线跨版/同版同步/机台跨版徽标、目标基线选择、仅未决、O/L/N 三栏、逐行决议、生成合并草稿**；冲突未决不提交；stale 重算；其他机台不动 |
| 2152 | 参数数值读取：独立于定义提取的导入计划 | 2148,2133,2141 | C | M | **导入参数 modal：目标修订选择器、空间文件/上传文件、映射、重复检测**；需 source_version_ref；未冻结 D0 更新 canonical 草稿，其余生新草稿；缺失不删；与定义提取分离 |
| 2153 | 精确模板导出 XML/JSON/INI | 2139,2132,2138,2133,2140,2144 | C | M | **四步：配置修订→选择模板→核对映射→预览并下载；模板来源、停用字段确认、preview token 绑 digest、新文件不覆盖原文件**；冻结修订+精确模板+已确认绑定 |
| 2154 | 参数历程/事件审计/恢复/撤销 | 2140,2128,2139,2145 | C | M | **配置历史（分支/版本筛选、事件详情、撤销最近可逆）+ 配置记录（当前工作区事件）+ 参数历程；恢复产新草稿**；事件按 W 追加不可变；发布/冻结/下载不可撤 |
| 2155 | 配置管理全流程/旧 config 域停写 | 2129,2135,2126,2148,2154,2143,2144,2151,2152,2153 | C/U/T | L | 图→工作台→总览→文件→值导入/提取→合并→导出→恢复 E2E；旧 ChangeSet/delivery/registry/upgrade/lifecycle/audit 只读；remarks/review/file/DL 正常 |

### M15 迁移/总门禁/发布（6 张）

| 卡 | 目标 | blockedBy | 文件面 | 量 | 验收要点 |
|---|---|---|---|---|---|
| 2156 | 全域旧数据 dry-run/清单/备份先验 | 2136,2130,2129,2121 | T | L | 扫 profiles/config_management/sources/archives/records/favorites/remarks/DL/任务/全局人员；manifest 映射；dry-run 前后旧根 sha 相同；备份可恢复 |
| 2157 | 迁移执行/v1 兼容读/回退演练 | 2135,2156,2155 | T | L | 单 writer gate 原子迁入；分阶段写暂存后公布；旧路径只读；旧程序拒写新 schema；回退不扁平化多 W |
| 2158 | 跨 W 安全/权限/幂等/故障注入总门禁 | 2155,2157,2148 | T | L | 双空间同名对象四角色正反；SSRF/CSRF/XSS/穿越/DTD/重复键；current 竞争；3200+ 原子故障；200 并发；跨区全拒 |
| 2159 | 真实浏览器/原 UI/UX/全流程/无障碍 | 2129,2149,2155 | T/U | M | **按 §1.3 逐屏对照原型；1366/1920/375/768；键盘 only；模态焦点/ESC；异步态；3k 分页；冲突恢复；截图/视频/console；旧导航不缺失** |
| 2160 | 真实 XML/JSON/INI/离线/性能/设备边界 | 2149,2157,2153 | T | L | 脱敏真实模板导出后目标系统可读；20 并发/4C8G/SSD；50k/3200；INI 兼容；断网首访；Win10/11+3.9.11；设备证据 BLOCKED 不伪造 |
| 2161 | V3 发行包/文档/旧路线图收口/最终可回滚发布门禁 | 2157,2160,2159,2158,**1910** | T | L | 新 JS/API/schema/迁移/回退纳入 `make_release.py`；无 mock/凭据/样本；README/manual/API/ADR/角色矩阵；Q1–Q6+A01–A17 全 PASS 才 Done，否则 BLOCKED |

## 6. 跨切面工作流

- **Workspace context**：单一 resolver 负责 path/header/query；`RequestContext` 增 `workspace_id/workspace_ctx/epoch`；禁止全局 `set_active_profile` 作为业务根；缓存、token、任务、幂等、重试全部带 W+P；v1 无 wid 写 428、冲突 409、跨区 404；legacy 只读固定 W。
- **FileResource/FileVersion**：`file_index` 保存 file_id/version_id/sha；current 指针 + archive/record alias + tombstone；删除阻断新导入/提取/导出但保冻结值；blob 可共享，业务身份不共享；禁止 `copy-from-profile` 或第二上传库。
- **FixedConfig/参数**：每个 W+P 一个 `config_management` 仓储实例；D0 每版一次、Bn/rn 不可变、发布只移动 head；目录提取只提名称/层级，释义空、值 unset；值读取独立计划；草稿批量 CAS + 幂等回执。
- **前端 contextEpoch**：`MC.api` 支持 v1/v3 base 与 W header；路由带 wid；切换 W 时 epoch++，所有异步结果按 epoch 丢弃；localStorage 键含全局用户+W+pid；`mock-api.js` 与 `tests.js` 必须同 PR 补端点并更新 `EXPECTED_CASES`。
- **迁移**：dry-run（零写）→manifest→全域备份→旧根短时只读→建默认 W→切上下文→逐域比对→旧 config 域只读→开放多 W 写；全局身份/权限/IP/管理员不复制；不把多 W 扁平合并回旧 profile 根。
- **安全/性能**：XML 禁 DTD/ENTITY、JSON 拒重复键/NaN、路径穿越、跨区枚举 404；50k 首批 p95≤500ms、草稿≤800ms、3200 提取≤3s、原子提交≤5s、20 并发；Python 3.9.11、零运行时依赖、离线。

## 7. ADR / 待决事项

| ADR | 决策建议 | 时点 |
|---|---|---|
| ADR-001 | W 是全应用业务边界，profile 在 W 内；全局身份/权限唯一；禁 WorkspaceMembership/空间角色 | 2119/2120 前 |
| ADR-002 | 数据根 `data/workspaces/{wid}/profiles/{pid}`；catalog `data/workspace_catalog.json`；全局身份留原全局位置；备份/恢复纳入 workspaces | 2121/2156 |
| ADR-003 | FileResource/FileVersion + file_index + tombstone；blob 可共享、身份不共享；禁止第二上传库 | 2130 前 |
| ADR-004 | v3 前缀 + v1 适配；新域加 `MCHECKER_CONFIG_V3`（默认 off，M14-06 后 on）；旧域复用 `MCHECKER_CONFIG_MANAGEMENT`，M14-06 后默认 readonly；不封 v1 file/review/DL | 2120/2155 |
| ADR-005 | 前端 contextEpoch；localStorage 键含 uid+wid；v1/v3 base 可切换；mock 同 PR | 2122 前 |
| ADR-006 | 2123 加 blocks→2124；2161 保持被 1910 阻塞；1932 不升级为 block；estimate 不回填；2123 卡面 `check_py39.py` 按仓库 `.sh` 执行 | 开工前 |
| ADR-007 | 原型 XML 索引入口路径 vs MD 的 namespace URI+完整路径+occurrence；M11-03 定义同时满足显示与稳定定位的 canonical path，不能同时满足则由 M9-00 ADR 决定改哪一侧并同步下游 | 2118/2132 前 |
| ADR-008 | 原型“权限随当前空间切换”文案与全局权限实现冲突；统一“工作区”叫法，权限全局不变 | 2119/2122 前 |
| ADR-009 | 原型配置域 `spaceStore` 只按 W 存；后端要求 FixedConfig W+P；实现必须 W+P 隔离并补同 W 不同 P fixtures | 2121/2136 前 |
| ADR-010 | 原型配置域是 iframe+`MCCV3Host`；推荐原生视图+等价宿主 API，iframe 只作原型打包；不得出现第二文件库/第二工作区管理 | 2122/2142 前 |
| ADR-011 | 原型 50 行/页与卡面 50/100；默认 50、100 可选；原型模拟数据不参与性能/验收 | 2147 前 |

Linear relation 更新（2123→2124）需要在获批后单独写；若暂不更新，则用计划层门禁兜底：M10-01 开工前必须贴 2123 绿证据。

## 8. 验证矩阵

| 卡类 | 最小命令 | 判据 |
|---|---|---|
| 后端单域 | `.venv39/bin/python -m pytest -q tests/test_<area>.py` | 0 failed |
| 后端全量 | 带真实 `data/` 的主工作树：`MCCHECKER_REQUIRE_H3=1 .venv39/bin/python -m pytest -q` | 2571+ passed；H3 执法；0 failed |
| Python 3.9 | `PYTHON=.venv39/bin/python bash scripts/check_py39.sh` | exit 0 |
| 前端 | 按 `docs/know-how.md` §4 起 keep-alive 静态服务 + Chrome headless `--dump-dom` 打开 `tests/index.html` | `<title>ALL PASSED (n)</title>`；n 与 `EXPECTED_CASES` 同步；抖动重跑 |
| 原型 UI 对照 | Playwright/headless Chrome 按 §1.3 屏幕清单逐屏截图 + DOM/可访问性断言；1366/1920/375/768；键盘/模态/空错加载态 | 每屏有截图与断言；布局/文案/状态/交互与原型一致；差异有 ADR |
| 隔离/安全 | `pytest -q tests/test_v3_workspace_isolation.py tests/test_workspace_context.py tests/test_workspace_store.py` | 0 failed；失败后权威 SHA 不变 |
| 迁移/备份 | `scripts/config_backup.py backup-full`、`verify-full`、`restore-full --dry-run`（2121/2156 扩展 workspaces 后） | 零写；可解析；计数/hash 一致 |
| 性能 | `scripts/bench_api.py`、`bench_search.py`、`bench_xml.py` | 记录硬件、规模、20 并发、p95 |
| 发布 | `python scripts/make_release.py --out …` + `--verify-only` | 离线包无 mock/凭据；可回滚 |

## 9. 首批动作

1. **先合并 L9**：在 `chore/fix-main-red-20261028` 提交现有 9 个修改文件（mock-api、4 份文档、3 个测试），跑全量 2571、前端 96、`check_py39.sh`，PR 合入 main；记录新的 main SHA。L9 是 V3 的硬前置。
2. **建 `feat/mc-v3`**：从 L9 合并后的 main 创建；设保护；PR 模板固定卡号、范围、文件面、命令、证据、回滚；base 恒为 `feat/mc-v3`。
3. **落计划文档**：以 docs-only PR 把 `docs/MCChecker_V3_PLAN.md` 合入 `feat/mc-v3`；不改业务代码。
4. **定 ADR/relations**：确认 ADR-001–011；批准后更新 2123→2124 block、保留 2161←1910、记录 1932 非阻断；不回填 estimate。ADR-007–011 的 UI/身份冲突必须先有结论，再开 2119/2122/2132/2142/2147。
5. **M9-00 启动条件**：main 已绿、计划已合、分支已建、附件可下载且 SHA 复核、2117 移 In Progress、冻结 L9 后 main SHA；**下载原型 HTML 并按 §1.3 导出屏幕/交互清单，逐屏映射到卡**；2117/2118 只出文档，不写业务代码；M9 各卡只进 `feat/mc-v3`，不进 main。
6. **M9 顺序**：2117→2118→2119→2121→2120→2122→2123；2119 与 2121 可在 schema 冻结后局部重叠，但 2121 卡面 blockedBy 2119，不能把并行当成无依赖；2132 可在 2118 后提前启动；M10-01 必须等 2123 绿。

## 10. 风险与不做的事

**风险**
- 2123 未被 Linear 强制，M10 可能抢跑；先按计划层门禁执行。
- 1910（M4-08）长期 In Progress 会卡住 2161；需要尽早决定补证据或书面豁免。
- M12 七卡串行是长关键路径；2140 前不能开 2144/2145/2153/2154，需提前准备 fixtures。
- 全局 active profile、缓存、任务、临时 token 泄漏到其它 W；所有入口必须显式 W/P。
- `tests/conftest.py::DATA_SUBDIRS` 漏 `workspaces` 会让 H3/备份/夹具假绿。
- `index.html` 与 `template.html` 脚本清单不同，mock 易漂移；前端卡必须同 PR 补 mock 与 `EXPECTED_CASES`。
- 旧 config 停写可能误伤 v1 file/review/DL；M14-06 只停旧配置编排，不封整个 v1。
- 50k/3200 性能数字尚未实测；不拿原型数字当验收。
- 文件身份迁移若引入第二套上传库，会直接违反 Q3；禁止 copy-from-profile 和第二上传表。
- 原型与 MD 的 XML 身份、工作区权限文案、W+P 作用域、iframe/原生集成、50/100 分页尚未定；ADR-007–011 未闭合前不开对应实现卡，避免下游返工。
- 原型配置域是 W-only 的 `spaceStore`；直接照搬会在同一工作区两个机型间串配置/文件，必须在 2121/2136/2142 显式 W+P。

**不做**
- 不重写 117KB 设计；不在本轮启动 2117 或改业务代码。
- 不做 WorkspaceMembership、空间角色、私有空间、跨 W/跨机型复制、第二上传库。
- 不做物理机台下发/安装/回读；发布只移动管理系统 head。
- 不引入数据库、ORM、消息队列、前端框架、新运行时依赖。
- M9 不删旧页/token；不把 HTML 模拟当后端证据。
- 不写日期、人日、完成度；不把旧 M5–M8 Done 当 V3 完成。
