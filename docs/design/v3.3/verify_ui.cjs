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
const mappings = [['prototype-model', 'model.js'], ['prototype-sources', 'sources.js'], ['prototype-roadmap', 'roadmap.js'], ['prototype-ui', 'app.js']];
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

function createHarness(options = {}) {
  let document;
  const timers = new Set(), documentEvents = new Map(), windowEvents = new Map();
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag.toUpperCase(); this.attributes = {}; this.dataset = {};
      this.children = []; this.parentNode = null; this.events = new Map();
      this._html = ''; this._text = ''; this.value = ''; this.style = {};
      this.checked = false; this.indeterminate = false; this.disabled = false; this.hidden = false; this.open = false;
      this.selectionStart = 0; this.scrollLeft = 0; this.scrollTop = 0;
      this.focusCalls = []; this.scrollCalls = [];
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
        if (node.tagName === 'TEXTAREA') node.value = node.textContent;
      }
      // Resolve option text values before each select chooses its initial option.
      // A single preorder pass reads SELECT before its OPTION children.
      for (const node of this.descendants()) if (node.tagName === 'SELECT') {
        const options = node.querySelectorAll('option'); node.value = (options.find(o => o.hasAttribute('selected')) || options[0])?.value || '';
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
    focus(options) { this.focusCalls.push(options ? clone(options) : null); document.activeElement = this; }
    setSelectionRange(start) { this.selectionStart = start; }
    scrollIntoView(options) { this.scrollCalls.push(options ? clone(options) : null); } click() {}
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
  const location = { hash: options.initialHash ?? '#/files/f-machine/parameters' };
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
const check = (name, run, options = {}) => checks.push([name, run, options]);
const file = h => h.M.getFile('f-machine');
const branch = (h, id = 'base-691') => file(h).branches.find(b => b.id === id);
const record = (f, id) => f.revisions.find(r => r.id === id);
const current = (h, id = 'base-691') => record(file(h), branch(h, id).headId);
const snapshot = h => JSON.stringify({ head: branch(h).headId, revisions: file(h).revisions, events: h.M.state.events });
const versionSnapshot = h => JSON.stringify({ file: file(h), events: h.M.state.events });
const versionCatalog = h => h.M.versionCatalog('f-machine');
const minorForRelease = (h, releaseId) => {
  const catalog = versionCatalog(h), release = catalog.releases.find(r => r.id === releaseId); assert(release);
  const minor = catalog.minors.find(m => m.id === release.minorId); assert(minor); return minor;
};
const releasePreview = h => h.query('#release-preview', 'dialog').textContent.replace(/\s+/g, '');
const evidenceLinks = ['https://quality.example.invalid/tickets/QA-219', 'http://intranet.example.invalid/validation/691?run=42'];
const explain = (h, reason, links = []) => {
  h.input('#save-message', reason);
  h.input('#save-links', links.join('\n'));
};
const checkRevisionEvidence = (h, revision, reason, links = evidenceLinks) => {
  assert.equal(revision.reason, reason);
  assert.equal(revision.message, reason);
  assert.deepEqual(clone(revision.links), links);
  const event = h.M.state.events.find(ev => ev.revisionId === revision.id || ev.details?.after === revision.id);
  assert(event, 'Saved revision must have an audit event');
  assert.equal(event.details.reason, reason);
  assert.deepEqual(clone(event.details.links), links);
  return event;
};
const roadmapOptions = h => Object.assign({}, clone(h.S.roadmap), {
  assets: h.M.state.assets, branchFilter: h.S.roadmapBranch || 'all',
  selectedRevisionId: h.S.roadmapSelected, query: h.S.search
});
const roadmapGraph = h => h.context.MCPrototypeRoadmap.layout(file(h), roadmapOptions(h));
const roadmapDestination = (h, id) => clone(h.context.MCPrototypeRoadmap.locateRevision(file(h), id, roadmapOptions(h)));
const assertLocatedRevision = (h, id, expected) => {
  for (const key of ['level', 'familyId', 'minorId', 'releaseId', 'columnPage', 'lanePage']) assert.equal(h.S.roadmap[key], expected[key], 'Incorrect roadmap destination: ' + key);
  assert.equal(h.S.roadmapBranch || 'all', expected.branchFilter);
  assert.equal(h.S.roadmapSelected, id);
  assert(roadmapGraph(h).visibleNodes.some(n => n.kind === 'revision' && n.id === id), 'Selected revision must be inside the displayed history window');
  assert.equal(h.query('[data-action="roadmap-select"][data-revision="' + id + '"]', 'app').getAttribute('aria-pressed'), 'true');
  assert.equal(h.document.activeElement.dataset.action, 'roadmap-select');
  assert.equal(h.document.activeElement.dataset.revision, id);
  const card = h.query('[data-roadmap-record="' + id + '"]', 'app');
  assert.deepEqual(card.scrollCalls.at(-1), { block: 'nearest', inline: 'nearest' }, 'Location must request reveal of the actual record card');
};
const extendBaselineHistory = (h, count = 35) => {
  for (let i = 0; i < count; i++) {
    const params = clone(current(h).params); params.find(p => p.id === 'p-temp').value = 43 + i % 2;
    h.M.save({ fileId: 'f-machine', branchId: 'base-691', expectedHeadId: branch(h).headId, params, reason: '窗口核对样本：第 ' + (i + 1) + ' 次验证温控修订' });
  }
};
const assertRoadmapReset = h => {
  assert.equal(h.S.roadmap.level, 'families');
  assert.equal(h.S.roadmap.familyId, null); assert.equal(h.S.roadmap.minorId, null); assert.equal(h.S.roadmap.releaseId, null);
  assert.deepEqual(clone(h.S.roadmap.expandedFamilies), []);
  assert.equal(h.S.roadmap.columnPage, 0); assert.equal(h.S.roadmap.lanePage, 0);
  assert.equal(h.S.roadmapSelected, null); assert.equal(h.S.roadmapBranch, 'all');
};

check('default entry and file links open the roadmap while the parameter page remains reachable', async h => {
  assert.equal(h.S.route[0], 'files');
  assert.equal(h.S.fileId, 'f-machine');
  assert.equal(h.S.route[2], 'roadmap');
  assertRoadmapReset(h);
  assert.equal(h.all('[data-action="roadmap-parameters"]', 'app').length, 0, 'The overview must not imply that an old revision is selected');
  assert.equal(h.all('[data-action="roadmap-export"]', 'app').length, 0, 'A summary is not an exportable configuration snapshot');
  assert.match(h.query('#app').textContent, /路线|版本/);
  const parameters = h.query('a[href="#/files/f-machine/parameters"]', 'app');
  h.route(parameters.getAttribute('href'));
  assert.equal(h.all('[data-select-param]', 'app').length, 18);
  assert.equal(h.query('[data-action="edit"]', 'app').disabled, false);
  h.route('#/files');
  const openFile = h.query('a[href="#/files/f-machine/roadmap"]', 'app');
  h.route(openFile.getAttribute('href'));
  assert.equal(h.S.route[2], 'roadmap');
  h.route('#/files/f-machine/versions');
  assert.match(h.query('#app').textContent, /路线|版本/);
  assert(h.query('a[href="#/files/f-machine/parameters"]', 'app'));
}, { initialHash: '' });

check('roadmap selection and details locate the exact adopted baseline across history windows', async h => {
  // Real model writes create a history longer than the bounded column window.
  // The latest machine record still adopts its original, older baseline.
  extendBaselineHistory(h);
  // Independent later baseline saves do not advance a machine's causal steps.
  // Give the machine its own long history so its adopted basis is off-window.
  for (let i = 0; i < 15; i++) {
    const params = clone(current(h, 'machine-01').params);
    params.find(p => p.id === 'p-speed').value += 1;
    h.M.save({ fileId: 'f-machine', branchId: 'machine-01', expectedHeadId: branch(h, 'machine-01').headId, params, reason: '机台窗口核对：第 ' + (i + 1) + ' 次现场送料验证' });
  }
  const machine = branch(h, 'machine-01'), params = clone(current(h, machine.id).params);
  const speed = params.find(p => p.id === 'p-speed'); speed.value = Number(speed.value) + 1; speed.mode = 'override';
  const reason = '机台复验后继续采用原基线，仅调整现场送料速度';
  const saved = h.M.save({ fileId: 'f-machine', branchId: machine.id, expectedHeadId: machine.headId, params, reason, links: evidenceLinks });
  const basisId = saved.baseRevisionId; assert(basisId);
  const destination = roadmapDestination(h, saved.id);
  h.route('#/files/f-machine/roadmap?revision=' + encodeURIComponent(saved.id));
  assertLocatedRevision(h, saved.id, destination);
  const viewport = clone(h.S.roadmap);
  await h.action('roadmap-select', { revision: saved.id });
  assert.deepEqual(clone(h.S.roadmap), viewport, 'Selecting an already visible revision must preserve its viewport');
  await h.change('#roadmap-focus', machine.id);
  assert.equal(h.S.roadmapBranch, machine.id); assert.equal(h.S.roadmapSelected, saved.id);
  const focused = roadmapGraph(h);
  assert(focused.projectedNodes.some(n => n.kind === 'revision' && n.id === saved.id));
  assert(focused.projectedNodes.some(n => n.id === basisId), 'Machine focus must retain its actually adopted baseline in the projection');
  assert(!focused.projectedNodes.some(n => n.id === branch(h).headId), 'Unrelated later baseline changes must not appear in the machine projection');
  const focusedDestination = roadmapDestination(h, saved.id);
  await h.action('roadmap-select', { revision: saved.id });
  assertLocatedRevision(h, saved.id, focusedDestination);
  await h.action('roadmap-detail', { revision: saved.id });
  assert(h.query('#drawer').textContent.includes(reason));
  for (const url of evidenceLinks) assert(h.query('a[href="' + url + '"]', 'drawer'));
  assert(h.query('[data-action="roadmap-jump"][data-revision="' + basisId + '"]', 'drawer'));
  await h.action('close-drawer');
  await h.change('#roadmap-focus', 'all');
  assert.equal(h.S.roadmapBranch, 'all');
  assert.equal(h.S.roadmapSelected, saved.id);
  const currentDestination = roadmapDestination(h, saved.id);
  await h.action('roadmap-select', { revision: saved.id });
  assertLocatedRevision(h, saved.id, currentDestination);
  await h.action('roadmap-detail', { revision: saved.id });
  assert(h.query('[data-action="roadmap-jump"][data-revision="' + basisId + '"]', 'drawer'));
  const oldDestination = roadmapDestination(h, basisId);
  assert.notEqual(oldDestination.columnPage, h.S.roadmap.columnPage, 'Fixture must place the adopted baseline outside the selected revision window');
  assert(!roadmapGraph(h).visibleNodes.some(n => n.id === basisId));
  await h.action('roadmap-jump', { revision: basisId });
  assertLocatedRevision(h, basisId, oldDestination);
  assert.equal(h.query('#drawer').open, false);
  assert.equal(record(file(h), saved.id).baseRevisionId, basisId);
  assert(h.query('[data-action="roadmap-compare"][data-revision="' + basisId + '"]', 'app'));
  await h.action('roadmap-compare', { revision: basisId });
  assert.equal(h.S.route[2], 'compare'); assert.equal(h.S.compareB, basisId);
  assert.equal(h.S.compareA, record(file(h), basisId).parentIds[0] || basisId);
  h.route('#/files/f-machine/roadmap?revision=' + encodeURIComponent(basisId));
  await h.action('roadmap-detail', { revision: basisId });
  assert(h.query('[data-action="roadmap-export"][data-revision="' + basisId + '"]', 'drawer'));
  await h.action('roadmap-export', { revision: basisId });
  assert.equal(h.S.route[2], 'export'); assert.equal(h.S.revisionId, basisId);
  assert.equal(h.S.branchId, record(file(h), basisId).branchId);
  h.route('#/files/f-machine/roadmap?revision=' + encodeURIComponent(basisId));
  assert(h.query('[data-action="roadmap-parameters"][data-revision="' + basisId + '"]', 'app'));
  await h.action('roadmap-parameters', { revision: basisId });
  assert.equal(h.S.route[2], 'parameters'); assert.equal(h.S.revisionId, basisId);
  assert.equal(h.S.branchId, record(file(h), basisId).branchId);
  assert.equal(h.query('[data-action="edit"]', 'app').disabled, true);
});

check('roadmap family expansion and patch navigation preserve real records and expose the frozen axes', async h => {
  h.route('#/files/f-machine/roadmap');
  const before = snapshot(h), overview = roadmapGraph(h);
  assert.equal(overview.families.length, 2);
  assert(overview.visibleNodes.length > 0);
  assert(overview.visibleNodes.every(n => n.kind === 'family-summary'));
  assert.equal(h.all('[data-action="roadmap-select"]', 'app').length, 0);
  const familyButton = h.query('[data-action="roadmap-open-family"]', 'app'), familyId = familyButton.dataset.family;
  const toggleSelector = '[data-action="roadmap-toggle-family"][data-family="' + familyId + '"]';
  assert.equal(h.query(toggleSelector, 'app').getAttribute('aria-expanded'), 'false');
  await h.action('roadmap-toggle-family', { family: familyId });
  assert.equal(h.S.roadmap.level, 'families'); assert(h.S.roadmap.expandedFamilies.includes(familyId));
  assert.equal(h.query(toggleSelector, 'app').getAttribute('aria-expanded'), 'true');
  assert(roadmapGraph(h).visibleNodes.some(n => n.kind === 'release-summary'));
  assert.equal(snapshot(h), before, 'Expanding presentation groups must not alter saved history');
  await h.action('roadmap-toggle-family', { family: familyId });
  assert(!h.S.roadmap.expandedFamilies.includes(familyId));
  assert.equal(h.query(toggleSelector, 'app').getAttribute('aria-expanded'), 'false');
  await h.action('roadmap-open-family', { family: familyId });
  assert.equal(h.S.roadmap.level, 'releases'); assert.equal(h.S.roadmap.familyId, familyId);
  assert(roadmapGraph(h).visibleNodes.every(n => n.kind === 'release-summary'));
  const minorButton = h.query('[data-action="roadmap-open-release"]', 'app'), minorId = minorButton.dataset.minor;
  assert(minorId);
  await h.action('roadmap-open-release', { family: familyId, minor: minorId });
  assert.equal(h.S.roadmap.level, 'patches'); assert.equal(h.S.roadmap.minorId, minorId);
  assert(roadmapGraph(h).visibleNodes.length > 0);
  assert(roadmapGraph(h).visibleNodes.every(n => n.kind === 'patch-summary'));
  assert.equal(h.all('[data-action="roadmap-parameters"]', 'app').length, 0);
  assert.equal(h.all('[data-action="roadmap-export"]', 'app').length, 0, 'A patch summary must not export an implicit snapshot');
  const buildButton = h.query('[data-action="roadmap-open-history"]', 'app'), releaseId = buildButton.dataset.release;
  assert(releaseId);
  await h.action('roadmap-open-history', { family: familyId, minor: minorId, release: releaseId });
  assert.equal(h.S.roadmap.level, 'history'); assert.equal(h.S.roadmap.minorId, minorId); assert.equal(h.S.roadmap.releaseId, releaseId);
  assert(roadmapGraph(h).visibleNodes.every(n => n.kind === 'revision'));
  const scroll = h.query('[data-roadmap-scroll]', 'app');
  for (const selector of ['[data-roadmap-corner]', '[data-roadmap-family]', '[data-roadmap-release]', '[data-roadmap-lane-header]']) {
    const node = h.query(selector, 'app');
    assert.equal(node.closest('[data-roadmap-scroll]'), scroll, 'Frozen axes and records must share the declared scrolling region');
  }
  assert(h.query('[data-action="roadmap-open-release"][data-minor="' + minorId + '"]', 'app'));
  await h.action('roadmap-open-release', { family: familyId, minor: minorId }); assert.equal(h.S.roadmap.level, 'patches');
  assert(h.query('[data-action="roadmap-open-family"][data-family="' + familyId + '"]', 'app'));
  await h.action('roadmap-open-family', { family: familyId }); assert.equal(h.S.roadmap.level, 'releases');
  assert(h.query('[data-action="roadmap-overview"]', 'app'));
  await h.action('roadmap-overview'); assert.equal(h.S.roadmap.level, 'families');
  assert.equal(h.all('[data-action="roadmap-parameters"]', 'app').length, 0);
  assert.equal(snapshot(h), before);
});

check('roadmap history and machine windows move independently and locate an off-window machine', async h => {
  extendBaselineHistory(h);
  const added = [];
  for (let i = 0; i < 25; i++) {
    const id = 'qa-window-asset-' + i;
    h.M.state.assets.push({ id, name: 'QA-WINDOW-' + String(i).padStart(2, '0'), model: '69X' });
    added.push(h.M.fork('f-machine', { machineId: id, fromRevisionId: 'r-b12', reason: '多机台窗口核对：建立第 ' + (i + 1) + ' 台配置' }));
  }
  h.route('#/files/f-machine/roadmap?revision=r-b12');
  await h.change('#roadmap-column-limit', 12); await h.change('#roadmap-lane-limit', 12);
  const listPage = h.S.page, listLimit = h.S.limit;
  let graph = roadmapGraph(h); assert(graph.columnPageCount > 1); assert(graph.lanePageCount > 1);
  assert(graph.visibleLanes.length <= 13, 'The fixed baseline and current machine window must remain bounded');
  let scroll = h.query('[data-roadmap-scroll]', 'app'); scroll.scrollLeft = 220; scroll.scrollTop = 80;
  const columnNext = h.query('[data-action="roadmap-column-next"]', 'app'), oldLanePage = h.S.roadmap.lanePage;
  await h.action('roadmap-column-next', { page: columnNext.dataset.page });
  assert.equal(h.S.roadmap.columnPage, Number(columnNext.dataset.page)); assert.equal(h.S.roadmap.lanePage, oldLanePage);
  scroll = h.query('[data-roadmap-scroll]', 'app'); assert.equal(scroll.scrollLeft, 0); assert.equal(scroll.scrollTop, 80);
  scroll.scrollLeft = 140; scroll.scrollTop = 240;
  const laneNext = h.query('[data-action="roadmap-lane-next"]', 'app'), oldColumnPage = h.S.roadmap.columnPage;
  await h.action('roadmap-lane-next', { page: laneNext.dataset.page });
  assert.equal(h.S.roadmap.lanePage, Number(laneNext.dataset.page)); assert.equal(h.S.roadmap.columnPage, oldColumnPage);
  scroll = h.query('[data-roadmap-scroll]', 'app'); assert.equal(scroll.scrollLeft, 140); assert.equal(scroll.scrollTop, 0);
  assert.equal(h.S.page, listPage); assert.equal(h.S.limit, listLimit, 'Roadmap windows must not mutate another view\'s list limits');
  graph = roadmapGraph(h); assert(graph.visibleLanes.length <= 13);
  assert(graph.visibleNodes.length <= 12 * 13, 'Rendered history must remain bounded by both windows');
  h.route('#/files/f-machine/parameters?revision=r-b12');
  h.route('#/files/f-machine/roadmap?revision=r-b12');
  assert(h.query('[data-action="roadmap-detail"][data-revision="r-b12"]', 'app'));
  await h.action('roadmap-detail', { revision: 'r-b12' });
  const target = added[added.length - 1], expected = roadmapDestination(h, target.headId);
  assert.equal(expected.branchFilter, 'all'); assert(expected.lanePage > 0);
  assert(h.query('[data-action="roadmap-jump"][data-revision="' + target.headId + '"]', 'drawer'));
  await h.action('roadmap-jump', { revision: target.headId });
  assertLocatedRevision(h, target.headId, expected); assert.equal(h.query('#drawer').open, false);
  assert.equal(record(file(h), target.headId).baseRevisionId, 'r-b12');
  const viewport = clone(h.S.roadmap);
  await h.action('roadmap-select', { revision: target.headId });
  assert.deepEqual(clone(h.S.roadmap), viewport);
});

check('roadmap context survives a same-file parameter visit and resets for another file or workspace', async h => {
  extendBaselineHistory(h);
  h.route('#/files/f-machine/roadmap');
  const familyId = h.query('[data-action="roadmap-toggle-family"]', 'app').dataset.family;
  await h.action('roadmap-toggle-family', { family: familyId });
  h.route('#/files/f-machine/roadmap?revision=' + encodeURIComponent(branch(h).headId));
  assert.equal(h.S.roadmap.level, 'history'); assert(h.S.roadmap.expandedFamilies.includes(familyId));
  const context = clone(h.S.roadmap), selected = h.S.roadmapSelected, focused = h.S.roadmapBranch;
  const scroll = h.query('[data-roadmap-scroll]', 'app'); scroll.scrollLeft = 320; scroll.scrollTop = 120;
  assert(h.query('[data-action="roadmap-parameters"][data-revision="' + selected + '"]', 'app'));
  await h.action('roadmap-parameters', { revision: selected });
  h.route(h.query('a[href="#/files/f-machine/roadmap"]', 'app').getAttribute('href'));
  assert.deepEqual(clone(h.S.roadmap), context); assert.equal(h.S.roadmapSelected, selected); assert.equal(h.S.roadmapBranch, focused);
  const returnedScroll = h.query('[data-roadmap-scroll]', 'app');
  assert.equal(returnedScroll.scrollLeft, 320); assert.equal(returnedScroll.scrollTop, 120);
  const other = h.M.state.workspaces.find(w => w.id === h.M.state.workspaceId).files.find(f => f.id !== 'f-machine'); assert(other);
  h.route('#/files/' + encodeURIComponent(other.id) + '/roadmap'); assertRoadmapReset(h);
  h.route('#/files/f-machine/roadmap'); assertRoadmapReset(h);
  await h.action('roadmap-toggle-family', { family: familyId });
  h.route('#/files/f-machine/roadmap?revision=r-m13');
  assert.equal(h.S.roadmap.level, 'history');
  await h.change('#workspace', 'ws-lab'); assertRoadmapReset(h);
  assert.equal(h.M.state.workspaceId, 'ws-lab');
  assert.equal(h.all('[data-action="roadmap-parameters"]', 'app').length, 0);
});

check('file list page selection has a mixed state, persists across pages, and clears explicitly', async h => {
  await h.action('demo-files'); await h.change('#page-limit', 30);
  const pageOne = h.all('[data-select-file]', 'app').map(n => n.dataset.selectFile);
  assert.equal(pageOne.length, 30);
  assert.equal(h.query('[data-select-page="files"]').checked, false);
  await h.change('[data-select-file="' + pageOne[0] + '"]', true);
  assert.equal(h.query('[data-select-page="files"]').indeterminate, true);
  await h.change('[data-select-page="files"]', true);
  assert.equal(h.S.selected.size, 30);
  assert(pageOne.every(id => h.S.selected.has(id)));
  assert.equal(h.query('[data-select-page="files"]').checked, true);
  assert.equal(h.query('[data-select-page="files"]').indeterminate, false);
  await h.action('next');
  assert.equal(h.query('[data-select-page="files"]').checked, false);
  const secondId = h.all('[data-select-file]', 'app')[0].dataset.selectFile;
  await h.change('[data-select-file="' + secondId + '"]', true);
  assert.equal(h.S.selected.size, 31);
  assert.equal(h.query('[data-select-page="files"]').indeterminate, true);
  await h.action('prev');
  assert.equal(h.query('[data-select-page="files"]').checked, true);
  assert(h.S.selected.has(secondId), 'Changing page must not discard selection on other pages');
  await h.action('clear-selection');
  assert.equal(h.S.selected.size, 0);
  assert.equal(h.query('[data-select-page="files"]').checked, false);
  assert.equal(h.query('[data-select-page="files"]').indeterminate, false);
});

check('select all matching files obeys the active filter and reaches beyond the current page', async h => {
  await h.action('demo-files'); await h.change('#page-limit', 30);
  h.input('[data-search="main"]', '.json');
  await new Promise(resolve => setTimeout(resolve, 10)); h.clean();
  const expected = h.M.state.workspaces[0].files.filter(f => (f.name + ' ' + f.description).toLowerCase().includes('.json')).map(f => f.id);
  assert(expected.length > 30, 'Fixture must exercise selection beyond page one');
  await h.change('[data-select-page="files"]', true);
  assert(h.query('[data-action="select-all-matches"][data-scope="files"]'));
  await h.action('select-all-matches', { scope: 'files' });
  assert.deepEqual([...h.S.selected].sort(), clone(expected).sort());
  await h.action('next');
  assert(h.all('[data-select-file]', 'app').every(n => n.checked));
  await h.action('clear-selection'); assert.equal(h.S.selected.size, 0);
});

check('parameter selection has explicit page/all scopes and preserves selections across pages', async h => {
  await h.action('demo-large'); h.route('#/files/f-machine/parameters'); await h.change('#page-limit', 30);
  const firstPage = h.all('[data-select-param]', 'app').map(n => n.dataset.selectParam);
  await h.change('[data-select-param="' + firstPage[0] + '"]', true);
  assert.equal(h.query('[data-select-page="params"]').indeterminate, true);
  await h.change('[data-select-page="params"]', true); assert.equal(h.S.selected.size, 30);
  await h.action('next'); const secondId = h.all('[data-select-param]', 'app')[0].dataset.selectParam;
  await h.change('[data-select-param="' + secondId + '"]', true); assert.equal(h.S.selected.size, 31);
  await h.action('prev'); assert(firstPage.every(id => h.S.selected.has(id))); assert(h.S.selected.has(secondId));
  await h.action('clear-selection');
  h.input('[data-search="main"]', '/Axis1/'); await new Promise(resolve => setTimeout(resolve, 10)); h.clean();
  const expected = current(h).params.filter(p => (p.name + ' ' + p.path + ' ' + String(p.value)).toLowerCase().includes('/axis1/')).map(p => p.id);
  assert(expected.length > 30);
  await h.change('[data-select-page="params"]', true);
  assert(h.query('[data-action="select-all-matches"][data-scope="params"]'));
  await h.action('select-all-matches', { scope: 'params' });
  assert.deepEqual([...h.S.selected].sort(), clone(expected).sort());
  assert.equal(h.query('[data-select-page="params"]').checked, true);
  await h.action('clear-selection'); assert.equal(h.S.selected.size, 0);
});

check('source preview provides select all, mixed state, and explicit deselection', async h => {
  h.route('#/sources/new'); await h.action('source-sample', { kind: 'html' }); await h.action('detect-source');
  assert.equal(h.S.sourcePreview.files.length, 2);
  assert.equal(h.query('[data-select-preview-all]').checked, true);
  await h.change('[data-preview-select="0"]', false);
  assert.equal(h.query('[data-select-preview-all]').indeterminate, true);
  await h.change('[data-select-preview-all]', false);
  assert(h.S.sourcePreview.files.every(row => !row.selected));
  assert.equal(h.query('[data-select-preview-all]').indeterminate, false);
  await h.change('[data-select-preview-all]', true);
  assert(h.S.sourcePreview.files.every(row => row.selected));
});

check('parameter picker distinguishes its displayed page from all matches and can clear both', async h => {
  await h.action('demo-large'); h.route('#/files/f-machine/parameters'); await h.action('choose-parameters');
  await h.action('picker-clear');
  assert.equal(h.S.tracePick.size, 0);
  const visible = h.all('[data-trace-pick]', 'dialog'); assert.equal(visible.length, 100);
  await h.change('[data-trace-pick="' + visible[0].dataset.tracePick + '"]', true);
  assert.equal(h.query('[data-select-picker-page]', 'dialog').indeterminate, true);
  await h.change('[data-select-picker-page]', true); assert.equal(h.S.tracePick.size, 100);
  await h.action('picker-select-all');
  const allIds = new Set(file(h).revisions.flatMap(r => r.params.map(p => p.id)));
  assert.equal(h.S.tracePick.size, allIds.size);
  await h.action('picker-clear'); assert.equal(h.S.tracePick.size, 0);
  assert.equal(h.query('[data-select-picker-page]', 'dialog').checked, false);
  assert.equal(h.query('[data-select-picker-page]', 'dialog').indeterminate, false);
});

check('record comparison selects the latest six explicitly and rejects an oversized selection', async h => {
  h.route('#/parameters?file=f-machine'); await h.action('trace-versions');
  const previousRecords = [...h.S.traceRevs];
  assert(h.query('[data-action="select-trace-records"]', 'dialog'));
  assert(h.query('[data-action="clear-trace-records"]', 'dialog'));
  await h.action('clear-trace-records'); assert.equal(h.all('[data-trace-record]:checked', 'dialog').length, 0);
  await h.action('select-trace-records');
  const selected = h.all('[data-trace-record]:checked', 'dialog').map(n => n.dataset.traceRecord);
  const expected = file(h).revisions.filter(r => r.kind !== 'initial').slice().sort((a, b) => b.at.localeCompare(a.at)).slice(0, 6).map(r => r.id);
  assert.deepEqual(selected.slice().sort(), clone(expected).sort());
  assert.equal(selected.length, 6);
  const seventh = h.all('[data-trace-record]', 'dialog').find(n => !n.checked); assert(seventh);
  seventh.checked = true;
  await assert.rejects(h.action('apply-trace-versions'), /1[–—-]6|最多.*6/);
  assert.deepEqual([...h.S.traceRevs], previousRecords);
  await h.action('select-trace-records'); await h.action('apply-trace-versions');
  assert.deepEqual([...h.S.traceRevs].sort(), clone(expected).sort());
});

check('file/machine/parameter/review/source/audit routes render for both roles', async h => {
  const routes = ['#/files', '#/files/f-machine/parameters', '#/files/f-machine/versions', '#/files/f-machine/compare', '#/files/f-machine/merge?target=machine-01', '#/files/f-machine/export', '#/files/f-machine/reviews', '#/files/f-machine/updates', '#/files/f-machine/records', '#/machines', '#/machines/asset-01', '#/parameters', '#/reviews', '#/review/CR-024', '#/review/CR-025', '#/sources', '#/sources/new', '#/records', '#/initialize'];
  for (const actor of ['lin', 'guest']) { h.M.state.actorId = actor; for (const route of routes) { h.route(route); assert(h.query('#page-title').textContent.length > 0); } }
});

check('visible saved-record names are readable across roadmap, selectors and review pages', async h => {
  for (const route of ['#/files/f-machine/roadmap', '#/files/f-machine/roadmap?revision=r-m13', '#/files/f-machine/parameters', '#/files/f-machine/compare', '#/files/f-machine/merge?target=machine-01', '#/files/f-machine/export', '#/review/CR-024']) {
    h.route(route);
    assert.doesNotMatch(h.query('#app').textContent, /\bB\d+\b|\br\d+\b/, 'Raw storage labels remain visible at ' + route);
  }
});

check('parameter/file pages respect 30, 50, 100 limits with reachable subsequent pages', async h => {
  await h.action('demo-large');
  h.route('#/files/f-machine/parameters');
  assert.equal(current(h).params.length, 3248);
  for (const limit of [30, 50, 100]) { await h.change('#page-limit', limit); assert.equal(h.all('[data-select-param]', 'app').length, limit); await h.action('next'); assert.equal(h.all('[data-select-param]', 'app').length, limit); assert.equal(h.S.page, 1); }
  await h.action('demo-files'); assert.equal(h.M.state.workspaces[0].files.length, 100);
  for (const limit of [30, 50, 100]) { await h.change('#page-limit', limit); assert.equal(h.all('[data-select-file]', 'app').length, limit); }
});

check('large check/save dialog and optional review paginate differences at 50 rows', async h => {
  await h.action('demo-large'); h.route('#/files/f-machine/parameters'); await h.action('edit');
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
  explain(h, '批量参数经过测试核对，需要按本轮验证结果统一修订');
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

check('empty reason and unsafe links reject before mutation while preserving the edited values', async h => {
  await h.action('edit'); h.input('[data-edit-value="p-temp"]', 44); await h.action('review-save');
  assert.equal(h.query('#save-message').value, '');
  assert(h.query('#save-message').hasAttribute('required'));
  explain(h, '   ', evidenceLinks); const before = snapshot(h);
  await assert.rejects(h.action('commit-edit'), error => error.code === 'REASON_REQUIRED' || /原因|说明/.test(error.message));
  assert.equal(snapshot(h), before);
  assert.equal(h.S.edit.params.find(p => p.id === 'p-temp').value, 44);
  assert.equal(h.query('#save-message').value.trim(), '');
  assert.equal(h.query('#save-links').value, evidenceLinks.join('\n'));
  for (const unsafe of ['javascript:alert(1)', 'data:text/html,unsafe', 'file:///etc/passwd']) {
    explain(h, '验证完成后调整温控设定', [evidenceLinks[0], unsafe]);
    await assert.rejects(h.action('commit-edit'), error => /^INVALID_LINK/.test(error.code || '') || /链接|HTTP/.test(error.message));
    assert.equal(snapshot(h), before, 'Rejected URL must not create a revision or audit event');
    assert.equal(h.S.edit.params.find(p => p.id === 'p-temp').value, 44);
    assert(h.query('#save-links').value.includes(unsafe), 'Invalid input must remain available for correction');
  }
  const reason = '温控设定已通过现场验证，按验证记录调整至 44 度';
  explain(h, reason, [...evidenceLinks, evidenceLinks[0]]); await h.action('commit-edit');
  const saved = current(h), event = checkRevisionEvidence(h, saved, reason);
  assert.equal(saved.params.find(p => p.id === 'p-temp').value, 44);
  await h.action('event-detail', { event: event.id });
  assert(h.query('#drawer').textContent.includes(reason));
  for (const url of evidenceLinks) assert(h.query('a[href="' + url + '"]', 'drawer'));
});

check('review submission rejects an empty reason and preserves evidence through approval and merge', async h => {
  await h.action('edit'); h.input('[data-edit-value="p-temp"]', 44); await h.action('review-save');
  explain(h, '', evidenceLinks); const before = snapshot(h), reviewCount = h.M.state.reviews.length;
  await assert.rejects(h.action('submit-review'), error => error.code === 'REASON_REQUIRED' || /原因|说明/.test(error.message));
  assert.equal(snapshot(h), before); assert.equal(h.M.state.reviews.length, reviewCount); assert(h.S.edit);
  const reason = '温控调整需要另一位管理员结合现场验证记录确认';
  explain(h, reason, evidenceLinks); await h.action('submit-review');
  const review = h.M.state.reviews.find(r => r.id === h.S.route[1]);
  assert.equal(review.reason, reason); assert.equal(review.message, reason); assert.deepEqual(clone(review.links), evidenceLinks);
  assert.equal(branch(h).headId, 'r-b12'); assert.equal(h.S.edit, null);
  assert(h.query('#app').textContent.includes(reason));
  for (const url of evidenceLinks) assert(h.query('a[href="' + url + '"]', 'app'));
  const created = h.M.state.events.find(ev => ev.action === 'review_created' && ev.reviewId === review.id);
  assert(created); assert.equal(created.details.reason, reason); assert.deepEqual(clone(created.details.links), evidenceLinks);
  h.M.state.actorId = 'wang'; h.app.render(); h.clean();
  await h.action('approve-review', { review: review.id }); await h.action('merge-review', { review: review.id });
  assert.equal(review.status, 'merged'); checkRevisionEvidence(h, current(h), reason);
});

check('failed direct save preserves inputs and retries through the actual save handler', async h => {
  await h.action('edit'); h.input('[data-edit-value="p-temp"]', 44); await h.action('review-save');
  explain(h, '热稳定验证要求将温控设定调整为 44 度');
  const before = snapshot(h); h.S.failNext = true;
  await assert.rejects(h.action('commit-edit'), /模拟保存失败/);
  assert.equal(snapshot(h), before); assert.equal(h.S.edit.params.find(p => p.id === 'p-temp').value, 44);
  await h.action('commit-edit'); assert.equal(h.S.edit, null); assert.equal(current(h).params.find(p => p.id === 'p-temp').value, 44);
});

check('concurrent same-field choice preserves independent server name and unit changes', async h => {
  await h.action('edit'); h.input('[data-edit-value="p-speed"]', 125);
  await h.action('review-save'); explain(h, '采用本轮节拍验证的 125 速度，同时保留现场校准属性');
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
  h.input('#source-reason', '配置经过核对的文件来源，后续更新先进入待核对');
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
  await h.action('review-save'); assert.equal(h.query('#save-message').value, '', 'Generated source-operation text must not stand in for a user reason');
  explain(h, '已核对来源中的温控变化，采用验证后的设定'); await h.action('commit-edit');
  assert.notEqual(branch(h).headId, oldHead); assert.equal(current(h).params.find(p => p.id === 'p-temp').value, 45);
  assert(!h.sources.candidates('ws-prod', source.id).some(c => c.id === candidate.id));
  assert(h.M.state.events.some(ev => ev.action === 'source_candidate_applied' && ev.details.candidateId === candidate.id));
});

check('source changes require a fresh reason and audited links without invalidating extraction metadata', async h => {
  h.route('#/sources/new'); await h.action('source-sample', { kind: 'file' }); h.input('#source-name', '来源审计核对'); await h.action('detect-source');
  const previewId = h.S.sourcePreview.testId, original = JSON.stringify(h.sources.list('ws-prod')), auditCount = h.M.state.events.length;
  h.input('#source-reason', ''); h.input('#source-links', evidenceLinks.join('\n'));
  assert.equal(h.S.sourcePreview.testId, previewId, 'Reason and links are operation metadata, not extraction rules');
  await assert.rejects(h.action('save-source'), error => error.code === 'REASON_REQUIRED' || /原因|说明/.test(error.message));
  assert.equal(JSON.stringify(h.sources.list('ws-prod')), original); assert.equal(h.M.state.events.length, auditCount);
  h.input('#source-reason', '为已核对的配置建立更新入口，关联下载规范和验证记录');
  h.input('#source-links', 'javascript:alert(1)');
  await assert.rejects(h.action('save-source'), error => /^INVALID_LINK/.test(error.code || '') || /链接|HTTP/.test(error.message));
  assert.equal(JSON.stringify(h.sources.list('ws-prod')), original); assert.equal(h.M.state.events.length, auditCount);
  h.input('#source-links', evidenceLinks.join('\n')); await h.action('save-source');
  const source = h.sources.list('ws-prod').find(s => s.name === '来源审计核对'); assert(source);
  const first = h.M.state.events.find(ev => ev.action === 'source_saved' && ev.sourceId === source.id);
  assert(first); assert.equal(first.details.reason, '为已核对的配置建立更新入口，关联下载规范和验证记录'); assert.deepEqual(clone(first.details.links), evidenceLinks);
  h.route('#/sources/new?source=' + source.id);
  assert.equal(h.query('#source-reason').value.trim(), '', 'A new source modification must not silently reuse the prior reason');
  h.input('#source-name', '来源审计核对（修订）'); await h.action('detect-source');
  const before = JSON.stringify(h.sources.get('ws-prod', source.id));
  await assert.rejects(h.action('save-source'), error => error.code === 'REASON_REQUIRED' || /原因|说明/.test(error.message));
  assert.equal(JSON.stringify(h.sources.get('ws-prod', source.id)), before);
  h.input('#source-reason', '调整来源名称以对应新的设备参数目录'); h.input('#source-links', evidenceLinks[1]); await h.action('save-source');
  const second = h.M.state.events.find(ev => ev.action === 'source_saved' && ev.sourceId === source.id);
  assert.notEqual(second.id, first.id); assert.equal(second.details.reason, '调整来源名称以对应新的设备参数目录'); assert.deepEqual(clone(second.details.links), [evidenceLinks[1]]);
  await h.action('event-detail', { event: second.id }); assert(h.query('#drawer').textContent.includes(second.details.reason));
  assert(h.query('a[href="' + evidenceLinks[1] + '"]', 'drawer'));
});

check('initialization rejects an empty reason and stores the supplied explanation and links', async h => {
  h.route('#/initialize'); await h.action('init-sample');
  const count = h.M.state.workspaces[0].files.length, params = JSON.stringify(h.S.init.params), events = h.M.state.events.length;
  h.input('#init-reason', '');
  await assert.rejects(h.action('init-save'), error => error.code === 'REASON_REQUIRED' || /原因|说明/.test(error.message));
  assert.equal(h.M.state.workspaces[0].files.length, count); assert.equal(h.M.state.events.length, events); assert.equal(JSON.stringify(h.S.init.params), params);
  h.input('#init-reason', '为新设备建立经过核对的参数起点'); h.input('#init-links', evidenceLinks.join('\n')); await h.action('init-save');
  const created = h.M.state.workspaces[0].files.find(f => f.name === 'DeviceSettings.xml'); assert(created);
  const baseline = record(created, created.branches[0].headId);
  assert.equal(baseline.reason, '为新设备建立经过核对的参数起点'); assert.deepEqual(clone(baseline.links), evidenceLinks);
});

check('new-machine creation validates its reason before creating an asset or branch', async h => {
  await h.action('fork');
  h.query('#fork-asset').value = 'new'; h.input('#fork-name', 'QA-NEW-MACHINE'); h.input('#fork-reason', '');
  const assets = JSON.stringify(h.M.state.assets), branches = JSON.stringify(file(h).branches), events = h.M.state.events.length;
  await assert.rejects(h.action('create-fork'), error => error.code === 'REASON_REQUIRED' || /原因|说明/.test(error.message));
  assert.equal(JSON.stringify(h.M.state.assets), assets); assert.equal(JSON.stringify(file(h).branches), branches); assert.equal(h.M.state.events.length, events);
  h.input('#fork-reason', '新机台完成入场验证，从核定基线建立独立配置'); h.input('#fork-links', evidenceLinks.join('\n')); await h.action('create-fork');
  const asset = h.M.state.assets.find(a => a.name === 'QA-NEW-MACHINE'); assert(asset);
  const created = file(h).branches.find(b => b.machineId === asset.id); assert(created);
  const saved = record(file(h), created.headId); assert.equal(saved.reason, '新机台完成入场验证，从核定基线建立独立配置'); assert.deepEqual(clone(saved.links), evidenceLinks);
});

check('new software version keeps a separate required reason and associated links', async h => {
  const parent = minorForRelease(h, '691');
  await h.action('new-release'); await h.change('#release-family', parent.familyId); await h.change('#release-minor', 'new');
  h.input('#release-name', '692'); h.input('#release-reason', '');
  assert.equal(h.query('#release-kind').value, 'original'); assert.equal(h.query('#release-source').value, '');
  const before = versionSnapshot(h), recordCount = file(h).revisions.length;
  await assert.rejects(h.action('create-release'), error => error.code === 'REASON_REQUIRED' || /原因|说明/.test(error.message));
  assert.equal(versionSnapshot(h), before, 'A rejected form must not leave a family, minor, build or audit event behind');
  assert.equal(h.query('#release-name').value, '692');
  h.input('#release-reason', '为软件 692 建立独立参数版本，等待对应版本文件'); h.input('#release-links', evidenceLinks.join('\n')); await h.action('create-release');
  const catalog = versionCatalog(h), minor = catalog.minors.find(m => m.familyId === parent.familyId && m.version === '692'); assert(minor);
  const release = catalog.releases.find(r => r.minorId === minor.id && r.patchNumber === null); assert(release);
  const created = file(h).branches.find(b => b.id === release.branchId); assert(created);
  assert.equal(created.headId, null); assert.equal(file(h).revisions.length, recordCount);
  assert.equal(h.S.route[2], 'parameters'); assert.equal(h.S.branchId, created.id);
  assert(h.query('[data-action="initialize-branch"]', 'app'));
  assert.equal(created.creationReason, '为软件 692 建立独立参数版本，等待对应版本文件'); assert.deepEqual(clone(created.links), evidenceLinks);
  const event = h.M.state.events.find(ev => ev.action === 'release_created' && ev.branchId === created.id); assert(event);
  assert.equal(event.details.reason, created.creationReason); assert.deepEqual(clone(event.details.links), evidenceLinks);
});

check('version form resets dependent choices and source while preserving evidence and previewing explicit months', async h => {
  const first = minorForRelease(h, '691'), second = minorForRelease(h, '6A1'), sibling = minorForRelease(h, '6A0');
  const before = versionSnapshot(h), reason = '根据发布计划确认目标版本，保留验证资料供复核';
  await h.action('new-release');
  h.input('#release-reason', reason); h.input('#release-links', evidenceLinks.join('\n'));
  await h.change('#release-family', first.familyId); await h.change('#release-minor', first.id);
  assert.equal(h.query('#release-kind').value, 'patch');
  assert.equal(h.query('#release-kind').querySelector('[value="original"]').disabled, true);
  assert.equal(releasePreview(h), '691SPC001');
  await h.change('#release-source', 'r-b12');
  await h.change('#release-family', second.familyId);
  assert.equal(h.query('#release-minor').value, 'new'); assert.equal(h.query('#release-kind').value, 'original');
  assert.equal(h.query('#release-source').value, '');
  assert.equal(h.query('#release-reason').value, reason); assert.equal(h.query('#release-links').value, evidenceLinks.join('\n'));
  await h.change('#release-minor', second.id);
  assert.equal(releasePreview(h), '6A1SPC002');
  await h.change('#release-source', 'r-b15');
  await h.change('#release-minor', sibling.id);
  assert.equal(h.query('#release-source').value, ''); assert.equal(releasePreview(h), '6A0SPC001');
  assert.equal(h.query('#release-reason').value, reason); assert.equal(h.query('#release-links').value, evidenceLinks.join('\n'));
  await h.change('#release-family', 'new');
  for (const [month, label] of [['2026-09', '69X'], ['2026-10', '6AX'], ['2027-01', '71X']]) {
    await h.change('#release-month', month);
    assert.equal(h.query('.release-generated', 'dialog').textContent, label);
    assert.equal(h.query('#release-minor').value, 'new'); assert.equal(h.query('#release-source').value, '');
    assert.equal(h.query('#release-reason').value, reason); assert.equal(h.query('#release-links').value, evidenceLinks.join('\n'));
  }
  h.input('#release-name', '   ');
  assert.equal(h.query('[data-action="create-release"]', 'dialog').disabled, true);
  await assert.rejects(h.action('create-release'), /小版本/);
  assert(h.query('#release-error', 'dialog').textContent.includes('小版本'));
  assert.equal(versionSnapshot(h), before);
  await h.change('#release-family', first.familyId); await h.change('#release-minor', 'new');
  h.input('#release-name', '691');
  await assert.rejects(h.action('create-release'), /原版.*存在|版本.*存在/);
  assert.equal(versionSnapshot(h), before); assert.equal(h.query('#release-name').value, '691');
  h.input('#release-name', '692SPC001');
  await assert.rejects(h.action('create-release'), error => error.code === 'INVALID_MINOR_VERSION');
  assert.match(h.query('#release-error', 'dialog').textContent, /补丁/);
  assert.equal(versionSnapshot(h), before); assert.equal(h.query('#release-name').value, '692SPC001');
  assert.equal(h.query('#release-reason').value, reason); assert.equal(h.query('#release-links').value, evidenceLinks.join('\n'));
  await h.action('close-dialog'); assert.equal(h.S.releaseDraft, null);
  await h.action('new-release');
  assert.equal(h.query('#release-reason').value, ''); assert.equal(h.query('#release-links').value, '');
  assert.equal(h.query('#release-source').value, ''); assert.equal(versionSnapshot(h), before);
  await h.action('close-dialog');
});

check('a new minor can start with a pending patch and later inherit an explicitly selected snapshot without inventing an original', async h => {
  const originalFamily = versionCatalog(h).families.find(f => f.monthKey === '2026-09'); assert(originalFamily);
  const originalHead = branch(h).headId, originalSnapshot = JSON.stringify(record(file(h), 'r-b12'));
  const initialRecordCount = file(h).revisions.length;
  await h.action('new-release'); await h.change('#release-family', 'new'); await h.change('#release-month', '2036-09');
  h.input('#release-name', '699'); await h.change('#release-kind', 'patch');
  assert.equal(releasePreview(h), '699SPC001'); assert.equal(h.query('#release-source').value, '');
  const pendingReason = '为新年月版本登记首个补丁包，等待该补丁的配置文件';
  h.input('#release-reason', pendingReason); h.input('#release-links', evidenceLinks.join('\n')); await h.action('create-release');
  let catalog = versionCatalog(h);
  const family = catalog.families.find(f => f.monthKey === '2036-09'); assert(family);
  assert.equal(family.label, '69X'); assert.notEqual(family.id, originalFamily.id, 'The repeated short code must not merge different full years');
  const minor = catalog.minors.find(m => m.familyId === family.id && m.version === '699'); assert(minor);
  assert.equal(minor.originalReleaseId, null);
  const pending = catalog.releases.find(r => r.minorId === minor.id && r.patchNumber === 1); assert(pending);
  assert.equal(pending.headId, null); assert.equal(file(h).revisions.length, initialRecordCount);
  assert.equal(h.S.route[2], 'parameters'); assert.equal(h.S.branchId, pending.branchId);
  assert(h.query('[data-action="initialize-branch"]', 'app'));
  const pendingEvent = h.M.state.events.find(ev => ev.action === 'release_created' && ev.branchId === pending.branchId); assert(pendingEvent);
  assert.equal(pendingEvent.details.reason, pendingReason); assert.deepEqual(clone(pendingEvent.details.links), evidenceLinks);
  assert.equal(pendingEvent.details.sourceRevisionId, null); assert.equal(pendingEvent.details.initialized, false);
  await h.action('new-release');
  const familySelect = h.query('#release-family');
  assert(familySelect.querySelector('[value="' + originalFamily.id + '"]').textContent.includes('2026-09'));
  assert(familySelect.querySelector('[value="' + family.id + '"]').textContent.includes('2036-09'));
  await h.change('#release-family', family.id); await h.change('#release-minor', minor.id); await h.change('#release-kind', 'patch');
  assert.equal(releasePreview(h), '699SPC002'); assert.equal(h.query('#release-source').value, '');
  await h.change('#release-source', 'r-b12');
  const reason = '第二个补丁明确采用已核对的保存记录作为配置起点';
  h.input('#release-reason', reason); h.input('#release-links', evidenceLinks.join('\n')); await h.action('create-release');
  catalog = versionCatalog(h);
  const initialized = catalog.releases.find(r => r.minorId === minor.id && r.patchNumber === 2); assert(initialized?.headId);
  const createdBranch = file(h).branches.find(b => b.id === initialized.branchId), head = record(file(h), initialized.headId), initial = record(file(h), createdBranch.initialRevisionId);
  assert(initial); assert.deepEqual(clone(initial.parentIds), ['r-b12']); assert.equal(initial.sourceRevisionId, 'r-b12');
  assert.deepEqual(clone(head.parentIds), [initial.id]); assert.deepEqual(clone(head.params), clone(record(file(h), 'r-b12').params));
  assert.equal(initial.releaseId, initialized.id); assert.equal(head.releaseId, initialized.id);
  assert.equal(catalog.minors.find(m => m.id === minor.id).originalReleaseId, null);
  assert(catalog.releases.filter(r => r.minorId === minor.id).every(r => r.patchNumber !== null));
  assert.equal(JSON.stringify(record(file(h), 'r-b12')), originalSnapshot); assert.equal(branch(h).headId, originalHead);
  assertLocatedRevision(h, head.id, roadmapDestination(h, head.id));
  assert.equal(h.S.roadmap.familyId, family.id); assert.equal(h.S.roadmap.minorId, minor.id); assert.equal(h.S.roadmap.releaseId, initialized.id);
  checkRevisionEvidence(h, head, reason);
  assert.equal(h.S.releaseDraft, null);
});

check('stale patch numbering rejects atomically and requires a second explicit submission with the refreshed number', async h => {
  const minor = minorForRelease(h, '691'), originalHead = branch(h).headId;
  await h.action('new-release'); await h.change('#release-family', minor.familyId); await h.change('#release-minor', minor.id);
  assert.equal(releasePreview(h), '691SPC001'); await h.change('#release-source', 'r-b12');
  const reason = '补丁发布前再次确认编号，并按验证记录建立配置';
  h.input('#release-reason', reason); h.input('#release-links', evidenceLinks.join('\n'));
  const competing = h.M.addRelease('f-machine', { target: { familyId: minor.familyId, minorId: minor.id, kind: 'patch' }, expectedPatchNumber: 1, reason: '并发样本：另一位维护者已登记首个补丁包' });
  const before = versionSnapshot(h);
  await assert.rejects(h.action('create-release'), error => error.code === 'PATCH_STALE');
  assert.equal(versionSnapshot(h), before, 'A stale preview must not write a replacement number silently');
  assert.equal(h.query('#dialog').open, true); assert.equal(releasePreview(h), '691SPC002');
  assert.match(h.query('#release-error', 'dialog').textContent, /再次创建|重新.*创建/);
  assert.equal(h.query('#release-reason').value, reason); assert.equal(h.query('#release-links').value, evidenceLinks.join('\n'));
  assert.equal(h.query('#release-source').value, 'r-b12');
  assert.equal(h.M.nextPatch('f-machine', minor.id).number, 2);
  await h.action('create-release');
  const catalog = versionCatalog(h), created = catalog.releases.find(r => r.minorId === minor.id && r.patchNumber === 2); assert(created?.headId);
  assert.equal(catalog.releases.filter(r => r.minorId === minor.id && r.patchNumber !== null).length, 2);
  assert(file(h).branches.some(b => b.id === competing.id)); assert.equal(branch(h).headId, originalHead);
  assert.equal(h.query('#dialog').open, false); assert.equal(h.S.releaseDraft, null);
  const head = record(file(h), created.headId);
  assertLocatedRevision(h, head.id, roadmapDestination(h, head.id));
  assert.equal(h.S.roadmap.releaseId, created.id); assert.equal(h.S.roadmap.minorId, minor.id);
  checkRevisionEvidence(h, head, reason);
});

check('explicit head snapshot exits read-only and historical machine shows its own basis', async h => {
  h.route('#/files/f-machine/parameters?branch=base-691&revision=r-b12');
  assert.equal(h.S.revisionId, 'r-b12'); assert.equal(h.query('[data-action="edit"]', 'app').disabled, true);
  await h.action('return-current'); assert.equal(h.S.revisionId, null); assert.equal(h.query('[data-action="edit"]', 'app').disabled, false);
  const b = branch(h, 'machine-01');
  h.M.save({ fileId: 'f-machine', branchId: b.id, expectedHeadId: b.headId, params: record(file(h), 'r-b12').params, sourceRevisionId: 'r-b12', message: '采用已验证的新基线，核对历史记录保持原参考' });
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
  assert.equal(h.query('#save-message').value, '', 'Generated merge description must not satisfy the reason requirement');
  explain(h, '采用最新节拍基线，同时保留机台已校准的显示名称和保持值', evidenceLinks);
  await h.action('commit-edit');
  const saved = record(file(h), b.headId), speed = saved.params.find(p => p.id === 'p-speed'), pinned = saved.params.find(p => p.id === 'p-offset');
  assert.equal(saved.baseRevisionId, 'r-b12'); assert.equal(saved.sourceRevisionId, 'r-b12');
  assert.equal(speed.value, 120); assert.equal(speed.name, '送料速度（机台显示）');
  assert.equal(pinned.value, 0.15); assert.equal(pinned.mode, 'pin');
  assert.equal(saved.params.find(p => p.id === 'p-temp').value, 42);
  assert(saved.parentIds.includes(before.id) && saved.parentIds.includes('r-b12'));
  assert.equal(h.M.state.reviews.length, reviews);
  checkRevisionEvidence(h, saved, '采用最新节拍基线，同时保留机台已校准的显示名称和保持值');
});

check('historical machine restore uses actual UI save and retains the old software/basis and history', async h => {
  const b = branch(h, 'machine-01'), before = b.headId, old = record(file(h), 'r-m11');
  const oldSnapshot = JSON.stringify(old), count = file(h).revisions.length, reviews = h.M.state.reviews.length;
  h.route('#/files/f-machine/parameters?branch=machine-01&revision=r-m11');
  await h.action('restore', { revision: old.id }); await h.action('confirm-restore');
  assert.equal(b.headId, before); assert.equal(h.S.edit.restoreRevisionId, old.id);
  assert.equal(h.S.edit.headId, before); assert.equal(h.S.edit.sourceRevisionId, old.id);
  assert(h.query('[data-action="commit-edit"]', 'dialog')); assert(h.query('[data-action="submit-review"]', 'dialog'));
  assert.equal(h.query('#save-message').value, '', 'Generated restore description must not satisfy the reason requirement');
  explain(h, '现场复验要求恢复此前稳定配置，并保留恢复前版本供追溯', evidenceLinks);
  await h.action('commit-edit');
  const saved = record(file(h), b.headId);
  assert.notEqual(saved.id, before); assert.equal(file(h).revisions.length, count + 1);
  assert.equal(saved.releaseId, '690'); assert.equal(b.releaseId, '690');
  assert.equal(saved.baseRevisionId, 'r-b10'); assert.equal(b.baseRevisionId, 'r-b10');
  assert.equal(JSON.stringify(saved.params), JSON.stringify(old.params));
  assert.equal(JSON.stringify(record(file(h), old.id)), oldSnapshot); assert(record(file(h), before));
  assert.equal(h.M.state.reviews.length, reviews); assert.equal(h.S.edit, null);
  checkRevisionEvidence(h, saved, '现场复验要求恢复此前稳定配置，并保留恢复前版本供追溯');
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
  for (const [name, run, options] of checks) {
    let h;
    try { h = createHarness(options); await run(h); h.clean(); console.log('PASS ' + name); }
    catch (error) { failed++; console.error('FAIL ' + name + '\n' + error.stack); }
    finally { h?.dispose(); }
  }
  console.log(`\n${checks.length - failed}/${checks.length} Node VM UI integration checks passed. No browser, layout, accessibility or visual verification.`);
  if (failed) process.exitCode = 1;
})();
