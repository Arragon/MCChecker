/*
 * mock↔后端「人员姓名/IP 键」契约 cross-check 的 JS 侧 oracle（M3-13 / INH-1898 修复轮）。
 *
 * 它把**真实的** app/web/assets/js/mock/mock-api.js 载入并执行（不是读它的源码文本），
 * 从 stdin 收一组用例 `{kind, value}`，逐条调 `MC.mock.handle`，把
 * `{kind, value, status, code, message, field}` 原样打到 stdout。
 * 比对与判红逻辑在 tests/test_api_people.py::TestMockPeopleContract：那边对同一批输入打
 * 真 HTTP 后端，两侧必须给出同一个「收 / 拒」判决。
 *
 * 为什么必须执行而不是 grep（docs/know-how.md 第 4 节 R45 那条、以及 dlmath cross-check
 * 的同款教训）：`mock-api.js` 把 `validPersonName` 的斜杠那一档删掉、或把 token 名单从
 * `%2f` 改成 `%2E`，源码里那些中文字面串可以一个字没动，而模板模式的判决已经完全不一样。
 * 字面串断言抓不住这个，执行可以。
 *
 * 加载清单 = `app/web/tests/index.html` 里 mock 的那几条依赖（core/util、core/api、
 * mock/docparse、mock/dlmath、mock/fixtures、mock/mock-api）。vm 沙箱只需要 `window` +
 * `setTimeout`/`console`：`util.storageGet` 读不到 localStorage 会走自己的 try/catch 回退，
 * 正是浏览器之外的形态。路径一律由本文件位置向上推到仓库根，不含绝对路径。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const JS_DIR = path.join(REPO_ROOT, 'app', 'web', 'assets', 'js');
const MOCK_FILES = [
  'core/util.js',
  'core/api.js',
  'mock/docparse.js',
  'mock/dlmath.js',
  'mock/fixtures.js',
  'mock/mock-api.js',
];

function loadMock() {
  const sandbox = { console: console, setTimeout: setTimeout, clearTimeout: clearTimeout, Blob: global.Blob };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  sandbox.MC = { config: { mock: true } };
  for (const rel of MOCK_FILES) {
    const file = path.join(JS_DIR, rel);
    vm.runInContext(fs.readFileSync(file, 'utf8'), sandbox, { filename: file });
  }
  if (!sandbox.MC.mock || typeof sandbox.MC.mock.handle !== 'function') {
    throw new Error('mock-api.js did not register MC.mock.handle');
  }
  // 写接口要有部署者身份，否则一律 403，量不到校验判决。
  sandbox.MC.mock.setIdentity('deployer-admin');
  return sandbox.MC.mock;
}

/** 与前端 `api.seg()` 同一个编码口径。 */
function seg(value) {
  return encodeURIComponent(String(value));
}

function request(mock, c) {
  if (c.kind === 'admin') {
    return mock.handle({ method: 'POST', path: '/p/default/admins', query: {}, body: { name: c.value }, headers: {} });
  }
  if (c.kind === 'ip') {
    // 路径给一个干净的 IP，让判决只来自 **body 里那一个值**（与 Python 侧同一摆法）。
    return mock.handle({
      method: 'PUT',
      path: '/p/default/ip-mapping/' + seg('10.0.0.1'),
      query: {},
      body: { ip: c.value, name: '对照' },
      headers: {},
    });
  }
  throw new Error('unknown case kind: ' + c.kind);
}

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', function (chunk) { input += chunk; });
process.stdin.on('end', function () {
  const cases = JSON.parse(input || '[]');
  const mock = loadMock();
  Promise.all(
    cases.map(function (c) {
      return request(mock, c).then(function (res) {
        const err = (res.body && res.body.error) || {};
        return {
          kind: c.kind,
          value: c.value,
          status: res.status,
          code: err.code || null,
          message: err.message || null,
          field: (err.detail && err.detail.field) || null,
        };
      });
    })
  ).then(function (results) {
    process.stdout.write(JSON.stringify({ count: results.length, results: results }));
  }, function (e) {
    process.stdout.write(JSON.stringify({ error: String(e && e.stack ? e.stack : e) }));
  });
});
