# MCChecker Collector（M16-14 / INH-2179）

独立于主应用的采集器环境。主应用仍然是 **Python 3.9.11 + 标准库、零运行时第三方依赖**；
本目录只允许在 **Python 3.12** 的独立环境里运行，依赖 Playwright / Chromium / cryptography。

**当前状态：本地模拟环境闭环已接通。** main 使用私有分帧 IPC，runtime 复用
SdaLogin / SessionManager / SecretStore。真实 SDK 登录、Cookie 过期恢复、保活、
文件提交与表候选比较/写草稿均有实际子进程与 TLS 模拟服务证据；真实 SDA 和
Windows/Linux 离线 bundle 尚未验证。

模拟环境复跑：在独立环境执行 `uv venv --python python3.12 .venv-collector`，再执行
`uv pip install --python .venv-collector/bin/python -r collector/requirements-simulation.lock`。
该锁仅记录 macOS arm64 本地验证版本，不替代目标平台的 requirements.lock。
沿用 Playwright（Apache-2.0）与 cryptography（Apache-2.0 OR BSD-3-Clause），未复制第三方源码。

`./.venv39/bin/python scripts/run_sda_simulation.py --port 50009` 启动临时数据的应用、
实际 collector 与 loopback TLS SDA，并预置明确标记为模拟环境的管理员和来源。
秘密目录在临时数据目录之外，密钥只在子进程环境；不更改系统信任库。
模拟代理只接受 sda.sim.invalid:443 并转发到自己的临时 TLS 端口。

正式启动通过 MCHECKER_COLLECTOR_CONFIG 指定 JSON（origins 按 HTTPS origin 索引，
每项包含 LoginConfig 的 login 字段与可选 browser.proxy/args），并单独设置
MCHECKER_SECRET_ROOT、MCHECKER_SECRET_KEY、MCHECKER_COLLECTOR_PYTHON。
真实部署不得直接采用 /synthetic 端点作为真实 SDA 契约。

## 目录边界

| 约束 | 说明 |
| --- | --- |
| 不 import 进主应用 | `app/`、`run.py`、`main.py` 与核心 `requirements.txt` 都不得 `import collector`；collector 包仅供独立子进程与隔离测试使用 |
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
python3.12 main.py               # 私有二进制 stdio，由主应用 manager/client 管理
```

自检字段：`protocol_version`、`collector_version`、`python_version`、`python_ok`、
`browser`、`reason_code`、`message`。原因码与主应用一致：
`collector_version_mismatch`、`collector_browser_missing`。

可选环境变量 `MCHECKER_COLLECTOR_CHROMIUM` 指向已解包的浏览器可执行文件；设置后自检
以该文件是否存在且可执行作为浏览器可用判据，否则只检查 Playwright 是否可解析
（Chromium 实际启动验证属于目标环境证据）。

受管启动/停止/诊断由主应用 `app/services/collector_manager.py` 负责，见
`docs/M16_COLLECTOR.md`。
