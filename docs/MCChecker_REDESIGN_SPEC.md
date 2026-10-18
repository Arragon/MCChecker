# MCChecker 重构设计说明书（架构 · 功能 · 前后端 · UI/UX · 实施 · 验收）

| 项 | 内容 |
|---|---|
| 文档状态 | 已批准的重构目标设计（Target Design），尚未实现。实现完成状态以代码与测试为准 |
| 基线代码 | 分支 `v1.0.1`，提交 `9ebc6a8`（运行代码与 `e47e54a` 相同） |
| 已确认决策 | 技术栈 B：Python 标准库 HTTP + JSON API + 原生 HTML/CSS/JS；浏览器基线：近 2 年 Chrome/Edge（CSS 变量 / Grid / ES2017）；完全兼容现有 `data/` 目录与 JSON 格式 |
| 配套文档 | [`MCChecker_FEATURE_CHANGES.md`](MCChecker_FEATURE_CHANGES.md)：逐条列出“原实现 → 新方案”的功能实现变更；前端模板：`app/web/` |
| 适用读者 | 负责实现的工程师或代码模型。每一节都给出“做什么 / 放在哪 / 怎么验收”，按 §15 的任务卡顺序执行即可 |

> 与旧文档的关系：`docs/MCChecker_ARCHITECTURE.md` §1、§17 曾写明“保留 NiceGUI 单体，不做独立前端重写”。该决定已由项目负责人在 2026-10-15 改为本文的方案 B（理由见 §1.2）。旧架构书中的数据安全、原子写、定位符、审阅校验、离线发布等原则**全部继续有效**，本文把它们落到新技术栈上；凡与本文冲突之处以本文为准。

---

## 0. 阅读与执行约定（给实现者）

1. **术语**：见附录 C。文中 `pid` = 机型（profile）ID，`FileRef` = (机型, 文件名, 版本)，“部署者”= 从服务器本机访问的用户，“管理员”= 在管理员名单中的人员。
2. **代码风格**：Python 必须能在 **3.9.11** 运行。禁止 `X | Y` 类型联合、`match`、`str.removeprefix` 以外的 3.10+ API（`removeprefix` 3.9 可用）、`dataclass(slots=True)`、`zip(strict=)`、`tomllib`。所有模块顶部加 `from __future__ import annotations`。
3. **零运行时依赖**：运行只依赖 Python 标准库。`requirements.txt` 清空为注释说明；`pytest` 仅为开发依赖（§14.4）。
4. **前端**：不引入任何框架、构建工具、CDN、Web 字体。使用经典 `<script>` 与 `window.MC` 命名空间（与模板一致）。动态内容一律用 `textContent`/`h()`，**禁止**把数据拼进 `innerHTML`。
5. **每个任务卡（§15）完成时必须**：运行该卡列出的测试；不得修改与该卡无关的文件；在 PR 描述中写明对应的 F 编号（§2.1）与 C 编号（变更说明）。
6. **“必须 / 应该 / 可以”**：必须 = 验收不通过即不合格；应该 = 有合理理由可偏离，需在 PR 说明；可以 = 可选。

---

## 1. 设计概述

### 1.1 产品定位

MCChecker 是部署在内网、面向设备配置人员的 **XML/JSON 配置文件查看、比较与变更审阅工具**。核心工作流：

```text
找到文件 → 读懂配置（结构树/参数范围）→ 比较变化（历史版本/修改记录/上传文件）
        → 提出修改建议 → 管理员审阅并生成新文件 → 下载取走
辅助：收藏速览、全局搜索、更新源与定时拉取、变量绑定、DL 多项式计算、多机型隔离
```

它是高频、信息密集的工程工具，不是运营 Dashboard。设计目标按优先级：**正确（不误改、不丢数据）> 快（大文件流畅）> 清楚（任何人都知道自己能做什么）> 好看（安静、克制）**。

### 1.2 为什么改技术栈（决策记录 ADR-01）

| 维度 | NiceGUI 现状 | 方案 B |
|---|---|---|
| 运行依赖 | `nicegui` + `apscheduler` 及传递依赖约 20 个包（fastapi/starlette/uvicorn/pydantic/…），需联网安装或手工准备 wheelhouse | 0 个，Python 3.9.11 标准库即可 |
| 多用户内存 | 每个浏览器会话在服务端保存完整 UI 组件树，内存随会话线性增长；大文件树每节点生成多个服务端元素 | 服务端无 UI 状态；树只在浏览器虚拟渲染 |
| 交互延迟 | 每次点击经 WebSocket 回服务端重绘；页面有 0.3 s / 1 s 定时器轮询 | 交互全部本地完成，只有数据读写走 HTTP |
| 目标环境 | 当前代码在 3.9 下**无法导入**（`storage.py:355` 使用 `str \| None`，已实测 `TypeError`） | 设计即以 3.9.11 为准 |
| 代价 | — | `app/pages/*`（约 4 500 行）全部重写；`app/core` 大部分保留 |

结论：方案 B 同时满足“零外部依赖、离线、性能、多用户”四个硬约束，代价集中在 UI 层，而 UI 层本来就需要彻底重做。

### 1.3 硬约束

| 编号 | 约束 | 验证方式 |
|---|---|---|
| H1 | Python 3.9.11，Windows 10/11 与 Linux x86_64 均可运行 | §16.6 在 3.9.11 下全量测试 |
| H2 | 运行时零第三方依赖；无公网访问也能安装、启动、首次访问 | §16.7 断网首装首访 |
| H3 | 现有 `data/` 目录、文件名与 JSON 字段**原样可读**；新增字段一律可选；旧版本不认识的新字段不影响旧版读取 | §16.5 兼容测试 |
| H4 | 单进程、单调度线程写同一个 data 根；不支持多进程/多实例共写 | 启动时写入 `data/.lock` 并检测（§3.5） |
| H5 | 50 个并发浏览器、单文件 30 万节点内，交互保持流畅（§12 预算） | §16.4 性能测试 |
| H6 | 现有全部功能（§2.1 F01–F28）在新版本中均有入口且行为不退化 | §16.2 功能用例 |

### 1.4 明确不做

- 不引入数据库、Redis、消息队列、Celery、ORM、前端框架、构建工具、Node 运行时。
- 不做账号密码/SSO；继续使用内网 IP 轻量识别（§4，明确告知它不是强认证）。
- 不做 Dashboard、大屏、暗色主题、多语言（文案集中管理，未来可扩展）。
- 不做多级审批、自动跨版本合并节点、实时协同编辑。
- 不做在线升级器、零停机热更新。

### 1.5 交付物与仓库位置

| 交付物 | 位置 | 说明 |
|---|---|---|
| 本设计说明书 | `docs/MCChecker_REDESIGN_SPEC.md` | 架构、接口、UI/UX、实施与验收 |
| 功能实现变更说明 | `docs/MCChecker_FEATURE_CHANGES.md` | 每项与旧实现不同的地方：原因、兼容、验收 |
| 前端模板（即生产前端的起点） | `app/web/` | 可直接打开 `app/web/template.html` 离线演示全部页面；`assets/js/mock/` 为浏览器内 mock API，实现了 §7 全部接口 |
| 前端单元测试页 | `app/web/tests/index.html` | 无依赖，打开即运行，标题显示 `ALL PASSED (n)` |

模板的使用方式见 §8.9。

---

## 2. 现状审计（证据）

### 2.1 现有功能清单（必须全部保留）

下表是新版本的**功能验收基线**。“来源”指当前实现文件；“新位置”指新版本中的入口（详见 §9.8）。

| 编号 | 功能 | 现有行为要点 | 来源 | 新位置 |
|---|---|---|---|---|
| F01 | 本地上传导入 | 选择文件 → 解析校验 → 部署者保存（同名先归档旧版）；访客“仅会话可见”（实际无法查看，缺陷） | `pages/home.py::_show_upload_dialog` | 侧栏「配置文件 +」→ 导入对话框 · 本地文件 |
| F02 | 链接下载导入 | 输入 URL + 文件名 → 服务端下载（3 次重试、30 s 超时）→ 解析 → 同 F01 | `home.py::_show_url_dialog`、`core/downloader.py` | 导入对话框 · 从链接下载 |
| F03 | 文件列表 | 文件名、映射文件显示更新时间、单文件下载、管理员删除；每秒轮询 | `home.py::_render_sidebar*` | 侧栏「配置文件」 |
| F04 | 结构树查看 | XML 元素/属性（`@name`）/混合文本（`#text`）；JSON 对象/数组（`[i]`）/标量；`param_*` 节点把描述/编辑权限内联、范围（default/min/max/incMin/incMax）可展开；全部展开/折叠 | `pages/viewer.py`、`core/parser.py` | 文件页 · 结构 |
| F05 | 文件内搜索 | 按名称/值/收藏备注过滤，保留祖先与命中节点子树 | `core/searching.py`、`viewer.py` | 结构页工具栏筛选框 |
| F06 | 收藏 | 星标收藏叶子或父节点（父节点保存子树快照）；被父级覆盖的子节点提示先取消父级；收藏对本机型所有用户共享 | `viewer.py::_toggle_favorite`、`storage.add_favorite` | 结构树星标 / 详情面板「收藏」 |
| F07 | 配置速览 | 收藏按文件分组显示实时值；节点消失显示“未检测到，请检查”；源文件不存在归入“已删除”组；备注编辑；单项取消、按文件清空 | `home.py::_render_overview_panel`、`core/favorites_live.py` | 「速览」 |
| F08 | 修改建议（备注） | 对有值节点提交建议值，自动记录 IP 与姓名（IP 对应表）；节点旁显示已有建议 | `viewer.py::_open_edit_remark_dialog`、`storage.add_edit_remark` | 详情面板「修改建议」 |
| F09 | 审阅修改 | 仅管理员；每个待审节点选一条建议或“不通过”；生成 `原名_YYYYMMDD_HHMMSS.ext` 新文件存入 configs；更新建议状态；提供下载 | `pages/review.py`、`core/reviewing.py` | 「审阅修改」 |
| F10 | 全局搜索 | 搜索全部当前文件，按文件分组显示过滤后的树与命中数 | `pages/search.py` | 「全局搜索」+ Ctrl+K 命令面板 |
| F11 | 版本历史 | 当前版本 + 归档列表（日期、大小、变更摘要 +/−/~/绑定）；下载、查看归档、与当前对比 | `pages/history.py`、`storage.list_archived_versions/ensure_archive_diffs` | 文件页 · 历史版本 |
| F12 | 版本对比 | 四种模式：当前 vs 历史、历史 vs 历史、上传 vs 当前、上传 vs 历史；结构化差异 + unified diff（预览 200 行、全文弹窗、复制） | `pages/comparison.py`、`core/differ.py` | 文件页 · 对比 |
| F13 | 修改记录 | 每个文件可配置 `record_url`；拉取的记录文件列表与相邻记录差异；部署者手动刷新；独立页 `/record_view` 解析显示 | `pages/records.py`、`pages/record_view.py`、`scheduler.run_single_record_update` | 文件页 · 修改记录 |
| F14 | 文件下载 | 当前版本与历史版本下载，保留原文件名/版本名；失败提示并可重试；路由 `/api/file-download` | `pages/file_downloads.py` | 各处「下载」；旧路由保留兼容 |
| F15 | 更新源管理 | 映射表（文件名、下载链接、修改记录链接）增删改；改名可迁移本地文件与归档；单条刷新；全量更新与状态徽标 | `pages/management.py`、`storage.*config_mapping*` | 设置 · 更新源 |
| F16 | 定时更新 | 启用开关 + 间隔小时（“天”单位未接入）；每机型一个周期任务，执行全量更新（含修改记录）；每 24 h 清理归档解析缓存 | `core/scheduler.py`、`management.py` | 设置 · 定时更新 |
| F17 | IP 对应表 | IP → 姓名，增删改；按机型保存 | `management.py`、`storage.*ip_mapping*` | 设置 · 人员与权限 |
| F18 | 管理员列表 | 姓名列表，增删改；按机型保存 | `management.py`、`storage.*admin_user*` | 设置 · 人员与权限 |
| F19 | 变量绑定 | 绑定组 = 名称 + 若干（文件, 路径）；对比时标注绑定组 | `pages/bindings.py`、`storage.*binding*`、`differ` | 设置 · 变量绑定；对比结果紫色标签 |
| F20 | 工具菜单 | 名称/说明/地址增删改；右侧工具箱点击新窗口打开 | `pages/tools.py`、`home.py::_render_tools_sidebar` | 设置 · 工具菜单；侧栏「外部工具」 |
| F21 | DL 快捷计算 | 三个计算项（PP 能量/RP 能量/RP 脉宽）；系数 k1–k8、b；PP 额外“传输系数/功率转换系数”；x/y 范围；正向/反向；多解检测与提示；多解时手选生成唯一解表；从配置文件按字段映射提取系数并可刷新；修改/保存/取消 | `pages/dltool.py`、`core/dltool.py` | 「DL 计算」 |
| F22 | 多机型 | 切换、新增（可从当前复制）、重命名、删除（可选同时删数据）；所有业务数据按机型隔离 | `home.py`、`core/device_models.py`、`storage.copy/delete_profile_data` | 顶栏机型切换；设置 · 机型管理 |
| F23 | 标签页工作区 | 多标签、上限 15（淘汰最早非活动）、关闭相邻激活、返回历史栈、按机型持久化 | `home.py`、`core/tab_manager.py`、`core/tabs_state.py` | 标签栏（§8.4） |
| F24 | 身份识别 | 部署者 = 服务器本机 IP；管理员 = IP 对应姓名在管理员名单；顶栏身份徽标 | `utils/auth.py` | 顶栏身份按钮 + 身份说明弹层 |
| F25 | 删除文件 | 管理员删除文件及其归档、记录、收藏、绑定引用、建议 | `home.py::_show_delete_file_dialog`、`storage.delete_config_file` | 文件「更多」菜单 |
| F26 | 旧布局迁移 | 启动时把 `data/*.json`、`data/configs|archive|cache` 迁到 `data/profiles/default/` | `storage.ensure_profile_layout` | 启动迁移（§5.6，改为复制-校验） |
| F27 | 解析缓存 | 按源路径 + mtime/size 缓存解析树；归档缓存 7 天 / 500 条清理 | `core/parse_cache.py` | 文档缓存 v2（§6.5） |
| F28 | 启动与端口 | `python main.py`；`MCHECKER_PORT` 已定义但实际固定 50002 | `run.py` | §3.6 配置；默认端口仍为 50002 |

### 2.2 关键问题（按用户影响排序）

现有版本截图保存在 `/opt/cursor/artifacts/before/`（运行于本机，部署者身份）。

| 级别 | 问题 | 证据 | 本设计的解决位置 |
|---|---|---|---|
| P0 | 目标环境 Python 3.9.11 下无法启动 | `python3.9 -c "import app.core.storage"` → `TypeError: unsupported operand type(s) for \|`（`storage.py:355`、`viewer.py:30` 等） | H1、§0.2 |
| P0 | 审阅可能改错节点：建议用“过滤后树的子节点下标”定位，审阅时套到最新完整树 | `viewer.py:207,400`（`make_node_key` 基于过滤后 children 下标）、`reviewing.get_node_by_key` | §6.4 定位符、§6.7 |
| P0 | 审阅输出从展示树重建：大 XML（≥200 KB）解析时丢弃白名单外属性，生成文件会丢属性 | `parser.py:12-13,108`、`reviewing.serialize_tree` | §6.1、§6.7 |
| P0 | 写入非原子、无锁；先 move 旧文件再写新文件；损坏 JSON 读成空列表后被覆盖 | `storage._save_json`、`save_config_file:534-538`、`_load_json` | §5.4 |
| P0 | XSS：配置值/标签/备注直接拼进 `ui.html(sanitize=False)`；工具 URL 拼进 `window.open` | `viewer.py:266-276`、`home.py:258,567` | §11.3、§8.1 |
| P0 | 授权缺口：机型增删改、DL 保存无部署者校验；部署者显示为“游客” | `home.py:1004-1100`、`dltool.py:69-95`、截图 `01-home-overview.png` 顶栏显示“游客 127.0.0.1 127.0.0.1” | §4 |
| P0 | 更新不校验：HTTP 200 错误页也会覆盖当前配置；重复内容也归档 | `scheduler.run_single_update:134-140` | §10.2 |
| P1 | 导航分散：顶部 6 个纯图标按钮 + 标签 + 返回栈 + 页内链接；同一文件开出“文件/历史/对比/记录”4 个标签 | 截图 `02-file-viewer.png`、`05-settings.png`（实为变量绑定页） | §9.2 |
| P1 | 大文件卡顿：每个节点生成多个服务端元素，默认全部展开，属性逐行展开，行高 30 px | 截图 `02-file-viewer.png` | §8.7、§12 |
| P1 | 修改操作只在悬停时出现、走弹窗；无键盘路径；值不可选中复制 | `style.css` 树 `user-select:none`、`viewer.py:308-323` | §9.8.3 详情面板 |
| P1 | 访客导入“仅会话可见”实际打不开（只用文件名开标签，指向服务器同名文件或不存在） | `home.py:1163-1170` | §7.4 临时文件 |
| P1 | 历史“与当前对比”没有把选中的版本传给对比页 | `history.py:158-181` | §9.8.4 |
| P1 | 修改记录页每个客户端自己定时拉取，与调度器重复；`/record_view` 链接不带机型 | `records.py:85-87,148-151` | §10.3、C13 |
| P1 | 右侧工具箱常驻占宽但内容稀少；状态（加载/空/失败）表现不一致 | 截图 `01`、`06` | §9.3、§9.6 |
| P2 | 8 色彩虹缩进线、卡片 hover 阴影等视觉噪声 | 截图 `02` | §9.4 |

---

## 3. 总体架构

### 3.1 运行形态

```text
浏览器（Chrome/Edge）                              服务器（单进程 Python 3.9.11）
┌──────────────────────────────┐   HTTP/1.1      ┌───────────────────────────────────────────┐
│ app/web 静态前端（原生 JS）   │ ─────────────▶  │ ThreadingHTTPServer（每请求一线程）        │
│  · 路由 / 标签 / 虚拟树       │  JSON / 原文字节 │  ├─ 静态文件：/、/assets/*（ETag/gzip）     │
│  · localStorage：标签、草稿   │ ◀─────────────  │  ├─ /config.js（运行配置）                  │
│  · 10 s 修订号轮询            │                 │  ├─ /api/v1/*  JSON API（§7）              │
└──────────────────────────────┘                 │  └─ 旧路由兼容：/api/file-download、/record_view │
                                                  │                                           │
                                                  │ services：文档 / 更新 / 审阅 / 临时文件 / 任务 │
                                                  │ core：parser / differ / searching / dltool  │
                                                  │ storage：路径校验 / 原子写 / 机型锁 / WriteGate │
                                                  │ 后台：Scheduler 线程 + TaskRunner 线程池     │
                                                  └───────────────────────┬───────────────────┘
                                                                          ▼
                                                     data/（与现有完全兼容，§5）
```

### 3.2 目标目录结构

```text
main.py                      # 兼容入口：from run import main; main()
run.py                       # 唯一启动入口：解析配置 → 初始化 → 启动 HTTP 与调度
requirements.txt             # 运行时无第三方依赖（只保留注释）
requirements-dev.txt         # pytest（开发/测试用，可离线 wheel 安装）
app/
  __init__.py                # __version__ = "2.0.0"
  config.py                  # Settings 数据类 + load_settings()（§3.6）
  core/                      # 纯领域逻辑，不 import server/services
    parser.py                # 旧树接口（保留）+ to_compact()（新）
    differ.py                # compare_versions()（旧，保留）+ compare_documents()（新）
    searching.py             # 旧过滤（保留）+ search_compact()（新）
    favorites_live.py        # 改为基于 compact 文档解析实时值
    reviewing.py             # apply_edits()（新：在原文上修改并校验）；旧函数保留至迁移完成
    dltool.py                # 纯计算：poly_eval / find_roots / forward / reverse / multi_check
    downloader.py            # 安全下载（scheme、大小、超时、重试、重定向校验）
    storage.py               # 兼容门面：现有函数签名不变，内部改用 fsutil + locks
    fsutil.py                # 新：atomic_write_*、read_json（区分损坏）、validate_filename、resolve_within
    locks.py                 # 新：profile_lock()、registry_lock、WriteGate
    device_models.py         # 机型注册表（改用 fsutil 原子写）
    parse_cache.py           # 旧缓存（只读兼容，逐步废弃）
    tab_manager.py           # 保留（测试仍覆盖）；前端有等价实现
  services/                  # 应用操作：授权后的业务用例，组合 core + storage
    context.py               # Actor、RequestContext
    auth.py                  # 身份解析（替代 utils/auth.py 中的 NiceGUI 依赖）
    documents.py             # 文档加载/缓存、导入、删除、下载
    temp_store.py            # 访客/临时文件
    updates.py               # 更新源下载 → 校验 → 提交；修改记录拉取
    review.py                # 建议提交、审阅提交
    tasks.py                 # TaskRunner + 任务注册表
    scheduler.py             # 标准库调度线程（替代 APScheduler）
    recovery.py              # 删除/迁移的恢复区与清单
  server/
    http.py                  # MCServer、Handler、静态文件、gzip、错误映射
    router.py                # 路由表与路径参数
    api_*.py                 # 按领域分组的 handler（§7）
    legacy.py                # /api/file-download、/record_view 兼容
  web/                       # 前端（由模板演进而来，§8）
tests/
  （现有测试保留）+ test_fsutil.py / test_compact.py / test_review_apply.py / test_api_*.py …
```

`app/pages/`、`app/pages/theme.py`、`app/static/css/style.css`、`app/utils/auth.py` 中的 NiceGUI 部分在 §15 的 T-30 删除。`app/utils/helpers.py` 保留。

### 3.3 分层与依赖规则（必须）

```text
server  ──▶ services ──▶ core
   │            │          ▲
   └────────────┴──▶ core.storage / fsutil / locks
```

- `core` 不得 import `services`、`server`、任何 UI；不得读取请求、IP、cookie。
- `services` 每个公开函数第一个参数是 `ctx: RequestContext`（或后台任务的 system 上下文），在函数入口调用 `authorize(ctx, action)`。
- `server` 只做：解析请求 → 构造 `ctx` → 调用一个 service 函数 → 序列化响应。**不得**在 handler 里直接写文件。
- 简单只读查询（如列出工具）可以由 handler 直接调用 `storage` 读取函数，但必须在 `with storage.use_profile(ctx.profile_id):` 内。
- `differ` 不再 `from .storage import get_bound_paths`；改由调用方传入 `bindings` 快照（消除 `storage ↔ differ` 循环依赖）。

### 3.4 请求生命周期

```text
1. Handler.do_GET/POST/PUT/PATCH/DELETE
2. router.match(method, path) → (handler_fn, path_params)；无匹配 → 404 NOT_FOUND
3. 读取 body：Content-Length 必须存在且 ≤ 上限（JSON 1 MB，原文上传 settings.upload_max_bytes）→ 否则 413 TOO_LARGE
4. ctx = RequestContext(actor=auth.resolve_actor(client_ip, headers), profile_id=path_params.get("pid"), client_id=cookie mc_cid, request_id=uuid4)
5. 若路径含 {pid}：device_models.exists(pid) 否则 404（不回退 default）
6. with storage.use_profile(pid): result = handler_fn(ctx, params, query, body)
7. 成功：200/201/202 JSON（UTF-8，ensure_ascii=False）；>1 KB 且客户端接受 gzip → gzip
8. 异常：AppError(code, message, status, detail) → {"error": {...}}；其他异常 → 500 STORAGE_FAILURE，日志记录堆栈，响应不含堆栈
9. 访问日志：method path status duration_ms actor_ip profile request_id（不记录 query 中的 URL 凭据，§13）
```

### 3.5 并发模型

| 组件 | 实现 | 规则 |
|---|---|---|
| HTTP | `http.server.ThreadingHTTPServer`，`daemon_threads=True`，`request_queue_size=128`，`Handler.timeout=60` | 每请求一线程；handler 内不得长时间阻塞（>2 s 的工作必须交给 TaskRunner） |
| 机型锁 | `locks.profile_lock(pid)` 返回该机型唯一的 `threading.RLock` | 任何“读-改-写”权威 JSON 或配置文件必须在锁内；锁内禁止网络 I/O 与大 diff |
| 注册表锁 | `locks.registry_lock` | `device_models.json` 读改写 |
| WriteGate | `locks.write_gate`：`enter_write()`/`exit_write()` 计数 + `maintenance()` 上下文 | 备份/恢复时进入维护态：新写请求返回 503 `MAINTENANCE`，读继续 |
| 任务 | `services.tasks.TaskRunner(max_workers=4)`（`ThreadPoolExecutor`） | 相同 `(pid, type, target)` 去重返回 409 `BUSY`；任务内部自己加机型锁 |
| 调度 | `services.scheduler.Scheduler`：1 个 daemon 线程 | 只负责到点向 TaskRunner 提交任务，不自己执行业务 |
| 单实例 | 启动时以独占方式创建 `data/.mcchecker.lock`（写入 pid+端口+启动时间）；若存在且记录的进程仍存活 → 拒绝启动并提示；进程退出时删除 | 满足 H4 |

ContextVar 说明：`storage` 的 `_profile_var` 是 ContextVar，每个请求线程有独立上下文，`use_profile()` 在线程内生效。**必须删除** `get_active_profile()` 中读取 NiceGUI `app.storage.user` 的回退分支。

### 3.6 配置项

优先级：命令行参数 > 环境变量 > 默认值。`app/config.py` 提供：

```python
@dataclass(frozen=True)
class Settings:
    host: str = "0.0.0.0"
    port: int = 50002                 # 与现网实际端口一致（run.py 当前固定 50002）
    data_dir: str = "<仓库根>/data"
    web_dir: str = "<仓库根>/app/web"
    trusted_proxies: Tuple[str, ...] = ()
    upload_max_bytes: int = 50 * 1024 * 1024
    download_timeout_s: int = 30
    download_retries: int = 3
    download_allow: Tuple[str, ...] = ()   # 为空=不额外限制（仍执行 §11.2 的禁止规则）
    temp_ttl_hours: int = 24
    tab_limit: int = 15
    max_workers: int = 4
    log_level: str = "INFO"

def load_settings(argv: Optional[List[str]] = None) -> Settings: ...
```

| 环境变量 | CLI | 说明 |
|---|---|---|
| `MCHECKER_HOST` | `--host` | 监听地址 |
| `MCHECKER_PORT` | `--port` | 端口；非法值启动失败并打印原因（不静默回退） |
| `MCHECKER_DATA_DIR` | `--data-dir` | 数据根；默认仓库内 `data/`（兼容旧部署） |
| `MCHECKER_TRUSTED_PROXIES` | — | 逗号分隔 IP；只有来自这些地址的请求才读取 `X-Forwarded-For` |
| `MCHECKER_UPLOAD_MAX_MB` | — | 上传上限（MB） |
| `MCHECKER_DOWNLOAD_ALLOW` | — | 逗号分隔的主机名、IP 或 CIDR；非空时下载目标必须命中 |
| `MCHECKER_LOG_LEVEL` | — | DEBUG/INFO/WARNING |

`NICEGUI_STORAGE_SECRET` 与 `data/nicegui_storage_secret.txt` 不再使用；**不删除**该文件（用户数据目录内文件一律不主动删除）。启动日志打印：版本、Python 版本、监听地址、data 根、调度状态、已加载机型数、待恢复操作数。

---

## 4. 身份与权限

### 4.1 Actor 解析（`services/auth.py`）

```python
@dataclass(frozen=True)
class Actor:
    ip: str
    name: str              # IP 对应表中的姓名；未登记时等于 ip
    registered: bool
    is_deployer: bool
    is_admin: bool
    is_system: bool = False  # 调度任务

def client_ip(handler) -> str:
    """直连取 socket 对端地址；仅当对端在 trusted_proxies 中时，取 X-Forwarded-For 最左侧合法 IP。"""

def resolve_actor(ip: str, profile_id: str) -> Actor:
    deployer = ip in deployer_ips()        # 127.0.0.1、::1、本机所有接口地址（启动时计算并缓存，同现有逻辑）
    with storage.use_profile(profile_id):
        name = storage.resolve_person_by_ip(ip) or ip
        admin = storage.is_admin_user(name)
    return Actor(ip, name, name != ip, deployer, admin)
```

- IP 对应表与管理员名单**保持按机型保存**（与现状一致，架构书 §2.9）。因此同一个人在不同机型可能权限不同，`/session?profile=` 返回的是该机型下的身份。
- 角色标签：`部署者 · 管理员` / `部署者` / `管理员` / `成员`（已登记非管理员）/ `访客`（未登记）。修复“部署者显示为游客”。
- IPv4 映射的 IPv6（`::ffff:10.1.2.3`）先规范化为 IPv4 再匹配。

### 4.2 权限矩阵（服务端强制；前端仅用于禁用按钮与解释）

| 操作 | 访客 | 成员 | 管理员 | 部署者 | 接口 |
|---|:-:|:-:|:-:|:-:|---|
| 查看文件/历史/记录、对比、搜索、下载 | ✓ | ✓ | ✓ | ✓ | GET … |
| 临时上传/临时链接导入（仅本人可见） | ✓ | ✓ | ✓ | ✓ | `POST /temp`、`POST /import-url {persist:false}` |
| 收藏增删、收藏备注（共享，**保持现状**） | ✓ | ✓ | ✓ | ✓ | `/favorites` |
| 提交修改建议 | ✓ | ✓ | ✓ | ✓ | `POST /remarks` |
| DL 试算（用草稿参数计算） | ✓ | ✓ | ✓ | ✓ | `POST /dl/{item}/compute`、`/extract` |
| 审阅建议 | | | ✓ | ✓¹ | `/review*` |
| 删除文件 | | | ✓ | ✓¹ | `DELETE /files/{name}` |
| 保存上传/链接文件到服务器 | | | | ✓ | `PUT /files/{name}`、`import-url {persist:true}` |
| 更新源、定时、IP 表、管理员、绑定、工具、DL 保存 | | | | ✓ | 设置类写接口 |
| 立即更新 / 拉取修改记录 | | | | ✓ | `POST /tasks` |
| 机型新增/重命名/删除 | | | | ✓ | `/profiles` 写接口 |

¹ 部署者需同时是管理员（与现状一致：审阅与删除只认管理员名单）。表中“部署者”列仅表示部署者身份本身可做的操作。

**提交点再校验**：所有写接口在执行前重新解析 Actor（每个请求都是新解析，天然满足“撤权后旧页面的按钮也会被拒绝”）。

### 4.3 前端如何表达权限

- 不可用的写操作按钮 `disabled` 并带 `title` 说明原因；设置页顶部显示只读横幅“只有部署者（在服务器本机访问）可以修改”。
- 身份按钮弹层列出 4 项能力（✓/✕）与当前识别地址（可复制），说明“把此地址提供给部署者登记”。这是唯一显示 IP 的地方（顶栏不显示 IP，符合 know-how）。
- 前端判断仅用于体验，**不得**作为安全边界。

---

## 5. 数据模型与持久化（完全兼容）

### 5.1 目录布局

```text
data/
  device_models.json                 # [{id, name}]；default 永远存在
  nicegui_storage_secret.txt         # 旧文件，保留不动、不再使用
  .mcchecker.lock                    # 新：单实例锁（运行期）
  profiles/<pid>/
    configs/<name>                   # 当前版本原文（权威）
    archive/<name>/<archive_name>    # 历史版本原文（权威）
    archive/<name>/<archive_name>.diff.json   # 旧派生摘要（兼容读取；新版不再写入此处）
    records/<name>/record_YYYYMMDD_HHMMSS<ext> # 修改记录原文（权威）
    config_mapping.json  favorites.json  bindings.json  edit_remarks.json
    tools.json  schedule.json  ip_mapping.json  admin_users.json  dltool_config.json
    update_status.json               # 新（可选）：各更新源最近一次结果
    cache/parse_tree/                # 旧缓存，只读兼容，清理任务可删除
    cache/doc_v2/<sha256>.json.gz    # 新：compact 文档缓存（可丢弃）
    cache/diff_v2/<key>.json         # 新：差异摘要缓存（可丢弃）
  temporary/<token>/{meta.json, content}      # 新：临时文件（§7.4）
  recovery/<YYYYMMDD_HHMMSS>_<op>_<pid>_<name>/{manifest.json, …}  # 新：删除/迁移前的备份
  logs/mcchecker.log(.1…5)           # 新：滚动日志（RotatingFileHandler，5×10 MB）
```

`default` 机型的历史兼容回退（`data/configs`、`data/archive`、`data/*.json` 存在而 `profiles/default/...` 不存在时读旧位置）**保留**，规则同现有 `get_configs_dir` 等函数。

### 5.2 JSON 文件结构（现有字段不变；“新增”字段均可选）

所有时间字段格式统一为本地时间 `"YYYY-MM-DD HH:MM:SS"`（与现状一致）。

**device_models.json**
```json
[{"id": "default", "name": "默认机型"}, {"id": "dl-200-line2", "name": "DL-200 二号线"}]
```
- `id` 规则：`[A-Za-z0-9_-]+`，由名称 slug 生成（现有 `_slugify` + `_unique_id`）；不得为 `.`/`..`。

**config_mapping.json**
```json
[{"name": "scenario_a.xml", "url": "http://10.20.1.15/cfg/scenario_a.xml", "record_url": "http://…"}]
```

**update_status.json**（新增）
```json
{"scenario_a.xml": {"status": "success|unchanged|skipped|download_failed|rejected|error",
                    "label": "已更新", "at": "2026-10-15 10:08:00", "reason": "…", "archived": "scenario_a_20261015.xml"}}
```

**favorites.json**
```json
[{"path": "root/CONFIG_ROOT/SCENARIO_BLOCK/input/param_02", "label": "param_02", "value": "12",
  "source_file": "scenario_a.xml", "note": "脉冲计数上限",
  "children": [{"label": "@type", "value": "int32", "path": "root/…/param_02@type", "children": []}],
  "added_at": "2026-10-01 09:00:00"}]
```
- `path` 是**旧版节点 ID**（§6.2），必须继续按旧规则生成，保证旧收藏可解析。`value` 是收藏时的值快照（速览会显示“收藏时为 x”）。

**bindings.json**
```json
[{"group_name": "脉冲计数", "variables": [{"file": "scenario_a.xml", "path": "scenario_a.xml/CONFIG_ROOT/SCENARIO_BLOCK/input/param_02"}]}]
```
- `path` 是“值路径”：`<根标签>/<标签>/<标签>…`，根标签为文件名（旧 `parser.get_all_values` 的格式）。匹配规则见 §6.6。

**edit_remarks.json**（建议与审阅记录）
```json
[{"id": "9f…", "source_file": "scenario_a.xml", "node_key": "0.0.1.3", "node_path": "root/…/param_03",
  "node_label": "param_03", "original_value": "5.00e+05", "proposed_value": "6.00e+05",
  "node_type": "element", "tree_type": "xml", "actor_ip": "10.12.3.21", "actor_name": "李娜",
  "actor_display": "李娜", "status": "pending|approved|rejected", "reviewed_by": "", "reviewer_ip": "",
  "reviewed_at": "", "generated_file": "", "created_at": "2026-10-15 11:02:00",
  "locator": "/CONFIG_ROOT[1]/SCENARIO_BLOCK[1]/input[1]/param_03[1]",
  "source_sha256": "e3b0…", "schema_version": 2}]
```
- 新增：`locator`、`source_sha256`、`schema_version`。新建议 `node_key` 写空字符串（字段保留以兼容旧读取）。
- 无 `locator` 的旧建议称为 **legacy 建议**，审阅规则见 §6.7。

**tools.json** `[{"name", "description", "url", "added_at"}]`；新版保存时要求 `name` 唯一、`url` 为 http/https。

**schedule.json** `{"enabled": false, "interval_hours": 24, "last_run": {"at", "success", "unchanged", "failed", "skipped"}}`；`last_run` 为新增可选字段。

**ip_mapping.json** `[{"ip", "name", "updated_at"}]`；**admin_users.json** `[{"name", "updated_at"}]`。

**dltool_config.json**（字段与现状完全相同）
```json
{"editing": false, "items": {"pp_energy": {"name": "计算PP能量", "trans_coeff": 1.0, "power_conv_coeff": 1.0,
  "coeffs": {"k1": 0.0, "…": 0.0, "k8": 0.0, "b": 0.0},
  "binding": {"source_file": "", "variable_path": "", "field_map": {"m1": "k1"}},
  "x_min": null, "x_max": null, "y_min": null, "y_max": null}, "rp_energy": {…}, "rp_width": {…}}}
```
- `editing` 字段读取时忽略、写入时保持 `false`（编辑态改为前端本地草稿，C15）。`null` 表示未设置，**不得**再用 `value or 0.0` 把空值写成 0。

### 5.3 原文文件命名（兼容）

| 类型 | 规则 | 冲突处理 |
|---|---|---|
| 归档 | `<stem>_<YYYYMMDD><.ext>`（`_add_date_suffix`） | 已存在 → `<stem>_<YYYYMMDD>_<HHMMSS><.ext>` → 仍存在 → 追加 `_2`、`_3`…（新增，旧版同秒会覆盖） |
| 审阅生成 | `<stem>_<YYYYMMDD>_<HHMMSS><.ext>`，存入 `configs/` | 同上追加 `_2`… |
| 修改记录 | `record_<YYYYMMDD>_<HHMMSS><ext>`，ext 取自 record_url 路径（≤10 个字母数字）否则 `.txt` | 同上 |

所有新建文件使用独占创建（`os.open(..., O_CREAT | O_EXCL)`）获得唯一名后再写入。

### 5.4 写入可靠性（`core/fsutil.py`，必须）

```python
class CorruptDataError(Exception): ...

def atomic_write_bytes(path: str, data: bytes) -> None:
    """同目录 tempfile.mkstemp(prefix='.'+basename+'.', suffix='.tmp') → write → flush → os.fsync → os.replace。
    失败时删除临时文件并抛出；Windows 上 os.replace 遇到 PermissionError 时重试 5 次（间隔 50 ms），仍失败则抛出。"""

def atomic_write_json(path: str, obj) -> None:
    """json.dumps(obj, ensure_ascii=False, indent=2) 后调用 atomic_write_bytes。"""

def read_json(path: str, default):
    """文件不存在 → 返回 default 的深拷贝；存在但 JSONDecodeError/UnicodeDecodeError → 抛 CorruptDataError(path)。"""

def validate_filename(name: str) -> str:
    """去除首尾空白后校验：非空；长度 ≤ 180；不含 / \\ : * ? \" < > | 与 \\x00-\\x1f；不是 . 或 ..；
    不以 . 或空格结尾；不是 Windows 保留名（CON PRN AUX NUL COM1-9 LPT1-9，忽略扩展名与大小写）。
    允许中文等 Unicode。返回规范化后的名字；不合法抛 AppError(INVALID_INPUT)。"""

def resolve_within(root: str, *parts: str) -> str:
    """os.path.realpath(os.path.join(root, *parts)) 必须以 realpath(root)+os.sep 开头，否则 AppError(FORBIDDEN)。"""
```

- `storage._save_json` → `atomic_write_json`；`storage._load_json` → `read_json`，捕获 `CorruptDataError` 后**向上抛出**，HTTP 层返回 503 `CORRUPT_DATA`，日志记录文件路径；该机型对该文件的写操作全部拒绝，读操作对该数据返回错误，其他数据不受影响。**禁止**把损坏文件当空列表继续写。
- `save_config_file(name, content)` 新流程（锁内）：
  1. `validate_filename(name)`；目标 `configs/name`。
  2. 若当前文件存在且 `sha256(old) == sha256(content)` → 不归档、不写，返回 `{"archived": None, "unchanged": True}`。
  3. 若存在：以**复制**方式写归档（`atomic_write_bytes(archive_path, old)`），成功后再 `atomic_write_bytes(current, content)`。任何一步失败：当前文件保持旧内容。
  4. 计算并写入 diff 摘要缓存（失败只记日志）。
  5. 失效文档缓存（按旧路径的 v1 缓存与 v2 内存 LRU）。
- 所有“读-改-写”（收藏、建议、绑定、映射、工具、IP、管理员、DL、调度、机型注册表）必须在对应锁内完成整个过程。

### 5.5 旧布局迁移（替换启动时的 `shutil.move`）

`ensure_profile_layout(migrate_legacy=True)` 改为：

1. 若 `data/` 根下存在旧条目且 `profiles/default/` 下对应条目不存在：先把旧条目**复制**到目标（目录用 `copytree`，文件用 `copy2`）；
2. 比较文件数与每个文件的 sha256；一致后把旧条目**移动**到 `data/recovery/<ts>_legacy_layout/` 并写 `manifest.json`；
3. 任一步失败：删除不完整的目标副本、保留旧条目、日志 ERROR、启动继续（读取仍走旧位置回退）。
4. 幂等：重复执行不会产生重复副本。

### 5.6 恢复区（`services/recovery.py`）

所有破坏性操作先把受影响数据移入 `data/recovery/<ts>_<op>_<pid>_<name>/`，并写：

```json
{"op": "delete_file|delete_profile|legacy_layout|rename_source", "profile": "default", "target": "scenario_a.xml",
 "created_at": "…", "actor": {"ip": "…", "name": "…"},
 "moved": [{"from": "profiles/default/configs/scenario_a.xml", "to": "configs/scenario_a.xml"}],
 "removed_refs": {"favorites": [...], "bindings": [...], "remarks": [...]}}
```

恢复为运维操作（§14.6 手工步骤），本期不做 UI。恢复区不自动清理。

---

## 6. 文档解析、定位与差异

### 6.1 解析规则（`core/parser.py::to_compact`）

```python
PARSER_VERSION = 2

def to_compact(content: bytes, filename: str) -> Dict[str, Any]:
    """返回 {"format": "xml"|"json", "warnings": [str], "nodes": {"p","t","l","v","id","loc"}}。
    格式判定：扩展名 .json → JSON；.xml → XML；其他 → 先试 JSON 再试 XML；都失败抛 ParseError（→ 422 PARSE_FAILED）。"""
```

**JSON**：`content.decode("utf-8-sig")` → `json.loads`（保持键顺序）。不支持的编码 → `PARSE_FAILED`。

**XML**：
1. 用 `ET.XMLParser(target=ET.TreeBuilder(insert_comments=False))` 一次性解析为元素树（**不再**按文件大小丢弃属性，F06/C04）。
2. 兼容现有容错：遇到 `unbound prefix` → 使用现有 `_preprocess_xml_namespaces` 补声明后重试，并加入 `warnings`：“文件包含未声明的命名空间前缀，已自动补全后解析（仅影响显示，原文未修改）”；遇到 `junk after document element` → 截取到最后一个 `>` 重试，并加入相应 warning。
3. 额外用 `ET.iterparse(events=("start-ns",))` 收集 `uri → prefix`（首次出现者），用于显示标签。
4. 安全：拒绝包含 `<!DOCTYPE` 且声明 `<!ENTITY` 的文档（防实体膨胀），返回 `PARSE_FAILED`，提示“文件包含实体声明，出于安全原因不予解析”。节点数上限 2 000 000、深度上限 512，超过返回 `TOO_LARGE`。

**遍历顺序（先序，必须与模板 `mock/docparse.js` 一致）**：元素行 → 该元素的属性行（按源顺序，跳过 `xmlns`/`xmlns:*`）→ 混合内容的 `#text` 行 → 子元素（递归）。JSON：对象按键顺序、数组按下标。使用显式栈的迭代实现，禁止递归（深文档会触发 RecursionError）。

### 6.2 节点字段（compact 文档）

| 字段 | 含义 | XML | JSON |
|---|---|---|---|
| `p[i]` | 父节点下标，根为 -1 | | |
| `t[i]` | 类型（与旧 `attrs.type` 一致，供建议的 `node_type` 使用） | 根 `xml`；`element` / `attribute` / `text` | 根 `json`；容器 `object` / `array`；标量 `str` / `int` / `float` / `bool` / `NoneType` |
| `l[i]` | 显示标签 | 根为文件名；元素为 `prefix:local`（无前缀时为 local；未知 uri 时为 ET 的 `{uri}local`）；属性 `@name`；文本 `#text` | 根为文件名；键名；数组元素 `[i]` |
| `v[i]` | 值（字符串）或 `null` | 仅子元素为空且 `elem.text.strip()` 非空的元素有值；属性值原样；`#text` 为 strip 后文本 | 标量格式化：`None→"null"`，`True→"true"`，`False→"false"`，其他 `str(value)`；容器为 `null` |
| `id[i]` | **旧版节点 ID**（收藏/绑定/旧建议兼容） | 根 `root`；元素 `父ID + "/" + elem.tag`（ET 原始 tag，命名空间为 `{uri}local`）；属性 `元素ID + "@" + 属性名`；文本 `元素ID + "#text"` | 根 `root`；对象键 `父ID + "." + key`；数组 `父ID + "[" + i + "]"` |
| `loc[i]` | **定位符**：在同一内容（同 sha256）内唯一 | 根 `""`；元素 `父loc + "/" + 名称 + "[" + k + "]"`，k 为同名兄弟中的 1 起序号，名称用 ET 原始 tag；属性 `元素loc + "/@" + 名称`；文本 `元素loc + "/#text"` | JSON Pointer（RFC 6901，`~`→`~0`，`/`→`~1`），根 `""` |

> 旧 ID 规则与 `parser._parse_xml_iterparse` / `_json_to_tree` 完全相同，**不得修改**。同名兄弟共享同一个旧 ID，这是旧数据的既有语义；新功能一律以 `loc` 为准。

`#text` 只取 `elem.text`（第一个子元素之前的文本），不展示 tail 文本——与旧版一致；在 §17 风险中列出。

### 6.3 compact 文档响应

```json
{
  "file": {
    "profile": "default", "name": "scenario_a.xml", "kind": "current|archive|record|temp",
    "version": "current | archive:<文件名> | record:<文件名> | temp", "version_label": "当前版本",
    "sha256": "<64 位十六进制>", "format": "xml", "size": 4683, "updated_at": "2026-10-15 10:08:00",
    "parser_version": 2, "node_count": 123, "read_only": false, "warnings": [], "expires_at": null
  },
  "nodes": {"p": [-1, 0, 1], "t": ["xml", "element", "attribute"], "l": ["scenario_a.xml", "CONFIG_ROOT", "@machineID"],
            "v": [null, null, "MACHINE_ID_01"], "id": ["root", "root/CONFIG_ROOT", "root/CONFIG_ROOT@machineID"],
            "loc": ["", "/CONFIG_ROOT[1]", "/CONFIG_ROOT[1]/@machineID"]}
}
```

- `node_count = len(p) - 1`。前端用 `p` 计算 depth/size/childCount（`MC.doc.buildIndex`）。
- 响应头：`ETag: "<sha256>-v2"`，`Cache-Control: no-cache`；请求带 `If-None-Match` 且相同 → 304。
- 典型体积：每节点约 70–110 字节，gzip 后约为 1/6；30 万节点约 30 MB 原始 / 5 MB gzip（预算见 §12）。

### 6.4 版本引用（FileRef）

`version` 参数取值：`current`（缺省）、`archive:<归档文件名>`、`record:<记录文件名>`；临时文件用独立路径 `/temp/{token}`。服务端用 `validate_filename` 校验归档/记录名后用 `resolve_within` 解析路径，不存在 → 404。

### 6.5 文档缓存（`services/documents.py`）

```python
def load_document(pid: str, name: str, version: str = "current") -> CompactDoc
```

1. 解析出源文件路径 → `os.stat` 得 `(size, mtime_ns)`。
2. 内存 LRU（`OrderedDict`，键 `(realpath, size, mtime_ns)`）→ 命中直接返回**已序列化**的响应体。条目上限 = `max(24, 2 × 该机型配置文件数)`（M4-05 / INH-1907 实测定档，取代原先钉死 24 项；钉死 24 会让 30 文件 × 3 万节点的全局搜索逐出重取，725 ms > §12 的 500 ms），**总序列化字节 ≤ 256 MB 仍是内存的硬护栏，条目数放大不配套放松它**（同规模机型约 71 个文件起就是字节上限先绑）。gzip 由 HTTP 层每次现压、压缩体不进缓存（实测差额 ≈16 ms，缓存它要多吃约 1/6 驻留字节，见 §12 与 `docs/know-how.md`）。
3. 未命中：读文件字节 → `sha256` → 查磁盘缓存 `cache/doc_v2/<sha256>.json.gz`（内含 `parser_version` 与 `cache_schema=1`，不一致视为未命中）→ 仍未命中则 `to_compact` 并原子写入磁盘缓存。
4. 读文件时若 `(size, mtime_ns)` 在读取前后不同，重读一次（防止读到正在替换的文件）。
5. 磁盘缓存是派生数据：损坏即删除重建；清理任务每天删除 30 天未访问的 `doc_v2` 条目与旧 `parse_tree/` 中 7 天以上的条目（保留现有 TTL 语义）。

### 6.6 结构化差异（`core/differ.py::compare_documents`）

```python
def compare_documents(base: CompactDoc, target: CompactDoc, base_name: str, target_name: str,
                      bindings: List[Dict]) -> Dict[str, Any]:
    """返回 {"type": "structural", "summary": {"added","removed","modified","bound"}, "items": [...]}"""
```

1. 分别建立 `loc → (value, display_path, value_path)`，只取 `v != null` 的节点（与旧版“只比较有值节点”一致）。
   - `display_path`：去掉根后用 ` › ` 连接的标签；`value_path`：去掉根后用 `/` 连接的标签。
2. 键集合并集按字典序遍历：仅 target 有 → `added`；仅 base 有 → `removed`；两边都有且值不同 → `modified`。
3. 绑定匹配：对每个绑定变量取 `path` 去掉第一段（文件名）得到 `norm`；若条目的 `value_path == norm`，且绑定变量的 `file` 等于 base 或 target 的**逻辑文件名**（归档/记录比较时用其所属配置文件名），则标注 `bound_group`，`bound` 计数 +1。
4. 条目：`{"op", "loc", "path": display_path, "old", "new", "bound_group"}`；`items` 不截断，前端分页显示。
5. 与旧算法差异：旧版以“标签路径”为键，同名兄弟互相覆盖；新版以 `loc` 为键，同名兄弟各自比较（C11）。

文本差异沿用 `difflib.unified_diff`（`lineterm=""`）；任一侧超过 20 000 行时不计算文本差异，返回 `{"too_large": true}`；返回前 `text_limit`（默认 2 000）行与 `total`、`truncated`。

**差异摘要缓存**：键 `sha1(base_sha + target_sha + DIFFER_VERSION + bindings_hash)` → `cache/diff_v2/<key>.json`（只含 summary/has_changes/structural）。历史列表的 `diff` 优先读该缓存；缺失时计算并写入。旧 `.diff.json` 只在 `bindings_hash` 无法确认时作为降级显示（并标注“旧统计”）。

`compare_versions()`（旧接口）保留给现有测试与旧调用方，内部不变。

### 6.7 修改建议与审阅写回（`services/review.py`、`core/reviewing.py`）

**提交建议** `POST /remarks`：
1. 校验 `proposed_value.strip()` 非空且不等于当前值。
2. 读取当前文档；若请求带 `sha256` 且与当前不同 → 409 `SOURCE_CHANGED`。
3. 用 `loc` 找到节点；节点无值 → 400。
4. JSON 类型检查：原类型 `int` 要求匹配 `^-?\d+$`；`float` 要求可 `float()` 且有限；`bool` 要求 `true|false`；`NoneType` 只允许 `null`；不匹配 → 400 `INVALID_INPUT`，提示需要的类型。XML 不做类型检查。
   `NoneType` 节点的值恒为 `null`，对它提交建议会被第 1 步判为「与当前值相同」而拒绝；也不允许把 `int/float/bool/str` 节点改成 `null`。即 **null 是只读值**（D1，M3-01 裁定）。若将来需要「清空为 null」，属语义变更，需另开卡同时改本节、`services/review.py::_required_type`、`core/reviewing.apply_edits` 的类型转换与 `mock-api.js` 四处。
5. 锁内追加记录（§5.2 字段，`node_key=""`，`schema_version=2`）。

**审阅提交** `POST /review/submit`，请求见 §7.8。服务端步骤（全部在机型锁内完成第 3–8 步）：

1. `authorize(admin)`；`request_id` 若在最近 10 分钟的幂等表中 → 直接返回上次结果。
2. 读取当前原文 `src` 与 `sha256`；`!= source_sha` → 409 `REVIEW_CONFLICT`（前端会刷新）。
3. 取该文件所有 `pending` 建议，按 `group_key = locator or node_path or node_key` 分组；只处理请求中出现的组，未出现的组保持 pending（允许部分提交，C07）。
4. 对每个“通过”的组：定位节点——有 `locator` 用 `loc`；legacy 建议用 `node_path` 匹配旧 ID，**恰好 1 个**节点匹配才可用，否则 409 `REVIEW_CONFLICT`（“无法唯一定位”）。定位后要求当前值 `== original_value`，否则 409（“值已变化”）。
5. 生成 `edits = [(loc, new_value, kind)]`，调用 `reviewing.apply_edits(src, fmt, edits) -> bytes`：
   - XML：`ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))` 解析；先 `ET.register_namespace(prefix, uri)` 注册 §6.1 收集到的前缀；按 `loc` 逐段定位（`名称[k]` 取第 k 个同名子元素，`@a` 设属性，`#text` 或元素自身值设 `.text`）；输出 `ET.tostring(root, encoding=<源声明编码，默认 utf-8>, xml_declaration=<源是否有声明>)`；不调用 `ET.indent`（保持原缩进）。
   - JSON：`json.loads`，按 JSON Pointer 设置，值按原类型转换；输出 `json.dumps(obj, ensure_ascii=False, indent=2)`。
6. **校验**：`to_compact(out)` 后与 `to_compact(src)` 按 `loc` 比较全部有值节点：差异集合必须**恰好等于** edits 中的 loc 且值等于建议值；节点总数与结构（`t` 序列）不变。任一不满足 → 422 `REVIEW_UNSAFE`，不写任何文件，不改任何状态。
7. 写新文件（§5.3 命名）到 `configs/`。
8. 更新建议状态：通过的那条 `approved`（`generated_file` = 新文件名），同组其他条与“不通过”组全部 `rejected`；写 `reviewed_by/reviewer_ip/reviewed_at`。原子写一次。
9. 返回 `{generated_file, approved, rejected}`；登记幂等表。

已知限制（必须在 UI 帮助与 §17 中说明）：标准库重写 XML 会把属性引号统一为双引号、把空元素写成 `<a />`、可能改变实体写法；**业务语义不变**由第 6 步保证。若真实样本中出现第 6 步无法通过的结构（如 DTD），该文件的审阅会被明确拒绝，管理员可下载原文手工修改。

---

## 7. HTTP API 规范（`/api/v1`）

### 7.1 通用约定

- 编码 UTF-8；请求体 JSON 需 `Content-Type: application/json`；原文上传用 `application/octet-stream` + 头 `X-Filename: <encodeURIComponent(文件名)>`。
- 路径参数中的文件名、组名、IP 等用 `encodeURIComponent` 编码；服务端 `urllib.parse.unquote` 后再校验。
- 成功：`200`（读取/更新）、`201`（创建）、`202`（已受理任务）。
- 失败统一：
  ```json
  {"error": {"code": "INVALID_INPUT", "message": "下载链接需以 http:// 或 https:// 开头", "detail": {"field": "url"}}}
  ```
  `detail.field` 存在时前端把错误显示在对应输入框下（模板已实现）。
- 时间均为服务器本地时间字符串；数字均为 JSON number；未设置用 `null`。
- 所有写接口在成功后递增对应修订计数（§7.3）。

| code | HTTP | 含义 |
|---|---|---|
| `INVALID_INPUT` | 400 | 参数缺失/格式错误（含 `detail.field`） |
| `FORBIDDEN` | 403 | 身份无权限 |
| `NOT_FOUND` | 404 | 机型/文件/版本/接口不存在 |
| `FILE_EXISTS` | 409 | 保存时同名文件存在且未声明 `overwrite=1` |
| `FAVORITE_COVERED` | 409 | 节点已被父级收藏覆盖（`detail.parent`） |
| `BUSY` | 409 | 相同任务进行中（`detail.task_id`） |
| `REVISION_CONFLICT` | 409 | `If-Match` 不一致 |
| `SOURCE_CHANGED` | 409 | 基于的文件版本已变化 |
| `REVIEW_CONFLICT` | 409 | 审阅时源文件或节点值变化 / 无法唯一定位 |
| `TOO_LARGE` | 413 | 超过大小/节点/深度上限 |
| `PARSE_FAILED` | 422 | 无法解析为 XML/JSON |
| `REVIEW_UNSAFE` | 422 | 生成结果未通过一致性校验 |
| `DOWNLOAD_FAILED` | 502 | 服务端从链接下载失败（`message` 含原因） |
| `STORAGE_FAILURE` | 500 | 写入失败/未知错误（不含堆栈） |
| `CORRUPT_DATA` | 503 | 权威 JSON 损坏，只读保护 |
| `MAINTENANCE` | 503 | 维护中（WriteGate） |

### 7.2 会话与机型

| 方法 路径 | 权限 | 请求 | 响应 |
|---|---|---|---|
| `GET /session?profile={pid}` | 全部 | — | `{"identity": {"ip","name","registered","is_deployer","is_admin","role_label"}, "profile": {"id","name"}, "profiles": [{"id","name"}], "server": {"version","time","scheduler": bool}, "limits": {"upload_max_bytes","tab_limit","temp_ttl_hours"}}`。`profile` 不存在时回落 `default`（**仅此接口**，用于首次进入）。若无 `mc_cid` cookie 则下发 `Set-Cookie: mc_cid=<随机 24 字节>; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax` |
| `POST /profiles` | 部署者 | `{"name": "…", "copy_from": "<pid>"\|null}` | 201 `{"profile": {"id","name"}}`；名称为空/重复 → 400/409 |
| `PATCH /profiles/{pid}` | 部署者 | `{"name"}` | `{"profile"}` |
| `DELETE /profiles/{pid}` | 部署者 | — | `{"deleted": true, "recovery": "<恢复区目录名>"}`；`default` → 400。数据目录移入恢复区（C27） |

复制机型（`copy_from`）复制：`configs/`、`archive/`、`config_mapping.json`、`favorites.json`、`bindings.json`、`tools.json`、`schedule.json`（`last_run` 置空）、`dltool_config.json`；**不复制**：`records/`、`edit_remarks.json`、`ip_mapping.json`、`admin_users.json`、`update_status.json`、`cache/`（与现有 `copy_profile_data` 一致，另外不再复制 cache）。

### 7.3 修订号（多用户同步）

`GET /p/{pid}/revision` → `{"rev": "12.3.5.8.2", "parts": {"files": 12, "favorites": 3, "remarks": 5, "settings": 8, "tasks": 2}}`

- 服务端在内存为每个机型维护 5 个单调递增计数；对应写操作成功后 +1；进程启动时从 1 开始（客户端只比较是否变化）。
- 调度任务、其他用户的写入同样递增，因此前端 10 s 轮询即可得知“别人改了什么”（§8.5）。
- 该接口必须极轻量（不读磁盘），P99 < 5 ms。

### 7.4 文件

| 方法 路径 | 权限 | 说明 / 请求 | 响应 |
|---|---|---|---|
| `GET /p/{pid}/files` | 全部 | 当前文件列表（只列 `configs/` 下的普通文件，按名称排序） | `{"items": [{"name","format":"xml\|json\|other","size","updated_at","mapped": bool,"archive_count","record_count","pending_remarks"}]}` |
| `GET /p/{pid}/files/{name}/document?version=` | 全部 | §6.3 | compact 文档；无法解析 → 422 `PARSE_FAILED`（`detail.raw_available=true`，前端提供“下载原文”） |
| `GET /p/{pid}/files/{name}/raw?version=` | 全部 | 原文字节 | `Content-Type` 按扩展名；`Content-Disposition: attachment; filename="<ascii 回退>"; filename*=UTF-8''<编码名>`；归档/记录下载名为版本文件名 |
| `PUT /p/{pid}/files/{name}?overwrite=1` | 部署者 | 原文字节；先 `to_compact` 校验 | 新建 201 / 覆盖 200：`{"file": {"name"}, "archived": "<归档名>"\|null, "unchanged": bool}`；同名且无 `overwrite=1` → 409 `FILE_EXISTS` |
| `DELETE /p/{pid}/files/{name}` | 管理员 | 当前版本、`archive/<name>/`、`records/<name>/` 移入恢复区；移除映射、收藏、绑定变量（空组删除）、建议，被移除的引用写入 manifest | `{"deleted": true, "removed": {"archives","records","remarks","favorites"}, "recovery": "<目录名>"}` |
| `GET /p/{pid}/files/{name}/archives` | 全部 | 归档按修改时间倒序 | `{"current": {"name","updated_at","size","sha256"}, "items": [{"filename","date","size","compared_to","diff": {"has_changes","summary": {"added","removed","modified","bound"},"structural": bool}\|null}]}`；每个归档与“紧邻的较新版本”比较（同现有 `ensure_archive_diffs`） |
| `GET /p/{pid}/files/{name}/records` | 全部 | 修改记录倒序 | `{"record_url": "…", "items": [{"filename","date","size","diff_prev": {同上}\|null}]}` |
| `POST /p/{pid}/temp` | 全部 | 原文字节 + `X-Filename`；校验可解析 | 201 `{"token","name","format","size","expires_at"}` |
| `GET /p/{pid}/temp/{token}/document` / `…/raw` | 所有者 | 只有上传者（同 `mc_cid` 且同 IP）可访问 | 同 document/raw；`file.kind="temp"` |
| `POST /p/{pid}/import-url` | 全部；`persist=true` 需部署者 | `{"url","name","persist": bool,"overwrite": bool}` | `persist=true`：同 PUT 响应 + `"persisted": true`；`false`：`{"persisted": false,"token","name"}` |

临时文件规则（`services/temp_store.py`）：目录 `data/temporary/<token>/`，`token = secrets.token_urlsafe(18)`；`meta.json = {"owner_cid","owner_ip","profile","name","size","created_at","expires_at"}`；同一 `owner_cid` 最多 20 个，全局总量 ≤ 1 GB（超过 → 413）；调度器每小时删除过期目录（临时区不属于权威数据）。过期后访问返回 404“临时文件已过期，请重新上传”。**不得**在临时 token 失效时回退去读同名服务器文件（修复 F01 缺陷）。

### 7.5 对比

`POST /p/{pid}/compare`

```json
{"base":   {"file": "scenario_a.xml", "version": "archive:scenario_a_20261008.xml"},
 "target": {"file": "scenario_a.xml", "version": "current"},
 "text_limit": 2000}
```
任一侧可为 `{"temp": "<token>"}`（上传的本地文件，所有者校验）。响应：

```json
{"has_changes": true,
 "base":   {"label": "历史版本 scenario_a_20261008.xml", "name": "scenario_a_20261008.xml"},
 "target": {"label": "当前版本", "name": "scenario_a.xml"},
 "structural": {"type": "structural|unsupported", "summary": {"added": 0, "removed": 0, "modified": 3, "bound": 2},
                "items": [{"op": "modified", "loc": "/CONFIG_ROOT[1]/…/param_02[1]", "path": "CONFIG_ROOT › … › param_02",
                           "old": "9", "new": "12", "bound_group": "脉冲计数"}]},
 "unified": {"lines": ["--- …", "+++ …", "@@ … @@", "-…", "+…"], "total": 20, "truncated": false, "too_large": false}}
```
旧版四种模式全部可表达：当前 vs 历史、历史 vs 历史、上传 vs 当前、上传 vs 历史；另外支持记录 vs 记录、任意组合。

### 7.6 搜索

`GET /p/{pid}/search?q=&file=&limit=200&offset=0`

- `q` 去空白后非空，大小写不敏感，匹配 `label`、`value`、该节点（按旧 ID）的收藏备注；`file` 为空表示全部当前文件。
- 响应：`{"q","total","files": {"<文件名>": 命中数},"items": [{"file","id","loc","path","label","value","type","note","match": "label|value|note"}],"truncated": bool}`；`limit ≤ 1000`。
- 实现：对每个文件 `load_document`（命中缓存时只做数组扫描），缓存的是 §6.5 的**文档响应体**；「预先缓存每个文档的小写 label/value 数组」**未实现**，M4-05 实测也不需要——瓶颈是响应体条目数不够导致的逐出重取（`_read_bytes` 每轮搜索 30 次），按 §6.5 的容量规则放大后 p50 282–296 ms。30 个文件 × 3 万节点应在 300 ms 内完成（§12），但**通过线取 §12 表格的 500 ms**（D3 裁定，M4-05 按此验收；本行的 300 ms 保留不动，不是判据）。
- 计数语义修正：每个命中节点计 1（旧版父节点命中时计 0，导致“有结果却显示未找到”）。

### 7.7 收藏

| 方法 路径 | 请求 | 响应 / 规则 |
|---|---|---|
| `GET /p/{pid}/favorites` | — | `{"groups": [{"file","exists","updated_at","items": [{收藏字段…, "value": 实时值, "saved_value": 收藏时值, "missing": bool, "children": [{…, "missing"}]}]}]}`；存在的文件在前；实时值按旧 ID 匹配，多个同 ID 节点时取先序最后一个（与旧 `favorites_live` 一致） |
| `POST /p/{pid}/favorites` | `{"file","id"}` | 服务端从当前文档取 label/value；有值节点 → 叶子收藏；无值节点 → 子树快照（无子项 → 400）；已被父级收藏覆盖 → 409 `FAVORITE_COVERED`；已收藏 → 200 返回原项 |
| `PATCH /p/{pid}/favorites` | `{"file","path","note"}` | 更新备注 |
| `DELETE /p/{pid}/favorites?file=&path=` | — | 删除一项；不带 `path` 时清空该文件全部收藏 → `{"removed": n}` |

### 7.8 建议与审阅

| 方法 路径 | 权限 | 请求 | 响应 |
|---|---|---|---|
| `GET /p/{pid}/remarks?file=&status=` | 全部 | — | `{"items": [建议记录…]}` |
| `POST /p/{pid}/remarks` | 全部 | `{"file","sha256","loc","id","label","original_value","proposed_value","node_type"}` | 201 `{"item"}`（§6.7） |
| `GET /p/{pid}/review` | 管理员 | — | `{"files": [{"file","exists","current_sha","nodes": [{"group_key","loc","id","label","path","node_type","original_value","current_value","stale": bool,"legacy": bool,"resolvable": bool,"remarks": [{"id","actor_display","actor_ip","proposed_value","created_at"}]}]}]}` |
| `POST /p/{pid}/review/submit` | 管理员 | `{"request_id","file","source_sha","decisions": [{"group_key","choice": "<remark_id>"\|"reject"}]}` | `{"generated_file": "…"\|null,"approved","rejected"}` |

`stale = resolvable && current_value != original_value`；前端对 stale/不可定位节点只允许“不通过”。

### 7.9 设置类

| 方法 路径 | 权限 | 请求 → 响应 |
|---|---|---|
| `GET /p/{pid}/sources` | 全部 | `{"items": [{"name","url","record_url","local_updated_at","last_result": update_status\|null}]}` |
| `POST /p/{pid}/sources` | 部署者 | `{"name","url","record_url"}` → 201；`name` 用 `validate_filename`（**允许中文**，替代旧的 ASCII 白名单，C26）；`url`/`record_url` 必须 http(s) |
| `PATCH /p/{pid}/sources/{name}` | 部署者 | `{"name","url","record_url","migrate_files": bool}`；改名且 `migrate_files`：迁移 `configs/`、`archive/`、`records/`，并更新收藏、绑定、建议、DL 绑定中的文件名引用；迁移前目标存在任一项 → 409；迁移失败 → 回滚已移动项 |
| `DELETE /p/{pid}/sources/{name}` | 部署者 | 只删映射（同现状），不删文件 |
| `GET /p/{pid}/schedule` | 全部 | `{"enabled","interval_hours","last_run","next_run_at"}` |
| `PUT /p/{pid}/schedule` | 部署者 | `{"enabled","interval_hours"}`，`1 ≤ interval_hours ≤ 2160` 的整数；保存后调度器重算 |
| `GET /p/{pid}/ip-mapping` | 全部 | `{"items"}` |
| `PUT /p/{pid}/ip-mapping/{ip}` | 部署者 | `{"ip","name"}`；路径 ip 与 body ip 不同时视为改 IP（先删旧再写新，同一锁内）；IP 必须是合法 IPv4/IPv6（`ipaddress.ip_address`） |
| `DELETE /p/{pid}/ip-mapping/{ip}` | 部署者 | — |
| `GET /p/{pid}/admins` / `POST /p/{pid}/admins {"name"}` / `DELETE /p/{pid}/admins/{name}` | 读全部，写部署者 | 重复 → 409 |
| `GET /p/{pid}/bindings` / `PUT /p/{pid}/bindings/{group}` / `DELETE …/{group}` | 读全部，写部署者 | PUT body `{"group_name","variables": [{"file","path"}]}`；至少 2 个有效变量；改名时新名不得重复 |
| `GET /p/{pid}/tools` / `PUT /p/{pid}/tools/{name}` / `DELETE …/{name}` | 读全部，写部署者 | PUT body `{"name","description","url"}`；路径 name 不存在即新增 |

### 7.10 任务

| 方法 路径 | 权限 | 请求 → 响应 |
|---|---|---|
| `POST /p/{pid}/tasks` | 部署者 | `{"type": "update_source"\|"update_all"\|"refresh_records", "name": "<文件名，update_all 不需要>"}` → 202 `{"task"}`；重复 → 409 `BUSY` |
| `GET /p/{pid}/tasks?active=1` | 全部 | `{"items": [task]}` |
| `GET /tasks/{id}` | 全部 | `{"task"}` |

`task = {"id","profile","type","target","state": "queued|running|succeeded|partial|failed","progress": {"done","total"},"result": {"items": [{"name","status","label","reason","at"}]},"error","actor","created_at","finished_at"}`。已结束任务在内存保留 1 小时 / 最近 200 条。

### 7.11 DL 计算

| 方法 路径 | 权限 | 请求 → 响应 |
|---|---|---|
| `GET /p/{pid}/dl` | 全部 | `{"rev": "<dltool_config.json 的 sha1 前 12 位>", "items": {pp_energy, rp_energy, rp_width}}` |
| `PUT /p/{pid}/dl/{item}` | 部署者 | 头 `If-Match: <rev>`；body 为完整 item；校验（§9.8.7 规则）→ `{"rev","item"}`；rev 不符 → 409 `REVISION_CONFLICT` |
| `POST /p/{pid}/dl/{item}/extract` | 全部 | `{"binding": {"source_file","variable_path","field_map"}}` → `{"coeffs": {成功提取的键}, "fields": [{"field","coeff","status": "ok\|missing\|invalid","value"\|"raw"}]}`（只预览，不保存） |
| `POST /p/{pid}/dl/{item}/compute` | 全部 | `{"mode": "forward\|reverse\|multi_check","values": [number],"params": item}` → forward：`{"results": [{"input_index","x","y_base","y","status": "ok\|out_of_x_range\|out_of_y_range"}]}`；reverse：`{"results": [{"input_index","y","y_base","x_values","residuals","status": "single\|multiple\|none\|identity\|out_of_y_range"}],"exhaustive": false}`；multi_check：`{"has_multi","cases": [{"y","x_values"}]}` |

`variable_path` 为旧节点 ID（与现有绑定配置一致）；提取子节点值作为字段（XML 子元素/属性标签、JSON 键），无法转为有限数 → `invalid`；缺失 → `missing`。**不再**用 0 替代失败字段（C15）。

### 7.12 兼容旧路由（`server/legacy.py`）

| 旧路由 | 新行为 |
|---|---|
| `GET /api/file-download?kind=current\|archive&filename=&archive_filename=&profile=` | 等价于 `/api/v1/p/{profile or default}/files/{filename}/raw?version=…`；头 `X-MC-Download-Probe: 1` 时返回旧版 JSON `{"ok": true/false, "detail"}` |
| `GET /record_view?config=&record=&profile=` | 302 到 `/#/p/{profile or default}/f/{config}?version=record:{record}` |
| `GET /static/*` | 404（旧 NiceGUI 静态资源不再提供） |

---

## 8. 前端架构

### 8.1 技术约束（必须）

| 项 | 规定 |
|---|---|
| 语言 | ES2017（`async/await` 可用但模板统一用 Promise 链）；不得使用可选链 `?.`、空值合并 `??`、`Promise.prototype.finally` 以外更新的语法（模板已避免 `finally`） |
| 模块 | 经典 `<script>`，每个文件 `(function (global) { var MC = global.MC; … })(window)`，按 index.html 顺序加载 |
| DOM | 只用 `MC.util.h(sel, attrs, ...children)` 建元素；字符串子节点即文本节点。唯一允许 `innerHTML` 的地方是 `ui/icons.js` 注入固定常量图标 |
| 外链 | `MC.util.safeUrl()` 只放行 http/https；`<a target="_blank" rel="noopener noreferrer">` |
| 存储 | `localStorage` 只存 UI 状态（§8.4），键前缀 `mc.`；不存任何服务器数据副本 |
| 资源 | 无外部字体/图标/CDN；系统字体栈；图标为自绘内联 SVG sprite |
| 体积 | 未压缩 JS+CSS ≤ 400 KB（模板约 330 KB），gzip 后 ≤ 90 KB |

### 8.2 文件组织（模板即生产代码起点）

```text
app/web/
  index.html            # 生产入口：加载 /config.js（服务端生成）+ core + views，不含 mock
  template.html         # 模板入口：加载 assets/js/mock/enable-mock.js + mock/* + 同一套 core/views
  assets/css/
    tokens.css          # 设计令牌（§9.4），唯一定义颜色/尺寸的地方
    base.css            # reset、排版、工具类
    components.css      # 按钮、表单、徽标、分段、面板、表格、状态、横幅、弹窗、菜单、Toast、提示
    layout.css          # 顶栏、侧栏、标签栏、视图容器、响应式
    views.css           # 文件视图/虚拟树/详情面板/对比/速览/搜索/审阅/DL/设置
  assets/js/
    core/util.js        # h()、格式化、剪贴板、下载、localStorage、safeUrl
    core/api.js         # MC.api（fetch 封装、错误对象）、MC.bus（事件）、MC.router（hash 路由）
    ui/icons.js         # 图标 sprite + MC.icon(name)
    ui/components.js    # MC.toast / modal / confirm / popover / menu / stateBlock / field / switchEl / segmented / withBusy / copyButton / diffStats
    ui/tree.js          # MC.doc.buildIndex + MC.Tree（虚拟树）
    views/shell.js      # MC.App：外壳、侧栏、标签、轮询、命令面板
    views/importer.js   # 导入对话框
    views/overview.js   # 速览
    views/fileview.js   # 文件工作区（结构/历史/对比/记录；临时文件同用）
    views/search.js     # 全局搜索
    views/review.js     # 审阅
    views/dltool.js     # DL 计算
    views/settings.js   # 设置
    mock/               # 仅模板：enable-mock.js、docparse.js、dlmath.js、fixtures.js、mock-api.js
    app.js              # 启动
  tests/index.html      # 前端纯逻辑测试页（tests.js）
```

### 8.3 路由（hash，可分享、可前进后退）

| 路由 | 视图 | query |
|---|---|---|
| `#/p/{pid}/overview` | 速览 | — |
| `#/p/{pid}/search` | 全局搜索 | `q`、`file` |
| `#/p/{pid}/f/{name}` | 文件工作区 | `view=tree\|history\|compare\|records`（缺省 tree）、`version=archive:…\|record:…`、`node=<loc 或旧 ID>`、`base`/`target`（对比两侧，取值 `current`/`archive:…`/`record:…`/`temp:<token>`） |
| `#/p/{pid}/t/{token}` | 临时文件（同文件工作区，仅结构/对比） | 同上 |
| `#/p/{pid}/review` | 审阅 | — |
| `#/p/{pid}/dl` | DL 计算 | `item=pp_energy\|rp_energy\|rp_width` |
| `#/p/{pid}/settings/{section}` | 设置 | section：`sources\|schedule\|people\|bindings\|tools\|profiles` |

URL 永远携带 `pid`，从而修复“记录链接不带机型”；同事拿到链接即打开同一机型、文件、版本与节点。

### 8.4 标签工作区与本地状态

- 标签键：`overview`（固定、不可关闭）、`search`、`review`、`dl`、`settings`（单例）、`f:<name>`、`t:<token>`。**一个文件只占一个标签**，结构/历史/对比/记录是该标签内的分段视图（C02）。
- 打开路由 → 找到或创建标签 → 激活；视图实例保留在内存（隐藏），切回时保留滚动、展开、选择状态。
- 上限 `limits.tab_limit`（15，不含速览）：超出时关闭“最久未使用且没有未保存修改”的标签，并 Toast 告知；若全部都有未保存修改，则不关闭并警告（修复“带草稿的标签被静默淘汰”）。
- 关闭有未保存修改的标签需确认。中键点击关闭。标签栏“⋯”提供“关闭其他 / 关闭全部”。
- 去掉旧“返回栈”按钮，返回/前进交给浏览器（路由已完整表达状态）。

| localStorage 键 | 内容 |
|---|---|
| `mc.profile` | 上次使用的机型 |
| `mc.tabs.<pid>` | 标签列表 `[{key, route, title, opened}]`（不含临时文件标签） |
| `mc.active.<pid>` | 上次的完整 hash |
| `mc.sidebarCollapsed` / `mc.inspector` / `mc.showAttrs` / `mc.udiffOpen` | 布局偏好 |
| `mc.reviewDraft.<pid>` | 审阅草稿 `{"<file>\|<group_key>": "<remark_id>"\|"reject"}` |

### 8.5 多用户同步

- 每 10 s（页面可见时）请求 `GET /revision`；页面从后台切回前台立即请求一次。
- 比较 `parts`：`files` 变化 → 刷新侧栏文件列表，打开中的文件页检查 sha，若与正在显示的不同则显示黄色横幅“此文件已被他人或定时任务更新…［重新加载］”（**不自动刷新**，避免打断阅读与草稿）；`favorites`/`remarks` 变化 → 刷新速览与当前文件的星标/建议徽标；`settings` → 刷新工具列表与设置页；`tasks` → 拉取任务进度。
- 本地写操作成功后调用 `app.syncRevision()` 更新基准，避免把自己的修改当成别人的。
- 有进行中任务时每 1.2 s 轮询 `/tasks?active=1`，结束后自动停止并广播 `task:done`。

### 8.6 组件清单（`ui/components.js`，新增组件前先复用）

| 组件 | API | 要点 |
|---|---|---|
| Toast | `MC.toast.success/error/warning/info(title, desc, {action, duration})` | 右下角堆叠；错误 8 s，其他 3.5 s；可带“重试/撤销”动作；`aria-live=polite` |
| 错误文案 | `MC.errorText(err)`、`MC.toastError(title, err, opts)` | 只显示服务端 `message` 或错误码对照文案，不显示堆栈 |
| Modal | `MC.modal({title, subtitle, body, footer(close), size: 'wide'\|'xwide', dismissible})` | Esc 关闭、焦点陷阱、关闭后焦点回到触发元素、点击遮罩关闭 |
| Confirm | `MC.confirm({title, message, detail, confirmText, danger, requireText})` → `Promise<boolean>` | 删除文件/机型需输入名称确认 |
| Popover / Menu | `MC.popover(anchor, node, {align})`、`MC.menu(anchor, items)` | ↑↓ 键导航、Esc 关闭、点击外部关闭；菜单项支持 `checked/disabled/title/danger/meta` |
| 状态块 | `MC.stateBlock({kind: empty\|loading\|error\|locked, icon, title, desc, actions})`、`MC.errorBlock(err, retry)` | 四类状态视觉区分（§9.6） |
| 表单 | `MC.field({label, required, help, control})` → `.setError(msg)`；`MC.switchEl(label, checked, onChange, disabled)` | label 与控件 `for/id` 关联；错误贴近字段并设 `aria-invalid` |
| 分段 | `MC.segmented(items, value, onChange, ariaLabel)` | `role=tablist`；可显示计数 |
| 防重复提交 | `MC.withBusy(button, () => promise)` | 期间按钮 loading + disabled |
| 复制 | `MC.copyButton(textOrFn, label)` | HTTP 内网自动回退 `execCommand`；失败明确提示，不假装成功 |
| 差异统计 | `MC.diffStats(diff)` | `+n −n ~n ⛓n`，带 `aria-label` 文字说明，不只靠颜色 |

### 8.7 虚拟树（`ui/tree.js`）

**数据**：`MC.doc.buildIndex(doc)` 一次 O(n) 计算 `depth`、`size`（子树节点数，逆序累加）、`childCount`、`byId`、`byLoc`、`param_` 元数据（§9.8.3），并懒生成小写 label/value 数组供筛选。

**可见行计算**（每次展开/折叠/筛选后调用 `layout()`）：

```text
rows = []; i = 1
while i < n:
  if 节点被隐藏（param 元属性 / 关闭“显示属性”时的属性 / 筛选不可见）: i += size[i]; continue
  rows.push(i)
  i += (size[i] > 1 && !expanded[i]) ? size[i] : 1
```

**渲染**：外层滚动容器内放高度 = `rows.length × 28px` 的占位层，只为可视区 ± 12 行创建 DOM（约 40–60 行），滚动用 `requestAnimationFrame` 节流，窗口 `transform: translateY` 定位。事件委托在容器上（行、展开按钮、星标）。

**默认展开**：节点 ≤ 3 000 → 全部展开（与旧版习惯一致）；更大 → 只展开前两层（C03）。工具栏提供“展开到第 1/2/3 层 / 全部展开 / 全部折叠”。

**筛选语义**（与旧版 `filter_tree` 一致并修正计数）：命中 = label/value/收藏备注包含关键字；显示 = 命中节点 + 其全部祖先 + 命中节点的整棵子树；自动展开命中节点的祖先；返回命中数并支持 Enter/Shift+Enter 在命中间跳转，显示 `当前/总数`。

**键盘**：↑↓ 选择；← 折叠或跳到父节点；→ 展开或进入第一个子节点；Home/End；PageUp/PageDown；Enter 打开详情面板；S 收藏/取消收藏；双击行切换展开。

**无障碍**：容器 `role=tree`，行 `role=treeitem` + `aria-level` + `aria-expanded` + `aria-selected`。

### 8.8 视图实例接口

```js
MC.views[kind] = function (app, route) {
  return { el, title, icon,
           update(route) {},      // 同一标签内路由变化（切换子视图/版本/节点）
           onShow() {},           // 每次被激活
           isDirty() { return false }, // 有未保存修改时 true（标签显示圆点，关闭需确认）
           destroy() {} };
};
```

`app` 提供：`session`、`pid`、`identity`、`can('deployer'|'admin')`、`p(rest)`（拼机型路径）、`files`、`go(route)`、`openFile(name, query)`、`downloadFile(name, version)`、`deleteFile(file)`、`refreshFiles()`、`refreshTools()`、`startTask(body)`、`syncRevision()`、`setTabTitle(inst, title)`、`renderTabbar()`。

### 8.9 从模板到生产

1. **模板演示**：直接双击 `app/web/template.html`，或 `python -m http.server 8090 --directory app/web` 后访问 `http://127.0.0.1:8090/template.html`。右上角紫色虚线“演示”按钮可切换模拟身份（部署者+管理员 / 部署者 / 管理员 / 成员 / 访客）并模拟“他人更新文件”。
2. **生产**：服务端对 `/` 返回 `index.html`，对 `/config.js` 返回 `window.MC=window.MC||{};window.MC.config={mock:false};`。`index.html` 与 `template.html` 唯一区别是：不加载 `assets/js/mock/*`，改为加载 `config.js`。服务端**不提供** `/assets/js/mock/*`、`/template.html`、`/tests/*`（返回 404），避免生产环境误用 mock。
3. **契约一致性**：`mock/mock-api.js` 的每个路由都对应 §7 的一个接口；实现后端时以 §7 为准，若需改动契约，必须同步修改 mock 与本文。
4. 前端代码改动后：打开 `tests/index.html` 必须显示 `ALL PASSED`；用 `template.html` 走一遍 §16.2 的 UI 用例。

---

## 9. UI / UX 设计规范

### 9.1 设计原则

1. **安静、精确**：中性灰阶 + 单一蓝色强调色；颜色只表达状态与优先级，不做装饰（无渐变、无玻璃拟态、无彩虹缩进线）。
2. **密度适配高频工作**：数据行 28 px、正文 13–14 px、等宽字体显示路径与值；页面留白克制。
3. **一个地方做一件事**：侧栏 = 去哪里；标签 = 打开了什么；分段 = 看这个文件的哪一面；详情面板 = 对选中节点做什么。
4. **不靠悬停发现主操作**：主操作常驻可见；悬停只用于次要的快捷操作，且键盘聚焦时同样出现。
5. **永远说清楚**：权限不足说明原因与解决办法；加载、空、无匹配、失败、只读、数据过期分别表达；成功只在服务端确认后提示。
6. **可恢复**：危险操作说明影响范围与恢复方式；取消收藏提供“撤销”；删除进入恢复区。

### 9.2 信息架构

```text
顶栏：侧栏开关 │ MC MCChecker │ 机型 ▾ │ [ 搜索文件、变量名、值或备注…  Ctrl K ] │ 任务进度 │ (演示) │ 身份
侧栏：
  速览
  全局搜索
  审阅修改  (待审数)            ← 仅管理员可见
  DL 计算
  设置
  ── 配置文件 n  [+导入] ──
  [筛选文件名]
  文件条目（格式标签 · 名称 · 更新时间/历史数/手动导入 · 待审徽标；悬停/聚焦：下载、更多）
  ── 外部工具 ──
  工具链接（新窗口）
主区：标签栏 + 当前视图
  文件视图分段：结构 | 历史版本 (n) | 对比 | 修改记录 (n)
  设置子导航：更新源 | 定时更新 | 人员与权限 | 变量绑定 | 工具菜单 | 机型管理
```

旧入口迁移对照：顶栏“上传/URL 下载”→ 侧栏“+”导入；顶栏“全局搜索”→ 顶栏搜索框与命令面板；顶栏“更新设置/变量绑定/工具菜单”→ 设置；右侧工具箱“DL 快捷计算”→ 侧栏“DL 计算”；右侧工具箱工具 → 侧栏“外部工具”；“返回”按钮 → 浏览器返回；机型菜单 → 顶栏机型 ▾ + 设置 · 机型管理。

### 9.3 布局与尺寸

| 区域 | 尺寸 | 行为 |
|---|---|---|
| 顶栏 | 高 48 px，白底下边框 | 固定 |
| 侧栏 | 宽 264 px | 可收起（记忆）；≤ 960 px 变为覆盖式抽屉 |
| 标签栏 | 高 38 px | 横向滚动，激活标签自动滚入视野 |
| 视图头部 | 最小高 56 px，左右内边距 20 px | 标题 + 说明 + 主操作 |
| 内容容器 | 最大宽 1120 px（设置/历史/搜索），窄版 820 px（审阅详情）；文件结构视图铺满 | 居中 |
| 详情面板 | 宽 344 px | 可隐藏（记忆）；≤ 960 px 变为右侧覆盖抽屉 |
| 数据行 | 28 px（树）、32 px（紧凑表）、40 px（普通表） | — |

验收视口：1366×768、1440×900、1920×1080 完成全部核心任务；900 px 宽可用（侧栏抽屉）。

### 9.4 设计令牌（`assets/css/tokens.css`，唯一事实源）

| 类别 | 令牌 | 值 | 用途 |
|---|---|---|---|
| 背景 | `--c-bg` / `--c-surface` / `--c-surface-2` | `#f5f6f8` / `#fff` / `#f9fafb` | 页面底 / 面板 / 表头、次级底 |
| 边框 | `--c-border` / `--c-border-strong` | `#e4e7ec` / `#d0d5dd` | 分隔 / 控件边框 |
| 文字 | `--c-text` / `-2` / `-3` / `-disabled` | `#101828` / `#344054` / `#5d6676` / `#98a2b3` | 主 / 次 / 说明（对白底 ≥ 5.5:1）/ 禁用 |
| 强调 | `--c-primary` / `-hover` / `-soft` / `-text` | `#2563eb` / `#1d4ed8` / `#eef4ff` / `#1d4ed8` | 主按钮、选中、链接（沿用旧主色） |
| 新增/成功 | `--c-success(-text/-soft)` | `#16a34a` / `#15803d` / `#ecfdf3` | 与旧约定一致：绿 = 新增 |
| 删除/危险 | `--c-danger(-text/-soft)` | `#dc2626` / `#b42318` / `#fef3f2` | 红 = 删除 |
| 修改/警告 | `--c-warning(-text/-soft)` | `#d97706` / `#b54708` / `#fffaeb` | 橙 = 修改、待审、过期 |
| 绑定 | `--c-bind(-text/-soft)` | `#7c3aed` / `#6d28d9` / `#f5f3ff` | 紫 = 绑定变量 |
| 收藏 | `--c-star` | `#f5a524` | 星标 |
| 字体 | `--font-ui` / `--font-mono` | 系统中文无衬线 / `ui-monospace, Cascadia Mono, Consolas…` | 界面 / 路径与值 |
| 字号 | `--fs-xs/sm/md/lg/xl` | 12/13/14/16/20 px | 说明 / 表格与控件 / 正文 / 区块标题 / 页面标题 |
| 间距 | `--sp-1…8` | 4/8/12/16/20/24/32 px | 4 px 基线 |
| 圆角 | `--r-sm` / `--r` / `--r-lg` | 4 / 6 / 8 px | 徽标 / 控件 / 面板与弹窗 |
| 阴影 | `--sh-pop` / `--sh-overlay` | — | 仅用于浮层（菜单、Toast、弹窗）；面板不用阴影 |
| 焦点 | `--focus-ring` | `0 0 0 3px rgba(37,99,235,.28)` | 所有可聚焦元素 `:focus-visible` |
| 动效 | `--dur-fast` / `--dur` | 120 / 180 ms | `prefers-reduced-motion` 时为 0 |

值的颜色按类型区分（树与表格）：数值 `#067647`、布尔 `#7a2bd1`、字符串 `#0b57d0`、null 灰色斜体；XML 属性名 `#9a4b0b`。

### 9.5 组件视觉规范

- **按钮**：高 32 px（小号 28 px），圆角 6 px。层级：主按钮（蓝底白字，每个区域最多 1 个）> 普通（白底边框）> 幽灵（透明）> 危险（红底，仅确认框）> 危险幽灵（红字，表格行“删除”）。图标按钮必须有 `aria-label` 与 `data-tip` 提示。
- **输入**：高 32 px；路径/值/URL/文件名使用等宽字体；错误时红边框 + 下方红字。
- **徽标**：高 20 px、圆角 4 px；语义色只用于状态（成功/危险/警告/绑定/主色）；计数徽标为圆形蓝底。
- **面板**：白底 1 px 边框 8 px 圆角，无阴影；头部 44 px（标题 + 次要信息 + 操作）。
- **表格**：表头 34 px 灰底吸顶；行 40 px（紧凑 32 px）；悬停浅灰；操作列右对齐、不换行。
- **弹窗**：宽 480 / 720 / 960 px；最大高度 80vh，内容区滚动；底部操作右对齐（取消在左、主操作在右）。
- **格式标签**：XML 橙底、JSON 蓝底、其他灰底，34×18 px 等宽大写。

### 9.6 状态规范

| 状态 | 表现 | 文案规则 |
|---|---|---|
| 加载 | 列表/页面：居中转圈 + “正在…”；树：8 行骨架；按钮：按钮内转圈并禁用 | 只描述正在做什么，不显示假进度；>500 ms 才显示的位置可以先不显示 |
| 空 | 灰色圆形图标 + 标题 + 一句说明 + 可选主操作 | 说明“为什么空”和“下一步做什么” |
| 无匹配 | 与空区分：“没有找到“xxx””+ 建议 | 回显关键词 |
| 失败 | 红色圆形图标 + “加载失败” + 原因 + 重试 | 原因来自 `message`；网络错误统一“无法连接服务器，请检查网络或稍后重试” |
| 只读/无权限 | 黄色锁图标或灰色横幅 + 当前身份 + 谁可以做 | 例：“只读：当前身份为「成员」，只有部署者（在服务器本机访问）可以修改这些设置。” |
| 过期（他人更新） | 黄色横幅 + “重新加载” | 不自动替换内容 |
| 未保存 | 黄色横幅（DL）/ 标签圆点 / 关闭确认 | “参数已修改，尚未保存。” |
| 成功 | Toast；需要后续动作时带链接（如审阅生成文件） | 简短，动词开头：“已保存”“已收藏 param_02” |
| 进行中任务 | 顶栏蓝色胶囊“全量更新 2/3”，点击看详情 | — |

### 9.7 文案与术语

| 统一用词 | 不再使用 | 说明 |
|---|---|---|
| 配置文件 / 文件 | 全量配置 | — |
| 当前版本 / 历史版本 | 归档版本（界面中） | 存储层仍叫 archive |
| 修改记录 | 记录 | 指上游 record_url 拉取的文件，与历史版本区分 |
| 修改建议 | 修改备注 | 存储层仍为 edit_remarks |
| 审阅修改 | — | 管理员动作 |
| 更新源 | 更新链接 / 全量配置映射 | 设置中的映射表 |
| 机型 | 设备型号 / profile | — |
| 部署者 / 管理员 / 成员 / 访客 | 游客 | 角色名 |
| 收藏 / 速览 | 配置速览（页面标题简化为“速览”） | — |
| 绑定路径 | 变量路径 | 绑定使用的“文件名/节点/…”格式 |

按钮动词：导入并打开、保存、保存为配置、提交建议、提交审阅结果、开始对比、立即更新、全部立即更新、立即拉取、从配置刷新系数、应用到草稿、放弃修改。确认框标题用“动作 + 对象”，主按钮重复动作（“删除”“清空”“移除”）。

### 9.8 页面规格

#### 9.8.1 外壳（模板 `views/shell.js`）

- **机型切换**：点击显示菜单（勾选当前、显示 id），底部“管理机型…”。切换时若有未保存修改先确认；切换后加载该机型的标签与上次位置（不整页刷新）。
- **搜索框 / Ctrl+K**：打开命令面板：第一项“在全部文件中搜索 “kw””，随后最多 8 个匹配的文件、匹配的导航项；↑↓ 选择、Enter 打开、Esc 关闭。
- **身份按钮**：头像（姓氏首字）+ 姓名 + 角色徽标；弹层见 §4.3。
- **任务胶囊**：有进行中任务才出现；弹层列出任务、发起人、进度条，链接到“更新源”。
- **侧栏文件条目**：整行可点（打开文件）；右侧在悬停/聚焦时显示“下载当前版本”“更多（打开、版本历史、复制文件名、删除文件…）”；有待审建议时显示橙色徽标与数量。筛选框实时过滤。0 文件时显示空状态“点击上方 + 导入”。

#### 9.8.2 速览（`views/overview.js`）

- 左：按文件分组的收藏面板。头部：格式标签、文件名（可点击打开）、项数、更新时间、“清空”（确认）。表格列：变量（等宽）｜当前值（父级收藏显示子项列表，最多 12 项后折叠为“… 共 n 项”）｜备注（行内输入，失焦或 Enter 保存，Esc 还原）｜路径（截断 + title）｜操作（定位、取消收藏）。当前值与收藏时值不同 → 值下方灰字“收藏时为 x”；节点不存在 → 橙色“未检测到，请检查”+“收藏时：x”。
- “源文件已不存在”分组：黄色说明“文件恢复后会自动回到原分组”。
- 取消收藏 Toast 带“撤销”（重新收藏并恢复备注）。
- 右：最近更新（6 个文件，点击有历史的打开历史版本）；待审建议（管理员可见）；定时更新状态（启用/间隔/下次/上次结果）。
- 空：“还没有收藏变量”+ 指引 + “打开 <第一个文件>”。

#### 9.8.3 文件工作区 · 结构（`views/fileview.js`）

- **头部**：格式标签、文件名、（临时文件）黄色“临时 · 仅自己可见”、（非当前版本）蓝色版本徽标 + “回到当前版本”；右侧“下载 / 下载此版本”与“⋯”（复制文件名、复制本页链接、删除文件… / 临时文件：保存到服务器…）。元信息：更新时间、大小、节点数、内容指纹前 8 位、临时文件过期时间。分段：结构｜历史版本 (n)｜对比｜修改记录 (n)。
- **工具栏**：筛选框（名称/值/备注，160 ms 防抖，Esc 清空）+ 命中计数与上/下一个；展开菜单；全部折叠；“显示属性”开关（记忆）；只读徽标（历史/记录/临时）；详情面板开关。
- **树行**：缩进引导线（单一浅灰）→ 展开箭头 → 星标（实心 = 已收藏，浅色实心 = 被父级收藏覆盖）→ 标签 → `=` 值（超过 48 字符截断，完整值在详情面板）→ param 描述 · 编辑权限（灰色）→ 徽标：范围 `[min, max]`（含/不含用方/圆括号）、建议数（橙）→ 折叠时子项数 `(n)`。
- **param_ 节点规则**（沿用旧版）：标签以 `param_` 开头、有值、且子节点中含 `@description/@editPrivilege/@default/@min/@max/@incMin/@incMax` 任一 → 这些属性不作为子行显示，改为行内描述与范围徽标；其余属性（如 `@type/@units/@precisionLength`）仍为子行。
- **状态栏**（26 px）：节点总数、显示行数、加载耗时、快捷键提示。
- **详情面板**（选中节点后）：
  1. 值（完整、可选中、复制）或“n 个直接子项 · 共 m 个后代节点”；
  2. 参数信息：描述、编辑权限、类型、单位；默认值/最小值/最大值三格（含/不含）；当前值越界时红色“当前值超出参数范围”；普通元素显示属性列表；
  3. 位置：显示路径（复制为 `/` 分隔）、绑定路径（复制，供变量绑定与 DL 绑定使用）、折叠的“技术定位信息”（定位符、节点 ID、类型）；
  4. 收藏：按钮“收藏到速览 / 收藏此节点及子项 / 取消收藏 / 已被父级收藏包含（禁用）”，显示备注；
  5. 修改建议：该节点待审建议列表（提交人、时间、原值 → 建议值）；表单“建议值 + 提交建议”（Enter 提交）；历史/记录/临时版本或无值节点给出不可提交的原因；输入未提交时标签显示未保存圆点。
- **加载**：骨架行；无法解析 → 错误状态 + “下载原文”；不存在 → 错误 + 重试。

#### 9.8.4 文件工作区 · 历史版本

- 面板标题“历史版本”+ 归档数 + “对比所选（已选 n/2）”。
- 表格：选择框｜版本（等宽；当前版本带绿色“当前”）｜保存时间｜大小｜变更（相对较新版本，`+ − ~ ⛓`，悬停说明比较对象）｜操作：查看（在结构视图打开该版本）、与当前对比（直接进入对比并自动运行，修复旧版传参缺陷）、下载。
- 勾选任意两个版本 → “对比所选”：较旧者为基准、较新者为目标。
- 空：“还没有历史版本 · 每次覆盖时旧版本自动归档到这里”。

#### 9.8.5 文件工作区 · 对比

- 顶部：基准（旧，红色标签）选择器 ｜ 交换按钮 ｜ 目标（新，绿色标签）选择器 ｜ “开始对比”。选择器分组：当前版本、历史版本（名称 · 时间）、修改记录、已上传、“上传本地文件…”（选择后上传为临时文件，仅用于本次对比）。路由带 `base`、`target` 时自动运行。
- 结果：
  - 两侧相同 → 空状态“两个版本内容完全相同”。
  - 摘要卡片：新增 / 删除 / 修改（可点击筛选，再次点击取消）、涉及绑定变量数。
  - 结构化差异表：类型标签（新增/删除/修改，带文字）｜路径｜基准值（红）｜目标值（绿）｜绑定组（紫色徽标）｜“定位”（目标为当前版本时，跳到结构视图并选中节点）；表头上方筛选框与“仅绑定变量”开关；默认显示 300 条，“显示全部 n 条”。
  - 文本差异（可折叠，记忆展开状态）：行数、截断说明、复制、下载 `.diff`；新增行绿底、删除行红底、hunk 行蓝底。
  - 任一侧无法解析 → 提示“仅提供文本差异”。

#### 9.8.6 文件工作区 · 修改记录

- 面板头：标题、来源 URL（截断 + title）、“立即拉取”（部署者，创建 `refresh_records` 任务；完成后列表自动刷新）。
- 表格：记录文件｜拉取时间｜大小｜相对上一条（`+ − ~`，最早一条显示“最早一条”）｜操作：查看（结构视图 `version=record:…`）、与上一条对比、下载。
- 未配置链接 → 空状态 + （部署者）“前往配置”。说明“修改记录由更新源提供，与历史版本是两类数据”。

#### 9.8.7 DL 计算（`views/dltool.js`）

- 头部分段：PP 能量｜RP 能量｜RP 脉宽（有未保存修改的项显示“•”）。
- 左“函数与参数”：公式预览（只显示非零项；PP 追加“PP = f(x) × 传输系数 ÷ 功率转换系数”）；系数 3×3 网格（k₁…k₈、b，等宽，失焦校验，非法值红框 + Toast，不写入）；PP 换算参数；有效范围 4 格（留空 = 不限制，保存为 `null`）；系数绑定（来源文件可点击定位、变量路径、字段映射；“从配置刷新系数”→ 预览表：字段｜系数｜当前｜提取值｜状态（将更新/未变化/字段缺失/非数值）→“应用到草稿”；“修改绑定”对话框：文件选择、变量路径、映射文本 `字段=系数`，逐行校验）；多解检测横幅（参数变化 300 ms 后由服务端检测）。
- 右“计算”：分段 正向 x→y / 反向 y→x；多行输入（空格、逗号、分号、换行分隔，无法识别的片段 Toast 列出并忽略）；Ctrl+Enter 计算；结果表：序号（与输入顺序一一对应）｜输入｜（PP 正向：f(x)）｜输出｜状态（正常 / x 超出范围 / y 超出范围 / 唯一解 / n 个解 / 范围内无实数解 / 常数函数）；多解行提供下拉“选择采用的解”，复制 CSV / 下载 CSV 使用该选择；横幅说明“系统不会自动替你挑选”。反向结果标注“为在范围内检测到的候选根”。
- 草稿与保存：任何参数修改进入本地草稿 → 黄色横幅“参数已修改，尚未保存”+“放弃修改”+“保存为配置”（仅部署者；`If-Match` 冲突时提示加载最新或保留草稿）。非部署者可用草稿试算。
- 校验规则（前后端一致）：系数与换算参数必须是有限数；功率转换系数 ≠ 0；`x_min < x_max`、`y_min < y_max`（两者都设置时）；输入值必须有限。

#### 9.8.8 全局搜索（`views/search.js`）

- 搜索栏（38 px 大输入框）+ 范围选择（全部文件 / 单个文件）+ “搜索”；回车提交并写入路由。
- 结果摘要“共 n 个匹配，分布在 m 个文件（已显示 k）”；按文件分组面板（格式标签、文件名、命中数、“打开文件”）；每条命中：标签 = 值（关键字高亮）、匹配字段徽标（名称/值/备注）、路径、收藏备注；点击或 Enter → 打开文件并定位节点。
- 分页：每次 200 条，“加载更多结果”。
- 空：提示 Ctrl+K；无结果：“没有找到“kw”· 搜索只覆盖当前版本”。

#### 9.8.9 审阅修改（`views/review.js`）

- 头部：说明、“已决定 a / b”、刷新。
- 左列（300 px）：按文件分组，每组标题显示 `已决定/总数`；节点条目：标签、状态徽标（已变化 / 旧版）、决定徽标（通过 / 不通过 / n 条）。
- 右侧节点详情：文件 › 路径；“提交时的原值 → 文件当前值”对照；警示横幅（值已变化 → 只能驳回；无法唯一定位 → 只能驳回；legacy → 已按路径唯一匹配并核对原值）；单选列表：每条建议（建议值、提交人、时间）+ “不通过（驳回该节点的全部建议）”；“在文件中查看该节点”；“清除选择”。
- 提交区：说明“可以只提交已决定的节点…原文件不会被覆盖”+“提交 <文件> 的审阅结果（已决定/总数）”。确认框列出通过项（原值 → 新值）与驳回数，以及将生成的文件名模式。
- 成功横幅：“审阅完成：通过 n 条，驳回 m 条。已生成 <文件（可点击打开）>”+“下载”。`REVIEW_CONFLICT` → 错误 Toast 并自动刷新。
- 非管理员：锁定状态“仅管理员可审阅”+ 当前角色 + 如何获得权限。

#### 9.8.10 设置（`views/settings.js`）

通用：左侧 208 px 子导航；每页标题 + 一句说明 + 主操作；非部署者显示只读横幅且写按钮禁用；所有表单错误显示在字段下方（`detail.field`）；保存后局部刷新（不整页 reload，C29）。

| 子页 | 内容 |
|---|---|
| 更新源 | 主操作“全部立即更新”（任务进行中禁用并显示“进行中…”）、“新增更新源”；表格：文件（本地版本时间 / “尚未下载”）｜下载链接与修改记录链接｜最近结果（状态点 + 文案 + 时间 + 原因）｜操作：更新、⋯（编辑、打开文件、删除更新源…）；编辑对话框含“改名时同时迁移本地文件、历史版本、收藏与建议”；页脚说明更新的校验流程 |
| 定时更新 | 启用开关；间隔数值 + 单位（小时/天，天按 24 小时换算存储）；保存；运行状态：状态、下次执行、上次执行结果 |
| 人员与权限 | IP 对应表（IP、姓名、角色、更新时间；当前访问者标“你”；编辑/删除）；管理员列表（姓名、已登记 IP，未登记显示“未登记 IP，暂不生效”）；删除/移除确认说明影响 |
| 变量绑定 | 表格：绑定组（紫色徽标）｜变量列表（格式标签 + 绑定路径）｜编辑/删除；编辑对话框：组名 + 至少 2 行（文件下拉 + 路径输入 + 移除），“添加变量” |
| 工具菜单 | 表格：名称｜说明｜地址｜编辑/删除；URL 校验 http/https |
| 机型管理 | 表格：名称｜标识｜当前/默认｜切换、重命名、删除（输入标识确认，数据移入恢复区）；新增对话框：名称 + 初始数据（空白 / 复制某机型，说明复制范围） |

#### 9.8.11 导入对话框（`views/importer.js`）

- 分段：本地文件（拖放区，支持点击/键盘，显示大小上限；选择后显示文件卡片可移除）｜从链接下载（URL，输入时自动推断文件名）。
- 文件名（等宽，必填，校验非法字符）；导入方式单选：保存到服务器（仅部署者，否则禁用并说明）/ 临时查看（仅自己可见，n 小时后清除）。
- 同名且选择保存 → 黄色提示“保存后当前版本会自动归档”。
- “导入并打开”：成功后打开文件标签（或临时标签）；内容与服务器版本相同 → “内容与服务器版本一致，未产生新的历史版本”。错误：同名 → 字段错误；解析失败 → “无法解析该文件”+ 原因；下载失败 → 原因。

### 9.9 可访问性与键盘（基线，非 WCAG 合规声明）

- 所有交互元素可 Tab 到达，焦点环可见；图标按钮有 `aria-label`；弹窗焦点陷阱与焦点返还；菜单 ↑↓。
- 状态不只靠颜色：差异类型有文字标签、diffStats 有 `aria-label`、状态徽标有文字。
- 表单 label 关联、错误 `role=alert` + `aria-invalid`；Toast `aria-live=polite`。
- 全局快捷键：Ctrl/⌘+K 命令面板；树内快捷键见 §8.7；DL 输入 Ctrl+Enter。
- 文字对比度：正文 ≥ 7:1，说明文字 ≥ 5.5:1（`#5d6676` on `#fff`）。
- `prefers-reduced-motion` 关闭动画。

### 9.10 响应式

| 宽度 | 变化 |
|---|---|
| > 1180 px | 速览双栏；DL 左右双栏 |
| ≤ 1180 px | 速览、DL 改为单栏 |
| ≤ 1100 px | 搜索框隐藏快捷键提示 |
| ≤ 960 px | 侧栏变覆盖抽屉（顶栏按钮开关）；详情面板变右侧覆盖抽屉；设置/审阅左侧导航变为顶部 200 px 区；隐藏品牌文字与身份姓名；对比选择器纵向排列 |

---

## 10. 后台任务与定时更新

### 10.1 TaskRunner（`services/tasks.py`）

```python
class Task:  # 字段同 §7.10
    ...

class TaskRunner:
    def __init__(self, max_workers: int = 4): ...
    def submit(self, ctx: RequestContext, ttype: str, target: Optional[str], fn: Callable[[Task], None]) -> Task:
        """key=(pid, ttype, target or '*')；同 key 有 queued/running → AppError(BUSY, detail={'task_id'})。
        update_all 与同机型任意 update_source 互斥（同一机型同时只有一个“更新类”任务修改同一文件）。"""
    def get(self, task_id: str) -> Optional[Task]: ...
    def list(self, pid: str, active_only: bool) -> List[Task]: ...
    def shutdown(self, timeout: float = 30) -> None:
        """停止接收新任务，等待运行中任务至多 timeout 秒。"""
```

- `fn` 在工作线程中执行，内部通过 `task.progress`/`task.result.items.append()` 更新，结束设置 `state`；任何异常 → `failed` 并记录 `error`（用户可读）与日志堆栈。
- 每次进度变化递增机型的 `tasks` 修订计数；结束时按类型递增：`update_all` 递增 `settings`，`update_source` 的 `files` **只在文件字节真的变了时**由服务层递增（runner 不补，否则 `unchanged` 会给所有客户端弹虚假的「文件已更新」横幅）。

### 10.2 更新源更新（`services/updates.py::update_source`）

```text
输入：pid, name, actor（人或 system）
1. 读映射；无此项 → not_found；无 url → skipped（“无下载链接”）
2. downloader.fetch(url, policy)：
     - 只允许 http/https；拒绝 userinfo 之外的非法 URL
     - 解析主机 → 地址；拒绝 0.0.0.0/::、169.254.0.0/16、fe80::/10；
       下载白名单非空时目标必须命中；访客临时导入额外拒绝 127.0.0.0/8 与 ::1
     - 自定义 HTTPRedirectHandler：最多 5 次重定向，每跳重新执行上述检查
     - 超时 settings.download_timeout_s；分块读取，累计 > upload_max_bytes → TOO_LARGE
     - 失败重试 settings.download_retries 次（间隔 1s、2s）
     - 失败 → download_failed（reason：HTTP 状态 / 超时 / 连接失败 / 被策略拒绝）
3. to_compact(content, name) 失败 → rejected（“下载内容不是有效的 XML/JSON”）
4. 结构指纹：当前文件存在时比较 XML 根元素 tag / JSON 根类型（object/array）；不同 → rejected
   （“根节点由 A 变为 B，可能下载到了错误页面，已拒绝替换”）
5. sha256 与当前相同 → unchanged（“内容与当前版本一致，未归档”），只更新 update_status
6. 机型锁内：重新读取当前 sha 确认未变 → storage.save_config_file（§5.4）→ success（archived=归档名）
7. 写 update_status.json；递增 files 修订
```

`update_all(pid)`：对映射逐项执行 `update_source` 与 `refresh_records`（有 record_url 时），汇总写 `schedule.json.last_run`。

### 10.3 修改记录拉取（`refresh_records`）

下载 → 若与最新一条记录 sha256 相同 → `unchanged`（不再重复生成记录文件，C13）→ 否则按 §5.3 命名保存。记录内容不要求可解析（无法解析的在查看时显示原文下载）。**页面不再有自己的定时器**；只有调度器与“立即拉取”按钮会触发。

### 10.4 调度器（`services/scheduler.py`，替代 APScheduler）

```python
class Scheduler(threading.Thread):
    def __init__(self, runner: TaskRunner): daemon=True
    def reload(self) -> None: """读取全部机型 schedule.json，重算 next_run；设置内部 Event 唤醒循环。"""
    def run(self) -> None:
        """循环：计算最早 next_run；Event.wait(min(剩余秒数, 60))；到点的机型提交 update_all（actor=system）；
        提交后 next_run = now + interval（BUSY 则 1 分钟后重试）。每小时清理临时区；每天清理缓存。"""
    def stop(self) -> None
    def next_run_at(self, pid) -> Optional[datetime]
```

- `next_run` 初值：`schedule.last_run.at + interval`（若已过期 → 启动后 2 分钟执行，避免启动风暴）；无 `last_run` → `启动时间 + interval`（与 APScheduler IntervalTrigger 行为一致）。
- 修改定时、增删机型后调用 `reload()`。
- 停止服务时：`scheduler.stop()` → `runner.shutdown(30)` → `server.shutdown()`。

---

## 11. 安全设计

### 11.1 身份
见 §4。IP 识别不是强认证：在 README/手册中明确“适用于可信内网；共享电脑或不可信网络需要另行评估”。反向代理部署必须配置 `MCHECKER_TRUSTED_PROXIES`，否则所有请求看起来来自代理地址（可能被识别为部署者）——启动时若检测到请求带 `X-Forwarded-For` 而未配置受信代理，日志 WARNING 一次。

### 11.2 输入与路径
- 文件名一律 `validate_filename`；组名、工具名、姓名：去空白后 1–100 字符、无控制字符。
- 所有拼接到 data 根下的路径经 `resolve_within`。
- JSON 请求体 ≤ 1 MB；原文上传 ≤ `upload_max_bytes`；`Content-Length` 缺失的带体请求 → 411。
- XML 拒绝 DTD 实体（§6.1）；节点/深度上限。

### 11.3 输出（XSS）
- 前端只用文本节点渲染数据（§8.1）；服务端 JSON 响应 `Content-Type: application/json; charset=utf-8`；原文下载 `Content-Disposition: attachment` + `X-Content-Type-Options: nosniff`，**不在浏览器内联渲染原文**。
- 全站响应头：`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: same-origin`、`Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'`（生产 index.html 不含内联脚本，`config.js` 为同源文件）。

### 11.4 CSRF
状态变更接口只接受 JSON 或 `application/octet-stream` 请求体，并要求请求头 `X-Requested-With: MCChecker`（`MC.api` 自动添加；跨站表单无法设置自定义头）。缺少该头的写请求 → 403。

### 11.5 敏感信息
日志不记录完整配置值、建议值、Cookie、URL 中的 userinfo 与 query（记录为 `scheme://host/path`）。错误响应不含绝对路径与堆栈。

---

## 12. 性能设计与预算

| 场景 | 预算（参考：4 核 / 8 GB / 千兆内网 / Chrome） | 手段 |
|---|---|---|
| 首屏（冷缓存） | ≤ 1.0 s 可交互 | 静态资源 ≈ 90 KB gzip；无框架；ETag 304 |
| 打开 5 万节点文件（服务端缓存命中） | 请求 ≤ 300 ms，渲染 ≤ 150 ms | 内存 LRU 存**已序列化**的响应体，gzip 每次响应现压（M4-05 实测：现压多花 ≈16 ms，缓存压缩体不划算，见 §6.5）；前端只渲染可视行 |
| 打开 30 万节点文件（冷） | ≤ 3 s，显示骨架 | 解析一次后写 doc_v2 缓存 |
| 树滚动 / 展开 | 60 fps；单次展开 ≤ 50 ms（5 万节点） | `size[]` 跳跃、rAF 节流、事件委托 |
| 文件内筛选 | 5 万节点 ≤ 80 ms | 预计算小写数组；160 ms 防抖 |
| 全局搜索 | 30 文件 × 3 万节点 ≤ 500 ms | 缓存文档 + 数组扫描；分页 |
| 对比 | 2 × 5 万节点结构化 ≤ 800 ms | loc 字典；文本差异 > 20 000 行跳过 |
| 修订号轮询 | 50 客户端 × 每 10 s，服务端 CPU < 1% | 纯内存计数 |
| 服务端内存 | 基础 < 80 MB；+LRU ≤ 256 MB；与在线人数基本无关 | 无会话 UI 状态 |

模板实测（本仓库 mock，浏览器内解析）：5.4 万节点 `large_params.xml` 加载 + 索引约 320 ms，首屏只渲染 42 行。

测量方法：`scripts/bench_api.py`（T-27）用 `http.client` 对固定样本计时并输出 P50/P95；前端在状态栏显示“加载 N ms”。

---

## 13. 日志与错误

- 使用标准 `logging`，格式 `%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s`；控制台 + `data/logs/mcchecker.log`（RotatingFileHandler 10 MB × 5）。
- 访问日志 INFO：`GET /api/v1/p/default/files 200 12ms ip=10.12.3.21 pid=default`。
- 业务日志：任务开始/结束、文件保存/归档、审阅生成、删除进恢复区、迁移、损坏数据，均含 `profile`、`actor`、`target`、`result`、`duration`。
- `AppError(code, message, status=…, detail=None)` 是唯一的业务异常类；不要按错误码建子类。

---

## 14. 部署与离线发布

### 14.1 运行要求
- Python 3.9.11（官方安装包或项目内嵌的 Windows embeddable 包，见 14.3）。无需 pip 安装任何包。

### 14.2 启动 / 停止
```bash
python run.py                      # 或 python main.py（兼容）
python run.py --port 50002 --data-dir D:\MCChecker\data
```
- 成功标志：日志 `MCChecker 2.0.0 listening on http://0.0.0.0:50002 (data=…, profiles=2, scheduler=on)`。
- 停止：前台 Ctrl+C（触发 §10.4 的有序停止）；Windows 服务/计划任务方式见手册。

### 14.3 离线发布包（`scripts/make_release.py`，标准库实现）
- 生成 `dist/MCChecker-2.0.0.zip`：包含 `run.py`、`main.py`、`app/`（排除 `app/web/assets/js/mock/`、`app/web/template.html`、`app/web/tests/`、`__pycache__`、`*.pyc`）、`docs/manual.md`、`README.md`、`start.bat`/`start.sh`。**不包含** `data/`、`.nicegui/`。
- 可选：把 `python-3.9.11-embed-amd64.zip` 解压到 `runtime/python/`（由发布人员在有网环境下载一次放入），`start.bat` 优先使用 `runtime\python\python.exe`，否则使用系统 `python`。
- 升级：停止旧版 → 备份整个 `data/`（复制）→ 解压新版本覆盖程序文件（不覆盖 `data/`）→ 启动 → 检查日志的迁移与恢复提示。回滚：停止 → 恢复程序文件 → 启动（新版只新增可选字段与新目录，旧版可读）。

### 14.4 开发与测试依赖
- `requirements-dev.txt`：`pytest==7.4.4`（最后支持 3.7+ 且无额外编译依赖的稳定版本之一；选择理由：项目现有测试基于 pytest fixtures）。离线环境可在有网机器执行 `pip download -r requirements-dev.txt -d tools/wheels`，目标机 `pip install --no-index --find-links tools/wheels -r requirements-dev.txt`。
- 运行时代码不得 import pytest。

### 14.5 文档同步（实现完成后必须更新）
`README.md`（技术栈、快速开始、端口）、`requirements.md`（零依赖、Python 3.9.11）、`requirements.txt`（清空为注释）、`docs/manual.md`（启动、配置、权限、恢复）、`docs/architecture.md`（指向本文并概述新结构）、`docs/know-how.md`（3.9 兼容、IP 识别与代理、恢复区）。

### 14.6 手工恢复（运维）
1. 停止服务；2. 在 `data/recovery/<目录>/manifest.json` 查看 `moved` 列表；3. 把每项 `to` 复制回 `from`；4. 如需恢复引用，把 `removed_refs` 中的条目追加回对应 JSON；5. 启动。T-29 提供 `python run.py --restore <目录> --dry-run` 打印将执行的步骤（可选任务）。

---

## 15. 实施计划（任务卡）

依赖顺序：M0 → M1 → M2 → M3 → M4。每张卡可独立提交、独立回滚。“文件”列出主要新增/修改文件；“验收”必须全部满足。

### M0 · 基础（先做，解除 3.9 阻塞）

**T-01 Python 3.9 兼容与测试隔离**
- 文件：`app/**/*.py`、`tests/conftest.py`
- 步骤：全部模块加 `from __future__ import annotations`；替换 `X | None`；`tests/conftest.py` 增加 autouse fixture：`tmp_path` 下建立 data 根并 monkeypatch `storage.DATA_DIR/PROFILES_DIR/CONFIGS_DIR/ARCHIVE_DIR/CACHE_DIR/PARSE_CACHE_DIR/*_FILE` 与 `device_models._models_file`；修正 `test_differ` 中 `>= 0` 无效断言为确定值。
- 验收：`python3.9 -m pytest -q` 全部通过；测试前后仓库 `data/` 内容哈希不变；`python3.9 -c "import app.core.storage, app.core.differ, app.core.dltool"` 成功。

**T-02 fsutil 与 locks**
- 文件：`app/core/fsutil.py`、`app/core/locks.py`、`tests/test_fsutil.py`
- 验收：原子写在写入中途抛异常时旧文件内容不变（monkeypatch `os.replace` 抛错）；`read_json` 对损坏文件抛 `CorruptDataError`；`validate_filename` 覆盖 `../a`、`a\\b`、`C:x`、`CON.xml`、`a.`、空串、181 字符（拒绝）与 `中文配置.xml`（接受）；`resolve_within` 拒绝符号链接越界（Linux 上测试）。

**T-03 storage 改造（行为兼容）**
- 文件：`app/core/storage.py`、`app/core/device_models.py`、`app/core/dltool.py`
- 步骤：删除 NiceGUI 回退；`_save_json/_load_json` 改用 fsutil；所有读改写函数包 `profile_lock`；`save_config_file` 按 §5.4；归档/记录唯一命名；`copy_profile_data` 不复制 cache；`update_config_mapping` 迁移 records、remarks、DL 绑定引用；`_normalize_profile_id` 对非法 id 抛 `AppError(NOT_FOUND)` 而非回退 default（HTTP 层先校验 exists）。
- 验收：现有 `test_storage.py`、`test_profiles.py` 全部通过；新增：同秒两次保存产生两个不同归档；相同内容保存不归档；并发 20 线程各添加 1 个收藏后共 20 条。

**T-04 config 与启动骨架**
- 文件：`app/config.py`、`run.py`、`main.py`、`app/server/http.py`、`app/server/router.py`
- 步骤：`load_settings`；单实例锁；ThreadingHTTPServer；静态文件服务（`app/web`，排除 mock/template/tests）；`/config.js`；`/api/v1/session` 先返回固定身份；有序停止。
- 验收：`python3.9 run.py --port 50110` 启动日志正确；`curl /` 返回 index.html；`curl /assets/js/mock/mock-api.js` → 404；`MCHECKER_PORT=abc` 启动失败并提示。

### M1 · 读取链路

**T-05 compact 解析器**：`parser.to_compact` 按 §6.1/6.2；`tests/test_compact.py` 对照模板测试用例（XML occurrence、属性、#text、命名空间、JSON pointer 转义、类型名、旧 ID 与 `parse_file` 的 id 集合一致）；对 `test.xml` 与 200 KB 以上样本断言属性不丢失。

**T-06 文档服务与缓存**：`services/documents.py`（§6.5）；`GET /files`、`/document`、`/raw`；ETag/304；gzip。验收：二次请求命中内存缓存（日志 debug 可见）；修改文件后返回新 sha；`version=archive:../x` → 400。

**T-07 身份与权限**：`services/auth.py`、`services/context.py`、权限矩阵装饰器 `@require("deployer"|"admin")`；`/session` 完整实现与 `mc_cid`。验收：§16.3 权限用例全部通过。

**T-08 前端接入真实后端**：`app/web/index.html`（生产入口）、`MC.api` 增加 `X-Requested-With` 头；去掉 mock 后能浏览文件列表与结构树。验收：§16.2 UC-01～UC-06 在真实后端通过。

### M2 · 写入链路

**T-09 收藏**（§7.7，`favorites_live` 改为基于 compact）
**T-10 修改建议**（§6.7 提交部分，JSON 类型检查）
**T-11 搜索**（§7.6，计数修正）
**T-12 历史与差异摘要**（§7.4 archives/records、§6.6 摘要缓存与旧 `.diff.json` 兼容）
**T-13 对比**（`compare_documents`、`/compare`、临时文件参与对比）
**T-14 导入与临时文件**（`PUT /files`、`/temp`、`/import-url`、`temp_store` 清理）
**T-15 删除与恢复区**（`DELETE /files`、`recovery.py`、manifest）
- 每卡验收：对应接口单元测试（`tests/test_api_<领域>.py`，在测试中用随机端口启动 server 线程，`http.client` 请求）+ §16.2 对应 UI 用例。

### M3 · 审阅、更新、计算、设置

**T-16 审阅写回**：`reviewing.apply_edits` + 校验 + `/review`、`/review/submit`（§6.7）。验收：§16.2 UC-20～UC-24；`tests/test_review_apply.py` 覆盖：XML 属性值修改、元素文本修改、同名兄弟只改第 2 个、命名空间文档、带注释文档注释保留、JSON int/float/bool 类型保持、类型不符拒绝、校验失败返回 REVIEW_UNSAFE 且不写文件、重复 request_id 幂等、legacy 建议唯一匹配可用/重复 ID 拒绝。
**T-17 下载器与更新**：`downloader.fetch`（§10.2 策略）、`updates.py`、`/sources` CRUD、`update_status.json`。验收：本地 `http.server` 提供合法/非法/错误页/超大/重定向到 169.254 的样本，结果分别为 success/rejected/rejected/TOO_LARGE/download_failed；相同内容 unchanged 且无新归档。
**T-18 任务与调度**：`tasks.py`、`scheduler.py`、`/tasks`、`/schedule`；删除 APScheduler。验收：interval_hours=1 时 `next_run_at` 正确；手动与定时同时触发返回 BUSY；停止服务时等待任务结束。
**T-19 修改记录**：`refresh_records`、记录列表/查看/对比、`/record_view` 302。
**T-20 DL**：`core/dltool.py` 增加 `find_roots`（采样 + 二分 + 切触检测 + 端点 + 常数函数，算法与 `mock/dlmath.js` 一致）、`forward/reverse/multi_check`；`/dl` 全部接口与 If-Match。验收：`tests/test_dltool_multi.py` 继续通过；新增用例与前端测试页 DL 用例一致（(x-3)² 切触根、越界标注、input_index 对应、常数函数、功率转换系数 0 拒绝、提取缺失字段不置 0）。
**T-21 设置其余项**：IP 表、管理员、绑定、工具、机型 CRUD（§7.2、§7.9）。
**T-22 修订号与多用户**：`/revision` 计数接入所有写路径。验收：§16.2 UC-40。

### M4 · 收尾

**T-23 旧路由兼容**（§7.12）。**T-24 安全头与 CSRF**（§11.3–11.4）。**T-25 日志**（§13）。**T-26 旧布局迁移改造**（§5.5）。**T-27 性能基准脚本**（§12）。**T-28 离线发布脚本**（§14.3）。**T-29 恢复辅助（可选）**。
**T-30 移除 NiceGUI**：删除 `app/pages/`、`app/static/`、`app/utils/auth.py` 的 NiceGUI 部分、`.nicegui/` 的版本控制跟踪（`git rm --cached`，不删部署机文件）、`requirements.txt` 依赖；更新 §14.5 文档。验收：`grep -r "nicegui\|apscheduler" app run.py` 无结果；全部测试在 3.9.11 通过；§16 全部验收完成。

---

## 16. 验收方案

### 16.1 自动化测试入口

| 层 | 命令 | 通过标准 |
|---|---|---|
| Python 单元/集成 | `python3.9 -m pytest -q` | 0 失败；仓库 `data/` 未被修改 |
| 前端纯逻辑 | 浏览器打开 `app/web/tests/index.html`（或 `google-chrome --headless=new --dump-dom …/tests/index.html` 检查 `<title>ALL PASSED`） | 全部通过 |
| 接口 | `tests/test_api_*.py`（随机端口启动真实 server） | 全部通过 |
| 性能 | `python3.9 scripts/bench_api.py --samples tests/fixtures/perf` | 满足 §12 |

### 16.2 功能用例（UI，可用 `template.html` 预演，最终在真实后端执行）

| 编号 | 场景 | 步骤 | 期望 |
|---|---|---|---|
| UC-01 | 首次进入 | 清空 localStorage 打开 `/` | 显示速览；顶栏机型“默认机型”；身份显示正确角色（本机 = 部署者） |
| UC-02 | 打开文件 | 侧栏点击 `scenario_a.xml` | 新标签；结构树；状态栏显示节点数与加载耗时 |
| UC-03 | param 节点 | 选中 `param_03` | 行内显示描述 · 编辑权限、范围徽标；详情面板显示默认/最小/最大（含/不含） |
| UC-04 | 键盘浏览 | 树内 ↑↓←→、Enter、S | 选择/折叠/展开/详情/收藏均可用 |
| UC-05 | 文件内筛选 | 输入 `param_1` 后 Enter/Shift+Enter | 显示命中计数并跳转；Esc 清空 |
| UC-06 | 显示属性开关 | 关闭“显示属性” | 属性行隐藏；param 行内信息仍在 |
| UC-07 | 大文件 | 打开 5 万节点文件，快速滚动、全部展开 | 无卡顿（§12）；默认只展开前两层 |
| UC-08 | 收藏叶子 | 星标 `param_02` | 速览出现实时值；再次点击取消 |
| UC-09 | 收藏父节点 | 收藏 `header` | 子节点星标为“被父级覆盖”；点子节点星标提示先取消父级 |
| UC-10 | 速览备注 | 修改备注后 Enter | Toast 已保存；刷新后仍在；其他用户 10 s 内可见 |
| UC-11 | 速览缺失 | 删除收藏所在文件 | 该组移到“源文件已不存在”；文件恢复后回到原组 |
| UC-12 | 撤销取消收藏 | 速览取消收藏 → 撤销 | 收藏与备注恢复 |
| UC-13 | 提交建议 | 选中有值节点输入建议提交 | 建议出现在列表；侧栏与树显示建议数；与当前值相同时报字段错误 |
| UC-14 | 历史版本 | 打开“历史版本” | 当前 + 归档，统计正确 |
| UC-15 | 历史直达对比 | 第 2 个归档点“与当前对比” | 对比页基准 = 该归档、目标 = 当前，自动运行 |
| UC-16 | 勾选两个版本对比 | 勾选任意两行 → 对比所选 | 较旧为基准 |
| UC-17 | 上传对比 | 对比选择器选“上传本地文件…” | 上传后作为一侧参与对比；服务器文件不变 |
| UC-18 | 结构化差异 | 点击“修改”卡片、筛选、仅绑定变量、定位 | 筛选正确；绑定组紫色；定位跳到结构视图选中节点 |
| UC-19 | 文本差异 | 展开文本差异、复制、下载 .diff | 内容正确；HTTP 下复制可用或给出明确失败提示 |
| UC-20 | 审阅通过 | 管理员为 2 个节点选择建议并提交 | 生成 `原名_时间戳.ext`；原文件 sha 不变；新文件仅这两个值不同；建议状态更新 |
| UC-21 | 部分审阅 | 只决定 1/3 个节点提交 | 未决定的仍为待审 |
| UC-22 | 审阅冲突 | 审阅期间更新源文件后提交 | REVIEW_CONFLICT，自动刷新，已变化节点只能驳回 |
| UC-23 | legacy 建议 | 使用旧数据中的无 locator 建议 | 唯一匹配时可审阅并标“旧版”；不唯一时只能驳回 |
| UC-24 | 审阅草稿 | 选择后切换到其他标签再回来 / 刷新页面 | 选择仍在 |
| UC-25 | 全局搜索 | Ctrl+K 输入 `firmware` → 回车 | 搜索页分组结果；点击结果打开文件并选中节点 |
| UC-26 | 搜索计数 | 搜索命中父节点名的关键字 | 结果不为 0（修复旧缺陷） |
| UC-27 | 导入保存 | 部署者上传新文件 | 文件出现在侧栏并打开；同名覆盖时生成归档；内容相同不归档 |
| UC-28 | 临时查看 | 访客上传与服务器同名文件 | 打开临时标签显示上传内容；服务器文件不变；其他浏览器无法打开该 token |
| UC-29 | 链接导入失败 | 输入不可达 URL | 明确失败原因；不产生文件 |
| UC-30 | 修改记录 | 立即拉取 → 查看 → 与上一条对比 | 任务进度显示；记录列表刷新；内容相同不新增记录 |
| UC-31 | 更新源 | 新增、编辑（改名迁移）、删除、单条更新、全部更新 | 状态列显示 success/unchanged/download_failed/rejected 与原因；改名后收藏/建议/历史跟随 |
| UC-32 | 错误页保护 | 更新源返回 HTML 错误页 | rejected，当前版本不变 |
| UC-33 | 定时 | 设为 2 天保存 | `interval_hours=48`；下次执行时间正确；页面显示“2 天” |
| UC-34 | IP 与管理员 | 登记当前 IP 并加为管理员 | 刷新身份后显示管理员，侧栏出现“审阅修改” |
| UC-35 | 变量绑定 | 新增 2 变量的组 | 对比中这些变量带紫色组名；少于 2 个时字段报错 |
| UC-36 | 工具菜单 | 新增 `ftp://x` → 报错；改 http 保存 | 侧栏外部工具出现并新窗口打开 |
| UC-37 | DL 正向/反向 | PP 输入 `10 20 200`；RP 反向输入 `60` | 越界行标注；多解给出选择；CSV 使用选择 |
| UC-38 | DL 草稿与保存 | 访客修改系数试算；部署者保存；两人同时保存 | 访客不能保存；后保存者收到 REVISION_CONFLICT |
| UC-39 | DL 提取 | 从配置刷新系数，其中一个字段缺失 | 预览标“字段缺失”；该系数保持原值 |
| UC-40 | 多用户提示 | A 打开文件；B 更新该文件 | A 在 10 s 内看到黄色横幅，点“重新加载”后显示新内容 |
| UC-41 | 机型 | 新增（复制）、切换、重命名、删除 | 复制范围符合 §7.2；删除进入恢复区；切换不整页刷新 |
| UC-42 | 标签 | 打开 16 个文件 | 最久未用的无草稿标签被关闭并提示；有草稿的不被关闭 |
| UC-43 | 链接分享 | 复制本页链接在另一浏览器打开 | 打开同一机型/文件/版本/节点 |
| UC-44 | 访客权限 | 访客访问设置、审阅、删除 | 只读横幅/锁定状态；直接调用写接口返回 403 |
| UC-45 | 响应式 | 900 px 宽 | 侧栏与详情面板为抽屉，功能可用 |
| UC-46 | 旧链接 | 打开旧 `/api/file-download?...` 与 `/record_view?...` | 分别下载/跳转到正确位置 |

### 16.3 权限用例（接口级）

对 §4.2 矩阵中每个写接口，分别以访客、成员、管理员、部署者调用：允许的返回 2xx，不允许的返回 403 且对应文件 sha256 不变。管理员被移出名单后，再次提交审阅返回 403。

### 16.4 性能用例
`tests/fixtures/perf/` 放置 5 万、30 万节点样本（由脚本生成，不入库大文件），执行 §12 测量并记录结果到 PR。

### 16.5 数据兼容用例
1. 复制一份真实脱敏的旧 `data/`（含旧布局根目录文件、`.diff.json`、legacy 建议、`editing: true` 的 DL 配置）到测试目录启动；
2. 全部文件、历史、记录、收藏、绑定、建议、工具、定时、人员、管理员、DL 参数在新界面可见且一致；
3. 在新版做一次保存/审阅/收藏后，用旧版（v1.0.1，Python 3.12）启动同一数据副本仍可读取。

### 16.6 Python 版本
在 3.9.11 下执行 16.1 全部；额外 `python3.9 -m compileall -q app run.py main.py`。

### 16.7 离线
目标机器断开外网：解压发布包 → 启动 → 空缓存浏览器访问；浏览器网络面板中**不存在**任何非本服务器的请求；核心流程 UC-02/08/13/15/20/27 通过。

### 16.8 安全
- 构造含 `<img src=x onerror=alert(1)>` 的标签/值/备注/工具名，所有页面以文本显示且不执行。
- 工具 URL `javascript:alert(1)` 保存被拒绝。
- 路径参数 `..%2F..%2Fetc%2Fpasswd`、`C%3A%5Cwin.ini` → 400/403。
- 无 `X-Requested-With` 的跨站 POST → 403。

---

## 17. 风险与开放问题

| 风险 | 影响 | 应对 |
|---|---|---|
| 标准库 XML 重写改变格式（引号、空元素、实体） | 生成文件与原文 diff 噪声；极少数下游工具若做字节比较会受影响 | §6.7 语义校验保证值正确；在审阅确认框说明；若真实样本出现必须字节保真的需求，再评估基于 expat 偏移的文本级替换（仅替换值的字节区间） |
| tail 文本不显示 | 混合内容 XML 中元素之后的文本看不到 | 与旧版一致；如样本需要，后续增加 `#tail` 行（新增节点类型，不影响旧 ID） |
| IP 识别 | 共享电脑、代理配置错误会导致身份错误 | §11.1 文档说明与启动告警 |
| 30 万节点以上文件 | 浏览器内存与传输变大 | §6.1 上限；若真实需要，增加按子树懒加载接口（不改变现有接口） |
| 旧建议 node_key 不可靠 | 部分旧待审建议无法自动应用 | 唯一匹配 + 原值核验，否则只能驳回（明确提示） |
| 单进程 | 无法水平扩展 | 已在 H4 声明；当前规模足够 |

开放问题（默认按括号内执行，如需改变请在实现前确认）：
1. 收藏与收藏备注是否需要限制为“成员及以上”？（默认保持现状：所有人可修改，见 C19“不改变”）
2. 更新时根节点变化是否允许部署者“强制更新”？（默认不允许，需先删除更新源对应文件或手动上传）
3. 恢复区是否需要 UI？（默认不做，运维手工恢复）

---

## 附录 A：旧模块 → 新位置

| 旧 | 新 |
|---|---|
| `run.py`（NiceGUI `ui.run`） | `run.py`（`load_settings` + `MCServer` + `Scheduler`） |
| `app/pages/home.py` | `app/web/assets/js/views/shell.js`、`overview.js`、`importer.js` |
| `app/pages/viewer.py` | `views/fileview.js`（结构）+ `ui/tree.js` |
| `app/pages/history.py`、`comparison.py`、`records.py`、`record_view.py` | `views/fileview.js`（历史/对比/记录） |
| `app/pages/review.py` | `views/review.js` + `services/review.py` |
| `app/pages/search.py` | `views/search.js` + `/search` |
| `app/pages/management.py`、`bindings.py`、`tools.py` | `views/settings.js` + `/sources`、`/schedule`、`/ip-mapping`、`/admins`、`/bindings`、`/tools` |
| `app/pages/dltool.py` | `views/dltool.js` + `/dl` |
| `app/pages/file_downloads.py` | `/files/{name}/raw` + `server/legacy.py` |
| `app/pages/theme.py`、`app/static/css/style.css` | `app/web/assets/css/*` |
| `app/utils/auth.py` | `app/services/auth.py` |
| `app/core/scheduler.py`（APScheduler） | `app/services/scheduler.py` + `services/tasks.py` + `services/updates.py` |
| `app/core/tabs_state.py`（NiceGUI 存储） | 浏览器 localStorage（`views/shell.js`） |
| `app/core/parse_cache.py` | `services/documents.py`（doc_v2）；旧模块保留只读 |

## 附录 B：模板文件索引（`app/web/`）

| 文件 | 行数（约） | 内容 |
|---|---|---|
| `template.html` / `index.html` | 60 | 入口 |
| `assets/css/tokens.css` | 100 | 令牌 |
| `assets/css/components.css` | 1 000 | 通用组件 |
| `assets/css/layout.css` | 520 | 外壳 |
| `assets/css/views.css` | 900 | 视图 |
| `assets/js/core/util.js` / `api.js` | 300 / 230 | 工具、API、总线、路由 |
| `assets/js/ui/components.js` / `tree.js` / `icons.js` | 450 / 470 / 110 | 组件、虚拟树、图标 |
| `assets/js/views/*.js` | 3 000 | 8 个视图 |
| `assets/js/mock/*.js` | 1 500 | mock 解析、DL 算法、演示数据、接口实现 |
| `tests/index.html` / `tests.js` | 200 | 前端测试 |

## 附录 C：术语

| 术语 | 定义 |
|---|---|
| 机型（profile） | 一套独立的数据空间：`data/profiles/<pid>/` |
| 当前版本 | `configs/<name>` |
| 历史版本（归档） | `archive/<name>/<name>_<日期>.ext`，覆盖当前版本时自动生成 |
| 修改记录 | 从 `record_url` 拉取的上游变更记录文件 `records/<name>/record_*.ext` |
| 临时文件 | 仅上传者可见、带过期时间的文件 `data/temporary/<token>/` |
| compact 文档 | §6.3 的扁平先序数组格式 |
| 旧节点 ID | §6.2 `id`，收藏/绑定/旧建议使用 |
| 定位符（locator, loc） | §6.2 `loc`，在同一内容内唯一的节点地址 |
| 绑定路径 | `<文件名>/<标签>/…`，变量绑定使用 |
| 修订号 | §7.3，每机型 5 个内存计数，用于多用户变更感知 |
| 部署者 / 管理员 / 成员 / 访客 | §4.1 |
