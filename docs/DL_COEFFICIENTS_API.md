# DL 系数按时间查询 API 使用教程

读取指定工作区、机型、计算模块在时间点 `at` 生效的**完整计算配置**。选择规则为 `effective_at <= at` 中最后一次生效的配置，区间为 `[effective_at, valid_until)`。例如 10 月 8 日保存 A、10 月 11 日保存 B，查询 10 月 9 日返回 A，查询 10 月 11 日的精确生效时刻返回 B。生效时间是服务端成功保存时间，不支持追溯改写或预约生效。

## 查询

`GET /api/v3/workspaces/{workspace_id}/profiles/{profile_id}/dl/{module_id}/coefficients?at={timestamp}`

`at` 必填，使用含秒、显式时区的 ISO 8601，例如 `2026-10-09T00:00:00+08:00` 或 `2026-10-08T16:00:00Z`，支持最多六位小数秒。URL 中的 `+` 必须编码为 `%2B`；推荐用客户端的查询参数编码功能。日期、无时区时间、非法日期均返回 400。

```sh
curl --get 'http://127.0.0.1:50014/api/v3/workspaces/default/profiles/default/dl/pp_energy/coefficients' \
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


## 从零开始调用

本教程面向需要从其他程序读取 DL 系数的调用方，只介绍只读查询，不修改系数或历史。示例日期和数值用于说明契约，不表示你的环境已经存有这些历史记录。

### 第一步：确定服务地址

下面使用当前本地测试服务 `http://127.0.0.1:50014`。端口不是固定产品约定；重启或部署后应以实际服务地址为准。`127.0.0.1` 只代表调用程序所在的电脑，其他电脑调用时应替换为 MCChecker 服务器地址，并确保服务监听和网络规则允许访问。不要为调用接口随意开放公网访问。

macOS / Linux 终端先设置变量：

```bash
MCC_BASE='http://127.0.0.1:50014'
```

### 第二步：查工作区 ID

```bash
curl --fail-with-body --silent --show-error "$MCC_BASE/api/v3/workspaces"
```

响应为 `{"items": [...]}`，从所需工作区取 `id`。不要把工作区显示名称填进 URL。

```bash
MCC_WORKSPACE='default'
```

### 第三步：查这个工作区的机型 ID

```bash
curl --fail-with-body --silent --show-error   "$MCC_BASE/api/v3/workspaces/$MCC_WORKSPACE/profiles"
```

同样从返回的 `items` 中选择机型的 `id`。这里的机型不是单台设备编号；查询范围是“工作区 + 机型 + 计算模块”。不同工作区即使存在相同的机型 ID，也需要分别查询。

```bash
MCC_PROFILE='default'
```

### 第四步：确认模块

```bash
curl --fail-with-body --silent --show-error   "$MCC_BASE/api/v3/workspaces/$MCC_WORKSPACE/profiles/$MCC_PROFILE/dl/modules"
```

响应的 `items` 每项包含 `module_id` 和 `name`。例如 PP 能量使用 `pp_energy`，无需填写中文名称。

### 第五步：查询并保存响应

```bash
curl --fail-with-body --silent --show-error --get   "$MCC_BASE/api/v3/workspaces/$MCC_WORKSPACE/profiles/$MCC_PROFILE/dl/pp_energy/coefficients"   --data-urlencode 'at=2026-10-09T00:00:00+08:00'   --output dl-coefficients.json
```

成功后 `dl-coefficients.json` 是完整 JSON 响应。先检查 HTTP 是否成功，再读取 `configuration`；不能把错误响应当成系数。较旧的 curl 若不支持 `--fail-with-body`，可使用 `--fail`，但失败时可能不输出错误正文。

## 时间选择的具体规则

假设 PP 能量分别在北京时间以下时刻保存：

| 保存时间 | 配置 |
| --- | --- |
| 2026-10-08 09:00:00 +08:00 | A |
| 2026-10-11 09:00:00 +08:00 | B |

| 查询时间 | 结果 |
| --- | --- |
| 2026-10-07 12:00:00 +08:00 | 若没有更早记录，返回 404 |
| 2026-10-09 00:00:00 +08:00 | A |
| 2026-10-11 08:59:59 +08:00 | A |
| 2026-10-11 09:00:00 +08:00 | B |

这是“查询时刻之前最后生效的一组”，不是按日期找最接近的一组，也不是取当天最后一组。只写 `2026-10-09` 不合法，需要明确具体时刻和时区。北京时间零点可写为 `2026-10-09T00:00:00+08:00`，它与 `2026-10-08T16:00:00Z` 是同一时刻。响应时间统一使用 UTC 的 `Z` 表示。

生效时间由服务端保存操作记录，接口不支持补录一个任意的过去生效时间。读取旧日期前，需要确认当时确实有记录；仅启动模拟服务不会凭空生成 10 月份的历史数据。

## Python 完整示例（无第三方依赖）

以下脚本可保存为 `query_dl_coefficients.py`，使用 Python 3.9 或更高版本执行。它只发送一次 GET，请求超时为 10 秒；网络失败不会伪造系数。

```python
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

BASE_URL = "http://127.0.0.1:50014"
WORKSPACE_ID = "default"
PROFILE_ID = "default"
MODULE_ID = "pp_energy"
AT = "2026-10-09T00:00:00+08:00"

path = "/api/v3/workspaces/{}/profiles/{}/dl/{}/coefficients".format(
    quote(WORKSPACE_ID, safe=""),
    quote(PROFILE_ID, safe=""),
    quote(MODULE_ID, safe=""),
)
url = BASE_URL.rstrip("/") + path + "?" + urlencode({"at": AT})
request = Request(url, headers={"Accept": "application/json"})

try:
    with urlopen(request, timeout=10) as response:
        result = json.load(response)
except HTTPError as exc:
    print("HTTP 错误：", exc.code, file=sys.stderr)
    print(exc.read().decode("utf-8", errors="replace"), file=sys.stderr)
    sys.exit(1)
except (URLError, TimeoutError, OSError) as exc:
    print("连接失败：", exc, file=sys.stderr)
    sys.exit(1)
except (ValueError, UnicodeError) as exc:
    print("服务没有返回有效 JSON：", exc, file=sys.stderr)
    sys.exit(1)

# 本示例要求可验证的生效区间；是否接纳旧历史由业务方明确决定。
if result.get("interval_verified") is not True:
    print("此结果来自旧历史，无法确认完整生效区间。", file=sys.stderr)
    sys.exit(2)

configuration = result["configuration"]
print("生效时间：", result["effective_at"])
print("下次变更：", result["valid_until"])
print("记录编号：", result["revision_id"])
print("多项式系数：", configuration["coeffs"])
print(json.dumps(configuration, ensure_ascii=False, indent=2))
```

执行：

```bash
python3 query_dl_coefficients.py
```

退出码 `0` 表示成功，`1` 表示请求或响应失败，`2` 表示返回了未经完整区间验证的旧历史。若需要允许旧历史，请由接入方制定明确规则，不要直接删掉检查后宣称结果已经过验证。

## JavaScript 调用示例

适用于支持 `fetch` 的运行环境。浏览器页面优先使用同源地址，避免跨域限制。

```javascript
async function getDlCoefficients(baseUrl, workspaceId, profileId, moduleId, at) {
  const ids = [workspaceId, profileId, moduleId].map(encodeURIComponent);
  const url = new URL(
    `/api/v3/workspaces/${ids[0]}/profiles/${ids[1]}/dl/${ids[2]}/coefficients`,
    baseUrl
  );
  url.searchParams.set('at', at);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(url, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      signal: controller.signal,
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${body.error?.message || '查询失败'}`);
    }
    if (body.interval_verified !== true) {
      throw new Error('返回旧历史，无法确认完整生效区间');
    }
    return body;
  } finally {
    clearTimeout(timeout);
  }
}

// 在 async 函数或支持顶层 await 的模块内调用。
const result = await getDlCoefficients(
  'http://127.0.0.1:50014', 'default', 'default', 'pp_energy',
  '2026-10-09T00:00:00+08:00'
);
console.log(result.configuration);
```

调用方应捕获异常并显示失败，不要用零系数补齐。跨域调用是否允许取决于部署规则；不要假定接口已经向任意网页开放跨域访问。外部系统可从自己的服务端调用。

## 返回字段说明

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| `workspace_id` / `profile_id` / `module_id` | string | 核对实际查询范围 |
| `requested_at` | string | 查询时刻，已换算为 UTC |
| `effective_at` | string | 本组设置的生效时刻，包含这个时刻 |
| `valid_until` | string / null | 下一次已记录变更，不包含这个时刻；null 不是永久不变的承诺 |
| `revision_id` | string | 生效记录编号，和工作区、机型一起使用 |
| `configuration` | object | 完整模块设置，不只是多项式系数 |
| `coverage` | string | `timeline` 或 `legacy_history` |
| `interval_verified` | boolean | 是否来自受支持的完整生效时间线 |
| `tracking_started_at` | string / null | 新时间线开始记录的时间 |
| `history_id` | string / null | 关联的展示历史编号；可为空 |

`configuration.coeffs` 包含 `k1` 至 `k8` 和 `b`；`x_min`、`x_max`、`y_min`、`y_max` 是范围设置，可能为 null。`binding` 是文件字段关联信息。PP 能量额外包含 `trans_coeff`、`power_conv_coeff`。RP 脉宽的顶层是 delay 设置，`aom_width` 是 width 设置，`aom_sum` 是固定和；接入时应保留整份配置，不能只取顶层系数后用于两种模式。

数值 `0` 和 `null` 含义不同，不要使用 `value || 默认值` 把零覆盖。此接口返回配置，不执行 DL 计算，不返回计算结果。

## 常见问题与排查

### 返回 400：时间不合法

检查是否填写秒和时区，以及 URL 中的 `+` 是否变成空格。推荐由 `urlencode`、`URL.searchParams` 或 curl 的 `--data-urlencode` 编码，不要手动拼接查询参数。

### 返回 404：找不到记录

先依次查询工作区、机型和模块列表，确认 ID 正确；然后确认指定时刻之前有该模块的保存记录。404 既可能是对象不存在，也可能是没有历史配置，应阅读响应中的错误信息。

### 返回 409：配置和时间线不一致

请管理员核对是否直接改过配置文件、使用旧程序写入，或发生过异常中断。保留错误信息并停止本次查询，不要自动拿当前值代替，也不要反复重试来掩盖不一致。

### 返回 500、超时或连接失败

检查服务是否运行、实际端口和网络是否正确。500 需查看服务日志与存储状态；超时和网络失败可以有限重试，但不得无限循环，或把失败当成系数为零。

### 错误正文格式

业务错误通常返回：

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "该时间点没有可用的系数配置",
    "detail": {}
  }
}
```

这是结构示例，实际 `detail` 可能包含模块和查询时间等诊断信息。代理或网络设备可能返回非 JSON 错误页，因此接入方也要处理 JSON 解析失败。

### 能否设置 Token 或从 URL 传密码？

这个只读接口沿用服务现有身份与网络边界，不另设 Token；管理员和访客均可读。无需在查询字符串里放密码、Cookie 或密钥。不要把本地模拟管理员身份理解为其他部署环境的身份配置。

### 历史删除、恢复会怎样？

展示历史删除不清除独立的生效时间线。恢复历史配置在“恢复成功的当前时刻”生效，之前时段保持原来的结果。未来时间只返回目前已知的最后配置；该结果以后可能因新保存而改变，因此接口返回 `Cache-Control: no-store`，调用方不应把未来查询结果永久缓存。

## 接入验收清单

- 使用目标工作区、机型的真实 ID，四个模块按需分别查询。
- 验证两次保存之间返回旧组、第二次保存的精确时刻返回新组。
- 验证最早记录之前返回 404，无时区时间返回 400。
- 严格追溯场景检查 `interval_verified=true`，保留查询时间、记录编号和完整设置。
- 正确区分 null 与 0，RP 脉宽分别读取 delay / width 设置。
- 处理错误、超时和非 JSON 响应，不伪造成功，不补默认系数。
- 联调在隔离的测试工作区进行；不要为了造历史样例修改生产服务器时钟或手动编辑历史文件。

上述示例属于 API 接入说明。模拟环境验证不等于真实 SDA、真实设备或其他操作系统的外部验收。
