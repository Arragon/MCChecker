# M16 兼容矩阵与落地契约（M16-01 / INH-2166）

> 状态：M16-01 契约冻结，2026-11-02。审计 SHA：`beedeab453293237b4226d5b4582d1a5fb36194e`。
> 事实源：`docs/M16_CURRENT_STATE.md`、`docs/MCChecker_V3.1_BASELINE.md`、`docs/MCChecker_V3.1_PLAN.md`、ADR-012–020。
> 本文件只冻结边界与调用契约，不写业务代码；实际实现以下游卡卡面 + JIT brief 为准。

## 1. 总体边界

- 一个应用、一个数据根、一个文件库、一个业务调度器；不引入数据库/ORM/Redis/新前端框架。
- Workspace 是业务边界，Profile 在 W 内；全局身份/权限唯一；不做 WorkspaceMembership/空间角色。
- V3.0 的 workspace、FileResource/FileVersion、FixedConfig、D0/Bn/rn、原 remarks/review、文件、DL、设置、任务全部保留。
- M16 只新增：Sync 域、独立 collector、SecretStore、候选/last-good、更新管理 UI、迁移/发布证据。
- 后台同步不自动发布固化配置；D0/Bn/rn 只由既有 V3 事务移动；设备层保持关闭。
- 旧 v1 更新源/记录/任务/设置继续可读；旧 config-domain 不恢复 ChangeSet。

## 2. 保留 / 改造 / 新增 / 只读 / 停写

| 范围 | 动作 | 现有入口 | 主责卡 | 约束 |
|---|---|---|---|---|
| 宿主壳/工作区/会话/路由 | 扩展 | `shell.js`、`core/api.js`、`http.py` | 31 | 不重写 shell；导航新增 updates；旧深链重定向 |
| 速览/搜索/审阅/DL/文件/设置/工具 | 保留 | 现有 views/services | 01/40 | 行为不退化；M16-40 回归 |
| 更新源/定时/任务 | 改造 | `updates.py`/`scheduler.py`/`tasks.py` | 03–13/31–35 | 旧 per-profile 保留兼容；新 workspace 级 Sync |
| 文件/归档/记录/下载 | 保留 + 复用 | `documents.py`/`file_versions.py` | 07/13/24/40 | 唯一文件库；不复制第二套 |
| 配置管理/D0-Bn-rn/草稿 | 复用 | `config_v3_*` | 26–29/36 | 只产候选/草稿；不自动 publish |
| 旧 config-domain | 只读/停写 | `config_changes.py` 等 | 01/42 | 不恢复旧 ChangeSet/审批/交付 |
| 设备层 | 保留关闭 | `config_device_layer.py` | 42 | `ENABLED=False`；不物理下发 |
| Sync 域 | 新增 | 无 | 03–13/23–29 | `app/core/sync_models.py` + `app/services/sync_*.py` |
| Collector/SecretStore | 新增 | 无 | 14–22/30 | 独立目录/独立 venv/独立密文根 |
| 更新管理 UI | 新增 | 原型 HTML 增量屏 | 31–36/40 | 原生视图；不复制 mock/演示库 |

## 3. F01–F28 映射

| F | 功能 | V3.1 动作 | 主责卡 | 现有测试/门禁 |
|---|---|---|---|---|
| F01 | 本地上传导入 | 保留 | 01/40 | `tests/test_api_import.py`、`tests/test_v3_file_workflows.py` |
| F02 | 链接下载导入 | 保留 | 01/40 | `tests/test_api_import.py`、`tests/test_update_bundle.py` |
| F03 | 文件列表 | 保留 | 01/31/40 | `tests/test_api_documents.py` |
| F04 | 结构树查看 | 保留 | 01/40 | `app/web/tests/index.html` 前端用例 |
| F05 | 文件内搜索 | 保留 | 01/40 | `tests/test_searching.py` |
| F06 | 收藏 | 保留 | 01/40 | `tests/test_api_favorites.py`、`tests/test_favorites_live.py` |
| F07 | 配置速览 | 保留 | 01/40 | `tests/test_favorites_live.py` |
| F08 | 修改建议 | 保留 | 01/40 | `tests/test_api_remarks.py` |
| F09 | 审阅修改 | 保留 | 01/40 | `tests/test_api_review.py`、`tests/test_review_apply.py` |
| F10 | 全局搜索 | 保留 | 01/40 | `tests/test_api_search.py`、`tests/test_searching.py` |
| F11 | 版本历史 | 保留 | 01/07/40 | `tests/test_api_history.py`、`tests/test_v3_file_identity.py` |
| F12 | 版本对比 | 保留 | 01/40 | `tests/test_api_compare.py` |
| F13 | 修改记录 | 扩展 | 13/24/35/40 | `tests/test_api_records.py` |
| F14 | 文件下载 | 保留 | 24/40 | `tests/test_file_downloads.py`、`tests/test_api_legacy.py` |
| F15 | 更新源管理 | 改造 | 05/06/31/32/40 | `tests/test_api_sources.py`、`tests/test_config_mapping.py` |
| F16 | 定时更新 | 改造 | 08/09/33/40 | `tests/test_api_schedule.py`、`tests/test_scheduler.py` |
| F17 | IP 对应表 | 保留 | 01/40 | `tests/test_api_people.py` |
| F18 | 管理员列表 | 保留 | 01/40 | `tests/test_api_people.py` |
| F19 | 变量绑定 | 保留 | 01/40 | `tests/test_api_bindings.py` |
| F20 | 工具菜单 | 保留 | 31/40 | `tests/test_api_tools.py` |
| F21 | DL 快捷计算 | 保留 | 01/40 | `tests/test_api_dl.py`、`tests/test_dltool_multi.py`、`tests/test_dlmath_crosscheck.py` |
| F22 | 多机型 | 保留 | 01/31/40 | `tests/test_api_profiles.py`、`tests/test_profiles.py` |
| F23 | 标签页工作区 | 保留 | 31/40 | `app/web/tests/index.html` 前端用例 |
| F24 | 身份识别 | 保留 | 01/40 | `tests/test_api_session.py`、`tests/test_api_authz.py` |
| F25 | 删除文件 | 保留 | 01/24/40 | `tests/test_api_delete.py` |
| F26 | 旧布局迁移 | 保留 | 01/37 | `tests/test_config_migration.py` |
| F27 | 解析缓存 | 保留 | 01/40 | `tests/test_api_documents.py` |
| F28 | 启动与端口 | 保留 | 14/42 | `tests/test_release.py`、`tests/test_api_http.py`、`check_py39.sh` |

**F 的结论**：M16 不改 F01–F28 的业务语义；F15/F16/F13 因更新管理迁移做兼容改造；M16-40 做真实浏览器回归，M16-01 只冻结映射。

## 4. Q1–Q6 映射

| Q | 验收面 | 主责卡 | 回归测试 |
|---|---|---|---|
| Q1 | A/B workspace 同名 profile/file/task 不串；身份/权限不变 | 04/05/10/31/38/40 | `tests/test_v3_workspace_isolation.py`、`tests/test_api_workspaces.py` |
| Q2 | 导航/旧深链/配置管理入口 | 31/40 | `app/web/tests/index.html` |
| Q3 | FileResource/FileVersion/tombstone/唯一文件入口 | 07/24/40 | `tests/test_v3_file_identity.py`、`tests/test_v3_file_provenance.py` |
| Q4 | 参数工作台/参数树共享选中/修订/ETag | 40 | `app/web/tests/index.html` |
| Q5 | D0/Bn/rn 实际快照/差异/历史 | 26/27/29/40 | `tests/test_v3_config_e2e.py` |
| Q6 | 提取/合并/导出/来源变化/原子提交 | 40 | `tests/test_config_merge.py`、`tests/test_config_export.py`、`tests/test_v3_extract_preview.py` |

## 5. S01–S52 映射

- Canonical 表：`docs/MCChecker_V3.1_PLAN.md` §7；逐行主责/联合门禁已列。
- M16-01 冻结测试命名：后端 `tests/test_sync_*.py`、`tests/test_api_sync_*.py`、`tests/test_collector_*.py`、`tests/test_secret_store.py`、`tests/test_sync_migration*.py`、`tests/test_sync_release.py`；fixture `tests/fixtures/sda/`；前端单 `app/web/tests/tests.js` 的 `SYNC-*` 用例。
- 每张下游卡的 JIT brief/PR 必须把对应 S 行落到具名测试；M16-01 不提前发明不存在的测试名。
- S 行在计划阶段全部“待实施”；M16-41 做真实内网归因，M16-42 做最终审计；HTML/mock/CI runner 不能写成 PASS。

## 6. API 契约（proposed，M16-05/30/26 冻结精确包络）

- 新 API 统一 `/api/v3/workspaces/{wid}/...`；旧 `/api/v1/p/{pid}/sources|schedule|tasks` 保留只读/兼容。
- 写操作带 `If-Match`/expected revision/`Idempotency-Key`；stale 409；跨 W 403/404；权限服务端强制。
- 主要端点（建议）：

| 资源 | 端点 | 主责卡 |
|---|---|---|
| 来源 | `GET/POST /sync/sources`、`PATCH/DELETE /sync/sources/{sid}`、`POST /sync/sources/{sid}/check` | 05/24 |
| 计划 | `GET/PUT /sync/policy`、`POST /sync/source-policy-batches` | 08 |
| 预览 | `POST /sync/previews` | 10 |
| 运行 | `POST /sync/runs`、`GET /sync/runs`、`GET /sync/runs/{run_id}`、`GET .../events`、`GET .../diagnostic` | 12/23/35 |
| 连接 | `GET/POST /connections`、`PATCH/DELETE /connections/{cid}`、`POST .../credential-candidates`、`POST .../check`、`POST .../disable` | 04/19/30 |
| 目录 | `GET /connections/{cid}/catalog` | 16/30 |
| 候选 | `GET /sync/candidates`、`POST /sync/candidates/{id}/apply-preview`、`POST .../apply`、`POST .../dismiss` | 26/27/28 |
| 观测 | `GET /sync/observations`（只读） | 29 |

- 孤儿接口归属：source-check→24；catalog→16+30；session-candidates→19+30；connection create→04+30。
- 响应只返回安全字段；不返回 password/Cookie/CSRF/完整请求 body/未脱敏 URL。

## 7. UI 契约

- NAV 新增 `updates`（标签“更新管理”，位置在配置管理与外部工具之间）；旧 `settings` 的 sources/schedule 深链重定向到唯一新页。
- 31 建 `views/update_management.js` 壳 + `core/sync-api.js`；32–36 各自独立 tab 文件；共享文件改动由 31 owner 审。
- 三处 `index.html` 同步；`core/api.js` 的 `KINDS` 加新 kind；`taskLabel` 加 `sync_run`；mock 只测试用。
- 单 `tests.js` + `SYNC-*` + `EXPECTED_CASES`；前端判据只认 HTTP `<title>ALL PASSED (n)</title>`。
- 工作区切换 `epoch++` 丢弃旧响应；不读当前页面筛选替换 run 冻结值；旧页面/旧入口不删。

## 8. 迁移与兼容

- 旧 `config_mapping.json`/`schedule.json`/`update_status.json` 保留；迁移只做加法，不删旧 JSON/归档/记录。
- 06 只 dry-run/plan；37 才 execute/backup/rollback；一套 `sync_migration` service + `scripts/sync_migrate.py`。
- 迁移前后旧文件/record/archive SHA 一致；未知旧字段有确定结果；重复迁移幂等。
- 旧 `/api/v1/p/{pid}/sources` 只读兼容；旧设置深链可达；旧 config-domain 只读/停写。

## 9. 下游卡文件面契约

| 卡组 | 允许新增/修改 | 禁止 |
|---|---|---|
| 03 | `app/core/sync_models.py`、`tests/test_sync_models.py` | HTTP/collector/DB |
| 04/05/07/08/10/11/25/26/29 | `app/services/sync_*.py`、对应 `tests/test_sync_*.py` | 第二文件库/第二调度器 |
| 12/23 | `tasks.py`、`updates.py`、`sync_runs.py`、`sync_executor.py` | 不受管线程池/自动发布 |
| 14–22 | `collector/`、`collector_manager.py`、`collector_client.py` | 主应用 import 三方；collector 直写 data |
| 24/28/29 | `sync_commit.py`、`sync_candidates.py`、`sync_observations.py` + V3 事务 | 直接改 D0/Bn/rn head |
| 30/32–36 | `api_sync_*.py`、`api_connections.py`、`views/update_*.js`、`core/api.js`、`shell.js`、`tests.js`、`mock-api.js`、三处 `index.html` | 第二套站点/生产 mock |
| 37 | `scripts/sync_migrate.py`、`backup_v3.py` | 删除旧数据/复制密钥 |
| 38/39 | 安全/故障测试与文档 | 弱化旧权限/吞异常 |
| 40–42 | 浏览器/内网/发布文档与脚本 | 把 mock/HTML/CI runner 当 PASS |

## 10. 非目标与门禁

- 不改 M9–M15 卡；不恢复旧 ChangeSet；不建 WorkspaceMembership/空间角色。
- 不引入数据库/ORM/Redis/新前端框架；主应用零第三方运行依赖。
- 不自动 publish、不物理下发机台、不绕验证码/MFA。
- 不把 HTML/mock/CI runner/负责人豁免写成 PASS；真实 SDA/平台证据未取得时保持 `BLOCKED-EXTERNAL`。
- 每卡门禁：定向 + 全量 pytest + H3 + `check_py39.sh` + 前端 HTTP + 真实 HTTP 冒烟；动发布包加 `make_release.py --verify-only`。
