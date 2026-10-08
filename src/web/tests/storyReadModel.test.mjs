import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { storyImport, storyProjection, receiptModule } from './storySupport.mjs';
import { createClientId } from '../src/utils/clientId.js';
const require = createRequire(import.meta.url);
function load(path, imports = {}) {
  const exports = {};
  const code = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  new Function('require', 'exports', code)(name => imports[name] ?? storyImport(name, imports) ?? require(name), exports);
  return exports;
}
const message = seq => ({ id: `m-${seq}`, session_id: 'branch-a', seq, turn: Math.floor(seq / 2) + 1,
  actor: 'npc-a', kind: 'roleplay', status: 'final', content: `Synthetic ${seq}`, variants: [], generation_meta: null });
function fixture(getView) {
  const all = Array.from({ length: 240 }, (_, seq) => message(seq));
  const requests = [];
  const view = (start, end) => ({ session: { id: 'branch-a', branch_revision: 9, turn: 120, pinned_facts: [] },
    messages: all.slice(start, end), characters: [], lorebooks: [], groups: [], scenes: [], active_scene_id: null,
    event_cursor: 'cursor:9', next_before_seq: start || null, latest_seq: 239, turn_runs: [], conversation_runs: [] });
  const api = { getStoryView: async (sid, options = {}) => {
    requests.push({ sid, ...options });
    if (getView) return getView(sid, options, view);
    return options.around ? view(0, 100) : options.before_seq ? view(Math.max(0, options.before_seq - 100), options.before_seq) : view(140, 240);
  }, listSessions: async () => [{ id: 'branch-a', branch_revision: 9, title: 'Summary', turn: 120 }] };
  const exports = load('../src/store/chatStore.ts', {
    zustand: require('zustand'), '../features/stories/storyApi': { storyApi: api },
    '../api/adapters': { toView: value => value },
    '../api/navigation': { readStoryLocation: () => ({}), writeStoryLocation() {} },
    '../utils/channels': {}, '../utils/assistFreshness': {}, '../utils/clientId.js': { createClientId },
    '../utils/optimisticMessages': { pendingAfterSnapshot: () => [], removeConfirmedLocalSegment: value => value },
    '../types': { SSE_EVENT_TYPES: [] },
    '../api/sse': { connectSSE: () => ({ addEventListener() {}, close() {} }) },
  });
  return { ...exports, api, requests, view };
}
test('summary refresh does not own or empty fixed facts on the current branch', async () => {
  const { useChatStore: store } = fixture();
  store.setState({ sessions: [{ id: 'branch-a', branch_revision: 9, pinned_facts: [{ id: 'fact-a', content: 'Synthetic fact' }] }] });
  await store.getState().loadSessions();
  assert.equal(store.getState().sessions[0].pinned_facts[0].id, 'fact-a');
  assert.equal(store.getState().sessions[0].title, 'Summary');
});

test('a tool partial receipt cannot advance the installed message revision or swallow another message commit', () => {
  const { useChatStore: store, storyBranchEpoch } = fixture();
  const runtime = load('../src/features/stories/storyRuntime.ts', {
    '../../store/chatStore': { useChatStore: store, storyBranchEpoch }, '../../api/adapters': { toView: value => value },
    '../../utils/commandReceipt': receiptModule,
  }).storyRuntime;
  store.setState({ currentSessionId: 'branch-a', sessions: [{ id: 'branch-a', branch_revision: 9, options_style: 'mixed' }],
    appliedSnapshotRevision: 9, messages: [message(1)], latestSeq: 1 });
  runtime.applySessionPatch('branch-a', receiptModule.attachCommandReceipt({ options_style: 'action' }, { command_id: 'tool-new', branch_revision: 11 }));
  assert.equal(store.getState().sessions[0].branch_revision, 9);
  runtime.applySessionPatch('branch-a', receiptModule.attachCommandReceipt({ options_style: 'mixed' }, { command_id: 'tool-old', branch_revision: 10 }));
  assert.equal(store.getState().sessions[0].options_style, 'action');
  store.getState().handleSseEvent({ type: 'message.updated', message: { ...message(1), content: 'Another commit' }, branch_revision: 10, outbox_event_id: 'tool-before-message' });
  assert.equal(store.getState().messages[0].content, 'Another commit');
  assert.equal(store.getState().sessions[0].branch_revision, 10);
});
test('reader projection removes nested materials without changing the original or generation identities', () => {
  const original = { ...message(1), generation_meta: { generation_id: 'g-a', provenance: { baseline_state: { secret: 'archive' }, sources: [{ message_id: 'm-0' }] } },
    variants: [{ id: 'v-a', content: 'Candidate', generation_meta: { provenance: { baseline_state: { secret: 'variant' } } } }] };
  const projected = storyProjection.projectMessage(original);
  assert.equal(projected.generation_meta.generation_id, 'g-a');
  assert.deepEqual(projected.generation_meta.provenance.sources, [{ message_id: 'm-0' }]);
  assert.equal(JSON.stringify(projected).includes('baseline_state'), false);
  assert.equal(original.generation_meta.provenance.baseline_state.secret, 'archive');
  assert.equal(storyProjection.projectMessage(projected), projected);
});
test('store enforces projection for external writes and all message SSE ingress; turn runs lose parallel context', () => {
  const { useChatStore: store } = fixture();
  const heavy = { ...message(1), generation_meta: { provenance: { baseline_state: { content: 'Heavy material' } } } };
  store.setState({ currentSessionId: 'branch-a', messages: [heavy], turnRuns: [{ operation_id: 'op-a', parallel_context: { content: 'Heavy run' } }] });
  assert.equal(JSON.stringify(store.getState().messages).includes('baseline_state'), false);
  assert.equal('parallel_context' in store.getState().turnRuns[0], false);
  for (const type of ['message.pending', 'message.final', 'message.updated', 'message.error']) {
    store.getState().handleSseEvent({ type, message_id: 'm-1', message: { ...heavy, status: type === 'message.pending' ? 'pending' : 'final' }, error: 'Synthetic error' });
    assert.equal(JSON.stringify(store.getState().messages).includes('baseline_state'), false, type);
  }
  store.getState().disconnect();
});
test('an exact 100-note page has an earlier cursor, and loading earlier prepends without duplicates', async () => {
  const { useChatStore: store } = fixture();
  await store.getState().selectSession('branch-a', false);
  assert.equal(store.getState().messages.length, 100);
  assert.equal(store.getState().nextBeforeSeq, 140);
  await store.getState().loadEarlier();
  assert.equal(store.getState().messages.length, 200);
  assert.equal(new Set(store.getState().messages.map(message => message.id)).size, 200);
  assert.equal(store.getState().nextBeforeSeq, 40);
  store.getState().disconnect();
});
test('old around window resync stays contiguous and uses the full branch turn/latest seq', async () => {
  const { useChatStore: store, requests } = fixture();
  await store.getState().selectSession('branch-a', false);
  await store.getState().locateMessage('m-20');
  await store.getState().refreshSession('branch-a');
  assert.equal(requests.at(-1).around, 'm-20');
  assert.deepEqual(store.getState().messages.map(message => message.seq), Array.from({ length: 100 }, (_, seq) => seq));
  assert.equal(store.getState().sessions[0].turn, 120);
  assert.equal(store.getState().latestSeq, 239);
  store.getState().handleSseEvent({ type: 'message.final', message: message(240) });
  assert.equal(store.getState().messages.length, 100);
  assert.equal(store.getState().latestSeq, 240);
  assert.equal(store.getState().sessions[0].turn, 121);
  store.getState().disconnect();
});
test('a slow earlier page cannot attach to a newer around window on the same branch', async () => {
  let finish;
  const { useChatStore: store } = fixture((_sid, options, view) => options.before_seq
    ? new Promise(resolve => { finish = () => resolve(view(40, 140)); }) : options.around ? view(0, 100) : view(140, 240));
  await store.getState().selectSession('branch-a', false);
  const earlier = store.getState().loadEarlier();
  await store.getState().locateMessage('m-20');
  finish(); await earlier;
  assert.equal(store.getState().messages.at(-1).seq, 99);
  store.getState().disconnect();
});


test('durable replay rejects old revisions and duplicates but accepts every event in one commit', () => {
  const { useChatStore: store } = fixture();
  store.setState({ currentSessionId: 'branch-a', sessions: [{ id: 'branch-a', turn: 120, branch_revision: 9 }], messages: [message(1)], latestSeq: 239 });
  const send = (type, seq, revision, id, content) => store.getState().handleSseEvent({ type, message: { ...message(seq), content }, message_id: `m-${seq}`, turn: 1, branch_revision: revision, outbox_event_id: id });
  send('message.updated', 1, 8, 'old-update', 'Stale');
  send('message.deleted', 1, 8, 'old-delete');
  assert.equal(store.getState().messages[0].content, 'Synthetic 1');
  send('message.updated', 1, 10, 'commit-a', 'Current first');
  send('message.updated', 2, 10, 'commit-b', 'Current second');
  assert.equal(store.getState().sessions[0].branch_revision, 10);
  send('message.updated', 1, 9, 'late-old-commit', 'Late stale');
  assert.equal(store.getState().messages.length, 2);
  send('message.updated', 1, 10, 'commit-a', 'Duplicate');
  assert.equal(store.getState().messages.find(item => item.seq === 1).content, 'Current first');
});


test('foreign branch messages cannot advance current branch global turn or sequence', () => {
  const { useChatStore: store } = fixture();
  store.setState({ currentSessionId: 'branch-a', sessions: [{ id: 'branch-a', turn: 4, branch_revision: 9 }], messages: [message(1)], latestSeq: 7 });
  store.getState().handleSseEvent({ type: 'message.final', message: { ...message(999), session_id: 'branch-b' } });
  assert.equal(store.getState().latestSeq, 7);
  assert.equal(store.getState().sessions[0].turn, 4);
  assert.equal(store.getState().messages.length, 1);
});

test('SSE decoder rejects malformed payloads and projects material before store ingress', () => {
  const { decodeStoryEvent } = load('../src/features/stories/storyEvents.ts', { './storyView': storyProjection });
  assert.equal(decodeStoryEvent('message.final', { message: { id: 'invalid' } }), null);
  assert.equal(decodeStoryEvent('message.delta', { message_id: 'm-1', delta: 'text', offset: 'invalid' }), null);
  const event = decodeStoryEvent('message.final', { type: 'message.deleted', message: { ...message(1), generation_meta: { baseline_state: { private: true } } } });
  assert.equal(event.type, 'message.final');
  assert.equal('baseline_state' in event.message.generation_meta, false);
});


test('a newer list summary cannot retire an unapplied final commit on the current branch', async () => {
  const { useChatStore: store, api } = fixture();
  store.setState({ currentSessionId: 'branch-a', sessions: [{ id: 'branch-a', turn: 2, branch_revision: 9 }], messages: [message(1)], latestSeq: 1 });
  api.listSessions = async () => [{ id: 'branch-a', title: 'Newer summary', turn: 3, branch_revision: 11 }];
  await store.getState().loadSessions();
  assert.equal(store.getState().sessions[0].branch_revision, 9);
  store.getState().handleSseEvent({ type: 'message.final', message: message(2), branch_revision: 10, outbox_event_id: 'summary-before-final' });
  assert.equal(store.getState().messages.find(item => item.id === 'm-2').content, 'Synthetic 2');
  assert.equal(store.getState().sessions[0].branch_revision, 10);
});


test('old HTTP edit receipt cannot overwrite a newer event; duplicate receipt is idempotent', async () => {
  const { useChatStore: store, api } = fixture();
  const response = receiptModule.attachCommandReceipt({ ...message(1), content: 'Receipt content' }, { command_id: 'http-edit', branch_revision: 10 });
  api.editMessage = async () => response;
  store.setState({ currentSessionId: 'branch-a', appliedSnapshotRevision: 9, sessions: [{ id: 'branch-a', turn: 2, branch_revision: 9 }], messages: [message(1)], latestSeq: 1 });
  await store.getState().editMessage('m-1', 'Receipt content', 9, 'original');
  await store.getState().editMessage('m-1', 'Receipt content', 9, 'original');
  assert.equal(store.getState().messages.length, 1);
  store.getState().handleSseEvent({ type: 'message.updated', message: { ...message(1), content: 'Newer event' }, branch_revision: 11, outbox_event_id: 'newer-than-receipt' });
  await store.getState().editMessage('m-1', 'Receipt content', 9, 'original');
  assert.equal(store.getState().messages[0].content, 'Newer event');
  assert.equal(store.getState().sessions[0].branch_revision, 11);
});

test('a delayed edit response cannot attach after switching A to B and back to A', async () => {
  const { useChatStore: store, api } = fixture(); let finish;
  api.editMessage = async () => new Promise(resolve => { finish = resolve; });
  store.setState({ currentSessionId: 'branch-a', sessions: [{ id: 'branch-a', turn: 2, branch_revision: 9 }], messages: [message(1)], latestSeq: 1 });
  const editing = store.getState().editMessage('m-1', 'Delayed edit', 9, 'original');
  await store.getState().selectSession('branch-b', false);
  await store.getState().selectSession('branch-a', false);
  const before = store.getState().messages;
  finish(receiptModule.attachCommandReceipt({ ...message(1), content: 'Delayed edit' }, { command_id: 'http-aba', branch_revision: 10 }));
  await editing;
  assert.equal(store.getState().messages, before);
  store.getState().disconnect();
});

test('cross-message outbox disorder schedules one snapshot recovery instead of dropping a commit forever', async () => {
  const { useChatStore: store, requests } = fixture((_sid, _options, view) => ({ ...view(0, 3), session: { id: 'branch-a', branch_revision: 11, turn: 2 },
    messages: [{ ...message(1), content: 'New A' }, { ...message(2), content: 'New B' }] }));
  store.setState({ currentSessionId: 'branch-a', appliedSnapshotRevision: 9, sessions: [{ id: 'branch-a', turn: 2, branch_revision: 9 }], messages: [message(1), message(2)], latestSeq: 2 });
  store.getState().handleSseEvent({ type: 'message.updated', message: { ...message(1), content: 'New A' }, branch_revision: 11, outbox_event_id: 'cross-a' });
  const late = { type: 'message.updated', message: { ...message(2), content: 'New B' }, branch_revision: 10, outbox_event_id: 'cross-b' };
  store.getState().handleSseEvent(late); store.getState().handleSseEvent(late);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.length, 1);
  assert.equal(store.getState().messages.find(message => message.id === 'm-2').content, 'New B');
  store.getState().handleSseEvent({ ...late, outbox_event_id: 'already-in-snapshot' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.length, 1);
  store.getState().disconnect();
});


test('a preserved loaded history prefix cannot inherit the newer tail snapshot floor', async () => {
  const { useChatStore: store, requests } = fixture((_sid, options, view) => {
    const page = options.around ? view(0, 100) : view(100, 240);
    return { ...page, session: { id: 'branch-a', branch_revision: 11, turn: 120 },
      messages: page.messages.map(item => item.id === 'm-30' ? { ...item, content: 'Edited historical page' } : item) };
  });
  store.setState({ currentSessionId: 'branch-a', appliedSnapshotRevision: 9, sessions: [{ id: 'branch-a', branch_revision: 9, turn: 120 }],
    messages: Array.from({ length: 240 }, (_, seq) => message(seq)), locatedMessageId: 'm-30', nextBeforeSeq: null, latestSeq: 239 });
  await store.getState().refreshSession('branch-a');
  assert.equal(store.getState().appliedSnapshotRevision, 9);
  assert.equal(store.getState().messages.find(message => message.id === 'm-30').content, 'Synthetic 30');
  store.getState().handleSseEvent({ type: 'message.updated', message: { ...message(30), content: 'Edited historical page' }, branch_revision: 10, outbox_event_id: 'old-page-unseen-edit' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.length, 2); assert.equal(requests[1].around, 'm-30');
  assert.equal(store.getState().messages.find(message => message.id === 'm-30').content, 'Edited historical page');
  assert.equal(store.getState().appliedSnapshotRevision, 11);
  store.getState().disconnect();
});

function runtimeFor(store, storyBranchEpoch) {
  return load('../src/features/stories/storyRuntime.ts', {
    '../../store/chatStore': { useChatStore: store, storyBranchEpoch }, '../../api/adapters': { toView: value => value },
    '../../utils/commandReceipt': receiptModule,
  }).storyRuntime;
}
function partialStyle(runtime, revision, style) {
  runtime.applySessionPatch('branch-a', receiptModule.attachCommandReceipt({ options_style: style }, { command_id: `partial-${revision}`, branch_revision: revision }));
}
test('metadata fields share summary and partial cursors without swallowing message commits', async () => {
  const { useChatStore: store, storyBranchEpoch, api } = fixture();
  const runtime = runtimeFor(store, storyBranchEpoch);
  store.setState({ currentSessionId: 'branch-a', sessions: [{ id: 'branch-a', branch_revision: 9, options_style: 'mixed' }], appliedSnapshotRevision: 9, messages: [message(1)], latestSeq: 1 });
  partialStyle(runtime, 11, 'action');
  api.listSessions = async () => [{ id: 'branch-a', branch_revision: 10, options_style: 'mixed' }];
  await store.getState().loadSessions();
  assert.equal(store.getState().sessions[0].options_style, 'action');
  api.listSessions = async () => [{ id: 'branch-a', branch_revision: 12, options_style: 'dialogue' }];
  await store.getState().loadSessions();
  partialStyle(runtime, 11, 'action');
  assert.equal(store.getState().sessions[0].options_style, 'dialogue');
  assert.equal(store.getState().sessions[0].branch_revision, 9);
  store.getState().handleSseEvent({ type: 'message.updated', message: { ...message(1), content: 'Message revision ten' }, branch_revision: 10, outbox_event_id: 'metadata-independent-message' });
  assert.equal(store.getState().messages[0].content, 'Message revision ten');
  assert.equal(store.getState().sessions[0].branch_revision, 10);
  store.getState().disconnect();
});
test('all complete view installs retain newer partial metadata while installing message revision independently', async () => {
  const { useChatStore: store, storyBranchEpoch, api, view } = fixture();
  const runtime = runtimeFor(store, storyBranchEpoch);
  await store.getState().selectSession('branch-a', false);
  partialStyle(runtime, 12, 'action');
  const older = { ...view(140, 240), session: { ...view(140, 240).session, branch_revision: 11, options_style: 'mixed' } };
  runtime.applyView('branch-a', older);
  assert.equal(store.getState().sessions[0].options_style, 'action');
  assert.equal(store.getState().sessions[0].branch_revision, 9);
  api.getStoryView = async () => older;
  await store.getState().refreshSession('branch-a');
  assert.equal(store.getState().sessions[0].branch_revision, 11);
  assert.equal(store.getState().sessions[0].options_style, 'action');
  await store.getState().locateMessage('m-150');
  assert.equal(store.getState().sessions[0].options_style, 'action');
  store.getState().applyRestore(older.session, []);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(store.getState().sessions[0].options_style, 'action');
  api.getStoryView = async () => ({ ...older, session: { ...older.session, branch_revision: 13, options_style: 'dialogue' } });
  await store.getState().refreshSession('branch-a');
  assert.equal(store.getState().sessions[0].options_style, 'dialogue');
  assert.equal(store.getState().sessions[0].branch_revision, 13);
  store.getState().disconnect();
});
test('disconnect and same-branch reselection keep metadata cursors; genuine branch ABA resets them', async () => {
  const { useChatStore: store, storyBranchEpoch, api, view } = fixture();
  await store.getState().selectSession('branch-a', false);
  const runtime = runtimeFor(store, storyBranchEpoch);
  partialStyle(runtime, 11, 'action');
  store.getState().disconnect();
  api.listSessions = async () => [{ id: 'branch-a', branch_revision: 10, options_style: 'mixed' }];
  await store.getState().loadSessions();
  assert.equal(store.getState().sessions[0].options_style, 'action');
  api.getStoryView = async sid => ({ ...view(140, 240), session: { id: sid, branch_revision: 9, options_style: 'mixed', pinned_facts: [] } });
  await store.getState().selectSession('branch-a', false);
  assert.equal(store.getState().sessions[0].options_style, 'action');
  const ownsA = runtime.capture('branch-a');
  await store.getState().selectSession('branch-b', false);
  await store.getState().selectSession('branch-a', false);
  assert.equal(ownsA(), false);
  assert.equal(store.getState().sessions.find(s => s.id === 'branch-a').options_style, 'mixed');
  assert.equal(store.getState().sessions.find(s => s.id === 'branch-a').branch_revision, 9);
  store.getState().disconnect();
});
