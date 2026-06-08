# UI/UX 设计优化说明（NiceGUI）

## 1. 目标与边界

- 目标：在不改变核心功能与权限逻辑的前提下，统一视觉语言与交互反馈，提升界面一致性、可读性与操作效率，并兼顾响应式与可访问性。
- 边界：不引入新的第三方依赖；仅在现有 NiceGUI/Quasar 能力范围内优化布局与样式；尽量避免影响业务逻辑与数据结构。

## 2. 统一设计规范（本次落地的设计系统）

### 2.1 色彩与层级

- 背景：浅灰蓝 `--mc-bg`，降低大面积纯白眩光，增强内容区层次感。
- 卡片/面板：`--mc-surface`、`--mc-border`、轻阴影 `--mc-shadow-sm/md`，强调信息承载区域。
- 品牌主色：`--mc-primary`（对应 NiceGUI `ui.colors(primary=...)`）。

### 2.2 字体与信息密度

- 默认字体：系统 UI 字体栈（避免引入外部字体资源）。
- 等宽场景（路径/值）：统一使用 `.mc-mono`，提升对齐与可读性。
- 标题体系：
  - 页面标题：`.mc-page-title`
  - 分区标题：`.mc-section-title`
  - 次要说明：`.mc-page-subtitle`

### 2.3 交互与可访问性

- Focus 可见：`*:focus-visible` 增加清晰的可视化描边，保证键盘操作可用性。
- Reduced motion：`prefers-reduced-motion: reduce` 时禁用动画/过渡，减少晕动风险。
- Hover 反馈：卡片、表格行等提供轻量背景变化，避免“按下才知道可点”的体验。

### 2.4 响应式布局

- 左右抽屉改为 `show-if-above`：
  - 大屏：常驻显示侧栏，提高信息密度与多任务效率。
  - 小屏：侧栏为临时抽屉，顶部提供按钮切换（菜单/工具箱），避免挤压内容区。

## 3. 代码修改范围

### 3.1 新增/调整的全局资产

- 新增主题注入模块： [theme.py](file:///d:/Project/MCChecker/app/pages/theme.py)
  - 统一设置 `ui.colors(...)`
  - 注入全局 CSS（link）与树形折叠 JS（script）
- 静态资源挂载： [main.py](file:///d:/Project/MCChecker/main.py)
  - `app.add_static_files("/static", ".../app/static")`
- 全局样式文件： [style.css](file:///d:/Project/MCChecker/app/static/css/style.css)
  - 统一 tokens、卡片/表格/输入框、收藏树、DL 工具页等样式

### 3.2 页面级调整

- 主页与布局： [home.py](file:///d:/Project/MCChecker/app/pages/home.py)
  - 引入并调用 `ensure_theme()`
  - 左右抽屉使用 `show-if-above`，并补充移动端切换按钮
  - 侧栏卡片统一使用 `.mc-nav-card`
  - 移除页面内大量 `ui.add_head_html("<style>...")` 的重复样式注入（改由全局 CSS 管理）
- 文件查看页： [viewer.py](file:///d:/Project/MCChecker/app/pages/viewer.py)
  - 移除页面内 Tree CSS/JS 注入，依赖全局主题资产（保持树形交互逻辑不变）
- DL 工具页： [dltool.py](file:///d:/Project/MCChecker/app/pages/dltool.py)
  - 移除页面内 CSS 注入，统一由全局 CSS 管理
- 管理/绑定/工具/对比/历史页面： [management.py](file:///d:/Project/MCChecker/app/pages/management.py) / [bindings.py](file:///d:/Project/MCChecker/app/pages/bindings.py) / [tools.py](file:///d:/Project/MCChecker/app/pages/tools.py) / [comparison.py](file:///d:/Project/MCChecker/app/pages/comparison.py) / [history.py](file:///d:/Project/MCChecker/app/pages/history.py)
  - 标题/分区标题样式统一（`mc-page-title` / `mc-section-title`）
  - 表格增加 `mc-table` 统一表头与 hover 行反馈
  - 绑定页新增绑定组的变量行：修正为“按行移除 + 保存时读取当前输入值”，提升可用性

### 3.3 版本历史增强

- 版本更新时自动对比并落盘摘要： [storage.py](file:///d:/Project/MCChecker/app/core/storage.py)
  - 归档旧版本时对比“旧版本(归档) vs 新版本(当前/更高版本)”，生成 `.diff.json` 元数据（增/删/改/绑定统计，及“无变更”）
  - 历史页面打开时对缺失的旧数据做补算与回填（仅首次需要）
- 版本历史页面改版： [history.py](file:///d:/Project/MCChecker/app/pages/history.py)
  - 由时间线改为紧凑卡片列表：信息更集中、视线移动更少
  - 每条历史记录展示对比摘要（+新增 / -删除 / ~修改；无变更则显示“无变更”）
  - 当前版本区域提供“一键与上一版本对比”入口

## 4. 依赖变更

- 无新增/删除/升级任何 Python 依赖。
- `requirements.txt` 未变更。

## 5. 兼容性与验证报告

### 5.1 已执行验证

- 单元/集成测试：
  - 执行命令：`pytest tests/ -v`
  - 结果：59 passed（Python 3.12.11）
- 语法/可编译性验证：
  - 执行命令：`python -m compileall -q .`
  - 结果：通过（Python 3.12.11）
- UI 手工检查：
  - 启动 `python main.py`，在浏览器打开首页确认：主题样式加载、抽屉布局、收藏树折叠、各卡片/表格 hover 与 focus 可见。

### 5.2 未能在当前环境执行的验证

- Python 3.9 / 3.11 未在当前执行环境安装（无 `py` launcher 且仅检测到 3.12），因此无法在本机直接运行多版本测试。

建议在具备多版本解释器的环境执行：

```bash
pip install -r requirements.txt
pytest tests/ -v
python -m compileall -q .
```

并分别在 Python 3.9、3.11、3.12 下重复上述验证。

## 6. 回归关注点

- 主要风险点：CSS 对 Quasar 组件的全局覆盖可能影响个别组件视觉细节（但不影响功能逻辑）。
- 建议回归：文件树展开/折叠、收藏/取消收藏、版本历史/对比入口、绑定组新增保存、DL 工具对话框展示。

