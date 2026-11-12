# 配置管理 V3.3 设计包

先打开 [单文件交互原型](MCChecker_Config_Prototype.html)。下载 HTML 后可在浏览器中打开；GitHub 文件页显示的是源码。2026-11-12 本轮迭代补充“大版本 → 小版本 → 原版/补丁 → 参数保存历史”四级查看，保留横向信息卡、冻结双轴，并让真实因果关系向右展开。软件身份为前三层，保存次数不是软件层级。多选、完整记录名称与修改原因规则保留，生产实现尚未开始；最终包 166 项离线检查通过，真实浏览器验收仍待完成。

| 资料 | 内容 |
| --- | --- |
| [产品与交互设计](MCChecker_Config_UX_Design.md) | 页面结构、日常流程、状态和验收口径 |
| [后端设计](MCChecker_Config_Backend_Design.md) | 真实代码审查、身份、版本、保存、评审、来源与迁移 |
| [更新来源设计](MCChecker_Update_Source_Design.md) | 网页识别、人工规则、多格式、候选与采集边界 |
| [实施计划](MCChecker_Config_Implementation_Plan.md) | 依赖明确的实施任务及验收/迁移要求 |
| [工作区检查点](WORKSPACE_CHECKPOINT.md) | 本轮重建、远端已保存范围与待完成事项 |

## 原型入口

文件详情默认打开大版本总览：纵轴为版本基线与机台编号/型号，横轴逐级查看大版本、小版本、原版或补丁，再进入精确软件版本的保存历史。汇总卡用于导航，真实保存卡显示完整名称、原因、作者和时间，并可查看参数、比较、详情或导出。参数保留独立标签；普通管理员“编辑 → 核对保存”，评审可选，三个视图读取同一组记录。

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
| 四级版本查看 | 打开 `MachineConfig.xml`，从 69X/6AX 大版本进入小版本，再选原版或补丁包，最后查看精确软件版本的保存历史。折叠和返回后仍可定位所选记录，汇总卡不冒充保存记录。 |
| 补丁与机台型号 | 在 6AX→6A1 中比较原版与 `6A1SPC001`。补丁明确来自原版 `r-b15`，只增加独立补丁基线，原版与 MC-003 当前采用记录不变；该机台型号 MX-820 与软件大版本分别显示。 |
| 创建软件版本 | 从当前小版本或保存记录打开“新建软件版本”，默认该小版本的下一个补丁；选择任何已有小版本（包括尚无原版者）也默认下一个补丁。可按年月建大版本、手填小版本（如 6910）；新小版本默认原版，也可先建补丁。显式选来源或“稍后导入”，填原因后创建，取消或失败不占号。 |
| 空间与来源 | 连线从源卡右侧出、目标卡左侧入，按真实因果向右推进；必要分段保留原版本和真实关系。画布留出横向连接空间，顶部版本轴、左侧对象轴及交叉角应固定。“聚焦机台及来源”收拢行，“全部配置”恢复；列/行分别分页。实际浏览器效果仍待验收。 |
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

大版本按完整年月登记，短码取年份末位和月份 1–9/A/B/C：2026-09→69X、2026-10→6AX、2027-01→71X。2036-09 的 69X 以完整年月和独立身份消歧，不与 2026 合并。小版本号可手填多位尾号，但需匹配大版本；补丁按小版本独立递增为 SPC001、SPC002，规范软件号连写如 `692SPC001`。原版就是小版本自身，不存在 SPC000；未知旧版本保持可用且不猜年月或补丁。

真实保存卡按自身精确软件版本归组。横轴表达因果与版本组织，日期单独显示；必要分段不改变版本身份，汇总连线只聚合真实父引用，不能因折叠、排序或软件前驱补造关系。机台型号不决定软件家族。生产目录迁移、编号并发保护与同事务审计仍待实现。

上一轮 6A0/6A1 的 5 条示例保存保留，本轮新增 1 条 `6A1SPC001` 记录，带准确父引用、演示原因和 `.invalid` 链接。原 690/691、MC-001/002/003 与 CR-024/025 的记录和当前头不变；没有原始输入证据的初始快照仍为空。原型基线写入以所选版本分支 head 为起点，生产则有全配置 main_branch；本轮不以布局补线掩盖两者差异，也不改变核心保存语义。

保存配置、创建评审、初始化、新建软件版本、增加机台、恢复历史和修改来源等实际内容或业务设置，都需要用户填写非空原因，自动操作说明不代填。关联链接仅接受完整 HTTP(S) 地址，规范化并去重后保存和显示为可点击链接；非法协议、嵌入账号密码及控制字符会被拒绝。旧历史缺少原因时仍显示未知，原有说明不被补写成用户原因；恢复操作也不改写原始作者。

来源仅使用 `.invalid` 离线样例，未访问真实网址。预览中的网页、清单和压缩包结果用于演示文件对应、失效检查与候选流程，不证明该站点、登录、提取规则或真实压缩包已适配。自动更新设置不会在此页面启动生产调度器。

来源设置的原因/链接由原型 UI `onEvent` 接线补入事件和 `rules`。生产实现必须由服务端在同一事务中校验并保存设置、原因/链接及审计，不能依赖客户端补事件形成一致性。

本地 XML/JSON 样本处理限定原型支持的结构及大小；它不替代既有生产解析器。当前下载为示例序列化内容，多文件下载为 JSON 文件集。正式模板保真导出、ZIP 与 manifest 清单仍待接入，不能将示例下载直接当作可部署的设备配置包。

生产实现还需要持久提交和审计、跨文件机台绑定、真实来源适配、迁移、模板保真及规模测试。机台视图表示管理系统保存记录，没有设备回读或实际部署证明；多文件操作不承诺一次原子生效。

## 本轮验证

| 验证 | 本轮结果 | 证明范围 |
| --- | --- | --- |
| [verify_model.cjs](verify_model.cjs) | 47 / 47 本轮通过 | 保留既有业务验证，增加完整年月/十年同名、目录只读与未知兼容、多位小版本、SPC 字段边界、先补丁后原版、独立编号与过期预览、失败原子性和准确来源 |
| [verify_sources.cjs](verify_sources.cjs) | 69 / 69 本轮通过 | 最终包复跑：离线来源状态、预览失效、范围/候选隔离，以及受限 JSON/XML 解析与精度规则 |
| [verify_ui.cjs](verify_ui.cjs) | 34 / 34 本轮通过 | 对最终 HTML 复跑四级导航、软件版本创建、错误恢复和保存卡操作的接线回归；使用 Node VM 与 DOM 替身 |
| [verify_roadmap.cjs](verify_roadmap.cjs) | 16 / 16 本轮通过 | 四级分组、展开/定位、向右连线、必要分段、真实父边聚合、行列窗口与转义；属于数据和生成 HTML 检查 |
| 真实浏览器视觉/交互 | 待完成 | 双轴冻结、交叉角、多尺寸、滚动、键盘/焦点、弹窗和实际下载尚未验收 |

本轮最终包共 166 项检查通过：模型 47、来源 69、路线图 16、UI 34。输出见 [模型](verification/model.txt)、[来源](verification/sources.txt)、[路线图](verification/roadmap.txt) 与 [页面](verification/ui.txt)；UI 已在最后一次已有小版本默认补丁修正后，对最终 HTML 重新运行。Node 数据/HTML 与 VM 检查不能替代真实浏览器、冻结轴视觉或生产服务验证，范围及待验项见 [验证记录](VERIFICATION.md)。

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

生产项目基准为 [codex/v32-prototype-parity-20261109](https://github.com/Arragon/MCChecker/tree/codex/v32-prototype-parity-20261109)，准确提交 `d19ee3e8f896a089a2d8eb5628c0e8bb9b18a0de`。本轮原型从 [a3e04dfc](https://github.com/Arragon/MCChecker/commit/a3e04dfc546d2a663a73490cc7f4b1188d68e966) 继续。技能入口与正文固定于 [Arragon/skill-hub@23a6e990](https://github.com/Arragon/skill-hub/tree/23a6e990fec0b7024c5e64098e8a607485b9eb8e)，主要视觉规范参考 [pbakaus/impeccable@2f98774f](https://github.com/pbakaus/impeccable/tree/2f98774f86bc05563876643db37e6c37c322850f)。

路线图的信息组织参考 [GitKraken Desktop 官方界面说明](https://help.gitkraken.com/gitkraken-desktop/interface/)，采用图、记录信息和选中详情配合的方式；视觉继续沿用 MCChecker，不复制商业素材或代码。

组织方式也参考 [FullCalendar Timeline View](https://fullcalendar.io/docs/timeline-view) 的水平资源行和层级展开，以及 [GitHub Projects 路线图布局](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/customizing-the-roadmap-layout) 的分组与查看密度，不复制日期排期语义、不引入 Premium/Scheduler。本轮阅读 [ELK Layered](https://eclipse.dev/elk/reference/algorithms/org-eclipse-elk-layered.html) 与 [Port Side](https://eclipse.dev/elk/reference/options/org-eclipse-elk-port-side.html)，只参考有向分层、连接通道和固定端口侧，不引入 ELK 库。

三层软件身份、四级查看、因果向右、双轴冻结与既有多选/原因规则属于本轮原型迭代。最终离线包检查已完成，真实浏览器验收仍待进行；本轮反馈未批准生产改造，后续生产工作按实施计划推进，本设计包不代表生产功能已经完成。
