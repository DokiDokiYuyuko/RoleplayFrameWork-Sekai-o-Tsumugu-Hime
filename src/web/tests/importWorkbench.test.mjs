import test from 'node:test';
import assert from 'node:assert/strict';
import { load } from './storySupport.mjs';
const { sameImportInput, importStatusLabel, committedImportRevision, readImportReviewAliases, saveImportReviewAliases, clearImportReviewAliases } = load('../src/features/asset-import/importWorkbenchState.ts');

test('a retry reuses the created import task only for the same submitted source and destination', () => {
  const submitted = { source:'合成世界原稿', target:'background', worldId:'synthetic-world' };
  assert.equal(sameImportInput(submitted, { ...submitted }), true);
  for (const key of ['source','target','worldId']) {
    assert.equal(sameImportInput(submitted, { ...submitted,[key]:`${submitted[key]}-changed` }), false);
  }
});

test('import task states have Chinese presentation without leaking new backend enum values', () => {
  for (const state of ['ready','analyzing','classified','generating','regenerating','review','needs_review','completed','committed','saved','failed','interrupted','cancelled','future_backend_value']) {
    const label = importStatusLabel(state);
    assert.notEqual(label,state);
    assert.match(label,/\p{Script=Han}/u);
  }
  assert.equal(importStatusLabel('classified'),'待确认候选');
  assert.equal(importStatusLabel('interrupted'),'已中断');
});

test('retained review freezes the exact commit revision rather than a newer background refresh', () => {
  assert.equal(committedImportRevision({target_asset_id:null,target_revision:null}),1);
  assert.equal(committedImportRevision({target_asset_id:'synthetic-role',target_revision:8}),9);
  assert.throws(() => committedImportRevision({target_asset_id:'synthetic-role',target_revision:null}),/提交修订/);
});

test('unsupported draft aliases are explicitly staged locally for exactly one saved draft revision', () => {
  const rows = new Map();
  const storage = { getItem:key => rows.get(key),setItem:(key,value)=>rows.set(key,value),removeItem:key=>rows.delete(key) };
  saveImportReviewAliases(storage,'synthetic-job','draft-a',4,['本机暂存别名']);
  assert.deepEqual(readImportReviewAliases(storage,'synthetic-job','draft-a',4,[]),['本机暂存别名']);
  assert.deepEqual(readImportReviewAliases(storage,'synthetic-job','draft-a',5,['服务器别名']),['服务器别名']);
  assert.deepEqual(readImportReviewAliases(storage,'synthetic-job','draft-b',4,[]),[]);
  saveImportReviewAliases(storage,'synthetic-job','draft-a',4,['目标A别名'],'target-a');
  assert.deepEqual(readImportReviewAliases(storage,'synthetic-job','draft-a',4,['目标B原别名'],'target-b'),['目标B原别名']);
  clearImportReviewAliases(storage,'synthetic-job','draft-a');
  assert.deepEqual(readImportReviewAliases(storage,'synthetic-job','draft-a',4,[]),[]);
});
