# 使用与运维手册

## 1. 概述

MCChecker 是一个基于 Python 标准库 HTTP 服务的内网配置文件解析检查工具，支持 XML/JSON 配置的在线解析、搜索、对比与版本归档。

## 2. 环境准备

- Python: 3.9/3.11/3.12
- 依赖：见 [requirements.txt](file:///d:/Project/MCChecker/requirements.txt)
- 测试依赖：需要额外安装 `pytest`（仓库未在 `requirements.txt` 内声明）

## 3. 安装

```bash
pip install -r requirements.txt
```

## 4. 启动与停止

启动：

```bash
python main.py
```

默认监听：`0.0.0.0:50003`（`app/config.py` 的 `DEFAULT_PORT`）

默认开启 Python 热更新。服务开着的时候覆盖 `app/`、`run.py` 或 `main.py` 里的 `.py`，进程会在源码稳定约半秒后自动停掉再拉起，随后刷新页面即使用新代码。这次重启会中断正在处理的请求。页面的 HTML/JS/CSS 本来就按请求读磁盘，改那些文件只要刷新，不必等重启。不需要热更新时用 `python main.py --no-reload`，或设置 `MCHECKER_RELOAD=0`。

停止：

- 前台运行：在终端按 `Ctrl+C`
- 后台运行：在发布目录执行 `./service.sh stop`。脚本发送 `SIGTERM` 并等待有序停止（任务排空，写盘结束后才删除单实例锁）。不要用 `kill -9`。

Linux 后台：

```bash
./service.sh start --data-dir /var/lib/mcchecker
./service.sh status
./service.sh logs
./service.sh restart
./service.sh stop
```

`start` 之后关闭终端，进程仍在。参数和 `python run.py` 相同，并会记下来，下次 `restart` 不带参数时沿用。`status` 在运行中返回 0，未运行返回 1。

用 systemd 托管时，先审查单元再安装。单元里固定加了 `--no-reload`：热更新的父进程收到停止信号后会立刻退出，systemd 会把还在做有序停止的子进程一起杀掉。装好之后用 `systemctl` 管理，不要再同时执行 `./service.sh start`。

```bash
./service.sh unit --data-dir /var/lib/mcchecker | sudo tee /etc/systemd/system/mcchecker.service
sudo systemctl daemon-reload
sudo systemctl enable --now mcchecker
sudo systemctl stop mcchecker
```

## 5. 多机型适配

### 5.1 切换入口

- 顶部导航栏的“机型切换”入口支持：
  - 快速切换机型
  - 新增机型（可选从当前机型复制配置）
  - 删除机型（可选同时删除该机型的全部配置数据）

### 5.2 隔离范围

每个机型拥有独立的数据空间，互不影响，包含但不限于：

- 全量配置映射（文件名 ↔ 下载链接）
- 收藏夹（收藏变量与备注）
- 变量绑定配置
- 工具菜单
- 定时更新配置
- DL 快捷计算参数
- 上传/下载保存的配置文件、历史归档版本与解析缓存

## 6. 配置项

### 6.1 端口与数据目录

默认端口是 `app/config.py` 的 `DEFAULT_PORT`（50003）。`python run.py --port <端口> --data-dir <目录>` 可覆盖。v2 不再使用 NiceGUI，`data/nicegui_storage_secret.txt` 不再读取或删除（部署机上的旧文件可保留）。

### 6.2 更新源回读

更新源下载的内容可以是 XML、JSON，或 zip / tar / gzip 压缩包。压缩包里的 XML 和 JSON 会解压进当前机型的文件列表。可选的正则关键词用来切削文件名：关键词 `MachineConstant` 会把 `Default_MachineConstant_basics.xml` 保存为 `MachineConstant.xml`。不填关键词时，单个文件仍按更新源的文件名保存，压缩包按包内原文件名加入。

## 7. 数据目录说明

默认数据目录为 `data/`，其中：

- 默认机型使用 `data/profiles/default/` 作为数据空间
- 非默认机型使用 `data/profiles/<model_id>/` 作为数据空间

该目录内包含配置文件、历史归档、缓存与若干 JSON 持久化文件。

## 8. 文件下载

- 左侧“文件列表”中的每个当前配置文件条目都提供独立下载按钮，可直接下载当前生效版本。
- 管理员可在左侧文件列表中删除配置文件。确认框说明将删除的历史版本、修改记录以及恢复方式，确认后即删除，不需要输入文件名。文件移入服务器恢复区。
- 侧栏「配置文件」旁的添加按钮打开导入对话框。本地文件可一次选择多个 XML 或 JSON；多个文件按各自的文件名保存，仅导入一个文件时仍可修改文件名，并在完成后打开。从链接导入仍是一次一个文件。
- 文件解析页顶部提供“下载文件”按钮，便于在查看内容时直接下载当前版本。
- “版本历史”页面支持下载当前版本与任意历史归档版本，历史文件会保留原始文件名及版本日期后缀，避免版本混淆。
- 下载失败时，界面会给出明确失败原因，并提供“重试下载”入口。

## 9. 审阅修改

- 文件解析页中的条目支持在 hover 时打开“修改备注”输入弹窗，系统会自动记录提交 IP，并通过 IP 对应表归属到具体人员。
- “更新设置”页的人员与权限对所有机型共用一套，不随机型切换：
  - `IP 对应表`：给访问地址记一个姓名，只用于显示。
  - `管理员列表`：按 IP 授权。管理员是最高权限，可以审阅、删除文件、保存配置、改设置和机型。
- 右上角身份：未登记姓名显示“访客”，登记了姓名但不是管理员显示“成员”，IP 在管理员列表中显示“管理员”。
- 本机访问不再自动成为管理员。还没有管理员时，在服务器上用 sudo 写入。`sudo` 默认不继承当前环境变量；数据根以 `./service.sh status` 里的为准，不在发布目录下的 `data/` 时要写 `--data-dir`。

```bash
sudo ./add-admin.sh --data-dir /var/lib/mcchecker --name 张三 --ip 10.1.2.3
```

`--ip` 决定谁有管理员权限，`--name` 只是助记。写完后文件属主改回数据目录原来的属主。该地址刷新页面后即为管理员。
- “审阅修改”页仅对管理员开放，管理员必须对每个待审节点明确选择一条通过项或“不通过”。
- 审阅通过后，系统会按时间戳生成新的目标文件，保存到当前机型的 `configs` 目录，并自动出现在左侧文件列表中。

## 9.1 配置文件查看

结构视图尽量把纵向空间留给节点列表：文件名和更新时间在同一行，视图切换在下一行。选中节点后，详情里的「修改建议」在「属性」上面，「位置」在属性下面。XML 元素上的属性都会列在「属性」里；`param_` 节点另外用「参数信息」标出描述和取值范围。左侧「显示属性」关掉时，树里不单列属性行，详情里仍然看得到。

## 10. DL 计算

侧栏「DL 计算」以八阶多项式进行正向求值与反向求解。页面分为「求解」（输入、曲线、结果）、「函数与参数」（系数、范围、绑定），以及 RP 脉宽的「输出量」。参数修改仅作用于当前会话，保存后对所有用户生效。

四个计算项的横轴、纵轴是：

- PP 能量：x 为电动波片角度（urad），y 为 PP@PF能量（mJ）
- RP 能量：x 为 RP能量（mJ），y 为 TFP2角度(SMTM页面)（urad）
- RP 脉宽：x 为 RP脉宽（ns），y 为 AOM2 delay(SMIM1页面) 或 AOM2 width(SMIM1页面)（ns）
- PPz：x 为调焦镜发散角（urad），y 为 BTS_ppz（um）。与 RP 能量一样使用八阶多项式，没有传输系数或功率转换系数

**求解。** 在「正向 x → y」与「反向 y → x」之间切换，于左侧输入数值（以空格、逗号、分号或换行分隔）。输入停止约 0.35 秒后自动计算，参数变更后同样重算，结果始终对应当前参数；`Ctrl+Enter` 立即计算。右侧曲线按已填写的 x、y 上下界和计算点自动取景，上述要素均完整保留在画面内。距其较近的极值点纳入画面，并以较小字号标注坐标；过远的极值不改变坐标范围。超出该范围的曲线部分予以裁切。填写 x 的上下限后，图下给出该范围内曲线上 x 与 y 的最大值和最小值；若同时填写 y 的上下限，仅统计落在 y 范围内的点。结果行悬停时高亮对应计算点。结果表与输入逐行对应，可复制或下载 CSV。反向求解出现多个解时，须逐行指定采用的解，系统不自动选取。

**RP 脉宽。** 「输出量」提供 AOM2 delay 与 AOM2 width 两种模式。两种模式各自维护系数、有效范围与系数绑定；切换后，参数、曲线与结果同步切换。模式卡片上的橙色圆点表示该套参数有未保存的修改。「固定和」为 AOM2 delay 与 AOM2 width 之和（ns），可修改。当前 y 为其中一项时，结果表增加一列，值为「和 − y」。未填写固定和时，该列显示破折号。保存时两套参数与固定和一并写入。

系数可以从配置文件提取：在「系数绑定」里点「修改绑定」，选择文件后可以搜索、点选变量，也可以直接填写或粘贴变量路径（`文件名/标签/标签`、节点 ID 或定位符）。XML 和 JSON 都用这一格式。XML 属性写成 `min` 或 `@min` 都可以。RP 脉宽的两种模式各自有一份绑定。

从链接导入时，文件名会保留链接路径或 `file` / `filename` / `name` 参数里的 `.xml` / `.json` 后缀。

## 14.6 恢复区手工恢复（v2）

1. 停止 MCChecker（前台 Ctrl+C，或 `./service.sh stop`），确认单实例锁已删除。
2. 在 `data/recovery/` 下找到目标条目，读取 `manifest.json` 的 `moved`（`from` 是原位置、`to` 是恢复区内副本）与 `removed_refs`。
3. 先运行 `python run.py --restore data/recovery/<条目> --dry-run --data-dir data` 复核计划；该命令只打印、不写任何文件。
4. 手工把 `to` 指向的副本复制回 `from`，逐文件比对 sha256；引用（收藏/绑定/建议）按 `removed_refs` 手工回填。
5. 重启服务，复核文件列表、对比与修订号；恢复区条目保留，不自动清理。

## 15. 配置管理域（M5–M8）

配置管理默认开启（未设置 `MCHECKER_CONFIG_MANAGEMENT` 时等同 `on`）。若需关闭或只读，在服务端设置 `MCHECKER_CONFIG_MANAGEMENT=off` 或 `readonly` 后重启；`off` 时配置域 API 返回 503，`readonly` 只允许读取。入口在“审阅修改”页的分段中：修改建议、配置变更、版本升级、经验与历史、审计与谱系。

工作流：登记软件版本与参数定义 → 生成默认 XML/机型基线 → 采集现场 JSON 为不可变观测 → 文件内三栏对账 → 提交变更单 → 管理员批准并发布新基线 → 从已批准基线生成不可变 XML → 下载并在真实系统人工导入 → 在“交付”页登记操作者/时间/证据/结果 → 用新观测做回读核验。`已生成`、`已下载`、`已声明成功`、`已核验` 是四个不同状态，不能互相冒充。

配置管理域的数据在 `data/config_management/`；原 `profiles/`、`configs/`、`archive/` 与人员表保持原位。管理域的写操作走事务与审计，设备层未启用时不会出现设备目录或设备例外入口。

## 16. 旧数据预检、初始化与迁移

先停服务，再对数据根做只读预检：

```bash
python scripts/inspect_config_management.py inspect --data-dir /var/lib/mcchecker
```

确认无 blocker 后，初始化必须先备份：

```bash
python scripts/inspect_config_management.py initialize \
  --data-dir /var/lib/mcchecker \
  --backup-dir /var/backups/mcc-before-config-init --yes
```

初始化只建立空的 `config_management` 域，不猜版本、不自动建基线。之后按 `plan.json` 迁移文件快照：

```bash
python scripts/inspect_config_management.py migrate \
  --data-dir /var/lib/mcchecker --plan plan.json --apply
```

`plan.json` 每项至少包含 `filename`、`profile_id`、`decision`；只有显式给出 `release_id` 与 `logical_file_id` 且 `decision=link` 才会登记快照。缺版本、缺逻辑文件或哈希不一致的项写入 `config_management/imports/pending_links.json`，标记“待关联”，不会自动猜当前版本。重复执行是幂等的；换 plan 需显式 `--force-plan`。

## 17. 全域备份、恢复与回滚

```bash
# 1) 停服务，确认单实例锁已释放
./service.sh stop

# 2) 备份 profiles、人员/管理员/机型表、config_management 与恢复区；cache 不入包
python scripts/config_backup.py backup-full \
  --data-dir /var/lib/mcchecker --out /var/backups/mcc-20261023

# 3) 校验 manifest、逐文件 sha256
python scripts/config_backup.py verify-full --backup /var/backups/mcc-20261023

# 4) 先看计划，不写任何文件
python scripts/config_backup.py restore-full \
  --backup /var/backups/mcc-20261023 --data-dir /var/lib/mcchecker --dry-run

# 5) 确认后显式执行；目标非空必须 --force，清理陈旧文件用 --prune
python scripts/config_backup.py restore-full \
  --backup /var/backups/mcc-20261023 --data-dir /var/lib/mcchecker \
  --force --prune
```

恢复会在 `data/recovery/pre_restore_<时间>/` 留一份恢复前安全副本；中途失败按该副本回滚。同一备份重复恢复是幂等的，不会重复写审计。`--exclude-recovery` 可让备份不包含恢复区（包更小，但恢复后手工恢复区记录不回来）。

也可以从启动入口执行；默认只打印计划：

```bash
python run.py --restore-backup /var/backups/mcc-20261023 --data-dir /var/lib/mcchecker
python run.py --restore-backup /var/backups/mcc-20261023 --data-dir /var/lib/mcchecker --apply-restore
```

回滚原则：先恢复备份，再启动服务；不要让新业务数据在未备份的情况下被旧快照覆盖。若只需要恢复一个误删文件，仍走 §14.6 的恢复区手工流程，不要执行全域恢复。

## 18. 发布与已知未验证项

发布前依次执行：`.venv39/bin/python -m pytest -q`、`PYTHON=./.venv39/bin/python scripts/check_py39.sh`、前端 HTTP 验收页、`pytest -q tests/test_release.py`，再构建发布包。发布包不含 `data/`、mock、测试资产或私人样本。

截至 2026-10-23，以下证据仍未取得，不得写成“已通过”：

- M4-08 的逐 UC 真实浏览器人工记录、§16.5 脱敏旧数据副本、§16.7 物理断网；
- M6-14 的真实系统 XML 导入、M7-10 的真实设备回读；
- M8-03/M8-05 的真实浏览器完整业务流、真实设备与物理断网验收；
- INH-1932 的 100 文件搜索性能决策。

缺这些证据时，验收台账与 Linear 卡保持未关闭；M8-07 最终发布闸门不能签署“全部通过”。详见 `docs/MCChecker_ACCEPTANCE_LEDGER.md` 与 `docs/MCChecker_LEFTOVERS.md`。
