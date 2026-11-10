const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const context = vm.createContext({window: {MC: {}}});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/js/views/config-editor.js'), 'utf8'), context);
const editor = context.window.MC.configEditor;
const member = (value, intent = 'inherit') => ({membership:'included',enabled:true,value_state:'present',value,intent});

test('restoring a value restores its implicit mode and clears the edit', () => {
  const original = member('0');
  const changed = editor.change(original, member('1'), true, false);
  assert.equal(changed.intent, 'override');
  assert.equal(editor.change(original, member('0', 'override'), true, false), null);
});
test('equal values do not hide an explicit change in mode', () => {
  assert.equal(editor.change(member('0'), member('0','pin'), true, true).intent, 'pin');
});
test('fixed values stay fixed when their value is edited', () => {
  assert.equal(editor.change(member('1','pin'), member('2','override'), true, false).intent, 'pin');
});
test('large numbers remain exact text', () => {
  const value = '900719925474099312345678901234567890.001';
  assert.equal(editor.error({type:'number'}, member(value)), '');
  assert.equal(editor.change(member('0'), member(value), false, false).value, value);
});
test('false, zero, empty string, null and unset remain distinct', () => {
  assert.equal(editor.error({type:'bool'}, member('false')), '');
  assert.equal(editor.error({type:'number'}, member('0')), '');
  assert.equal(editor.error({type:'string'}, member('')), '');
  const keys = [member('false'),member('0'),member(''),{value_state:'null'},{value_state:'unset'}].map(row=>editor.key(row,false));
  assert.equal(new Set(keys).size, 5);
});
test('invalid numeric and boolean input has an actionable error', () => {
  assert.ok(editor.error({type:'number'}, member('')));
  assert.ok(editor.error({type:'number'}, member('NaN')));
  assert.ok(editor.error({type:'int'}, member('1.5')));
  assert.ok(editor.error({type:'bool'}, member('0')));
});
test('multi-page edits persist in CAS batches and keep the uncompleted portion on failure', async () => {
  const edits = Object.fromEntries(Array.from({length:250}, (_,i)=>['p'+i,member(String(i))]));
  const completed = [], calls = [];
  await assert.rejects(editor.persist({edits, branch:true, draft:{draft_id:'md-1',revision:4,values:{}}, current:()=>true,
    write:async (items,expected)=>{calls.push([items.length,expected]); if(calls.length===2) throw {status:409}; return {revision:5,values:Object.fromEntries(items.map(row=>[row.parameter_id,row]))};},
    saved:(ids,draft)=>{completed.push(...ids); assert.equal(draft.revision,5);}, read:()=>{throw Error('409 must not be retried');}
  }));
  assert.deepEqual(calls,[[100,4],[100,5]]); assert.equal(completed.length,100);
});
test('lost write response is accepted only after an exact draft readback', async () => {
  let reads=0, saved=0;
  const draft={draft_id:'md-1',revision:2,values:{p:member('1')}};
  const result=await editor.persist({edits:{p:member('2')},draft,branch:true,current:()=>true,
    write:async ()=>{throw {status:0};}, read:async ()=>{reads++; return {...draft,revision:3,values:{p:member('2')}};}, saved:()=>saved++});
  assert.equal(reads,1); assert.equal(saved,1); assert.equal(result.revision,3);
  await assert.rejects(editor.persist({edits:{p:member('2')},draft,branch:true,current:()=>true,
    write:async ()=>{throw {status:0};}, read:async ()=>({...draft,revision:3,values:{p:member('2'),other:member('9')}}),
    saved:()=>{throw Error('unreviewed concurrent values');}}));
});
test('context changes stop later batches', async () => {
  let active=true,calls=0;
  await assert.rejects(editor.persist({edits:Object.fromEntries(Array.from({length:101},(_,i)=>['p'+i,member('1')])),
    draft:{draft_id:'md-1',revision:0,values:{}},current:()=>active,branch:true,
    write:async ()=>{calls++; return {revision:1};},saved:()=>{active=false;}}));
  assert.equal(calls,1);
});
test('conflict recovery preserves independent changes and requires decisions for values or modes changed on both sides', () => {
  const original={p:member('1'),q:member('0'),mode:member('0'),same:member('1')};
  const edits={p:member('2'),q:member(''),mode:member('0','pin'),same:member('3')};
  const latest={p:member('1'),q:{membership:'included',enabled:true,value_state:'null'},mode:member('0','override'),same:member('3'),other:member(false)};
  const result=editor.rebase(original,edits,latest,true,false);
  assert.equal(result.edits.p.value,'2');
  assert.deepEqual(Array.from(result.conflicts, row=>row.parameter_id),['q','mode']);
  assert.equal(result.edits.same,undefined);
  assert.equal(editor.rebase(original,{p:member('2')},latest,true,true).conflicts.length,1);
});
