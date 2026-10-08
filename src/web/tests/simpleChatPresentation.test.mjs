import test from 'node:test';
import assert from 'node:assert/strict';
import { chatTitle, chatDateGroup, chatRelativeTime, modelShortName } from '../src/features/simple-chat/simpleChatPresentation.js';

test('chat title cleanup is display-only and handles empty Markdown titles', () => {
  const original = ' ## **测试**\n  对话 `';
  assert.equal(chatTitle(original), '测试 对话');
  assert.equal(original, ' ## **测试**\n  对话 `');
  assert.equal(chatTitle('**  **'), '未命名聊天');
  assert.equal(chatTitle('一段真实聊天'), '一段真实聊天');
});

test('chat date groups respect local calendar day and seven calendar days', () => {
  const now = new Date(2026, 9, 8, 12);
  assert.equal(chatDateGroup(new Date(2026, 9, 8, 0).toISOString(), now), '今天');
  assert.equal(chatDateGroup(new Date(2026, 9, 2, 0).toISOString(), now), '近 7 天');
  assert.equal(chatDateGroup(new Date(2026, 9, 1, 23, 59).toISOString(), now), '更早');
  assert.equal(chatDateGroup('invalid', now), '更早');
});

test('model labels and timestamps use provided data and explicit missing states', () => {
  const now = new Date('2026-10-08T12:00:00Z');
  assert.equal(modelShortName('provider/example-model'), 'example-model');
  assert.equal(modelShortName(''), '未选择模型');
  assert.equal(chatRelativeTime('2026-10-08T11:30:00Z', now), '30 分钟前');
  assert.equal(chatRelativeTime('invalid', now), '时间未知');
});
