import { storyImport } from './storySupport.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { createClientId } from '../src/utils/clientId.js';

const require = createRequire(import.meta.url);
function compile(path, imports = {}) {
  const exports = {};
  const source = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  new Function('require', 'exports', source)((name) => { if (storyImport(name, imports)) return storyImport(name, imports); assert.ok(name in imports, `Unexpected import ${name}`); return imports[name]; }, exports);
  return exports;
}
const tick = () => new Promise((resolve) => setImmediate(resolve));
const identities = { generation_id: 'current-generation', operation_id: 'current-run-operation', attempt_id: 'current-attempt' };
const pending = (extra = {}) => ({ id: 'current-message', session_id: 'branch-a', seq: 2, turn: 2, actor: 'npc-a',
  kind: 'roleplay', scene_id: 'scene-a', status: 'pending', content: '刷新前已输出', fingerprint: '', visible_to: 'all',
  generation_meta: null, variants: [], active_variant: null, edited: false, hygiene: null,
  created_at: '2026-01-01T00:00:01Z', ...identities, ...extra });
const committed = () => ({ ...pending(), id: 'committed-message', seq: 1, content: '已经保存的前一条', status: 'final',
  generation_id: 'committed-generation', operation_id: 'committed-operation', attempt_id: 'committed-attempt', fingerprint: 'committed-fingerprint' });
const run = (extra = {}) => ({ id: 'current-run', session_id: 'branch-a', operation_id: identities.operation_id,
  mode: 'observe', participant_ids: ['npc-a'], scene_id: 'scene-a', player_identity_id: 'identity-a', directive: '',
  max_replies: 6, completed_replies: 1, status: 'running', stage: 'generating', current_speaker_id: 'npc-a',
  current_message_id: 'current-message', current_generation_id: identities.generation_id, current_attempt_id: identities.attempt_id,
  stop_reason: '', last_error: null, epoch: 1, pause_requested: false, stop_requested: false,
  last_committed_step_id: 'committed-step', last_committed_message_id: 'committed-message',
  cumulative_usage: { input_tokens: 1, output_tokens: 1, cached_tokens: 0 }, steps: [],
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:01Z', ...extra });
const rawSnapshot = (extra = {}) => ({ meta: { id: 'branch-a', title: '合成路线', character_ids: ['npc-a'], branch_revision: 7, player_identity_id: 'identity-a' },
  messages: [committed(), pending()], characters: [], lorebooks: [], groups: [], scenes: [{ id: 'scene-a', group_ids: [] }],
  active_scene_id: 'scene-a', conversation_runs: [run()], player_identities: [], event_cursor: 'events:18', ...extra });

function harness(snapshot = rawSnapshot()) {
  const snapshots = new Map([['branch-a', snapshot], ['branch-b', rawSnapshot({
    meta: { id: 'branch-b', character_ids: [], branch_revision: 1 }, messages: [], conversation_runs: [], event_cursor: 'events:1',
  })]]), connections = [], requests = [];
  const adapters = compile('../src/api/adapters.ts');
  const api = compile('../src/api/client.ts', {
    './adapters': adapters, './request': { jsonRequest: async (path, input = {}) => {
      requests.push({ path, ...input });
      const match = path.match(/^\/api\/v1\/sessions\/([^/]+)$/);
      if (match) return structuredClone(snapshots.get(decodeURIComponent(match[1])));
    const viewMatch = path.match(/^\/api\/v1\/sessions\/([^/]+)\/view(?:\?|$)/);
    if (viewMatch) { const raw = structuredClone(snapshots.get(decodeURIComponent(viewMatch[1]))); return { ...raw,
      session: adapters.mapSessionState(raw), next_before_seq: null, latest_seq: Math.max(-1, ...raw.messages.map(message => message.seq)) }; }
      if (path === '/api/v1/sessions') return [...snapshots.values()].map((value) => value.meta);
      if (path.endsWith('/messages')) return { messages: [], errors: [] };
      throw new Error(`Unexpected synthetic API path ${path}`);
    } },
  }).api;
  const exports = compile('../src/store/chatStore.ts', {
    zustand: require('zustand'), '../api/client': { api }, '../features/stories/storyApi': { storyApi: api }, '../api/adapters': adapters,
    '../api/navigation': { readStoryLocation: () => ({ branchId: 'branch-a' }), writeStoryLocation() {} },
    '../utils/clientId.js': { createClientId }, '../utils/channels': { parseChannelParts: (text) => [{ kind: 'roleplay', text }] },
    '../utils/assistFreshness': {}, '../utils/optimisticMessages': compile('../src/utils/optimisticMessages.ts'),
    '../types': { SSE_EVENT_TYPES: ['message.pending', 'message.delta', 'message.final', 'message.error', 'conversation.run.updated'] },
    '../api/sse': { connectSSE: (sid, cursor, onStatus, onResync) => {
      const listeners = new Map();
      const connection = { sid, cursor, closed: false, addEventListener: (type, fn) => listeners.set(type, fn),
        close() { this.closed = true; }, recover() { onResync(); }, emit(event) { listeners.get(event.type)?.({ data: JSON.stringify(event) }); } };
      connections.push(connection); return connection;
    } },
  });
  return { store: exports.useChatStore, exports, snapshots, requests, connections };
}

test('real snapshot client restores the current pending prefix and next delta before any final or generation HTTP result', async () => {
  const h = harness(); await h.store.getState().selectSession('branch-a', false);
  assert.equal(h.connections.at(-1).cursor, 'events:18');
  const restored = h.store.getState().messages.find((message) => message.id === 'current-message');
  assert.equal(restored.status, 'pending'); assert.equal(restored.content, '刷新前已输出');
  assert.equal(restored.attempt_id, identities.attempt_id);
  h.connections.at(-1).emit({ type: 'message.delta', ...identities, message_id: restored.id, offset: restored.content.length, delta: '，继续输出', delivery: 'live' });
  assert.equal(h.store.getState().messages.find((message) => message.id === restored.id).content, '刷新前已输出，继续输出');
  assert.equal(h.store.getState().messages.find((message) => message.id === restored.id).status, 'pending');
  assert.equal(h.requests.some((request) => request.method === 'POST'), false);
  h.connections.at(-1).emit({ type: 'message.final', ...identities, message: pending({ status: 'final', content: '最终合成回应', fingerprint: 'final-fingerprint' }) });
  assert.equal(h.store.getState().messages.find((message) => message.id === restored.id).content, '最终合成回应');
  h.store.getState().disconnect();
});

test('leaving and returning reads the latest runtime prefix and rejects the closed connection', async () => {
  const h = harness(); await h.store.getState().selectSession('branch-a', false);
  const previous = h.connections.at(-1);
  await h.store.getState().selectSession('branch-b', false);
  h.snapshots.get('branch-a').messages[1] = pending({ content: '离开期间的新前缀' });
  h.snapshots.get('branch-a').event_cursor = 'events:21';
  await h.store.getState().selectSession('branch-a', false);
  assert.equal(previous.closed, true);
  previous.emit({ type: 'message.delta', ...identities, message_id: 'current-message', offset: 0, delta: '旧连接内容', delivery: 'live' });
  assert.equal(h.store.getState().messages[1].content, '离开期间的新前缀');
  assert.equal(h.connections.at(-1).cursor, 'events:21');
  h.connections.at(-1).emit({ type: 'message.delta', ...identities, message_id: 'current-message', offset: '离开期间的新前缀'.length, delta: '，返回后继续', delivery: 'live' });
  assert.equal(h.store.getState().messages[1].content, '离开期间的新前缀，返回后继续');
  h.store.getState().disconnect();
});

test('a restored current attempt rejects older attempt delta/final/error and preserves replayed current prefix', async () => {
  const h = harness(); await h.store.getState().selectSession('branch-a', false);
  const emit = (event) => h.connections.at(-1).emit(event);
  const old = { ...identities, attempt_id: 'older-attempt' };
  emit({ type: 'message.delta', ...old, message_id: 'current-message', offset: 0, delta: '迟到旧尝试', delivery: 'live' });
  emit({ type: 'message.final', ...old, message: pending({ ...old, status: 'final', content: '迟到旧完成' }) });
  emit({ type: 'message.error', ...old, message_id: 'current-message', error: '迟到旧失败', discard_pending: true });
  emit({ type: 'message.pending', ...identities, message: pending({ content: '' }) });
  assert.equal(h.store.getState().messages[1].content, '刷新前已输出');
  assert.equal(h.store.getState().generationError, null);
  emit({ type: 'message.delta', ...identities, message_id: 'current-message', offset: '刷新前已输出'.length, delta: '新片段', delivery: 'live' });
  assert.equal(h.store.getState().messages[1].content, '刷新前已输出新片段');
  h.store.getState().disconnect();
});

test('canonical final wins over duplicate pending and a reconnect never reopens a completed current attempt', async () => {
  const final = pending({ status: 'final', content: '已保存的当前回应', fingerprint: 'done-fingerprint' });
  const h = harness(rawSnapshot({ messages: [committed(), final, pending()] }));
  await h.store.getState().selectSession('branch-a', false);
  assert.equal(h.store.getState().messages.length, 2);
  assert.equal(h.store.getState().messages[1].status, 'final');
  h.snapshots.get('branch-a').messages = [committed(), pending()];
  h.connections.at(-1).recover(); await tick();
  h.connections.at(-1).emit({ type: 'message.pending', ...identities, message: pending({ content: '' }) });
  h.connections.at(-1).emit({ type: 'message.delta', ...identities, message_id: 'current-message', offset: 0, delta: '完成后的旧流', delivery: 'live' });
  assert.equal(h.store.getState().messages[1].status, 'final');
  assert.equal(h.store.getState().messages[1].content, '已保存的当前回应');
  h.store.getState().disconnect();
});

test('foreign runtime projections do not create a bubble or a generation slot, while the run trace stays readable', async () => {
  const h = harness(rawSnapshot({ conversation_runs: [run({ session_id: 'parent-branch' })] }));
  await h.store.getState().selectSession('branch-a', false);
  assert.deepEqual(h.store.getState().messages.map((message) => message.id), ['committed-message']);
  assert.equal(h.store.getState().conversationRuns[0].session_id, 'parent-branch');
  assert.equal(h.exports.isConversationActive(h.store.getState().conversationRuns[0], 'branch-a'), false);
  h.connections.at(-1).emit({ type: 'message.delta', ...identities, message_id: 'current-message', offset: 0, delta: '父运行旧流', delivery: 'live' });
  const local = { generation_id: 'local-generation', operation_id: 'local-operation', attempt_id: 'local-attempt' };
  h.connections.at(-1).emit({ type: 'message.pending', ...local, message: pending({ ...local, content: '' }) });
  h.connections.at(-1).emit({ type: 'message.delta', ...local, message_id: 'current-message', offset: 0, delta: '当前分支新流', delivery: 'live' });
  assert.equal(h.store.getState().messages[1].content, '当前分支新流');
  h.store.getState().disconnect();
});

test('a terminal-run resync removes an uncommitted bubble rather than preserving it as an optimistic segment', async () => {
  const h = harness(); await h.store.getState().selectSession('branch-a', false);
  h.snapshots.get('branch-a').messages = [committed()];
  h.snapshots.get('branch-a').conversation_runs = [run({ status: 'cancelled', current_message_id: null, current_generation_id: null, current_attempt_id: null,
    stop_reason: 'user_stopped', epoch: 2, updated_at: '2026-01-01T00:00:02Z' })];
  h.snapshots.get('branch-a').event_cursor = 'events:22';
  h.connections.at(-1).recover(); await tick();
  assert.deepEqual(h.store.getState().messages.map((message) => message.id), ['committed-message']);
  assert.equal(h.store.getState().conversationRuns[0].status, 'cancelled');
  h.store.getState().disconnect();
});
