# MCChecker Architecture

> 本文件是 Agent 兼容入口。正式架构设计请查看：
> - 目标架构：[docs/MCChecker_ARCHITECTURE.md](MCChecker_ARCHITECTURE.md)
> - 开发路线图：[docs/MCChecker_ROADMAP.md](MCChecker_ROADMAP.md)
> - Linear：[MCChecker Project](https://linear.app/inhandy/project/mcchecker-9275450a22c3/overview)

## Overview

MCChecker 是一个基于 Python + NiceGUI 的内网配置文件解析与管理工具。

当前实现基线：

- Runtime: Python 3.9 / 3.11 / 3.12
- UI/Web: NiceGUI v2+
- Scheduler: APScheduler v3.10+
- Parsing: XML / JSON
- Storage: 本地文件 + JSON
- Diff: 本地结构/文本比较

## Current Implementation

```text
app/
├── core/           # parser, storage, scheduler, differ, downloader, dltool
│                   # errors (统一错误码), logging_config (日志脱敏)
├── pages/          # NiceGUI 页面与交互（不直接暴露 storage 细节）
├── utils/          # 辅助能力（auth, helpers）
└── static/         # 本地静态资源

data/
└── profiles/       # 按机型/profile 保存业务数据

tests/
└── pytest 回归测试（428+ 用例）
```

### 模块依赖边界

```text
Pages / HTTP
    ↓
Application Operations  ← errors.py / logging_config.py
    ↓
Core Domain Logic       ← differ, parser, reviewing, operations
    ↓
Persistence Boundary    ← storage (唯一可读写磁盘的 core 模块)
    ↓
Local Files + JSON
```

- `core` 模块之间单向依赖，`differ` 不直接读 storage，由调用方注入 binding snapshot
- `pages` 不 import 其他 `pages`
- `storage` 不依赖浏览器状态

## Architectural Direction

项目保持模块化单体，不进行前后端重写或微服务拆分。

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
