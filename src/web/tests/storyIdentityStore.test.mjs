import { storyImport } from './storySupport.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { createClientId } from '../src/utils/clientId.js';
const require = createRequire(import.meta.url);
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const snapshot = (id, stage, revision) => ({ session: { id, branch_revision: revision, player_identity_id: stage },
  messages: [], characters: [], groups: [], lorebooks: [], scenes: [], active_scene_id: null, event_cursor: null });
function harness(api) {
  api.getStoryView ??= api.getSessionSnapshot;
  const exports = {};
  const imports = {
    zustand: require('zustand'), '../api/client': { api }, '../features/stories/storyApi': { storyApi: api }, '../api/adapters': { toView: (value) => value },
    '../api/navigation': { readStoryLocation: () => ({}), writeStoryLocation: () => {} },
    '../utils/channels': { parseChannelParts: (text) => [{ kind: 'roleplay', text }] },
    '../utils/assistFreshness': {}, '../utils/clientId.js': { createClientId },
    '../utils/optimisticMessages': { pendingAfterSnapshot: () => [], removeConfirmedLocalSegment: (messages) => messages },
    '../types': { SSE_EVENT_TYPES: [] },
    '../api/sse': { connectSSE: () => ({ addEventListener: () => {}, close: () => {} }) },
  };
  const source = readFileSync(new URL('../src/store/chatStore.ts', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  new Function('require', 'exports', compiled)((name) => imports[name] ?? storyImport(name, imports) ?? require(name), exports);
  return exports.useChatStore;
}
test('A → B → A branch loads discard an earlier request for the same branch', async () => {
  const slow = deferred(); let aCalls = 0;
  const store = harness({ getSessionSnapshot: async (id) => id === 'a' && ++aCalls === 1 ? slow.promise : snapshot(id, `${id}-new`, 5) });
  store.setState({ sessions: [{ id: 'a' }, { id: 'b' }] });
  const first = store.getState().selectSession('a', false);
  await store.getState().selectSession('b', false);
  await store.getState().selectSession('a', false);
  slow.resolve(snapshot('a', 'a-old', 1)); await first;
  assert.equal(store.getState().sessions.find((s) => s.id === 'a').player_identity_id, 'a-new');
});
test('an old summary cannot restore a previous identity roster after a switch', async () => {
  const store = harness({ listSessions: async () => [{ id: 'a', branch_revision: 1, character_ids: ['old-npc'] }] });
  store.setState({ sessions: [{ id: 'a', branch_revision: 5, player_identity_id: 'new-stage', character_ids: ['new-npc'] }] });
  await store.getState().loadSessions();
  assert.deepEqual(store.getState().sessions[0].character_ids, ['new-npc']);
});
test('a late resync snapshot cannot overwrite the successful switch response', async () => {
  const slow = deferred();
  let reads = 0;
  const store = harness({ getSessionSnapshot: () => ++reads === 1 ? slow.promise : Promise.resolve(snapshot('a', 'new', 5)), switchPlayer: async () => snapshot('a', 'new', 5) });
  store.setState({ currentSessionId: 'a', sessions: [{ id: 'a', branch_revision: 1, player_identity_id: 'old' }] });
  store.getState().handleSseEvent({ type: 'session.player.changed' });
  await store.getState().switchPlayer({});
  slow.resolve(snapshot('a', 'old', 1)); await new Promise((r) => setImmediate(r));
  assert.equal(store.getState().sessions[0].player_identity_id, 'new');
  assert.equal(store.getState().switchingPlayer, false);
});
