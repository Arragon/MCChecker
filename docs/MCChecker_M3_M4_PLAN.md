# MCChecker v2 · M3 / M4 剩余开发计划（26 卡）

> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐卡执行。本文是**卡级**计划；每张卡开工前再按第 7 节的模板单独产出该卡的 TDD 步骤计划（含真实测试代码），不要在本文里堆步骤级内容。

**Goal:** 把 Linear 项目 P-INH-14 剩余 26 张卡（M3 审阅/更新/DL/设置 17 张、M4 收尾 9 张）按单线一卡一 PR 做完，使 MCChecker v2 达到 §16 全量验收门禁可关闭的状态。

**Architecture:** 沿用已建好的四层：`server`（只解析请求/序列化响应）→ `services`（组合 core+storage 并做授权、递增修订号）→ `core`（纯逻辑，不 import HTTP）→ `storage`（唯一持久化出口，原子写 + 机型锁）。M3 的新增面集中在 5 个新 core/service 模块（`reviewing.apply_edits`、`downloader` 收紧、`updates`、`tasks`、`scheduler`）与 9 个新 handler 文件；M4 只做兼容、安全、迁移、发布与去 NiceGUI。

**Tech Stack:** Python 3.9.11 标准库（零运行时第三方依赖）、`http.server.ThreadingHTTPServer`、原生 JS（无框架）、pytest 7.4.4、系统 Chrome headless 跑前端测试页。

**Spec:** `docs/MCChecker_REDESIGN_SPEC.md`（§6.7、§7.8–7.12、§10、§11–14、§15 T-16–T-30、§16）。行为变更见 `docs/MCChecker_FEATURE_CHANGES.md`（C01–C32）。执行协议与台账见 `docs/MCChecker_DELIVERY_PLAN.md`。卡定义与状态以 Linear 为准。

## Global Constraints

以下每条对**每一张卡**都生效，卡内不再重复：

- **H1 Python 3.9.11**：禁用 `match`、`tomllib`、`slots=True`、`zip(strict=)`、运行期 `X | Y` 注解。每个模块首行 `from __future__ import annotations`。守护：`PYTHON=./.venv39/bin/python scripts/check_py39.sh` 必须 exit 0。
- **H2 零运行时第三方依赖**：`app/` 新代码只 import 标准库 + 项目内部模块。`app/pages/`、`app/core/scheduler.py`、`app/utils/auth.py` 是旧 NiceGUI 路径，M4-07 才删，M3 期间**不 import 它们、也不修改它们**。
- **H3 权威数据不可损伤**：每张卡验证前后 `data/` 内容必须一致，证据是 `tests/conftest.py::h3_data_guard` 自动守卫的红/绿（INH-1914 起这才是唯一可信证据；守卫每一轮都当场公告「执法中 N 个文件」或「未执法：data/ 不存在或为空」，绿只证明会话首末快照一致，两个盲区见 `docs/know-how.md`）。要报哈希必须当场跑口径命令并把命令一起贴上：`478d9c26ea8dbb8f` 是从文档抄的、当前树上任何拼法都复现不出来、已作废且禁止再引用；本机残留树实测 `5591aacb4a22f34c`，只代表这台机器。临时脚本不得在 pytest 之外跑 `storage`（见 `docs/know-how.md` 第 7 节）。
- **分层红线（§3.3）**：handler 不得直接读写业务文件；持久化一律走 `storage`；`core` 不 import HTTP。
- **授权与修订号**：每个新写接口必须 `authz.authorize(ctx, action)` + 用 `tests/authz_fixtures.py` 补四角色测试；写成功后在 **service 层** `revision.bump(pid, part)`，handler 不碰计数；读接口、失败写（400/403/404/409）与「删了 0 条」都不递增。
- **CSRF 硬条件**：写接口依赖 `http.py` 已有的 `X-Requested-With` 检查，不要在新 handler 里重复实现或放宽。
- **能力开关（决策 14）**：前端入口未接入一律 `disabled` + `title` 且**不发请求**。放行 = 把键加进 `app/web/assets/js/core/api.js` 的 `WIRED`（必须是 `CAP_KEYS` 子集，前端测试页有断言）。新增能力键时 `CAP_KEYS` 与 `MC.unwired` 必须同步。
- **mock 与后端契约同步**：改任何接口语义，`app/web/assets/js/mock/mock-api.js` 必须同 PR 改，并跑前端测试页。
- **证据三件套**（每卡必跑，命令见第 6 节）：全量 `pytest -q`、`check_py39.sh`、`data/` 前后哈希；涉及前端补 Chrome headless `ALL PASSED (n)`；涉及 HTTP 补真实 `run.py` + `curl` 走卡内验收条目。
- **分支与 PR**：`mc-v2/<卡号小写>-<slug>` → PR(base=`feat/mc-v2-delivery`) → `gh pr merge --merge`。一卡一 PR。推送/PR 后不删远端卡分支。网络抖动用外层重试循环（决策 10）。
- **不得伪造验证**：未跑的检查不得报通过；体感/跨设备用例只记「未验证 · 待人工确认」。

---

## 1. 现状与差距（事实源已对齐）

Linear P-INH-14 共 **57 卡**（原 54 + 执行中新立 INH-1912 / INH-1913 / INH-1914）：**34 Done / 23 未完成**。M0（9）、M1（7）、M2（12）、M3 已合入 6 张（01–05、07）；M3 余 11 张、M4 余 9 张。集成分支 `feat/mc-v2-delivery`。**卡数、测试数、哈希号一律当场实测再写**（见 R40/R43），本行此前长期停在「54 卡 / 28 Done / 506 passed」这种过期快照上。

### 1.1 「In Progress」的真实含义（读卡评论得出，不是字面进度）

10 张 In Progress 卡**全部是「前端模板已做完、后端 API 未做」**，卡评论都写了「已完成 / 还没做 / 接着做」。这意味着 M3 的后端完成后，这些卡的前端**通常不需要改**，只需放行能力键 + 联调验收：

| 卡 | 前端已完成（评论原文要点） | 后端缺口 |
|---|---|---|
| M3-03 INH-1888 | 审阅页双栏、`mc.reviewDraft.<pid>` 草稿（键 `文件\|group_key`）、确认框列「原值→新值」、stale/legacy 只能驳回并标「旧版」、`REVIEW_CONFLICT` 后刷新不清其他草稿、`withBusy` 防重 | `GET /review`、`POST /review/submit` 全部（依赖 M3-01/02） |
| M3-06 INH-1891 | 更新源列表/增删改/单条更新/全部更新、状态列 4 态 + 原因 + 最近检查时间、保存只刷新本列表、非部署者只读横幅、改名对话框带「同时迁移本地文件」勾选；更新按钮**只**调 `POST /tasks` | `/sources` CRUD（M3-05）、`POST /tasks`（M3-07） |
| M3-08 INH-1893 | 设置页「定时更新」读 `GET /schedule`、存 `PUT /schedule`、显示下次/上次运行、非部署者禁用 | 标准库调度线程、到点触发 `update_all`、与手动任务互斥 |
| M3-09 INH-1894 | `version=record:<名称>` 同标签打开、复用树/对比/下载、「与上一条对比」预填两侧、「立即拉取」只 `POST /tasks`、任务结束刷新列表 | `refresh_records` 真正下载、内容相同不新建文件 |
| M3-12 INH-1897 | 三计算项 + k1–k8/b、本地草稿、越界行保留、反向多解逐行手选、CSV 浏览器生成、提取预览、访客无保存按钮、`If-Match` 保存、非法值标红不发请求 | 服务端求根（M3-10）、`GET/PUT /dl`、`compute`、`extract`（M3-11） |
| M3-13 INH-1898 | 设置页「人员与权限」两张表、保存后重取 `/session` 刷新顶栏不整页刷新、字段错误留输入框下、非部署者禁用 | `GET/PUT/DELETE /ip-mapping`、`/admins`、IP 合法性、重名 409、删除后下次审阅 403 |
| M3-14 INH-1899 | 改名/增删变量、≥2 变量字段校验、行内显示格式标签与路径、绑定路径可粘贴、局部刷新、对比用 `bound_group` 画紫色组名 | `GET/PUT/DELETE /bindings` 与服务端 ≥2 变量校验 |
| M3-15 INH-1900 | 设置页增删改工具、侧栏 `<a target="_blank" rel="noopener noreferrer">` + `MC.util.safeUrl()`、局部刷新、名称 HTML 以文本显示 | `GET/PUT/DELETE /tools` 与服务端拒绝 `javascript:` / `ftp:` |
| M3-16 INH-1901 | 顶栏切换不整页刷新、标签按 `mc.tabs.<pid>` 隔离、设置页新增（可选复制源）/重命名/删除、删除确认必须输 机型标识、无「物理删除」开关 | `POST/PATCH/DELETE /profiles`、删除走恢复区 |
| M3-17 INH-1902 | 可见时每 10 s 轮询、后台切回立即请求、files 变化刷侧栏 + sha 不同出黄横幅（不自动替换）、favorites/remarks/settings/tasks 分别刷新、本地写后 `syncRevision()`、有任务时 1.2 s 轮询 `/tasks?active=1` | `settings` 与 `tasks` 两个 part 从未被 bump（见 1.2） |

> 结论：**M3 的真实工作量几乎全在后端**。7 张 Todo 卡（M3-01/02/04/05/07/10/11）是硬骨头，10 张 In Progress 卡里 3 张（M3-03/12 及大部分设置卡）只是「放行 + 联调」。

### 1.2 代码现状盘点：可复用 vs 缺口（已核实）

**已有、直接复用、禁止重写：**

| 能力 | 位置 | 谁用 |
|---|---|---|
| `apply_edits` 需要的 loc 定位、compact 文档、`to_compact` | `core/parser.py::to_compact`、`_collect_ns_prefixes`（uri→prefix，私有） | M3-01 |
| 建议提交/列表、JSON 类型校验、`locator`/`source_sha256`/`schema_version=2` | `services/review.py::submit/list_items` | M3-02 |
| 建议状态批量更新（旧路径，语义要核对） | `storage.apply_review_results`、`storage.list_review_items` | M3-02 |
| 下载器骨架 `download_file(url, retries)` | `core/downloader.py`（`MAX_RETRIES=3`） | M3-04 收紧；M2-10 已调用，**函数名保持稳定** |
| 更新源改名迁移（configs/archive/records + 收藏/绑定/建议/DL 引用 + 回滚） | `storage.update_config_mapping` | M3-05（卡面明令「不复制一套移动逻辑」） |
| 映射/记录读写 | `storage.load_config_mapping/save_config_mapping/add_config_mapping/remove_config_mapping/update_config_record_url`、`save_record_file/list_record_versions/load_record_file/get_record_dir` | M3-05、M3-09 |
| 保存并归档（唯一命名、内容相同不归档） | `storage.save_config_file_detailed` → `SaveResult` | M3-05、M3-02 |
| TaskRunner 需要的配置 | `config.Settings.max_workers`（默认 4）、`download_timeout_s`、`download_retries`、`download_allow`、`upload_max_bytes` | M3-07、M3-04 |
| 机型复制（排除项已符合 §7.2） | `storage.copy_profile_data` + `COPY_PROFILE_DIRS=("configs","archive")` + `COPY_PROFILE_FILES`（含 schedule 的 `last_run` 置 None） | M3-16 |
| 恢复区（复制→逐文件 sha256→才删源→才改 JSON→写 manifest，失败逆序回滚） | `services/recovery.py`、`storage.get_recovery_root_dir` | M3-16、M4-04 |
| 人员/绑定/工具/调度的持久化 | `storage.upsert_ip_mapping/remove_ip_mapping/resolve_person_by_ip`、`upsert_admin_user/remove_admin_user/is_admin_user`、`add_binding/remove_binding/save_bindings/get_bound_paths`、`add_tool/remove_tool/update_tool`、`load_schedule/save_schedule` | M3-13/14/15/08 |
| 机型注册表 | `device_models.add_model/remove_model/rename_model/exists/load_models` | M3-16 |
| 修订计数模块与端点 | `services/revision.py`（`PARTS=("files","favorites","remarks","settings","tasks")`、`bump`、`snapshot`、`reset`）、`server/api_revision.py`、路由 `GET /api/v1/p/{pid}/revision` | M3-17 收口 |
| DL 现有算法与前端参照实现 | `core/dltool.py`（`poly_eval`、`poly_find_x`、`batch_calc`、`batch_reverse`、`detect_multi_solutions`、`parse_coeffs_from_data`）、`app/web/assets/js/mock/dlmath.js`（`MC.dlmath = {poly, findRoots, forward, reverse, multiCheck, validate, KEYS}`） | M3-10/11 |
| 错误码全集 | `core/errors.py`：`BUSY/REVIEW_CONFLICT/REVIEW_UNSAFE/REVISION_CONFLICT/TOO_LARGE/DOWNLOAD_FAILED/PARSE_FAILED/SOURCE_CHANGED` 均已定义且已映射 HTTP 状态 | 全部 M3 卡 |

**缺口（本计划要新建）：**

1. `core/reviewing.py::apply_edits` 不存在（现有 `serialize_tree`/`apply_review_updates` 是旧路径，M3-01 明令**新路径不得调用 `serialize_tree`**）。
2. `parser._collect_ns_prefixes` 是私有；`apply_edits` 需要 uri→prefix 注册表 → M3-01 在 `parser` 加公开薄封装 `collect_ns_prefixes(content) -> Dict[str, str]`，避免 core 内部跨模块调私有函数。
3. `update_status.json` **没有任何读写函数**（`storage.py:1670` 只有一句注释提到它属于运行期数据不随机型复制）→ M3-05 在 `storage` 新增 `get_update_status_file/load_update_status/save_update_status`。
4. `services/tasks.py`、`services/scheduler.py`（新，标准库线程）、`services/updates.py` 全部不存在。`app/core/scheduler.py` 是旧 APScheduler 路径，M4-07 才删。
5. `core/dltool.py` 缺 `find_roots`（等距采样 + 变号二分 + 切触重根 + 端点 + 常数函数），且现有代码可能仍有 `value or 0.0` / `or 1.0` 这类把 0 与 None 混淆的写法（M3-10 明令禁止）。
6. `storage.delete_profile_data` 是**硬删**；M3-16 要求机型删除进恢复区 → 需要 `recovery` 侧新函数（如 `move_profile_to_recovery`），**不得修改 `delete_profile_data` 的旧语义**（与 M2-12 对 `delete_config_file` 的处理一致）。
7. **设置页缺分段级能力开关**（本计划新发现，见 2.2）。
8. `revision.bump` 只有 4 个调用点：`recovery.py:54`、`upload.py:55`、`review.py:121`、`favorites.py:161/171/193`。**`settings` 与 `tasks` 两个 part 从未被递增** → M3-05/07/08/13/14/15/16 各自接，M3-17 收口验收。
9. **`tools` 能力键被借用为「任务接口是否可用」的代理，且缺独立 `tasks` 键**（本计划新发现，见 2.4）。已核实：`views/fileview.js:937` 的「立即拉取」门控是 `app.can("deployer") && res.record_url && MC.caps.tools`，`title` 写「拉取修改记录需要任务接口（M3-07 / M3-09）」；而 `shell.js:813`（`GET /tasks?active=1` 轮询）与 `shell.js:830`（`app.startTask` → `POST /tasks`）**完全没有 caps 门控**，`settings.js:78/88` 的「全部更新 / 单条更新」按钮也没有。

---

## 2. 四个前置决策（已定，开工即执行）

### 2.1 D1 · NoneType 建议语义 → 维持现状并写进规范（M3-01 的前置动作）

**问题**：null 节点在 compact 文档里 `v` 是字符串 `"null"`、`t` 是 `NoneType`。§6.7 第 1 步「`proposed_value.strip()` 非空且不等于当前值」会把它当「与当前值相同」400 拦下；而 §6.7 第 4 步的类型表里 `int` 要 `^-?\d+$`、`float` 要可 `float()`、`bool` 要 `true|false`，都不接受 `"null"` → **null 相关建议永远无法通过审阅**。

**决策（已定）**：维持现状 —— null 节点不可提建议，也不可把有值节点改成 null。**不加代码分支**。落地动作放在 M3-01 的同一个 PR 里：

1. `docs/MCChecker_REDESIGN_SPEC.md` §6.7 第 4 步的类型表后补一句：「`NoneType` 节点的值恒为 `null`，对它提交建议会被第 1 步判为『与当前值相同』而拒绝；也不允许把 `int/float/bool/str` 节点改成 `null`。即 **null 是只读值**。若将来需要『清空为 null』，属语义变更，需另开卡同时改本节、`services/review.py::_required_type`、`core/reviewing.apply_edits` 的类型转换与 `mock-api.js` 四处。」
2. 前端：`views/fileview.js` 的建议表单对 `t === "NoneType"` 的节点保持 `disabled`，`title` 写「null 值为只读，不支持提交修改建议」（若已如此，只核对文案）。
3. `tests/test_review_apply.py` 加一条断言：对 `NoneType` 节点提交 `"null"` → `AppError(INVALID_INPUT)`，消息含「与当前值相同」。
4. `docs/MCChecker_DELIVERY_PLAN.md` 第 8 节把「M2-03 遗留的规范疑问」改写成已决条目，第 11 节「下一步」删掉该前置阻塞。

> 高风险提示：这是**规范文本变更**（不是代码语义变更），需在 PR 描述与 Linear INH-1876/INH-1886 评论里显式点名。

### 2.2 D2 · 设置页分段级能力开关（新发现的架构缺口，落在 M3-06）

**事实（已核实）**：`grep -c "MC.caps"` 在 `views/settings.js`、`views/review.js`、`views/dltool.js` 均为 **0** —— 只有 `shell.js` 用 `MC.caps[n.kind]` 控制导航项。也就是说设置页目前是**页级全有/全无**：`shell.js:332` 一旦 `MC.caps.settings` 为真，整个设置页六个分段（sources / schedule / people / bindings / tools / profiles）全部可点。

**冲突**：按卡序，M3-06 是第一个放行 `settings` 键的卡。放行后用户能进设置页，但 people / bindings / tools / profiles 四个分段的 API 分别要到 M3-13/14/15/16 才有 → 这些分段会打空接口报 404，直接违反决策 14「未接入的入口一律 disabled + title，并且**不发请求**」。

**决策**：在 **M3-06** 引入分段级白名单，作为后续 5 张设置卡共用的基础设施：

```js
/* core/api.js：设置页分段接入白名单。必须是 SETTINGS_SEGMENTS 的子集，前端测试页有断言。 */
var SETTINGS_SEGMENTS = ["sources", "schedule", "people", "bindings", "tools", "profiles"];
var WIRED_SETTINGS = ["sources"];              // M3-06 起点
MC.wiredSettingsSegments = WIRED_SETTINGS.slice();
MC.settingsSegmentWired = function (seg) {
  return !!(MC.config && MC.config.mock) || WIRED_SETTINGS.indexOf(seg) >= 0;
};
MC.unwiredSettings = {                          // 与 MC.unwired 同风格，给出接入去向
  schedule: "定时更新将在 M3-08 接入",
  people:   "人员与权限将在 M3-13 接入",
  bindings: "变量绑定将在 M3-14 接入",
  tools:    "外部工具将在 M3-15 接入",
  profiles: "机型管理将在 M3-16 接入",
};
```

`views/settings.js` 在渲染每个分段前调 `MC.settingsSegmentWired(seg)`：未接入的分段渲染只读横幅 + 表单控件 `disabled` + `title` 取 `MC.unwiredSettings[seg]`，且**不发请求**。后续每张设置卡只把自己那一项加进 `WIRED_SETTINGS`（M3-08 `schedule`、M3-13 `people`、M3-14 `bindings`、M3-15 `tools`、M3-16 `profiles`），并在 `app/web/tests/tests.js` 补一条断言（`WIRED_SETTINGS ⊆ SETTINGS_SEGMENTS`、`MC.unwiredSettings` 覆盖未接入项）。

> 这属于**前端能力机制的扩展**，会动 `CAP_KEYS` 相邻代码与前端测试页断言 → M3-06 的 PR 描述必须点名，并跑 Chrome headless 确认 `ALL PASSED (n)` 的 n 增加。M4-08 全绿后与 `MC.caps` 一起删除（决策 14 的既有约定）。

### 2.3 D3 · §12 搜索预算 vs 文档缓存条目数 → 留给 M4-05，M3 不碰

决策 17 已实测：`services/documents.MEMORY_CACHE_MAX_ENTRIES = 24` 是全局搜索超预算的真因（30 文件 × 3 万节点：预热 731 ms，条目数提到 64 后降到 279.5 ms）。**M3 期间不得顺手改这个常量**（属 M1-02 / §6.5 已定的缓存策略）。M4-05 用 `scripts/bench_api.py` 量完后统一决定「按机型内文件数动态定容量」还是「按 §7.6 缓存小写数组」，并回写 SPEC。另注意 M4-05 卡面已明确：§7.6 写 300 ms、§12 表格写 500 ms，**以表格 500 ms 为通过线**，不改规范、不把计时断言改成恒真。

### 2.4 D4 · 新增 `tasks` 能力键，解除 `tools` 的借用（落在 M3-07）

**事实（已核实）**：M2-09 做历史列表时，「立即拉取」按钮需要任务接口但当时没有对应能力键，于是借用了 `MC.caps.tools`（`fileview.js:937`），并在 `title` 里注明「需要任务接口（M3-07 / M3-09）」。同时 `shell.js` 的 `/tasks` 轮询、`app.startTask`、`settings.js` 的两个更新按钮**都没有任何门控**。

**冲突**：按本计划卡序，`tools` 键到 **M3-15** 才放行。若不修，M3-07（任务 API）与 M3-09（记录拉取）做完后，「立即拉取」按钮仍是禁用态 → **UC-30 无法在 M3-09 自己的 PR 里验收**，违反「一卡一 PR、卡内验收自闭合」。反过来，若为了 UC-30 提前放行 `tools`，侧栏「外部工具」会在 M3-15 之前打空的 `/tools` 接口 → 违反决策 14。

**决策**：M3-07 引入独立的 `tasks` 能力键，`tools` 键回归「外部工具」本义：

```js
/* core/api.js */
var CAP_KEYS = [
  "favorites", "remarks", "schedule", "search", "review", "dl", "settings", "import",
  "delete", "history", "compare", "records", "tools", "revision", "temp", "tasks",   // ← 新增 tasks（16 项）
];
var WIRED = [/* …既有 10 项… */, "tasks"];                                            // M3-07 放行
MC.unwired.tasks = "后台任务（更新、拉取记录）将在 M3-07 接入";
```

同 PR 改三处门控：

1. `views/fileview.js:937-938`：`MC.caps.tools` → `MC.caps.tasks`，`title` 文案改为「拉取修改记录需要任务接口（M3-07）」；顺手把 `:936` 那句注释里的「属 M3-08」纠正为「属 M3-07 / M3-09」（原注释写错了卡号）。
2. `views/shell.js:813`（`/tasks?active=1` 轮询）与 `:830`（`startTask`）：加 `if (!MC.caps.tasks) return Promise.resolve(...)` 早退，未接入时**不发请求**。
3. `views/settings.js:78/88`（全部更新 / 单条更新按钮）：按 `MC.caps.tasks` 决定 disabled + title，与 D2 的分段开关叠加（分段未接入 → 整段只读；分段已接入但 `tasks` 未接入 → 只禁用更新按钮）。

`app/web/tests/tests.js` 的「`WIRED ⊆ CAP_KEYS`」与「`MC.unwired` 覆盖全部 `CAP_KEYS`」两条断言会因新增键而红 → 同 PR 补齐（决策 14 的既定要求）。

> M3-15 放行 `tools` 后，`fileview.js` 不再引用 `MC.caps.tools`；M4-08 全绿后 `tasks` 键与其余能力开关一起删除。

---

## 3. 卡序（单线，一卡一 PR）

用户已选**严格单线**。但纯卡号顺序在 M3-06 处会卡住：M3-06 的验收要求 UC-31/UC-32 在真实后端通过，而 UC-31 含「单条更新、全部更新」，其按钮**只**调 `POST /tasks`（M3-07）——若按卡号先做 M3-06，它无法在自身 PR 内完成验收，违反「一卡一 PR、卡内验收自闭合」。同理 M3-08 的调度线程要提交 `update_all` 任务，也依赖 M3-07。

### 3.1 推荐执行顺序（17 步）

| # | 卡 | Linear | 主题 | 放行能力键 / 分段 | 预估 |
|---|---|---|---|---|---|
| 1 | M3-01 | INH-1886 | 审阅写回纯函数 + 语义校验（含 D1 规范回填） | — | 1.5 d |
| 2 | M3-02 | INH-1887 | `GET /review`、`POST /review/submit`（部分提交/冲突/幂等） | — | 2 d |
| 3 | M3-03 | INH-1888 | 审阅页放行 + UC-20~24 联调 | `review` | 0.5 d |
| 4 | M3-04 | INH-1889 | 下载器收紧（scheme/大小/超时/重试/逐跳重定向） | — | 1 d |
| 5 | M3-05 | INH-1890 | `services/updates.py` + `/sources` CRUD + `update_status.json` | — | 2 d |
| 6 | **M3-07** | INH-1892 | TaskRunner + `/tasks`（**提前到 M3-06 之前**）+ 新增 `tasks` 能力键（D4） | `tasks` | 1.5 d |
| 7 | **M3-06** | INH-1891 | 更新源设置页 + **分段能力开关基础设施（D2）** | `settings` + `WIRED_SETTINGS=["sources"]` | 1 d |
| 8 | M3-08 | INH-1893 | 标准库调度器 + `/schedule` + 定时分段 | `schedule` + `WIRED_SETTINGS+=["schedule"]` | 1.5 d |
| 9 | M3-09 | INH-1894 | `refresh_records` 拉取 + 记录查看联调（UC-30） | —（`records` 已于 M2-09 放行） | 1 d |
| 10 | M3-10 | INH-1895 | `dltool.find_roots` / forward / reverse / multi_check 对齐 `dlmath.js` | — | 1.5 d |
| 11 | M3-11 | INH-1896 | `GET/PUT /dl`、`compute`、`extract`（If-Match） | — | 1.5 d |
| 12 | M3-12 | INH-1897 | DL 计算页放行 + UC-37/38/39 联调 | `dl` | 0.5 d |
| 13 | M3-13 | INH-1898 | `/ip-mapping`、`/admins` + 人员分段 | `WIRED_SETTINGS+=["people"]` | 1 d |
| 14 | M3-14 | INH-1899 | `/bindings` + 绑定分段 + 紫色组名联调 | `WIRED_SETTINGS+=["bindings"]` | 1 d |
| 15 | M3-15 | INH-1900 | `/tools` + 工具分段 + 侧栏外链 | `tools` + `WIRED_SETTINGS+=["tools"]` | 0.5 d |
| 16 | M3-16 | INH-1901 | `/profiles` CRUD（删除走恢复区）+ 机型分段 | `WIRED_SETTINGS+=["profiles"]` | 1.5 d |
| 17 | M3-17 | INH-1902 | 修订计数全写路径收口 + UC-40 | — | 1 d |

**M3 合计 ≈ 21 人日。**

### 3.2 依赖图（为什么是这个顺序）

```
M3-01 ──> M3-02 ──> M3-03(放行 review)
                        │
M3-04 ──> M3-05 ──┬──> M3-07 ──┬──> M3-06(放行 settings + D2 分段开关)
 (下载器)  (updates│            │        │
           /sources)            │        └──> M3-08(调度器, 提交 update_all)
                                │                 │
                                └──> M3-09(refresh_records 任务) <──┘
M3-10 ──> M3-11 ──> M3-12(放行 dl)                     [DL 链，与上面两条互不依赖]
M3-13 / M3-14 / M3-16                                  [设置类，互不依赖，都依赖 M3-06 的分段开关]
M3-15(放行 tools)                                       [侧栏 + 设置页两处]
                        └──────────> M3-17(修订计数收口，必须最后)
```

- **M3-17 必须最后**：它的验收是「列出 §7 的写接口清单，缺调用即失败」，只有所有写路径都实现了才能收口。
- **M3-13/14/15/16 理论上可并行**（互不共享文件，除 `WIRED_SETTINGS` 一行），但用户选了单线，故串行；若后续改并行，冲突面只有 `core/api.js` 的 `WIRED_SETTINGS` 数组与 `app/web/tests/tests.js`。
- **DL 链（M3-10/11/12）与更新链（M3-04~09）互不依赖**，如需压缩工期可整段换序，但不要交错。

---

## 4. M3 逐卡详细计划

每卡格式统一：目标 / 文件 / 接口契约（Produces = 后续卡依赖的精确签名）/ 复用点 / 实现要点 / 验收 / 风险。

---

### 卡 1 · M3-01（INH-1886）审阅写回：原文修改与语义校验 · Urgent · 1.5 d

**目标**：在原文字节上做「只改选中节点」的重写，并用「输出再解析后与原文逐节点比较，差异必须恰好等于本次修改」做语义自检；不接 HTTP。

**Files**
- Modify: `app/core/reviewing.py`（新增 `apply_edits`；旧 `serialize_tree`/`apply_review_updates` 保留不动，M4-07 删）
- Modify: `app/core/parser.py`（新增公开 `collect_ns_prefixes`）
- Modify: `docs/MCChecker_REDESIGN_SPEC.md` §6.7（D1 规范回填）
- Modify: `app/web/assets/js/views/fileview.js`（NoneType 建议表单 disabled + title，若已如此只核对文案）
- Modify: `docs/MCChecker_DELIVERY_PLAN.md` 第 8/11 节（D1 改为已决）
- Test: `tests/test_review_apply.py`（新建）

**Interfaces · Produces**（M3-02 依赖，签名必须一字不差）
```python
# app/core/reviewing.py
Edit = Tuple[str, str, str]   # (loc, new_value, kind)；kind = compact 节点的原类型 t
                              # ("str"|"int"|"float"|"bool"|"NoneType"|"attr"|"text")

def apply_edits(src: bytes, fmt: str, edits: Sequence[Edit]) -> bytes:
    """在原文 src 上应用 edits，返回新原文字节。

    fmt: "xml" | "json"（由 storage/文件扩展名判定，与 parser._detect_format 同口径）。
    语义校验不通过时抛 AppError(REVIEW_UNSAFE, ...)，**不返回任何字节**，
    调用方因此拿不到可写入的“成功正文”（卡面验收第 2 条）。
    edits 为空 → 抛 AppError(INVALID_INPUT)。
    loc 在 src 中定位不到 → 抛 AppError(REVIEW_CONFLICT, "无法唯一定位")。
    """

# app/core/parser.py
def collect_ns_prefixes(content: bytes) -> Dict[str, str]:
    """uri → prefix（首次出现者）。§6.1 第 3 步。_collect_ns_prefixes 的公开薄封装。"""
```

**复用点**：`parser.to_compact`（校验时两侧的 compact 文档）、`parser.collect_ns_prefixes`（注册原前缀）、`core/errors.py` 的 `REVIEW_UNSAFE`（已映射 422）。**禁止**调用 `reviewing.serialize_tree`。

**实现要点**
1. **XML 路径**（§6.7 第 5 步）：`ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))` 解析；对 `collect_ns_prefixes(src)` 的每项先 `ET.register_namespace(prefix, uri)`；按 `loc` 逐段定位——`名称[k]` 取第 k 个同名子元素、`@a` 设属性、`#text` 或元素自身值设 `.text`；输出 `ET.tostring(root, encoding=<源声明编码，默认 utf-8>, xml_declaration=<源是否有声明>)`；**不调用 `ET.indent`**（保持原缩进）。
2. **JSON 路径**：`json.loads`，按 JSON Pointer 设值（复用 `parser._pointer_escape` 的逆运算；若不存在逆变换，在 `parser` 加 `pointer_unescape(token) -> str` 并同 PR 测试），值按 `kind` 转换：`int`→`int()`、`float`→`float()`、`bool`→`True/False`、`NoneType`→`None`、其余→`str`；输出 `json.dumps(obj, ensure_ascii=False, indent=2)`。
3. **语义校验（第 6 步，本卡的核心）**：`to_compact(out)` 与 `to_compact(src)` 按 `loc` 比较全部有值节点。断言三件事：① 差异 loc 集合**恰好等于** edits 的 loc 集合；② 每个差异 loc 的新值等于建议值；③ 节点总数与 `t` 序列不变（结构未动）。任一不满足 → `AppError(REVIEW_UNSAFE)`。
4. **注释与处理指令**：带注释样本重写后注释仍在（`insert_comments=True` 的可观测断言：输出字节里 `<!--` 计数与原文一致）。
5. **D1 前置动作**：按 2.1 节四步做完，与本卡同一个 PR。

**验收**
- `tests/test_review_apply.py` 覆盖 T-16 清单全部 11 条：XML 属性值修改、元素文本修改、同名兄弟只改第 2 个、命名空间文档、带注释文档注释保留、JSON int/float/bool 类型保持、类型不符拒绝、校验失败返回 `REVIEW_UNSAFE` 且不写文件、（M3-02 的幂等与 legacy 两条留到卡 2）。
- 断言**业务值与节点集合**，不断言引号风格/空元素写法（卡面已知限制）。
- 变异验证：临时把校验改成「只比 loc 集合不比值」，确认「值改错但 loc 对」的用例会红，再改回。
- `./.venv39/bin/python -m pytest -q tests/test_review_apply.py` + 全量 + `check_py39.sh` + `data/` 哈希。

**风险**
- 标准库重写必然改引号/空元素写法 → 靠第 3 步语义校验兜底；若真实样本含 DTD，校验会拒绝（§17 已列为已知风险，管理员下载原文手工改）。
- `loc` 定位与 `to_compact` 的 occurrence 编号必须同源，否则「同名兄弟只改第 2 个」会改错 → 用 M1-01 的既有断言样本做回归。

---

### 卡 2 · M3-02（INH-1887）审阅提交 API：部分提交、冲突与幂等 · Urgent · 2 d

**目标**：管理员看审阅列表、按文件部分提交；`request_id` 幂等落盘；源文件变化 409；校验失败 422 且不写任何文件。

**Files**
- Modify: `app/services/review.py`（新增 `list_review`、`submit_review`；现有 `submit`（提建议）不动）
- Create: `app/server/api_review.py`
- Modify: `app/server/router.py`（`api_review.register(router)`）
- Modify: `app/core/storage.py`（新增幂等表与审阅结果落盘；见下）
- Modify: `app/web/assets/js/mock/mock-api.js`（`/review`、`/review/submit` 契约同步）
- Test: `tests/test_api_review.py`（新建）、`tests/test_review_apply.py`（补幂等与 legacy 两条）

**Interfaces · Produces**（M3-03 前端与 M3-17 修订计数依赖）
```python
# app/services/review.py
REVIEW_PART = "remarks"     # 审阅写回属于建议域，bump "remarks"（不是 "files"：原文件不变，只新增生成文件）

def list_review(profile_id: str) -> Dict[str, Any]:
    """GET /review 的 payload：{"files": [{...§7.8 结构...}]}。仅管理员（调用方已 authorize）。"""

def submit_review(profile_id: str, actor: Actor, payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST /review/submit。payload = {"request_id","file","source_sha","decisions":[{"group_key","choice"}]}。
    返回 {"generated_file": str|None, "approved": int, "rejected": int}。
    抛 AppError(REVIEW_CONFLICT|REVIEW_UNSAFE|INVALID_INPUT|NOT_FOUND)。"""

# app/core/storage.py（新增）
def load_review_idempotency() -> Dict[str, Any]: ...
def save_review_idempotency(table: Dict[str, Any]) -> None: ...
```

**复用点**：`reviewing.apply_edits`（卡 1）、`storage.save_config_file_detailed`（§5.3 命名 `原名_YYYYMMDD_HHMMSS.ext`，不覆盖原文件）、`storage.list_review_items`、`storage.apply_review_results`（**先核对语义**：它是 v1 旧路径，若其状态更新规则与 §6.7 第 8 步不一致，改为在 service 层自己组装后调 `storage.save_edit_remarks`，不要沿用旧语义）、`storage.use_profile`/`profile_write`（机型锁）、`services/auth.Actor`、`services/revision.bump`。

**实现要点**（§6.7 第 1–9 步，第 3–8 步全部在机型锁内，锁内禁止网络 I/O）
1. `authorize(ctx, "admin")`；非管理员 403 且文件 sha 不变（§16.3）。
2. **幂等**：`request_id` 命中最近 10 分钟的幂等表 → 直接返回上次结果，不生成第二份文件。表**必须落盘**（卡面明令：进程重启后重复 `request_id` 仍不得生成第二份文件）→ 存机型数据里的可选 JSON（建议 `edit_remarks.json` 顶层新增可选键，或独立 `review_idempotency.json`；选独立文件更小改动，但要在 `conftest.DATA_SUBDIRS`/隔离夹具里核对）。返回体与首次一致。
3. 读当前原文与 `sha256`；`!= source_sha` → 409 `REVIEW_CONFLICT`，不写文件。
4. 取该文件全部 `pending` 建议，按 `group_key = locator or node_path or node_key` 分组；**只处理请求中出现的组**，未出现的保持 pending（C07 部分提交）。
5. 每个「通过」的组：有 `locator` 用 `loc`；legacy（无 locator）用 `node_path` 匹配旧 ID，**恰好 1 个**匹配才可用，否则 409 `REVIEW_CONFLICT`（"无法唯一定位"）；定位后要求 `current_value == original_value`，否则 409（"值已变化"）。
6. 一个节点只能通过一条建议：`choice` 是 `remark_id` → 该条 `approved`（写 `generated_file`），**同组其余条与所有「不通过」组全部 `rejected`**；写 `reviewed_by`/`reviewer_ip`/`reviewed_at`；**原子写一次**。
7. `stale = resolvable && current_value != original_value`；stale 或不可定位的节点只能驳回 → 若 `decisions` 里对这类组给了 `choice != "reject"`，返回 400 `INVALID_INPUT`（`detail.field` 指到该 `group_key`）。
8. 校验失败（`REVIEW_UNSAFE`）→ 422，**不写任何文件、不改任何建议状态**（先 apply+verify，全通过后才落盘）。
9. 成功后 `revision.bump(pid, "remarks")`；返回 `{generated_file, approved, rejected}`；登记幂等表。

**验收**
- UC-20/21/22/23 的 API 断言：生成文件仅两个值不同、原文件 sha 不变；未决定的仍 pending；冲突不写文件；legacy 唯一可过、重复 ID 只能驳回。
- 重复 `request_id` 只产生一个新文件（含**重启进程后**再提交一次的用例）。
- 四角色（`tests/authz_fixtures.py`）：访客/成员 403 且 `configs/` 与 `edit_remarks.json` sha 不变；管理员/部署者 2xx。
- `pytest -q tests/test_api_review.py tests/test_review_apply.py` + 全量 + `check_py39.sh` + `data/` 哈希 + 前端 `ALL PASSED`（mock 契约改了就要跑）。
- 真实 `run.py` + `curl`：走一遍 UC-20（提交 2 个节点 → 下载生成文件 → diff 确认只有两处值不同）。

**风险**
- 幂等表落盘位置若选错（写进 `edit_remarks.json` 顶层）会破坏 §5.2「现有字段不变」→ 优先独立文件，并在 PR 描述里说明这是**新增可选文件**（v1.0.1 读不到它，但不影响读取其它 JSON；M4-08 的数据兼容用例要覆盖）。**这是本卡唯一的高风险项（持久化数据格式）**，必须在 PR 与 Linear 评论显式报告。
- 锁内做 `to_compact` 校验属 CPU 密集，大文件会长时间持锁 → 卡面允许（"锁内禁止重解析以外的网络 I/O"），但要在 PR 里记录一次 5 万节点样本的持锁时长，供 M4-05 参考。

---

### 卡 3 · M3-03（INH-1888）审阅页与本地草稿 · Urgent · 0.5 d

**目标**：放行 `review` 能力键，让已做完的审阅页打真实后端，逐项验收 UC-20~24。

**Files**
- Modify: `app/web/assets/js/core/api.js`（`WIRED` 加 `"review"`）
- Modify: `app/web/tests/tests.js`（caps 断言随卡更新）
- Modify（仅在联调发现不一致时）: `app/web/assets/js/views/review.js`
- Test: 前端测试页 + 真实后端手工/脚本验收记录

**Interfaces · Consumes**：卡 2 的 `GET /review`、`POST /review/submit`（含 `REVIEW_CONFLICT` / `REVIEW_UNSAFE` 错误码）。

**实现要点**
1. `WIRED` 加 `"review"`（必须是 `CAP_KEYS` 子集，已在表内）。
2. 联调核对（评论说前端不用改，但必须逐条验）：草稿键是 `文件|group_key`、写 `mc.reviewDraft.<pid>`；确认框列「原值 → 新值」且含「标准库可能统一引号与空元素写法」的说明；stale/不可定位/不唯一 legacy 只能选「不通过」并标「旧版」；`REVIEW_CONFLICT` 后自动刷新列表且不清其他文件草稿；成功给出生成文件名与下载链接。
3. 访客打开审阅路由看到锁定说明、无提交按钮（`shell.js` 的 `can("admin")` 已控）。
4. UC-44 的审阅入口部分（访客直接调 API 得 403）由卡 2 保证，本卡只记录。

**验收**：UC-20、UC-21、UC-22、UC-23、UC-24 在真实后端逐条通过并记录；前端测试页 `ALL PASSED (n)`（n 应比 26 增加）；全量 pytest + `check_py39.sh` + `data/` 哈希。

**风险**：低。唯一坑是 `MC.unwired.review` 文案要留着（`WIRED` 只影响 `caps` 推导，测试页断言 `MC.unwired` 覆盖全部 `CAP_KEYS`）。

---

### 卡 4 · M3-04（INH-1889）安全下载器 · Urgent · 1 d

**目标**：把 `core/downloader.py` 收紧到 §10.2 策略 + §11.2，为 M3-05/09 提供可信下载原语。**不实现更新业务**。

**Files**
- Modify: `app/core/downloader.py`（保持 `download_file(url, retries)` 名称稳定——M2-10 的 `services/upload.py` 在调它）
- Modify: `app/web/assets/js/mock/mock-api.js`（仅当导入链接失败的文案/错误码变化时）
- Test: `tests/test_downloader.py`（已存在，扩展）

**Interfaces · Produces**
```python
# app/core/downloader.py
class DownloadPolicy:      # 或用关键字参数，二选一，但要在 PR 里写明
    timeout_s: int = 30
    retries: int = 3
    max_bytes: int         # 取 Settings.upload_max_bytes
    allow: Tuple[str, ...] = ()        # MCHECKER_DOWNLOAD_ALLOW，非空则目标必须命中
    block_loopback: bool = False       # 访客临时导入额外拒绝 127.0.0.0/8 与 ::1（§10.2）

def fetch(url: str, policy: DownloadPolicy) -> bytes:
    """成功返回正文字节。失败抛 AppError：
    TOO_LARGE(413) 超限；DOWNLOAD_FAILED(502) 含 reason=HTTP状态/超时/连接失败/被策略拒绝。
    错误消息含原因，**不含响应正文里的凭据**、不含 URL 的 userinfo 与 query。"""

def download_file(url: str, retries: int = MAX_RETRIES) -> Optional[bytes]:
    """旧签名保留（M2-10 在用）；内部委托 fetch + 默认 policy，失败返回 None。"""
```

**实现要点**
1. 只允许 `http`/`https`；**不跟随到 `file:` 或其他 scheme**（自定义 `HTTPRedirectHandler`，每跳重跑校验）。
2. 解析主机 → 地址；拒绝 `0.0.0.0`/`::`、`169.254.0.0/16`、`fe80::/10`；`allow` 非空时目标必须命中；`block_loopback=True` 时额外拒绝 `127.0.0.0/8` 与 `::1`。
3. 最多 5 次重定向，**每跳重新执行上述检查**（这是 SSRF 的关键：白名单在首跳通过后，第二跳指向 169.254 必须被拦）。
4. 分块读取，累计 > `max_bytes` → `TOO_LARGE`，**不把正文当配置**（即不返回半截字节）。
5. 失败重试 `retries` 次，间隔 1 s、2 s（测试里把 sleep 注入成可控函数，不要真等）。
6. 错误消息脱敏：只记 `scheme://host/path`（§11.5）。

**验收**
- 用本地 `http.server` 提供五类样本，结果分别为：合法小文件 → 成功；非 XML/JSON → **正文原样返回给上层**（由 M3-05 判 rejected，本卡不判内容）；HTML 错误页（HTTP 200）→ 同上返回正文；超大正文 → `TOO_LARGE`；30x `Location` 指向 `169.254.169.254` → `DOWNLOAD_FAILED`（重定向测试绑 `127.0.0.1`，不需要真访问该地址）。
- `MCHECKER_DOWNLOAD_ALLOW` 非空时：命中 → 成功；不命中 → `DOWNLOAD_FAILED`。
- `file:///etc/passwd` → 拒绝；`javascript:` → 拒绝。
- 断言错误消息里不出现构造好的假凭据（如 URL 里塞 `user:pass@`、正文里塞 `token=abc`）。
- `pytest -q tests/test_downloader.py tests/test_api_import.py`（回归 M2-10 的链接导入）+ 全量 + `check_py39.sh` + `data/` 哈希。
- 变异验证：临时去掉「每跳重校验」，确认重定向到 169.254 的用例会红。

**风险**：这是**安全高风险卡**（SSRF）。PR 描述与 Linear 评论必须显式报告策略清单与未覆盖场景（如 DNS rebinding：解析后连接前主机记录变化 —— §11.2 未要求，记为已知限制，不要静默扩大范围）。

---

### 卡 5 · M3-05（INH-1890）更新源校验提交与映射接口 · Urgent · 2 d

**目标**：`services/updates.py::update_source` 的「下载 → 解析校验 → 根指纹 → sha 比对 → 锁内归档替换」全链路，加 `/sources` CRUD 与 `update_status.json`。HTTP 触发走 M3-07 的任务，本卡函数可被同步测试直接调用。

**Files**
- Create: `app/services/updates.py`
- Create: `app/server/api_sources.py`
- Modify: `app/server/router.py`
- Modify: `app/core/storage.py`（新增 `update_status.json` 三个函数）
- Modify: `app/web/assets/js/mock/mock-api.js`（`/sources` 契约）
- Test: `tests/test_api_sources.py`（新建）

**Interfaces · Produces**（M3-07/08/09 依赖）
```python
# app/services/updates.py
PART = "files"

def update_source(pid: str, name: str, actor: str) -> Dict[str, Any]:
    """§10.2 七步。返回 {"name","status","label","reason","at","archived": str|None}。
    status ∈ success | unchanged | rejected | download_failed | skipped | too_large。
    永不抛业务异常（结果进 task.result.items），只有存储失败才抛。"""

def update_all(pid: str, actor: str, task: Optional[Any] = None) -> Dict[str, Any]:
    """对映射逐项 update_source + refresh_records（有 record_url 时），汇总写 schedule.json.last_run。"""

def refresh_records(pid: str, name: str, actor: str) -> Dict[str, Any]:
    """§10.3。M3-09 实现主体；本卡先给出 not_found/skipped 的骨架与签名，M3-09 填下载与去重。"""

# app/core/storage.py
def get_update_status_file(profile_id: Optional[str] = None) -> str: ...
def load_update_status() -> Dict[str, Any]: ...          # {name: {status,label,reason,at,archived}}
def save_update_status(status: Dict[str, Any]) -> None: ...   # 原子写
```

**复用点**：`downloader.fetch`（卡 4）、`parser.to_compact`（解析校验）、`storage.save_config_file_detailed`（归档 + 内容相同不归档）、`storage.load_config_mapping/save_config_mapping/add_config_mapping/remove_config_mapping/update_config_record_url`、**`storage.update_config_mapping`（M0-07 的改名迁移，卡面明令共用同一函数，不复制一套移动逻辑）**、`fsutil.validate_filename`（允许中文，C26）、`storage.use_profile/profile_write`、`revision.bump`。

**实现要点**
1. `update_source` 严格按 §10.2 七步：无映射项 → `not_found`；无 url → `skipped`（"无下载链接"）；下载失败 → `download_failed`/`too_large`；`to_compact` 失败 → `rejected`（"下载内容不是有效的 XML/JSON"）；**根节点指纹**（XML 根元素 tag / JSON 根类型 object|array）与当前不同 → `rejected`（"根节点由 A 变为 B，可能下载到了错误页面，已拒绝替换"）；sha256 与当前相同 → `unchanged`（不归档，只更新 `update_status` 与检查时间）；否则**机型锁内重新读 sha 确认未变** → `save_config_file` → `success`（带归档名）。
2. **不允许「强制更新」**：没有任何参数能绕过第 4/5 步。
3. 每步结束写 `update_status.json`（可选文件，缺省视为全 null）；`success`/`unchanged` 时 `revision.bump(pid, "files")`（`unchanged` 是否 bump 要定：**不 bump**，因为文件字节没变，与决策 16「失败写与删了 0 条不递增」同口径 —— 在 PR 里写明并加守卫测试）。
4. CRUD：`GET /sources`（全部角色）返回 `{"items":[{name,url,record_url,local_updated_at,last_result}]}`；`POST`（部署者）201，`name` 走 `validate_filename`、`url`/`record_url` 必须 http(s)；`PATCH /sources/{name}`（部署者）改名 + `migrate_files` → 调 `storage.update_config_mapping`，目标已存在任一项 → 409 `FILE_EXISTS`，迁移失败 → 回滚已移动项（`update_config_mapping` 自带 `_rollback`）；`DELETE`（部署者）**只删映射不删文件**。
5. 每个写操作成功后 `revision.bump(pid, "settings")` ← **这是 `settings` part 的第一个调用点**（M3-17 会审）。

**验收**
- 本地 `http.server` 样本：合法 → `success` 且有新归档；相同内容再跑 → `unchanged` 且归档数不变；HTML 错误页/非法内容 → `rejected` 且当前文件 sha 不变；超大 → `too_large`；重定向到 169.254 → `download_failed`。
- UC-31 的改名：收藏、建议、历史、绑定、DL 绑定中的文件名全部跟随（断言 5 份 JSON）。
- UC-32 API 通过。非部署者 `POST` → 403 且 `config_mapping.json` sha 不变（四角色夹具）。
- `pytest -q tests/test_api_sources.py` + 全量 + `check_py39.sh` + `data/` 哈希。
- 变异验证：临时去掉根指纹检查，确认 HTML 错误页用例会红。

**风险**：`update_status.json` 是**新增持久化文件**（高风险类别）→ PR 与 Linear 评论显式报告；M4-08 的数据兼容用例（§16.5）要把它列入「新版写入后 v1.0.1 仍能读同一副本」的检查范围（v1 读不到它但不该崩）。

---

### 卡 6 · M3-07（INH-1892）后台任务队列与任务接口 · High · 1.5 d

**目标**：`services/tasks.py` 的 TaskRunner（`ThreadPoolExecutor`）+ `/tasks` API + 新增 `tasks` 能力键（D4）。**提前到 M3-06 之前**（理由见 3.1）。调度线程另卡（M3-08）。

**Files**
- Create: `app/services/tasks.py`
- Create: `app/server/api_tasks.py`
- Modify: `app/server/router.py`
- Modify: `app/server/http.py`（有序停止：`runner.shutdown(30)` 插进现有停止序列）
- Modify: `run.py`（构造 runner 并注入 router / 停止流程）
- Modify: `app/web/assets/js/core/api.js`（**D4**：`CAP_KEYS` 加 `"tasks"` → 16 项；`WIRED` 加 `"tasks"`；`MC.unwired.tasks` 补文案）
- Modify: `app/web/assets/js/views/fileview.js:936-938`（**D4**：`MC.caps.tools` → `MC.caps.tasks`，title 与注释里的卡号纠正）
- Modify: `app/web/assets/js/views/shell.js:813/830`（**D4**：`/tasks` 轮询与 `startTask` 加 `MC.caps.tasks` 早退；顶栏任务入口列 active 任务，详细进度面板可与设置页更新按钮共用）
- Modify: `app/web/assets/js/views/settings.js:78/88`（**D4**：两个更新按钮按 `MC.caps.tasks` 禁用 + title）
- Modify: `app/web/tests/tests.js`（`WIRED ⊆ CAP_KEYS`、`MC.unwired` 覆盖全部键两条断言随新键更新）
- Modify: `app/web/assets/js/mock/mock-api.js`（`/tasks` 契约）
- Test: `tests/test_api_tasks.py`（新建）

**Interfaces · Produces**（M3-06/08/09 依赖）
```python
# app/services/tasks.py
class Task:            # 字段严格同 §7.10
    id: str; profile: str; type: str; target: Optional[str]
    state: str         # queued|running|succeeded|partial|failed
    progress: Dict[str, int]          # {"done","total"}
    result: Dict[str, Any]            # {"items":[{"name","status","label","reason","at"}]}
    error: Optional[str]; actor: str
    created_at: str; finished_at: Optional[str]

class TaskRunner:
    def __init__(self, max_workers: int = 4) -> None: ...
    def submit(self, ctx: RequestContext, ttype: str, target: Optional[str],
               fn: Callable[[Task], None]) -> Task:
        """key=(pid, ttype, target or '*')；同 key 有 queued/running → AppError(BUSY, task_id=...)。
        update_all 与同机型任意 update_source 互斥。"""
    def get(self, task_id: str) -> Optional[Task]: ...
    def list(self, pid: str, active_only: bool) -> List[Task]: ...
    def shutdown(self, timeout: float = 30) -> None: ...

TASK_TYPES = ("update_source", "update_all", "refresh_records")
RETAIN_SECONDS = 3600; RETAIN_MAX = 200
def get_runner() -> TaskRunner: ...        # 进程内单例，run.py 注入
def register_handler(ttype: str, fn: Callable[..., None]) -> None: ...
```

**实现要点**
1. `max_workers` 取 `Settings.max_workers`（默认 4）。
2. 互斥：相同 `(pid, type, target)` 进行中 → 409 `BUSY` 且 `detail.task_id`；`update_all` 与同机型任意 `update_source` 互斥（卡面括号里的理由：同一机型同时只有一个「更新类」任务修改同一文件）。
3. `POST /p/{pid}/tasks` 仅部署者 → 202 `{"task"}`；`GET /p/{pid}/tasks?active=1` 与 `GET /tasks/{id}` 所有角色可读。
4. `fn` 在工作线程执行，内部自己加机型锁（**不在 HTTP 线程持锁等下载**）；handler 不得同步跑超过 2 秒的下载。
5. 进度变化 → `revision.bump(pid, "tasks")`；结束时按类型 bump `files`/`settings`（§10.1）。← **`tasks` part 的第一个调用点**。
6. 已结束任务内存保留 1 小时或最近 200 条（惰性清理，参照 `temp_store` 的清理写法）。
7. 停止：`scheduler.stop()`（M3-08 接）→ `runner.shutdown(30)` → `server.shutdown()`；本卡先接 runner，留 scheduler 的挂钩点。异常 → `failed` + 用户可读 `error` + 日志堆栈。
8. **D4 前端门控**：按 2.4 节改四处（`api.js` 新增 `tasks` 键、`fileview.js` 换掉借用的 `MC.caps.tools`、`shell.js` 的轮询与 `startTask` 加早退、`settings.js` 两个更新按钮加禁用）。改完后 `grep -n "MC.caps.tools" app/web/assets/js/views/fileview.js` 必须无结果（`tools` 键从此只代表外部工具，M3-15 放行）。

**验收**
- 并发两次相同任务，第二次 409 且 `detail.task_id` 指向第一次。
- 非部署者 `POST` → 403（四角色夹具）。
- 用一个**阻塞的假任务**（只存在于测试）验证 BUSY 与「停止时等待进行中任务结束」；断言停止后进程能退出、不残留非 daemon 工作线程（`threading.enumerate()` 断言）。
- `partial` 状态：`update_all` 里部分项失败时 state 为 `partial` 而非 `failed`（构造 3 项、1 项 404 的映射）。
- **D4 回归**：模板模式（mock 全开）下 `MC.caps.tasks` 为真；生产模式下本卡放行后为真、侧栏「外部工具」仍为假（`MC.caps.tools` 未放行）；未接入时浏览器网络面板里没有对 `/tasks` 的请求。
- `pytest -q tests/test_api_tasks.py` + 全量 + `check_py39.sh` + `data/` 哈希 + 前端 `ALL PASSED (n)`（n 增加）。

**风险**：线程生命周期是本卡唯一难点 —— 测试里若不清理 runner 会污染后续用例（残留工作线程）→ 在 `conftest.py` 加 autouse 夹具 reset 单例；这属于**测试隔离基础设施改动**，PR 要点名。

---

### 卡 7 · M3-06（INH-1891）更新源设置页 + 分段能力开关（D2）· High · 1 d

**目标**：放行 `settings` 能力键，引入分段级白名单（D2），让更新源分段打真实后端并验收 UC-31/UC-32。

**Files**
- Modify: `app/web/assets/js/core/api.js`（`WIRED` 加 `"settings"`；新增 `SETTINGS_SEGMENTS`/`WIRED_SETTINGS`/`MC.settingsSegmentWired`/`MC.unwiredSettings`，见 2.2）
- Modify: `app/web/assets/js/views/settings.js`（每个分段渲染前判 `MC.settingsSegmentWired(seg)`；未接入 → 只读横幅 + 控件 disabled + title，不发请求）
- Modify: `app/web/tests/tests.js`（新增分段开关断言）
- Modify（仅联调需要时）: `app/web/assets/js/mock/mock-api.js`

**Interfaces · Consumes**：卡 5 的 `/sources` CRUD、卡 6 的 `POST /tasks`（`update_source`/`update_all`）。**Produces**：`MC.settingsSegmentWired(seg)` 供 M3-08/13/14/15/16 使用。

**实现要点**
1. `WIRED_SETTINGS = ["sources"]`，其余五项在 `MC.unwiredSettings` 里给出接入去向文案。
2. 状态列显示 `success`/`unchanged`/`download_failed`/`rejected` + 原因 + 最近检查时间（数据来自 `/sources` 的 `last_result`，即 `update_status.json`）。
3. 保存成功只刷新本列表，**不 `location.reload()`**；字段错误显示在输入框下并保留输入（`detail.field`）。
4. 非部署者整页只读横幅，写按钮 disabled + title 说明原因。
5. 更新按钮只调 `POST /tasks`（202）→ 轮询任务 → 结束刷新状态列。**不做第二套同步按钮**（卡面明令）。按钮可用性 = 分段已接入（`MC.settingsSegmentWired("sources")`）**且** `MC.caps.tasks`（D4 已在卡 6 放行，此处只核对）。
6. 改名对话框明确「是否迁移本地文件」（`migrate_files` 勾选）。

**验收**
- UC-31：新增、编辑（改名迁移）、删除、单条更新、全部更新；改名后收藏/建议/历史跟随。
- UC-32：错误页更新后当前文件内容不变，状态列 `rejected`。
- **D2 回归**：进设置页后 people/bindings/tools/profiles/schedule 五个分段是只读横幅 + 禁用控件，且浏览器网络面板里**没有**对 `/admins`、`/bindings`、`/tools`、`/profiles`、`/schedule` 的请求（用 Chrome headless 或手工记录）。
- 前端测试页 `ALL PASSED (n)`（n 增加）；全量 pytest + `check_py39.sh` + `data/` 哈希。

**风险**：`settings.js` 目前**完全没有** caps 判断，本卡是第一次给它加门控 → 改动面比「放行一个键」大，PR 描述要写清这是 D2 基础设施，后续 5 张卡依赖它。

---

### 卡 8 · M3-08（INH-1893）标准库调度器与定时设置 · High · 1.5 d

**目标**：用标准库 daemon 线程替换 APScheduler，到点向 TaskRunner 提交 `update_all`；`/schedule` 读写 + `next_run_at`。

**Files**
- Create: `app/services/scheduler.py`（**新代码放 services**；`app/core/scheduler.py` 旧文件本卡不删，M4-07 删）
- Create: `app/server/api_schedule.py`
- Modify: `app/server/router.py`、`run.py`（只启动新调度器；停止顺序 `scheduler.stop()` → `runner.shutdown(30)` → `server.shutdown()`）
- Modify: `app/core/storage.py`（`load_schedule` 默认值补 `last_run: None`；如需 `next_run_at` 持久化则另议——**不持久化**，由调度器算）
- Modify: `app/web/assets/js/core/api.js`（`WIRED` 加 `"schedule"`；`WIRED_SETTINGS` 加 `"schedule"`）
- Modify: `app/web/assets/js/views/overview.js`（速览的定时状态块用 `schedule` 键）
- Test: `tests/test_scheduler.py`（新建）、`tests/test_api_schedule.py`（可并入前者）

**Interfaces · Produces**（§10.4 原样）
```python
class Scheduler(threading.Thread):
    def __init__(self, runner: TaskRunner) -> None: ...   # daemon=True
    def reload(self) -> None: ...        # 读全部机型 schedule.json，重算 next_run，Event 唤醒
    def run(self) -> None: ...
    def stop(self) -> None: ...
    def next_run_at(self, pid: str) -> Optional[datetime]: ...
```

**实现要点**
1. 循环：算最早 `next_run` → `Event.wait(min(剩余秒数, 60))` → 到点的机型提交 `update_all`（`actor="system"`）→ `next_run = now + interval`；**BUSY 则 1 分钟后重试**（不丢这次）。
2. `next_run` 初值：`schedule.last_run.at + interval`（已过期 → 启动后 2 分钟执行，避免启动风暴）；无 `last_run` → `启动时间 + interval`（与 APScheduler IntervalTrigger 一致）。
3. 每小时调临时文件清理（`temp_store` 的惰性清理函数）；每 24 小时清理可丢弃的归档解析缓存（`cache/doc_v2`、`cache/diff_v2` 是派生数据，损坏即丢弃重建 → 可安全删）。
4. `interval_hours` 是 1–2160 的整数；界面可用小时或天，「天」保存为 24 的倍数，回显自动选单位（前端已做，后端只校验范围，非法 → 400 且原文件不变）。
5. `GET /schedule` 返回 `{enabled, interval_hours, last_run, next_run_at}`；`PUT` 仅部署者，保存后调 `scheduler.reload()`，**不重启进程**；成功 `revision.bump(pid, "settings")`。
6. 手动与定时同时触发同一目标 → `BUSY`（由卡 6 的互斥保证，本卡只需断言）。
7. **测试把时钟注入为可控函数**（`now_fn` 参数或模块级可 monkeypatch 钩子），不真 sleep。

**验收**
- UC-33：保存 2 天 → `interval_hours=48`，页面显示「2 天」，`next_run_at` 正确。
- `tests/test_scheduler.py` 断言 `interval_hours=1` 时 `next_run_at == 基准 + 1 小时`。
- 非法间隔（0、2161、`"abc"`、`1.5`）→ 400，`schedule.json` 字节不变。
- 非部署者 `PUT` → 403（四角色）。
- 新启动路径不再 import apscheduler：`grep -n "apscheduler" run.py app/services/*.py app/server/*.py` 无结果（旧 `app/core/scheduler.py` 与 `app/pages/` 仍在，M4-07 删）。
- 停止顺序有测试或日志证据（构造一次 `stop()`，断言线程退出、无非 daemon 残留）。
- 全量 pytest + `check_py39.sh` + `data/` 哈希 + 前端 `ALL PASSED`。

**风险**：daemon 线程 + 测试 = 容易泄漏到别的用例 → autouse 夹具里 `stop()`；时钟必须可注入，否则测试要么慢要么脆。

---

### 卡 9 · M3-09（INH-1894）修改记录拉取与文件内查看 · High · 1 d

**目标**：实现 `updates.refresh_records` 并注册为任务类型；页面不再自己定时拉取；内容相同不新建记录文件。

**Files**
- Modify: `app/services/updates.py`（填 `refresh_records` 主体，替换卡 5 的骨架）
- Modify: `app/services/tasks.py`（`register_handler("refresh_records", ...)`）
- Modify: `app/web/assets/js/views/fileview.js`（records 分段：「立即拉取」→ `POST /tasks` + 进度 + 结束刷新。**按钮门控已在卡 6 的 D4 里从 `MC.caps.tools` 换成 `MC.caps.tasks` 并放行**，本卡只联调，不要再动门控）
- Modify: `app/web/assets/js/mock/mock-api.js`（records 契约同步）
- Test: `tests/test_api_records.py`（新建）

**Interfaces · Consumes**：`downloader.fetch`、`tasks.register_handler`、`storage.save_record_file(name, content, source_url=...)`、`storage.list_record_versions(name)`、`storage.load_record_file`。

**实现要点**
1. 下载 `record_url` → 若与**最新一条**记录 sha256 相同 → `unchanged`，**不新建记录文件**（C13）→ 否则 `storage.save_record_file` 按 §5.3 命名保存。
2. 记录内容**不要求可解析**（无法解析的在查看时显示原文下载）。
3. 扩展名取自 `record_url` 路径（≤10 个字母数字），否则 `.txt`。
4. `records/` 目录与命名保持兼容（`storage.get_record_dir`/`get_record_path` 已实现，不要另起一套）。
5. 只有调度器与「立即拉取」任务触发下载 → 确认 `fileview.js` 里没有残留的页面级定时器（M2-09 已禁用，本卡核对并删掉死代码）。
6. 查看记录在同一文件标签内用 `version=record:<名称>` 打开（前端已做，后端 `documents`/`history` 已支持 → 只联调）。
7. 任务成功后 `revision.bump(pid, "files")`（记录属于文件域）。

**验收**
- UC-30：拉取后列表增加；再拉相同内容条数不变；「与上一条对比」能打开。
- 本地 `http.server` 提供两条不同记录 + 一次相同内容；断言下载次数 = 2（不是 3）。
- **两个浏览器打开记录页时，服务端下载次数不随打开页面的人数增加**（断言 `http.server` 的请求计数）。
- `pytest -q tests/test_api_records.py` + 全量 + `check_py39.sh` + `data/` 哈希 + 前端 `ALL PASSED`。

**风险**：低。旧 `/record_view` 跳转**不在本卡**（M4-01），别顺手做。

---

### 卡 10 · M3-10（INH-1895）DL 求根与正向反向计算 · High · 1.5 d

**目标**：把 `core/dltool.py` 的纯计算与 `app/web/assets/js/mock/dlmath.js` 逐函数对齐。不接 HTTP，不改配置文件格式。

**Files**
- Modify: `app/core/dltool.py`
- Test: `tests/test_dltool_multi.py`（扩展，**保留现有通过用例**）
- 参照（只读，不改）: `app/web/assets/js/mock/dlmath.js`、`app/web/tests/tests.js` 的 DL 用例

**Interfaces · Produces**（M3-11 依赖；命名与 §7.11 响应对齐）
```python
KEYS = ("k1","k2","k3","k4","k5","k6","k7","k8")

def find_roots(target: float, coeffs: Dict[str, float], lo: Optional[float], hi: Optional[float],
               samples: int = 200) -> Dict[str, Any]:
    """等距采样 + 变号二分 + 切触重根 + 端点 + 常数函数。
    返回 {"status": "ok"|"identity"|"constant"|"no_range", "roots": [{"x": float, "residual": float}]}。
    结果是「范围内的候选根」，**不自动选绝对值最小**。
    lo/hi 为 None → status="no_range"，roots=[]（空范围是 null，不是 0）。"""

def forward(item: Dict[str, Any], kind: str, xs: Sequence[float]) -> List[Dict[str, Any]]:
    """每行 {"input_index": int, "x","y_base","y","status": "ok"|"out_of_x_range"|"out_of_y_range"}。
    越界行**标注不丢弃**。"""

def reverse(item: Dict[str, Any], kind: str, ys: Sequence[float]) -> Tuple[List[Dict[str, Any]], bool]:
    """每行 {"input_index","y","y_base","x_values","residuals",
             "status": "single"|"multiple"|"none"|"identity"|"out_of_y_range"}；第二返回值 = exhaustive。"""

def multi_check(item: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """{"has_multi": bool, "cases": [{"y","x_values"}]}。"""

def validate(item: Dict[str, Any], kind: str) -> str:
    """返回错误文案；空串表示合法。功率转换系数为 0 → 拒绝。"""
```

**实现要点**
1. 算法与 `dlmath.js` 一致：`poly`、`isConstant`、`bisect`、`minAbs`、`findRoots`、`inRange`、`scaleOf`、`validate` 逐一对应。**不一致时以规范与前端测试页用例为准，并改到两边一致**（卡面明令）——若需要改 `dlmath.js`，同 PR 改并跑前端测试页。
2. `(x-3)²` 必须检出切触根（变号二分检不出重根 → 需要 `minAbs` 那一路的极值检测）。
3. 常数函数有明确状态（`identity`/`constant`），不静默返回空。
4. **空范围是 `null`，0 就是 0**：全模块 grep 掉 `value or 0.0` / `or 1.0` 这类写法，改成显式 `if value is None`。这是 C15 的核心。
5. 功率转换系数为 0 → `validate` 返回拒绝文案（除零）。
6. 每行带 `input_index`（前端要靠它对回输入行）。

**验收**
- 切触根、越界标注、`input_index`、常数函数、功率转换系数 0 拒绝均有断言。
- 与 `app/web/tests` 中 DL 用例的期望值**逐一相同**（把前端用例的输入/期望抄进 pytest 做对照表，是最省事的对齐方式）。
- `pytest -q tests/test_dltool_multi.py`（现有用例不得改期望值来通过）+ 全量 + `check_py39.sh` + `data/` 哈希。
- `grep -n "or 0\.0\|or 1\.0" app/core/dltool.py` 无结果。

**风险**：浮点一致性 —— JS 与 Python 的 `round`、二分收敛容差（`dlmath.js` 的 `bisect` 与 `dltool._bisect(tol=1e-12, max_iter=80)`）必须同参数，否则边界用例两边结果差 1e-12 导致断言不稳 → 断言用 `pytest.approx`，容差写死并在 PR 里说明。

---

### 卡 11 · M3-11（INH-1896）DL 试算、提取预览与条件保存 · High · 1.5 d

**目标**：`GET /dl`、`POST /dl/{item}/compute`、`POST /dl/{item}/extract`、`PUT /dl/{item}`（`If-Match`）。页面另卡。

**Files**
- Create: `app/services/dl.py`
- Create: `app/server/api_dl.py`
- Modify: `app/server/router.py`
- Modify: `app/web/assets/js/mock/mock-api.js`（`/dl` 契约）
- Test: `tests/test_api_dl.py`（新建）

**Interfaces · Produces**（M3-12 前端依赖）
```python
# app/services/dl.py
PART = "settings"          # DL 配置属设置域（M3-17 会审这个归属）
ITEMS = ("pp_energy", "rp_energy", "rp_width")

def current_rev(pid: str) -> str: ...                  # dltool_config.json 的 sha1 前 12 位
def get_state(pid: str) -> Dict[str, Any]: ...         # {"rev", "items": {pp_energy, rp_energy, rp_width}}
def compute(pid: str, item: str, mode: str, values: List[float], params: Dict[str, Any]) -> Dict[str, Any]: ...
def extract(pid: str, item: str, binding: Dict[str, Any]) -> Dict[str, Any]: ...
def save_item(pid: str, item: str, payload: Dict[str, Any], if_match: str) -> Dict[str, Any]: ...
```

**复用点**：卡 10 的 `find_roots`/`forward`/`reverse`/`multi_check`/`validate`、`dltool.parse_coeffs_from_data`（提取）、`dltool.load_config`/`save_config`、`storage.use_profile/profile_write`、`services/documents`（提取时读源文件的 compact 文档）。

**实现要点**
1. `GET /dl`（全部角色）返回 `rev` 与三个 item；**`editing` 读到后忽略**（不做共享编辑态，C15）。
2. `POST /dl/{item}/compute`（全部角色）：按**请求里的 `params`** 计算，**不写文件**；三种 mode 的响应结构严格按 §7.11 表格。
3. `POST /dl/{item}/extract`（全部角色）：**只预览**。`variable_path` 是旧节点 ID；取子节点值作字段（XML 子元素/属性标签、JSON 键）；无法转为有限数 → `invalid`；缺失 → `missing`；**这两种都保持原系数值，绝不写 0**（C15）。响应 `{"coeffs": {只含成功提取的键}, "fields": [{"field","coeff","status","value"|"raw"}]}`。
4. `PUT /dl/{item}`（仅部署者）：必须 `If-Match: <rev>`；不符 → 409 `REVISION_CONFLICT`；保存时 `editing` 写 `false`；空值保存为 `null`（不是 0、不是 ""）；非法数值 → 400 且**不写文件**；`dltool_config.json` **字段集合不变**（只改值）。
5. 成功后 `revision.bump(pid, "settings")`。

**验收**
- UC-38 的 API：访客 `PUT` → 403；两人并发，第二次 → 409 `REVISION_CONFLICT`；文件只被先保存者改一次（断言 mtime/ sha 与内容）。
- UC-39 的 API：缺失字段 `status="missing"`，`coeffs` **不含该键**；非数值 `status="invalid"` 且带 `raw`。
- `If-Match` 缺失 → 428 还是 400？**规范未明说** → 按 §7.1 通用约定选 400 `INVALID_INPUT`（`detail.header="If-Match"`），并在 PR 里记录这个补白（不要静默选 428，前端已按某个码写了处理）。**开工前先核对 `views/dltool.js` 期望哪个码**，以前端既有实现为准。
- 保存成功后 `editing` 仍为 `false`（读回断言）。
- 四角色 + `pytest -q tests/test_api_dl.py tests/test_dltool_multi.py` + 全量 + `check_py39.sh` + `data/` 哈希 + 前端 `ALL PASSED`。

**风险**：`rev` 用 sha1 前 12 位 → 必须与前端 `dltool.js` 已实现的算法完全一致（前端已做完，先读它再写后端，别自己发明）。

---

### 卡 12 · M3-12（INH-1897）DL 计算页 · High · 0.5 d

**目标**：放行 `dl`，DL 页打真实后端，验收 UC-37/38/39。

**Files**
- Modify: `app/web/assets/js/core/api.js`（`WIRED` 加 `"dl"`）
- Modify: `app/web/tests/tests.js`（caps 断言更新）
- Modify（仅联调需要时）: `app/web/assets/js/views/dltool.js`

**实现要点 / 验收**
1. 联调核对（前端已做完，逐条验）：三计算项、k1–k8/b、PP 另有传输系数与功率转换系数；修改只进本地草稿（内存，刷新即丢 = 「放弃修改」）；所有人都可试算；正向输入 `10 20 200` 时越界行**可见**；反向多解由用户每行选择、**不自动选最优**、CSV 使用该选择；提取先预览再「应用到草稿」，缺失字段标「字段缺失」且系数保持原值；访客没有「保存为配置」；部署者保存带 `If-Match`，冲突提示重新加载不覆盖他人；非法数值输入框标红且不发保存。
2. UC-37、UC-38、UC-39 在真实后端逐条记录；保存成功后 `editing` 仍为 false（读 `/dl` 断言）。
3. 前端测试页 DL 用例 `ALL PASSED`；全量 pytest + `check_py39.sh` + `data/` 哈希。

**风险**：低（前端已完成）。

---

### 卡 13 · M3-13（INH-1898）IP 对应表与管理员名单 · High · 1 d

**目标**：`/ip-mapping`、`/admins` 读写 API + 放行设置页 people 分段；保存后刷新顶栏身份。

**Files**
- Create: `app/services/people.py`、`app/server/api_people.py`
- Modify: `app/server/router.py`
- Modify: `app/web/assets/js/core/api.js`（`WIRED_SETTINGS` 加 `"people"`）
- Modify: `app/web/assets/js/mock/mock-api.js`
- Test: `tests/test_api_people.py`（新建）

**Interfaces · Produces**
```python
# app/services/people.py
PART = "settings"
def list_ip_mapping(pid) -> Dict[str, Any]              # {"items":[{"ip","name"}]}
def put_ip_mapping(pid, path_ip, payload) -> Dict[str, Any]   # 路径 ip != body ip → 视为改 IP，同一锁内先删后写
def delete_ip_mapping(pid, ip) -> Dict[str, Any]
def list_admins(pid) -> Dict[str, Any]
def add_admin(pid, name) -> Dict[str, Any]              # 重名 → AppError(FILE_EXISTS) 409
def remove_admin(pid, name) -> Dict[str, Any]
```

**复用点**：`storage.load_ip_mapping/save_ip_mapping/upsert_ip_mapping/remove_ip_mapping/resolve_person_by_ip`、`storage.load_admin_users/save_admin_users/upsert_admin_user/remove_admin_user/is_admin_user`、`storage.profile_write`（同一锁内先删后写）、`services/auth`（身份重算）、`revision.bump`。**格式保持 `ip_mapping.json` / `admin_users.json` 不变**。

**实现要点**
1. GET 全部角色；写仅部署者。IP 必须合法 IPv4/IPv6（`ipaddress.ip_address`，非法 → 400 `INVALID_INPUT` + `detail.field="ip"`）。
2. 路径 IP 与 body IP 不同 = 改 IP，**同一锁内先删后写**（不能分两次锁，否则并发下会丢/重）。
3. 管理员重名 → 409。删除管理员后，该人**下一次审阅请求 403**（与 M1-04 的 `authz` 一致 —— `is_admin_user` 每次请求实时读，不缓存）。
4. 每个写操作后 `revision.bump(pid, "settings")`。
5. 前端保存成功后重新 `GET /session` 刷新顶栏（已做）：登记当前 IP 并加为管理员后，身份弹层变管理员、侧栏出现「审阅修改」、**不整页刷新**。

**验收**
- UC-34、UC-44 在真实后端通过。
- 非部署者 `PUT` → 403 且 `ip_mapping.json`/`admin_users.json` sha 不变（四角色）。
- 非法 IP（`999.1.1.1`、`abc`、空串、`10.0.0.1/24`）→ 400，文件不变。
- 改 IP 用例：`PUT /ip-mapping/10.0.0.1` body `{"ip":"10.0.0.2","name":"张三"}` → 旧 IP 条目消失、新 IP 存在、总数不变。
- 删除管理员后立刻以该人身份调 `GET /review` → 403。
- `pytest -q tests/test_api_people.py` + 全量 + `check_py39.sh` + `data/` 哈希 + 前端 `ALL PASSED`。

**风险**：**身份/授权高风险卡**（改 IP 表等于改谁能写）。PR 与 Linear 评论必须显式报告；断言「403 时文件 sha 不变」是本卡的安全底线。

---

### 卡 14 · M3-14（INH-1899）变量绑定编辑与校验 · High · 1 d

**Files**
- Create: `app/services/bindings.py`、`app/server/api_bindings.py`
- Modify: `app/server/router.py`、`app/web/assets/js/core/api.js`（`WIRED_SETTINGS` 加 `"bindings"`）、`mock-api.js`
- Test: `tests/test_api_bindings.py`（新建）

**Interfaces · Produces**
```python
# app/services/bindings.py
PART = "settings"
def list_bindings(pid) -> Dict[str, Any]                    # {"items":[{group_name, variables:[{file,path}]}]}
def put_binding(pid, original_group: str, payload: Dict[str, Any]) -> Dict[str, Any]
    # PUT 路径中的组名 = 原名，body 的 group_name = 新名；路径组名不存在即新增
def delete_binding(pid, group: str) -> Dict[str, Any]
```

**复用点**：`storage.load_bindings/save_bindings/add_binding/remove_binding/get_bound_paths`、`core/differ.compare_documents` 的 `bound_group`（M2-08 已实现）、`revision.bump`。**`bindings.json` 格式不变，`path` 仍是 `<文件名>/<标签>/…`**。

**实现要点**
1. 至少 2 个**有效**变量（`file` 存在且 `path` 非空）；不足 → 400 `INVALID_INPUT` + `detail.field="variables"`。空变量列表 → 400。
2. 改名不得与已有组重名 → 409 `FILE_EXISTS`，原组仍在。
3. 写仅部署者；成功后 `revision.bump(pid, "settings")`。
4. 前端：行内显示格式标签与绑定路径；详情面板复制的绑定路径可粘贴进表单；保存后局部刷新；对比里这些变量带紫色组名（联调 M2-08 的 `bound_group`，**不重做**）。

**验收**
- UC-35：两个变量的新组在对比中显示紫色组名（真实后端跑一次 `POST /compare`，断言 items 里对应 loc 的 `bound_group` 等于组名）；1 个变量时字段报错且不写入（`bindings.json` sha 不变）。
- 同名改名 → 409，原组仍在。
- 四角色 + `pytest -q tests/test_api_bindings.py` + 全量 + `check_py39.sh` + `data/` 哈希 + 前端 `ALL PASSED`。

**风险**：低。「有效变量」的判定口径要与前端一致（前端已做字段校验）→ 开工前读 `views/settings.js` 的绑定分段确认。

---

### 卡 15 · M3-15（INH-1900）外部工具菜单 · High · 0.5 d

**Files**
- Create: `app/services/tools.py`、`app/server/api_tools.py`
- Modify: `app/server/router.py`、`app/web/assets/js/core/api.js`（`WIRED` 加 `"tools"`；`WIRED_SETTINGS` 加 `"tools"`）、`mock-api.js`
- Test: `tests/test_api_tools.py`（新建）

**Interfaces · Produces**
```python
# app/services/tools.py
PART = "settings"
ALLOWED_SCHEMES = ("http", "https")
def list_tools(pid) -> Dict[str, Any]
def put_tool(pid, original_name: str, payload: Dict[str, Any]) -> Dict[str, Any]
    # 路径 name 不存在即新增；名称唯一；url 只允许 http/https
def delete_tool(pid, name: str) -> Dict[str, Any]
```

**复用点**：`storage.load_tools/save_tools/add_tool/remove_tool/update_tool`、`revision.bump`。**`tools.json` 字段不变**。

**实现要点**
1. 名称唯一（重复 → 409）；URL 只允许 `http`/`https` → `javascript:alert(1)`、`ftp://x`、`data:`、空串、无 scheme 全部 400 `INVALID_INPUT` + `detail.field="url"`。**用 `urllib.parse.urlparse` 判 scheme，不要用字符串前缀匹配**（`java\nscript:` 这类绕过）。
2. 名称/描述去空白后 1–100 字符、无控制字符（§11.2）。
3. 写仅部署者；成功后 `revision.bump(pid, "settings")`。
4. 前端已做：侧栏 `<a target="_blank" rel="noopener noreferrer">` + `MC.util.safeUrl()`，禁止把 URL 拼进 `window.open("...")` → 本卡只联调 + 核对 `shell.js:373/453` 的 `MC.caps.tools` 分支现在会走真值路径。
5. **D4 收尾核对**：`tools` 键自此回归「外部工具」本义 → `grep -rn "MC.caps.tools" app/web/assets/js/` 结果应只剩 `shell.js` 的侧栏三处（373/453/454）与 `settings.js` 的工具分段门控，**`fileview.js` 不应再出现**（卡 6 已换掉）。

**验收**
- UC-36：`ftp://x` 字段报错；改 http 后侧栏出现并在新窗口打开。
- `tests/test_api_tools.py` 必含 `javascript:alert(1)` 与重复名称两条。
- 工具名中的 HTML（`<img src=x onerror=alert(1)>`）以文本显示（M4-02 会做全站回归，本卡先记录页面证据）。
- 四角色 + 全量 + `check_py39.sh` + `data/` 哈希 + 前端 `ALL PASSED`。

**风险**：**安全卡**（XSS/协议注入）。PR 显式报告被拒 scheme 清单。

---

### 卡 16 · M3-16（INH-1901）机型切换、复制与删除 · High · 1.5 d

**Files**
- Create: `app/services/profiles.py`、`app/server/api_profiles.py`
- Modify: `app/services/recovery.py`（新增机型级恢复；**不改 `storage.delete_profile_data` 的旧硬删语义**）
- Modify: `app/server/router.py`、`app/web/assets/js/core/api.js`（`WIRED_SETTINGS` 加 `"profiles"`）、`mock-api.js`
- Test: `tests/test_api_profiles.py`（新建）

**Interfaces · Produces**
```python
# app/services/profiles.py
PART = "settings"
def list_profiles(pid) -> Dict[str, Any]                  # device_models.load_models()
def create_profile(pid_ctx, payload) -> Dict[str, Any]    # {"name","copy_from"|None} → {"id","name"}
def rename_profile(model_id, payload) -> Dict[str, Any]   # PATCH 改显示名
def delete_profile(model_id) -> Dict[str, Any]            # → {"recovery": "<恢复区目录名>"}

# app/services/recovery.py（新增）
def move_profile_to_recovery(model_id: str, actor: str) -> str:
    """把 data/profiles/<id>/ 整个目录按 M2-12 同一套顺序移入 data/recovery/<ts>_delete_profile_<id>/：
    复制 → 逐文件 sha256 校验 → 才删源 → 写 manifest.json（含 moved 与 removed_refs）。
    失败逆序回滚。返回恢复区目录名。"""
```

**复用点**：`device_models.add_model(name, copy_from)`/`rename_model`/`remove_model`/`exists`、**`storage.copy_profile_data`（复制范围已符合 §7.2 与卡面：`COPY_PROFILE_DIRS=("configs","archive")` + `COPY_PROFILE_FILES` 六个 JSON，不含 cache/records/建议/IP表/管理员/update_status，且 schedule 的 `last_run` 置 None）**、M2-12 的 `recovery.py` 顺序与 `manifest.json` 结构、`revision.bump`。

**实现要点**
1. `POST /profiles` 仅部署者；名称为空 → 400；重复 → 409；`copy_from` 可空（空则只建空机型）。
2. `PATCH` 改显示名（`device_models.rename_model`）。
3. `DELETE`：**不能删 default**（400 或 409，以前端既有文案为准）；数据目录移入恢复区；响应含 `recovery` 目录名；**不再提供「物理删除」开关**；前端确认框要求输入机型标识（已做）。
4. 非法 id **不回退到 default 的数据**（`_normalize_profile_id` 已抛 `NOT_FOUND`，`http.py` 已统一校验 `exists` → 核对即可，不要新增回退）。
5. 顶栏切换机型不整页刷新，标签按 `mc.tabs.<pid>` 隔离（前端已做，联调）。
6. 增删机型后调 `scheduler.reload()`（M3-08 已提供）。
7. 成功后 `revision.bump(pid, "settings")`（删除时用**被删机型**还是**当前机型**的计数？→ 用当前会话机型，因为被删机型的计数无意义；在 PR 里写明这个选择）。

**验收**
- UC-41：新增并复制、切换、重命名、删除。删除后原目录不在 `profiles/` 下，恢复区有 `manifest.json`（断言 `moved` 条目数与 sha256）。
- 复制范围断言：新机型有 `configs/`、`archive/` 与 6 个 JSON；**没有** `cache/`、`records/`、`edit_remarks.json`、`ip_mapping.json`、`admin_users.json`、`update_status.json`；`schedule.json` 的 `last_run` 为 null 但 `interval_hours` 照抄。
- 非部署者 `POST`/`DELETE` → 403，`device_models.json` sha 不变（四角色）。
- `pytest -q tests/test_api_profiles.py tests/test_profiles.py` + 全量 + `check_py39.sh` + `data/` 哈希。
- 变异验证：临时把 `move_profile_to_recovery` 的「校验后才删源」改成「先删源」，确认注入失败的回滚用例会红。

**风险**：**破坏性操作 + 持久化数据高风险卡**（删机型）。PR 与 Linear 评论必须显式报告；`storage.delete_profile_data` 的旧硬删语义**一行都不动**（与 M2-12 对 `delete_config_file` 的处理一致），新路径只走 recovery。

---

### 卡 17 · M3-17（INH-1902）修订号与多用户更新横幅 · High · 1 d（M3 收口）

**目标**：把 §7 的**全部**写接口接上 `revision.bump`，验收 UC-40。前端轮询与横幅已做完。

**现状（已核实）**：`services/revision.py` 与 `GET /revision` 自 M2-01 就存在，`PARTS` 五个键齐全。但 `revision.bump` 只有 6 处调用（`recovery.py:54`、`upload.py:55`、`review.py:121`、`favorites.py:161/171/193`）→ **`settings` 与 `tasks` 从未被递增**。M3-05/07/08/13/14/15/16 已各自接了自己那部分（见各卡实现要点），本卡做**全量审计 + 补漏 + 收口验收**。

**Files**
- Modify: `app/services/*.py`（补漏的写路径）
- Modify: `app/web/assets/js/views/shell.js`（仅联调发现不一致时）
- Test: `tests/test_api_revision.py`（新建）、`tests/test_revision.py`（已存在，扩展）

**实现要点**
1. **列出 §7 的写接口清单**（卡面明令：缺调用即失败）。至少包含：`PUT /files/{name}`、`POST /import-url`、`POST /temp`、`DELETE /files/{name}`、`POST /remarks`、`POST /review/submit`、收藏四端点、`/sources` POST/PATCH/DELETE、`/schedule` PUT、`/ip-mapping` PUT/DELETE、`/admins` POST/DELETE、`/bindings` PUT/DELETE、`/tools` PUT/DELETE、`/profiles` POST/PATCH/DELETE、`PUT /dl/{item}`、`POST /tasks`（进度变化 bump `tasks`，结束 bump `files`/`settings`）。逐个核对 → 在 `tests/test_api_revision.py` 里**每个写接口一条用例**：调一次，断言对应 part 从 N 变 N+1。
2. 不递增的情况也要有守卫测试：读接口、400/403/404/409 失败写、「删了 0 条」、`unchanged` 的更新（决策 16 的既有口径 + 卡 5 的新口径）。
3. `GET /p/{pid}/revision` **不读磁盘**：断言方式是 monkeypatch `storage` 的读函数使其抛异常，然后调用接口仍成功（这是「不读磁盘」的可观测断言，比计时更硬）。进程启动从 1 开始。
4. 性能：P99 < 5 ms → 本地断言单次调用远小于该值（例如 1000 次循环总时长 < 200 ms），并注明这是本地近似，真实 P99 留给 M4-05 的 `bench_api.py`。
5. 前端联调 UC-40：A 打开文件，B 更新该文件，A 在 10 秒内看到黄横幅；未点「重新加载」前内容不变；本地保存后**不出现**横幅（`syncRevision()`）。
6. 有进行中任务时每 1.2 秒轮询 `/tasks?active=1`，结束后停止（前端已做，联调）。

**验收**
- UC-40 在真实后端通过（双浏览器或双 `curl` + 真实服务，记录证据）。**注意：UC-40 同时在第 9 节「待人工确认清单」里**（10 秒内看到对方更新属跨设备体感）→ 自动化只断言「B 写后 A 的下一次 `/revision` 拿到新计数」，10 秒体感仍记「未验证 · 待人工确认」。
- `pytest -q tests/test_api_revision.py tests/test_revision.py` + 全量 + `check_py39.sh` + `data/` 哈希 + 前端 `ALL PASSED`。
- **M3 出口条件**（在 Linear 发 `save_status_update(type="project", project="P-INH-14", health="onTrack")`）：M3 17/17 Done；全量测试通过数记录在案；`CAP_KEYS` 全部进入 `WIRED`（`favorites/remarks/schedule/search/review/dl/settings/import/delete/history/compare/records/tools/revision/temp/tasks` **16 项齐**，含 D4 新增的 `tasks`）；`WIRED_SETTINGS` 六段齐（sources/schedule/people/bindings/tools/profiles）。

**风险**：本卡最容易「补了测试却没补实现」→ 用变异验证：临时注释掉任一 `bump` 调用，确认对应用例会红。

---

## 5. M4 纲要（9 卡，≈ 9.5 人日；M3 收口后再细化为卡级计划）

顺序按卡号即可（M4 内部依赖弱），但 **M4-08 必须最后**，M4-07 在 M4-08 之前（门禁要跑「无 nicegui」的 grep），M4-09 排在 M4-08 之后（卡面明说不阻塞门禁）。

| 序 | 卡 | Linear | 主题 | 出口条件（细化时展开） | 预估 |
|---|---|---|---|---|---|
| 1 | M4-01 | INH-1903 | `server/legacy.py`：旧下载链接、`/record_view` 302、`/static/*` 404 | UC-46 通过；`X-MC-Download-Probe: 1` 返回旧 JSON；中文文件名 Content-Disposition 走 RFC 5987；路径穿越 400/403；路由器在 `/api/v1` **之前**匹配 legacy | 0.5 d |
| 2 | M4-02 | INH-1904 | 安全响应头 + 跨站写保护回归 | 全站头（`nosniff`、`Referrer-Policy`、CSP `default-src 'self'` 等按 §11.3 原样）在 **HTTP 层统一加**；无 `X-Requested-With` 的写请求 403 且不写文件；§16.8 四条全有测试或页面记录；**CSP 下生产页仍可用**（系统字体、内联 SVG sprite 的策略要写明，不得把 `default-src` 设成 `*`） | 1 d |
| 3 | M4-03 | INH-1905 | 访问日志 + 业务日志 | 格式 `%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s`；控制台 + `data/logs/mcchecker.log`（Rotating 10 MB × 5，用 handler 属性断言）；访问日志不含 query 里的 URL 凭据；业务日志含 profile/actor/target/result/duration；`AppError` 是唯一业务异常；未知异常日志有堆栈、响应无堆栈；**日志目录在 data 根下，测试隔离不得写仓库 data**（`conftest` 的前缀替换逻辑要加 `logs`） | 1 d |
| 4 | M4-04 | INH-1906 | 旧布局迁移：`shutil.move` → 复制 + 校验 + 回滚 | 触发条件（根上有 `*.json`/`configs|archive|cache` 且 `profiles/default/` 不存在）；顺序：复制 → 校验文件数与哈希 → 成功才把旧条目移入恢复区；失败删副本继续从旧位置读；幂等（第二次启动不新增恢复目录）；`default` 的历史回退保留到迁移成功；**不删 `data/nicegui_storage_secret.txt`**；不要两套迁移（改 `storage.ensure_profile_layout` 或放 `services/recovery.py`，二选一） | 1.5 d |
| 5 | M4-05 | INH-1907 | `scripts/bench_api.py` | 五条预算线（§12 表格为准）：5 万节点缓存命中 ≤ 300 ms、30 万节点冷 ≤ 3 s、30×3 万搜索 ≤ **500 ms**、2×5 万对比 ≤ 800 ms、修订号轮询不读磁盘；样本运行时生成到临时目录（仓库体积不增）；`http.client` 打真实服务、预热一次再计命中；超预算退出码非零；**在此卡解决 D3**（缓存条目数 24 → 按机型文件数动态，或按 §7.6 缓存小写数组）并回写 SPEC；PR 贴一次真实输出 + 机器规格 | 1 d |
| 6 | M4-06 | INH-1908 | `scripts/make_release.py` + `start.bat`/`start.sh` | 只用 `zipfile`/`pathlib`；`dist/MCChecker-2.0.0.zip` 名单符合 §14.3（含 `run.py`/`main.py`/`app/`/`docs/manual.md`/`README.md`/`start.*`；排除 mock、`template.html`、`app/web/tests/`、`__pycache__`、`*.pyc`、`data/`、`.nicegui/`）；`requirements.txt` 只剩注释；`requirements-dev.txt` 固定 `pytest==7.4.4`；运行时代码不 import pytest；断网证据（无法真断网时用浏览器网络日志 + HTML/JS 外链扫描，并写明未做物理断网） | 1 d |
| 7 | M4-07 | INH-1909 | 移除 NiceGUI + 文档同步 | `rg -n "nicegui\|apscheduler" app run.py main.py` 无匹配；删 `app/pages/`、`app/static/`、NiceGUI 专用 `app/utils/auth.py`（**先 rg 确认无引用**）、旧 `app/core/scheduler.py`；`git rm --cached .nicegui/` + 补 `.gitignore`（不删部署机文件）；`compileall` 成功；全量测试通过且 `data/` 哈希不变；更新 `README.md`（技术栈、快速开始、**端口 50002**、零运行时依赖）、`requirements.md`、`docs/manual.md`、`docs/architecture.md`、`docs/know-how.md`（3.9、代理 IP、恢复区）；旧架构书文首加「已被 MCChecker_REDESIGN_SPEC 取代」但**不删历史文档**；**同时删除 `MC.caps`/`WIRED`/`WIRED_SETTINGS`/`MC.unwired*` 机制与所有条件分支**（决策 14 的既定收尾）与 D1 的 NoneType 只读文案核对 | 1 d |
| 8 | M4-08 | INH-1910 | §16 全量验收门禁（**不写新功能、不在本卡修缺陷**） | §16.1 四层命令都有退出码；UC-01–UC-46 **每一行**有真实后端的步骤与结果记录（缺一行不能关闭）；§4.2 每个写接口四角色结果 + 403 时文件 sha 不变；M4-05 的一次真实性能输出；§16.5 数据兼容（脱敏旧 `data/` 副本可被新版读出，新版写入后 v1.0.1 + Python 3.12 仍能读同一副本；**没有真实脱敏数据就标未验证，不得用空目录代替「兼容通过」**）；3.9.11 下 `pytest` + `compileall`；发布包首访无外部请求；§16.8 安全；把第 9 节「待人工确认清单」（UC-07/19/40/45）的人工确认结果回填到本卡与 `DELIVERY_PLAN` | 2 d |
| 9 | M4-09 | INH-1911 | 恢复区 `--dry-run`（可选，用户已确认纳入） | `python run.py --restore <目录> --dry-run` 打印将执行的复制步骤（读 `manifest.json` 的 `moved` 与 `removed_refs`，输出 from/to），退出码 0，**不创建不修改任何权威文件**（`data/` 哈希不变）；目录不存在 → 非零退出并说明原因；不提供无 `--dry-run` 的自动写回；`docs/manual.md` 补 §14.6 五步手工恢复（若 M4-07 已写手册，本卡只补这一节） | 0.5 d |

**M4 收尾后的最后一步**：`feat/mc-v2-delivery` → `v1.0.1` 一条汇总 PR（§2 分支拓扑），发布说明必列 **C09、C12、C13、C20、C25、C26、C27**（`DELIVERY_PLAN` 第 10 节）。

---

## 6. 每卡统一的验证命令（照抄）

```bash
cd /Users/wangdongxin/projects/mcchecker

# 开工
git fetch && git checkout feat/mc-v2-delivery && git pull --ff-only
git checkout -b mc-v2/<卡号小写>-<slug>

# H3：data/ 哈希前
B=$(find data -type f -exec shasum -a 256 {} + 2>/dev/null | sort | shasum -a 256)

# 全量 + 定向
./.venv39/bin/python -m pytest -q
./.venv39/bin/python -m pytest -q tests/test_<本卡>.py

# 3.9 守护
PYTHON=./.venv39/bin/python scripts/check_py39.sh          # exit 0

# H3：data/ 哈希后
A=$(find data -type f -exec shasum -a 256 {} + 2>/dev/null | sort | shasum -a 256)
[ "$B" = "$A" ] && echo "✅ data 未变 ($A)" || echo "❌ data 被改动"

# 涉及前端时
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu \
  --no-sandbox --virtual-time-budget=8000 --dump-dom \
  "file://$PWD/app/web/tests/index.html" | grep -o "<title>[^<]*</title>"   # 期望 ALL PASSED (n)

# 涉及 HTTP 时：真实服务 + curl 走卡内验收条目
D=$(mktemp -d); ./.venv39/bin/python run.py --port 50110 --data-dir "$D" &   # 随机端口 + 临时 data 根
curl -sS -H 'X-Requested-With: MCChecker' http://127.0.0.1:50110/api/v1/p/default/...

# 提交与 PR
git add <精确文件>            # 不用 git add -A
git commit -m "feat(<域>): <一句话>（<卡号> / INH-xxxx）"
for i in $(seq 1 10); do git push -u origin mc-v2/<...> && break; sleep 4; done
gh pr create --base feat/mc-v2-delivery --title "..." --body "..."
gh pr merge <n> --merge
```

**Linear 同步**：开工 `save_issue(state="In Progress")`；完成 `save_issue(state="Done")` + `save_comment`（模板见 `DELIVERY_PLAN` 第 4 节）。Linear MCP 不稳，同调用重试 3–8 次；创建 PR 用 `gh` CLI 而非 GitHub MCP。

---

## 7. 逐卡 JIT 细化模板（开工前填这一份，不要提前批量写）

每张卡开工前，用 `superpowers:writing-plans` 单独产出一份 `docs/plans/<日期>-<卡号>-<slug>.md`，内容：

1. **读卡证据**：`get_issue` 的原文四段（范围/功能需求/开发方案/验收方案）+ 对应 SPEC 章节原文摘录 + `FEATURE_CHANGES` 的 C 编号。
2. **文件清单**：Create/Modify/Test 精确路径（Modify 带行号区间）。
3. **接口契约**：Consumes（前序卡的精确签名）/ Produces（后续卡依赖的精确签名）。
4. **TDD 步骤**：每条行为一个循环 —— 写失败测试（贴真实测试代码）→ 跑红（贴命令与期望错误）→ 最小实现（贴代码）→ 跑绿 → commit。
5. **变异验证**：至少一条「临时构造反向实现确认测试会红」的具体做法。
6. **验收映射**：卡内每条验收 → 命令 + 实际输出。
7. **高风险点名**：密钥/鉴权/持久化数据/迁移/破坏性操作/外部集成/许可，本卡涉及哪些、怎么保住既有防护。

---

## 8. Self-Review（本计划对 SPEC 与 Linear 的覆盖检查）

**覆盖**：§6.7 → 卡 1、2；§7.8 → 卡 2、3；§7.9 sources/schedule → 卡 5、8；§7.9 ip-mapping/admins/bindings/tools → 卡 13、14、15；§7.2 profiles → 卡 16；§7.10 + §10.1 → 卡 6；§10.2 → 卡 4、5；§10.3 → 卡 9；§10.4 → 卡 8；§7.11 + T-20 → 卡 10、11、12；§7.3 + §8.5 + T-22 → 卡 17；§7.12 + T-23 → M4-01；§11.3–11.4 + T-24 → M4-02；§13 + T-25 → M4-03；§5.5 + T-26 → M4-04；§12 + §16.4 + T-27 → M4-05；§14.3–14.4 + T-28 → M4-06；§14.5 + T-30 → M4-07；§16 → M4-08；§14.6 + T-29 → M4-09。T-16–T-30 全部有归属，无遗漏。

**UC 覆盖**：UC-20~24 → 卡 2、3；UC-30 → 卡 9；UC-31、32 → 卡 5、7；UC-33 → 卡 8；UC-34、44 → 卡 13；UC-35 → 卡 14；UC-36 → 卡 15；UC-37~39 → 卡 11、12；UC-40 → 卡 17（体感部分留人工确认）；UC-41 → 卡 16；UC-46 → M4-01。UC-07/19/45 与 UC-42/43 属既有能力或纯体感，由 M4-08 门禁统一收证据。

**类型一致性**：`apply_edits(src, fmt, edits) -> bytes`（卡 1 定义，卡 2 消费）；`Edit = (loc, new_value, kind)`；`update_source(pid, name, actor) -> Dict`（卡 5 定义，卡 6/8 消费）；`TaskRunner.submit(ctx, ttype, target, fn) -> Task`（卡 6 定义，卡 8/9 消费）；`register_handler(ttype, fn)`（卡 6 定义，卡 9 消费）；`find_roots/forward/reverse/multi_check/validate`（卡 10 定义，卡 11 消费）；`current_rev/get_state/compute/extract/save_item`（卡 11 定义，卡 12 消费）；`MC.settingsSegmentWired(seg)`（卡 7 定义，卡 8/13/14/15/16 消费）；`move_profile_to_recovery(model_id, actor) -> str`（卡 16 定义，M4-04 复用同一套顺序）。签名在各卡间已核对一致。

**已知补白（SPEC 未明说，开工时以前端既有实现为准并在 PR 记录）**：① `PUT /dl/{item}` 缺 `If-Match` 头的状态码（卡 11）；② `DELETE /profiles/default` 的错误码（卡 16）；③ `unchanged` 的更新是否 bump `files`（卡 5，本计划定为**不 bump**）。

**计划期内新发现的两个前端能力机制缺口（均已定决策、已落到具体卡）**：D2 设置页缺分段级能力开关（2.2 节 → 卡 7 / M3-06 建基础设施，卡 8/13/14/15/16 各自追加一段）；D4 `tools` 键被借用为任务接口代理、且缺独立 `tasks` 键（2.4 节 → 卡 6 / M3-07 新增键并改四处门控）。两者都会让 `app/web/tests/tests.js` 的既有断言变红，必须同 PR 补齐，不能留到 M4-08。

---

## 9. 待人工确认清单（不得写成「通过」，M4-08 收口）

沿用 `DELIVERY_PLAN` 第 9 节：**UC-07**（数十万节点浏览不卡顿）、**UC-19**（收藏/复制体感）、**UC-40**（双浏览器 10 秒内看到对方更新 —— 卡 17 只自动化「计数递增」部分）、**UC-45**（手机/窄屏响应式）。M3 期间若新增体感依赖项，追加到该清单。

---

## 10. 执行期裁定（卡 1 执行中核代码得出，2026-10-17）

以下是控制方逐张卡读真实代码得出的结论。**后续派发该卡时必须把对应条目随 brief 下发**，不要重新发现一遍。

### 基线（当场实测，禁止引用本文数字）

**2026-10-18 更新，取代此前写在本文里的所有旧基线**：全量测试数每张卡都在涨（506 → … → **905**），`ALL PASSED` 也在涨（26 → … → 29）。**派发任何一张卡前，自己跑一遍拿到当下的数**，不要把本文任何一个数字当事实源（R43）。

H3 的证据形式已变更：**从 INH-1914 起，「测试没损伤权威数据」的唯一可信证据是 `tests/data_guard.py` 会话级守卫的红/绿**，它每一轮都当场播报「执法中(N 个文件)」或「未执法」。守卫绿 = 首末快照一致，**不等于**期间无人写入（两个盲区见 R40）。
> 历史说明：本节原先写着「`data/` 内容哈希 `478d9c26ea8dbb8f…`（实测）」，而那个号后来被证明**从未被真正测过**、是抄文档来的，当前树也复现不出来（本机实测 `5591aacb4a22f34c`，且本机 `data/` 全是测试残留）。这条教训的全文见 `.superpowers/sdd/MCChecker_M3_M4_PLAN/h3-baseline-audit.md`。

`data/` 被 `.gitignore` 忽略且**完全未被跟踪** ⇒ 任何数据完整性检查**只能在真实工作区做**，另开 worktree/clone 里没有 `data/`（守卫在这种情况下会明确播报「未执法」，不会假装绿）。仓库内**禁止**嵌套 clone（会被 pytest 从根目录连带收集，污染计数）。

### 卡 2（M3-02）

- **R1** 分组必须自己算 `group_key = locator or node_path or node_key`。**不得复用 `storage.list_review_items`** —— 它按 `(source_file, node_key or node_path)` 分组（storage.py:1441），对 M2-03 写的「`node_key=""` 且带 locator」新建议会退化成按旧 ID 分组，同名兄弟被错误并组，可能连带批准错建议。可复用 `storage.load_edit_remarks()` 取原始条目。
- **R2** §6.7 第 8 步复用 `storage.apply_review_results`（storage.py:1462），两个 ID 列表由 service 算。**嵌套锁安全**：`locks.profile_lock` 返回 **RLock**（locks.py:26/33），外层 `profile_write(pid)` 与内部 `_profile_write()` 同 pid 可重入，不会自锁。
- **R3** 幂等表落独立文件 `review_idempotency.json`；路径用 `_get_profile_json_file(...)` 在**调用期**求值，不得新增模块级常量；**不加入** `COPY_PROFILE_FILES`。属新增持久化文件，PR 与 Linear 须按高风险点名（v1.0.1 不读它，不破坏 §16.5 向后兼容）。
- **R6** `current_value` 用 JSON `null` 表示节点无值，不能用空串（`review.js:137` 判 `=== null`）。
- **R7** legacy 建议「定位不唯一」必须用 **`resolvable=false`** 表达 —— `review.js:103` 只看 `n.stale || !n.resolvable`。只标 `legacy=true` 会让前端给出可点的「通过」按钮而点下必 409。`legacy` 仅用于打「旧版」徽标。
- **R8** `original_value` 与 `current_value` 每个节点都要给（:119 冲突横幅、:160 确认表直接用）。

### 卡 4（M3-04）

现状：`core/downloader.py` 仅 44 行，**scheme／大小上限／重定向策略全无**，超时是写死的模块常量。调用方 4 处，v2 活路径只有 `services/upload.py:79`；`app/pages/home.py`、`app/core/scheduler.py` 是 M4-07 才删的旧路径。
- **R4** `TOO_LARGE` 必须**透传**，不得塌缩成 `None`。证据：`tests/test_api_import.py:153-165` 断言超限在服务边界是 413，`:292-301` 断言下载失败是 502。规则：下载类失败仍返回 `None`（保 502 与 UC-29 文案），超限让 `AppError(TOO_LARGE)` 向上抛（保 413）。
- `upload.py:76` 的 http/https 前缀检查**要留着**（它给 400 + `field="url"`，fetch 给 502，两层是纵深防御）。
- 现有 `logger.info("下载文件: %s", url)` 会把含 query/userinfo 的完整 URL 写进日志，违反 §11.5，本卡顺带修并加断言。

### 卡 5（M3-05）

- **R5** `unchanged` 判定用现成的 `storage.save_config_file_detailed` → `NamedTuple(path, archived, unchanged)`（storage.py:770-780），不必自己再比一次 sha256。据此：**仅当 `unchanged is False` 才 `revision.bump(pid, "files")`**，配守卫测试。
- `update_status.json` **没有任何现成读写函数**（storage.py:1670 只有一句注释），本卡新建 `get_update_status_file/load/save_update_status`，同样属新增持久化文件。

### 卡 6 / 卡 8（M3-07 / M3-08）

现状：`run.py:72-79` 信号处理器用 `threading.Thread(target=server.shutdown, daemon=True)`；`http.py:422` `daemon_threads = True`。
- **R9** 停止顺序必须 **先 `runner.shutdown(...)` 再 `server.shutdown()`**，`_handle_signal` 与正常退出路径两处都要走同一条。
  **（卡 6 实测更正，2026-10-18）** 原文里「`ThreadPoolExecutor` 保持非 daemon，所以进程退出前一定把在途任务跑完」这条前提**是错的**：Python 3.9 `threading.py:828` 是 `self._daemonic = current_thread().daemon`，**工作线程继承提交方的 daemon 位**，而 `MCServer.daemon_threads = True`，因此从 HTTP 请求发起的任务跑在 daemon 线程里。「有没有人还在写」的判据只能是**线程名**（`tasks.THREAD_NAME_PREFIX`，见 `worker_threads()`）。真正兜住退出的是 `concurrent.futures.thread` 用 `threading._register_atexit` 注册的 `_python_exit`，它不分 daemon 地 join 工作线程——但那发生在**解释器退出阶段**，比 `run.py` 往下走完要晚，所以「删锁」必须显式等到线程散尽（卡 6 的 `stop_and_release`）。派发卡 8 时必须把这条随 brief 下发，否则它会照一条假标准验收。
- **R10** `shutdown()` 必须幂等（信号连按两次不得抛、不得重复 join）。
- **R11** 调度线程是 daemon，`stop()` 靠 Event 唤醒自循环退出，「等任务跑完」由 `runner.shutdown` 负责，两者不要混。

### 卡 10（M3-10）

- **R15** `or` 混淆点已定位到 `dltool.py:295` `x_samples = max(int(x_samples or 0), 50)`。`:169` 的 `if not roots or abs(...)` 是根去重，属合法写法，**不要顺手改它**。
- **R16** `tests/test_dltool_multi.py` **只有 2 个 test 函数**，回归基线极薄。本卡质量取决于新增用例：切触根 `(x-3)²`、越界标注、`input_index`、常数函数状态、功率转换系数 0 拒绝 —— **各自独立命名**，不要挤进一个大测试。

### 卡 13 / 卡 16

- **R13** 「删除管理员后下一次审阅 403」是**既有行为**：`auth.py:116` 每次解析身份都实时 `storage.is_admin_user(name)`，无 Actor 缓存。只写断言，**不要引入身份缓存或失效钩子**。
- **R12** 卡 16 **不要新写恢复原语**：`recovery.py` docstring 第 10 行明写机型删除复用 `create_entry`/`move_in`/`write_manifest`，且 `move_in` 已支持「文件或整个目录」，`create_entry(op, ...)` 的 `op` 已参数化（加 `OP_DELETE_PROFILE`）。`move_profile_to_recovery` 必须是这三者的**组合函数**，重抄 copy/verify/rollback 会被判为「逐字复制逻辑块」。删除顺序：先 `device_models.remove_model(id, delete_data=False)` 摘注册表，再安置数据目录，失败回滚注册表。
- **R18** `DELETE /profiles/default` → **400 `INVALID_INPUT` + `detail.field="id"`**，且**必须把底层裸 `ValueError` 转成 `AppError`** —— `storage.delete_profile_data` 与 `device_models.remove_model` 抛的是 `ValueError`，透传会让 HTTP 层按未知异常返回 **500**。前端 `settings.js:434` 对 default 根本不渲染删除按钮，所以这条只有非 UI 客户端能触发。

### 卡 11 / 卡 17

- **R17** `PUT /dl/{item}` 缺 `If-Match` → **409 `REVISION_CONFLICT`**（不用 400/428）。`dltool.js:84` 永远带该头，`:94` 只处理 `REVISION_CONFLICT`；响应必须回新 `rev`（`:86` 要 `S.rev = res.rev`）。
- **R20** `POST /compare` 是 POST 但纯计算、不写权威数据 ⇒ **不递增**，必须列入「明确不递增」清单并配「5 个 part 全不变」的守卫测试，否则下一个审计者会误以为漏接而去加 bump。
- **R21** `POST /temp` 与 `import-url` 的非持久分支**不递增**：临时区只对上传者可见，递增 `files` 会让所有客户端在 10 秒轮询后弹出虚假的「文件已更新」黄横幅。`import-url` 只在 `persist=true` 走 `save_bytes` 时命中既有 `files` 递增。
- 卡 17 的审计基数 = **31 个写端点**（现存 10 + M3 新增 21）。

### 全卡通用

- **R19** `app/web/assets/js/mock/mock-api.js`（1261 行）**已实现 M3 全部端点**（`/review` :842、`/review/submit` :870、`/profiles` :489/:511/:522、任务 :391、`schedule.last_run` :401/:505、`/records` :669、绑定 :603、admins/people、dl）。每张后端卡开工前先读对应那条 `route(...)` —— 它是前端据以构建并被测试页验过的参考实现，字段名以它对齐。**但 mock 不是权威，SPEC 才是**：已发现 `mock-api.js:527` 的 `DELETE /profiles` 是硬删，与 §5.6 冲突，卡 16 要改 mock。**且模板模式下测试页一直是绿的，`ALL PASSED` 不能作为后端接对的证据**，真实验收只认 `tests/test_api_*.py` + 真实 `run.py` 走 `curl`。
- **R14** D2 的分段门控只需插一处：`settings.js:62-67` 的 `renderSection()` 是唯一调度器，第 66 行的 `fn(sec)` 就是发请求处 —— 未接入时在其之前直接渲染只读横幅并 `return`，压根不调 `fn`。
- **R22**（计划自身缺陷，控制方责任）：卡 1 的 D1 动作第 3 步原写「在 `tests/test_review_apply.py` 断言 NoneType 提 `"null"` 返回『与当前值相同』」**是错位的** —— 那是 §6.7 第 1 步、属 `services/review.py::submit`；`apply_edits` 不做此检查。正确归属 `tests/test_api_remarks.py`。若实现者为此把 service 判断塞进 core，是越层而非缺陷，由控制方改判。
- **D4** `tasks` 能力键（`CAP_KEYS` 增至 16 项）落在卡 6；改完 `fileview.js` 后 `MC.caps.tools` 不应再出现在 `fileview.js` 里。
- **R37**（评审会读到「过期草稿」，控制方必须复核每条断言）：卡 6 的第一次任务评审被判**作废**。它报的 1 Critical + 2 Important 建立在一整套本卡最终提交里**不存在**的机制上（`_decrement`、`_ensure_capacity_locked`、`update_state`、runner 里 `storage.save_settings` 调用、`run.py` 的 `from app.core import scheduler`）。根因不是编造，而是**它按 prompt 去主工作区读 `app/services/tasks.py`，而那份文件是 R35/R36 期间并行事故留下的过期草稿**（同一文件的最终版重写过的标志：`update_state()` → 拆成 `mark_running()`/`mark_finished()`）。主工作区当时 checkout 在 `6382efa`，卡 6 的文件在磁盘上根本不属于任何提交 —— 磁盘内容、HEAD、被评审的 SHA 三者不同源。真实代码在 `tasks.py:386` 用 `_active.pop(...)` 释放互斥键（`:286` 是 submit 回滚分支）、`:207` 输出 `finished_at`、runner 的 import 只有 `revision, updates`；`git log --all -S"_decrement"` 全历史零命中，而草稿还多一段最终版没有的 `_dedupe_names`。
  **对策（写死）**：① **派评审前先建 detached worktree**：`git worktree add --detach ../mcc-review-<卡> <HEAD_SHA>`，prompt 里给这个绝对路径，明确禁止读主工作区；② 评审结论里凡是「某机制缺失」的断言，控制方先 `grep` 该符号 + 追状态写入点复核，复核不过就作废重派，**不要先派修复**；③ 重派 prompt 里点名上一轮为何作废，并要求每条断言逐字引用 file:line、断言机制缺失前先说明搜到了什么；④ 若评审提到主工作区磁盘状态，先问自己「磁盘 == 被评审的 SHA 吗」。
  **代价**：照单全收会为了「修好」一个不存在的 bug 重写正确实现，比跳过评审闸门（R34）更隐蔽 —— 跳过评审至少不破坏代码。
- **R38** 外部写入（Linear）交给**独立 CLI 会话**跑是有效的：`qoder-cli` 的新会话有自己的 MCP 注册表，能调到本会话启动时快照里没有的 Linear 连接器。用法：`--session-id <uuid> --system-prompt ... -- "任务"`，再 `--resume <uuid> "继续"` 追问；正文让它自己从 `docs/MCChecker_BLOCKERS.md` 读，控制方不在 prompt 里重编事实。约束要写进 prompt：**只准编辑那一个文档、禁止任何 git 写操作**，否则会和同工作区里跑的评审/实现互相踩。
- **R39** `revision` 分量口径（卡 5/6 定稿）：`update_all` 自己不递增 `settings`（`updates.py` 里只在 create/update/delete source 递增），由 runner 在任务结束时按 `FINISH_PARTS = {"update_all": ("settings",)}` 补一次；`files` **不补**，因为 `update_source` 只在字节真变了才递增，runner 无从判断，补了就是虚假通知。M3-08 的调度器复用 runner 时必须沿用同一张表，不要在 handler 里另写一次 bump。
- **R23**（H4/H5 的具体形态）：实现代理曾在 `docs/MCChecker_DELIVERY_PLAN.md` 第 11 节写下「M3-01 已完成」，而当时**未提交、未评审、未合并**。交付文档是后续 agent 的接力入口，写早了等于伪造进度。口径：**状态词只能由控制方在真合并之后写实**，代理一律写「已实现待评审/待合并」。
- **R40** **H3 的证据形式已变更（INH-1914 起生效）**：从此「测试没损伤权威数据」的唯一可信证据是 `tests/data_guard.py` 会话级守卫的**红/绿**，不是任何人报出的哈希号。历史基线 `478d9c26ea8dbb8f` **作废**——它只存在于文档里、从未被真正测过，当前树实测是 `5591aacb4a22f34c`（旧号在任何路径拼法下都复现不出来）。两个已知盲区必须知道：**用例内写了又删** → 守卫看（最终态）= 绿；**会话结束之后才落盘的写入** → 绿，且 `mctask` 之外的线程（`ui.timer`、`asyncio`、卡 8 的调度线程）不在守卫可见范围内。所以「守卫绿」**不等于**「没有测试写过 `data/`」，只等于「首末快照一致」。
- **R41** **控制方并行派出两个实现代理，是本项目最贵的流程错误（犯过两次，卡 6 一次、卡 7 一次）**。两个代理同时改同一个工作区时，一个的中间态会污染另一个的验证结果，甚至产出一个双方都不认识、但测试可能全绿的产物。控制方派卡 7 时又派了一个，与卡 6 的修复代理同时在跑，发现后立即 `TaskStop` 停掉第二个并确认工作区无残留。**How to apply:** 派发前必须确认上一个实现代理已收尾（收到完成通知）；需要并行时必须各用**独立 worktree**且文件面零重叠。
- **R42** **评审用工作树必须与被评审 SHA 同源，派发词里要评审方回显 `git log --oneline -1`**（R37 的加强版）。同一轮里我给评审方的路径是对的、但那个工作树还 checkout 在上一轮的 base，评审方读到的全是修复前的代码——它把「没修复」当成结论，而这次我在派出去之后才发现，只能停掉重派。路径正确但提交号没跟上，和路径指错一样致命。
- **R43** `docs/MCChecker_DELIVERY_PLAN.md` 与 `docs/MCChecker_M3_M4_PLAN.md` 里凡是写死「M3 合入 N 张 / 基线 N passed」的句子都是**会过期的快照**，不是事实源。改动它们时必须**当场实测**后再写，否则就会重演 R40 那类「抄文档报数」的失效。
- **R44** Linear 的卡号（`P-INH-*`）与正文里的编号会漂移：本项目实测过公告正文里写的卡号与实际返回的 issue id 不一致（1914/1915/1912/1913 那批）。凡外部系统的写入，**以接口返回的对象 id 为准**，不要相信自己起草时写的号。

## 11. 执行状态快照（2026-10-18 07:10，恢复时从这里接）

**里程碑**：**57 卡 / 34 Done / 23 未完成**。已合入集成分支：M3-01…M3-05、M3-07（卡 6，PR #43）、H3 守卫（INH-1914，PR #44），加上文档 chore。**集成分支 tip 一律用 `git log --oneline -1 origin/feat/mc-v2-delivery` 现查**，别信本文任何写死的 SHA（R43）。

**验证基线（控制方在合并 INH-1914 后亲手实测，两种环境各跑一遍全量）**：**905 passed**（约 60 s，输出干净）、`check_py39.sh` exit 0、前端 `ALL PASSED (29)`。H3 证据从此不看哈希号，看守卫播报：本机 `data/` 存在时打印「H3 守卫执法中：真实 data/ 基线快照 3 个文件…」；在无 `data/` 的检出里打印「H3 守卫未执法…这一轮的『绿』是没有可比对对象的结果，不是『数据完好』的证据」——两种绿是可观察不同的，这条是 R40 的落点。

**卡 7 = M3-06 / INH-1891（更新源设置页 + D2 分段门控）是下一张**，尚未开工。brief = `.superpowers/sdd/MCChecker_M3_M4_PLAN/task-7-brief.md`，**必须同时下发** `.superpowers/sdd/MCChecker_M3_M4_PLAN/task-7-handdowns.md`（卡 6 移交的后端口径、卡 3 遗留的 `MC.api.get` 桩污染必修项、D2 唯一插入点、`progress.total` 与 409 文案两项改派）。必带裁定：R14、R19、R39、R40。

**之后**：M3-08 → 09 → 10 → 11 → 12 → 13 → 14 → 15 → 16 → 17（17 恒为最后）。M4 九张（INH-1903…1911）照 §9 纲要。另有三张执行中新立的卡：INH-1912（N1 中文改名）、INH-1913（N2 访客临时导入的回环拦截未开）、INH-1914 已完成。

**流程红线（每张卡都适用，全部是踩过才写的）**：R34 不得跳过合并前评审；R35/R41 实现代理一律串行，一个工作区同一时刻只属于一张卡；R36 切分支只能在代理停止之后；R37/R42 评审方必须用与被评审 SHA 同源的独立工作树，并回显 `git log --oneline -1`；R40 H3 只认守卫红/绿、禁止抄哈希号；R23 未合并不得在文档里自称完成。

**本机的两个后台代理**（不属于代码，恢复时先确认状态）：`同步 Linear 进度` 会话负责外部写入（禁止它动 git 与被追踪文件，结果落 `.superpowers/linear-sync-log.md`）；`.superpowers/` 已写进 `.git/info/exclude`（本地生效，未改仓库配置），不会污染 `git status`。

## 12. Linear 同步状态

**已结清**（执行明细与返回 id 见 `docs/MCChecker_BLOCKERS.md` 的「B1 · 已执行」与 `.superpowers/linear-sync-log.md`）：M3-01…M3-05 与 INH-1914 的 Done + 证据注释、N1/N2 建卡（INH-1912 / INH-1913）、H3 基线作废的**公开更正公告**（旧评论不删，就地补正，Linear 留有编辑痕迹）。更早发出的：`INH-1886` D1 裁定 `0337c38d`、`INH-1887` 的 R19 评论 `d87d91dd`、项目状态更新 `8b3551ef`。

**仍需跟进**：
1. `INH-1892`（M3-07，PR #43）合并后的 Done + 证据注释。
2. 本项目对**共享集成分支**有过一次直推（`78d0fbb`，纯文档），未改写历史；此后一律走 PR。
3. 每合并一张卡都当场同步该卡状态与证据，不要攒——看板比实际乐观是真实的交付风险。
4. 外部系统的编号**以接口返回的 id 为准**（R44）：起草时写的卡号与实际创建的曾出现过不一致。
