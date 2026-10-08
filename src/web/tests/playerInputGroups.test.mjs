import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/playerInputGroups.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const exports = {};
new Function('exports', compiled)(exports);
const { buildPlayerInputGroups } = exports;

const message = (id, actor, turn, input_group_id = null, kind = 'roleplay') => ({
  id, actor, turn, input_group_id, kind,
});

test('newly sent player parts group by stable input id across all channel kinds', () => {
  const parts = [
    message('a', 'player', 3, 'input-a', 'roleplay'),
    message('b', 'player', 3, 'input-a', 'inner'),
    message('c', 'player', 3, 'input-a', 'scene'),
  ];
  const groups = buildPlayerInputGroups(parts);
  assert.deepEqual(groups.get('b').map((item) => item.id), ['a', 'b', 'c']);
});

test('legacy saves group only adjacent player messages from the same turn', () => {
  const messages = [
    message('a', 'player', 4),
    message('b', 'player', 4),
    message('npc', 'char-a', 4),
    message('c', 'player', 4),
    message('d', 'player', 5),
  ];
  const groups = buildPlayerInputGroups(messages);
  assert.deepEqual(groups.get('a').map((item) => item.id), ['a', 'b']);
  assert.deepEqual(groups.get('c').map((item) => item.id), ['c']);
  assert.deepEqual(groups.get('d').map((item) => item.id), ['d']);
});
