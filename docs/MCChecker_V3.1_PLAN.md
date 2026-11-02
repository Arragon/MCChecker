# MCChecker V3.1 开发计划（M16）

> **定位**：本文件是仓库内的 MCChecker V3.1（M16）开发计划镜像；事实源仍是 Linear 项目 `P-INH-14`、M16 执行索引 `0729099b-bc2b-4af3-9235-b569fac2a59f` 与 INH-2165 的 V3.1 HTML/MD 原件。生成 2026-11-02；**本轮只落计划与 ADR，不写业务代码、不改 Linear 卡状态/关系**。
> **事实源**：[Linear 项目 P-INH-14](https://linear.app/inhandy/project/mc-checker-v2-a9004135219e/overview)、[INH-2165 M16-00](https://linear.app/inhandy/issue/INH-2165)。
> **执行口径**：43 张卡、六条工作流 A–F、逐卡 JIT；`feat/mc-v3.1` 集成分支、一卡一 PR；不重开 M9–M15；不编造人日/日期/完成度；HTML 模拟数据不算生产验收。
> **本轮范围**：只落计划与 ADR；M16-00 基线、M16-01 契约、M16-02 协议文档等开工后用真实命令产出。

## Context

V3.0 的 M9–M15 已收口：M15 6/6 Done，其中 2160/2161 是 2026-11-02 负责人试用后的书面豁免（ACCEPTED-WITH-EXEMPTION），**不是外部证据 PASS**；V3.0 未合 main、未发布。M16-00 冻结时 `origin/main@b964c6e`、`feat/mc-v3@1c10582`、`feat/mc-v3.1@6b018a7`；main 在计划研究快照后新增 3 个非 V3 的 UI 提交，feat/mc-v3.1 领先 main 146 个 commit、落后 3 个。M16 是 V3.1 的唯一里程碑，共 43 张卡 `INH-2165–INH-2207`（M16-00…M16-42），全部 Backlog；设计事实源是 INH-2165 的两个附件，执行索引是 Linear 文档 `0729099b-bc2b-4af3-9235-b569fac2a59f`。

V3.1 的目标不是重写 V3，而是在既有 workspace、唯一 FileResource/FileVersion、FixedConfig、D0/Bn/rn、原 remarks/review、文件、DL、设置、任务之上，增量加入工作区级更新管理、SDA 会话自治与可信同步。**编号代表功能边界，不等于必须按数字串行；按真实 `blockedBy` 调度。**

## 0. 执行口径

- 事实源：Linear 卡面、M16 执行索引、INH-2165 的 V3.1 HTML/MD；仓库文档只做镜像，冲突时以 Linear/设计原件为准并回填。
- 卡级节奏：每卡开工时产出 JIT brief（范围、文件面、接口、测试、验收命令、风险），不预先给 43 张卡写逐步 TDD。
- 不编造人日、日期、完成度；用依赖、波次和相对工作量级 S/M/L 表达（S=单点改动，M=一个域内多模块，L=跨域/迁移/门禁）。Linear estimate 不回填。
- 一卡一 PR，base 恒为 `feat/mc-v3.1`；每卡必须有实现 diff、定向测试、全量回归、Python 3.9 与零依赖验证；改前端加 HTTP 前端页；动数据根/备份加 H3 与 `test_release.py`。
- M16-00/01/02 只产出基线、审计、契约与协议文档，不写业务代码；HTML 原型模拟数据不算后端验收证据。
- 旧 `remarks`/`review`、速览、文件、搜索、DL、工具、设置、机型、更新源、任务与旧链接必须保留；V3.1 只增量新增 Sync/Collector/Candidate，不替换整个 v1。

## 1. 基线与事实

### 1.1 Linear 与 43 卡

- M16：**43 张卡 `INH-2165–INH-2207`（M16-00…M16-42），全部 Backlog**；六条工作流 A–F；S01–S52 逐项映射；依赖已写成真实 Linear `blockedBy`。
- 卡号与 INH 号对照见附录 A；正文统一写 `M16-XX`，避免两套编号混用。
- 研究阶段统计：43 张卡、130 条 `blockedBy`（129 条 M16 内部 + 1 条到 M15-06/INH-2161）；落地时逐卡复核，不把研究统计当 Linear 事实。

### 1.2 设计输入

| 文件 | SHA-256 | 字节数 | 来源/校验 |
|---|---|---:|---|
| `MCChecker_V3.1_Frontend.html` | `88eca2cea8b8d90d2fdde1379b162ef6af3a312736b76c3bd6d412a3d984db82` | 997,085 | INH-2165 Resources；M16-00 当天用 `get_attachment(format=content)` 取回并重算 |
| `MCChecker_V3.1_Backend_Design.md` | `4b7f3ea0f02fce8dae4efd1f184a57d40104818c77a38113b15fe24dd3c4b413` | 184,805 | 同上 |

- 附件签名 URL 会过期；不把签名 URL 写进仓库。
- 冲突优先级：**V3.1 MD 正文 §1–22 > 随附 V3.0 附录同主题条款 > 更早 SDA 方案**。
- HTML 是完整 V3.0 原型上增量加入更新管理的单文件模拟器；模拟数据、浏览器存储、演示身份**不构成**生产实现或验收证据。

### 1.3 代码与分支基线

- `origin/main@b964c6e`（2026-11-02 实际；计划研究快照是 `429dafa`，之后 main 新增 3 个非 V3 的 UI 提交）。
- `feat/mc-v3@1c10582`（M15 代码 tip）；`feat/mc-v3.1@6b018a7`（计划/ADR 合并后）。
- main 不是 `feat/mc-v3.1` 的祖先；feat/mc-v3.1 领先 146、落后 3；`origin/main..feat/mc-v3.1` 为 161 个文件（+18176/-558）；main 上没有 V3 的 workspace/FileResource/D0-Bn-rn 代码（V3 文件 0，feat 23）。
- M16 集成分支 `feat/mc-v3.1` 从 `feat/mc-v3` 的 M15 豁免文档提交后 tip 派生；不先合 main。
- M15 豁免文档 PR #193 在 M16-00 冻结时仍 open；`feat/mc-v3.1` 已包含该文档提交 `df96271`；合入 #193 后用 merge commit 保留谱系。

### 1.4 M15 已完成能力与豁免

- M9–M14 全部 Done；M15 6/6 Done；2163/2164 两张补卡 Done。
- 2160/2161 为负责人 Lamires Asu 人工试用后的书面豁免；`docs/V3_RELEASE_GATE.md` 状态 `ACCEPTED`，不是 `PASS`。
- 未取得的外部证据、INH-1910、INH-1978、INH-1932、权威备份在 `/tmp`、零进包审计仍是已知风险；无发布、无真实 data 迁移。
- M16-42 可把 2161 作为“已关闭的历史门禁”引用，但不得写成“外部证据全部 PASS”。

### 1.5 仓库可复用能力与缺口

可直接复用：

- `app/services/updates.py`：下载、bundle 展开、策略守卫、解析、根指纹、原子保存、修订号；`unchanged` 只信 `SaveResult.unchanged`。
- `app/core/downloader.py`：`DownloadPolicy`、逐跳重定向校验、分块限流、Basic auth、日志脱敏。
- `app/services/tasks.py`：`TaskRunner`、内存 Task、互斥表、进度、停止/排空。
- `app/services/scheduler.py`：只投递不写盘的 `mcsched`、假钟测试缝、`STALE_RUN_DELAY_SECONDS=120`、`BUSY_RETRY_SECONDS=60`、`MAX_INTERVAL_HOURS=2160`。
- `app/services/file_versions.py`、`documents.py`、`config_v3_*`：FileResource/FileVersion、精确引用、D0/Bn/rn、草稿/发布、导入/导出、观测。
- `app/core/config_repository.py`：内容寻址 blob、原子 state、CAS、幂等回执、audit，可作为 SyncRun 持久化参考。
- 前端 `shell.js`、`core/api.js`、`views/settings.js`、`views/config-manager.js`、`ui/components.js`、单文件 `tests.js` 与 HTTP 验收模式。

必须新增或替换：

- Workspace 级来源/计划/运行记录：现有全部 per-profile；`update_all` 只吃一个 pid。
- 持久 SyncRun/SyncItem：现有只有内存 Task + 一个 `last_result`，重启即失。
- 候选/last-good：现无模型；`update_status.json` 不能表达候选 sha、来源、信任状态、尝试历史。
- Workspace 级任务/调度：`Task.profile` 必填、`Scheduler._Plan` 键 `(W,P)`、`TaskRunner.list` 按 `(W,P)` 过滤、revision 无 W 级分量。
- 凭据/会话存储：无 SecretStore、无加密、无 keyring/DPAPI/Keychain；`GET /sources` 目前对所有角色返回原始 URL。
- SDA collector：全无；Playwright/cryptography 只能进独立 collector 环境。

### 1.6 硬约束

- 主应用 Python 3.9.11 + 标准库，运行时零第三方依赖；Playwright/Chromium/cryptography 只允许在独立 collector 环境（Python 3.12）。
- Workspace 是业务数据边界，Profile 在 W 内；全局身份/权限唯一，不新增 WorkspaceMembership/空间角色。
- 唯一 FileResource/FileVersion；D0/Bn/rn 只能由既有 V3 事务移动；后台同步不得自动发布固化配置。
- 密码、Cookie、CSRF、密钥不得进入任务参数、日志、Git、Linear、业务备份、HTML、诊断 Blob、异常 trace。
- 出站固定 scheme/host/port/allowlist；禁 HTTPS→HTTP、任意跳转、任意 proxy、任意 JS/URL/路径、无鉴权 loopback HTTP。
- HTML/mock 不能替代生产验收；没有真实网络/平台证据不能标 PASS。

## 2. 分支、卡务与证据规则

- 集成分支：`feat/mc-v3.1`，从 `feat/mc-v3` 的 M15 豁免文档提交后 tip 创建并设保护。
- 卡分支：`mc-v31/<卡号>-<slug>`；PR base 恒为 `feat/mc-v3.1`；合并用 `--merge`；M16 收口后再开 `feat/mc-v3.1 → main` 的汇总 PR。
- 非卡改动（文档、测试基建、脚本）用 `chore/...` 或 `mc-v31/...`，同样走 PR，不直接推 main 或集成分支。
- 每卡 PR 必须写：卡号、范围、文件面、改动、验证命令+结果、已知取舍/风险、回滚方式。未跑过的检查不得写成通过。
- 每卡 Done 前至少：定向测试 + 全量 `pytest -q` + `check_py39.sh`；改 `app/web/**` 加 HTTP 前端页；动 `data/`/备份加 H3 与 `test_release.py`；动发布包加 `make_release.py --verify-only`。
- Devin Review 从 M11-06 起实际 `Full review skipped: trial expired`；`gh pr checks` 状态可能仍是 pass。每次合并前看检查正文；若 skipped，按本地门禁合并，并在 PR、Linear 完成评论、本地台账三处写明“无独立机器评审”。
- 证据格式：命令 + 原始输出 + 解释器/硬件/并发 + commit/artifact SHA；不粘贴过期数字。

## 3. 决策冻结

| 编号 | 决策 | 推荐值/决议 | 影响卡 |
|---|---|---|---|
| D1 | 分支与基线 | `feat/mc-v3.1` 从 `feat/mc-v3` 文档提交后 tip 派生；不合 main；记录 main/feat 双 SHA | 全部，尤其 00/01/42 |
| D2 | M15 豁免口径 | 2160/2161 = `ACCEPTED-WITH-EXEMPTION`，不是 PASS；2161 Done 只满足 42 的 blockedBy，不代表 V3.0 已发布 | 00/01/42 |
| D3 | 代码基线 | `origin/main@b964c6e` + `feat/mc-v3@1c10582` + `feat/mc-v3.1@6b018a7`；M16-01 审计实际 HEAD | 01/03/05/07/08/10/11/12/14/18/23/24/25/26/30/37/42 |
| D4 | 卡面调整 | 保持 43 卡原样；只在计划/ADR 写建议，不改 Linear 状态/关系 | 全部 |
| D5 | API 命名与归属 | 新 API 统一 `/api/v3/workspaces/{wid}/...`；废卡面 `/api/v1/w/...`；source-check→24，catalog→16+30，session-candidates→19+30，connection create→04+30 | 05/08/10/11/12/23/30/32–36/38 |
| D6 | 06/37 迁移边界 | 一个 `sync_migration` service + 一个 CLI；06 只 plan/dry-run，37 才 execute/backup/rollback | 06/37/42 |
| D7 | TaskRunner/SyncRun | 新增 `sync_run` 类型；Task 带 workspace；网络在锁外、提交在 W/P 锁内；幂等键→run；只重试 failed/blocked | 11/12/23/24/35/39 |
| D8 | SecretStore/operator | 全局 `integration_credentials_manage` 能力；密文根在 `data/` 外；AAD 绑 W+connection+generation；不做空间角色 | 18/19/30/34/38/42 |
| D9 | 前端模块 | 31 建壳 + `core/sync-api.js`；32–36 各自独立 tab 文件；单 `tests.js` + `SYNC-*` + `EXPECTED_CASES` | 31–36/40 |
| D10 | 外部证据 | E1–E5 ledger；真实 SDA 暂不可得 → 02/20/41/42 `BLOCKED-EXTERNAL`；HTML/mock/CI runner 不得冒充 PASS | 02/14/20/41/42 |
| D11 | 数据根与备份 | 新增 `data/workspaces/<wid>/sync/` 与 `.../profiles/<pid>/sync/`；`DATA_SUBDIRS` 补 `sync`；备份覆盖 sync、排除 secret root | 03/06/11/18/25/37/38/42 |
| D12 | 发布与版本 | 两个 artifact：核心 zip（不加 Playwright）+ collector bundle（Python 3.12/Chromium/wheels/hash manifest）；版本号 42 冻结 | 14/42 |

## 4. 六条工作流与 43 卡表

证据层：`U` 单测/合约，`H` 真实 HTTP/API，`B` 浏览器/UI，`X` 外部真实环境。计划覆盖不等于 PASS。

### A 设计与现场协议（3 张）

| 卡 | 交付边界 | blockedBy | 文件面 | 量 | 验收/证据 | 波 |
|---|---|---|---|---|---|---|
| M16-00 | V3.1 设计基线、HTML/MD 附件与 43 卡/S01–S52 追踪冻结 | — | T | S | 附件可下载且 SHA/字节一致；索引覆盖 S01–S52；blockedBy 可查；不改 M9–M15；零业务代码（U/T） | W0 |
| M16-01 | V3 主干差异审计、原功能保留矩阵与落地契约冻结 | 00 | T, P | L | F01–F28/Q1–Q6/S01–S52 映射到卡与具名测试；实际 SHA/调用点；无 data 写入（U/T） | W1 |
| M16-02 | SDA 登录/目录/表/文件脱敏契约与可续期心跳实验 | 00 | T | L | 三端点成功/失效/业务错误 fixture 可离线复放；每能力独立 canary；真实 idle 才置 heartbeat_validated；无秘密（U/X） | W1 |

### B 工作区来源、计划、run 与旧 HTTP（11 张）

| 卡 | 交付边界 | blockedBy | 文件面 | 量 | 验收/证据 | 波 |
|---|---|---|---|---|---|---|
| M16-03 | Sync 领域模型/枚举/验证与纯函数状态聚合 | 01 | M, T | M | 非法枚举/错 scope/空绑定拒绝；一成功一失败=partial、全失败=failed；roundtrip/日志无 secret；Py3.9 可导入（U） | W2 |
| M16-04 | Workspace 连接注册表、允许的 SDA Origin/能力/资源范围 | 03 | M, P, A, T | M | A/B 同名 connection 隔离；https→http、端口变化、非 allowlist 拒绝；旧 revision 409；备份只含非敏感字段（U/H） | W3 |
| M16-05 | 更新源稳定身份、CRUD/分页筛选及 CAS API | 03, 04 | M, A, T | M | A/B 同名 source 分页/过滤正确；跨 W 猜测 404/403；双客户端同 revision 仅一方成功；删除保留 FileResource/history（U/H） | W4 |
| M16-06 | 旧更新源/record_url/keyword/按机型计划迁移 dry-run | 05, 08 | M, X, T | L | 两个旧 profile 6/48h 迁移后行为不变；重复迁移/断点重试幂等；dry-run 零写；旧文件/record/archive SHA 不变（U/H） | W5 |
| M16-07 | 来源目标绑定 FileResource/FixedConfig/版本与机台身份 | 05 | M, F, T | M | 同名跨版本/跨 W 不错写；catalog 路径不一致拒绝；tombstone/revision 冲突拒绝；无 device_id 不伪造 rn（U/H） | W5 |
| M16-08 | 工作区更新计划与来源覆盖策略持久化/CAS | 03 | M, P, A, T | M | inherit/custom/manual 三态；批量 10 项含 1 stale 时 0 项生效；暂停全局自动后手动仍可；新 W 初始 auto off（U/H） | W3 |
| M16-09 | 按源到期计算、错过周期合并与 BUSY 重排 | 08 | S, T | M | 假时钟跨日/回拨/重启错过 5 周期只补一次；手动不移动锚点；manual/disabled 不入自动列；调度线程零权威写（U） | W4 |
| M16-10 | 更新范围解析、全量预览及冻结 read_set | 05, 07, 08 | M, A, T | M | workspace_all 含 manual/全部 profile，disabled 排除；预览后改 source/connection/target 409；A/B 同名不串；expired 拒绝（U/H） | W6 |
| M16-11 | 持久 SyncRun/SyncItem/事件日志与清理保全 | 03 | M, P, T | M | 断电/截断 JSONL 启动恢复最后完整事件；Task 清理后 run 仍可查；30 天/2000 run 上限保护未完成；日志白名单无 secret（U） | W3 |
| M16-12 | SyncRun 与 TaskRunner 整合、去重/取消/终态聚合 | 10, 11 | S, T | M | 一成功一 blocked=partial、全失败=failed；取消保留已提交项；同幂等键同参数同 run；旧任务 API 回归（U/H） | W7 |
| M16-13 | 普通 HTTP 文件/记录下载兼容适配与旧行为回归 | 05 | S, T | M | 旧 URL/XML/zip/record/keyword/unchanged 一致；HTTP 500 不触发 SDA 登录；失败不覆盖 current；变化才归档（U/H） | W5 |

### C 独立浏览器采集器与会话自治（9 张）

| 卡 | 交付边界 | blockedBy | 文件面 | 量 | 验收/证据 | 波 |
|---|---|---|---|---|---|---|
| M16-14 | 独立采集器环境、离线依赖锁定、受管子进程生命周期 | 00, 01 | C, X, T | L | 核心 3.9 不 import 三方；无 collector 仍可启动普通 MCChecker；SIGTERM/Windows 进程树无孤儿；离线安装留证（U/X） | W2 |
| M16-15 | Collector 私有 stdio IPC 帧、分块校验与取消/超时 | 14, 03 | C, M, T | M | 1MB 分块完整重组并 sha 校验；缺块/错序/大帧/版本不匹配失败；cancel/disable 生效；stdout 无协议外文字（U） | W3 |
| M16-16 | SDA 三端点协议适配、合法请求模板与错误语义 | 15, 02, 04 | C, T | L | 目录/表/文件成功/失效/业务错误 fixture；200 登录页/错误 JSON/错资源身份拒绝；同源跨端口/重定向拒绝携带凭据（U） | W4 |
| M16-17 | 普通账密登录自动化与受保护能力核验 | 14, 16, 18 | C, T | L | 每连接隔离 BrowserContext；密码错/表单变/非法跳转可区分；两连接 Cookie/CSRF 独立；无密码/Cookie 泄露（U/X） | W5 |
| M16-18 | 独立加密 SecretStore、部署密钥与运维授权隔离 | 04, 14 | C, M, A, T | L | 无独立运维授权时读写凭据 403；密文篡改/密钥缺失停用；备份/源码/日志/任务无 secret；撤销后重启不复活（U/H） | W4 |
| M16-19 | 候选账密验证、CAS 激活/轮换/撤销与 auth_epoch 竞态 | 17, 18, 15 | C, M, T | L | 晚到 probe 不能激活/发布；旧 epoch 不覆盖新 Cookie；错密码不替换原密文；撤销失败不谎称已删除（U） | W6 |
| M16-20 | 会话保活、续期证据和 Cookie/CSRF 正确轮换 | 02, 17, 19, 16 | C, M, T | L | 仅心跳跨真实 idle 仍可取数才 heartbeat_validated；失败显示未验证；成功心跳不改 last_verified；同连接最多一个心跳（U/X） | W7 |
| M16-21 | 会话失效 singleflight 自动重登、密码错误锁存与退避 | 19, 20 | C, M, T | L | 10 源同时过期只一次 login；成功最多一次安全重放；错密码跨 5 周期登录次数仍 1；网络故障有界退避（U/X） | W8 |
| M16-22 | SDA 内容读取真流式限额/重定向/下载异常保护 | 15, 16 | C, T | M | 慢流/大 Content-Length/未知长度/解压炸弹写前终止；跨域/HTTPS 降级/错误 HTML/500 不泄露 Cookie；staging 清理（U） | W5 |

### D 可信同步提交与候选/观测（7 张）

| 卡 | 交付边界 | blockedBy | 文件面 | 量 | 验收/证据 | 波 |
|---|---|---|---|---|---|---|
| M16-23 | 统一 Sync 执行编排：HTTP/SDA 路由、任务阶段与恢复 | 09, 10, 11, 12, 13, 21, 22 | M, S, C, T | L | 混合 SDA+HTTP 一个认证失败、其他成功=partial；手动/定时/重试同一 runner；切页面不改冻结 W/P；满池不死锁（U/H） | W9 |
| M16-24 | SDA/HTTP 响应与内容双重校验、旧文件/记录安全提交 | 07, 13, 16, 22 | M, F, T | L | 200 登录 HTML/错误 JSON/500 空 body/错表 ID 不产生业务文件；并发手改 current 不覆盖；unchanged 只刷新核验；真实变化归档（U/H） | W6 |
| M16-25 | last-good 精确来源证据/新鲜度与断电恢复一致性 | 11, 24 | M, P, T | M | 保活/登录 canary/目录成功不改 last_verified；同内容只刷新 verified；提交后 status 写失败按 receipt 恢复；人工修改不让 last_good 套到 current（U/H） | W7 |
| M16-26 | 默认配置更新候选创建、稳定去重、关联统一文件版本 | 07, 24, 25 | M, F, A, T | M | 同 source/hash 多次同步同候选；新 hash superseded；候选可追溯 FileVersion/hash；D0/Bn/rn 已发布 head 字节不变（U/H） | W8 |
| M16-27 | 候选三方参数差异计划/类型校验/草稿 CAS 预览 | 26 | M, T | M | A=B 取 C、C=A 保留 B、B=C unchanged、三方不同需决议；null/0/false 不混；预览后 draft/head 变化 409；D0 冻结不提供覆盖（U） | W9 |
| M16-28 | 候选决议一次事务写入 V3 草稿/审计与幂等回执 | 27 | M, F, T | L | 10 参数 1 个类型无效时整体 0 写入；双浏览器同幂等 key 同正文一份；head 变化 409；D0 冻结/Bn/rn head 不动（U/H） | W10 |
| M16-29 | 机台实际值的可信观测接入与 last-good 观测指针 | 07, 24, 25 | M, F, T | M | 无 device_id 明确阻断机台观测；机台数据不变机型基线；解析失败观测保留审计；同设备乱序不倒退 last_good（U） | W8 |

### E 真实后端 API 和前端接线（7 张）

| 卡 | 交付边界 | blockedBy | 文件面 | 量 | 验收/证据 | 波 |
|---|---|---|---|---|---|---|
| M16-30 | 连接与认证管理 API：安全摘要、异步操作、权限与幂等 | 04, 18, 19, 20, 21, 15 | A, M, T | L | 只读用户看状态不看 account/secret；无运维授权 403；跨站/伪造 Host/跨 W 拒绝；密码错锁存；异步 202 op_id（U/H） | W9 |
| M16-31 | 更新管理工作区级导航、旧设置入口重定向与快捷入口 | 01, 03 | W, T | M | 原 V3 六配置子页/文件工具可达；旧书签跳唯一新页；切 W 无残留；1366×768 主列表有空间；键盘/深链正常（U/B） | W3 |
| M16-32 | 更新源真实列表筛选/编辑抽屉/目录选择/单项更新 | 05, 07, 31 | W, T | L | 搜索/分页/排序/局部刷新；另一客户端 409 保留草稿；catalog ID/路径不一致拒绝保存；只读用户不能编辑；旧 record_url/keyword 不丢（U/B） | W6 |
| M16-33 | 更新计划与工作区全量预览/手动/批量任务真实交互 | 08, 10, 12, 31 | W, T | L | 仅失败筛选下 workspace 全量仍含所有启用源/全部 profile/manual；策略变化 409；暂停自动手动仍可；刷新/切 W run 仍属原 W（U/B） | W8 |
| M16-34 | 连接设置/保活/自动重登/密文状态的真实交互界面 | 30, 31 | W, T | L | 无运维权限不出现账密操作，直接 API 403；开关独立；错密码只人工重试；换凭据失败不回显；切 W 不整页刷新（U/B） | W10 |
| M16-35 | 持久运行记录、任务日志、失败项重试与脱敏 Blob 下载 | 11, 12, 23, 25, 31 | W, T | L | Task 清理后 run 仍可查；A 日志不展示 B；partial 只重试失败源；取消后已提交项可见；诊断无 secret（U/B） | W10 |
| M16-36 | 更新候选差异/冲突决议/应用草稿前端与 V3 联动 | 26, 27, 28, 31 | W, F, T | L | D0 冻结候选无覆写 D0 操作；无未决冲突才可应用；并发草稿 409 不丢决议；成功只标“已写入草稿”，发布跳 V3（U/B） | W11 |

### F 迁移、安全、浏览器与发布证据（6 张）

| 卡 | 交付边界 | blockedBy | 文件面 | 量 | 验收/证据 | 波 |
|---|---|---|---|---|---|---|
| M16-37 | 旧来源和机型计划无损升级、备份、恢复/回退脚本 | 06, 07, 08, 25 | X, M, T | L | 双空间双同名 profile/6/48h/包正则/历史记录迁移前后 SHA 相同；dry-run 零写；重复 upgrade 不重复；崩溃恢复/旧版本读；备份不复制密钥（U/H） | W8 |
| M16-38 | 统一服务端安全验收：Workspace 隔离/凭据权限/CSRF/SSRF/脱敏 | 30, 22, 23, 24, 28, 29 | T, A | L | 同名 source 跨 W 读取/提交/日志/候选/连接互不可见；无运维授权不能导入/替换/撤销；CSRF/SSRF/重定向拒绝；日志/Blob/trace/Task/包/备份/git 无 secret（U/H） | W11 |
| M16-39 | 崩溃/断线/并发故障注入、receipt 一致性与终态恢复 | 15, 21, 23, 24, 25, 28, 29 | T, M | L | kill collector/断 IPC/中断解压无半 file/登录 HTML 归档；文件已提交但 status 写失败按 receipt 恢复；双 W 并发/同源二次提交/撤权/改 binding/关连接旧回包不落数据（U/H） | W11 |
| M16-40 | 真实浏览器多角色/工作区/移动窄屏与原 UI 全回归 | 31, 32, 33, 34, 35, 36 | T, W | L | 旧 V3 F01–F28 导航/文件/收藏/搜索/remarks/DL/参数界面可达；更新管理 E2E 截图/步骤；只读/管理员/运维、跨空间、草稿未保存/409、键盘焦点、375/768 无页面级溢出（B） | W12 |
| M16-41 | 内网端到端协议/真实闲置保活/过期自动重登验收 | 02, 20, 21, 23, 24, 25, 26, 29, 34, 37, 38, 39, 40 | T | L | 真实登录→probe→表/文件→提交/候选；保活跨真实 idle；失效一次重登安全重放；坏密码锁存；CA/代理/域跳转/超限/网络故障有步骤/时间/脱敏日志；S01–S52 归因（X） | W13 |
| M16-42 | M16 全量回归、离线发布包、运行指南与最终门禁 | 37, 38, 39, 40, 41, 2161 | X, T | L | Windows/Linux 断网安装/首次启动/SDA 关闭普通功能/SIGTERM 清理；全仓 pytest+前端真实浏览器+S01–S52 PASS 或明确阻塞；发布包无 secret/data/mock；回退演练；密文/key 错误停用（U/B/X） | W14 |

## 5. 波次、关键路径与提前启动

### 5.1 波次

| 波次 | 卡 | 数量 |
|---|---|---:|
| W0 | M16-00 | 1 |
| W1 | M16-01、M16-02 | 2 |
| W2 | M16-03、M16-14 | 2 |
| W3 | M16-04、M16-08、M16-11、M16-15、M16-31 | 5 |
| W4 | M16-05、M16-09、M16-16、M16-18 | 4 |
| W5 | M16-06、M16-07、M16-13、M16-17、M16-22 | 5 |
| W6 | M16-10、M16-19、M16-24、M16-32 | 4 |
| W7 | M16-12、M16-20、M16-25 | 3 |
| W8 | M16-21、M16-26、M16-29、M16-33、M16-37 | 5 |
| W9 | M16-23、M16-27、M16-30 | 3 |
| W10 | M16-28、M16-34、M16-35 | 3 |
| W11 | M16-36、M16-38、M16-39 | 3 |
| W12 | M16-40 | 1 |
| W13 | M16-41 | 1 |
| W14 | M16-42 | 1 |

合计 43 张；数量序列 `1,2,2,5,4,5,4,3,5,3,3,3,1,1,1`。波次是“依赖允许的最早开工”，不是日历排期。

### 5.2 关键路径

- **P1 严格边链（主关键路径）**：`00→01→14→15→16→22→24→25→26→27→28→36→40→41→42`。
- **P2 资源脊线**：`00→01→03→04→14→18→17→19→20→21→23→35→40→41→42`。其中 `04→14` 不是 blockedBy，是按人力投入排序的脊线；17 还依赖 16，20 还依赖 02/16，35 还依赖 11/12/25/31。
- **P2′ 严格边链交叉校验**：`00→01→03→04→05→07→24→25→26→27→28→36→40→41→42`。用于证明 15 波不是拍脑袋；不与 P1/P2 并列为第三条“主路径”。

M16-23 是第一道完整 Sync 编排门；M16-42 是最终发布门。

### 5.3 可提前启动

| 卡 | 最早波次 | 可提前做什么 | 不能提前宣称 |
|---|---|---|---|
| `M16-02` | W1 | 合成 fixture、协议文档、登录/心跳实验脚本骨架 | `heartbeat_validated=true` |
| `M16-08/11/31` | W3 | 策略模型、run 持久化、前端壳与路由 | 前面来源/计划 API 未完成时接真实数据 |
| `M16-18` | W4 | SecretStore、密钥隔离、403 测试 | 暴露连接 API |
| `M16-06` | W5 | 只读 dry-run、映射表、幂等计划 | 执行迁移、写 data |
| `M16-22` | W5 | 流式限额、重定向、下载异常测试 | 替代 M16-16 的三端点 fixture |
| `M16-32` | W6 | 列表/筛选/编辑抽屉壳、catalog picker 占位 | 没有 05/07 时声称真实来源 CRUD |
| `M16-34/35` | W10 | 拆文件后并行；34 连接 UI、35 运行记录 UI | 共用同一个 `update_management.js` 大文件 |
| `M16-38/39` | W11 | 测试 harness、故障注入 fixture | 前置卡未 Done 时标 Done |
| `M16-40` | W12 | 浏览器脚本、视口/键盘用例 | 用前端单测页代替逐屏截图 |

## 6. 跨切面契约

### 6.1 Sync 领域模型与状态机

- 对象：`SdaConnection`、`ConnectionRuntime`、`SyncSource`、`WorkspaceSyncPolicy`、`SyncRun`、`SyncItem`、`SyncCandidate`、`SourceFreshness`、`ConnectionEvent`。
- `SyncRun` 父记录 + `SyncItem` 子项；状态 `queued/running/partial/succeeded/failed/canceled`；`blocked` 子项计入 `partial`，全失败为 `failed`。
- 终态聚合是纯函数；取消只取消实际未完成项；已提交项保留；幂等键作用域到 run。
- 所有模型 roundtrip/日志/DTO 不含 password/Cookie/CSRF/key/完整请求 body。

### 6.2 Workspace 来源、计划与 read_set

- 来源、连接、凭据、run、候选均按 workspace 隔离；人员权限仍全局。
- `workspace_all` = 当前 workspace 全部 profile 的启用来源，**包含仅手动**，不包含 disabled；与搜索/列表筛选无关。
- 定时投递只含启用且非 manual；失败重试只取旧 failed/可恢复中断项与当前仍启用来源的交集。
- 预览默认 5 分钟；`start` 必须带 `plan_id + expected revision + Idempotency-Key`；来源/连接/策略/目标变化返回 409，要求重新预览。
- 调度保留 1–2160 整数小时；天=24h；启动 120s 延迟、BUSY 60s 重排；错过多个周期只合并补跑一次；手动更新不移动定时锚点。

### 6.3 Collector、IPC 与 SecretStore

- collector 独立目录（建议 `collector/`），Python 3.12/Playwright/Chromium/cryptography；主应用不 import，不进 `app/`，不进核心 `requirements.txt`。
- 主进程 ↔ collector 私有 stdio length-prefixed JSON envelope + 有序 base64 chunk；request_id/instance_id/epoch/deadline 强制校验；stdout 只允许协议帧。
- 一个连接一个 BrowserContext；登录后验证本连接需要的受保护能力；不绕验证码/MFA；不保存录屏/HAR/密码页面 trace。
- SecretStore 独立密文根（`data/` 外），与密钥分离；AAD 绑 workspace+connection+generation；密钥由部署端注入；无明文回退。
- `integration_credentials_manage` 是独立于 admin 的全局能力；业务管理员无该能力时读写凭据一律 403。
- collector 不直接写主 data；只回有界内容 + 安全元数据；主进程重新计算 SHA/大小并走既有提交事务。

### 6.4 候选、V3 草稿与 last-good

- 默认配置更新只形成 `SyncCandidate`；候选不复制原文到 `sync/`，只引用统一 FileVersion/blob。
- 候选三方：A=来源原值、B=本地草稿、C=新来源值；A=B 取 C，C=A 保留 B，B=C unchanged，三方不同需人工决议。
- 应用候选走 V3 草稿事务；D0 冻结后只能新建 Bn 草稿，不覆盖 D0；Bn/rn 发布 head 只由既有 V3 发布动作移动。
- last-good 只认真实目标内容通过业务验证并有持久提交回执；保活、登录 canary、匿名目录成功都不刷新 `last_verified`。
- 登录页/错误 JSON/500 空 body/错资源身份不得进入可见文件、正常观测或审计原文库。

### 6.5 旧 v1 更新源与迁移

- 旧 `config_mapping.json`、`schedule.json`、`update_status.json` 继续保留；M16 迁移只做加法，不删除旧 JSON/归档/记录。
- 旧 HTTP 下载路径复用 `updates.py`/`downloader.py`；普通 HTTP 500 绝不触发 SDA 登录。
- 06 只做 dry-run/映射/幂等计划；37 才执行/备份/回退；代码只留一套 `sync_migration` service + `scripts/sync_migrate.py`。
- 迁移前后旧文件/record/archive SHA 必须一致；重复迁移幂等；未知旧字段有确定结果；dry-run 不写 data。

### 6.6 前端模块与导航

- 31 建 `views/update_management.js` 壳 + `core/sync-api.js` + `shell.js` 导航/路由 + 三处 `index.html`。
- 32–36 各自独立文件：`update_sources.js`、`update_plan.js`、`update_connections.js`、`update_runs.js`、`update_candidates.js`；共享文件改动由 31 owner 审。
- 单 `tests.js` + `SYNC-*` 用例 + `EXPECTED_CASES`；mock 只用于测试，生产入口不加载 mock。
- 工作区切换 `epoch++` 丢弃旧响应；不读当前页面选择替换 run 冻结值；旧设置更新源/定时深链跳唯一新页。

### 6.7 安全、性能与打包

- 新 API 走 `/api/v3/workspaces/{wid}/...`；写操作带 `If-Match`/expected revision/幂等键；权限服务端强制。
- 出站固定 scheme/host/port/allowlist；禁 HTTPS→HTTP、任意跳转、任意 proxy、无鉴权 loopback HTTP；SDA 浏览器导航另立 allowlist。
- 50k 参数/3200 提取/20 并发等既有性能预算不回归；M16 新增列表/日志分页。
- 发布两个 artifact：核心 zip（现有 `app/` 白名单，不加 Playwright）+ collector bundle（Python 3.12 + Chromium + wheels + hash manifest）；发布包无 secret/data/mock。

## 7. S01–S52 追踪矩阵

状态只允许 `待实施 / 进行中 / BLOCKED-EXTERNAL / PASS / FAIL / NOT-TESTED`；计划阶段全部“待实施”。证据层是计划提示，最终以 M16-41/42 的 ledger 为准。

| S | 主责与联合门禁 | 证据层 | 状态 |
|---|---|---|---|
| S01 | M16-04、M16-05、M16-38 | U/H | 待实施 |
| S02 | M16-10、M16-23、M16-31、M16-40 | U/H/B | 待实施 |
| S03 | M16-10、M16-33、M16-40 | U/H/B | 待实施 |
| S04 | M16-08、M16-09、M16-33 | U/H/B | 待实施 |
| S05 | M16-08、M16-09、M16-33 | U/H/B | 待实施 |
| S06 | M16-06、M16-37 | U/H | 待实施 |
| S07 | M16-02、M16-16 | U | 待实施 |
| S08 | M16-02、M16-20、M16-41 | U/X | 待实施 |
| S09 | M16-20、M16-25 | U/X | 待实施 |
| S10 | M16-20 | U/X | 待实施 |
| S11 | M16-20、M16-21、M16-34 | U/B/X | 待实施 |
| S12 | M16-16、M16-21、M16-41 | U/X | 待实施 |
| S13 | M16-21、M16-41 | U/X | 待实施 |
| S14 | M16-17、M16-21、M16-41 | U/X | 待实施 |
| S15 | M16-14、M16-21、M16-39 | U/X | 待实施 |
| S16 | M16-16、M16-23 | U/H | 待实施 |
| S17 | M16-21、M16-39 | U/H | 待实施 |
| S18 | M16-19、M16-38 | U/H | 待实施 |
| S19 | M16-17、M16-19、M16-20、M16-38 | U/H/X | 待实施 |
| S20 | M16-19、M16-20 | U/X | 待实施 |
| S21 | M16-16、M16-22、M16-24 | U/H | 待实施 |
| S22 | M16-16、M16-24 | U/H | 待实施 |
| S23 | M16-13、M16-23 | U/H | 待实施 |
| S24 | M16-15、M16-22、M16-39 | U/H | 待实施 |
| S25 | M16-04、M16-16、M16-22、M16-38 | U/H | 待实施 |
| S26 | M16-18、M16-30、M16-38 | U/H | 待实施 |
| S27 | M16-18、M16-19、M16-38 | U/H | 待实施 |
| S28 | M16-05、M16-10、M16-24、M16-38 | U/H | 待实施 |
| S29 | M16-07、M16-19、M16-24、M16-38 | U/H | 待实施 |
| S30 | M16-24、M16-25 | U/H | 待实施 |
| S31 | M16-24、M16-25、M16-26 | U/H | 待实施 |
| S32 | M16-25 | U/H | 待实施 |
| S33 | M16-03、M16-12、M16-23 | U/H | 待实施 |
| S34 | M16-10、M16-12、M16-35 | U/H/B | 待实施 |
| S35 | M16-12、M16-35 | U/H/B | 待实施 |
| S36 | M16-12、M16-21、M16-39 | U/H | 待实施 |
| S37 | M16-15、M16-22、M16-39 | U/H | 待实施 |
| S38 | M16-14、M16-39、M16-42 | U/X | 待实施 |
| S39 | M16-11、M16-25、M16-39 | U/H | 待实施 |
| S40 | M16-26、M16-27、M16-28、M16-36 | U/H/B | 待实施 |
| S41 | M16-26、M16-27、M16-28、M16-36 | U/H/B | 待实施 |
| S42 | M16-27、M16-28、M16-36 | U/H/B | 待实施 |
| S43 | M16-27、M16-28、M16-36 | U/H/B | 待实施 |
| S44 | M16-07、M16-29 | U/H | 待实施 |
| S45 | M16-29 | U/H | 待实施 |
| S46 | M16-13、M16-31、M16-32、M16-40 | U/H/B | 待实施 |
| S47 | M16-31、M16-32、M16-33、M16-34、M16-36、M16-40 | U/B | 待实施 |
| S48 | M16-18、M16-30、M16-34、M16-38、M16-40 | U/H/B | 待实施 |
| S49 | M16-11、M16-35 | U/H/B | 待实施 |
| S50 | M16-11、M16-35、M16-38 | U/H/B | 待实施 |
| S51 | M16-09、M16-33、M16-39 | U/H/B | 待实施 |
| S52 | M16-04、M16-06、M16-18、M16-37、M16-38、M16-42 | U/H/X | 待实施 |

## 8. 外部证据 ledger

**真实 SDA 暂时不可得**；以下 E2–E5 未取得时一律 `BLOCKED-EXTERNAL`，不得用 mock/HTML/CI runner 冒充。

| ID | 内容 | 主责卡 | 消费卡 | 未取得时 |
|---|---|---|---|---|
| E1 | 三端点合成 fixture、协议 envelope、错误语义 | M16-02 | 16/20/24/41 | 可离线完成，不算 EXT PASS |
| E2 | 真实账密登录、受保护 capability probe、一次可续期心跳实验 | M16-02/20 | 20/21/41 | `BLOCKED-EXTERNAL` |
| E3 | 真实 idle 保活、Cookie/CSRF 轮换、失效后一次重登 | M16-20/41 | 21/30/34/42 | `BLOCKED-EXTERNAL` |
| E4 | Windows/Linux/离线 Chromium 安装、受管进程退出 | M16-14 | 38/42 | `BLOCKED-EXTERNAL` |
| E5 | 两平台断网安装/首次启动/回退 | M16-42 | 42 | `BLOCKED-EXTERNAL` |

02/20/41 证据分三阶段：02 做协议 canary，20 做单连接 idle canary，41 做全链路真实闭环；20 不得用 41 的结果倒推 Done，42 不得在 41/20 未 Done 时 Done。

## 9. 验证矩阵

| 卡类 | 最小命令 | 判据 |
|---|---|---|
| 纯模型/服务 | `./.venv39/bin/python -m pytest -q tests/test_sync_<area>.py` | 0 failed；新增用例真实执行 |
| 全量回归 | `./.venv39/bin/python -m pytest -q` | 0 failed；记录 passed/skipped |
| H3 硬门禁 | `MCCHECKER_REQUIRE_H3=1 ./.venv39/bin/python -m pytest -q` | 出现「H3 守卫执法中：真实 data/ 基线快照 N 个文件…」；0 failed |
| Python 3.9 | `PYTHON=./.venv39/bin/python bash scripts/check_py39.sh` | exit 0 |
| 前端 HTTP | keep-alive 静态服务 + Chrome `--dump-dom` | `<title>ALL PASSED (n)</title>`；其它标题重跑 |
| 真实 HTTP 冒烟 | `run.py --port <随机> --data-dir "$(mktemp -d)"` + `curl` | 卡内验收条目逐条通过 |
| 发布包 | `make_release.py --verify-only --out <zip>` | 成员数、外部主机 0、无 mock/凭据/样本 |
| 外部证据 | E1–E5 ledger | `PASS/FAIL/BLOCKED-EXTERNAL/NOT-TESTED` 必须真实 |

## 10. 首批动作

1. 只读预检：`git status`、分支/双 SHA、`pytest --collect-only`、`EXPECTED_CASES`；不符先记录。
2. 把 M15 豁免文档作为 docs-only chore PR 合入 `feat/mc-v3`；不把 `ACCEPTED` 改成 `PASS`。
3. 从 `feat/mc-v3` 文档提交后 tip 新建 `feat/mc-v3.1`；不 rebase、不 merge main；记录双 SHA。
4. 写 `docs/MCChecker_V3.1_PLAN.md` 与 `docs/adr/ADR-012-020-v3.1-sync-contract.md`；docs-only PR base `feat/mc-v3.1`。
5. 文档校验：43 卡、52 S、15 波、`git diff --check`、无 `2160/2161 PASS`。
6. 计划/ADR 合入后启动 M16-00：取回附件、重算 SHA/字节、写 `docs/MCChecker_V3.1_BASELINE.md`、ADR 改 Accepted。
7. M16-01/02 并行：01 写 `M16_CURRENT_STATE.md`/`M16_COMPAT_MATRIX.md`；02 写 `M16_SDA_PROTOCOL.md` 与合成 fixture，真实证据标 `BLOCKED-EXTERNAL`。
8. W2/W3 首卡：M16-03、M16-14、M16-04、M16-08、M16-11、M16-31；每卡按卡面全文 + 计划行 + ADR + CURRENT_STATE 执行。

## 11. 风险与不做的事

**风险**

- V3.0 未合 main，M16 会叠加 146 个 commit、落后 main 3 个；M16-01 必须审计 main/feat 差异，不在 M16 开工时合 main。
- 真实 SDA 不可得，02/20/41/42 会停在 `BLOCKED-EXTERNAL`；先做 E1 合成 fixture 与代码主体，不用 mock/HTML/CI runner 冒充。
- 31–36 都改 `shell.js`/`api.js`/`tests.js`/`index.html`；先拆文件，31 owner 审共享文件，单 `tests.js` + `EXPECTED_CASES`。
- SecretStore/候选/观测若直接改 V3 权威 head 或漏 `DATA_SUBDIRS`/备份，会出现“测试全绿但权威数据被改/备份漏项”。
- 卡面有 `/api/v1/w/...`、不存在的 `WorkspaceContext/PathResolver`、孤儿 API；用 ADR-013/014 冻结 canonical 路径/实际符号，M16-01 逐函数审计，PR 引用 ADR。
- M15 豁免容易被误读成 PASS；所有文档/评论必须写 `ACCEPTED-WITH-EXEMPTION`，不写“V3 已发布/已上线”。

**不做**

- 不改 M9–M15 卡；不改 Linear 卡状态/关系；不写业务代码。
- 不引入第二文件库、第二工作区管理器、第二业务调度器、数据库/ORM/Redis/新前端框架。
- 不自动发布固化配置；不物理下发机台；不绕过验证码/MFA。
- 不把 HTML/mock/CI runner/负责人豁免写成 PASS；不发布、不改现网数据。

## 附录 A 卡号对照

| 卡号 | Linear |
|---|---|
| `M16-00` | INH-2165 |
| `M16-01` | INH-2166 |
| `M16-02` | INH-2167 |
| `M16-03` | INH-2168 |
| `M16-04` | INH-2169 |
| `M16-05` | INH-2170 |
| `M16-06` | INH-2171 |
| `M16-07` | INH-2172 |
| `M16-08` | INH-2173 |
| `M16-09` | INH-2174 |
| `M16-10` | INH-2175 |
| `M16-11` | INH-2176 |
| `M16-12` | INH-2177 |
| `M16-13` | INH-2178 |
| `M16-14` | INH-2179 |
| `M16-15` | INH-2180 |
| `M16-16` | INH-2181 |
| `M16-17` | INH-2182 |
| `M16-18` | INH-2183 |
| `M16-19` | INH-2184 |
| `M16-20` | INH-2185 |
| `M16-21` | INH-2186 |
| `M16-22` | INH-2187 |
| `M16-23` | INH-2188 |
| `M16-24` | INH-2189 |
| `M16-25` | INH-2190 |
| `M16-26` | INH-2191 |
| `M16-27` | INH-2192 |
| `M16-28` | INH-2193 |
| `M16-29` | INH-2194 |
| `M16-30` | INH-2195 |
| `M16-31` | INH-2196 |
| `M16-32` | INH-2197 |
| `M16-33` | INH-2198 |
| `M16-34` | INH-2199 |
| `M16-35` | INH-2200 |
| `M16-36` | INH-2201 |
| `M16-37` | INH-2202 |
| `M16-38` | INH-2203 |
| `M16-39` | INH-2204 |
| `M16-40` | INH-2205 |
| `M16-41` | INH-2206 |
| `M16-42` | INH-2207 |

## 附录 B 文件面代号与关键路径

| 代号 | 范围 | 关键路径（建议，M16-01 审计后冻结） |
|---|---|---|
| M | Sync 模型/服务 | `app/core/sync_models.py`、`app/services/sync_*.py`（sources/policy/preview/runs/status/candidates/bindings/commit/observations/migration） |
| S | 既有服务整合 | `app/services/updates.py`、`tasks.py`、`scheduler.py` |
| A | API/权限 | `app/server/api_sync_*.py`、`api_connections.py`、`router.py`、`authz.py` |
| C | Collector | `collector/`、`app/services/collector_manager.py`、`collector_client.py` |
| W | 前端 | `app/web/assets/js/views/update_management.js`、`update_sources.js`、`update_plan.js`、`update_connections.js`、`update_runs.js`、`update_candidates.js`、`core/api.js`、`shell.js`、`tests/tests.js`、`mock-api.js`、三处 `index.html` |
| F | V3 文件/配置集成 | `app/services/file_versions.py`、`documents.py`、`config_v3_drafts.py`、`config_v3_import.py`、`config_v3_baselines.py`、`config_v3_machines.py`、`config_v3_provenance.py` |
| P | 持久化/工作区 | `app/core/storage.py`、`app/services/workspace_catalog.py`、`workspace_context.py`、`tests/conftest.py`、`scripts/backup_v3.py` |
| X | 迁移/发布脚本 | `scripts/sync_migrate.py`、`sync_upgrade.py`、`make_release.py`、`check_py39.sh` |
| T | 测试/文档 | `tests/test_sync_*.py`、`tests/fixtures/sda/`、`docs/M16_*.md` |

## 附录 C 术语映射

| 卡面/旧写法 | 计划 canonical | 说明 |
|---|---|---|
| `WorkspaceContext` | `app/services/workspace_context.py::resolve/assert_profile` | 仓库没有这个类；不新建同名类 |
| `PathResolver` | `workspace_catalog.workspace_root/profile_root` + `storage.get_*_dir` | 不新建通用路径解析器 |
| `/api/v1/w/{wid}/...` | `/api/v3/workspaces/{wid}/...` | 新 UI/API 只走 v3；旧 `/api/v1/p/{pid}/sources` 保留只读兼容 |
| `config_policies.ROUTE_CANDIDATE` | M16 `SyncCandidate` | 旧 config-domain 候选通道不是文件级候选 |
| `config_ingest` 的“采集” | SDA collector | 两者不是同一对象；不要复用旧命名 |
| `update_status.json` | `SyncRun`/`SourceFreshness` | 旧状态只有最近一次结果，不能当运行审计/last-good |
