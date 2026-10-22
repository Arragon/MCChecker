# MCChecker 配置管理领域契约（M5-01 / CFG-00）

> **状态**：M5-01 交付物，v1 冻结候选。
> **上位依据**：Linear `MC-checker-v2` M5-01（INH-1935）条目 3 条验收。原始《MCChecker 新 UI 与配置生命周期管理实现方案》CFG-00 未纳入本仓库，本文按其卡片文字与 M5-02～M8-07 的引用面反推重建；**发现与原始方案冲突时以原始方案为准**，并在本文件追加"偏离记录"。
> **代码基线**：`main@3a60746`。
> **硬约束**：Python 3.9.11、零运行时第三方依赖、单进程写者、离线。本契约不引入 SQLite、消息队列、外部缓存。

---

## 1. 术语表

| 术语 | 代码名 | 含义 | **不是** |
|---|---|---|---|
| 配置族 | `ConfigFamily` | 一组业务上同一目的的参数集合（如"轴参数"） | 不是文件名，也不是机型 |
| 逻辑文件 | `LogicalFile` | 某个配置族里**身份稳定**的一份配置，跨越改名与版本 | 不是磁盘文件，也不是 `archive` 目录项 |
| 软件版本 | `SoftwareRelease` | 被测软件的发布版本（版本标签 + 显式前驱） | **不是** `data/profiles/*/archive/<name>` 下的归档目录名 |
| 文件快照 | `FileSnapshot` | 某软件版本的**某一次**具体文件字节（XML 出口或 JSON 现场） | 不是"当前文件"，不随覆盖变化 |
| 出口默认批次 | `ReleaseDefaultBatch` | 某个软件版本认可的出口 XML 默认文件集合，一次生成不可变 | 不是"最新导入的那个文件" |
| 模板变体 | `TemplateVariant` | 出口模板的结构变体（不同机型/选项导致的结构差异） | 不是软件版本；同一版本可有多个变体 |
| 参数主档 | `Parameter` | 一个参数的**稳定身份**（业务名、类别、备注） | 不是 XML 节点，也不是 JSON 键 |
| 定义修订 | `DefinitionRevision` | 某参数在**某个定义集合**下的定位、类型、单位与约束，逐版不可变 | 不是参数值的修订 |
| 定义集合 | `DefinitionSetRevision` | 某个软件版本 + 变体下，一批定义修订的**冻结**快照 | 不是"当前所有参数" |
| 基线修订 | `BaselineRevision` | 一组已确认的参数值 + 继承意图，作用域为通用/机型/设备 | 不是现场采集值 |
| 现场观测 | `Observation` | 从现场采集到的原始 JSON 字节及其解析投影，只读 | 不是基线，不能直接发布 |
| 变更单 | `ChangeSet` | 从当前基线到目标基线的受控提案，有状态机 | 不是审计日志 |
| 产物 | `Artifact` | 由已批准基线 + 模板编译出的不可变 XML 字节 | 不是模板，也不是快照 |
| 跟踪策略 | `TrackingPolicyRevision` | 某个配置类别（可再按机型收窄）采用 `archive_only` 还是 `managed`，含理由 | 不是权限，也不是开关记录之外的配置 |
| 交付记录 | `DeliveryRecord` | 某产物在某个目标上"人工导入"的事实登记 | 不是自动下发，不是设备确认 |
| 审计事件 | `AuditEvent` | 一次提交的不可变证据（谁、何时、为何、前后哈希） | 不是普通运行日志 |

**四类必须严格区分的身份**（卡面验收第 1 条）：

1. `SoftwareRelease`——软件发布版本；
2. `FileSnapshot`——文件字节归档（含 `archive_ref`）；
3. `DefinitionRevision`——参数定义修订；
4. `BaselineRevision`——基线发布。

任何代码路径**不得**用一个 `version` 字段同时表达四者。旧接口里的 `version`（归档目录名）只允许出现在 `FileSnapshot.archive_ref` 内部，且必须带 `pid + filename` 才是可复现引用。

---

## 2. 作用域与 ID

### 2.1 作用域（`scope`）

| scope | 含义 | 谁引用它 |
|---|---|---|
| `global` | 跨机型共享（配置族、软件版本、参数主档、定义修订/集合） | `/api/v1/config/*` |
| `profile` | 某个机型（`pid`） | `/api/v1/p/{pid}/config/*` |
| `model` | 机型级基线（`pid` 即机型） | 同 profile |
| `device` | 单台设备例外（仅当存在真实设备身份时启用） | 同 profile |

**规则**：请求路径的 `{pid}` 是作用域的**唯一来源**。请求体里的 `pid` 一律被忽略或拒绝（`INVALID_INPUT`）；缺 `{pid}` 时不得回落 `default`。

### 2.2 ID 规则

- 格式：`<prefix>_<6 位十进制全局序号>`，例如 `sr_000042`。
- 前缀：`cf`(family) `lf`(logical file) `sr`(software release) `fs`(file snapshot) `rb`(release batch) `tv`(template variant) `pm`(parameter) `dr`(definition revision) `ds`(definition set) `tp`(tracking policy) `bl`(baseline) `ob`(observation) `ce`(capture event) `cs`(change set) `ar`(artifact) `dl`(delivery)。
- 序号来自 `state.seq`（单调递增，持久化），**由仓储分配**，调用方不得自造。
- 实体内部修订号：`r1`、`r2`…；全局审计序号：`state.seq` 同源。
- 改名、改单位、换文件**不改变** ID。ID 一经分配永不复用（退役对象保留 ID）。

---

## 3. 对象契约

以下为**逻辑字段**；持久化形态见第 5 节。`created_at`/`updated_at` 为 UTC ISO-8601 秒级字符串。

### 3.1 `ConfigFamily`

```
id, name(唯一, 1..80), code(可选, ^[A-Za-z0-9_-]{1,40}$), notes(<=2000),
status: active|retired, created_at, updated_at
```

### 3.2 `LogicalFile`

```
id, family_id, role: xml_export|json_field, display_name,
aliases: [{name, since(iso), until(iso|null)}],   # 改名留痕
status: active|retired, created_at, updated_at
```

`aliases` 用于"按历史名称检索仍命中同一逻辑文件"。`display_name` 必须等于 `aliases` 中 `until == null` 的那一项。

### 3.3 `SoftwareRelease`

```
id, version_label(1..80, 与 build 联合唯一), build(可空字符串),
predecessor_id(可空; 显式前驱, 禁止"时间最近自动推断"),
status: draft|active|retired,
source: manual|import|archive_candidate,
notes, created_at, updated_at
```

**注意**：`version_label` **不得**由文件名或 mtime 反推后直接写入；`source=archive_candidate` 只表示"从旧归档里发现，待确认"，此时 `status=draft` 且必须带 `candidates` 说明。

### 3.4 `FileSnapshot`

```
id, release_id, logical_file_id,
kind: xml_export|json_field,
variant_id(可空; xml_export 应有),
blob_sha256(64 hex), original_filename, byte_size, captured_at,
source: archive|upload|update|manual_seed,
archive_ref: {pid, filename, archive_name} | null,   # 仅 source=archive 时非空
batch_id(可空; 出口默认批次),
provenance: {resolution: exact|ambiguous|unresolved, note}
status: candidate|confirmed|unknown
```

**规则**：`blob_sha256` 是唯一内容身份；同一 blob 可被多个快照引用。`archive_ref` 三项齐全才可复现，缺一即为 `unresolved`。

### 3.5 `ReleaseDefaultBatch`

```
id, release_id, snapshot_ids[](有序, 非空), supersedes(可空),
created_at, created_by, note
```

**不可变**：新批次只能 `supersedes` 旧批次，不得原地追加。同一 `release_id` 可有多个批次（补发），"当前默认"由 `state.pointers.release_default[release_id]` 指向。

### 3.6 `TemplateVariant`

```
id, family_id, release_id, 
structure_signature(结构指纹: 元素路径+出现次数+命名空间的 sha256),
template_snapshot_id(xml_export 快照),
status: active|retired, created_at
```

`definition_set` 与 `variant` **必须**精确绑定；不同 `structure_signature` 不得共用定义集合。

### 3.7 `Parameter`

```
id, family_id, name(1..120), category(可空), notes(<=2000),
aliases: [{name, until}], status: active|retired, created_at, updated_at
```

### 3.8 `DefinitionRevision`

```
id, parameter_id, revision_no(int, 从 1 起), 
xml: {name, loc} | null,          # loc 见 4.1
json: {key, pointer} | null,      # pointer 为 RFC 6901
type: string|bool|int|decimal|enum|raw,
unit: null|"ms"|"s"|"mm"|...,
constraints: {min, max, enum[], pattern, max_length},
remarks, definition_set_id(可空: 主档默认定义),
created_at, created_by, supersedes(可空), status: active|superseded
```

**不可变**：任何字段变化生成新 `revision_no`；旧引用（基线、定义集合）始终指向旧修订号。

### 3.9 `DefinitionSetRevision`

```
id, release_id, variant_id, 
definition_ids[](frozen, 按 parameter_id 唯一),
created_at, created_by, note
```

### 3.9a `TrackingPolicyRevision`

```
id, scope: family|model,
family_id(scope=family 必填；scope=model 时可空=该机型全域),
profile_id(scope=model 必填),
mode: archive_only|managed,
reason(非空,写审计), notes,
created_by, created_at, updated_at
```

**规则**：

- 同一 `(scope, family_id, profile_id)` 只有**一个对象**，改动追加 `r<n>` 修订，历史可查；
- 有效策略优先级：机型-类别 > 机型 > 类别 > 默认 `archive_only`（默认档与旧行为完全一致）；
- `managed` 只改变旧入口的**去向**（observation / candidate / change_source / reassociate），
  任何旧入口都不可能直接发布基线（`may_publish_baseline()` 恒假）；
- 关闭 `managed` 不删除、不隐藏任何已产生的对象与审计。

**旧接口的可见性**：特性开启时，五个旧入口（上传、URL 导入、定时下载、旧审阅提交、
删除）的响应体多出一个 `tracking: {route, mode}`；特性**未开启时不加该字段**，旧响应
逐字节不变。定时下载是批量任务，被拦的单条回 `status="blocked"` 并继续跑完整批，
不把整批判失败。

### 3.10 `BaselineRevision`

```
id, scope: common|model|device,
profile_id(scope != common 时必填), device_id(scope = device 时必填),
release_id, definition_set_id, variant_id(可空),
values: {parameter_id: {value, intent: inherit|override|pin, source_ref}},
upstream_ref: baseline_id|null,          # 通用←软件默认；机型←通用；设备←机型
previous_revision_id: baseline_id|null,  # 同作用域前修订
state: draft|submitted|approved|rejected|withdrawn|superseded,
reason, created_at, created_by, decided_at, decided_by, decide_note
```

**规则**：

- `upstream_ref` 与 `previous_revision_id` 语义不同，必须分别存放，禁止用任一顶替另一。
- `pin` 即使数值等于默认也保留意图（M6-09 验收）。
- 只有 `approved` 基线可被编译为产物；`state` 变更走变更单（M6-11/12），不直接改。

### 3.11 `Observation`

```
id, scope=profile, profile_id, logical_file_id, family_id, role,
release_ref: {release_id|null, resolution: confirmed|unconfirmed|unknown},
variant_id(可空), device_id(可空),
blob_sha256, byte_size, format: xml|json, structure_signature,
captured_at, captured_by, capture_kind: scheduled|manual|manual_import,
source_name, filename,
completeness: full|partial,
mapping: {
  entries: [{parameter_id|null, raw_key, status: mapped|unmapped|ambiguous|missing|invalid,
             value_ref, reason}],
  unresolved_keys: [], truncated: bool, definition_set_id
},
mapping_revision: 1..n,
definition_set_id, status: parsed|parse_failed, reason,
stale_capture: bool,
remapped_at(可空), remapped_by(可空)
```

**规则**：

- 观测只读；重新映射给**同一个对象**追加 `r<n>` 修订，`r1` 永远是采集那一刻的结论，
  原始字节（blob）与上一次的 `mapping` 都不被覆写。
- `missing` 不等于删除：节点不存在只说明"这次没看到值"，不是"该删掉这个参数"。
- `unmapped` 的条目 `parameter_id` 必须为 `null`（文件里有、没人认领的键）；
  同一目标被多个参数占用时**全部**标 `ambiguous`，不静默选一个。
- `invalid` = 值在，但不符合定义的**类型或单位**，此时 `value_ref` 必须为 `null`——
  不能给调用方一个"看起来能用"的值。
- `completeness=full` 要求每条 entry 都是 `mapped` 且 `unresolved_keys` 为空；
  解析失败一律 `partial`。
- `captured_at/captured_by` 与 `remapped_at/remapped_by` 分离：重新映射不冒充"重新采集过"。
- 解析失败也入库（`status=parse_failed`）：它不可能成为基线，但"现场给了错误页"本身要可追溯。

### 3.11a `CaptureEvent`

```
id, observation_id, profile_id, logical_file_id, blob_sha256,
captured_at, captured_by, capture_kind, source_name, filename,
deduplicated: bool, stale_capture: bool, status, reason
```

**规则**：每次采集**必有一条**事件，即使内容与上次完全相同（`deduplicated=true` 指向
既有观测）——"这个时间点它还是这个样子"本身是证据。事件与其指向的观测在**同一次事务**
里公布，不存在"有事件没观测"的中间态。

### 3.12 `ChangeSet`

```
id, baseline_id, base_revision_id, 
plan_ref: {algorithm: three_way|model_upgrade|device_rebase|promote, inputs:{...}, output_sha256},
changes: [{parameter_id, from, to, intent, decision: use_new|inherit_old|manual|pin|clear_pin, reason}],
state: draft|submitted|approved|rejected|withdrawn|stale,
submitted_at, submitted_by, decided_at, decided_by, decide_note,
previous_revision_id
```

`stale` 是**计算标记**：目标 head 或定义集合变化即标记，不销毁历史。

### 3.13 `Artifact`

```
id, baseline_id, variant_id, definition_set_id, template_snapshot_id,
blob_sha256, filename, byte_size, created_at, created_by,
validation: {ok: bool, checks[], warnings[]},
determinism_key: sha256(baseline_id, variant_id, definition_set_id, template_sha256)
```

`determinism_key` 相同即"同输入同生成版本"，返回已知产物（M6-14）。

### 3.14 `DeliveryRecord`

```
id, artifact_id, target: {profile_id, device_id|null},
registered_by, executed_at, result: pending|declared_ok|failed|insufficient_evidence,
evidence: {note, photo_ref|null}, created_at
```

**只有人工登记**，无自动下发通道。

### 3.15 `AuditEvent` / `OperationReceipt`

```
AuditEvent: {seq, at, actor:{ip, name}, action, object:{kind, id, revision},
             before_sha|null, after_sha|null, reason, idempotency_key|null, detail}
OperationReceipt: {idempotency_key, request_hash, seq, result_summary, at}
```

---

## 4. 定位与值

### 4.1 XML 定位（`loc`）

```
loc = {path: [{name, occurrence}], attribute: 属性名|null}
```

- `path` 从根元素下一层开始，`occurrence` 从 1 起，表示同名兄弟中的第几个；缺省 1。
- `attribute == null` 表示文本值；否则为该元素属性。
- 定位**必须**在目标 `structure_signature` 下唯一；重复即 `MAPPING_AMBIGUOUS`。
- 命名空间以 `{uri}local` 形式写在 `name` 里（与 ElementTree 一致）。

### 4.2 JSON 定位（`pointer`）

RFC 6901；`key` 是便于人读的最后一段。`~`→`~0`、`/`→`~1` 转义必须成对。

### 4.3 值模型

- 数值一律 `decimal.Decimal`，**不经过** JS `Number`；对外传输用"类型化文本"（`{"type":"decimal","text":"0.8"}`）。
- 严格区分 `missing`（键不存在）/ `null`（显式空）/ `""` / `0` / `false`。
- JSON 解析必须拒绝重复键、`NaN`、`Infinity`。
- 单位换算显式且可逆（`ms ↔ s`、`mm ↔ um`）；有损转换必须阻断。

---

## 5. 持久化契约

### 5.1 布局（决策 D2）

```
data/config_management/
  state.json                 # 唯一权威指针（原子替换）
  objects/<kind>/<id>/r<n>.json
  blobs/<aa>/<sha256>
  audit/<seq:010d>.json
  receipts/<sha256(idempotency_key)>.json
  cache/                     # 可删，非权威
```

`state.json`：

```json
{
  "schema": 1,
  "seq": 128,
  "heads": {"parameter": {"pm_000007": "r3"}},
  "pointers": {"release_default": {"sr_000002": "rb_000011"}},
  "created_at": "...", "updated_at": "..."
}
```

### 5.2 提交协议（M5-04）

1. `write_gate.write()` → `management_lock`（顺序固定，反向即死锁）；
2. 重读 `state.json`，核验 `expected_revision`（CAS）；不符 → `REVISION_CONFLICT`；
3. 幂等键查重：同键同 `request_hash` → 直接回放旧回执；同键异体 → `IDEMPOTENCY_MISMATCH`；
4. 写 blob（内容寻址，已存在则跳过）→ 写对象修订 → 写审计事件 → 写回执；
5. **最后一次**原子替换 `state.json`（新 `seq` + 新 heads + 新 pointers）；
6. 任一步失败：不替换 state，已写对象成为不可达孤儿（允许，可被一致性检查列出）。

**半发布禁止**：`state.json` 未替换成功时，新对象一律不可见。

### 5.3 损坏处理

`state.json` 不可解析、引用对象缺失、blob 哈希不符 → `CorruptDataError`（503），进入只读保护；**禁止**当作空状态继续写入。

---

## 6. API 契约

| 方法 | 路径 | 作用域 | 说明 |
|---|---|---|---|
| GET | `/api/v1/config/revision` | global | 配置域全局修订号（`{rev, seq}`） |
| GET | `/api/v1/config/capabilities` | global | 当前动作能力矩阵 |
| GET | `/api/v1/config/families` | global | 配置族列表 |
| GET/POST | `/api/v1/config/releases` | global | 软件版本 |
| GET | `/api/v1/config/releases/{id}` | global | 版本详情（含默认批次） |
| GET/POST | `/api/v1/config/parameters` | global | 参数主档（四种检索维度） |
| GET/POST | `/api/v1/config/parameters/{id}/definitions` | global | 定义修订 |
| GET/POST | `/api/v1/config/definition-sets` | global | 定义集合 |
| GET | `/api/v1/p/{pid}/config/status` | profile | 机型管理域状态 |
| GET/POST | `/api/v1/p/{pid}/config/baselines` | profile | 基线 |
| GET | `/api/v1/p/{pid}/config/observations` | profile | 现场观测 |
| GET/POST | `/api/v1/p/{pid}/config/changes` | profile | 变更单 |
| POST | `/api/v1/p/{pid}/config/changes/{id}/submit` | profile | 提交 |
| POST | `/api/v1/p/{pid}/config/changes/{id}/approve` | profile | 批准并发布 |
| GET | `/api/v1/p/{pid}/config/artifacts` | profile | 产物 |
| GET | `/api/v1/p/{pid}/config/artifacts/{id}/raw` | profile | 下载 |
| GET | `/api/v1/p/{pid}/config/audit` | profile | 审计检索 |

**通用约定**：写请求必须带 `X-Requested-With: MCChecker`（`http.py` 既有）；可选 `Idempotency-Key`、`If-Match`（`expected_revision`）。分页 `?cursor=&limit=`（limit 默认 50，上限 200）。

### 6.1 错误码

| 码 | HTTP | 触发 |
|---|---|---|
| `CONFIG_DISABLED` | 503 | 特性门禁未开 |
| `PLAN_STALE` | 409 | 计划基于的 head/定义集合已变 |
| `POLICY_BLOCKED` | 403 | 追踪策略禁止该动作 |
| `DEFINITION_CONFLICT` | 409 | 定义定位/类型冲突 |
| `MAPPING_AMBIGUOUS` | 422 | 定位不唯一 |
| `VALUE_OUT_OF_RANGE` | 422 | 越界 |
| `VALUE_LOSSY_CONVERSION` | 422 | 有损单位转换 |
| `OBSERVATION_UNKNOWN_RELEASE` | 422 | 未知版本禁止发布 |
| `ARTIFACT_TEMPLATE_INCOMPATIBLE` | 422 | 模板与定义集合/变体不匹配 |
| `IDEMPOTENCY_MISMATCH` | 409 | 同键异体 |

---

## 7. 动作与能力

| action | 最小角色 | 能力键 |
|---|---|---|
| `config_read` | 所有（含访客） | `config_read` |
| `config_registry_write` | 管理员 | `config_registry_write` |
| `config_tracking_admin` | 管理员 | `config_tracking_admin` |
| `config_change_submit` | 管理员 | `config_change_submit` |
| `config_change_approve` | 管理员 | `config_change_approve` |
| `config_artifact_generate` | 管理员 | `config_artifact_generate` |

**偏离记录 D4**：`8a1d8ba` 已取消部署者身份（`is_deployer` 恒 False）。本契约因此**不引入**"部署者"档：批准权只与管理员绑定，且**不可**通过"本机访问"获得。M5-07 的验收措辞按此口径解释——"部署者不因访问配置页自动获得批准权"在实现上等价于"是否存在回环特权"这一命题的否定式：当前不存在任何回环特权。

---

## 8. 新旧兼容

- 旧 `data/profiles/*` 结构与旧 JSON 格式**零改动**；配置域只写 `data/config_management/`。
- 旧接口（`/api/v1/p/{pid}/files`、`/document`、`/raw`、`/review`、`/dl`、`/search`…）行为、错误码、修订号语义不变。
- 旧"归档版本"只作为 `FileSnapshot.archive_ref` 的取值来源之一；**归档目录名 ≠ 软件版本号**。
- 配置域新端点未接入前端时保持 `off`，不得让旧页面出现不可用入口。

---

## 9. 模块依赖方向

分层不变：`server` → `services` → `core`，**`core` 不得 import services 或 server**。

| 层 | 新增模块 | 只允许依赖 |
|---|---|---|
| `core` | `config_repository.py`、`config_values.py`、`config_mapping.py`、`config_merge.py` | `core.*`（`fsutil`、`locks`、`errors`、`parser`）+ 标准库 |
| `services` | `config_*.py` | `core.*`、同层 `authz`/`revision`/`context` |
| `server` | `api_config_*.py` | `services.*`、`core.errors`、`http`/`router` |

三条附加规则：

1. `handler` 不直接读写业务文件，全部经 `services`；
2. `core` 内的纯算法（`config_merge`、`config_values`）**不做 I/O**，便于表驱动测试；
3. `config_repository` 与旧 `storage` **互不 import**（决策 D1）：旧 `data/profiles/*` 与新 `data/config_management/` 各自只有一个写者模块。
