/*
 * mock↔后端「外部工具 CRUD」契约 cross-check 的 JS 侧 oracle（M3-15 / INH-1900）。
 *
 * 把真实的 app/web/assets/js/mock/mock-api.js 载入并执行（不是读源码文本），逐条调
 * `MC.mock.handle`，输出 `{name, status, body}`。tests/test_mock_bindings_contract.py
 * 的同款 Python 用例会把同一串请求打真后端，两侧状态码、错误码、detail.field、文案
 * 必须一致（R19；R45 反对源码 grep）。
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
  const sandbox = { console: console, setTimeout: setTimeout, clearTimeout: clearTimeout, Blob: global.Blob, URL: global.URL };
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

function pathOf(c) {
  if (c.path) return c.path;
  if (c.pathName === undefined) return '/p/default/tools';
  return '/p/default/tools/' + seg(c.pathName);
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
  mock.profileState('default').tools = [];
  let chain = Promise.resolve();
  const results = [];
  cases.forEach(function (c) {
    chain = chain.then(function () {
      return mock.handle({
        method: c.method || 'GET',
        path: pathOf(c),
        query: {},
        body: c.body === undefined ? {} : c.body,
        headers: {},
      }).then(function (res) {
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
