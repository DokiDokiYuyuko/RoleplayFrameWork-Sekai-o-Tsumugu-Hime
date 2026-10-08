import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { load } from './storySupport.mjs';
const require = createRequire(import.meta.url);
function harness() {
  const requests = [];
  const api = { getCost: (id) => new Promise((resolve, reject) => requests.push({ id, resolve, reject })) };
  const exports = {};
  const source = ts.transpileModule(readFileSync(new URL('../src/store/costStore.ts', import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const { QueryClient } = require('@tanstack/react-query');
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const resourceQueries = { cost: id => ({ queryKey: ['story-cost', id], queryFn: () => api.getCost(id) }) };
  const facade = load('../src/features/resources/queryFacade.ts', { react: require('react'), '../../queryClient': { queryClient } });
  const mocks = { '../features/resources/queryFacade': facade, '../features/resources/resourceQueries': { resourceQueries }, '../queryClient': { queryClient } };
  new Function('require', 'exports', source)(name => mocks[name] ?? { api }, exports);
  return { store: exports.useCostStore, requests };
}
test('a late cost response cannot replace another branch total', async () => {
  const { store, requests } = harness();
  const a = store.getState().load('branch-a'), b = store.getState().load('branch-b');
  requests[1].resolve({ total: { input_tokens: 20 } }); await b;
  requests[0].resolve({ total: { input_tokens: 100 } }); await a;
  assert.equal(store.getState().sessionId, 'branch-b');
  assert.equal(store.getState().report.total.input_tokens, 20);
});
test('a stale same-branch request cannot replace a newer refresh or its error', async () => {
  const { store, requests } = harness();
  const older = store.getState().load('branch-a'), refresh = store.getState().load('branch-a');
  requests[1].reject(new Error('合成统计读取失败')); await refresh;
  requests[0].resolve({ total: { input_tokens: 100 } }); await older;
  assert.equal(store.getState().report, null);
  assert.equal(store.getState().error, '合成统计读取失败');
});
