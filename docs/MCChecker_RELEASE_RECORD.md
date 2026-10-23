# MCChecker v2 发布记录（M5–M8 收口）

生成：2026-10-23。本文是发布闸门的底稿，不是“全部验收通过”的声明。

## 版本与范围

- 运行时：Python **3.9.11**，标准库 HTTP + 原生 JS，**零运行时第三方依赖**。
- 本次范围：M5 配置治理基础、M6 单版本闭环、M7 跨版本生命周期、M8 迁移/发布门禁。
- 集成分支：`mc-v2/m5-m8`；最终合并到 main 的提交以 `git log -1` 为准。
- 设备层：M7-07/M7-08 已 Canceled，按计划交付“功能关闭”，不提供 `/config/devices` 或 `device_rebase` 入口。

## 功能清单

- 配置域：不可变对象/blob、事务/CAS/幂等/审计；参数主档、定义集合、软件版本与出口快照；追踪策略与旧入口守卫；现场采集、对账、变更单批准发布；XML 产物、下载与人工导入登记；回读核验；审计检索与来源谱系；机型/配置类别退役、停用重开漂移确认。
- 运维工具：`scripts/inspect_config_management.py`（inspect/initialize/migrate/verify）、`scripts/config_backup.py`（最小域备份与 schema 2 全域备份/恢复）、`run.py --restore-backup`。
- 发布：`scripts/make_release.py` 包含新增脚本；`scripts/check_py39.sh` 覆盖 `app/`、入口与 `scripts/`；发布包不包含 `data/`、mock、测试资产或私人样本。

## 验证证据（本地，2026-10-23）

- 全量后端：`2544 passed`（M7-12 收口时；M8 追加测试后以最终全量输出为准），Python 3.9.11。
- 前端：HTTP 形态无头 Chrome，`ALL PASSED (96)`（M7-11 后；M8 文档不改变用例数）。
- `check_py39.sh`：通过（已包含 `scripts/`）。
- 发布测试：`tests/test_release.py` 通过；真实仓库构建已包含备份/迁移脚本，成员集合与假树同步。
- 备份/迁移：合成 v1/v2/mixed 树的 inspect 只读、先备份初始化、幂等迁移、pending 标记、全域备份校验/恢复/重复恢复幂等/注入失败回滚均有 pytest 证据。
- 安全：新写路由登记四角色/CSRF；线程并发批准只产一份修订；state 替换失败不留下“审计有、状态无”。

## Schema 兼容矩阵

| 数据 | 当前格式 | 兼容策略 |
|---|---|---|
| `data/config_management/state.json` | schema 1（仓库内置） | 只读旧 state；损坏时报 `CORRUPT_DATA`，不归零；迁移/恢复前必须先备份 |
| `data/config_management/objects|blobs|audit|receipts` | 内容寻址/不可变 | 旧对象保持可读；不可原地改写，只能新增修订 |
| `device_models.json` | `{id,name}`，新增 `status/retired_*` 可选字段 | 缺省视为 `active`；退役保留条目与目录 |
| 全域备份 manifest | schema 1 = M8-02a 最小域；schema 2 = M8-02 全域 | schema 1 只能用于 `restore`（配置域）；schema 2 用 `verify-full/restore-full` |
| 旧 data 布局 | v1（根 configs/archive） / v2（profiles/<pid>） / mixed | `inspect` 只读分类；mixed 必须人工确认权威来源；初始化不猜版本 |

## 回滚

1. 停服务，确认单实例锁已释放。
2. 用 `restore-full --dry-run` 核对备份；执行时保留 `pre_restore_<ts>` 安全副本。
3. 恢复失败时按安全副本回滚；不要删除恢复区历史。
4. 只回滚单个误删文件时，走 `docs/manual.md` §14.6 的恢复区流程，不做全域恢复。

## 未解决风险（不得写成已通过）

- M4-08：UC-01…UC-46 的逐行真实浏览器记录、§16.5 脱敏旧数据副本、§16.7 物理断网仍未完成。
- M6-14：真实系统 XML 导入与操作者/环境/结果未取得；卡不得关闭。
- M7-10：真实设备回读端到端未做；卡保持 In Progress。
- M8-02：两套真实副本、磁盘满、`kill -9` 真实进程演练未做；本地只覆盖故障注入回滚。
- M8-03/M8-05：完整真实浏览器业务流、真实设备、物理断网、配置矩阵/审计性能与 INH-1932 决策未完成；卡不得关闭。
- M8-06：Windows/Linux 首装首访证据未做；本地只验证了构建/成员/离线扫描与零第三方导入。
- M8-07：最终闸门依赖上述证据；在缺口关闭或取得带批准人与日期的书面豁免前，不签署“全部通过”。
