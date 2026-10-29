# V3 UI 验收记录（INH-2159）

日期：2026-10-29。**状态：BLOCKED（未取得全部真实浏览器证据，不宣称 PASS）。**

## 已取得的自动化证据

- 前端 HTTP 套件（keep-alive 静态服务 + Chrome headless `--dump-dom`）：
  `<title>ALL PASSED (105)</title>`，覆盖工作区壳层、配置管理五 tab、参数列表/树双模式、提取 modal 50/100 分页与逐行决议、合并 modal 三栏/决议/提交关闭。
- 真实后端 API/服务：全量 `pytest -q` **2720 passed / 0 failed**（H3 执法中），含工作区隔离、文件版本、配置内核、提取/合并/导入/导出/恢复旅程。
- 响应式断点相关 CSS 仍为原型 tokens；自动断言只覆盖逻辑，不含像素对比。

## 真实浏览器冒烟（2026-10-29）

- 真实服务：`run.py --data-dir /tmp/mcc-v3-ui-data --port 8899`，浏览器 127.0.0.1 登记为管理员；fixture 含 MC-001/690/两个参数。
- 桌面 1366×900：截图 `docs/evidence/v3-config-1366.png`；配置管理、固定配置、版本演进、按钮与只读提示均正常，console 只有 macOS CVDisplayLink 系统噪声。
- 窄屏 375×812：**修正测量方式**——Chrome `--window-size=375` 实际最小布局视口为 500，旧截图 `v3-config-375.png` 是“500 布局裁成 375 图”，不能当 375 证据，已删除。
- 用 CDP `Emulation.setDeviceMetricsOverride` 375×812 mobile 复测：修复前顶栏搜索/身份 chip 越界；修复后 `.app-header` 收起搜索、身份文案与徽章，`document.scrollWidth=375`、越界元素 0，五 tab 自动换行。CSS-only 修复见 INH-2164。
- 1920×1080 与 768×900：`docs/evidence/v3-config-1920.png`、`v3-config-768.png`；配置管理布局正常，768 下侧栏收起、主内容仍完整。
- 旧页面 1366：`v3-overview-1366.png`（速览）、`v3-review-1366.png`（审阅）、`v3-dl-1366.png`（DL）、`v3-settings-1366.png`（设置）均渲染；文件列表是宿主左侧栏，无独立 `files` 路由（旧 files 截图与 overview 相同）。
- 模态：真实页「合并配置」打开后焦点落在基准输入框（base_revision_id），Escape 可关闭；role=status/aria-live 已加。提取 modal 在空文件态无输入控件，文件选择聚焦未单独验证；键盘-only 全走查仍未完成。
- 参数树键盘：真实浏览器 Tab 到 `.vtree` 后按 ↓、Enter，选中 `Mode`，右侧详情显示类型 string/当前 unset；树键盘可用。
- 未保存值保护：真实浏览器改值后点「参数总览」，弹出“有未保存的值”确认；点取消仍停在参数工作台，修改不丢。
- console：1 条 issue「A form field element should have an id or name attribute (count: 3)」，无 JS error/unhandled rejection；表单可访问名需修。
- 尚未覆盖：完整键盘-only 走查、375 修复后的可保存截图（CDP 量化已通过）、失败/空态逐屏、Q1–Q6/A01–A17 全矩阵。

## 尚未取得的阻断级证据

- 真实服务 + fixture 下 1366/1920/375/768 的逐屏截图与 DOM/可访问性断言（宿主 8 屏 + 配置域 5 tab）。
- 纯键盘走查（Tab/Shift+Tab/ESC/方向键）、模态焦点陷阱与恢复、异步加载/失败/空态的系统性记录。
- 浏览器 console 与网络失败记录（当前仅 `dump-dom` 标题，不看 console）。
- 老速览/文件侧栏/标签/审阅/DL/设置的逐屏像素与行为回归截图。
- 3k 分页/批量提取冲突在真实浏览器中的实测；参数工作台/树/总览 50k DOM 仍全量，已由 INH-2163 跟踪。

## 已知 UI 缺口（不得当作已通过）

- 模板导出/配置历史 tab 已接真实 API（选择修订+模板、预览/生成/下载；事件搜索/恢复），前端自动测试覆盖；真实浏览器四步逐屏与失败态仍未逐项取证。
- 值导入没有独立 UI（只有 API），全流程用提取/合并/导出/恢复覆盖。
- 合并/导出在真实浏览器中的机台选择、确认停用/移除、下载中断等边界未逐项验证。
- 表单控件已补 id/name（console 的 3 处 issue），但未重新截图确认控制台完全清空。

## 复跑命令

```
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --no-sandbox \
  --virtual-time-budget=12000 --dump-dom "http://127.0.0.1:<port>/tests/index.html" | grep '<title>'
MCCHECKER_REQUIRE_H3=1 .venv39/bin/python -m pytest -q
```

结论：自动逻辑门禁绿；**真实浏览器/无障碍/旧页面像素回归未完成，2159 不 Done**。
