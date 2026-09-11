# MCChecker 开发路线图

状态：Canonical execution roadmap  
基准：`v1.0.1` HEAD `abe83cb20764d6c63a623078d7d3ec60fd4a7fe8`  
架构设计：[MCChecker_ARCHITECTURE.md](MCChecker_ARCHITECTURE.md)  
Linear：[MCChecker › Overview](https://linear.app/inhandy/project/mcchecker-9275450a22c3/overview)

## 0. 执行原则

目标：让 MCChecker 从当前可用工具演进为可靠内网工具，而不是重写为新平台。

顺序：

P0 正确性/数据安全/兼容/离线
→ 测试安全门槛
→ 应用边界
→ 核心工作流
→ 有证据性能优化
→ 视觉 polish

禁止：

- 为未来未知需求建设插件系统、微服务、工作流引擎。
- 为了“现代化”替换 NiceGUI。
- 没有规模证据就迁移数据库。
- 文档、Linear 或 Issue 创建替代实际实现和测试。

## 1. Milestones

### M1 · 修复正确性、数据安全与离线发布

目标：消除已确认 P0，并建立开发/发布安全基础。

任务：

| Task | Linear | Priority |
|-|-|-|
| T01 授权与显式 profile | INH-612 | P0 |
| T02 文件、上传与下载源边界 | INH-613 | P0 |
| T03 HTML/JS 注入边界 | INH-615 | P0 |
| T04 原子持久化、并发保护、WriteGate | INH-616 | P0 |
| T05 迁移、重命名、删除恢复 | INH-617 | P0 |
| T06 SourceSnapshot/ParsedDocument/NodeRef | INH-618 | P0 |
| T07 审阅版本校验与保真输出 | INH-619 | P0 |
| T08 FileRef 临时/归档/记录引用 | INH-620 | P0 |
| T09 更新验证与受控异步执行 | INH-621 | P0 |
| T10 解析与派生缓存一致性 | INH-622 | P0 |
| T11 DL 数值语义 | INH-623 | P0 |
| T12 可复现离线发布 | INH-624 | P0 |
| T13 测试数据隔离与回归门槛 | INH-625 | P0 |

验收：

- 测试不会污染真实 data/.nicegui。
- 写失败不破坏权威数据。
- 未授权写入失败。
- 审阅不误改、不静默丢字段。
- 更新错误页/错误结构不会覆盖当前配置。
- 冷缓存断公网基础安装和首访可验证。

### M2 · 明确应用边界与维护契约

| Task | Linear | Priority |
|-|-|-|
| T14 应用操作、错误、日志、文档契约 | INH-626 | P1 |

目标：形成少量稳定业务入口，不创建 Service/Repository 过度抽象。

验收：核心操作可脱离 NiceGUI 测试；文档事实源统一。

### M3 · 优化核心工作流与信息密度

| Task | Linear | Priority |
|-|-|-|
| T15 工作区与管理表单 | INH-627 | P1 |
| T16 树、搜索、版本连续性 | INH-628 | P1 |
| T17 草稿与任务结果 | INH-629 | P1 |

目标：

- 文件查看、搜索、比较、审阅连续。
- 临时/当前/归档来源明确。
- DL/审阅草稿不会误写共享配置。

### M4 · 验证性能与发布可靠性

| Task | Linear | Priority |
|-|-|-|
| T18 性能基线与局部优化 | INH-630 | P1 |
| T19 核心流程、离线、升级恢复验收 | INH-631 | P1 |

目标：

- 只优化测量出的瓶颈。
- 完成发行验收和恢复演练。

### M5 · 收敛非必要视觉样式

| Task | Linear | Priority |
|-|-|-|
| T20 视觉样式收敛 | INH-632 | P2 |

不阻塞可用版本。

## 2. 关键依赖

```text
T01/T02/T03/T04/T06/T11/T12/T13
              ↓
T05/T07/T08/T09/T10
              ↓
T14
              ↓
T15/T16/T17
              ↓
T18
              ↓
T19
              ↓
T20
```

说明：

- 安全补丁可以独立提交，不等待目录重构。
- T13 测试隔离提前，因为后续所有验证都依赖它。
- T12 离线包不需要等待所有 P0 功能完成，但最终组合验收由 T19 完成。

## 3. Deferred / Future Consideration

不创建 Issue：

| 项目 | 触发条件 |
|-|-|
| 数据库迁移 | 文件恢复复杂度或真实查询规模超过文件方案 |
| SSO/组织权限 | 出现正式身份、安全审计或部门隔离需求 |
| 多实例部署 | 单进程成为实际吞吐/可用性瓶颈 |
| React/Vue 重写 | NiceGUI 实测无法满足需求 |
| 插件系统 | 多个独立扩展需求真实出现 |
| 工作流引擎 | 长事务编排需求明确 |
| 自动历史清理 | 明确保留策略和业务授权 |

## 4. Definition of Done

一个任务完成必须同时满足：

- 代码实现。
- 对应测试/验收证据。
- 数据兼容确认。
- 文档同步。
- Linear 状态真实更新。

Milestone 和 Issue 创建不是完成证明。
