# MCChecker 架构设计书

文档日期：2026-09-11。文档状态：正式整改设计，尚非实现完成证明。审查基线：`v1.0.1` 分支提交 `e47e54a01f0ad6d74db3a08670a1034f937575c2`。执行配套：[MCChecker_ROADMAP.md](MCChecker_ROADMAP.md)。项目：[MCChecker › Overview](https://linear.app/inhandy/project/mcchecker-9275450a22c3/overview)。

## 1. 总体判断与审查边界

【建议】保留 Python + NiceGUI + APScheduler + 本地文件/JSON 的模块化单体，不做前后端重写，不立即换数据库。整改重点是把已经存在的配置文件生命周期、审阅写回、机型上下文和长任务执行变成可验证的边界。界面围绕“找到文件—理解配置—比较变化—提出修改—审阅生成—取走结果”展开，不改造成运营 Dashboard。

【代码确认】MCChecker 已有 XML/JSON 解析、收藏速览、局部/全局搜索、四类版本对比、配置更新、归档、修改记录、建议审阅、多机型和 DL 计算，业务范围并不小。当前问题不只是代码较长：已经存在可定位的权限漏检、非原子保存、有损解析参与写回、临时文件丢失引用和数值处理错误。仅调整 CSS 无法使这些流程可靠。依据：[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S06 · XML/JSON 解析与节点路径](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/parser.py)；[S08 · 审阅提交与结果文件生成](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/review.py)；[S09 · 定时与手动更新](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/scheduler.py)；[S16 · DL 输入、编辑、绑定与结果页](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/dltool.py)。

【合理推断】下一阶段继续增加同类功能时，首先失控的不是数据库容量或前端框架，而是“一次操作究竟属于哪个机型、哪份文件、哪个版本、哪个用户”的隐式上下文；其次是从页面直接拼出的写入流程，以及越来越多的树视图和页面状态副本。应先把这三处边界收紧，再按证据优化性能。

本设计依据前面已经取得的固定提交源码，对核心运行模块、页面、CSS、主要测试和部署说明进行静态审查。没有访问部署服务器、业务原始数据或现网浏览器，也没有完成整仓运行测试、压力测试和冷缓存断网安装验证。本次尝试在隔离执行环境取得仓库副本时，GitHub DNS 解析失败，且环境未安装 NiceGUI/APScheduler，因此不报告未经执行的 pytest 通过率或浏览器成绩。文中的验收标准是开发完成后必须执行的条件，不是本次已经通过的测试。

【代码确认】本次可审查的核心运行范围包括 `run.py`、`app/core` 的 parser/storage/differ/downloader/scheduler/parse_cache/device_models/reviewing/searching/favorites_live/dltool/tab_manager/tabs_state，及 `app/pages` 的 home/viewer/management/comparison/history/records/record_view/review/search/bindings/tools/dltool/theme/file_downloads、`app/utils` 和本地 CSS。测试结论主要来自解析、存储、机型、缓存、下载、对比和 DL 用例。已跟踪的 `.pyc` 是派生物，不作为当前源码行为依据；未把 README、旧设计文档或旧聊天记录视为代码事实。

### 1.1 三项需要明确的技术判断

【代码确认】profile 使用 `ContextVar`，不能称为“一个普通全局 active_profile 被所有用户覆盖”。真正缺陷是显式 default 与隐式浏览器回退混用，以及业务边界未固定上下文。Python 的 `asyncio.to_thread` 会传播当前 context；不应在没有证据时归咎于线程丢失上下文。依据：[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[Python ContextVar](https://docs.python.org/3/library/contextvars.html)、[asyncio.to_thread](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread)。

【代码确认】项目文档写过 NiceGUI 首次需要网络，但查到的上游模板包含同源 `/_nicegui/.../static/` 资源路径，不能据此断言该项目运行必须依赖公网 CDN。真正的发布阻塞是依赖未锁定、实际运行版本未知、没有冷缓存禁公网证据。上游 main 只能说明可用机制，不代表现网安装版本。依据：[S29 · 依赖声明](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/requirements.txt)；[S39 · 现有架构说明，仅作对照](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/docs/architecture.md)；[NiceGUI 上游模板](https://github.com/zauberzeug/nicegui/blob/main/nicegui/templates/index.html)。

【建议】`父路径 + 标签 + occurrence` 只能在某个确定的源版本内定位，不能自动成为跨版本稳定身份。节点重排、重复标签、数组插入或筛选都要求“源版本校验 + 原文定位 + 原值/类型校验”；缺一不可。审阅输出不能继续从可能丢字段的展示树生成。

## 2. 当前架构与实际工作流

### 2.1 当前结构

```text
浏览器
  ├─ NiceGUI 页面 / WebSocket 事件 / Python 回调
  │    ├─ home：工作区、标签、机型、导入、收藏
  │    ├─ viewer/search/history/comparison/records：查看与比较
  │    ├─ review：审阅并生成文件
  │    └─ management/bindings/tools/dltool：配置与计算
  └─ HTTP：/api/file-download、/record_view、/static
         ↓
app.core：解析、差异、收藏解析、存储、缓存、调度、计算
         ↓
部署端 data/profiles/<profile_id>/ 与 .nicegui 用户状态
```

【代码确认】`run.py` 在导入阶段注册静态资源和路由、执行旧布局迁移、初始化调度，最后运行 NiceGUI；调度初始化异常被记录后应用仍可能启动。持久化业务数据在代码目录下的 `data`，NiceGUI 用户状态与业务 JSON 不是同一种存储。没有独立前端构建应用，没有独立数据库，也没有完整对外 REST 业务 API。依据：[S01 · 启动入口](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/run.py)；[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S11 · 当前文件与归档下载路由](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/file_downloads.py)；[S21 · 修改记录独立页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/record_view.py)；[S38 · 标签持久化](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/tabs_state.py)。

| 当前模块 | 实际职责 | 主要边界问题 | 应保留的基础 |
|---|---|---|---|
| parser / reviewing / differ | 展示树、值比较、从树生成文件 | 展示模型混入写回模型，定位与类型不足 | XML/JSON 统一查看结构、标准库文本差异 |
| storage / device_models / dltool | JSON、文件、机型、配置、引用变更 | 多处直接写、读改写无共同保护、默认上下文回退 | 本地目录、JSON 格式、现有兼容读取 |
| scheduler / downloader | 自动更新、记录拉取、重试 | 校验/提交不统一，来源与大小无统一策略 | 单调度器、现有周期配置 |
| home / tab_manager / tabs_state | 标签、路由分派、收藏、页面刷新 | home 过多业务职责，跨页状态重复 | 已有 15 标签上限、相邻关闭、历史清理 |
| viewer / search / history / records | 树、结果、版本导航 | 重复渲染、同步重活、来源传递缺失 | 查看、历史、四类对比与文本 diff |
| theme / style.css | 本地 CSS、主题、树交互 | 注入方式/全局覆盖分散 | tokens、系统字体、键盘焦点、减少动画规则 |

表中模块判断见各对应源码链接：[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S06 · XML/JSON 解析与节点路径](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/parser.py)；[S07 · 审阅定位、修改与序列化](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/reviewing.py)；[S09 · 定时与手动更新](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/scheduler.py)；[S14 · 结构化与文本差异](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/differ.py)；[S27 · 主题与 JavaScript 注入](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/theme.py)；[S28 · CSS 与信息密度](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/static/css/style.css)；[S37 · 标签策略](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/tab_manager.py)；[S38 · 标签持久化](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/tabs_state.py)。

### 2.2 核心工作流

【代码确认】导入流程：上传或 URL 下载 → `parser.parse_file` 校验 → 部署者调用 `storage.save_config_file` → 以文件名打开标签 → viewer 从持久化文件/缓存读取。访客分支只通知“仅会话可见”，却没有把已解析数据交给 viewer，因此该分支不是一个完整的临时查看实现。依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S05 · 文件查看、筛选与修改备注](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/viewer.py)。

【代码确认】更新流程：APScheduler 或管理页 → `run_single_update` → urllib 下载 → 直接保存新内容并归档旧版 → 尝试计算 diff/失效解析缓存。管理页与调度执行位置不同，缺少共同的输入验证、防重入和最终提交契约。依据：[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S09 · 定时与手动更新](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/scheduler.py)；[S10 · URL 下载](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/downloader.py)；[S17 · 更新设置、人员映射与管理员表单](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/management.py)。

【代码确认】阅读与关联流程：viewer 为每个节点生成控件 → 收藏原 path/子树 → overview 重新解析当前文件并用 id 字典定位 → 搜索过滤树并显示结果 → 绑定配置按文件/路径在 diff 标注。节点 ID 重复或路径表示不一致会同时影响收藏、绑定和差异，不能把它当成单一页面小问题。依据：[S05 · 文件查看、筛选与修改备注](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/viewer.py)；[S06 · XML/JSON 解析与节点路径](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/parser.py)；[S14 · 结构化与文本差异](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/differ.py)；[S22 · 搜索过滤与计数](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/searching.py)；[S24 · 收藏实时值解析](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/favorites_live.py)；[S25 · 绑定管理页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/bindings.py)。

【代码确认】审阅流程：用户提交 node_key、原值和建议值 → `edit_remarks.json` → 管理员对待审节点选择一项或驳回 → 重新加载当前树 → 改节点值 → 序列化成带时间戳新文件 → 更新备注状态。生成的是新文件，并非原地覆盖原配置；这一原文件保护语义应继续保留，但不足以抵消生成文件本身错误的风险。依据：[S07 · 审阅定位、修改与序列化](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/reviewing.py)；[S08 · 审阅提交与结果文件生成](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/review.py)；[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)。

【代码确认】历史与记录流程：历史列表读取归档并补算差异，提供查看和比较；修改记录从单独 URL 下载，记录页再打开 `/record_view`。这两类数据不应混为一类“版本”。当前记录链接缺少机型，历史比较入口未传完选定版本。依据：[S18 · 历史列表与比较入口](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/history.py)；[S19 · 比较页面与复制结果](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/comparison.py)；[S20 · 修改记录列表](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/records.py)；[S21 · 修改记录独立页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/record_view.py)。

【代码确认】DL 流程：读取三项计算配置 → 手动输入/绑定提取系数 → 正向多项式计算或采样反求 → 展示结果/多解 → 编辑配置保存。范围、缩放与原始输入之间存在确定性错误，求解算法还有能力边界。应先修正确性，再整理结果弹窗。依据：[S15 · DL 计算引擎与配置](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/dltool.py)；[S16 · DL 输入、编辑、绑定与结果页](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/dltool.py)。

## 3. 问题优先级总表

P0：正确性、数据安全、稳定性、核心功能、兼容性或离线发布阻塞。P1：明显影响核心体验、维护成本、长期可靠性或近期功能扩展。P2：有收益但不阻塞可用版本。P3：需求未证实或当前收益不足，不进入执行 Issue。P0 不等同于已发生生产事故；“已确认缺陷”和“尚未完成的发布验证”在问题描述中分开。

| 问题 | 优先级 | 最小交付任务 | 阶段 |
|---|---|---|---|
| F01 | P0 | T01 · 统一写操作授权并显式传递机型上下文 | M1 |
| F02 | P0 | T02 · 收紧文件路径与下载源输入边界 | M1 |
| F03 | P0 | T03 · 消除配置内容与工具链接的脚本注入路径 | M1 |
| F04 | P0 | T04 · 实现原子持久化与并发安全的版本保存 | M1 |
| F05 | P0 | T05 · 补齐迁移、重命名与删除的恢复链路 | M1 |
| F06 | P0 | T06 · 分离完整源文档与展示树并修复节点定位 | M1 |
| F07 | P0 | T07 · 重建审阅提交的版本校验与保真输出 | M1 |
| F08 | P0 | T08 · 修复临时上传与历史记录的文件引用链路 | M1 |
| F09 | P0 | T09 · 统一更新入口的校验与受控异步执行 | M1 |
| F10 | P0 | T10 · 保证解析缓存与源版本的一致性 | M1 |
| F11 | P0 | T11 · 修复 DL 数值输入、结果对应与求解语义 | M1 |
| F12 | P0 | T12 · 建立可复现离线发布包并修复启动配置 | M1 |
| F13 | P1 | T13 · 隔离测试数据并建立关键回归门槛 | M2 |
| F14 | P1 | T14 · 提取应用操作边界并统一错误与日志 | M2 |
| F15 | P1 | T15 · 收敛工作区导航与配置管理表单 | M3 |
| F16 | P1 | T16 · 统一树视图并修复搜索与版本对比连续性 | M3 |
| F17 | P1 | T17 · 保留审阅与计算草稿并明确任务结果 | M3 |
| F18 | P1 | T18 · 按代表性负载优化渲染与查询成本 | M4 |
| F19 | P1 | T19 · 完成核心流程、离线与升级恢复验收 | M4 |
| F20 | P2 | T20 · 收敛视觉样式并清理重复覆盖 | M5 |

P3 仅列入第 16 节 Deferred，不创建占用开发队列的 Issue。优先级反映业务影响，不以修复行数判断。

## 4. 重要问题的依据、影响与最小整改

### F01 · 统一写操作授权并显式传递机型上下文（P0）

【代码确认】home.py 的机型新增、更名、删除入口及对应回调未形成完整授权检查；DL 页面可直接保存共享系数与绑定。部分危险操作只在打开对话框时校验权限。storage 使用 ContextVar，而不是普通共享全局变量，但 get_active_profile() 在上下文值为 default 时仍回读 app.storage.user 的 device_model，因此显式 use_profile("default") 可能被浏览器选择覆盖。无效 profile 字符又被静默归一为 default。

依据：[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S03 · 身份与权限判断](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/utils/auth.py)；[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S12 · 机型注册、复制与删除](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/device_models.py)；[S16 · DL 输入、编辑、绑定与结果页](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/dltool.py)。

影响与不整改成本：未授权修改、机型数据误操作以及同一操作在不同入口下使用不同身份/机型。后续增加入口会继续复制这些缺陷。代理把客户端识别为本机的风险依赖实际拓扑，尚未现场验证。

推荐的最小整改：【建议】先原地封堵机型与 DL 管理写入口，在提交时重新校验。定义轻量 Actor 与动作权限表；deployer 与 admin 保持独立能力，不强行改成互斥角色。页面解析身份和机型，向业务函数传入 profile_id；任务在创建时固定机型。显式 default 优先于浏览器状态，无效/不存在机型返回明确错误。保留旧入口作为兼容包装，但包装在 UI 边界解析上下文，不能让核心存储回读 NiceGUI。直连默认不信任转发头；反向代理须配置受信代理及来源规则。

验收标准：访客调用机型增删改、DL 配置保存、更新源写入均被拒绝，持久化文件哈希不变。管理员撤权后，已经打开的提交按钮也被拒绝。default 与非 default 两个会话及后台任务交错执行不串数据。未知机型不能读写默认目录。旧 IP 对应表、管理员表与既有协作收藏/备注仍可使用；直连与实际代理拓扑分别验收。

边界与迁移：管理写操作、身份边界与现有机型隔离；不建设账号中心、SSO 或通用 RBAC。 先补提交守卫与 default 判定，再逐个将业务入口改为显式参数；保留旧文件结构与读取方式。 执行任务：T01。

### F02 · 收紧文件路径与下载源输入边界（P0）

【代码确认】get_config_path()/get_archive_dir() 直接拼接名称；save_config_file() 没有统一文件名校验；新增映射也不走更新映射使用的校验。downloader 使用 urllib.request.urlopen 和无上限 resp.read()，未限制 scheme、目标、重定向与大小。现有下载路由有 basename 检查，但没有统一的真实路径包含性与符号链接策略。

依据：[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S10 · URL 下载](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/downloader.py)；[S11 · 当前文件与归档下载路由](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/file_downloads.py)；[S17 · 更新设置、人员映射与管理员表单](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/management.py)。

影响与不整改成本：输入可越出预期存储边界；任意 URL 让服务端成为不受控的读取代理；超大内容可耗尽资源。内网下载是业务能力，不能简单封禁全部私有地址。

推荐的最小整改：【建议】统一 validate_filename 与 resolve_within：拒绝绝对路径、路径分隔符、空字节、驱动器/UNC/ADS 和越界解析，不靠替换字符偷偷改名。保留安全的既有 Unicode 文件名；对历史危险名称只提示并隔离操作，不自动删除。下载仅允许 http/https，目标由部署者配置明确主机/端口或所需网段，默认不允许 loopback、链路本地、元数据端点及未批准目标；每次重定向重新检查，DNS 解析结果和最终连接目标都应受策略约束。使用分块读取、总字节与耗时上限，解析增加深度/节点预算。

验收标准：覆盖 ../、反斜杠、绝对路径、Windows 盘符与 UNC、符号链接越界、编码路径和安全中文名。file:/ftp:/data: 等协议被拒绝；重定向不能绕过允许列表；批准的内网 HTTP/HTTPS 仍工作。超大、超时和异常来源不覆盖当前文件，不在日志泄漏 URL 凭据。

边界与迁移：所有导入、更新、下载、归档与记录路径，及服务端 URL 读取。 先统一校验并盘点历史名称/来源；在启用收紧策略前给出兼容检查结果，对不安全旧项显式阻止执行，保留原数据。 执行任务：T02。

### F03 · 消除配置内容与工具链接的脚本注入路径（P0）

【代码确认】viewer、search 和收藏树把 label/value/描述等直接拼入 ui.html(..., sanitize=False)。home 的 data-fav-path、DOM 查询以及 window.open 直接插入数据或工具 URL。records 中部分差异输出已经转义，说明现有处理不一致，而不是完全没有防护。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S05 · 文件查看、筛选与修改备注](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/viewer.py)；[S23 · 全局搜索页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/search.py)；[S20 · 修改记录列表](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/records.py)；[S26 · 工具菜单页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/tools.py)。

影响与不整改成本：导入文件或共享收藏可以把数据变为浏览器可执行内容，影响其他查看者与有管理权限的用户。

推荐的最小整改：【建议】普通文本优先使用 ui.label 等文本 API；必须拼 HTML 时逐个转义文本与属性。固定模板才可 sanitize=False。JavaScript 参数统一 JSON 编码，URL 使用原生导航/链接组件并校验 scheme，外链使用 noopener/noreferrer。收藏移除使用受控元素引用或编码后的稳定键，不再拼选择器字符串。保留已转义的差异展示。

验收标准：含引号、尖括号、HTML 标签、事件属性和脚本样式文本的 XML/JSON/备注均作为文字显示，不执行脚本、不额外出网。工具链接不接受 javascript:；正常特殊字符 URL 可打开。收藏、搜索与历史页面均覆盖，不能只修 viewer。

边界与迁移：所有动态 HTML、JS 字符串、DOM 定位和外链；不靠强行关闭所有 HTML 破坏现有树控件。 先逐个修危险插值，再归并安全渲染助手；不要等共用树重构完成才修注入。 执行任务：T03。

### F04 · 实现原子持久化与并发安全的版本保存（P0）

【代码确认】storage._save_json、device_models 和 DL 配置直接覆盖 JSON；_load_json 在文件损坏时返回空默认值。save_config_file 先移动当前版本再写新文件，归档重名回退仅精确到秒；记录文件也只用秒时间戳。多个读改写没有共同互斥边界。

依据：[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S12 · 机型注册、复制与删除](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/device_models.py)；[S15 · DL 计算引擎与配置](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/dltool.py)。

影响与不整改成本：中断可留下截断 JSON/缺失当前文件；并发会丢修改；同秒保存可能覆盖旧归档或记录。损坏后读成空集合再保存会把可恢复数据覆盖。

推荐的最小整改：【建议】用同目录唯一临时文件完成写入、flush/fsync、原子替换；按机型的进程内 RLock 覆盖完整读—校验—修改—提交，机型注册表另用全局锁，锁内不做网络请求或大 diff。先可靠保留旧版本，再原子替换当前文件，不提前搬走唯一有效副本。归档/记录采用独占创建和唯一后缀，旧命名仍可读取。区分文件不存在与损坏，损坏的权威 JSON 进入只读保护并提示恢复。写入失败必须上抛；缓存/diff 失败可以降级但须日志。限定单服务进程、单调度器写同一 data 根。

验收标准：注入写入中断、磁盘满、无权限、归档失败、os.replace 失败，旧文件仍可读且不得虚报成功。同秒连续更新和并发写不会覆盖归档；两个用户同时增加不同备注均保留。损坏 JSON 不被自动清空。恢复后原始文件与旧版哈希一致；平台特定持久性限制有记录。

边界与迁移：权威 JSON、配置、归档、修改记录与生成文件；不引入数据库，也不声称单文件 replace 提供多文件事务。 保持所有旧格式与命名读取；替换底层写入原语后再逐步收拢各模块的独立写入。 执行任务：T04。

### F05 · 补齐迁移、重命名与删除的恢复链路（P0）

【代码确认】启动调用 ensure_profile_layout(migrate_legacy=True)，逐项 shutil.move，异常只告警。重命名先保存映射，再移动文件，更新收藏/绑定但遗漏 records、审阅引用和 DL 来源等。copy_profile_data 仅复制部分类型，还复制可丢弃缓存；删除使用 ignore_errors=True，可能部分失败却显示成功。

依据：[S01 · 启动入口](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/run.py)；[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S12 · 机型注册、复制与删除](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/device_models.py)；[S17 · 更新设置、人员映射与管理员表单](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/management.py)。

影响与不整改成本：升级可能留下半迁移布局；重命名后记录、备注、计算系数来源失联；删除和复制结果与界面承诺不一致，难以恢复。

推荐的最小整改：【建议】为迁移和重命名建立具体操作的 preflight、备份清单、步骤状态、校验和恢复流程，不搭通用事务引擎。旧数据迁移改为备份—复制—校验—启用，原目录保留到人工确认。重命名先检查目标冲突，收集所有已存在的引用，完成数据与引用写入后再切换映射；中断启动时明确恢复/阻止继续写。删除先移入有清单的隔离目录，默认不物理清除，不自动过期删除。机型复制明确区分业务配置和操作历史；默认不复制权限身份、待审事项和缓存。

验收标准：旧布局、纯新布局、混合布局、目标冲突、任意步骤失败均有确定结果。重复迁移幂等，失败不丢原数据。重命名后 configs/archive/records/favorites/bindings/edit_remarks/DL 来源以及生成文件引用可追溯。删除可恢复且部分失败不能报全部成功。复制说明与实际内容逐类一致。

边界与迁移：升级与现有文件生命周期；不把机型空间升级为部门/租户系统。 先建立只读盘点与备份，再替换有破坏性的 move/rmtree 路径；旧目录不自动清理。 执行任务：T05。

### F06 · 分离完整源文档与展示树并修复节点定位（P0）

【代码确认】parser 对 >=200,000 字节 XML 使用属性白名单，非白名单属性直接不进入解析树；XML 同名兄弟共用 id，get_all_values 字典可能覆盖同路径值。JSON 展示值统一为字符串，根标量/空根类型不足以由当前树准确还原。viewer 在过滤树及参数元数据隐藏后的 children 上重新 enumerate，随后用该下标形成审阅 node_key。

依据：[S05 · 文件查看、筛选与修改备注](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/viewer.py)；[S06 · XML/JSON 解析与节点路径](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/parser.py)；[S07 · 审阅定位、修改与序列化](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/reviewing.py)；[S14 · 结构化与文本差异](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/differ.py)；[S22 · 搜索过滤与计数](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/searching.py)。

影响与不整改成本：大文件显示/差异遗漏数据；重复节点收藏与对比混淆；筛选页面对某节点提交的建议可能定位到另一个原始节点。位置或 label+occurrence 都不能被当成跨版本稳定身份。

推荐的最小整改：【建议】保留统一展示结构作为 ViewTree，但从完整源文档解析出 DocumentSnapshot：原始 bytes、hash、格式、完整类型与节点 locator。JSON locator 使用有转义的 token 路径/JSON Pointer；XML 用展开命名空间、兄弟位置和明确的 text/attribute 定位，仅在对应源版本内解释。过滤、分组、隐藏字段只改变展示，不重建定位。结构化 diff 对类型、空容器和重复节点有明确语义；旧 path 仅作为显示/迁移别名，不保证跨版本自动匹配。取消按文件大小丢弃语义字段，改为展示层按需展开。

验收标准：199,999/200,000 字节两侧相同字段均被保留；重复 XML 标签、命名空间、带点/斜杠 JSON 键、空对象/数组/根标量及类型变化有用例。搜索命中子节点、隐藏参数属性后，提交 locator 仍指向完整原始文档同一节点。旧收藏/绑定可唯一解析者继续使用，歧义项标记待重新绑定，不猜测迁移。

边界与迁移：解析契约、节点引用、收藏/绑定兼容与结构化差异；不建设 schema registry，不给所有文件改 UUID。 为节点增加 locator、source_hash 和 parser_version，保留原 id/label；新旧读取并行，旧缓存按版本失效重建。 执行任务：T06。

### F07 · 重建审阅提交的版本校验与保真输出（P0）

【代码确认】apply_review_updates 仅按 node_key 修改展示树，不检查源版本和原值；review 页面从缓存/当前文件生成内容，再批量更新备注状态。序列化会从有损展示树重建 XML/JSON，多文件任一失败可留下部分结果；提交没有完整幂等与过期选择检查。

依据：[S07 · 审阅定位、修改与序列化](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/reviewing.py)；[S08 · 审阅提交与结果文件生成](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/review.py)；[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S06 · XML/JSON 解析与节点路径](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/parser.py)。

影响与不整改成本：可能误改节点、覆盖过期建议、丢非修改字段，或出现“文件已生成但状态未提交”。这是正确性问题，不能仅通过换节点 ID 解决。

推荐的最小整改：【建议】建议记录保存 source_hash、locator、原始类型/值和 schema_version。提交时重新授权、在锁内核验 hash 与仍为 pending 的记录，只修改原始源文档中选定位置；输出重解析并比较预期差异后才能发布。不将展示树作为写回源。按单个源文件组成可独立提交批次，批次有唯一 ID/结果清单和可恢复状态；跨文件明确逐文件成功/失败，不伪装全局原子。无法保证保真的文档暂保留只读和原文下载，明确不允许不安全生成；保留原文，不静默修复命名空间后当作原始文档导出。

验收标准：源文件在建议提交后更新时拒绝自动应用并显示冲突。筛选/重复节点建议不误改。仅选定值变化，未选属性、类型、文本、尾文本及受支持注释/命名空间语义保留；不支持的特性明确阻止生成。重复点击/双管理员提交只产生一次业务结果。故障重启后能辨识待恢复批次；单文件失败不标为 approved，原配置不被覆盖。

边界与迁移：现有建议—审阅—生成新文件闭环；不新增多级审批、工作流引擎或自动合并。 新增版本与批次字段，旧备注原样保留；缺少基准版本的待审备注必须重新核验/提交，不能自动假定适用于当前文件。 执行任务：T07。

### F08 · 修复临时上传与历史记录的文件引用链路（P0）

【代码确认】访客上传/URL 导入解析后丢弃内容，只以 filename 打开标签；viewer 随后仅从持久化目录加载，同名时可能显示服务器旧文件，否则显示不存在。home._load_archive_tree 的冷缓存分支使用 os 但模块未导入 os。records 打开 /record_view 时没有 profile 参数，独立页面再次依赖浏览器上下文。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S05 · 文件查看、筛选与修改备注](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/viewer.py)；[S11 · 当前文件与归档下载路由](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/file_downloads.py)；[S18 · 历史列表与比较入口](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/history.py)；[S20 · 修改记录列表](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/records.py)；[S21 · 修改记录独立页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/record_view.py)；[S38 · 标签持久化](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/tabs_state.py)。

影响与不整改成本：已经承诺的访客临时查看不成立；旧版本冷缓存不可查看；切换机型后修改记录链接可能打开错误空间。

推荐的最小整改：【建议】引入简单 FileRef(profile_id, kind, name, version/token)，kind 仅为 current/archive/record/temp。临时原始内容放入会话受控临时目录或有容量限制的会话存储，使用不可猜测 token、所有者验证和过期清理；不放共享 configs，也不只用文件名索引。查看、下载和标签持有同一 FileRef，明确显示来源。补齐 os 导入与异常清理。记录链接显式携带机型，旧链接保留兼容解析但显示当前解析机型。

验收标准：访客上传新文件能查看；上传与服务器同名文件时显示临时内容且服务器 hash 不变；不同会话不能打开对方临时 token。临时项过期有明确提示。清空缓存后归档仍可查看。跨机型同名记录链接始终打开指定版本；当前/归档下载的旧 URL 参数继续兼容。

边界与迁移：现有临时文件、当前版本、归档与修改记录；不统一成复杂资源平台。 先修缺失导入和临时内容流，旧标签字典通过适配器转成 FileRef；过期临时标签不再回退同名服务器文件。 执行任务：T08。

### F09 · 统一更新入口的校验与受控异步执行（P0）

【代码确认】上传已部分使用 asyncio.to_thread，但管理页全量/单项更新、历史 diff、全局搜索、审阅和部分 DL 计算仍同步执行。scheduler.run_single_update 下载后直接保存，缺少解析验证。home 的 _busy_processing 是全局布尔，不能表达多个并发任务；记录页另有定时拉取，与服务器调度重叠。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S09 · 定时与手动更新](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/scheduler.py)；[S10 · URL 下载](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/downloader.py)；[S17 · 更新设置、人员映射与管理员表单](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/management.py)；[S18 · 历史列表与比较入口](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/history.py)；[S20 · 修改记录列表](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/records.py)；[S23 · 全局搜索页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/search.py)；[S16 · DL 输入、编辑、绑定与结果页](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/dltool.py)。

影响与不整改成本：网络错误页可能替代有效配置；一次长操作影响其他用户连接；手动/定时重入重复归档。全局 busy 既相互干扰，又可能被先完成的任务提前解除。

推荐的最小整改：【建议】保留 APScheduler，启动/关闭由统一生命周期管理。手动与定时入口调用同一 update 操作：下载到暂存、按格式验证、比较源 hash、原子提交，再生成可重建 diff。按 profile+操作键防重入，使用有上限的执行器/队列和明确 TaskStatus。I/O/计算离开 UI 事件循环；UI 更新回到原客户端上下文，导航离开后结果不得覆盖新页面。记录拉取只有服务端调度负责，页面仅刷新状态。取消只在安全边界生效，不能宣称取消 await 已终止工作线程。

验收标准：HTTP 200 错误页、畸形 XML/JSON、超时和超量均不覆盖当前配置。相同文件内容不重复归档，并记录检查时间。手动与定时同时触发同一更新不会重复提交。双客户端慢下载/比较期间另一客户端可操作且无断连；异常、离页、重试后 busy 必须恢复。关闭服务停止接单并明确等待/恢复行为。

边界与迁移：更新、重型页面工作与任务结果；不引入 Celery、Redis 或分布式队列。 保留现有 to_thread 和加载代际守卫，先修其他同步入口；待同等失败测试通过再移除全局 busy，不能直接删除超时 workaround。 执行任务：T09。

### F10 · 保证解析缓存与源版本的一致性（P0）

【代码确认】parse_cache 按源路径散列定位，检查 mtime_ns/size，但没有 parser/schema 版本；保存 tree 后再读取/写入元数据可能把不同时间的源与树组合。两份缓存文件使用固定 .tmp，多个写者可冲突。缓存清理用删除文件数扣除条目数，统计口径也不一致。

依据：[S13 · 解析缓存](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/parse_cache.py)；[S05 · 文件查看、筛选与修改备注](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/viewer.py)；[S08 · 审阅提交与结果文件生成](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/review.py)；[S24 · 收藏实时值解析](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/favorites_live.py)。

影响与不整改成本：修复解析器后旧树仍可能命中；并发更新可返回错误版本或产生损坏缓存。审阅尤其不能以缓存作为权威输入。

推荐的最小整改：【建议】缓存只对应不可变的源快照，键/元数据包括源 hash、parser_version、cache_schema_version。用一次快照解析得到树，不在解析后重新读取源状态来冒充版本。优先单文件缓存封装或唯一代际目录加原子指针，避免两文件不一致；使用唯一临时名与同键互斥。审阅从校验后的原文重新构建或使用同 hash 的完整源模型。缓存损坏直接丢弃重建；清理只访问受控缓存根，统一按条目报告。

验收标准：解析器升级即失效；同大小/同 mtime 的不同内容不会用于权威操作。源在解析期间替换、同键并发写、缓存半写与过期清理均不返回混合版本。删全部缓存后业务可用，缓存清理不触碰原配置或归档。

边界与迁移：已有解析缓存，不引入外部缓存服务。 旧缓存一律按缺少版本元数据处理为 miss；保留源文件和历史版本，不迁移可丢弃树。 执行任务：T10。

### F11 · 修复 DL 数值输入、结果对应与求解语义（P0）

【代码确认】_collect_inputs 使用 inp.value or 0.0，将未设置范围变为 0；_pp_scale_info 用 or 1.0，使真实 0 被替换且分母为 0 检查失效。反向结果经过范围过滤后按旧 y_vals 下标贴回目标 y。poly_find_x 仅网格命中/变号检测，不能保证找出切触重根，且未单独处理左端点和恒等情形。字段提取失败可默认为 0 并仍出现成功提示。

依据：[S15 · DL 计算引擎与配置](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/dltool.py)；[S16 · DL 输入、编辑、绑定与结果页](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/dltool.py)；[S36 · DL 多解测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_dltool_multi.py)。

影响与不整改成本：结果数值、输入标签或边界可能错误；“全部实根/无解/最优解”的文案超出了算法保证，影响工具可信度。

推荐的最小整改：【建议】区分 None、0 和非法数值；拒绝 NaN/Inf、非正采样数及无效区间。结果全程携带 input_index 与原始输入，不根据过滤后的数组位置重标。提取系数返回成功/缺失/无效字段，不默默用 0 替代。即时收敛求解文案为“已检测到的候选根”，增加端点、重根、恒等与残差校验；实现可验证的有界多项式实根策略前不得承诺穷尽。优先评估现有依赖是否已有可信求根能力；没有则比较至多八阶导数分段算法与新增数值依赖的成本，不只增加采样数。多解选择不自动称最小绝对值为最优。

验收标准：未设置范围保存后仍为 None；传输系数 0 与分母 0 分别按规则处理。过滤第一个 y 后，其余行仍对应正确输入。覆盖 (x-a)^2 的非网格根、左端点根、常数/零多项式、近重根、区间外根、负缩放和溢出，输出残差及不确定状态。无效绑定不能改变已生效系数。原有三类计算及系数文件可读。

边界与迁移：现有正反向计算与系数提取；不建立通用数学平台。 先修确定性数值与行映射缺陷，再完善求根覆盖；无法证明完整性时保留诚实的近似输出与原始参数。 执行任务：T11。

### F12 · 建立可复现离线发布包并修复启动配置（P0）

【代码确认】requirements 只有 nicegui>=2.0.0、apscheduler>=3.10.0，无已验证版本锁；run.py 定义了 MCHECKER_PORT 读取却固定使用 50002，与 README 的 50001/环境变量行为矛盾。仓库包含 .nicegui 用户状态和 __pycache__。现有源码未证明必须使用公网 CDN，不能将文档“首次需要网络”的表述直接当作事实。

依据：[S01 · 启动入口](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/run.py)；[S29 · 依赖声明](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/requirements.txt)；[S39 · 现有架构说明，仅作对照](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/docs/architecture.md)；[S40 · 现有使用与部署说明，仅作对照](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/README.md)。

影响与不整改成本：同一源码安装出不同运行环境，离线部署无法保证复现；端口配置失效；发布可能携带开发会话。实际部署版本与全部资源请求仍需实测。

推荐的最小整改：【建议】从当前已工作的环境导出并验证 Python/NiceGUI/APScheduler 和传递依赖精确版本、哈希、平台信息，生成对应 OS/架构的 wheelhouse 与离线安装脚本。修复端口读取；升级已有 50002 部署时显式保留其监听地址，不静默改端口。支持可选数据根但保留旧 data 默认，存储 secret 安全持久化失败须明确报错。打包本地字体/图标/JS/CSS 所需资产，首访禁公网验收。从版本控制/发行包移除开发会话与字节码，不删除部署机实际会话；检查已公开内容是否需要凭据轮换。

验收标准：全新目标机器不访问索引即可安装；空浏览器缓存且禁止公网时完成查看、搜索、比较、审阅、DL、下载与首屏字体/图标加载。允许的内网更新源照常工作，公网来源在离线模式明确禁用。MCHECKER_PORT 生效；旧部署升级不意外换端口或数据根。发布包不含用户 .nicegui、业务数据和 secret。

边界与迁移：构建、安装、运行资产、端口、数据根与发布卫生；不新增强制容器、在线更新器或环境管理服务。 先记录现网版本/端口/路径并做部署副本验证，再锁定发行环境；不为了追新直接升依赖主版本。 执行任务：T12。

### F13 · 隔离测试数据并建立关键回归门槛（P1）

【代码确认】test_differ 的绑定测试只替换 BINDINGS_FILE，而当前存储实际使用 profile 文件路径，测试可能写到仓库默认 data。该测试以 len(bound_items)>=0 作为断言，无法发现绑定完全失效。已有测试覆盖若干纯函数和存储流程，但不能证明浏览器权限、审阅保真、离线首访和并发恢复正确。

依据：[S30 · 对比测试与测试隔离](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_differ.py)；[S31 · 存储回归测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_storage.py)；[S32 · 机型回归测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_profiles.py)；[S33 · 文件下载测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_file_downloads.py)；[S34 · 解析测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_parser.py)；[S35 · 解析缓存测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_parse_cache.py)；[S36 · DL 多解测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_dltool_multi.py)。

影响与不整改成本：测试可污染本地数据；无效断言给出假安全感；重构和新增功能容易重复引入已经修过的 P0。

推荐的最小整改：【建议】在统一 conftest 中按测试提供临时 DATA_DIR、profiles、缓存和会话目录，重置 ContextVar/单例；禁止测试写到 fixture 根外。修正绑定断言为明确路径/组/数量。每个 P0 修复随 PR 添加反例与正例，CI 运行纯函数、存储故障注入和接口测试，浏览器/离线套件可在发行候选阶段运行。测试工具是开发依赖，不进入运行包。

验收标准：测试前后真实 data/.nicegui 不变；用例可独立、随机顺序重复执行。绑定故意破坏时测试必失败。每个 P0 关联回归用例，失败会阻止发布；不存在吞异常后默认通过。

边界与迁移：测试隔离、有效断言与最小 CI；不设无依据的覆盖率百分比。 先隔离目录再跑原测试；不以删除失败用例来获得全绿。 执行任务：T13。

### F14 · 提取应用操作边界并统一错误与日志（P1）

【代码确认】页面直接串联权限、解析、存储、结果通知；differ 直接导入 storage.get_bound_paths，storage 又调用 differ；storage 的机型选择反向依赖 NiceGUI。三个模块重复实现 JSON 读写，异常经常被 pass。

依据：[S02 · 存储、机型上下文与文件生命周期](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/storage.py)；[S03 · 身份与权限判断](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/utils/auth.py)；[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S08 · 审阅提交与结果文件生成](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/review.py)；[S09 · 定时与手动更新](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/scheduler.py)；[S14 · 结构化与文本差异](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/differ.py)；[S15 · DL 计算引擎与配置](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/dltool.py)；[S17 · 更新设置、人员映射与管理员表单](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/management.py)。

影响与不整改成本：新增入口必须复制保存和权限逻辑；错误无法定位；目录虽然分开，核心业务仍难脱离 UI 测试。

推荐的最小整改：【建议】仅提取已存在的复用操作：配置导入/更新/生命周期、审阅提交、任务执行。普通查询可直接调用明确 profile 的存储函数。保留 core.storage 兼容门面，底层原子 I/O 和路径边界归一；不要按每张 JSON 表生成 Repository。differ 接收绑定快照而非自行读存储。应用错误提供 code/message/retryable/context，UI 和 HTTP 各自映射。标准 logging 记录 operation_id、profile、actor、目标、结果、耗时和恢复信息，轮转并脱敏；无需求不拆多个日志平台。

验收标准：核心导入、审阅、diff 可不加载 NiceGUI 测试。上传与定时更新复用同一验证/提交路径。核心模块不导入 pages；新入口不复制授权与文件提交。磁盘满、权限拒绝、版本冲突、解析失败在 UI 和日志有一致代码且不泄密。旧函数进口/参数包装有兼容测试。

边界与迁移：形成模块边界，不为所有 getter 包 service，也不把 storage.py 拆文件数当目标。 先特征测试，再一次迁出一个业务用例并保留转发函数；每步可独立回滚，避免业务逻辑与目录重排混在同一个大提交。 执行任务：T14。

### F15 · 收敛工作区导航与配置管理表单（P1）

【代码确认】home 集中工作区、收藏、机型、导入、历史加载；双侧栏默认展开，主内容有 max-width。管理页把更新源、调度、IP 和管理员堆在同页，小时/天选择未接入保存计算；tools/bindings 保存后通知成功但列表未即时刷新。角色标签仅反映 admin，部署者可显示为游客。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S17 · 更新设置、人员映射与管理员表单](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/management.py)；[S25 · 绑定管理页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/bindings.py)；[S26 · 工具菜单页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/tools.py)；[S27 · 主题与 JavaScript 注入](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/theme.py)；[S28 · CSS 与信息密度](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/static/css/style.css)；[S37 · 标签策略](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/tab_manager.py)；[S38 · 标签持久化](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/tabs_state.py)。

影响与不整改成本：核心数据区域被压缩，操作位置与权限不易理解；用户容易重复保存或误判调度周期。新增页面会继续堆积 home 的分支与状态逻辑。

推荐的最小整改：【建议】保留文件工作区和标签，左侧改紧凑可搜索文件列表，右工具区默认可收起；顶部主动作增加文字，当前机型、来源和权限明确。设置按更新源/调度、人员权限、机型、工具/绑定分区，不另建 Dashboard。统一页面标题、工具栏、带标签表单、行级错误与空状态，更新源/IP/管理员/历史优先紧凑表格。小时/天正确换算但磁盘仍存 interval_hours。保存成功局部刷新并保留筛选。提取轻量 workspace/tab 操作，不建立动态插件路由。

验收标准：现有功能入口全部可到达，角色显示真实；新增/修改/删除后列表立即一致。小时/天设置往返一致；非法 IP/重复名称/空绑定有行级反馈。1366×768 和 1920×1080 代表窗口可完成核心任务，不依赖 hover 才发现唯一主操作；机型切换和标签恢复不误打开其他来源。

边界与迁移：信息架构、工作区和现有设置流程；界面尺寸是验收视口，不是对当前页面实测结论。 保留旧标签 key 与入口适配，按页面替换布局；先解决功能与密度，再做视觉修饰。 执行任务：T15。

### F16 · 统一树视图并修复搜索与版本对比连续性（P1）

【代码确认】viewer/search/收藏/归档/record_view 多套树渲染存在重复和差异；收藏树调用 window.mct，但主题只定义 mcSetTreeNode/mcToggleTree/mcTreeSetAll。filter_tree_and_count 在父节点匹配时提前返回且可能计为 0，页面据此显示未找到。history._compare_with_current 接收 archive_filename 却未存入比较标签，比较页默认另选第一项。CSS 对树禁用文本选择，部分长值被截断。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S05 · 文件查看、筛选与修改备注](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/viewer.py)；[S18 · 历史列表与比较入口](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/history.py)；[S19 · 比较页面与复制结果](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/comparison.py)；[S21 · 修改记录独立页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/record_view.py)；[S22 · 搜索过滤与计数](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/searching.py)；[S23 · 全局搜索页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/search.py)；[S27 · 主题与 JavaScript 注入](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/theme.py)；[S28 · CSS 与信息密度](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/static/css/style.css)。

影响与不整改成本：相同数据在不同页面表现不同；有命中却无结果；点击某个版本后对比了另一个版本，破坏用户对结果的理解。

推荐的最小整改：【建议】共用树行/节点控件与本地交互脚本，读写能力以少量明确选项控制，不建通用 schema-driven renderer。保留原 locator 与过滤上下文，搜索分别表达命中节点与命中变量，父节点命中不能丢结果。比较 FileRef 明确 old/new、指定版本和原始上传名称；历史入口传入并持久化完整比较选择，重用标签时也更新选择。增加路径/值复制、展开长值、键盘操作；clipboard 确认成功后再提示，HTTP 不支持时提供选中文本/下载替代。

验收标准：搜索父节点、文件名和备注都有预期结果；筛选前后定位相同。从第三个历史版本点击比较时，两侧来源准确且重新打开仍一致。所有树页面折叠/展开、焦点、长值和复制一致；HTTP 内网复制失败不显示假成功；原有四类对比都覆盖。

边界与迁移：同类树控件、搜索与比较，不新增全文搜索服务。 先修版本参数与搜索计数，再从 viewer/search 两个已重复页面提取控件，其余页面逐步复用。 执行任务：T16。

### F17 · 保留审阅与计算草稿并明确任务结果（P1）

【代码确认】审阅要求本页所有待审节点先做选择才能提交，选择状态在渲染局部闭包；DL 的 editing 写入共享配置，绑定“应用”会即时保存，因此“取消”不一定撤销已应用变更。导入提前关闭对话框，长任务提示与失败恢复不统一。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S08 · 审阅提交与结果文件生成](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/review.py)；[S15 · DL 计算引擎与配置](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/dltool.py)；[S16 · DL 输入、编辑、绑定与结果页](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/dltool.py)。

影响与不整改成本：切页丢审阅选择、多个用户共享编辑模式、取消语义不可信，长流程失败后必须重做。

推荐的最小整改：【建议】编辑草稿与生效配置分开；DL editing、输入控件和中间绑定放会话状态，保存时校验版本并一次提交，取消不写共享文件。审阅按文件/已选条目提交，未选择项维持 pending；切页保留当前会话草稿并标注源版本。复用已有 TaskStatus 展示排队/执行/成功/部分失败/冲突，避免长期悬挂 toast；失败保留输入，明确重试是否安全。结果页展示来源文件、版本、参数和时间，不自动覆盖当前配置。

验收标准：切换标签后审阅选择和 DL 草稿可恢复；两会话进入编辑互不影响；取消后生效配置 hash 不变。部分审阅不影响未选项；源变化产生冲突提示。离页、断连、重连和重复提交均给出可解释状态，输入不被无提示丢弃。

边界与迁移：现有审阅/计算表单与任务状态，不新增跨设备草稿同步和通知中心。 兼容读取旧 editing 字段但不再当作全站状态；保留旧系数与未审备注，逐页迁移草稿。 执行任务：T17。

### F18 · 按代表性负载优化渲染与查询成本（P1）

【代码确认】文件树一次创建全部后代节点；全局搜索遍历文件；history/records 会在渲染期间解析或比较多个版本；home 有 0.3 秒标签检测和每秒文件/收藏检查。性能退化程度尚未在真实部署测量。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S05 · 文件查看、筛选与修改备注](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/viewer.py)；[S18 · 历史列表与比较入口](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/history.py)；[S20 · 修改记录列表](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/records.py)；[S23 · 全局搜索页面](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/search.py)；[S13 · 解析缓存](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/core/parse_cache.py)。

影响与不整改成本：随着节点、版本和客户端增加，重复解析、全量 DOM 和每客户端文件扫描可能形成首要性能瓶颈；不能用丢字段来换速度。

推荐的最小整改：【建议】先记录部署硬件、文件数/节点数、冷暖缓存、客户端数、交互延迟、事件循环延迟和峰值内存。优先 lazy expand、结果分页、按需 diff、复用同 hash 的解析结果；本地动作直接刷新，跨客户端用轻量版本签名定时检查，隐藏页面暂停检查。保留全量原文和搜索语义；只有测量证明仍不够时才考虑进程池或搜索索引。

验收标准：同一数据/硬件/脚本下记录前后结果；全字段解析和旧功能结果一致。大文件展开/搜索可中断显示且不污染新页面；长会话切页/关页后任务、timer 和临时项有界释放。冷缓存可用，不能只报缓存命中成绩；不设无依据的性能提升百分比。

边界与迁移：现有性能瓶颈与资源释放；不凭预测引入数据库、Redis 或前端框架。 每次只改变一个有测量证据的热点；无法证明收益的优化撤回。 执行任务：T18。

### F19 · 完成核心流程、离线与升级恢复验收（P1）

【代码确认】现有 tests 的主要验证单位是函数与文件操作，尚没有足够证据覆盖完整多用户浏览器流程、首访断网、升级中断与恢复。此项是集成验收，不代替各 P0 的本地回归。

依据：[S30 · 对比测试与测试隔离](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_differ.py)；[S31 · 存储回归测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_storage.py)；[S32 · 机型回归测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_profiles.py)；[S33 · 文件下载测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_file_downloads.py)；[S34 · 解析测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_parser.py)；[S35 · 解析缓存测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_parse_cache.py)；[S36 · DL 多解测试](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/tests/test_dltool_multi.py)；[S40 · 现有使用与部署说明，仅作对照](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/README.md)。

影响与不整改成本：独立修复可能在真实页面、目标 OS、原有数据和代理环境中组合失效；没有恢复演练就不能确认升级安全。

推荐的最小整改：【建议】用脱敏旧布局/新布局/混合布局和代表文件建立发行验收包。浏览器覆盖访客、部署者、管理员、两机型、当前/临时/归档/记录，以及上传—查看—搜索—收藏—比较—建议—审阅—下载闭环。目标环境禁公网首次安装和访问；模拟停止、磁盘错误及多文件操作中断后恢复。记录失败项、环境与证据，完成后才更新支持矩阵和发布状态。

验收标准：各 P0 验收通过，完整业务闭环通过；升级前后业务文件与引用核对一致；回滚恢复旧代码及对应备份可用，运行 secret/会话按策略保留。无运行时公网请求，数据留部署端。未知/失败项不能标通过；明确单进程与文件规模边界。

边界与迁移：发布门禁与恢复演练，不建设复杂测试平台。 先在副本环境验收再计划部署；本路线图交付不代表已经改动或验收生产代码。 执行任务：T19。

### F20 · 收敛视觉样式并清理重复覆盖（P2）

【代码确认】已有 CSS tokens、系统字体栈、focus-visible 与 reduced-motion，但全局 .q-card:hover 阴影、彩色收藏边框、多个树配色和 DL 内联样式并存。其存在不等于必须换 UI 框架。

依据：[S04 · 首页、工作区、上传与机型管理](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/home.py)；[S16 · DL 输入、编辑、绑定与结果页](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/dltool.py)；[S27 · 主题与 JavaScript 注入](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/pages/theme.py)；[S28 · CSS 与信息密度](https://github.com/Arragon/MCChecker/blob/e47e54a01f0ad6d74db3a08670a1034f937575c2/app/static/css/style.css)。

影响与不整改成本：视觉噪声、非交互区域的错误可点击暗示和升级 CSS 覆盖维护成本；不阻塞当前正确使用。

推荐的最小整改：【建议】复用现有 tokens，收敛语义色、圆角、密度与焦点；移除无功能收益的 hover 阴影/缩放，样式限制在 mc 命名空间，减少 Quasar 全局覆盖。保留必要 loading 动画和可访问性。主题资产通过本地静态入口加载，不在每个页面重复发送整段 CSS。

验收标准：代表页面的字体、间距、状态色和焦点一致；非交互容器不诱导点击；长值、键盘与密度不退化；没有新增公网资源或运行依赖。视觉回归与功能回归通过，P2 不阻塞前一阶段可用版本发布。

边界与迁移：非必要视觉 polish，不新增 Dashboard、深浅双主题或整套 CSS 框架。 在功能布局稳定后小范围归并样式，保留旧选择器过渡并逐项删除失效覆盖。 执行任务：T20。


## 5. 目标架构：可靠的单体，而不是新平台

### 5.1 依赖方向

```text
NiceGUI pages / workspace          现有 HTTP 路由
             \                       /
              明确的应用操作（少量函数或模块）
              ├─ 配置导入、更新、改名、删除
              ├─ 审阅提交与结果生成
              └─ 受控任务执行
                 │       │
       纯解析 / 定位 / diff / DL 数学逻辑
                 │
       显式 profile 的存储门面 + 安全文件 I/O
                 │
       本地 JSON / 原文 / 归档 / 临时数据
```

【建议】页面负责布局、输入、会话草稿和把结果显示给正确客户端；应用操作负责授权、版本校验、业务顺序和提交；parser/differ/reviewing 的算法部分不读取 UI 或权限；存储负责显式路径、读写、互斥和恢复。简单只读查询不必绕过一个毫无逻辑的 Service 类。不要为了图形分层把每个函数封装成接口。

### 5.2 建议模块落点

| 边界 | 最小落点 | 不承担的职责 | 迁移方式 |
|---|---|---|---|
| 启动配置 | `app/settings.py` 和现有 `run.py` | 不实现配置中心 | 汇总现有环境变量和默认值，保留入口 |
| 身份/动作授权 | `app/utils/auth.py` 的 UI 适配 + 一个无 NiceGUI 的授权模块 | 不实现登录平台 | 先修检查，再提取纯策略 |
| 机型与路径 | 在 storage 内建立显式 profile 接口，必要时抽出小模块 | 不从浏览器推断机型 | 旧函数为兼容包装，新业务必传参数 |
| 安全持久化 | `app/core/file_io.py` 或等价小模块 | 不做通用 Repository/事务引擎 | 替代已有重复 JSON/bytes 写入 |
| 配置操作 | `app/services/config_files.py` | 不渲染页面，不持有 UI 元素 | 导入/更新/生命周期逐个迁入 |
| 审阅操作 | `app/services/reviews.py` + 现有 `reviewing.py` | 不定义多级审批流程 | 页面不再负责文件与状态协调 |
| 任务执行 | `app/services/tasks.py` + 现有 scheduler | 不实现分布式队列 | 保留调度，合并手动/自动业务入口 |
| 页面状态 | `app/ui/workspace.py`，复用 tab_manager/tabs_state | 不持久化业务角色和生效配置 | 保留标签兼容适配 |
| 共用视图 | `app/ui/tree_view.py` 和少量表单/反馈 helper | 不做 schema-driven UI 引擎 | 从两处已重复的控件提取 |
| HTTP 文件接口 | 保留 file_downloads 的行为，可后续移动目录 | 不新增全部业务 REST API | 保留旧 import 与旧 URL 转发 |

以上是职责落点，不是一次性必须创建的目录清单。先在原文件里完成正确性修复，再根据已经形成的接口提取模块。`storage.py` 可长期保留兼容门面；不要仅因行数高就拆成十几个单方法文件。

### 5.3 必须稳定的部分

【建议】稳定现有文件名、profile ID、默认数据目录、配置 JSON 的主要形状、收藏/绑定信息、归档原文、修改记录、原文件下载和四类对比能力。稳定“不自动覆盖原配置”的审阅结果语义。保留原有标签数量限制和关闭策略、UI 超时修复的已知动机及现有命名空间兼容分支，直到有回归用例和替代行为。

安全漏洞本身不属于必须兼容的行为。过去不受限的路径、危险 URL、未授权管理写入和误导性数值结果应明确收紧，但收紧不能通过删除原始数据实现。不能将“完美兼容”解释为继续允许不安全执行。

## 6. 数据模型、节点与配置边界

### 6.1 区分四类数据

【建议】第一类是原文与历史版本，是权威数据，必须可下载、备份和按字节核对。第二类是业务 JSON，包括映射、收藏、绑定、建议、DL 参数、机型和权限表，也属于权威数据。第三类是解析树/diff/搜索中间结果，可删除重建。第四类是会话状态，包括当前标签、筛选、折叠、草稿和临时文件索引，不得混入共享生效配置。

```text
data/                              默认保持原目录，可显式配置到外部路径
  device_models.json
  nicegui_storage_secret.txt
  profiles/<profile_id>/
    configs/                       当前与审阅生成的配置原文
    archive/<name>/                 原有归档继续读取
    records/<name>/                 下载的修改记录
    config_mapping.json
    favorites.json
    bindings.json
    edit_remarks.json
    tools.json
    schedule.json
    ip_mapping.json
    admin_users.json
    dltool_config.json
    cache/                         可重建、带版本
    operations/                    仅复杂文件操作的恢复清单，按需建立
  temporary/                       受控临时原文、会话所有者和期限
  recovery/                        备份/隔离清单，不做自动破坏性清理
```

新增目录只服务于已经存在的临时导入、破坏性文件操作和失败恢复。路径名称可调整，不能变成广义资产管理系统。`.nicegui` 的实际位置需由锁定版本及运行配置确认，升级时单独保留；不能把仓库里的开发用户状态作为发行数据。

### 6.2 轻量契约

```python
# 建议的概念契约；不是可以直接替换项目的完整实现。
FileRef(profile_id, kind, name, version_or_token=None)
DocumentSnapshot(file_ref, raw_bytes, content_hash, format, parser_version)
NodeRef(content_hash, locator, value_type, original_value)
OperationResult(operation_id, status, artifacts, warnings)
TaskStatus(task_id, profile_id, operation, state, progress, error_code)
```

`FileRef` 明确当前/归档/记录/临时来源，不把绝对服务器路径暴露给浏览器。`NodeRef` 绑定源版本，不承诺可在不同版本自动漂移。`OperationResult` 不把通知消息当作业务结果。用 dataclass/TypedDict 和普通函数足够，不需要对象工厂、依赖注入容器或通用消息总线。

### 6.3 读取模型与写入模型

【建议】完整源模型必须保留 XML 全部属性、文本/尾文本和格式语义，JSON 类型、容器与根类型；ViewTree 可以隐藏元数据和懒加载，但不能从完整源模型删除字段。预处理未绑定命名空间或异常尾部内容时，保留原 bytes 并产生“兼容解析/已修复用于查看”的警告；除非可以明确证明写回语义，否则不进入审阅自动生成。

JSON 的 locator 使用转义 token 区分键中的 `/`、`~`、`.` 和数组下标；XML 的 locator 在固定原文版本内区分同名兄弟、属性、text 和 tail。节点展示 ID、可复制的显示路径和机器 locator 是不同概念，不再混用一个字符串。

对比的最小正确目标是可解释的结构和值/类型变化，不强求树移动识别、语义重排合并或最优匹配。对于重复 XML 节点重排，可以报告增删/位置变化和文本 diff，不能无证据匹配为同一实体。绑定和收藏的旧路径若只对应一个节点可自动适配；有多个候选时标记歧义并让用户重绑，不能随便选第一个或最后一个。

### 6.4 配置演进与旧数据

【建议】保持 `schedule.json` 的 `interval_hours`，UI 的天/小时只是输入转换；保持现有 JSON 列表形状，不为加 schema_version 把所有列表改成包装对象。只有真正发生结构变更的数据新增版本字段或独立布局版本文件。未知字段尽量原样保留，旧字段通过明确适配补齐，不能在保存时过滤掉不认识的业务字段。

损坏文件与不存在文件分开处理：不存在可给安全默认；损坏/格式不符时保存原文件、记录问题并拒绝覆盖该权威数据。默认机型、历史别名和读取回退仅属于兼容层，新代码不能依赖无效输入回退 default。

## 7. 写入、归档和恢复协议

### 7.1 单文件保存

【建议】读改写在同一机型锁下执行。内容先写入目标目录里的唯一临时文件，校验成功后 flush/fsync，再调用 `os.replace` 切换。POSIX 下按部署文件系统能力考虑目录 fsync；Windows 的占用和替换失败必须有清楚错误，不可“忽略后继续”。这只提供单文件原子替换，不是断电永不丢失的跨平台保证。参考：[Python os.replace / fsync](https://docs.python.org/3/library/os.html)。

更新配置时，网络下载、完整解析和耗时 diff 在锁外进行；提交锁内核对预期旧版本仍相同，再保留旧版并替换当前版。同内容更新记为 unchanged 和最后检查时间，不制造重复归档。归档名称可在原日期样式后增加唯一后缀，必须独占创建，不能第二次重名还覆盖。缓存和 diff 不是提交成功的前提，但其失败必须可见、可重建。

### 7.2 多文件操作

【建议】改名、机型迁移和审阅发布确实跨多个权威文件，不要声称“每个 JSON 都 replace”就完成事务。采用仅针对这些操作的小型恢复清单：operation_id、操作类型、原/目标引用、预期 hash、备份位置、已完成步骤和结果。提交前盘点冲突，步骤幂等，失败时禁止继续对受影响对象写入并提供恢复结果。日志与恢复清单用途不同，不能靠读日志猜怎么恢复。

改名需要更新当前/归档/records 目录、映射、收藏、绑定、待审与已审引用、DL 绑定、生成文件关系；旧标签在恢复时通过改名信息更新或显示明确失效，不能误指向另一个同名对象。复制机型默认复制业务模板而不是身份权限与操作历史，界面必须列出实际范围；更广复制只有明确选择后执行。

删除默认只进入隔离区域并保留恢复清单，不自动永久删除；“删除更新链接”仍只删映射而不删本地文件。永久清除属于明确的部署者操作，需要显示范围并验证不存在活动任务，不加入自动保留期策略。

### 7.3 备份与回滚

【建议】备份至少覆盖全部权威数据、布局版本、运行配置和 secret；缓存可以排除。最小一致备份流程是暂停新写入、等待当前提交完成，再复制并校验。发布初期宁可有计划地短暂进入只读，也不复制正在多文件写入的数据后声称可恢复。

升级采用“代码与依赖新版本 + 经验证的数据副本 + 恢复点”，不在启动时无提示搬走原数据。失败可恢复到旧代码及匹配的数据备份；升级后新增业务数据须另行保留/导出，不得回滚时直接丢弃。回滚不保证旧代码可读任意未来 schema，兼容矩阵必须明确。

## 8. 身份、安全与本地部署边界

【建议】现阶段可以保留内网 IP 识别，但只能把它当作已知可信网络内的轻量授权依据，不应描述为强认证。人员名称是展示/审计标签，不是不可伪造的身份。存在共享终端、不可信内网、代理不能可靠还原来源或正式审计要求时，必须重新评估本地认证/现有组织身份接入；不需要今天就预建账号平台。

| 操作类别 | 当前整改后的最低要求 |
|---|---|
| 查看、搜索、下载共享配置 | 保持现有共享阅读范围，明确 profile；profile 不是部门权限隔离 |
| 临时上传、临时比较 | 允许现有访客能力，但有会话所有者、大小和期限 |
| 收藏与建议备注 | 保留既有协作语义，明确共享范围和操作人，防并发丢写 |
| 更新源、调度、机型、人员、工具/绑定、DL 生效参数 | 提交时按部署者管理策略校验，不仅隐藏按钮 |
| 审阅与删除配置 | 提交时按已有管理员策略校验，破坏性操作要恢复点 |
| 后台定时任务 | 使用明确 system actor 和 profile；不借浏览器会话身份运行 |

不擅自把部署者自动提升为管理员，也不把所有访客协作写入一刀切成只读。权限矩阵需要以真实业务行为验收，但代码漏检必须封堵。身份来自服务端连接信息；只接受受信代理的转发信息，不能任意信任 X-Forwarded-For。使用内部 HTTPS 时可改善传输与浏览器安全功能，但不能引入公网站点/证书 API 的运行依赖。

安全文件读取要验证真实路径位于正确根下，处理符号链接和 Windows 路径差异。URL 读取要允许明确批准的内网业务源，同时拒绝本地文件协议、危险重定向和非批准目标。没有一个单一“过滤私有 IP”规则适合本项目。

输入数据永远不是 HTML/JavaScript。以纯文本组件显示配置、路径与备注；动态 URL 先校验再通过原生链接或编码参数使用。对现有 Markdown/文本记录也保持相同信任边界。错误不回显绝对路径、完整凭据 URL 或配置值到无权限页面。

## 9. 任务执行、错误与日志

### 9.1 任务生命周期

【建议】保留单进程服务和一个 APScheduler 实例。调度只负责触发同一个业务操作，不拥有另一套下载/保存逻辑。任务状态最小包括 queued、running、succeeded、partially_failed、failed、cancel_requested、cancelled；对不支持真正中断的计算，取消仅停止后续步骤/展示并明确说明。不得将“取消等待”当成文件写入已经停止。

以 profile+资源/操作键控制重入；异步 I/O/执行器有并发和队列上限。不要先无限创建任务再靠 semaphore 让它们全堵在内存中。UI 控件对象不传入工作线程；任务结果用类型明确的数据返回，由仍存活且加载代际匹配的客户端显示。重要写任务与 UI 脱钩，客户端离开不能让提交完成一半；关闭时停止接单、等待安全点，并为复杂提交记录恢复状态。

CPU 密集任务先加输入预算并测量，再决定线程还是独立进程；没有测量依据不引入常驻进程池。页面只读服务器任务状态，不再另行定时爬取修改记录。参考：[APScheduler 3.x user guide](https://apscheduler.readthedocs.io/en/3.x/userguide.html)。

### 9.2 错误契约

| code 示例 | 业务含义 | UI 行为 | HTTP 适配（适用时） |
|---|---|---|---|
| INVALID_INPUT / UNSUPPORTED_FORMAT | 输入不合法或不支持安全操作 | 保留表单，定位字段，说明可做什么 | 400/422 |
| FORBIDDEN | 当前操作未授权 | 明确权限不足，不泄漏文件细节 | 403 |
| NOT_FOUND / TEMP_EXPIRED | 指定对象不存在/临时项失效 | 显示准确来源，不回退同名文件 | 404 |
| SOURCE_CHANGED / REVIEW_CONFLICT | 版本或待审状态已变化 | 显示冲突，要求重新检查 | 409 |
| TOO_LARGE / BUSY | 资源预算或并发限制 | 显示限额与重试条件 | 413/429 |
| DOWNLOAD_FAILED | 内网源不可达/状态错误 | 显示来源摘要和可重试性 | 按具体适配返回 |
| STORAGE_FAILURE / CORRUPT_DATA | 权威数据写入失败/损坏 | 不报成功，进入恢复说明 | 500/503 |

表中是建议接口，不要求为每个错误创建一个继承层级。普通核心异常在应用边界转为少量领域错误；UI 不能显示完整栈，也不能 catch Exception 后当作空列表继续保存。JSON 损坏、权限和版本冲突不自动重试；网络瞬时失败可限次重试，写操作只有具备幂等条件才可重试。

### 9.3 日志

【建议】使用标准 logging，记录时间、级别、operation_id、profile_id、actor 标识、对象引用、动作、结果、耗时、错误代码。配置内容、建议值、完整 URL 查询凭据、Cookie 和 secret 不进普通日志。保留可定位的异常栈于部署端受控日志，按大小/数量轮转；需要操作审计时在现有 logger 上增加结构化字段即可，暂不引入日志平台或无限期双日志。

启动日志报告实际端口、数据根、依赖版本、调度状态和未完成恢复项。机型删除/改名、审阅发布、数据迁移、权限失败和恢复操作有可追踪记录。任务状态与可恢复清单是业务信息，不能仅保存在 toast 或 Python 局部闭包。

## 10. 前后端与 HTTP 边界

【建议】保留 NiceGUI 的服务端 UI 模型，不因为有 services 就建立新的 React/Vue 应用。已有下载路由继续稳定提供原始内容，业务函数不直接返回 UI 元素。

| 现有入口 | 保持兼容的内容 | 必须补强 |
|---|---|---|
| `/` | 当前工作区入口 | 稳定页面/机型/标签上下文 |
| `/api/file-download?kind=...&filename=...&profile=...` | 当前和历史下载、原文件名、probe 行为 | profile 验证、统一路径策略、错误；临时项加入所有者校验 |
| `/record_view?config=...&record=...` | 旧修改记录链接仍可读取 | 新链接显式带 profile，旧链接解析结果可见；安全失败 |
| `/static/...` 与框架静态路径 | 同源资产 | 固定版本资产清单、冷缓存离线验收 |
| NiceGUI 上传/事件 | 既有按钮和上传行为 | 提交时授权、业务校验、任务结果、容量限制 |

【建议】不新建 `/api/v1` 全量资源接口。只有出现真实第二客户端或集成方时，再给已经稳定的应用操作加薄 HTTP 适配，定义版本和权限；此时无须改写算法和存储。下载 probe 成功只能表示资源当时可请求，不能保证浏览器最终下载完成；界面使用“已开始下载”，不显示“文件已保存到电脑”。

## 11. 核心 UI/UX 方案

### 11.1 工作区与信息架构

【建议】保留“左侧文件列表 + 中央标签工作区”的熟悉结构。左侧用紧凑列表而非每文件一张悬浮卡片，加入本地文件名筛选、当前选择和更新/异常状态。右工具栏默认可以收起，工具入口集中，不长期挤占配置查看宽度。中央区域随可用空间伸展，表单可有阅读宽度限制，长树与差异不机械限制到统一窄列。

顶部优先展示当前机型和“上传、链接导入、搜索”等主要动作；设置和低频工具归组。部署者/管理员/协作访客名称与权限矩阵一致，不只显示一个容易误解的游客 badge。配置速览是按文件分组的关键变量列表；叶子收藏优先紧凑行，父节点收藏允许展开，不强制把全部收藏都变成高装饰卡片。

### 11.2 页面级改造

| 页面/流程 | 核心布局与操作 | 必须保留的用户上下文 |
|---|---|---|
| 文件查看 | 一条文件/来源/版本工具栏，搜索、下载、历史、对比；安全共用树 | 机型、FileRef、搜索词、展开状态、滚动位置 |
| 导入 | 文件/URL、名称、大小、来源目标；解析与保存状态在同一流程 | 失败输入与临时内容，不自动覆盖同名当前文件 |
| 更新设置 | 更新源表格，名称、目标、最近检查/成功时间、状态、动作；调度独立分区 | 筛选、选中行、编辑值、单位 |
| 全局搜索 | 关键词与文件分组结果，命中类型、数量和上下文 | 原 node locator、搜索词、目标 FileRef |
| 版本历史/比较 | 历史紧凑列表；明确“旧 → 新”，两侧文件/版本可见 | 被点击的具体历史版本和上传原名 |
| 修改记录 | 与配置版本区分，按时间列出，可打开原文/解析 | 明确 profile、记录 ID/文件名 |
| 审阅 | 按文件分组，原值/建议值/提出人/源版本/状态；选中项提交 | 未提交选择、冲突、结果文件引用 |
| DL | 三个既有计算项，系数与范围分组，生效/草稿明确，结果表 | 输入行序、参数版本、候选根与选择 |
| 机型/权限/绑定/工具 | 紧凑表格和明确标签表单，危险操作范围确认 | 编辑草稿与提交时授权 |

### 11.3 统一交互要求

【建议】Loading 有操作名、可确定进度和结束状态；无法确定进度时只显示正在执行，不制造百分比。成功只在提交完成后显示；局部失败显示哪些对象成功、哪些失败、能否安全重试。空状态区分“没有数据”“没有匹配”“没有权限”“加载失败”，不能全部当作暂无数据。

表单使用可见 label，不以 placeholder 替代字段名；校验贴近字段，保留输入。保存成功后刷新受影响区域，避免全页面 location.reload 成为默认 CRUD 实现。危险删除展示文件、版本、关联数据和恢复方式；明确取消不写业务数据。

树节点值允许选中、复制和展开完整内容；只在必要范围使用 user-select:none。按钮有文字或可访问名称，hover 操作也能用键盘访问；不把颜色作为唯一状态信号。比较界面同时展示来源和方向，避免用户从图标或标签顺序猜测。

标签状态按页面实例/机型隔离，浏览器持久化只保存轻量引用，不保存 UI 控件、角色授权结果和大型原文。15 标签策略可保留，但带未保存草稿的页面不能无提示被自动淘汰；优先关闭无草稿只读标签，必要时明确提示已达上限。对于临时数据，声明刷新/过期行为并测试，不能恢复标签后悄悄读取同名服务器文件。

## 12. 完全离线部署与依赖策略

### 12.1 离线的定义

【建议】“离线”是客户端和服务器无需公网完成安装与核心使用，不是不允许访问部署内网。手动配置的内网 HTTP/HTTPS 更新源可以保留；公网更新链接和外部工具链接必须在离线模式明确不可用，但不能导致首页或查看功能失败。外链不能成为字体、图标、JS、CSS 或启动 API 的隐藏依赖。

构建环境可以联网准备包，发行后安装环境不依赖 PyPI、npm、GitHub、Google Fonts、CDN 或在线图标。平台匹配的 wheelhouse、完整锁文件、hash 清单、应用源码/资产、安装脚本和验证说明一起交付。离线安装示例：

```sh
python -m pip install --no-index --find-links ./wheelhouse --require-hashes -r requirements.lock
```

这个命令以“完整精确锁文件及所有依赖 wheel 已准备好”为前提，不是现有仓库现在就拥有的能力。wheelhouse 通常受 OS、架构和 Python ABI 约束，不能把一台机器的环境目录任意拷贝后称为通用安装包。参考：[pip repeatable installs](https://pip.pypa.io/en/stable/topics/repeatable-installs/)。

### 12.2 启动与升级兼容

【建议】明确 `MCHECKER_PORT` 生效，并保持升级时实际使用的端口。审查源码实际监听 50002；不能为了让 README 的 50001 正确而静默修改所有已有部署。升级脚本记录当前端口并显式写配置，文档与真实默认统一。增加 host/data_root 的显式配置可以接受，但默认保持已有位置与行为，不要求用户搬数据。

保留 `NICEGUI_STORAGE_SECRET` 或已有 secret 文件；生成新 secret 时必须安全保存，失败停止启动而不是每次随机变化。secret 不进入源码、日志或示例；操作系统权限/Windows ACL 按部署账户收紧。业务目录位于安装目录之外时通过配置指定，不自动移动。

开发资源、缓存、`.nicegui` 用户状态、业务 data 和 secret 从发行包中排除。升级前后保留原部署会话目录；删除 Git 跟踪的开发会话文件不等于删除真实会话。当前仓库有跟踪状态文件这一事实不证明其中存在密码，应检查内容与历史，再决定是否需要轮换。

### 12.3 依赖取舍

【建议】优先继续使用 NiceGUI 已带的组件和本地资产、现有 APScheduler、标准库 JSON/XML/difflib/urllib/logging。现有成熟实现能满足的控件、调度和文本差异不要重写。新增依赖必须说明具体缺口、选定版本、维护状态、许可证、传递依赖、离线打包和可替换性。

若保真 XML 写回的已证实样本超出标准库实现能力，先评估成熟 XML 库是否更低风险，而不是自行手写完整 XML 解析/重写器。DL 的完整求根也须比较成熟数值实现与小范围自实现的真实成本。未验证之前不把 lxml、NumPy/SciPy 或第三方 UI 框架直接写入必装清单。不要仅因某库许可证名称看起来宽松就跳过具体发行方式审查。

现阶段无必要新增 CSS 框架。已有 tokens 足以统一外观；需要的静态图标/CSS 必须本地随包，保留相应许可声明。版本升级按离线包与回归验证处理，不使用只有下界的运行依赖范围作为发行锁。

## 13. 测试与验收策略

【建议】测试先隔离，再运行。统一临时数据根、机型上下文、NiceGUI 用户存储和单例清理，阻止 fixture 根外写入；不要在生产 checkout 直接运行可能使用默认 data 的测试。纯算法与存储测试不必依赖浏览器；现有测试应保留并修正无效断言。

| 测试层 | 必须验证的行为 | 验证方式 |
|---|---|---|
| 纯算法 | 类型、重复节点、根容器、完整 XML 字段、节点定位、DL 数值 | 小型确定性正反例，必要时性质测试 |
| 持久化 | 原子替换、并发读改写、同秒归档、损坏数据保护 | 故障注入和临时目录，不操作真实数据 |
| 应用操作 | 授权、profile、版本冲突、幂等、部分失败 | 调用实际服务函数并检查原文件哈希/状态 |
| 路由/会话 | 路径拒绝、临时所有者、旧 URL、跨机型记录 | HTTP/会话集成测试 |
| 浏览器 | 上传到结果下载的完整闭环、页面状态、键盘、真实版本选择 | NiceGUI 支持的测试机制或现有浏览器自动化，仅作开发依赖 |
| 发行 | 禁公网首次安装/首次访问、备份、升级中断、恢复 | 目标 OS/ABI 的隔离副本环境与网络记录 |
| 性能 | 冷/暖缓存、文件/节点/版本/用户规模、长会话释放 | 固定样本与步骤，记录环境和结果，不伪造百分比 |

关键反例至少包括：搜索/参数隐藏后的审阅节点；XML 重复兄弟与 200,000 字节阈值两侧；JSON 带特殊字符键与空根；过期建议；两个管理员重复提交；访客同名临时文件；default 与其他机型并行；半写缓存；同秒多次保存；重命名期间失败；HTTP 错误页更新；DL 分母 0、未设置范围和过滤后行映射；首次断网打开有图标/代码高亮的页面。

测试通过不能只看“没有抛异常”。验证被修改的是正确对象、未修改的原文件仍相同、状态与文件一致、没有非批准出网。P0 每项随修复 PR 带用例，最终发行验收覆盖组合流程。

## 14. 渐进迁移决策：Current → Target → Why → Migration

| Current | Target | Why：当前成本 | Migration：最小迁移 |
|---|---|---|---|
| 浏览器回退/隐式 profile | 操作捕获显式 profile、提交重新授权 | 错空间与越权风险 | 先修 default，再新增关键字参数和旧入口包装 |
| 各处 JSON 直接写 | 同一原子写/锁/损坏保护 | 截断与丢修改 | 不换存储格式，逐个替换 writer |
| 移动旧版后写当前 | 先保全旧版，再原子切当前 | 中断留下不可用当前文件 | 保留旧版命名读取，新增唯一后缀 |
| 展示树作为所有用途模型 | 原文快照 + 完整模型 + ViewTree | 丢字段、类型与节点身份 | 新增元数据/locator，不批量删除旧 path |
| 下标审阅直接改最新树 | hash+locator+类型/原值校验 | 过期或过滤后误改 | 旧建议先核验，生成结果保留原配置 |
| 页面拼接全部写流程 | 少量应用操作函数 | 同业务多份逻辑 | 每次迁移一个真实用例，旧页面入口不变 |
| 多页面树和标签逻辑 | 共用安全控件与工作区状态 | 差异行为与复制维护 | 先两处真实复用，再渐进替换 |
| 无锁依赖、端口硬编码 | 精确发行环境与显式配置 | 安装不可复现、升级失联 | 先导出现网基线再锁定，不顺手追新 |
| 启动时无恢复搬迁 | 明确备份、复制校验、启用和恢复 | 半迁移/兼容失败 | 原目录保留，混合状态显式处理 |

【建议】实施顺序是原地 P0 修复 → 稳定操作契约与测试 → 核心工作流 → 有证据的性能优化 → 非必要视觉。纯安全补丁不等待目录重构；有依赖的任务表示最终集成验收依赖，不要求所有子修复延后。

## 15. 复杂度削减与重大设计独立复核

### 15.1 复杂度削减结论（$razor）

根目标是可靠处理已有配置和协作流程，同时使下一次合理功能增加局部化。最低成功条件不是新增多少模块，而是同一输入产生正确结果、失败不破坏数据、权限和机型明确、离线可部署、同类页面不再复制核心逻辑。

| 候选设计 | 当前具体问题 | 不改的实际成本 | 最小整改与删去的复杂度 |
|---|---|---|---|
| 应用操作层 | 保存/授权/提交重复 | 新入口继续复制缺陷 | 少量函数/模块；删去全量 Service/Repository/DI |
| 源快照与 locator | 展示树有损、位置误用 | 错改与错误结果 | hash+原文定位；删去跨版本自动语义身份平台 |
| 可靠写入 | 中断、冲突、重名 | 数据丢失和难恢复 | 原子文件+机型锁；不马上加数据库 |
| 特定操作恢复清单 | 改名/迁移/审阅跨文件 | 半完成状态无法处理 | 明确步骤和恢复；不建设通用事务/工作流引擎 |
| 受控任务 | UI 阻塞和重入 | 断连、重复提交 | 有界执行器+状态；删去消息中间件 |
| 共用树/页面外壳 | 多份渲染和状态 | 新页面再复制故障 | 小控件组合；不做 schema 驱动低代码平台 |
| 离线包 | 没有可复现部署证据 | 无法在隔离环境交付 | 锁版本+wheelhouse+验收；不强制容器/Kubernetes |
| 性能优化 | 已见全量 DOM/扫描 | 数据增长后交互变慢 | 先测量、lazy/paging；不提前上索引服务 |

每项保留修改都可追溯到 F/T 编号。没有当前问题的扩展点不落地；新增文件数量、设计模式数量、技术栈“现代程度”不作为完成指标。

### 15.2 独立复核结论（$think-twice）

【建议】从“只做局部修补”重新审视：局部补授权、导入、DL 数值、缺失导入和端口就能消除一批严重缺陷，应立即做。但只补 if/except 无法解决审阅使用有损展示树、跨文件操作恢复和重复业务提交路径。因此仅这些边界需要结构改变，其余保持原型。

【建议】从“统一换成数据库/前后端重写”审视：数据库能提供更强的元数据事务，但不能自动保护文件原文、修复过滤后的节点定位或修复浏览器注入；全面前后端重写还要迁移现有全部 UI 状态与行为。当前优先收益不在这里。只有文件式恢复逻辑持续扩张、真实并发需求超出单进程边界或需要复杂查询时，才重新比较 SQLite 等方案与继续手写恢复的成本。

【建议】从“只给节点增加稳定 ID”审视：路径加 occurrence 无法跨版本处理重复节点插入、重排；hash+确定原文内 locator 才能先保证不误改。当前先拒绝跨版本自动应用，比建设猜测式自动合并更安全、更可验收。

【建议】从“为离线替换 NiceGUI”审视：上游本地资产机制说明更小路径是锁定部署版本、检查资产请求和打离线包。仅当实际验证发现无法在目标约束下消除运行外部依赖时，才比较替换；不能凭过时文档先认定框架不适合。

【建议】从“追求绝对零依赖”审视：复杂 XML 保真或数值求根若必须支持已存在样本，成熟实现可能比自写更可靠。克制不是拒绝所有依赖，而是只为已证明的缺口引入可验证、可离线、许可适配的最小能力。

## 16. 未来扩展边界与明确不做的事情

| 现在建立的边界 | 服务的下一步合理功能 | 现在不实现 |
|---|---|---|
| 显式 profile + Actor + 动作校验 | 新增管理动作、现有机型下新增操作 | 部门租户/RBAC 平台 |
| FileRef + 原文快照 | 新的文件来源或查看入口 | 广义对象存储服务 |
| 节点 locator + 类型契约 | 新增检查/建议展示 | 自动跨版本合并、全局节点注册中心 |
| 应用操作入口 | CLI/第二客户端复用已有业务 | 全量 REST 化 |
| 可版本化业务 JSON 与缓存 | 新增少量字段/计算参数 | schema registry/ORM |
| 工作区状态与共用树控件 | 新增同类页面 | 插件系统/动态 UI DSL |
| 有界任务与结果 | 新增单机批处理 | 分布式工作流/Celery |

P3 / Deferred 不创建执行 Issue：

| 事项 | 现在不做的原因 | 重新评估触发条件 |
|---|---|---|
| React/Vue 独立前端重写 | 当前问题可以局部解决，迁移成本高 | 实测 NiceGUI 交互/部署约束无法满足已确认需求 |
| 数据库/搜索索引迁移 | 缺乏规模与查询证据 | 单进程限制不够、跨文件恢复持续膨胀、已测查询成为瓶颈 |
| SSO/账号中心/部门隔离 | 用户没有确认这些产品目标 | 共享终端/不可信访问或明确组织权限需求出现 |
| 插件/微服务/工作流引擎 | 未验证复用和独立部署需求 | 多个真实独立扩展或长事务编排需求经评估成立 |
| 零停机代码热更新 | 与当前整改目标无直接必要性 | 有明确可用性指标，维护窗口无法接受且成本获批准 |
| Dashboard/指标大屏/主题系统 | 不服务核心文件工作 | 真实用户反复出现独立监控或显示需求 |
| 自动历史清除与全局保留策略 | 会引入不可逆数据风险 | 存储压力经测量且备份、保留政策由用户确认 |
| 跨设备草稿/通知中心 | 当前只需会话内连续性 | 实际多设备协作需求足以覆盖新增状态复杂度 |

## 17. 最终架构验收判断

完成整改不等于一次提交。P0 缺陷及离线发布门槛关闭后，MCChecker 才能被认为在已验证部署边界内可靠；P1 完成后，现有功能的可用性与可维护性才形成稳定基础；P2 样式收敛不应阻塞前面的可用版本。

新增一个合理的配置查看/检查页面时，应主要新增页面及必要纯逻辑，并复用 FileRef、授权、源快照、存储和反馈；新增一种已确认的后台操作时，应复用现有任务和提交接口。若仍需复制权限、路径、原文写入和整套树控件，说明边界整改没有通过验收。

本设计不承诺未知规模下永久无需重构。它要求：当前真实问题得到修复，下一阶段同类能力主要通过局部扩展完成；只有新的证据改变需求边界时，才重新设计。没有服务于其中任一目标的架构工作，不进入实施范围。

## 附录 A：源码证据索引

以下链接固定到审查提交，避免分支后续变化使证据漂移。上文引用均为静态代码依据，非现网测试结果。

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
