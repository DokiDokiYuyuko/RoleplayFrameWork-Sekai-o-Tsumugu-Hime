import test from 'node:test';
import assert from 'node:assert/strict';
import { composerConversationRun } from '../src/utils/conversationDisplay.js';

test('ending a recovered run clears the input notice and does not revive older failures', () => {
  const old = { id: 'old', session_id: 'branch', status: 'failed' };
  for (const status of ['cancelled', 'completed']) {
    const ended = { id: 'latest', session_id: 'branch', status, last_error: 'historical error' };
    const runs = [old, ended];
    assert.equal(composerConversationRun(runs, 'branch'), null);
    assert.deepEqual(runs, [old, ended]);
  }
});

test('unfinished runs stay actionable after reload while other branches stay out of the input', () => {
  for (const status of ['paused', 'awaiting_user', 'failed', 'interrupted']) {
    const local = { id: 'local', session_id: 'branch', status };
    assert.equal(composerConversationRun([local, { session_id: 'other', status: 'running' }], 'branch'), local);
  }
  assert.equal(composerConversationRun([{ session_id: 'other', status: 'failed' }], 'branch'), null);
});

test('late updates for an older run cannot hide an actively generating reply', () => {
  const active = { id: 'active', session_id: 'branch', status: 'running' };
  assert.equal(composerConversationRun([active, { session_id: 'branch', status: 'cancelled' }], 'branch'), active);
});
