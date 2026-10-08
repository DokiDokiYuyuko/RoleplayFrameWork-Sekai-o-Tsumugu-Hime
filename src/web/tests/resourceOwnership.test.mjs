import test from 'node:test';
import assert from 'node:assert/strict';
import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { load } from './storySupport.mjs';
const flush = () => new Promise(resolve => setImmediate(resolve));
function harness(read) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const options = { queryKey: ['characters'], queryFn: read, staleTime: 15000 };
  const { createQueryFacade } = load('../src/features/resources/queryFacade.ts', { '../../queryClient': { queryClient } });
  const facade = createQueryFacade('characters', options, (set) => ({ characters: [], selected: null,
    select: selected => set({ selected }), upsert: characters => set({ characters }), load: () => queryClient.fetchQuery(options) }));
  return { queryClient, options, facade };
}
test('Query is the sole resource owner across facade and direct consumers and deduplicates concurrent reads', async () => {
  let reads = 0, finish;
  const h = harness(() => { reads++; return new Promise(resolve => { finish = resolve; }); });
  const first = h.facade.getState().load(), direct = h.queryClient.fetchQuery(h.options);
  assert.equal(reads, 1); finish([{ id: 'synthetic-asset', revision: 1 }]); await Promise.all([first, direct]);
  assert.equal(h.facade.getState().characters, h.queryClient.getQueryData(h.options.queryKey));
  h.queryClient.setQueryData(h.options.queryKey, [{ id: 'synthetic-asset', revision: 2 }]);
  assert.equal(h.facade.getState().characters[0].revision, 2);
  h.facade.getState().upsert([{ id: 'synthetic-asset', revision: 3 }]);
  assert.equal(h.queryClient.getQueryData(h.options.queryKey)[0].revision, 3); h.queryClient.clear();
});
test('local selection survives remote invalidation and active observers see mutation updates', async () => {
  let revision = 1;
  const h = harness(async () => [{ id: 'synthetic-asset', revision: revision++ }]);
  const observer = new QueryObserver(h.queryClient, h.options), observed = [];
  const stop = observer.subscribe(result => { if (result.data) observed.push(result.data); });
  await h.facade.getState().load(); h.facade.getState().select('unsaved selection');
  await h.queryClient.invalidateQueries({ queryKey: h.options.queryKey });
  assert.equal(h.facade.getState().selected, 'unsaved selection');
  assert.equal(h.facade.getState().characters[0].revision, 2);
  h.facade.getState().upsert([{ id: 'synthetic-asset', revision: 3 }]);
  assert.equal(observed.at(-1)[0].revision, 3); stop(); h.queryClient.clear();
});
test('a completed resource mutation cancels an older read rather than accepting its late body', async () => {
  let finish;
  const h = harness(() => new Promise(resolve => { finish = resolve; }));
  const old = h.facade.getState().load().catch(() => undefined);
  h.facade.getState().upsert([{ id: 'synthetic-asset', revision: 8 }]);
  finish([{ id: 'synthetic-asset', revision: 1 }]); await old; await flush();
  assert.equal(h.facade.getState().characters[0].revision, 8); h.queryClient.clear();
});

test('settings writes serialize and cancel a direct Query read before publishing shared facade data', async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const { readSettings, writeSettings } = load('../src/features/resources/settingsResource.ts', { '../../queryClient': { queryClient } });
  const options = { queryKey: ['settings'], queryFn: () => readSettings(() => new Promise(resolve => { oldFinish = resolve; })) };
  const { createQueryFacade } = load('../src/features/resources/queryFacade.ts', { '../../queryClient': { queryClient } });
  const facade = createQueryFacade('settings', options, () => ({ settings: null, draft: 'player draft' }));
  let oldFinish, firstFinish, secondFinish;
  const direct = queryClient.fetchQuery(options).catch(() => undefined); await flush();
  const calls = [];
  const first = writeSettings(() => { calls.push(1); return new Promise(resolve => { firstFinish = resolve; }); });
  const second = writeSettings(() => { calls.push(2); return new Promise(resolve => { secondFinish = resolve; }); });
  await flush(); assert.deepEqual(calls, [1]);
  firstFinish({ title: 'first saved' }); await first; await flush();
  assert.deepEqual(calls, [1, 2]); secondFinish({ title: 'second saved' }); await second;
  oldFinish({ title: 'old read' }); await direct; await flush();
  assert.equal(queryClient.getQueryData(['settings']).title, 'second saved');
  assert.equal(facade.getState().settings.title, 'second saved'); assert.equal(facade.getState().draft, 'player draft'); queryClient.clear();
});

test('a settings read started during a write waits for the committed server value; failure does not block later writes', async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const { readSettings, writeSettings } = load('../src/features/resources/settingsResource.ts', { '../../queryClient': { queryClient } });
  let finish, readCalls = 0;
  const write = writeSettings(() => new Promise(resolve => { finish = resolve; })); await flush();
  const read = readSettings(async () => { readCalls++; return { title: 'new server value' }; });
  await flush(); assert.equal(readCalls, 0); finish({ title: 'new server value' }); await write;
  assert.equal((await read).title, 'new server value');
  await assert.rejects(writeSettings(async () => { throw new Error('synthetic failed write'); }));
  await writeSettings(async () => ({ title: 'later save' }));
  assert.equal(queryClient.getQueryData(['settings']).title, 'later save'); queryClient.clear();
});

test('changing a compatibility facade scope does not erase a different resource cache', async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const { createQueryFacade } = load('../src/features/resources/queryFacade.ts', { '../../queryClient': { queryClient } });
  queryClient.setQueryData(['scoped', 'a'], [{ id: 'kept-a' }]); queryClient.setQueryData(['scoped', 'b'], [{ id: 'kept-b' }]);
  const facade = createQueryFacade('rows', state => ({ queryKey: ['scoped', state.scope], queryFn: async () => [] }), set => ({ scope: 'a', rows: [], select: scope => set({ scope }) }));
  facade.getState().select('b'); assert.equal(facade.getState().rows[0].id, 'kept-b');
  facade.getState().select(''); assert.deepEqual(facade.getState().rows, []);
  assert.equal(queryClient.getQueryData(['scoped', 'a'])[0].id, 'kept-a'); assert.equal(queryClient.getQueryData(['scoped', 'b'])[0].id, 'kept-b'); queryClient.clear();
});
