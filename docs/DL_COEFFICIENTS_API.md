# DL 系数按时间查询 API

读取指定工作区、机型、计算模块在时间点 `at` 生效的**完整计算配置**。选择规则为 `effective_at <= at` 中最后一次生效的配置，区间为 `[effective_at, valid_until)`。例如 10 月 8 日保存 A、10 月 11 日保存 B，查询 10 月 9 日返回 A，查询 10 月 11 日的精确生效时刻返回 B。生效时间是服务端成功保存时间，不支持追溯改写或预约生效。

## 查询

`GET /api/v3/workspaces/{workspace_id}/profiles/{profile_id}/dl/{module_id}/coefficients?at={timestamp}`

`at` 必填，使用含秒、显式时区的 ISO 8601，例如 `2026-10-09T00:00:00+08:00` 或 `2026-10-08T16:00:00Z`，支持最多六位小数秒。URL 中的 `+` 必须编码为 `%2B`；推荐用客户端的查询参数编码功能。日期、无时区时间、非法日期均返回 400。

```sh
curl --get 'http://127.0.0.1:50012/api/v3/workspaces/default/profiles/default/dl/pp_energy/coefficients' \
  --data-urlencode 'at=2026-10-09T00:00:00+08:00'
```

部署时将主机端口换成 MCChecker 实际服务地址；沿用现有网络访问边界和身份识别。此只读接口不需要另设 Token，也不要求写请求的 CSRF 头。管理员、访客都可读取；不会改变用户身份、权限或服务监听范围。

成功响应示例（省略部分系数）：

```json
{
  "workspace_id": "default",
  "profile_id": "default",
  "module_id": "pp_energy",
  "requested_at": "2026-10-08T16:00:00.000000Z",
  "effective_at": "2026-10-08T01:00:00.000000Z",
  "valid_until": "2026-10-11T01:00:00.000000Z",
  "revision_id": "dt_000000001",
  "configuration": {
    "coeffs": {"k1": 8.0, "k2": 0.0, "b": 0.0},
    "x_min": null,
    "x_max": null,
    "y_min": null,
    "y_max": null,
    "trans_coeff": 9.0,
    "power_conv_coeff": 2.0,
    "binding": {"source_file": "", "variable_path": "", "field_map": {}}
  },
  "coverage": "timeline",
  "interval_verified": true,
  "tracking_started_at": "2026-10-08T01:00:00.000000Z",
  "history_id": "ch_000001"
}
```

实际 `coeffs` 始终包含 `k1`–`k8` 和 `b`。PP 能量还包含传输系数与功率转换系数；RP 脉宽同时返回 delay 的顶层参数、width 的 `aom_width` 及 `aom_sum`，不会混用两套系数。`valid_until=null` 表示暂无下一次已记录变更。未来时间只能返回目前已知的最后配置，不代表有预约配置。`revision_id` 在同一工作区/机型时间线内稳定。

| HTTP | 含义 |
| --- | --- |
| 200 | 返回已记录的配置及证据范围 |
| 400 `INVALID_INPUT` | 缺少时间、时间格式/时区不合法 |
| 404 `NOT_FOUND` | 工作区/机型/模块不存在，或指定时间之前无可用记录 |
| 409 `REVISION_CONFLICT` | 当前配置与新生效时间线不一致，需管理员核对，不返回推断值 |
| 500 | 文件损坏或存储错误，不返回默认零系数代替 |

## 查找机型和模块

- 工作区：`GET /api/v3/workspaces`
- 机型：`GET /api/v3/workspaces/{workspace_id}/profiles`，使用返回的 `id`，不是界面显示名。
- 模块：`GET /api/v3/workspaces/{workspace_id}/profiles/{profile_id}/dl/modules`，返回 `items: [{module_id, name}]`。

| module_id | 模块 |
| --- | --- |
| `pp_energy` | PP 能量 |
| `rp_energy` | RP 能量 |
| `rp_width` | RP 脉宽 |
| `ppz` | PPz |

## 记录与历史边界

成功保存参数、保存完整模块配置，以及应用历史导致实际参数变化时，都会追加生效记录；相同配置重复保存不制造新版本。历史恢复在恢复时刻生效，不改写旧时段。界面历史的 200 条保留限制和管理员删除操作只影响展示记录，生效时间线持续保留，避免外部调用的历史结果随清理而变化。

数据继续位于当前工作区/机型的 `dl_coeff_history.json`，增加兼容的 `timeline`、`timeline_next`、`tracking_started_at` 字段；不改 `dltool_config.json` 的结构，不迁移或覆盖旧系数。GET 永不创建历史或配置文件。普通写入异常会回滚本次配置与历史字节；进程/断电发生在两个文件替换之间时，按时间查询会检测当前快照不一致并拒绝推断。

旧历史记录没有显式时区，按原服务本地时区解释，并以 `coverage=legacy_history`、`interval_verified=false` 返回；它只能证明当时保存过该配置。旧版本中已经删除的记录、未记时间的历史恢复，以及以前服务器时区变化无法自动重建。需要严格追溯的调用方必须检查 `interval_verified`；新时间线开始后的受支持保存路径可验证。没有记录的时间返回 404，不用当前配置或默认零值补齐。

现有完整模块保存、参数保存和应用历史 API 仍是写入入口；此功能只新增只读对外接口。直接编辑磁盘文件或以不支持时间线的旧程序写入不在记录范围内，发现快照不一致时需人工核对，不能自动修补生效时间。
