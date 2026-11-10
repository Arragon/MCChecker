# Project Architecture

> 本文件是兼容入口，不再作为完整架构设计事实源。
>
> **v2 设计（2026-10-15 批准，已实现）**：`docs/MCChecker_REDESIGN_SPEC.md`
> ——技术栈改为 Python 3.9.11 标准库 HTTP + JSON API + 原生前端（`app/web/`），零运行时依赖；
> 与旧实现的功能实现差异见 `docs/MCChecker_FEATURE_CHANGES.md`。
>
> 此前的架构设计：`docs/MCChecker_ARCHITECTURE.md`（数据安全、定位、审阅校验等原则继续有效；“保留 NiceGUI”的结论已被上述重构设计取代）
> 此前的开发计划：`docs/MCChecker_ROADMAP.md`
>
> **执行手册（进度台账、分支/PR 协议、单卡 SOP、接力入口）**：`docs/MCChecker_DELIVERY_PLAN.md`
>
> 下方“Current Implementation”已按 v2 运行代码更新；NiceGUI 页面与 APScheduler 已在 M4-07 移除。

## V3.1（M16，进行中）

V3.2 配置管理重构进行中，指定基线和完整验收清单见 `docs/MCChecker_V3.2_PLAN.md`。新增 W/P 内只读 `config-standards` / `config-machines` 索引，由 `config_v3_indexes.py` 从既有目录投影并分页，不建立平行存储。机台正式版本依据其 head 的不可变记录读取，草稿目标单独返回。标准选择与标准草稿取值来源均限定本软件版本，序号及历史持久格式沿用现有实现。

配置目录增加可选 file_id，供 V3.2 文件优先入口显式关联旧配置。`config-files` 只读投影唯一文件库和配置摘要，`PUT configs/{cid}/file` 复用配置写锁完成唯一归属校验。旧目录不迁移、不猜测文件名，原深链继续读取；删除后重传产生的新文件身份不接管旧配置历史。

V3.2 新版本可选择 `initial_mode=separate`，canonical D0 在原领域内继续承担首次保存标准，公开 initial_revision_id 保持空直到存在明确的软件初始快照，手工冻结不自动建立该身份。旧版本继续采用其原始 D0 契约。前端 config-evolution.js 仅投影真实保存记录和来源，不复制原型的内存业务模型。

配置对象的 `parameter-query` 是有请求体的只读 POST：从所选记录对应的定义快照筛选后分页，覆盖名称、文件字段、子目录、差异和本次修改。查询中的编辑覆盖只用于展示，不写目录或草稿；实际修改继续经领域 PATCH 校验。数值在浏览器保持精确文本，布尔值、null、未设值和空字符串不合并。

配置目录服务的 `configuration_record` 统一在同一配置内解析保存记录与活动草稿，供参数查询及元数据读取选择定义。`parameters` 和 `parameter-tree` 可携带 revision_id；同时传 release_id 时必须与记录归属一致。页面的普通分页、目录和参数树也传入记录 ID，避免仅按 release 读取时把新定义混入旧记录；管理全局参数的入口继续明确读取全局目录。

值导入计划公开预览时的草稿 ID/修订号，新客户端提交时同时校验该预览和当前草稿；不能更换请求中的修订号绕开重新预览。同次 request_id 保留计划回执，提交重放不重复导入或审计。导入新标准草稿与手动新建草稿共用 `draft_from_standard` 的同版本来源选择。按指定 V3.2 原型，普通导入只处理已有参数；仅名称模式只核对已有字段，不创建定义、不改草稿。新增参数仍由参数管理维护，不将未知字段按相似名称静默纳入版本。

V3.2 保存由前端顺序编排批量草稿 PATCH 与原有正式保存接口，不构成跨请求事务。机台写入可携带 expected_draft_id 防止同修订号的新草稿被旧请求命中；正式保存使用稳定 request_id 回执，重试返回原记录而非新的 head。标准回执保存在不可变记录中，打开下一份草稿后仍可识别旧请求。保存说明附于原记录，历史不覆盖。导出授权使用 config_read，仅生成已保存快照的独立文件，不修改配置、模板或设备；参数写入仍要求管理员。

机台升级通过既有 merge-plans 的显式 machine_id 模式进入 `config_v3_upgrade`，从实际活动草稿或正式 head 捕获会话。历史恢复形成的来源链追溯到真实标准，不将机台历史记录冒充版本标准。预览与提交同时校验草稿身份、修订、取值方式、参照来源、目标软件版本和 head；提交在配置写锁内再次核对。保持值自动保留，自定义与新标准同时变化须显式选择；只更新捕获的草稿，采用目标标准作为新来源，不发布、不移动正式 head。重复请求返回持久回执，旧通用合并流程保留。

`discard-draft` 只删除指定活动草稿，携带草稿 ID、修订和请求回执。已有机台清草稿后恢复正式记录的版本归属；从未保存的新机台可移除。未首次保存的软件版本仅在无后续版本、机台和更新来源引用时移除。锁顺序固定为工作区同步锁 → 配置写锁，与候选写入一致；历史快照不删除。前端可选 confirmLeave 由配置模块提供三项离开处理，宿主在切工作区、切机型和关页前调用；其他视图继续使用原确认行为。

文件内更新复用来源、候选和运行服务。来源按配置 ID 或该配置唯一文件身份匹配；候选归属读取其冻结 target_scope，运行归属读取启动时新增的可选 frozen.source_targets。后两者不按当前来源绑定回推历史。无冻结目标的旧运行记录保留在工作区历史，不冒认某个文件。筛选在分页前完成，不建平行文件库或事件库。

候选比较同时返回正式标准和当前活动标准草稿，来源基线继续来自原来源文件。编辑会话指纹覆盖活动草稿和已保存标准，写入在工作区锁与配置锁内再次核对；不能比较 D0 后盲写另一份草稿。API 路由只读取预览归属，服务先检查持久写入回执再验证预览，避免成功写入导致自身预览失效后，丢响应重试无法取回回执。候选的 review_state 是只读投影：写入草稿仍待保存，只有本软件版本的正式保存值与候选值相符才从待处理投影中移除；原候选状态机与审计格式保持不变。

权限决策（2026-11-06）：全局只有管理员和访客。已登记非管理员也显示访客；连接/凭据/保活管理由 `authz.integration_credentials_manage` 映射管理员并在每次请求强制校验，不再要求独立运维 token。旧 `deployer` 能力键仅保留管理员同义兼容；SecretStore 密钥与凭据加密不受影响。

设计/计划/ADR：`docs/MCChecker_V3.1_PLAN.md`、`docs/adr/ADR-012-020-v3.1-sync-contract.md`；
当前实现审计：`docs/M16_CURRENT_STATE.md`；采集器边界：`docs/M16_COLLECTOR.md`。

R6 接线修订：来源查询增加 configuration_id/release_id、失败/候选筛选与真实 freshness 投影；新增管理员来源输入检查 `/sync/source-tests` 和运行取消 `/sync/runs/{run_id}/cancel`。候选增加可选 `base_file_version_id`，只引用现有同一 FileResource 的精确版本，不迁移或覆盖旧记录。核验回执附来源绑定指纹，绑定变化后不能沿用旧核验或旧比较基线。来源显示名称不决定已绑定文件的存储路径。

独立 collector 现已接入分帧 IPC、Vault/probe/read 与独立保活循环。`run.py` 仅在 `MCHECKER_COLLECTOR_CONFIG` 显式配置时安装 `sync_collector.CollectorBridge`；秘密根与业务 data 必须分离，缺密钥不回退明文。主应用只通过受管二进制管道访问子进程，不 import collector 或第三方库。文件/表正文通过既有 meta + chunk 协议传输，元数据在同一次请求锁内冻结；凭据仅通过私有存取操作进入子进程。

用户指定使用自建模拟环境验收。TLS SDA 模拟器与固定 CONNECT 隧道在 `scripts/sda_simulator.py`，实际 Playwright 登录、Cookie 恢复、加密凭据、SDA 文件提交及表候选写草稿已通过本地端到端验证；真实 SDA 协议/目标平台仍未验证。SDA 表只把已验证 table_id 对应的 document 保存为精确 FileVersion，传输元数据不参与参数映射。来源、连接 policy_revision、auth_epoch 和目标版本均在提交前复核。

机台观测仍缺旧 LogicalFile 与工作区归属契约，不能把 V3 FileResource 的 ID 当旧 LogicalFile ID，也不能写入无 workspace 的旧共享观测库。具体缺口与证据边界见 `docs/M16_R6_UI_AUDIT.md`。

- 主应用仍是 Python 3.9.11 + 标准库；Sync/Collector 只做增量，不替换旧 v1 入口。
- Collector 是独立目录 `collector/`（Python 3.12 + Playwright/Chromium/cryptography），
  主应用不 import、不进核心 `requirements.txt`、不进核心发布白名单；由
  `app/services/collector_manager.py` 以受管子进程启动/停止/诊断，并纳入
  `run.ordered_stop`（排空任务后、关 HTTP/释放单实例锁之前）。
- 采集不可用只影响采集能力：无 collector 时普通 MCChecker 照常启动，状态显式
  `unavailable`/`collector_offline`。

## V3.0（代码已实现于 `feat/mc-v3`，未发布）

V3.0 的设计事实源是 Linear INH-2117 的原型 HTML + 后端设计 MD，仓库镜像与验收记录：

- 开发计划：`docs/MCChecker_V3_PLAN.md`
- 设计基线/追踪矩阵：`docs/MCChecker_V3_BASELINE.md`
- 契约与替换清单：`docs/MCChecker_V3_CONTRACT.md`
- 原型 UI parity matrix：`docs/MCChecker_V3_UI_MATRIX.md`
- 原型/MD 冲突 ADR：`docs/adr/ADR-007-011-v3-ui-contract.md`
- Q1–Q6/A01–A17 追踪：`docs/V3_CONTRACT_TRACE.md`
- UI/真实浏览器：`docs/V3_UI_ACCEPTANCE.md`；集成/离线/性能：`docs/V3_INTEGRATION_EVIDENCE.md`
- 发布门禁：`docs/V3_RELEASE_GATE.md`（**仍 BLOCKED，未发布**）

已实现的架构边界：

- Workspace 是业务数据边界，Profile 在其内；全局身份/角色/管理员/IP 映射唯一，不随工作区复制。W=default 仍映射旧 `data/profiles/`，其他 W 在 `data/workspaces/<wid>/profiles/<pid>/`。
- 新 API 前缀 `/api/v3`；v1 保留动作/包络，写请求需 `X-Workspace-Id`，无 wid 写 428、冲突 409、跨区枚举 404；旧无 wid 深链只读 legacy workspace。
- 文件统一为 FileResource/FileVersion（`file_id/version_id/sha/tombstone`，`services/file_versions.py` + `config_v3_provenance.py`）；左下“配置文件”是唯一持久文件入口，无第二上传库。file-selector/file-versions API 在 `server/api_v3_files.py`。
- 配置管理：FixedConfig/SoftwareRelease/参数身份/定义快照/BindingSet/D0/Bn/Machine rn/草稿/不可变 head；核心在 `services/config_v3_*.py`（catalog/drafts/baselines/machines/projection/diff/merge/merge_plans/import/export/history/extract/plans），API 在 `server/api_v3_config.py`、`api_v3_plans.py`、`api_v3_exports.py`、`api_v3_events.py`；数据在 `<profile>/configs_v3/`。
- 提取（名称/层级，零写预览）、值导入（按已确认绑定）、O/L/N 合并（只产新草稿）、精确模板导出（不可变 artifact）、事件/恢复（新草稿，不改历史）彼此分离；发布只移动管理 head，不做物理机台下发。
- 旧 config-domain 的 ChangeSet/registry/delivery/upgrade/lifecycle/audit 编排保留只读兼容；`MCHECKER_CONFIG_MANAGEMENT=readonly` 可停写。原 remarks/review、速览、文件、搜索、DL、工具、设置、机型、更新源、任务与旧链接保留。
- 配置视图新增机台草稿值 PATCH、草稿/不可变修订 GET 与参数目录维护；复用配置域 CAS、管理员权限和 W/P 隔离。全局参数身份与每版冻结定义独立；空目录只增加可选定义元数据。导出支持本次映射和分页读取，保留来源、模板、产物真实身份。
- DL 对外按时间读取：GET /api/v3/workspaces/{wid}/profiles/{pid}/dl/{item}/coefficients?at=...，模块发现同域 /dl/modules。dl_history.py 在既有历史文件保留生效时间线，受支持保存/恢复路径记录完整快照；展示历史删除/限额不影响时间线，旧记录标明不完整。普通 I/O 异常回滚、进程中断的不一致拒绝推断。详见 docs/DL_COEFFICIENTS_API.md。
- 前端 `views/config-manager.js` 是原生配置管理视图；旧 `configspace` 深链映射到 configv3；宿主仍提供唯一文件列表、工作区/机型切换和全局搜索。
- 迁移工具：`scripts/inspect_v3_workspace.py`、`backup_v3.py`、`migrate_v3.py`；发布包包含迁移/回退脚本。

## Overview

MCChecker 是一个基于 Python 标准库 HTTP 服务与原生前端的内网配置文件解析与管理工具。

当前实现基线：

- Runtime: Python
- UI/Web: 原生 JavaScript（`app/web/`）
- Scheduler: 标准库线程 + `services/scheduler.py`
- Parsing: XML/JSON
- Storage: 本地文件 + JSON
- Diff: 本地结构/文本比较

## Current Implementation

当前主要目录：

```text
app/
├── core/       # parser, storage, differ, downloader, dltool 等核心逻辑
├── server/     # HTTP 分发、路由、legacy 兼容、api_*.py
├── services/   # 业务服务（含 scheduler）
└── web/        # 原生前端（index.html + assets/js）

data/
├── ip_mapping.json        # 人员 IP 对应表，全局共用
├── admin_users.json       # 管理员名单，全局共用
├── workspace_catalog.json # V3 工作区元数据（不存在时合成 legacy default）
├── profiles/<pid>/        # legacy/default 工作区；其下 configs_v3/ 为 V3 配置数据
├── workspaces/<wid>/profiles/<pid>/  # 非 default 工作区；同样有 configs_v3/
└── config_management/     # 旧 M5–M8 配置域（只读兼容）

tests/
└── pytest 回归测试
```

## Architectural Direction

项目保持模块化单体，不进行前后端重写或微服务拆分。

未来架构边界：

```text
Pages / HTTP
    ↓
Application Operations
    ↓
Core Domain Logic
    ↓
Persistence Boundary
    ↓
Local Files + JSON
```

详细设计、迁移策略、安全边界和 Roadmap 请阅读：

- `docs/MCChecker_ARCHITECTURE.md`
- `docs/MCChecker_ROADMAP.md`

## Important Note

旧版本文档中的以下描述已过时，不应作为开发依据：

- NiceGUI 必须依赖公网资源
- 展示树直接作为写回模型
- profile 等同权限隔离
- 审阅生成可以直接重建配置

这些问题已经在正式架构设计中重新定义。
