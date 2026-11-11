# 配置管理实施计划 · V3.3

2026-11-11 重建版，已纳入本轮用户确定的路线图、多选与修改原因规则。本计划以 `codex/v32-prototype-parity-20261109` / `d19ee3e8f896a089a2d8eb5628c0e8bb9b18a0de` 为生产实施起点；任务编号不代表此前产物或测试结果仍存在。当前工作仍是独立设计原型迭代，用户反馈未批准生产改造。

目标契约见 [后端设计](MCChecker_Config_Backend_Design.md) 和 [产品与交互设计](MCChecker_Config_UX_Design.md)。本计划不创建外部任务、不发布消息、不自动合并或部署。实现时从用户指定分支创建隔离工作分支，并记录当时准确 base SHA；不能改从其他默认分支开始。

## 执行边界与依赖

文件详情默认版本路线图，基线主线贯穿软件版本，机台各有独立轨道；选节点查看参数、比较、详情或导出，参数表保留独立标签，`versions` 旧路由继续兼容。所有记录用人可读全称，内部 ID/Bn/rn label 保留。普通管理员走“编辑 → 核对保存”，评审可选；所有实际内容与业务设置提交都要求明确用户原因，可附规范化 HTTP(S) 链接。不可变记录、并发校验、审计和跨文件机台身份仍是基础，旧历史缺原因不补造。

新增模块/测试名在下面标为“拟新增”；现有路径可以直接核对。各任务在实现前只读取相关规则与依赖，不顺便升级运行环境、改框架或改权限体系。每次提交只覆盖当前任务及必要回归，实际结果与未验证项分别记录。

| 任务 | 结果 | 前置任务 |
| --- | --- | --- |
| T01 | 固定已确定原型契约与验证起点 | 本轮设计/原型可审阅 |
| T02 | V3 一致提交、CAS、幂等与审计 | T01 |
| T03 | 保存记录与真实来源契约 | T02 |
| T04 | 跨文件机台身份与绑定 | T02、T03 |
| T05 | 初始化与初始快照 | T03 |
| T06 | 分页查询、参数窗口与选择范围 | T03 |
| T07 | 默认版本路线图与共享上下文 | T01、T06 |
| T08 | 日常编辑、核对保存与对比 | T02、T03、T07 |
| T09 | 持久合并计划与三方决议 | T02、T03 |
| T10 | 可选评审、评论与策略 | T02、T03 |
| T11 | 合并/评审界面 | T07、T08、T09、T10 |
| T12 | 来源规则与人工链接组成 | T01、T03 |
| T13 | 常见网页和清单自动识别 | T12 |
| T14 | 多文件候选与批次恢复 | T02、T03、T12 |
| T15 | 受控采集与登录场景 | T12、T13 |
| T16 | 来源更新界面 | T07、T08、T12、T13、T14、T15 |
| T17 | 操作记录、恢复与字段来源 | T02、T03、T09、T10 |
| T18 | 机台页和多参数视图 | T04、T06、T07、T17 |
| T19 | 单文件及机台多文件导出 | T03、T04、T06 |
| T20 | 可重入迁移与回退演练 | T02–T06 的 schema 稳定；T09、T10、T12 新对象契约稳定 |
| T21 | 真实规模与界面性能验证 | T06–T08、T11、T16–T19 |
| T22 | 集成验收、文档与分阶段交付 | T01–T21 |

T03 之后数据查询、来源规则、评审与机台绑定可在各自边界并行；共享提交层、持久结构和公共 UI 组件由单一负责人协调。T20 的迁移样本和 dry-run 可提前准备，正式迁移必须等待结构及回退路径验证。任务先后不等于工期承诺。

## T01 · 固定已确定原型契约与验证起点

范围：记录已确定方向、本轮原型的真实可点击流程、模拟内容与待接入能力；核对 [DESIGN.md](../../../DESIGN.md)、`CLAUDE.md` 及 `agent/docs/` 的相关规则。建立同一环境下的当前版本基线，包括版本号、启动方式、数据规模和关键场景，不重新索取既定设计方向许可。

代码入口：[config-manager.js](../../../app/web/assets/js/views/config-manager.js)、[config-editor.js](../../../app/web/assets/js/views/config-editor.js)、[update_management.js](../../../app/web/assets/js/views/update_management.js)。复用现有 `scripts/e2e_config_prototype.py`、`scripts/e2e_config_layout.py`、`scripts/e2e_config_axes.py` 的思路，先检查它们是否仍与运行环境匹配。

验收：文件详情默认路线图、跨软件版本主线/机台独立轨道及真实父关系，参数标签与旧路由兼容；记录全称、多选范围、原因/链接、直接保存/可选评审、来源更新与错误恢复都有测试场景。界面组织参考 [GitKraken 官方说明](https://help.gitkraken.com/gitkraken-desktop/interface/)，不复制商业素材或代码。未覆盖项明确列出，没有真实浏览器证据就不写视觉通过。

非目标与迁移：本任务不改生产 UI、不迁移数据、不把静态模拟结果视为后台正确性。后续测试只引用本轮实际运行输出。

## T02 · 统一 V3 保存与审计提交

范围：为单个逻辑配置建立统一提交入口，绑定目标/草稿/定义版本、含用户原因和关联链接的请求摘要及权限。服务端拒绝空原因和非法链接；不可变记录、当前指针、事件、回执一致公布，失败后能辨别未提交与已提交状态。

代码目标：[config_v3_catalog.py](../../../app/services/config_v3_catalog.py)、[baselines](../../../app/services/config_v3_baselines.py)、[machines](../../../app/services/config_v3_machines.py)、[history](../../../app/services/config_v3_history.py)、[api_v3_config.py](../../../app/server/api_v3_config.py)；拟新增 `app/services/config_v3_repository.py`。参考 [core/config_repository.py](../../../app/core/config_repository.py)，不向旧配置域写入 V3 对象。

验收/测试：两个客户端同起点只有一个可提交，另一个收到可恢复的 409；同请求重放得到同一记录；同 ID 不同内容冲突。逐阶段注入写失败和进程终止，读者不看到缺审计的成功记录，未提交回执不能被重放为成功。扩展 `tests/test_v3_draft_cas.py`、`test_v32_save_receipts.py`，拟新增一致提交/崩溃恢复测试。

迁移：先加读取适配与受控切换；一个 W/P 同时只能有一个权威写入实现。非目标：不承诺多进程/多主服务，不承诺多个配置一起原子生效。

## T03 · 记录身份、主线与来源契约

范围：内部保留配置内连续 Bn 和机台 rn，显示统一用“版本基线/机台 · 软件版本 · 第几次保存”全称；补充准确内容起点、父引用、定义、来源/模板指纹、用户原因和链接，以及可重建的软件版本索引。明确初始快照、手工标准与兼容 D0 的区别，旧历史原因保持未知。

代码目标：[config_v3_catalog.py](../../../app/services/config_v3_catalog.py)、[baselines](../../../app/services/config_v3_baselines.py)、[machines](../../../app/services/config_v3_machines.py)、[provenance](../../../app/services/config_v3_provenance.py)、[file_versions.py](../../../app/services/file_versions.py)、[diff](../../../app/services/config_v3_diff.py)。

验收/测试：跨软件版本连续保存后全局主线完整，各版本最新标准仍正确；同名文件重传不误关联；参数更名仍为同一 ID；旧记录使用旧定义。扩展 `test_v3_baseline_head.py`、`test_v3_file_identity.py`、`test_v3_param_history_diff.py`、`test_v3_default_freeze.py`。

迁移：新增字段允许“未知/未记录”，保留旧 `parent_id` 和所有旧 ID；不改写历史。非目标：不从版本名排序制造前驱，不把手工标准补成软件原始数据。

## T04 · 统一跨文件机台身份

范围：新增 W/P 内稳定 `device_id`，以显式绑定连接多个 `config_id + machine_id`。提供未绑定/歧义列表和管理员确认，显示绑定版本并对修改做 CAS。

代码目标：[config_v3_machines.py](../../../app/services/config_v3_machines.py)、[config_v3_indexes.py](../../../app/services/config_v3_indexes.py)、[api_v3_config.py](../../../app/server/api_v3_config.py)；拟新增 `app/services/config_v3_devices.py` 与相应 API/测试。

验收/测试：同一机台的多个配置可汇集；同编号但实际不同的机台不被自动合并；不同 W/P 不串联；绑定与解绑都有事件且不删除旧机台记录。扩展 `test_v3_machine_branch.py`、`test_v3_workspace_isolation.py`。

迁移：dry-run 输出建议绑定与歧义，保留文件内 `machine_id`。非目标：不建立物理部署系统，不假定机台所有文件软件版本相同。

## T05 · 初始化与真实初始参数

范围：从已有文件或新文件建立逻辑配置，确认格式、参数定义和目标软件版本；取得实际原始数据时固定初始快照，没有原始数据时保持为空。手工建立版本基线仍可完成。

代码目标：[config_v3_extract.py](../../../app/services/config_v3_extract.py)、[parse](../../../app/services/config_v3_parse.py)、[import](../../../app/services/config_v3_import.py)、[catalog](../../../app/services/config_v3_catalog.py)、[drafts](../../../app/services/config_v3_drafts.py) 及现有文件 API。

验收/测试：重复定位符、重复名称、类型不明、无值、精确小数及结构解析错误可定位；预览改变后旧提交被拒绝；错误不产生半初始化配置。扩展 `test_v3_extract_preview.py`、`test_v3_extract_identity.py`、`test_v3_extract_commit.py`、`test_v32_import_contract.py`。

迁移：保留现有文件身份及 canonical D0 的兼容读法，记录新增快照的真实来源。非目标：不自动匹配不同文件的同名参数，不自动发布导入内容。

## T06 · 有界查询与选择范围

范围：统一文件、参数、路线图记录、机台和事件的服务端筛选/排序/分页，路线图包含必要父引用和边界状态。多选同时支持本页全选、全部筛选结果、跨页保持、筛选外计数、半选与清空；参数选择器/来源预览同样明确范围。记录比较最多 6 条，批量按钮写“全选最近6条”。

代码目标：[config_v3_workbench.py](../../../app/services/config_v3_workbench.py)、[projection](../../../app/services/config_v3_projection.py)、[indexes](../../../app/services/config_v3_indexes.py)、[diff](../../../app/services/config_v3_diff.py)、[api_v3_events.py](../../../app/server/api_v3_events.py)。

验收/测试：3000+ 参数下搜索覆盖全量数据，分页前权限/范围已过滤；跨页选择不丢失，筛选变化保留选择并正确显示筛选外数量，本页半选及清空可预测。超过 6 条比较在服务端明确拒绝。查询有数据版本，执行前重核；扩展 `test_v3_large_catalog.py`、`test_v3_parameter_tree.py`、`test_v32_workbench.py`、`test_v3_events_pagination.py`。

迁移：索引可删除重建且与权威记录校验；不借索引重写历史。非目标：不通过浏览器下载全历史再切页实现规模支持，不允许无界参数×记录矩阵。

## T07 · 默认版本路线图与共享上下文

范围：文件详情默认路线图，一条基线主线贯穿软件版本，机台轨道独立；真实父关系显示分叉、采用和合并，记录行对齐完整名称、原因、作者与时间。“聚焦机台及来源”只显示该机台及真实祖先/采用关系并收拢轨道，“全部配置”恢复完整图；原始节点和边保持真实，不制造过滤后的父边。固定 W/P/文件/所选记录上下文，节点动作进入参数、比较、详情或导出。参数保留独立标签，旧 `versions` 深链兼容；复用现有文件标题、图标和 tokens。

代码目标：[config-manager.js](../../../app/web/assets/js/views/config-manager.js)、[config-evolution.js](../../../app/web/assets/js/views/config-evolution.js)、[config-api.js](../../../app/web/assets/js/core/config-api.js)、[components.js](../../../app/web/assets/js/ui/components.js)、[tokens.css](../../../app/web/assets/css/tokens.css) 与 `views.css`。只按实际职责拆分过大视图，不换框架。

验收/测试：跨软件版本主线不中断，同一机台不被拆成多条不相干轨道；不按屏幕位置造父连线，未知/窗口外父引用有标识，选中历史节点的参数和导出不跳到当前 head。页签保留合适筛选/滚动，工作区切换清空旧计划，晚到响应无污染；记录窗口、末项、动作与键盘焦点可达，补真实浏览器证据。

迁移：现有深链继续可达或有明确兼容跳转。非目标：不靠修改全局 modal 尺寸修复单页，不给全应用添加第二套设计系统。

## T08 · 日常编辑、核对保存与比较

范围：管理员从节点或参数标签编辑，填写明确原因和可选链接后核对保存；保持暂存、取消和离开保护。空原因与非法链接定位并保留输入，自动操作说明单独存放。支持保存记录、初始参数或上传文件比较，外部文件不默认写入。

代码目标：[config-editor.js](../../../app/web/assets/js/views/config-editor.js)、[config-manager.js](../../../app/web/assets/js/views/config-manager.js)、[config_v3_diff.py](../../../app/services/config_v3_diff.py)、[drafts](../../../app/services/config_v3_drafts.py)、[api_v3_config.py](../../../app/server/api_v3_config.py)。

验收/测试：空值/未填写/0/false/空字符串、继承/覆盖/固定和高精度数值正确往返；原因 trim 校验、`reason/message` 兼容、HTTP(S) 规范化去重与不可变传播正确。历史不可编辑，409 按字段保留独立变更，失败不丢原因/链接。扩展 `test_v3_draft_cas.py`、`test_v32_save_receipts.py`、`test_v32_discard.py` 与前端行为测试。

迁移：保存沿用旧记录身份与 T02 权威路径。非目标：不把每次保存强制变成评审，也不把比较中的同名参数擅自配对为同一身份。

## T09 · 持久合并与更新标准

范围：将合并/升级计划及逐项决议持久化；使用真实起点、当前目标和新来源，提交前重检目标记录、草稿、定义与来源指纹。结果先进入草稿，正式保存保留合并来源。

代码目标：[config_v3_merge_plans.py](../../../app/services/config_v3_merge_plans.py)、[merge](../../../app/services/config_v3_merge.py)、[upgrade](../../../app/services/config_v3_upgrade.py)、[core/config_merge.py](../../../app/core/config_merge.py)、[api_v3_plans.py](../../../app/server/api_v3_plans.py)。

验收/测试：重启后可继续，目标前进后计划过期；删除/编辑、类型/单位变化、固定值、重命名和缺少共同起点均可解释。扩展 `test_v3_merge_plan.py`、`test_v32_upgrade.py`、`test_v3_oln_matrix.py`，补计划过期/重启恢复测试。

迁移：旧内存计划不伪造恢复；新计划有 schema/失效期限。非目标：没有真实起点时不自动合并，不通过解决冲突直接跳过核对保存。

## T10 · 可选评审与讨论

范围：为 V3 增加冻结提交内容、用户原因/关联链接、评论、批准/请求修改及可选独立评审策略。批准绑定该内容和精确目标，原因/链接改变也产生新提交内容。合并传播同一原因和链接，不用自动描述或另一个标题替换。

代码目标：参考 [config_changes.py](../../../app/services/config_changes.py) 的既有经验，拟新增 `app/services/config_v3_reviews.py`；接入 [authz.py](../../../app/services/authz.py)、T02 提交层和 V3 API。

验收/测试：默认单管理员直接保存可用；受保护目标无法绕过 API；不能用改显示名通过独立批准；评论跟随稳定参数/字段，旧提交评论标明过时。新增 V3 评审状态、策略、并发及权限测试，回归 `test_api_authz.py`、`test_api_config_authz.py`。

迁移：默认不开启新的强制策略，不把旧保存补为“已评审”。现有共享身份不能证明独立性时明确阻止宣称独立批准。非目标：不把旧域重启为 V3 写入后端，不引入通用审批引擎或额外角色。

## T11 · 合并与评审交互

范围：同一差异视图支持普通核对、合并冲突和可选评审；记录名称使用全称，原因与可点击关联链接在核对和详情中可见。主要动作使用短中文，评审入口不挤占普通编辑；讨论按参数/字段展开。

代码目标：[config-manager.js](../../../app/web/assets/js/views/config-manager.js)、[config-editor.js](../../../app/web/assets/js/views/config-editor.js)、[review.js](../../../app/web/assets/js/views/review.js) 的可复用显示能力及配置局部样式。

验收/测试：区别草稿、待评审、待修改、已批准、已保存、已过期；过期内容不能批准/保存。冲突处理可全键盘完成，长差异页上下文与动作可达，按字段选择不丢失另一方独立属性变化。演示评审不使其他流程默认要求评审。

迁移与非目标：不把既有文件审阅功能偷偷改成新配置评审；共享组件改动要列明现有消费者并回归。生产真实 API 接入后再做视觉验收。

## T12 · 来源规则与手工组成链接

范围：扩展现有来源配置，表达地址模板、声明变量、清单路径、成员筛选和目标映射；高级项折叠。实际保存来源规则、映射、连接或调度设置都要求用户原因，可附关联链接；这些元数据进入规则及审计。试取与保存分离，敏感凭据只用服务端引用。

代码目标：[sync_sources.py](../../../app/services/sync_sources.py)、[sync_source_tests.py](../../../app/services/sync_source_tests.py)、[sync_http_adapter.py](../../../app/services/sync_http_adapter.py)、[api_sync_sources.py](../../../app/server/api_sync_sources.py)、[downloader.py](../../../app/core/downloader.py)。

验收/测试：组成地址编码正确，输入变化废弃预览；试取识别登录页/格式不匹配，失败不改文件或新鲜度。无原因或无效关联链接不能保存，源配置与原因/链接/审计同事务。扩展 `test_sync_sources.py`、`test_api_sync_source_tests.py`、`test_downloader.py`、`test_sync_security_gate.py`。

迁移：旧来源类型及 keyword 筛选/文件名处理语义保留，新增模式明确版本。非目标：不支持任意脚本、命令或未经定义的请求编排。

## T13 · 常见网页和清单识别

范围：新增受限发现层，处理直接文件、普通 HTML 下载链接、JSON/XML 文件清单及压缩包；返回建议、证据与待确认项，再复用 T12 的试取流程。

代码目标：拟新增 `app/services/sync_source_discovery.py`；复用 [sync_source_tests.py](../../../app/services/sync_source_tests.py)、[core/bundle.py](../../../app/core/bundle.py) 和下载政策。

验收/测试：覆盖相对链接、重复链接、跨源跳转、有限分页、列表字段歧义、文件名碰撞、无下载项、恶意 HTML/XML 和压缩包限制。所有自动建议能解释，未识别可转人工规则。新增离线 fixture 与有界网络替身测试，不依赖任意线上站点稳定性。

迁移与非目标：只增加来源草案，不自动创建正式来源或应用配置；不宣称任意网页自动识别，不提供编造的准确率。

## T14 · 多文件候选与批次恢复

范围：固定本次发现清单、文件来源哈希及目标映射；逐文件解析/校验并生成候选，展示有效、无变化、失败、待确认。支持明确子集与幂等重试。

代码目标：[sync_commit.py](../../../app/services/sync_commit.py)、[sync_candidates.py](../../../app/services/sync_candidates.py)、[sync_candidate_plans.py](../../../app/services/sync_candidate_plans.py)、[sync_candidate_apply.py](../../../app/services/sync_candidate_apply.py)、[sync_run_store.py](../../../app/services/sync_run_store.py)。

验收/测试：原来源/本地草稿/新来源三方不被替换；重复目标拒绝；一项失败不谎报整批成功；重启可定位已完成项，重试不重复写入。扩展 `test_sync_commit.py`、`test_sync_candidates.py`、`test_sync_candidate_plans.py`、`test_sync_candidate_apply.py`、`test_sync_faults.py`。

迁移：保留旧候选 ID 和源引用；新批次清单用版本化结构。非目标：不承诺跨文件原子保存，不自动更新版本基线或物理机台。

## T15 · 受控采集与登录来源

范围：明确自动识别转入现有 collector 的条件，复用已配置连接、目录、登录与保活；动态网页只在受控能力可用时继续。

代码目标：[sync_collector.py](../../../app/services/sync_collector.py)、[sync_connections.py](../../../app/services/sync_connections.py)、[sync_heartbeat.py](../../../app/services/sync_heartbeat.py)、[collector/runtime.py](../../../collector/runtime.py)、[collector/session_manager.py](../../../collector/session_manager.py)、[collector/bounded_fetch.py](../../../collector/bounded_fetch.py)。

验收/测试：连接缺失、会话过期、凭据错误、采集器离线、目录变化都有准确状态；不会无限重登，也不会把登录成功当作文件已更新。复用 `test_sync_connections.py`、`test_sync_heartbeat.py`、`test_collector_bundle.py`、`test_bounded_fetch.py` 及现有模拟器；真实站点能力另做授权环境验收。

迁移与非目标：核心 Python 与独立 collector 环境保持边界；不把浏览器依赖塞进核心服务，不把用户浏览器 Cookie 复制到前端。

## T16 · 更新来源与候选界面

范围：一个链接输入起步，展示识别结果/试取文件，确认目标后生成候选；人工组成与高级规则按需展开。预览具全选、半选、清空并说明未检查范围，来源设置与候选应用都采集明确原因/链接。更新管理与文件上下文共用来源和任务状态。

代码目标：[update_management.js](../../../app/web/assets/js/views/update_management.js)、[config-manager.js](../../../app/web/assets/js/views/config-manager.js)、来源 API 和已有来源表单组件。

验收/测试：直链、清单、压缩包和登录来源可走通真实分支；错误定位具体文件/字段，部分失败和子集选择明确；上下文切换清除旧计划，提取设置变化使旧预览失效。原型由 UI `onEvent` 补充原因/链接并进入 `rules` 的接线只用于演示；生产应验证服务端同事务，不以客户端补事件代替。

迁移与非目标：旧来源深链和常驻更新/设置入口保留；不让普通用户理解解析器实现细节才能完成任务，不把一次试取成功当作长期适配保证。

## T17 · 操作记录、恢复与字段来源

范围：事件摘要和详情统一显示记录全称、用户原因及可点击关联链接；成功事件纳入 T02，失败/拒绝/取消独立记录。旧历史缺原因保持未知并保留历史说明；恢复需填写自己的原因，字段来源仍追到最后真实修改者。

代码目标：[config_v3_history.py](../../../app/services/config_v3_history.py)、[config_v3_diff.py](../../../app/services/config_v3_diff.py)、[config_v3_provenance.py](../../../app/services/config_v3_provenance.py)、[api_v3_events.py](../../../app/server/api_v3_events.py) 与记录界面。

验收/测试：未变字段在合并后保留原作者，手工决议显示解决者，缺少证据显示未知；恢复不改历史/head，保存恢复草稿才新增记录。扩展 `test_v3_restore_audit.py`、`test_v3_param_history_diff.py`、`test_v3_events_pagination.py`，补定义变更审计及字段级来源测试。

迁移：旧事件原文不改，缺失字段不能推造；计划从内存转持久化需要失效兼容。非目标：不把哈希校验包装成防系统管理员篡改的合规审计。

## T18 · 机台与多参数视图

范围：机台页汇集绑定文件及各自精确采用基线/当前记录；参数页支持选择器全选与清空，按一组参数和最多 6 条记录显示更名、属性与值变化。记录选择按钮明确“全选最近6条”，名称用全称并回到同一文件路线图/参数上下文。

代码目标：[config-manager.js](../../../app/web/assets/js/views/config-manager.js)、[config-evolution.js](../../../app/web/assets/js/views/config-evolution.js)、T04 机台查询、[workbench](../../../app/services/config_v3_workbench.py)、[projection](../../../app/services/config_v3_projection.py)。

验收/测试：文件/机台/参数三个入口显示同一记录事实；查看旧机台记录时采用基线取该记录，不取当前 head；未绑定与无记录有明确状态。行列窗口、筛选与跨页选择可预测。扩展 `test_v3_overview_scope.py`、`test_v32_workbench.py` 并补三视图一致性浏览器场景。

迁移与非目标：不缓存三套可互相漂移的业务副本，不靠同名跨文件合并参数或机台，不默认铺开全部历史列。

## T19 · 精确导出与机台配置包

范围：复用单文件预览/模板保留；增加机台多个文件的固定记录清单与 ZIP + `manifest.json`。包含每项记录、软件版本、采用基线、模板、输出路径、大小、哈希与未包含原因。

代码目标：[config_v3_export.py](../../../app/services/config_v3_export.py)、[api_v3_exports.py](../../../app/server/api_v3_exports.py)、[core/bundle.py](../../../app/core/bundle.py) 的安全路径经验及导出界面。

验收/测试：数字精度、XML 注释/处理指令、未映射字段及原始下载边界保留；映射改变使预览失效；包内路径无碰撞/逃逸；导出期间新增保存不改变已固定清单。扩展 `test_v3_export_preservation.py`，新增包清单、部分失败及可重复内容测试。

迁移与非目标：旧导出 API 兼容，权限不因批量功能放宽；不把导出包称为机台已部署状态，不静默交付缺文件的“完整包”。

## T20 · 数据迁移与回退

范围：扩展盘点、备份、dry-run、增量迁移和恢复。记录 schema、阶段、哈希与引用；机台歧义、未知来源、孤立记录进入可审查报告。

代码目标：[inspect_v3_workspace.py](../../../scripts/inspect_v3_workspace.py)、[backup_v3.py](../../../scripts/backup_v3.py)、[migrate_v3.py](../../../scripts/migrate_v3.py) 及新存储适配。

验收/测试：多次执行结果一致，中断后可恢复；旧 ID/不可变快照/导出内容保持一致；切换期间无双写；回退保留新格式期间所有保存和回执。扩展 `test_v3_migration_dryrun.py`、`test_v3_migration_recovery.py`、`test_config_backup_full.py`、`test_v3_workspace_isolation.py`。

非目标：不直接对生产做不可逆变换，不用旧备份覆盖仍有新记录的目录。交付需包含真实执行步骤、预检/失败判定与恢复演练结果，再选择工作区逐步开启。

## T21 · 3000+ 参数与多文件性能

范围：在相同环境和样本下比较改造前后；覆盖单文件 3000+ 参数、多机台/多文件、长历史路线图、差异、来源批次及导出。检查路线图窗口不会退化为全量历史布局，跨页全选不触发无界 DOM；服务端和浏览器一起测量。

代码目标：[performance_v3.py](../../../tests/performance_v3.py)、`scripts/bench_api.py`、现有浏览器脚本；仅根据瓶颈调整 T02 的存储读取、T06 的索引和前端窗口渲染。

验收/测试：记录数据规模、环境、冷/热条件、样本数、p50/p95、首批显示/可交互时间、长任务、DOM、内存、请求体积和解析/读取次数。预算在取得基线后明确；没有实测就保留待测，不能把固定样本演示写成已支持大规模生产。

非目标与迁移：不凭感觉加缓存或改数据库；缓存必须受 W/P/定义/记录/查询版本约束，过期和内存边界可验证。性能优化不能改变精确值、权限或查询范围。

## T22 · 集成验收与交付

范围：用实际运行版本串联初始化、普通保存、可选评审、合并、外部文件比较、网页/批量更新、历史恢复、三视图及多文件导出。覆盖管理员/访客、并发、断网、重启、空数据和旧数据。

验证入口：按 [requirements.md](../../../requirements.md) 的环境运行相关 pytest，执行 `scripts/check_py39.sh`、`scripts/check_web_ui.py` 与适用的配置浏览器脚本。先查看脚本参数，不编造未经验证的完整命令。验证范围按实际变化决定，失败需修复或明确阻塞。

界面验收：1366×768、1920×1080、768×1024、375×812 下检查内容高度、滚动、表尾、主要动作、键盘焦点、弹窗关闭、可访问名称、颜色之外的状态表达与减少动效。截图只作为对应版本的证据，不以旧截图替代新验收。

文档与迁移：更新 `DESIGN.md`、`docs/architecture.md`、`docs/manual.md`、来源教程和必要的 README/运行说明；记录新增依赖或确认无依赖变化。把原型设计、真实实现、测试通过和现场数据验收分别列出，附迁移/回退证据。

交付条件：目标流程真实可用、不可变历史和权限回归无破坏、迁移与恢复可演练、主要规模瓶颈有实测结论。所有未完成项给出具体影响，不能用“原型通过”替代生产完成。发布/合并按用户已有授权及仓库流程执行，不在本设计阶段发生。

## 当前状态

这 22 个任务是生产工作的执行拆分，均未被本文件标记为生产完成。本轮按已经确定的方向完成独立原型迭代，不重复索取方向许可。模型检查 38/38、来源模块 69/69、路线图 10/10、UI 28/28 通过，共 145 项，实际输出见 [验证记录](VERIFICATION.md)。真实浏览器与生产回归未完成，UI 检查采用 Node VM 与 DOM 替身。本轮没有新增生产依赖或修改生产代码。
