import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/features/library/workbenchPresentation.ts', import.meta.url), 'utf8');
const exports = {};
new Function('exports', ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText)(exports);
const { groupLorebooks, relativeModified, scenarioCreateInput, scenarioPlayLabels } = exports;

test('world groups keep current result order inside each group and put unassigned books last', () => {
  const books = [{ id: 'unassigned' }, { id: 'b2' }, { id: 'a' }, { id: 'b1' }];
  const owners = { a: { world_id: 'wa', world_title: 'A' }, b1: { world_id: 'wb', world_title: 'B' }, b2: { world_id: 'wb', world_title: 'B' } };
  const result = groupLorebooks(books, owners);
  assert.deepEqual(result.map(group => [group.title, group.books.map(book => book.id)]), [['A', ['a']], ['B', ['b2', 'b1']], ['未指定世界', ['unassigned']]]);
  assert.deepEqual(books.map(book => book.id), ['unassigned', 'b2', 'a', 'b1']);
  assert.deepEqual(groupLorebooks(books, owners, true), [{ id: '', title: '', books }]);
});

test('relative modified labels handle missing, future, recent and old timestamps without fabricated dates', () => {
  const now = Date.parse('2026-10-08T12:00:00Z');
  assert.equal(relativeModified(undefined, now), '修改时间未记录');
  assert.equal(relativeModified('not-a-date', now), '修改时间未记录');
  assert.equal(relativeModified('2026-10-09T12:00:00Z', now), '刚刚');
  assert.equal(relativeModified('2026-10-08T11:58:00Z', now), '2 分钟前');
  assert.equal(relativeModified('2026-10-07T12:00:00Z', now), '昨天');
});

test('scenario save serialization preserves sharing, all play choices, chosen world and authored persona', () => {
  const original = { title: ' 合成预设 ', description: ' 简介 ', author: ' 作者 ', license: ' 署名 ', source_url: ' https://example.invalid/ ', tags: [' 一 ', '', '二'], character_ids: ['c2', 'c1'], lorebook_ids: ['b1'], world_id: 'world', player_persona: '  保留玩家人设原格式\n', instructions: ' 剧情 ', opening: { location: ' ', description: ' 雨夜 ', narration: ' 旁白 ', member_keys: [] }, play: { narrative_pov: 'third', narrative_density: 'atmosphere', director_mode: 'confirm' } };
  const result = scenarioCreateInput(original);
  assert.equal(result.title, '合成预设'); assert.equal(result.license, '署名'); assert.equal(result.source_url, 'https://example.invalid/');
  assert.deepEqual(result.character_ids, ['c2', 'c1']); assert.deepEqual(result.lorebook_ids, ['b1']); assert.equal(result.world_id, 'world');
  assert.equal(result.player_persona, original.player_persona); assert.equal(result.opening.location, '开场');
  assert.deepEqual(result.play, original.play); assert.deepEqual(scenarioPlayLabels(result.play), ['视角：第三人称', '叙事：氛围描写', '导演：每次确认']);
  result.character_ids.push('c3'); result.play.director_mode = 'auto';
  assert.equal(original.character_ids.length, 2); assert.equal(original.play.director_mode, 'confirm');
});

test('empty optional world and narrative choices stay default rather than being invented', () => {
  const input = { title: '合成', description: '', author: '', license: '', source_url: '', tags: [], character_ids: ['c1'], lorebook_ids: [], world_id: '', player_persona: '', instructions: '', opening: { location: '', description: '', narration: '', member_keys: [] }, play: { narrative_pov: null, narrative_density: null, director_mode: null } };
  const result = scenarioCreateInput(input);
  assert.equal(result.world_id, null); assert.deepEqual(result.play, input.play); assert.deepEqual(scenarioPlayLabels(result.play), []);
});
