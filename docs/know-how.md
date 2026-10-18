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
- 更新源的「内容变没变」只认 `storage.save_config_file_detailed` 返回的 `unchanged`（M3-05 / R5）：它在机型锁内读旧字节、逐字节相同就**不写不归档**并返回 `unchanged=True`。调用方再自己比一次 sha256 会得出两套「变没变」，`revision.bump(pid, "files")` 就会在文件字节没变时通知全部客户端刷新。§10.2 第 6 步要求的锁内重读比的是另一件事——**比较基准（当前内容）没被并发换掉**，不是「新内容是否等于当前内容」；两者都要有，措辞别混。
- 根节点指纹可以从已经解析好的 compact 文档里拿，不必再解析一遍（M3-05）：XML 的根元素是 `nodes["id"][1]`，形如 `root/<原始 tag>`（命名空间写作 `{uri}local`），去掉固定前缀 `root/` 就是可比较的 tag——用它而不是 `nodes["l"][1]`（显示标签），因为换一种前缀写法（`a:root` → `root`）显示标签会变、根节点却没变，会把好更新误判成「下载到了错误页面」。JSON 的根类型（object/array）compact 文档里**没有**（合成根的 `t` 恒为 `"json"`，空容器更是没有子节点可推），只能自己 `json.loads` 再 `isinstance`——别用「第一个子节点的标签长什么样」去猜。
- 走 `storage._sanitize_config_filename` 的路径会抛**裸 `ValueError`**，而它仍是 M0-07 的 ASCII 白名单（`A-Za-z0-9-_.`）：`update_config_mapping`（改名迁移）、`update_config_record_url`、`get_record_dir`/`save_record_file`、`delete_config_file` 都在里面（M3-05 撞上）。两个后果：① 中文文件名在这几条路上被拒，而 §7.9 与 C26 要求「所有入口统一 `fsutil.validate_filename`（允许中文）」——目前只有「新建更新源」做到了；② HTTP 层把未知异常记成 500，所以服务层必须 `except ValueError` 翻成 400 `INVALID_INPUT` + `detail.field="name"`。要把 C26 收尾，得把 `_sanitize_config_filename` 改成委托 `fsutil.validate_filename`（只放宽不收紧、`/ \ : * ? " < > |` 与控制字符仍然非法），并同步 M0-07 的改名迁移用例。补两条：③ `update_config_mapping` 对**新旧两个名字**都过这条白名单，所以中文源不仅不能改名、连「名字不动、只改 url」都是 400（`tests/test_api_sources.py` 的 `test_rename_of_a_chinese_named_source_is_400_not_500` 与 `test_chinese_named_source_can_still_be_updated_and_deleted` 是收敛时的两条哨兵，前者届时期望值改 200）；④ 翻译时只 `except ValueError` 包住整个调用会把净化内部别的 `ValueError` 也掩成「用户文件名写错了」，必须带上原消息并 `logger.warning` 一条。
- 后台任务的「进度可见性」只能靠**带通知的容器**拿到，不要指望任务函数自觉上报（M3-07）：§10.1 规定 `fn` 用 `task.progress[...]` 与 `task.result["items"].append()` 更新，而卡 5 的 `updates._publish()` 正是这么直接写的——它不调任何回调，也不该被改成调。所以 `services/tasks.py::Task` 把这两个字段做成 dict/list 的浅子类，写入时回调 runner 递增 `tasks`；去重键是 `(done, total, 条数)`，同一个值写第二次不算变化，否则无意义的递增会把别人的刷新提示刷成噪声。浅子类不影响 `json.dumps` 与 `==` 普通 dict/list 的既有断言。
- 任务结束时 runner **只补 `settings`，绝不补 `files`**（M3-07 与 R5 的接缝）：`files` 由 `updates.update_source` 在「字节真的变了」时递增一次，runner 无从判断有没有变，再递增一次就是虚假通知（unchanged 的更新会惊动全部客户端）。`update_all` 写 `schedule.json.last_run` 而卡 5 已裁定自己不递增 `settings`，那一份由 `FINISH_PARTS` 补。抛异常的失败任务两类业务分量都不补（失败写不递增），但 `tasks` 必须补，否则别人的界面永远停在「进行中」。卡 9 若要给 `refresh_records` 加 `files`，改这个表，不要在 handler 里 `bump`。
- 工作线程的 daemon 位**不能当判据**，停止顺序仍必须先排空任务再关 HTTP（M3-07 / R9，修复轮 2 更正）：`threading.Thread.__init__` 把 daemon 从**创建者**继承下去（`threading.py:828`），而 `MCServer.daemon_threads = True` 且 `submit` 是 HTTP 处理线程调的 ⇒ **现网里 `mctask_N` 工作线程就是 daemon**；只有从主线程提交（单测的形状）才是非 daemon。两种形态下解释器最后都会等它们（非 daemon 走 `threading._shutdown`，daemon 走 `concurrent.futures.thread` 注册的 `_python_exit`，实测一个卡死的下载把进程拖到 91.7 秒才退），但**该等的不是「进程别死」，而是「删锁之前它们得散」**。因此：① 停止顺序仍是 `runner.shutdown(30)` 先、`server.shutdown()` 后，且 `shutdown()` 要幂等（信号连按）；② 「还有没有人在写」只能按**线程名**数（`services/tasks.py::worker_threads`，摘掉不写盘的 `mctask-shutdown` 等待线程），第一版按 `not t.daemon` 过滤时，测试全绿而真实进程 30 秒就把锁删了、下载还在跑——是手工 SIGTERM 验证抓出来的。测试里只比对调用顺序串也测不出真隐患：让假 server 在**自己被关闭的那一刻**采样「还有几个进行中任务」并断言为 0，把两行互换这条才会红。
- 单实例锁是本应用**唯一**的跨进程写锁，所以它必须活得比每一个写的人久（M3-07 修复轮 2 / C1 / H3）：`locks.profile_lock` 是 `threading.RLock`、`write_gate` 只在进程内，跨进程谁都不挡；两个进程同时写 `data/profiles/<pid>/` 时，`update_all` 对 `schedule.json` 的读-改-写和逐文件归档会互相静默覆盖。三条机制要一起记：① `SingleInstanceLock.release()` 是无条件 `os.unlink`，它不知道有没有人还在写；② `runner.shutdown()` 那 30 秒**小于**一个卡死下载的最坏耗时（`DEFAULT_DOWNLOAD_TIMEOUT_S` 30 × `DEFAULT_DOWNLOAD_RETRIES` 3 + 退避 `(1, 2)` ≈ 93 秒，而 `updates.update_all` 还串行遍历映射项），所以「排空超时」是常态不是意外，超时后线程仍在写而端口已经释放；③ 残留锁能被下一次启动接管（`acquire()` 读 pid → `_process_alive()` 判活），删错的锁不能。**因此后续卡片绝不允许在任何超时路径上删锁**：删锁前调 `tasks.wait_for_workers(EXIT_WORKER_TIMEOUT)`（300 秒，取值的推导写在该常量的注释里），等不散就保留锁文件并记 ERROR，让进程自然把线程跑完。`tests/test_api_tasks.py::TestLockOutlivesWriters` 用「闸门不放行 + 排空预算 0.01 秒」钉住这条不变量，其中「提交方必须是 daemon 线程」那条形状是必须的——它挡住上面那个 daemon 判据的错修法。
- 更新类任务的互斥面**必须包含 `refresh_records`，而且这一条不看 target**（M3-07 复审 F2）：§10.1 只写了「`update_all` 与 `update_source` 互斥」，很容易被读成「`refresh_records` 动的是 `records/`，不在冲突面里」——但 `updates.update_all` 自己就会对每个带 `record_url` 的映射项再跑一次 `refresh_records`（`updates.py:164`），所以单独 `POST {"type":"refresh_records"}` 与 `update_all` 并发，就是在同一机型、同一 target 上把 `records/<name>` 的读-改-写**同时跑两遍**。机型锁只保证单次读写不被撕裂，**不**保证两次不互相覆盖（§5.4 的丢失更新正是这个形状）。规则落在 `services/tasks.py::_CONFLICTING_TYPES`：只要有一方是 `refresh_records` 就按**机型**互斥（`update_all` 会碰全部映射项，按 target 判也挡不住）；`update_source ↔ update_source` 仍按 target 细分，因为不同源写不同的 `configs/<name>`，而共享文件（`update_status.json`、`schedule.json`）的读-改-写已经整段扣在 `storage.profile_write()` 里——细分到串行会让卡 5 的多源更新退化成排队。卡 8（调度器）、卡 9（修改记录）往这个 runner 提交任务时不要按自己类型的便利改这张表：新增类型要么进互斥面，要么说清它碰的是哪个**不重叠**的文件面。
- `update_status.json` 的键就是**映射名**，所以它归改名与删除的数据生命周期管（M3-05 复审 I1–I3）：改名时键要跟着搬（`migrate_files` 只决定本地产物走不走，映射名照样改，所以状态键两种情况下都必须跟随），删除时**清除**而不是保留。不处理有三笔账：① 改名后 `GET /sources` 的 `last_result` 恒为 `null`，UC-31 的「状态列」在改名那一刻失真；② 旧键成为永久孤儿（文件只增不减）；③ 最坏的一种——`DELETE` 之后用同一个名字重新 `POST`，新源直接继承上一个同名源的「最近结果」，显示一条从未发生在它身上的更新。删除选「清除」正是因为第三笔账。§7.9 的迁移清单没点这个文件名（它是新增文件，不在 v1 里），而 `storage.update_config_mapping` 也**不**搬它（storage 不知道它），调用方必须自己补这一步。
- 「锁外读 + 锁内写」是 `storage` 这套「每个 `save_*` 自锁」接口最容易拼错的形状（M3-05 复审 I1/I2）：`load_schedule()` 与 `save_schedule()`、`load_update_status()` 与 `save_update_status()` 各自只锁住自己那一步，分两次加锁地拼起来就是 §5.4 要防的丢失更新——`update_all` 写 `schedule.json.last_run` 期间进来的 `PUT /schedule`（卡 7 的部署者设置）会被这次读到的旧副本整份覆盖，落在**用户配置**上；`update_status.json` 的合并同理，两个并发完成的结果会互相丢项。修法是把整段读-改-写包进**一次** `storage.profile_write(pid)`：`locks.profile_lock` 返回 per-pid **RLock**，`save_*` 内部再取一次同名锁只是重入，无死锁（`create_source` 从一开始就是这个形状，照它来）。

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
- 进程内单例 + 会阻塞的工作线程 = 夹具必须**自己拥有闸门**，并且排在 monkeypatch 撤销之前拆（M3-07；修复轮 2 的 I2 补了后半条）：`services/tasks.py` 的 runner 是进程内单例，测试里忘了放行就会把任务连同线程泄漏到后续用例。`tests/conftest.py::isolated_task_runner` 显式声明依赖 `isolated_data_root`——pytest 的拆序是装序的反面，所以本夹具一定在 `storage` 的数据根被恢复成仓库 `data/` **之前**把 runner 关掉等空；否则在途任务的 `save_*` 会把字节写进真实数据目录（H3）。**但「拆在前面」本身不够**：用例里那句 `assert elapsed < 2.0` 排在 `gate.set()` 前面，一失败工作线程就还扣在闸门上，而夹具原先只等 5 秒、闸门自锁 20 秒——夹具放弃、monkeypatch 撤销，线程十几秒后醒过来照样往真实 `data/` 写（实测：拆掉放行那一步以后，pytest 摘要在 5.2 秒报 ERROR，进程却活到 20.4 秒）。现在的机制是一件事而不是逐条补：闸门由 `gates` 夹具创建并登记在 `isolated_task_runner` 里，拆的时候**无条件 `set()` 全部闸门**、再按 `RUNNER_DRAIN_TIMEOUT`(30) 等散，而 `GATE_TIMEOUT`(20) 与它都由 conftest 出、两个数的大小关系有回归用例守着（`test_gate_and_fixture_budgets_are_aligned`）。超时**不是**放过：残留线程仍然直接判用例失败，不要放宽等待、也不要改线程名绕过检查。写新的阻塞任务用例时一律 `release = gates()`，不要自己 `threading.Event()`。
- 等待异步任务落地的测试要**拿 Task 对象引用等**，不要用 `runner.get(id)`（M3-07）：惰性清理会在下一次 `list`/`get`/`submit` 时把刚结束的任务摘掉，`get` 返回 None 就再也分不清「没跑完」还是「已被清」。把清理上限 monkeypatch 成 0/2 的用例尤其如此。
- 存储层测试使用 monkeypatch 替换 DATA_DIR 为临时目录
- 下载器测试**不要**再打桩 `urllib.request.urlopen`（M3-04 改）：`app/core/downloader.py` 现在用自己的 `OpenerDirector`，桩打不进去；更要命的是打桩会把「逐跳重定向校验、分块限流、日志脱敏」三类行为一起屏蔽。做法是起本地 `http.server`（127.0.0.1 随机端口），用「服务端记到的请求条数」证明被策略拒绝的连接根本没发出去。两个环境坑：macOS 上 urllib 会读系统代理（`getproxies` 必须打桩成 `{}`，否则请求被代理接走返回 502）；需要「非回环首跳」时不要用 UDP-connect 探到的地址（开发机上那是 Clash/TUN 的 198.18.0.0/15，bind 得到但 connect 被黑洞吞掉），要逐个做 bind+connect 探针。
- 对比引擎测试需注意文件名不同导致路径不匹配的问题
- 证明「读-改-写整段在同一把机型锁里」的用例**必须让竞争方是另一个线程**（M3-05 复审 I1/I2）：同线程在 load 与 save 之间注入一次修改会重入同一把 per-pid `RLock`，锁内锁外都改得动，坏实现照样绿。可复用的写法在 `tests/test_api_sources.py::_race_after_first_load`：打桩被读的那个函数，读到的瞬间拉起竞争线程并 `Event().wait(0.5)`——坏实现里竞争方几毫秒就写完（主线程随后用旧副本覆盖，用例确定性地红）；好实现里锁还扣在主线程手上，`wait` 必然超时，主线程写完释放、竞争线程才轮到。两条路径都不靠时序碰运气，代价是好实现那条多等 0.5 s。

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
