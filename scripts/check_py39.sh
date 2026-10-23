#!/usr/bin/env bash
# H1 守护脚本：源码必须在 Python 3.9.11 下可编译，且不得使用 3.10+ 专属写法。
#
# 用法：
#   PYTHON=python3.9 scripts/check_py39.sh
#
# 失败即返回非 0，用于提交前自检（规范 §0.2、§16.6；H1）。
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PYTHON:-python3.9}"

echo "[1/2] compileall（$("$PY" --version 2>&1)）"
"$PY" -m compileall -q app run.py main.py scripts

echo "[2/2] 禁止 3.10+ 专属写法"
status=0

check() {
  local pattern="$1" reason="$2" hits
  hits="$(grep -rnE --include='*.py' -- "$pattern" app run.py main.py scripts 2>/dev/null || true)"
  if [ -n "$hits" ]; then
    printf '  ✗ %s\n%s\n' "$reason" "$hits" >&2
    status=1
  fi
}

check '(^|[[:space:]])(import tomllib|from tomllib)' 'tomllib 仅 3.11+（H1 只允许 3.9.11）'
check 'slots[[:space:]]*=[[:space:]]*True' 'dataclass(slots=True) 仅 3.10+'
check "zip\([^)]*strict[[:space:]]*=" 'zip(strict=) 仅 3.10+'

if [ "$status" -eq 0 ]; then
  echo "  ✓ 通过（match 语句会被 3.9 的 compileall 直接拒绝）"
fi
exit "$status"
