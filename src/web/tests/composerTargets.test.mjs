import test from 'node:test';
import assert from 'node:assert/strict';
import { canReconcileComposerTargets, filterValidComposerTargets } from '../src/utils/composerTargets.js';

test('returning to a branch keeps saved group targets until its snapshot finishes loading', () => {
  const saved = ['merchants', 'guide', 'guard'];
  const branch = 'story-branch-a';
  // During selectSession, old/empty roster arrays must not trigger persistence.
  assert.equal(canReconcileComposerTargets(branch, branch, null), false);
  assert.deepEqual(saved, ['merchants', 'guide', 'guard']);

  // Once the complete branch snapshot is installed, its active group survives filtering.
  assert.equal(canReconcileComposerTargets(branch, branch, branch), true);
  assert.deepEqual(filterValidComposerTargets(saved, [
    { id: 'guide', present: true, muted: false },
    { id: 'guard', present: true, muted: false },
  ], [{ id: 'merchants' }]), saved);
});

test('loaded branch filtering still removes genuinely unavailable targets', () => {
  assert.deepEqual(filterValidComposerTargets(['absent', 'muted', 'merchants'], [
    { id: 'absent', present: false, muted: false },
    { id: 'muted', present: true, muted: true },
  ], [{ id: 'merchants' }]), ['merchants']);
});
