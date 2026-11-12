'use strict';
// Pure projection, HTML and provenance checks. No browser/layout/a11y claims.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const Model = require('./prototype/model.js');
const R = require('./prototype/roadmap.js');
const tests = [], test = (name, run) => tests.push([name, run]);
const copy = value => JSON.parse(JSON.stringify(value));
const esc = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const file = m => m.getFile('f-machine');
const rev = (f, id) => f.revisions.find(r => r.id === id);
const branch = (f, id) => f.branches.find(b => b.id === id);
const edgeIds = list => list.map(e => JSON.stringify([e.childId, e.parentId])).sort();
const expectedEdges = f => f.revisions.flatMap(r => [...new Set(r.parentIds)].map(id => JSON.stringify([r.id, id]))).sort();
const unique = values => [...new Set(values)];
const family = (g, label) => g.families.find(f => f.label === label);
const plot = (m, options) => R.layout(file(m), Object.assign({ assets: m.state.assets }, options));

function fixture(saves = 40, machines = 25) {
  const f = { id: 'window-fixture', name: 'Window.json', branches: [{ id: 'base', kind: 'baseline', label: '版本基线', releaseId: '691', headId: 'b' + saves }], revisions: [] };
  for (let i = 1; i <= saves; i++) f.revisions.push({ id: 'b' + i, label: 'B' + i, branchId: 'base', releaseId: '691', parentIds: i === 1 ? [] : ['b' + (i - 1)], at: '2026-11-01T00:00:00Z', reason: '基线参数核对 ' + i, authorName: '林工' });
  for (let i = 0; i < machines; i++) {
    f.branches.push({ id: 'machine-' + i, kind: 'machine', label: 'MC-' + i, releaseId: '691', headId: 'm' + i, machineId: 'asset-' + i });
    f.revisions.push({ id: 'm' + i, label: 'r1', branchId: 'machine-' + i, releaseId: '691', parentIds: ['b' + saves], baseRevisionId: 'b' + saves, at: '2026-11-02T00:00:00Z', reason: '机台确认采用基线 ' + i, authorName: '王工' });
  }
  return f;
}
function assertActualProjection(graph, f) {
  const actual = new Set(expectedEdges(f));
  for (const edge of graph.visibleEdges) {
    assert(edge.originalEdgeIds.length > 0);
    assert.equal(edge.count, edge.originalEdgeIds.length);
    for (const id of edge.originalEdgeIds) assert(actual.has(id), 'projected connectors only aggregate actual parent edges');
    assert(!/NaN|undefined|Infinity/.test(edge.path));
  }
  assertRouteGeometry(graph);
}
function assertRouteGeometry(graph) {
  const visible = new Map(graph.visibleNodes.map(n => [n.id, n])), projected = new Map(graph.projectedNodes.map(n => [n.id, n]));
  for (const edge of graph.visibleEdges) {
    const parent = visible.get(edge.parentNodeId), child = visible.get(edge.childNodeId);
    if (parent) assert.deepEqual(edge.startPort, { x: parent.x + parent.width, y: parent.y + parent.height / 2 }, 'visible parent exits its right edge');
    if (child) assert.deepEqual(edge.endPort, { x: child.x, y: child.y + child.height / 2 }, 'visible child enters its left edge');
    if (edge.parentNodeId && edge.childNodeId) assert(projected.get(edge.parentNodeId).columnIndex < projected.get(edge.childNodeId).columnIndex, 'every complete projected dependency moves forward');
    assert(edge.startPort.x < edge.endPort.x);
    assert(edge.routeSegments.length > 0);
    for (const segment of edge.routeSegments) {
      assert(segment.to.x >= segment.from.x, 'route must not double back horizontally');
      if (segment.control) assert(segment.control.x >= segment.from.x && segment.control.x <= segment.to.x);
      const points = [segment.from, ...(segment.control ? [segment.control] : []), segment.to];
      const xs = points.map(p => p.x), ys = points.map(p => p.y);
      const box = { left: Math.min(...xs), right: Math.max(...xs), top: Math.min(...ys), bottom: Math.max(...ys) };
      // A quadratic stays inside its control-point convex hull. Checking this
      // bounding box is stricter than sampling routePoints or the curve itself.
      for (const card of graph.visibleNodes) {
        if (card.id === edge.parentNodeId || card.id === edge.childNodeId) continue;
        const overlaps = box.right > card.x + 0.01 && box.left < card.x + card.width - 0.01 && box.bottom > card.y + 0.01 && box.top < card.y + card.height - 0.01;
        assert(!overlaps, 'actual line/quadratic must not cross card ' + card.id + ' on edge ' + edge.id);
      }
    }
  }
}

test('canonical identities, topology and adopted bases remain immutable across views', () => {
  const m = Model.create(), f = file(m), before = JSON.stringify(f), g = plot(m);
  assert.equal(g.nodes.length, f.revisions.length);
  assert.equal(new Set(g.nodes.map(n => n.id)).size, f.revisions.length);
  assert.deepEqual(edgeIds(g.edges), expectedEdges(f));
  assert(g.nodes.filter(n => branch(f, n.branchId).kind === 'baseline').every(n => n.laneId === 'baseline'));
  assert.equal(g.lanes.find(l => l.id === 'machine-roadmap-03').model, 'MX-820');
  assert(g.nodes.find(n => n.id === 'r-b11').adoptedBy.some(b => b.branchId === 'machine-01'));
  for (const e of g.edges) assert(g.nodes.find(n => n.id === e.childId).row < g.nodes.find(n => n.id === e.parentId).row);
  const skewed = copy(f); rev(skewed, 'r-b10').at = '2099-01-01T00:00:00Z';
  const skew = R.layout(skewed);
  for (const e of skew.edges) assert(skew.nodes.find(n => n.id === e.childId).row < skew.nodes.find(n => n.id === e.parentId).row);
  for (const release of g.releases) {
    const detail = plot(m, { level: 'history', releaseId: release.id });
    const occupied = detail.visibleNodes.map(n => JSON.stringify([n.laneId, n.columnId]));
    assert.equal(new Set(occupied).size, occupied.length, 'history cards cannot overlap in a cell');
    assert.deepEqual(edgeIds(detail.edges), expectedEdges(f));
  }
  assert.equal(JSON.stringify(f), before, 'neither projection nor grouping mutates model snapshots');
});

test('families expand in place, minor and patch views drill down, and history contains only real records', () => {
  const m = Model.create(), f = file(m), overview = plot(m);
  assert.equal(overview.level, 'families');
  assert.deepEqual(unique(overview.columns.map(c => c.label)), ['69X', '6AX']);
  assert(overview.visibleNodes.every(n => n.kind === 'family-summary'));
  assert.equal(overview.visibleNodes.flatMap(n => n.revisionIds).length, f.revisions.length);
  const id = family(overview, '69X').id;
  const expanded = plot(m, { expandedFamilies: [id] });
  assert.deepEqual(unique(expanded.columns.map(c => c.kind + ':' + c.label)), ['release:690', 'release:691', 'family:6AX']);
  const releases = plot(m, { level: 'releases', familyId: id });
  assert.deepEqual(unique(releases.columns.map(c => c.minorId)), ['minor-2026-09-690', 'minor-2026-09-691']);
  assert(releases.visibleNodes.every(n => n.kind === 'release-summary'));
  const patches = plot(m, { level: 'patches', minorId: 'minor-2026-10-6A1' });
  assert.deepEqual(unique(patches.columns.map(c => c.label)), ['原版', 'SPC001']);
  assert(patches.visibleNodes.every(n => n.kind === 'patch-summary'));
  const patchHTML = R.render({ file: f, level: 'patches', minorId: 'minor-2026-10-6A1' });
  assert(patchHTML.includes('data-action="roadmap-open-history"'));
  assert(!patchHTML.includes('data-roadmap-record='));
  const history = plot(m, { level: 'history', familyId: id, releaseId: '691' });
  assert(history.visibleNodes.every(n => n.kind === 'revision' && rev(f, n.id)?.releaseId === '691'));
  assert.equal(history.visibleNodes.length, f.revisions.filter(r => r.releaseId === '691').length);
  const overviewHTML = R.render({ file: f, assets: m.state.assets });
  assert(!overviewHTML.includes('data-roadmap-record='));
  assert(!overviewHTML.includes('data-action="roadmap-select"'));
  assert(overviewHTML.includes('data-action="roadmap-open-family"'));
  assert(overviewHTML.includes('aria-expanded="false"'));
  const historyHTML = R.render({ file: f, level: 'history', releaseId: '691', selectedRevisionId: 'r-m13' });
  assert(historyHTML.includes('data-roadmap-record="r-m13"'));
  assert(historyHTML.includes('aria-pressed="true"'));
  assert(!/第\d+步/.test(historyHTML), 'layout indices must not masquerade as saved versions');
  assert(historyHTML.includes('软件 691 · 保存记录'));
  assert(!/>B\d+</.test(historyHTML) && !/>r\d+</.test(historyHTML));
  assert.deepEqual(plot(m).columns, overview.columns, 'returning to overview restores the same version grouping');
});

test('summary connectors preserve exact edge sets and hide only internal real edges', () => {
  const m = Model.create(), f = file(m), overview = plot(m);
  assertActualProjection(overview, f);
  const represented = [...overview.hiddenInternalEdgeIds, ...overview.visibleEdges.flatMap(e => e.originalEdgeIds)].sort();
  assert.deepEqual(represented, expectedEdges(f));
  assert.equal(new Set(represented).size, represented.length);
  assert(overview.hiddenInternalEdgeIds.length > 0);
  const expanded = plot(m, { expandedFamilies: overview.families.map(f => f.id) });
  assertActualProjection(expanded, f);
  const again = [...expanded.hiddenInternalEdgeIds, ...expanded.visibleEdges.flatMap(e => e.originalEdgeIds)].sort();
  assert.deepEqual(again, expectedEdges(f));
  for (const edge of expanded.visibleEdges) assert.notEqual(edge.childNodeId, edge.parentNodeId);
});

test('restoring an older machine release repeats that version later and keeps causal edges forward', () => {
  const m = Model.create(), f = file(m), target = rev(f, 'r-m11');
  const saved = m.save({ fileId: f.id, branchId: 'machine-01', expectedHeadId: 'r-m13', params: target.params, sourceRevisionId: target.id, restoreRevisionId: target.id, reason: '恢复旧软件配置以验证当前回归问题' });
  branch(f, 'machine-01').baseRevisionId = 'r-b12'; // Deliberately stale mutable metadata.
  const g = plot(m, { level: 'releases', familyId: 'family-69x' });
  assert(g.nodes.find(n => n.id === 'r-b10').adoptedBy.some(b => b.branchId === 'machine-01'));
  assert(!g.nodes.find(n => n.id === 'r-b12').adoptedBy.some(b => b.branchId === 'machine-01'));
  const back = g.visibleEdges.find(e => e.originalEdges.some(e => e.childId === saved.id && e.parentId === 'r-m13'));
  assert(back);
  const child = g.projectedNodes.find(n => n.id === back.childNodeId), parent = g.projectedNodes.find(n => n.id === back.parentNodeId);
  assert(child.columnIndex > parent.columnIndex, 'a restore into 690 follows its 691 parent without changing either software version');
  assert.equal(child.minorId, 'minor-2026-09-690');
  assert(g.columns.some(c => c.minorId === child.minorId && c.index < parent.columnIndex), 'the older software label remains visible in its original position as well');
  assertActualProjection(g, f);
  const located = R.locateRevision(f, saved.id, { assets: m.state.assets });
  assert.equal(located.releaseId, '690');
  const html = R.render(Object.assign({ file: f, assets: m.state.assets }, located));
  assert(html.includes('采用 690 · 第10次保存'));
});

test('pending releases keep empty headers and initialization never invents saved ancestry', () => {
  const m = Model.create(), f = file(m), count = f.revisions.length;
  const pending = m.addRelease(f.id, { releaseId: '692', reason: '登记待确认初始配置的软件版本' });
  const empty = plot(m, { level: 'history', releaseId: '692' });
  assert.equal(empty.nodes.length, count);
  assert.equal(empty.visibleNodes.length, 0);
  assert.equal(empty.columns.length, 1);
  assert.equal(empty.columns[0].step, null);
  const html = R.render({ file: f, level: 'history', releaseId: '692' });
  assert(html.includes('data-roadmap-release="692"') && html.includes('设置初始配置'));
  assert(!html.includes('data-roadmap-record='));
  const saved = m.save({ fileId: f.id, branchId: pending.id, expectedHeadId: null, params: rev(f, 'r-b12').params, reason: '手工核对并建立此软件版本首份配置' });
  assert.deepEqual(saved.parentIds, []);
  assert(!plot(m).edges.some(e => e.childId === saved.id));
  const derived = m.addRelease(f.id, { releaseId: '693', sourceRevisionId: 'r-b12', reason: '从已确认记录显式建立新软件配置' });
  const g = plot(m, { level: 'history', releaseId: '693' });
  assert(g.nodes.some(n => n.id === derived.initialRevisionId && n.kind === 'initial'));
  assert(g.edges.some(e => e.childId === derived.initialRevisionId && e.parentId === 'r-b12'));
  assert(g.edges.some(e => e.childId === derived.headId && e.parentId === derived.initialRevisionId));
  assert.equal(R.formatRevision(f, derived.initialRevisionId), '初始文件 · 软件 693');
  assertActualProjection(g, f);
});

test('both window axes are bounded, baseline repeats, and all saved records remain reachable', () => {
  const f = fixture(), first = R.layout(f, { level: 'history', releaseId: '691' }), seen = new Set(), seenMachines = new Set();
  assert(first.columnPageCount > 1 && first.lanePageCount > 1);
  for (let columnPage = 0; columnPage < first.columnPageCount; columnPage++) {
    for (let lanePage = 0; lanePage < first.lanePageCount; lanePage++) {
      const g = R.layout(f, { level: 'history', releaseId: '691', columnPage, lanePage });
      assert.equal(g.visibleLanes[0].id, 'baseline');
      assert(g.visibleColumns.length <= 12 && g.visibleLanes.length <= 13);
      assert(g.visibleNodes.length <= 12 * 13);
      g.visibleLanes.filter(l => l.kind === 'machine').forEach(l => seenMachines.add(l.id));
      g.visibleNodes.forEach(n => seen.add(n.id));
      assertActualProjection(g, f);
    }
  }
  assert.equal(seen.size, f.revisions.length); assert.equal(seenMachines.size, 25);
  const clamped = R.layout(f, { level: 'history', releaseId: '691', columnLimit: 10000, laneLimit: 10000, columnPage: 10000, lanePage: 10000 });
  assert.equal(clamped.columnLimit, 30); assert.equal(clamped.laneLimit, 30);
  assert.equal(clamped.columnPage, clamped.columnPageCount - 1);
  const html = R.render({ file: f, level: 'history', releaseId: '691' });
  assert(html.includes('id="roadmap-column-limit"') && html.includes('id="roadmap-lane-limit"'));
  const long = fixture(3000, 1), last = R.locateRevision(long, 'b3000', {});
  const window = R.layout(long, last);
  assert(window.visibleNodes.some(n => n.id === 'b3000'));
  assert(window.visibleNodes.length <= 12 * 13);
  const rendered = R.render(Object.assign({ file: long }, last), window);
  assert.equal((rendered.match(/data-roadmap-record=/g) || []).length, window.visibleNodes.length);
});

test('locate opens exact history context and resolves both pages without changing all scope', () => {
  const f = fixture(), target = R.locateRevision(f, 'm24', { level: 'families', query: 'hidden-query', branchFilter: 'all' });
  assert.equal(target.level, 'history'); assert.equal(target.releaseId, '691');
  assert.equal(target.branchFilter, 'all'); assert.equal(target.query, '');
  assert(target.columnPage > 0 && target.lanePage > 0);
  assert(R.layout(f, target).visibleNodes.some(n => n.kind === 'revision' && n.id === 'm24'));
  const outside = R.locateRevision(f, 'm24', { branchFilter: 'machine-0' });
  assert.equal(outside.branchFilter, 'all');
  const inside = R.locateRevision(f, 'm24', { branchFilter: 'machine-24' });
  assert.equal(inside.branchFilter, 'machine-24');
  assert.equal(inside.lanePage, 0);
  const m = Model.create();
  for (const r of file(m).revisions) {
    const found = R.locateRevision(file(m), r.id, { assets: m.state.assets, columnLimit: 2, laneLimit: 1 });
    assert(R.layout(file(m), found).visibleNodes.some(n => n.id === r.id));
  }
  assert.throws(() => R.locateRevision(f, 'missing', {}), e => e.code === 'REVISION_NOT_FOUND');
});

test('machine focus keeps only its true ancestry rows; query omits endpoints without shortcut edges', () => {
  const f = fixture(2, 80);
  f.revisions.push({ id: 'merge79', label: 'r2', branchId: 'machine-79', releaseId: '691', parentIds: ['m79', 'm12'], baseRevisionId: 'b2', reason: 'selected-target', at: '2026-11-03T00:00:00Z' });
  branch(f, 'machine-79').headId = 'merge79';
  const all = R.layout(f), focused = R.layout(f, { branchFilter: 'machine-79' });
  assert.equal(all.lanes.length, 81); assert.equal(focused.lanes.length, 81);
  assert.deepEqual(focused.projectedLanes.map(l => l.id), ['baseline', 'machine-12', 'machine-79']);
  assert.equal(focused.visibleLanes.length, 3);
  assert.deepEqual(edgeIds(focused.edges), expectedEdges(f));
  const searched = R.layout(f, { level: 'history', releaseId: '691', branchFilter: 'machine-79', query: 'selected-target' });
  assert.deepEqual(searched.matchingIds, ['merge79']);
  assert.equal(searched.visibleNodes.length, 1);
  assert.equal(searched.visibleEdges.flatMap(e => e.originalEdgeIds).length, 2);
  assert(searched.visibleEdges.every(e => e.boundary === 'filtered-parent'));
  assert(!searched.visibleEdges.some(e => e.originalEdges.some(e => e.childId === 'merge79' && e.parentId === 'b2')));
  assertActualProjection(searched, f);
  const baseline = R.layout(f, { branchFilter: 'baseline' });
  assert.deepEqual(baseline.projectedLanes.map(l => l.id), ['baseline']);
});

test('explicit version IDs and display labels take precedence; unknown families are not guessed', () => {
  const f = {
    id: 'metadata', releaseFamilies: [{ id: 'release-family', label: '69X', order: 2 }, { id: 'other-family', label: '6AX', order: 1 }],
    releases: [{ id: 'rel-uuid', version: '691', familyId: 'release-family', order: 9 }, { id: 'another-id', version: '6A0', label: '6A0 正式版', familyId: 'other-family', order: 1 }],
    branches: [{ id: 'b', label: '基线', kind: 'baseline', headId: 'r', releaseId: 'rel-uuid' }, { id: 'm', label: 'MC-X', kind: 'machine', headId: 'mr', releaseId: 'rel-uuid', machineId: 'asset' }],
    revisions: [{ id: 'r', branchId: 'b', releaseId: 'rel-uuid', label: 'B7', parentIds: [] }, { id: 'mr', branchId: 'm', releaseId: 'rel-uuid', label: 'r1', parentIds: ['r'], baseRevisionId: 'r' }]
  };
  const g = R.layout(f, { assets: [{ id: 'asset', name: 'MC-X', model: 'MX-820' }] });
  assert.deepEqual(g.families.map(f => f.label), ['6AX', '69X']);
  assert.equal(R.formatRevision(f, 'r'), '版本基线 · 软件 691 · 第7次保存');
  assert.equal(R.formatRevision(f, 'mr'), 'MC-X · 软件 691 · 第1次保存');
  assert(!g.nodes.find(n => n.id === 'r').title.includes('rel-uuid'));
  assert.equal(g.lanes.find(l => l.id === 'm').model, 'MX-820');
  const html = R.render({ file: f, level: 'history', releaseId: 'rel-uuid' });
  assert(html.includes('软件 691 · 保存记录'));
  assert(!html.includes('软件 rel-uuid'));
  const fallback = fixture(1, 0);
  fallback.branches[0].releaseId = 'ZZ-2026'; fallback.revisions[0].releaseId = 'ZZ-2026';
  assert.equal(R.layout(fallback).families[0].label, '未分组');
  fallback.branches[0].releaseId = '6A9'; fallback.revisions[0].releaseId = '6A9';
  assert.equal(R.layout(fallback).families[0].label, '6AX');
});

test('HTML escapes metadata and identifiers and only safe related URLs become links', () => {
  const m = Model.create(), f = copy(file(m)), payload = String.fromCharCode(34) + '<img src=x onerror=alert(1)><script>bad()</script>&' + String.fromCharCode(39);
  f.id += payload;
  branch(f, 'machine-01').label = 'MC-' + payload;
  const r = rev(f, 'r-m13'), old = r.id; r.id += payload; branch(f, 'machine-01').headId = r.id;
  f.revisions.forEach(x => { x.parentIds = x.parentIds.map(id => id === old ? r.id : id); });
  r.reason = payload; r.links = [{ url: 'javascript:alert(1)', label: 'unsafe' }, { url: 'https://user:pass@example.test', label: 'credential' }, { url: 'https://example.test/\nunsafe', label: 'control' }, { url: 'https://example.test/task?a=1&b=2', label: payload }, 'https://example.test/second'];
  const opts = Object.assign({ file: f, actorLabel: () => payload }, R.locateRevision(f, r.id));
  const html = R.render(opts);
  assert(!/<script\b|<img\b/i.test(html));
  assert(html.includes(esc(payload)));
  assert(html.includes('data-roadmap-record="' + esc(r.id) + '"'));
  assert(!html.includes('href="javascript:') && !html.includes('href="https://user:'));
  assert(html.includes('href="https://example.test/task?a=1&amp;b=2"'));
  assert(html.includes('+1 链接'));
  const summary = R.render({ file: f, actorLabel: () => payload });
  assert(!/<script\b|<img\b/i.test(summary)); assert(summary.includes(esc(payload)));
  const query = R.render({ file: f, query: payload });
  assert(query.includes('value="' + esc(payload) + '"'));
});

test('legacy descriptions remain visibly distinct from explicit change reasons', () => {
  const f = copy(file(Model.create())), old = rev(f, 'r-m13');
  old.reason = null; old.message = '旧系统保存说明';
  let html = R.render({ file: f, level: 'history', releaseId: '691', getReason: r => r.reason || r.message || '' });
  assert(html.includes('原因未记录 · 历史说明：旧系统保存说明'));
  old.reason = '已确认的新修改原因';
  html = R.render({ file: f, level: 'history', releaseId: '691' });
  assert(html.includes('已确认的新修改原因'));
  assert(!html.includes('原因未记录 · 历史说明：已确认的新修改原因'));
});

test('unknown ancestry and malformed DAGs remain explicit without phantom records', () => {
  const f = copy(file(Model.create())); rev(f, 'r-b10').parentIds = ['not-loaded'];
  const g = R.layout(f, { level: 'history', releaseId: '690' });
  assert.equal(g.nodes.length, f.revisions.length);
  assert(g.visibleEdges.some(e => e.boundary === 'missing-parent'));
  assert(R.render({ file: f, level: 'history', releaseId: '690' }).includes('部分来源未载入'));
  const cycle = copy(file(Model.create())); rev(cycle, 'r-b10').parentIds = ['r-b12'];
  assert.throws(() => R.layout(cycle), e => e.code === 'INVALID_DAG');
  assert(R.render({ file: cycle }).includes('保存关系需要核对'));
  const duplicate = copy(file(Model.create())); duplicate.revisions.push(copy(duplicate.revisions[0]));
  assert.throws(() => R.layout(duplicate), e => e.code === 'DUPLICATE_REVISION');
  const invalid = copy(file(Model.create())); invalid.revisions[0].parentIds = null;
  assert.throws(() => R.layout(invalid), e => e.code === 'INVALID_PARENTS');
  assert(R.render({ file: { id: 'empty', branches: [], revisions: [] } }).includes('还没有保存记录'));
  assert(R.render({ file: f, query: 'no-matching-saved-record' }).includes('没有匹配的保存记录'));
});

test('render accepts its prepared graph and emits one shared matrix with semantic header rows', () => {
  const m = Model.create(), opts = { file: file(m), assets: m.state.assets, expandedFamilies: ['family-69x'] }, graph = R.layout(opts.file, opts);
  assert.equal(R.render(opts, graph), R.render(opts));
  const html = R.render(opts, graph);
  assert.equal((html.match(/data-roadmap-scroll/g) || []).length, 1);
  assert(html.includes('data-roadmap-corner role="columnheader" aria-colindex="1" aria-rowspan="2"'));
  assert.equal((html.match(/class="rm-axis-row" role="row"/g) || []).length, 2);
  assert(html.includes('aria-controls=') && html.includes('aria-expanded="true"'));
  assert(html.includes('data-roadmap-lane-header='));
  assert(!html.includes('id="roadmap-column-limit"') && !html.includes('id="roadmap-lane-limit"'), 'small views retain range summaries without unnecessary pagination controls');
  const css = fs.readFileSync(require.resolve('./prototype/roadmap.css'), 'utf8');
  assert(css.includes('scroll-padding-left:176px') && css.includes('scroll-padding-top:88px'));
  assert(css.includes('.rm-axis{position:sticky;top:0') && css.includes('.rm-lane-head{position:sticky;left:0'));
  // These are static hooks, not proof of scrolling, overlap, focus or visual quality.
});

test('summary forks followed by merges split into causal cards instead of creating an aggregate cycle', () => {
  const f = fixture(1, 2);
  f.revisions.push({ id: 'b2', label: 'B2', branchId: 'base', releaseId: '691', parentIds: ['b1', 'm0'], reason: '采用机台已验证修改', at: '2026-11-03T00:00:00Z' });
  f.revisions.push({ id: 'm0-next', label: 'r2', branchId: 'machine-0', releaseId: '691', parentIds: ['m0', 'b2'], baseRevisionId: 'b2', reason: '机台采用更新后的基线', at: '2026-11-04T00:00:00Z' });
  f.revisions.push({ id: 'b3', label: 'B3', branchId: 'base', releaseId: '691', parentIds: ['b2', 'm0-next', 'm1'], reason: '核对多台机台的共同修改', at: '2026-11-05T00:00:00Z' });
  branch(f, 'base').headId = 'b3'; branch(f, 'machine-0').headId = 'm0-next';
  const before = JSON.stringify(f), g = R.layout(f);
  assert(g.projectedNodes.filter(n => n.laneId === 'baseline').length >= 3);
  assert(g.projectedNodes.filter(n => n.laneId === 'machine-0').length >= 2);
  assert.deepEqual([...g.hiddenInternalEdgeIds, ...g.visibleEdges.flatMap(e => e.originalEdgeIds)].sort(), expectedEdges(f));
  assert.deepEqual(g.projectedNodes.flatMap(n => n.revisionIds).sort(), f.revisions.map(r => r.id).sort());
  assertActualProjection(g, f);
  assert.equal(JSON.stringify(f), before, 'splitting a displayed summary cannot rewrite original records');
});

test('dense multi-parent routes and their actual quadratic corners avoid intervening cards in filtered windows', () => {
  const f = fixture(6, 4);
  rev(f, 'b6').parentIds.push('b1'); // A true long edge must bypass b2–b5.
  f.revisions.push({ id: 'm0-next', label: 'r2', branchId: 'machine-0', releaseId: '691', parentIds: ['m0', 'm2'], baseRevisionId: 'b6', reason: '跨机台核对选定参数', at: '2026-11-03T00:00:00Z' });
  f.revisions.push({ id: 'b7', label: 'B7', branchId: 'base', releaseId: '691', parentIds: ['b6', 'm0-next', 'm1', 'm3'], reason: '采纳多台机台共同验证的修改', at: '2026-11-04T00:00:00Z' });
  f.revisions.push({ id: 'm3-next', label: 'r2', branchId: 'machine-3', releaseId: '691', parentIds: ['m3', 'b7'], baseRevisionId: 'b7', reason: '应用核对后的基线', at: '2026-11-05T00:00:00Z' });
  branch(f, 'base').headId = 'b7'; branch(f, 'machine-0').headId = 'm0-next'; branch(f, 'machine-3').headId = 'm3-next';
  const full = R.layout(f, { level: 'history', releaseId: '691' });
  assert(full.visibleEdges.some(e => e.routeSegments.some(s => s.kind === 'quadratic')));
  assertActualProjection(full, f);
  const long = full.visibleEdges.find(e => e.originalEdges.some(e => e.childId === 'b6' && e.parentId === 'b1'));
  assert(long.routeSegments.length > 1, 'long edges must use a gap instead of crossing intervening baseline cards');
  for (const options of [
    { columnLimit: 3, columnPage: 1, laneLimit: 2, lanePage: 1 },
    { columnLimit: 3, columnPage: 2, laneLimit: 2, lanePage: 0 },
    { query: '跨机台核对选定参数' },
    { branchFilter: 'machine-3', columnLimit: 4, columnPage: 1 }
  ]) assertActualProjection(R.layout(f, Object.assign({ level: 'history', releaseId: '691' }, options)), f);
});

test('explicit minor and SPC catalogs keep original/SPC001/SPC002 distinct and disambiguate repeated family names', () => {
  const f = {
    id: 'spc-fixture', name: 'Patch.json',
    releaseFamilies: [{ id: 'f26', label: '69X', year: 2026, month: 9, monthKey: '2026-09', order: 1 }, { id: 'f27', label: '71X', year: 2027, month: 1, monthKey: '2027-01', order: 2 }, { id: 'f36', label: '69X', year: 2036, month: 9, monthKey: '2036-09', order: 3 }],
    releaseMinors: [{ id: 'minor-uuid', version: '692', familyId: 'f26', order: 1 }, { id: 'minor-future', version: '710', familyId: 'f27', order: 2 }, { id: 'minor-next-decade', version: '692', familyId: 'f36', order: 3 }],
    releases: [{ id: 'original-uuid', version: '692', label: '692', familyId: 'f26', minorId: 'minor-uuid', patchNumber: null, order: 1 }, { id: 'patch-one-uuid', version: '692SPC001', label: '692SPC001', familyId: 'f26', minorId: 'minor-uuid', patchNumber: 1, order: 2 }, { id: 'patch-two-uuid', version: '692SPC002', label: '692SPC002', familyId: 'f26', minorId: 'minor-uuid', patchNumber: 2, order: 3 }, { id: 'only-patch-uuid', version: '692SPC002', label: '692SPC002', familyId: 'f36', minorId: 'minor-next-decade', patchNumber: 2, order: 4 }],
    branches: [{ id: 'base0', kind: 'baseline', releaseId: 'original-uuid', headId: 'save12' }, { id: 'base1', kind: 'baseline', releaseId: 'patch-one-uuid', headId: 'save13' }, { id: 'base2', kind: 'baseline', releaseId: 'patch-two-uuid', headId: 'save14' }, { id: 'base-empty', kind: 'baseline', releaseId: 'only-patch-uuid', headId: null }],
    revisions: [{ id: 'save12', label: 'B12', branchId: 'base0', releaseId: 'original-uuid', parentIds: [], reason: '确认软件原版配置' }, { id: 'save13', label: 'B13', branchId: 'base1', releaseId: 'patch-one-uuid', parentIds: ['save12'], reason: '验证首个补丁参数' }, { id: 'save14', label: 'B14', branchId: 'base2', releaseId: 'patch-two-uuid', parentIds: ['save13'], reason: '验证第二个补丁参数' }]
  };
  const overview = R.layout(f);
  assert.deepEqual(overview.families.map(x => x.displayLabel), ['69X · 2026-09', '71X', '69X · 2036-09']);
  const patchView = R.layout(f, { level: 'patches', minorId: 'minor-uuid' });
  assert.deepEqual(patchView.columns.map(c => c.label), ['原版', 'SPC001', 'SPC002']);
  assert.deepEqual(patchView.patches.map(r => r.id), ['original-uuid', 'patch-one-uuid', 'patch-two-uuid']);
  assert(patchView.visibleNodes.every(n => n.kind === 'patch-summary'));
  assertActualProjection(patchView, f);
  const located = R.locateRevision(f, 'save13');
  assert.equal(located.minorId, 'minor-uuid'); assert.equal(located.releaseId, 'patch-one-uuid');
  const html = R.render(Object.assign({ file: f }, located));
  assert(html.includes('软件 692SPC001 · 保存记录') && html.includes('第13次保存'));
  assert(!html.includes('SPC013') && !html.includes('软件 patch-one-uuid'));
  const onlyPatch = R.layout(f, { level: 'patches', minorId: 'minor-next-decade' });
  assert.equal(onlyPatch.minor.originalReleaseId, null);
  assert.deepEqual(onlyPatch.columns.map(c => c.label), ['SPC002']);
  assert.equal(onlyPatch.visibleNodes.length, 0);
  const pending = R.render({ file: f, level: 'patches', minorId: 'minor-next-decade' });
  assert(pending.includes('692SPC002 · 尚未初始化') && !pending.includes('SPC000'));
  assert.equal(f.revisions.length, 3, 'the catalog must not synthesize an original record for a patch-only minor');
});

let failed = 0;
for (const [name, run] of tests) {
  try { run(); console.log('PASS ' + name); }
  catch (error) { failed++; console.error('FAIL ' + name + '\n' + error.stack); }
}
console.log((tests.length - failed) + '/' + tests.length + ' roadmap data/HTML checks passed (not browser QA)');
if (failed) process.exitCode = 1;
