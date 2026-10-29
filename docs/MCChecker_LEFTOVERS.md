# MCChecker v2 遗留任务（2026-10-22）

本文件是**阻塞项的唯一入口**，不是验收通过证据。按用户指示：有阻塞项先跳过、记录在此，解除后回填。

## L1 · M4-08 §16 全量验收门禁（阻塞）

- Linear：**INH-1910**，状态 In Progress。
- 已完成的自动化/代码证据（收集于 `fdd8558`；当时 main 为 `3a60746`，2026-10-28 已到 `c24ab23`）：全量 1859×3、H3「执法中：真实 data/ 3 文件/8 目录」、前端 `ALL PASSED (44)` ×3、发布测试 168、`check_py39.sh` 通过；29 条写路由修订语义对账；四角色/403 sha 不变由各卡用例执法。
- 已回填的真实浏览器/真实后端证据（分支 `mc-v2/m4-08-acceptance` @ `6df6d2b`，**未合并**）：UC-01～06、43、45、46；其余 UC 已补自动化测试证据矩阵。该分支基于 `5ea1aef`，main 已到 `c24ab23`，且台账已追加 M5–M8 段，合并前需 rebase。
- 仍缺（卡面规定缺一行不能关闭）：
  1. UC-01–46 每一行的真实浏览器步骤与结果；尤其 UC-07（5 万节点体感）、UC-19（文本差异复制/下载）、UC-40（双浏览器 10 秒）、UC-45（响应式体感）。
  2. §16.5 数据兼容：脱敏旧 `data/` 副本 → 新版读出 → 新版写入 → v1.0.1 + Python 3.12 读回；没有副本就标「未验证」，不得用空目录代替。
  3. §16.7 离线：物理断网机器首访/功能；构建期外链扫描不能替代。
- 解除条件：提供脱敏旧数据副本、物理断网机器、人工验收时间；补齐台账后重跑 §16.1 四层命令并关闭。

## L2 · INH-1932 · 100 文件档搜索预算（阻塞）

- 实测（主线 `c3f252f`，`scripts/bench_search.py --files 100 --nodes 30000`）：P50 **2688.0 ms**、P95 2836.1 ms，预算 500 ms；无命中扫描 P50 1794.7 ms。
- 原因：100 文件 × 3 万节点，文档内存缓存按字节上限 ~71 项开始逐出，每次搜索重新反序列化。
- 需在「换机制（预计算小写数组/索引）」与「正式放宽该规模档预算并同步 SPEC/know-how/基准脚本」之间二选一；未决定前不改通过线。

## L3 · M3-11 非阻塞残项（不影响验收）

- 锁键归一化（profile id 大小写/空白）待统一。
- mock 极端值（超大数/全角数字）与后端判决差异，已在 `tests/test_mock_dl_contract.py` 记录覆盖缺口。
- 全角数字输入未纳入数值语法。
- B10.1：§7.3「revision 不读磁盘」与 R13 身份实时读的冲突已按决策 16 收窄为「计数逻辑零 I/O、端点已知读 3 个身份文件」；若要求端点字面零读盘，需另开规范修订卡。

## L4 · M6-14 真实系统 XML 导入（阻塞）

- Linear：**INH-1959**，仍 Backlog（代码已落地：产物生成/不可变/同输入复用/下载不重生成）。
- 卡面要求把一份代表 XML 真实导入目标系统并记录操作者/环境/结果；当前 `delivery_status` 恒为 `unverified`。
- 解除条件：提供真实系统与导入机会；在此之前不得把 M6-14 置 Done，也不得把“真实导入”写成已通过。

## L5 · M7-10 真实设备端到端（阻塞）

- Linear：**INH-1969**，已置 In Progress。本地实现已提交 `d2be668`，覆盖错版本、旧快照、缺项、不可观测与逐次留档；真实设备回读未做。
- 卡面明确“真实端到端需要目标设备验证”；缺设备时只能给自动化证据，不能宣称卡关闭。

## L6 · M8-02 全域备份/恢复演练（阻塞）

- Linear：**INH-1973**，仍 Backlog；**M8-02a 最小子集**（`9611dbb`）与 **M8-02 全域实现**（`backup-full`/`restore-full`、manifest schema 2、维护态、安全副本、回滚、重复恢复幂等、故障注入测试）已落代码。
- 仍缺：两套真实数据副本演练、磁盘满、`kill -9` 真实进程中断、生产规模备份耗时与恢复窗口记录。
- M8-02 是正式现网受管写入的硬前置；未演练前不得放开生产写入。

## L7 · M8-03 / M8-05 真实浏览器、设备与物理断网（阻塞）

- **INH-1974 / M8-03**：已做真实 `run.py` + 临时数据根 + 无头 Chrome 的生产壳冒烟（`mock:false`，首屏正常）；完整“参数登记→采集→对账→变更→发布→下载→人工导入登记→迁移→回读”的真实浏览器步骤、双分辨率、四角色、前进后退/草稿/冲突截图仍未做。
- **INH-1976 / M8-05**：真实设备 XML 导入/回读、物理断网首访、配置矩阵/审计大规模性能与 INH-1932 决策仍未完成；不能用构建期外链扫描或 `--host-resolver-rules` 冒充物理断网。
- 两卡在证据补齐前保持未关闭，M8-07 不签最终闸门。

## L8 · M8-06 Windows/Linux 首装首访（阻塞）

- **INH-1977 / M8-06**：本地已把备份/迁移脚本纳入发行白名单、更新成员断言、`-S` 零第三方导入与离线扫描通过；Windows/Linux 目标机首装首访证据未做。本机 macOS 证据不能冒充目标 OS。

## L9 · 当前 main 的 4 条红灯（2026-10-28 已修，待提交/合并）

- 事实：`main@c24ab23` 上 `.venv39/bin/python -m pytest -q` 实测 **2567 passed / 4 failed**（168 s，H3 守卫执法中：6 文件/8 目录）；4 条在干净检出（无 `data/`）中也同样失败，不是本机数据或执行顺序噪声。
- 归属：
  1. `tests/test_api_import.py::TestPutFile::test_new_file_is_201_and_bumps_files` 与 `tests/test_api_delete.py::TestRecoveryMove::test_current_archive_and_records_all_move_into_one_entry`：PR #117 把配置管理默认档改为 `on` 后，旧写接口响应多出 `tracking` 字段，两条用例仍按旧形状逐字段断言。
  2. `tests/test_api_revision.py::TestRouteTable::test_route_table_and_expectation_table_cover_each_other`：PR #120/#121 新增的三条 DL 系数历史写路由（`POST /dl/{item}/coeffs`、`POST /dl/{item}/coeff-history/{hid}/apply`、`DELETE /dl/{item}/coeff-history/{hid}`）未登记进 `WRITE_ROUTE_EXPECTATIONS`；服务层实际会在参数变化时 bump `settings`，删除历史不 bump。
  3. `tests/test_mock_bindings_contract.py::test_mock_and_backend_agree_on_every_binding_case`：PR #120 在 `app/web/assets/js/mock/mock-api.js` 里新增了第二个 `bindingView`（DL 用），JS 函数声明覆盖了原有的绑定视图，`/bindings` 路由因此返回 `{source_file,variable_path,field_map}` 而不是 `{group_name,variables}`。
- 处置：先把 DL 用的 `bindingView` 改名（如 `dlBindingView`）并更新其调用点；补三条路由期望；旧写接口用例显式接受 `tracking` 或在该用例里关闭特性门禁。修完重跑全量，绿之前不得再写“全量通过”。
- **修复验证（2026-10-28，工作区未提交）**：4 条已改（mock 的 DL 视图改名 `dlBindingView`、补三条路由期望、两条旧写接口用例接受 `tracking`）；全量 **2571 passed**（166.48 s，H3 执法中），前端 HTTP **`ALL PASSED (96)`**，`check_py39.sh` exit 0。当前改动在分支 `chore/fix-main-red-20261028` 的工作区，尚未提交；main 仍按红灯口径，提交/合并后把本条移入「已解除」。

## 已解除

- B14（`https<LF>://` 控制符紧贴 scheme 冒号）：PR #101 修复并补形状/构建用例；发布闸门回归见该 PR。
