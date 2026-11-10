# V3 API 速查（更新至 V3.2，2026-11-09）

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
| GET | `/configs/{cid}` | 读 | 配置 + releases；可选 saved_authors 从本配置保存审计推导记录操作人，不修改旧历史 |
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
| POST | `/configs/{cid}/releases/{rid}/baseline-drafts` | 管理员 | 从本软件版本最新已保存标准派生，无本版 Bn 时用本版 D0 |
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
| POST/GET/PATCH | `/configs/{cid}/merge-plans`、`/{id}/rows`、`/decisions`、`/commit` | 管理员/读 | 标准合并或机台升级计划；机台升级可继续捕获的活动草稿，均不发布 |
| POST/GET/PATCH | `/configs/{cid}/value-import-plans`… | 管理员/读 | 按已确认绑定导入值 |
| POST | `/configs/{cid}/export-plans`、`/{id}/commit` | 读 | 已保存配置+精确模板，产独立 ExportArtifact；不修改配置/模板 |
| GET | `/configs/{cid}/events`、`/events` | 读 | 配置/工作区事件 |
| POST | `/configs/{cid}/restore-plans`、`/{id}/undo` | 管理员 | 旧修订→新草稿；撤销仅未修改草稿 |
| GET | `/exports/{artifact_id}/download` | 读 | 安全 basename 下载，不覆盖原文件 |

错误包络统一为 `{"error":{"code","message","detail"}}`；`PLAN_STALE`/`REVISION_CONFLICT`/`SOURCE_CHANGED` 为常见可恢复冲突。V3 发布只移动内部 head，不代表设备已部署。

## V3.2 文件工作台增量

下表相对本文开头 W/P 前缀；权限仍只有管理员与访客，接口能力键沿宿主授权。没有批量升级或设备下发接口。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/config-files?q&offset&limit` | 唯一文件库及其显式配置归属、最近版本、草稿和待核对摘要；默认50，最多100 |
| PUT | `/configs/{cid}/file` | 管理员显式关联 `file_id`；唯一性及真实文件身份校验，同一关联可重试，冲突409 |
| GET | `/config-standards`、`/config-machines` | `config_id/release_id/status/q/offset/limit` 在服务端过滤后分页，默认50；saved与draft独立，草稿目标不冒充正式版本 |
| POST | `/configs/{cid}/parameter-query` | 只读查询；`revision_id/q/directory/filter/edits/changed_ids/offset/limit`；定义和单位取所选记录快照；编辑覆盖不落盘 |
| POST | `/configs/{cid}/discard-draft` | 管理员；`draft_id/expected_revision/request_id`；只移除指定活动草稿，不删已保存历史；引用存在的新版本不可移除 |

`parameters` 和 `parameter-tree` 支持可选 `revision_id`；与 `release_id` 同时提供时须归属一致。草稿值 PATCH 可带 `expected_draft_id`。正式保存支持可选 `note`（最多1000字）及稳定 `request_id`，重放返回原保存记录。值导入 commit 同时校验预览捕获的草稿 ID/修订与当前草稿；不能将更新过的修订号填回旧预览绕过核对。

机台升级复用合并计划：

```json
{"machine_id":"m-…","ours_revision_id":"md-…","theirs_revision_id":"bl-…"}
```

`ours_revision_id` 必须是该机台当前活动草稿，或无草稿时的正式 head；服务端解析它实际采用的原标准，不能用客户端任意替换。原来源确实缺失时才允许明确 `base_revision_id`。行返回完整 `base_member/ours_member/theirs_member` 和稳定选项 `keep_local/adopt_target`；未处理冲突不能提交。`commit` 必须有 `request_id`，在同一配置锁中核对完整会话后更新草稿及目标来源，head不变；重复请求取持久回执，不再次升级。

工作区更新 API 前缀为 `/api/v3/workspaces/{wid}/sync`：

- `sources` 的 `configuration_id` 同时匹配配置目标和该配置唯一文件的归档目标。
- `candidates`、`runs` 支持 `configuration_id` + `profile_id`，在分页前筛选；候选归属读冻结 `target_scope`，运行读 `frozen.source_targets`。没有冻结归属的旧运行仍可在工作区历史读取。
- `candidates?pending_only=true` 返回待核对与草稿待保存项。`review_state` 是只读派生字段，`awaiting_save` 不等于正式保存；`saved` 以该软件版本正式值与候选值相符为依据，原 `state` 枚举保持不变。
- 当前文件全量预览使用 `scope=current_configuration`、`profile_id`、`configuration_id`；工作区全量预览继续用 `workspace_all`，不能从当前页的前50个来源拼全量。
- 候选预览包含正式值 `saved`、真实活动草稿 `current`、来源值及编辑会话指纹；提交在锁内核对，草稿被替换或修改返回409。成功写入导致原预览失效后，同请求仍可取回原回执。

`initial_mode=separate` 是 V3.2 新手工版本的可选创建模式：首次保存标准不建立软件自带初始快照；`initial_revision_id=null` 如实表达缺失。旧版本沿原 D0 契约读取，不迁移、不伪造初始数据。
