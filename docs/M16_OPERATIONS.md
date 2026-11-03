# MCChecker V3.1 运维手册（M16-42）

> 面向部署/运维：怎么装、怎么升级、怎么备份回退、保活怎么调、凭据怎么恢复、出问题看哪里。
> 只写**已实现**的入口；标注 `[外部]` 的步骤需要真实目标机或授权内网，本地无法代替。

## 1. 安装与启动

| 场景 | 命令 |
|---|---|
| 直接启动（开发/小规模） | `python main.py`（默认 `0.0.0.0:50003`） |
| 指定数据根与端口 | `python run.py --data-dir /srv/mcc-data --port 50003 --no-reload` |
| Linux 常驻 | `./service.sh start / stop / status / restart / logs`；`./service.sh unit` 打印 systemd 单元 |
| 加管理员 | `sudo ./add-admin.sh --name 张三 --ip 10.1.2.3 --data-dir /srv/mcc-data` |

环境变量（优先级高于默认值）：

| 变量 | 用途 |
|---|---|
| `MCHECKER_PORT` / `MCHECKER_HOST` | 监听端口/地址 |
| `MCHECKER_DATA_DIR` | 数据根（**不要**与代码同目录） |
| `MCHECKER_TRUSTED_PROXIES` | 信任的反代地址（只有它们发的 `X-Forwarded-For` 才被采信） |
| `MCHECKER_ALLOWED_HOSTS` | 允许的 Host 名单（反代/多域名部署必配） |
| `MCHECKER_OPERATOR_TOKEN` | 独立运维授权 token（凭据导入/轮换/撤销必需） |
| `MCHECKER_SECRET_KEY` | SecretStore 主密钥（V3.1 凭据加密；丢失=密文不可解，必须重新导入） |

运行时零第三方依赖（Python 3.9.11）。collector 是**独立进程/独立环境**（Python 3.12 + Playwright + Chromium），见 §6。

## 2. 升级

```bash
# 1) 停服务（SIGTERM 会排空任务、停调度、释放单实例锁）
./service.sh stop

# 2) 备份（先备份再升级，失败可回退）
python scripts/backup_v3.py backup --data-dir /srv/mcc-data --out /backup/mcc-$(date +%F)
python scripts/backup_v3.py verify --data-dir /srv/mcc-data --backup /backup/mcc-$(date +%F)

# 3) 解包新版本代码（保留数据根不动），启动并看日志
./service.sh start && ./service.sh logs
```

旧数据升级（M16-37）：先 dry-run 看映射，再执行；两者都幂等，重复执行不会重复写。

```bash
python scripts/sync_upgrade.py --data-dir /srv/mcc-data --dry-run
python scripts/sync_upgrade.py --data-dir /srv/mcc-data --execute
python scripts/sync_migrate.py --data-dir /srv/mcc-data --dry-run
```

> 迁移只做加法：旧 `config_mapping.json` / `schedule.json` / `update_status.json`、归档与记录都保留；
> 迁移前后旧文件/记录 SHA 应一致。

## 3. 备份与回退

```bash
python scripts/backup_v3.py backup  --data-dir /srv/mcc-data --out /backup/mcc-20261102
python scripts/backup_v3.py verify  --data-dir /srv/mcc-data --backup /backup/mcc-20261102
python scripts/backup_v3.py restore --data-dir /srv/mcc-data --backup /backup/mcc-20261102 --dry-run
python scripts/backup_v3.py restore --data-dir /srv/mcc-data --backup /backup/mcc-20261102   # 真正恢复
```

- 备份默认**不复制** SecretStore 密文根（密文根在数据根之外），避免与密钥一起泄露。
- 回退演练至少每季度做一次 `restore --dry-run`，确认 diff 与预期一致。

## 4. 保活与重登（SDA 连接）

界面路径：**更新管理 → 连接设置**。

| 项目 | 说明 |
|---|---|
| 保活开关 | 连接级；关闭后不会"暗中"继续发心跳 |
| 保活间隔 | 1–60 分钟；同一连接同时最多一个心跳（singleflight） |
| 保活状态 | `保活待验证` = 从未跨真实 idle 成功；`已验证` = 取得过真实 idle 证据 |
| 自动重登 | 会话失效时 singleflight 重登一次并做一次安全重放；失败不循环重试 |
| 密码错误锁存 | 连续失败后锁存，避免账号风暴；需人工在界面重试 |
| 凭据轮换 | 导入候选 → probe → 激活（CAS + `auth_epoch`）；晚到的 probe 不会覆盖新凭据 |

`[外部]` 「保活待验证 → 已验证」只能在真实内网跨自然 idle 成功后才成立（E3）。

## 5. 凭据恢复与安全清理

**凭据不可用**（密文损坏 / `MCHECKER_SECRET_KEY` 变更 / 撤销后）：

1. 连接会进入 `disabled`/`locked` 状态，**不会**回退明文；业务功能（文件浏览、搜索、DL）不受影响，仅 SDA 同步不可用。
2. 设置 `MCHECKER_OPERATOR_TOKEN` 后，在「连接设置」重新导入凭据候选 → probe → 激活。
3. 密钥丢失无法解密历史密文：重新导入即可，旧密文会被新 generation 覆盖。

**安全清理**：

```bash
# 诊断 Blob 只含脱敏摘要；不下载 HAR/Cookie/正文
# 更新管理 → 运行记录 → 选中 run → 下载诊断
python scripts/scan_secrets.py --tree          # 发布前自查
python scripts/scan_secrets.py --all-history   # 发布门
```

不要把 `data/`、`logs/`、`*.har`、浏览器 profile 目录拷到工单或聊天里。

## 6. collector（SDA 采集器）

- 独立环境：Python **3.12** + `playwright` + `cryptography`；主应用不 import 它。
- 打包与校验（与核心包分离）：

```bash
python scripts/collector_bundle.py --out dist/collector-bundle.zip
python scripts/collector_bundle.py --verify-only --out dist/collector-bundle.zip
#[外部] 目标机准备好 wheel 与 Chromium 后再带上
python scripts/collector_bundle.py --out dist/collector-bundle.zip \
  --wheels /path/wheels --chromium /path/chromium
```

- 缺 collector 时主应用照常启动，只是 SDA 连接显示不可用；普通文件/搜索/DL 不受影响。
- 停止顺序：先排空任务 → 停调度 → 停 collector → 关 HTTP → 释放单实例锁（`service.sh stop` 已内置）。

## 7. 诊断入口

| 现象 | 看哪里 |
|---|---|
| 同步失败/部分失败 | 更新管理 → 运行记录 → 该 run 的 items/events；失败项可"重试失败项" |
| 来源不参与同步 | 更新源列表状态（停用/已删除）、计划（manual 不入自动列） |
| 预览 409 | 来源/连接/计划/目标在预览后变了 → 重新预览 |
| 凭据 403 | 无 `integration_credentials_manage` 能力或缺 `X-Operator-Token` |
| 日志 | `<数据根>/logs/mcchecker.log`（已脱敏） |
| 启动失败 | 单实例锁（同数据根+端口只能一个实例）、端口占用、数据根权限 |

## 8. 发布前检查清单

```bash
PYTHON=./.venv39/bin/python bash scripts/check_py39.sh
MCCHECKER_REQUIRE_H3=1 ./.venv39/bin/python -m pytest -q
./.venv39/bin/python scripts/check_web_ui.py
./.venv39/bin/python scripts/e2e_browser.py --only all
./.venv39/bin/python scripts/scan_secrets.py --all-history
./.venv39/bin/python scripts/make_release.py --out dist/MCChecker-<版本>.zip
./.venv39/bin/python scripts/make_release.py --verify-only --out dist/MCChecker-<版本>.zip
./.venv39/bin/python scripts/collector_bundle.py --verify-only --out dist/collector-bundle.zip
```

`[外部]` 未完成前不得宣称可发布：Windows/Linux 断网安装与首次启动（E4/E5）、真实 SDA 与真实 idle（E2/E3）。
