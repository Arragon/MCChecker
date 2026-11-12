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
const save = (model, params, extra = {}) => model.save(Object.assign({ fileId: 'f-machine', branchId: 'base-691', expectedHeadId: branch(model).headId, params, message: '验证本测试场景的参数保存结果' }, extra));
function tiny(model, params) { return model.initialize({ name: 'MergeFixture.json', format: 'json', releaseId: '691', message: '建立合并与来源归因测试使用的输入配置', params: params || [clone(head(model).params[0])] }); }

test('reconstructed seed preserves file, branch and stable parameter identities', () => {
  const m = Model.create(), f = file(m);
  assert.equal(Model.reconstruction, '2026-11-11');
  assert.deepEqual(m.state.workspaces[0].files.map(f => f.id), ['f-machine', 'f-process', 'f-coeff']);
  assert.equal(head(m).params.length, 18);
  assert.equal(m.getFile('f-process').revisions[0].params.length, 6);
  assert.equal(m.getFile('f-coeff').revisions[0].params.length, 6);
  assert.deepEqual(f.branches.map(b => b.id), ['base-691', 'base-690', 'machine-01', 'machine-02', 'base-6A0', 'base-6A1', 'machine-roadmap-03', 'base-release-2026-10-6A1-SPC001']);
  assert.equal(branch(m, 'machine-01').headId, 'r-m13');
  assert.equal(branch(m, 'machine-01').baseRevisionId, 'r-b11');
  assert.equal(revision(f, 'r-m11').releaseId, '690');
  assert.equal(revision(f, 'r-b10').params.find(p => p.id === 'p-speed').name, '传送速度');
  assert.equal(revision(f, 'r-b11').params.find(p => p.id === 'p-speed').name, '送料速度');
  assert(Object.isFrozen(head(m)) && Object.isFrozen(head(m).params[0]));
});

test('release families group real saved records without replacing identity, parentage or historical evidence', () => {
  const m = Model.create(), f = file(m), lab = m.state.workspaces.find(w => w.id === 'ws-lab').files.find(f => f.id === 'f-machine');
  assert.deepEqual(f.releaseFamilies, [{ id: 'family-69x', label: '69X', year: 2026, month: 9, monthKey: '2026-09', order: 1 }, { id: 'family-6ax', label: '6AX', year: 2026, month: 10, monthKey: '2026-10', order: 2 }]);
  assert.deepEqual(f.releaseMinors, [
    { id: 'minor-2026-09-690', version: '690', familyId: 'family-69x', order: 1 },
    { id: 'minor-2026-09-691', version: '691', familyId: 'family-69x', order: 2 },
    { id: 'minor-2026-10-6A0', version: '6A0', familyId: 'family-6ax', order: 3 },
    { id: 'minor-2026-10-6A1', version: '6A1', familyId: 'family-6ax', order: 4 }
  ]);
  assert.deepEqual(f.releases, [
    { id: '690', version: '690', label: '690', familyId: 'family-69x', minorId: 'minor-2026-09-690', patchNumber: null, order: 1 },
    { id: '691', version: '691', label: '691', familyId: 'family-69x', minorId: 'minor-2026-09-691', patchNumber: null, order: 2 },
    { id: '6A0', version: '6A0', label: '6A0', familyId: 'family-6ax', minorId: 'minor-2026-10-6A0', patchNumber: null, order: 3 },
    { id: '6A1', version: '6A1', label: '6A1', familyId: 'family-6ax', minorId: 'minor-2026-10-6A1', patchNumber: null, order: 4 },
    { id: 'release-2026-10-6A1-SPC001', version: '6A1SPC001', label: '6A1SPC001', familyId: 'family-6ax', minorId: 'minor-2026-10-6A1', patchNumber: 1, order: 5 }
  ]);
  const releases = new Set(f.releases.map(r => r.id)), records = new Map(f.revisions.map(r => [r.id, r]));
  assert.equal(records.size, f.revisions.length);
  for (const r of f.revisions) {
    assert(releases.has(r.releaseId), 'every saved record belongs to an explicit software release');
    assert(f.branches.some(b => b.id === r.branchId));
    r.parentIds.forEach(id => assert(records.has(id), 'parent reference resolves to a real saved record'));
    if (r.baseRevisionId) assert(records.has(r.baseRevisionId));
    if (r.sourceRevisionId) assert(records.has(r.sourceRevisionId));
  }
  const expected = [
    ['r-b13', '6A0', ['r-b12']], ['r-b14', '6A1', ['r-b13']], ['r-b15', '6A1', ['r-b14']],
    ['r-roadmap-m31', '6A0', ['r-b13']], ['r-roadmap-m32', '6A1', ['r-roadmap-m31', 'r-b15']]
  ];
  for (const [id, releaseId, parents] of expected) {
    const r = records.get(id);
    assert.equal(r.releaseId, releaseId); assert.deepEqual(r.parentIds, parents);
    assert(r.reason.startsWith('演示：')); assert.equal(r.message, r.reason);
    assert.deepEqual(r.links, ['https://docs.example.invalid/demo/software-6a']);
    assert(Object.isFrozen(r) && Object.isFrozen(r.links) && Object.isFrozen(r.params[0]));
    assert.deepEqual(r.params.map(p => p.id), records.get('r-b12').params.map(p => p.id));
    assert(m.diff(records.get(parents[0]).params, r.params).length > 0);
  }
  assert.equal(f.initialRevisionId, null);
  ['base-6A0', 'base-6A1', 'machine-roadmap-03'].forEach(id => assert.equal(branch(m, id).initialRevisionId, null));
  const asset = m.state.assets.find(a => a.id === 'asset-roadmap-03');
  assert.equal(asset.name, 'MC-003'); assert.equal(asset.model, 'MX-820');
  assert(!f.releaseFamilies.some(family => family.label === asset.model), 'machine model does not determine software family');
  assert.equal(branch(m, 'machine-roadmap-03').headId, 'r-roadmap-m32');
  assert.equal(branch(m, 'machine-roadmap-03').baseRevisionId, 'r-b15');
  assert.equal(records.get('r-roadmap-m31').releaseId, '6A0', 'historical release is not the branch current release');
  const offset = m.blame(f.id, 'r-roadmap-m32', 'p-offset').find(row => row.field === 'value');
  assert.equal(offset.originRevisionId, 'r-roadmap-m31'); assert.equal(offset.authorId, 'lin');
  assert.equal(branch(m).headId, 'r-b12'); assert.equal(branch(m, 'base-690').headId, 'r-b10');
  assert.equal(branch(m, 'machine-02').headId, 'r-m22');
  assert.equal(records.get('r-b12').reason, null); assert.equal(records.get('r-b12').reviewId, 'CR-025');
  assert.equal(m.state.reviews.find(r => r.id === 'CR-024').baseRevisionId, 'r-b12');
  assert.equal(m.state.reviews.find(r => r.id === 'CR-025').mergedRevisionId, 'r-b12');
  assert.deepEqual(lab.releases.map(r => r.id), ['690', '691']);
  assert.equal(lab.revisions.length, 8); assert.equal(lab.branches.length, 4);
  assert(!lab.branches.some(b => b.machineId === asset.id));
  const patch = records.get('r-demo-6a1-spc001');
  assert.deepEqual(patch.parentIds, ['r-b15']); assert.equal(patch.sourceRevisionId, 'r-b15');
  assert.equal(patch.params.find(p => p.id === 'p-retries').value, 4);
  assert.equal(branch(m, 'base-6A1').headId, 'r-b15', 'a patch has its own baseline and does not advance the original');
  assert.deepEqual(patch.links, ['https://docs.example.invalid/demo/software-6a-spc001']);
  assert.equal(m.versionCatalog(f.id).releases.find(r => r.id === '6A1').patchNumber, null);
  assert.equal(m.versionCatalog(f.id).releases.find(r => r.id === patch.releaseId).patchNumber, 1);
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
  throws('HEAD_REQUIRED', () => m.save({ fileId: 'f-machine', branchId: 'base-691', params, message: '验证缺少当前记录时拒绝保存' }));
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
  throws('FORBIDDEN', () => m.fork('f-machine', { machineId: 'asset-02', fromRevisionId: 'r-b12', message: '验证访客不能创建机台配置' }));
  throws('FORBIDDEN', () => m.initialize({ name: 'New.json', format: 'json', releaseId: '692', params: head(m).params, message: '验证访客不能初始化配置' }));
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
  const pending = m.addRelease(f.id, { releaseId: '692', message: '验证未提供原始输入时版本保持为空' });
  assert.equal(pending.headId, null); assert.equal(pending.initialRevisionId, null); assert.equal(f.revisions.length, count);
  const copyBranch = m.addRelease(f.id, { releaseId: '693', sourceRevisionId: 'r-b12', message: '验证显式选择来源的版本初始化' });
  const baseline = revision(f, copyBranch.headId), initial = revision(f, copyBranch.initialRevisionId);
  assert.equal(initial.kind, 'initial'); assert.equal(initial.sourceRevisionId, 'r-b12');
  assert.deepEqual(baseline.parentIds, [initial.id]); assert.deepEqual(initial.params, revision(f, 'r-b12').params);
});

test('family labels use calendar months while full year remains an independent identity', () => {
  assert.equal(Model.formatFamily(2026, 9), '69X'); assert.equal(Model.formatFamily('2026', '10'), '6AX');
  assert.equal(Model.formatFamily(2026, 11), '6BX'); assert.equal(Model.formatFamily(2026, 12), '6CX');
  assert.equal(Model.formatFamily(2027, 1), '71X'); assert.equal(Model.formatFamily(2036, 9), '69X');
  for (const [year, month] of [[26, 9], [2026, 0], [2026, 13], [2026, 1.5], [null, 9], ['2026x', 9], [2026, ''], [Infinity, 1]]) throws('INVALID_FAMILY_DATE', () => Model.formatFamily(year, month));
  const m = Model.create(), previous = clone(file(m).revisions);
  const b = m.addRelease('f-machine', { target: { family: { year: 2036, month: 9 }, minorVersion: '691', kind: 'original' }, reason: '验证十年后同短名仍使用独立年月身份' });
  const catalog = m.versionCatalog('f-machine'), families = catalog.families.filter(f => f.label === '69X');
  assert.deepEqual(families.map(f => f.monthKey), ['2026-09', '2036-09']);
  assert.equal(new Set(families.map(f => f.id)).size, 2); assert.equal(b.releaseId, 'release-2036-09-691');
  assert.equal(catalog.releases.find(r => r.id === b.releaseId).minorId, 'minor-2036-09-691');
  assert.equal(branch(m).headId, 'r-b12'); assert.deepEqual(clone(file(m).revisions), previous);
});

test('catalog is detached, workspace scoped and never infers a date or patch from unknown legacy names', () => {
  const m = Model.create(), before = JSON.stringify(m.state), catalog = m.versionCatalog('f-machine');
  const original = catalog.releases.find(r => r.id === '691');
  assert.equal(original.branchId, 'base-691'); assert.equal(original.headId, 'r-b12'); assert.equal(original.patchNumber, null);
  assert.equal(catalog.minors.find(m => m.id === original.minorId).originalReleaseId, '691');
  catalog.families[0].year = 2036; catalog.minors[0].version = 'changed'; catalog.releases.pop();
  assert.equal(JSON.stringify(m.state), before);
  const b = m.addRelease('f-machine', { releaseId: 'Vendor.v2+SPC001', reason: '保留未知格式的既有软件版本入口' });
  const legacy = m.versionCatalog('f-machine'), row = legacy.releases.find(r => r.id === b.releaseId);
  assert.equal(row.version, 'Vendor.v2+SPC001'); assert.equal(row.patchNumber, null); assert.equal(row.kind, 'original');
  const family = legacy.families.find(f => f.id === row.familyId);
  assert.equal(family.year, null); assert.equal(family.monthKey, null);
  throws('RELEASE_EXISTS', () => m.addRelease('f-machine', { releaseId: b.releaseId, reason: '重复旧版本应被拒绝' }));
  m.state.workspaceId = 'ws-lab';
  assert(!m.versionCatalog('f-machine').releases.some(r => r.id === b.releaseId));
  throws('FILE_NOT_FOUND', () => m.versionCatalog('f-process'));
});

test('minor versions accept multi-digit suffixes, suggest unused defaults and validate family membership', () => {
  const m = Model.create();
  assert.equal(m.suggestMinorVersion('f-machine', { familyId: 'family-69x' }), '692');
  assert.equal(m.suggestMinorVersion('f-machine', { family: { year: 2027, month: 1 } }), '710');
  const b = m.addRelease('f-machine', { target: { familyId: 'family-69x', minorVersion: ' 6910 ', kind: 'original' }, reason: '允许已确认的多位小版本尾号' });
  const minor = m.versionCatalog('f-machine').minors.find(r => r.originalReleaseId === b.releaseId);
  assert.equal(minor.version, '6910'); assert.equal(minor.familyId, 'family-69x');
  assert.equal(m.suggestMinorVersion('f-machine', { familyId: 'family-69x' }), '6911');
  const before = JSON.stringify(m.state);
  for (const minorVersion of ['6A2', '69', '69X']) {
    throws('MINOR_FAMILY_MISMATCH', () => m.addRelease('f-machine', { target: { familyId: 'family-69x', minorVersion, kind: 'original' }, reason: '拒绝不属于所选大版本的编号' }));
    assert.equal(JSON.stringify(m.state), before);
  }
  throws('MINOR_FAMILY_MISMATCH', () => m.addRelease('f-machine', { target: { familyId: 'family-6ax', minorId: minor.id, kind: 'patch' }, reason: '拒绝错误的父层归属' }));
  assert.equal(JSON.stringify(m.state), before);
  for (const minorVersion of ['692SPC001', '692spc001', '6910SPC000']) {
    assert.throws(() => m.addRelease('f-machine', { target: { familyId: 'family-69x', minorVersion, kind: 'patch' }, reason: '补丁编号应只通过独立补丁字段创建' }), error => error.code === 'INVALID_MINOR_VERSION' && error.message.includes('补丁包字段'));
    assert.equal(JSON.stringify(m.state), before);
  }
});

test('a new minor may start with SPC001 and later create its real original without invented saves', () => {
  const m = Model.create(), f = file(m), previous = clone(f.revisions), links = ['HTTPS://Docs.Example.invalid:443/new-minor', 'https://docs.example.invalid/new-minor'];
  const b = m.addRelease(f.id, { target: { family: { year: 2027, month: 1 }, minorVersion: '710', kind: 'patch' }, expectedPatchNumber: 1, reason: '  先登记补丁交付，再等待原始文件  ', links });
  assert.equal(b.headId, null); assert.equal(b.initialRevisionId, null); assert.deepEqual(clone(f.revisions), previous);
  let catalog = m.versionCatalog(f.id), release = catalog.releases.find(r => r.id === b.releaseId), minor = catalog.minors.find(r => r.id === release.minorId);
  assert.equal(release.version, '710SPC001'); assert.equal(release.patchNumber, 1);
  assert.equal(minor.originalReleaseId, null); assert.equal(minor.originalBranchId, null);
  assert.deepEqual(m.nextPatch(f.id, minor.id), { number: 2, label: 'SPC002' });
  assert.equal(catalog.releases.filter(r => r.minorId === minor.id).length, 1);
  const ev = m.state.events[0];
  assert.equal(ev.details.year, 2027); assert.equal(ev.details.monthKey, '2027-01'); assert.equal(ev.details.patchNumber, 1);
  assert.equal(ev.details.reason, '先登记补丁交付，再等待原始文件'); assert.equal(ev.details.createdFamily, true);
  assert.deepEqual(ev.details.links, ['https://docs.example.invalid/new-minor']);
  links.push('https://docs.example.invalid/late');
  assert(Object.isFrozen(f.releases.at(-1)) && Object.isFrozen(f.releases.at(-1).links));
  const original = m.addRelease(f.id, { target: { familyId: release.familyId, minorId: minor.id, kind: 'original' }, sourceRevisionId: 'r-b15', reason: '显式采用已核对来源补齐此小版本原版' });
  catalog = m.versionCatalog(f.id); minor = catalog.minors.find(r => r.id === minor.id);
  assert.equal(minor.originalReleaseId, original.releaseId); assert.notEqual(original.id, b.id);
  assert.equal(b.headId, null); assert.equal(catalog.releases.find(r => r.id === original.releaseId).patchNumber, null);
  assert(!catalog.releases.some(r => r.version.includes('SPC000')));
  assert.equal(revision(f, original.initialRevisionId).sourceRevisionId, 'r-b15');
  assert.deepEqual(clone(f.revisions.slice(0, previous.length)), previous);
  const afterOriginal = JSON.stringify(m.state);
  throws('RELEASE_EXISTS', () => m.addRelease(f.id, { target: { familyId: release.familyId, minorId: minor.id, kind: 'original' }, reason: '已存在原版不能再次创建或覆盖' }));
  assert.equal(JSON.stringify(m.state), afterOriginal);
});

test('patch allocation is per minor and stale previews fail without occupying a number', () => {
  const m = Model.create(), f = file(m), minorId = 'minor-2026-09-691';
  const input = { target: { familyId: 'family-69x', minorId, kind: 'patch' }, expectedPatchNumber: 1, reason: '登记第一份明确交付的补丁包' };
  const first = m.addRelease(f.id, input);
  assert.deepEqual(m.nextPatch(f.id, minorId), { number: 2, label: 'SPC002' });
  assert.deepEqual(m.nextPatch(f.id, 'minor-2026-09-690'), { number: 1, label: 'SPC001' });
  const before = JSON.stringify(m.state);
  assert.throws(() => m.addRelease(f.id, input), error => error.code === 'PATCH_STALE' && error.actualPatchNumber === 2);
  assert.equal(JSON.stringify(m.state), before);
  const second = m.addRelease(f.id, { ...input, expectedPatchNumber: 2, reason: '核对新序号后登记第二份补丁包' });
  assert.notEqual(first.releaseId, second.releaseId); assert.equal(second.releaseId, 'release-2026-09-691-SPC002');
  const version = m.versionCatalog(f.id).releases.find(r => r.id === second.releaseId);
  assert.equal(version.version, '691SPC002'); assert.equal(version.label, '691SPC002');
  assert.deepEqual(m.nextPatch(f.id, minorId), { number: 3, label: 'SPC003' });
  const noPreview = m.addRelease(f.id, { target: input.target, reason: '兼容无需预览的同步调用' });
  assert.equal(m.versionCatalog(f.id).releases.find(r => r.id === noPreview.releaseId).patchNumber, 3);
  assert.equal(new Set(f.branches.map(b => b.releaseId).filter(Boolean)).size, f.branches.filter(b => b.kind === 'baseline').length);
});

test('structured creation validates reason, links, source, parameters and identity before changing any state', () => {
  const m = Model.create(), target = { family: { year: 2027, month: 2 }, minorVersion: '720', kind: 'patch' };
  const base = { target, expectedPatchNumber: 1, reason: '核对新大版本补丁的原始输入' }, before = JSON.stringify(m.state);
  const cases = [
    ['REASON_REQUIRED', { reason: '  ' }], ['INVALID_LINK', { links: ['javascript:alert(1)'] }],
    ['REVISION_NOT_FOUND', { sourceRevisionId: 'missing-source' }], ['INVALID_PARAMS', { params: [] }],
    ['OUT_OF_RANGE', { params: change(head(m).params, 'p-speed', { value: 1000 }) }],
    ['INVALID_FAMILY_DATE', { target: { ...target, family: { year: 2027, month: 13 } } }],
    ['MINOR_FAMILY_MISMATCH', { target: { ...target, minorVersion: '710' } }],
    ['INVALID_MINOR_VERSION', { target: { ...target, minorVersion: '720\nInjected' } }],
    ['INVALID_TARGET', { releaseId: '720' }], ['INVALID_PATCH_NUMBER', { expectedPatchNumber: 0 }],
    ['PATCH_STALE', { expectedPatchNumber: 2 }], ['FAMILY_NOT_FOUND', { target: { familyId: 'not-a-family', minorVersion: '720', kind: 'patch' } }]
  ];
  for (const [code, update] of cases) {
    throws(code, () => m.addRelease('f-machine', { ...base, ...update })); assert.equal(JSON.stringify(m.state), before, code);
  }
  m.state.actorId = 'guest'; const forbidden = JSON.stringify(m.state);
  throws('FORBIDDEN', () => m.addRelease('f-machine', base)); assert.equal(JSON.stringify(m.state), forbidden);
  m.state.actorId = 'lin'; const b = m.addRelease('f-machine', base);
  assert.equal(m.versionCatalog('f-machine').releases.find(r => r.id === b.releaseId).patchNumber, 1);
  assert.equal(m.state.events[0].details.createdFamily, true);
});

test('patch copies only an explicit saved source and preserves immutable authorship and reasons', () => {
  const m = Model.create(), f = file(m), oldRecords = clone(f.revisions), oldHead = branch(m).headId;
  const input = { target: { familyId: 'family-69x', minorId: 'minor-2026-09-691', kind: 'patch' }, expectedPatchNumber: 1, sourceRevisionId: 'r-b11', reason: '仅采用指定的旧基线验证补丁兼容性', links: 'https://docs.example.invalid/patch-check' };
  const b = m.addRelease(f.id, input), initial = revision(f, b.initialRevisionId), saved = revision(f, b.headId);
  assert.deepEqual(initial.parentIds, ['r-b11']); assert.equal(initial.sourceRevisionId, 'r-b11');
  assert.deepEqual(initial.params, revision(f, 'r-b11').params);
  assert.equal(initial.params.find(p => p.id === 'p-speed').value, 105, 'must not silently copy the latest original');
  assert.deepEqual(saved.parentIds, [initial.id]); assert.equal(saved.reason, input.reason);
  assert.deepEqual(saved.links, ['https://docs.example.invalid/patch-check']);
  assert(Object.isFrozen(saved) && Object.isFrozen(saved.links) && Object.isFrozen(saved.params));
  assert.equal(m.blame(f.id, saved.id, 'p-speed').find(r => r.field === 'value').authorId, 'wang');
  assert.deepEqual(clone(f.revisions.slice(0, oldRecords.length)), oldRecords); assert.equal(branch(m).headId, oldHead);
  const pending = m.addRelease(f.id, { target: input.target, expectedPatchNumber: 2, reason: '仅登记补丁身份，稍后提供准确文件' });
  assert.equal(pending.headId, null); assert.equal(pending.initialRevisionId, null);
  const count = f.revisions.length, result = m.save({ fileId: f.id, branchId: pending.id, expectedHeadId: null, params: saved.params, reason: '手工确认补丁的第一份真实配置输入' });
  assert.equal(f.revisions.length, count + 1); assert.deepEqual(result.parentIds, []); assert.equal(result.sourceRevisionId, null);
});

test('structured initialization can create an original or patch from real input without inventing an original package', () => {
  const m = Model.create(), params = clone(head(m).params.slice(0, 2)), before = JSON.stringify(m.state);
  const input = { name: 'PatchInput.json', format: 'json', target: { family: { year: 2027, month: 1 }, minorVersion: '7110', kind: 'patch' }, expectedPatchNumber: 1, params, reason: '从交付文件初始化指定小版本补丁', links: ['https://docs.example.invalid/patch-input'] };
  throws('REASON_REQUIRED', () => m.initialize({ ...input, reason: '' })); assert.equal(JSON.stringify(m.state), before);
  throws('INVALID_FAMILY_DATE', () => m.initialize({ ...input, target: { ...input.target, family: { year: 27, month: 1 } } })); assert.equal(JSON.stringify(m.state), before);
  const f = m.initialize(input), catalog = m.versionCatalog(f.id), initial = revision(f, f.initialRevisionId), saved = revision(f, f.branches[0].headId);
  assert.equal(catalog.minors.length, 1); assert.equal(catalog.minors[0].originalReleaseId, null);
  assert.equal(catalog.releases.length, 1); assert.equal(catalog.releases[0].patchNumber, 1);
  assert.equal(initial.releaseId, saved.releaseId); assert.deepEqual(initial.parentIds, []); assert.deepEqual(saved.parentIds, [initial.id]);
  assert.equal(saved.reason, input.reason); assert.deepEqual(saved.links, input.links);
  assert.equal(m.state.events[0].details.monthKey, '2027-01'); assert.equal(m.state.events[0].details.patchLabel, 'SPC001');
  params[0].value = 0; assert.equal(initial.params[0].value, 120);
  const original = m.initialize({ ...input, name: 'OriginalInput.json', target: { ...input.target, kind: 'original' }, expectedPatchNumber: undefined });
  assert.equal(m.versionCatalog(original.id).releases[0].patchNumber, null);
});

test('manual save of a pending release does not invent an initial source snapshot', () => {
  const m = Model.create(), f = file(m), pending = m.addRelease(f.id, { releaseId: '692', message: '创建待手工填写的测试版本' });
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
  const b = m.fork('f-machine', { machineId: 'asset-03', fromRevisionId: 'r-b11', message: '验证新机台采用指定而非最新基线' });
  const r = revision(file(m), b.headId);
  assert.equal(b.baseRevisionId, 'r-b11'); assert.equal(r.sourceRevisionId, 'r-b11'); assert.deepEqual(r.parentIds, ['r-b11']);
  assert.deepEqual(r.params, revision(file(m), 'r-b11').params);
  throws('DUPLICATE_MACHINE', () => m.fork('f-machine', { machineId: 'asset-03', fromRevisionId: 'r-b12', message: '验证重复机台绑定会被拒绝' }));
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
  const m = Model.create(), f = tiny(m), base = f.branches[0], original = revision(f, base.headId), machine = m.fork(f.id, { machineId: 'asset-01', fromRevisionId: original.id, message: '建立独立更名测试用机台' });
  const target = m.save({ fileId: f.id, branchId: machine.id, expectedHeadId: machine.headId, params: change(original.params, 'p-speed', { name: '现场显示名称' }), message: '机台仅修改参数显示名称' });
  const source = m.save({ fileId: f.id, branchId: base.id, expectedHeadId: base.headId, params: change(original.params, 'p-speed', { value: 125 }), message: '基线仅调整送料值以验证独立字段合并' });
  const plan = m.planMerge(f.id, { baseRevisionId: original.id, targetRevisionId: target.id, sourceRevisionId: source.id });
  assert.equal(plan.conflicts.length, 0); assert.equal(plan.params[0].name, '现场显示名称'); assert.equal(plan.params[0].value, 125);
});

test('unit changes conflict with a custom value instead of silently changing its meaning', () => {
  const m = Model.create(), originalParam = clone(revision(file(m), 'r-b10').params.find(p => p.id === 'p-pressure'));
  const f = tiny(m, [originalParam]), base = f.branches[0], original = revision(f, base.headId), machine = m.fork(f.id, { machineId: 'asset-01', fromRevisionId: original.id, message: '建立单位冲突测试用机台' });
  const target = m.save({ fileId: f.id, branchId: machine.id, expectedHeadId: machine.headId, params: change(original.params, 'p-pressure', { value: 150000, mode: 'override' }), message: '用 Pa 设置机台压力覆盖值' });
  const source = m.save({ fileId: f.id, branchId: base.id, expectedHeadId: base.headId, params: change(original.params, 'p-pressure', { value: 100, max: 500, unit: 'kPa' }), message: '把基线压力及范围转换为 kPa' });
  const plan = m.planMerge(f.id, { baseRevisionId: original.id, targetRevisionId: target.id, sourceRevisionId: source.id });
  assert(plan.conflicts[0].conflicts.includes('unit')); assert(plan.conflicts[0].conflicts.includes('value'));
  assert.equal(plan.params[0].unit, 'Pa'); assert.equal(plan.params[0].value, 150000);
});

test('merge preview detects incompatible combined value and range changes', () => {
  const m = Model.create(), f = tiny(m, [Object.assign(clone(head(m).params[0]), { value: 50, max: 200 })]);
  const base = f.branches[0], original = revision(f, base.headId), machine = m.fork(f.id, { machineId: 'asset-01', fromRevisionId: original.id, message: '建立值与范围冲突测试用机台' });
  const target = m.save({ fileId: f.id, branchId: machine.id, expectedHeadId: machine.headId, params: change(original.params, 'p-speed', { value: 150, mode: 'override' }), message: '验证机台值高于后续收紧范围时的冲突' });
  const source = m.save({ fileId: f.id, branchId: base.id, expectedHeadId: base.headId, params: change(original.params, 'p-speed', { max: 100 }), message: '收紧基线范围以验证组合值校验' });
  const plan = m.planMerge(f.id, { baseRevisionId: original.id, targetRevisionId: target.id, sourceRevisionId: source.id });
  assert.equal(plan.conflicts[0].validationError.code, 'OUT_OF_RANGE');
  assert.equal(plan.params[0].value, 150); assert.equal(plan.params[0].max, 200);
});

test('inherited machine values cannot diverge from the exact chosen basis', () => {
  const m = Model.create(), b = branch(m, 'machine-01'), original = head(m, b.id), before = clone(b);
  throws('INHERIT_MISMATCH', () => m.save({ fileId: 'f-machine', branchId: b.id, expectedHeadId: b.headId, params: change(original.params, 'p-temp', { value: 45, mode: 'inherit' }), message: '验证继承值不可偏离采用基线' }));
  assert.deepEqual(b, before);
  const saved = m.save({ fileId: 'f-machine', branchId: b.id, expectedHeadId: b.headId, params: change(original.params, 'p-temp', { value: 45, mode: 'override' }), message: '明确以机台覆盖值改变温度' });
  assert.equal(saved.baseRevisionId, 'r-b11');
});

test('adopting a new basis can record provenance even when parameter values are unchanged', () => {
  const m = Model.create(), f = file(m), b = branch(m, 'machine-02'), original = head(m, b.id);
  const newRelease = m.addRelease(f.id, { releaseId: '692', sourceRevisionId: 'r-b12', message: '验证内容相同但采用来源不同的新版记录' });
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
  const r = m.revertReview(f.id, b.id, 'r-m11', '验证评审恢复保留历史软件版本与采用基线'); assert.equal(b.headId, before);
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
  const saved = m.save({ fileId: f.id, branchId: b.id, expectedHeadId: b.headId, params: revision(f, 'r-b12').params, sourceRevisionId: 'r-b12', message: '验证采用基线与原始字段作者分别记录' });
  const provenance = m.blame(f.id, saved.id, 'p-temp').find(x => x.field === 'value');
  assert.equal(provenance.known, true); assert.equal(provenance.originRevisionId, 'r-b12'); assert.equal(provenance.appliedRevisionId, saved.id);
  const next = m.save({ fileId: f.id, branchId: b.id, expectedHeadId: b.headId, params: change(saved.params, 'p-speed', { value: 123, mode: 'override' }), message: '调整速度并验证未变温度字段保留来源' });
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

const metadataOperations = [
  ['save', (m, metadata) => m.save({ fileId: 'f-machine', branchId: 'base-691', expectedHeadId: 'r-b12', params: change(head(m).params, 'p-temp', { value: 44 }), ...metadata })],
  ['review', (m, metadata) => m.createReview({ fileId: 'f-machine', branchId: 'base-691', params: change(head(m).params, 'p-temp', { value: 44 }), ...metadata })],
  ['initialize', (m, metadata) => m.initialize({ name: 'ReasonFixture.json', format: 'json', releaseId: '692', params: head(m).params, ...metadata })],
  ['release', (m, metadata) => m.addRelease('f-machine', { releaseId: '692', ...metadata })],
  ['fork', (m, metadata) => m.fork('f-process', { machineId: 'asset-02', fromRevisionId: 'r-process-b1', ...metadata })],
  ['direct restore', (m, metadata) => m.save({ fileId: 'f-machine', branchId: 'machine-01', expectedHeadId: 'r-m13', params: revision(file(m), 'r-m11').params, sourceRevisionId: 'r-m11', restoreRevisionId: 'r-m11', ...metadata })],
  ['reviewed restore', (m, metadata) => m.revertReview('f-machine', 'machine-01', 'r-m11', metadata)]
];

test('all configuration-writing entry points reject missing or blank reasons without mutations', () => {
  for (const [name, run] of metadataOperations) {
    const m = Model.create(), before = JSON.stringify(m.state);
    for (const metadata of [{}, { reason: ' \n\t ' }, { message: '' }, { message: '\u3000' }]) {
      throws('REASON_REQUIRED', () => run(m, metadata));
      assert.equal(JSON.stringify(m.state), before, name + ' must not create a record, branch, review or audit event');
    }
    for (const reason of [null, 42, { text: 'not a string' }]) {
      throws('INVALID_REASON', () => run(m, { reason }));
      assert.equal(JSON.stringify(m.state), before, name + ' must not coerce a reason');
    }
  }
});

test('explicit reason/message aliases are trimmed and review titles cannot override an explicit blank reason', () => {
  assert.deepEqual(Model.changeMetadata({ reason: '  采用已核对的温控范围\n', links: [] }), { reason: '采用已核对的温控范围', message: '采用已核对的温控范围', links: [] });
  assert.deepEqual(Model.changeMetadata({ message: '  核对供气压力后调整  ' }), { reason: '核对供气压力后调整', message: '核对供气压力后调整', links: [] });
  assert.equal(Model.changeMetadata({ reason: '确认温控范围', message: ' 确认温控范围 ' }).reason, '确认温控范围');
  throws('REASON_CONFLICT', () => Model.changeMetadata({ reason: '更换传感器', message: '更换电机' }));
  throws('INVALID_REASON', () => Model.changeMetadata({ reason: '更换传感器', message: null }));
  const m = Model.create(), before = JSON.stringify(m.state), input = { fileId: 'f-machine', branchId: 'base-691', params: change(head(m).params, 'p-temp', { value: 44 }), title: '  温控复测后调整设定  ' };
  throws('REASON_REQUIRED', () => m.createReview({ ...input, reason: ' ' }));
  assert.equal(JSON.stringify(m.state), before);
  const review = m.createReview(input);
  assert.equal(review.title, '温控复测后调整设定'); assert.equal(review.reason, review.title); assert.equal(review.message, review.title);
  assert.equal(Model.changeMetadata({}, ' 明确填写的评审标题 ').reason, '明确填写的评审标题');
});

test('links normalize and deduplicate HTTP(S) URLs while preserving deliberate path, query and fragment distinctions', () => {
  const input = [' HTTPS://DOCS.EXAMPLE.INVALID:443/demo/./check#details ', 'https://docs.example.invalid/demo/check#details', 'http://intranet:8080/changes?version=692', 'https://docs.example.invalid/demo/Check#details', 'https://docs.example.invalid/demo/check?phase=2#details', ''];
  const original = clone(input), expected = ['https://docs.example.invalid/demo/check#details', 'http://intranet:8080/changes?version=692', 'https://docs.example.invalid/demo/Check#details', 'https://docs.example.invalid/demo/check?phase=2#details'];
  assert.deepEqual(Model.normalizeLinks(input), expected); assert.deepEqual(input, original);
  assert.deepEqual(Model.normalizeLinks(' HTTPS://DOCS.EXAMPLE.INVALID:443/demo/check#details \r\n\nhttps://docs.example.invalid/demo/check#details\nhttp://intranet:8080/changes?version=692'), expected.slice(0, 2));
  assert.deepEqual(Model.normalizeLinks(undefined), []); assert.deepEqual(Model.normalizeLinks(' \n '), []);
  assert.deepEqual(Model.normalizeLinks(['http://127.0.0.1:8080/demo', 'https://[::1]/demo', 'https://docs.example.invalid/%E4%B8%AD%E6%96%87']), ['http://127.0.0.1:8080/demo', 'https://[::1]/demo', 'https://docs.example.invalid/%E4%B8%AD%E6%96%87']);
});

test('unsafe or incomplete links are rejected before any configuration mutation', () => {
  const invalid = ['javascript:alert(1)', 'data:text/plain,demo', 'file:///demo/config.xml', 'ftp://files.example.invalid/config', '//docs.example.invalid/demo', '/demo', 'docs.example.invalid/demo', 'https://', 'https:/docs.example.invalid/demo', 'https:///docs.example.invalid/demo', 'https://?path=demo', 'https://.', 'https://docs.example.invalid:', 'https://reader:secret@docs.example.invalid/demo', 'https://reader@docs.example.invalid/demo', 'https://@docs.example.invalid/demo', 'https://docs.example.invalid\\demo', 'https://docs.example.invalid/a b', 'https://docs.example.invalid/\ncheck', '\thttps://docs.example.invalid/demo', 'https://docs.example.invalid/\u007f', 'https://docs.example.invalid/\u0085', 'https://docs.example.invalid/%0Acheck', 'https://docs.example.invalid/%C2%85', 'https://docs.example.invalid/%ZZ', null, 42];
  const m = Model.create(), before = JSON.stringify(m.state);
  for (const link of invalid) {
    throws('INVALID_LINK', () => metadataOperations[0][1](m, { reason: '核对热稳定记录后调整', links: [link] }));
    assert.equal(JSON.stringify(m.state), before);
  }
  for (const links of [null, 17, { url: 'https://docs.example.invalid/demo' }]) throws('INVALID_LINKS', () => Model.normalizeLinks(links));
  for (const [name, run] of metadataOperations) {
    throws('INVALID_LINK', () => run(m, { reason: '核对相关记录后修改配置', links: ['javascript:alert(1)'] }));
    assert.equal(JSON.stringify(m.state), before, name + ' must not write before link validation');
  }
});

test('saved reasons and links are immutable, copied from inputs and available in events and parameter history', () => {
  const m = Model.create(), links = ['HTTPS://DOCS.EXAMPLE.INVALID:443/demo/temperature'], reason = '  温度复测后把设定改为 44 °C  ';
  const next = metadataOperations[0][1](m, { reason, links });
  links.push('https://docs.example.invalid/demo/later');
  assert.equal(next.reason, reason.trim()); assert.equal(next.message, reason.trim());
  assert.deepEqual(next.links, ['https://docs.example.invalid/demo/temperature']);
  assert.throws(() => { next.reason = '事后改写'; }, TypeError);
  assert.throws(() => { next.message = '事后改写'; }, TypeError);
  assert.throws(() => { next.links = []; }, TypeError);
  assert.throws(() => { next.links.push('https://docs.example.invalid/demo/other'); }, TypeError);
  const details = m.state.events[0].details;
  assert.equal(details.reason, next.reason); assert.equal(details.message, next.message); assert.deepEqual(details.links, next.links);
  assert.throws(() => { details.links[0] = 'https://docs.example.invalid/demo/changed'; }, TypeError);
  const history = m.history('f-machine', 'p-temp').find(row => row.revisionId === next.id);
  assert.equal(history.reason, next.reason); assert.deepEqual(history.links, next.links);
  history.links.length = 0; assert.equal(next.links.length, 1);
  assert.equal(revision(file(m), 'r-b12').reason, null);
  assert.equal(revision(file(m), 'r-b12').message, '提高送料速度并优化通信等待');
});

test('review metadata is locked and merge propagates the proposal reason/links without changing source attribution', () => {
  const m = Model.create(), links = ['https://docs.example.invalid/demo/adoption', 'https://config.example.invalid/demo/thermal'];
  const review = m.createReview({ fileId: 'f-machine', branchId: 'machine-01', title: '核对后采用 B12', reason: '热稳定复核通过后采用已保存标准', links, params: revision(file(m), 'r-b12').params, sourceRevisionId: 'r-b12' });
  links.length = 0;
  for (const key of ['title', 'description', 'reason', 'message', 'links']) assert.throws(() => { review[key] = key === 'links' ? [] : '改变批准的内容'; }, TypeError);
  assert.throws(() => review.links.push('https://docs.example.invalid/demo/unapproved'), TypeError);
  assert.equal(review.links.length, 2); assert.equal(review.authorId, 'lin');
  m.state.actorId = 'wang'; m.approve(review.id); const next = m.merge(review.id);
  assert.equal(next.reason, review.reason); assert.equal(next.message, review.reason); assert.notEqual(next.message, review.title); assert.deepEqual(next.links, review.links);
  assert.equal(next.authorId, 'lin'); assert.equal(next.mergedBy, 'wang');
  assert.equal(next.sourceRevisionId, 'r-b12');
  const provenance = m.blame('f-machine', next.id, 'p-temp').find(row => row.field === 'value');
  assert.equal(provenance.authorId, 'wang'); assert.equal(provenance.originRevisionId, 'r-b12'); assert.equal(provenance.appliedRevisionId, next.id);
  for (const action of ['review_created', 'review_approved', 'review_merged']) {
    const details = m.state.events.find(event => event.reviewId === review.id && event.action === action).details;
    assert.equal(details.reason, review.reason); assert.deepEqual(details.links, review.links); assert(Object.isFrozen(details.links));
  }
});

test('initialization, version creation and machine creation preserve user metadata separately from system descriptions', () => {
  const m = Model.create(), metadata = { reason: '首次核对设备输入后纳入配置管理', links: ['https://docs.example.invalid/demo/commissioning'] };
  const initialized = m.initialize({ name: 'Commissioning.json', format: 'json', releaseId: '692', params: head(m).params, ...metadata });
  assert.equal(initialized.revisions.length, 2);
  for (const saved of initialized.revisions) {
    assert.equal(saved.reason, metadata.reason); assert.equal(saved.message, metadata.reason); assert.deepEqual(saved.links, metadata.links);
    assert(saved.systemDescription); assert.notEqual(saved.systemDescription, saved.reason);
  }
  assert.equal(m.state.events[0].details.reason, metadata.reason);
  const pending = m.addRelease('f-machine', { releaseId: '692', message: '为新软件适配预留未填写版本', links: metadata.links });
  assert.equal(pending.headId, null); assert.equal(pending.creationReason, '为新软件适配预留未填写版本'); assert.deepEqual(pending.links, metadata.links);
  assert.equal(m.state.events[0].details.reason, pending.creationReason);
  const copied = m.addRelease('f-machine', { releaseId: '693', sourceRevisionId: 'r-b12', reason: '以已复核标准建立兼容性验证版本', links: metadata.links });
  for (const id of [copied.initialRevisionId, copied.headId]) {
    const saved = revision(file(m), id); assert.equal(saved.reason, copied.creationReason); assert.deepEqual(saved.links, metadata.links);
  }
  const machine = m.fork(initialized.id, { machineId: 'asset-01', fromRevisionId: initialized.branches[0].headId, reason: '登记机台的首次现场核对配置', links: metadata.links });
  const saved = revision(initialized, machine.headId);
  assert.equal(saved.reason, machine.creationReason); assert.equal(saved.message, '登记机台的首次现场核对配置'); assert.deepEqual(saved.links, metadata.links);
  assert.equal(saved.sourceRevisionId, initialized.branches[0].headId); assert(saved.systemDescription.includes('MC-001'));
  assert.equal(m.state.events[0].details.reason, saved.reason); assert.deepEqual(m.state.events[0].details.links, metadata.links);
});

test('restore uses its own explicit reason and links while original field authors remain historical', () => {
  const m = Model.create(), historical = revision(file(m), 'r-m11'); m.state.actorId = 'wang';
  const restored = m.save({ fileId: 'f-machine', branchId: 'machine-01', expectedHeadId: 'r-m13', params: historical.params, sourceRevisionId: historical.id, restoreRevisionId: historical.id, message: '复核旧版兼容性后恢复现场参数', links: 'https://docs.example.invalid/demo/rollback-check\nhttps://config.example.invalid/demo/previous-version' });
  assert.equal(restored.authorId, 'wang'); assert.equal(restored.reason, '复核旧版兼容性后恢复现场参数'); assert.equal(restored.links.length, 2);
  assert.equal(restored.restoreRevisionId, historical.id); assert.equal(historical.authorId, 'lin'); assert.equal(historical.reason, null);
  const provenance = m.blame('f-machine', restored.id, 'p-temp').find(row => row.field === 'value');
  assert.equal(provenance.originRevisionId, 'r-b10'); assert.equal(provenance.authorId, 'lin'); assert.equal(provenance.appliedRevisionId, restored.id);
  const reviewed = Model.create(), proposal = reviewed.revertReview('f-machine', 'machine-01', 'r-m11', { reason: '需要另一位管理员复核旧版回退', links: ['https://docs.example.invalid/demo/reviewed-rollback'] });
  reviewed.state.actorId = 'wang'; reviewed.approve(proposal.id); const merged = reviewed.merge(proposal.id);
  assert.equal(merged.reason, proposal.reason); assert.deepEqual(merged.links, proposal.links); assert.equal(merged.restoreRevisionId, 'r-m11');
  assert.equal(reviewed.state.events[0].details.restoreRevisionId, 'r-m11');
});

let failed = 0;
for (const [name, run] of tests) {
  try { run(); console.log('PASS ' + name); }
  catch (error) { failed++; console.error('FAIL ' + name + '\n' + error.stack); }
}
console.log(`\n${tests.length - failed}/${tests.length} reconstructed model checks passed (fresh run).`);
if (failed) process.exitCode = 1;
