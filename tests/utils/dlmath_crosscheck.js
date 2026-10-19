/*
 * DL 等价性 cross-check 的 JS 侧 oracle（M3-10 / INH-1895 修复轮）。
 *
 * 它把**真实的** app/web/assets/js/mock/dlmath.js 载入并执行（不是读它的源码文本），
 * 从 stdin 收一组 JSON 用例，逐条调用导出的函数，把返回值原样写到 stdout。
 * 比较与判红逻辑在 tests/test_dlmath_crosscheck.py：那边用 app/core/dltool.py
 * 对同一批输入算一遍，两边必须逐值相等。
 *
 * 之所以要跑真身而不是 grep：grep 只能证明「字面串还在」，证明不了语义。
 * dlmath.js 里把 1e-9 改成 1e-8、把 tol 判据换个写法、把默认 samples 从 4000 改成
 * 400，源码文本可以一个字没变而结果完全不同（know-how / R45 记的就是这类假绿）。
 *
 * 路径一律由本文件位置向上推到仓库根（tests/utils → 仓库根），不含任何绝对路径，
 * 因此换机器、换 worktree、换 clone 目录都能跑。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const DLMATH_JS = path.join(REPO_ROOT, 'app', 'web', 'assets', 'js', 'mock', 'dlmath.js');

function loadDlmath() {
  // dlmath.js 是浏览器 IIFE：(function (global) { ... })(window)。
  // 给它一个只带 window 的 vm 沙箱就够了，它不碰 DOM。
  const src = fs.readFileSync(DLMATH_JS, 'utf8');
  const sandbox = { window: {} };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: DLMATH_JS });
  const dl = sandbox.window.MC && sandbox.window.MC.dlmath;
  if (!dl) {
    throw new Error('dlmath.js did not register window.MC.dlmath');
  }
  return dl;
}

function invoke(dl, c) {
  if (c.fn === 'poly') return dl.poly(c.x, c.coeffs);
  if (c.fn === 'findRoots') return dl.findRoots(c.target, c.coeffs, c.lo, c.hi, c.samples);
  if (c.fn === 'forward') return dl.forward(c.item, c.kind, c.inputs);
  if (c.fn === 'reverse') return dl.reverse(c.item, c.kind, c.inputs);
  if (c.fn === 'multiCheck') return dl.multiCheck(c.item, c.kind);
  if (c.fn === 'validate') return dl.validate(c.item, c.kind);
  throw new Error('unknown case fn: ' + c.fn);
}

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', function (chunk) { input += chunk; });
process.stdin.on('end', function () {
  const cases = JSON.parse(input || '[]');
  const dl = loadDlmath();
  const results = cases.map(function (c) { return invoke(dl, c); });
  process.stdout.write(JSON.stringify({ pairs: results.length, results: results }));
});
