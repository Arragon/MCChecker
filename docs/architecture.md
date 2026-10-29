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

## V3.0（计划，未实现）

V3.0 的设计事实源是 Linear INH-2117 的原型 HTML + 后端设计 MD，仓库镜像见：

- 开发计划：`docs/MCChecker_V3_PLAN.md`
- 设计基线/追踪矩阵：`docs/MCChecker_V3_BASELINE.md`
- 契约与替换清单：`docs/MCChecker_V3_CONTRACT.md`
- 原型 UI parity matrix：`docs/MCChecker_V3_UI_MATRIX.md`
- 原型/MD 冲突 ADR：`docs/adr/ADR-007-011-v3-ui-contract.md`

V3 的架构方向（计划，不代表已实现）：

- Workspace 是全应用业务数据边界；Profile 在 Workspace 内；全局身份/权限唯一，不随工作区切换。
- 新 API 前缀 `/api/v3`；v1 保留动作/包络但业务请求需带工作区上下文；旧无 wid 链接只读固定 legacy workspace。
- 文件统一为 FileResource/FileVersion（file_id/version_id/sha/tombstone）；左下“配置文件”是唯一持久文件入口，不建第二上传库。
- 配置管理改为 FixedConfig/SoftwareRelease/参数身份与定义/BindingSet/D0/Bn/Machine rn/草稿/不可变 head；旧 config-domain 的 ChangeSet/registry/delivery/upgrade/lifecycle/audit 编排替换、停写或只读。
- 原 remarks/review、速览、文件、搜索、DL、工具、设置、机型、更新源、任务与旧链接保留；发布只移动管理 head，不做物理机台下发。

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
├── ip_mapping.json    # 人员 IP 对应表，全机型共用
├── admin_users.json   # 管理员名单，全机型共用
└── profiles/          # 按机型/profile 保存配置与业务数据

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
