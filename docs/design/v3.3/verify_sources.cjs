/* Rebuilt v3.3 source module verification. Node only; real XML/JSON strings.
 * No DOM stubs, browser, network, production parser, or previous test result.
 * Repository placement: docs/design/v3.3/verify_sources.cjs reads prototype/sources.js.
 */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const modulePath = fs.existsSync(path.join(__dirname, 'prototype', 'sources.js')) ? path.join(__dirname, 'prototype', 'sources.js') : path.join(__dirname, 'prototype-sources.js');
const S = require(modulePath);
const clone = value => JSON.parse(JSON.stringify(value));
function parameter(id, path, value, type = typeof value) {return {id, path, name: id, type, value, unit: '', min: null, max: null, mode: 'inherit'};}
function fixture(extra = {}) {
  const workspaces = {
    'ws-prod': [{id: 'f-machine', name: 'MachineConfig.xml', branches: [{id: 'base-691', kind: 'baseline'}]}, {id: 'f-process', name: 'ProcessRecipe.json', branches: [{id: 'base-691', kind: 'baseline'}]}],
    'ws-lab': [{id: 'f-machine', name: 'MachineConfig.xml', branches: [{id: 'base-691', kind: 'baseline'}]}]
  };
  const heads = new Map(), events = []; let actor = 'lin';
  for (const [wid, files] of Object.entries(workspaces)) for (const file of files) heads.set(JSON.stringify([wid, file.id, 'base-691']), {
    id: wid + ':' + file.id + ':r1', label: 'r1', params: file.id === 'f-machine' ? [parameter('p-temp', '/Thermal/Temperature', wid === 'ws-prod' ? 40 : 30, 'number'), parameter('p-temp-max', '/Thermal/Maximum', 60, 'number'), parameter('p-name', '/Identity/Name', 'Fixture')] : [parameter('p-time', '/recipe/time', 8, 'number'), parameter('p-enable', '/recipe/enabled', true)]
  });
  const options = Object.assign({getFiles: wid => workspaces[wid], getHead: (wid, fileId, branchId) => heads.get(JSON.stringify([wid, fileId, branchId])), getActor: () => actor, onEvent: e => events.push(e), now: () => '2026-11-11T12:00:00Z', delayMs: 0}, extra);
  const engine = S.create(options);
  const head = (wid, file = 'f-machine') => heads.get(JSON.stringify([wid, file, 'base-691']));
  const saveHead = (wid, file, params, id = 'saved-r2') => {const revision = {id, label: id, params: clone(params)}; heads.set(JSON.stringify([wid, file, 'base-691']), revision); return revision;};
  async function source(wid = 'ws-prod', kind = 'html', opts = {}) {
    const draft = Object.assign(S.sampleDraft(kind), opts); const preview = await engine.detect(wid, draft); const saved = engine.save(wid, draft, preview); return {draft, preview, saved};
  }
  return {engine, workspaces, heads, head, saveHead, events, source, setActor: value => {actor = value;}};
}
for (const [name, text] of Object.entries({array: '{"a":[1,2]}', null: '{"a":null}', emptyKey: '{"":1}', slashKey: '{"a/b":1}', emptyObject: '{"a":{}}', emptyRoot: '{}', scalarRoot: '5', duplicateKey: '{"a":1,"a":2}', escapedDuplicateKey: '{"a":1,"\\u0061":2}', reservedKey: '{"constructor":1}'})) {
  test('JSON rejects unsupported ' + name, () => assert.throws(() => S.parseText(text, 'sample.json')));
}
test('JSON supported values and escaped strings round-trip', () => {
  const input = '{"settings":{"value":42,"enabled":false,"text":" A & < B ","quote":"line\\nquote\\\"","unicode":"参数"}}';
  const parsed = S.parseText(input, 'sample.json'); assert.deepEqual(JSON.parse(S.serialize(parsed.params, 'json')), JSON.parse(input));
});
for (const token of ['9007199254740993', '0.10000000000000001', '1e400', '1e-400', '-0', '1.2300', '10e-1', '0.000000000000000000000001']) {
  test('JSON numeric text preserved: ' + token, () => {
    const parsed = S.parseText('{"n":' + token + '}', 'sample.json');
    assert.equal(parsed.params[0].rawNumeric, token); assert.equal(parsed.params[0].type, 'number');
    assert.ok(S.serialize(parsed.params, 'json').includes('"n": ' + token + '\n'));
    const again = S.parseText(S.serialize(parsed.params, 'json'), 'again.json'); assert.equal(again.params[0].rawNumeric, token);
  });
}
test('Editing a numeric value discards obsolete original numeric text', () => {
  const parsed = S.parseText('{"n":1.2300}', 'sample.json'); parsed.params[0].value = 2;
  assert.ok(S.serialize(parsed.params, 'json').includes('"n": 2\n')); assert.ok(!S.serialize(parsed.params, 'json').includes('1.2300'));
});
test('JSON syntax cannot accept prefixes, trailing commas, controls or trailing roots', () => {
  ['{"a":01}', '{"a":1,}', '{"a":trueX}', '{"a":"\u0001"}', '{"a":1}{}', '{"a":NaN}'].forEach(input => assert.throws(() => S.parseText(input, 'sample.json')));
});
test('Reference IDs match by path without discarding unknown fields', () => {
  const reference = [parameter('stable', '/known', 0, 'number')], parsed = S.parseText('{"known":4,"new":8}', 'sample.json', reference);
  assert.equal(parsed.params[0].id, 'stable'); assert.equal(parsed.matched, 1); assert.equal(parsed.unknown, 1); assert.equal(reference[0].value, 0);
});
test('Duplicate reference paths are rejected', () => assert.throws(() => S.parseText('{"a":1}', 'sample.json', [parameter('1', '/a', 0), parameter('2', '/a', 0)]), e => e.code === 'REFERENCE_COLLISION'));
test('UTF-8 byte and depth limits are enforced', () => {
  assert.throws(() => S.parseText('{"text":"' + '汉'.repeat(Math.floor(S.LIMITS.bytes / 3)) + '"}', 'sample.json'), e => e.code === 'SAMPLE_TOO_LARGE');
  assert.throws(() => S.parseText('{"a":'.repeat(34) + '1' + '}'.repeat(34), 'sample.json'), e => e.code === 'TOO_DEEP');
});
test('Parameter count has an explicit bounded failure', () => {
  const input = '{' + Array.from({length: S.LIMITS.params + 1}, (_, i) => '"k' + i + '":1').join(',') + '}';
  assert.throws(() => S.parseText(input, 'sample.json'), e => e.code === 'TOO_MANY_PARAMS');
});
test('XML sample values preserve precision, whitespace, escaping and root name', () => {
  const input = '<?xml version="1.0" encoding="UTF-8"?><Machine><serial>007</serial><space> A </space><value>9007199254740993</value><decimal>1.2300</decimal><text>A &amp; &lt; B &#13; &#x1F600;</text><empty/></Machine>';
  const parsed = S.parseText(input, 'sample.xml'); const output = S.serialize(parsed.params, 'xml', {rootName: parsed.rootName}); const again = S.parseText(output, 'again.xml');
  assert.equal(parsed.rootName, 'Machine'); assert.deepEqual(again.params, parsed.params);
  assert.equal(parsed.params.find(p => p.path === '/serial').value, '007'); assert.equal(parsed.params.find(p => p.path === '/space').value, ' A ');
  assert.ok(output.includes('<decimal>1.2300</decimal>')); assert.ok(output.includes('&#13;'));
});
for (const [name, text] of Object.entries({attributes: '<C><a unit="mm">1</a></C>', namespace: '<x:C><a>1</a></x:C>', declaredNamespace: '<C xmlns="urn:x"><a>1</a></C>', mixedText: '<C><a>prefix<b>1</b></a></C>', nonXmlWhitespace: '<C>&#160;<a>1</a></C>', repeatedLeaf: '<C><a>1</a><a>2</a></C>', repeatedContainers: '<C><g><a>1</a></g><g><b>2</b></g></C>', dtd: '<!DOCTYPE C [<!ENTITY x "y">]><C><a>&x;</a></C>', comment: '<C><!-- comment --><a>1</a></C>', cdata: '<C><a><![CDATA[text]]></a></C>', processingInstruction: '<C><?custom x?><a>1</a></C>', unknownEntity: '<C><a>&unknown;</a></C>', invalidCharacter: '<C><a>&#0;</a></C>', mismatchedClose: '<C><a>1</b></C>', trailingRoot: '<C><a>1</a></C><C/>', rootValue: '<C>42</C>'})) {
  test('XML rejects unsupported or lossy ' + name, () => assert.throws(() => S.parseText(text, 'sample.xml')));
}
test('XML root and nested sibling uniqueness are both checked', () => assert.throws(() => S.parseText('<C><outer><g><a>1</a></g><g><b>2</b></g></outer></C>', 'sample.xml'), e => e.code === 'XML_REPEATED'));
test('Export rejects path collisions in either insertion order', () => {
  const p = [parameter('a', '/a', 1, 'number'), parameter('b', '/a/b', 2, 'number')];
  assert.throws(() => S.serialize(p, 'json'), e => e.code === 'PATH_COLLISION'); assert.throws(() => S.serialize(p.reverse(), 'xml'), e => e.code === 'PATH_COLLISION');
  assert.throws(() => S.serialize([parameter('a', '/x', 1), parameter('b', '/x', 2)], 'json'));
});
test('Export rejects null, unsafe numeric JS values, incompatible XML names and invalid control characters', () => {
  assert.throws(() => S.serialize([parameter('a', '/a', null, 'string')], 'json'));
  assert.throws(() => S.serialize([parameter('a', '/a', 9007199254740992, 'number')], 'json'));
  assert.throws(() => S.serialize([parameter('a', '/not an xml name', 'v', 'string')], 'xml'));
  assert.throws(() => S.serialize([parameter('a', '/a', '\u0000', 'string')], 'xml'));
});
test('URL composition encodes values and redaction removes query secrets', () => {
  assert.equal(S.composeUrl('https://config.example.invalid/{model}/{version}/files.json', {model: 'A/B', version: '1 2'}), 'https://config.example.invalid/A%2FB/1%202/files.json');
  assert.throws(() => S.composeUrl('https://x.invalid/{unknown}', {}));
  assert.equal(S.redactUrl('https://user:password@x.invalid/file?signature=secret#token'), 'https://x.invalid/file?…');
});
for (const kind of ['file', 'html', 'zip', 'json', 'xml']) test('Detection returns explicitly simulated ' + kind + ' fixture', async () => {
  const f = fixture(), draft = S.sampleDraft(kind); const preview = await f.engine.detect('ws-prod', draft);
  assert.equal(preview.kind, kind); assert.equal(preview.mock, true); assert.equal(preview.rulesExecuted, false); assert.equal(preview.sampled, preview.files.length); assert.equal(preview.workspaceId, 'ws-prod');
  assert.ok(preview.files.every(e => e.status === 'demo')); assert.ok(preview.message.includes('不证明'));
});
test('Source detection refuses real websites, unsupported protocols and embedded credentials', async () => {
  const f = fixture();
  for (const url of ['https://example.com/file.xml', 'file:///sample.xml', 'https://user:pass@x.invalid/file.xml']) await assert.rejects(f.engine.detect('ws-prod', Object.assign(S.sampleDraft('file'), {url})));
});
test('Source detection works from immutable input; save refuses subsequently changed rules', async () => {
  const f = fixture(), draft = S.sampleDraft('file'); const pending = f.engine.detect('ws-prod', draft);
  draft.url = S.sampleDraft('json').url; draft.kind = 'json'; const preview = await pending;
  assert.equal(preview.kind, 'file'); assert.throws(() => f.engine.save('ws-prod', draft, preview), e => e.code === 'STALE_TEST');
});
test('Invalidating a context cancels late recognition and prior preview reuse', async () => {
  const f = fixture(), draft = S.sampleDraft('html'), pending = f.engine.detect('ws-prod', draft); f.engine.invalidate('source-editor');
  await assert.rejects(pending, e => e.code === 'STALE_CONTEXT');
  const preview = await f.engine.detect('ws-prod', draft); f.engine.invalidate(); assert.throws(() => f.engine.save('ws-prod', draft, preview), e => e.code === 'STALE_TEST');
});
test('Newer detection supersedes older result and cancellation is explicit', async () => {
  const f = fixture(), old = f.engine.detect('ws-prod', S.sampleDraft('file')), latest = f.engine.detect('ws-prod', S.sampleDraft('zip'));
  await assert.rejects(old, e => e.code === 'STALE_CONTEXT'); assert.equal((await latest).kind, 'zip');
  const controller = new AbortController(), pending = f.engine.detect('ws-prod', S.sampleDraft('html'), {signal: controller.signal}); controller.abort(); await assert.rejects(pending, e => e.code === 'CANCELLED');
});
test('A preview cannot be saved in another workspace or after files were retargeted outside it', async () => {
  const f = fixture(), draft = S.sampleDraft('html'), preview = await f.engine.detect('ws-prod', draft);
  assert.throws(() => f.engine.save('ws-lab', draft, preview), e => e.code === 'STALE_TEST');
  preview.files[0].target = 'outside'; assert.throws(() => f.engine.save('ws-prod', draft, preview), e => e.code === 'TARGET_NOT_FOUND');
});
test('Name/schedule changes preserve test validity, rules and mappings survive reopen', async () => {
  const f = fixture(), draft = Object.assign(S.sampleDraft('json'), {path: '/releases', urlField: 'asset'}), before = f.engine.fingerprint('ws-prod', draft), preview = await f.engine.detect('ws-prod', draft);
  draft.name = 'Renamed'; draft.schedule = '每天 09:00'; assert.equal(f.engine.fingerprint('ws-prod', draft), before);
  preview.files[0].selected = false; preview.files[1].target = 'f-machine'; preview.files[1].branchId = 'base-691';
  const saved = f.engine.save('ws-prod', draft, preview, {actorId: 'wang'}), reopened = f.engine.draft('ws-prod', saved.id), next = await f.engine.detect('ws-prod', reopened);
  assert.equal(reopened.path, '/releases'); assert.equal(reopened.urlField, 'asset'); assert.equal(next.files[0].selected, false); assert.equal(next.files[1].target, 'f-machine'); assert.equal(f.events.at(-1).actorId, 'wang');
});
test('Duplicate targets, changed source identities and empty selection cannot be saved', async () => {
  const f = fixture(), draft = S.sampleDraft('html'), preview = await f.engine.detect('ws-prod', draft);
  const duplicate = clone(preview); duplicate.files[1].target = duplicate.files[0].target; assert.throws(() => f.engine.save('ws-prod', draft, duplicate), e => e.code === 'MAPPING_COLLISION');
  const changed = clone(preview); changed.files[0].path = '/different'; assert.throws(() => f.engine.save('ws-prod', draft, changed), e => e.code === 'MAPPING_INVALID');
  preview.files.forEach(e => {e.selected = false;}); assert.throws(() => f.engine.save('ws-prod', draft, preview), e => e.code === 'MAPPING_EMPTY');
});
test('Concurrent rule edits are rejected by saved source version', async () => {
  const f = fixture(), {saved} = await f.source(), one = f.engine.draft('ws-prod', saved.id), two = clone(one);
  const p1 = await f.engine.detect('ws-prod', one, {contextKey: 'one'}), p2 = await f.engine.detect('ws-prod', two, {contextKey: 'two'});
  one.name = 'First editor'; f.engine.save('ws-prod', one, p1); two.name = 'Second editor';
  assert.throws(() => f.engine.save('ws-prod', two, p2), e => e.code === 'STALE_SOURCE');
});
test('A run is deduplicated and never mutates the managed file', async () => {
  const f = fixture(), {saved} = await f.source(), before = clone(f.head('ws-prod')), p1 = f.engine.run('ws-prod', saved.id), p2 = f.engine.run('ws-prod', saved.id);
  assert.strictEqual(p1, p2); const [r1, r2] = await Promise.all([p1, p2]); assert.equal(r1.id, r2.id); assert.equal(f.engine.listRuns('ws-prod').length, 1); assert.deepEqual(f.head('ws-prod'), before); assert.equal(f.engine.get('ws-prod', saved.id).status, 'ready');
});
test('Completion stays in original workspace and captures initiating actor', async () => {
  const f = fixture(), {saved} = await f.source('ws-prod', 'file'); const run = f.engine.run('ws-prod', saved.id, {actorId: 'wang'}); f.setActor('guest'); f.engine.list('ws-lab'); const record = await run;
  assert.equal(record.workspaceId, 'ws-prod'); assert.equal(record.actorId, 'wang'); assert.equal(f.engine.listRuns('ws-lab').length, 0); assert.equal(f.events.at(-1).workspaceId, 'ws-prod'); assert.equal(f.events.at(-1).actorId, 'wang');
});
test('Different workspaces with identical source IDs have independent running locks and candidates', async () => {
  const f = fixture({seed: {'ws-prod': [{id: 'same', name: 'prod', kind: 'file', files: ['f-machine']}], 'ws-lab': [{id: 'same', name: 'lab', kind: 'file', files: ['f-machine']}]}});
  const p = f.engine.run('ws-prod', 'same'), l = f.engine.run('ws-lab', 'same'); assert.notStrictEqual(p, l); await Promise.all([p, l]);
  assert.equal(f.engine.candidates('ws-prod', 'same')[0].headId, 'ws-prod:f-machine:r1'); assert.equal(f.engine.candidates('ws-lab', 'same')[0].headId, 'ws-lab:f-machine:r1');
});
test('Source settings cannot be replaced while its run is active', async () => {
  const f = fixture(), {saved} = await f.source(), draft = f.engine.draft('ws-prod', saved.id), preview = await f.engine.detect('ws-prod', draft), running = f.engine.run('ws-prod', saved.id);
  assert.throws(() => f.engine.save('ws-prod', draft, preview), e => e.code === 'SOURCE_RUNNING'); await running;
});
test('Failed runs and cancellation retain previous good candidates', async () => {
  const f = fixture(), {saved} = await f.source(); await f.engine.run('ws-prod', saved.id); const before = f.engine.candidates('ws-prod', saved.id);
  const failed = await f.engine.run('ws-prod', saved.id, {fail: true}); assert.equal(failed.status, 'error'); assert.deepEqual(f.engine.candidates('ws-prod', saved.id), before);
  const controller = new AbortController(), pending = f.engine.run('ws-prod', saved.id, {signal: controller.signal}); controller.abort(); const cancelled = await pending;
  assert.equal(cancelled.status, 'cancelled'); assert.deepEqual(f.engine.candidates('ws-prod', saved.id), before); assert.notEqual(f.engine.get('ws-prod', saved.id).status, 'running');
});
test('Partial file failure keeps good independent results and prior failed-file candidate', async () => {
  const f = fixture(), {saved} = await f.source(); await f.engine.run('ws-prod', saved.id); const prior = f.engine.candidates('ws-prod', saved.id).find(c => c.fileId === 'f-process');
  const result = await f.engine.run('ws-prod', saved.id, {failPaths: ['/files/ProcessRecipe.json']});
  assert.equal(result.status, 'partial'); assert.equal(result.files, 1); assert.equal(result.errors.length, 1); assert.deepEqual(f.engine.candidates('ws-prod', saved.id).find(c => c.fileId === 'f-process'), prior);
});
test('A candidate cannot be applied to another workspace or a newer target head', async () => {
  const f = fixture(), {saved} = await f.source('ws-prod', 'file'); await f.engine.run('ws-prod', saved.id); const candidate = f.engine.candidates('ws-prod', saved.id)[0];
  assert.throws(() => f.engine.toDraft('ws-lab', saved.id, candidate.id), e => e.code === 'SOURCE_NOT_FOUND');
  f.saveHead('ws-prod', 'f-machine', f.head('ws-prod').params, 'concurrent-r2'); assert.throws(() => f.engine.toDraft('ws-prod', saved.id, candidate.id), e => e.code === 'STALE_HEAD');
});
test('Applying requires a verified saved revision and closes the run-review-save loop', async () => {
  const f = fixture(), {saved} = await f.source('ws-prod', 'file'); await f.engine.run('ws-prod', saved.id); const c = f.engine.candidates('ws-prod', saved.id)[0], draft = f.engine.toDraft('ws-prod', saved.id, c.id);
  assert.equal(draft.candidateId, c.id); assert.equal(draft.sourceId, saved.id);
  assert.equal(f.engine.markApplied('ws-prod', saved.id, c.id, f.head('ws-prod').id).applied, false);
  f.saveHead('ws-prod', 'f-machine', draft.params, 'adopted-r2');
  assert.equal(f.engine.markApplied('ws-prod', saved.id, c.id, 'wrong-revision').applied, false);
  assert.equal(f.engine.markApplied('ws-prod', saved.id, c.id, 'adopted-r2', {actorId: 'lin'}).applied, true);
  assert.equal(f.engine.get('ws-prod', saved.id).pending, 0); assert.equal((await f.engine.run('ws-prod', saved.id)).status, 'unchanged');
});
test('Changing actual extraction rules invalidates old candidates; metadata-only changes retain them', async () => {
  const f = fixture(), {saved} = await f.source('ws-prod', 'file'); await f.engine.run('ws-prod', saved.id); const before = f.engine.candidates('ws-prod', saved.id);
  const metadata = f.engine.draft('ws-prod', saved.id), preview = await f.engine.detect('ws-prod', metadata); metadata.name = 'Renamed'; f.engine.save('ws-prod', metadata, preview);
  assert.deepEqual(f.engine.candidates('ws-prod', saved.id), before);
  const rules = f.engine.draft('ws-prod', saved.id); rules.include = 'Machine*.xml'; const updated = await f.engine.detect('ws-prod', rules); f.engine.save('ws-prod', rules, updated);
  assert.deepEqual(f.engine.candidates('ws-prod', saved.id), []); assert.equal(f.engine.get('ws-prod', saved.id).pending, 0);
});
test('Public source and candidate results cannot mutate stored state', async () => {
  const f = fixture(), {saved} = await f.source('ws-prod', 'file'); const exposed = f.engine.get('ws-prod', saved.id); exposed.name = 'Mutated'; assert.notEqual(f.engine.get('ws-prod', saved.id).name, 'Mutated');
  await f.engine.run('ws-prod', saved.id); const candidate = f.engine.candidates('ws-prod', saved.id)[0]; candidate.params[0].value = 999;
  assert.notEqual(f.engine.candidates('ws-prod', saved.id)[0].params[0].value, 999);
});
