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

## 3. Development Notes
- NiceGUI 的 `app.storage.user` 基于浏览器会话
- 若需要 `app.storage.user` 在刷新后仍可用，需要在 `ui.run()` 里设置 `storage_secret`（本项目默认自动生成）
- NiceGUI 的 `app.storage.general` 用于全局持久化
- 使用 `ui.run(host='0.0.0.0')` 允许内网访问
- 文件上传使用 `ui.upload` 组件，通过 `e.content.read()` 获取内容
- Windows 下 Python 输出需 `sys.stdout.reconfigure(encoding='utf-8')` 避免编码错误
- PowerShell 写入文件内容过长时可能触发文件名过长错误，需拆分文件

## 4. Testing Notes
- 使用 pytest 运行测试，`pytest tests/ -v`
- 存储层测试使用 monkeypatch 替换 DATA_DIR 为临时目录
- 下载器测试使用 unittest.mock.patch 模拟 urllib
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
- 不要使用小众第三方库
- 不要在日志中输出敏感信息
- 不要修改用户上传的原始文件
- 不要在客户端会话中持久化服务端配置
- 不要使用 Python 3.10+ 专属语法（match, X|Y 类型联合）
- 不要在 ui.tree 上使用 add_slot 来实现自定义交互，事件处理与 NiceGUI 集成困难
- 不要一次性写入过大的文件内容到 PowerShell
