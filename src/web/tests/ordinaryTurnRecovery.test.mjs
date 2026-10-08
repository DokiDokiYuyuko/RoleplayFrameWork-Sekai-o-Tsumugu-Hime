import { storyImport } from './storySupport.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { createClientId } from '../src/utils/clientId.js';

const require = createRequire(import.meta.url);
const tick = () => new Promise((resolve) => setImmediate(resolve));
function compile(path, imports = {}) {
  const exports = {};
  const source = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  new Function('require', 'exports', source)((name) => {
    if (storyImport(name, imports)) return storyImport(name, imports); assert.ok(name in imports, `Unexpected import ${name}`); return imports[name];
  }, exports);
  return exports;
}

const identity = { generation_id: 'generation-1', operation_id: 'ordinary-operation', attempt_id: 'attempt-1' };
const message = (changes = {}) => ({ id: 'reply-a', session_id: 'branch-a', seq: 1, turn: 1, actor: 'npc-a',
  kind: 'roleplay', scene_id: 'scene-a', status: 'pending', content: '未完成前缀', fingerprint: '', visible_to: 'all',
  generation_meta: null, variants: [], active_variant: null, edited: false, hygiene: null,
  created_at: '2026-01-01T00:00:01Z', ...identity, ...changes });
const player = () => message({ id: 'player-input', actor: 'player', seq: 0, status: 'final', content: '已保存的输入',
  generation_id: null, operation_id: null, attempt_id: null, fingerprint: 'player-fingerprint' });
const run = (changes = {}) => ({ id: 'ordinary-run', session_id: 'branch-a', operation_id: identity.operation_id,
  status: 'running', epoch: 0, updated_at: '2026-01-01T00:00:01Z',
  slots: [{ actor_id: 'npc-a', message_id: 'reply-a', generation_id: identity.generation_id, status: 'pending', seq: 1 }],
  ...changes });
const snapshot = (changes = {}) => ({ meta: { id: 'branch-a', title: '合成路线', branch_revision: 5, player_identity_id: 'identity-a' },
  messages: [player(), message()], characters: [], groups: [], lorebooks: [], scenes: [{ id: 'scene-a', group_ids: [] }],
  active_scene_id: 'scene-a', conversation_runs: [], turn_runs: [run()], pending_director: null,
  event_cursor: 'journal:5', ...changes });

function harness(initial = snapshot()) {
  const snapshots = new Map([['branch-a', initial], ['branch-b', snapshot({ meta: { id: 'branch-b', branch_revision: 1 },
    messages: [], turn_runs: [], event_cursor: 'journal:1' })]]);
  const requests = [], connections = [], handlers = new Map();
  const adapters = compile('../src/api/adapters.ts');
  const api = compile('../src/api/client.ts', { './adapters': adapters, './request': { jsonRequest: async (path, input = {}) => {
    requests.push({ path, ...input });
    if (handlers.has(path)) { const raw = await handlers.get(path)(input); return path.endsWith('/view') ? { ...raw, session: adapters.mapSessionState(raw), next_before_seq: null, latest_seq: Math.max(-1, ...raw.messages.map(message => message.seq)) } : raw; }
    const match = path.match(/^\/api\/v1\/sessions\/([^/]+)$/);
    if (match) return structuredClone(snapshots.get(decodeURIComponent(match[1])));
    const viewMatch = path.match(/^\/api\/v1\/sessions\/([^/]+)\/view(?:\?|$)/);
    if (viewMatch) { const raw = structuredClone(snapshots.get(decodeURIComponent(viewMatch[1]))); return { ...raw,
      session: adapters.mapSessionState(raw), next_before_seq: null, latest_seq: Math.max(-1, ...raw.messages.map(message => message.seq)) }; }
    if (path === '/api/v1/sessions') return [...snapshots.values()].map((value) => value.meta);
    throw new Error(`Unexpected synthetic path ${path}`);
  } } }).api;
  const exports = compile('../src/store/chatStore.ts', {
    zustand: require('zustand'), '../api/client': { api }, '../features/stories/storyApi': { storyApi: api }, '../api/adapters': adapters,
    '../api/navigation': { readStoryLocation: () => ({ branchId: 'branch-a' }), writeStoryLocation() {} },
    '../utils/clientId.js': { createClientId }, '../utils/channels': { parseChannelParts: (text) => [{ kind: 'roleplay', text }] },
    '../utils/assistFreshness': {}, '../utils/optimisticMessages': compile('../src/utils/optimisticMessages.ts'),
    '../types': { SSE_EVENT_TYPES: ['message.pending', 'message.delta', 'message.final', 'message.error', 'turn.run.updated', 'director.pending'] },
    '../api/sse': { connectSSE: (sid, cursor, onStatus, onResync) => {
      const listeners = new Map();
      const connection = { sid, cursor, closed: false, addEventListener: (type, fn) => listeners.set(type, fn),
        close() { this.closed = true; }, recover() { onResync(); },
        emit(event) { listeners.get(event.type)?.({ data: JSON.stringify(event) }); } };
      connections.push(connection); return connection;
    } },
  });
  return { api, store: exports.useChatStore, exports, handlers, snapshots, requests, connections,
    emit: (event) => connections.at(-1).emit(event) };
}

test('ordinary HTTP carries the durable run and serializes explicit recovery with revision and identity', async () => {
  const h = harness(snapshot({ messages: [player()], turn_runs: [run({ status: 'interrupted' })] }));
  h.handlers.set('/api/v1/sessions/branch-a/messages', () => ({ messages: [player()], errors: ['模型失败'], turn_run: run({ status: 'failed' }) }));
  const response = await h.api.sendMessage('branch-a', '输入', ['npc-a']);
  assert.equal(response.turn_run.status, 'failed');
  h.handlers.set('/api/v1/sessions/branch-a/turn-runs/ordinary-operation/resume', (input) => {
    assert.deepEqual(JSON.parse(input.body), { expected_branch_revision: 5, expected_player_identity_id: 'identity-a' });
    const completed = run({ status: 'completed', epoch: 1, updated_at: '2026-01-01T00:00:03Z',
      slots: [{ ...run().slots[0], status: 'committed', generation_id: 'generation-2' }] });
    h.snapshots.set('branch-a', snapshot({ messages: [player(), message({ status: 'final', generation_id: 'generation-2', content: '补完回应' })],
      turn_runs: [completed], meta: { ...snapshot().meta, branch_revision: 9 } }));
    return { messages: h.snapshots.get('branch-a').messages, errors: [], turn_run: completed };
  });
  await h.store.getState().selectSession('branch-a', false);
  await h.store.getState().controlTurn(identity.operation_id, 'resume');
  assert.equal(h.store.getState().turnRuns[0].status, 'completed');
  assert.equal(h.store.getState().messages.at(-1).content, '补完回应');
  assert.equal(h.store.getState().sending, false);
  h.store.getState().disconnect();
});

test('restart snapshot restores ordinary execution and current live prefix without starting another call', async () => {
  const h = harness(); await h.store.getState().selectSession('branch-a', false);
  assert.equal(h.store.getState().sending, true);
  h.emit({ type: 'message.delta', ...identity, message_id: 'reply-a', offset: 5, delta: '继续', delivery: 'live' });
  assert.equal(h.store.getState().messages.at(-1).content, '未完成前缀继续');
  assert.equal(h.requests.some((request) => request.method === 'POST'), false);
  h.store.getState().disconnect();
});

test('terminal turn update discards unfinished text and rejects delayed pending, delta, final and error', async () => {
  const h = harness(); await h.store.getState().selectSession('branch-a', false);
  const terminal = run({ status: 'interrupted', epoch: 1, updated_at: '2026-01-01T00:00:02Z',
    slots: [{ ...run().slots[0], status: 'cancelled' }] });
  h.emit({ type: 'turn.run.updated', run: terminal, branch_revision: 6 });
  assert.deepEqual(h.store.getState().messages.map((item) => item.actor), ['player']);
  assert.equal(h.store.getState().sending, false);
  h.emit({ type: 'message.pending', ...identity, message: message({ content: '' }) });
  h.emit({ type: 'message.delta', ...identity, message_id: 'reply-a', offset: 0, delta: '迟到文本', delivery: 'live' });
  h.emit({ type: 'message.final', ...identity, message: message({ status: 'final', content: '迟到完成' }) });
  h.emit({ type: 'message.error', ...identity, message_id: 'reply-a', error: '迟到失败', discard_pending: true });
  h.emit({ type: 'turn.run.updated', run: run(), branch_revision: 5 });
  assert.equal(h.store.getState().turnRuns[0].status, 'interrupted');
  assert.equal(h.store.getState().generationError, null);
  assert.deepEqual(h.store.getState().messages.map((item) => item.actor), ['player']);
  h.store.getState().disconnect();
});

test('resume activates only its new generation and allows its durable final after completion notification', async () => {
  const h = harness(snapshot({ messages: [player()], turn_runs: [run({ status: 'interrupted', epoch: 1 })] }));
  await h.store.getState().selectSession('branch-a', false);
  const nextIdentity = { ...identity, generation_id: 'generation-2', attempt_id: 'attempt-2' };
  const next = run({ epoch: 2, updated_at: '2026-01-01T00:00:03Z', slots: [{ ...run().slots[0], generation_id: nextIdentity.generation_id }] });
  h.emit({ type: 'turn.run.updated', run: next, branch_revision: 8 });
  h.emit({ type: 'message.pending', ...nextIdentity, message: message({ ...nextIdentity, content: '' }) });
  h.emit({ type: 'message.delta', ...identity, message_id: 'reply-a', offset: 0, delta: '旧尝试', delivery: 'live' });
  h.emit({ type: 'message.delta', ...nextIdentity, message_id: 'reply-a', offset: 0, delta: '新尝试', delivery: 'live' });
  assert.equal(h.store.getState().messages.at(-1).content, '新尝试');
  const committed = { ...next, slots: [{ ...next.slots[0], status: 'committed' }], updated_at: '2026-01-01T00:00:04Z' };
  h.emit({ type: 'turn.run.updated', run: committed, branch_revision: 9 });
  h.emit({ type: 'message.final', ...nextIdentity, message: message({ ...nextIdentity, status: 'final', content: '完整新回应' }) });
  h.emit({ type: 'turn.run.updated', run: { ...committed, status: 'completed', updated_at: '2026-01-01T00:00:05Z' }, branch_revision: 10 });
  assert.equal(h.store.getState().messages.at(-1).status, 'final');
  assert.equal(h.store.getState().sending, false);
  h.store.getState().disconnect();
});

test('interrupted and foreign ordinary snapshots keep committed history and remove uncommitted projections', async () => {
  for (const value of [run({ status: 'interrupted', slots: [{ ...run().slots[0], status: 'cancelled' }] }), run({ session_id: 'parent-branch' })]) {
    const h = harness(snapshot({ turn_runs: [value] }));
    await h.store.getState().selectSession('branch-a', false);
    assert.deepEqual(h.store.getState().messages.map((item) => item.actor), ['player']);
    assert.equal(h.store.getState().sending, false);
    if (value.session_id !== 'branch-a') await assert.rejects(h.store.getState().controlTurn(identity.operation_id, 'resume'), /另一条路线/);
    h.store.getState().disconnect();
  }
});

test('an old send HTTP cannot overwrite a newer resumed epoch or unlock its active generation', async () => {
  const h = harness(snapshot({ messages: [], turn_runs: [] }));
  let resolveSend, clientId;
  h.handlers.set('/api/v1/sessions/branch-a/messages', (input) => {
    clientId = JSON.parse(input.body).client_message_id;
    return new Promise((resolve) => { resolveSend = resolve; });
  });
  await h.store.getState().selectSession('branch-a', false);
  const sending = h.store.getState().sendMessage('合成输入', ['npc-a'], 'dialogue', 'serial');
  await tick();
  const old = run({ operation_id: clientId });
  const newer = run({ operation_id: clientId, epoch: 2, updated_at: '2026-01-01T00:00:03Z',
    slots: [{ ...old.slots[0], generation_id: 'generation-2' }] });
  h.emit({ type: 'turn.run.updated', run: newer, branch_revision: 8 });
  h.emit({ type: 'message.pending', operation_id: clientId, generation_id: 'generation-2', attempt_id: 'attempt-2',
    message: message({ operation_id: clientId, generation_id: 'generation-2', attempt_id: 'attempt-2', content: '恢复中' }) });
  resolveSend({ turn_run: old, messages: [message({ status: 'final', content: '旧HTTP完成', operation_id: clientId })], errors: ['旧错误'] });
  await sending;
  assert.equal(h.store.getState().turnRuns[0].epoch, 2);
  assert.equal(h.store.getState().messages.at(-1).content, '恢复中');
  assert.equal(h.store.getState().generationError, null);
  assert.equal(h.store.getState().sending, true);
  h.store.getState().disconnect();
});

test('same epoch stale checkpoints cannot decrease committed slots or reopen completed execution', () => {
  const h = harness();
  const done = run({ status: 'completed', updated_at: '2026-01-01T00:00:03Z', slots: [{ ...run().slots[0], status: 'committed' }] });
  const prior = [done];
  assert.equal(h.exports.mergeTurnRun(prior, run({ updated_at: '2026-01-01T00:00:01Z' })), prior);
  assert.equal(h.exports.mergeTurnRun(prior, run({ updated_at: '2026-01-01T00:00:04Z' })), prior);
  assert.equal(h.exports.mergeTurnRun(prior, { ...done, status: 'running', updated_at: done.updated_at }), prior);
});

test('director proposal survives restart snapshot and confirmation refreshes its persisted execution', async () => {
  const decision = { turn: 1, chosen: ['npc-a'], action: 'pick_speaker', trigger: 'llm_route', rationale: '等待确认' };
  const h = harness(snapshot({ messages: [player()], turn_runs: [run({ status: 'awaiting_director', slots: [] })], pending_director: decision }));
  h.handlers.set('/api/v1/sessions/branch-a/director/pending/confirm', () => {
    const complete = run({ status: 'completed', updated_at: '2026-01-01T00:00:03Z', slots: [{ ...run().slots[0], status: 'committed' }] });
    const messages = [player(), message({ status: 'final', content: '导演确认后的回应' })];
    h.snapshots.set('branch-a', snapshot({ messages, turn_runs: [complete], pending_director: null, meta: { ...snapshot().meta, branch_revision: 8 } }));
    return { messages, turn_run: complete, errors: [] };
  });
  await h.store.getState().selectSession('branch-a', false);
  assert.equal(h.store.getState().pendingDirector.rationale, '等待确认');
  await h.store.getState().confirmDirector();
  assert.equal(h.store.getState().pendingDirector, null);
  assert.equal(h.store.getState().turnRuns[0].status, 'completed');
  assert.equal(h.store.getState().messages.at(-1).content, '导演确认后的回应');
  h.store.getState().disconnect();
});

test('material refresh keeps the loaded route and inspector mounted while adopting a newer snapshot', async () => {
  const h = harness(snapshot({ messages: [player()], turn_runs: [] }));
  await h.store.getState().selectSession('branch-a', false);
  const target = { characterId: 'npc-a', messageId: 'reply-a', generationId: 'generation-1' };
  h.store.setState({ inspectTarget: target });
  let resolveRefresh;
  h.handlers.set('/api/v1/sessions/branch-a/view', () => new Promise((resolve) => { resolveRefresh = resolve; }));
  const pending = h.store.getState().refreshSession('branch-a');
  assert.equal(h.store.getState().snapshotStatus, 'ready');
  assert.equal(h.store.getState().snapshotLoadedSessionId, 'branch-a');
  assert.equal(h.store.getState().messages.length, 1);
  resolveRefresh(snapshot({ messages: [player(), message({ status: 'final', content: '纠错新候选' })],
    turn_runs: [], meta: { ...snapshot().meta, branch_revision: 9 } }));
  await pending;
  assert.equal(h.store.getState().messages.at(-1).content, '纠错新候选');
  assert.deepEqual(h.store.getState().inspectTarget, target);
  h.store.getState().disconnect();
});

test('material refresh failure is reported without clearing the current story', async () => {
  const h = harness(snapshot({ messages: [player()], turn_runs: [] }));
  await h.store.getState().selectSession('branch-a', false);
  h.handlers.set('/api/v1/sessions/branch-a/view', () => { throw new Error('合成快照读取失败'); });
  await assert.rejects(h.store.getState().refreshSession('branch-a'), /合成快照读取失败/);
  assert.equal(h.store.getState().snapshotStatus, 'ready');
  assert.equal(h.store.getState().messages[0].content, '已保存的输入');
  h.store.getState().disconnect();
});

test('dismissal clears the transient error and keeps the failed round recoverable', async () => {
  const failed = run({ status: 'failed', last_error: 'Synthetic upstream failure' });
  const h = harness(snapshot({ messages: [player()], turn_runs: [failed] }));
  await h.store.getState().selectSession('branch-a', false);
  h.store.setState({ generationError: '409: 这一轮仍在执行，请等待后再恢复' });
  h.store.getState().dismissGenerationError();
  assert.equal(h.store.getState().generationError, null);
  assert.equal(h.store.getState().turnRuns[0].last_error, 'Synthetic upstream failure');
  assert.equal(h.store.getState().messages[0].content, '已保存的输入');
  h.store.setState({ generationError: 'A new failure' });
  assert.equal(h.store.getState().generationError, 'A new failure');
  h.store.getState().disconnect();
});

test('recovery refresh sees a live round and never posts a duplicate resume', async () => {
  const h = harness(snapshot({ messages: [player()], turn_runs: [run({ status: 'failed' })] }));
  await h.store.getState().selectSession('branch-a', false);
  h.snapshots.set('branch-a', snapshot({ turn_runs: [run({ status: 'running', epoch: 1 })] }));
  await h.store.getState().controlTurn(identity.operation_id, 'resume');
  assert.equal(h.requests.some((request) => request.path.endsWith('/resume')), false);
  assert.equal(h.store.getState().sending, true);
  assert.equal(h.store.getState().generationError, null);
  assert.equal(h.store.getState().conversationActionBusy, null);
  h.store.getState().disconnect();
});

test('a completed round received during recovery clears a stale error', async () => {
  const h = harness(snapshot({ messages: [player()], turn_runs: [run({ status: 'failed' })] }));
  await h.store.getState().selectSession('branch-a', false);
  h.store.setState({ generationError: 'Old failure' });
  h.emit({ type: 'turn.run.updated', run: run({ status: 'completed', epoch: 1 }), branch_revision: 6 });
  assert.equal(h.store.getState().generationError, null);
  h.store.getState().disconnect();
});

test('snapshot recovery clears a stale failure after another client completes the round', async () => {
  const h = harness(snapshot({ messages: [player()], turn_runs: [run({ status: 'failed' })] }));
  await h.store.getState().selectSession('branch-a', false);
  h.store.setState({ generationError: 'Old failure' });
  h.snapshots.set('branch-a', snapshot({ messages: [player(), message({ status: 'final' })],
    turn_runs: [run({ status: 'completed', epoch: 1 })], meta: { ...snapshot().meta, branch_revision: 6 } }));
  await h.store.getState().refreshSession('branch-a');
  assert.equal(h.store.getState().generationError, null);
  assert.equal(h.store.getState().sending, false);
  h.store.getState().disconnect();
});
