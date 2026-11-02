# M16-39 故障注入覆盖矩阵

> 2026-11-02。离线/合成 fixture 与本地子进程证据；真实 SDA 内网 mid-download kill 仍归 M16-41。

| 卡面验收 | 覆盖 | 证据 |
|---|---|---|
| collector 子进程崩溃可见 | 真实 dummy collector 启动后被 kill，status=failed/EXITED | `tests/test_collector_manager.py::test_child_crash_after_start_is_reported` |
| IPC 断流/EOF 不产出半文件 | 分块读取中途抛错，staging abort；末块截断恢复 | `tests/test_collector_streaming.py`、`tests/test_bounded_fetch.py` |
| 中断解压/炸弹写前终止 | zip/tar 条目数/解压总量/压缩比预检 | `tests/test_bounded_fetch.py::test_archive_preflight_rejects_bomb_before_extraction` |
| 已提交但 status 写失败按 receipt 恢复 | 匹配 current sha 才推进 last_good；不匹配标 unknown | `tests/test_sync_faults.py::test_receipt_recovery_never_invents_success` |
| 双 W 并发同名来源 | 各自文件字节不串 | `tests/test_sync_faults.py::test_two_workspaces_run_same_names_without_stealing_bytes` |
| 同源二次提交 | BUSY，第二个 run interrupted | `tests/test_sync_faults.py::test_second_run_same_profile_is_busy_and_marked_interrupted` |
| 改 binding / 旧回包 | run 启动冻结 binding，提交前复核；人工改文件则 blocked | `tests/test_sync_guards.py::test_binding_change_blocks_commit` |
| 撤权/换凭据 epoch | 提交前 assert_epoch；旧 epoch blocked | `tests/test_sync_guards.py::test_credential_epoch_change_blocks_commit` |
| 关连接旧回包 | 提交前复核 enabled，停用则 blocked | `tests/test_sync_guards.py::test_connection_disabled_blocks_old_response` |
| 旧 base_sha 回包 | 锁内 base_sha 不符拒绝，不覆盖人工修改 | `tests/test_sync_faults.py::test_old_response_with_stale_base_sha_does_not_overwrite` |

运行：

```bash
./.venv39/bin/python -m pytest -q \
  tests/test_sync_faults.py tests/test_sync_guards.py \
  tests/test_collector_manager.py tests/test_collector_streaming.py tests/test_bounded_fetch.py
```

边界：真实 SDA 下载过程中 SIGKILL、真实企业网络断线、Windows 任务树终止仍属 E2/E4，保持 `BLOCKED-EXTERNAL`。
