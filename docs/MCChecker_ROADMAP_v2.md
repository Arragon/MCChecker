# MCChecker 开发路线图

**状态：Canonical Execution Roadmap（完整版）**\
**版本：2026-09-12 二次架构评审修订版**\
**代码基准：`v1.0.1`**\
**基准说明：当前 `v1.0.1` 在二次评审开始时的 HEAD 为
`abe83cb20764d6c63a623078d7d3ec60fd4a7fe8`；该提交相对运行代码审查提交
`e47e54a01f0ad6d74db3a08670a1034f937575c2` 仅新增两份 v2
文档，因此本文中的运行代码事实仍以同一套源码为依据。**\
**架构设计：`docs/MCChecker_ARCHITECTURE.md`**\
**Linear Project：MCChecker / Inhandy**

> 本文件是 MCChecker 的正式开发计划与任务定义源。Linear
> 用于反映执行状态与真实 blocking/blockedBy；`MCChecker_ROADMAP_v2.md`
> 为历史审查版本，不再作为当前执行计划。Issue 创建、文档更新或 Milestone
> 建立均不代表功能完成。

------------------------------------------------------------------------

# 1. 目标与裁决

MCChecker 当前已经形成一个实际可用的内网配置检查工具：具备 XML/JSON
查看、搜索、收藏、版本归档、四类对比、修改记录、审阅生成、多机型、定时更新以及
DL 快捷计算。下一阶段不应 Rewrite，也不应为未来未知需求搭建平台。

整改目标只有四个：

1.  **正确性**：同一输入得到正确、可解释的结果；审阅、diff、DL 不误导。
2.  **数据安全**：任何失败、并发、升级、删除和测试都不能静默损伤既有权威数据。
3.  **可维护性**：新增相似功能不再复制授权、profile、保存、错误、树渲染和任务逻辑。
4.  **可部署性**：单进程内网部署可复现，完全断公网仍能完成核心使用。

目标架构保持：

``` text
Python + NiceGUI + APScheduler
        +
Local files / JSON
        +
Modular Monolith
```

明确不做：

-   NiceGUI → React/Vue Rewrite
-   微服务
-   Redis / Celery
-   通用插件系统
-   工作流引擎
-   DI Container / Repository Framework
-   无证据的 SQLite/数据库迁移
-   全量 REST 化
-   Dashboard 化重设计
-   零停机热更新
-   无业务授权的自动历史清理

------------------------------------------------------------------------

# 2. 优先级

  ------------------------------------------------------------------------------------------------------------------
  优先级                              定义
  ----------------------------------- ------------------------------------------------------------------------------
  P0                                  影响正确性、数据安全、稳定性、核心兼容、离线部署，或开发过程可能触碰真实数据

  P1                                  明显影响核心体验、维护成本、长期可靠性和近期扩展

  P2                                  有明确收益，但不阻塞成熟可用版本

  P3                                  尚未验证的未来能力，仅记录触发条件，不创建当前开发 Issue
  ------------------------------------------------------------------------------------------------------------------

Linear 映射：

-   P0 → Urgent
-   P1 → High
-   P2 → Medium
-   P3 → Deferred，不创建执行 Issue

------------------------------------------------------------------------

# 3. 执行原则

## 3.1 修正确认问题，而不是先搭框架

每个架构变化都必须回答：

1.  当前真实问题是什么？
2.  不改会产生什么实际成本？
3.  最小可行整改是什么？

能在原文件安全修复的 P0 不等待目录整理。

## 3.2 兼容已有数据，不兼容错误行为

应保持兼容：

-   profile ID 与已有机型数据
-   configs / archive / records 原文
-   config mapping
-   favorites / bindings
-   edit remarks
-   schedule
-   DL 参数
-   IP / admin 数据
-   旧下载链接，在安全可解析时

不要求保留：

-   未授权管理写入
-   路径穿越
-   任意 URL 服务端读取
-   错误 DL 数值
-   误改审阅节点
-   静默丢 XML 字段
-   损坏 JSON 被视为空数据继续覆盖

## 3.3 测试先安全，再谈覆盖率

T13 是 P0。任何会运行现有 pytest 的开发工作都不得先让测试触碰真实
`data/` 或 `.nicegui/`。

## 3.4 依赖表示"最终完成前置"，不表示禁止提前做局部修复

例如：

-   T12 的端口修复和 wheelhouse 可以立即进行。
-   T05 的恢复清单可以在 T04 原子 I/O 完整完成前先设计和测试纯逻辑。
-   T16 的历史版本参数 bug 可以先修，再提取共用树。

------------------------------------------------------------------------

# 4. Milestone 总览

## M1 · 修复正确性、数据安全与离线发布

**目标：** 消除全部确认 P0，同时建立后续开发不会损伤真实数据的基础。

  Task   Linear      Priority 交付对象
  ------ --------- ---------- -------------------------------------------
  T01    INH-612           P0 授权与显式 profile
  T02    INH-613           P0 文件、上传、下载源安全边界
  T03    INH-615           P0 HTML / JavaScript 注入边界
  T04    INH-616           P0 原子持久化、并发保护、WriteGate
  T05    INH-617           P0 迁移、改名、删除、备份恢复
  T06    INH-618           P0 SourceSnapshot / ParsedDocument / NodeRef
  T07    INH-619           P0 审阅版本校验与保真输出
  T08    INH-620           P0 FileRef 与临时/历史/记录生命周期
  T09    INH-621           P0 更新验证、手动刷新、异步任务
  T10    INH-622           P0 解析与派生 diff 缓存一致性
  T11    INH-623           P0 DL 数值输入、对应与求解语义
  T12    INH-624           P0 可复现完全离线发布
  T13    INH-625           P0 测试数据隔离与回归安全门槛

**M1 Gate：**

-   测试不会触碰真实 data/.nicegui。
-   非授权写入失败且原文件不变。
-   profile/default/后台任务无串数据。
-   配置/JSON 写失败后旧数据仍可用。
-   迁移/改名/删除可以确定恢复。
-   大 XML 不再为性能丢语义字段。
-   审阅不能误改节点，不静默丢字段。
-   临时文件不冒充持久文件。
-   错误更新源不能替换当前有效配置。
-   cache 不会向用户返回旧版本结果。
-   DL 结果与算法保证一致。
-   冷浏览器 + 禁公网可安装、启动和加载核心资源。

------------------------------------------------------------------------

## M2 · 明确应用边界与维护契约

  Task   Linear      Priority
  ------ --------- ----------
  T14    INH-626           P1

**目标：** 把 M1
中已经验证的真实业务流程沉淀成少量应用操作、错误和日志契约，并统一架构文档事实源。

不做：

-   全量 Service 化
-   Repository
-   DI
-   Command Bus
-   Plugin Framework

------------------------------------------------------------------------

## M3 · 优化核心工作流与信息密度

  Task   Linear      Priority
  ------ --------- ----------
  T15    INH-627           P1
  T16    INH-628           P1
  T17    INH-629           P1

**目标：** 让文件、版本、搜索、审阅、DL 和管理操作连续、明确、可恢复。

------------------------------------------------------------------------

## M4 · 验证性能与发布可靠性

  Task   Linear      Priority
  ------ --------- ----------
  T18    INH-630           P1
  T19    INH-631           P1

**目标：**
先测量，再优化；最终完成旧数据升级、完整浏览器闭环、断网安装、备份与恢复演练。

------------------------------------------------------------------------

## M5 · 收敛非必要视觉样式

  Task   Linear      Priority
  ------ --------- ----------
  T20    INH-632           P2

**目标：** 在核心版本可发布后收敛视觉噪音，不反向阻塞 P0/P1。

------------------------------------------------------------------------

# 5. 真实依赖图

## 5.1 精确任务依赖

  Task   前置任务
  ------ ----------------------------------------
  T01    无
  T02    无
  T03    无
  T04    无
  T05    T01, T02, T04
  T06    无
  T07    T01, T04, T06
  T08    T01, T02
  T09    T01, T02, T04, T06
  T10    T04, T06
  T11    无
  T12    无
  T13    无
  T14    T01, T04, T06, T07, T09, T13
  T15    T01, T08, T09, T14
  T16    T03, T06, T08, T14, T15
  T17    T07, T09, T11, T14, T15
  T18    T09, T10, T16, T17
  T19    T05, T12, T13, T14, T15, T16, T17, T18
  T20    T19

## 5.2 推荐并行工作线

``` text
测试安全线:        T13 ───────────────────────────────┐
授权安全线:        T01 ─┬─ T05 ──────────────────────┤
路径/网络线:       T02 ─┘   ├─ T08 ─┐                │
持久化线:          T04 ──────┤       ├─ T14 ─────────┤
解析正确性线:      T06 ─┬─ T07 ─────┘                │
                         ├─ T09 ──────────────────────┤
                         └─ T10 ──────────────────────┤
浏览器注入线:      T03 ────────────────────┐          │
DL 线:             T11 ────────────────────┼─ T17 ───┤
离线发行线:        T12 ───────────────────────────────┤
                                              T15/T16 │
                                                  ↓   │
                                                 T18  │
                                                  ↓   │
                                                 T19 ◄┘
                                                  ↓
                                                 T20
```

------------------------------------------------------------------------

# 6. M1 任务明细

## T01 · 统一写操作授权并显式传递机型上下文

**Linear：INH-612**\
**Priority：P0 / Urgent**

### Problem

当前身份依赖客户端 IP；机型新增/删除/改名、更新设置、DL
生效配置等入口的授权检查不统一，有些只在打开 UI 时判断。`storage` 使用
`ContextVar`，不是普通共享全局变量，但 `get_active_profile()` 在上下文为
`default` 时仍读取 NiceGUI `app.storage.user["device_model"]`，因此显式
`use_profile("default")` 可能被浏览器当前机型覆盖。非法 profile ID
又被静默归一到 default。

另一个容易忽略的事实：`ip_mapping.json` 和 `admin_users.json`
当前实际存放在各 profile 内，因此身份/管理员语义也受 active profile
影响。整改不得未经产品确认把它们偷偷改成全局身份表。

### Scope

覆盖：

-   persistent write
-   device model 管理
-   update source / schedule 管理
-   DL 生效参数
-   review / delete
-   后台任务
-   profile 解析
-   受信代理来源

不覆盖：

-   登录平台
-   OAuth / SSO
-   通用 RBAC
-   部门/租户系统

### Implementation Notes

最小方案：

``` text
UI / HTTP
  ↓
resolve Actor
resolve explicit profile_id
  ↓
authorize(actor, action, profile_id)
  ↓
application operation
```

要求：

-   提交时重新授权，不能只依赖按钮是否显示。
-   `default` 显式指定后不得再读取浏览器机型。
-   未知 profile 返回明确错误，不回落 default。
-   后台任务创建时固定 profile，使用 system actor。
-   core/storage 不再反向读取 NiceGUI user state。
-   直连默认只使用真实 peer address。
-   只有配置了 trusted proxy 才接受 forwarded client identity。
-   保留当前 profile-scoped IP/admin 数据语义。

### Acceptance Criteria

-   访客直接调用管理写入口失败且相关文件 hash 不变。
-   管理员撤权后已经打开的对话框提交仍失败。
-   default 与非 default 双会话交错操作不串数据。
-   浏览器会话 + scheduler 同时运行不串 profile。
-   未知 profile 不读取 default。
-   反向代理部署不会把所有客户端误认成 deployer。
-   原 IP/admin 表继续可读，不发生无授权迁移。

### Dependencies

无。

### Definition of Done

权限矩阵、显式 profile、system
actor、代理识别、旧数据兼容和双会话测试全部完成。

### Review Boundary

建议拆两组 PR：

1.  补权限漏点、提交再校验。
2.  显式 profile/Actor 与兼容包装。

不要和身份体系重构绑定。

------------------------------------------------------------------------

## T02 · 收紧文件、上传与下载源输入边界

**Linear：INH-613**\
**Priority：P0 / Urgent**

### Problem

当前配置路径校验不统一；`get_config_path()`、archive
等调用链仍可能接收未经统一验证的名称。URL 下载使用服务端
`urllib`，缺少统一的
scheme、重定向、DNS/目标、大小与耗时预算。上传和对比文件也可能一次性读入内存，没有统一容量边界。

更新 URL / record URL 可能包含 userinfo 或 query token；若完整出现在 UI
或日志，会形成额外泄漏面。

### Scope

-   upload
-   comparison upload
-   temporary URL import
-   update URL
-   record URL
-   current/archive/record path
-   redirect
-   response budget
-   parse node/depth budget

### Implementation Notes

统一两个基础边界：

``` text
validate_filename()
resolve_within(root, relative)
```

必须覆盖：

-   `../`
-   `\`
-   absolute path
-   Windows drive
-   UNC
-   ADS
-   NUL
-   symlink escape
-   安全 Unicode 文件名

网络读取：

-   只允许 `http/https`。
-   每次 redirect 都重新验证。
-   访客临时 URL 导入采用更严格允许策略。
-   deployer 已配置的内网业务源可以按明确 host/subnet policy 放行。
-   默认拒绝未批准 loopback、link-local、metadata endpoint。
-   分块读取。
-   总字节限制。
-   总耗时/连接超时。
-   响应 Content-Type 只作为提示，最终以内容解析为准。
-   parse 有 depth/node budget。
-   日志和非管理 UI 脱敏 userinfo/query secret。

### Acceptance Criteria

-   路径穿越、UNC、盘符、symlink escape 全部失败。
-   正常中文/Unicode 文件名继续可用。
-   `file:/ftp:/data:` 等协议被拒绝。
-   redirect 不能绕过目标策略。
-   超量内容在覆盖当前配置前停止。
-   失败临时文件被清理。
-   批准内网源仍工作。
-   URL 凭据不进入普通日志和访客页面。

### Dependencies

无。

### Definition of Done

路径、上传、URL、redirect、容量和兼容测试全部进入回归。

### Review Boundary

先统一 path/filename；再处理 URL/redirect/budget。不要夹带 UI 重构。

------------------------------------------------------------------------

## T03 · 消除配置内容与工具链接的 HTML / JavaScript 注入路径

**Linear：INH-615**\
**Priority：P0 / Urgent**

### Problem

viewer/search/home/records/history 等多处使用
`ui.html(..., sanitize=False)`；问题不是 `sanitize=False`
本身，而是动态配置 label/value/path/备注/URL 被拼进原始 HTML、DOM
attribute 或 JavaScript 字符串。

配置文件属于外部输入，恶意内容可能在浏览器上下文执行，影响普通查看者甚至管理员。

### Scope

-   dynamic HTML
-   JS parameters
-   DOM selector
-   favorite path
-   tool URL
-   config label/value
-   review note
-   record/diff text

### Implementation Notes

规则：

-   普通数据优先 `ui.label` / text API。
-   只有固定模板允许 raw HTML。
-   文本和 HTML attribute 分别 escape。
-   JS 参数使用 JSON 序列化，不直接拼接字符串。
-   URL 使用原生 link/navigation 能力。
-   只允许合理 scheme。
-   外链 `noopener/noreferrer`。
-   favorite/remove 等操作优先通过元素引用或编码 key，不拼 CSS
    selector。

### Acceptance Criteria

输入包含：

-   `<script>`
-   `<img onerror>`
-   quotes
-   slash/backslash
-   HTML
-   CSS
-   Unicode control chars

均只能显示为文本，不执行、不破坏 DOM、不额外出网。

### Dependencies

无。

### Definition of Done

危险 sink 盘点、修复和注入回归完成；viewer 之外的
search/history/records/home/tools 同样覆盖。

### Review Boundary

P0 注入修复不要等待 T16 共用树。

------------------------------------------------------------------------

## T04 · 实现原子持久化、并发保护与 WriteGate

**Linear：INH-616**\
**Priority：P0 / Urgent**

### Problem

当前 `_save_json()` 等直接覆盖文件；损坏 JSON 被 `_load_json()`
当默认空值返回后，下一次保存可能永久覆盖仍可恢复的数据。`save_config_file()`
先 move 当前版，再写新文件，异常可留下 current
缺失。读改写缺少统一互斥，同秒归档/record 名称也可能碰撞。

同时，一致备份/升级没有真正的"停止新写"边界。

### Scope

-   all authoritative JSON
-   current config
-   archive
-   record
-   generated review files
-   single-process write coordination
-   maintenance backup gate

### Implementation Notes

单文件：

``` text
mkstemp in same dir
→ write / serialize
→ flush
→ fsync
→ os.replace
```

按平台记录目录 fsync 与 Windows replace 限制。

并发：

-   profile 级 RLock 包完整 read → validate → modify → commit。
-   device model registry 等全局数据单独锁。
-   网络、解析、大 diff 不在锁内。
-   commit 前重新校验 expected source hash。

配置更新：

-   先保留有效旧版。
-   再原子切换 current。
-   archive/record 使用 exclusive unique filename。
-   同内容更新不制造重复 archive。

损坏数据：

-   missing → 可以安全 default。
-   corrupt → 明确 error / read-only protection。
-   不能 `corrupt -> [] -> save []`。

WriteGate：

``` text
OPEN
→ DRAINING
→ MAINTENANCE
→ OPEN
```

用途：

-   consistent backup
-   upgrade
-   recovery

它只负责单进程写准入，不是分布式锁。

### Acceptance Criteria

故障注入：

-   temp write fail
-   fsync fail
-   replace fail
-   ENOSPC
-   permission denied
-   archive fail

之后旧权威数据仍完整。

并发：

-   两个用户同时追加不同备注都保留。
-   同秒保存不覆盖历史。
-   maintenance 时新写明确被拒绝/排队。
-   在途写完成到安全点后才能备份。
-   解除 maintenance 后可正常写。

### Dependencies

无。

### Definition of Done

关键 writer 全部落到共同原语和 WriteGate；故障注入、并发和 backup
barrier 测试通过。

### Review Boundary

建议：

1.  atomic I/O + corrupt protection
2.  read-modify-write lock
3.  unique archive
4.  WriteGate

------------------------------------------------------------------------

## T05 · 补齐迁移、重命名、删除、备份与恢复链路

**Linear：INH-617**\
**Priority：P0 / Urgent**

### Problem

启动迁移使用逐项 `shutil.move`；配置重命名先改 mapping
再移动文件并只更新部分引用；删除可能
`ignore_errors=True`。跨文件操作中断后无法确定当前状态。

### Scope

-   legacy → profile migration
-   config rename
-   profile copy
-   profile/config delete
-   quarantine
-   recovery
-   consistent backup manifest

### Implementation Notes

只针对真实跨文件操作建立 operation manifest：

``` text
operation_id
type
source/target
expected_hash
preimage / backup
completed_steps
state
```

状态可保持简单：

``` text
PREPARED
APPLYING
COMMITTED
RECOVERY_REQUIRED
```

迁移：

-   backup/copy
-   verify
-   activate
-   原路径保留到确认
-   repeat safe

rename：

必须盘点：

-   configs
-   archive
-   records
-   mapping
-   favorites
-   bindings
-   edit remarks
-   DL source/binding
-   generated-file relation
-   tab/FileRef

delete：

-   默认 move to quarantine。
-   保留 manifest。
-   不自动过期清空。
-   permanent delete 必须明确动作。

backup：

-   使用 T04 WriteGate。
-   权威数据 + config + secret 恢复材料。
-   cache/temp 可排除。
-   restore 时发现新目标 hash 不同：停止，不覆盖。

### Acceptance Criteria

-   旧/新/混合布局均可处理。
-   任何步骤中断都可确定恢复。
-   重复 migration 幂等。
-   rename 后所有已有引用可追溯。
-   delete 可恢复。
-   partial failure 不能报全部成功。
-   backup hash 清单可校验。
-   恢复不覆盖恢复点之后新增的未知数据。

### Dependencies

T01、T02、T04。

### Definition of Done

迁移、rename、delete/quarantine、backup/restore 的 fault injection
在副本环境通过。

### Review Boundary

按 migration / rename / delete-recovery 三组完成，不扩展成通用事务框架。

------------------------------------------------------------------------

## T06 · 建立不可变 SourceSnapshot、完整 ParsedDocument 与稳定 NodeRef

**Linear：INH-618**\
**Priority：P0 / Urgent**

### Problem

当前 parser 对较大 XML 丢弃非白名单 attribute；同名 XML sibling 的
path/id 冲突；JSON 值常被字符串化；筛选/隐藏后 viewer 重新 enumerate
child，审阅 key 可能不再对应原节点。

旧设计如果直接变成 `raw bytes + full model + ViewTree`
三套长期模型，又会产生新的同步负担。

### Scope

-   source revision
-   complete parse semantics
-   locator
-   type
-   duplicate sibling
-   search/favorite/binding/diff reference
-   UI projection

### Implementation Notes

只保留两层核心模型。

#### SourceSnapshot

``` text
FileRef
content_hash
format
parser_version
source_handle/path
```

-   小临时文件可持有 bytes。
-   大文件不强制为了 snapshot 再复制一份完整 bytes。

#### ParsedDocument

唯一完整解析模型：

-   JSON 类型保持
-   object / array / scalar root
-   XML expanded namespace
-   attributes
-   text
-   tail
-   sibling occurrence

UI 的：

-   filter
-   search
-   hide parameter metadata
-   lazy expand

只生成 NodeRef/投影，不复制并重新编号整棵权威树。

NodeRef：

``` text
source_hash
locator
value_type
original_value
```

JSON 使用 escaped pointer/token path。XML locator 只在固定 source hash
内定义身份，不声称跨版本稳定。

### Acceptance Criteria

覆盖：

-   199999 / 200000 bytes 两侧
-   repeated XML sibling
-   namespace
-   attribute/text/tail
-   JSON key 带 `/ ~ .`
-   arrays
-   empty object/array
-   root scalar
-   type change

过滤/搜索后 NodeRef 仍指向原始节点。

旧 favorite/binding：

-   唯一匹配 → 可继续。
-   多候选 → 标记歧义，要求重新绑定。
-   不选择第一个/最后一个猜测。

### Dependencies

无。

### Definition of Done

完整语义、locator、旧 path 兼容和大文件内存行为有 fixture 证明。

### Review Boundary

先 SourceSnapshot/locator；再 parser completeness；最后消费者适配。

------------------------------------------------------------------------

## T07 · 重建审阅提交的版本校验与保真输出

**Linear：INH-619**\
**Priority：P0 / Urgent**

### Problem

当前 review 根据 positional `node_key` 修改当前展示树，不校验 source
hash / original value，再从展示树重新序列化。源版本变化、过滤、重复
sibling 都可能误改；XML 非展示字段可能被静默丢失。

### Scope

现有：

``` text
suggest
→ pending review
→ approve/reject
→ generate result
```

不增加：

-   multi-stage approval
-   workflow engine
-   auto merge

### Implementation Notes

remark 新记录保存：

-   source hash
-   NodeRef
-   original type/value
-   schema version

submit：

1.  重新授权。
2.  校验 remark 仍 pending。
3.  校验 source hash。
4.  校验 locator + original value/type。
5.  对完整 ParsedDocument / 安全源执行修改。
6.  先生成 candidate。
7.  重新 parse candidate。
8.  验证只有预期语义变化。
9.  发布 artifact。
10. 再更新 remark status。

XML 保真：

目标是"未修改业务语义不丢失"，不是声称 byte-for-byte 相同。

如果标准库无法保留真实业务样本中的：

-   comments
-   PI
-   prefix/namespace requirement
-   encoding
-   tail / mixed content
-   DOCTYPE 等

则：

-   评估成熟 XML 库；
-   或拒绝自动生成；

不能静默丢失。

批次以 source file
为最小提交单元。不同文件逐文件成功/失败，不伪装全局事务。

### Acceptance Criteria

-   suggestion 后 source 改动 → conflict。
-   repeated sibling 不误改。
-   filter/search 后不误改。
-   未选字段保持语义。
-   unsupported XML 明确拒绝。
-   双管理员重复 submit 只有一次业务结果。
-   candidate 生成失败不把 remark 标 approved。
-   restart 能识别未完成 operation。

### Dependencies

T01、T04、T06。

### Definition of Done

版本冲突、semantic preservation、idempotency、recovery 和 legacy remark
测试通过。

### Review Boundary

先"拒绝误改"，再"安全生成"，最后"批次恢复"。

------------------------------------------------------------------------

## T08 · 修复 FileRef、临时上传、归档和记录引用链路

**Linear：INH-620**\
**Priority：P0 / Urgent**

### Problem

访客上传内容解析后没有持久化，却仍通过 filename 打开 viewer；viewer
再从服务器 configs
读取，因此同名时可能显示旧服务器文件，不同名则找不到。历史/record
入口也并非所有链接都携带 profile/version。

### Scope

FileRef kind：

-   current
-   archive
-   record
-   temp

覆盖：

-   upload
-   URL temporary import
-   comparison upload
-   viewer
-   download
-   tabs
-   history
-   record_view

### Implementation Notes

最小：

``` text
FileRef(profile_id, kind, name, version_or_token)
```

temp：

-   session isolated dir / bounded temp storage
-   random token
-   owner/session validation
-   capacity limit
-   TTL
-   cleanup
-   不进入 shared configs
-   默认不进入 global search / review / favorite，除非明确保存

所有 viewer/tab/download 都持有 FileRef，不用 filename 推断来源。

旧 URL：

-   current/archive 继续支持安全参数。
-   record 新链接必须携带 profile。
-   旧 record link 兼容解析，但要显示解析到哪个 profile。
-   expired temp 不回退同名 persistent。

### Acceptance Criteria

-   访客上传可完整查看。
-   同名 temp 优先显示 temp，服务器 hash 不变。
-   session A 不能访问 session B temp。
-   temp expired 有明确错误。
-   cold cache archive 可查看。
-   profile 切换后旧 record link 不串。
-   tab restore 不把 temp 换成同名 current。

### Dependencies

T01、T02。

### Definition of Done

FileRef 在 temp/current/archive/record 全链路一致，生命周期测试通过。

### Review Boundary

先修明显缺失 import/版本参数 bug，再接 FileRef。

------------------------------------------------------------------------

## T09 · 统一更新入口的校验、手动刷新与受控异步执行

**Linear：INH-621**\
**Priority：P0 / Urgent**

### Problem

manual update、scheduler、record refresh
和重型页面操作执行方式不统一。`run_single_update()` 下载后直接保存，合法
XML/JSON 格式的错误页仍可能覆盖 current。页面还可能有自己的 periodic
record fetch，与 scheduler 重复。

### Scope

-   single update
-   full update
-   schedule
-   manual record refresh
-   periodic record update
-   heavy diff/search work
-   TaskStatus

### Implementation Notes

统一 update pipeline：

``` text
download to staging
→ network/size validation
→ parse
→ structural fingerprint check
→ compare expected current hash
→ atomic archive/current commit
→ derive diff/cache
```

structural fingerprint 最小要求：

-   XML root expanded name
-   JSON root type

定时更新：

-   root/type 异常 → reject。
-   不弹 UI 确认。

管理员手动：

-   如果可解析但 root/type 明显变化，展示变更并要求 explicit confirm。
-   不直接覆盖。

record：

-   APScheduler 负责周期抓取。
-   页面 periodic fetch 删除。
-   **手动刷新按钮保留**，触发同一 server task。

execution：

-   bounded executor/queue
-   profile+resource operation key 防重入
-   UI event loop 不运行 blocking I/O
-   client 离开不影响已经进入 commit 的业务操作
-   cancel 只在安全边界生效

### Acceptance Criteria

-   200 HTML error 不覆盖。
-   valid-but-wrong XML/JSON root 不自动覆盖。
-   malformed/too large/timeout 不覆盖。
-   same content 不重复 archive。
-   manual + schedule 同时触发不会重复 commit。
-   manual record refresh 仍工作。
-   page timer 不重复抓取 record。
-   双客户端慢任务期间另一个客户端可正常操作。

### Dependencies

T01、T02、T04、T06。

### Definition of Done

三种 update/record 入口共享同一 operation、验证、TaskStatus 和错误契约。

### Review Boundary

先 validation/commit；再 async/dedup；最后 scheduler/manual UI。

------------------------------------------------------------------------

## T10 · 保证解析与派生 diff 缓存一致性

**Linear：INH-622**\
**Priority：P0 / Urgent**

### Problem

parse cache 使用 path + mtime + size，没有 parser/schema
version，源替换和并发可能命中旧树。archive `.diff.json`
同样是派生数据，但和历史原文混放，并且包含 `bound_count` 等依赖当前
bindings 的统计，bindings 修改后旧 summary 可能过时。

### Scope

-   parsed document cache
-   archive diff cache
-   future derived search cache

不包括：

-   authoritative configs
-   archive raw files
-   remarks
-   business JSON

### Implementation Notes

统一派生缓存 identity：

``` text
source content hash
algorithm version
cache schema version
```

diff 若保存绑定信息：

``` text
+ binding revision/hash
```

否则绑定统计改为实时计算。

要求：

-   version mismatch → miss。
-   parser/differ upgrade → miss。
-   old cache 不迁移，直接 rebuild。
-   source 在 parse 过程中变化 → 结果丢弃。
-   同 key 写入互斥。
-   unique temp。
-   cache corrupt → delete cache only。
-   `.diff.json` 保持 legacy readable，但新架构将其视为 cache，不是
    archive authority。

### Acceptance Criteria

-   same mtime/size different bytes 不命中。
-   parser version 改变失效。
-   differ version 改变失效。
-   binding 修改后 bound_count 不陈旧。
-   half cache 不返回。
-   clear all cache 后业务仍正确。
-   cache clean 不碰 raw archive/current。

### Dependencies

T04、T06。

### Definition of Done

parse/diff cache 都有确定 identity、atomic write、invalidation 与
cleanup 规则。

### Review Boundary

先 correctness，再性能。

------------------------------------------------------------------------

## T11 · 修复 DL 数值输入、结果对应与求解语义

**Linear：INH-623**\
**Priority：P0 / Urgent**

### Problem

当前存在确定性 bug：

-   `None` 被 `or 0` 转成 0。
-   真正 0 被 `or 1.0` 替换。
-   过滤结果后按旧下标贴回 y。
-   coefficient extract 失败可能默认为 0。
-   poly root solver 只靠 sampling/sign change，可能漏
    even-multiplicity/tangent root。
-   结果文案可能超出算法保证。

### Scope

现有三类 DL calculation、input/range、coefficient extraction、reverse
root、result mapping。

### Implementation Notes

第一阶段只修确定性错误：

-   None / 0 分开。
-   reject NaN/Inf。
-   invalid interval 拒绝。
-   结果携带 original input index。
-   coefficient result 明确 success/missing/invalid。

第二阶段：

-   candidate roots
-   endpoint detection
-   residual check
-   even multiplicity/tangent cases
-   constant/zero polynomial

在没有可证明完整求解前：

不能写"全部实根"。

若真实需求要求 8 阶有界区间完整根：

再比较：

-   mature numerical dependency
-   derivative isolation + bisection

不要靠增加 sampling 数量假装正确。

### Acceptance Criteria

覆盖：

-   None
-   real zero
-   denominator zero
-   multi row filtering
-   `(x-a)^2` 非采样点 root
-   endpoint root
-   zero polynomial
-   constant
-   no real root
-   near multiple root
-   overflow
-   negative scale

每个候选根输出 residual/有效性。

### Dependencies

无。授权/持久化由 T01/T04 组合验收，但数值修复本身不应被它们阻塞。

### Definition of Done

确定性 bug 和算法能力边界都有 tests；旧 DL config 继续可读。

------------------------------------------------------------------------

## T12 · 建立可复现完全离线发布并修复启动配置

**Linear：INH-624**\
**Priority：P0 / Urgent**

### Problem

`requirements.txt` 只给下限；环境不可复现。`MCHECKER_PORT`
存在读取函数但运行固定 50002。仓库跟踪
`.nicegui/storage-user-*.json`、`__pycache__/*.pyc`，`.gitignore`
没有覆盖这些开发产物。

无法仅凭旧文档断言 NiceGUI 必须联网；必须实际做 cold-cache
blocked-public-network 验证。

### Scope

-   Python/runtime lock
-   NiceGUI/APScheduler lock
-   transitive dependencies
-   wheelhouse
-   static assets
-   port
-   data root
-   storage secret
-   repo/release hygiene

### Implementation Notes

从实际已运行环境出发锁定：

-   Python version
-   NiceGUI
-   APScheduler
-   transitive wheels
-   OS/architecture

先支持真实部署平台，不维护未知平台包矩阵。

提供：

``` text
offline wheelhouse
install script
verification manifest
```

启动：

-   `MCHECKER_PORT` 真正生效。
-   旧部署的 50002 必须显式迁移，不悄悄改回 50001。
-   data root 可选但默认保持旧路径。
-   storage secret 持久化失败不能静默每次生成新 secret。

资源：

-   浏览器 cold cache。
-   禁公网。
-   NiceGUI/Quasar/icon/font/JS/CSS 必须同源加载。

不要为离线新增全局产品 toggle。断公网由部署网络保证；外部工具或公网 URL
只需明确失败。

仓库卫生：

-   `.nicegui/`
-   `__pycache__/`
-   `*.pyc`
-   generated test output

从 Git/发行包移除；检查历史是否包含真正敏感内容。

### Acceptance Criteria

-   fresh machine 无 package index 安装成功。
-   cold browser + no public internet 首屏成功。
-   local upload/view/search/compare/download smoke 成功。
-   无公网静态请求。
-   `MCHECKER_PORT` 生效。
-   旧部署不意外换端口/data root。
-   release bundle 无业务 data、secret、`.nicegui`、pyc。

T12 不重复承担 T07/T11 的业务正确性；完整业务组合由 T19。

### Dependencies

无。

### Definition of Done

锁文件、wheelhouse、离线安装、网络记录、启动配置和发布卫生可复现。

### Review Boundary

先 port/repo hygiene；再 dependency lock/wheelhouse；最后 cold-cache
offline smoke。

------------------------------------------------------------------------

## T13 · 隔离测试数据并建立关键回归安全门槛

**Linear：INH-625**\
**Priority：P0 / Urgent**

### Problem

现有 test fixture 并没有全局在 import 前重定向 data root。`test_differ`
只替换某个 legacy 常量时，真实实现可能仍通过 profile path 写到仓库
`data/profiles/default`。同时存在 `len(x) >= 0` 等无法失败的断言。

运行测试本身可能损害开发者或部署副本数据，属于 P0。

### Scope

-   pytest root
-   DATA_ROOT
-   profiles
-   cache
-   NiceGUI storage
-   ContextVar
-   scheduler/cache singleton
-   test guards
-   invalid assertions
-   minimal CI

### Implementation Notes

测试进程开始：

1.  创建 temp root。
2.  设置环境/配置。
3.  再 import 捕获路径的业务模块。

每 test：

-   unique profile
-   reset ContextVar
-   reset scheduler
-   reset parse cache state
-   reset user storage mock

增加 write guard：

任何 authoritative write path 如果不在 fixture root：

**立即 fail test**。

不要"测试结束后再清理生产 data"。

修复所有 vacuous assertion。

每个 P0 PR 带：

-   failing regression before fix
-   positive case
-   compatibility case

### Acceptance Criteria

-   在真实 checkout 预先放 sentinel data。
-   跑 tests 后 sentinel hash/mtime 不变。
-   tests random order 可重复。
-   单 test 可独立运行。
-   故意破坏 binding 时相关 test 必 fail。
-   fixture root 外写立即失败。

### Dependencies

无。

### Definition of Done

测试隔离与 guard 成为所有后续任务的安全基础。

### Review Boundary

优先级上可视为最早任务之一。

------------------------------------------------------------------------

# 7. M2 任务明细

## T14 · 提取应用操作边界并统一错误、日志与文档契约

**Linear：INH-626**\
**Priority：P1 / High**

### Problem

当前页面直接组合：

``` text
auth
parser
storage
scheduler
reviewing
differ
notify
```

相同操作在不同入口重复。`differ` 读取 storage bindings，而 storage
又调用 differ；storage 读取 NiceGUI profile，造成反向依赖。

文档也存在事实源冲突：Agent rules 指向
`docs/architecture.md`，而正式设计此前另有 v2
文件；旧架构文档仍包含已经被审查否定的陈旧结论。

### Scope

只提取真正跨入口复用的用例：

-   config import/update/lifecycle
-   review submit
-   task/update execution

统一：

-   domain error
-   logging
-   doc source-of-truth

### Implementation Notes

目标：

``` text
pages/http
   ↓
application operations
   ↓
core + storage
```

不是：

``` text
controller
service
repository
DAO
domain manager
...
```

只读 getter 可以直接使用显式 profile storage。

differ 接收：

``` text
binding snapshot
```

而不是自己读 storage。

错误：

``` text
code
message
retryable
context
```

少量 code：

-   INVALID_INPUT
-   FORBIDDEN
-   NOT_FOUND
-   SOURCE_CHANGED
-   REVIEW_CONFLICT
-   TOO_LARGE
-   BUSY
-   DOWNLOAD_FAILED
-   STORAGE_FAILURE
-   CORRUPT_DATA

日志：

-   operation id
-   profile
-   actor
-   target
-   result
-   duration
-   error code

不记录：

-   config content
-   password/token
-   Cookie
-   full credential URL

文档事实源：

1.  `docs/MCChecker_ARCHITECTURE.md`
2.  `docs/MCChecker_ROADMAP.md`
3.  Linear = execution mirror
4.  `docs/architecture.md` = Agent compatibility entry
5.  v2 文件 = historical

### Acceptance Criteria

-   import/update/review 可不加载 NiceGUI 测试。
-   new entry 不复制 authorization/commit logic。
-   core 不 import pages。
-   storage core 不依赖 browser state。
-   error 在 UI/log 中有一致 code。
-   docs/architecture.md 明确指向 canonical。
-   Roadmap T ID 与 Linear 一一对应。

### Dependencies

T01、T04、T06、T07、T09、T13。

### Definition of Done

一次迁移一个真实用例并保留旧 wrapper；不把目录重排和业务变化混成一个大
PR。

### Review Boundary

1.  config/update operation
2.  review operation
3.  error/log
4.  docs source-of-truth

------------------------------------------------------------------------

# 8. M3 任务明细

## T15 · 收敛工作区导航与配置管理表单

**Linear：INH-627**\
**Priority：P1 / High**

### Problem

home
承担太多工作区/收藏/机型/导入状态；双侧栏和卡片降低信息密度。management
把 update/schedule/IP/admin 放在一个长页面；tools/bindings CRUD
状态反馈不完全即时。小时/天调度 UI 与实际存储语义也有偏差风险。

### Scope

-   home workspace
-   left file nav
-   tools drawer
-   management
-   model
-   binding
-   tool form
-   current identity/profile display

不做：

-   Dashboard
-   plugin navigation
-   new business feature

### Implementation Notes

工作区保留：

``` text
left compact file list
+
center tabs
+
optional right tools
```

left：

-   search/filter
-   selected state
-   current update/error state

top：

-   current profile/model
-   source
-   role
-   major actions with label

settings：

-   update source/schedule
-   identity/admin
-   model
-   tools/bindings

用 table/row/form，而不是每行一个悬浮 card。

schedule：

disk 继续 `interval_hours`，day/hour 只做 UI conversion。

CRUD：

-   local refresh affected section。
-   不默认 `location.reload()`。
-   keep filter/selection。

tab：

-   基于 FileRef。
-   draft tab 不无提示 auto-evict。

### Acceptance Criteria

-   所有旧入口仍可达。
-   role/profile/source 清楚。
-   CRUD 后立即一致。
-   hour/day round-trip 正确。
-   invalid IP/duplicate/empty binding 有 inline error。
-   1366×768 和 1920×1080 完成核心操作。
-   keyboard/touch 不依赖 hover。
-   tab restore 不串 profile/source。

### Dependencies

T01、T08、T09、T14。

### Definition of Done

功能/密度先验收，视觉 polish 留 T20。

### Review Boundary

按 page group 改，不做全站一次性换肤。

------------------------------------------------------------------------

## T16 · 统一树视图并修复搜索与版本对比连续性

**Linear：INH-628**\
**Priority：P1 / High**

### Problem

viewer/search/favorites/archive/record_view
有多套树；折叠、复制、长值、range、remark
行为不一致。搜索父节点命中可能计数为 0。history 点击某 archive 后
comparison 可能默认另一版本。

### Scope

-   common tree row/node
-   local/global search
-   history
-   comparison
-   record view
-   long value/copy
-   keyboard

### Implementation Notes

基于 T06 ParsedDocument/NodeRef。

common tree 只做 display + explicit hooks：

-   read only
-   favorite
-   remark
-   search hit
-   diff state

不读取 storage。

search：

区分：

-   node hit
-   leaf/value hit
-   filename hit
-   note hit

父 node hit 不能因为 leaf count=0 被误判"无结果"。

comparison：

tab payload 持有：

``` text
old FileRef
new FileRef
upload original name
```

点击历史第三版，就必须展示第三版。

copy：

clipboard 真成功后才显示 success；失败提供 selectable text/download
fallback。

### Acceptance Criteria

-   搜索 parent/file/note/value 都正确。
-   locator 不因 filter 改变。
-   任意 archive click → exact version。
-   reload/reopen 后 exact version 保留。
-   四种 comparison 全回归。
-   所有 tree 页面 focus/expand/copy/long value 一致。
-   T03 safe rendering 不回归。

### Dependencies

T03、T06、T08、T14、T15。

### Definition of Done

先修 correctness bugs，再提取 viewer/search 共用 tree，最后逐页迁移。

### Review Boundary

不做 schema-driven renderer。

------------------------------------------------------------------------

## T17 · 保留审阅与计算草稿并明确任务结果

**Linear：INH-629**\
**Priority：P1 / High**

### Problem

review selection 只在局部 render state；切 tab 会丢。DL `editing` 存在
shared config，中间 binding apply 会持久化，因此 Cancel
不一定意味着"没写"。

长任务结果也依赖 toast/局部状态，不利于 reconnect/partial failure。

### Scope

-   review draft
-   DL draft
-   TaskStatus presentation
-   partial submit
-   retry semantics

### Implementation Notes

DL：

``` text
effective config
≠
session draft
```

session draft：

-   editing state
-   input
-   temporary binding selection

Save：

-   带 base config hash/revision。
-   once commit。

Cancel：

-   authoritative config hash 不变。

review：

-   selection 按 file/session 保存。
-   未选 item 继续 pending。
-   draft 绑定 source hash。
-   source changed → conflict。

TaskStatus UI：

-   queued
-   running
-   succeeded
-   partially_failed
-   failed
-   conflict

失败：

-   preserve inputs
-   show safe retry condition

### Acceptance Criteria

-   tab switch 后 draft 恢复。
-   两客户端编辑互不影响。
-   Cancel 后 config hash 不变。
-   partial review 不改变未选。
-   source change 有 conflict。
-   disconnect/reconnect 后任务结果可解释。
-   repeated click 不重复业务提交。

### Dependencies

T07、T09、T11、T14、T15。

### Definition of Done

DL draft、review selection、TaskStatus 三组浏览器回归通过。

### Review Boundary

不要新增跨设备 draft sync。

------------------------------------------------------------------------

# 9. M4 任务明细

## T18 · 按代表性负载优化渲染与查询成本

**Linear：INH-630**\
**Priority：P1 / High**

### Problem

已观察到潜在热点：

-   full tree DOM
-   global search all files
-   history/records parse/diff on render
-   high-frequency timers
-   repeated file scans

但没有真实性能基线。

### Scope

仅优化测量证明的：

-   parsing reuse
-   tree render
-   search
-   diff
-   polling/timers
-   long session resource release

### Implementation Notes

基准记录：

-   deployment hardware
-   file count
-   node count
-   archive count
-   clients
-   cold/warm cache
-   interaction latency
-   event-loop delay
-   peak memory

优先顺序：

1.  lazy expand
2.  paging/chunked result
3.  on-demand diff
4.  same source hash ParsedDocument reuse
5.  stop hidden-page timers
6.  lightweight version signature

只有仍不足时再评估：

-   process pool
-   search index
-   database

### Acceptance Criteria

-   同一环境有 before/after。
-   cold cache 也记录。
-   result semantics 完全一致。
-   no field dropped for speed。
-   large search/tree 可以分批显示。
-   closed page/tab resource bounded。
-   没有无证据性能百分比。

### Dependencies

T09、T10、T16、T17。

### Definition of Done

每个性能 change 都有数据；无收益 change 撤回。

### Review Boundary

一个 PR 一个主要 hotspot。

------------------------------------------------------------------------

## T19 · 完成核心流程、完全离线与升级恢复验收

**Linear：INH-631**\
**Priority：P1 / High**

### Problem

单元修复不能证明最终系统在：

-   actual browser
-   target OS
-   old data
-   mixed migration state
-   proxy
-   public network blocked
-   interruption

条件下仍正确。

### Scope

最终 release gate。

### Implementation Notes

准备脱敏 fixture：

-   legacy layout
-   new layout
-   mixed layout
-   repeated XML
-   large XML
-   special JSON
-   current/archive/record/temp
-   two profiles
-   pending remarks
-   DL config
-   bindings

角色：

-   guest
-   deployer
-   admin

浏览器闭环：

``` text
upload
→ view
→ search
→ favorite
→ compare
→ propose
→ review
→ generated file
→ download
```

另覆盖：

-   DL
-   update management
-   manual record refresh
-   profile switch
-   reconnect

release：

-   clean offline install
-   cold browser
-   no public internet

backup/recovery：

-   WriteGate
-   hash manifest
-   migration interruption
-   rename interruption
-   review batch interruption
-   update commit interruption

rollback：

优先代码 rollback + 保留当前有效业务数据。

不能：

``` text
restore old snapshot
→ overwrite everything newer
```

如果 schema 不兼容：

按 compatibility matrix 做 export/recovery。

### Acceptance Criteria

-   所有 P0 已通过各自回归。
-   完整业务闭环通过。
-   offline first install/visit 通过。
-   no public runtime dependency。
-   backup 可恢复。
-   interrupted upgrade 可恢复。
-   double client / two profile 不串。
-   no unresolved P0。
-   明确 single process/single scheduler 支持边界。
-   记录实测最大代表文件，不虚构"无限"。

### Dependencies

T05、T12、T13、T14、T15、T16、T17、T18。

### Definition of Done

发行报告包含：

-   environment
-   fixture
-   result
-   failed case
-   manual step
-   supported boundary
-   recovery procedure
-   compatibility matrix

未验证项不能标通过。

### Review Boundary

验收失败回归所属 T task，不新建大量"验收碎片 Issue"。

------------------------------------------------------------------------

# 10. M5 任务明细

## T20 · 收敛视觉样式并清理重复覆盖

**Linear：INH-632**\
**Priority：P2 / Medium**

### Problem

已有 CSS token、system font、focus-visible、reduced motion，但全局 Card
hover elevation、彩色边框、多个 tree 色系、DL inline style
等造成视觉噪声和维护分叉。

### Scope

只改既有页面：

-   spacing
-   radius
-   card/border
-   status color
-   hover
-   focus
-   responsive
-   repeated CSS

### Implementation Notes

-   复用 existing CSS variables。
-   默认 list/row/table/section。
-   Card 只表示真正独立 group。
-   移除全局 `.q-card:hover` elevation。
-   减少 decorative color。
-   保留 semantic status color。
-   CSS 尽量落在 `mc-*` namespace。
-   减少全局 Quasar override。
-   hover action 必须 focus/touch 可达。
-   不增加 external UI library/font。
-   不做 Dashboard。
-   不顺手加 dark theme。

### Acceptance Criteria

-   core pages visual hierarchy 一致。
-   information density 不退化。
-   long text readable。
-   focus visible。
-   mobile/narrow width 关键按钮不丢。
-   reduced motion 保留。
-   no public asset dependency。
-   functional regression 通过。

### Dependencies

T19。

### Definition of Done

视觉改动可独立回滚；不阻塞已经成熟可发布版本。

------------------------------------------------------------------------

# 11. Milestone Definition of Done

## M1 Gate

必须全部满足：

-   [ ] T13 test isolation 已先验证。
-   [ ] 所有 P0 有 regression。
-   [ ] 未授权写入不能发生。
-   [ ] profile/default 不串。
-   [ ] dangerous path/URL 被阻止。
-   [ ] XSS/JS injection 被阻止。
-   [ ] atomic write/fault injection 通过。
-   [ ] migration/delete 有 recovery。
-   [ ] parser 不丢语义。
-   [ ] review 不误改/不静默丢字段。
-   [ ] temp/current/archive/record 来源明确。
-   [ ] bad update 不覆盖。
-   [ ] derived cache 不陈旧。
-   [ ] DL deterministic bugs 修复。
-   [ ] offline package 基础可重建。

## M2 Gate

-   [ ] 核心写 use case 不依赖 NiceGUI。
-   [ ] authorization/validation/commit 不复制。
-   [ ] error code 可统一映射。
-   [ ] log 脱敏。
-   [ ] canonical architecture/roadmap 唯一。

## M3 Gate

-   [ ] 核心入口完整可达。
-   [ ] exact history selection。
-   [ ] tree/search semantics 一致。
-   [ ] temp/current/archive 明确。
-   [ ] draft 不丢。
-   [ ] Cancel 不写 authoritative config。
-   [ ] loading/success/error/conflict 状态一致。

## M4 Gate

-   [ ] 性能先测量后优化。
-   [ ] cold/warm 都有数据。
-   [ ] complete browser flow。
-   [ ] offline clean install。
-   [ ] backup/restore。
-   [ ] upgrade/recovery。
-   [ ] no unresolved P0。

## M5 Gate

-   [ ] visual consistency。
-   [ ] keyboard/focus/touch。
-   [ ] long-value usability。
-   [ ] no new external runtime asset。
-   [ ] functional regression。

------------------------------------------------------------------------

# 12. Linear 同步规则

Linear 不是第二份独立设计文档。

每个 Issue 必须至少保持：

-   Task ID
-   Problem
-   Scope
-   Implementation Notes
-   Acceptance Criteria
-   Dependencies
-   Priority
-   DoD

Roadmap 改任务语义时：

1.  先修改本文件。
2.  同步 Issue title/description。
3.  同步 Milestone。
4.  同步 blocks/blockedBy。
5.  回查 Linear。
6.  不重新创建重复 Issue。

当前映射固定：

  Task   Linear
  ------ ---------
  T01    INH-612
  T02    INH-613
  T03    INH-615
  T04    INH-616
  T05    INH-617
  T06    INH-618
  T07    INH-619
  T08    INH-620
  T09    INH-621
  T10    INH-622
  T11    INH-623
  T12    INH-624
  T13    INH-625
  T14    INH-626
  T15    INH-627
  T16    INH-628
  T17    INH-629
  T18    INH-630
  T19    INH-631
  T20    INH-632

`INH-614` 是已取消的 MCP 测试 Issue，不属于 Roadmap。

------------------------------------------------------------------------

# 13. Deferred / Future Consideration

以下内容当前 **不创建 Issue**。

## Database / SQLite

重新评估触发：

-   需要多进程 writer。
-   文件跨对象事务逻辑继续明显膨胀。
-   查询/关系复杂度已经实测成为核心成本。
-   文件 storage 成为明确性能瓶颈。

数据库不能自动解决：

-   XML 保真
-   NodeRef
-   XSS -错误 DL
-   arbitrary URL

因此现在不迁。

## SSO / Account / Organization Isolation

重新评估触发：

-   shared workstation。
-   untrusted intranet。
-   audit identity requirement。
-   department/organization isolation 明确成为产品需求。

当前 profile 不等于 tenant。

## Multi-process / HA

重新评估触发：

-   单进程成为实测吞吐瓶颈。
-   可用性指标要求进程级故障不中断。
-   WriteGate / file locks 无法覆盖部署模型。

届时必须重新设计 writer ownership，不能简单启动多个 NiceGUI worker
指向同一 data root。

## React / Vue Rewrite

只有 NiceGUI 经实际验证无法满足已确认的：

-   UX
-   offline
-   performance
-   deployment

要求时才重新比较。

## Plugin System

至少出现多个真实独立扩展，并且固定代码分支成本已经成为持续问题后再评估。

## Workflow Engine

仅当审批/长事务/补偿流程真实复杂化，而不是只有当前 review batch
时再考虑。

## Automatic Retention

历史、recovery、quarantine
都包含可恢复数据。没有明确业务保留策略和管理员授权前，不增加自动永久删除。

## Zero-downtime Hot Code Update

当前不属于 MCChecker
成熟化必要条件。只有明确维护窗口不可接受、有可用性指标并接受复杂度时再评估。

------------------------------------------------------------------------

# 14. 最终执行顺序

推荐首批并行：

``` text
T13  测试隔离
T01  权限/profile
T02  path/network/upload
T03  injection
T04  atomic I/O
T06  parser/locator
T11  DL deterministic fixes
T12  port + dependency/offline baseline
```

第二批：

``` text
T05  recovery
T07  review
T08  FileRef/temp
T09  update/tasks
T10  cache consistency
```

然后：

``` text
T14 → T15/T16/T17 → T18 → T19 → T20
```

其中：

-   P0 可以独立发布。
-   T12 不等待 M1 全部完成。
-   T19 才是完整 Release Gate。
-   T20 永远不阻塞正确性/稳定性发布。

------------------------------------------------------------------------

# 15. 全局 Definition of Done

任何 T task 只有同时满足以下条件才可以在 Linear 标记 Done：

1.  实际代码已经实现。
2.  对应正例、反例和兼容回归已经执行。
3.  没有损伤已有数据。
4.  需要的 migration/recovery 已验证。
5.  相关文档已经同步。
6.  真实 blocks/blockedBy 已更新。
7.  未验证内容明确留下，不用"理论上应该"代替结果。

**文档完成 ≠ 开发完成。Issue 创建 ≠ 开发完成。测试未执行 ≠ 测试通过。**
