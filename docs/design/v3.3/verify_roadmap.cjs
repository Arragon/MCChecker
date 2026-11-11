'use strict';
// Pure data/HTML verification. This is not browser, layout or accessibility QA.
const assert = require('node:assert/strict');
const Model = require('./prototype/model.js');
const Roadmap = require('./prototype/roadmap.js');
const tests = [];
const test = (name, run) => tests.push([name, run]);
const copy = value => JSON.parse(JSON.stringify(value));
const escape = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const file = model => model.getFile('f-machine');
const revision = (f, id) => f.revisions.find(r => r.id === id);
const branch = (f, id) => f.branches.find(b => b.id === id);
const edgeSet = edges => edges.map(e => JSON.stringify([e.childId, e.parentId])).sort();
const expectedEdges = f => f.revisions.flatMap(r => [...new Set(r.parentIds)].map(id => JSON.stringify([r.id, id]))).sort();

test('seed records have unique positions, one baseline lane and separate machine lanes', () => {
  const f = file(Model.create()), before = JSON.stringify(f), graph = Roadmap.layout(f);
  assert.equal(graph.nodes.length, f.revisions.length);
  assert.equal(new Set(graph.nodes.map(n => n.id)).size, graph.nodes.length);
  assert.equal(new Set(graph.nodes.map(n => n.row)).size, graph.nodes.length);
  assert.equal(graph.lanes.length, 3);
  for (const node of graph.nodes) {
    const b = branch(f, node.branchId);
    assert.equal(node.laneId, b.kind === 'baseline' ? 'baseline' : b.id);
    assert.equal(node.y, node.row * graph.rowHeight + graph.rowHeight / 2);
    assert.equal(node.isHead, f.branches.some(b => b.headId === node.id));
  }
  assert.equal(graph.nodes.find(n => n.id === 'r-b10').laneIndex, 0);
  assert.equal(graph.nodes.find(n => n.id === 'r-b12').laneIndex, 0);
  assert.notEqual(graph.nodes.find(n => n.id === 'r-m13').laneIndex, graph.nodes.find(n => n.id === 'r-m22').laneIndex);
  assert.equal(JSON.stringify(f), before, 'layout must not mutate snapshots or branch metadata');
});

test('every connector is an actual parent edge and child order survives clock skew', () => {
  const f = copy(file(Model.create()));
  revision(f, 'r-b10').at = '2099-01-01T00:00:00Z';
  const graph = Roadmap.layout(f), nodes = new Map(graph.nodes.map(n => [n.id, n]));
  assert.deepEqual(edgeSet(graph.edges), expectedEdges(f));
  for (const edge of graph.edges) assert(nodes.get(edge.childId).row < nodes.get(edge.parentId).row);
  assert.equal(graph.edges.find(e => e.childId === 'r-m11' && e.parentId === 'r-b10').kind, 'fork');
  assert.equal(graph.edges.find(e => e.childId === 'r-m12' && e.parentId === 'r-b11').kind, 'adopt');
  assert.equal(graph.edges.filter(e => e.childId === 'r-m12').length, 2);
  assert.equal(graph.edges.filter(e => e.childId === 'r-m13').length, 1);
});

test('software initialization stays on the main lane without inventing root ancestry', () => {
  const m = Model.create(), f = file(m), before = f.revisions.length;
  const pending = m.addRelease(f.id, { releaseId: '692', message: '登记等待配置输入的软件版本' });
  let graph = Roadmap.layout(f);
  assert.equal(graph.nodes.length, before);
  assert(graph.pendingBranches.some(b => b.id === pending.id));
  const first = m.save({ fileId: f.id, branchId: pending.id, expectedHeadId: null, params: revision(f, 'r-b12').params, message: '手工确认此软件版本的首份配置' });
  graph = Roadmap.layout(f);
  assert.deepEqual(first.parentIds, []);
  assert.equal(graph.nodes.find(n => n.id === first.id).laneId, 'baseline');
  assert(!graph.edges.some(e => e.childId === first.id), 'same lane does not imply an invented parent');
  assert(!graph.pendingBranches.some(b => b.id === pending.id));
  const derived = m.addRelease(f.id, { releaseId: '693', sourceRevisionId: 'r-b12', message: '采用已验证记录初始化软件版本' });
  graph = Roadmap.layout(f);
  const initial = revision(f, derived.initialRevisionId);
  assert.equal(initial.kind, 'initial');
  assert.equal(graph.nodes.find(n => n.id === initial.id).laneId, 'baseline');
  assert(graph.edges.some(e => e.childId === initial.id && e.parentId === 'r-b12'));
  assert(graph.edges.some(e => e.childId === derived.headId && e.parentId === initial.id && e.kind === 'initialize'));
  assert.deepEqual(edgeSet(graph.edges), expectedEdges(f));
  const fresh = m.initialize({ name: 'RoadmapFixture.json', format: 'json', releaseId: '700', params: revision(f, 'r-b12').params.slice(0, 1), message: '建立明确输入的测试文件' });
  const freshGraph = Roadmap.layout(fresh);
  assert.equal(freshGraph.nodes.length, 2); assert.equal(freshGraph.edges.length, 1);
  assert.equal(Roadmap.formatRevision(fresh, fresh.initialRevisionId), '初始文件 · 软件 700');
});

test('fork and historical restore preserve explicit provenance and current adoption', () => {
  const m = Model.create(), f = file(m);
  m.state.assets.push({ id: 'asset-03', name: 'MC-003', model: '69X' });
  const fork = m.fork(f.id, { machineId: 'asset-03', fromRevisionId: 'r-b10', message: '机台三采用经过确认的软件配置' });
  const old = revision(f, 'r-m11');
  const saved = m.save({ fileId: f.id, branchId: 'machine-01', expectedHeadId: 'r-m13', params: old.params, sourceRevisionId: old.id, restoreRevisionId: old.id, message: '恢复机台历史软件和配置以排查回归' });
  // A mutable branch's stale metadata must never replace the saved record's basis.
  branch(f, 'machine-01').baseRevisionId = 'r-b12';
  const graph = Roadmap.layout(f), b10 = graph.nodes.find(n => n.id === 'r-b10'), b12 = graph.nodes.find(n => n.id === 'r-b12');
  assert.equal(graph.lanes.length, 4);
  assert(graph.edges.some(e => e.childId === fork.headId && e.parentId === 'r-b10' && e.kind === 'fork'));
  assert(graph.edges.some(e => e.childId === saved.id && e.parentId === old.id && e.kind === 'restore'));
  assert.equal(graph.nodes.find(n => n.id === saved.id).revision.releaseId, '690');
  assert(b10.adoptedBy.some(b => b.branchId === 'machine-01'));
  assert(!b12.adoptedBy.some(b => b.branchId === 'machine-01'));
  assert.deepEqual(edgeSet(graph.edges), expectedEdges(f));
  const html = Roadmap.render({ file: f });
  assert(html.includes('采用 690 · 第10次保存'));
});

test('all long-history records remain reachable with bounded 30/50/100-row pages', () => {
  const f = { id: 'large', branches: [{ id: 'base', kind: 'baseline', label: '基线', releaseId: '691', headId: 'base-300' }], revisions: [] };
  for (let i = 0; i <= 300; i++) f.revisions.push({ id: 'base-' + i, label: 'B' + (i + 1), branchId: 'base', releaseId: '691', parentIds: i ? ['base-' + (i - 1)] : [], at: '2026-11-01T00:00:00Z', message: '分页保存 ' + i });
  for (let i = 0; i < 80; i++) {
    f.branches.push({ id: 'machine-' + i, kind: 'machine', label: 'MC-' + String(i).padStart(3, '0'), releaseId: '691', headId: 'machine-record-' + i });
    f.revisions.push({ id: 'machine-record-' + i, label: 'r1', branchId: 'machine-' + i, releaseId: '691', parentIds: ['base-200'], baseRevisionId: 'base-200', at: '2026-11-02T00:00:00Z', message: '从已确认基线建立机台' });
  }
  for (const pageSize of [30, 50, 100]) {
    const first = Roadmap.layout(f, { pageSize }), visited = [];
    assert.equal(first.lanes.length, 81); assert(first.graphWidth > 3000);
    for (let page = 0; page < first.pageCount; page++) {
      const graph = Roadmap.layout(f, { page, pageSize }), ids = new Set(graph.visibleNodes.map(n => n.id));
      assert(graph.visibleNodes.length <= pageSize);
      visited.push(...ids);
      for (const edge of graph.visibleEdges) {
        assert(ids.has(edge.childId) || ids.has(edge.parentId));
        assert(expectedEdges(f).includes(edge.id));
        assert(!/NaN|Infinity/.test(edge.path));
      }
      const html = Roadmap.render({ file: f, page, pageSize });
      assert.equal((html.match(/data-roadmap-row=/g) || []).length, graph.visibleNodes.length);
      assert(html.includes('id="roadmap-page-size"'));
    }
    assert.equal(visited.length, f.revisions.length);
    assert.equal(new Set(visited).size, f.revisions.length);
  }
  assert.equal(Roadmap.layout(f, { pageSize: 100000 }).visibleNodes.length, 100);
  assert.equal(Roadmap.layout(f, { page: 100000 }).page, Roadmap.layout(f).pageCount - 1);
});

test('query filtering marks omitted endpoints and focus preserves canonical full data', () => {
  const f = copy(file(Model.create()));
  for (const r of f.revisions) r.message = ['r-m13', 'r-b10'].includes(r.id) ? 'matching-special' : 'other';
  const graph = Roadmap.layout(f, { query: 'matching-special' });
  assert.deepEqual(new Set(graph.visibleNodes.map(n => n.id)), new Set(['r-m13', 'r-b10']));
  assert(graph.visibleEdges.some(e => e.childId === 'r-m13' && e.parentId === 'r-m12' && e.boundary === 'filtered-parent'));
  assert(!graph.visibleEdges.some(e => e.childId === 'r-m13' && e.parentId === 'r-b10'), 'never create a shortcut through hidden records');
  assert(Roadmap.render({ file: f, query: 'matching-special' }).includes('来源在筛选范围外'));
  const focused = Roadmap.layout(f, { branchFilter: 'machine-01' });
  assert.equal(focused.nodes.length, f.revisions.length);
  assert.equal(focused.visibleNodes.length, 5);
  for (const id of ['r-m11', 'r-m12', 'r-m13', 'r-b10', 'r-b11']) assert(focused.nodes.find(n => n.id === id).focused);
  assert(!focused.nodes.find(n => n.id === 'r-m22').focused);
  assert(!focused.nodes.find(n => n.id === 'r-b12').focused);
  assert(focused.visibleEdges.every(e => edgeSet(focused.edges).includes(e.id)));
});

test('machine focus compresses 81 tracks to the selected machine and actual source tracks', () => {
  const f = { id: 'focus-space', branches: [{ id: 'base', kind: 'baseline', label: '基线', releaseId: '691', headId: 'b2' }], revisions: [
    { id: 'b1', branchId: 'base', label: 'B1', releaseId: '691', parentIds: [], at: '2026-11-01T00:00:00Z' },
    { id: 'b2', branchId: 'base', label: 'B2', releaseId: '691', parentIds: ['b1'], at: '2026-11-02T00:00:00Z' }
  ] };
  for (let i = 0; i < 80; i++) {
    f.branches.push({ id: 'm' + i, label: 'MC-' + i, kind: 'machine', releaseId: '691', headId: 'mr' + i });
    f.revisions.push({ id: 'mr' + i, branchId: 'm' + i, label: 'r1', releaseId: '691', parentIds: ['b1'], baseRevisionId: 'b1', at: '2026-11-03T00:00:00Z' });
  }
  f.revisions.push({ id: 'merge79', branchId: 'm79', label: 'r2', releaseId: '691', parentIds: ['mr79', 'mr12'], baseRevisionId: 'b1', at: '2026-11-04T00:00:00Z', message: 'selected-merge' });
  branch(f, 'm79').headId = 'merge79';
  const all = Roadmap.layout(f), focused = Roadmap.layout(f, { branchFilter: 'm79' });
  assert.equal(all.visibleLanes.length, 81);
  assert.equal(focused.lanes.length, 81, 'canonical lanes remain available');
  assert.equal(focused.nodes.length, all.nodes.length);
  assert.deepEqual(edgeSet(focused.edges), edgeSet(all.edges));
  assert.deepEqual(focused.visibleLanes.map(l => l.id), ['baseline', 'm12', 'm79']);
  assert.deepEqual(focused.visibleLanes.map(l => l.index), [0, 1, 2]);
  assert.deepEqual(focused.visibleLanes.map(l => l.x), [28, 76, 124]);
  assert(focused.graphWidth < 200 && all.graphWidth > 3800);
  assert.deepEqual(new Set(focused.matchingIds), new Set(['merge79', 'mr79', 'mr12', 'b1']));
  assert.equal(focused.total, 4); assert.equal(focused.scopeTotal, 4);
  assert.equal(focused.visibleNodes.find(n => n.id === 'merge79').x, 124);
  assert(focused.visibleEdges.every(e => focused.matchingIds.includes(e.childId)));
  assert.deepEqual(edgeSet(focused.visibleEdges), expectedEdges({ revisions: f.revisions.filter(r => focused.matchingIds.includes(r.id)) }));
  const baseline = Roadmap.layout(f, { branchFilter: 'baseline' });
  assert.deepEqual(baseline.visibleLanes.map(l => l.id), ['baseline']);
  assert.deepEqual(baseline.matchingIds, ['b2', 'b1']);
  const searched = Roadmap.layout(f, { branchFilter: 'm79', query: 'selected-merge' });
  assert.deepEqual(searched.matchingIds, ['merge79']);
  assert.equal(searched.visibleEdges.length, 2);
  assert(searched.visibleEdges.every(e => e.boundary === 'filtered-parent' && !/NaN|undefined/.test(e.path)));
  const page = Roadmap.layout(f, { branchFilter: 'm79', page: 1, pageSize: 2 });
  assert.deepEqual(page.visibleNodes.map(n => n.id), focused.matchingIds.slice(2, 4));
  const html = Roadmap.render({ file: f, branchFilter: 'm79' });
  assert.equal((html.match(/class="rm-lane-label/g) || []).length, 3);
  assert.equal((html.match(/data-roadmap-row=/g) || []).length, 4);
});

test('every visible record provides selectable and detail buttons with readable names', () => {
  const f = file(Model.create()), graph = Roadmap.layout(f), html = Roadmap.render({ file: f, selectedRevisionId: 'r-m13', actorLabel: id => ({ lin: '林工', wang: '王工' }[id] || '访客') });
  assert.equal(Roadmap.formatRevision(f, 'r-b12'), '版本基线 · 软件 691 · 第12次保存');
  assert.equal(Roadmap.formatRevision(f, 'r-m13'), 'MC-001 · 软件 691 · 第3次保存');
  assert.equal(Roadmap.formatRevision(f, 'r-m13', { compact: true }), '第3次保存');
  for (const node of graph.visibleNodes) {
    assert(html.includes('data-action="roadmap-select" data-revision="' + node.id + '"'));
    assert(html.includes('data-action="roadmap-detail" data-revision="' + node.id + '"'));
    assert(html.includes(escape(node.title)));
  }
  assert(html.includes('aria-pressed="true"'));
  assert(html.includes('当前配置')); assert(html.includes('正在采用')); assert(html.includes('林工'));
  assert(!/>B\d+</.test(html) && !/>r\d+</.test(html), 'technical revision labels are not primary text');
  assert(!html.includes('真实保存关系') && !html.includes('较新记录在上'));
});

test('untrusted names, IDs, reasons, actor names and search text are escaped', () => {
  const f = copy(file(Model.create())), payload = '\"><img src=x onerror=alert(1)><script>bad()</script>&\'';
  f.id = 'file' + payload;
  branch(f, 'machine-01').label = 'MC' + payload;
  revision(f, 'r-m13').reason = payload;
  revision(f, 'r-m13').id = 'id' + payload;
  branch(f, 'machine-01').headId = 'id' + payload;
  const html = Roadmap.render({ file: f, actorLabel: () => payload, getReason: r => r.reason || r.message });
  assert(!/<script\b|<img\b/i.test(html));
  assert(html.includes(escape(payload)));
  assert(html.includes('data-revision="' + escape('id' + payload) + '"'));
  assert(html.includes('data-roadmap-file="' + escape(f.id) + '"'));
  const queryHtml = Roadmap.render({ file: f, query: payload });
  assert(queryHtml.includes('value="' + escape(payload) + '"'));
  assert(!/<script\b|<img\b/i.test(queryHtml));
});

test('missing ancestry is explicit; malformed or cyclic identities fail without fake nodes', () => {
  const f = copy(file(Model.create())); revision(f, 'r-b10').parentIds = ['not-loaded'];
  const graph = Roadmap.layout(f);
  assert.equal(graph.nodes.length, f.revisions.length);
  assert.equal(graph.unknownParents.length, 1);
  assert(graph.visibleEdges.some(e => e.parentId === 'not-loaded' && e.boundary === 'missing-parent'));
  assert(Roadmap.render({ file: f }).includes('来源记录未载入'));
  const cycle = copy(file(Model.create())); revision(cycle, 'r-b10').parentIds = ['r-b12'];
  assert.throws(() => Roadmap.layout(cycle), e => e.code === 'INVALID_DAG');
  assert(Roadmap.render({ file: cycle }).includes('保存关系需要核对'));
  const duplicate = copy(file(Model.create())); duplicate.revisions.push(copy(duplicate.revisions[0]));
  assert.throws(() => Roadmap.layout(duplicate), e => e.code === 'DUPLICATE_REVISION');
});

let failed = 0;
for (const [name, run] of tests) {
  try { run(); console.log('PASS ' + name); }
  catch (error) { failed++; console.error('FAIL ' + name + '\n' + error.stack); }
}
console.log(`${tests.length - failed}/${tests.length} roadmap data/HTML checks passed (not browser QA)`);
if (failed) process.exitCode = 1;
