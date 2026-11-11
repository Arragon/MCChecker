# 配置管理 V3.3 设计包

先打开 [单文件交互原型](MCChecker_Config_Prototype.html)。下载 HTML 后可在浏览器中打开；GitHub 文件页显示的是源码。当前为 2026-11-11 重建的设计演示，生产实现尚未开始，核心交互的 Node VM 验证已完成，真实浏览器验收仍待完成。

| 资料 | 内容 |
| --- | --- |
| [产品与交互设计](MCChecker_Config_UX_Design.md) | 页面结构、日常流程、状态和验收口径 |
| [后端设计](MCChecker_Config_Backend_Design.md) | 真实代码审查、身份、版本、保存、评审、来源与迁移 |
| [更新来源设计](MCChecker_Update_Source_Design.md) | 网页识别、人工规则、多格式、候选与采集边界 |
| [实施计划](MCChecker_Config_Implementation_Plan.md) | 依赖明确的实施任务及验收/迁移要求 |
| [工作区检查点](WORKSPACE_CHECKPOINT.md) | 本轮重建、远端已保存范围与待完成事项 |

## 原型入口

默认打开文件参数表。普通管理员采用“编辑 → 核对保存”，需要协作时再提交评审。文件、机台和参数视图读取同一组示例保存记录。

| 场景 | 入口 |
| --- | --- |
| 配置文件列表 | [配置文件](MCChecker_Config_Prototype.html#/files) |
| 文件参数表 | [MachineConfig.xml](MCChecker_Config_Prototype.html#/files/f-machine/parameters) |
| 保存记录与关系 | [版本记录](MCChecker_Config_Prototype.html#/files/f-machine/versions)，默认配置列表，“保存关系”按真实父记录查看历史 |
| 一台机台的全部文件 | [MC-001](MCChecker_Config_Prototype.html#/machines/asset-01) |
| 参数更名、属性和值变化 | [参数变化](MCChecker_Config_Prototype.html#/parameters?file=f-machine) |
| 可选评审 | [CR-024](MCChecker_Config_Prototype.html#/review/CR-024) |
| 更新来源 | [更新管理](MCChecker_Config_Prototype.html#/sources)、[添加来源](MCChecker_Config_Prototype.html#/sources/new) |
| 操作回溯 | [操作记录](MCChecker_Config_Prototype.html#/records) |
| 初始化 | [初始化配置](MCChecker_Config_Prototype.html#/initialize) |

## 建议试用

| 例子 | 操作与观察 |
| --- | --- |
| 日常编辑 | 打开 `MachineConfig.xml`，编辑一个参数，核对差异后直接保存。查看新增记录与参数来源；历史记录保持只读。 |
| MC-001 采用 B12 | 在 MC-001 的 `MachineConfig.xml` 行打开“合并更新”。示例当前采用 B11，选择 B12 后核对共同起点/机台当前/新基线，处理冲突，再核对保存；固定校准值需保留。 |
| CR-024 评审 | 该示例由王工提出，林工可回复并处理讨论后批准，再合并。提出人不能独立批准自己的提案；这条示例不让普通保存强制走评审。目标已改变时应重新核对。 |
| 大表与多文件 | 顶部“交互原型”中分别载入“3,248 项参数”和“100 个配置文件”，检查搜索、分页、表格空间及跨页选择。它们是合成样本，不是生产性能成绩。 |
| 来源更新 | “添加来源”载入网页列表、ZIP、JSON 列表、XML 列表或单文件样例，预览并核对接收文件；保存来源后获取更新，再核对候选、载入草稿和保存。高级区可演示手动规则与要素组成地址。 |
| 本地文件 | 在“比较”中导入 [MachineConfig_Compare.xml](samples/MachineConfig_Compare.xml)，相对初始 B12 有 3 个参数值变化；在“初始化”中导入 [DeviceSettings_Initialize.json](samples/DeviceSettings_Initialize.json)，可检查 6 项参数及精确整数。 |
| 错误恢复 | “交互原型”可切换访客、模拟下次保存失败、他人保存、来源失败和多文件部分失败。不同示例互相改变数据；需要回到初始状态时刷新页面。 |

## 演示边界

数据、人员、机台与网址都是示例，状态只保存在页面内存中，刷新还原。身份切换演示权限状态，不代表生产身份认证；并发与不可变记录的模型检查不代表磁盘事务已经实现。

来源仅使用 `.invalid` 离线样例，未访问真实网址。预览中的网页、清单和压缩包结果用于演示文件对应、失效检查与候选流程，不证明该站点、登录、提取规则或真实压缩包已适配。自动更新设置不会在此页面启动生产调度器。

本地 XML/JSON 样本处理限定原型支持的结构及大小；它不替代既有生产解析器。当前下载为示例序列化内容，多文件下载为 JSON 文件集。正式模板保真导出、ZIP 与 manifest 清单仍待接入，不能将示例下载直接当作可部署的设备配置包。

生产实现还需要持久提交和审计、跨文件机台绑定、真实来源适配、迁移、模板保真及规模测试。机台视图表示管理系统保存记录，没有设备回读或实际部署证明；多文件操作不承诺一次原子生效。

## 本轮验证

| 验证 | 本轮结果 | 证明范围 |
| --- | --- | --- |
| [verify_model.cjs](verify_model.cjs) | 30 / 30 通过 | 纯模型中的保存、并发拒绝、不可变快照、独立评审、字段合并、恢复、身份及来源归属 |
| [verify_sources.cjs](verify_sources.cjs) | 69 / 69 通过 | 离线来源状态、预览失效、范围/候选隔离，以及受限 JSON/XML 解析与精度规则 |
| [verify_ui.cjs](verify_ui.cjs) | 13 / 13 通过 | 实际单 HTML 的页面路由、控件处理函数、草稿/并发/评审/来源/合并/恢复及工作区隔离；使用 DOM 替身 |
| 真实浏览器视觉/交互 | 待完成 | 多尺寸、滚动、键盘/焦点、响应式、弹窗和实际下载尚未验收 |

以上三组通过结果来自本轮重建后的实际执行，不沿用中断前的测试数字。Node 检查不能替代真实浏览器或生产服务验证。检查范围及待验项见 [验证记录](VERIFICATION.md)。

在仓库根目录可重新构建与运行模块检查：

```sh
python docs/design/v3.3/build_prototype.py
node docs/design/v3.3/verify_model.cjs
node docs/design/v3.3/verify_sources.cjs
node docs/design/v3.3/verify_ui.cjs
```

源文件在 [prototype/](prototype/)，构建脚本把样式、模型、来源模块与界面合并成一个 HTML；应修改源文件后重建，不手动维护两份不同内容。

## 基准与下一检查点

项目基准为 [codex/v32-prototype-parity-20261109](https://github.com/Arragon/MCChecker/tree/codex/v32-prototype-parity-20261109)，准确提交 `d19ee3e8f896a089a2d8eb5628c0e8bb9b18a0de`。本轮技能入口与正文固定于 [Arragon/skill-hub@23a6e990](https://github.com/Arragon/skill-hub/tree/23a6e990fec0b7024c5e64098e8a607485b9eb8e)，主要视觉规范参考 [pbakaus/impeccable@2f98774f](https://github.com/pbakaus/impeccable/tree/2f98774f86bc05563876643db37e6c37c322850f)。

生产 UI 改造前，需要确认这次具体原型的默认页面、核心流程、信息密度、术语与状态恢复。原型确认后进入实施计划，逐项区分真实实现、测试通过和现场验收；本设计包本身不代表生产功能已经完成。
