# M16-41 真实内网验收手册（BLOCKED-EXTERNAL）

> 状态：**BLOCKED-EXTERNAL**。本文件与 `scripts/sda_live_check.py` 只提供可执行的验收路径与记录格式；
> 在拿到授权内网、批准账号之前，本卡**不构成通过证据**，也不得把下面的空白表格当成 PASS。

## 0. 前置条件（缺一不可）

| 条件 | 说明 |
|---|---|
| 授权内网 | 能访问目标 SDA；出站在允许清单内，代理/CA 由部署端提供 |
| 批准账号 | 只读权限的普通账密；不绕过验证码/MFA |
| 测试表/文件 | 已批准的只读表与单文件各一个（最小样本） |
| 隔离环境 | 使用临时数据根（`--data-dir`），不得拿生产 `data/` 做验收 |
| 凭据注入 | 只用环境变量 `MCC_SDA_USER` / `MCC_SDA_PASSWORD`，不进命令行、不进文件、不进日志 |

## 1. 执行步骤

### 1.1 先 dry-run（不联网，可随时做）

```bash
cp scripts/sda_live_check.example.json /tmp/sda-live.json   # 按实际填写 origin/路径
./.venv39/bin/python scripts/sda_live_check.py --config /tmp/sda-live.json
./.venv39/bin/python scripts/sda_live_check.py --config /tmp/sda-live.json --json   # 机器可读计划
```

dry-run 只校验配置与输出脱敏计划，**任何情况下都不发请求**。

### 1.2 真实只读执行

```bash
export MCC_SDA_USER=...          # 交互式输入，不要写进 shell 历史
export MCC_SDA_PASSWORD=...
./.venv39/bin/python scripts/sda_live_check.py \
  --config /tmp/sda-live.json --execute --out /tmp/sda-live-result.json
unset MCC_SDA_USER MCC_SDA_PASSWORD
```

脚本只做：登录 → 受保护 probe → 单表读取 → 单文件读取 → idle 窗口后仅心跳再探测。
**不提交、不发布、不写业务数据**；重定向只允许同 origin；输出只含状态码、布尔、字节数与 sha256。

### 1.3 平台侧全链路（真实 UI + 真实服务）

| 步骤 | 动作 | 记录字段 |
|---|---|---|
| L1 | 管理员身份注册连接（无需独立运维 token） | 时间、origin（脱敏）、HTTP 状态 |
| L2 | 凭据导入 → 候选 → 激活 | 是否 202、op_id、auth_epoch |
| L3 | 来源绑定（catalog 表/文件） | connection_id、resource_ref、display_path |
| L4 | 单项更新（`POST .../sources/{sid}/run`） | run_id、终态、items 计数 |
| L5 | 候选生成 → 写入 V3 草稿 | candidate_id、draft_revision（**不发布**） |
| L6 | 关闭保活后再触发同步 | 是否重新登录、失败次数 |
| L7 | 坏密码连续 5 个周期 | 登录尝试次数应恒为 1（锁存生效） |

## 2. 证据表格（执行人填写，不得代填）

| 编号 | 内容 | 期望 | 实际 | 结论 |
|---|---|---|---|---|
| E2-1 | 真实普通账密登录 | 200 + 会话可用 | | |
| E2-2 | 受保护 capability probe | 200 且返回受保护内容 | | |
| E2-3 | 目录/表/文件三端点 | 200 + 内容指纹 | | |
| E2-4 | CSRF/Cookie 轮换 | Set-Cookie 轮换且旧值失效 | | |
| E3-1 | 跨真实 idle 仅心跳 | 仍可取受保护数据 | | |
| E3-2 | 失效后一次自动重登 | 只登录 1 次、安全重放 1 次 | | |
| E3-3 | 坏密码锁存 | 5 个周期登录次数 = 1 | | |
| E4-1 | CA/代理/域跳转 | 拒绝跨域、拒绝降级 | | |
| E4-2 | 超限/大文件 | 写前终止，staging 清理 | | |
| E4-3 | 网络故障 | 有界退避，不无限重试 | | |

> `heartbeat_validated` 只有在 **E3-1 跨真实 idle 成功** 后才能置 `true`；否则 UI 必须显示“保活待验证”。

## 3. 归因与回填

1. 每步失败按 `S01–S52` 归因（见 `docs/MCChecker_V3.1_PLAN.md` §7），写进 `docs/M16_RELEASE_GATE.md`。
2. 结果 JSON 只保留脱敏字段；原始响应、Cookie、CSRF、HAR、业务正文一律不落盘、不入库、不进 Linear。
3. 回填 Linear：M16-41 评论写明执行时间、环境、步骤、结论；只有全部步骤有真实证据才可改 Done。
4. M16-02 / M16-20 的 `heartbeat_validated` 与真实 idle 证据由本卡结果联动更新。

## 4. 明确不做

- 不用合成 fixture、HTML 原型、CI runner 或“推测能运行”替代真实证据。
- 不把本文件里的空白表格当作已完成模板提交。
- 不在本卡内做内网 SSO 账号验收、不改动任何生产数据。
