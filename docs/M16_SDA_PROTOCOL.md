# M16 SDA 协议与心跳契约（M16-02 / INH-2167）

> 状态：2026-11-02。**合成 fixture / 离线协议契约已完成；真实内网登录、真实 idle 心跳与真实三端点证据未取得（E2/E3 BLOCKED-EXTERNAL）。**
> 设计基线：V3.1 MD §1、§6.1–6.2、§7、§11、§17、§20；计划：`docs/MCChecker_V3.1_PLAN.md`；审计：`docs/M16_CURRENT_STATE.md`。
> 本文件不保存真实账号、密码、Cookie、CSRF、HAR、公司业务原文或真实请求 URL；只定义合成契约、错误语义与待补现场输入。

## 1. 结论与证据边界

- **已完成（E1）**：`tests/fixtures/sda/{catalog,table,file,login,heartbeat}/cases.json` 的合成成功/鉴权失效/业务错误 fixture；离线测试 `tests/test_sda_protocol.py`。
- **未完成（E2/E3）**：真实普通账密登录、受保护 capability probe、真实三端点脱敏 request/response、真实 idle 窗口续期、Cookie/CSRF 轮换与自动重登。没有授权内网时不得猜字段、不得标 PASS。
- **硬规则**：`heartbeat_validated=true` 只能由真实 idle 实验设置；合成 fixture 永远为 `false`；UI 在未验证时显示“保活待验证”，不能谎称稳定保活。
- M16-02 因此在真实证据取得前保持 In Progress/Blocked；M16-20/41/42 同样不得 Done。

## 2. Adapter 边界（不猜上游未公开字段）

collector 只接受**已注册**的 adapter kind 与资源引用，不接受任意 URL、JavaScript 或本地路径。真实 SDA 的 method/body/CSRF 字段由 E2 授权会话补齐；本文件只冻结 MCChecker 侧的归一化 envelope。

| 层 | 输入 | 输出 |
|---|---|---|
| 主进程 | `workspace_id, connection_id, adapter_kind, resource_ref, deadline_at, limit_bytes, expected_policy_revision, expected_auth_epoch` | 安全进度、元数据、按序 chunk、最终 end（总字节 + sha256） |
| collector adapter | 上面字段 + 受控登录上下文 | `catalog` / `table` / `file` 的归一化结果；不含 Cookie/CSRF |
| 提交层 | 归一化结果 + 主进程重算的字节/sha | 端点语义校验 → 资源身份校验 → 业务文件/候选/观测 |

- `catalog`：只读目录；匿名成功不能证明已登录，也不能刷新 `last_verified`。
- `table`：只读表；成功 envelope 必须带稳定表身份与行身份；空表是合法结果，不等于失败。
- `file`：单文件；必须带资源身份、内容类型、实际字节数、sha256；主进程重新计算后才提交。
- 真实上游不支持的能力保持 `Disabled`；不启用未知 adapter。

## 3. 登录与受保护能力

1. 每个 `(workspace_id, connection_id)` 使用一个非持久化 BrowserContext；不同连接不复用上下文。
2. 使用正常业务入口，等待实际账密表单；定位器放在部署侧版本化 adapter 配置，优先 label/role/name；业务 UI 不粘贴任意 JS。
3. 只允许已配置的 SSO/身份域跳转；登录成功必须到达允许的业务页，并通过当前所有启用来源要求的 **protected capability probe**。
4. 表读取 probe 不能冒称文件下载已验证；每个启用能力有独立 canary。
5. 密码错误、账号锁定、表单结构变化：立即 `locked`，后续心跳/计划周期不得重复提交同一错误密码；由更换凭据或运维明确“重试登录”解除。
6. 网络/collector 故障不判密码错误；1/5/15 分钟有界退避；没有等待任务/启用来源时不持续探测。
7. 不保存密码页面截图/录像/trace/HAR；密码只在部署秘密入口/运行时注入。

## 4. 心跳契约

- 初始产品建议 10 分钟，可配置 **1–60 整数分钟**；这不是服务方已测 TTL。真实下限由 E3 的 idle 窗口决定，必要时扩展经验证的下限。
- 心跳必须是“已证实可续期”的轻量只读请求；匿名目录/任意 200 响应不作为会话有效性或续期证据。
- `next_heartbeat_due = last_renewing_activity + interval`；正常成功取数只有确认同样续期时才可推迟心跳。
- 同一连接最多一个心跳；轻量请求与登录/正常受保护取数在同一连接串行。
- 成功心跳不改文件 hash/`last_verified`/files revision；它只更新连接健康/续期证据。
- 关闭保活/自动登录必须真正停止，不保留隐藏循环；暂停工作区自动计划不等于关闭连接保活。
- 不模拟鼠标/键盘活动，不为保活全量拉文件，不并发变更同一 CookieJar。

## 5. Cookie/CSRF 与安全

- BrowserContext 与 `BrowserContext.request` 共享 CookieJar；不要误用独立 `api_request.new_context()`。
- 不硬编码初次 Cookie 字符串，不按 name-only 字典折叠不同 path/domain Cookie；接收后续 Set-Cookie 轮换。
- CSRF、Origin/Referer 或页面 token 按真实协议单独处理；共享 Cookie 不等于请求完全等价于页面 JS。
- SDA 受保护取数禁止自动跟随任意重定向；登录导航与数据读取的重定向策略分开。
- Cookie/CSRF 值不经主应用任务参数、日志、Linear、Git、业务备份、HTML 或诊断 Blob。
- 应急 Cookie 导入只作排障恢复，产品建议 64 KiB；只解析纯 Cookie/受限结构化条目，不执行 cURL/shell/用户脚本；原始 Cookie 缺 Domain/Path/过期属性时不猜到期倒计时。
- 首版不做跨主机采集、账号池、扩展、公网服务或远程调试端口。

## 6. 错误语义

| reason_code | 含义 | 是否重试/重登 |
|---|---|---|
| `auth_failed` | 受保护端点确认认证失效（已知 500 空 body 是其中一种） | 单次 singleflight 重登 + 单次安全重放；再次失败终止本轮 |
| `business_error` | 200/envelope 内业务错误、错资源身份、错表 ID | 不重登；记录源级错误 |
| `network_error` | 超时/DNS/连接失败/collector 离线 | 有界退避；不判密码错误 |
| `redirect_blocked` | 跨站/降级/非法跳转 | 不跟随；不外泄 Cookie |
| `protocol_mismatch` | 版本/envelope/字段不符 | 不重登；停止该 adapter |
| `login_flow_changed` | 登录表单/按钮结构变化 | 锁存；人工处理 |
| `bad_credentials` / `locked` | 密码错误/账号锁定 | 锁存；不按周期重试 |
| `collector_offline` | collector 进程不可用 | 网络态；不判密码错误 |

- 普通 HTTP URL 500 绝不触发 SDA 账密登录。
- 200 登录页、错误 JSON、500 空 body、错资源身份不得进入业务文件/正常观测/审计原文库。
- 失败日志只保留安全元数据：adapter kind、资源引用（已脱敏）、阶段、reason_code、时间、字节上限；不保留请求 body/完整 URL/Cookie。

## 7. 现场证据清单

### E1（已完成）

- 三端点 success/auth_failed/business_error 合成 fixture。
- 登录 success/bad_credentials/flow_changed 合成 fixture。
- 心跳 renewed/not_renewed/expired 合成 fixture。
- 离线测试：`.venv39/bin/python -m pytest -q tests/test_sda_protocol.py`。

### E2（未取得，BLOCKED-EXTERNAL）

授权内网内记录：

- 正常账密登录一次；表单定位器/允许的 SSO 跳转；受保护 capability probe。
- 目录/表/单文件的真实 method/body/CSRF 需求与成功 envelope（脱敏）。
- 已知认证失败的真实表现（500 空 body 或其他）；业务错误 envelope。
- 合法最大响应/文件大小；企业 CA/代理/域跳转要求。
- **不记录**密码、Cookie、CSRF 值、HAR、业务原文；只记录布尔/枚举/上限。

### E3（未取得，BLOCKED-EXTERNAL）

- 单独发送已选择心跳、停止人工操作，跨真实 idle 窗口仍能取数。
- 记录 `renewal_observed`、`cookie_rotated`、`idle_window_minutes`、`validated_ttl_minutes`（只布尔/数值，不含值）。
- 只有跨真实 idle 成功，才允许 `heartbeat_validated=true`；否则 UI 显示“保活待验证”，单次登录仍可取数。

## 8. 验收追踪

| S | 场景 | 本卡落点 | 当前状态 |
|---|---|---|---|
| S07 | 目录匿名成功、内容未认证 | catalog fixture + adapter 规则 | 合成覆盖；真实待 E2 |
| S08 | 轻量心跳跨真实 idle | heartbeat fixture + E3 清单 | `BLOCKED-EXTERNAL` |
| S09 | 保活成功不改文件 freshness | 契约 §4 | 设计覆盖；真实待 E3 |
| S10 | 正常取数代替心跳 | 契约 §4 | 设计覆盖；真实待 E3 |

## 9. 运行与门禁

```bash
./.venv39/bin/python -m pytest -q tests/test_sda_protocol.py
./.venv39/bin/python -m pytest -q
PYTHON=./.venv39/bin/python bash scripts/check_py39.sh
```

- 本卡新增测试只读 JSON，不触网、不写 data；满足 H3 的无害路径。
- 真实内网实验脚本不进入普通测试；不得在 CI 跑真实登录；没有授权时保持 BLOCKED。
