# MCChecker Release Report

**版本**: v2.0.0-rc  
**日期**: 2026-09-12  
**基线**: e47e54a0  
**审查基线**: v1.0.1  

---

## 1. 环境 (Environment)

| 项目 | 值 |
|------|-----|
| Python | 3.12.13 |
| OS | macOS (darwin 26.x, aarch64) |
| 框架 | NiceGUI >= 2.0.0 |
| 调度器 | APScheduler >= 3.10.0 |
| 测试框架 | pytest 9.1.1 |
| 运行模式 | 单进程 / 单调度器 |

---

## 2. Fixture 清单 (Fixture)

| Fixture | 格式 | 用途 | 大小 |
|---------|------|------|------|
| legacy layout XML | XML | 旧版无 namespace 配置 | ~250B |
| new layout JSON | JSON | 新版嵌套配置 | ~200B |
| mixed layout XML | XML | 属性混合元素 | ~180B |
| repeated XML | XML | 重复元素 | ~180B |
| large XML | XML | 大文件（700 entries） | ~63KB |
| special JSON | JSON | Unicode / 特殊字符 | ~200B |
| two profiles (alpha/beta) | — | profile 隔离验证 | — |
| pending remarks | — | 审阅建议 | — |
| DL config | — | 八阶函数系数 | — |
| bindings | — | 变量绑定 | — |

---

## 3. 测试结果 (Result)

### 3.1 总览

| 指标 | 值 |
|------|-----|
| 总测试数 | 571 |
| 通过 | 571 |
| 失败 | 0 |
| 跳过 | 0 |
| 新增 release gate 测试 | 40 |
| 执行时间 | ~2.5s |

### 3.2 Release Gate 分类 (40 tests)

| 分类 | 测试数 | 通过 |
|------|--------|------|
| 浏览器闭环 | 8 | 8 |
| DL 计算工作流 | 2 | 2 |
| 更新管理 | 2 | 2 |
| 手动记录刷新 | 1 | 1 |
| Profile 切换隔离 | 1 | 1 |
| Disconnect/Reconnect | 3 | 3 |
| 离线安装 | 2 | 2 |
| 冷浏览器/无公网 | 2 | 2 |
| 本地 smoke | 1 | 1 |
| 无公网静态请求 | 1 | 1 |
| Backup/WriteGate | 2 | 2 |
| Hash manifest | 1 | 1 |
| 迁移中断恢复 | 2 | 2 |
| 改名中断恢复 | 1 | 1 |
| 审阅批次中断恢复 | 1 | 1 |
| 更新提交中断恢复 | 1 | 1 |
| 双客户端隔离 | 1 | 1 |
| 双 profile 隔离 | 1 | 1 |
| 支持边界声明 | 2 | 2 |
| 代表文件大小 | 2 | 2 |
| P0 模块验证 | 6 | 6 |
| 恢复流程 | 2 | 2 |

---

## 4. 失败用例 (Failed Case)

**无失败用例。**

所有 571 个测试均通过，包含 40 个新增的 release gate 测试。

---

## 5. 手动步骤 (Manual Step)

以下场景需要手动验证（自动化测试无法覆盖）：

| # | 场景 | 验证方法 |
|---|------|----------|
| 1 | 真实浏览器首屏渲染 | 启动服务后手动访问 http://localhost:50001 |
| 2 | 真实网络断连后恢复 | 断开网络 → 启动服务 → 验证首屏可用 |
| 3 | 多浏览器并发操作 | 两个浏览器窗口同时操作不同 profile |
| 4 | 大文件上传体验 | 上传 > 1MB 配置文件，观察响应时间 |

---

## 6. 支持边界 (Supported Boundary)

### 6.1 单进程 / 单调度器

| 边界 | 说明 |
|------|------|
| 进程模型 | 单进程，WriteGate 保证写入一致性 |
| 调度器 | 单 BackgroundScheduler 实例 |
| 防重入 | 按 profile+resource 粒度隔离锁 |
| 并发写入 | 同 profile 同资源串行化 |
| 跨 profile | 不同 profile 互不阻塞 |

### 6.2 文件大小

| 类型 | 实测最大值 | 说明 |
|------|-----------|------|
| XML 配置 | ~63KB (700 entries) | 测试验证可解析 |
| JSON 配置 | 无特殊限制 | 受内存约束 |
| 临时文件总量 | 500MB | TempStorage 容量上限 |
| 临时文件 TTL | 1 小时 | 超期自动清理 |

### 6.3 不声明"无限"

- 不虚构"无限"文件支持，实测代表文件 ~63KB
- 大文件阈值：XML > 200KB 时启用精简属性模式
- 解析缓存 LRU 容量：32 个 ParsedDocument

---

## 7. 恢复流程 (Recovery Procedure)

### 7.1 迁移中断恢复

```
1. 扫描 ops_dir 中 state=RECOVERY_REQUIRED 的 migration manifest
2. 调用 retry_migration(manifest, ops_dir)
3. 从 completed_steps 判断断点，继续未完成的步骤
4. 验证 hash 一致后标记 COMMITTED
```

### 7.2 改名中断恢复

```
1. 扫描 ops_dir 中 state=RECOVERY_REQUIRED 的 rename manifest
2. 根据 completed_steps 判断已完成的步骤
3. 重新执行未完成步骤（rename_config/rename_archive/.../update_mapping/...）
4. 标记 COMMITTED 或 needs_manual_review
```

### 7.3 备份恢复

```
1. 从 backup_dir 读取数据
2. 调用 restore_backup(backup_dir, target_dir, ops_dir)
3. 验证恢复后 hash 与备份一致
4. 标记 COMMITTED
```

### 7.4 全局恢复扫描

```python
from app.core.operations import scan_and_recover
results = scan_and_recover(ops_dir)
# 返回每个未完成操作的处理结果
```

---

## 8. 兼容性矩阵 (Compatibility Matrix)

| 维度 | 支持 | 验证状态 |
|------|------|----------|
| Python 3.12 | ✅ | 已验证 |
| macOS (aarch64) | ✅ | 已验证 |
| 离线安装 | ✅ | 已验证（无公网依赖） |
| XML 配置 | ✅ | 已验证（含属性/namespace/重复元素） |
| JSON 配置 | ✅ | 已验证（含 Unicode/嵌套/特殊字符） |
| 多 profile | ✅ | 已验证（数据完全隔离） |
| 双客户端 | ✅ | 已验证（session 隔离） |
| WriteGate 维护模式 | ✅ | 已验证（备份时阻止写入） |
| 原子写入 | ✅ | 已验证（mkstemp + fsync + replace） |
| 数据损坏检测 | ✅ | 已验证（CorruptDataError） |
| 路径穿越防护 | ✅ | 已验证（resolve_within + validate_filename） |
| XSS 防护 | ✅ | 已验证（搜索/展示层转义） |
| 审阅幂等提交 | ✅ | 已验证（applied_hashes 去重） |
| 中断恢复 | ✅ | 已验证（manifest 跟踪 + retry） |
| 任务状态持久化 | ✅ | 已验证（disconnect/reconnect 可解释） |

---

## 9. 验收结论

| 验收项 | 状态 |
|--------|------|
| 所有 P0 已通过各自回归 | ✅ PASS |
| 完整业务闭环通过 | ✅ PASS |
| offline first install/visit 通过 | ✅ PASS |
| no public runtime dependency | ✅ PASS |
| backup 可恢复 | ✅ PASS |
| interrupted upgrade 可恢复 | ✅ PASS |
| double client / two profile 不串 | ✅ PASS |
| no unresolved P0 | ✅ PASS |
| 明确 single process/single scheduler 支持边界 | ✅ PASS |
| 记录实测最大代表文件，不虚构"无限" | ✅ PASS |

**结论**: 所有 release gate 验收通过，可进入 T20 视觉样式收敛阶段。
