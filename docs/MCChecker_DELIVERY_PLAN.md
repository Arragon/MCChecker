# MCChecker v2 交付执行手册（多 Agent 接力）

> **本文件只回答「怎么做、做到哪、下一步」**，不重复需求细节。
>
> | 事实源 | 用途 |
> |---|---|
> | `docs/MCChecker_REDESIGN_SPEC.md` | 需求与技术规范（§15 任务卡 T-01–T-30、§16 验收） |
> | `docs/MCChecker_FEATURE_CHANGES.md` | 行为变更 C01–C32（相对 v1.0.1） |
> | Linear 项目 **MC-checker-v2**（`P-INH-14`，54 卡） | 每卡范围/验收/开发方案与执行状态 |
> | 本文件 | 执行协议、进度台账、已定决策、接力入口 |
>
> 卡定义在 Linear；进度以 Linear 为准，本文件第 7 节是镜像台账（如两者不一致，以 Linear 为准并回填本文件）。

---

## 0. 一句话现状（2026-10-17）

**M0、M1 全部完成（9 + 7 卡）**；**M2 完成 5/12**（M2-01 收藏 API 与修订号、M2-02 速览与结构树收藏、M2-03 修改建议提交、M2-04 详情面板建议放行、M2-05 全局搜索 API）；集成分支 `feat/mc-v2-delivery`（tip 以 `git log -1 feat/mc-v2-delivery` 为准），工作区干净、无未推送提交；全量测试 **422 passed**（Python 3.9.11）、前端测试页 **ALL PASSED (22)**。下一个待做卡：**M2-06 / INH-1879 搜索页与 Ctrl+K 命令面板**（把 `search` 加进 `WIRED`）。

---

## 1. 交付目标与不变量（违反即失败）

| 编号 | 不变量 | 落地手段 |
|---|---|---|
| H1 | 只能跑在 **Python 3.9.11**（禁用 3.10+ 语法：`match`、`tomllib`、`slots=True`、`zip(strict=)`、`X | Y` 运行期注解） | `scripts/check_py39.sh`（compileall + 禁用写法 grep） |
| — | **零运行时第三方依赖**（`app/` 不得 import nicegui/apscheduler 等） | 运行时代码只 import 标准库 + 项目内部模块；旧 `app/pages/` 除外（M4-07 删除） |
| H3 | 测试与失败**不得损伤权威数据**：`data/` 内容哈希测试前后一致 | 每卡验证时对 `data/` 取前后哈希 |
| — | 权威 JSON 损坏必须抛 `CorruptDataError`，**不得**当空数据覆盖 | `fsutil.read_json`；原子写 `fsutil.atomic_write_*` |
| — | 不得伪造/放宽验证；测试必须真的跑过 | 未运行的检查不得报告为通过；必要时改断言需说明原因 |
| — | 纯体感/跨设备用例**只记「未验证 · 待人工确认」**，不得写成通过 | 见第 9 节清单 |
| — | 高 risk 改动（密钥、鉴权、持久化数据、迁移、破坏性操作、外部集成、许可敏感）必须显式报告 | 开工前在计划里点名，完成后在 PR/Linear 注释里说明 |

**分层红线（§3.3）**：`server` 只解析请求/序列化响应；handler 不得直接读写业务文件；`services` 组合 `core`+`storage` 并做授权；`core` 不 import HTTP。

---

## 2. 分支与 PR 拓扑

```
v1.0.1（默认分支，不在 M4 收尾前接收 v2 提交）
  └─ feat/mc-v2-delivery                  集成分支（唯一合并目标）
        ├─ mc-v2/m0-01 … m0-06            历史 6 提交已用「堆叠 PR」补进集成分支（PR #4–#9）
        ├─ chore/batch-0-hygiene          仓库卫生（PR #10）
        ├─ mc-v2/m0-07-registry … m0-09-http（PR #11–#13）
        ├─ mc-v2/m1-01-compact … m1-03-identity（PR #14–#16）
        └─ 后续每卡：mc-v2/<m?-??>-<slug> → PR(base=feat/mc-v2-delivery) → merge
M4 收尾：feat/mc-v2-delivery → v1.0.1 一条汇总 PR
```

规则：

- 一卡一 PR，**base 恒为 `feat/mc-v2-delivery`**；合并方式 `--merge`（保留父提交，便于堆叠卡 diff 干净）。
- 集成分支基点含 `2134c54`（前端补齐，原 PR #3 已注释关闭并并入基点）。
- 分支名：`mc-v2/<卡号小写>-<slug>`；非卡改动用 `chore/…`（同样走 PR）。
- 推送/PR 之后**不要**删除远端卡分支（便于回看堆叠关系）。

---

## 3. 单卡 SOP（照抄执行）

1. **读卡**：`get_issue` 拿「范围／功能需求／开发方案／验收方案／规范」；对照 spec 相应章节与 `FEATURE_CHANGES` 的 C 编号。
2. **开工同步**：Linear 卡置 **In Progress**（前端已做/后端未做的卡见第 7 节说明）。
3. **建分支**：`git fetch && git checkout feat/mc-v2-delivery && git pull --ff-only` → `git checkout -b mc-v2/<card>-<slug>`。
4. **实现**：最小满足当前卡；沿用既有模式；新增公共行为必须带测试。
5. **验证（证据三件套）**：
   ```bash
   ./.venv39/bin/python -m pytest -q                      # 全量（定向加 tests/test_xxx.py）
   PYTHON=./.venv39/bin/python scripts/check_py39.sh      # 3.9 守护
   # H3：测试前后 data/ 内容哈希一致
   B=$(find data -type f -exec shasum -a 256 {} + 2>/dev/null | sort | shasum -a 256)
   # …跑测试…
   A=$(find data -type f -exec shasum -a 256 {} + 2>/dev/null | sort | shasum -a 256); [ "$B" = "$A" ] && echo ✅
   ```
   涉及前端时补：`"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --no-sandbox --virtual-time-budget=8000 --dump-dom "file://$PWD/app/web/tests/index.html"` → `<title>ALL PASSED (n)</title>`。
   涉及 HTTP 时补：真实 `run.py --port <随机> --data-dir "$(mktemp -d)"` + `curl` 走一遍本卡的验收条目。
6. **提交**：`feat(<域>): <一句话>（<卡号> / <INH-xxxx>）`；同分支多次提交可，但**一个卡一个 PR**。
7. **PR + 合并**：写清「范围／改动／验证（命令+结果）／已知取舍」；`gh pr create --base feat/mc-v2-delivery` → `gh pr merge <n> --merge` → 本地 `feat/mc-v2-delivery` 拉平。
8. **Linear 收尾**：状态 **Done** + 证据注释（模板见第 4 节）。

> 顺序依赖：**M0 → M1 → M2 → M3 → M4**，同里程碑按卡号/依赖顺序（例如 M1-03 先于 M1-04；M2-03 先于 M3-01）。有疑问查卡内「不做/依赖」段。

---

## 4. Linear 同步协议

**开工**：`save_issue(state="In Progress")`。

**完成**：`save_issue(state="Done")` + `save_comment`，注释模板：

```markdown
## 完成（已合并进集成分支 `feat/mc-v2-delivery`，tip <sha>）

PR: <url> · 提交 `<sha>`

**已完成**
- <改动要点，含关键文件与行为/契约变化>

**验收证据（3.9.11）**
- `pytest -q` → N passed（新增/修改了哪些测试）
- <卡内验收条目逐条对应：命令 + 结果>
- `data/` 哈希不变；`check_py39.sh` exit 0；（前端）ALL PASSED (n)

**已知取舍 / 留给后续**
- <与规范的偏差、未覆盖场景、交给哪张卡>
```

**里程碑出口**：`save_status_update(type="project", project="P-INH-14", health="onTrack", body=…)`，写「已完成卡 + 出口条件证据 + 下一步」。

**注意**：Linear MCP 偶发 `ECONNRESET`，需**同调用重试 3–8 次**；网络代理抖动见第 8 节。

---

## 5. 验证与证据规范（写进 PR/注释时必须可复现）

- 断言要**可观测**而非口头：缓存命中用 `documents.cache_info()` + monkeypatch `_read_bytes`；并发用线程计数；原子写用 monkeypatch 注入失败后对比文件字节。
- 关键修复优先**变异验证**：临时构造反向实现（去锁、退回吞异常、退回覆盖命名…）确认测试会红，再改回。
- `tests/conftest.py` 的 autouse 夹具已把数据根隔离到 tmp；**测试不得写真实 `data/`**。
- 测试进程统一用 `./.venv39/bin/python`（Python 3.9.11 + pytest 7.4.4）。
- 不要为了让检查通过而放宽/跳过既有测试；行为应按规范改（如 M1-03 让 `/session` 的旧占位断言按新身份语义更新，并在 PR 里说明）。

---

## 6. 卡序与依赖（54 卡全表）

> 状态快照于 2026-10-17。**In Progress = 前端界面已在模板中完成、后端/接口未做**（历史上 18 张卡如此标记，注释里写了「已完成/还没做/接着做」）。后端完成后才能置 Done。

### M0 基础：3.9 兼容、原子写与 HTTP 骨架（9/9 Done）

| 卡 | Linear | 状态 | 内容 |
|---|---|---|---|
| M0-01 | INH-1858 | Done | Python 3.9.11 语法兼容 |
| M0-02 | INH-1859 | Done | 测试数据隔离与 differ 断言修正 |
| M0-03 | INH-1860 | Done | fsutil 原子写与损坏 JSON 保护 |
| M0-04 | INH-1861 | Done | 文件名与路径越界校验 |
| M0-05 | INH-1862 | Done | 机型锁、注册表锁与 WriteGate |
| M0-06 | INH-1863 | Done | storage 兼容门面：原子写与并发保护 |
| M0-07 | INH-1864 | Done | 机型注册表、复制范围与映射改名迁移 |
| M0-08 | INH-1865 | Done | Settings：端口、数据目录与启动配置 |
| M0-09 | INH-1866 | Done | HTTP 骨架、静态资源与单实例锁 |

### M1 读取链路：解析、文档、身份与浏览（7/7 Done）

| 卡 | Linear | 状态 | 内容 |
|---|---|---|---|
| M1-01 | INH-1867 | Done | compact 解析器：完整属性与稳定定位符 |
| M1-02 | INH-1868 | Done | 文档服务：文件列表、原文与缓存 |
| M1-03 | INH-1869 | Done | 身份解析、会话 cookie 与角色展示 |
| M1-04 | INH-1870 | Done | 服务端权限入口与四角色测试夹具 |
| M1-05 | INH-1871 | Done | 生产外壳接入会话与文件列表 |
| M1-06 | INH-1872 | Done | 结构树、详情面板与文件下载 |
| M1-07 | INH-1873 | Done | 标签工作区、本地状态与可分享链接（纯前端） |

### M2 写入链路：收藏、搜索、对比、导入与删除（5/12）

| 卡 | Linear | 状态 | 内容 |
|---|---|---|---|
| M2-01 | INH-1874 | Done | 收藏 API 与实时值（含 `services/revision.py` + `GET /revision`） |
| M2-02 | INH-1875 | Done | 速览页与结构树收藏 |
| M2-03 | INH-1876 | Done | 修改建议提交与定位符（含 `services/review.py` 提交部分） |
| M2-04 | INH-1877 | Done | 详情面板提交修改建议（`WIRED` 放行 `remarks`） |
| **M2-05** | INH-1878 | Done | 全局搜索 API 与命中计数（`services/search.py` + `core.searching.search_compact`） |
| **M2-06** | INH-1879 | In Progress | **搜索页与 Ctrl+K 命令面板 ← 下一张** |
| M2-06 | INH-1879 | In Progress | 搜索页与 Ctrl+K 命令面板 |
| M2-07 | INH-1880 | Todo | 历史与修改记录列表及差异摘要 |
| M2-08 | INH-1881 | Todo | 结构化对比 API |
| M2-09 | INH-1882 | In Progress | 历史列表与对比选择器 |
| M2-10 | INH-1883 | Todo | 临时文件、上传保存与链接导入 |
| M2-11 | INH-1884 | In Progress | 导入对话框与临时文件标签 |
| M2-12 | INH-1885 | Todo | 删除文件进入恢复区 |

### M3 审阅、更新、DL 与设置（0/17）

| 卡 | Linear | 状态 | 内容 |
|---|---|---|---|
| M3-01 | INH-1886 | Todo | 审阅写回：原文修改与语义校验 |
| M3-02 | INH-1887 | Todo | 审阅提交 API：部分提交、冲突与幂等 |
| M3-03 | INH-1888 | In Progress | 审阅页与本地草稿 |
| M3-04 | INH-1889 | Todo | 安全下载器 |
| M3-05 | INH-1890 | Todo | 更新源校验提交与映射接口 |
| M3-06 | INH-1891 | In Progress | 更新源设置页 |
| M3-07 | INH-1892 | Todo | 后台任务队列与任务接口 |
| M3-08 | INH-1893 | In Progress | 标准库调度器与定时设置 |
| M3-09 | INH-1894 | In Progress | 修改记录拉取与文件内查看 |
| M3-10 | INH-1895 | Todo | DL 求根与正向反向计算 |
| M3-11 | INH-1896 | Todo | DL 试算、提取预览与条件保存 |
| M3-12 | INH-1897 | In Progress | DL 计算页 |
| M3-13 | INH-1898 | In Progress | IP 对应表与管理员名单 |
| M3-14 | INH-1899 | In Progress | 变量绑定编辑与校验 |
| M3-15 | INH-1900 | In Progress | 外部工具菜单 |
| M3-16 | INH-1901 | In Progress | 机型切换、复制与删除 |
| M3-17 | INH-1902 | In Progress | 修订号与多用户更新横幅 |

### M4 收尾：兼容、安全、迁移、发布与去 NiceGUI（0/9）

| 卡 | Linear | 状态 | 内容 |
|---|---|---|---|
| M4-01 | INH-1903 | Todo | 旧下载与记录链接兼容 |
| M4-02 | INH-1904 | Todo | 安全响应头与跨站写保护 |
| M4-03 | INH-1905 | Todo | 访问日志与业务日志 |
| M4-04 | INH-1906 | Todo | 旧布局复制校验迁移 |
| M4-05 | INH-1907 | Todo | 性能基准脚本 |
| M4-06 | INH-1908 | Todo | 离线发布包 |
| M4-07 | INH-1909 | Todo | 移除 NiceGUI 并同步文档（含 `.nicegui/` 取消跟踪、`docs/know-how.md` 更新） |
| M4-08 | INH-1910 | Todo | §16 全量验收门禁 |
| M4-09 | INH-1911 | Todo | 恢复区干跑说明（可选，不做界面） |

---

## 7. 进度台账（本文件与 Linear 的对照）

已合并的卡 PR（全部 base = `feat/mc-v2-delivery`）：

| PR | 卡 | 提交要点 |
|---|---|---|
| #4–#9 | M0-01…M0-06 | 历史 6 提交以堆叠 PR 补入集成分支 |
| #10 | 批次 0 | `.gitignore`、取消跟踪 57 个 pyc、`requirements-dev.txt`、`scripts/check_py39.sh` |
| #11 | M0-07 | 注册表锁+原子写、复制范围、改名引用迁移与回滚、`dltool_config` 原子化 |
| #12 | M0-08 | `app/config.py`：`Settings`/`load_settings`（命令行 > 环境变量 > 默认值） |
| #13 | M0-09 | `app/server/*`：HTTP 骨架、静态+ETag/gzip、单实例锁、有序停止；`run.py` 重写 |
| #14 | M1-01 | `parser.to_compact`（完整属性、稳定定位符、显式栈）+ mock 命名空间契约同步 |
| #15 | M1-02 | `services/documents.py` + `/files`、`/document`、`/raw`（缓存/ETag/版本校验） |
| #16 | M1-03 | `services/auth.py`、`services/context.py`、完整 `/session`、HTTP 层 ctx 接线 |
| #17 | 批次 1 | `docs/MCChecker_DELIVERY_PLAN.md` 执行手册 + `architecture.md` 指针 |
| #18 | M1-04 | `services/authz.py` 权限矩阵、HTTP 层 `X-Requested-With` 硬条件、`tests/authz_fixtures.py` 四角色夹具 |
| #19 | M1-05 | 生产外壳切真实后端：`MC.caps`/`MC.unwired` 能力开关、未接入入口 `disabled`+原因、速览降级状态块、顶栏不再显示未登记 IP |
| #20 | M1-06 | 文件工作区接真实后端：`MC.segmented` 支持 item 级 `disabled`/`title`、`MC.Tree` 新增 `starReason`/`onStarBlocked`、`fileview.js` 按 caps 跳过 `/favorites`·`/remarks`·`/archives` 并禁用未接入分段与建议表单 |
| #21 | M2-01 | `services/favorites.py`（§7.7 四端点，实时值按旧 ID 取先序最后一个）+ `services/revision.py` 与 `GET /revision`（§7.3）+ `router.query_param`/`json_body` 收口；mock 的 `resolveLive` 同步改为取最后一个 |
| #22 | M2-01 | 补齐 INH-1874 验收剩余两条：文件放回回到原组、旧 `favorites.json` 样本可解析（含子节点实时值） |
| #23 | M2-02 | 速览与结构树收藏接真实后端：能力开关改为 `CAP_KEYS` + `WIRED`（本卡加 `favorites`/`revision`），新增 `schedule` 能力避免速览白打 404，+2 条 caps 守卫测试 |
| #24 | M2-03 | `services/review.py`（§6.7 提交五步）+ `server/api_remarks.py`（GET/POST remarks）；`storage.add_edit_remark` 新增 `locator`/`source_sha256`/`schema_version` 可选字段并放宽 `node_key` 空值；mock 补 JSON 类型检查并去掉多余的 `files` 递增 |
| #25 | M2-04 | `api.js` 的 `WIRED` 放行 `remarks`（详情面板建议表单在生产可用），mock 的「只能对有值的节点提交建议」补 `field: "loc"` 以对齐 `services/review.py`；前端测试页 caps 断言随卡改名并覆盖新放行项 |
| #26 | M2-05 | `core/searching.py` 新增 `search_compact()` / `display_path()`（每个命中节点计 1，修掉父节点命中计 0 的旧缺陷）+ `services/search.py`（反序列化前按响应体字节预筛）+ `server/api_search.py`（`GET /p/{pid}/search`，`read` 档）；mock 补 `file` 未知 → 404 与 `limit`/`offset` 下限；新增 `scripts/bench_search.py` |

（另有 PR #2 设计说明书 + 前端模板；PR #3 前端补齐，已并入集成分支基点并关闭。）

**环境事实**：`./.venv39`（3.9.11 + pytest 7.4.4 + nicegui/apscheduler 仅旧代码用；用 `uv pip install` 装的，`pip` 直连代理易超时）；旧 `/tmp/py39env` 可作后备。前端测试用系统 Chrome（路径见第 3 节）。

---

## 8. 已定决策与已知坑（接力必读）

1. **数据根切换**：`storage.configure_data_dir(root)` 必须在 import 任何「导入期派生路径」的模块**之前**调用（如 `dltool.DLTOOL_FILE` 是模块级常量）。`run.py` 因此把 HTTP 层 import 放在 `configure_data_dir` 之后——不要为「整洁」把 import 提到文件顶部。
2. **旧 ID 与 loc 双轨**：旧节点 `id`（`root/R/item@attr`、`#text`）必须与旧 `parse_file` 完全一致（旧收藏/绑定/旧建议靠它）；新功能一律用 `loc`（唯一）。同名兄弟共享旧 `id`。
3. **`to_compact` 与规范的两处细化**（已在代码注释与 PR 说明）：`junk after document element` 用 expat `position` 精确截断（规范写的「最后一个 `>`」在 `<R/>\n<stray/>` 上不可靠）；带前缀属性的显示标签用 `@prefix:local`、`id/loc` 用 ET 原始名。
4. **mock 与后端契约必须同步改**：`app/web/assets/js/mock/docparse.js` 的 id/loc/occurrence 已改为 ET 原始 tag 形式；改契约时两边一起改，并跑前端测试页。
5. **响应协议**：handler 返回 `Response`（`payload` 或 `body` 二选一 + `headers`）；`ETag` 命中 `If-None-Match` 由 `http.py` 统一降级 304；`{pid}` 存在性校验与 `use_profile(pid)` 由 `http.py` 统一做（handler 不再自查机型）。
6. **`/session` 特例**：只有该接口在 `?profile=` 不存在/非法时回落 `default`（§7.2）；其他 `{pid}` 接口一律 404。
7. **缓存策略**：内存 LRU 键 `(realpath, size, mtime_ns)`，命中直接返回**已序列化**响应体；磁盘缓存 `cache/doc_v2/<sha256>.json.gz` 是派生数据（损坏即丢弃重建）。内存缓存目前**不存 gzip 结果**——是否补在 M4-05/M4-08 用基准脚本决定。
8. **光杆原子写**：模块级常量 `MAX_NODES/MAX_DEPTH/MEMORY_CACHE_*` 是测试可 monkeypatch 的钩子（用于廉价验证上限分支）。
9. **单实例锁**：Windows 存活判定用 `ctypes OpenProcess`（`os.kill(pid,0)` 在 Windows 会真杀进程）；**该分支未在 macOS 实测**。
10. **网络**：本机代理 `127.0.0.1:7890` 间歇性抖动 → `git push`/`gh`/Linear MCP 都要**外层重试循环**（示例：`for i in $(seq 1 10); do git push … && break; sleep 4; done`）。GitHub MCP 不稳时改用 `gh` CLI（同一凭据）。
11. **`linear` MCP 返回值/连接都不稳**：`save_issue`/`save_comment` 常需重试；创建 PR 用 `gh` 更可靠。
12. **测试隔离**：`tests/conftest.py` 的 autouse 夹具把数据根重定向到 tmp；新增模块若在 import 期派生 data 路径，需同步加入夹具的「前缀替换」逻辑。
13. **未做的housekeeping**：Linear 卡之间尚未补齐 `blockedBy` 关系（当前以本文件第 6 节的顺序为准）；`.nicegui/` 的 5 个已跟踪文件按 §15 T-30 留到 M4-07。
14. **`MC.caps` 能力开关（M1-05 起，前端逐卡必须遵守）**：`app/web/assets/js/core/api.js` 里的 `MC.caps` 是「哪些后端接口已接入」的唯一事实源，由 `MC.config.mock` 推导（模板全开、生产默认全关），`MC.unwired` 存每项能力的接入去向文案。未接入的入口一律 `disabled` + `title`，并且**不发请求**（不要靠后端假接口消除 404）。每张前端卡在自己的 PR 里把对应键加进 `api.js` 的 `WIRED`（必须是 `CAP_KEYS` 的子集，测试页有断言）：M2-02 `favorites`+`revision`、M2-04 `remarks`、M2-06 `search`、M2-09 `history`/`compare`/`records`、M2-11 `import`/`temp`、M3 各卡 `review`/`dl`/`settings`/`tools`。新增能力键时 `CAP_KEYS` 与 `MC.unwired` 必须同步（前端测试页有断言会红）。M4-08 全绿后该机制连同条件分支一起删除。
15. **同名兄弟的实时值取「先序最后一个」**：`favorite["path"]` 是旧节点 ID，同名兄弟共享它，因此实时值必须按 §7.7 取先序**最后一个**（与 v1 `favorites_live._build_id_index` 的后写覆盖结果一致）。`mock-api.js` 的 `resolveLive` 原先取第一个，已在 M2-01 按决策 4 与后端一起改正，并留了断言测试（`test_live_value_uses_last_sibling_in_preorder`）。今后这类双轨语义调整，都要 mock + 后端 + SPEC 同 PR 改。
16. **修订号提前到 M2-01 落地**：`services/revision.py` + `GET /api/v1/p/{pid}/revision` 已在 M2-01 建好（原表排在 M3-17），因为 §7.1 要求「所有写接口在成功后递增对应修订计数」——没有计数器，每张写卡都无法满足该条。规则：只在 service 写成功后 `revision.bump(pid, part)`，handler 不碰计数；读接口、失败写（400/403/404/409）与「删了 0 条」都不递增（有守卫测试）。M3-17 只剩多用户横幅 UX 与 `settings`/`tasks` 计数的接入。
17. **全局搜索的预算瓶颈在文档缓存条目数，不在扫描（M2-05 实测）**：`scripts/bench_search.py` 在 Apple Silicon / 3.9.11 上量到单节点均摊约 **0.33 µs**，所以扫描本身不是问题。真正的超预算来自 `services/documents.MEMORY_CACHE_MAX_ENTRIES = 24`：机型里超过 24 个文件时，一次全量搜索会把 LRU 打满循环逐出（每次重取都要 gunzip → `json.loads` → `json.dumps`）。实测同一台机器：
    - 12 文件 × 5 000 节点（6 万节点，现实规模）：预热 **19.6 ms**，无命中 **4.6 ms**。
    - 20 文件 × 30 000 节点（60 万，未超条目数）：预热 **198.6 ms**，无命中 **35.0 ms** ✔ §12 的 500 ms。
    - 30 文件 × 30 000 节点（90 万，超出 24 项）：预热 **731 ms**（未达 §12 的 500 ms，也未达卡面 300 ms）。把条目数临时提到 64 后同一批文件降到 **279.5 ms**，`cache_info` 从 90 次 miss 变 30 次。
    结论：修 §12 的方式是让缓存容量按「机型内文件数」而不是写死 24（256 MB 字节上限已经是真正的护栏），或按 §7.6 建议缓存小写数组。**这属于 M1-02 / §6.5 已定的缓存策略，改它要在 M4-05 里做并回写 SPEC，不在 M2-05 顺手改。**
18. **临时脚本不要在 pytest 之外跑 storage**：见 `docs/know-how.md` 第 7 节。M2-05 有一次为了拆分耗时直接在 shell 里 `storage.DATA_DIR = tmp`，结果样本和 `cache/doc_v2` 落进了仓库真实 `data/`（已删除，`data/` 哈希回到 `478d9c26ea8dbb8f`）。

---

## 9. 待人工确认清单（不得写成「通过」）

以下用例依赖真实体感/多端环境，**自动化不覆盖**，统一记录为「未验证 · 待人工确认」，由用户人工确认后再补记录：

- **UC-07** 超大文件（数十万节点）浏览不卡顿
- **UC-19** 收藏/复制相关体感
- **UC-40** 双浏览器同时在线、10 秒内看到对方更新
- **UC-45** 响应式（手机/窄屏）布局

（M4-08 的 §16 全量验收门禁会再次点名单，届时把人工确认结果回填到该卡与本文件。）

---

## 10. 发布说明必列项（M4-06 打包与最终 PR）

以下行为变更**必须**出现在发布说明中（相对 v1.0.1；详见 `FEATURE_CHANGES`）：

**C09、C12、C13、C20、C25、C26、C27**

同时遵守：README/手册在 M4-07 同步更新（含「IP 识别适用于可信内网」的提示，§11.1）。

---

## 11. 多 Agent 接力提示

**开工前读什么**（按序，不要贪多）：

1. 本文件第 0/3/7/8 节（3 分钟建立上下文）；
2. Linear 该卡描述（范围/验收/开发方案）；
3. `MCChecker_REDESIGN_SPEC.md` 中该卡点名的章节与 §16 对应用例；
4. 要改动的代码与相邻测试。

**一次只做一张卡**：实现 → 证据 → PR → Linear Done → 再开下一张。不要在未验证的情况下连续堆多卡。

**交接出口标准**（任一时刻退出都要满足）：工作区干净或已 stash 说明；线性状态与代码一致；PR/集成分支 tip 记录在本文件第 7 节与 Linear 注释里；下一步的卡号写明确。

**下一步**：M2-06（`INH-1879`）——搜索页与 Ctrl+K 命令面板：把 `search` 加进 `api.js` 的 `WIRED`，接 `GET /p/{pid}/search?q=&file=&limit=&offset=`，核对 UC-25（Ctrl+K 输入关键词 → 分组结果 → 点击打开文件并选中节点）与 UC-26（命中父节点名时结果不为 0）。§12 的搜索预算瓶颈见第 8 节决策 17（>24 个文件时由文档内存缓存逐出造成，留给 M4-05，不要在搜索侧做二次缓存）。M2-03 遗留的规范疑问：NoneType 节点在 compact 文档里 `v` 是字符串 `"null"`、`t` 是 `NoneType`，所以它是有值节点、可以提建议，但「建议又写回 null」会被 §6.7 第 1 步当成「与当前值相同」拦下——需在 M3-01 之前找产品确认，不要静默改语义。此后每张写卡都必须调用 `authz.authorize(ctx, action)` 并用 `tests/authz_fixtures.py` 补四角色测试、写成功后 `revision.bump`。
