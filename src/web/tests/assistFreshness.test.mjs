import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/assistFreshness.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const exports = {};
new Function('exports', compiled)(exports);
const { latestPlayerVisibleFinal, isAssistBatchCurrent } = exports;

const message = (id, seq, options = {}) => ({
  id, seq, actor: 'char-a', status: 'final', visible_to: 'all', fingerprint: `fp-${id}`, ...options,
});

test('candidate anchor uses highest-sequence visible final, ignoring private and pending tails', () => {
  const visible = message('visible', 4);
  assert.equal(latestPlayerVisibleFinal([
    visible,
    message('private', 9, { visible_to: ['char-a'] }),
    message('pending', 10, { status: 'pending' }),
    message('retracted', 11, { status: 'retracted' }),
  ]).id, 'visible');
});

test('candidate batch becomes stale after branch, anchor, or fingerprint changes', () => {
  const latest = message('latest', 7);
  const batch = { branch_id: 'branch-a', anchor_message_id: 'latest', anchor_fingerprint: 'fp-latest' };
  assert.equal(isAssistBatchCurrent({ ...batch, branch_revision: 4 }, 'branch-a', [latest], 4), true);
  assert.equal(isAssistBatchCurrent({ ...batch, branch_revision: 4 }, 'branch-a', [latest], 5), false);
  assert.equal(isAssistBatchCurrent(batch, 'branch-b', [latest]), false);
  assert.equal(isAssistBatchCurrent(batch, 'branch-a', [message('next', 8)]), false);
  assert.equal(isAssistBatchCurrent(batch, 'branch-a', [message('latest', 7, { fingerprint: 'edited' })]), false);
});
