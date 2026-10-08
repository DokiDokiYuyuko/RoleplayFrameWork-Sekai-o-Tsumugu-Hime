import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { load } from './storySupport.mjs';

const require = createRequire(import.meta.url);
function harness(name, read, overrides = {}) {
  const { QueryClient } = require('@tanstack/react-query');
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const resourceQueries = { characters: () => ({ queryKey: ['characters'], queryFn: read }), lorebooks: () => ({ queryKey: ['lorebooks'], queryFn: read }) };
  const facade = load('../src/features/resources/queryFacade.ts', { react: require('react'), '../../queryClient': { queryClient } });
  const imports = {
    '../features/resources/queryFacade': facade, '../features/resources/resourceQueries': { resourceQueries }, '../queryClient': { queryClient },
    zustand: require('zustand'), '@tanstack/react-query': require('@tanstack/react-query'),
    '../api/client': { api: { listCharacters: read, listLorebooks: read, ...overrides } },
    './chatStore': { useChatStore: { setState() {} } },
    '../features/stories/storyRuntime': { storyRuntime: { applyCharacter() {}, capture: () => () => true } },
  };
  const exports = {};
  const source = readFileSync(new URL(`../src/store/${name}Store.ts`, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  new Function('require', 'exports', compiled)((id) => { assert.ok(id in imports, id); return imports[id]; }, exports);
  return exports[name === 'character' ? 'useCharacterStore' : 'useLorebookStore'];
}

for (const name of ['character', 'lorebook']) {
  test(`${name}: a failed library read is explicit and can recover to a genuinely empty list`, async () => {
    let count = 0;
    const store = harness(name, async () => { if (++count === 1) throw new Error('合成读取故障'); return []; });
    assert.equal(store.getState().loadStatus, 'idle');
    await assert.rejects(store.getState().load());
    assert.equal(store.getState().loadStatus, 'error');
    assert.equal(store.getState().loadError, '合成读取故障');
    await store.getState().load();
    assert.equal(store.getState().loadStatus, 'ready');
    assert.equal(store.getState().loadError, null);
    assert.equal(store.getState()[name === 'character' ? 'characters' : 'books'].length, 0);
  });
  test(`${name}: shared startup and page reads use one pending request`, async () => {
    let count = 0, finish;
    const store = harness(name, () => { count++; return new Promise((resolve) => { finish = resolve; }); });
    const first = store.getState().load(), second = store.getState().load();
    assert.equal(count, 1); assert.equal(first, second);
    assert.equal(store.getState().loadStatus, 'loading');
    finish([{ id: 'synthetic-asset' }]); await first;
    assert.equal(store.getState().loadStatus, 'ready');
    assert.equal(store.getState()[name === 'character' ? 'characters' : 'books'][0].id, 'synthetic-asset');
  });
}


test('avatar upload advances only media metadata; saving the draft uses the returned revision', async () => {
  let expected;
  const store = harness('character', async () => [{ id: 'char-test', revision: 1, card: { name: 'Guide', description: 'Original', avatar_path: null } }], {
    uploadAvatar: async (id, file, revision) => { assert.equal(revision, 1); return { revision: 2, avatar_path: 'char-test/avatar.png' }; },
    updateCharacter: async (id, patch) => { expected = patch.expected_revision; return { ...store.getState().characters[0], revision: 3, card: patch.card }; },
  });
  await store.getState().load();
  await store.getState().uploadMedia('char-test', 'avatar', {});
  assert.equal(store.getState().characters[0].card.description, 'Original');
  await store.getState().updateCharacter('char-test', { card: { description: 'Unsaved draft' } });
  assert.equal(expected, 2);
  assert.equal(store.getState().characters[0].card.description, 'Unsaved draft');
});

test('a failed upload never advances the saved revision', async () => {
  const store = harness('character', async () => [{ id: 'char-test', revision: 4, card: {} }], { uploadAvatar: async () => { throw new Error('Upload failed'); } });
  await store.getState().load();
  await assert.rejects(store.getState().uploadMedia('char-test', 'avatar', {}));
  assert.equal(store.getState().characters[0].revision, 4);
});

test('late upload metadata cannot downgrade another completed edit', async () => {
  let finish;
  const store = harness('character', async () => [{ id: 'char-test', revision: 1, card: {} }], { uploadAvatar: () => new Promise((resolve) => { finish = resolve; }) });
  await store.getState().load();
  const pending = store.getState().uploadMedia('char-test', 'avatar', {});
  store.getState().upsert({ id: 'char-test', revision: 3, card: { description: 'New edit' } });
  finish({ revision: 2, avatar_path: 'char-test/avatar.png' }); await pending;
  assert.equal(store.getState().characters[0].revision, 3);
  assert.equal(store.getState().characters[0].card.description, 'New edit');
});
