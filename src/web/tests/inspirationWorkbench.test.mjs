import test from 'node:test';
import assert from 'node:assert/strict';
import { load } from './storySupport.mjs';

const { createRequestScope, sameBrief, committedInspirationSnapshot } = load('../src/pages/inspirationState.ts');
const brief = { requirement:'合成观测员', detail:'保留访客关系', borrow:'开场节奏', avoid:'重复对白' };

test('task switching and deletion reject an earlier pending result, including switching back to the same task', async () => {
  const scope = createRequestScope();
  let resolveFirst;
  let selected = 'task-a';
  const ownsFirst = scope.capture();
  const first = new Promise(resolve => { resolveFirst = resolve; }).then(value => { if (ownsFirst()) selected = value; });
  scope.advance(); selected = 'task-b';
  scope.advance(); selected = 'task-a';
  resolveFirst('old-task-a-snapshot'); await first;
  assert.equal(selected, 'task-a');
  const ownsCurrent = scope.capture();
  assert.equal(ownsCurrent(), true);
  scope.advance(); // deletion / unmount
  assert.equal(ownsCurrent(), false);
});

test('a successful brief save recognizes only its submitted snapshot; every field preserves later typing', () => {
  const submitted = { ...brief };
  assert.equal(sameBrief(submitted, { ...brief }), true);
  for (const key of ['requirement','detail','borrow','avoid']) {
    assert.equal(sameBrief({ ...brief, [key]: `${brief[key]} · 新修改` }, submitted), false, key);
  }
  assert.deepEqual(submitted, brief);
});

test('independent search, preview and task owners do not invalidate each other', () => {
  const search = createRequestScope(), preview = createRequestScope(), task = createRequestScope();
  const searchOwns = search.capture(), previewOwns = preview.capture(), taskOwns = task.capture();
  preview.advance();
  assert.equal(previewOwns(), false);
  assert.equal(searchOwns(), true);
  assert.equal(taskOwns(), true);
  search.advance();
  assert.equal(searchOwns(), false);
  assert.equal(taskOwns(), true);
});

test('reopening a committed inspiration draft freezes current role content and the same revision instead of stale history', () => {
  const draft = { id:'historical-draft', payload:{name:'旧姓名',description:'旧原稿'}, aliases:['旧别名'], committed_character_id:'saved-character' };
  const role = { id:'saved-character', revision:7, card:{name:'正式姓名',description:'其他编辑页的新原稿',tags:['新标签']}, aliases:['正式别名'] };
  const opened = committedInspirationSnapshot(draft,role);
  assert.equal(opened.draft.payload.description,'其他编辑页的新原稿');
  assert.deepEqual(opened.draft.aliases,['正式别名']);
  assert.deepEqual(opened.identity,{id:'saved-character',revision:7});
  opened.draft.payload.tags.push('窗口本地修改');
  assert.deepEqual(role.card.tags,['新标签']);
  assert.equal(draft.payload.description,'旧原稿');
  assert.throws(() => committedInspirationSnapshot(draft,{ ...role,id:'different-character' }),/身份与修订/);
  assert.throws(() => committedInspirationSnapshot(draft,{ ...role,revision:undefined }),/身份与修订/);
});
