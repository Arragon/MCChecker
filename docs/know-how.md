# Project Know-How

## 1. Common Problems
- NiceGUI 的 `ui.tree` 组件需要特定的数据格式，需将解析结果转换为 `{id, label, children}` 结构
- XML 属性需在树形视图中特殊处理，作为 `@attr_name` 子节点展示
- NiceGUI 标签页管理需手动维护状态，使用定时器刷新 UI
- APScheduler 在 NiceGUI 中需要正确初始化，避免重复创建 scheduler
- 版本对比时文件名前缀会导致路径不匹配，需要归一化路径后再比较

## 2. Proven Solutions
- 树形视图使用 ui.expansion 替代 ui.tree，可实现在节点旁添加自定义组件（如收藏星标），ui.tree 的 add_slot 方式与 NiceGUI 事件系统集成较复杂
- 返回导航使用历史栈（session_tab_history list），每次切换标签页时将当前页面压栈，返回时弹出栈顶
- 收藏星标使用 ui.icon 组件，通过修改 _props["name"] 和 _props["color"] 切换 star/star_outline 和颜色，然后调用 update() 刷新
- "部署者"和IP显示已从界面移除，is_deployer() 仅用于功能权限判断（管理按钮可见性等）
- 使用递归函数将 XML/JSON 统一转换为树形结构
- 使用 IP 地址判断部署者权限，127.0.0.1 和服务端本机 IP 视为部署者
- 文件版本归档使用日期后缀: `filename_20260524.xml`，若已存在则精确到秒
- 版本对比使用 difflib.unified_diff 生成文本差异，结构化差异使用归一化路径匹配
- 会话级标签页状态使用 Python 列表+字典在渲染函数闭包中维护
- 审阅写回要在原文上保住根元素之外的内容（M3-01 / B3）：`ET` 的事件里没有位置信息，位置只能自己用 `xml.parsers.expat` 量（`CurrentByteIndex`）。三个坑：① `ParserCreate()` 必须**关掉命名空间处理**，expat 文档写明开启后该偏移不保证对应原始字节；② 根元素有两种收尾，`</名称>` 的偏移落在 `<` 上、自闭合 `<a/>` 的偏移已在 `/>` 之后，判据用「偏移处是不是 `</`」，不能「往后找第一个 `>`」（`<R><a/></R>` 会在根结束之前就撞上 `/>`）；③ 区间两端要按字节核对，而 UTF-16 下 `<`/`>` 各占两字节，marker 得跟着编码换。
- `ET.register_namespace` 改的是**进程级**的 `xml.etree.ElementTree._namespace_map`，注册只增不减，没有公开 API 能读回原值。不要在请求路径上「序列化前后快照/恢复」它：FastAPI 的同步端点跑在线程池里，两个并发写回会互相踩——A 的 `tostring` 可能读到 B 刚恢复进去的映射。每次写回都先把**当前文档自己的**前缀登记进去，所以同一份原文在同一进程内产出一致；别的文档留下的条目只会多不会少（str→str，量级可忽略），并发下最坏情况是前缀拼写换一种写法，`loc`/值/节点数都不变，第 6 步校验与 `to_compact` 的显示标签都不受影响。因此 `apply_edits` 的 docstring 明确写着它不是纯函数。
- 同一份 XML 用两种口径解析会得到不同的 `.text`（M3-01 / B7）：显示链路 `insert_comments=False` 时，标准库把被丢弃的注释/PI 前后的字符数据**拼进** `elem.text`；写入侧 `insert_comments=True` 时同一段文本被切成 `elem.text` + 各注释/PI 兄弟的 `.tail`。所以「元素的有效文本」必须自己算（`elem.text` 加上首个子元素之前那些注释/PI 的 tail），写入也要跨这一整段跨度写，否则会出现「界面显示有值、提交建议被接受、写回永久 `REVIEW_CONFLICT 无法唯一定位`」这种用户刷不出去的死循环。
- 审阅分组只认 `group_key = locator or node_path or node_key`，不要用 `storage.list_review_items`（M3-02）：它按 `(source_file, node_key or node_path)` 分组，而 v2 建议按 §5.2 写 `node_key=""`，于是会退化成按旧节点 ID 并组——同名兄弟共享旧 ID，「item 第 1 个」和「item 第 2 个」的建议被并成一组，批准其中一条会把另一条连带驳回，写回还可能指错节点（正是 v1 的根因）。分组只能在 service 层自己算，原始条目仍可以复用 `storage.load_edit_remarks()`。
- 审阅的「能不能通过」必须列表与提交共用一份定位算法（M3-02）：`views/review.js` 只用 `n.stale || !n.resolvable` 两个布尔决定按钮是否可点，服务端另算一套就会出现「按钮能点、点下去 400/409」的死路。legacy（无 `locator`）建议按旧 ID 试匹配，**恰好 1 个**匹配才 `resolvable=true`；`legacy` 只用于打徽标。另外当前文件解析不了或被删时仍要给出真实 `current_sha`（拿不到就 `null`），否则前端只能带空 `source_sha` 提交，连「驳回」这条唯一出路都会被 409 挡死。
- 审阅写回的落盘顺序（M3-02）：`apply_edits`（内含 §6.7 第 6 步的再解析比对）必须在生成文件与状态更新**之前**跑完，这样「校验失败 422 不写任何文件、不改任何建议状态」是靠顺序保证的，不需要回滚；幂等表落在独立的 `profiles/<pid>/review_idempotency.json`（10 分钟窗口、**不**进 `COPY_PROFILE_FILES`），塞进 `edit_remarks.json` 顶层会破坏 §5.2「现有字段不变」。

## 3. Development Notes
- NiceGUI 的 `app.storage.user` 基于浏览器会话
- 若需要 `app.storage.user` 在刷新后仍可用，需要在 `ui.run()` 里设置 `storage_secret`（本项目默认自动生成）
- NiceGUI 的 `app.storage.general` 用于全局持久化
- 使用 `ui.run(host='0.0.0.0')` 允许内网访问
- 文件上传使用 `ui.upload` 组件，通过 `e.content.read()` 获取内容
- Windows 下 Python 输出需 `sys.stdout.reconfigure(encoding='utf-8')` 避免编码错误
- 子类只覆盖 `HTTPRedirectHandler.http_error_302` 是**不够的**：基类里 `http_error_301 = http_error_303 = http_error_307 = http_error_302` 是在类体求值时绑定的别名，指向基类函数本身，301/303/307 仍会绕过子类的实现（M3-04 自定义重定向策略时发现；308 基类压根不处理）。要么在本类里重新赋一遍别名，要么像本卡这样自己接管 3xx（校验的 URL 与请求的 URL 必须是同一个）。
- PowerShell 写入文件内容过长时可能触发文件名过长错误，需拆分文件

## 4. Testing Notes
- 使用 pytest 运行测试，`pytest tests/ -v`
- Python 3.9 兼容性不能只靠 `compileall`：函数注解里的 `X | None` 能编译，但在导入时求值才报 `TypeError`。验证方式是用 3.9 解释器实际导入模块（`python3.9 -c "import app.core.storage"`）；当前 v1.0.1 的 `storage.py`、`differ.py`、`dltool.py` 在 3.9.11 下导入即失败。
- 前端模板（`app/web/`）的纯逻辑测试：浏览器打开 `app/web/tests/index.html`，或 `google-chrome --headless=new --dump-dom http://127.0.0.1:<port>/tests/index.html` 检查 `<title>ALL PASSED`。
- `tests.js` 的 `test()` 支持**返回 Promise 的异步用例**（M3-03 加）：视图的 DOM 断言必须走这条路，因为 `fileview.js`/`review.js` 的渲染在 `api.get(...)` 的 Promise 链末端才落到 DOM，同步断言只能看到空壳。做法是 stub `MC.api.get` 后让出一个宏任务（`setTimeout(resolve, 0)`）再查 DOM；汇总会等全部异步用例 settle 后才写 `<title>`，所以 headless 的 `--virtual-time-budget` 照常能抓到结果。新增异步用例别忘了它**默认记为失败**，只有 settle 后才改写——断言必须真的跑到。
- 视图脚本要进测试页就直接加进 `app/web/tests/index.html` 的加载列表（`importer.js` M2-11、`fileview.js` M3-03 都是这么做的既有做法），**不要**为了好测把工厂闭包内的私有函数（如 `renderSuggest`）导出成公共 API。
- 存储层测试使用 monkeypatch 替换 DATA_DIR 为临时目录
- 下载器测试**不要**再打桩 `urllib.request.urlopen`（M3-04 改）：`app/core/downloader.py` 现在用自己的 `OpenerDirector`，桩打不进去；更要命的是打桩会把「逐跳重定向校验、分块限流、日志脱敏」三类行为一起屏蔽。做法是起本地 `http.server`（127.0.0.1 随机端口），用「服务端记到的请求条数」证明被策略拒绝的连接根本没发出去。两个环境坑：macOS 上 urllib 会读系统代理（`getproxies` 必须打桩成 `{}`，否则请求被代理接走返回 502）；需要「非回环首跳」时不要用 UDP-connect 探到的地址（开发机上那是 Clash/TUN 的 198.18.0.0/15，bind 得到但 connect 被黑洞吞掉），要逐个做 bind+connect 探针。
- 对比引擎测试需注意文件名不同导致路径不匹配的问题

## 5. UI/UX Notes
- 树形视图使用自定义 HTML (ui.html + ui.row + ui.column) 构建，折叠箭头为 CSS 三角形（无需 Material Icons），层级竖线使用不同颜色区分（8色循环，1.5px 细线）
- 折叠展开通过原生 JavaScript `mct(this)` 实现 DOM 遍历（closest('.tree-row') → nextElementSibling），无需生成元素 ID，比 getElementById 方案更健壮
- NiceGUI 3.0.4 要求 `ui.html()` 必须显式传入 `sanitize=False` 参数
- 默认展开全部节点；叶子节点旁始终显示收藏星标：未收藏空心星(grey-5)，已收藏实心星(yellow)
- 返回按钮使用 arrow_back 图标，返回上一页面而非主页
- 顶部导航栏不再显示"部署者"/"访客"标识和IP地址
- 版本对比使用颜色标注：绿色=新增，红色=删除，橙色=修改，紫色=绑定变量
- 导航菜单宽度固定240px
- 绑定变量标注使用 outline badge 样式，低调不干扰

## 6. Debugging Notes
- NiceGUI 日志级别可通过 logging 配置
- 解析错误需在界面上友好提示
- 文件下载失败需重试3次
- 使用 `ui.notify()` 显示操作反馈

## 7. Do Not Do
- 不要在 pytest 之外直接跑临时脚本调用 `app.core.storage` / `app.services.*`：数据根重绑定只发生在 `tests/conftest.py` 的夹具里，`storage.DATA_DIR = tmp` 这类手工赋值改不到由它派生的其它常量，脚本会把样本和 `cache/doc_v2` 写进仓库真实的 `data/`。要么写成 pytest 用例（自动隔离），要么显式用 `run.py --data-dir $(mktemp -d)`。
- 不要只 `realpath` 一侧就算相对路径（`os.path.relpath(源, storage.DATA_DIR)`）：`fsutil.resolve_within()` 返回 realpath，而 `get_archive_root_dir()` / `get_records_root_dir()` 拼出来的是未解析的路径；macOS 上 `/var` 又是指向 `/private/var` 的符号链接，于是恢复区 manifest 的 `from` 会变成 `../../../../../../private/var/…` 这种没法照着放回去的废值（M2-12 真实后端跑出来才发现，pytest 的 tmp 两侧恰好一致所以没抓到）。要两侧都 `os.path.realpath()` 再 `relpath`。
- 不要使用小众第三方库
- 不要在日志中输出敏感信息
- 不要修改用户上传的原始文件
- 不要在客户端会话中持久化服务端配置
- 不要使用 Python 3.10+ 专属语法（match, X|Y 类型联合）
- 不要在 ui.tree 上使用 add_slot 来实现自定义交互，事件处理与 NiceGUI 集成困难
- 不要一次性写入过大的文件内容到 PowerShell
