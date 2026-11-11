# 配置管理 V3.3 设计包

先打开 [单文件交互原型](MCChecker_Config_Prototype.html)。下载 HTML 后可在浏览器中打开；GitHub 文件页显示的是源码。本轮继续迭代 2026-11-11 重建设计，路线图改为横向“大版本 → 小版本 → 参数保存历史”，以信息卡展示并冻结双轴。多选、完整记录名称与修改原因规则保留，生产实现尚未开始；检查范围见下表，真实浏览器验收仍待完成。

| 资料 | 内容 |
| --- | --- |
| [产品与交互设计](MCChecker_Config_UX_Design.md) | 页面结构、日常流程、状态和验收口径 |
| [后端设计](MCChecker_Config_Backend_Design.md) | 真实代码审查、身份、版本、保存、评审、来源与迁移 |
| [更新来源设计](MCChecker_Update_Source_Design.md) | 网页识别、人工规则、多格式、候选与采集边界 |
| [实施计划](MCChecker_Config_Implementation_Plan.md) | 依赖明确的实施任务及验收/迁移要求 |
| [工作区检查点](WORKSPACE_CHECKPOINT.md) | 本轮重建、远端已保存范围与待完成事项 |

## 原型入口

文件详情默认打开大版本总览：纵轴为版本基线与机台编号/型号，横轴为软件版本层级。展开或进入大版本后查看小版本，再进入某个小版本的保存历史。汇总卡用于导航，真实保存卡显示完整名称、原因、作者和时间，并可查看参数、比较、详情或导出。参数保留独立标签；普通管理员“编辑 → 核对保存”，评审可选，三个视图读取同一组记录。

| 场景 | 入口 |
| --- | --- |
| 配置文件列表 | [配置文件](MCChecker_Config_Prototype.html#/files) |
| 文件默认详情 | [MachineConfig.xml 版本路线图](MCChecker_Config_Prototype.html#/files/f-machine/roadmap) |
| 文件参数表 | [MachineConfig.xml](MCChecker_Config_Prototype.html#/files/f-machine/parameters) |
| 旧版历史入口 | [versions 深链](MCChecker_Config_Prototype.html#/files/f-machine/versions)兼容到同一版本路线图 |
| 一台机台的全部文件 | [MC-001](MCChecker_Config_Prototype.html#/machines/asset-01) |
| 新增跨版本示例 | [MC-003 · MX-820](MCChecker_Config_Prototype.html#/machines/asset-roadmap-03) |
| 参数更名、属性和值变化 | [参数变化](MCChecker_Config_Prototype.html#/parameters?file=f-machine) |
| 可选评审 | [CR-024](MCChecker_Config_Prototype.html#/review/CR-024) |
| 更新来源 | [更新管理](MCChecker_Config_Prototype.html#/sources)、[添加来源](MCChecker_Config_Prototype.html#/sources/new) |
| 操作回溯 | [操作记录](MCChecker_Config_Prototype.html#/records) |
| 初始化 | [初始化配置](MCChecker_Config_Prototype.html#/initialize) |

## 建议试用

| 例子 | 操作与观察 |
| --- | --- |
| 三级版本路线图 | 打开 `MachineConfig.xml`，先看 69X/6AX 大版本；展开 69X 查看 690/691，进入 691 查看同一版本内的保存历史。折叠和返回后，仍可定位所选真实记录；汇总卡不冒充保存记录。 |
| 6AX 与机台型号 | 展开 6AX 的 6A0/6A1，核对基线保存卡和 MC-003 的采用关系。该机台型号为 MX-820，与软件大版本分开显示；6A1 有两次基线保存可比较，MC-003 保留原点校准值。 |
| 空间与来源 | 局部横向/纵向滚动时顶部版本轴、左侧对象轴及交叉角应固定；“聚焦机台及来源”收拢到该机台及真实祖先/采用关系，“全部配置”恢复。列与机台分别分页；实际浏览器冻结效果仍待验收。 |
| 日常编辑 | 从所选当前节点进入参数，或直接打开参数标签，编辑后填写具体修改原因，可按行附上关联 HTTP(S) 链接，再核对保存。查看新增记录与参数来源；历史记录保持只读。 |
| MC-001 采用新基线 | 在 MC-001 的 `MachineConfig.xml` 行打开“合并更新”。示例从“版本基线 · 软件 691 · 第11次保存”采用“版本基线 · 软件 691 · 第12次保存”；核对共同起点、机台当前及新基线，处理冲突、填写原因，再核对保存；固定校准值需保留。 |
| CR-024 评审 | 该示例由王工提出，林工可回复并处理讨论后批准，再合并。原因和关联链接在提交评审时冻结，并传播到合并后的记录。提出人不能独立批准自己的提案；普通保存仍可直接完成。目标已改变时应重新核对。 |
| 大表与多文件 | 顶部“交互原型”中分别载入“3,248 项参数”和“100 个配置文件”，检查搜索、分页与空间。多选提供本页全选、全部筛选结果、跨页保留、筛选外数量、半选状态和清空；这些是合成样本，不是生产性能成绩。 |
| 参数与记录多选 | 参数选择器可全选与清空，所选参数按稳定身份追踪更名、属性和值。记录比较最多 6 条，批量按钮明确为“全选最近6条”，不表示选中全部历史。 |
| 来源更新 | “添加来源”载入网页列表、ZIP、JSON 列表、XML 列表或单文件样例，预览支持全选、半选和清空；核对接收文件并填写原因后保存来源。获取更新后核对候选、载入草稿，填写本次配置修改原因再保存。高级区可演示手动规则与要素组成地址。 |
| 本地文件 | 在“比较”中导入 [MachineConfig_Compare.xml](samples/MachineConfig_Compare.xml)，相对示例“版本基线 · 软件 691 · 第12次保存”有 3 个参数值变化；在“初始化”中导入 [DeviceSettings_Initialize.json](samples/DeviceSettings_Initialize.json)，可检查 6 项参数及精确整数，并填写初始化原因。 |
| 错误恢复 | “交互原型”可切换访客、模拟下次保存失败、他人保存、来源失败和多文件部分失败。不同示例互相改变数据；需要回到初始状态时刷新页面。 |

## 演示边界

数据、人员、机台与网址都是示例，状态只保存在页面内存中，刷新还原。身份切换演示权限状态，不代表生产身份认证；并发与不可变记录的模型检查不代表磁盘事务已经实现。

软件大版本是分组，横轴版本次序不是日期。主文件有显式 69X/6AX 目录，旧文件仅对约定的 `69?`/`6A?` 做有限推导；机台型号不参与推导。真实保存卡按自身软件版本归列；汇总连线只聚合已存在的父引用，不因折叠、排序或软件前驱补造记录关系。生产可选大版本元数据、设置原因与审计仍待实现。

新增 6A0/6A1 的 5 条示例保存保留真实父引用、演示原因和链接，原 690/691、MC-001/002 与 CR-024/025 场景不变；没有原始输入证据的初始快照仍为空。原型基线写入当前以所选版本分支 head 为起点，生产则有全配置 main_branch；本轮不以布局补线掩盖两者差异，也不改变核心保存语义。

保存配置、创建评审、初始化、新建软件版本、增加机台、恢复历史和修改来源等实际内容或业务设置，都需要用户填写非空原因，自动操作说明不代填。关联链接仅接受完整 HTTP(S) 地址，规范化并去重后保存和显示为可点击链接；非法协议、嵌入账号密码及控制字符会被拒绝。旧历史缺少原因时仍显示未知，原有说明不被补写成用户原因；恢复操作也不改写原始作者。

来源仅使用 `.invalid` 离线样例，未访问真实网址。预览中的网页、清单和压缩包结果用于演示文件对应、失效检查与候选流程，不证明该站点、登录、提取规则或真实压缩包已适配。自动更新设置不会在此页面启动生产调度器。

来源设置的原因/链接由原型 UI `onEvent` 接线补入事件和 `rules`。生产实现必须由服务端在同一事务中校验并保存设置、原因/链接及审计，不能依赖客户端补事件形成一致性。

本地 XML/JSON 样本处理限定原型支持的结构及大小；它不替代既有生产解析器。当前下载为示例序列化内容，多文件下载为 JSON 文件集。正式模板保真导出、ZIP 与 manifest 清单仍待接入，不能将示例下载直接当作可部署的设备配置包。

生产实现还需要持久提交和审计、跨文件机台绑定、真实来源适配、迁移、模板保真及规模测试。机台视图表示管理系统保存记录，没有设备回读或实际部署证明；多文件操作不承诺一次原子生效。

## 本轮验证

| 验证 | 本轮结果 | 证明范围 |
| --- | --- | --- |
| [verify_model.cjs](verify_model.cjs) | 39 / 39 本轮通过 | 保留全部业务验证，增加大版本目录、真实保存引用、型号区分、工作区旧记录及来源归属的完整性检查 |
| [verify_sources.cjs](verify_sources.cjs) | 69 / 69 本轮通过 | 最终包复跑：离线来源状态、预览失效、范围/候选隔离，以及受限 JSON/XML 解析与精度规则 |
| [verify_ui.cjs](verify_ui.cjs) | 31 / 31 本轮通过 | 对最终 CSS 包运行横向三级导航与保存卡操作的接线回归；使用 Node VM 与 DOM 替身 |
| [verify_roadmap.cjs](verify_roadmap.cjs) | 13 / 13 本轮通过 | 三级分组、展开/定位、真实父边聚合、行列窗口、公共矩阵与转义；属于数据和生成 HTML 检查 |
| 真实浏览器视觉/交互 | 待完成 | 双轴冻结、交叉角、多尺寸、滚动、键盘/焦点、弹窗和实际下载尚未验收 |

最终包共 152 项检查通过，原始输出见 [模型](verification/model.txt)、[来源](verification/sources.txt)、[路线图](verification/roadmap.txt) 与 [页面](verification/ui.txt) 记录。源码与 HTML 内嵌脚本一致；Node 数据/HTML 与 VM 检查不能替代真实浏览器、冻结轴视觉或生产服务验证，范围及待验项见 [验证记录](VERIFICATION.md)。

在仓库根目录可重新构建与运行模块检查：

```sh
python docs/design/v3.3/build_prototype.py
node docs/design/v3.3/verify_model.cjs
node docs/design/v3.3/verify_sources.cjs
node docs/design/v3.3/verify_ui.cjs
node docs/design/v3.3/verify_roadmap.cjs
```

源文件在 [prototype/](prototype/)，构建脚本把样式、模型、来源模块、路线图与界面合并成一个 HTML；应修改源文件后重建，不手动维护两份不同内容。

## 基准与下一检查点

生产项目基准为 [codex/v32-prototype-parity-20261109](https://github.com/Arragon/MCChecker/tree/codex/v32-prototype-parity-20261109)，准确提交 `d19ee3e8f896a089a2d8eb5628c0e8bb9b18a0de`。本轮原型从 [e621696b](https://github.com/Arragon/MCChecker/commit/e621696be3b509c682ca0d7cd19876f1a4ffff10) 继续。技能入口与正文固定于 [Arragon/skill-hub@23a6e990](https://github.com/Arragon/skill-hub/tree/23a6e990fec0b7024c5e64098e8a607485b9eb8e)，主要视觉规范参考 [pbakaus/impeccable@2f98774f](https://github.com/pbakaus/impeccable/tree/2f98774f86bc05563876643db37e6c37c322850f)。

路线图的信息组织参考 [GitKraken Desktop 官方界面说明](https://help.gitkraken.com/gitkraken-desktop/interface/)，采用图、记录信息和选中详情配合的方式；视觉继续沿用 MCChecker，不复制商业素材或代码。

本轮也阅读了 [FullCalendar Timeline View](https://fullcalendar.io/docs/timeline-view)，仅参考水平资源行与层级展开组织，不引入 Premium/Scheduler 依赖；[GitHub Projects 路线图布局](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/customizing-the-roadmap-layout)仅参考总览/细览密度和分组组织，不复制日期、排期或拖动改计划语义，不添加依赖。这里的横轴仍是软件版本。

横向三级路线图、信息卡、双轴冻结、多选范围、完整记录名称和修改原因规则已落实为本轮原型，离线检查已完成。真实浏览器验收仍待进行，本轮反馈未批准生产改造；后续生产工作按实施计划推进，本设计包不代表生产功能已经完成。
