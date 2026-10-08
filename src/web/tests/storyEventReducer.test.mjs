import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { storyReducer } from './storySupport.mjs';
const message = (id, seq, extra = {}) => ({ id, seq, turn: 2, session_id: 'a', actor: 'npc', kind: 'roleplay', content: id, status: 'final', ...extra });
const state = (extra = {}) => ({ branchId: 'a', revision: 3, seenOutboxIds: [], messages: [], latestSeq: -1, turn: 0,
  locatedMessageId: null, turnRuns: [], attempts: new Map(), regenerationPrevious: {}, ...extra });
const final = (id, seq, revision = 3, extra = {}) => ({ type: 'message.final', message: message(id, seq), branch_revision: revision, outbox_event_id: id, ...extra });
test('committed replay merges by identity in sequence order and accepts every event at one revision', () => {
  let current = state();
  for (const event of [final('later', 9, 4), final('earlier', 8, 4), final('later', 9, 4)]) current = storyReducer.reduceStoryEvent(current, event).state;
  assert.deepEqual(current.messages.map(message => message.id), ['earlier', 'later']);
  assert.equal(current.revision, 4); assert.equal(current.latestSeq, 9);
  const result = storyReducer.reduceStoryEvent(current, { type: 'message.deleted', message_id: 'later', branch_revision: 3, outbox_event_id: 'old-delete' });
  assert.equal(result.accepted, false); assert.equal(result.state, current);
});
test('foreign messages/runs cannot change the ledger, attempts or global metadata', () => {
  const current = state();
  for (const event of [{ ...final('foreign', 100, 99), message: message('foreign', 100, { session_id: 'b' }) },
    { type: 'turn.run.updated', run: { session_id: 'b', operation_id: 'other' }, branch_revision: 99 }]) {
    assert.equal(storyReducer.reduceStoryEvent(current, event).state, current);
  }
});
test('attempt ABA and late final/delta cannot reopen a retired generation', () => {
  let current = state();
  const identity = attempt => ({ generation_id: `generation-${attempt}`, attempt_id: attempt, operation_id: 'one-operation' });
  const pending = attempt => ({ type: 'message.pending', message: message('same', 0, { status: 'pending', ...identity(attempt) }), ...identity(attempt) });
  current = storyReducer.reduceStoryEvent(current, pending('A')).state;
  const old = current.attempts.get('same');
  current = storyReducer.reduceStoryEvent(current, pending('B')).state;
  assert.equal(old.retired.size, 0, 'the input slot remains immutable');
  assert.equal(storyReducer.reduceStoryEvent(current, pending('A')).accepted, false);
  assert.equal(storyReducer.reduceStoryEvent(current, { ...final('same', 0), ...identity('A'), message: message('same', 0, identity('A')) }).accepted, false);
  current = { ...current, messages: [message('same', 0, { status: 'pending', ...identity('B') })] };
  assert.equal(storyReducer.reduceStoryEvent(current, { type: 'message.delta', message_id: 'same', offset: 0, delta: 'stale', ...identity('A') }).accepted, false);
});
test('unowned delta cannot create an attempt; history-window delivery leaves global progress intact', () => {
  const current = state({ messages: [message('old', 0)], locatedMessageId: 'old' });
  const missing = storyReducer.reduceStoryEvent(current, { type: 'message.delta', message_id: 'absent', generation_id: 'foreign', delta: 'text', offset: 0 });
  assert.equal(missing.state, current);
  const distant = storyReducer.reduceStoryEvent(current, final('latest', 100, 4));
  assert.equal(distant.accepted, true); assert.equal(distant.deliver, false);
  assert.equal(distant.state.latestSeq, 100); assert.equal(distant.state.messages, current.messages);
});
test('the pure reducer dependency closure excludes React, Zustand, API and side effects using AST', () => {
  const paths = ['../src/features/stories/storyEventReducer.ts', '../src/features/stories/storyView.ts', '../src/utils/optimisticMessages.ts'];
  for (const path of paths) {
    const ast = ts.createSourceFile(path, readFileSync(new URL(path, import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true);
    const violations = [];
    function visit(node) {
      if (ts.isImportDeclaration(node) && !node.importClause?.isTypeOnly) {
        const source = node.moduleSpecifier.text;
        if (/react|zustand|\/api\/|store|request|network/i.test(source) || !source.startsWith('.')) violations.push(source);
      }
      if (ts.isCallExpression(node)) {
        const callee = node.expression;
        if (ts.isIdentifier(callee) && ['fetch', 'setTimeout', 'setInterval', 'requestAnimationFrame'].includes(callee.text)) violations.push(callee.text);
        if (ts.isPropertyAccessExpression(callee) && ['window', 'document', 'localStorage', 'sessionStorage', 'Date', 'crypto'].includes(callee.expression.getText(ast))) violations.push(callee.getText(ast));
      }
      ts.forEachChild(node, visit);
    }
    visit(ast); assert.deepEqual(violations, [], path);
  }
});


test('unknown lower cross-message commit requests recovery once, while HTTP receipts never request it', () => {
  const current = state({ revision: 11, snapshotRevision: 9, messages: [message('A', 1), message('B', 2)] });
  const late = { type: 'message.updated', message: message('B', 2, { content: 'B at revision 10' }), branch_revision: 10, outbox_event_id: 'B-10' };
  const result = storyReducer.reduceStoryEvent(current, late);
  assert.equal(result.accepted, false); assert.deepEqual(result.effects, ['resync']);
  assert.deepEqual(storyReducer.reduceStoryEvent(result.state, late).effects, []);
  assert.deepEqual(storyReducer.reduceStoryEvent(current, { ...late, delivery_source: 'http' }).effects, []);
  assert.deepEqual(storyReducer.reduceStoryEvent({ ...current, snapshotRevision: 11 }, late).effects, []);
});
