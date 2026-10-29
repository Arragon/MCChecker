# V3 UI 验收记录（INH-2159）

日期：2026-10-29。**状态：BLOCKED（未取得全部真实浏览器证据，不宣称 PASS）。**

## 已取得的自动化证据

- 前端 HTTP 套件（keep-alive 静态服务 + Chrome headless `--dump-dom`）：
  `<title>ALL PASSED (105)</title>`，覆盖工作区壳层、配置管理五 tab、参数列表/树双模式、提取 modal 50/100 分页与逐行决议、合并 modal 三栏/决议/提交关闭。
- 真实后端 API/服务：全量 `pytest -q` **2720 passed / 0 failed**（H3 执法中），含工作区隔离、文件版本、配置内核、提取/合并/导入/导出/恢复旅程。
- 响应式断点相关 CSS 仍为原型 tokens；自动断言只覆盖逻辑，不含像素对比。

## 尚未取得的阻断级证据

- 真实服务 + fixture 下 1366/1920/375/768 的逐屏截图与 DOM/可访问性断言（宿主 8 屏 + 配置域 5 tab）。
- 纯键盘走查（Tab/Shift+Tab/ESC/方向键）、模态焦点陷阱与恢复、异步加载/失败/空态的系统性记录。
- 浏览器 console 与网络失败记录（当前仅 `dump-dom` 标题，不看 console）。
- 老速览/文件侧栏/标签/审阅/DL/设置的逐屏像素与行为回归截图。
- 3k 分页/批量提取冲突在真实浏览器中的实测；参数工作台/树/总览 50k DOM 仍全量，已由 INH-2163 跟踪。

## 已知 UI 缺口（不得当作已通过）

- 配置历史 tab 仍为占位，事件 API 已可用但未渲染。
- 四步模板导出 UI 未落地（后端导出已测）。
- 合并/值导入的机台选择器、导出预览界面未在真实浏览器验证。

## 复跑命令

```
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --no-sandbox \
  --virtual-time-budget=12000 --dump-dom "http://127.0.0.1:<port>/tests/index.html" | grep '<title>'
MCCHECKER_REQUIRE_H3=1 .venv39/bin/python -m pytest -q
```

结论：自动逻辑门禁绿；**真实浏览器/无障碍/旧页面像素回归未完成，2159 不 Done**。
