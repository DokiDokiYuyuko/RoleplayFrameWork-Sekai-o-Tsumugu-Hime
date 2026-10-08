import test from 'node:test';
import assert from 'node:assert/strict';
import { nextWorkshopPreviewUid, formatWorkshopElapsed, lorebookAgentStatusLabel } from '../src/utils/workshopPolicy.js';

test('adding preview entries cannot collide with ids assigned by generation or after deletion', () => {
  const generated = [{ uid: -1 }, { uid: -2 }, { uid: -3 }];
  const next = nextWorkshopPreviewUid(generated);
  assert.equal(next, -4);
  assert.ok(!generated.some(entry => entry.uid === next));
  assert.equal(nextWorkshopPreviewUid([{ uid: -1 }, { uid: -4 }]), -5);
  assert.equal(nextWorkshopPreviewUid([{ uid: 6 }, { uid: 9 }]), -1);
  assert.equal(nextWorkshopPreviewUid([]), -1);
});

test('waiting time crosses the minute boundary and remains readable for restored long tasks', () => {
  assert.equal(formatWorkshopElapsed(59), '0:59');
  assert.equal(formatWorkshopElapsed(60), '1:00');
  assert.equal(formatWorkshopElapsed(3601), '60:01');
  assert.equal(formatWorkshopElapsed(-4), '0:00');
  assert.equal(formatWorkshopElapsed(NaN), '0:00');
});

test('persisted Agent statuses and forward-compatible unknown statuses never leak enum values', () => {
  assert.equal(lorebookAgentStatusLabel('regenerating'), '重新整理中');
  assert.equal(lorebookAgentStatusLabel('committed'), '已保存');
  assert.equal(lorebookAgentStatusLabel('new_backend_phase'), '状态待更新');
});
