import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const compiled = ts.transpileModule(readFileSync(new URL('../src/features/stories/StoryContextPanel.tsx', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
function render(state, characters, worlds = []) {
  const exports = {}, queries = [];
  let tools = 0, closed = 0;
  const jsx = (type, props) => ({ type, props });
  const imports = {
    '../../design-system/Button': { Button: 'button' },
    '../../design-system/Panel': { Panel: 'panel' },
    './SceneImagePicker': { SceneImage: 'scene-image', SceneImagePicker: 'scene-picker' },
    'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' },
    '@tanstack/react-query': { useQuery: (query) => { queries.push(query); return { data: worlds }; } },
    'react-router': { Link: 'link' },
    '../../design-system/Icon': { BookOpen: 'book', Globe2: 'world', Users: 'users', X: 'close' },
    '../../components/common': { Avatar: 'avatar' },
    '../../appearance/PageArt': { PageArt: 'art' },
    '../resources/resourceQueries': { resourceQueries: { worlds: () => ({ queryKey: ['worlds'] }) } },
    '../../store/chatStore': { useChatStore: (selector) => selector(state) },
    '../../store/useActiveCharacters': { useActiveCharacters: () => characters },
  };
  new Function('require', 'exports', compiled)(name => imports[name], exports);
  const tree = exports.StoryContextPanel({ onClose: () => closed++, onOpenTools: () => tools++ });
  const nodes = [], text = [];
  const visit = node => {
    if (Array.isArray(node)) return node.forEach(visit);
    if (typeof node === 'string' || typeof node === 'number') { text.push(String(node)); return; }
    if (!node || typeof node !== 'object') return;
    nodes.push(node); visit(node.props?.children);
  };
  visit(tree);
  return { nodes, text: text.join(' '), queries, tools: () => tools, closed: () => closed };
}
const session = { id: 'branch', source_world_id: 'world', world_core_brief: '合成冻结世界资料', source_world_revision: 3 };
const state = { currentSessionId: 'branch', snapshotLoadedSessionId: 'branch', sessions: [session],
  activeScene: { id: 'scene', title: '合成真实场景', description: '合成场景正文', member_ids: ['present', 'absent'] },
  sessionGroups: [{ id: 'group', label: '合成在场群体', status: 'active', scene_id: 'scene' }, { id: 'left', label: '合成离场群体', status: 'left', scene_id: 'scene' }] };
const character = (id, present) => ({ id, present, color: 'slate', card: { name: `合成人物${id}`, description: '合成人物简介' } });

test('context reads the current snapshot and frozen world brief through existing resource Query', () => {
  const result = render(state, [character('present', true), character('absent', false), character('elsewhere', true)], [{ id: 'world', title: '合成来源世界', core_brief: '素材库后来修改的资料' }]);
  for (const value of ['合成真实场景', '合成场景正文', '合成人物present', '合成在场群体', '合成来源世界', '合成冻结世界资料']) assert.ok(result.text.includes(value));
  for (const value of ['合成人物absent', '合成人物elsewhere', '合成离场群体', '素材库后来修改的资料']) assert.ok(!result.text.includes(value));
  assert.deepEqual(result.queries[0].queryKey, ['worlds']);
  assert.equal(result.queries[0].enabled, true);
  const manage = result.nodes.filter(node => node.type === 'button' && node.props.onClick);
  manage.forEach(button => button.props.onClick());
  assert.equal(result.tools(), 2);
  assert.equal(result.closed(), 1);
  assert.equal(result.nodes.find(node => node.type === 'link').props.to, '/worlds/world');
});

test('context hides a previous branch snapshot during switching and states absent data honestly', () => {
  const switching = render({ ...state, snapshotLoadedSessionId: 'previous' }, [character('present', true)]);
  assert.ok(switching.text.includes('正在读取当前场景'));
  assert.ok(!switching.text.includes('合成真实场景'));
  assert.ok(!switching.text.includes('合成人物present'));
  assert.ok(!switching.text.includes('合成在场群体'));
  const empty = render({ ...state, sessions: [{ id: 'branch' }], activeScene: null, sessionGroups: [] }, []);
  assert.ok(empty.text.includes('暂未设置场景'));
  assert.ok(empty.text.includes('当前没有在场人物'));
  assert.ok(empty.text.includes('当前故事未关联世界'));
  assert.equal(empty.queries[0].enabled, false);
  assert.equal(empty.nodes.some(node => node.type === 'link'), false);
});
