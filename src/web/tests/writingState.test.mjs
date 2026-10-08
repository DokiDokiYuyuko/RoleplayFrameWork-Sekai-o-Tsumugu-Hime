import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function harness(call) {
  const storage = new Map();
  const localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) };
  const chat = { currentSessionId: 'branch-a', sessions: [{ id: 'branch-a', branch_revision: 1, player_identity_id: null }],
    messages: [], activeScene: null, sessionGroups: [] };
  const imports = { zustand: require('zustand'), '../api/client': { api: { draftAssist: call } },
    './chatStore': { useChatStore: { getState: () => chat } } };
  const exports = {};
  const src = readFileSync(new URL('../src/store/writingStore.ts', import.meta.url), 'utf8');
  const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  new Function('require', 'exports', 'localStorage', js)(id => imports[id], exports, localStorage);
  return { ...exports, chat, storage };
}
const result = () => ({ branch_id: 'branch-a', branch_revision: 1, player_identity_id: null,
  options: [1, 2, 3].map(i => ({ title: `Choice ${i}`, text: `Full choice ${i}`, mention_character_id: null })),
  warnings: [], context_version: 'server-context', stale: false });
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }

test('writing task includes separate requirement and draft and preserves output mode', async () => {
  let request;
  const h = harness(async (branch, body) => { request = body; return result(); });
  const store = h.useWritingStore;
  store.getState().openTask('branch-a:legacy', 'Unsent text');
  store.getState().edit('branch-a:legacy', { intent: 'Create a list', type: 'material' });
  await store.getState().generate('branch-a:legacy', 'branch-a', null, 'Unsent text');
  assert.equal(request.intent, 'Create a list');
  assert.equal(request.draft_text, 'Unsent text');
  assert.equal(request.output_type, 'material');
  assert.equal(store.getState().tasks['branch-a:legacy'].batch.options.length, 3);
  assert.deepEqual(h.chat.messages, []);
});

test('editing while model runs retains new input and marks result revision old', async () => {
  const d = deferred(); const h = harness(() => d.promise); const store = h.useWritingStore;
  store.getState().openTask('key', 'First'); store.getState().edit('key', { intent: 'First request' });
  const running = store.getState().generate('key', 'branch-a', null, 'First');
  store.getState().edit('key', { intent: 'New request', draft: 'New draft' });
  d.resolve(result()); await running;
  const task = store.getState().tasks.key;
  assert.equal(task.intent, 'New request'); assert.equal(task.draft, 'New draft');
  assert.notEqual(task.resultRevision, task.revision);
});

test('switching branch while model runs never overwrites another task', async () => {
  const d = deferred(); const h = harness(() => d.promise); const store = h.useWritingStore;
  store.getState().openTask('a', 'First'); store.getState().edit('a', { intent: 'Write' });
  const pending = store.getState().generate('a', 'branch-a', null, 'First');
  h.chat.currentSessionId = 'branch-b'; store.getState().openTask('b', 'Second');
  d.resolve(result()); await pending;
  assert.equal(store.getState().tasks.b.draft, 'Second');
  assert.equal(store.getState().tasks.a.batch.stale, true);
});

test('failure is visible and preserves task and prior candidates', async () => {
  let fail = false; const h = harness(async () => { if (fail) throw new Error('Model offline'); return result(); });
  const store = h.useWritingStore; store.getState().openTask('key', 'Keep'); store.getState().edit('key', { intent: 'Write' });
  await store.getState().generate('key', 'branch-a', null, 'Keep'); fail = true;
  await store.getState().generate('key', 'branch-a', null, 'Keep');
  const t = store.getState().tasks.key;
  assert.equal(t.error, 'Model offline'); assert.equal(t.draft, 'Keep'); assert.equal(t.batch.options.length, 3);
  assert.equal(t.generating, false);
});

test('duplicate clicks start only one model call and persist completed work', async () => {
  const d = deferred(); let calls = 0; const h = harness(() => { calls++; return d.promise; });
  const store = h.useWritingStore; store.getState().openTask('key', 'Keep'); store.getState().edit('key', { intent: 'Write' });
  const first = store.getState().generate('key', 'branch-a', null, 'Keep');
  await store.getState().generate('key', 'branch-a', null, 'Keep'); assert.equal(calls, 1);
  d.resolve(result()); await first;
  store.setState({ tasks: {} }); store.getState().openTask('key', 'Different input');
  assert.equal(store.getState().tasks.key.draft, 'Keep');
  assert.equal(store.getState().tasks.key.batch.stale, true);
});
