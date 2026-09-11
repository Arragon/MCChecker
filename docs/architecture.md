# Project Architecture

> 本文件是兼容入口，不再作为完整架构设计事实源。
>
> 当前正式架构设计：`docs/MCChecker_ARCHITECTURE.md`
> 当前正式开发计划：`docs/MCChecker_ROADMAP.md`

## Overview

MCChecker 是一个基于 Python + NiceGUI 的内网配置文件解析与管理工具。

当前实现基线：

- Runtime: Python
- UI/Web: NiceGUI
- Scheduler: APScheduler
- Parsing: XML/JSON
- Storage: 本地文件 + JSON
- Diff: 本地结构/文本比较

## Current Implementation

当前主要目录：

```text
app/
├── core/       # parser, storage, scheduler, differ, downloader, dltool 等核心逻辑
├── pages/      # NiceGUI 页面与交互
├── utils/      # 辅助能力
└── static/     # 本地静态资源

data/
└── profiles/   # 按机型/profile 保存业务数据

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
