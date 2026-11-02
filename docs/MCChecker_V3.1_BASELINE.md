# MCChecker V3.1 设计基线与追踪矩阵（M16-00 / INH-2165）

> 状态：M16-00 交付，2026-11-02。**本卡只冻结设计输入、代码基线、M15 能力复核、保留/新增矩阵、43 卡关系、S01–S52 落点与 ADR；不写业务代码。**
> 设计事实源：INH-2165 Resources 的 `MCChecker_V3.1_Frontend.html` 与 `MCChecker_V3.1_Backend_Design.md`；仓库计划见 `docs/MCChecker_V3.1_PLAN.md`；执行索引见 Linear 文档 `0729099b-bc2b-4af3-9235-b569fac2a59f`。
> 代码基线：`origin/main@b964c6e`、`feat/mc-v3@1c10582`、`feat/mc-v3.1@6b018a7`（2026-11-02）。

## 1. 设计输入校验

| 文件 | SHA-256 | 字节数 | 来源/校验 |
|---|---|---:|---|
| `MCChecker_V3.1_Frontend.html` | `88eca2cea8b8d90d2fdde1379b162ef6af3a312736b76c3bd6d412a3d984db82` | 997,085 | INH-2165 attachment `a4967a33-ac4e-4d7d-b383-0a47f5dda99f`；`get_attachment(format=url)` 取签名 URL，`curl` 原始下载后 `shasum -a 256` + `wc -c` |
| `MCChecker_V3.1_Backend_Design.md` | `4b7f3ea0f02fce8dae4efd1f184a57d40104818c77a38113b15fe24dd3c4b413` | 184,805 | INH-2165 attachment `6a1d0532-def4-4333-8ee5-7e05be274a2f`；同上 |

- 两个文件的本地重算 SHA 与字节数均与卡面/执行索引完全一致；不是 URL 快捷方式，不是截断文本。
- 附件签名 URL 会过期；本基线只记录 SHA/字节与附件 ID，不把签名 URL 写进仓库。
- HTML 是完整 V3.0 原型上增量加入更新管理的单文件模拟器；模拟数据、浏览器存储、演示身份**不构成**生产实现或验收证据。
- MD 是服务端行为事实源，正文 §1–§22 + 附录 A（V3.0 原文）；冲突优先级：**V3.1 MD 正文 > V3.0 附录同主题 > 更早 SDA 方案**。

## 2. 代码基线

| 项 | 值 |
|---|---|
| `origin/main` | `b964c6e`（2026-11-02 09:19 UTC；计划研究快照为 `429dafa`，之后新增 3 个非 V3 的 UI 提交） |
| `feat/mc-v3` | `1c10582`（M15 代码 tip） |
| `feat/mc-v3.1` | `6b018a7`（PR #194 合并后；包含 M15 豁免文档提交 `df96271`） |
| 计划分支 | `mc-v31/m16-plan-adr@dd456fa`（已合并为 #194） |
| main...feat/mc-v3.1 | 落后 3、领先 146；`origin/main..feat/mc-v3.1` 为 161 个文件（+18176/-558） |
| V3 文件数 | main **0**；feat/mc-v3.1 **23**（`config_v3|api_v3|file_versions|workspace_catalog`） |
| M15 豁免文档 | `df96271` 已在 feat/mc-v3.1；PR #193 在 M16-00 冻结时仍 open，合入后用 merge commit 保留谱系 |
| 工作树 | clean |

- main 不是 `feat/mc-v3.1` 的祖先；M16 不先合 main，M16-01 逐函数审计 main/feat 差异。
- 计划研究快照的 `429dafa` 已过时；本文件与 `docs/MCChecker_V3.1_PLAN.md` §1.3、ADR-012 已回填实际 SHA。
- 新增 V3.1 文件不得放进 `app/` 以外的发布盲区；collector 独立目录，主应用不 import 第三方。

## 3. M15 已完成能力复核

- M9–M14 全部 Done；M15 **6/6 Done**；2163/2164 两张补卡 Done。
- 2160/2161 是 2026-11-02 负责人 Lamires Asu 人工试用后的**书面豁免**；`docs/V3_RELEASE_GATE.md` 状态为 `ACCEPTED`，不是 `PASS`。
- 未取得的外部证据仍保留：目标系统真实模板、实体设备/PLC 回读、Windows 10/11 + Linux 实机、4C8G/SSD 独立压测；INH-1910、INH-1978、INH-1932、权威备份在 `/tmp`、生产页面零进包审计仍未闭合。
- V3.0 **未发布、未合 main、未改现网数据**；2161 Done 只表示 M16-42 的 blockedBy 已满足，不代表 V3.0 已发布或外部证据取得。
- M16 只复核/复用 V3 已实现能力，不重开 M9–M15，不把旧 Done 当 V3.1 验收。

## 4. 保留 / 新增 / 替换矩阵

| 范围 | V3.1 动作 | 现有实现/入口 | 主要卡 | 说明 |
|---|---|---|---|---|
| 宿主壳层/工作区/会话/路由 | 扩展 | `app/web/assets/js/views/shell.js`、`core/api.js`、`app/server/http.py`、`api_session.py` | 31 | 新增更新管理导航；旧设置深链重定向；身份/权限不变 |
| 速览/收藏/搜索/结构树 | 保留 | `views/overview.js`、`search.js`、`services/documents.py` | 01/31/40 | 不重写原页面 |
| 原 remarks/审阅 | 保留 | `services/review.py`、`server/api_review.py` | 01/40 | 不得误删 |
| DL 计算/绑定/历史 | 保留 | `services/dl.py`、`dl_history.py`、`views/dltool.js` | 01/40 | 数学不变 |
| 文件/上传/URL/临时/归档/记录/删除 | 保留 | `services/documents.py`、`upload.py`、`history.py`、`recovery.py` | 01/07/24/40 | 唯一文件入口；FileResource/FileVersion |
| 更新源/调度/任务/设置/工具 | 扩展 + 作用域 | `services/updates.py`、`scheduler.py`、`tasks.py`、`views/settings.js`、`tools.js` | 03–13/31–35 | 旧源/计划迁移；新 Sync 域；不建第二调度器 |
| 旧 config-domain | 保留只读/停写 | `config_changes.py`、`config_delivery.py`、`config_upgrade.py`、`config_audit.py`、`config_lifecycle.py` | 01/42 | 不复用旧 ChangeSet；不复活旧编排 |
| FileResource/FileVersion | 复用 | `file_versions.py`、`documents.py`、`config_v3_provenance.py` | 07/24/26 | 候选/观测引用统一版本，不建第二文件库 |
| FixedConfig / D0-Bn-rn / 草稿 | 复用/只写草稿 | `config_v3_catalog.py`、`config_v3_baselines.py`、`config_v3_machines.py`、`config_v3_drafts.py` | 26–29/36 | 后台同步不自动发布；候选只入草稿 |
| 设备层 | 保留关闭 | `config_device_layer.py`（`ENABLED=False`） | 42 | M7-07/08 Canceled；不做物理下发 |
| Sync 域 | 新增 | 无 | 03–13/23–29 | 来源/计划/run/候选/last-good/观测 |
| Collector/SecretStore | 新增 | 无 | 14–22/30 | 独立进程/独立依赖/独立密文根 |
| 更新管理 UI | 新增 | 原型 HTML 增量屏 | 31–36/40 | 原生视图；不复制 mock/演示库 |

**不做**：第二文件库、第二工作区管理器、第二业务调度器、数据库/ORM/Redis、新前端框架、WorkspaceMembership/空间角色、物理机台下发/回读、MFA 绕过。

### 4.1 源码与目标 HTML 差异清单

- **生产已有、不得重写**：V3.0 宿主壳（顶栏/侧栏/标签/工作区切换）、速览/搜索/审阅/DL/设置/配置管理五 tab、唯一文件与记录/归档、Workspace/FileResource/D0-Bn-rn。
- **生产缺失、由 M16 新增**：更新管理页、来源列表/编辑抽屉、计划/全量预览、连接设置/保活/自动重登、运行记录/日志、候选差异/决议；对应 31–36。
- **生产缺失、由 M16 新增**：workspace 级 SyncSource/Policy/Run/Item/Candidate/last-good/Connection、SecretStore、独立 collector；对应 03–29。
- **HTML 的 mock 边界**：mock state/localStorage/演示身份/模拟任务只作交互演示，不进入生产持久化；生产必须走真实 API、真实权限和真实任务。
- **事实源分工**：HTML 是界面/交互事实源；V3.1 MD 是服务端行为事实源；冲突走 ADR-013/014，不让实现卡自行选边。
- **旧入口处理**：旧设置页更新源/定时更新深链保留并重定向到唯一新页；旧 `/api/v1/p/{pid}/sources` 只读兼容；旧 config-domain 不重开。
- **旧数据迁移**：旧 `config_mapping.json`/`schedule.json`/`update_status.json` 与 profile 计划要迁移，不直接丢弃；迁移前后旧文件/记录/归档 SHA 不变。
- **安全边界**：设备层保持 `ENABLED=False`；候选只入 V3 草稿，不自动发布 D0/Bn/rn；密码/Cookie/CSRF 不进 HTML/任务/日志/备份。

## 5. 43 卡、依赖与 15 波

- 43 张卡 `INH-2165–INH-2207`（M16-00…M16-42），全部 Backlog；六条工作流 A–F。
- 研究阶段统计 130 条 `blockedBy`（129 条 M16 内部 + 1 条到 M15-06/INH-2161）；落地时逐卡复核。
- 15 个波次 W0–W14；数量序列 `1,2,2,5,4,5,4,3,5,3,3,3,1,1,1`。
- 主关键路径 P1：`00→01→14→15→16→22→24→25→26→27→28→36→40→41→42`。
- P2 资源脊线：`00→01→03→04→14→18→17→19→20→21→23→35→40→41→42`；P2′ 严格边链交叉校验：`00→01→03→04→05→07→24→25→26→27→28→36→40→41→42`。
- 卡表、精确 blockedBy、文件面、验收要点与波次见 `docs/MCChecker_V3.1_PLAN.md` §4–§5；本文件不重复 43 行，避免两处漂移。

## 6. S01–S52 追踪

- Canonical 表：`docs/MCChecker_V3.1_PLAN.md` §7 + Linear 执行索引 `0729099b-bc2b-4af3-9235-b569fac2a59f`。
- 52 行全部有主责卡与联合门禁；计划阶段状态全部 **待实施**，**不代表 PASS**。
- 外部证据相关行（S08/S09/S10/S11/S12/S13/S14/S19/S20/S38/S52 等）在真实 SDA/平台证据取得前保持 `BLOCKED-EXTERNAL` 或“待实施”。
- M16-41 负责真实内网归因；M16-42 负责最终审计；本基线不提前写 S 的 PASS。

## 7. ADR 与决策

- `docs/adr/ADR-012-020-v3.1-sync-contract.md` 已由 `Proposed` 改为 `Accepted`（2026-11-02）。
- ADR-012 的分支/基线已回填：`origin/main@b964c6e`、`feat/mc-v3@1c10582`、`feat/mc-v3.1@6b018a7`；main 不是 feat 祖先，不 merge main。
- ADR-013：新 API 统一 `/api/v3/workspaces/{wid}/...`；source-check→24，catalog→16+30，session-candidates→19+30，connection create→04+30。
- ADR-014：使用实际 `workspace_context.resolve/assert_profile`、`workspace_catalog.workspace_root/profile_root`、`storage.use_workspace/use_profile`；不伪造 `WorkspaceContext/PathResolver`。
- ADR-015：2160/2161 = `ACCEPTED-WITH-EXEMPTION`，不是 PASS；2161 Done 不代表 V3.0 已发布。
- ADR-016：06 只 plan/dry-run，37 才 execute/backup/rollback；一套迁移 service + 一个 CLI。
- ADR-017：`sync_run` 任务、W 级 run、网络锁外/提交锁内、幂等键→run、只重试 failed/blocked。
- ADR-018：独立 SecretStore/密文根、`integration_credentials_manage`、无明文回退、不做空间角色。
- ADR-019：31 建壳 + 独立 tab 文件；单 `tests.js` + `SYNC-*` + `EXPECTED_CASES`。
- ADR-020：E1–E5；SDA 不可得时 02/20/41/42 `BLOCKED-EXTERNAL`；HTML/mock/CI runner/豁免不能冒充 PASS。

## 8. 外部证据与阻塞

**真实 SDA 暂时不可得**；本基线不把合成 fixture 当真实证据。

| ID | 内容 | 主责卡 | 当前状态 |
|---|---|---|---|
| E1 | 三端点合成 fixture、协议 envelope、错误语义 | M16-02 | 可离线完成，不算 EXT PASS |
| E2 | 真实账密登录、受保护 capability probe、一次可续期心跳实验 | M16-02/20 | `BLOCKED-EXTERNAL` |
| E3 | 真实 idle 保活、Cookie/CSRF 轮换、失效后一次重登 | M16-20/41 | `BLOCKED-EXTERNAL` |
| E4 | Windows/Linux/离线 Chromium 安装、受管进程退出 | M16-14 | `BLOCKED-EXTERNAL` |
| E5 | 两平台断网安装/首次启动/回退 | M16-42 | `BLOCKED-EXTERNAL` |

- M16-02 可以完成协议文档与合成 fixture，但卡面要求的真实内网心跳证据未取得；M16-02 不得 Done。
- M16-20/41/42 同样不得在外部证据未取得时 Done；M16-42 不得在 41/20 未 Done 时 Done。
- 1910/1978/1932、备份转存、零进包审计仍作为已知风险；V3.0 没有发布。

## 9. DoD 与证据

- [x] 两个 INH-2165 附件可下载；本地重算 SHA/字节与卡面/执行索引一致。
- [x] 代码基线记录：`origin/main@b964c6e`、`feat/mc-v3@1c10582`、`feat/mc-v3.1@6b018a7`；main 不是 feat 祖先；V3 文件 main 0 / feat 23。
- [x] 43 卡 / 130 blockedBy / 15 波 / 52 S 的 canonical 表在 plan 与 Linear 执行索引；本基线记录计数与链接。
- [x] 六条工作流、保留/新增/替换矩阵、ADR-012–020 已落。
- [x] 2160/2161 只写 `ACCEPTED-WITH-EXEMPTION`，不写 PASS；V3.0 未发布。
- [x] 零业务代码；本卡只改文档。
- [x] 工作树 clean；M16 集成分支 `feat/mc-v3.1` 已建立。
- [ ] PR #193（M15 豁免文档）尚未合入 `feat/mc-v3`；合入后用 merge commit 保留谱系，不改变本基线代码内容。
- [ ] E2–E5 外部证据未取得；02/20/41/42 保持 `BLOCKED-EXTERNAL`。

**复现命令**

```bash
# 附件校验（签名 URL 由 Linear get_attachment(format=url) 当天获取）
shasum -a 256 /tmp/m16-baseline/MCChecker_V3.1_Frontend.html
wc -c < /tmp/m16-baseline/MCChecker_V3.1_Frontend.html
shasum -a 256 /tmp/m16-baseline/MCChecker_V3.1_Backend_Design.md
wc -c < /tmp/m16-baseline/MCChecker_V3.1_Backend_Design.md

# 代码基线
git rev-parse origin/main feat/mc-v3 feat/mc-v3.1
git rev-list --left-right --count origin/main...feat/mc-v3.1
git diff --stat origin/main..feat/mc-v3.1 | tail -1
git ls-tree -r --name-only origin/main | grep -E 'config_v3|api_v3|file_versions|workspace_catalog' | wc -l
git ls-tree -r --name-only feat/mc-v3.1 | grep -E 'config_v3|api_v3|file_versions|workspace_catalog' | wc -l

# 计划计数
grep -cE '^\| M16-[0-9]{2} \|' docs/MCChecker_V3.1_PLAN.md
grep -cE '^\| S[0-9]{2} ' docs/MCChecker_V3.1_PLAN.md
grep -cE '^\| W[0-9]+' docs/MCChecker_V3.1_PLAN.md
```

## 10. 已知限制

- main 在计划研究快照后新增 3 个非 V3 的 UI 提交；M16-01 必须审计它们与 feat/mc-v3.1 的差异，不能把 main 当 V3 基线。
- PR #193 仍 open；M15 豁免文档已在 feat/mc-v3.1 的 `df96271`，但尚未进入 feat/mc-v3 的合并历史。
- 真实 SDA/Windows/Linux/离线证据不可得；相关卡不得 Done，最终 M16-42 不得 Done。
- 计划中的文件名为建议；M16-01 逐函数审计后冻结，不得把建议名当既成实现。
