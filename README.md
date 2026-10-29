# MCChecker - 配置文件解析检查工具

基于 Python 标准库 HTTP 服务的离线内网配置文件解析检查工具，支持 XML/JSON 格式配置的在线解析、查看、搜索、对比和版本管理。

## 快速启动

```bash
# 运行时零第三方依赖，不需要 pip install
python main.py
```

Python 要求 **3.9.11**；发布包内已带全部运行代码，目标机器无需联网安装依赖。

服务默认在 `0.0.0.0:50003` 启动（`app/config.py` 的 `DEFAULT_PORT`），内网其他机器可通过 `http://<服务器IP>:50003` 访问；可通过环境变量 `MCHECKER_PORT` 或 `--port` 指定端口。

Linux 上要脱离终端运行时用 `./service.sh start`，停止用 `./service.sh stop`（发送 SIGTERM，等有序退出）。`./service.sh status`、`restart`、`logs` 查看和管理。需要开机自启时，`./service.sh unit` 会打印一份 systemd 单元，审查后再交给 `systemctl`。在服务器上添加管理员：`sudo ./add-admin.sh --name 张三 --ip 10.1.2.3 --data-dir <数据根>`。细节见 `docs/manual.md`。

## 功能列表

- **文件解析**: 支持 XML/JSON 文件上传或 URL 下载解析，保留完整层级结构
- **标签页管理**: 每个文件独立标签页，互不干扰
- **配置速览**: 收藏关键变量，快速查看核心参数及更新日期
- **全局/局部搜索**: 跨文件检索或单文件检索，覆盖参数及备注
- **更新设置**: 配置文件名与下载链接映射，一键更新/管理更新源
- **定时自动更新**: 按小时/天设置自动刷新周期
- **版本归档**: 更新自动归档旧版本，支持版本时间轴查看
- **原文件下载**: 当前配置文件与历史归档版本均支持一键下载，保留原始文件名与版本标识
- **版本对比**: 4类对比场景（当前vs历史、历史vs历史、上传vs当前、上传vs历史）
- **变量绑定**: 配置不同命名变量的等价关系，对比时自动标注
- **工具菜单**: 聚合常用工具链接
- **多机型适配**: 顶部导航栏支持机型新增/删除/快速切换；各机型的下载链接、收藏、绑定、工具、定时配置、配置文件与历史版本互相隔离
- **权限隔离**: 管理员可修改配置，其他访问者仅可查看和临时上传

## 权限说明

- **管理员**（IP 在管理员列表中）：可修改所有配置，操作持久化保存。姓名只是助记，不决定权限
- **其他访问者**：仅可查看和临时上传文件，不会修改服务端配置

## 运行测试

```bash
pytest tests/ -v
```

## 技术栈

- Python 3.9.11（本机安装，或按手册使用发布包可选运行时）
- 标准库 `http.server` + JSON API
- 原生 JavaScript 前端（`app/web/`），零运行时第三方依赖
- 标准库进行 XML/JSON 解析、下载、定时调度与文件存储

## V3 工作区与配置管理

V3 把“工作区（Workspace）→ 机型（Profile）”作为业务边界，入口在左侧导航“配置管理”：固定配置、软件版本、canonical D0、基线 Bn 与机台 rn。参数提取、合并、值导入、模板导出、配置历史/恢复都走真实后端；发布只移动管理系统的 head，**不表示真实机台已下发/安装/回读**。

旧 `M5–M8` 配置域保留只读兼容；需要停写时设 `MCHECKER_CONFIG_MANAGEMENT=readonly` 并重启，旧写接口返回 `CONFIG_DISABLED`。V3 数据在 `data/workspaces/<wid>/profiles/<pid>/configs_v3/`，全局身份/权限仍在数据根，不随工作区复制。

V3 运维命令（服务停止后执行；先备份再迁移）：

```bash
python scripts/inspect_v3_workspace.py --data-dir data --out backups/v3-manifest.json
python scripts/backup_v3.py backup --data-dir data --out backups/v3-full
python scripts/backup_v3.py verify --backup backups/v3-full
python scripts/migrate_v3.py plan --data-dir data
python scripts/migrate_v3.py execute --data-dir data --backup backups/v3-full
python scripts/migrate_v3.py rollback --data-dir data --yes
```

发布包：`python scripts/make_release.py --out dist/`。V3 最终发布门禁仍受真实浏览器/真实模板/设备/Windows/Linux/离线证据与 `INH-1910`/`INH-1978` 阻塞，见 `docs/V3_RELEASE_GATE.md`；在此之前不要把 V3 称为“已发布”。

## 配置管理（M5–M8，旧域只读兼容）

配置管理域默认开启（未设置 `MCHECKER_CONFIG_MANAGEMENT` 时等同 `on`）；设为 `off` 或 `readonly` 并重启可关闭或只读。它提供参数登记、软件版本/定义集合、现场采集与对账、变更单批准发布、XML 产物下载、人工导入登记、回读核验、审计谱系和机型生命周期；设备层按计划延期，状态接口会明确返回“未启用”。

常用运维命令（均在服务停止后执行，详细步骤见 `docs/manual.md`）：

```bash
# 预检旧 data/，只读
python scripts/inspect_config_management.py inspect --data-dir data

# 先备份，再初始化配置管理域
python scripts/inspect_config_management.py initialize --data-dir data --backup-dir backups/mcc-before-init --yes

# 全域备份 / 校验 / 恢复
python scripts/config_backup.py backup-full --data-dir data --out backups/mcc-backup
python scripts/config_backup.py verify-full --backup backups/mcc-backup
python scripts/config_backup.py restore-full --backup backups/mcc-backup --data-dir data --dry-run

# 或由启动入口执行（默认 dry-run，只有 --apply-restore 才写）
python run.py --restore-backup backups/mcc-backup --data-dir data
python run.py --restore-backup backups/mcc-backup --data-dir data --apply-restore
```

发布包与兼容性：`python scripts/make_release.py --out dist/`；`check_py39.sh` 覆盖 `app/`、`run.py`、`main.py` 与 `scripts/`。跨版本/真实设备/物理断网证据仍未全部取得，详见 `docs/MCChecker_LEFTOVERS.md` 与验收台账。

## 配置说明

- 默认端口是 `app/config.py` 的 `DEFAULT_PORT`（50003）；`python run.py --port <端口> --data-dir <目录>` 可覆盖。
