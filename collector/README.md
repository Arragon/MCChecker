# MCChecker Collector（M16-14 / INH-2179）

独立于主应用的采集器环境。主应用仍然是 **Python 3.9.11 + 标准库、零运行时第三方依赖**；
本目录只允许在 **Python 3.12** 的独立环境里运行，依赖 Playwright / Chromium / cryptography。

**当前状态：骨架。** M16-14 交付环境边界、自检与受管子进程生命周期；真正的私有 stdio
IPC 帧（分块、校验、取消/超时）在 M16-15 接入，SDA 协议适配在 M16-16 之后。

## 目录边界

| 约束 | 说明 |
| --- | --- |
| 不 import 进主应用 | `app/`、`run.py`、`main.py` 与核心 `requirements.txt` 都不得 `import collector`；本目录没有 `__init__.py`，不参与主应用 import 图 |
| 不进核心发布包 | `scripts/make_release.py` 的 `RELEASE_ENTRIES` 白名单不含 `collector/`；collector 作为独立 bundle 随 M16-42 发布 |
| 不写主应用数据 | 采集器只回有界内容 + 安全元数据；权威文件由主应用重新计算 SHA/大小后走既有提交事务 |
| 无明文/无命令行秘密 | 密码不得出现在 argv；凭据只允许走私有 stdio 通道（M16-15），且不进日志/DTO |

## 环境安装（目标机）

```bash
# Windows: py -3.12 -m venv .venv
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock   # 版本由目标环境锁定后回填
.venv/bin/python -m playwright install chromium
.venv/bin/python main.py --self-test
```

`requirements.lock` 目前是 **BLOCKED-EXTERNAL** 占位：精确版本必须在目标 Windows/Linux
机器上锁定，本仓库不猜“最新版”。断网安装步骤与留证要求写在该文件头部。

## 自检与生命周期

```bash
python3.12 main.py --self-test   # 一行 JSON；ok=false 时退出码 2
python3.12 main.py               # 极简 stdio：{"op":"ping"} / {"op":"shutdown"}
```

自检字段：`protocol_version`、`collector_version`、`python_version`、`python_ok`、
`browser`、`reason_code`、`message`。原因码与主应用一致：
`collector_version_mismatch`、`collector_browser_missing`。

可选环境变量 `MCHECKER_COLLECTOR_CHROMIUM` 指向已解包的浏览器可执行文件；设置后自检
以该文件是否存在且可执行作为浏览器可用判据，否则只检查 Playwright 是否可解析
（Chromium 实际启动验证属于目标环境证据）。

受管启动/停止/诊断由主应用 `app/services/collector_manager.py` 负责，见
`docs/M16_COLLECTOR.md`。
