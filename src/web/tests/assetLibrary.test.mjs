import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
function load(name) {
  const exports = {};
  const compiled = ts.transpileModule(readFileSync(new URL(`../src/features/library/${name}.ts`, import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  new Function('exports', compiled)(exports); return exports;
}
const { readAssetFilters, updateAssetFilters, filterAndSortAssets, selectionCounts, toggleVisibleSelection } = load('assetList');
const { applyTagBatch } = load('tagBatch');
const rows = [{ id:'a',name:'旅人',aliases:['渡鸦'],tags:['星海'],worldId:'world-a',updatedAt:'2026-01-02' },
  { id:'b',name:'合成向导',tags:['星海'],worldId:null,updatedAt:'2026-01-03' },
  { id:'c',name:'合成向导',tags:['草原'],worldId:'world-b',updatedAt:'2026-01-03' }];
test('query, tag, source world and sort survive route updates and legacy unowned links', () => {
  const params = updateAssetFilters(new URLSearchParams('q=渡鸦&tag=星海&world=world-a&extra=keep'), { sort:'name' });
  assert.equal(params.get('extra'),'keep'); assert.equal(readAssetFilters(params).query,'渡鸦');
  assert.deepEqual(filterAndSortAssets(rows,readAssetFilters(params), row=>row).map(row=>row.id),['a']);
  const unowned = readAssetFilters(new URLSearchParams('unowned=1'));
  assert.deepEqual(filterAndSortAssets(rows,unowned,row=>row).map(row=>row.id),['b']);
  assert.equal(updateAssetFilters(new URLSearchParams('unowned=1'),{world:''}).has('unowned'),false);
});
test('recent ordering is deterministic and selection keeps hidden stable IDs', () => {
  assert.deepEqual(filterAndSortAssets(rows,readAssetFilters(new URLSearchParams()),row=>row).map(row=>row.id),['b','c','a']);
  assert.deepEqual(selectionCounts(['a','b'],['b','c']),{total:2,hidden:1});
  assert.deepEqual(toggleVisibleSelection(['a'],['b','c']),['a','b','c']);
  assert.deepEqual(toggleVisibleSelection(['a','b','c'],['b','c']),['a']);
  assert.deepEqual(toggleVisibleSelection(['a'],[]),['a']);
});
test('batch tag mutation uses captured revisions and reports individual failures without rolling back successes', async () => {
  const original = [{id:'a',name:'一',revision:3,tags:['旧']},{id:'b',name:'二',revision:4,tags:[]}];
  const seen=[];
  const result=await applyTagBatch(original,' 新 ',false,async(id,tags,revision)=>{seen.push({id,tags,revision});if(id==='b')throw new Error('资料已更新');});
  assert.deepEqual(seen,[{id:'a',tags:['旧','新'],revision:3},{id:'b',tags:['新'],revision:4}]);
  assert.equal(result[0].ok,true);assert.equal(result[1].ok,false);assert.match(result[1].error,/已更新/);
  assert.deepEqual(original[0].tags,['旧']);
  await assert.rejects(applyTagBatch(original,'',false,async()=>{}),/标签/);
});
