# V3 Q1–Q6 / A01–A17 追踪矩阵（2026-10-29）

状态只认自动测试/真实证据；PARTIAL 表示有实现但仍有未取证的浏览器/异常路径。

| ID | 状态 | 证据 |
|---|---|---|
| Q1-A | PASS | `tests/test_v3_workspace_isolation.py`、`test_v3_original_features.py`、前端 M9-05/工作区测试 |
| Q1-B | PASS | `test_v3_workspace_isolation.py`（两 W 同名 MC-001/Pulse）、`test_v3_overview_scope.py` |
| Q1-C | PASS | `test_api_workspace_profiles.py`、`test_api_security.py`：普通用户不能建空间、全局权限一致 |
| Q1-D | PASS | 工作区名称 1–50/说明 ≤300/全局唯一/仅管理员用例（M9-02/05） |
| Q2 | PASS | 前端 `configspace` 深链 → configv3；旧导航与默认速览保留 |
| Q3-A | PASS | `test_v3_file_identity/facade/workflows.py`：原上传/临时/归档/记录/下载 |
| Q3-B | PASS | `test_v3_file_provenance.py`、`test_v3_file_source_faults.py`：tombstone/冻结值 |
| Q4 | PASS | 前端 M13-01 双模式共享 draft/ETag/选中；树层级与 0/false 用例 |
| Q5-A | PASS | `test_v3_overview.py`、`test_v3_overview_scope.py` |
| Q5-B | PASS | `test_v3_param_history_diff.py` 八类差异/null/unset |
| Q6-A | PASS | `test_v3_extract_identity.py` 3,001 叶、`test_v3_extract_preview.py` 分页 |
| Q6-B | PASS | `test_v3_extract_commit.py` 3000+50、三次重复 0 新建 |
| Q6-C | PASS | 补绑默认关/开启只补缺失/冻结拒绝（`test_v3_extract_commit.py`） |
| Q6-D | PASS | 故障零提交、幂等/不同体 409、stale 拒绝 |
| A01 | PASS | 前端壳层/标签/收藏/备注/命令面板用例 |
| A02 | PASS | `test_v3_review_scope.py`、原 review 回归 |
| A03 | PASS | `test_v3_overview_scope.py`、`test_v3_file_workflows.py` |
| A04 | PASS | `test_v3_dl_scope.py`、`test_v3_tasks_settings_scope.py` |
| A05 | PASS | `test_v3_tasks_settings_scope.py` 任务建时捕获 W/P |
| A06 | PASS | `test_v3_default_freeze.py` 双冻结/唯一 D0 |
| A07 | PASS | `test_v3_parameter_definition.py`、`test_v3_release.py` |
| A08 | PASS | `test_v3_baseline_head.py`、`test_v3_machine_branch.py` |
| A09 | PASS | `test_v3_draft_cas.py` typed 0/false/空串/null |
| A10 | PASS | `test_v3_oln_matrix.py`、`test_v3_merge_plan.py` pin/同值不同意图/停用 |
| A11 | PASS | `test_v3_value_import.py` 未冻结原地/冻结新草稿/缺失不删 |
| A12 | PASS | `test_v3_export_preservation.py`、`test_v3_restore_audit.py` |
| A13 | PASS | `test_v3_security.py`、`test_v3_workspace_transaction.py` |
| A14 | PASS | `test_v3_parser_locators.py`、`test_v3_security.py`、`test_v3_file_source_faults.py` |
| A15 | PARTIAL | shell 工作区切换有 dirty 确认；配置域未保存值/决议离开保护、延迟响应关闭后写入未逐项取证 |
| A16 | PASS | `test_v3_migration_dryrun.py`、`test_v3_migration_recovery.py` |
| A17 | PARTIAL | 性能 `tests/performance_v3.py` PASS；375 移动视口修复与截图 PASS；键盘树/完整焦点走查未完成 |

## 结论

27 项 PASS、2 项 PARTIAL。A15/A17 是 2159 仍未 Done 的直接原因；其余代码合同由可复跑测试支撑。外部模板/设备/Windows/Linux/离线不在本矩阵内，见 `V3_INTEGRATION_EVIDENCE.md`。
