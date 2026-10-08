import { storyImport } from './storySupport.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { randomUUID, webcrypto } from 'node:crypto';
import ts from 'typescript';
import { createClientId } from '../src/utils/clientId.js';

const require = createRequire(import.meta.url);
function harness({ getSnapshot } = {}) {
  const connections = [];
  let finish;
  const snapshot = { messages: [], characters: [], lorebooks: [], groups: [], scenes: [],
    active_scene_id: null, session: { id: 'branch-a' }, event_cursor: 'epoch:0' };
  const api = {
    getSessionSnapshot: getSnapshot ?? (async () => snapshot),
    sendMessage: async () => new Promise((resolve) => { finish = resolve; }),
    listScenes: async () => ({ scenes: [], active_scene_id: null }),
    listSessions: async () => [],
  };
  api.getStoryView = api.getSessionSnapshot;
  const imports = {
    zustand: require('zustand'), '../api/client': { api }, '../features/stories/storyApi': { storyApi: api },
    '../api/adapters': { toView: (value) => value },
    '../api/navigation': { readStoryLocation: () => ({ branchId: 'branch-a' }), writeStoryLocation: () => {} },
    '../utils/channels': { parseChannelParts: (text) => [{ kind: 'roleplay', text }] },
    '../utils/assistFreshness': {},
    '../utils/clientId.js': { createClientId },
    '../utils/optimisticMessages': { pendingAfterSnapshot: (current) => current.filter((m) => m.status === 'pending'), removeConfirmedLocalSegment: (messages) => messages },
    '../types': { SSE_EVENT_TYPES: ['message.pending', 'message.delta', 'message.final'] },
    '../api/sse': { connectSSE: (sid, cursor) => {
      const listeners = new Map();
      const connection = { sid, cursor, listeners, closed: false,
        addEventListener: (type, fn) => listeners.set(type, fn), close() { this.closed = true; },
        emit(evt) { listeners.get(evt.type)?.({ data: JSON.stringify(evt) }); } };
      connections.push(connection);
      return connection;
    } },
  };
  const exports = {};
  const source = readFileSync(new URL('../src/store/chatStore.ts', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  new Function('require', 'exports', compiled)((name) => {
    if (storyImport(name, imports)) return storyImport(name, imports); assert.ok(name in imports, `Unexpected import ${name}`);
    return imports[name];
  }, exports);
  return { store: exports.useChatStore, connections, complete: (messages) => finish({ messages, errors: [] }) };
}

globalThis.crypto ??= { randomUUID };

test('failed snapshot can be retried on the same branch without becoming an empty session', async () => {
  let attempts = 0;
  const { store, connections } = harness({ getSnapshot: async () => {
    if (++attempts === 1) throw new Error('synthetic read failure');
    return { session: { id: 'branch-a' }, messages: [{ id: 'saved', content: '合成历史' }], characters: [], lorebooks: [], groups: [], scenes: [], event_cursor: 'epoch:3' };
  } });
  await assert.rejects(store.getState().selectSession('branch-a', false));
  assert.equal(store.getState().snapshotStatus, 'error');
  assert.equal(store.getState().snapshotLoadedSessionId, null);
  assert.equal(connections.length, 0);
  await store.getState().selectSession('branch-a', false);
  assert.equal(store.getState().snapshotStatus, 'ready');
  assert.equal(store.getState().messages[0].id, 'saved');
  assert.equal(store.getState().sessions[0].id, 'branch-a');
  store.getState().disconnect();
});

test('switching and leaving cancels reads, closes subscriptions and rejects late state', async () => {
  const pending = new Map(), signals = new Map();
  const { store, connections } = harness({ getSnapshot: (id, options) => {
    signals.set(id, options.signal);
    return new Promise((resolve) => pending.set(id, resolve));
  } });
  const a = store.getState().selectSession('branch-a', false);
  const b = store.getState().selectSession('branch-b', false);
  assert.equal(signals.get('branch-a').aborted, true);
  const value = (id) => ({ session: { id }, messages: [{ id }], characters: [], lorebooks: [], groups: [], scenes: [], event_cursor: 'epoch:1' });
  pending.get('branch-b')(value('branch-b')); await b;
  pending.get('branch-a')(value('branch-a')); await a;
  assert.equal(store.getState().messages[0].id, 'branch-b');
  assert.equal(connections.length, 1);
  store.getState().disconnect();
  assert.equal(connections[0].closed, true);
  const again = store.getState().selectSession('branch-b', false);
  pending.get('branch-b')(value('branch-b')); await again;
  assert.equal(connections.length, 2);
  assert.equal(connections[1].closed, false);
  store.getState().disconnect();
});

for (const mode of ['serial', 'parallel']) {
  test(`${mode}: disconnected stream restores and renders live text before HTTP completes`, async () => {
    const { store, connections, complete } = harness();
    await store.getState().selectSession('branch-a', false);
    store.getState().disconnect();
    const send = store.getState().sendMessage('你好', ['actor-a', 'actor-b'], 'chat', mode);
    await new Promise((resolve) => setImmediate(resolve));
    const connection = connections.at(-1);
    assert.equal(connection.closed, false);
    assert.equal(connection.cursor, 'epoch:0');
    const pending = { id: 'reply-a', actor: 'actor-a', session_id: 'branch-a', turn: 1, seq: 1, content: '', status: 'pending' };
    connection.emit({ type: 'message.pending', message: pending });
    assert.equal(store.getState().messages.find((m) => m.id === pending.id).content, '');
    connection.emit({ type: 'message.delta', message_id: pending.id, offset: 0, delta: '已经开始回应', delivery: 'live' });
    assert.equal(store.getState().messages.find((m) => m.id === pending.id).content, '已经开始回应');
    assert.equal(store.getState().sending, true);
    // A callback retained by the old connection must not change current data.
    connections[0].emit({ type: 'message.pending', message: { ...pending, id: 'stale-reply' } });
    assert.equal(store.getState().messages.some((m) => m.id === 'stale-reply'), false);
    complete([{ ...pending, status: 'final', content: '完整回应' }]);
    await send;
    assert.equal(store.getState().messages.find((m) => m.id === pending.id).content, '完整回应');
    store.getState().disconnect();
  });
}

test('plain HTTP phone: real story store sends and unlocks without randomUUID', async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: {
    getRandomValues: (bytes) => webcrypto.getRandomValues(bytes),
  } });
  const { store, complete } = harness();
  try {
    await store.getState().selectSession('branch-a', false);
    const request = store.getState().sendMessage('我们一起查地图。', ['actor-a'], 'chat', 'parallel');
    const player = store.getState().messages.find((message) => message.actor === 'player');
    assert.match(player.id, /^msg-[\da-f]{32}$/);
    assert.equal(store.getState().sending, true);
    complete([{ ...player, status: 'final' }]);
    await request;
    assert.equal(store.getState().sending, false);
    assert.equal(store.getState().messages.filter((message) => message.id === player.id).length, 1);
  } finally {
    store.getState().disconnect();
    Object.defineProperty(globalThis, 'crypto', descriptor);
  }
});
