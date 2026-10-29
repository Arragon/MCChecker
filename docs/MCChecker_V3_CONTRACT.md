# MCChecker V3.0 契约与替换清单（M9-01 / INH-2118）

> 状态：M9-01 交付，2026-10-29。事实源：INH-2117 的 V3 原型 HTML + 后端设计 MD；代码基线 `main@429dafa`，V3 集成分支 `feat/mc-v3`。
> 本文件回答“哪些旧模块替换/停写/只读、哪些保留、v1/v3 边界在哪、下游卡怎么接”。具体字段/算法以设计 MD 为准，本文件不重抄全文。

## 1. 总体边界

- 一个应用、一个数据根、单进程写者；不引入第二站点、第二文件库、第二工作区管理器。
- **Workspace 是全应用业务数据边界**；Profile 在 Workspace 内；全局身份/权限唯一，不随工作区切换。
- 新 API 前缀 `/api/v3`；`W=/api/v3/workspaces/{wid}`，`P=W/p/{pid}`；v1 保留方法/路径/参数/响应/错误包络，但业务请求必须带工作区上下文。
- 旧 config-domain 的 ChangeSet/参数登记/交付/升级/生命周期/审计编排被 V3 替换或停写；原 remarks/review、文件、搜索、DL、工具、设置、机型、更新源、任务与旧链接保留。
- 发布/固化只移动管理系统 head，不代表物理机台下发或回读成功。

## 2. v1 / v3 兼容

| 维度 | 规则 |
|---|---|
| 路径 | v3 用 `/api/v3`；v1 路径不变，客户端加 `X-Workspace-Id`；旧无 wid 链接只读映射到显式配置的 legacy workspace |
| 归属 | 先验 workspace、再验 profile、最后验全局能力；路径/header/query 矛盾 `409 WORKSPACE_CONTEXT_MISMATCH`；写缺上下文 `428 WORKSPACE_CONTEXT_REQUIRED`；跨区枚举 `404` |
| 响应 | v1 保持旧包络；v3 列表 `{items,total,next_cursor,query_revision}`，详情 `{item,capabilities,etag}`；错误 `{code,message,detail,request_id}` |
| 幂等/CAS | v3 写要求 `Idempotency-Key`；更新加 `If-Match`；草稿整批 CAS；幂等域 `(principal,workspace,profile,operation,key)` |
| 后台 | 任务/重试/临时 token/缓存/下载都必须带 W+P，不能靠前端最近页面回落 |
| 旧域 | `/api/v1/config/*` 与 `/p/{pid}/config/*` 在 M14-06 后只读/关闭（`503 CONFIG_READONLY`）；不封 v1 file/review/DL |

## 3. 领域对象（与卡对应）

| 对象 | 关键契约 | 卡 |
|---|---|---|
| Workspace | 名称 1–50 全局唯一、说明 ≤300、`default_profile_id`、`data_revision`；不存成员/可见性 | 2119 |
| Profile | slug 工作区内唯一；同名不同空间非同一对象；复制不跨 W | 2124 |
| FileResource/FileVersion | `file_id` 稳定、`version_id` 不可变、`blob_sha256/byte_size/format/parse_status`；current 指针 + archive/record alias + tombstone | 2130/2133 |
| FixedConfig | code/name 在 W+P 唯一；不是跨机型共享目录 | 2136 |
| SoftwareRelease | code `^[A-Z0-9]{3}$`；显式 predecessor/order；`default_revision_id` | 2136 |
| Parameter/Definition | `stable_key` 不可变；定义修订不可变；叶名可重复、同叶名异层级合法 | 2137 |
| BindingSet | draft/frozen；发布后不可变；`frozen_with` | 2137/2138 |
| Branch/Machine | 基线按版本、机台按配置+机台跨版本连续；`head_revision_id`；机台初建 `head=null` | 2139/2140 |
| Draft/ConfigRevision | 草稿 CAS/ETag/expected head；发布后不可变 `content_hash`；只移动 head | 2141/2138/2139 |
| OperationPlan/ExportArtifact | 计划 30 分钟过期；digest/来源关联；导出产物不可变、下载不改 current | 2147/2148/2153 |
| Event | 全局或工作区追加不可变；恢复/撤销记新事件 | 2154 |

## 4. 旧 config-domain 分类

| 旧模块/入口 | 分类 | 处置 | 卡 |
|---|---|---|---|
| `server/api_config_registry.py` + `services/config_catalog.py` | replace | 参数登记/定义/目录改 V3 参数身份/定义/BindingSet；旧数据作为候选/legacy_id | 2136/2137 |
| `server/api_config_changes.py` + `services/config_changes.py` | replace/retire | ChangeSet 状态机停写；旧记录只读 provenance | 2155 |
| `server/api_config_approval.py` | replace | 批准/发布改 V3 冻结/发布；旧审批只读 | 2138/2155 |
| `server/api_config_delivery.py` + `services/config_delivery.py` | replace/read-only | 交付登记/回读改 V3 ExportArtifact + 事件；旧记录只读 | 2153/2154 |
| `server/api_config_upgrade.py` + `services/config_upgrade.py` | replace | 升级合并改 V3 O/L/N merge plan + 草稿 | 2150/2151 |
| `server/api_config_promote.py` + `services/config_promote.py` | replace/retire | 经验上提改 V3 合并/来源谱系；旧入口只读 | 2151/2154 |
| `server/api_config_lifecycle.py` + `services/config_lifecycle.py` | replace/retire | 机型/配置退役改 V3 生命周期；旧入口停写 | 2155 |
| `server/api_config_audit.py` + `services/config_audit.py` | read-only | 旧审计保留查询；V3 事件另立不可变投影 | 2154 |
| `server/api_config_baselines.py` + `services/config_baselines.py` | replace/reuse | 意图/取值/快照可复用；scope common/model/device 改 V3 baseline/machine | 2137–2140 |
| `services/config_export.py` + `services/config_artifacts.py` + `server/api_config_artifacts.py` | replace/reuse | 精确导出/模板/验证/产物改 V3 四步导出 | 2153 |
| `server/api_config_artifacts.py` + `config_artifacts.py` | replace/read-only | 旧产物只读；V3 ExportArtifact 不可变 | 2153/2154 |
| `services/config_verify.py` + `server/api_config_delivery.py` | reuse/read-only | 回读核验保留为证据；不得冒充物理机台成功 | 2153/2160 |
| `server/api_config_ingest.py` + `config_ingest.py` | reuse/replace | 采集/观测保留；V3 参数值导入另走 value-import-plan | 2152 |
| `server/api_config_reconcile.py` + `config_reconcile.py` | reuse/read-only | 对账保留为来源证据；不自动改基线 | 2152/2155 |
| `server/api_config_tracking.py` + `config_policies.py` | retire/read-only | 旧入口 tracking 策略停写；旧响应字段保留兼容 | 2155 |
| `server/api_review.py` + `services/review.py` | **keep** | 原 remarks 建议/管理员审阅完整保留，加 W+P 作用域 | 2126 |
| `server/api_documents.py`/`api_upload.py`/`api_history.py` + `documents/upload/history/recovery.py` | **keep + extend** | 唯一文件入口；FileResource/FileVersion；原上传/URL/临时/归档/记录/删除不回归 | 2130–2135 |
| `views/overview.js`/`search.js`/`dltool.js`/`settings.js`/`tools.js`/`fileview.js` | **keep + scope** | 原 UI/交互保留；W+P 上下文 | 2125–2128 |
| `config_device_layer.py` | **keep disabled** | `ENABLED=False`；M7-07/08 Canceled | 2161 |

## 5. 文件 / 参数 / 路径

- 唯一文件入口：宿主左下列表 + 导入；没有第二上传库、没有 `copy-from-profile`。
- 引用：`workspace_id + profile_id + file_id + version_id + sha256`；选 current 另记 `expected_current_version_id`；删除后来源 `missing` tombstone，冻结值/历史可读，新读取/提取/导出阻断。
- XML：UI 显示原型索引路径；服务端 locator `{namespace_uri, element_path, occurrence}`（ADR-007）；只取叶文本，属性不混入。
- JSON：RFC 6901 pointer；拒重复键/NaN/Infinity。
- INI：section/key + occurrence；只用于原文/值读取/导出，不做名称层级提取。
- 提取：只提名称+完整层级；`description=""`、`value_state=unset`；不覆盖已有名称/释义/值/绑定；`include_existing_missing_bindings` 默认关。
- 合并：O/L/N 真实引用 + 成员状态 + 定义快照；`allowed_decisions`；提交只产新草稿。
- 导出：冻结修订 + 精确模板 + 已确认绑定；停用/移除显式确认；preview token 绑 digest；新文件不覆盖原文件。

## 6. UI 契约

- 宿主：顶栏工作区→机型→搜索→任务→身份；侧栏唯一文件列表；tabs 按 W+P；切换 W 先确认 dirty 视图/模态，`epoch++` 丢弃旧响应，恢复该 W 上次路由。
- 配置管理：固定配置 / 配置文件 / 配置记录；五 tab：版本演进 / 参数工作台 / 参数总览 / 模板导出 / 配置历史。
- 工作区切换：名称 1–50、说明 ≤300、全局唯一、仅管理员建/改名；新工作区空 default；同名 409 字段级。
- 全局身份/权限唯一；非管理员只读横幅 + 禁用编辑入口；服务端仍强制 403。
- 配置域通过宿主能力取文件/上传/工作区管理/全局搜索/参数树；正式实现推荐原生视图 + 等价能力，iframe 只作原型打包。
- 所有提交（提取/合并/导出/恢复）前重新读来源并核对 current/hash；来源变化 `SOURCE_CHANGED`。
- 发布/固化 UI 明确“未下发真实机台”。

## 7. 下游卡映射与缺口

| 需求 | 卡 | 交付 |
|---|---|---|
| 工作区/身份/路由 | 2119–2123 | 元数据、context、session、顶栏、隔离门禁 |
| 原功能作用域 | 2124–2129 | profile/速览/审阅/DL/设置/任务回归 |
| 唯一文件 | 2130–2135 | FileResource/Version、选择器、tombstone、hash 门禁 |
| 配置内核 | 2136–2142 | FixedConfig/Release/参数/值/D0/Bn/rn/工作台 |
| 参数产品 | 2143–2149 | 列表/树、总览、差异/历程、提取、补绑、性能 |
| 合并/导入/导出/历史 | 2150–2155 | O/L/N、值导入、模板导出、恢复/撤销、旧域停写 |
| 迁移/总门禁/发布 | 2156–2161 | dry-run、迁移/回退、安全/并发、真实浏览器/模板/离线、发行包 |

**缺口/待 ADR**：XML 路径双表示（ADR-007）、全局权限文案（ADR-008）、配置域 W+P（ADR-009）、iframe/原生（ADR-010）、50/100 分页（ADR-011）。缺口未闭合前对应实现卡不启动，见 `docs/adr/ADR-007-011-v3-ui-contract.md`。

## 8. 非目标

- 不做 WorkspaceMembership、空间角色、私有空间、跨 W/跨机型复制、第二上传库。
- 不做物理机台下发/安装/回读；不引入数据库/ORM/队列/前端框架/新运行时依赖。
- 不删除旧页/token；不把 HTML 模拟当后端证据；不把 M5–M8 Done 当 V3 完成。

## 9. 验证与下游门禁

- 本卡只出文档；不写业务代码。
- 下游卡开工前：确认本契约 + `docs/MCChecker_V3_UI_MATRIX.md` 对应屏/组件；ADR 未闭合先不写实现。
- 每卡 Done 前：定向测试 + 全量 pytest + `check_py39.sh`；改前端加 HTTP 前端页；动数据根/备份加 H3 与 `test_release.py`。
