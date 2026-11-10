const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const scope = vm.createContext({window: {MC: {util: {}}}});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/js/views/config-evolution.js'), 'utf8'), scope);
const project = scope.window.MC.configEvolution.project;

function fixture(query = '', filter = 'all') {
  const releases = [{release_id: 'r1', version: '690', order: 1, default_revision_id: 'd0-r1'},
    {release_id: 'r2', version: '691', order: 2, default_revision_id: 'd0-r2'}];
  const records = [
    {id: 'd0-r1', kind: 'default', release_id: 'r1', status: 'published'},
    {id: 'd0-r2', kind: 'default', release_id: 'r2', status: 'published'},
    {id: 'b2', label: 'B2', kind: 'baseline', release_id: 'r1', status: 'published'},
    {id: 'b10', label: 'B10', kind: 'baseline', release_id: 'r1', status: 'published'},
    {id: 'b11', label: 'B11', kind: 'baseline', release_id: 'r2', status: 'published'},
    {id: 'm1', label: 'r1', kind: 'machine', machine_id: 'machine', release_id: 'r1', status: 'published', source: 'b2'},
    {id: 'draft', kind: 'machine', machine_id: 'machine', release_id: 'r2', status: 'draft', source: 'b11'}
  ];
  const machines = [{machine_id: 'machine', machine_no: 'MC-001', release_id: 'r2', head_id: 'm1'}];
  return {releases, records, machines, groups: project(releases, records, machines, query, filter)};
}

test('standards use numeric saved sequence within each software release', () => {
  const {groups} = fixture();
  assert.equal(groups[0].standard.id, 'b10');
  assert.equal(groups[1].standard.id, 'b11');
});
test('a machine and its target draft remain one row under its saved version', () => {
  const {groups} = fixture();
  assert.equal(groups[0].machines.length, 1);
  assert.equal(groups[1].machines.length, 0);
  assert.equal(groups[0].machines[0].draft.release_id, 'r2');
  assert.equal(groups[0].machines[0].saved.source, 'b2');
});
test('machine and software-version searches respect saved ownership', () => {
  assert.equal(fixture('  mc-001 ').groups.length, 1);
  assert.equal(fixture('691').groups[0].machines.length, 0);
  assert.equal(fixture('missing').groups.length, 0);
  assert.equal(fixture('', 'draft').groups[0].release.release_id, 'r1');
});
test('separately declared missing initial snapshot is not fabricated from a saved standard', () => {
  const data = fixture();
  data.releases[0].initial_revision_id = null;
  assert.equal(project(data.releases, data.records, data.machines, '', 'all')[0].initial, null);
});
test('new machine with no saved head appears once as a draft on its chosen version', () => {
  const data = fixture();
  data.machines[0].head_id = null;
  const groups = project(data.releases, data.records, data.machines, '', 'all');
  assert.equal(groups[0].machines.length, 0);
  assert.equal(groups[1].machines.length, 1);
  assert.equal(groups[1].machines[0].saved, null);
});

test('hierarchy separates saved machines from new drafts and retains full long names', () => {
  function h(tag, ...args) {
    const attrs = args[0] && !Array.isArray(args[0]) && typeof args[0] === 'object' && !args[0].tag ? args.shift() : {};
    return {tag, attrs, style: {}, children: args.flat(Infinity), appendChild(child) { this.children.push(child); }};
  }
  let uid = 0;
  scope.window.MC.util.h = h;
  scope.window.MC.util.uid = () => 'rows-' + (++uid);
  scope.window.MC.util.formatTime = () => '今天';
  scope.window.MC.icon = () => h('svg');
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/js/views/config-evolution.js'), 'utf8'), scope);
  const data = fixture();
  data.groups[0].machines.push({machine: {machine_id: 'new', machine_no: 'MC-' + 'Long'.repeat(40)}, saved: null, draft: {id: 'new-draft'}});
  const tree = scope.window.MC.configEvolution.hierarchy(data.groups, {collapsed: new Set(), canWrite: true, versionName: () => '691', open() {}, edit() {}, toggle() {}});
  function all(n) { return n && typeof n === 'object' ? [n, ...n.children.flatMap(all)] : []; }
  const nodes = all(tree);
  const counts = nodes.filter(n => n.tag.includes('cv32-machine-count'));
  assert.ok(counts.some(n => n.children.includes('1 台机台 · 1 台待保存')));
  const link = nodes.find(n => n.attrs['data-machine-link'] === 'new');
  assert.equal(link.attrs.title, 'MC-' + 'Long'.repeat(40));
  assert.equal(link.children[0].tag, 'span.cv32-name-text');
  const emptyToggle = nodes.find(n => n.attrs['data-version-toggle'] === 'r2');
  assert.equal(emptyToggle.disabled, true);
});
