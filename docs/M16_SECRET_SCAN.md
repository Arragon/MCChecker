# M16-38 Secret 扫描（git 侧门禁）

> 目标：把「发布包 / 日志 / 备份 / 任务结果没有 secret」补齐到「**Git 工作区与全历史也没有**」。
> 与 `tests/test_sync_security_gate.py` 的运行时脱敏断言互补：那边看产物，这边看源码与提交。

## 1. 运行方式

```bash
# 提交前（只看暂存的新增行，最快）
./.venv39/bin/python scripts/scan_secrets.py --staged

# 当前工作区受版本控制的文件
./.venv39/bin/python scripts/scan_secrets.py --tree

# 单卡 / 单 PR 的提交区间
./.venv39/bin/python scripts/scan_secrets.py --range origin/feat/mc-v3.1..HEAD

# 全部分支的全部历史 blob（发布门用）
./.venv39/bin/python scripts/scan_secrets.py --all-history

# 机器可读
./.venv39/bin/python scripts/scan_secrets.py --json
```

退出码：`0` 干净；`1` 有命中（或开 `--strict-allowlist` 时 allowlist 失效）；`2` 环境/参数错误（不是 git 仓库、allowlist 损坏、缺 `reason`）。

## 2. 规则

| 规则 | 说明 |
|---|---|
| `private-key` | `-----BEGIN … PRIVATE KEY-----` |
| `aws-access-key` | `AKIA` + 16 位大写 |
| `github-token` / `github-pat` | `ghp_/gho_/ghu_/ghs_/ghr_`、`github_pat_` |
| `slack-token` | `xox[baprs]-` |
| `llm-api-key` | `sk-` + 20 位以上 |
| `jwt` | `eyJ….….…` 三段 |
| `generic-assignment` | `password/passwd/pwd/secret/token/api_key/authorization` 后跟 `=`/`:` 与引号字面量 |
| `high-entropy` | ≥32 位、含大小写与数字、不含 `_ - /`、非纯十六进制、香农熵 ≥3.6 |

`high-entropy` 刻意收紧：snake_case 标识符、路径、常量名、sha256 内容哈希都不会命中，
否则整个 `tests/` 会被误报淹没（本轮首跑曾一次报 433 条，全部是测试函数名）。

占位值（`FAKE`/`dummy`/`example`/`changeme`/`<...>`/`***` 等）直接跳过。

## 3. 输出与脱敏

- 文本：`path:line[@blob] rule=<id> evidence=<前4 + 掩码 + 后4>`；**绝不打印完整值**。
- `--json`：`{mode, hits, allowed, stale_allowlist}`，供发布门汇总。
- 历史命中按 `(path, rule, evidence)` 去重：同一文件同一形态只报一次。

## 4. allowlist

文件：`scripts/secret_scan_allowlist.json`，结构 `{"entries":[{path, rule, line?, reason, commit?}]}`。

- `reason` 必填；缺失直接退出码 2（防止「为了过扫描而放过」）。
- `line` 固定到具体行：代码行号漂移后该命中会重新出现（fail-closed），此时更新 allowlist，而不是放宽规则。
- 历史命中的行号随版本漂移，因此 `commit` 存在时只按 `path + rule` 放行。
- `--strict-allowlist`：允许条目未命中即失败，用于清理已修好的豁免。

当前 7 条均为合成样例（测试口令、base64 XSS 载荷、字符集常量、已删除的历史性能用例），逐条附理由。

## 5. 基线（本轮实测）

```text
$ ./.venv39/bin/python scripts/scan_secrets.py --tree
扫描完成：mode=tree 命中 0 条，放行 6 条

$ ./.venv39/bin/python scripts/scan_secrets.py --all-history
扫描完成：mode=all-history 命中 0 条，放行 7 条

$ ./.venv39/bin/python scripts/scan_secrets.py --staged
扫描完成：mode=staged 命中 0 条，放行 0 条
```

## 6. 接入位置

- M16-42 发布门：`--all-history` 必须为 0 命中，否则不得出包。
- 单卡 PR：建议跑 `--range origin/feat/mc-v3.1..HEAD`。
- 不加入核心发布包（`scripts/make_release.py` 的 `RELEASE_ENTRIES` 未包含本脚本）：它是开发/门禁工具，
  不随运行时分发；`check_py39.sh` 仍会编译它。
- CI 触发分支仍只有 `feat/mc-v3`（历史遗留），是否扩到 `feat/mc-v3.1` 属 M16-42 范围，本轮不改。

## 7. 已知限制

- 只按文本形态匹配，不做语义判断；base64 编码的凭据需要人工复核。
- 二进制与超过 2MB 的文件跳过（与 `make_release.py` 的排除口径一致）。
- 历史扫描读 blob 而非逐 commit diff，因此报的是「该文件某个版本里存在过」，不直接给 commit 号（附 blob 短 id）。
