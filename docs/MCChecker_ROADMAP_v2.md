# MCChecker 开发路线图

文档日期：2026-09-11。设计基线：`v1.0.1` / `e47e54a01f0ad6d74db3a08670a1034f937575c2`。配套设计：[MCChecker_ARCHITECTURE.md](MCChecker_ARCHITECTURE.md)。执行项目：[MCChecker › Overview](https://linear.app/inhandy/project/mcchecker-9275450a22c3/overview)。本文件是可独立使用的开发计划，包含每个任务的问题、范围、实现注意、验收、依赖和优先级。

## 1. 目标、范围和当前判断

【建议】继续采用 NiceGUI 模块化单体、APScheduler 与本地文件/JSON。先修现有配置生命周期、权限、节点定位、审阅写回、临时文件、计算和离线部署，再整理应用操作边界和核心界面。目标不是换技术栈，而是当前可靠、下一阶段同类功能可局部扩展。

【代码确认】当前已实现配置导入/查看、收藏、搜索、更新/归档、四类比较、修改记录、审阅、多机型和 DL。已确认多处业务正确性与数据安全缺陷，详见下列 F/T 任务及固定源码链接。profile 是 ContextVar，不是普通共享全局；离线资源依赖尚未经过实测；位置/同名序号不能被视为跨版本稳定节点身份。

【合理推断】真实并发量、最大文件规模、现网 Python/NiceGUI/APScheduler 版本、代理拓扑和旧数据分布尚未确认。本路线图不报告未执行的测试、性能数字、工期或现网事故。先在副本和临时目录验证，再实施数据操作；尚未完成的验收不得标记为通过。

计划共 5 个阶段、20 个交付任务：12 个 P0、7 个 P1、1 个 P2。P3 只进入 Deferred，不创建执行 Issue。每个任务可以包含少量可独立 Review 的提交，但不拆成每个函数一个 Issue；跨多个文件的工作按业务验收边界组织。

## 2. 优先级和执行规则

P0 影响正确性、数据安全、稳定性、核心功能、兼容性或离线部署；P1 明显影响核心体验、维护成本、长期可靠性或近期功能扩展；P2 有明确收益但不阻塞可用版本；P3 需求未验证或收益不足。

Linear 优先级映射为 P0→Urgent(1)、P1→High(2)、P2→Normal(3)。不使用 Linear 的数值 0 表示 P0，因为该值实际是 No priority。所有任务初始 Backlog，不虚设完成状态、负责人、日期和工时。规划完成不代表开发完成。

依赖表示最终集成/验收的真实前置，不阻止在旧文件内先落独立安全补丁。例如 T08 的缺失导入、T11 的数值修复、T12 的端口修复，可以先交付，不必等待整个 M1 或大规模目录重构。

总体顺序：P0 稳定性/数据/兼容/离线 → 必要边界与测试 → 核心 UI/UX → 性能和发布可靠性 → 视觉 polish。测试隔离 T13 提前并行，不等 P0 结束才建立测试。

## 3. 阶段与 Milestone

### M1 · 修复正确性、数据安全与离线发布

目标：消除可定位的 P0，并证明基础离线发行路径可行。

主要工作：T01 统一写操作授权并显式传递机型上下文；T02 收紧文件路径与下载源输入边界；T03 消除配置内容与工具链接的脚本注入路径；T04 实现原子持久化与并发安全的版本保存；T05 补齐迁移、重命名与删除的恢复链路；T06 分离完整源文档与展示树并修复节点定位；T07 重建审阅提交的版本校验与保真输出；T08 修复临时上传与历史记录的文件引用链路；T09 统一更新入口的校验与受控异步执行；T10 保证解析缓存与源版本的一致性；T11 修复 DL 数值输入、结果对应与求解语义；T12 建立可复现离线发布包并修复启动配置。

依赖与进入条件：无统一前置阶段。T01/T02/T03/T04/T06/T11/T12 可并行起步，其余按任务级依赖集成；T13 测试隔离应尽早并行。

阶段验收：全部 P0 对应回归通过，原文/历史/配置可恢复，越权与串机型被阻止，审阅不误改、不丢未改字段，数值结果不误导，临时/归档/记录可用；冷缓存禁公网的基础部署验证通过。

Definition of Done：每个修复有反例、正例与兼容验收，故障场景保留原数据；没有“待验证”被记为通过。不等待整体重构再发布可以独立上线的安全修复。

Linear milestone ID：`41f344a7-0f42-4025-9131-e76fefef96f6`。

### M2 · 明确模块边界并建立回归门槛

目标：把修复形成的真实接口沉淀为少量应用操作、可靠存储和统一错误契约。

主要工作：T13 隔离测试数据并建立关键回归门槛；T14 提取应用操作边界并统一错误与日志。

依赖与进入条件：T13 可以立即开展；T14 需要相关 P0 的操作契约稳定。

阶段验收：核心导入、审阅、diff 可脱离 NiceGUI 测试，测试不写真实 data；旧入口兼容，新增入口不重复授权与提交逻辑。

Definition of Done：依赖方向清楚，旧测试修正后通过，回归用例与 CI 有效；没有通用 Repository、DI 或插件框架。

Linear milestone ID：`fe4bb3cc-efec-4bdb-8b08-0b2afb2c57e6`。

### M3 · 优化核心工作流与信息密度

目标：让文件、版本、搜索、审阅、计算和管理操作连续且可解释。

主要工作：T15 收敛工作区导航与配置管理表单；T16 统一树视图并修复搜索与版本对比连续性；T17 保留审阅与计算草稿并明确任务结果。

依赖与进入条件：T14 与对应 FileRef/locator/任务/审阅契约稳定；任务依赖为最终集成前置。

阶段验收：关键入口完整，保存后即时一致，历史选择准确，临时文件来源清楚，草稿/筛选不无故丢失，空/错/忙/冲突状态各有含义。

Definition of Done：代表用户角色与视口的浏览器验收通过；没有新增 Dashboard 或大量装饰卡片；所有控件可离线加载。

Linear milestone ID：`ceb51f90-fb97-48d5-8e73-b782805ced3a`。

### M4 · 验证性能与发布可靠性

目标：在完整语义不退化的前提下控制渲染/查询成本，并完成组合流程、升级与恢复验收。

主要工作：T18 按代表性负载优化渲染与查询成本；T19 完成核心流程、离线与升级恢复验收。

依赖与进入条件：T18 依赖任务、缓存正确性与核心视图；T19 汇总此前任务。缓存正确性 T10 属于 M1，不能拖到性能阶段。

阶段验收：代表性冷暖缓存、长会话、双客户端场景有测量；禁公网首次安装/首访及旧数据升级/恢复通过。

Definition of Done：记录环境、样本、结果、失败和支持边界；发布包可重建，备份可恢复，没有未解决 P0。

Linear milestone ID：`d5be8116-2c33-4c78-910b-4901a0c3a8d6`。

### M5 · 收敛非必要视觉样式

目标：统一已有样式并减少没有功能收益的装饰。

主要工作：T20 收敛视觉样式并清理重复覆盖。

依赖与进入条件：T19 验收后的稳定布局；不反向阻塞 M4 的可用版本。

阶段验收：焦点、可读性、信息密度不退化，无运行时公网资源和新 UI 依赖。

Definition of Done：代表页面视觉与功能回归通过，样式修改可单独回滚。

Linear milestone ID：`a5ebc7d2-e138-4595-88f0-ab48ff7d17c3`。

## 4. 任务总表与真实依赖

| 任务 | 优先级 | 阶段 | 交付对象 | 前置任务 | Linear |
|---|---|---|---|---|---|
| T01 | P0 | M1 | 统一写操作授权并显式传递机型上下文 | 无 | 尚未核实同步结果 |
| T02 | P0 | M1 | 收紧文件路径与下载源输入边界 | 无 | 尚未核实同步结果 |
| T03 | P0 | M1 | 消除配置内容与工具链接的脚本注入路径 | 无 | 尚未核实同步结果 |
| T04 | P0 | M1 | 实现原子持久化与并发安全的版本保存 | 无 | 尚未核实同步结果 |
| T05 | P0 | M1 | 补齐迁移、重命名与删除的恢复链路 | T01, T02, T04 | 尚未核实同步结果 |
| T06 | P0 | M1 | 分离完整源文档与展示树并修复节点定位 | 无 | 尚未核实同步结果 |
| T07 | P0 | M1 | 重建审阅提交的版本校验与保真输出 | T01, T04, T06 | 尚未核实同步结果 |
| T08 | P0 | M1 | 修复临时上传与历史记录的文件引用链路 | T01, T02 | 尚未核实同步结果 |
| T09 | P0 | M1 | 统一更新入口的校验与受控异步执行 | T01, T02, T04, T06 | 尚未核实同步结果 |
| T10 | P0 | M1 | 保证解析缓存与源版本的一致性 | T04, T06 | 尚未核实同步结果 |
| T11 | P0 | M1 | 修复 DL 数值输入、结果对应与求解语义 | 无 | 尚未核实同步结果 |
| T12 | P0 | M1 | 建立可复现离线发布包并修复启动配置 | 无 | 尚未核实同步结果 |
| T13 | P1 | M2 | 隔离测试数据并建立关键回归门槛 | 无 | 尚未核实同步结果 |
| T14 | P1 | M2 | 提取应用操作边界并统一错误与日志 | T01, T04, T06, T07, T09, T13 | 尚未核实同步结果 |
| T15 | P1 | M3 | 收敛工作区导航与配置管理表单 | T01, T08, T09, T14 | 尚未核实同步结果 |
| T16 | P1 | M3 | 统一树视图并修复搜索与版本对比连续性 | T03, T06, T08, T14, T15 | 尚未核实同步结果 |
| T17 | P1 | M3 | 保留审阅与计算草稿并明确任务结果 | T07, T09, T11, T14, T15 | 尚未核实同步结果 |
| T18 | P1 | M4 | 按代表性负载优化渲染与查询成本 | T09, T10, T16, T17 | 尚未核实同步结果 |
| T19 | P1 | M4 | 完成核心流程、离线与升级恢复验收 | T05, T12, T13, T14, T15, T16, T17, T18 | 尚未核实同步结果 |
| T20 | P2 | M5 | 收敛视觉样式并清理重复覆盖 | T19 | 尚未核实同步结果 |

任务表的前置关系不是仅写在描述里：同步完成后，在 Linear 使用实际 blocking/blockedBy 关系建立。不存在估算关键路径时长，因为尚无负责人和开发速度基线。

### 4.1 建议的并行工作线

安全线从 T01/T02/T03 开始；持久化与生命周期线从 T04 开始到 T05；语义正确性线从 T06 到 T07/T10；执行线 T09 复用安全、保存和解析边界；临时/版本线 T08 修现有核心功能；DL T11 和发行 T12 可独立开展；T13 从第一批修复同步提供测试隔离。不同工作线交会处用实际依赖合并，不以“阶段顺序”人为阻塞全部工作。

T14 将经过修复的应用操作提取为清晰边界，随后 T15/T16/T17 改界面与工作流。T18 只做有证据的性能优化，T19 是完整发布门禁；T20 视觉清理不能反向阻塞已经验收的可用版本。

## 5. 开发任务明细

以下每项均包含独立完成条件。所有源码链接固定到审查提交；开发时应先核对分支差异，已经修复的问题可用测试关闭，不重复重构。

### T01 · 统一写操作授权并显式传递机型上下文

Linear：尚未核实同步结果。阶段：M1。对应问题：F01。

路线图 T01 / 问题 F01；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】home.py 的机型新增、更名、删除入口及对应回调未形成完整授权检查；DL 页面可直接保存共享系数与绑定。部分危险操作只在打开对话框时校验权限。storage 使用 ContextVar，而不是普通共享全局变量，但 get_active_profile() 在上下文值为 default 时仍回读 app.storage.user 的 device_model，因此显式 use_profile("default") 可能被浏览器选择覆盖。无效 profile 字符又被静默归一为 default。

影响：未授权修改、机型数据误操作以及同一操作在不同入口下使用不同身份/机型。后续增加入口会继续复制这些缺陷。代理把客户端识别为本机的风险依赖实际拓扑，尚未现场验证。

依据：[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S03 · 身份与权限判断](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/utils/auth.py)；[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S12 · 机型注册、复制与删除](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/device_models.py)；[S16 · DL 输入、编辑、绑定与结果页](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/dltool.py)。

#### Scope
管理写操作、身份边界与现有机型隔离；不建设账号中心、SSO 或通用 RBAC。

#### Implementation Notes
【建议】先原地封堵机型与 DL 管理写入口，在提交时重新校验。定义轻量 Actor 与动作权限表；deployer 与 admin 保持独立能力，不强行改成互斥角色。页面解析身份和机型，向业务函数传入 profile_id；任务在创建时固定机型。显式 default 优先于浏览器状态，无效/不存在机型返回明确错误。保留旧入口作为兼容包装，但包装在 UI 边界解析上下文，不能让核心存储回读 NiceGUI。直连默认不信任转发头；反向代理须配置受信代理及来源规则。

迁移：先补提交守卫与 default 判定，再逐个将业务入口改为显式参数；保留旧文件结构与读取方式。

#### Acceptance Criteria
访客调用机型增删改、DL 配置保存、更新源写入均被拒绝，持久化文件哈希不变。管理员撤权后，已经打开的提交按钮也被拒绝。default 与非 default 两个会话及后台任务交错执行不串数据。未知机型不能读写默认目录。旧 IP 对应表、管理员表与既有协作收藏/备注仍可使用；直连与实际代理拓扑分别验收。

#### Dependencies
无，可独立启动。

#### Priority
P0 / Linear Urgent。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
先提交授权漏点与回归；再提交 default/context 解析和显式参数适配。不要同时变更身份数据模型。

### T02 · 收紧文件路径与下载源输入边界

Linear：尚未核实同步结果。阶段：M1。对应问题：F02。

路线图 T02 / 问题 F02；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】get_config_path()/get_archive_dir() 直接拼接名称；save_config_file() 没有统一文件名校验；新增映射也不走更新映射使用的校验。downloader 使用 urllib.request.urlopen 和无上限 resp.read()，未限制 scheme、目标、重定向与大小。现有下载路由有 basename 检查，但没有统一的真实路径包含性与符号链接策略。

影响：输入可越出预期存储边界；任意 URL 让服务端成为不受控的读取代理；超大内容可耗尽资源。内网下载是业务能力，不能简单封禁全部私有地址。

依据：[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S10 · URL 下载](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/downloader.py)；[S11 · 当前文件与归档下载路由](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/file_downloads.py)；[S17 · 更新设置、人员映射与管理员表单](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/management.py)。

#### Scope
所有导入、更新、下载、归档与记录路径，及服务端 URL 读取。

#### Implementation Notes
【建议】统一 validate_filename 与 resolve_within：拒绝绝对路径、路径分隔符、空字节、驱动器/UNC/ADS 和越界解析，不靠替换字符偷偷改名。保留安全的既有 Unicode 文件名；对历史危险名称只提示并隔离操作，不自动删除。下载仅允许 http/https，目标由部署者配置明确主机/端口或所需网段，默认不允许 loopback、链路本地、元数据端点及未批准目标；每次重定向重新检查，DNS 解析结果和最终连接目标都应受策略约束。使用分块读取、总字节与耗时上限，解析增加深度/节点预算。

迁移：先统一校验并盘点历史名称/来源；在启用收紧策略前给出兼容检查结果，对不安全旧项显式阻止执行，保留原数据。

#### Acceptance Criteria
覆盖 ../、反斜杠、绝对路径、Windows 盘符与 UNC、符号链接越界、编码路径和安全中文名。file:/ftp:/data: 等协议被拒绝；重定向不能绕过允许列表；批准的内网 HTTP/HTTPS 仍工作。超大、超时和异常来源不覆盖当前文件，不在日志泄漏 URL 凭据。

#### Dependencies
无，可独立启动。

#### Priority
P0 / Linear Urgent。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
先统一路径策略，再统一下载源/重定向/资源预算；两部分都保留兼容盘点。

### T03 · 消除配置内容与工具链接的脚本注入路径

Linear：尚未核实同步结果。阶段：M1。对应问题：F03。

路线图 T03 / 问题 F03；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】viewer、search 和收藏树把 label/value/描述等直接拼入 ui.html(..., sanitize=False)。home 的 data-fav-path、DOM 查询以及 window.open 直接插入数据或工具 URL。records 中部分差异输出已经转义，说明现有处理不一致，而不是完全没有防护。

影响：导入文件或共享收藏可以把数据变为浏览器可执行内容，影响其他查看者与有管理权限的用户。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S05 · 文件查看、筛选与修改备注](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/viewer.py)；[S23 · 全局搜索页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/search.py)；[S20 · 修改记录列表](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/records.py)；[S26 · 工具菜单页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/tools.py)。

#### Scope
所有动态 HTML、JS 字符串、DOM 定位和外链；不靠强行关闭所有 HTML 破坏现有树控件。

#### Implementation Notes
【建议】普通文本优先使用 ui.label 等文本 API；必须拼 HTML 时逐个转义文本与属性。固定模板才可 sanitize=False。JavaScript 参数统一 JSON 编码，URL 使用原生导航/链接组件并校验 scheme，外链使用 noopener/noreferrer。收藏移除使用受控元素引用或编码后的稳定键，不再拼选择器字符串。保留已转义的差异展示。

迁移：先逐个修危险插值，再归并安全渲染助手；不要等共用树重构完成才修注入。

#### Acceptance Criteria
含引号、尖括号、HTML 标签、事件属性和脚本样式文本的 XML/JSON/备注均作为文字显示，不执行脚本、不额外出网。工具链接不接受 javascript:；正常特殊字符 URL 可打开。收藏、搜索与历史页面均覆盖，不能只修 viewer。

#### Dependencies
无，可独立启动。

#### Priority
P0 / Linear Urgent。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
按危险输出点修复并加入数据转义用例；公共控件提取留到 T16。

### T04 · 实现原子持久化与并发安全的版本保存

Linear：尚未核实同步结果。阶段：M1。对应问题：F04。

路线图 T04 / 问题 F04；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】storage._save_json、device_models 和 DL 配置直接覆盖 JSON；_load_json 在文件损坏时返回空默认值。save_config_file 先移动当前版本再写新文件，归档重名回退仅精确到秒；记录文件也只用秒时间戳。多个读改写没有共同互斥边界。

影响：中断可留下截断 JSON/缺失当前文件；并发会丢修改；同秒保存可能覆盖旧归档或记录。损坏后读成空集合再保存会把可恢复数据覆盖。

依据：[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S12 · 机型注册、复制与删除](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/device_models.py)；[S15 · DL 计算引擎与配置](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/dltool.py)。

#### Scope
权威 JSON、配置、归档、修改记录与生成文件；不引入数据库，也不声称单文件 replace 提供多文件事务。

#### Implementation Notes
【建议】用同目录唯一临时文件完成写入、flush/fsync、原子替换；按机型的进程内 RLock 覆盖完整读—校验—修改—提交，机型注册表另用全局锁，锁内不做网络请求或大 diff。先可靠保留旧版本，再原子替换当前文件，不提前搬走唯一有效副本。归档/记录采用独占创建和唯一后缀，旧命名仍可读取。区分文件不存在与损坏，损坏的权威 JSON 进入只读保护并提示恢复。写入失败必须上抛；缓存/diff 失败可以降级但须日志。限定单服务进程、单调度器写同一 data 根。

迁移：保持所有旧格式与命名读取；替换底层写入原语后再逐步收拢各模块的独立写入。

#### Acceptance Criteria
注入写入中断、磁盘满、无权限、归档失败、os.replace 失败，旧文件仍可读且不得虚报成功。同秒连续更新和并发写不会覆盖归档；两个用户同时增加不同备注均保留。损坏 JSON 不被自动清空。恢复后原始文件与旧版哈希一致；平台特定持久性限制有记录。

#### Dependencies
无，可独立启动。

#### Priority
P0 / Linear Urgent。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
先原子 I/O 与损坏保护，再并发锁和唯一归档；故障注入随每一步提交。

### T05 · 补齐迁移、重命名与删除的恢复链路

Linear：尚未核实同步结果。阶段：M1。对应问题：F05。

路线图 T05 / 问题 F05；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】启动调用 ensure_profile_layout(migrate_legacy=True)，逐项 shutil.move，异常只告警。重命名先保存映射，再移动文件，更新收藏/绑定但遗漏 records、审阅引用和 DL 来源等。copy_profile_data 仅复制部分类型，还复制可丢弃缓存；删除使用 ignore_errors=True，可能部分失败却显示成功。

影响：升级可能留下半迁移布局；重命名后记录、备注、计算系数来源失联；删除和复制结果与界面承诺不一致，难以恢复。

依据：[S01 · 启动入口](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/run.py)；[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S12 · 机型注册、复制与删除](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/device_models.py)；[S17 · 更新设置、人员映射与管理员表单](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/management.py)。

#### Scope
升级与现有文件生命周期；不把机型空间升级为部门/租户系统。

#### Implementation Notes
【建议】为迁移和重命名建立具体操作的 preflight、备份清单、步骤状态、校验和恢复流程，不搭通用事务引擎。旧数据迁移改为备份—复制—校验—启用，原目录保留到人工确认。重命名先检查目标冲突，收集所有已存在的引用，完成数据与引用写入后再切换映射；中断启动时明确恢复/阻止继续写。删除先移入有清单的隔离目录，默认不物理清除，不自动过期删除。机型复制明确区分业务配置和操作历史；默认不复制权限身份、待审事项和缓存。

迁移：先建立只读盘点与备份，再替换有破坏性的 move/rmtree 路径；旧目录不自动清理。

#### Acceptance Criteria
旧布局、纯新布局、混合布局、目标冲突、任意步骤失败均有确定结果。重复迁移幂等，失败不丢原数据。重命名后 configs/archive/records/favorites/bindings/edit_remarks/DL 来源以及生成文件引用可追溯。删除可恢复且部分失败不能报全部成功。复制说明与实际内容逐类一致。

#### Dependencies
T01, T02, T04

#### Priority
P0 / Linear Urgent。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
按迁移、重命名、隔离删除三个可独立 Review 的提交组完成；共享同一恢复清单契约，不扩展为框架。

### T06 · 分离完整源文档与展示树并修复节点定位

Linear：尚未核实同步结果。阶段：M1。对应问题：F06。

路线图 T06 / 问题 F06；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】parser 对 >=200,000 字节 XML 使用属性白名单，非白名单属性直接不进入解析树；XML 同名兄弟共用 id，get_all_values 字典可能覆盖同路径值。JSON 展示值统一为字符串，根标量/空根类型不足以由当前树准确还原。viewer 在过滤树及参数元数据隐藏后的 children 上重新 enumerate，随后用该下标形成审阅 node_key。

影响：大文件显示/差异遗漏数据；重复节点收藏与对比混淆；筛选页面对某节点提交的建议可能定位到另一个原始节点。位置或 label+occurrence 都不能被当成跨版本稳定身份。

依据：[S05 · 文件查看、筛选与修改备注](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/viewer.py)；[S06 · XML/JSON 解析与节点路径](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/parser.py)；[S07 · 审阅定位、修改与序列化](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/reviewing.py)；[S14 · 结构化与文本差异](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/differ.py)；[S22 · 搜索过滤与计数](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/searching.py)。

#### Scope
解析契约、节点引用、收藏/绑定兼容与结构化差异；不建设 schema registry，不给所有文件改 UUID。

#### Implementation Notes
【建议】保留统一展示结构作为 ViewTree，但从完整源文档解析出 DocumentSnapshot：原始 bytes、hash、格式、完整类型与节点 locator。JSON locator 使用有转义的 token 路径/JSON Pointer；XML 用展开命名空间、兄弟位置和明确的 text/attribute 定位，仅在对应源版本内解释。过滤、分组、隐藏字段只改变展示，不重建定位。结构化 diff 对类型、空容器和重复节点有明确语义；旧 path 仅作为显示/迁移别名，不保证跨版本自动匹配。取消按文件大小丢弃语义字段，改为展示层按需展开。

迁移：为节点增加 locator、source_hash 和 parser_version，保留原 id/label；新旧读取并行，旧缓存按版本失效重建。

#### Acceptance Criteria
199,999/200,000 字节两侧相同字段均被保留；重复 XML 标签、命名空间、带点/斜杠 JSON 键、空对象/数组/根标量及类型变化有用例。搜索命中子节点、隐藏参数属性后，提交 locator 仍指向完整原始文档同一节点。旧收藏/绑定可唯一解析者继续使用，歧义项标记待重新绑定，不猜测迁移。

#### Dependencies
无，可独立启动。

#### Priority
P0 / Linear Urgent。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
先完整源快照/locator，再消费者适配和类型/重复节点 diff；保留旧 path 别名。

### T07 · 重建审阅提交的版本校验与保真输出

Linear：尚未核实同步结果。阶段：M1。对应问题：F07。

路线图 T07 / 问题 F07；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】apply_review_updates 仅按 node_key 修改展示树，不检查源版本和原值；review 页面从缓存/当前文件生成内容，再批量更新备注状态。序列化会从有损展示树重建 XML/JSON，多文件任一失败可留下部分结果；提交没有完整幂等与过期选择检查。

影响：可能误改节点、覆盖过期建议、丢非修改字段，或出现“文件已生成但状态未提交”。这是正确性问题，不能仅通过换节点 ID 解决。

依据：[S07 · 审阅定位、修改与序列化](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/reviewing.py)；[S08 · 审阅提交与结果文件生成](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/review.py)；[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S06 · XML/JSON 解析与节点路径](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/parser.py)。

#### Scope
现有建议—审阅—生成新文件闭环；不新增多级审批、工作流引擎或自动合并。

#### Implementation Notes
【建议】建议记录保存 source_hash、locator、原始类型/值和 schema_version。提交时重新授权、在锁内核验 hash 与仍为 pending 的记录，只修改原始源文档中选定位置；输出重解析并比较预期差异后才能发布。不将展示树作为写回源。按单个源文件组成可独立提交批次，批次有唯一 ID/结果清单和可恢复状态；跨文件明确逐文件成功/失败，不伪装全局原子。无法保证保真的文档暂保留只读和原文下载，明确不允许不安全生成；保留原文，不静默修复命名空间后当作原始文档导出。

迁移：新增版本与批次字段，旧备注原样保留；缺少基准版本的待审备注必须重新核验/提交，不能自动假定适用于当前文件。

#### Acceptance Criteria
源文件在建议提交后更新时拒绝自动应用并显示冲突。筛选/重复节点建议不误改。仅选定值变化，未选属性、类型、文本、尾文本及受支持注释/命名空间语义保留；不支持的特性明确阻止生成。重复点击/双管理员提交只产生一次业务结果。故障重启后能辨识待恢复批次；单文件失败不标为 approved，原配置不被覆盖。

#### Dependencies
T01, T04, T06

#### Priority
P0 / Linear Urgent。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
先版本/原值校验与拒绝不安全导出，再按源文件批次完成保真生成和恢复；不等待新 UI。

### T08 · 修复临时上传与历史记录的文件引用链路

Linear：尚未核实同步结果。阶段：M1。对应问题：F08。

路线图 T08 / 问题 F08；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】访客上传/URL 导入解析后丢弃内容，只以 filename 打开标签；viewer 随后仅从持久化目录加载，同名时可能显示服务器旧文件，否则显示不存在。home._load_archive_tree 的冷缓存分支使用 os 但模块未导入 os。records 打开 /record_view 时没有 profile 参数，独立页面再次依赖浏览器上下文。

影响：已经承诺的访客临时查看不成立；旧版本冷缓存不可查看；切换机型后修改记录链接可能打开错误空间。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S05 · 文件查看、筛选与修改备注](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/viewer.py)；[S11 · 当前文件与归档下载路由](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/file_downloads.py)；[S18 · 历史列表与比较入口](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/history.py)；[S20 · 修改记录列表](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/records.py)；[S21 · 修改记录独立页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/record_view.py)；[S38 · 标签持久化](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/tabs_state.py)。

#### Scope
现有临时文件、当前版本、归档与修改记录；不统一成复杂资源平台。

#### Implementation Notes
【建议】引入简单 FileRef(profile_id, kind, name, version/token)，kind 仅为 current/archive/record/temp。临时原始内容放入会话受控临时目录或有容量限制的会话存储，使用不可猜测 token、所有者验证和过期清理；不放共享 configs，也不只用文件名索引。查看、下载和标签持有同一 FileRef，明确显示来源。补齐 os 导入与异常清理。记录链接显式携带机型，旧链接保留兼容解析但显示当前解析机型。

迁移：先修缺失导入和临时内容流，旧标签字典通过适配器转成 FileRef；过期临时标签不再回退同名服务器文件。

#### Acceptance Criteria
访客上传新文件能查看；上传与服务器同名文件时显示临时内容且服务器 hash 不变；不同会话不能打开对方临时 token。临时项过期有明确提示。清空缓存后归档仍可查看。跨机型同名记录链接始终打开指定版本；当前/归档下载的旧 URL 参数继续兼容。

#### Dependencies
T01, T02

#### Priority
P0 / Linear Urgent。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
缺失 os 导入可立即单独修；随后接通临时 FileRef 和显式记录机型。

### T09 · 统一更新入口的校验与受控异步执行

Linear：尚未核实同步结果。阶段：M1。对应问题：F09。

路线图 T09 / 问题 F09；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】上传已部分使用 asyncio.to_thread，但管理页全量/单项更新、历史 diff、全局搜索、审阅和部分 DL 计算仍同步执行。scheduler.run_single_update 下载后直接保存，缺少解析验证。home 的 _busy_processing 是全局布尔，不能表达多个并发任务；记录页另有定时拉取，与服务器调度重叠。

影响：网络错误页可能替代有效配置；一次长操作影响其他用户连接；手动/定时重入重复归档。全局 busy 既相互干扰，又可能被先完成的任务提前解除。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S09 · 定时与手动更新](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/scheduler.py)；[S10 · URL 下载](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/downloader.py)；[S17 · 更新设置、人员映射与管理员表单](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/management.py)；[S18 · 历史列表与比较入口](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/history.py)；[S20 · 修改记录列表](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/records.py)；[S23 · 全局搜索页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/search.py)；[S16 · DL 输入、编辑、绑定与结果页](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/dltool.py)。

#### Scope
更新、重型页面工作与任务结果；不引入 Celery、Redis 或分布式队列。

#### Implementation Notes
【建议】保留 APScheduler，启动/关闭由统一生命周期管理。手动与定时入口调用同一 update 操作：下载到暂存、按格式验证、比较源 hash、原子提交，再生成可重建 diff。按 profile+操作键防重入，使用有上限的执行器/队列和明确 TaskStatus。I/O/计算离开 UI 事件循环；UI 更新回到原客户端上下文，导航离开后结果不得覆盖新页面。记录拉取只有服务端调度负责，页面仅刷新状态。取消只在安全边界生效，不能宣称取消 await 已终止工作线程。

迁移：保留现有 to_thread 和加载代际守卫，先修其他同步入口；待同等失败测试通过再移除全局 busy，不能直接删除超时 workaround。

#### Acceptance Criteria
HTTP 200 错误页、畸形 XML/JSON、超时和超量均不覆盖当前配置。相同文件内容不重复归档，并记录检查时间。手动与定时同时触发同一更新不会重复提交。双客户端慢下载/比较期间另一客户端可操作且无断连；异常、离页、重试后 busy 必须恢复。关闭服务停止接单并明确等待/恢复行为。

#### Dependencies
T01, T02, T04, T06

#### Priority
P0 / Linear Urgent。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
先导入/更新的下载验证与提交，再收拢异步执行、防重入和调度生命周期。

### T10 · 保证解析缓存与源版本的一致性

Linear：尚未核实同步结果。阶段：M1。对应问题：F10。

路线图 T10 / 问题 F10；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】parse_cache 按源路径散列定位，检查 mtime_ns/size，但没有 parser/schema 版本；保存 tree 后再读取/写入元数据可能把不同时间的源与树组合。两份缓存文件使用固定 .tmp，多个写者可冲突。缓存清理用删除文件数扣除条目数，统计口径也不一致。

影响：修复解析器后旧树仍可能命中；并发更新可返回错误版本或产生损坏缓存。审阅尤其不能以缓存作为权威输入。

依据：[S13 · 解析缓存](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/parse_cache.py)；[S05 · 文件查看、筛选与修改备注](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/viewer.py)；[S08 · 审阅提交与结果文件生成](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/review.py)；[S24 · 收藏实时值解析](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/favorites_live.py)。

#### Scope
已有解析缓存，不引入外部缓存服务。

#### Implementation Notes
【建议】缓存只对应不可变的源快照，键/元数据包括源 hash、parser_version、cache_schema_version。用一次快照解析得到树，不在解析后重新读取源状态来冒充版本。优先单文件缓存封装或唯一代际目录加原子指针，避免两文件不一致；使用唯一临时名与同键互斥。审阅从校验后的原文重新构建或使用同 hash 的完整源模型。缓存损坏直接丢弃重建；清理只访问受控缓存根，统一按条目报告。

迁移：旧缓存一律按缺少版本元数据处理为 miss；保留源文件和历史版本，不迁移可丢弃树。

#### Acceptance Criteria
解析器升级即失效；同大小/同 mtime 的不同内容不会用于权威操作。源在解析期间替换、同键并发写、缓存半写与过期清理均不返回混合版本。删全部缓存后业务可用，缓存清理不触碰原配置或归档。

#### Dependencies
T04, T06

#### Priority
P0 / Linear Urgent。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
先缓存版本与源快照一致性，再原子缓存/并发和清理统计；不把它当性能可选项。

### T11 · 修复 DL 数值输入、结果对应与求解语义

Linear：尚未核实同步结果。阶段：M1。对应问题：F11。

路线图 T11 / 问题 F11；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】_collect_inputs 使用 inp.value or 0.0，将未设置范围变为 0；_pp_scale_info 用 or 1.0，使真实 0 被替换且分母为 0 检查失效。反向结果经过范围过滤后按旧 y_vals 下标贴回目标 y。poly_find_x 仅网格命中/变号检测，不能保证找出切触重根，且未单独处理左端点和恒等情形。字段提取失败可默认为 0 并仍出现成功提示。

影响：结果数值、输入标签或边界可能错误；“全部实根/无解/最优解”的文案超出了算法保证，影响工具可信度。

依据：[S15 · DL 计算引擎与配置](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/dltool.py)；[S16 · DL 输入、编辑、绑定与结果页](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/dltool.py)；[S36 · DL 多解测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_dltool_multi.py)。

#### Scope
现有正反向计算与系数提取；不建立通用数学平台。

#### Implementation Notes
【建议】区分 None、0 和非法数值；拒绝 NaN/Inf、非正采样数及无效区间。结果全程携带 input_index 与原始输入，不根据过滤后的数组位置重标。提取系数返回成功/缺失/无效字段，不默默用 0 替代。即时收敛求解文案为“已检测到的候选根”，增加端点、重根、恒等与残差校验；实现可验证的有界多项式实根策略前不得承诺穷尽。优先评估现有依赖是否已有可信求根能力；没有则比较至多八阶导数分段算法与新增数值依赖的成本，不只增加采样数。多解选择不自动称最小绝对值为最优。

迁移：先修确定性数值与行映射缺陷，再完善求根覆盖；无法证明完整性时保留诚实的近似输出与原始参数。

#### Acceptance Criteria
未设置范围保存后仍为 None；传输系数 0 与分母 0 分别按规则处理。过滤第一个 y 后，其余行仍对应正确输入。覆盖 (x-a)^2 的非网格根、左端点根、常数/零多项式、近重根、区间外根、负缩放和溢出，输出残差及不确定状态。无效绑定不能改变已生效系数。原有三类计算及系数文件可读。

#### Dependencies
无，可独立启动。

#### Priority
P0 / Linear Urgent。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
先 None/0/行对应/系数提取的确定性 bug，再求根边界与结果文案；保留明确算法能力限制。

### T12 · 建立可复现离线发布包并修复启动配置

Linear：尚未核实同步结果。阶段：M1。对应问题：F12。

路线图 T12 / 问题 F12；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】requirements 只有 nicegui>=2.0.0、apscheduler>=3.10.0，无已验证版本锁；run.py 定义了 MCHECKER_PORT 读取却固定使用 50002，与 README 的 50001/环境变量行为矛盾。仓库包含 .nicegui 用户状态和 __pycache__。现有源码未证明必须使用公网 CDN，不能将文档“首次需要网络”的表述直接当作事实。

影响：同一源码安装出不同运行环境，离线部署无法保证复现；端口配置失效；发布可能携带开发会话。实际部署版本与全部资源请求仍需实测。

依据：[S01 · 启动入口](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/run.py)；[S29 · 依赖声明](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/requirements.txt)；[S39 · 现有架构说明，仅作对照](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/docs/architecture.md)；[S40 · 现有使用与部署说明，仅作对照](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/README.md)。

#### Scope
构建、安装、运行资产、端口、数据根与发布卫生；不新增强制容器、在线更新器或环境管理服务。

#### Implementation Notes
【建议】从当前已工作的环境导出并验证 Python/NiceGUI/APScheduler 和传递依赖精确版本、哈希、平台信息，生成对应 OS/架构的 wheelhouse 与离线安装脚本。修复端口读取；升级已有 50002 部署时显式保留其监听地址，不静默改端口。支持可选数据根但保留旧 data 默认，存储 secret 安全持久化失败须明确报错。打包本地字体/图标/JS/CSS 所需资产，首访禁公网验收。从版本控制/发行包移除开发会话与字节码，不删除部署机实际会话；检查已公开内容是否需要凭据轮换。

迁移：先记录现网版本/端口/路径并做部署副本验证，再锁定发行环境；不为了追新直接升依赖主版本。

#### Acceptance Criteria
全新目标机器不访问索引即可安装；空浏览器缓存且禁止公网时完成查看、搜索、比较、审阅、DL、下载与首屏字体/图标加载。允许的内网更新源照常工作，公网来源在离线模式明确禁用。MCHECKER_PORT 生效；旧部署升级不意外换端口或数据根。发布包不含用户 .nicegui、业务数据和 secret。

#### Dependencies
无，可独立启动。

#### Priority
P0 / Linear Urgent。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
先记录部署基线并修端口，再构建锁文件/wheelhouse/本地资产和冷缓存验收；不夹带依赖大升级。

### T13 · 隔离测试数据并建立关键回归门槛

Linear：尚未核实同步结果。阶段：M2。对应问题：F13。

路线图 T13 / 问题 F13；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】test_differ 的绑定测试只替换 BINDINGS_FILE，而当前存储实际使用 profile 文件路径，测试可能写到仓库默认 data。该测试以 len(bound_items)>=0 作为断言，无法发现绑定完全失效。已有测试覆盖若干纯函数和存储流程，但不能证明浏览器权限、审阅保真、离线首访和并发恢复正确。

影响：测试可污染本地数据；无效断言给出假安全感；重构和新增功能容易重复引入已经修过的 P0。

依据：[S30 · 对比测试与测试隔离](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_differ.py)；[S31 · 存储回归测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_storage.py)；[S32 · 机型回归测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_profiles.py)；[S33 · 文件下载测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_file_downloads.py)；[S34 · 解析测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_parser.py)；[S35 · 解析缓存测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_parse_cache.py)；[S36 · DL 多解测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_dltool_multi.py)。

#### Scope
测试隔离、有效断言与最小 CI；不设无依据的覆盖率百分比。

#### Implementation Notes
【建议】在统一 conftest 中按测试提供临时 DATA_DIR、profiles、缓存和会话目录，重置 ContextVar/单例；禁止测试写到 fixture 根外。修正绑定断言为明确路径/组/数量。每个 P0 修复随 PR 添加反例与正例，CI 运行纯函数、存储故障注入和接口测试，浏览器/离线套件可在发行候选阶段运行。测试工具是开发依赖，不进入运行包。

迁移：先隔离目录再跑原测试；不以删除失败用例来获得全绿。

#### Acceptance Criteria
测试前后真实 data/.nicegui 不变；用例可独立、随机顺序重复执行。绑定故意破坏时测试必失败。每个 P0 关联回归用例，失败会阻止发布；不存在吞异常后默认通过。

#### Dependencies
无，可独立启动。

#### Priority
P1 / Linear High。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
先测试目录隔离，再修无效断言和最小 CI；任何环境都不得先碰生产 data。

### T14 · 提取应用操作边界并统一错误与日志

Linear：尚未核实同步结果。阶段：M2。对应问题：F14。

路线图 T14 / 问题 F14；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】页面直接串联权限、解析、存储、结果通知；differ 直接导入 storage.get_bound_paths，storage 又调用 differ；storage 的机型选择反向依赖 NiceGUI。三个模块重复实现 JSON 读写，异常经常被 pass。

影响：新增入口必须复制保存和权限逻辑；错误无法定位；目录虽然分开，核心业务仍难脱离 UI 测试。

依据：[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S03 · 身份与权限判断](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/utils/auth.py)；[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S08 · 审阅提交与结果文件生成](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/review.py)；[S09 · 定时与手动更新](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/scheduler.py)；[S14 · 结构化与文本差异](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/differ.py)；[S15 · DL 计算引擎与配置](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/dltool.py)；[S17 · 更新设置、人员映射与管理员表单](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/management.py)。

#### Scope
形成模块边界，不为所有 getter 包 service，也不把 storage.py 拆文件数当目标。

#### Implementation Notes
【建议】仅提取已存在的复用操作：配置导入/更新/生命周期、审阅提交、任务执行。普通查询可直接调用明确 profile 的存储函数。保留 core.storage 兼容门面，底层原子 I/O 和路径边界归一；不要按每张 JSON 表生成 Repository。differ 接收绑定快照而非自行读存储。应用错误提供 code/message/retryable/context，UI 和 HTTP 各自映射。标准 logging 记录 operation_id、profile、actor、目标、结果、耗时和恢复信息，轮转并脱敏；无需求不拆多个日志平台。

迁移：先特征测试，再一次迁出一个业务用例并保留转发函数；每步可独立回滚，避免业务逻辑与目录重排混在同一个大提交。

#### Acceptance Criteria
核心导入、审阅、diff 可不加载 NiceGUI 测试。上传与定时更新复用同一验证/提交路径。核心模块不导入 pages；新入口不复制授权与文件提交。磁盘满、权限拒绝、版本冲突、解析失败在 UI 和日志有一致代码且不泄密。旧函数进口/参数包装有兼容测试。

#### Dependencies
T01, T04, T06, T07, T09, T13

#### Priority
P1 / Linear High。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
一次迁移一个真实业务操作，业务逻辑保持不变；错误/日志契约再统一，目录搬移单独 Review。

### T15 · 收敛工作区导航与配置管理表单

Linear：尚未核实同步结果。阶段：M3。对应问题：F15。

路线图 T15 / 问题 F15；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】home 集中工作区、收藏、机型、导入、历史加载；双侧栏默认展开，主内容有 max-width。管理页把更新源、调度、IP 和管理员堆在同页，小时/天选择未接入保存计算；tools/bindings 保存后通知成功但列表未即时刷新。角色标签仅反映 admin，部署者可显示为游客。

影响：核心数据区域被压缩，操作位置与权限不易理解；用户容易重复保存或误判调度周期。新增页面会继续堆积 home 的分支与状态逻辑。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S17 · 更新设置、人员映射与管理员表单](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/management.py)；[S25 · 绑定管理页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/bindings.py)；[S26 · 工具菜单页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/tools.py)；[S27 · 主题与 JavaScript 注入](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/theme.py)；[S28 · CSS 与信息密度](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/static/css/style.css)；[S37 · 标签策略](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/tab_manager.py)；[S38 · 标签持久化](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/tabs_state.py)。

#### Scope
信息架构、工作区和现有设置流程；界面尺寸是验收视口，不是对当前页面实测结论。

#### Implementation Notes
【建议】保留文件工作区和标签，左侧改紧凑可搜索文件列表，右工具区默认可收起；顶部主动作增加文字，当前机型、来源和权限明确。设置按更新源/调度、人员权限、机型、工具/绑定分区，不另建 Dashboard。统一页面标题、工具栏、带标签表单、行级错误与空状态，更新源/IP/管理员/历史优先紧凑表格。小时/天正确换算但磁盘仍存 interval_hours。保存成功局部刷新并保留筛选。提取轻量 workspace/tab 操作，不建立动态插件路由。

迁移：保留旧标签 key 与入口适配，按页面替换布局；先解决功能与密度，再做视觉修饰。

#### Acceptance Criteria
现有功能入口全部可到达，角色显示真实；新增/修改/删除后列表立即一致。小时/天设置往返一致；非法 IP/重复名称/空绑定有行级反馈。1366×768 和 1920×1080 代表窗口可完成核心任务，不依赖 hover 才发现唯一主操作；机型切换和标签恢复不误打开其他来源。

#### Dependencies
T01, T08, T09, T14

#### Priority
P1 / Linear High。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
先工作区/角色/单位与 CRUD 一致性，再设置分区和紧凑布局；旧快捷入口保留适配。

### T16 · 统一树视图并修复搜索与版本对比连续性

Linear：尚未核实同步结果。阶段：M3。对应问题：F16。

路线图 T16 / 问题 F16；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】viewer/search/收藏/归档/record_view 多套树渲染存在重复和差异；收藏树调用 window.mct，但主题只定义 mcSetTreeNode/mcToggleTree/mcTreeSetAll。filter_tree_and_count 在父节点匹配时提前返回且可能计为 0，页面据此显示未找到。history._compare_with_current 接收 archive_filename 却未存入比较标签，比较页默认另选第一项。CSS 对树禁用文本选择，部分长值被截断。

影响：相同数据在不同页面表现不同；有命中却无结果；点击某个版本后对比了另一个版本，破坏用户对结果的理解。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S05 · 文件查看、筛选与修改备注](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/viewer.py)；[S18 · 历史列表与比较入口](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/history.py)；[S19 · 比较页面与复制结果](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/comparison.py)；[S21 · 修改记录独立页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/record_view.py)；[S22 · 搜索过滤与计数](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/searching.py)；[S23 · 全局搜索页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/search.py)；[S27 · 主题与 JavaScript 注入](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/theme.py)；[S28 · CSS 与信息密度](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/static/css/style.css)。

#### Scope
同类树控件、搜索与比较，不新增全文搜索服务。

#### Implementation Notes
【建议】共用树行/节点控件与本地交互脚本，读写能力以少量明确选项控制，不建通用 schema-driven renderer。保留原 locator 与过滤上下文，搜索分别表达命中节点与命中变量，父节点命中不能丢结果。比较 FileRef 明确 old/new、指定版本和原始上传名称；历史入口传入并持久化完整比较选择，重用标签时也更新选择。增加路径/值复制、展开长值、键盘操作；clipboard 确认成功后再提示，HTTP 不支持时提供选中文本/下载替代。

迁移：先修版本参数与搜索计数，再从 viewer/search 两个已重复页面提取控件，其余页面逐步复用。

#### Acceptance Criteria
搜索父节点、文件名和备注都有预期结果；筛选前后定位相同。从第三个历史版本点击比较时，两侧来源准确且重新打开仍一致。所有树页面折叠/展开、焦点、长值和复制一致；HTTP 内网复制失败不显示假成功；原有四类对比都覆盖。

#### Dependencies
T03, T06, T08, T14, T15

#### Priority
P1 / Linear High。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
先搜索计数和历史选择传递，再合并两套树控件并逐页接入；不一次性换全部渲染。

### T17 · 保留审阅与计算草稿并明确任务结果

Linear：尚未核实同步结果。阶段：M3。对应问题：F17。

路线图 T17 / 问题 F17；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】审阅要求本页所有待审节点先做选择才能提交，选择状态在渲染局部闭包；DL 的 editing 写入共享配置，绑定“应用”会即时保存，因此“取消”不一定撤销已应用变更。导入提前关闭对话框，长任务提示与失败恢复不统一。

影响：切页丢审阅选择、多个用户共享编辑模式、取消语义不可信，长流程失败后必须重做。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S08 · 审阅提交与结果文件生成](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/review.py)；[S15 · DL 计算引擎与配置](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/dltool.py)；[S16 · DL 输入、编辑、绑定与结果页](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/dltool.py)。

#### Scope
现有审阅/计算表单与任务状态，不新增跨设备草稿同步和通知中心。

#### Implementation Notes
【建议】编辑草稿与生效配置分开；DL editing、输入控件和中间绑定放会话状态，保存时校验版本并一次提交，取消不写共享文件。审阅按文件/已选条目提交，未选择项维持 pending；切页保留当前会话草稿并标注源版本。复用已有 TaskStatus 展示排队/执行/成功/部分失败/冲突，避免长期悬挂 toast；失败保留输入，明确重试是否安全。结果页展示来源文件、版本、参数和时间，不自动覆盖当前配置。

迁移：兼容读取旧 editing 字段但不再当作全站状态；保留旧系数与未审备注，逐页迁移草稿。

#### Acceptance Criteria
切换标签后审阅选择和 DL 草稿可恢复；两会话进入编辑互不影响；取消后生效配置 hash 不变。部分审阅不影响未选项；源变化产生冲突提示。离页、断连、重连和重复提交均给出可解释状态，输入不被无提示丢弃。

#### Dependencies
T07, T09, T11, T14, T15

#### Priority
P1 / Linear High。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
先 DL 草稿/取消，再审阅选择保留与部分提交，最后统一任务结果显示。

### T18 · 按代表性负载优化渲染与查询成本

Linear：尚未核实同步结果。阶段：M4。对应问题：F18。

路线图 T18 / 问题 F18；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】文件树一次创建全部后代节点；全局搜索遍历文件；history/records 会在渲染期间解析或比较多个版本；home 有 0.3 秒标签检测和每秒文件/收藏检查。性能退化程度尚未在真实部署测量。

影响：随着节点、版本和客户端增加，重复解析、全量 DOM 和每客户端文件扫描可能形成首要性能瓶颈；不能用丢字段来换速度。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S05 · 文件查看、筛选与修改备注](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/viewer.py)；[S18 · 历史列表与比较入口](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/history.py)；[S20 · 修改记录列表](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/records.py)；[S23 · 全局搜索页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/search.py)；[S13 · 解析缓存](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/parse_cache.py)。

#### Scope
现有性能瓶颈与资源释放；不凭预测引入数据库、Redis 或前端框架。

#### Implementation Notes
【建议】先记录部署硬件、文件数/节点数、冷暖缓存、客户端数、交互延迟、事件循环延迟和峰值内存。优先 lazy expand、结果分页、按需 diff、复用同 hash 的解析结果；本地动作直接刷新，跨客户端用轻量版本签名定时检查，隐藏页面暂停检查。保留全量原文和搜索语义；只有测量证明仍不够时才考虑进程池或搜索索引。

迁移：每次只改变一个有测量证据的热点；无法证明收益的优化撤回。

#### Acceptance Criteria
同一数据/硬件/脚本下记录前后结果；全字段解析和旧功能结果一致。大文件展开/搜索可中断显示且不污染新页面；长会话切页/关页后任务、timer 和临时项有界释放。冷缓存可用，不能只报缓存命中成绩；不设无依据的性能提升百分比。

#### Dependencies
T09, T10, T16, T17

#### Priority
P1 / Linear High。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
每个性能提交附同环境前后证据；无收益或损害语义的变化撤回。

### T19 · 完成核心流程、离线与升级恢复验收

Linear：尚未核实同步结果。阶段：M4。对应问题：F19。

路线图 T19 / 问题 F19；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】现有 tests 的主要验证单位是函数与文件操作，尚没有足够证据覆盖完整多用户浏览器流程、首访断网、升级中断与恢复。此项是集成验收，不代替各 P0 的本地回归。

影响：独立修复可能在真实页面、目标 OS、原有数据和代理环境中组合失效；没有恢复演练就不能确认升级安全。

依据：[S30 · 对比测试与测试隔离](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_differ.py)；[S31 · 存储回归测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_storage.py)；[S32 · 机型回归测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_profiles.py)；[S33 · 文件下载测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_file_downloads.py)；[S34 · 解析测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_parser.py)；[S35 · 解析缓存测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_parse_cache.py)；[S36 · DL 多解测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_dltool_multi.py)；[S40 · 现有使用与部署说明，仅作对照](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/README.md)。

#### Scope
发布门禁与恢复演练，不建设复杂测试平台。

#### Implementation Notes
【建议】用脱敏旧布局/新布局/混合布局和代表文件建立发行验收包。浏览器覆盖访客、部署者、管理员、两机型、当前/临时/归档/记录，以及上传—查看—搜索—收藏—比较—建议—审阅—下载闭环。目标环境禁公网首次安装和访问；模拟停止、磁盘错误及多文件操作中断后恢复。记录失败项、环境与证据，完成后才更新支持矩阵和发布状态。

迁移：先在副本环境验收再计划部署；本路线图交付不代表已经改动或验收生产代码。

#### Acceptance Criteria
各 P0 验收通过，完整业务闭环通过；升级前后业务文件与引用核对一致；回滚恢复旧代码及对应备份可用，运行 secret/会话按策略保留。无运行时公网请求，数据留部署端。未知/失败项不能标通过；明确单进程与文件规模边界。

#### Dependencies
T05, T12, T13, T14, T15, T16, T17, T18

#### Priority
P1 / Linear High。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
先运行核心浏览器套件，再断网/恢复演练；失败回到所属缺陷任务，不用新建大量验收碎片。

### T20 · 收敛视觉样式并清理重复覆盖

Linear：尚未核实同步结果。阶段：M5。对应问题：F20。

路线图 T20 / 问题 F20；固定审查基线 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。

#### Problem
【代码确认】已有 CSS tokens、系统字体栈、focus-visible 与 reduced-motion，但全局 .q-card:hover 阴影、彩色收藏边框、多个树配色和 DL 内联样式并存。其存在不等于必须换 UI 框架。

影响：视觉噪声、非交互区域的错误可点击暗示和升级 CSS 覆盖维护成本；不阻塞当前正确使用。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S16 · DL 输入、编辑、绑定与结果页](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/dltool.py)；[S27 · 主题与 JavaScript 注入](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/theme.py)；[S28 · CSS 与信息密度](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/static/css/style.css)。

#### Scope
非必要视觉 polish，不新增 Dashboard、深浅双主题或整套 CSS 框架。

#### Implementation Notes
【建议】复用现有 tokens，收敛语义色、圆角、密度与焦点；移除无功能收益的 hover 阴影/缩放，样式限制在 mc 命名空间，减少 Quasar 全局覆盖。保留必要 loading 动画和可访问性。主题资产通过本地静态入口加载，不在每个页面重复发送整段 CSS。

迁移：在功能布局稳定后小范围归并样式，保留旧选择器过渡并逐项删除失效覆盖。

#### Acceptance Criteria
代表页面的字体、间距、状态色和焦点一致；非交互容器不诱导点击；长值、键盘与密度不退化；没有新增公网资源或运行依赖。视觉回归与功能回归通过，P2 不阻塞前一阶段可用版本发布。

#### Dependencies
T19

#### Priority
P2 / Linear Normal。

#### Definition of Done
实现已 Review，相关正反例和兼容回归通过，记录执行环境与证据；仅创建 Issue 不代表完成。

#### Review 边界
按 tokens、作用域、重复覆盖收敛；视觉调整单独提交且不与功能修复混合。

## 6. 统一 Definition of Done

每项任务只有在实现已 Review、正反例通过、与旧数据/接口的兼容条件满足、错误分支可解释且相关文档更新后才可 Done。不能仅以“新增了模块”“页面能打开”或“测试未报错”作为验收。

数据类任务必须证明未选定/未修改的原文没有变化，旧归档与业务引用可恢复，写失败不会显示成功。权限类任务必须直接调用提交入口验证，而非仅看按钮不可见。任务执行类必须验证离页、双客户端、重入和异常清理。数值类必须验证输入/输出对应、边界值和算法限制，而不是只看常规样本有结果。

UI 任务需要在实际浏览器完成核心操作、键盘与长文本验收；浏览器测试工具仅作为开发依赖。发行类任务必须清空缓存、禁止公网，测试安装和首访，不用已经联网预热过的浏览器作为证据。任何未验证环境应保留明确支持限制。

每个 PR 说明 Current → Target → Why → Migration。路径/数据布局/依赖变更必须有回滚说明；安全与数据修复尽可能与纯目录移动或样式变化分开提交。不要删除历史兼容分支，除非对应样本和约束已确认过时。

## 7. 核心验收场景矩阵

| 场景 | 必须看到的结果 | 关联任务 |
|---|---|---|
| 访客直接调用机型/DL 管理写操作 | 拒绝且生效配置不变 | T01 |
| 打开对话框后撤权再提交 | 提交时拒绝，非只隐藏入口 | T01 |
| default/其他机型、前台/后台交错 | 每次操作始终落固定机型 | T01/T09 |
| 路径越界、符号链接、Windows 路径变体 | 根外不可读写，安全中文名仍可用 | T02 |
| file URL、非法协议、重定向和超大下载 | 拒绝/受限，当前原文不被替换 | T02/T09 |
| 配置值含 HTML、JS 样式文本 | 仅作为文字显示，无脚本执行与额外出网 | T03 |
| JSON 半写/损坏、磁盘满、同时增加备注 | 原数据可恢复，无丢写/假成功 | T04 |
| 同秒多次更新和修改记录保存 | 所有版本独立，不覆盖 | T04 |
| 旧/新/混合布局迁移中断 | 原数据保留、明确恢复、重复执行幂等 | T05 |
| 文件改名/删除/恢复 | 所有既有引用一致或明确失效，禁止静默错指向 | T05 |
| XML 阈值前后、重复兄弟、命名空间 | 全字段保留、节点不合并 | T06 |
| 搜索/隐藏参数元数据后提交建议 | 仍定位原文的同一节点 | T06/T07 |
| 源版本变化、重复审阅、多文件部分失败 | 冲突/幂等/逐文件结果明确，状态不虚报 | T07 |
| 访客临时上传与服务器同名 | 展示临时内容，服务器不变 | T08 |
| 临时 token 跨会话、过期 | 非所有者拒绝，过期不回退同名文件 | T08 |
| 冷缓存打开归档与跨机型记录链接 | 具体来源正确，可查看/下载 | T08 |
| 下载到 HTTP 200 错误页 | 验证失败，不覆盖有效配置 | T09 |
| 慢操作时另一浏览器继续工作 | 无事件循环长阻塞和全局 busy 污染 | T09 |
| parser 版本变化或解析期间源替换 | 缓存失效或重试，不返回混合版本 | T10 |
| None/0/NaN/Inf/过滤后的 y 行 | 正确区分，行对应不变，无静默默认 | T11 |
| 切触重根/左端点/恒等多项式 | 结果与限制诚实，不把未检测当证明无解 | T11 |
| 清洁机器、浏览器冷缓存、禁公网 | 安装与核心流程完整，无 CDN/字体/API 依赖 | T12/T19 |
| 在含真实 data 的 checkout 运行测试 | 测试写入被限制到临时根 | T13 |
| 新增相似入口调用现有业务操作 | 不复制授权/路径/保存，实现仍可纯逻辑测试 | T14 |
| CRUD 保存与调度天/小时 | 页面即时一致，持久化时间语义正确 | T15 |
| 从第三个历史版本进入比较 | 两侧来源与用户选择完全一致 | T16 |
| 切页/取消/双用户编辑 | 草稿独立，取消不改生效数据 | T17 |
| 大文件与长会话 | 完整语义不退化，资源释放有界，冷热均测 | T18 |
| 升级失败后恢复旧版本 | 旧代码/对应备份可运行，新业务数据不被无提示删除 | T19 |

## 8. 发布、升级和回滚安排

【建议】第一批可发布的是能独立回归的 P0 补丁，不必等视觉阶段。涉及源模型、恢复或原子提交的组合改动必须通过相应依赖验收后发布；不得只上线新页面，仍让后台走旧不安全写入。

每次发行先记录实际 Python/依赖版本、监听地址、数据根、secret 来源、机型数和布局状态；创建一致备份并校验。候选代码在副本上运行，验证旧数据、临时数据和归档。停接新写或进入明确只读窗口后切换；启动发现未完成恢复清单时，不继续随机修改受影响对象。

失败回滚恢复旧代码与匹配备份；升级后产生的数据单独保全，不能直接用旧备份覆盖。任何破坏性迁移需要用户明确的部署操作，不由本次路线图交付自动执行。当前交付仅写规划到 Linear，不修改 GitHub 业务源码和生产数据。

支持矩阵至少记录实际验证的 OS/架构、Python、NiceGUI/APScheduler、浏览器、直连/代理、HTTP/内部 HTTPS、数据布局和规模。没有验证的版本不写“完全支持”。单进程/单调度器是本阶段明确边界；多 worker 不属于默认部署方式。

## 9. Deferred / Future Consideration（P3，不建 Issue）

| 能力 | 暂缓理由 | 触发条件 |
|---|---|---|
| 独立 React/Vue 前端 | 重写成本高且不直接修已知问题 | NiceGUI 的已测限制阻断明确需求 |
| SQLite/数据库或全文索引 | 未证明规模/事务成本需要 | 真实并发/查询瓶颈，或文件恢复代码复杂度持续高于迁移成本 |
| SSO、账号中心、部门/租户隔离 | 未确认产品与身份要求 | 不可信访问、共享终端或明确组织管理需求 |
| 插件系统、微服务、工作流引擎 | 无多个真实独立扩展需求 | 已存在扩展方/编排需求及独立部署收益 |
| 代码热替换/零停机部署 | 当前没有可用性指标要求 | 维护窗口不能接受且有资源承担实现/验证成本 |
| Dashboard、大屏、通知中心 | 与文件工作流无直接必要性 | 实际持续使用场景经确认 |
| 自动清理历史/统一保留规则 | 可能损害数据与恢复 | 存储压力与备份/保留政策同时确认 |
| 跨设备草稿、通用资源平台 | 现有会话和 FileRef 足够 | 多设备协作需求已验证且无法局部解决 |
| 全量 REST 化 | 暂无第二消费者 | 已确认集成方/CLI 需要稳定 API |

Deferred 不设置日期和任务占位，不用低优先级大量 Issue 伪造路线图。触发条件出现时重新验证，不直接照旧设想实施。

## 10. 未知项及最小验证步骤

| 未知项 | 最小验证 | 如何改变决策 |
|---|---|---|
| 实际部署版本与平台 | 读取部署端版本、启动参数和依赖清单 | 确定锁文件/打包矩阵，不预先追新 |
| 代理/共享 IP 情况 | 一次直连与代理身份测试，检查来源映射 | 决定 IP 模式是否仍可接受，是否必须强认证 |
| XML 特性与保真边界 | 取脱敏典型和边界原文，零修改/单修改往返比较 | 标准库足够则保留；不够才评估成熟库 |
| 旧目录与迁移中间态 | 只读列清单、hash 和引用，不移动数据 | 选择兼容适配/恢复流程 |
| 性能上限 | 固定冷暖缓存、文件/节点/客户端样本测量 | 决定 lazy/paging 是否足够，是否需要额外计算能力 |
| 求根完整性要求 | 用已知多项式反例与真实系数范围验证 | 决定候选根模式是否足够，是否引入更可靠数值实现 |
| 真正公网依赖 | 冷缓存浏览器 + 服务器禁公网请求记录 | 确定需随包的资产，不凭 README 替换框架 |

这些验证嵌入 T01/T06/T11/T12/T18/T19，不单独制造调研 Issue。获得足以改变决策的证据后结束调查，回到交付。

## 11. Linear 落地记录

Project：`c76b3c74-f0a2-4ac1-a6ab-42c07b5a0e71`；Team：Inhandy（INH）。Project → 5 Milestones → 20 Issues。

同步状态尚未最终核对；不把未返回 identifier 的创建请求视为成功。

| 任务 | 实际 Issue | Milestone | 建立的 blocker |
|---|---|---|---|
| T01 | 尚未核实同步结果 | M1 | 无 |
| T02 | 尚未核实同步结果 | M1 | 无 |
| T03 | 尚未核实同步结果 | M1 | 无 |
| T04 | 尚未核实同步结果 | M1 | 无 |
| T05 | 尚未核实同步结果 | M1 | 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果 |
| T06 | 尚未核实同步结果 | M1 | 无 |
| T07 | 尚未核实同步结果 | M1 | 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果 |
| T08 | 尚未核实同步结果 | M1 | 尚未核实同步结果, 尚未核实同步结果 |
| T09 | 尚未核实同步结果 | M1 | 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果 |
| T10 | 尚未核实同步结果 | M1 | 尚未核实同步结果, 尚未核实同步结果 |
| T11 | 尚未核实同步结果 | M1 | 无 |
| T12 | 尚未核实同步结果 | M1 | 无 |
| T13 | 尚未核实同步结果 | M2 | 无 |
| T14 | 尚未核实同步结果 | M2 | 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果 |
| T15 | 尚未核实同步结果 | M3 | 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果 |
| T16 | 尚未核实同步结果 | M3 | 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果 |
| T17 | 尚未核实同步结果 | M3 | 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果 |
| T18 | 尚未核实同步结果 | M4 | 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果 |
| T19 | 尚未核实同步结果 | M4 | 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果, 尚未核实同步结果 |
| T20 | 尚未核实同步结果 | M5 | 尚未核实同步结果 |

关系验收：尚未回读完成；最终交付前核对。

## 12. 最终交付成功条件

架构书与路线图完整可读，代码事实、推断和建议有明确区分；Linear 项目中任务位于正确 Milestone，每个任务包含问题、范围、实现注意、验收、依赖和优先级，并以真实 blocker 关系表达顺序。

实际开发结束后，必须同时证明：当前导入、解析、比较、审阅、计算和数据操作更可靠；新增同类功能主要通过局部页面/纯逻辑扩展，复用已有身份、机型、文件、节点与提交边界。仅完成目录重排或视觉翻新不满足这两个条件。

## 附录：固定源码证据

- S01：[启动入口 · `run.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/run.py)。
- S02：[存储、机型上下文与文件生命周期 · `app/core/storage.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)。
- S03：[身份与权限判断 · `app/utils/auth.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/utils/auth.py)。
- S04：[首页、工作区、上传与机型管理 · `app/pages/home.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)。
- S05：[文件查看、筛选与修改备注 · `app/pages/viewer.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/viewer.py)。
- S06：[XML/JSON 解析与节点路径 · `app/core/parser.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/parser.py)。
- S07：[审阅定位、修改与序列化 · `app/core/reviewing.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/reviewing.py)。
- S08：[审阅提交与结果文件生成 · `app/pages/review.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/review.py)。
- S09：[定时与手动更新 · `app/core/scheduler.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/scheduler.py)。
- S10：[URL 下载 · `app/core/downloader.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/downloader.py)。
- S11：[当前文件与归档下载路由 · `app/pages/file_downloads.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/file_downloads.py)。
- S12：[机型注册、复制与删除 · `app/core/device_models.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/device_models.py)。
- S13：[解析缓存 · `app/core/parse_cache.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/parse_cache.py)。
- S14：[结构化与文本差异 · `app/core/differ.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/differ.py)。
- S15：[DL 计算引擎与配置 · `app/core/dltool.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/dltool.py)。
- S16：[DL 输入、编辑、绑定与结果页 · `app/pages/dltool.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/dltool.py)。
- S17：[更新设置、人员映射与管理员表单 · `app/pages/management.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/management.py)。
- S18：[历史列表与比较入口 · `app/pages/history.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/history.py)。
- S19：[比较页面与复制结果 · `app/pages/comparison.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/comparison.py)。
- S20：[修改记录列表 · `app/pages/records.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/records.py)。
- S21：[修改记录独立页面 · `app/pages/record_view.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/record_view.py)。
- S22：[搜索过滤与计数 · `app/core/searching.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/searching.py)。
- S23：[全局搜索页面 · `app/pages/search.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/search.py)。
- S24：[收藏实时值解析 · `app/core/favorites_live.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/favorites_live.py)。
- S25：[绑定管理页面 · `app/pages/bindings.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/bindings.py)。
- S26：[工具菜单页面 · `app/pages/tools.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/tools.py)。
- S27：[主题与 JavaScript 注入 · `app/pages/theme.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/theme.py)。
- S28：[CSS 与信息密度 · `app/static/css/style.css`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/static/css/style.css)。
- S29：[依赖声明 · `requirements.txt`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/requirements.txt)。
- S30：[对比测试与测试隔离 · `tests/test_differ.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_differ.py)。
- S31：[存储回归测试 · `tests/test_storage.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_storage.py)。
- S32：[机型回归测试 · `tests/test_profiles.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_profiles.py)。
- S33：[文件下载测试 · `tests/test_file_downloads.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_file_downloads.py)。
- S34：[解析测试 · `tests/test_parser.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_parser.py)。
- S35：[解析缓存测试 · `tests/test_parse_cache.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_parse_cache.py)。
- S36：[DL 多解测试 · `tests/test_dltool_multi.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_dltool_multi.py)。
- S37：[标签策略 · `app/core/tab_manager.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/tab_manager.py)。
- S38：[标签持久化 · `app/core/tabs_state.py`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/tabs_state.py)。
- S39：[现有架构说明，仅作对照 · `docs/architecture.md`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/docs/architecture.md)。
- S40：[现有使用与部署说明，仅作对照 · `README.md`](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/README.md)。
