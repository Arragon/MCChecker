# V3 集成证据（INH-2160）

日期：2026-10-29。**状态：BLOCKED（外部/设备证据未取得，不伪造 PASS）。**

## 已取得

### 性能（真实 HTTP，临时数据根；macOS arm64 / 12 核 / Python 3.9.11）
复跑：`.venv39/bin/python tests/performance_v3.py --json /tmp/perf_v3.json`

| 项 | 实测 | 预算 |
|---|---|---|
| 3200 XML 预览 | 334 ms | ≤3000 ms |
| 3200 原子提交 | 2005 ms | ≤5000 ms |
| 50k 计划首行 | 11 ms | ≤500 ms |
| 50k parameters 首页 | 260 ms | ≤500 ms |
| 50k overview 首页 | 361 ms | ≤500 ms |
| 50k 计划创建 | 536 ms | 无硬预算，记录 |
| 50k 提交 | 2164 ms | 无硬预算，记录 |
| 20 并发 rows p95 / 中位 | 460 / 170 ms | 量化记录 |

### 50k 前端分页（2026-10-29，INH-2163）
- 真实服务 + 50,000 参数：工作台列表 DOM 100 行；参数树自动进入“目录分组模式”，根只加载 10 个目录（G0–G9 各 5000），进入 G0 后 DOM 100 行；总览 DOM 100 行，分页“第 1 / 500 页 · 共 50000 行”。
- 服务端新增 `GET /configs/{cid}/parameter-tree?parent=&offset=&limit=`，只返回直接子目录与当前目录参数页；`tests/test_v3_parameter_tree.py` 覆盖根/子目录/分页无重复。
- 前端 M13-PERF 用例覆盖列表翻页、目录钻取、总览翻页只保留当前页。

### 离线首访（2026-10-29）
- 当前源码重建发布包 176 成员，`--verify-only` 外部主机 0；解压到 `/tmp/mcc-offline`，用独立数据根启动 `run.py --port 8902`。
- Chrome `--host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"` 下访问：速览页标题正常，配置管理显示“还没有固定配置”；证明首访不依赖外网。
- INI 导出新增回归：`[s] key/keep` 模板只改绑定 key=5，keep=x 保留，下载字节一致。

### 其他
- 真实 `data/`：dry-run manifest 4 files（duplicates=1, errors=0）；全域备份 7 files，verify ok，restore dry-run diff=[]。
- 安全/并发：跨 W 404、草稿 CAS 200+409、同计划双提交 200+409、200 并发读 ≥199、DTD/重复键/NaN 422。
- 导出：JSON/XML 未匹配字段保留、3 次同 hash、下载 hash 一致、原模板字节不变（tests/test_v3_export_preservation.py）。

## 未取得（阻断）

- 脱敏真实目标软件模板在目标系统读取：无授权模板/实体设备。
- 实体设备/PLC/机台部署与回读：无环境；V3 发布只移动内部 head。
- Windows 10/11 + Python 3.9.11、Linux 实机包运行。
- 断网首访（离线内网）真实浏览器验证。
- 4C8G/SSD 目标硬件与 20 并发独立进程压测；当前是本机线程级。
- INH-1932（100 文件档搜索 P50 ~2.4–2.7s vs 500ms）风险仍未解决，继续追踪，不当作已解决。

结论：代码级性能/安全/导出证据存在；**外部模板、设备、Windows/Linux、离线未验证，2160 不 Done**。
