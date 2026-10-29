# V3 API 速查（2026-10-29）

除特别说明外，路径前缀为 `/api/v3/workspaces/{wid}/profiles/{pid}`；写请求需带 `X-Requested-With: MCChecker`（CSRF）。读权限为 `config_read`（任意已识别身份），写权限为 `config_registry_write`（全局管理员）。跨 W/P 的对象一律 404。

## 会话 / 工作区
| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/v3/session` | 读 | 当前身份、workspaces、profiles、capabilities |
| GET/POST | `/api/v3/workspaces` | 读 / 管理员 | 工作区列表 / 新建（名称 1–50、说明 ≤300、全局唯一） |
| PATCH | `/api/v3/workspaces/{wid}` | 管理员 | 改名/说明；ID 与数据不变 |
| GET/POST | `/api/v3/workspaces/{wid}/profiles` | 读 / 管理员 | 机型列表/新建 |
| PATCH/DELETE | `/api/v3/workspaces/{wid}/profiles/{pid}` | 管理员 | 改名/删除（恢复区） |

## 文件
| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/file-selector` | 读 | 当前文件 + current FileVersion（临时文件不出现） |
| GET | `/file-versions/{vid}(/document,/raw)` | 读 | 按不可变 version_id 读取，跨 W/P 404 |

## 固定配置 / 版本 / 参数
| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET/POST | `/configs` | 读 / 管理员 | FixedConfig 列表/新建（code 唯一） |
| GET | `/configs/{cid}` | 读 | 配置 + releases |
| POST | `/configs/{cid}/releases` | 管理员 | 新建软件版本（空 D0，不继承值） |
| GET | `/configs/{cid}/graph` | 读 | 真实 predecessor 图 |
| GET/POST | `/configs/{cid}/parameters` | 读 / 管理员 | 参数定义（可 `offset/limit`） |
| PATCH | `/configs/{cid}/parameters/{pmid}` | 管理员 | 名称/层级/类型/属性 |
| GET | `/configs/{cid}/definition-revisions(/{n})` | 读 | 不可变定义快照 |
| GET/POST | `/configs/{cid}/releases/{rid}/bindings` | 读 / 管理员 | BindingSet；冻结后只读 |

## D0 / 基线 / 机台
| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET/PATCH | `/configs/{cid}/drafts/d0-{rid}(/values)` | 读 / 管理员 | canonical D0 草稿；If-Match/expected_revision + request_id |
| POST | `/configs/{cid}/drafts/d0-{rid}/freeze` | 管理员 | 唯一冻结 |
| POST | `/configs/{cid}/releases/{rid}/baseline-drafts` | 管理员 | 从 D0/head 派生 |
| PATCH/POST | `/configs/{cid}/baseline-drafts/{did}(/values,/publish)` | 管理员 | 基线值/发布 Bn |
| GET | `/configs/{cid}/branches/main` | 读 | 主线 head/基线 |
| GET/POST | `/configs/{cid}/machines`、`/machines/{mid}/publish` | 读 / 管理员 | 机台 rn；初建 head=null |

## 总览 / 差异 / 提取 / 合并 / 导入 / 导出 / 历史
| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/configs/{cid}/releases/{rid}/overview?offset&limit&machines&baselines&compare_release_id` | 读 | D0/Bn/机台矩阵（可只取当前页） |
| GET | `/configs/{cid}/revisions/{rid}/diff` | 读 | 与真实 previous 比较 |
| GET | `/configs/{cid}/parameters/{pmid}/history` | 读 | 参数历程 |
| POST/GET | `/configs/{cid}/extraction-plans`、`/{id}/rows` | 管理员/读 | 服务端全量提取计划、分页/筛选/filter_digest |
| PATCH/POST | `/extraction-plans/{id}/decisions`、`/commit`、`/recompute` | 管理员 | 逐行/筛选决议、原子提交、stale 重算 |
| POST/GET/PATCH | `/configs/{cid}/merge-plans`、`/{id}/rows`、`/decisions`、`/commit` | 管理员/读 | O/L/N 计划、逐行决议、只产新草稿 |
| POST/GET/PATCH | `/configs/{cid}/value-import-plans`… | 管理员/读 | 按已确认绑定导入值 |
| POST | `/configs/{cid}/export-plans`、`/{id}/commit` | 管理员 | 冻结修订+精确模板，产不可变 ExportArtifact |
| GET | `/configs/{cid}/events`、`/events` | 读 | 配置/工作区事件 |
| POST | `/configs/{cid}/restore-plans`、`/{id}/undo` | 管理员 | 旧修订→新草稿；撤销仅未修改草稿 |
| GET | `/exports/{artifact_id}/download` | 读 | 安全 basename 下载，不覆盖原文件 |

错误包络统一为 `{"error":{"code","message","detail"}}`；`PLAN_STALE`/`REVISION_CONFLICT`/`SOURCE_CHANGED` 为常见可恢复冲突。V3 发布只移动内部 head，不代表设备已部署。
