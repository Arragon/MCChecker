# M16 / V3.1 发布门（M16-42）

> 结论先行：**本地门禁全绿；外部证据（真实 SDA、Windows/Linux 断网安装、真实 idle）未取得，
> 因此 M16-42 保持 `In Progress / BLOCKED-EXTERNAL`，不标 Done、不发布。**
> 生成时间：2026-11-02；基线 `feat/mc-v3.1`（合并 PR #247/#248/#249/#250 后）。
> 证据层级：`LOCAL` = 本地自动化（pytest/H3/前端/E2E/脚本实跑）；`EXT` = 真实环境，当前为空。

## 1. 门禁结果

| 门禁 | 命令 | 结果 |
|---|---|---|
| 全量回归 + H3 | `PYTHONPATH=… MCCHECKER_REQUIRE_H3=1 ./.venv39/bin/python -S -m pytest -q` | **3045 passed, 1 skipped**（211.10s）；H3 执法：真实 `data/` 8 文件 / 9 目录，首末一致 |
| Python 3.9 | `PYTHON=./.venv39/bin/python bash scripts/check_py39.sh` | exit 0（compileall + 禁 3.10+ 写法） |
| 前端真实浏览器 | `./.venv39/bin/python scripts/check_web_ui.py` | `<title>ALL PASSED (120)</title>` |
| 浏览器 E2E | `./.venv39/bin/python scripts/e2e_browser.py --only all` | interactive **13/13**、reachable **28/28** |
| Git secret 扫描 | `scan_secrets.py --tree` / `--all-history` / `--staged` | 命中 **0**（allowlist 9/10/0） |
| 离线发布包 | `make_release.py --out …` + `--verify-only` | 成员 **213**、外部主机引用 **0**、哈希校验通过 |
| collector 独立包 | `collector_bundle.py --out …`（构建+自校验） | 成员 **14**、manifest 逐文件 sha256、无 secret 标记、无 `__pycache__`；`chromium_bundled=false`（待 E4 回填） |
| 备份/回退演练 | `backup_v3.py backup/verify/restore --dry-run` | 7 文件；`{"ok": true, "bad": []}`；dry-run 不改盘 |
| SIGTERM 排空 | `run.py --data-dir /tmp/mcc-sigterm` + `kill -TERM` | exit 0；调度线程退出、任务队列停止、锁文件已删除、无残留进程 |

> 注：全量命令加了 `-S` + 显式 `PYTHONPATH`。原因：本会话的 Python 启动钩子让每次文件操作走 broker，
> 单用例约 1 秒；`-S` 后同一套用例、同一套断言、同一 H3 校验，耗时回到 3.5 分钟。不加 `-S` 的命令
> 已在更早一轮跑到 4%仍正常（无失败），仅慢。

## 2. S01–S52 台账

状态口径：`LOCAL-PASS` = 本地自动化通过；`BLOCKED-EXTERNAL` = 卡面明确要求真实环境，未取得；
`NOT-VERIFIED` = 无本地证据（本轮为 0）。

| S | 主责卡 | 本地证据（代表文件） | 状态 |
|---|---|---|---|
| S01 | 04/05/38 | `test_sync_security_gate.py`、`test_api_sync_sources.py` | LOCAL-PASS |
| S02 | 10/23/31/40 | `test_sync_preview.py`、E2E `updates-plan` | LOCAL-PASS |
| S03 | 10/33/40 | `test_api_sync_preview.py`、`SYNC-33` | LOCAL-PASS |
| S04 | 08/09/33 | `test_sync_policy.py`、`test_api_sync_policy.py` | LOCAL-PASS |
| S05 | 08/09/33 | `test_sync_policy.py`（inherit/custom/manual + 暂停后手动） | LOCAL-PASS |
| S06 | 06/37 | `test_sync_migration.py` | LOCAL-PASS |
| S07 | 02/16 | `test_sda_protocol.py`、`test_sda_adapter.py`（E1 合成） | LOCAL-PASS（真实端点 EXT 空） |
| S08 | 02/20/41 | `test_sync_heartbeat.py` | **BLOCKED-EXTERNAL**（需跨真实 idle） |
| S09 | 20/25 | `test_sync_heartbeat.py`、`test_sync_status.py` | LOCAL-PASS |
| S10 | 20 | `test_sync_heartbeat.py`（单连接 singleflight） | **BLOCKED-EXTERNAL**（跨 idle 结论） |
| S11 | 20/21/34 | `SYNC-34`、`test_sync_credentials.py` | LOCAL-PASS |
| S12 | 16/21/41 | `test_sda_adapter.py`、`test_sync_executor.py` | **BLOCKED-EXTERNAL**（真实失效重登） |
| S13 | 21/41 | `test_sync_credentials.py`（锁存/退避） | **BLOCKED-EXTERNAL**（真实周期计数） |
| S14 | 17/21/41 | `test_sda_login.py` | LOCAL-PASS（真实表单变体 EXT 空） |
| S15 | 14/21/39 | `test_collector_manager.py`、`test_collector_ipc.py` | **BLOCKED-EXTERNAL**（Windows/Linux 进程树） |
| S16 | 16/23 | `test_sda_adapter.py`、`test_sync_executor.py` | LOCAL-PASS |
| S17 | 21/39 | `test_sync_faults.py` | LOCAL-PASS |
| S18 | 19/38 | `test_api_authz.py`、`test_api_connections.py`、`test_sync_credentials.py`（仅管理员/访客） | LOCAL-PASS |
| S19 | 17/19/20/38 | `test_sync_credentials.py`、`test_api_connections.py` | LOCAL-PASS |
| S20 | 19/20 | `test_sync_credentials.py`（auth_epoch 竞态） | LOCAL-PASS |
| S21 | 16/22/24 | `test_collector_streaming.py` | LOCAL-PASS |
| S22 | 16/24 | `test_sda_adapter.py`（登录页/错误 JSON/错身份） | LOCAL-PASS |
| S23 | 13/23 | `test_sync_http_adapter.py` | LOCAL-PASS |
| S24 | 15/22/39 | `test_collector_ipc.py` | LOCAL-PASS |
| S25 | 04/16/22/38 | `test_sync_security_gate.py`（SSRF/重定向/降级） | LOCAL-PASS |
| S26 | 18/30/38 | `test_secret_store.py` | LOCAL-PASS |
| S27 | 18/19/38 | `test_sync_credentials.py`、`test_api_connections.py` | LOCAL-PASS |
| S28 | 05/10/24/38 | `test_api_sync_sources.py`、`test_sync_security_gate.py` | LOCAL-PASS |
| S29 | 07/19/24/38 | `test_sync_bindings.py` | LOCAL-PASS |
| S30 | 24/25 | `test_sync_commit.py` | LOCAL-PASS |
| S31 | 24/25/26 | `test_sync_commit.py`、`test_sync_candidates.py` | LOCAL-PASS |
| S32 | 25 | `test_sync_status.py` | LOCAL-PASS |
| S33 | 03/12/23 | `test_sync_models.py`、`test_sync_run_store.py` | LOCAL-PASS |
| S34 | 10/12/35 | `test_sync_preview.py`、`SYNC-35` | LOCAL-PASS |
| S35 | 12/35 | `SYNC-35`、`test_api_sync_runs.py` | LOCAL-PASS |
| S36 | 12/21/39 | `test_sync_faults.py` | LOCAL-PASS |
| S37 | 15/22/39 | `test_collector_streaming.py`（解压炸弹/大文件写前终止） | LOCAL-PASS |
| S38 | 14/39/42 | `test_collector_manager.py`（collector 缺失仍可启动） | LOCAL-PASS（macOS）；跨平台 EXT 空 |
| S39 | 11/25/39 | `test_sync_run_store.py`、`test_sync_faults.py` | LOCAL-PASS |
| S40 | 26/27/28/36 | `test_sync_candidates.py`、`SYNC-36` | LOCAL-PASS |
| S41 | 26/27/28/36 | `test_sync_candidate_plans.py`、`SYNC-36B` | LOCAL-PASS |
| S42 | 27/28/36 | `test_sync_candidate_apply.py`、`SYNC-36B` | LOCAL-PASS |
| S43 | 27/28/36 | `test_sync_candidate_apply.py`、`SYNC-36C` | LOCAL-PASS |
| S44 | 07/29 | `test_sync_bindings.py`、`test_sync_observations.py` | LOCAL-PASS |
| S45 | 29 | `test_sync_observations.py` | LOCAL-PASS |
| S46 | 13/31/32/40 | E2E `F15/F16`（旧入口收口）、`SYNC-32` | LOCAL-PASS |
| S47 | 31–36/40 | E2E 五 tab + 焦点/409/视口（13 项） | LOCAL-PASS |
| S48 | 18/30/34/38/40 | `SYNC-34`、`test_sync_security_gate.py`、E2E `admin-visitor-auth` | LOCAL-PASS |
| S49 | 11/35 | `SYNC-35`、`test_sync_run_store.py` | LOCAL-PASS |
| S50 | 11/35/38 | `test_sync_security_gate.py`（run events/Task 脱敏）+ `scan_secrets.py` | LOCAL-PASS |
| S51 | 09/33/39 | `test_sync_policy.py`、E2E `updates-plan` | LOCAL-PASS |
| S52 | 04/06/18/37/38/42 | `test_secret_store.py`、`backup_v3` 演练、`scan_secrets.py --all-history` | LOCAL-PASS（macOS）；跨平台 EXT 空 |

> 台账口径：`LOCAL-PASS` 只说明「本地自动化覆盖并通过」，**不等于**卡面要求的真实环境 PASS。
> 权威外部台账仍是 `docs/M16_LIVE_ACCEPTANCE.md` 与 M16-41 的执行结果。

## 3. 外部证据（E2–E5）现状

| ID | 内容 | 状态 | 解除条件 |
|---|---|---|---|
| E1 | 三端点合成 fixture、envelope、错误语义 | 已取得（`tests/fixtures/sda/`） | — |
| E2 | 真实账密登录、受保护 probe、真实三端点样本 | **未取得** | 授权内网 + 批准账号 |
| E3 | 真实 idle 保活、Cookie/CSRF 轮换、失效后一次重登 | **未取得** | E2 + 跨自然 idle 观察窗口 |
| E4 | Windows/Linux/离线 Chromium 安装、受管进程退出 | **未取得** | 目标机实机 |
| E5 | 两平台断网安装/首次启动/回退 | **未取得** | E4 + 断网环境 |

## 4. 发布包与回退

- 核心产物：`dist/MCChecker-<版本>.zip`（本轮产物在 `/tmp/mcc-release-gate.zip`，未发布）：
  成员 213、外部主机引用 0、逐文件 sha256 校验通过；不含 `data/`、`tests/`、`.qoder/`、mock。
- collector 产物：`dist/collector-bundle.zip`（本轮在 `/tmp/mcc-collector-bundle.zip`）：
  14 个成员（collector 源码 + `requirements.lock` + `MANIFEST.json`），manifest 记录
  `python: 3.12`、`entry: collector/main.py`、逐文件 sha256；`chromium.bundled=false`、
  `wheels.bundled=false` —— 二者必须由目标机（Windows/Linux，E4/E5）实机安装后
  用 `--chromium/--wheels` 重新打包回填，不在本机伪造。
- 版本号冻结留待真实发布时决定；本轮**不发布**、不改现网数据。
- 回退演练：`backup_v3.py backup → verify(ok) → restore --dry-run`，7 个文件路径与哈希对齐，未写盘。
- 已知限制：`2161`（M15 的负责人书面豁免）只能作为「已关闭的历史门禁」引用，
  **不得**写成「外部证据全部 PASS」；V3.0 未合 main、未发布。

## 5. 运维与门禁接入（本轮新增）

- `docs/M16_OPERATIONS.md`：安装/升级/备份回退/保活调参/凭据恢复/安全清理/collector/诊断/发布前清单。
- `README.md` 新增 V3.1 章节：五个 tab 的职责、关键命令与「不得宣称已发布」的边界。
- `scripts/collector_bundle.py` + `tests/test_collector_bundle.py`：collector 独立包打包/校验，
  篡改成员、夹带额外文件、secret 标记、`__pycache__` 都会让校验失败。
- **CI 未扩展**：本轮试过把 `feat/mc-v3.1` 加入触发分支并新增 compileall + secret 扫描两步，
  Ubuntu 侧 4m46s 全绿；**Windows job 在 `Pytest` 步骤长时间不结束**（>60 分钟，历史同类运行为 9–11 分钟），
  定位成本高且与本卡验收无关，已回退该改动（commit 见 PR #252），留作独立排查项：
  建议加 `--faulthandler-timeout` 与 `timeout-minutes` 先抓出具体挂起用例，再决定是否纳入。

## 6. 未完成与下一步

1. M16-02 / 20 / 41：真实 SDA 与真实 idle（E2/E3），按 `docs/M16_LIVE_ACCEPTANCE.md` 执行并回填。
2. M16-14 / 42：Windows/Linux 目标机的 Python 3.12 + Playwright + Chromium 安装、进程树回收、断网安装（E4/E5）；
   拿到 wheel 与 Chromium 后用 `collector_bundle.py --wheels/--chromium` 重新打包并回填 manifest。
3. **Windows CI 挂起排查**：`feat/mc-v3.1` 上的 Windows job 在 `Pytest` 步骤长时间不结束；
   先用 `-o faulthandler_timeout=180` + 步骤级 `timeout-minutes` 抓出具体用例，再决定是否把该分支纳入触发。
4. 本门禁全绿只代表**本地自动化**通过；在 E2–E5 补齐前，M16-42 保持 `In Progress / BLOCKED-EXTERNAL`。
