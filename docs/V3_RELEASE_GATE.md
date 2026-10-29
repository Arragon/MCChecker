# V3 发布门禁（INH-2161）

日期：2026-10-29。**状态：BLOCKED；不发布、不改现网数据。**

## 代码与自动化门禁

- [x] 全量 pytest 2720 passed / 0 failed（H3 执法）
- [x] 前端 HTTP ALL PASSED (105)
- [x] Python 3.9 `check_py39.sh` exit 0
- [x] M9–M14 共 40 张卡代码/测试收口（M14 6/6）
- [x] 迁移 dry-run + 真实 data 备份/verify/restore dry-run
- [x] 安全/并发/故障注入（跨 W、CAS、幂等、200 并发、零半批）
- [x] 性能脚本三预算 pass（50k 首批分页/3200 预览/3200 提交）

## 未完成/阻断

- [x] 真实浏览器逐屏截图/无障碍/旧页面像素回归（INH-2159 Done）
- [ ] 真实模板在目标系统读取、设备边界、Windows/Linux、4C8G（INH-2160 BLOCKED；离线首访/INI/JSON/XML/性能已验）
- [x] 断网首访（176 成员离线包 + 仅 localhost 解析）
- [ ] Windows 10/11、Linux、4C8G 独立压测
- [x] Q1–Q6 + A01–A17 追踪矩阵 29 PASS（docs/V3_CONTRACT_TRACE.md）
- [ ] INH-1910（M4-08）未关闭；INH-1978（M8-07）未完成验收
- [ ] INH-1932 搜索性能风险未解决
- [ ] 权威备份未转存正式位置（当前 /tmp）
- [x] `make_release.py` 已纳入 inspect/backup/migrate V3 脚本；本机构建 174 成员、外部主机 0、verify-only 通过（离线 Windows/Linux 实机仍未验证）
- [ ] 生产页面真实无 mock、凭据/样本零进包最终审计

## 结论

**2161 保持未完成**。任何“V3 已发布/已上线”的说法都不成立；待外部证据与 1910/1978 门禁关闭后重新跑本清单。
