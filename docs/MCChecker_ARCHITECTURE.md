# MCChecker 架构设计书

文档状态：Canonical / 当前正式目标架构  
基准：`v1.0.1` 当前 HEAD `abe83cb20764d6c63a623078d7d3ec60fd4a7fe8`。该 HEAD 相对运行源码审查提交 `e47e54a01f0ad6d74db3a08670a1034f937575c2` 只新增文档，运行代码无变化。  
执行计划：[MCChecker_ROADMAP.md](MCChecker_ROADMAP.md)  
Linear：[MCChecker › Overview](https://linear.app/inhandy/project/mcchecker-9275450a22c3/overview)

> 本文件是架构事实源。`MCChecker_ARCHITECTURE_v2.md`、`docs/architecture.md` 和其他旧设计文档只作为历史记录或兼容入口；实现状态仍以实际代码与测试为准。

## 1. 总体结论

MCChecker 应继续采用 **Python + NiceGUI + APScheduler + 本地文件/JSON 的模块化单体**。当前没有证据支持前后端重写、微服务、插件系统、工作流引擎或数据库迁移。真正阻碍成熟度的是：写操作的身份/profile 上下文不稳定、文件与 JSON 保存缺少可靠提交边界、展示树被用于权威写回、文件来源/版本引用不完整、长任务执行方式分散，以及 UI/任务状态存在多份临时实现。

目标不是把项目改造成平台，而是让下一阶段新增同类功能时，主要增加局部应用操作、页面和少量数据字段，而不再次进行全局重构。

二次评审后保留的核心判断：

- 保留 NiceGUI 单体与 APScheduler；不因为“现代化”拆前后端。
- 保留本地文件和 JSON；数据库只有在真实并发、查询或文件式恢复复杂度继续增长时重新评估。
- 保留现有机型/profile、配置原文、历史、收藏、绑定、审阅、DL、更新等业务语义。
- 安全漏洞、错误数值、误改节点、静默丢字段、危险路径和未授权写入不属于需要兼容的旧行为。
- 当前明确支持 **单服务进程 + 单调度器写同一 data 根**。若未来需要多进程/多实例，再重新设计协调边界，不能假装进程内锁足够。

## 2. 二次评审纠正的设计错误

### 2.1 Roadmap / Linear 漂移

原 v2 Roadmap 的 T13–T19 与第一次同步到 Linear 的任务语义发生漂移。已经重新校正为同一编号体系；Linear 只作为执行镜像，Roadmap 是任务定义源。

### 2.2 测试隔离被低估

现有 `test_differ` 可能绕过 monkeypatch 后写入真实 `data/profiles/default`，且存在 `len(...) >= 0` 这类无效断言。按照项目 P0 定义，这不是普通维护问题，而是开发过程的数据安全问题。因此 T13 提升为 **P0 / M1**，应在第一批修复前或并行完成。

### 2.3 不长期维护三套文档模型

原设计容易被理解为 `raw bytes + 完整模型 + ViewTree` 三套长期数据结构。目标架构改为两层：

1. **SourceSnapshot**：不可变源版本引用，包含 FileRef、content hash、格式、parser version 和受控 source handle/path；小文件可直接持有 bytes，大文件不得为了“快照”强制复制整份原文到内存。
2. **ParsedDocument**：单一完整、带类型的解析模型。搜索、筛选、隐藏字段和 lazy expand 都是它的 UI 投影或 NodeRef 集合，不再复制树并重新编号节点。

原始配置文件本身仍是最终权威数据；ParsedDocument 和所有 diff/search 结果均可重建。

### 2.4 手动 record 刷新不能被“统一任务”误删

页面自己的周期性 record 拉取应移除，周期更新由 APScheduler 唯一负责；但现有“手动刷新修改记录”是有效功能，必须保留。按钮只负责触发同一服务端 task 并观察结果，而不是维护第二套下载实现。

### 2.5 “能解析”不足以保护自动更新

一个 HTTP 200 的错误页可能仍是合法 XML/JSON。自动更新除了格式解析，还需要与当前文件做最小结构指纹校验：XML root expanded name、JSON root type 等。结构根发生异常变化时，定时任务拒绝提交；手动管理操作可以展示差异并要求显式确认。

### 2.6 派生 diff 缓存也需要版本一致性

现有 archive `.diff.json` 是派生数据，但会包含绑定相关统计；bindings 改变后旧 `bound_count` 可能继续显示。T10 扩展为“解析与派生缓存一致性”：cache key/metadata 应包含 source hash、parser/differ version、cache schema version；绑定相关结果还需 binding revision/hash，或改为实时计算。

### 2.7 一致备份需要写入闸门

仅有 per-profile RLock 无法定义“备份期间停止新写”的系统行为。单进程目标架构增加一个轻量 **WriteGate / maintenance gate**：正常写操作进入闸门；备份、升级和恢复可暂停新写、等待在途提交到安全点，读取仍可继续。它不是分布式锁，也不是工作流引擎。

### 2.8 离线部署不需要新建产品“离线模式”

完全离线是部署约束：服务器和浏览器在无公网时仍能安装与完成核心使用。外部工具链接或公网更新源在断网时明确失败即可；网络隔离优先由部署环境 egress policy 完成。除非出现真实产品需求，不新增全局 offline toggle。

### 2.9 profile 身份语义必须保持现状而非偷偷重构

当前 `ip_mapping.json` 与 `admin_users.json` 实际是 profile-scoped；profile 本身又不是租户/部门授权边界。整改必须显式携带 profile 并保留当前行为，不能在没有产品决定时把身份表静默改成全局，也不能宣称现有 profile 已提供部门数据隔离。

## 3. 当前架构

```text
Browser
  │
  ├─ NiceGUI pages / WebSocket callbacks
  ├─ /api/file-download
  ├─ /record_view
  └─ /static + NiceGUI framework assets
          │
          ▼
app/pages
  │  目前仍直接组合 auth/storage/parser/scheduler/reviewing
  ▼
app/core
  ├─ storage / device_models
  ├─ parser / searching / favorites_live
  ├─ reviewing / differ
  ├─ scheduler / downloader
  ├─ parse_cache
  └─ dltool
          │
          ▼
local data + .nicegui user state
```

当前优点应保留：功能已经形成完整业务闭环；解析、diff、调度和本地持久化都足够轻量；没有外部数据库和消息系统；已有 tests 能作为回归基础。

当前失控点不是“文件太多”，而是跨层依赖和隐式上下文：页面直接完成授权、读写和错误处理；storage 会从 NiceGUI 用户状态推断 profile；展示树同时承担 UI、diff、收藏和写回；同一长任务在不同页面有不同执行方式。

## 4. 目标架构

```text
NiceGUI pages / existing HTTP adapters
                │
                ▼
       UI context adapter
       Actor + explicit profile + FileRef
                │
                ▼
        Application operations
  ┌─────────────┼─────────────┐
  │ config      │ review      │ tasks/update
  │ lifecycle   │ submit      │ execution
  └─────────────┼─────────────┘
                │
        ┌───────┴────────┐
        ▼                ▼
Pure domain/core      Persistence boundary
parser/locator        path + atomic I/O
search/diff/DL        locks + WriteGate
        │                │
        └───────┬────────┘
                ▼
 local authoritative files / JSON
 + derived cache / temporary data
```

只提取已有复用操作，不为每个 getter 建 Service。简单只读查询可直接调用显式 profile 的存储函数。旧 `app.core.storage` 可以长期作为兼容 facade；行数不是拆模块的理由。

## 5. 核心契约

建议概念契约如下；可用 dataclass/TypedDict 和普通函数实现，不需要 DI 容器：

```python
Actor(ip, display_name, is_deployer, profile_admin_scope)
FileRef(profile_id, kind, name, version_or_token=None)
SourceSnapshot(file_ref, content_hash, format, parser_version, source_handle)
NodeRef(content_hash, locator, value_type, original_value)
OperationResult(operation_id, status, artifacts, warnings)
TaskStatus(task_id, profile_id, operation, state, error_code=None)
```

`kind` 只覆盖已存在的 `current/archive/record/temp`。FileRef 不包含浏览器可见的绝对服务器路径。NodeRef 只在其 source hash 对应的原文版本内有意义，不承诺跨版本自动漂移。

## 6. 数据分类与目录

四类数据必须分开理解：

1. **权威原文**：current configs、archive、records、审阅生成文件。
2. **权威业务 JSON**：mapping、favorites、bindings、remarks、DL、models、schedule、IP/admin 等。
3. **可重建派生数据**：parsed document cache、diff summary、未来可能的 search cache。
4. **会话/临时状态**：tabs、筛选、草稿、临时文件索引。

建议目录：

```text
data/
  device_models.json
  nicegui_storage_secret.txt
  profiles/<profile_id>/
    configs/
    archive/<name>/
    records/<name>/
    config_mapping.json
    favorites.json
    bindings.json
    edit_remarks.json
    tools.json
    schedule.json
    ip_mapping.json
    admin_users.json
    dltool_config.json
    cache/                # 只放可重建派生数据
    operations/           # 复杂文件操作恢复清单
  temporary/              # 有 owner/token/TTL/容量限制
  recovery/               # 备份/隔离数据；不自动破坏性清理
```

旧 archive 目录中的 `.diff.json` 保持兼容读取，但目标状态应将其视为 cache，而非历史原文的一部分。

## 7. 解析、定位与审阅

### 7.1 ParsedDocument

取消“大 XML 为了显示性能直接丢属性”的做法。完整解析模型必须保留 JSON 类型/容器/根类型以及 XML 的 element、attribute、text、tail、namespace 等当前业务需要的语义。UI 不需要展示全部字段，但不能从解析模型删除后再用于写回。

JSON locator 使用有转义的 token path/JSON Pointer；XML locator 在固定 source hash 内使用展开命名空间和兄弟 occurrence，并区分 attribute/text/tail。展示 label、复制路径和机器 locator 是不同概念。

### 7.2 审阅写回

审阅记录保存 source_hash + NodeRef + 原值/类型。提交时核验 source 未变化，再修改对应 ParsedDocument/安全原文。生成结果先写候选、重解析、检查只出现预期语义差异后才能发布。

“保真”定义为**未选业务语义不丢失**，不是无依据承诺 XML 字节完全不变。标准库无法可靠保留的 comments/PI/namespace/encoding/特殊 XML 构造必须实测；如果真实样本需要，优先评估成熟 XML 库，否则拒绝不安全自动生成。不要自行写完整 XML parser/rewriter。

## 8. 安全边界

### 8.1 身份与授权

保留现有内网 IP 轻量授权，但明确它不是强认证。页面/HTTP 只负责取得 Actor 与目标 profile；应用操作使用 `authorize(actor, action, profile_id)`。写操作在提交点重新校验。

显式 `default` 不再回读浏览器选择；未知 profile 不回退 default。后台任务使用 system actor + 固定 profile。只接受明确受信代理的转发来源，避免同机反向代理让所有请求看起来来自 loopback。

当前 IP/admin 表按 profile 保存，整改保持该语义；未来若出现共享终端、不可信内网或部门隔离需求，再重新评估本地认证/组织身份接入。

### 8.2 文件与 URL

所有路径经过单一 filename + root containment 规则。拒绝路径穿越、绝对路径、UNC/驱动器/ADS、越界 symlink 等。保留安全 Unicode 文件名。

上传和 URL 下载都有可配置容量、超时、解析节点/深度预算。访客临时 URL 导入采用比部署者配置更新源更严格的允许策略；http/https 重定向每跳重新校验。userinfo/query token 不显示在普通日志和访客页面。

### 8.3 HTML / JavaScript

配置、路径、备注和外部记录永远是数据。普通文本用安全文本 API；必须生成 HTML 时逐个 escape，固定模板才允许 raw HTML。JS 参数使用 JSON 编码，不拼 selector/脚本字符串。外链校验 scheme 并使用安全打开方式。

## 9. 写入、备份与恢复

### 9.1 单文件提交

使用同目录唯一 temp → serialize/write → flush/fsync → `os.replace`。Windows/POSIX 差异明确记录。权威 JSON 损坏与不存在必须区分；损坏时进入只读保护，不返回空列表后继续覆盖。

完整 read-modify-write 进入 profile 级锁。网络、解析和大 diff 在锁外做；提交锁内重新检查 expected hash。配置更新先保留旧版本，再原子切换当前。归档/record 名称使用独占唯一创建。

### 9.2 WriteGate

单进程全局 WriteGate 只做写入准入，不替代 profile 锁。备份/升级/恢复切到 maintenance：拒绝或排队新写，等待在途提交结束；读操作继续。关闭服务同样先停止新任务，再到安全点结束。

### 9.3 跨文件恢复

只有迁移、改名、隔离删除、审阅发布等真实跨文件操作使用 operation manifest。它记录 operation id、预期 hash、备份/源/目标、已完成步骤和恢复状态。不要把所有普通字段修改都包装成伪事务。

恢复时遇到不同 hash 的新目标必须保留现场并进入 `RECOVERY_REQUIRED`，不能“先删冲突再回滚”。

### 9.4 备份

一致备份在 WriteGate maintenance 下完成，覆盖全部权威原文、业务 JSON、布局/运行配置；cache 和 temporary 可排除。storage secret 属于敏感恢复材料：运维恢复备份可以包含但必须受限保护；用户可移植/导出包默认不包含 secret 和 `.nicegui` session。

升级失败优先回退代码并保留升级后新增业务数据；不能用旧快照粗暴覆盖所有新变化。

## 10. 更新与任务执行

保留 APScheduler，一个服务进程只有一个 scheduler。手动和定时更新调用同一 operation：

```text
download to staging
→ size/scheme/source validation
→ parse
→ minimal structural fingerprint validation
→ compare expected current hash
→ atomic commit/archive
→ derive cache/diff
```

XML root 或 JSON root type 异常变化时，定时任务拒绝；手动管理员操作可以展示差异后显式确认。

页面自己的周期性 record 拉取移除；APScheduler 负责周期任务。**手动 record 刷新保留**，但只是触发同一服务端 task。

任务执行器有并发和队列上限，按 profile+resource 防重入。取消只能在安全边界生效。UI 控件对象不进入线程；任务结果由当前仍存活的 client/context 显示。

## 11. 派生缓存

parse/diff/search cache 都是可丢弃数据。cache key 至少包含：source hash、算法/parser/differ version、cache schema version；绑定相关 diff 还需要 binding revision/hash，或把绑定统计实时计算。

任何版本不匹配都 miss。缓存半写只删除缓存，不能影响源文件。清空全部 cache 后核心业务必须可重新工作。

## 12. UI/UX 目标

保留左侧文件列表 + 中央标签工作区。左侧使用紧凑可搜索列表，右侧低频工具可收起；主区域优先给长树和 diff 空间。不要改成 Dashboard。

统一页面要求：

- FileRef 的 profile/source/version 明确可见。
- Loading 表示真实操作，不制造虚假百分比。
- success 只在业务提交完成后显示。
- empty / no match / forbidden / failed 分开表达。
- 表单使用 label，不只靠 placeholder；错误贴近字段并保留输入。
- CRUD 优先局部刷新，`location.reload()` 不是默认方案。
- 危险操作显示范围、恢复方式和当前任务占用。
- 树值可选择/复制/展开，hover-only 操作必须可通过 focus/touch 访问。
- 带未保存草稿的 tab 不能被标签上限无提示淘汰。

## 13. 错误与日志

少量业务错误 code 足够：`INVALID_INPUT`、`FORBIDDEN`、`NOT_FOUND`、`SOURCE_CHANGED`、`REVIEW_CONFLICT`、`TOO_LARGE`、`BUSY`、`DOWNLOAD_FAILED`、`STORAGE_FAILURE`、`CORRUPT_DATA`。不要为了每个 code 建异常类层级。

标准 logging 记录 operation_id、profile、actor、target、result、duration、error code。不得记录完整配置值、Cookie、secret、完整凭据 URL。绝对路径和堆栈只进入部署端受控日志，不回显给普通用户。

启动日志应报告实际端口、data root、锁定依赖版本、scheduler 状态和待恢复 operation。

## 14. 离线发布

发行环境必须锁定 Python、NiceGUI、APScheduler 及传递依赖，并针对实际目标 OS/ABI 准备 wheelhouse/离线安装脚本。先支持真实部署平台，不为未知平台提前维护包矩阵。

浏览器冷缓存、服务器无公网时必须加载本地 NiceGUI/Quasar/icon/font/JS/CSS。`MCHECKER_PORT` 和可选 data root 必须真实生效且升级不意外改变旧部署。

发行包与 Git 仓库应排除业务 data、storage secret、`.nicegui` 开发用户状态、`__pycache__`、`*.pyc` 等。当前已跟踪的 `.nicegui` 主要包含 UI tab/device_model 状态；仍应从版本控制移除并检查历史，而不能因此断言其中存在密码。

## 15. 测试与发布门禁

测试隔离是 P0。统一测试根必须在导入捕获路径的业务模块前建立；测试期间任何权威写路径落到 fixture 根外都应立即失败。ContextVar、scheduler/cache singleton 和用户状态在测试间重置。

测试层：

- 纯算法：parser/locator/diff/DL。
- 持久化：原子写、锁、WriteGate、故障注入、同秒归档、损坏保护。
- 应用操作：authorization、profile、source hash、幂等、部分失败。
- route/session：path、temp owner、profile、旧 URL。
- browser：核心工作流、草稿、键盘、来源版本。
- release：断公网首装首访、备份、升级中断、恢复。
- performance：固定样本、冷暖 cache、长会话释放。

T19 是最终发布门，不替代每个 P0 随修复提交的回归。

## 16. Current → Target → Why → Migration

| Current | Target | Why | Minimum Migration |
|---|---|---|---|
| 隐式 browser/profile fallback | Actor + explicit profile | 越权/串空间 | 先修 default，再逐入口显式参数 |
| 各处直接 JSON/file write | atomic I/O + profile lock + WriteGate | 截断/丢写/备份不一致 | 保持文件格式，替换 writer |
| move current 后再写新 | preserve old + atomic replace | 中断留下缺失当前版 | 保留旧 archive 读取 |
| 展示树承担全部语义 | SourceSnapshot + single ParsedDocument + UI projection | 丢字段/重复模型/定位漂移 | 新增 hash+locator，旧 path 兼容 |
| node index 审阅最新树 | source hash + NodeRef + original value/type | 过期误改 | 旧建议重新核验 |
| page 自己组合写流程 | 少量 application operations | 规则复制 | 一次迁移一个真实用例 |
| page 定时 record fetch + scheduler | scheduler 周期 + page 手动 task | 重复拉取 | 保留手动按钮，移除 page 周期抓取 |
| mtime/size cache | source hash + algorithm/schema version | 混合旧缓存 | 旧 cache miss 重建 |
| archive diff 当历史数据 | derived diff cache | bindings/算法变化后陈旧 | 兼容旧 `.diff.json`，新结果进 cache |
| 启动搬迁/物理删除 | operation manifest + recovery | 半完成无法恢复 | 先备份/复制校验/隔离删除 |
| 无一致备份屏障 | WriteGate maintenance backup | 复制到一半的数据不一致 | 单进程轻量准入，不引入分布式锁 |
| 版本下限依赖 | locked offline release | 安装不可复现 | 从当前可用环境锁定，不顺手升级大版本 |

## 17. 明确不做与未来扩展边界

现在建立：显式 profile/Actor、FileRef、SourceSnapshot/NodeRef、少量应用操作、可版本化 JSON/cache、WriteGate、TaskStatus、共用树组件。

现在不做：

- React/Vue 独立前端重写。
- 微服务、Celery/Redis、消息总线、插件系统、工作流引擎。
- ORM/schema registry、全量 REST 化。
- 未经测量的数据库/搜索索引迁移。
- 自动跨版本节点合并/全局节点注册中心。
- 新的全局 offline mode。
- 零停机代码热更新。
- Dashboard、大屏或完整主题系统。
- 自动删除历史/恢复数据的全局保留策略。

重新评估触发条件：

- SQLite/数据库：文件式恢复持续膨胀、真实并发超出单进程边界、已测复杂查询成为主要瓶颈。
- SSO/部门隔离：出现共享终端、不可信内网、正式审计或明确组织权限需求。
- 多进程/多实例：存在明确吞吐/高可用指标且单进程成为实测瓶颈。
- 新前端：NiceGUI 经实际验证无法满足已确认交互或离线约束。
- 新依赖：标准库实现无法可靠满足真实 XML/数值样本，且成熟库的许可、离线包和维护成本更低。

## 18. 文档事实源

从本文件起：

1. `docs/MCChecker_ARCHITECTURE.md`：目标架构、边界和设计决策。
2. `docs/MCChecker_ROADMAP.md`：T01–T20 的阶段、依赖、验收与 Linear 映射。
3. Linear：执行状态、Milestone 和真实 blocks/blockedBy。
4. `docs/architecture.md`：Agent 兼容入口，只摘要当前实现并指向本文件。
5. `MCChecker_ARCHITECTURE_v2.md` / `MCChecker_ROADMAP_v2.md`：历史审查记录，不再作为执行状态源。

任何实现完成状态必须由代码与测试证明；更新文档或 Linear 不等于功能已经完成。
