# M16-14 采集器环境与受管生命周期（INH-2179）

> 状态：代码骨架已完成（`collector/` + `app/services/collector_manager.py`）；真实
> Python 3.12/Playwright/Chromium/断网安装证据为 **BLOCKED-EXTERNAL**（E4，见 §8）。
> 事实源：`docs/MCChecker_V3.1_PLAN.md` M16-14、ADR-012/020、
> `/tmp/m16-baseline/MCChecker_V3.1_Backend_Design.md` §7.1/§7.4。

## 1. 范围

本卡只解决三件事：

1. **环境隔离**：collector 是独立 Python 3.12 环境，主应用继续 Python 3.9.11 + 标准库；
   主应用 import 图、`app/`、核心 `requirements.txt`、`scripts/make_release.py` 的发布
   白名单都不包含 collector。
2. **受管子进程**：主应用用 `start/stop/status/diagnose` 管理 collector；`shell=False`、
   命令行只有解释器与入口；POSIX 进程组 / Windows 进程树回收；停止有超时与强制结束。
3. **可观察失败**：collector 缺失、解释器缺失、自检失败、浏览器缺失、协议/Python 版本
   不匹配、启动即崩、停止超时都返回稳定原因码；状态与诊断只含安全字段。

不在本卡范围：私有 stdio IPC 帧与分块校验（M16-15）、SDA 适配（M16-16）、SecretStore
（M16-18）、collector bundle 打包与断网安装证据（M16-42）。M16-14 的 `collector/main.py`
是**骨架**：它只做自检和 `ping`/`shutdown` 生命周期，不 import Playwright，也不访问网络。

## 2. 目录与边界

```text
collector/                 # 独立环境；没有 __init__.py，不属于 app 包
├── main.py                # 骨架：--self-test / 极简 stdio，仅标准库
├── README.md              # 环境安装与边界
└── requirements.lock      # BLOCKED-EXTERNAL 占位；版本必须由目标环境回填
app/services/collector_manager.py
tests/test_collector_manager.py
```

硬约束（`tests/test_collector_manager.py` 有守卫用例）：

- `app/`、`run.py`、`main.py` 不出现 `import collector` / `from collector ...`；
- `requirements.txt` 保持零第三方依赖，不含 playwright/chromium/cryptography；
- `scripts/make_release.py::RELEASE_ENTRIES` 不含 `collector`（collector 是独立 bundle，
  见设计 D12/M16-42）。

`run.ordered_stop` 已接入停止序列：**调度器 → 排空任务队列 → collector → 关 HTTP**。
`collector_manager.stop_all()` 在没有启动过 collector 时是 no-op，所以离线/无 collector
环境仍能启动普通 MCChecker，普通文件功能不受影响。

## 3. 配置

| 环境变量 | 作用 | 默认 |
| --- | --- | --- |
| `MCHECKER_COLLECTOR_PYTHON` | collector 解释器**可执行文件路径**（不接受 “py -3.12” 这类带参数命令串） | `python3.12`（PATH 解析） |
| `MCHECKER_COLLECTOR_MAIN` | collector 入口脚本；相对路径按项目根解析 | `<项目根>/collector/main.py` |
| `MCHECKER_COLLECTOR_CHROMIUM` | 可选：已解包浏览器可执行文件；自检以“存在且可执行”判定浏览器可用 | 未设置时看 Playwright 是否可解析 |

构造函数也可显式传 `python` / `entry` / `cwd`（测试用）。环境变量只决定**启动什么**，
不承载任何凭据。

## 4. 生命周期与状态

```python
from app.services import collector_manager

manager = collector_manager.init_manager()   # 进程内单例；不启动进程
manager.start()        # 先 --self-test，再 Popen；返回安全状态快照
manager.status()       # 永不抛异常；只含 STATUS_KEYS
manager.diagnose()     # {"status": ..., "probe": ...}，重新跑一次自检
manager.stop()         # 等进程树退出后才返回；幂等
collector_manager.stop_all()                 # 退出路径用；无实例时 no-op
```

状态机：

| state | 含义 |
| --- | --- |
| `stopped` | 未启动 / 正常停止 / 子进程以 0 退出 |
| `starting` | 已通过自检，正在 `Popen`（只在 `start()` 内部短暂出现） |
| `online` | 子进程存活；`available=true` |
| `unavailable` | 预检或自检失败（未启动）；原因码见 §6 |
| `failed` | 启动即崩、运行中异常退出、或停止超时被强制结束 |

`status()` 字段固定为：`state/available/reason_code/message/pid/started_at/
uptime_seconds/exit_code/protocol_version/python_version/browser/python/entry/
process_group/tree_kill`。**没有** env、argv、stdout/stderr、密码、Cookie、CSRF、token。

`start()` 每次都先运行 `python <entry> --self-test`。自检输出一行 JSON：

```json
{"ok": false, "protocol_version": "m16.collector/1", "collector_version": "0.1.0-skeleton",
 "python_version": "3.12.14", "python_ok": true, "browser": "missing",
 "browser_source": "none", "reason_code": "collector_browser_missing",
 "message": "未找到 Playwright（也没有配置浏览器可执行文件）"}
```

`ok=false` 时退出码 2；主应用把 `reason_code` 收敛成下表原因码。这样“浏览器缺失/版本
不匹配”在启动前就是可观察失败，而不是一个看起来 online、实际无法采集的子进程。

## 5. 停止与进程树

| 平台 | 启动 | 停止 | 强制 |
| --- | --- | --- | --- |
| POSIX | `start_new_session=True`（自成会话/进程组） | `killpg(SIGTERM)` | 超时后 `killpg(SIGKILL)` |
| Windows | `CREATE_NEW_PROCESS_GROUP` + `CREATE_NO_WINDOW` | `taskkill /PID <pid> /T /F` | 同一命令重试，再退回 `proc.kill()` |

- 子进程（含它拉起的 Chromium）与 collector 同组/同树，因此按组/按树回收，不会只杀
  Python 留下浏览器进程。Windows 侧 `taskkill /T` 是**尽力而为**：主动脱离进程树的
  子进程仍可能存活；真正的强保证需要 Job Object，留到目标环境实测（§8）再评估。
- `stop()` 在 `Popen.wait()` 返回**之后**才返回；`run.ordered_stop` 把它排在关 HTTP 之前，
  `run.stop_and_release` 随后才等后台线程并释放单实例锁。因此 SIGTERM 路径不会出现
  “collector 还在跑、锁已经删了”。
- 停止超时不是静默：`state=failed`、`reason_code=collector_stop_timeout`，`run` 仍会继续
  关 HTTP，但状态可诊断。若强制结束之后仍未回收（例如不可中断的系统调用），`pid` 会
  保留在状态里、`run` 记一条 ERROR；这一层无法替操作系统解决不可杀进程，只能如实上报。
- 标准输入管道在停止前关闭；正常路径靠 SIGTERM/进程树，不依赖 collector 主动读命令。
- 子进程 stdout/stderr 目前接到 `DEVNULL`（骨架没有协议输出，避免管道写满死锁）。
  M16-15 接入 length-prefixed IPC 时，由协议层接管管道并保持“stdout 只有协议帧”。

## 6. 错误码

| reason_code | 触发 |
| --- | --- |
| `collector_offline` | 未启动 / 正常停止（`unavailable` 的默认原因） |
| `collector_entry_missing` | 入口脚本不存在 |
| `collector_python_missing` | 解释器不存在或不可执行 |
| `collector_version_mismatch` | 协议版本不一致，或自检报告 Python < 3.12 |
| `collector_browser_missing` | 自检报告 Playwright/浏览器不可用 |
| `collector_selftest_timeout` | 自检超过 `selftest_timeout`（默认 15 s） |
| `collector_start_failed` | 自检无协议 JSON、自检失败或 `Popen` 抛 `OSError` |
| `collector_exited` | 启动后立即退出或运行中异常退出（带 `exit_code`） |
| `collector_stop_timeout` | 停止超时，已强制结束进程树 |

采集不可用时，未来的 Sync 项应把 `collector_offline` 写进 `SyncItem` 的 reason
（`app/core/sync_models.py::REASON_CODES` 已有该值），而不是让普通文件/配置功能报错。
当前卡不新增 API/任务类型，也不自动拉起 collector。

## 7. 安全规则

- 密码/凭据**不进 argv、环境变量新增项、日志、状态、诊断包**；私有凭据通道由 M16-15
  的 stdio envelope 承载。
- 状态/诊断只返回路径、版本、原因码等安全元数据；子进程原始 stdout/stderr 不返回
  （`test_selftest_failure_never_leaks_stderr_secrets` 用假密码钉住）。
- collector 不写主应用 `data/`；入库仍由主进程重算 SHA/大小并走既有提交事务
  （M16-15/24）。
- 不开 loopback HTTP、不开远程调试端口。

## 8. 目标环境锁定与证据

`collector/requirements.lock` **没有写任何版本号**：本仓库开发机不是目标环境，禁止用
“最新版”猜测。目标机锁定/断网安装步骤写在该文件头部，回填后需同时记录：

- Python 3.12 精确版本（`python --version`）；
- Playwright / cryptography 精确版本（`pip freeze`）；
- Chromium 版本与来源（`playwright --version`、安装目录 hash）；
- 断网机上 `pip install --no-index --find-links ...` 与
  `python collector/main.py --self-test` 的原始输出。

### 证据状态

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 核心 3.9 不 import 三方 | 本地验证 | `scripts/check_py39.sh` + import 图守卫用例 |
| 无 collector 可启动普通 MCChecker | 本地验证 | `stop_all()` no-op；collector 不参与路由/任务 |
| 受管 start/status/stop、失败原因码、停止超时 | 本地验证 | `tests/test_collector_manager.py`（dummy 子进程） |
| POSIX SIGTERM 进程组无孤儿 | 本地验证（dummy grandchild） | 真实 Chromium 未验证 |
| Windows 进程树回收 | **BLOCKED-EXTERNAL** | 本机 macOS；用例 `skipif(os.name != "nt")` |
| Linux 目标机 Python 3.12/Chromium 安装 | **BLOCKED-EXTERNAL** | E4 |
| 断网安装/首次启动/回退 | **BLOCKED-EXTERNAL** | E5（M16-42） |
| 真实 Chromium 崩溃/孤儿观测 | **BLOCKED-EXTERNAL** | 需目标机 + 真实 Playwright |

本机 `python3.12 collector/main.py --self-test` 实测为
`python_ok=true`、`browser=missing`、`reason_code=collector_browser_missing`、退出码 2：
这正说明“没有 Playwright 时如实不可用”，不是 E4 通过。

## 9. 后续卡衔接

- M16-15：接管 stdin/stdout 为 length-prefixed JSON envelope + 分块；`collector_manager`
  的 `start/stop` 仍是唯一生命周期入口，不新增第二套进程管理。
- M16-18/30：凭据只在私有通道内传递；不进 argv/env/状态。
- M16-42：collector bundle（Python 3.12 + Chromium + wheels + hash manifest）与断网
  安装证据；在此之前 `requirements.lock` 保持 BLOCKED-EXTERNAL，不上虚假版本。
