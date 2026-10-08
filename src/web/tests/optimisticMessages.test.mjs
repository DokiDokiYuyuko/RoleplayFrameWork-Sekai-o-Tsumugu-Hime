import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/optimisticMessages.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const exports = {};
new Function('exports', compiled)(exports);
const { removeConfirmedLocalSegment, pendingAfterSnapshot } = exports;

const segment = (id, content, status = 'pending') => ({
  id, actor: 'player', turn: 4, kind: 'scene', content, status,
});

test('confirming each story segment keeps later optimistic bubbles until their own final', () => {
  const first = segment('msg-client', '开场');
  const second = segment('local-channel-msg-client-1', '风起');
  const third = segment('local-channel-msg-client-2', '灯灭');
  const firstFinal = segment(first.id, first.content, 'final');
  let messages = removeConfirmedLocalSegment([first, second, third], firstFinal);
  assert.deepEqual(messages.map((message) => message.id), [first.id, second.id, third.id]);
  messages = [firstFinal, ...messages.slice(1)];

  const secondFinal = segment('msg-server-second', '风起', 'final');
  messages = [...removeConfirmedLocalSegment(messages, secondFinal), secondFinal];
  assert.deepEqual(messages.map((message) => message.id), [first.id, third.id, secondFinal.id]);

  // Replayed final already exists; it must not consume the third local segment.
  assert.deepEqual(removeConfirmedLocalSegment(messages, secondFinal), messages);
  const thirdFinal = segment('msg-server-third', '灯灭', 'final');
  messages = [...removeConfirmedLocalSegment(messages, thirdFinal), thirdFinal];
  assert.equal(messages.some((message) => message.id.startsWith('local-channel-')), false);
});

test('equal text in two parts still confirms one segment at a time', () => {
  const a = segment('local-channel-msg-client-1', '重复');
  const b = segment('local-channel-msg-client-2', '重复');
  const remaining = removeConfirmedLocalSegment([a, b], segment('msg-server', '重复', 'final'));
  assert.deepEqual(remaining.map((message) => message.id), [b.id]);
});

test('resync snapshot with only the first final preserves later local story parts', () => {
  const first = segment('msg-client', '开场');
  const second = segment('local-channel-msg-client-1', '风起');
  const third = segment('local-channel-msg-client-2', '灯灭');
  assert.deepEqual(
    pendingAfterSnapshot([first, second, third], [segment(first.id, first.content, 'final')])
      .map((message) => message.id),
    [second.id, third.id],
  );
  assert.deepEqual(
    pendingAfterSnapshot([first, second, third], [
      segment(first.id, first.content, 'final'), segment('msg-server-second', '风起', 'final'),
    ]).map((message) => message.id),
    [third.id],
  );
});
