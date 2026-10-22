# MCChecker v2 · M5–M8 开发计划（44 卡）

> **本文定位**：M5–M8 的**卡级执行计划**。它回答「按什么顺序做、每张卡改哪些文件、怎么证明做完了」，不重复方案论证。
>
> | 事实源 | 用途 | 权威性 |
> |---|---|---|
> | Linear 项目 **MC-checker-v2**（`P-INH-14`）M5–M8 共 44 卡 | 每卡的范围/验收/特别约束 | **最高**；本文与本文件冲突时以 Linear 为准 |
> | 本文 | 卡序、依赖、落点文件、测试策略、工时与风险 | 执行协议 |
> | `docs/MCChecker_REDESIGN_SPEC.md`、`docs/MCChecker_FEATURE_CHANGES.md` | M0–M4 已交付行为与 C01–C32 变更 | 旧功能回归的唯一判据 |
> | `docs/MCChecker_DELIVERY_PLAN.md` §3/§5 | 单卡 SOP、证据规范 | 沿用，不另立 |
> | `docs/MCChecker_M3_M4_PLAN.md` | 上一轮卡级计划的格式参照 | 格式样板 |
>
> **前置声明**：卡面引用的《MCChecker 新 UI 与配置生命周期管理实现方案》CFG-00–CFG-17 **不在本仓库**，本机也未检出该文件（已检索）。因此本文第 2 章按卡面文字反推了 CFG 编号与卡片的映射关系；M5-01 的第一项交付即为补齐这份领域规范，届时须与原始方案逐条核对，**不得以本文反推版替代原方案**。

---

## 0. 一句话现状（2026-10-22 实测）

- **代码基线**：`main@3a60746`（本文实测 HEAD）。44 张卡**全部 Backlog**，M5 尚未开工。
- **测试基线**：`.venv39/bin/python -m pytest -q` → **1885 passed, 3 failed**（152.58 s）。3 条失败全部来自 `tests/test_release.py`，根因单一：`app/web/assets/js/views/tools.js:117` 把输入框占位符写成 `http://…`，被 `scripts/make_release.py` 的离线外链扫描判为"未放行的外部主机引用"。
- **M4 收口状态**：M4-01～07、09 Done；**M4-08（INH-1910）仍 In Progress**，缺口是真实浏览器逐 UC 证据、脱敏旧数据副本、物理断网三类人工验收（`docs/MCChecker_LEFTOVERS.md` L1）。
- **结论**：M5 的**代码工作可以立即开始**（卡面明确允许隔离样本与代码实现），但**受管配置的生产写入不得开工**，直到 M8-02 的最小备份/恢复子集落地并通过演练。

---

## 1. 基线与偏差核对

### 1.1 卡面基线与实际主干的偏差

44 张卡统一引用审查基线 `f83863db`，并注明"实现时需重新 rebase 验证最新 main"。实测 `main` 已推进到 `3a60746`，其间 5 个提交改动了本计划高度依赖的三个面：

| 提交 | 影响 | 对本计划的后果 |
|---|---|---|
| `8a1d8ba` feat: 管理员按 IP 授权，**并取消部署者身份** | `services/auth.py::resolve_actor` 中 `is_deployer` 恒为 `False` | **最高优先级偏差**，见 1.3 |
| `3740453` docs: 页面说明改书面表述 | `tools.js:117` 占位符改为 `http://…` | 引入 3 条发布测试失败，见 1.4 |
| `3a60746` / `6a76aad` / `8b262d3` / `baf6a10` | DL 页取景、导入支持多文件、XML 属性列出 | M5-09/M5-10 的 UI 接入不得回退这些行为 |

### 1.2 可复用的代码资产（已核实）

| 能力 | 位置 | 复用方式 |
|---|---|---|
| 原子写 + 路径安全 | `app/core/fsutil.py`（`atomic_write_bytes/json`、`read_json`、`resolve_within`） | 配置域全部写入复用，**不新写** |
| 并发原语 | `app/core/locks.py`（`profile_lock`、`registry_lock`、`people_lock`、`WriteGate`） | 新增 `management_lock`，锁序 `WriteGate → profile_lock → management_lock` |
| 统一错误 | `app/core/errors.py`（`AppError`、`HTTP_STATUS`） | 扩展错误码，不新造异常体系 |
| 路由注册 | `app/server/router.py`（`Router.add`、`build_router`） | 新增 `api_config_*.py` 按同一习惯注册 |
| 权限矩阵 | `app/services/authz.py`（`ACTIONS`/`authorize`/`require`） | 扩展为新动作，保持单点定义 |
| 修订通知 | `app/services/revision.py`（5 段内存计数） | 见决策 D3 |
| XML 解析/写回 | `app/core/parser.py`（`to_compact`、NodeRef）、`app/core/reviewing.py::apply_edits` | M6-13 生成 XML 的骨架 |
| 归档版本 | `app/core/storage.py`（`list_archived_versions`、`load_archived_file`、`get_archived_path`） | M6-02 的 `FileSnapshot` 精确解析来源 |
| 更新源与定时 | `app/services/updates.py`、`scheduler.py`、`tasks.py` | M6-07 采集接入点 |
| 前端骨架 | `app/web/assets/js/views/shell.js`（`App.prototype.can`）、`views/fileview.js`、`views/review.js`、`views/settings.js` | M5-09/M5-10/M6-12/M6-05 的接入面 |

### 1.3 已发现的真实缺口（卡面未覆盖，必须在本轮内解决）

**缺口 A — "部署者"角色在当前主干已不存在。**

- `resolve_actor()` 硬编码 `is_deployer=False`；`Actor.role_label` 只区分管理员/成员/访客。
- `authz.ACTIONS` 定义了 `DEPLOYER` 常量，但**没有任何 action 使用它**；`allowed()` 对 `DEPLOYER` 与 `ADMIN` 都返回 `actor.is_admin`。
- 前端 `shell.js:106`：`can("deployer")` 与 `can("admin")` 实现完全相同，且**未知能力返回 `true`**。

后果：M5-07 的验收条目"部署者不因访问配置页自动获得管理员批准权"与"`app.can()` 未知能力不能默认返回 `true`"**在当前代码上必然不成立**；M5-07 不是"扩展权限"，而是"先补回角色语义"。详见决策 **D4**。

**缺口 B — 单一持久化出口的边界。

`docs/MCChecker_M3_M4_PLAN.md` 的红线是"持久化一律走 `storage`"，而 M5-03 要求新建 `app/core/config_repository.py`。`storage.py` 已 2166 行，再塞入 5 类不可变对象、blob 寻址与 state 指针不可行。详见决策 **D1**。

**缺口 C — 修订通知是 per-profile 的 5 段计数。

`/api/v1/p/{pid}/revision` 与 `PARTS = (files, favorites, remarks, settings, tasks)` 的契约被至少 29 条写路由与大量测试断言依赖。配置域是**全局**的（shared `/api/v1/config/*`），需要一个全局通知通道。详见决策 **D3**。

### 1.4 必须先修的回归（FIX-0，0.25 d）

`tools.js:117` 的 `placeholder: "http://…"` 触发了发布包的离线外链扫描。这不是"测试太严"，因为 §16.7 的判据是"断网机器首访浏览器不得请求本服务以外地址"，而扫描器按字面量匹配主机名，占位符同样会被匹配。

处置方案（二选一，推荐前者）：
1. 占位符改为无主机形态（如 `https://example.invalid/` 仍在名单外，或直接改为 `访问地址` 文案 + `inputmode="url"`）；
2. 把 `http://…` 加入 `make_release.py` 的 `INERT_URL_LITERALS` 惰性名单，并同步 `tests/test_release.py` 的"名单无脱节"断言。

**该项在 W0 完成**，否则 M8-06/M8-07 的发布闸门带着已知红灯开工。

### 1.5 环境事实（避免重复踩坑）

| 事实 | 命令 |
|---|---|
| 项目目标环境 | `.venv39/bin/python` = **Python 3.9.11**（系统 `/usr/bin/python3` 为 3.9.6） |
| 全量测试必须用 `.venv39` | 用系统 python 跑，`tests/test_data_guard_enforcement.py` 的 13 条会因 pytester 子进程找不到 pytest 而假红 |
| 3.9 语法守护 | `PYTHON=./.venv39/bin/python scripts/check_py39.sh` |
| 前端测试 | `app/web/tests/index.html`（浏览器 harness，无 Node 入口），headless 版见 `docs/MCChecker_M3_M4_PLAN.md` §6 |

---

## 2. CFG 编号与卡片映射（反推，待与原始方案核对）

| CFG | 主题 | 卡 |
|---|---|---|
| CFG-00 | 术语、四类身份、领域契约、黄金样本 | M5-01、M5-02 |
| CFG-01 | 不可变 blob / state 指针 / 事务提交与幂等 | M5-03、M5-04 |
| CFG-02 | 严格值模型、定位映射与模板变体 | M5-05、M5-06 |
| CFG-03 | 权限矩阵、API 骨架、特性门禁 | M5-07、M5-08 |
| CFG-04 | 参数登记矩阵视图、文件页接入、UI↔API 合同 | M5-09、M5-10、M5-11 |
| CFG-05 | 软件版本登记、配置类别/变体、参数主档与定义修订 | M6-01、M6-02、M6-03、M6-04 |
| CFG-06 | 追踪策略、旧入口守卫、现场采集 | M6-05、M6-06、M6-07 |
| CFG-07 | Observation 解析、基线初始化、文件内对账 | M6-08、M6-09、M6-10 |
| CFG-08 | 变更单状态机、批准与发布 | M6-11、M6-12 |
| CFG-09 | XML 编译生成、不可变产物与下载 | M6-13、M6-14 |
| CFG-10 | 三方合并算法、通用/机型升级、升级 UI | M7-01、M7-02、M7-03、M7-04 |
| CFG-11 | 现场经验上提、历史回滚与出口补发 | M7-05、M7-06 |
| CFG-12 | 可选设备层（可延期） | M7-07、M7-08 |
| CFG-13 | 交付记录、回读核验 | M7-09、M7-10 |
| CFG-14 | 全量审计谱系、退役与生命周期收口 | M7-11、M7-12 |
| CFG-15 | 旧 data 预检迁移、备份与恢复 | M8-01、M8-02 |
| CFG-16 | 浏览器全域回归、安全并发、真实设备与性能 | M8-03、M8-04、M8-05 |
| CFG-17 | 发布包与零依赖门禁、文档与最终闸门 | M8-06、M8-07 |

---

## 3. 六个前置决策（开工前定稿，不改则返工）

> 每条给出**建议**与**代价**。未定稿的决策会同时污染 5 张以上卡片，因此要求在 W0 内以一行决策记录写进 `docs/know-how.md`。

### D1 · 配置域的持久化出口放哪里

- **建议**：新增 `app/core/config_repository.py` 作为 core 层的**第二个持久化模块**，与 `storage.py` 平级，复用 `fsutil` + `write_gate` + 新增 `management_lock`；`storage.py` 一行不改。
- **理由**：`storage.py` 已 2166 行且承担旧 JSON 兼容；把 5 类不可变对象塞进去会使 M4 已验收的行为回归风险不可控。
- **代价**：红线表述需要修订——"持久化走 storage"应改为"持久化只允许 `storage.py` 或 `config_repository.py`，二者互不 import"。该修订须写进 `docs/MCChecker_ARCHITECTURE.md` 与 `CLAUDE.md` 同 PR。

### D2 · 数据根布局

- **建议**：全部落在 `data/config_management/`（`state.json`、`objects/`、`blobs/`、`cache/`、`audit/`），机型只作为对象内的 `scope.profile_id` 字段，**不按机型分目录**。
- **理由**：卡面要求"原 `data/` 保持不变前提下添加 `data/config_management/`"（M5-03 原文）；且参数主档、软件版本、定义集合天然跨机型共享；API 前缀 `/api/v1/config/*` 与 `/api/v1/p/{pid}/config/*` 的差别是**作用域校验**，不是存储位置。
- **代价**：`state.json` 成为单点热文件，需 M5-04 的 CAS + 原子替换保证；并发规模上限为单进程，需 M8-05 实测。

### D3 · 全局修订通知

- **建议**：**新增全局端点** `GET /api/v1/config/revision`（独立内存计数，`bump("config")` 五段之外），`revision.PARTS` **保持 5 段不动**。
- **理由**：改 `PARTS` 会改变 `/api/v1/p/{pid}/revision` 的 `rev` 点分串长度，直接打破 §7.3 契约与大量既有断言，且旧前端比较逻辑会静默失效。
- **代价**：前端需要第二套轮询/比较逻辑（`config-api.js` 内封装，M5-11 落地）。

### D4 · 角色语义：部署者是否恢复

- **现状**：`is_deployer` 恒 False，`can("deployer")` ≡ `can("admin")`，未知能力返回 true。
- **建议（推荐 A）**：**恢复部署者 = 本机回环地址**，且部署者**不具备** `config_change_approve`；同时把前端 `can()` 改为 deny-by-default，能力键由 `/session.capabilities` 下发。
- **理由**：M5-07、M6-12、M7-12、M8-04 的验收条目全部建立在"四角色 + 批准权不可下放"之上；不恢复则该批条目无法成立。
- **代价**：与 `8a1d8ba` 的"取消部署者身份"相反，需要确认该提交的业务意图是否为"不再区分部署者"。
- **备选 B**：**维持取消部署者**，改用"管理员 + 成员 + 访客"三角色，M5-07 的"部署者"表述统一改写为"管理员与会话能力分离"，并同步修订 M6-12/M8-04 的验收措辞。
- **这条必须由你裁定**，两种走法的代码量与风险不同：A 需改 `auth.py`/`authz.py`/`shell.js` 与一批既有测试（约 20 条断言涉及 `is_deployer`）；B 只需改卡面措辞与新增能力键。

### D5 · 特性门禁形式

- **建议**：环境变量 `MCHECKER_CONFIG_MANAGEMENT=off|readonly|on`（默认 `off`），服务端在 handler 入口判定并返回 503 `CONFIG_DISABLED`；`off` 时不注册 `api_config_*` 路由。
- **理由**：M5-08 要求"禁用特性时旧服务功能可正常使用，新路由拒绝管理写入"，且项目已有 `MCHECKER_*` 环境变量惯例（`MCHECKER_DATA_DIR`、`MCHECKER_PORT`）。
- **代价**：`readonly` 档需要在每个写 handler 额外判定一次；建议由 `authz` 统一拦截而非逐 handler 复制。

### D6 · 真实 XML/JSON 样本来源

- **现状**：仓库 `tests/fixtures/` 无配置管理样本；`data/` 只有 `profiles/default/{bindings.json, records, cache, archive, configs}`，无 XML/JSON 配对样本。
- **不解决的后果**：M5-02 只能交付合成 fixture（卡面允许，但须显式标注）；**M6-14 的真实导入验收、M8-05 的真实设备验收无法关闭**，M8-07 发布闸门随之无法通过。
- **需要你提供**：至少 2 个相邻软件版本 × 2 种机型的 XML/JSON 配对（脱敏版即可），以及一份可在目标系统实际导入的代表性 XML。脱敏流程须可复现并留档（M5-02 验收第 2 条）。

---

## 4. 架构落点（新增模块清单）

```
app/core/
  config_values.py        # M5-05 严格值模型（missing/null/Decimal/enum/单位）
  config_mapping.py       # M5-06 Parameter ID ↔ XML 名称+loc / JSON key+pointer
  config_repository.py    # M5-03/04 不可变对象、blob 寻址、state 指针、CAS、回执、审计
  config_merge.py         # M7-01 三方合并纯函数
app/services/
  config_authz.py         # M5-07 动作×能力矩阵（薄封装 authz）
  config_catalog.py       # M6-01/02/03 软件版本、类别、变体、参数主档、定义集合
  config_policies.py      # M6-05/06 追踪策略与旧入口守卫
  config_ingest.py        # M6-07/08 采集与 Observation
  config_baselines.py     # M6-09 通用/机型基线初始化
  config_changes.py       # M6-11/12 变更单状态机、批准发布
  config_export.py        # M6-13/14 XML 编译与产物
  config_upgrade.py       # M7-02/03/05/06 升级、上提、回滚
  config_artifacts.py     # M7-09/10 交付记录与回读核验  ← 与 M6-14 的产物 API 同批落地
  config_audit.py         # M7-11/12 审计检索与生命周期
app/server/
  api_config_catalog.py   # M5-08 起逐卡挂载，按域拆分
  api_config_registry.py
  api_config_tracking.py
  api_config_changes.py
  api_config_artifacts.py
  api_config_audit.py
app/web/assets/js/
  core/config-api.js      # M5-11 统一请求/状态/CAS 冲突
  views/registry.js       # M5-09 参数登记矩阵
  views/configuration.js  # M5-10 文件页配置管理分段
scripts/
  inspect_config_management.py  # M8-01
  config_backup.py              # M8-02
```

**新增错误码**（`core/errors.py`，沿用 `HTTP_STATUS` 表）：`CONFIG_DISABLED`(503)、`PLAN_STALE`(409)、`POLICY_BLOCKED`(403)、`DEFINITION_CONFLICT`(409)、`MAPPING_AMBIGUOUS`(422)、`VALUE_OUT_OF_RANGE`(422)、`VALUE_LOSSY_CONVERSION`(422)、`OBSERVATION_UNKNOWN_RELEASE`(422)、`ARTIFACT_TEMPLATE_INCOMPATIBLE`(422)、`IDEMPOTENCY_MISMATCH`(409)。

---

## 5. 卡序与依赖

### 5.1 为什么有一次前移

卡编号顺序整体可用，但**两处必须前移**，否则 M6 会在无安全网的条件下写入：

1. **M8-02 的"最小一致备份/恢复子集"** 卡面风险说明原文即"正式现网受管写入的硬前置……可先交付最小一致备份/恢复子集，使 M6 能在受控现网试用"。因此拆为 **M8-02a（W2.5，最小子集）** 与 **M8-02b（W19，全量演练）**。
2. **FIX-0（修 `tools.js` 离线扫描回归）** 必须在 W0 做，否则发布闸门带红灯。

### 5.2 波次表

| 波次 | 卡 | 依赖 | 出口判据 |
|---|---|---|---|
| **W0** | FIX-0、M5-01、M5-02 | 无 | 领域规范落地；全量测试回到 1888 passed / 0 failed |
| **W1** | M5-03 → M5-04 | W0 | 不可变对象 + CAS 提交故障注入通过 |
| **W2** | M5-05、M5-06 | M5-01 | 严格值模型与定位映射可用 |
| **W2.5** | **M8-02a** 最小备份/恢复子集 | M5-03 | 能备份/恢复 `data/config_management` 到最近完整状态 |
| **W3** | M5-07 → M5-08 | M5-03/04、D4 定稿 | 权限矩阵 + 路由骨架 + 门禁（默认 off） |
| **W4** | M5-09、M5-10、M5-11 | W3 | 矩阵视图可演示（真实 API 未就绪时明确 503，不回退 mock） |
| **W5** | M6-01、M6-02 | W1、M5-01 | 软件版本与文件快照可登记、可精确解析 |
| **W6** | M6-03、M6-04 | W5、M5-09 | 参数主档 + 定义修订 API 与矩阵视图联通 |
| **W7** | M6-05 → M6-06 | W6 | 追踪策略服务端强制，旧入口不能绕过 |
| **W8** | M6-07 → M6-08 | W7、W2 | 采集 → 不可变 Observation → 严格解析 |
| **W9** | M6-09、M6-10 | W8 | 基线草稿 + 文件内对账 |
| **W10** | M6-11 → M6-12 | W9、W3 | 变更单状态机 + 管理员批准发布（**单版本闭环在此闭合**） |
| **W11** | M6-13 → M6-14 | W10、M5-06 | 生成 XML + 不可变产物 + 真实导入（受 D6 制约） |
| **W12** | M7-01 | W2 | 三方合并纯函数表驱动全绿 |
| **W13** | M7-02、M7-03 | W12、W10 | 通用/机型升级分别使用真实父修订 |
| **W14** | M7-04 | W13、W4 | 升级合并 UI 以服务端 MergePlan 为权威 |
| **W15** | M7-05、M7-06 | W13 | 上提与回滚不改写历史 |
| **W16** | M7-07、M7-08（**可整体延期**） | W13 | 设备层关闭时机型闭环不受影响 |
| **W17** | M7-09、M7-10 | W11、W16 | 交付四态分离 + 回读核验结论可信 |
| **W18** | M7-11 → M7-12 | W7、W17 | 审计可下钻到 SHA；退役后历史可查 |
| **W19** | M8-01、M8-02b | W18 | 旧 data 副本幂等迁移 + 全量恢复演练 |
| **W20** | M8-03、M8-04、M8-05 | W19 | 浏览器全域 / 安全并发 / 真实设备与性能三份证据 |
| **W21** | M8-06 → M8-07 | W20 | 发布包零依赖离线可跑；最终闸门逐条签署 |

### 5.3 关键路径

```
M5-01 → M5-03 → M5-04 → M5-08 → M5-10
                            ↓
        M6-03 → M6-09 → M6-11 → M6-12 → M6-13 → M6-14
                                   ↓              ↓
                             M7-01 → M7-03 → M7-04  M7-09 → M7-10
                                   ↓
                             M7-12 → M8-01 → M8-02b → M8-03/04/05 → M8-07
```

**关键路径长度**：22 个卡位。任何一环延期直接顺延交付，因此 W0–W4（M5 骨架）不允许并行压缩质检。

---

## 6. M5 逐卡计划（11 卡，19.0 人日）

> M5 的统一约束：**不重写旧模块**；所有新写入走 `config_repository`；`data/` 原有内容零改动；每卡结束必须 `pytest -q` 全绿 + H3 守卫绿。

### M5-01（INH-1935）统一配置管理术语、四种版本身份和领域契约 · 1.5 d · **W0**

- **产出**：`docs/MCChecker_CONFIG_DOMAIN_SPEC.md`（新）、`tests/fixtures/config_management/contracts/*.json`
- **要点**：定义 `ConfigFamily` / `SoftwareRelease` / `FileSnapshot` / `DefinitionRevision` / `BaselineRevision` 五类身份的稳定 ID 规则、作用域（global｜profile｜model｜device）、父引用字段、状态机；`LogicalFile` / `TemplateVariant` / `DefinitionSetRevision` 的引用规则；**明确 `archive` 目录名 ≠ 软件版本**；给出 shared `/api/v1/config/*` 与 profile `/api/v1/p/{pid}/config/*` 的接口清单与错误码表。
- **测试**：`tests/test_config_contracts.py` — fixture 可加载、ID 唯一、状态枚举闭合；断言 `SoftwareRelease` 与旧 `version` 字段不共用命名（文本级检查）。
- **风险**：CFG-00 原始方案缺失（第 0 章），须与用户核对后再冻结。

### M5-02（INH-1936）双版本双机型 XML/JSON 黄金样本与合约测试 · 1.5 d · **W0**

- **产出**：`tests/fixtures/config_management/samples/`、`expected/`、`tests/test_config_fixtures.py`
- **要点**：覆盖参数改名、XML 位置移动、JSON 全大写命名、单位转换、类型/范围变化、新增/删除、同名兄弟、命名空间、空/部分采集、固定值、机型差异 11 类；每例写期望 `Parameter ID` 与输出 XML 值。
- **测试**：读取测试 + 期望清单比对；测试数据隔离守卫同步。
- **阻塞**：**D6**。无真实样本时交付合成 fixture，并在卡内与 `docs/` 显式标注"现场验证未做"，**不得**写成通过。

### M5-03（INH-1937）不可变 blob、对象存储与 state 指针 · 2.0 d · **W1**

- **产出**：`app/core/config_repository.py`、`tests/test_config_repository.py`
- **要点**：`data/config_management/{state.json,objects/,blobs/,cache/}`；SHA-256 内容寻址；**先写对象 → 单次原子替换公布 state**；损坏 state / 引用断裂 / blob 哈希不一致 → 可诊断只读失败（`CORRUPT_DATA`，禁止归零）。
- **测试**：正常/损坏/缺失写读；state 未发布时新对象不可见；删 `cache/` 不影响权威对象；与旧 `profiles/` 并发读。
- **约束**：单进程 writer，禁止 SQLite / WAL。

### M5-04（INH-1938）事务提交、CAS、审计与持久幂等 · 2.5 d · **W1**

- **产出**：`config_repository.py` 扩展（`commit()`、`OperationReceipt`、`AuditEvent`）、`tests/test_config_transactions.py`
- **要点**：一次提交同批公布"实体修订 + AuditEvent + 回执 + 必要的索引根"；锁序 `WriteGate → profile_lock → management_lock`，锁内重取 state 并核验 `expected_revision`；同幂等键+同正文回执重放，异体 `IDEMPOTENCY_MISMATCH`。
- **测试**：故障注入（磁盘满、`PermissionError`、kill/restart、重复请求、并发、孤儿对象）；两用户改同一旧修订只有一个成功。
- **约束**：先做成特性受控隔离，**不拦截**现有生产写入路径。

### M5-05（INH-1939）严格配置数值模型与 JSON 校验 · 1.5 d · **W2**

- **产出**：`app/core/config_values.py`、`tests/test_config_values.py`
- **要点**：`decimal.Decimal` + `object_pairs_hook` 拒重复键 + `parse_constant` 拒 NaN/Infinity；区分 missing / null / `""` / `0` / `false`；JS 侧以**类型化文本**承载精确值；单位换算（ms↔s）显式且可逆。
- **测试**：`10000000000000001` 不经 JS Number 舍入；有损/越界/舍入不明时阻断且原文件不变；负数、bool、枚举边界、无效 UTF-8、超深 JSON 拒绝。
- **注意**：旧 `parser`/`to_compact` **不改造**，新模型并行存在。

### M5-06（INH-1940）XML/JSON 定位映射校验与模板变体契约 · 2.0 d · **W2**

- **产出**：`app/core/config_mapping.py`、`tests/test_config_mapping.py`
- **要点**：稳定 `Parameter ID` ↔ 版本化「XML 名称 + loc」/「JSON 键 + Pointer」；Pointer 转义正确；XML 同名节点与 namespace 唯一性；`TemplateVariant` 兼容判定。`testParamX` 与 `TESTPARAMX` **仅作候选**，不自动确认。
- **测试**：定位唯一性、特殊键、数组插入、变体错配；两个可写参数指向同一目标必须拒绝；`parser` 旧行为回归。
- **边界**：人机确认接口留给 M6-04，本卡只做领域校验。

### M5-07（INH-1941）配置管理动作权限、错误码与会话能力 · 1.0 d · **W3**

- **产出**：`app/services/config_authz.py`（或直接扩 `authz.ACTIONS`）、`tests/test_api_config_authz.py`、前端能力矩阵用例
- **要点**：新增 `config_registry_write` / `config_tracking_admin` / `config_change_submit` / `config_change_approve` / `config_artifact_generate`；`/session` 增加 `capabilities` 字段（**保留** `is_deployer`/`is_admin`）；前端 `can()` 改 **deny-by-default**。
- **测试**：访客调用新写端点 403 且权威 sha 不变；撤权后旧页面提交被拒；未知能力返回 false。
- **阻塞**：**D4**（部署者是否恢复）。缺口 A 必须先在此卡解决。

### M5-08（INH-1942）API 骨架、特性门禁与全局修订通知 · 1.5 d · **W3**

- **产出**：`app/server/api_config_*.py`（骨架）、`tests/test_api_config_routes.py`、`/api/v1/config/revision`
- **要点**：统一 request/response/error 协议、分页 cursor、`Idempotency-Key` 与 `If-Match` 契约；缺 pid / 未知 pid **不回落 default**；跨 scope 不得用 body 改别的机型；门禁见 **D5**（默认 off，禁止回退 mock）。
- **测试**：路由 fixture、CSRF（沿用 `http.py` 的 `X-Requested-With`，不重复实现）、body 限额、错误响应一致性；更新 `tests/test_api_revision.py` 的路由覆盖表。

### M5-09（INH-1943）参数登记矩阵原生视图 · 2.5 d · **W4**

- **产出**：`app/web/assets/js/views/registry.js`、CSS 增量（`views.css`/`components.css`）、前端纯逻辑测试
- **要点**：一个参数主档 × 多软件版本定义的横向矩阵；搜索历史 XML/JSON 名称；差异高亮；版本切换；固定 ID/名称列 + 版本列横向滚动；沿用 `window.MC`、`MC.util.h`、CSS tokens，**不引入框架**。
- **测试**：1366×768 与 1920×1080 可读；历史名称命中同参数；键盘焦点/滚动保持；无匹配与过期状态。
- **依赖**：Linear INH-1943 Resources 中的 `MCChecker-Current-UI-Prototype.html`（702,801 B，SHA-256 `c017cf38…54ec`）——**需先下载留档到 `docs/`**，它是 UI 验收目标而非功能证据。

### M5-10（INH-1944）文件页配置管理分段与可恢复路由 · 1.5 d · **W4**

- **产出**：`app/web/assets/js/views/configuration.js`、`shell.js`/`fileview.js` 增量、`tests/test_config_ui_routing.py`
- **要点**：侧栏新增参数登记入口；文件页新增"配置管理"分段，原结构/历史/对比/修改记录**保持原样**；深链携带 `pid`、`filename`、`file_version`；从历史进入必须用所选 `archive_ref` 而非 current；无关联时显示"待关联"。
- **测试**：多标签/机型切换、tab 上限、历史选择；`tests/test_dl_file_layout.py` 必须继续通过（防回退 3a60746 的 DL 页布局）。
- **风险**：原型基线是 `main@dca331d`，与当前 `3a60746` 在 DL 页、导入页、文件页分段已有差异，**以当前 main 为准**，差异写进 PR。

### M5-11（INH-1945）UI ↔ API 合同测试与草稿/权限状态保护 · 1.5 d · **W4**

- **产出**：`app/web/assets/js/core/config-api.js`、`app/web/tests/tests.js` 增量、服务端合同用例
- **要点**：契约 mock 与真实 API 同 payload；状态区分 loading/empty/forbidden/error/conflict/draft；CAS 冲突保留用户决议；切机型/关标签前提示未保存草稿；只有服务端成功才 Toast「已发布」。
- **测试**：403/409/422 的 `detail.field` 与 UI 状态一致；production HTML 不加载 mock。

---

## 7. M6 逐卡计划（14 卡，25.5 人日）

> M6 的统一约束：**首轮仅对隔离脱敏副本验收**；正式数据写入须待 M8-02a 通过。

### M6-01（INH-1946）软件版本登记与不可变出口默认批次 · 1.5 d · **W5**

- 产出：`services/config_catalog.py`（release 部分）、`tests/test_config_release.py`
- 要点：`SoftwareRelease(显式前驱/build)` + `ReleaseDefaultRevision` + 出口 XML/可选 JSON 快照；同版本补发**新增批次**不覆盖原件；不接受仅凭文件名/mtime 推断版本，缺来源置"待确认"。
- 验收映射：补发后旧默认 XML 哈希与下载内容不变；无模板可归档 JSON 但不可标"可生成"。

### M6-02（INH-1947）配置类别、结构变体、稳定逻辑文件与来源关联 · 1.5 d · **W5**

- 产出：`config_catalog.py`（family/variant/logical file）、`tests/test_config_catalog.py`
- 要点：把现有 `pid + filename + kind + version` 精确解析到 `FileSnapshot`；XML/JSON 是同一业务配置的不同表示，**一个字段不得同时充当 `default_xml` 与 `field_json`**；改名保留稳定 ID 与历史别名；旧归档关联到不可变原文而非 current。
- 测试：A/B 机型同名文件不串；退役/改名后按旧关联可查历史。

### M6-03（INH-1948）参数主档、跨版本定义修订与冻结定义集合 · 2.0 d · **W6**

- 产出：`config_catalog.py`（parameter/definition）、`tests/test_config_definitions.py`
- 要点：`Parameter(id, 名称, 类别, 备注)` + `DefinitionRevision(xml 名称+loc, JSON key+pointer, type/unit/constraints/remarks)` + `DefinitionSetRevision`；定义修订**不可原地覆盖**；集合与目标版本出口批次/变体精确绑定；提供 retire/reactivate。
- 测试：旧名检索回同一 ID；改引用生成新修订且历史基线仍指旧集合；定位冲突/单位与 XML 元信息不符阻断发布。

### M6-04（INH-1949）参数登记查询/编辑 API 与前端联动 · 1.5 d · **W6**

- 产出：`api_config_registry.py`、`registry.js` 由 mock 切真实数据、`tests/test_api_config_registry.py`
- 要点：`GET /config/parameters`、`GET /config/parameters/{id}`、创建/主档修改、`definitions` 修订；表单含保存理由、`expected_revision`、修改前后摘要；409 保留草稿。
- 验收：两个软件版本定义写入后跨浏览器一致；四种检索维度（旧名/XML 键/JSON 键/用户名称）都定位稳定 ID。

### M6-05（INH-1950）管理员管理 archive_only / managed 追踪策略 · 1.5 d · **W7**

- 产出：`services/config_policies.py`、`api_config_tracking.py`、`settings.js` 追�踪子页、`tests/test_config_policies.py`
- 要点：`TrackingPolicyRevision`（类别级 + 机型级覆盖）；服务端统一计算**有效策略**并返回继承来源；开关写审计与理由；管理权限与原 `settings_write` 严格区分。
- 验收：关 managed 后历史仍可查；待审不能借关开关发布。

### M6-06（INH-1951）旧导入/审阅/删除/机型操作的策略守卫 · 2.0 d · **W7**

- 产出：`config_policies.py`（守卫）、`tests/test_config_policy_boundaries.py`
- 要点：在 `upload`、`updates`、`review`、`profiles`、`storage` 的每个写入口按稳定 `LogicalFile`/配置族判定可执行动作；managed 下现场 JSON → observation，XML 出口 → 候选，旧节点审阅 → 变更来源，**均不得直接发布基线**；改名/复制/删除保留关联与审计。
- 测试：`PUT /files`、URL 导入、定时任务、旧 review 提交、删文件五条旁路逐一断言不能越 gate；archive_only 回归不变。

### M6-07（INH-1952）现场采集接入不可变 Observation · 2.0 d · **W8**

- 产出：`services/config_ingest.py`、`tests/test_config_ingest_updates.py`
- 要点：复用现有手动/定时下载 + 归档 + `TaskRunner`；新增 source association（`pid + source_name + 角色 + family + 版本来源`）；采集事件与投影意图**同次持久提交**；任务入队防重复。
- 测试：同内容重复下载不生成重复修订（保留采集时间事件）；200 错误页/字段错误/未知格式/无版本不覆盖已批准基线；旧任务晚到不得覆盖新内容。

### M6-08（INH-1953）Observation 严格解析、版本归属与映射结果 · 2.0 d · **W8**

- 产出：`config_ingest.py`（Observation/MappingResult）、`tests/test_config_observations.py`
- 要点：持久保存原文、来源/采集时间、版本与可选设备身份、completeness、逐项可观测状态、未匹配键；`unknown` / `release_unconfirmed` / `partial` 三态；重新映射**不覆写**原始字节与上次结果。
- 测试：缺失/null/空串/0/false 区分；数组错位、重复键不静默匹配；采集者/采集时间与修改人/修改时间分离。

### M6-09（INH-1954）通用/机型基线初始化与继承意图 · 2.0 d · **W9**

- 产出：`services/config_baselines.py`、`tests/test_config_baselines.py`
- 要点：从指定出口默认 + 定义集合建 `CommonBaseline` 草稿；从**精确**通用基线建 `ModelBaseline` 草稿；记录 `inherit`/`override`/`pin`、`upstream_ref`、`previous_revision_id`；禁止"旧版最新"顶替实际父修订。
- 验收：值等于默认但显式 pin 的意图保留；新通用基线变化不使已发布旧机型基线漂移；缺定义/必填/模板时草稿可看但禁止进"可发布"。

### M6-10（INH-1955）文件内现场对账、目标值编辑与变更草稿联动 · 2.0 d · **W9**

- 产出：`configuration.js`（对账分段）、前端状态测试
- 要点：现场原值 / 批准基线 / 拟值三栏并列；`unknown`/`unmapped`/`missing`/`conflict` 分开呈现；编辑走服务端 plan + changed rows，**不得把整个 XML/JSON 存 localStorage 当权威**。
- 验收：切标签保留未保存输入；选机型/设备/通用层前提示影响范围；新 JSON 到达显示过期提醒而不静默覆盖。

### M6-11（INH-1956）变更单 draft/submit/withdraw/reject 状态机 · 2.0 d · **W10**

- 产出：`services/config_changes.py`（状态机）、`tests/test_config_changes.py`
- 要点：`ChangeSetRevision` 基于固定 `MergePlan`；草稿可多次修订；提交冻结输入/选择/理由；驳回、撤回、重提留执行人/时间/理由与前后修订链；`stale` 是计算标记不毁历史。
- 测试：并发、幂等、改后再提交、撤回终态、非法回跳；已提交差异冻结，目标 head 变化返回 `PLAN_STALE`。

### M6-12（INH-1957）管理员批准并发布基线与审阅页双流程 · 2.0 d · **W10**

- 产出：`config_changes.py`（approve/publish）、`review.js` 新增"配置变更"分段、`tests/test_config_approval.py`
- 要点：批准即**原子**发布新基线 + 审计 + 回执，不引入 approved-but-unpublished 中间态；发布前重新校验权限、计划状态、目标 head、定义集合、模板哈希、参数范围、未决冲突。
- 验收：仅管理员可批准（D4 决定"部署者不可批准"如何表述）；并发双批准只产一份修订；磁盘/替换出错旧基线仍有效。
- **里程碑**：M6 单版本闭环在此闭合。

### M6-13（INH-1958）从已批准基线生成目标 XML · 2.5 d · **W11**

- 产出：`services/config_export.py`、`tests/test_config_xml_export.py`
- 要点：以真实模板（固定 hash/变体/定义集合）为骨架，复用 `reviewing.apply_edits`；**禁止从精简 JSON 重建复杂 XML**；no-op 直接拷模板；保留 namespace/注释/PI；XML writer 全局锁防并发注册污染。
- 验收：结果可重新解析且恰只在批准位置变化；模板不兼容/需改空元素/不安全语义显式拒绝；不宣称逐字节保真。

### M6-14（INH-1959）不可变产物、下载 API 与单版本真实导入 · 1.5 d · **W11**

- 产出：`services/config_artifacts.py`（产物部分）、`api_config_artifacts.py`、`tests/test_config_artifacts.py`、文件页"交付"分段
- 要点：`Artifact(id, baseline/template/definition_set/blob_hash/validation_ref)`；同输入同生成版本返回已知产物；`GET artifacts/{id}/raw` 带 hash 与文件名；下载**不动态重生成**。
- 验收：重复下载 SHA 一致；真实系统导入一份 XML 并记录操作者/环境/结果——**受 D6 制约，缺环境时显式"待验"，卡不得关闭**。

---

## 8. M7 逐卡计划（12 卡，22.0 人日；含可延期 3.0）

### M7-01（INH-1960）三方合并纯算法 · 3.0 d · **W12**

- 产出：`app/core/config_merge.py`、`tests/test_config_merge.py`
- 要点：按稳定 `Parameter ID` 对齐 O（旧共同基准）/ L（旧有效机型值）/ N（新通用基线）；比较 presence + typed value + intent，而非节点位置或裸字符串；依对应版本 `DefinitionSet` 做单位/类型/枚举/范围转换；输出逐项候选、阻断项、允许动作，**不发布**。
- 验收：L 与 O 等值但 pin → 提示决定；`650ms` ↔ `0.8s` 先转换并给证据；参数拆分/合并、目标已删除却 override、未知参数 → 人工处理。
- 约束：**纯函数、无 I/O**，可被 M7-02/03/05/06 复用；表驱动正反例。

### M7-02（INH-1961）新版通用基线初始化：旧通用继承 + 真实出口默认 · 2.0 d · **W13**

- 要点：`common_release_upgrade` 三方合并 = 旧出口默认 A / 旧已批准通用 B / 新版出口默认 C；固定 release/default/definition_set/template 与转换规则；旧通用**只提供受控继承意图**，不得覆盖新版出口原 XML。
- 验收：多出口批次 + 两变体时不"随机选最新"；无上版来源可新建但不得显示"三方继承成功"。
- 测试：`tests/test_config_common_upgrade.py` 比对原始 XML hash、provenance、新基线谱系。

### M7-03（INH-1962）机型跨版本升级与同版本基线刷新 · 2.0 d · **W13**

- 要点：`model_release_upgrade` / `model_rebase` 分别取该机型**实际引用**的旧通用基线作共同基准；旧机型 override/pin 与机型特有范围校验形成新修订；通用升版**不自动传播**。
- 验收：A 引旧 r2、B 引 r4 时各用自身父修订；通用 r5 更新后机型头不漂移，显式刷新才发 r+1。
- 测试：`tests/test_config_model_upgrade.py`（多机型交叉、旧 parent 非 latest、`PLAN_STALE`）。

### M7-04（INH-1963）升级合并 UI：冲突决议、批量与重算 · 2.5 d · **W14**

- 要点：左"旧共同基准/旧机型值/新通用值"，右"候选值/决定/理由"；批量处理无冲突项；`use_new`/`inherit_old`/`manual`/`pin`/`clear_pin`；目标 head 或定义变化显示过期并保留草稿。
- 验收：可按新增/删除/改名/类型/单位/冲突/固定值过滤，**不以颜色替代文本**；无共同基准时禁用一键发布。
- 约束：候选值全部来自服务端 MergePlan，**不得移植 demo 的前端状态**。

### M7-05（INH-1964）现场经验上提（设备→机型、机型→通用） · 1.5 d · **W15**

- 要点：`promote_to_common` / `promote_to_model` 计划；排除单机校准与未知来源；形成新 `ChangeSet` 经管理员批准；批准后**不改写**所有下级基线。
- 验收：一台机型的补偿值不因多次出现自动变通用默认；上提后 A/B 仍引旧上级，需显式 rebase；证据撤销/版本不匹配 → 计划过期阻断。

### M7-06（INH-1965）历史回滚为新基线、出口补发与跨变体迁移 · 1.5 d · **W15**

- 要点：`restore_baseline`（历史内容 → **新**已批准修订）、同版出口补发后的受控刷新、跨不兼容变体的显式迁移计划；**不修改历史对象与 head 指针**。
- 验收：回滚后仍可检索被回滚修订与其产物；不得把旧软件 XML 当新软件 XML 下发；补发不改变已发布基线使用的批次引用。

### M7-07（INH-1966）可选设备目录：稳定设备 ID 与机型归属 · 1.5 d · **W16 · 可延期**

- 要点：仅当存在真实设备身份（序列号/标定差异）时启用；`/p/{pid}/config/devices`；**不得由"某机型 JSON"或 IP 反推设备**；设备 ≠ profile；无需求时 UI 隐藏入口。
- 交付：`tests/test_config_devices.py`（权限、归属、重名、退役、迁移冲突）。
- 验收：不存在 `device_id` 的观测不得被分配任意设备；设备退役后历史可读、新写禁用；不启用功能时机型级配置完整可用。

### M7-08（INH-1967）可选单台设备例外与 `device_rebase` · 1.5 d · **W16 · 可延期**

- 要点：`DeviceOverride` / `DeviceBaseline` 从**实际机型父基线**派生；按同一设备旧父修订做 `device_rebase`；设备结果只能写 device scope；上提机型须另建审批提案。
- 验收：单台标定值不进机型基线、不出现在其他设备的生成 XML；机型升级后设备例外须显式 rebase。
- **延期判据**：业务侧无设备级需求 → M7-07/08 整体标注"功能关闭"，并在 M8-07 文档中显式声明，验收不虚报。

### M7-09（INH-1968）交付记录四态分离 · 1.5 d · **W17**

- 要点：`DeliveryRecord` 连接 Artifact、目标机型/设备、登记操作人、实际执行时间、导入结果与证据；状态 `待导入 / 已声明成功 / 失败 / 证据不足`；**只做人工登记，不接自动控制器**。
- 验收：下载不自动标"已导入"；失败/撤回/重试逐次留记录；缺设备身份不得伪造"所有设备已完成"。

### M7-10（INH-1969）回读核验：覆盖率与新鲜度 · 2.0 d · **W17**

- 要点：新 Observation 对比 Artifact/Baseline，输出逐参数 + aggregate（`matched/missing/mismatched/unobservable`、`verification_scope`、`capture_time`、`source_fingerprint`）；结论 `verified/mismatch/incomplete/version_mismatch`。
- 验收：缺项不当 0；XML 独有字段无 JSON 观测时不宣传全量通过；采集时间早于导入 → 拒绝 verified；同产物允许多次核验且旧结论留档。

### M7-11（INH-1970）全量审计检索与来源谱系 · 2.0 d · **W18**

- 要点：`/config/audit`、`/config/history/{object_id}`，cursor 分页；可按对象/参数 ID、软件版、机型、修改人/时间/理由、类别检索，**含退役对象**；从参数主档下钻到变更单与原文件 SHA；**不依赖普通日志反推审计**。
- 验收：任意发布能解释"改了什么、谁何时为何、基于哪版、谁审阅、生成什么 XML"；审计大小与排序稳定。

### M7-12（INH-1971）配置/机型退役与生命周期收口 · 1.5 d · **W18**

- 要点：`managed → archive_only → managed` 往返、文件/更新源改名、机型复制/退役/恢复的全域关系保护；退役优先稳定 `LogicalFile` 与管理域索引；复制机型**新建草稿**而非复制伪造审计；停用期原文回归检测、重开须重对账。
- 验收：旧 `DELETE /files`、profile DELETE、sources rename、定时任务均无法物理删除受管历史；退役后历史 API 仍可按稳定 ID 访问。

---

## 9. M8 逐卡计划（7 卡，17.0 人日；含前移的 M8-02a）

### M8-02a（前移 · INH-1973 子集）最小一致备份/恢复子集 · 1.0 d · **W2.5**

- 产出：`scripts/config_backup.py`（子集模式）、`tests/test_config_backup_min.py`
- 要点：仅覆盖 `data/config_management`（`state.json` + 已提交对象 + blobs）+ 恢复后校验；`with write_gate.maintenance():` 保证安全点；恢复先只读 plan + 预期哈希校验。
- 判据：M6 现网试用前，能回到最近完整已提交状态；坏 state 不归零。

### M8-01（INH-1972）旧 data/ 预检、初始化与幂等迁移 · 2.5 d · **W19**

- 产出：`scripts/inspect_config_management.py`、`tests/test_config_migration.py`
- 要点：`inspect → initialize → verify` 只读预检与安全初始化；识别 v1/v2/mixed layout、归档、人员与管理员表、机型、待审记录；输出结构化迁移计划 + 人工确认项；**只在已备份副本上初始化**；重复执行不产生重复基线。
- 验收：真实脱敏旧副本前后原文件 SHA/路径完整可对账；缺版本/XML 标"待关联"不猜当前版；中断可重跑。

### M8-02b（INH-1973）全域备份恢复与中断演练 · 2.0 d · **W19**

- 要点：保护 `profiles`、全局人员/机型文件、管理域、不可变对象、恢复区；备份含 manifest/checksums/schema；修补现有 `--restore` 仅 dry-run 的限制。
- 验收：写入中断/磁盘满/投影滞后可恢复；重复恢复幂等且有审计；不得自动回滚新业务数据。
- 交付：两套副本演练 + 故障矩阵 + `kill -9` + 人工应急手册。

### M8-03（INH-1974）真实浏览器完整业务流与旧功能回归 · 3.0 d · **W20**

- 要点：全链路 参数跨版本登记 → 默认 XML/机型基线 → 采集 JSON → 对账 → 变更审阅 → 发布 → 下载 XML → 人工导入登记 → 版本迁移 → 回读核验；1366×768 与 1920×1080；机型 A/B；四角色；前进后退、deep-link、禁 mock、未保存草稿、冲突与跨页状态。
- 验收：每个场景留真实步骤/输入/结果/截图/日志与最终 commit，**缺项 = 未验**；旧文件树/历史/对比/搜索/收藏/节点审阅/DL/更新/导入/机型不回归。
- 交付：`docs/MCChecker_ACCEPTANCE_LEDGER.md` 新增 M5–M8 段，并与旧 UC-01～46 映射。

### M8-04（INH-1975）安全、权限、并发与故障注入联合验收 · 2.5 d · **W20**

- 要点：新全局 API + profile API 的四角色正反、CSRF、SSRF、XSS、路径处理、并发发布、旧入口旁路、全域故障注入；模拟编辑/审批中撤权、提交后断线重试、state 替换失败。
- 验收：403/409/422/503 均不静默改权威数据；管理域原子提交不出现"审计无基线/有基线无审计"；全仓回归跑三次并提交原始输出。
- 纪律：失败另开修复卡，**不改松原断言**。

### M8-05（INH-1976）真实设备 XML、离线首访、性能与缺口验收 · 2.5 d · **W20**

- 要点：真实/脱敏目标系统验证 XML 导入与采集回读覆盖率；Python 3.9.11；冷缓存 + 禁公网；参数矩阵/合并/审计的大规模性能；对照 `INH-1910`、`INH-1932` 现状定义新业务指标。
- 验收：XML 实际导入或明确标"缺设备/环境"；断网首访 Network 无非本服务器请求；性能先实测再优化，**不假造百分比**。
- 关联：`docs/MCChecker_LEFTOVERS.md` L2（搜索 P50 2688 ms vs 预算 500 ms）在 M8 前必须有结论（改机制 或 正式放宽预算并同步 SPEC）。

### M8-06（INH-1977）发布包与零依赖离线门禁 · 1.5 d · **W21**

- 要点：新增 API/JS/CSS/脚本纳入 `scripts/make_release.py` 白名单；`app/web/index.html` 只加载生产模块；离线资源扫描 + hash manifest 覆盖新增代码；Python 3.9.11 零第三方运行依赖。
- 验收：包内不含 `data/`、secret、私人样本、dev fixture、测试资产、未授权外链；Windows/Linux 首装首访留证。
- **回归前提**：FIX-0 已完成，`tests/test_release.py` 全绿。

### M8-07（INH-1978）管理员手册、迁移/回退文档与最终发布闸门 · 2.0 d · **W21**

- 要点：更新 `README.md`、`docs/manual.md`、`docs/MCChecker_ARCHITECTURE.md`、`requirements.md`、`docs/know-how.md`、M5–M8 台账；发布记录列破坏性变化、schema 兼容矩阵、备份恢复与回滚步骤。
- 闸门：M5–M8 所有非可选核心卡有真实通过证据；设备层若延期须显式关闭且不虚报；M4-08 阻塞与物理离线缺口**关闭或有获批准且留证的豁免**。
- 交付：最终 commit + 测试命令与退出码 + 目标系统导入/回读结论 + 未解决风险清单。

---

## 10. 每卡统一验证（照抄执行）

```bash
cd /Users/wangdongxin/projects/mcchecker

# 1) 全量后端（必须用 3.9.11，用系统 python 会出现 13 条假红）
.venv39/bin/python -m pytest -q

# 2) 定向
.venv39/bin/python -m pytest -q tests/test_config_<area>.py

# 3) 3.9 语法守护（H1）
PYTHON=./.venv39/bin/python scripts/check_py39.sh

# 4) 前端（涉及 web/ 的卡）
open app/web/tests/index.html          # 期望 ALL PASSED (n)，n 只增不减

# 5) 发布门禁（涉及 web/、scripts/、根目录的卡）
.venv39/bin/python -m pytest -q tests/test_release.py

# 6) H3 守卫：观察每轮开头的「H3 守卫执法中：真实 data/ …」为绿；
#    报告哈希必须当场跑口径命令并贴命令，禁止抄文档里的旧值
```

**硬性纪律**（继承 M3/M4 轮次）：

1. 一卡一 PR，正文写 `INH-xxxx` + 卡号 + 依赖卡；合入前 Linear 评论「范围/变更/验收结果/遗留」再置 Done。
2. 任何写接口必须 `authorize` + 四角色测试；成功后在 **service 层** `revision.bump`（配置域 bump 全局计数，见 D3）。
3. `data/` 只读：新域数据写 `data/config_management/`，测试一律用 `isolated_data_root`。
4. 前端不引入框架；改动 mock 必须同 PR 改 `mock-api.js` 并跑前端测试页。
5. 声称"M6 闭环 / M8 通过"必须有可复现命令与输出，**不接受"逻辑上应该通过"**。

---

## 11. 风险登记

| # | 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|---|
| R1 | **真实 XML/JSON 样本缺失**（D6） | 高 | M5-02 降级为合成样本；M6-14/M8-05 无法关闭 | 尽早提出需求；卡面显式标注"待验"，不虚报 |
| R2 | **M4-08 未关闭**（L1：真实浏览器逐 UC、旧数据副本、物理断网） | 已发生 | M8-07 闸门无法签署 | 与 M5 并行推进人工验收；缺口转为有留证的豁免 |
| R3 | **INH-1932 搜索性能超预算**（P50 2688 ms vs 500 ms） | 已发生 | M8-05 性能项、§12 预算 | M8 前二选一：换机制（预计算索引）或正式放宽并同步 SPEC |
| R4 | **UI 原型基线落后**（`main@dca331d` vs `3a60746`） | 高 | M5-09/10 误回归 DL/导入页 | 先下载原型留档；差异逐条写进 PR；以当前 main 行为为准 |
| R5 | **D4 未裁定**（部署者是否恢复） | 中 | M5-07/M6-12/M8-04 验收措辞与实现两套 | W0 内裁定；B 方案只需改措辞与能力键 |
| R6 | **单进程写入 + 禁 SQLite** 的性能天花板 | 中 | 大配置矩阵、审计分页可能超时 | 对象寻址 + 索引根预计算；M8-05 实测后再优化热点 |
| R7 | 44 卡规模下的评审带宽 | 高 | 关键路径 22 卡位顺延 | 采用"小 PR + 双人评审 + 每日关键路径对齐"；M8 验收类卡提前预约人工时段 |
| R8 | 设备层（M7-07/08）范围蔓延 | 中 | 拖累 M7 收口 | 默认延期；只在业务确认设备级需求后启动 |

---

## 12. 工时与里程碑

### 12.1 工时合计

| 里程碑 | 卡数 | 人日 |
|---|---|---|
| M5 配置治理基础 | 11 | 19.0 |
| M6 单版本闭环 | 14 | 25.5 |
| M7 跨版本生命周期 | 12（含可延期 2 卡 / 3.0 d） | 22.0 |
| M8 迁移发布门禁 | 7（含前移的 M8-02a） | 17.0 |
| **小计** | **44** | **83.5** |
| 评审/返工余量（15%） | — | 12.5 |
| **合计** | | **≈ 96 人日** |

### 12.2 里程碑与出口

| 里程碑 | 出口判据 | 单人节奏 | 双人并行 |
|---|---|---|---|
| **MS-5 骨架可演示** | 参数矩阵视图可操作；真实 API 未就绪的入口明确 503；权限与门禁生效；旧功能零回归 | W0–W4 ≈ 4 周 | ≈ 2.5 周 |
| **MS-6 单版本闭环** | 一个类别 × 一个软件版本 × 两种机型：登记 → 采集 → 对账 → 审批 → 生成 XML → 产物下载（真实导入待 D6） | +5.5 周 | +3 周 |
| **MS-7 跨版本** | 三方合并、通用/机型升级、上提与回滚、交付与回读、审计与退役；设备层可关闭 | +4.5 周 | +2.5 周 |
| **MS-8 可发布** | 旧数据幂等迁移、备份恢复演练、浏览器/安全/性能/离线四份证据、发布包零依赖 | +3.5 周 | +2 周 |
| **总计** | | **≈ 19 周** | **≈ 10–11 周** |

> 双人并行的压缩来自 M5 之后可拆 W5/W6 与 W12 三条支线；代价是 `config_repository` 与 `config_catalog` 两个共享模块的接口冻结要更早（建议 W3 结束即冻结）。

---

## 13. 待你确认的六件事

1. **D4 角色语义**：恢复"部署者 = 本机回环、且不可批准"（推荐 A），还是维持"已取消部署者"、改写卡面措辞（B）？
2. **D6 真实样本**：能否提供 2 个软件版本 × 2 机型的 XML/JSON 配对（脱敏）与一份可实际导入的 XML？不能的话，M6-14/M8-05 将以"待验"状态延后。
3. **CFG 原始方案**：《MCChecker 新 UI 与配置生命周期管理实现方案》能否提供？本文第 2 章的 CFG 映射是反推版。
4. **M4-08 处置**：是并行人工验收补齐 L1 三项，还是先申请有留证的豁免、把风险带进 M8-07？
5. **INH-1932 方向**：换搜索机制，还是正式放宽 100 文件档预算并同步 SPEC？
6. **设备层**：M7-07/08 默认延期是否可接受？

---

## 附：卡号速查

| 波次 | 卡号 | Linear |
|---|---|---|
| W0 | FIX-0 / M5-01 / M5-02 | — / INH-1935 / INH-1936 |
| W1 | M5-03 / M5-04 | INH-1937 / INH-1938 |
| W2 | M5-05 / M5-06 | INH-1939 / INH-1940 |
| W2.5 | M8-02a | INH-1973（子集） |
| W3 | M5-07 / M5-08 | INH-1941 / INH-1942 |
| W4 | M5-09 / M5-10 / M5-11 | INH-1943 / INH-1944 / INH-1945 |
| W5 | M6-01 / M6-02 | INH-1946 / INH-1947 |
| W6 | M6-03 / M6-04 | INH-1948 / INH-1949 |
| W7 | M6-05 / M6-06 | INH-1950 / INH-1951 |
| W8 | M6-07 / M6-08 | INH-1952 / INH-1953 |
| W9 | M6-09 / M6-10 | INH-1954 / INH-1955 |
| W10 | M6-11 / M6-12 | INH-1956 / INH-1957 |
| W11 | M6-13 / M6-14 | INH-1958 / INH-1959 |
| W12 | M7-01 | INH-1960 |
| W13 | M7-02 / M7-03 | INH-1961 / INH-1962 |
| W14 | M7-04 | INH-1963 |
| W15 | M7-05 / M7-06 | INH-1964 / INH-1965 |
| W16 | M7-07 / M7-08（可延期） | INH-1966 / INH-1967 |
| W17 | M7-09 / M7-10 | INH-1968 / INH-1969 |
| W18 | M7-11 / M7-12 | INH-1970 / INH-1971 |
| W19 | M8-01 / M8-02b | INH-1972 / INH-1973 |
| W20 | M8-03 / M8-04 / M8-05 | INH-1974 / INH-1975 / INH-1976 |
| W21 | M8-06 / M8-07 | INH-1977 / INH-1978 |
