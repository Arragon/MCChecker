/*
 * mock↔后端「DL 请求校验 / 提取取值」契约 cross-check 的 JS 侧 oracle（M3-11 /
 * INH-1896 修复轮）。
 *
 * 它把**真实的** app/web/assets/js/mock/mock-api.js 载入并执行（不是读源码文本），
 * 从 stdin 收一组用例，逐条调 `MC.mock.handle`，把 `{name, status, body}` 原样打到
 * stdout。断言在 tests/test_mock_dl_contract.py：那边对每一类判决同时打真 HTTP 后端
 * （tests/test_api_dl.py 的先例），两侧必须给出同一个「收 / 拒」判决。
 *
 * 为什么必须执行而不是 grep（know-how 第 4 节 R45、以及 dlmath / people 两个 cross-check
 * 的同款教训）：把 `dlNumber` 换回 `Number()`、把 PUT 的 rev 比较挪回 body 校验之前，
 * 源码里的中文文案可以一个字没动，模板模式的判决已经完全不一样。
 *
 * 加载清单 = `app/web/tests/index.html` 里 mock 的那几条依赖（core/util、core/api、
 * mock/docparse、mock/dlmath、mock/fixtures、mock-api）。vm 沙箱只需要 `window` +
 * `setTimeout`/`console`/`Blob`：`util.storageGet` 读不到 localStorage 会走自己的
 * try/catch 回退，正是浏览器之外的形态。这里只打 JSON 源（不碰 DOMParser）。
 * 路径一律由本文件位置向上推到仓库根，不含绝对路径。
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

/** 把一个 JSON 文件装进机型内存（`profileState` 是 mock 自带的用例专用入口）。 */
function installFile(mock, spec) {
  const P = mock.profileState(spec.pid || 'default');
  P.files[spec.name] = {
    current: { text: spec.text, mtime: '2026-10-20 00:00:00' },
    archives: [],
    records: [],
  };
}

const METHOD_BY_KIND = { put: 'PUT', extract: 'POST', compute: 'POST', get: 'GET' };

function handle(mock, c, body, headers) {
  const pid = c.pid || 'default';
  return mock.handle({
    method: c.method || METHOD_BY_KIND[c.kind],
    path: c.path ? c.path : '/p/' + seg(pid) + c.route,
    query: {},
    body: body,
    headers: headers || {},
  });
}

/** 先 GET /dl 拿当前 rev，再带 If-Match 发同一条 PUT（模拟前端 dltool.js:84）。 */
function withCurrentRev(mock, c) {
  const pid = c.pid || 'default';
  return handle(mock, { method: 'GET', route: '/dl', pid: pid }, {}, {}).then(function (res) {
    const headers = Object.assign({}, c.headers || {});
    headers['If-Match'] = res.body.rev;
    return handle(mock, c, c.body, headers);
  });
}

function runCase(mock, c) {
  if (c.file) installFile(mock, c.file);
  if (c.kind === 'put') {
    if (c.withRev) return withCurrentRev(mock, c);
    return handle(mock, c, c.body, c.headers || {});
  }
  if (c.kind === 'extract' || c.kind === 'compute') {
    return handle(mock, c, c.body || {}, {});
  }
  if (c.kind === 'get') return handle(mock, { method: 'GET', route: '/dl', pid: c.pid }, {}, {});
  throw new Error('unknown case kind: ' + c.kind);
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
  // 串行执行：PUT 会改 mock 的内存配置，并发下「先 GET rev 再 PUT」会互相踩。
  let chain = Promise.resolve();
  const results = [];
  cases.forEach(function (c) {
    chain = chain.then(function () {
      return runCase(mock, c).then(function (res) {
        results.push({ name: c.name, kind: c.kind, status: res.status, body: res.body });
      });
    });
  });
  chain.then(
    function () {
      process.stdout.write(JSON.stringify({ count: results.length, results: results }));
    },
    function (e) {
      process.stdout.write(JSON.stringify({ error: String(e && e.stack ? e.stack : e) }));
    }
  );
});
