# SDA 模拟环境接线 Implementation Plan

> **For agentic workers:** 使用 superpowers:executing-plans，逐项实施和验证；保留现有未提交改动与 .qoder/。

**Goal:** 在自建隔离 SDA 服务上验证正式应用、私有 IPC、实际浏览器登录、加密凭据及同步提交闭环。

**Architecture:** 复用 collector_client/ipc、SessionManager、SdaLogin、SecretStore 和现有同步提交层。部署配置显式启用接线；未配置时保持普通 MCChecker 离线可用。模拟服务仅在测试工具中，不把模拟结果注入生产 UI。

**Tech Stack:** 主应用 Python 3.9 标准库；独立 Python 3.12 + Playwright/cryptography；隔离 Chromium。

**Spec:** 用户提供的 MCChecker_V3.1_Backend_Design.md；用户 2026-11-07 明确以自建模拟环境替代真实 SDA 联调输入。

## Global Constraints

- 管理员/访客两档；凭据不进 argv、业务 data、日志、诊断或 Git。
- 保留 HTTPS/443 与 origin 边界；模拟器使用专用 CONNECT 代理到 loopback 临时端口及专用测试证书，不改系统信任库。
- 私有凭据存取操作只通过受管 stdio；只返回安全元数据。
- 工作区/机型、来源修订、auth_epoch、绑定和文件版本提交守卫保持有效。
- 模拟环境通过不代表真实 SDA、Windows/Linux 或物理设备验收。

## Review Focus

- 密码错误/表单变化锁存，不覆盖已有 active generation；未经实际验证不能激活。
- 过期只重登一次，匿名目录成功不构成认证/核验。
- 超时、取消、异常帧和旧 epoch 不提交文件；已提交项可追溯。
- 改名和并发编辑不能改变目标文件身份或覆盖刚改的本地文件。
- 表候选必须有可回读精确 FileVersion；机台必须有采集响应中的可信设备身份。

### Task 1: TLS 模拟服务与受管 IPC

**Files:** scripts/sda_simulator.py、tests/test_sda_runtime_integration.py、collector/main.py、collector/runtime.py、app/services/collector_manager.py、app/services/collector_ipc.py。

- [x] 写实际子进程失败测试，涵盖存取密文、未知操作、取消/过期、协议和退出。
- [x] 将 main 接入分帧协议；保留 --self-test JSON；管理器提供二进制私有管道与客户端。
- [x] 回归现有生命周期/IPC/进程树测试。

### Task 2: 登录、凭据和应用启动接线

**Files:** app/services/sync_collector.py、run.py、collector/playwright_session.py、collector/runtime.py、app/services/sync_operations.py。

- [x] 从受保护部署配置和单独秘密环境启用；复用已有 browser/session/login/vault。
- [x] 验证管理员创建候选、实际登录/probe、CAS 激活、轮换、撤销和访客拒绝。
- [x] 验证 Cookie 过期恢复、保活真实续期、错误锁存、旧 epoch 与跨工作区隔离。

### Task 3: 内容、表候选与机台观测

**Files:** app/services/sync_tasks.py、app/services/sync_source_tests.py、app/services/sync_collector.py。

- [x] SDA 内容通过实际网络返回，校验身份、大小、hash；当前输入测试不提交。
- [x] 表保存精确文件快照并复用候选提交，不发布 D0/Bn/rn。
- [ ] 机台观测：已确认旧 LogicalFile/全局观测库与 V3 W/P 的关联契约缺失，保持阻断，不以 FileResource ID 冒充旧逻辑文件或创建平行观测库。
- [x] 在等待取数期间冻结目标版本，测试并发修改/取消不覆盖。

### Task 4: 验收与交付

- [x] 运行新增端到端测试和相关后端回归；前端 143 项及完整浏览器回归。
- [x] 更新 architecture/know-how/R6 审计，列清模拟环境与真实平台证据边界。
- [x] agent-gc 先预览；只清理允许候选；保留测试服务与全部用户内容。

本地验收：143 项前端；824 passed、1 Windows 条件 skip；完整浏览器 38/28/1；运行详情末次修正另经前端和下载/375px 确认。TLS 模拟闲置 75 秒，实际经过 113.4 秒后受保护读取成功，登录仅 1 次。真正网络流式限额、pin 持久化及机台观测仍按 R6 审计列明，计划不据此标成全部功能完成。
