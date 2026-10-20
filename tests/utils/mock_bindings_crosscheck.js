/*
 * mock↔后端「变量绑定 CRUD」契约 cross-check 的 JS 侧 oracle（M3-14 / INH-1899）。
 *
 * 把**真实的** app/web/assets/js/mock/mock-api.js 载入并执行（不是读源码文本），
 * 从 stdin 收一串用例，逐条调 `MC.mock.handle`，把 `{name, status, body}` 原样打到
 * stdout。断言在 tests/test_mock_bindings_contract.py：同一串用例也打真 HTTP 后端，
 * 两侧的状态码、错误码、detail.field 与文案必须一致（R19；R45 反对源码 grep）。
 *
 * 用例串行执行：PUT/DELETE 会改 mock 的内存配置，改名冲突/二次删除依赖前序状态。
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
  sandbox.MC.mock.setIdentity('deployer-admin');
  return sandbox.MC.mock;
}

function seg(value) {
  return encodeURIComponent(String(value));
}

function installFile(mock, name) {
  const P = mock.profileState('default');
  P.files[name] = {
    current: { text: '<R><x>1</x></R>', mtime: '2026-10-20 00:00:00' },
    archives: [],
    records: [],
  };
}

function pathOf(c) {
  if (c.path) return c.path;
  if (c.group === undefined) return '/p/default/bindings';
  return '/p/default/bindings/' + seg(c.group);
}

function handle(mock, c) {
  return mock.handle({
    method: c.method || 'GET',
    path: pathOf(c),
    query: {},
    body: c.body === undefined ? {} : c.body,
    headers: {},
  });
}

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', function (chunk) { input += chunk; });
process.stdin.on('end', function () {
  let cases;
  try {
    cases = JSON.parse(input || '[]');
  } catch (e) {
    process.stdout.write(JSON.stringify({ error: String(e) }));
    return;
  }
  const mock = loadMock();
  installFile(mock, 'a.xml');
  installFile(mock, 'b.xml');
  /* 默认机型在 fixtures 里预置了示例绑定组；两侧必须从同一空表起步才能逐条比对。 */
  mock.profileState('default').bindings = [];
  let chain = Promise.resolve();
  const results = [];
  cases.forEach(function (c) {
    chain = chain.then(function () {
      return handle(mock, c).then(function (res) {
        results.push({ name: c.name, status: res.status, body: res.body });
      });
    });
  });
  chain.then(function () {
    process.stdout.write(JSON.stringify({ count: results.length, results: results }));
  }, function (err) {
    process.stdout.write(JSON.stringify({ error: String(err && err.stack || err) }));
  });
});
