import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const exported = {};
const compiled = ts.transpileModule(readFileSync(new URL('../src/features/stories/storyMessageMotion.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
new Function('exports', compiled)(exported);
const update = exported.storyMessageMotion;
const row = (id, seq) => ({ id, seq });

test('initial snapshot and branch changes never animate historical messages', () => {
  const first = update(null, 'branch-a', 'branch-a', [row('a', 8)]);
  assert.deepEqual(first.arriving, []);
  const switched = update(first.window, 'branch-b', 'branch-b', [row('b', 12)]);
  assert.deepEqual(switched.arriving, []);
  const awaiting = update(first.window, 'branch-a', null, []);
  assert.equal(awaiting.window.ready, false);
  assert.deepEqual(update(awaiting.window, 'branch-a', 'branch-a', [row('a', 8), row('b', 9)]).arriving, []);
});
test('new replies arrive once; streaming content, candidate replacement and history reads do not replay', () => {
  let state = update(null, 'branch-a', 'branch-a', [row('a', 8)]);
  state = update(state.window, 'branch-a', 'branch-a', [row('a', 8), row('new', 9)]);
  assert.deepEqual(state.arriving, ['new']);
  state = update(state.window, 'branch-a', 'branch-a', [row('a', 8), { ...row('new', 9), content: 'stream chunk', status: 'pending' }]);
  assert.deepEqual(state.arriving, []);
  state = update(state.window, 'branch-a', 'branch-a', [row('earlier', 2)]);
  assert.equal(state.window.highSeq, 9);
  assert.deepEqual(state.arriving, []);
  state = update(state.window, 'branch-a', 'branch-a', [row('a', 8), row('new', 9)]);
  assert.deepEqual(state.arriving, []);
  assert.deepEqual(update(state.window, 'branch-a', 'branch-a', [row('newer', 10)]).arriving, ['newer']);
});
