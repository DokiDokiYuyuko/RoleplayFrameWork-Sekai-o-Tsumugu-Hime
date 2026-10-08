import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { load as loadModule, receiptModule } from './storySupport.mjs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/store/memoryStore.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
function load(api) {
  const exports = {};
  const { QueryClient } = require('@tanstack/react-query');
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const resourceQueries = { memories: (cid, sid) => ({ queryKey: ['memories', sid ?? '', cid], queryFn: () => api.listMemories(cid, sid) }) };
  const facade = loadModule('../src/features/resources/queryFacade.ts', { react: require('react'), '../../queryClient': { queryClient } });
  const mocks = { '../features/resources/queryFacade': facade, '../features/resources/resourceQueries': { resourceQueries }, '../queryClient': { queryClient }, './chatStore': { useChatStore: { getState: () => ({ sessions: [{ id: 'a', branch_revision: 7 }] }) } }, '../utils/commandReceipt': receiptModule };
  new Function('exports', 'require', compiled)(exports, (name) => name === '../api/client' ? { api } : mocks[name] ?? require(name));
  exports.useMemoryStore.seed = records => queryClient.setQueryData(resourceQueries.memories(exports.useMemoryStore.getState().characterId, exports.useMemoryStore.getState().sessionId).queryKey, records);
  return exports.useMemoryStore;
}

test('same character, different branch: late records and loading cannot replace the active branch', async () => {
  const first = deferred(); const second = deferred();
  const store = load({ listMemories: (_cid, sid) => sid === 'old' ? first.promise : second.promise });
  const a = store.getState().select('guide', 'old');
  const b = store.getState().select('guide', 'new');
  first.resolve([{ id: 'old-memory' }]); await a;
  assert.deepEqual(store.getState().records, []); assert.equal(store.getState().loading, true);
  second.resolve([{ id: 'new-memory' }]); await b;
  assert.equal(store.getState().records[0].id, 'new-memory'); assert.equal(store.getState().sessionId, 'new');
});

test('clear invalidates outstanding requests', async () => {
  const request = deferred(); const store = load({ listMemories: () => request.promise });
  const pending = store.getState().select('guide', 'a'); store.getState().clear();
  request.resolve([{ id: 'stale' }]); await pending;
  assert.deepEqual(store.getState().records, []); assert.equal(store.getState().characterId, null);
});

test('returning to the same role does not revive its first stale request', async () => {
  const first = deferred(); let calls = 0;
  const store = load({ listMemories: async (cid) => {
    calls += 1;
    if (calls === 1) return first.promise;
    return [{ id: cid + '-fresh' }];
  } });
  const stale = store.getState().select('guide', 'a');
  await store.getState().select('guard', 'a');
  await store.getState().select('guide', 'a');
  first.resolve([{ id: 'guide-stale' }]); await stale;
  assert.equal(store.getState().records[0].id, 'guide-fresh');
});

test('late correction cannot mutate another role; revision and branch are sent', async () => {
  const request = deferred(); let args;
  const store = load({ listMemories: async () => [], updateMemory: (...input) => { args = input; return request.promise; } });
  await store.getState().select('guide', 'a');
  store.seed([{ id: 'memory', content: 'old', revision: 3 }]);
  const pending = store.getState().edit('memory', { content: 'corrected' });
  await store.getState().select('guard', 'b');
  request.resolve({ id: 'memory', content: 'corrected' }); await pending;
  assert.deepEqual(store.getState().records, []);
  assert.equal(args[3], 'a'); assert.equal(args[2].expected_revision, 3);
});

test('reviewed memory delete freezes record CAS and an older receipt cannot remove a restored record', async () => {
  let args;
  const result = receiptModule.attachCommandReceipt({ ok: true }, { operation_id: 'old-delete', branch_revision: 6 });
  const store = load({ listMemories: async () => [], deleteMemory: async (...input) => { args = input; return result; } });
  await store.getState().select('guide', 'a'); store.seed([{ id: 'memory', revision: 4, content: 'restored' }]);
  await store.getState().remove('memory', 3);
  assert.equal(args[3], 3); assert.equal(args[4], 7); assert.equal(store.getState().records.length, 1);
});

test('a consolidation job belongs to its selected branch and role and clear preserves another cache consumer', async () => {
  const store = load({ listMemories: async () => [{ id: 'record', revision: 1 }], consolidateAsync: async () => ({ operation_id: 'job-a', scheduled: true, status: 'pending' }) });
  await store.getState().select('guide', 'a'); await store.getState().consolidateNow('a', 'guide');
  assert.equal(store.getState().jobOperationId, 'job-a');
  await store.getState().select('guard', 'b'); assert.equal(store.getState().jobOperationId, null);
  store.getState().clear(); assert.equal(store.getState().jobOperationId, null); assert.deepEqual(store.getState().records, []);
});
