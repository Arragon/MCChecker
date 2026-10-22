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
id, profile_id, baseline_id, baseline_revision, baseline_scope, definition_set_id,
logical_file_id, observation_id, title, reason,
plan_ref: {algorithm: reconcile|three_way|model_upgrade|device_rebase|promote,
           inputs: {baseline_id, baseline_revision, definition_set_id,
                    logical_file_id, observation_id},
           output_sha256},
changes: [{parameter_id, from, from_intent, to, to_intent,
           decision: use_new|inherit_old|manual|pin|clear_pin,
           reason, writes_value}],
state: draft|submitted|approved|rejected|withdrawn,
frozen_at, submitted_at, submitted_by, decided_at, decided_by, decide_note, decided_state,
published_baseline_revision, previous_revision_id, reopened_from,
created_by, created_at, updated_at, updated_by
```

**状态机只有五条边**（`UNKNOWN` 之外的组合一律 `CHANGE_STATE_INVALID` 409）：

| 从 | 到 | 谁 | 附带要求 |
|---|---|---|---|
| `draft` | `submitted` | `config_change_submit` | 必须写明理由；提交时**重新算过期**，stale 即 `PLAN_STALE` |
| `draft` | `withdrawn` | 同上 | 必填撤回理由 |
| `submitted` | `approved` | `config_change_approve` | 见 §6.3，批准即发布 |
| `submitted` | `rejected` | 同上 | 必填驳回意见 |
| `submitted` | `withdrawn` | `config_change_submit` | 必填理由 |
| `rejected` / `withdrawn` | `draft` | `config_change_submit` | **新建对象**，`reopened_from` 指回原单 |

`stale` 是**计算标记，不是状态**：目标基线 head 或定义集合变化即标记，不销毁历史。
只对 `draft`/`submitted` 有意义——终态单的父修订号与当前 head 不同是历史的必然，
不是"过期"。`staleness()` 返回 `{stale, applicable, reasons, baseline_head, baseline_revision}`，
`reasons ∈ {baseline_missing, baseline_revision_changed, definition_set_changed, baseline_superseded}`。

**`from` 一律由服务端取基线现值**：客户端传的 before 视同噪声。否则"改了什么"由提交方
自己说了算，审阅的人看不到真差异。

**重开是新建对象，不是把终态改回 draft**：否则"谁在什么时候批准/驳回过什么"再也说不清。

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
| GET/POST | `/api/v1/config/baselines` | global | 基线（`?profile_id=&scope=&state=` 过滤；**对象带 `profile_id`**） |
| GET | `/api/v1/config/baselines/{id}` | global | 基线详情（`?revision=rN` 可读历史修订） |
| POST | `/api/v1/config/baselines/{id}/values` | global | 改草稿值（仅 `draft`） |
| POST | `/api/v1/config/baselines/{id}/intent` | global | 改 `inherit`/`override`/`pin` 意图 |
| GET/POST | `/api/v1/config/observations` | global | 现场观测（`?profile_id=&logical_file_id=` 过滤） |
| GET | `/api/v1/config/observations/{id}` | global | 观测详情（含逐项映射状态） |
| GET | `/api/v1/config/observations/{id}/raw` | global | 原始字节下载（`Content-Disposition`） |
| POST | `/api/v1/config/observations/{id}/remap` | global | 重新映射（追加修订，`r1` 不变） |
| GET | `/api/v1/config/ingest/summary` | global | 采集概览（最近一次、按状态计数） |
| GET/POST | `/api/v1/config/tracking` | global | 追踪策略（`archive_only`/`managed`） |
| GET | `/api/v1/config/tracking/effective` | global | 有效策略 + 继承链 |
| GET | `/api/v1/config/tracking/{id}/history` | global | 策略修订历史 |
| GET | `/api/v1/p/{pid}/config/reconcile` | profile | 文件内三栏对账（§6.2） |
| GET/POST | `/api/v1/p/{pid}/config/changes` | profile | 变更单列表 / 建草稿 |
| GET/POST | `/api/v1/p/{pid}/config/changes/{id}` | profile | 详情 / 改草稿（追加修订） |
| POST | `/api/v1/p/{pid}/config/changes/{id}/submit` | profile | 提交（冻结） |
| POST | `/api/v1/p/{pid}/config/changes/{id}/withdraw` | profile | 撤回（终态） |
| POST | `/api/v1/p/{pid}/config/changes/{id}/reopen` | profile | 重开一版草稿 |
| POST | `/api/v1/p/{pid}/config/changes/{id}/approve` | profile | 批准并发布（`config_change_approve`） |
| POST | `/api/v1/p/{pid}/config/changes/{id}/reject` | profile | 驳回（同上） |
| GET/POST | `/api/v1/p/{pid}/config/artifacts` | profile | 产物列表 / 生成（`?baseline_id=` 过滤；复用 200 / 新建 201） |
| GET | `/api/v1/p/{pid}/config/artifacts/{id}` | profile | 产物详情（含 `edits`/`validation`/`delivery_status`） |
| GET | `/api/v1/p/{pid}/config/artifacts/{id}/raw` | profile | 下载（`X-Content-Sha256` + `ETag`，**不重生成**） |
| GET/POST | `/api/v1/config/upgrade/common` | global | 通用层升版计划 / 按决议建草稿 |
| GET/POST | `/api/v1/p/{pid}/config/upgrade/model` | profile | 机型跨版本升级计划 / 落地 |
| GET/POST | `/api/v1/p/{pid}/config/upgrade/rebase` | profile | 同版本刷新计划 / 落地 |
| GET | `/api/v1/p/{pid}/config/audit` | profile | 审计检索 |

**路径形态的选择**：基线与观测是**全局对象**（携带 `profile_id` 字段、共用全局修订号），
所以接口放在 `/api/v1/config/*` 并用查询参数过滤，而不是嵌进 `/p/{pid}/` 前缀。
只有"以机型为坐标系"的读模型（`status`、`reconcile`、`changes`、`audit`）才用 profile 前缀。

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
| `CHANGE_STATE_INVALID` | 409 | 状态机不允许的流转（`detail.allowed` 给出可走的边） |

未知单位没有独立错误码：`INVALID_INPUT` + 消息 `未知单位：<unit>`。区分点在于
**"能不能换算"与"合不合法"是两件事**——`mm/s`、`rpm`、`%` 是合法声明但换不了，
`TypedValue.from_json` 放行、`convert()` 抛错；`parse_with_unit()` 保留声明单位。

### 6.2 文件内对账（`GET /p/{pid}/config/reconcile`）

查询参数：`logical_file_id` 或 `filename`（二选一，前者优先）、`baseline_id`、
`observation_id`。缺省时观测取该逻辑文件的**当前观测**，基线取"机型级 approved 优先、
其次通用 approved"。

响应（节选）：

```json
{
  "logical_file": {"id": "lf_1", "display_name": "轴参数", "role": "observation", "family_id": "f1"},
  "observation": {"id": "ob_1", "captured_at": "...", "status": "parsed", "completeness": "full",
                  "release_ref": {...}, "unresolved_keys": [], "mapping_revision": "r1"},
  "baseline": {"id": "bl_1", "scope": "model", "state": "approved", "revision": "r3"},
  "rows": [{
    "parameter_id": "pm_1", "name": "最大速度", "category": "运动", "unit": "m/s", "known": true,
    "observed": {"status": "mapped", "value": {"type": "decimal", "text": "1.2", "unit": "m/s"},
                 "raw_key": "MAX_SPEED", "reason": null, "observation_id": "ob_1", "captured_at": "..."},
    "baseline": {"value": {"type": "decimal", "text": "1", "unit": "m/s"}, "baseline_id": "bl_1",
                 "state": "approved", "intent": "inherit"},
    "proposed": {"value": {"type": "decimal", "text": "1.2", "unit": "m/s"}, "source": "observed"},
    "diff": "changed"
  }],
  "counts": {"same": 0, "changed": 1, "missing_observed": 0, "missing_baseline": 0,
             "missing_both": 0, "unknown": 0},
  "stale_observation": false,
  "newer_observation_id": null,
  "has_baseline": true,
  "has_observation": true
}
```

四条硬性约定：

1. **`diff` 六态**：`same` / `changed` / `missing_observed`（基线有、现场没采到）/
   `missing_baseline`（现场有、基线没覆盖）/ `missing_both`（谁都没说，**不等于一致**）/
   `unknown`（`unmapped`/`ambiguous`/`invalid`）。
2. **`proposed` 只是建议**，`source ∈ {observed, baseline, none}`；真正提交要走变更单（M6-11）。
3. **`stale_observation` 用对象身份判定**，不用时间戳——同一秒两次采集时间戳会打平，
   但"当前那条"确实换人了。前端据此提示，**不得静默替换**。
4. 只接受 `state=approved` 的基线作为基准；传了草稿基线一律 `INVALID_INPUT`（`field="baseline_id"`）。

**前端契约**（`MC.configUi`，`views/registry.js`）：`valueText` / `reconcileRow` /
`reconcileGroups` / `reconcileSummary` / `reconcileNotices` / `applyDraft` /
`pendingChanges`。草稿只存 `{note, edits, adopt, filter}` 于内存（`MC.configApi.drafts`），
**不落 localStorage，更不存整份 XML/JSON**；待提交行的判据是"勾了采纳 ∧ 有值 ∧ 与基线不同"。

### 6.3 批准即发布（`POST /p/{pid}/config/changes/{cid}/approve`）

批准在一个事务里同时写**新基线修订**与**变更单决议**，不存在 approved-but-unpublished
中间态。批准前按顺序重新校验六项：

1. 权限 —— `config_change_approve`（与 `config_change_submit` 是两个键，见 §7）；
2. 状态 —— 必须 `submitted`，否则 `CHANGE_STATE_INVALID`；
3. 目标 head —— `expected_baseline_revision` / 冻结的 `baseline_revision` 与当前 head 不一致即 `PLAN_STALE`；
4. 定义集合 —— 与冻结值不一致即 `PLAN_STALE`（`reasons=["definition_set_changed"]`）；
5. 参数范围 —— 逐项按定义集合的上下界/枚举复核，越界 `VALUE_OUT_OF_RANGE`（`detail.parameter_id`）；
6. 未决冲突 —— 行上有 `unresolved` 即拒绝。

**为什么批准要重算**：创建时的校验会过期。批准这一下才是真正生效的那次。

并发双批准：第二个请求进状态机时对象已是 `approved`，直接 `CHANGE_STATE_INVALID`，
基线不会被发布两次。CAS（`expected` 同时钉住 `change_set:{id}` 与 `baseline:{id}`）兜住
"两个管理员同时点"的窗口。

**`published_baseline_revision` 是预先算出的 `n+1`**：`Ref.revision` 要到 commit 才回填，
而载荷在 commit 时已写盘；因为 `expected` 把基线 head CAS 死在旧修订上，下一个修订号
只可能是 `n+1`。提交后立即回读校验，对不上就抛 `STORAGE_FAILURE`（宁可显式失败，
也不要悄悄记错修订号）。

**可见性**：通用层变更（`baseline.profile_id` 为空）在任一 `{pid}` 路径下可见可操作——
它一改就影响所有继承它的机型；机型层变更只在自己机型下可见。路径只是坐标，权限仍是
管理员那一层。

**前端契约**（`MC.changesUi`，`views/changes.js`，挂在审阅页的「配置变更」分段）：
`stateLabel` / `stateTone` / `changedCount` / `summary` / `groupByState` / `rowModel` /
`approveBlockers` / `canApprove`。按钮禁用只是体验，服务端仍会 403；`409` 类错误一律
提示 + 刷新，不停在旧状态。

### 6.4 目标文件生成与交付产物（M6-13 / M6-14）

**生成**（`config_export.generate`，只在服务端内部调用）：以**真实模板快照**为骨架，
只对批准位置做 `apply_edits`，绝不重排/重格式化整份文档。四条硬性约束：

| 约束 | 做法 | 失败时 |
|---|---|---|
| 只改批准位置 | 逐条按 `xml_loc` 定位后改写 | 定位不到 → `ARTIFACT_TEMPLATE_INCOMPATIBLE`（`loc`/`parameter_id`） |
| 结构不变 | 生成后重新解析并与模板比对 `structure_signature` | 签名不一致 → 显式失败，不落盘 |
| 落点值正确 | 逐落点读回比对目标值 | 不一致 → 显式失败 |
| 不宣称逐字节保真 | `validation.warnings` 固定带这句话 | —— |

no-op（没有任何改动）时**直接字节拷贝模板**，不经解析器重写——重写会改掉缩进与
自闭合形态，"没改却变了字节"是最难解释的一类差异。

定位串用**带命名空间花括号**的紧凑路径（`compact_path`），与 `parser.to_compact`
同一口径：`apply_edits` 认的就是这个字符串。第一段是根元素自身，读值时跳过。

**产物**（`config_artifacts`）：

- **不可变**：生成一次即 `repo.put_blob(content)` 落成内容寻址 blob；`GET .../raw`
  只 `read_blob`，**绝不重生成**。改值要走新的变更单 → 新的基线修订 → 新的产物。
- **同输入同产物**：`determinism_key = sha256("config_export/v1|baseline_id|baseline_revision|variant_id|definition_set_id|template_sha256")`。
  命中即返回既有产物（HTTP 200，`reused=true`），不新建对象。键里**必须**带基线修订号，
  否则同一基线改了一版值还会命中旧产物。
- **生成即自证**：`validation`（`checks` + `warnings`）随产物一起存，下载方与审计方
  都能看到"这次生成做了什么检查"。
- **生成权限独立**：`config_artifact_generate`（与提交/批准分开），前台按钮禁用只是体验。

**已知缺口 D6**：没有真实系统的 XML 样本，"真实导入并记录操作者/环境/结果"这一步
做不了，因此 `delivery_status` 恒为 `unverified`。前端在交付分段顶部明写这段缺口
（`MC.deliveryUi.deliveryNotice`），**下载成功不等于验过**。D6 解除前 M6-14 不关闭。

**前端契约**（`MC.deliveryUi`，`views/delivery.js`，挂在文件页配置管理下方的交付分段）：
`statusLabel` / `statusTone` / `statusHint` / `shortSha` / `editCount` / `rowModel` /
`canGenerate` / `baselineOptions` / `deliveryNotice`。未知状态一律按最保守的 `unverified`
呈现——把不认识的值显示成"已核验"是最坏的一种乐观。

### 6.5 跨版本升级（M7-04）

六个入口成对出现：`GET` 出计划（只读、幂等），`POST` 按决议建**草稿**。计划与落地的
权限键不同（`config_read` / `config_change_submit`）；落地产物是基线草稿，发布仍走
变更单（§6.3 的"批准即发布"）。

**三方是谁**（合并算法见 `core/config_merge.py`，纯函数）：

| 模式 | O 旧共同基准 | L 旧本地值 | N 新上游 |
|---|---|---|---|
| `common_release_upgrade` | 旧版本默认出口批次解析值 | 旧版本已批准通用基线 | 新版本默认出口批次 |
| `model_release_upgrade` | 机型基线引用的 `upstream_ref` + `upstream_revision` | 旧机型基线 | 新版本**已批准**通用基线 |
| `model_rebase` | 同上 | 旧机型基线 | 同一条通用基线的**新修订** |

**三个不许**：

1. **不随机选最新**：目标版本有多个模板变体时 `400`，`detail.candidates` 列全部候选。
   跨版本靠 `variant.family_id` 认"同一个模板"。
2. **不漂移共同基准**：O 取机型基线记录的 `upstream_revision`，不是上游 head。
   只存 `upstream_ref`（id）的话，通用层一发新修订，O 就等于 N，合并退化成"全都没变"。
3. **不自动传播**：通用层升版不触碰任何机型基线；机型必须显式调 `/upgrade/*` 才产生新对象。

**响应形状**（前端只摆位置，不自己算）：

```json
{
  "mode": "common_release_upgrade",
  "target": {"release_id": "...", "variant_id": "...", "definition_set_id": "...", "batch_id": "..."},
  "previous": {"release_id": "...", "baseline_id": "...", "baseline_revision": "r2"},
  "sources": {
    "base":   {"kind": "release_default", "present": true, "batch_id": "...", "snapshots": [{"sha256": "..."}]},
    "ours":   {"kind": "baseline", "present": true, "baseline_id": "...", "revision": "r2"},
    "theirs": {"kind": "release_default", "present": true, "batch_id": "...", "snapshots": [{"sha256": "..."}]}
  },
  "three_way": true,
  "items": [{"parameter_id": "...", "action": "conflict", "decision": null,
             "allowed": ["use_new", "inherit_old", "manual", "pin", "clear_pin"],
             "base": {...}, "ours": {...}, "theirs": {...}, "candidate": {...},
             "reasons": ["both_sides_changed"], "blockers": [], "evidence": []}],
  "blockers": [], "warnings": [], "needs_decision": ["..."], "manual": [],
  "can_apply": false, "publishable": false
}
```

`publishable` **恒为 false**：升级接口从不发布。`three_way=false`（无共同基准）时
`items` 全部 `reasons=["no_previous_source"]`，前端必须禁用"一键合并"，只能另走新建。

**`POST` 的请求体**：`{release_id, variant_id?, decisions: {parameter_id: decision}, expected?, reason?}`。

* `decisions` 缺项且该行是冲突/需人工 → `400`，`detail.pending` 点名；
* 需人工项（拆分/合并、目标已退役却有本地 override 等）必须显式 `drop` 才放行；
* `expected` 与计划不一致（`model_revision` / `parent_revision` / `previous_baseline_revision`）
  → `409 PLAN_STALE`，`detail.drift` 给期望值与实际值；
* 落地成功 `201`，返回 `{item, plan, applied}`，基线带 `upgrade_provenance`。

**`upgrade_provenance`** 记三方来源（含每个快照的 SHA-256）、逐项决议、换算证据与
`dropped` 列表——审计能回答"这一版为什么这么合"。

**前端契约**（`MC.upgradeUi`，`views/upgrade.js`，审阅页「版本升级」分段）：
`classify` / `matchFilter` / `countByFilter` / `rowModel` / `effectiveDecision` /
`bulkResolve` / `pendingOf` / `canApply` / `summaryText`。两条界面规矩：
**不以颜色替代文本**（每个状态都有字），**无共同基准不许一键合并**。

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
