'use strict';
// New verification for the 2026-11-11 reconstructed prototype model.
// This file does not reuse the lost workspace's test result or artifact hash.
const assert = require('node:assert/strict');
const Model = require('./prototype/model.js');
const clone = value => JSON.parse(JSON.stringify(value));
const tests = [];
const test = (name, run) => tests.push([name, run]);
const file = model => model.getFile('f-machine');
const branch = (model, id = 'base-691') => file(model).branches.find(b => b.id === id);
const revision = (f, id) => f.revisions.find(r => r.id === id);
const head = (model, id = 'base-691') => revision(file(model), branch(model, id).headId);
const change = (params, id, update) => params.map(p => Object.assign(clone(p), p.id === id ? update : {}));
const throws = (code, run) => assert.throws(run, error => error.code === code, 'expected ' + code);
const observation = model => JSON.stringify({ head: branch(model).headId, revisions: file(model).revisions, events: model.state.events, reviews: model.state.reviews });
const proposal = model => model.createReview({ fileId: 'f-machine', branchId: 'base-691', title: '调整温度', params: change(head(model).params, 'p-temp', { value: 44 }) });
const save = (model, params, extra = {}) => model.save(Object.assign({ fileId: 'f-machine', branchId: 'base-691', expectedHeadId: branch(model).headId, params, message: '测试修改' }, extra));
function tiny(model, params) { return model.initialize({ name: 'MergeFixture.json', format: 'json', releaseId: '691', params: params || [clone(head(model).params[0])] }); }

test('reconstructed seed preserves file, branch and stable parameter identities', () => {
  const m = Model.create(), f = file(m);
  assert.equal(Model.reconstruction, '2026-11-11');
  assert.deepEqual(m.state.workspaces[0].files.map(f => f.id), ['f-machine', 'f-process', 'f-coeff']);
  assert.equal(head(m).params.length, 18);
  assert.equal(m.getFile('f-process').revisions[0].params.length, 6);
  assert.equal(m.getFile('f-coeff').revisions[0].params.length, 6);
  assert.deepEqual(f.branches.map(b => b.id), ['base-691', 'base-690', 'machine-01', 'machine-02']);
  assert.equal(branch(m, 'machine-01').headId, 'r-m13');
  assert.equal(branch(m, 'machine-01').baseRevisionId, 'r-b11');
  assert.equal(revision(f, 'r-m11').releaseId, '690');
  assert.equal(revision(f, 'r-b10').params.find(p => p.id === 'p-speed').name, '传送速度');
  assert.equal(revision(f, 'r-b11').params.find(p => p.id === 'p-speed').name, '送料速度');
  assert(Object.isFrozen(head(m)) && Object.isFrozen(head(m).params[0]));
});

test('pressure unit history converts both value and bound exactly', () => {
  const f = file(Model.create());
  const old = revision(f, 'r-b10').params.find(p => p.id === 'p-pressure');
  const next = revision(f, 'r-b11').params.find(p => p.id === 'p-pressure');
  assert.equal(old.value / 1000, next.value); assert.equal(old.max / 1000, next.max);
  assert.equal(old.unit, 'Pa'); assert.equal(next.unit, 'kPa');
});

test('direct save requires one admin and CAS, appends an immutable snapshot without review', () => {
  const m = Model.create(), old = head(m), count = file(m).revisions.length, reviews = m.state.reviews.length;
  const params = change(old.params, 'p-temp', { value: 43 });
  const result = save(m, params);
  params.find(p => p.id === 'p-temp').value = 70;
  assert.equal(branch(m).headId, result.id); assert.equal(file(m).revisions.length, count + 1);
  assert.equal(m.state.reviews.length, reviews); assert.equal(result.reviewId, null);
  assert.deepEqual(result.parentIds, [old.id]);
  assert.equal(result.params.find(p => p.id === 'p-temp').value, 43);
  assert.equal(old.params.find(p => p.id === 'p-temp').value, 42);
  assert.throws(() => { result.params[0].value = 0; }, TypeError);
  const before = observation(m);
  throws('HEAD_REQUIRED', () => m.save({ fileId: 'f-machine', branchId: 'base-691', params }));
  throws('STALE_HEAD', () => save(m, params, { expectedHeadId: old.id }));
  assert.equal(observation(m), before);
});

test('same-name parameters retain distinct identities while rename remains one change', () => {
  const p = clone(head(Model.create()).params[0]);
  const renamed = Object.assign({}, p, { name: '新名称' });
  const rows = Model.diff([p], [renamed]);
  assert.equal(rows.length, 1); assert.equal(rows[0].kind, 'changed');
  assert.deepEqual(rows[0].fields.map(x => x.field), ['name']);
  const identities = Model.diff([p], [Object.assign({}, p, { id: 'different-id' })]);
  assert.deepEqual(identities.map(r => r.kind), ['removed', 'added']);
  assert.equal(Model.diff([Object.assign({}, p, { value: 0 })], [Object.assign({}, p, { value: false })])[0].fields[0].after, false);
  assert.equal(Model.diff([Object.assign({}, p, { value: '' })], [Object.assign({}, p, { value: 0 })])[0].fields[0].before, '');
});

test('reviews capture immutable proposal and author/base identities', () => {
  const m = Model.create(), params = change(head(m).params, 'p-temp', { value: 44 });
  const r = m.createReview({ fileId: 'f-machine', branchId: 'base-691', title: '温度验证', params });
  params.find(p => p.id === 'p-temp').value = 50;
  assert.equal(r.proposedParams.find(p => p.id === 'p-temp').value, 44);
  assert.throws(() => { r.baseRevisionId = 'r-b10'; }, TypeError);
  assert.throws(() => { r.proposedParams[0].value = 0; }, TypeError);
  assert.equal(r.status, 'open'); assert.equal(branch(m).headId, 'r-b12');
});

test('self approval and unresolved discussion block review publication', () => {
  const m = Model.create();
  m.state.actorId = 'wang'; throws('SELF_APPROVAL', () => m.approve('CR-024'));
  m.state.actorId = 'lin'; throws('UNRESOLVED', () => m.approve('CR-024'));
  m.resolveComment('CR-024', 'comment-024-1');
  const approved = m.approve('CR-024'), eventCount = m.state.events.length;
  assert.equal(approved.status, 'approved'); m.approve('CR-024');
  assert.equal(m.state.events.length, eventCount);
});

test('new discussion invalidates previous approval and scopes comment resolution', () => {
  const m = Model.create(); m.resolveComment('CR-024', 'comment-024-1'); m.approve('CR-024');
  m.state.actorId = 'guest'; const comment = m.addComment('CR-024', { paramId: 'p-temp', field: 'value', body: '需核对热稳定记录' });
  const review = m.state.reviews.find(r => r.id === 'CR-024');
  assert.equal(review.status, 'open'); assert.equal(review.approvals.length, 0);
  throws('FORBIDDEN', () => m.approve(review.id));
  m.resolveComment(review.id, comment.id); assert.equal(comment.resolvedBy, 'guest');
  m.state.actorId = 'lin'; m.approve(review.id); assert.equal(review.status, 'approved');
});

test('approved review cannot replace a concurrently advanced target', () => {
  const m = Model.create(), r = proposal(m);
  m.state.actorId = 'wang'; m.approve(r.id);
  const next = save(m, change(head(m).params, 'p-speed', { value: 121 }));
  const before = observation(m); throws('STALE_HEAD', () => m.merge(r.id));
  assert.equal(observation(m), before); assert.equal(branch(m).headId, next.id);
});

test('review merge records independent actor and replay does not duplicate history or audit', () => {
  const m = Model.create(), r = proposal(m), machineHead = branch(m, 'machine-01').headId;
  m.state.actorId = 'wang'; m.approve(r.id); const next = m.merge(r.id);
  assert.equal(next.authorId, 'lin'); assert.equal(next.mergedBy, 'wang'); assert.equal(next.reviewId, r.id);
  assert.equal(r.status, 'merged'); assert.equal(branch(m, 'machine-01').headId, machineHead);
  const before = observation(m); assert.equal(m.merge(r.id), next); assert.equal(observation(m), before);
});

test('workspace scope prevents cross-workspace reviews, files and mutations', () => {
  const m = Model.create(), prod = file(m), original = clone(prod);
  m.state.workspaceId = 'ws-lab';
  assert.equal(head(m).params.find(p => p.id === 'p-speed').value, 80);
  throws('REVIEW_NOT_FOUND', () => m.approve('CR-024'));
  throws('FILE_NOT_FOUND', () => m.getFile('f-process'));
  assert(file(m).revisions.every(r => r.reviewId === null));
  save(m, change(head(m).params, 'p-speed', { value: 81 }));
  assert.deepEqual(clone(prod), original);
  assert.equal(m.state.events[0].workspaceId, 'ws-lab');
});

test('guest may propose and comment but cannot save, approve, fork or initialize', () => {
  const m = Model.create(); m.state.actorId = 'guest';
  const r = proposal(m); m.addComment(r.id, { body: '请管理员核对' });
  throws('FORBIDDEN', () => save(m, head(m).params));
  throws('FORBIDDEN', () => m.approve(r.id));
  throws('FORBIDDEN', () => m.fork('f-machine', { machineId: 'asset-02', fromRevisionId: 'r-b12' }));
  throws('FORBIDDEN', () => m.initialize({ name: 'New.json', format: 'json', releaseId: '692', params: head(m).params }));
  assert.equal(branch(m).headId, 'r-b12');
});

test('invalid typed values and numeric bounds leave head, history and audit untouched', () => {
  const m = Model.create(), before = observation(m), initial = head(m).params;
  const cases = [
    ['p-speed', { value: NaN }, 'INVALID_VALUE'], ['p-speed', { value: Infinity }, 'INVALID_VALUE'],
    ['p-speed', { value: ' ' }, 'INVALID_VALUE'], ['p-speed', { value: '0x10' }, 'INVALID_VALUE'],
    ['p-speed', { value: true }, 'INVALID_VALUE'], ['p-speed', { min: 20, max: 10 }, 'INVALID_BOUNDS'],
    ['p-speed', { min: NaN }, 'INVALID_BOUNDS'], ['p-speed', { value: 300 }, 'OUT_OF_RANGE'],
    ['p-cooling', { value: 'false' }, 'INVALID_VALUE'], ['p-station', { value: 0 }, 'INVALID_VALUE'],
    ['p-speed', { type: 'integer' }, 'INVALID_TYPE'], ['p-speed', { mode: 'automatic' }, 'INVALID_MODE']
  ];
  for (const [id, update, code] of cases) {
    throws(code, () => save(m, change(initial, id, update)));
    assert.equal(observation(m), before, id + ' ' + code);
  }
});

test('numeric strings preserve large integers and exact fractional range boundaries', () => {
  const m = Model.create();
  const first = save(m, change(head(m).params, 'p-speed', { value: '9007199254740993', min: '9007199254740992', max: '9007199254740993' }));
  assert.equal(first.params.find(p => p.id === 'p-speed').value, '9007199254740993');
  const before = observation(m);
  throws('OUT_OF_RANGE', () => save(m, change(first.params, 'p-speed', { value: '9007199254740994' })));
  throws('PRECISION_RISK', () => save(m, change(first.params, 'p-speed', { value: 9007199254740992 })));
  assert.equal(observation(m), before);
  const second = save(m, change(first.params, 'p-speed', { value: '0.10000000000000000001', min: '0.1', max: '0.10000000000000000001' }));
  throws('OUT_OF_RANGE', () => save(m, change(second.params, 'p-speed', { value: '0.10000000000000000002' })));
  assert.equal(Model.compareDecimal('1e-500', '0'), 1);
  assert.equal(Model.compareDecimal('-100', '-99'), -1);
  assert.equal(Model.compareDecimal('100.00', '1e2'), 0);
});

test('new releases are empty until input or source is explicitly supplied', () => {
  const m = Model.create(), f = file(m), count = f.revisions.length;
  const pending = m.addRelease(f.id, { releaseId: '692' });
  assert.equal(pending.headId, null); assert.equal(pending.initialRevisionId, null); assert.equal(f.revisions.length, count);
  const copyBranch = m.addRelease(f.id, { releaseId: '693', sourceRevisionId: 'r-b12' });
  const baseline = revision(f, copyBranch.headId), initial = revision(f, copyBranch.initialRevisionId);
  assert.equal(initial.kind, 'initial'); assert.equal(initial.sourceRevisionId, 'r-b12');
  assert.deepEqual(baseline.parentIds, [initial.id]); assert.deepEqual(initial.params, revision(f, 'r-b12').params);
});

test('manual save of a pending release does not invent an initial source snapshot', () => {
  const m = Model.create(), f = file(m), pending = m.addRelease(f.id, { releaseId: '692' });
  const next = m.save({ fileId: f.id, branchId: pending.id, expectedHeadId: null, params: revision(f, 'r-b12').params, message: '人工确认的初始参数' });
  assert.equal(pending.initialRevisionId, null); assert.equal(f.initialRevisionId, null);
  assert.equal(next.sourceRevisionId, null); assert.equal(next.kind, undefined); assert.deepEqual(next.parentIds, []);
});

test('initialization creates separate frozen original and baseline snapshots', () => {
  const m = Model.create(), params = clone(head(m).params.slice(0, 2)), f = tiny(m, params);
  assert.equal(f.revisions.length, 2);
  const initial = revision(f, f.initialRevisionId), baseline = revision(f, f.branches[0].headId);
  assert.equal(initial.kind, 'initial'); assert.equal(initial.sourceRevisionId, null);
  assert.deepEqual(baseline.parentIds, [initial.id]);
  params[0].value = 1; assert.equal(initial.params[0].value, 120);
  throws('FILE_EXISTS', () => tiny(m, params));
});

test('fork uses an exact baseline and rejects duplicate physical machine association', () => {
  const m = Model.create(); m.state.assets.push({ id: 'asset-03', name: 'MC-003', model: '69X' });
  const b = m.fork('f-machine', { machineId: 'asset-03', fromRevisionId: 'r-b11' });
  const r = revision(file(m), b.headId);
  assert.equal(b.baseRevisionId, 'r-b11'); assert.equal(r.sourceRevisionId, 'r-b11'); assert.deepEqual(r.parentIds, ['r-b11']);
  assert.deepEqual(r.params, revision(file(m), 'r-b11').params);
  throws('DUPLICATE_MACHINE', () => m.fork('f-machine', { machineId: 'asset-03', fromRevisionId: 'r-b12' }));
});

test('three-way merge identifies value conflict and preserves pinned values', () => {
  const m = Model.create(), plan = m.planMerge('f-machine', { baseRevisionId: 'r-b11', targetRevisionId: 'r-m13', sourceRevisionId: 'r-b12' });
  assert(plan.conflicts.some(r => r.paramId === 'p-speed' && r.conflicts.includes('value')));
  assert.equal(plan.params.find(p => p.id === 'p-temp').value, 42);
  assert.equal(plan.params.find(p => p.id === 'p-offset').value, 0.15);
  assert.equal(plan.params.find(p => p.id === 'p-offset').mode, 'pin');
  throws('BASE_NOT_COMMON', () => m.planMerge('f-machine', { baseRevisionId: 'r-b12', targetRevisionId: 'r-m13', sourceRevisionId: 'r-b11' }));
});

test('source deletion of a pinned machine parameter requires explicit resolution', () => {
  const m = Model.create(), source = save(m, head(m).params.filter(p => p.id !== 'p-offset'));
  const plan = m.planMerge('f-machine', { baseRevisionId: 'r-b11', targetRevisionId: 'r-m13', sourceRevisionId: source.id });
  assert(plan.conflicts.some(r => r.paramId === 'p-offset' && r.conflicts.includes('membership')));
  assert.equal(plan.params.find(p => p.id === 'p-offset').value, 0.15);
});

test('independent name and value changes merge by stable identity', () => {
  const m = Model.create(), f = tiny(m), base = f.branches[0], original = revision(f, base.headId), machine = m.fork(f.id, { machineId: 'asset-01', fromRevisionId: original.id });
  const target = m.save({ fileId: f.id, branchId: machine.id, expectedHeadId: machine.headId, params: change(original.params, 'p-speed', { name: '现场显示名称' }) });
  const source = m.save({ fileId: f.id, branchId: base.id, expectedHeadId: base.headId, params: change(original.params, 'p-speed', { value: 125 }) });
  const plan = m.planMerge(f.id, { baseRevisionId: original.id, targetRevisionId: target.id, sourceRevisionId: source.id });
  assert.equal(plan.conflicts.length, 0); assert.equal(plan.params[0].name, '现场显示名称'); assert.equal(plan.params[0].value, 125);
});

test('unit changes conflict with a custom value instead of silently changing its meaning', () => {
  const m = Model.create(), originalParam = clone(revision(file(m), 'r-b10').params.find(p => p.id === 'p-pressure'));
  const f = tiny(m, [originalParam]), base = f.branches[0], original = revision(f, base.headId), machine = m.fork(f.id, { machineId: 'asset-01', fromRevisionId: original.id });
  const target = m.save({ fileId: f.id, branchId: machine.id, expectedHeadId: machine.headId, params: change(original.params, 'p-pressure', { value: 150000, mode: 'override' }) });
  const source = m.save({ fileId: f.id, branchId: base.id, expectedHeadId: base.headId, params: change(original.params, 'p-pressure', { value: 100, max: 500, unit: 'kPa' }) });
  const plan = m.planMerge(f.id, { baseRevisionId: original.id, targetRevisionId: target.id, sourceRevisionId: source.id });
  assert(plan.conflicts[0].conflicts.includes('unit')); assert(plan.conflicts[0].conflicts.includes('value'));
  assert.equal(plan.params[0].unit, 'Pa'); assert.equal(plan.params[0].value, 150000);
});

test('merge preview detects incompatible combined value and range changes', () => {
  const m = Model.create(), f = tiny(m, [Object.assign(clone(head(m).params[0]), { value: 50, max: 200 })]);
  const base = f.branches[0], original = revision(f, base.headId), machine = m.fork(f.id, { machineId: 'asset-01', fromRevisionId: original.id });
  const target = m.save({ fileId: f.id, branchId: machine.id, expectedHeadId: machine.headId, params: change(original.params, 'p-speed', { value: 150, mode: 'override' }) });
  const source = m.save({ fileId: f.id, branchId: base.id, expectedHeadId: base.headId, params: change(original.params, 'p-speed', { max: 100 }) });
  const plan = m.planMerge(f.id, { baseRevisionId: original.id, targetRevisionId: target.id, sourceRevisionId: source.id });
  assert.equal(plan.conflicts[0].validationError.code, 'OUT_OF_RANGE');
  assert.equal(plan.params[0].value, 150); assert.equal(plan.params[0].max, 200);
});

test('inherited machine values cannot diverge from the exact chosen basis', () => {
  const m = Model.create(), b = branch(m, 'machine-01'), original = head(m, b.id), before = clone(b);
  throws('INHERIT_MISMATCH', () => m.save({ fileId: 'f-machine', branchId: b.id, expectedHeadId: b.headId, params: change(original.params, 'p-temp', { value: 45, mode: 'inherit' }) }));
  assert.deepEqual(b, before);
  const saved = m.save({ fileId: 'f-machine', branchId: b.id, expectedHeadId: b.headId, params: change(original.params, 'p-temp', { value: 45, mode: 'override' }) });
  assert.equal(saved.baseRevisionId, 'r-b11');
});

test('adopting a new basis can record provenance even when parameter values are unchanged', () => {
  const m = Model.create(), f = file(m), b = branch(m, 'machine-02'), original = head(m, b.id);
  const newRelease = m.addRelease(f.id, { releaseId: '692', sourceRevisionId: 'r-b12' });
  const next = m.save({ fileId: f.id, branchId: b.id, expectedHeadId: b.headId, params: original.params, sourceRevisionId: newRelease.headId, message: '明确采用 692 基线' });
  assert.equal(m.diff(original.params, next.params).length, 0);
  assert.equal(next.releaseId, '692'); assert.equal(b.releaseId, '692');
  assert.equal(next.baseRevisionId, newRelease.headId); assert.deepEqual(next.parentIds, [original.id, newRelease.headId]);
});

test('direct restore preserves historical machine release and basis without mandatory review', () => {
  const m = Model.create(), f = file(m), b = branch(m, 'machine-01'), before = b.headId, count = f.revisions.length, reviews = m.state.reviews.length;
  const historical = revision(f, 'r-m11');
  const restored = m.save({ fileId: f.id, branchId: b.id, expectedHeadId: before, params: historical.params, sourceRevisionId: historical.id, restoreRevisionId: historical.id, message: '恢复 r1' });
  assert.equal(restored.releaseId, '690'); assert.equal(restored.baseRevisionId, 'r-b10');
  assert.equal(b.releaseId, '690'); assert.equal(b.baseRevisionId, 'r-b10');
  assert.equal(f.revisions.length, count + 1); assert(revision(f, before)); assert.equal(m.state.reviews.length, reviews);
});

test('reviewed restore is an append-only proposal and restores its historical basis', () => {
  const m = Model.create(), f = file(m), b = branch(m, 'machine-01'), before = b.headId;
  const r = m.revertReview(f.id, b.id, 'r-m11'); assert.equal(b.headId, before);
  assert.equal(r.restoreRevisionId, 'r-m11'); m.state.actorId = 'wang'; m.approve(r.id); const saved = m.merge(r.id);
  assert.equal(saved.releaseId, '690'); assert.equal(saved.baseRevisionId, 'r-b10'); assert(revision(f, before));
});

test('history reports real rename, unit, attribute and value diffs', () => {
  const m = Model.create(), rename = m.history('f-machine', 'p-speed').find(r => r.revisionId === 'r-b11');
  assert(rename.fields.some(f => f.field === 'name')); assert(rename.fields.some(f => f.field === 'value'));
  const pressure = m.history('f-machine', 'p-pressure').find(r => r.revisionId === 'r-b11');
  assert(pressure.fields.some(f => f.field === 'unit')); assert(pressure.fields.some(f => f.field === 'max'));
  assert(m.history('f-machine', 'p-speed', 'machine-01').every(r => r.branchId === 'machine-01'));
});

test('blame distinguishes source origin from application and retains unchanged attribution', () => {
  const m = Model.create(), f = file(m), b = branch(m, 'machine-01');
  const saved = m.save({ fileId: f.id, branchId: b.id, expectedHeadId: b.headId, params: revision(f, 'r-b12').params, sourceRevisionId: 'r-b12' });
  const provenance = m.blame(f.id, saved.id, 'p-temp').find(x => x.field === 'value');
  assert.equal(provenance.known, true); assert.equal(provenance.originRevisionId, 'r-b12'); assert.equal(provenance.appliedRevisionId, saved.id);
  const next = m.save({ fileId: f.id, branchId: b.id, expectedHeadId: b.headId, params: change(saved.params, 'p-speed', { value: 123, mode: 'override' }) });
  const unchanged = m.blame(f.id, next.id, 'p-temp').find(x => x.field === 'value');
  assert.equal(unchanged.originRevisionId, provenance.originRevisionId); assert.equal(unchanged.appliedRevisionId, provenance.appliedRevisionId);
});

test('first fork records application separately from source and preserves it after unchanged saves', () => {
  const m = Model.create(), f = file(m);
  const first = m.blame(f.id, 'r-m11', 'p-station').find(x => x.field === 'value');
  assert.equal(first.originRevisionId, 'r-b10'); assert.equal(first.appliedRevisionId, 'r-m11');
  const later = m.blame(f.id, 'r-m13', 'p-station').find(x => x.field === 'value');
  assert.equal(later.originRevisionId, 'r-b10'); assert.equal(later.appliedRevisionId, 'r-m11');
  const imported = tiny(m), baseline = revision(imported, imported.branches[0].headId);
  const baselineField = m.blame(imported.id, baseline.id, 'p-speed').find(x => x.field === 'value');
  assert.equal(baselineField.originRevisionId, imported.initialRevisionId);
  assert.equal(baselineField.appliedRevisionId, baseline.id);
});

test('blame reports unknown when predecessor evidence is missing', () => {
  const m = Model.create(), f = file(m), incomplete = Object.assign(clone(head(m)), { id: 'incomplete-evidence', parentIds: ['missing-parent'], sourceRevisionId: null, reviewId: null });
  f.revisions.push(Object.freeze(incomplete));
  const provenance = m.blame(f.id, incomplete.id, 'p-speed').find(x => x.field === 'value');
  assert.equal(provenance.known, false); assert.equal(provenance.originRevisionId, null); assert.equal(provenance.authorId, null);
});

let failed = 0;
for (const [name, run] of tests) {
  try { run(); console.log('PASS ' + name); }
  catch (error) { failed++; console.error('FAIL ' + name + '\n' + error.stack); }
}
console.log(`\n${tests.length - failed}/${tests.length} reconstructed model checks passed (fresh run).`);
if (failed) process.exitCode = 1;
