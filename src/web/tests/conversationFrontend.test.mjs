import { storyImport } from './storySupport.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { createClientId } from '../src/utils/clientId.js';
import { conversationPreferenceKey, normalizeMaxReplies, readConversationPreferences, saveConversationPreferences } from '../src/utils/conversationPreferences.js';
import { generationIdForMessage } from '../src/utils/inspectionTarget.js';

const require = createRequire(import.meta.url);
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const message = (id, actor = 'npc-a', extra = {}) => ({
  id, actor, session_id: 'branch-a', turn: 1, seq: 1, kind: 'roleplay', scene_id: 'scene-a',
  content: `合成回应 ${id}`, status: 'final', fingerprint: `fingerprint-${id}`, visible_to: 'all',
  variants: [{ id: `variant-${id}`, content: `合成回应 ${id}` }], active_variant: 0,
  generation_meta: null, hygiene: null, edited: false, created_at: '2026-01-01T00:00:00Z', ...extra,
});
const run = (extra = {}) => ({ id: 'run-a', operation_id: 'run-operation-a', mode: 'observe', participant_ids: ['npc-a', 'group-a'],
  scene_id: 'scene-a', player_identity_id: 'identity-a', directive: '', max_replies: 6, completed_replies: 0,
  status: 'running', stage: 'selecting', current_speaker_id: null, stop_reason: '', last_error: null,
  epoch: 1, pause_requested: false, stop_requested: false, steps: [],
  cumulative_usage: { input_tokens: 0, output_tokens: 0, cached_tokens: 0 },
  last_committed_step_id: null, last_committed_message_id: null,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', ...extra });

function harness(overrides = {}) {
  const connections = [], calls = [];
  const snapshots = new Map();
  const initial = { session: { id: 'branch-a', branch_revision: 4, player_identity_id: 'identity-a',
    player_identities: [{ id: 'identity-a', person_id: 'controlled-person' }] },
    messages: [message('a'), message('b', 'group-a', { seq: 2 })], characters: [], lorebooks: [], groups: [],
    scenes: [{ id: 'scene-a', group_ids: ['group-a'] }], active_scene_id: 'scene-a', conversation_runs: [], event_cursor: 'epoch:4' };
  snapshots.set('branch-a', initial);
  snapshots.set('branch-b', { ...initial, session: { id: 'branch-b', branch_revision: 1 }, messages: [message('branch-b-message', 'npc-b', { session_id: 'branch-b' })] });
  const api = {
    getSessionSnapshot: async (id) => snapshots.get(id), listSessions: async () => [...snapshots.values()].map((value) => value.session),
    sendMessage: async (...args) => { calls.push(['send', ...args]); return { messages: [], errors: [] }; },
    regenerateOne: async (...args) => { calls.push(['one', ...args]); return { messages: [message(args[1], 'npc-a', { content: '新合成回应' })], branch_revision: 5, affected_message_ids: [args[1]], operation_id: args[2].operation_id }; },
    regenerateDependents: async (...args) => { calls.push(['dependents', ...args]); return { messages: [message(args[1], 'group-a', { dependency_stale: false })], branch_revision: 5, affected_message_ids: [args[1]], operation_id: args[2].operation_id }; },
    acceptDependencies: async (...args) => { calls.push(['accept', ...args]); return { messages: [message(args[1], 'group-a', { dependency_stale: false })], branch_revision: 5, affected_message_ids: [args[1]], operation_id: args[2].operation_id }; },
    startConversation: async (...args) => { calls.push(['start', ...args]); return run(); },
    pauseConversation: async (...args) => { calls.push(['pause', ...args]); return run({ status: 'paused', stop_reason: 'user_paused', updated_at: '2026-01-01T00:00:01Z' }); },
    stopConversation: async (...args) => { calls.push(['stop', ...args]); return run({ status: 'cancelled', stop_reason: 'user_stopped', epoch: 2 }); },
    resumeConversation: async (...args) => { calls.push(['resume', ...args]); return run({ epoch: 2 }); },
    switchVariant: async (...args) => { calls.push(['variant', ...args]); return message(args[1], 'npc-a', { active_variant: args[2] }); },
    ...overrides,
  };
  api.getStoryView = api.getSessionSnapshot;
  const imports = {
    zustand: require('zustand'), '../api/client': { api }, '../features/stories/storyApi': { storyApi: api }, '../api/adapters': { toView: (value) => value },
    '../api/navigation': { readStoryLocation: () => ({ branchId: 'branch-a' }), writeStoryLocation: () => {} },
    '../utils/channels': { parseChannelParts: (text) => [{ kind: 'roleplay', text }] }, '../utils/assistFreshness': {},
    '../utils/clientId.js': { createClientId },
    '../utils/optimisticMessages': { pendingAfterSnapshot: (current, fresh) => current.filter((item) => item.status === 'pending' && !fresh.some((message) => message.id === item.id)), removeConfirmedLocalSegment: (messages) => messages },
    '../types': { SSE_EVENT_TYPES: ['message.pending', 'message.delta', 'message.final', 'message.error', 'message.updated', 'conversation.run.updated'] },
    '../api/sse': { connectSSE: (sid, cursor, onStatus, onResync) => {
      const listeners = new Map();
      const connection = { sid, cursor, closed: false, addEventListener: (type, listener) => listeners.set(type, listener),
        close() { this.closed = true; }, recover() { onResync(); }, emit(event) { listeners.get(event.type)?.({ data: JSON.stringify(event) }); } };
      connections.push(connection); return connection;
    } },
  };
  const exports = {};
  const compiled = ts.transpileModule(readFileSync(new URL('../src/store/chatStore.ts', import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  new Function('require', 'exports', compiled)((name) => { if (storyImport(name, imports)) return storyImport(name, imports); assert.ok(name in imports, `Unexpected import ${name}`); return imports[name]; }, exports);
  return { store: exports.useChatStore, connections, calls, snapshots, initial, exports };
}

test('single regeneration holds the original candidate and changes only the chosen bubble', async () => {
  const pending = deferred(); let request;
  const h = harness({ regenerateOne: (...args) => { request = args; return pending.promise; } });
  await h.store.getState().selectSession('branch-a', false);
  const sibling = h.store.getState().messages[1];
  const work = h.store.getState().regenerateOne('a');
  assert.equal(h.store.getState().messages[0].status, 'pending');
  assert.equal(h.store.getState().messages[0].content, '');
  assert.equal(h.store.getState().regenerationPrevious.a.content, '合成回应 a');
  assert.equal(h.store.getState().messages[1], sibling);
  assert.equal(request[2].expected_branch_revision, 4);
  assert.equal(request[2].expected_fingerprint, 'fingerprint-a');
  assert.equal(request[2].expected_player_identity_id, 'identity-a');
  pending.resolve({ messages: [message('a', 'npc-a', { content: '新候选', variants: [...h.initial.messages[0].variants, { id: 'variant-a-2', content: '新候选' }], active_variant: 1 })],
    branch_revision: 5, affected_message_ids: ['a'], operation_id: request[2].operation_id });
  await work;
  assert.equal(h.store.getState().messages[0].variants.length, 2);
  assert.equal(h.store.getState().messages[1], sibling);
  assert.equal(h.store.getState().regenerationPrevious.a, undefined);
  assert.equal(h.store.getState().sessions[0].branch_revision, 5);
  h.store.getState().disconnect();
});

test('failed regeneration restores all of the chosen candidate', async () => {
  const h = harness({ regenerateOne: async () => { throw new Error('synthetic failure'); } });
  await h.store.getState().selectSession('branch-a', false);
  const original = h.store.getState().messages[0];
  await assert.rejects(h.store.getState().regenerateOne('a'), /synthetic failure/);
  assert.deepEqual(h.store.getState().messages[0], original);
  assert.equal(h.store.getState().sending, false);
  assert.deepEqual(h.store.getState().regenerationPrevious, {});
  h.store.getState().disconnect();
});

test('a new attempt retires old deltas, final, error and pending events', async () => {
  const h = harness(); await h.store.getState().selectSession('branch-a', false);
  const emit = (event) => h.connections.at(-1).emit(event);
  const identity = { generation_id: 'generation-a', operation_id: 'operation-a', attempt_id: 'attempt-1' };
  const second = { ...identity, attempt_id: 'attempt-2' };
  emit({ type: 'message.pending', ...identity, message: message('a', 'npc-a', { ...identity, status: 'pending', content: '' }) });
  emit({ type: 'message.delta', ...identity, message_id: 'a', offset: 0, delta: '旧尝试', delivery: 'live' });
  assert.equal(h.store.getState().messages[0].content, '旧尝试');
  emit({ type: 'message.pending', ...second, message: message('a', 'npc-a', { ...second, status: 'pending', content: '' }) });
  assert.equal(h.store.getState().messages[0].content, '');
  emit({ type: 'message.delta', ...identity, message_id: 'a', offset: 0, delta: '迟到旧尝试', delivery: 'live' });
  emit({ type: 'message.final', ...identity, message: message('a', 'npc-a', { ...identity, content: '迟到完成' }) });
  emit({ type: 'message.error', ...identity, message_id: 'a', error: '迟到失败', message: message('a') });
  emit({ type: 'message.pending', ...identity, message: message('a', 'npc-a', { ...identity, status: 'pending', content: '' }) });
  assert.equal(h.store.getState().messages[0].content, '');
  assert.equal(h.store.getState().generationError, null);
  emit({ type: 'message.delta', ...second, message_id: 'a', offset: 0, delta: '新尝试', delivery: 'live' });
  emit({ type: 'message.final', ...second, message: message('a', 'npc-a', { ...second, content: '新尝试完成' }) });
  assert.equal(h.store.getState().messages[0].content, '新尝试完成');
  assert.equal(h.store.getState().messages[0].status, 'final');
  emit({ type: 'message.delta', ...second, message_id: 'a', offset: 0, delta: '完成后的旧流', delivery: 'live' });
  assert.equal(h.store.getState().messages[0].content, '新尝试完成');
  h.store.getState().disconnect();
});

test('message.error restores its authoritative old message despite failed operation identity', async () => {
  const operation = deferred(); let request;
  const h = harness({ regenerateOne: (...args) => { request = args; return operation.promise; } });
  await h.store.getState().selectSession('branch-a', false);
  const work = h.store.getState().regenerateOne('a');
  const identity = { generation_id: 'replacement', operation_id: request[2].operation_id, attempt_id: 'attempt-new' };
  h.connections.at(-1).emit({ type: 'message.pending', ...identity, message: message('a', 'npc-a', { ...identity, status: 'pending', content: '' }) });
  h.connections.at(-1).emit({ type: 'message.error', ...identity, message_id: 'a', error: '合成失败', message: h.initial.messages[0] });
  assert.deepEqual(h.store.getState().messages[0], h.initial.messages[0]);
  operation.reject(new Error('合成失败')); await assert.rejects(work);
  h.store.getState().disconnect();
});

test('stale bubble actions pass that node to both dependency operations', async () => {
  for (const action of ['regenerateDependents', 'acceptDependencies']) {
    const h = harness(); h.initial.messages[1].dependency_stale = true;
    await h.store.getState().selectSession('branch-a', false);
    await h.store.getState()[action]('b');
    assert.equal(h.calls[0][2], 'b');
    assert.equal(h.store.getState().messages[1].dependency_stale, false);
    h.store.getState().disconnect();
  }
});

test('late regeneration and closed subscriptions cannot affect a revisited branch', async () => {
  const pending = deferred(); const h = harness({ regenerateOne: () => pending.promise });
  await h.store.getState().selectSession('branch-a', false);
  const oldSource = h.connections.at(-1);
  const work = h.store.getState().regenerateOne('a');
  await h.store.getState().selectSession('branch-b', false);
  await h.store.getState().selectSession('branch-a', false);
  assert.equal(oldSource.closed, true);
  oldSource.emit({ type: 'message.pending', message: message('late-event', 'npc-a', { status: 'pending' }) });
  pending.resolve({ messages: [message('a', 'npc-a', { content: '迟到结果' })], branch_revision: 9, affected_message_ids: ['a'], operation_id: 'old-operation' });
  await work;
  assert.equal(h.store.getState().messages[0].content, '合成回应 a');
  assert.equal(h.store.getState().messages.some((message) => message.id === 'late-event'), false);
  assert.equal(h.store.getState().sending, false);
  h.store.getState().disconnect();
});

test('snapshot recovers a running conversation, prevents ordinary send, pause permits intervention', async () => {
  const h = harness(); h.initial.conversation_runs = [run()];
  await h.store.getState().selectSession('branch-a', false);
  assert.equal(h.store.getState().conversationRuns[0].status, 'running');
  await h.store.getState().sendMessage('合成干预', ['npc-a']);
  assert.equal(h.calls.length, 0);
  await h.store.getState().controlConversation('run-a', 'pause');
  assert.equal(h.store.getState().conversationRuns[0].status, 'paused');
  await h.store.getState().sendMessage('合成干预', ['npc-a'], undefined, 'serial');
  assert.equal(h.calls.at(-1)[0], 'send');
  h.store.getState().disconnect();
});

test('start and completed continuation keep participant/control/branch expectations and use latest snapshot revision', async () => {
  const h = harness(); await h.store.getState().selectSession('branch-a', false);
  await h.store.getState().startConversation(['npc-a', 'group-a'], 9, '交换合成情报');
  const start = h.calls[0][2];
  assert.deepEqual(start.participant_ids, ['npc-a', 'group-a']);
  assert.equal(start.max_replies, 9); assert.equal(start.directive, '交换合成情报');
  assert.equal(start.expected_branch_revision, 4); assert.equal(start.expected_player_identity_id, 'identity-a');
  const completed = run({ status: 'completed', completed_replies: 6, stop_reason: 'max_replies', updated_at: '2026-01-01T00:00:06Z' });
  h.connections.at(-1).emit({ type: 'conversation.run.updated', run: completed, branch_revision: 10 });
  h.initial.session.branch_revision = 11; h.initial.conversation_runs = [completed];
  await h.store.getState().controlConversation('run-a', 'resume', 8);
  const resume = h.calls.at(-1);
  assert.equal(resume[0], 'resume'); assert.equal(resume[3].additional_replies, 8);
  assert.equal(resume[3].expected_branch_revision, 11); assert.equal(resume[3].expected_player_identity_id, 'identity-a');
  h.store.getState().disconnect();
});

test('older run updates cannot regress a committed count, status or epoch', async () => {
  const h = harness(); await h.store.getState().selectSession('branch-a', false);
  const fresh = run({ epoch: 2, status: 'completed', completed_replies: 7, max_replies: 7, updated_at: '2026-01-01T00:00:07Z' });
  h.connections.at(-1).emit({ type: 'conversation.run.updated', run: fresh, branch_revision: 11 });
  h.connections.at(-1).emit({ type: 'conversation.run.updated', run: run(), branch_revision: 4 });
  assert.equal(h.store.getState().conversationRuns[0].epoch, 2);
  assert.equal(h.store.getState().conversationRuns[0].status, 'completed');
  assert.equal(h.store.getState().sessions[0].branch_revision, 11);
  h.store.getState().disconnect();
});

test('paused free-send HTTP cannot overwrite later intervention or clear its sending state', async () => {
  const first = deferred(), second = deferred(); let sends = 0;
  const h = harness({ sendMessage: () => (++sends === 1 ? first.promise : second.promise) });
  await h.store.getState().selectSession('branch-a', false);
  const initialSend = h.store.getState().sendMessage('合成自由交谈', ['npc-a'], undefined, 'free', 6);
  h.connections.at(-1).emit({ type: 'conversation.run.updated', run: run({ mode: 'free', status: 'paused', stop_reason: 'user_paused' }), branch_revision: 5 });
  assert.equal(h.store.getState().sending, false);
  const intervention = h.store.getState().sendMessage('合成干预', ['npc-a'], undefined, 'serial');
  assert.equal(h.store.getState().sending, true);
  h.connections.at(-1).emit({ type: 'conversation.run.updated', run: run({ mode: 'free', status: 'paused', stop_reason: 'user_paused' }), branch_revision: 5 });
  first.resolve({ messages: [message('old-http', 'npc-a')], errors: [], conversation_run: run({ mode: 'free', status: 'paused' }) });
  await initialSend;
  assert.equal(h.store.getState().sending, true);
  assert.equal(h.store.getState().messages.some((message) => message.id === 'old-http'), false);
  second.resolve({ messages: [], errors: [] }); await intervention;
  assert.equal(h.store.getState().sending, false);
  h.store.getState().disconnect();
});

test('conversation preferences are isolated by branch and control identity with safe malformed-storage defaults', () => {
  const entries = new Map(); const storage = { getItem: (key) => entries.get(key), setItem: (key, value) => entries.set(key, value) };
  const key = conversationPreferenceKey('branch-a', 'identity-a');
  saveConversationPreferences(storage, key, { recipients: ['npc-a', 'group-a'], replyMode: 'free', maxReplies: 7 });
  assert.deepEqual(readConversationPreferences(storage, key), { recipients: ['npc-a', 'group-a'], replyMode: 'free', maxReplies: 7 });
  assert.deepEqual(readConversationPreferences(storage, conversationPreferenceKey('branch-b', 'identity-a')).recipients, []);
  assert.deepEqual(readConversationPreferences(storage, conversationPreferenceKey('branch-a', 'identity-b')).recipients, []);
  entries.set(key, '{ malformed');
  assert.deepEqual(readConversationPreferences(storage, key), { recipients: [], replyMode: 'auto', maxReplies: 6 });
  entries.set(key, JSON.stringify({ recipients: ['npc-a', 'npc-a', 3], replyMode: 'invalid', maxReplies: 900 }));
  assert.deepEqual(readConversationPreferences(storage, key), { recipients: ['npc-a'], replyMode: 'auto', maxReplies: 30 });
  assert.equal(normalizeMaxReplies(-1), 1); assert.equal(normalizeMaxReplies('invalid'), 6);
  assert.equal(normalizeMaxReplies(''), 6);
  assert.doesNotThrow(() => saveConversationPreferences({ setItem() { throw new Error('synthetic private storage'); } }, key, {}));
});

test('candidate switching supplies concurrency fields and restores downstream stale state from the snapshot', async () => {
  let request; const h = harness({ switchVariant: async (...args) => {
    request = args;
    const selected = message('a', 'npc-a', { active_variant: 1, content: '合成候选二', fingerprint: 'fingerprint-a-two' });
    h.initial.messages = [selected, message('b', 'group-a', { seq: 2, dependency_stale: true, dependency_stale_sources: ['a'] })];
    h.initial.session.branch_revision = 5;
    return selected;
  } });
  await h.store.getState().selectSession('branch-a', false);
  await h.store.getState().switchVariant('a', 1);
  assert.equal(request[2], 1); assert.equal(request[3].expected_branch_revision, 4);
  assert.equal(request[3].expected_fingerprint, 'fingerprint-a'); assert.equal(request[3].expected_player_identity_id, 'identity-a');
  assert.equal(h.store.getState().messages[0].active_variant, 1);
  assert.equal(h.store.getState().messages[1].dependency_stale, true);
  assert.equal(h.store.getState().sending, false);
  h.store.getState().disconnect();
});

test('legacy untagged SSE continues to work for saved sessions', async () => {
  const h = harness(); await h.store.getState().selectSession('branch-a', false);
  const emit = (event) => h.connections.at(-1).emit(event);
  emit({ type: 'message.pending', message: message('legacy', 'npc-a', { status: 'pending', content: '' }) });
  emit({ type: 'message.delta', message_id: 'legacy', offset: 0, delta: '兼容旧消息', delivery: 'live' });
  assert.equal(h.store.getState().messages.find((message) => message.id === 'legacy').content, '兼容旧消息');
  emit({ type: 'message.final', message: message('legacy', 'npc-a', { content: '旧消息完成' }) });
  assert.equal(h.store.getState().messages.find((message) => message.id === 'legacy').content, '旧消息完成');
  h.store.getState().disconnect();
});

test('late conversation start and group reply HTTP cannot cross a branch navigation', async () => {
  for (const action of ['start', 'group']) {
    const pending = deferred(); const h = harness({ startConversation: () => pending.promise, replyGroup: () => pending.promise });
    await h.store.getState().selectSession('branch-a', false);
    const work = action === 'start' ? h.store.getState().startConversation(['npc-a'], 6) : h.store.getState().requestGroupReply('group-a');
    await h.store.getState().selectSession('branch-b', false);
    await h.store.getState().selectSession('branch-a', false);
    pending.resolve(action === 'start' ? run() : message('late-group', 'group-a'));
    await work;
    assert.equal(h.store.getState().conversationRuns.length, 0);
    assert.equal(h.store.getState().messages.some((message) => message.id === 'late-group'), false);
    assert.equal(h.store.getState().sending, false); assert.equal(h.store.getState().conversationActionBusy, null);
    h.store.getState().disconnect();
  }
});

test('conversation HTTP result cannot regress a more recent SSE state', async () => {
  const pending = deferred(); const h = harness({ startConversation: () => pending.promise });
  await h.store.getState().selectSession('branch-a', false);
  const work = h.store.getState().startConversation(['npc-a'], 6);
  h.connections.at(-1).emit({ type: 'conversation.run.updated', run: run({ status: 'completed', completed_replies: 2, stop_reason: 'natural_stop', updated_at: '2026-01-01T00:00:02Z' }), branch_revision: 6 });
  pending.resolve(run()); await work;
  assert.equal(h.store.getState().conversationRuns[0].status, 'completed');
  assert.equal(h.store.getState().conversationRuns[0].completed_replies, 2);
  h.store.getState().disconnect();
});

test('API serializes the agreed message operations, free budget and run controls', async () => {
  const requests = [];
  const imports = { './adapters': { mapSessionState: (value) => value, mapSessionSummary: (value) => value, toView: (value) => value },
    './request': { jsonRequest: async (path, input = {}) => { requests.push({ path, ...input, body: input.body ? JSON.parse(input.body) : undefined }); return { messages: [] }; } } };
  const exports = {};
  const compiled = ts.transpileModule(readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  new Function('require', 'exports', compiled)((name) => { if (storyImport(name, imports)) return storyImport(name, imports); assert.ok(name in imports, `Unexpected import ${name}`); return imports[name]; }, exports);
  const api = exports.api;
  const expected = { operation_id: 'operation-a', expected_branch_revision: 3, expected_fingerprint: 'fingerprint-a', expected_player_identity_id: 'identity-a' };
  for (const [method, suffix] of [['regenerateOne', 'regenerate-one'], ['regenerateDependents', 'regenerate-dependents'], ['acceptDependencies', 'accept-dependencies']]) {
    await api[method]('branch/a', 'message/a', expected);
    assert.equal(requests.at(-1).path, `/api/v1/sessions/branch%2Fa/messages/message%2Fa/${suffix}`);
    assert.equal(requests.at(-1).method, 'POST'); assert.deepEqual(requests.at(-1).body, expected);
  }
  await api.sendMessage('branch-a', '合成自由消息', ['npc-a'], undefined, 'client-a', 'free', 'identity-a', 7, ' 合成导演要求 ');
  assert.equal(requests.at(-1).body.max_replies, 7); assert.equal(requests.at(-1).body.reply_mode, 'free');
  assert.equal(requests.at(-1).body.conversation_directive, '合成导演要求');
  await api.sendMessage('branch-a', '合成普通消息', ['npc-a'], undefined, 'client-b', 'serial', 'identity-a', 7, '不会注入普通回复');
  assert.equal('max_replies' in requests.at(-1).body, false);
  assert.equal('conversation_directive' in requests.at(-1).body, false);
  await api.startConversation('branch-a', { operation_id: 'operation-a', participant_ids: ['npc-a'], max_replies: 6, directive: '合成要求', expected_branch_revision: 3, expected_player_identity_id: 'identity-a' });
  assert.equal(requests.at(-1).path, '/api/v1/sessions/branch-a/conversation-runs');
  await api.pauseConversation('branch-a', 'run/a'); assert.equal(requests.at(-1).path.endsWith('/run%2Fa/pause'), true);
  await api.stopConversation('branch-a', 'run/a'); assert.equal(requests.at(-1).path.endsWith('/run%2Fa/stop'), true);
  await api.resumeConversation('branch-a', 'run/a', { expected_branch_revision: 4, expected_player_identity_id: 'identity-a', additional_replies: 8 });
  assert.equal(requests.at(-1).path.endsWith('/run%2Fa/resume'), true); assert.equal(requests.at(-1).body.additional_replies, 8);
  await api.switchVariant('branch-a', 'message-a', 1, expected);
  assert.deepEqual(requests.at(-1).body, { active_variant: 1, ...expected });
  await api.getGenerationInspection('branch/a', 'message/a', 'generation/a');
  assert.equal(requests.at(-1).path, '/api/v1/sessions/branch%2Fa/generation-inspections/message%2Fa/generation%2Fa');
});

test('reconnection to a newer retry snapshot resets stale text and accepts the new attempt before HTTP completes', async () => {
  const pending = deferred(); let request;
  const h = harness({ regenerateOne: (...args) => { request = args; return pending.promise; } });
  await h.store.getState().selectSession('branch-a', false);
  const work = h.store.getState().regenerateOne('a');
  const first = { generation_id: 'retry-generation', operation_id: request[2].operation_id, attempt_id: 'first' };
  const second = { ...first, attempt_id: 'second' };
  h.connections.at(-1).emit({ type: 'message.pending', ...first, message: message('a', 'npc-a', { ...first, status: 'pending', content: '' }) });
  h.connections.at(-1).emit({ type: 'message.delta', ...first, message_id: 'a', offset: 0, delta: '旧尝试较长内容', delivery: 'live' });
  h.initial.messages[0] = message('a', 'npc-a', { ...second, status: 'pending', content: '新' });
  h.connections.at(-1).recover(); await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.store.getState().messages[0].content, '新');
  h.connections.at(-1).emit({ type: 'message.delta', ...second, message_id: 'a', offset: 1, delta: '尝试内容', delivery: 'live' });
  h.connections.at(-1).emit({ type: 'message.delta', ...first, message_id: 'a', offset: 0, delta: '迟到旧流', delivery: 'live' });
  assert.equal(h.store.getState().messages[0].content, '新尝试内容');
  pending.resolve({ messages: [message('a', 'npc-a', { ...second, content: '新尝试完成' })], branch_revision: 5, affected_message_ids: ['a'], operation_id: request[2].operation_id });
  await work; h.store.getState().disconnect();
});

test('stopping discards an unfinished reply and retires its late attempt without removing the committed prefix', async () => {
  const h = harness(); await h.store.getState().selectSession('branch-a', false);
  const prefix = h.store.getState().messages;
  const identity = { generation_id: 'stop-generation', operation_id: 'stop-operation', attempt_id: 'stop-attempt' };
  const emit = (event) => h.connections.at(-1).emit(event);
  emit({ type: 'message.pending', ...identity, message: message('unfinished', 'npc-a', { ...identity, status: 'pending', content: '' }) });
  emit({ type: 'message.delta', ...identity, message_id: 'unfinished', offset: 0, delta: '未提交内容', delivery: 'live' });
  emit({ type: 'message.error', ...identity, message_id: 'unfinished', error: '交谈已停止', discard_pending: true });
  emit({ type: 'conversation.run.updated', run: run({ session_id: 'branch-a', status: 'cancelled', epoch: 2, stop_reason: 'user_stopped' }), branch_revision: 4 });
  emit({ type: 'message.final', ...identity, message: message('unfinished', 'npc-a', { ...identity, content: '迟到的未提交结果' }) });
  assert.deepEqual(h.store.getState().messages, prefix);
  emit({ type: 'conversation.run.updated', run: run({ id: 'foreign-run', session_id: 'branch-b' }), branch_revision: 90 });
  assert.equal(h.store.getState().conversationRuns.length, 1);
  assert.equal(h.store.getState().sessions[0].branch_revision, 4);
  h.store.getState().disconnect();
});

function inspectorHarness(overrides = {}) {
  const calls = [];
  const api = { getInspection: async (...args) => { calls.push(['turn', ...args]); return { character_id: args[1], turn: args[2], prompt: '合成旧记录' }; },
    getGenerationInspection: async (...args) => { calls.push(['generation', ...args]); return { prompt: `合成单次记录 ${args[1]}/${args[2]}` }; }, ...overrides };
  const imports = { zustand: require('zustand'), '../api/client': { api }, '../features/stories/storyApi': { storyApi: api } }, exports = {};
  const compiled = ts.transpileModule(readFileSync(new URL('../src/store/inspectorStore.ts', import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  new Function('require', 'exports', compiled)((name) => { if (storyImport(name, imports)) return storyImport(name, imports); assert.ok(name in imports, `Unexpected import ${name}`); return imports[name]; }, exports);
  return { store: exports.useInspectorStore, calls };
}

test('same actor and turn inspections address different message generations and selected candidates', async () => {
  const h = inspectorHarness();
  await h.store.getState().load('branch-a', 'npc-a', 2, { characterId: 'npc-a', turn: 2, messageId: 'message-a-one', generationId: 'generation-a-one' });
  await h.store.getState().load('branch-a', 'npc-a', 2, { characterId: 'npc-a', turn: 2, messageId: 'message-a-two', generationId: 'generation-a-two' });
  await h.store.getState().load('branch-a', 'npc-a', 2, { characterId: 'npc-a', turn: 2, messageId: 'message-a-two', generationId: 'generation-a-two-candidate' });
  assert.deepEqual(h.calls, [
    ['generation', 'branch-a', 'message-a-one', 'generation-a-one'],
    ['generation', 'branch-a', 'message-a-two', 'generation-a-two'],
    ['generation', 'branch-a', 'message-a-two', 'generation-a-two-candidate'],
  ]);
  assert.equal(h.store.getState().source, 'generation');
  assert.equal(h.store.getState().notice, null);
});

test('inspection identity follows the selected candidate metadata and legacy absence stays explicit', () => {
  const candidates = [{ generation_meta: { generation_id: 'first-candidate' } }, { generation_meta: { generation_id: 'second-candidate' } }];
  assert.equal(generationIdForMessage(message('a', 'npc-a', { variants: candidates, active_variant: 0, generation_id: 'second-candidate', generation_meta: { generation_id: 'second-candidate' } })), 'first-candidate');
  assert.equal(generationIdForMessage(message('a', 'npc-a', { variants: candidates, active_variant: 1 })), 'second-candidate');
  assert.equal(generationIdForMessage(message('b', 'npc-a', { active_variant: null, generation_meta: { generation_id: 'other-same-turn-message' } })), 'other-same-turn-message');
  assert.equal(generationIdForMessage(message('legacy')), null);
});

test('legacy message inspection explicitly identifies the actor/turn fallback while director entry stays supported', async () => {
  const h = inspectorHarness();
  await h.store.getState().load('branch-a', 'group-a', 2, { characterId: 'group-a', turn: 2, messageId: 'old-message', generationId: null });
  assert.deepEqual(h.calls[0], ['turn', 'branch-a', 'group-a', 2]);
  assert.match(h.store.getState().notice, /没有单次生成标识/);
  assert.match(h.store.getState().notice, /无法确认它对应这条回应/);
  await h.store.getState().load('branch-a', 'npc-a', 2);
  assert.deepEqual(h.calls[1], ['turn', 'branch-a', 'npc-a', 2]);
  assert.equal(h.store.getState().notice, null);
});

test('an old inspection response or failure cannot replace a newer message or a cleared branch', async () => {
  const first = deferred(), second = deferred(); let count = 0;
  const h = inspectorHarness({ getGenerationInspection: () => (++count === 1 ? first.promise : second.promise) });
  const a = h.store.getState().load('branch-a', 'npc-a', 2, { characterId: 'npc-a', turn: 2, messageId: 'a', generationId: 'a-gen' });
  const b = h.store.getState().load('branch-a', 'npc-a', 2, { characterId: 'npc-a', turn: 2, messageId: 'b', generationId: 'b-gen' });
  second.resolve({ prompt: '新合成检查' }); await b;
  first.reject(new Error('迟到的旧检查失败')); await a;
  assert.equal(h.store.getState().prompt.prompt, '新合成检查');
  assert.equal(h.store.getState().loading, false);
  const pending = deferred(); const other = inspectorHarness({ getGenerationInspection: () => pending.promise });
  const work = other.store.getState().load('branch-a', 'npc-a', 2, { characterId: 'npc-a', turn: 2, messageId: 'a', generationId: 'a-gen' });
  other.store.getState().clear(); pending.resolve({ prompt: '旧分支检查' }); await work;
  assert.equal(other.store.getState().prompt, null); assert.equal(other.store.getState().loading, false);
});

test('an unavailable exact generation inspection fails without silently querying an actor/turn substitute', async () => {
  const h = inspectorHarness({ getGenerationInspection: async () => { throw new Error('synthetic missing generation'); } });
  await assert.rejects(h.store.getState().load('branch-a', 'npc-a', 2, { characterId: 'npc-a', turn: 2, messageId: 'a', generationId: 'a-gen' }), /synthetic missing generation/);
  assert.equal(h.calls.length, 0); assert.equal(h.store.getState().prompt, null); assert.equal(h.store.getState().loading, false);
});

test('conversation panel warns about incomplete usage only when the backend flag is true', () => {
  const exports = {};
  const imports = { 'react/jsx-runtime': require('react/jsx-runtime'),
    '../store/chatStore': { isConversationActive: (value) => value.status === 'running' || value.status === 'queued' } };
  const compiled = ts.transpileModule(readFileSync(new URL('../src/components/ConversationRunPanel.tsx', import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  new Function('require', 'exports', compiled)((name) => { if (storyImport(name, imports)) return storyImport(name, imports); assert.ok(name in imports, `Unexpected import ${name}`); return imports[name]; }, exports);
  const render = require('react-dom/server').renderToStaticMarkup;
  const createElement = require('react').createElement;
  const markup = (usage_incomplete) => render(createElement(exports.ConversationRunPanel, {
    run: run({ status: 'cancelled', usage_incomplete }), speakerName: null, busy: false,
    contextMatches: true, maxReplies: 6, onControl() {}, onIntervene() {},
  }));
  const notice = '上游用量可能未全部返回，目前仅统计已确认用量。';
  assert.equal(markup(true).includes(notice), true);
  assert.equal(markup(false).includes(notice), false);
  assert.equal(markup(undefined).includes(notice), false);
  assert.equal(markup(true).includes('aria-label="交谈运行状态"'), true);
});

test('a parent branch paused run is retained for audit but cannot be controlled on the child branch', async () => {
  const h = harness();
  h.initial.conversation_runs = [run({ id: 'parent-run', session_id: 'parent-branch', status: 'paused', scene_id: 'scene-a', player_identity_id: 'identity-a' })];
  await h.store.getState().selectSession('branch-a', false);
  assert.equal(h.store.getState().conversationRuns[0].id, 'parent-run');
  assert.equal(h.exports.isConversationActive(h.store.getState().conversationRuns[0], 'branch-a'), false);
  await assert.rejects(h.store.getState().controlConversation('parent-run', 'resume'), /属于另一条路线/);
  await assert.rejects(h.store.getState().controlConversation('parent-run', 'stop'), /属于另一条路线/);
  assert.equal(h.calls.length, 0);
  assert.equal(h.store.getState().conversationRuns[0].status, 'paused');
  assert.equal(h.store.getState().conversationActionBusy, null);
  h.store.getState().disconnect();
});

test('external queued or running snapshot traces never block current-branch sending or a new local run', async () => {
  for (const status of ['queued', 'running']) {
    const h = harness();
    h.initial.conversation_runs = [run({ id: 'parent-run', session_id: 'parent-branch', status })];
    await h.store.getState().selectSession('branch-a', false);
    assert.equal(h.exports.isConversationActive(h.store.getState().conversationRuns[0], 'branch-a'), false);
    assert.equal(h.exports.isConversationActive(run({ session_id: 'branch-a' }), 'branch-a'), true);
    assert.equal(h.exports.isConversationActive(run(), 'branch-a'), true);
    await h.store.getState().sendMessage('当前路线合成回应', ['npc-a'], undefined, 'serial');
    assert.equal(h.calls[0][0], 'send');
    await h.store.getState().startConversation(['npc-a'], 6);
    assert.equal(h.calls[1][0], 'start');
    assert.equal(h.store.getState().conversationRuns.some((item) => item.id === 'parent-run'), true);
    h.store.getState().disconnect();
  }
});

test('parent run panel remains visible but its resume and stop controls are disabled', () => {
  const exports = {};
  const imports = { 'react/jsx-runtime': require('react/jsx-runtime'),
    '../store/chatStore': { isConversationActive: (value) => value.status === 'running' || value.status === 'queued' } };
  const compiled = ts.transpileModule(readFileSync(new URL('../src/components/ConversationRunPanel.tsx', import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  new Function('require', 'exports', compiled)((name) => { if (storyImport(name, imports)) return storyImport(name, imports); assert.ok(name in imports, `Unexpected import ${name}`); return imports[name]; }, exports);
  const render = require('react-dom/server').renderToStaticMarkup;
  const element = require('react').createElement;
  const html = render(element(exports.ConversationRunPanel, {
    run: run({ session_id: 'parent-branch', status: 'paused' }), speakerName: null, busy: false,
    contextMatches: false, readOnly: true, maxReplies: 6, onControl() {}, onIntervene() {},
  }));
  assert.match(html, /此前交谈记录/);
  assert.match(html, /这段交谈属于另一条路线，仅供查看/);
  assert.match(html, /aria-label="继续交谈"[^>]*disabled=""/);
  assert.match(html, /aria-label="立即停止交谈"[^>]*disabled=""/);
});

test('free send forwards the composer director requirement while ordinary send omits it', async () => {
  const h = harness(); await h.store.getState().selectSession('branch-a', false);
  await h.store.getState().sendMessage('合成自由回应', ['npc-a'], undefined, 'free', 6, '合成导演要求');
  assert.equal(h.calls[0][9], '合成导演要求');
  await h.store.getState().sendMessage('合成普通回应', ['npc-a'], undefined, 'serial', 6, '不应带入普通回复');
  assert.equal(h.calls[1][9], undefined);
  h.store.getState().disconnect();
});
