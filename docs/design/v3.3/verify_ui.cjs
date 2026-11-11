'use strict';
/* Bounded integration checks for the reconstructed interaction prototype.
 * Runs the actual bundled scripts in Node VM with a small, explicit DOM stub.
 * This does NOT run a browser, CSS/layout engine, focus-trap implementation,
 * native file picker, accessibility audit, or network. It is not visual QA.
 * Default: execute the built HTML, requiring embedded/source identity.
 * During reconstruction only: node verify_ui.cjs --source
 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const directory = __dirname;
const mappings = [['prototype-model', 'model.js'], ['prototype-sources', 'sources.js'], ['prototype-ui', 'app.js']];
const htmlPath = path.join(directory, 'MCChecker_Config_Prototype.html');
const useHtml = !process.argv.includes('--source') && fs.existsSync(htmlPath);
const html = useHtml ? fs.readFileSync(htmlPath, 'utf8') : '';
const scripts = mappings.map(([id, filename]) => {
  const source = fs.readFileSync(path.join(directory, 'prototype', filename), 'utf8');
  if (!useHtml) return [filename, source];
  const match = html.match(new RegExp('<script\\s+id="' + id + '"[^>]*>([\\s\\S]*?)<\\/script>', 'i'));
  assert(match, 'Missing bundled script: ' + id);
  assert.equal(match[1], source, 'Stale HTML bundle for ' + filename + '; run build_prototype.py and retry.');
  return [id, match[1]];
});
const clone = value => JSON.parse(JSON.stringify(value));
const decode = value => String(value).replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (_, key) => {
  if (key[0] === '#') return String.fromCodePoint(key[1].toLowerCase() === 'x' ? parseInt(key.slice(2), 16) : Number(key.slice(1)));
  return { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" }[key.toLowerCase()];
});
const camel = key => key.replace(/-([a-z])/g, (_, c) => c.toUpperCase());

function createHarness() {
  let document;
  const timers = new Set(), documentEvents = new Map(), windowEvents = new Map();
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag.toUpperCase(); this.attributes = {}; this.dataset = {};
      this.children = []; this.parentNode = null; this.events = new Map();
      this._html = ''; this._text = ''; this.value = ''; this.style = {};
      this.checked = false; this.disabled = false; this.hidden = false; this.open = false;
      this.selectionStart = 0;
    }
    get id() { return this.attributes.id || ''; }
    set id(value) { this.attributes.id = String(value); }
    get className() { return this.attributes.class || ''; }
    set className(value) { this.attributes.class = String(value); }
    get isConnected() { let node = this; while (node) { if (node === document?.body) return true; node = node.parentNode; } return false; }
    get innerHTML() { return this._html; }
    set innerHTML(value) {
      this.children.forEach(child => { child.parentNode = null; }); this.children = []; this._text = ''; this._html = String(value);
      const stack = [this], tokens = String(value).matchAll(/<\/?[A-Za-z][^>]*>|[^<]+/g);
      const voids = new Set(['INPUT', 'BR', 'HR', 'IMG', 'META', 'LINK', 'AREA', 'BASE', 'COL', 'EMBED', 'PARAM', 'SOURCE', 'TRACK', 'WBR']);
      for (const token of tokens) {
        const raw = token[0];
        if (!raw.startsWith('<')) { const node = new Element('#text'); node._text = decode(raw); stack[stack.length - 1].append(node); continue; }
        const closing = /^<\//.test(raw), name = /^<\/?([A-Za-z][\w:-]*)/.exec(raw)?.[1];
        if (!name) continue;
        if (closing) { for (let i = stack.length - 1; i > 0; i--) if (stack[i].tagName === name.toUpperCase()) { stack.length = i; break; } continue; }
        const node = new Element(name), attrs = raw.slice(raw.indexOf(name) + name.length).replace(/\/?\s*>$/, '');
        for (const a of attrs.matchAll(/([^\s=/'">]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g)) node.setAttribute(a[1], decode(a[2] ?? a[3] ?? a[4] ?? ''));
        stack[stack.length - 1].append(node);
        if (!voids.has(node.tagName) && !/\/\s*>$/.test(raw)) stack.push(node);
      }
      for (const node of this.descendants()) {
        if (node.tagName === 'OPTION' && !node.hasAttribute('value')) node.value = node.textContent;
        if (node.tagName === 'SELECT') { const options = node.querySelectorAll('option'); node.value = (options.find(o => o.hasAttribute('selected')) || options[0])?.value || ''; }
        if (node.tagName === 'TEXTAREA') node.value = node.textContent;
      }
    }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    set textContent(value) { this._text = String(value); this.children.forEach(child => { child.parentNode = null; }); this.children = []; }
    setAttribute(name, value) {
      this.attributes[name] = String(value);
      if (name.startsWith('data-')) this.dataset[camel(name.slice(5))] = String(value);
      if (name === 'value') this.value = String(value);
      if (['checked', 'disabled', 'hidden', 'open'].includes(name)) this[name] = true;
    }
    removeAttribute(name) { delete this.attributes[name]; if (['checked', 'disabled', 'hidden', 'open'].includes(name)) this[name] = false; }
    getAttribute(name) { return this.hasAttribute(name) ? this.attributes[name] : null; }
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name); }
    append(...nodes) { for (const node of nodes) { node.parentNode = this; this.children.push(node); } }
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(n => n !== this); this.parentNode = null; }
    descendants() { const out = []; for (const child of this.children) { out.push(child); out.push(...child.descendants()); } return out; }
    matches(selector) {
      if (this.tagName === '#TEXT') return false;
      let text = selector.trim();
      if (text.endsWith(':checked')) { if (!this.checked) return false; text = text.slice(0, -8); }
      const tag = /^[A-Za-z][\w-]*/.exec(text)?.[0]; if (tag && this.tagName !== tag.toUpperCase()) return false;
      const id = /#([\w-]+)/.exec(text.replace(/\[[^\]]*\]/g, ''))?.[1]; if (id && this.id !== id) return false;
      for (const match of text.replace(/\[[^\]]*\]/g, '').matchAll(/\.([\w-]+)/g)) if (!this.className.split(/\s+/).includes(match[1])) return false;
      for (const match of text.matchAll(/\[([^\s~|^$*!=\]]+)(?:(\^?=)["']?([^\]"']*)["']?)?\]/g)) {
        const key = match[1], actual = key === 'open' ? (this.open ? '' : null) : this.getAttribute(key);
        if (actual === null) return false;
        if (match[2] === '=' && actual !== match[3]) return false;
        if (match[2] === '^=' && !actual.startsWith(match[3])) return false;
      }
      return true;
    }
    querySelectorAll(selector) { return this.descendants().filter(node => node.matches(selector)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { let node = this; while (node) { if (node.matches(selector)) return node; node = node.parentNode; } return null; }
    addEventListener(name, fn) { const list = this.events.get(name) || []; list.push(fn); this.events.set(name, list); }
    focus() { document.activeElement = this; }
    setSelectionRange(start) { this.selectionStart = start; }
    scrollIntoView() {} click() {}
    showModal() { this.open = true; }
    close() { this.open = false; for (const fn of this.events.get('close') || []) fn(); }
  }
  const body = new Element('body');
  document = {
    body, activeElement: body,
    getElementById: id => body.descendants().find(node => node.id === id) || null,
    createElement: tag => new Element(tag),
    querySelector: selector => body.querySelector(selector), querySelectorAll: selector => body.querySelectorAll(selector),
    addEventListener(name, fn) { const list = documentEvents.get(name) || []; list.push(fn); documentEvents.set(name, list); }
  };
  for (const [tag, id] of [['div', 'app'], ['dialog', 'dialog'], ['dialog', 'drawer'], ['div', 'toast']]) { const el = new Element(tag); el.id = id; body.append(el); }
  const location = { hash: '#/files/f-machine/parameters' };
  const context = { document, location, console, URL, URLSearchParams, Blob, AbortController, TextEncoder, TextDecoder,
    history: { pushState(_s, _title, url) { location.hash = String(url); }, replaceState(_s, _title, url) { location.hash = String(url); } },
    setTimeout(fn, ms, ...args) {
      const timer = setTimeout(() => { timers.delete(timer); fn(...args); }, ms <= 500 ? 1 : ms);
      if (ms > 500) timer.unref(); timers.add(timer); return timer;
    },
    clearTimeout(timer) { clearTimeout(timer); timers.delete(timer); },
    addEventListener(name, fn) { const list = windowEvents.get(name) || []; list.push(fn); windowEvents.set(name, list); },
    fetch() { throw new Error('Network is disabled in Node VM verification'); }
  };
  context.window = context;
  vm.createContext(context);
  for (const [name, script] of scripts) vm.runInContext(script, context, { filename: name, timeout: 5000 });
  const app = context.MCPrototypeApp;
  assert(app?.model && typeof app.handleAction === 'function', 'Application API is not complete');
  const query = (selector, scope) => {
    const root = scope ? document.getElementById(scope) : document;
    const node = root.querySelector(selector); assert(node, 'Missing UI control: ' + selector); return node;
  };
  const clean = () => assert.equal(app.state.renderError, null, 'UI render failed: ' + app.state.renderError);
  const action = async (name, data = {}) => { const node = new Element('button'); Object.assign(node.dataset, { action: name }, data); await app.handleAction(name, node); clean(); };
  const input = (selector, value) => { const node = selector.startsWith('#') ? query(selector) : query(selector); node.value = String(value); app.handleInput(node); clean(); return node; };
  const changeControl = async (selector, value) => { const node = query(selector); if (typeof value === 'boolean') node.checked = value; else node.value = String(value); await app.handleChange(node); clean(); return node; };
  const route = url => { app.go(url); clean(); return document.getElementById('app'); };
  clean();
  return { app, M: app.model, S: app.state, sources: app.sources, document, context, location, query, action, input, change: changeControl, route, clean,
    all: (selector, scope) => (scope ? document.getElementById(scope) : document).querySelectorAll(selector),
    dispose() { for (const timer of timers) clearTimeout(timer); }
  };
}

const checks = [];
const check = (name, run) => checks.push([name, run]);
const file = h => h.M.getFile('f-machine');
const branch = (h, id = 'base-691') => file(h).branches.find(b => b.id === id);
const record = (f, id) => f.revisions.find(r => r.id === id);
const current = (h, id = 'base-691') => record(file(h), branch(h, id).headId);
const snapshot = h => JSON.stringify({ head: branch(h).headId, revisions: file(h).revisions, events: h.M.state.events });

check('file/machine/parameter/review/source/audit routes render for both roles', async h => {
  const routes = ['#/files', '#/files/f-machine/parameters', '#/files/f-machine/versions', '#/files/f-machine/compare', '#/files/f-machine/merge?target=machine-01', '#/files/f-machine/export', '#/files/f-machine/reviews', '#/files/f-machine/updates', '#/files/f-machine/records', '#/machines', '#/machines/asset-01', '#/parameters', '#/reviews', '#/review/CR-024', '#/review/CR-025', '#/sources', '#/sources/new', '#/records', '#/initialize'];
  for (const actor of ['lin', 'guest']) { h.M.state.actorId = actor; for (const route of routes) { h.route(route); assert(h.query('#page-title').textContent.length > 0); } }
});

check('parameter/file pages respect 30, 50, 100 limits with reachable subsequent pages', async h => {
  await h.action('demo-large');
  assert.equal(current(h).params.length, 3248);
  for (const limit of [30, 50, 100]) { await h.change('#page-limit', limit); assert.equal(h.all('[data-select-param]', 'app').length, limit); await h.action('next'); assert.equal(h.all('[data-select-param]', 'app').length, limit); assert.equal(h.S.page, 1); }
  await h.action('demo-files'); assert.equal(h.M.state.workspaces[0].files.length, 100);
  for (const limit of [30, 50, 100]) { await h.change('#page-limit', limit); assert.equal(h.all('[data-select-file]', 'app').length, limit); }
});

check('large check/save dialog and optional review paginate differences at 50 rows', async h => {
  await h.action('demo-large'); await h.action('edit');
  for (const p of h.S.edit.params) {
    if (p.type === 'number') p.value = Math.min(Number(p.max ?? Infinity), Number(p.value) + 1);
    else if (p.type === 'boolean') p.value = !p.value;
    else p.value += '（核对）';
  }
  await h.action('review-save');
  assert.equal(h.all('tbody', 'dialog')[0].children.length, 50);
  const next = h.query('[data-action="diff-next"]', 'dialog'); await h.action('diff-next', { view: next.dataset.view });
  assert.equal(h.all('tbody', 'dialog')[0].children.length, 50);
  assert.match(h.query('#dialog').textContent, /51[–—-]100/);
  await h.action('submit-review');
  assert.equal(h.S.route[0], 'review');
  assert.equal(h.all('tbody', 'app')[0].children.length, 50);
  assert(h.query('[data-action="diff-next"]', 'app'));
});

check('new draft parameters can display blame without querying absent saved identities', async h => {
  await h.action('edit');
  h.S.edit.params.push({ id: 'qa-added', name: '新增参数', path: '/General/NewValue', type: 'string', unit: '', min: null, max: null, value: '', mode: 'inherit' });
  await h.action('toggle-blame');
  assert.equal(h.S.renderError, null);
  assert.match(h.query('#app').textContent, /尚未保存/);
  assert.equal(h.all('[data-select-param]', 'app').length, 19);
});

check('failed direct save preserves inputs and retries through the actual save handler', async h => {
  await h.action('edit'); h.input('[data-edit-value="p-temp"]', 44); await h.action('review-save');
  const before = snapshot(h); h.S.failNext = true;
  await assert.rejects(h.action('commit-edit'), /模拟保存失败/);
  assert.equal(snapshot(h), before); assert.equal(h.S.edit.params.find(p => p.id === 'p-temp').value, 44);
  await h.action('commit-edit'); assert.equal(h.S.edit, null); assert.equal(current(h).params.find(p => p.id === 'p-temp').value, 44);
});

check('concurrent same-field choice preserves independent server name and unit changes', async h => {
  await h.action('edit'); h.input('[data-edit-value="p-speed"]', 125);
  const params = clone(current(h).params), p = params.find(p => p.id === 'p-speed');
  Object.assign(p, { name: '送料速度（校准）', unit: 'mm·s⁻¹', value: 130 });
  const server = h.M.save({ fileId: 'f-machine', branchId: 'base-691', expectedHeadId: branch(h).headId, params, message: '并发属性及值更新' });
  await h.action('commit-edit'); assert.equal(branch(h).headId, server.id); assert.equal(h.S.stale.conflicts.length, 1); assert.equal(h.S.stale.conflicts[0].field, 'value');
  h.query('[data-stale-choice="0"]').value = 'local'; await h.action('apply-stale'); await h.action('commit-edit');
  const saved = current(h), result = saved.params.find(p => p.id === 'p-speed');
  assert.equal(result.value, 125); assert.equal(result.name, '送料速度（校准）'); assert.equal(result.unit, 'mm·s⁻¹'); assert.equal(saved.parentIds[0], server.id);
});

check('seed review resolves discussion, independently approves and merges without advancing machines', async h => {
  h.route('#/review/CR-024'); const oldMachine = branch(h, 'machine-01').headId;
  await assert.rejects(h.action('approve-review', { review: 'CR-024' }), e => e.code === 'UNRESOLVED');
  await h.action('resolve-comment', { review: 'CR-024', comment: 'comment-024-1' });
  await h.action('approve-review', { review: 'CR-024' }); await h.action('merge-review', { review: 'CR-024' });
  assert.equal(h.M.state.reviews.find(r => r.id === 'CR-024').status, 'merged');
  assert.equal(current(h).params.find(p => p.id === 'p-temp').value, 45); assert.equal(branch(h, 'machine-01').headId, oldMachine);
});

check('workspace switching clears transient foreign state and keeps workspace drafts isolated', async h => {
  h.route('#/files/f-machine/merge?target=machine-01&source=r-b12'); const oldPlan = h.S.mergePlan;
  h.S.external = { name: 'production.xml', params: clone(current(h).params) }; h.S.init = { name: 'production-input.xml' }; h.S.candidate = { workspaceId: 'ws-prod', fileId: 'f-machine' }; h.S.bundle = { workspaceId: 'ws-prod' };
  h.S.drafts['ws-prod/f-machine/base-691'] = { fileId: 'f-machine', branchId: 'base-691', headId: 'r-b12', params: clone(current(h).params), message: '生产草稿' };
  h.route('#/sources/new'); await h.action('source-sample', { kind: 'html' });
  const inFlightDetection = h.action('detect-source');
  assert.equal(h.S.sourceBusy, true);
  await h.change('#workspace', 'ws-lab');
  await inFlightDetection;
  for (const key of ['mergePlan', 'external', 'init', 'candidate', 'bundle', 'compareA', 'compareB']) assert.equal(h.S[key], null, key);
  assert.equal(h.M.state.workspaceId, 'ws-lab'); assert.equal(h.sources.list('ws-lab').length, 0);
  assert.equal(h.S.sourcePreview, null); assert.equal(h.S.sourceDraft, null); assert.equal(h.S.sourceBusy, false);
  h.route('#/files/f-machine/merge?target=machine-01&source=r-b12'); assert.notEqual(h.S.mergePlan, oldPlan); assert.equal(h.S.mergePlan.workspaceId, 'ws-lab');
  h.route('#/files/f-machine/parameters?branch=base-691'); await h.action('edit');
  assert.equal(h.S.edit.params.find(p => p.id === 'p-speed').value, 80); assert.notEqual(h.S.edit.message, '生产草稿');
});

check('source fingerprint invalidation and actual candidate adoption run through UI handlers', async h => {
  const originalSources = JSON.stringify(h.sources.list('ws-prod'));
  h.route('#/sources/new'); await h.action('source-sample', { kind: 'html' });
  h.input('#source-name', '集成核对来源'); await h.action('detect-source');
  const proof = h.S.sourcePreview; assert(proof?.testId);
  h.input('#source-path', 'a.download[href]'); assert.equal(h.S.sourcePreview, null); assert.equal(h.query('#save-source').disabled, true);
  await assert.rejects(h.action('save-source'), e => e.code === 'STALE_TEST'); assert.equal(JSON.stringify(h.sources.list('ws-prod')), originalSources);
  await h.action('detect-source'); assert.notEqual(h.S.sourcePreview.testId, proof.testId); await h.action('save-source');
  const source = h.sources.list('ws-prod').find(s => s.name === '集成核对来源'); assert(source); assert.equal(source.rules.path, 'a.download[href]');
  await h.action('run-source', { source: source.id });
  const candidate = h.sources.candidates('ws-prod', source.id).find(c => c.fileId === 'f-machine'); assert(candidate);
  const oldHead = branch(h).headId;
  await h.action('candidate', { source: source.id, candidate: candidate.id }); await h.action('apply-candidate');
  assert.equal(branch(h).headId, oldHead); assert.equal(h.S.edit.sourceId, source.id); assert.equal(h.S.edit.headId, candidate.headId);
  await h.action('review-save'); await h.action('commit-edit');
  assert.notEqual(branch(h).headId, oldHead); assert.equal(current(h).params.find(p => p.id === 'p-temp').value, 45);
  assert(!h.sources.candidates('ws-prod', source.id).some(c => c.id === candidate.id));
  assert(h.M.state.events.some(ev => ev.action === 'source_candidate_applied' && ev.details.candidateId === candidate.id));
});

check('explicit head snapshot exits read-only and historical machine shows its own basis', async h => {
  h.route('#/files/f-machine/parameters?branch=base-691&revision=r-b12');
  assert.equal(h.S.revisionId, 'r-b12'); assert.equal(h.query('[data-action="edit"]', 'app').disabled, true);
  await h.action('return-current'); assert.equal(h.S.revisionId, null); assert.equal(h.query('[data-action="edit"]', 'app').disabled, false);
  const b = branch(h, 'machine-01');
  h.M.save({ fileId: 'f-machine', branchId: b.id, expectedHeadId: b.headId, params: record(file(h), 'r-b12').params, sourceRevisionId: 'r-b12' });
  h.route('#/files/f-machine/parameters?branch=machine-01&revision=r-m13');
  assert(h.query('[data-revision="r-b11"]', 'app'));
  assert.equal(h.query('#revision').value, 'r-m13');
});

check('merge field selection saves the new basis while retaining independent name and pinned value', async h => {
  const b = branch(h, 'machine-01'), params = clone(current(h, b.id).params);
  params.find(p => p.id === 'p-speed').name = '送料速度（机台显示）';
  const before = h.M.save({ fileId: 'f-machine', branchId: b.id, expectedHeadId: b.headId, params, message: '机台独立显示名称' });
  const reviews = h.M.state.reviews.length;
  h.route('#/files/f-machine/merge?target=machine-01&source=r-b12');
  assert.equal(h.S.mergePlan.targetRevisionId, before.id);
  await h.change('[data-merge-id="p-speed"][data-merge-field="value"]', 'source');
  await h.action('merge-save', { target: b.id });
  assert.equal(b.headId, before.id); assert.equal(h.S.edit.sourceRevisionId, 'r-b12');
  assert.equal(h.S.edit.params.find(p => p.id === 'p-speed').name, '送料速度（机台显示）');
  await h.action('commit-edit');
  const saved = record(file(h), b.headId), speed = saved.params.find(p => p.id === 'p-speed'), pinned = saved.params.find(p => p.id === 'p-offset');
  assert.equal(saved.baseRevisionId, 'r-b12'); assert.equal(saved.sourceRevisionId, 'r-b12');
  assert.equal(speed.value, 120); assert.equal(speed.name, '送料速度（机台显示）');
  assert.equal(pinned.value, 0.15); assert.equal(pinned.mode, 'pin');
  assert.equal(saved.params.find(p => p.id === 'p-temp').value, 42);
  assert(saved.parentIds.includes(before.id) && saved.parentIds.includes('r-b12'));
  assert.equal(h.M.state.reviews.length, reviews);
});

check('historical machine restore uses actual UI save and retains the old software/basis and history', async h => {
  const b = branch(h, 'machine-01'), before = b.headId, old = record(file(h), 'r-m11');
  const oldSnapshot = JSON.stringify(old), count = file(h).revisions.length, reviews = h.M.state.reviews.length;
  h.route('#/files/f-machine/parameters?branch=machine-01&revision=r-m11');
  await h.action('restore', { revision: old.id }); await h.action('confirm-restore');
  assert.equal(b.headId, before); assert.equal(h.S.edit.restoreRevisionId, old.id);
  assert.equal(h.S.edit.headId, before); assert.equal(h.S.edit.sourceRevisionId, old.id);
  assert(h.query('[data-action="commit-edit"]', 'dialog')); assert(h.query('[data-action="submit-review"]', 'dialog'));
  await h.action('commit-edit');
  const saved = record(file(h), b.headId);
  assert.notEqual(saved.id, before); assert.equal(file(h).revisions.length, count + 1);
  assert.equal(saved.releaseId, '690'); assert.equal(b.releaseId, '690');
  assert.equal(saved.baseRevisionId, 'r-b10'); assert.equal(b.baseRevisionId, 'r-b10');
  assert.equal(JSON.stringify(saved.params), JSON.stringify(old.params));
  assert.equal(JSON.stringify(record(file(h), old.id)), oldSnapshot); assert(record(file(h), before));
  assert.equal(h.M.state.reviews.length, reviews); assert.equal(h.S.edit, null);
});

check('guest handlers reject editing/saving and leave the model unchanged', async h => {
  await h.action('demo'); await h.change('#demo-actor', 'guest');
  const before = snapshot(h);
  await assert.rejects(h.action('edit'), /管理员/);
  await assert.rejects(h.action('commit-edit'), /管理员/);
  assert.equal(snapshot(h), before); assert.equal(h.S.edit, null);
});

(async () => {
  let failed = 0;
  console.log('Execution target: ' + (useHtml ? 'built HTML inline scripts (exactly match sources)' : 'prototype source scripts'));
  for (const [name, run] of checks) {
    let h;
    try { h = createHarness(); await run(h); h.clean(); console.log('PASS ' + name); }
    catch (error) { failed++; console.error('FAIL ' + name + '\n' + error.stack); }
    finally { h?.dispose(); }
  }
  console.log(`\n${checks.length - failed}/${checks.length} Node VM UI integration checks passed. No browser, layout, accessibility or visual verification.`);
  if (failed) process.exitCode = 1;
})();
