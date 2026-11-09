# M16 当前实现审计（M16-01 / INH-2166）

> 本页为下述 SHA 的历史审计。2026-11-07 已按用户选择完成实际 collector + TLS 模拟 SDA 的本地闭环；当前 143 项前端和 824 项后端回归、现场/机台观测/持久化/流式限额边界见 [最新 R6 对照](M16_R6_UI_AUDIT.md)。历史 LOCAL-PASS 不等同于当前真实 SDA 全链路可用。

> 状态：M16-01 只读审计，2026-11-02。审计 SHA：`beedeab453293237b4226d5b4582d1a5fb36194e`（`feat/mc-v3.1`，M16-00 合并后）。
> 设计基线：`docs/MCChecker_V3.1_BASELINE.md`；计划：`docs/MCChecker_V3.1_PLAN.md`；ADR：`docs/adr/ADR-012-020-v3.1-sync-contract.md`。
> 本文件只记录真实入参、状态、写盘位置、调用方与缺口，**不写业务代码、不写生产 data**。行号对应当前审计 SHA；HEAD 移动后需重跑 `grep -n` 复核。

## 1. 审计方法

- 只读 `app/services/{updates,tasks,scheduler}.py`、`app/server/api_{sources,schedule,tasks}.py`、`app/core/storage.py`、`app/services/{workspace_context,workspace_catalog,file_versions,documents}.py`、`app/web/assets/js/views/{shell,settings}.js`、`scripts/make_release.py`、`tests/conftest.py`。
- 用函数/路由/常量锚点记录当前行为；没有运行会写 `data/` 的测试，没有调用生产服务。
- 本次审计没有改动任何业务文件；`git status --short` 为空。

## 2. 运行时与作用域

| 项 | 当前事实 | 位置 |
|---|---|---|
| 运行时 | Python 3.9.11 + 标准库；运行时零第三方依赖 | `requirements.txt`、`scripts/check_py39.sh` |
| 单进程/单写者 | HTTP 单实例锁 + 工作线程排空；`mctask` 线程前缀 | `app/server/http.py`、`app/services/tasks.py:70,171,452` |
| Workspace 解析 | `resolve()` 从 header/query 解析 wid；缺 wid 写 428、冲突 409、未知 404 | `app/services/workspace_context.py:57` |
| Profile 校验 | `assert_profile(workspace_id, profile_id)` | `app/services/workspace_context.py:106` |
| 数据根 | `storage.use_workspace/use_profile` + `_profile_base_dir`；legacy default 特殊处理 | `app/core/storage.py:86,121,162,231,236` |
| Workspace 根 | `workspace_root`/`profile_root`；legacy 无 `data/workspaces/default` | `app/services/workspace_catalog.py:40,47` |
| 修订号 | 只有 `(workspace, profile)` 的 files/favorites/remarks/settings/tasks 五段；无 W 级分量 | `app/services/revision.py` |

**M16 结论**：Sync 的 workspace 级来源/计划/run/候选必须显式带 W/P；不能把 `Task.profile` 当成唯一作用域，也不能假设 legacy 工作区有 `data/workspaces/default`。

## 3. 更新源、任务与调度现状

### 3.1 更新源服务

| 函数 | 真实入参/状态 | 写盘/副作用 | 调用方 | M16 契约 |
|---|---|---|---|---|
| `updates.update_source(pid, name, actor)` :94 | per-profile 映射名；下载 → bundle 展开 → 解析 → 根指纹 → 保存 | `configs/<name>`（`save_config_file_detailed`）、`update_status.json`、`schedule.json.last_run`、`revision.bump(pid,"files")` | `api_tasks` handler | M16-13 复用 HTTP 路径；M16-24 在其后加候选/观测分流 |
| `updates.update_all(pid, actor, task)` :138 | 单 pid；按映射顺序串行；有 `record_url` 再 `refresh_records` | 同上 + 汇总 `schedule.last_run` | `api_tasks` / scheduler | M16-23 改为 workspace 级 run，不直接复用单 pid 串行 |
| `updates.refresh_records(pid, name, actor)` :174 | 单 pid/单映射；下载 → 与最新 record 逐字节比 sha | `records/<name>/record_*`；实际新增才 bump `files`；不写 `update_status.json` | `api_tasks` / `update_all` | M16-13 保留旧行为；M16-24 记录结果独立校验 |
| `updates.list_sources(pid)` :241 | 返回映射名/url/record_url/keyword/status | 读 `config_mapping.json` + `update_status.json` | `api_sources` | M16-05 新 v3 列表；旧列表只读兼容 |
| `updates.create_source` :263 / `rename_source` :291 / `delete_source` :370 | 映射 CRUD；改名迁移状态键；删除清状态键 | `config_mapping.json`、`update_status.json` | `api_sources` | M16-05 新 SyncSource；旧入口保留兼容 |
| `updates._commit_config(pid, name, content)` :399 | 策略守卫 → `parser.to_compact` → 根指纹 → 锁内重读 → `save_config_file_detailed` | `configs/<name>`、archive、`update_status.json` | `update_source`/`update_all` | M16-24 三道验证后走同一提交边界 |
| `updates._validated_url` :571 | 只校验 scheme；URL userinfo 可原样落盘 | `config_mapping.json` | create/rename | M16-05/18 改为 secret_ref，不返回原始凭据 |
| `updates._policy` :490 | 复用 `downloader.default_policy()` | 无写盘 | update_source/update_all | M16-13 继续复用；SDA 网络策略另立 |

关键约束：

- `update_status.json` 的键是**映射名**，不是 file_id；`COPY_PROFILE_FILES` 复制 `config_mapping.json`/`schedule.json`，**不复制** `update_status.json`；`schedule.last_run` 复制时清零。
- `unchanged` 只信 `save_config_file_detailed().unchanged`；不要在 M16 另算一套 sha。
- `update_all` 是单 pid 串行；一个卡死源最坏约 93 秒，3 个源约 279 秒；停止预算 300 秒（`EXIT_WORKER_TIMEOUT`）。
- `GET /api/v1/p/{pid}/sources` 对所有角色返回原始 `url`/`record_url`；M16-05/30 必须改为安全投影。

### 3.2 TaskRunner

| 项 | 当前事实 | 位置 |
|---|---|---|
| 任务类型 | `update_source` / `update_all` / `refresh_records` | `app/services/tasks.py:61` |
| Task 字段 | `id/profile/type/target/state/progress/result/actor/created_at/finished_at`；`profile` 必填 | `app/services/tasks.py:214` |
| 并发/互斥 | `ThreadPoolExecutor`；同一 `(workspace, profile)` 内三类互斥；`refresh_records` 按机型整块挡 | `app/services/tasks.py:132,483` |
| 保留窗口 | 内存 1 小时 / 最近 200 条 | `app/services/tasks.py:63-64` |
| 修订分量 | `update_all → settings`；`update_source`/`refresh_records` 不补 files | `app/services/tasks.py:112` |
| 停止/排空 | `shutdown` 30s；`wait_for_workers` 300s；`worker_threads` 只数 `mctask` | `app/services/tasks.py:171,416,452` |
| 运行上下文 | `_run_in_workspace` 绑定 W/P；网络在锁外 | `app/services/tasks.py:503` |

**M16 结论**：新 `sync_run` 不能把 profile 必填当唯一作用域；需要 workspace 级 run + 子 item，且不能嵌套等待同池 Future；collector 不能直接写 data，必须走 runner/受控提交。

### 3.3 Scheduler

| 项 | 当前事实 | 位置 |
|---|---|---|
| 线程 | `mcsched`；只投递 `update_all`，不下载、不写盘 | `app/services/scheduler.py:323,407,446` |
| 计划键 | `_plans` 键 `(workspace_id, profile_id)` | `app/services/scheduler.py:345,469` |
| 作用域 | `_scopes()` 遍历所有 W/P | `app/services/scheduler.py:218` |
| 到期/重试 | `STALE_RUN_DELAY_SECONDS=120`、`BUSY_RETRY_SECONDS=60`；`BUSY` 重排 | `app/services/scheduler.py:485,512` |
| 保存 | `save_schedule` 整份替换；只发 interval 会关闭 enabled | `app/services/scheduler.py:597` |
| 间隔 | 1–2160 整数小时；天=24h | `app/services/scheduler.py:584` |

**M16 结论**：M16-09 扩展现有 scheduler 的到期扫描，不建第二个 timer；M16-08 的 policy 保存不能被旧 `PUT /schedule` 整份替换清掉新字段。

### 3.4 API、权限与前端

| 入口 | 当前行为 | 位置 |
|---|---|---|
| `GET/POST /api/v1/p/{pid}/sources` | 读 `read`（含访客）；写 `settings_write`（全局管理员） | `app/server/api_sources.py:23` |
| `PATCH/DELETE /api/v1/p/{pid}/sources/{name}` | 同上；改名/删除 | 同上 |
| `GET/PUT /api/v1/p/{pid}/schedule` | 读 `read`；写 `settings_write` | `app/server/api_schedule.py:22` |
| `POST/GET /api/v1/p/{pid}/tasks` | POST `tasks_run`；GET `read` | `app/server/api_tasks.py:33` |
| `GET /api/v1/tasks/{id}` | 读 `read`；按任务 id | 同上 |
| 导航 | `NAV` 静态数组：速览/搜索/审阅/DL/配置管理/外部工具/设置 | `app/web/assets/js/views/shell.js:15` |
| 设置页 | `SECTIONS`：sources/schedule/people/bindings/tools/profiles | `app/web/assets/js/views/settings.js:13` |
| 任务文案 | `taskLabel` 只认三类；`startTask`/`pollTasks` | `app/web/assets/js/views/shell.js:80,1013,1051` |

**M16 结论**：M16-31 新增 `updates`/`sync` 导航项和三处 `index.html` 脚本；旧 `settings` 的 sources/schedule 深链保留并重定向；`taskLabel` 必须新增 `sync_run`；权限服务端强制，前端隐藏不算授权。

## 4. 文件、配置与 V3 域

| 能力 | 当前入口 | 关键事实 | M16 契约 |
|---|---|---|---|
| FileResource/FileVersion | `file_versions.py:37,73,92,134,183,193,206,218` | `file_id = f-`+sha256(W/P/name/generation)；identity=`v-`+sha；tombstone；只枚举 current/archive/records | M16-07/24/26 复用；候选不写 archive/records，避免被当历史 |
| 文档引用 | `documents.py:168` `resolve_ref` | 只认 `current`/`archive:`/`record:`；跨 W/P 校验 | M16-24 提交前用 `config_v3_provenance.verify_ref` 复核 |
| 配置管理 | `config_v3_catalog.py`、`config_v3_baselines.py`、`config_v3_machines.py`、`config_v3_drafts.py` | D0/Bn/rn；机台初建 `head=null`；发布只移 head | M16-26–29/36 只产候选/草稿，不自动 publish |
| 值导入 | `config_v3_import.py:62,208` | D0 未冻结时可直接写 `release.d0.values`；冻结后才写 baseline draft | M16 不把该路径当候选转正模板 |
| 观测 | `config_ingest.py:551,713` | 原始字节进 blob；Observation + CaptureEvent；解析失败也入库；要求 LogicalFile | M16-29 仅在可信 device_id 下记录机台观测；登录页/错误 JSON 不入审计 |
| 候选 | 无文件级 `SyncCandidate`/last-good | `update_status.json` 只有最近结果 | M16-11/25/26 新增 SyncRun/SourceFreshness/Candidate |

**M16 结论**：新 Sync 域必须复用 FileResource/FileVersion 与 V3 事务；不要新增第二文件库；候选/观测的原始字节走内容寻址存储，不复制到 `sync/`。

## 5. 缺口清单（按卡）

| 缺口 | 影响卡 | 现状/建议 |
|---|---|---|
| workspace 级 SyncSource/Policy/Run/Item/Candidate/last-good/Connection | 03–13/23–29 | 全无；新增 `app/services/sync_*.py` + `app/core/sync_models.py` |
| Collector/SecretStore/SDA adapter | 14–22/30 | 全无；collector 独立目录/独立 venv；SecretStore 独立密文根 |
| 更新管理 UI | 31–36/40 | 全无；31 壳 + 32–36 独立 tab 文件；单 `tests.js` + `EXPECTED_CASES` |
| workspace 级任务/调度 | 09/12/23 | `Task.profile` 必填、`Scheduler._Plan` 是 `(W,P)`；新增 `sync_run`，不建第二调度器 |
| 安全投影 | 05/30/34/38 | `GET /sources` 返回原始 URL；凭据无 SecretStore；需 403/脱敏/CSRF/SSRF 门禁 |
| 数据根/备份 | 11/18/37/42 | 新增 `data/workspaces/<wid>/sync/` 要进 `DATA_SUBDIRS`；secret root 在 data 外；备份排除 secret |
| 旧接口归属 | 05/16/19/24/30/32 | source-check/catalog/session-candidates/connection create 无卡；按 ADR-013 归属 |

## 6. 旧设置/深链与迁移

- 旧设置入口：`settings` 路由的 `sources`/`schedule` 分段（`settings.js:13-15`）；旧深链必须继续可达，M16-31 重定向到唯一“更新管理”页。
- 旧数据：`config_mapping.json`、`schedule.json`、`update_status.json`；`config_mapping`/`schedule` 在 `COPY_PROFILE_FILES`，`update_status` 不在；迁移只做加法，不删旧 JSON/归档/记录。
- 旧 API：`/api/v1/p/{pid}/sources`/`schedule`/`tasks` 保留只读/兼容；新 UI 只走 `/api/v3/workspaces/{wid}/...`。
- 旧配置域：`config_changes/config_delivery/config_upgrade/config_audit/config_lifecycle` 保留只读/停写；M16 不恢复旧 ChangeSet。

## 7. 审计结论

1. V3.0 已实现 workspace/FileResource/D0-Bn-rn/文件/配置管理；M16 是**增量**，不是重写。
2. 现有更新源/任务/调度全部 per-profile；M16 的 workspace 级 Sync 必须新增，不能靠改 `Task.profile` 语义硬套。
3. 现有 API/前端没有 Sync/Connection/Candidate/Collector；M16-31–36 必须新增页面与 API，且保留旧入口。
4. 现有 `update_status.json` 不能当 SyncRun/last-good；M16-11/25 必须新建持久运行记录与新鲜度。
5. 现有凭据支持只有 URL userinfo；M16-18 必须新建独立 SecretStore，并先修 `GET /sources` 的安全投影。
6. 本次审计零业务代码、零生产 data 写入；后续卡按 `M16_COMPAT_MATRIX.md` 的调用契约执行。
