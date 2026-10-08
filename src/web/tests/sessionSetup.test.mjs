import { storyCommandModule } from './storySupport.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const flush = () => new Promise(resolve => setImmediate(resolve));
class ApiError extends Error { constructor(status, message) { super(message); this.status = status; } }
const setup = id => ({ meta: { id, title: 'Saved synthetic title', character_ids: ['npc-a'], lorebook_ids: ['book-private'],
  branch_revision: 12, player_persona: 'Frozen synthetic persona', streaming_enabled: false, hygiene_enabled: false, director_mode: 'rules' },
  characters: [], lorebooks: [{ id: 'book-private', name: 'Embedded synthetic book', source_format: 'embedded', entries: [] }] });
function harness(api, initial = { mode: 'edit', session: { id: 'branch-a', title: 'Summary title', character_ids: ['npc-a'] } }, library = { characters: [], books: [] }) {
  const hooks = [], effects = [], calls = []; let cursor = 0, props = initial, closed = false;
  const jsx = (type, props) => ({ type, props });
  const react = {
    useState(value) { const index = cursor++; if (!(index in hooks)) hooks[index] = typeof value === 'function' ? value() : value;
      return [hooks[index], update => { hooks[index] = typeof update === 'function' ? update(hooks[index]) : update; }]; },
    useEffect(effect, deps) { const index = cursor++, previous = hooks[index];
      if (!previous || deps.some((value, n) => value !== previous.deps[n])) effects.push(() => {
        previous?.cleanup?.(); hooks[index] = { deps, cleanup: effect() };
      }); },
  };
  const store = { loadSessions: async () => calls.push('list'), selectSession: async id => calls.push(['select', id]), refreshSession: async id => calls.push(['refresh', id]) };
  const imports = { react, 'react/jsx-runtime': { jsx, jsxs: jsx }, '../api/client': { api }, '../features/stories/storiesClient': { storiesClient: api }, '../api/request': { ApiError },
    '../api/adapters': { toView: value => value }, '../store/characterStore': { useCharacterStore: selector => selector({ characters: library.characters }) },
    '../store/lorebookStore': { useLorebookStore: selector => selector({ books: library.books }) },
    '../store/chatStore': { useChatStore: Object.assign(selector => selector(store), { getState: () => store }) },
    './common': { Avatar: 'avatar', Switch: 'switch' }, '../utils/playerPersona': {},
    '../design-system/WorkflowDialog': { WorkflowDialog: 'dialog' },
  };
  const exports = {};
  const code = ts.transpileModule(readFileSync(new URL('../src/components/SessionSetupModal.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  new Function('require', 'exports', code)(name => { assert.ok(name in imports, name); return imports[name]; }, exports);
  function render(next) {
    if (next) props = next;
    cursor = 0; const tree = exports.SessionSetupModal({ ...props, onClose: () => { closed = true; } });
    effects.splice(0).forEach(effect => effect());
    const nodes = []; const visit = node => { if (Array.isArray(node)) return node.forEach(visit); if (!node || typeof node !== 'object') return; nodes.push(node); visit(node.props?.children); }; visit(tree); return nodes;
  }
  const text = node => Array.isArray(node) ? node.map(text).join('') : node && typeof node === 'object' ? text(node.props?.children) : String(node ?? '');
  const button = (nodes, label) => nodes.find(node => node.type === 'button' && text(node) === label);
  return { render, button, calls, get closed() { return closed; } };
}
test('failed settings read cannot submit empty book bindings; retry uses the reviewed revision and saved IDs', async () => {
  let reads = 0, patched;
  const h = harness({ getSessionSetup: async id => { if (++reads === 1) throw new Error('Synthetic read failure'); return setup(id); },
    patchSessionSetup: async (_id, patch) => { patched = patch; } });
  let nodes = h.render(); assert.equal(h.button(nodes, '保存设置').props.disabled, true);
  await flush(); nodes = h.render();
  assert.equal(h.button(nodes, '保存设置').props.disabled, true);
  assert.ok(nodes.some(node => node.props?.role === 'alert'));
  h.button(nodes, '重新读取设置').props.onClick(); h.render(); await flush(); nodes = h.render();
  assert.equal(h.button(nodes, '保存设置').props.disabled, false);
  await h.button(nodes, '保存设置').props.onClick();
  assert.deepEqual(patched.lorebook_ids, ['book-private']); assert.equal(patched.expected_branch_revision, 12);
  assert.equal(patched.streaming_enabled, false); assert.equal(h.closed, true);
});
test('a revision conflict retains unsaved fields and requires explicit re-read before another save', async () => {
  const h = harness({ getSessionSetup: async id => setup(id), patchSessionSetup: async () => { throw new ApiError(409, 'Synthetic conflict'); } });
  h.render(); await flush(); let nodes = h.render();
  nodes.find(node => node.type === 'input').props.onChange({ target: { value: 'Unsaved revised title' } });
  nodes = h.render(); await h.button(nodes, '保存设置').props.onClick(); nodes = h.render();
  assert.equal(nodes.find(node => node.type === 'input').props.value, 'Unsaved revised title');
  assert.equal(h.button(nodes, '保存设置').props.disabled, true); assert.equal(h.closed, false);
  assert.ok(h.button(nodes, '重新读取最新设置'));
});
test('changing the edited branch discards a cancelled old settings response', async () => {
  let finish;
  const h = harness({ getSessionSetup: id => id === 'branch-a' ? new Promise(resolve => { finish = resolve; }) : Promise.resolve(setup(id)) });
  h.render(); h.render({ mode: 'edit', session: { id: 'branch-b', title: 'B', character_ids: [] } });
  await flush(); finish({ ...setup('branch-a'), meta: { ...setup('branch-a').meta, title: 'Wrong late A' } }); await flush();
  assert.equal(h.render().find(node => node.type === 'input').props.value, 'Saved synthetic title');
});
test('creation sends book bindings in the first command and never issues a partial follow-up patch', async () => {
  let created;
  const h = harness({ createSession: async body => { created = body; return { id: 'created-a' }; } }, { mode: 'create' }, {
    characters: [{ id: 'npc-a', color: 'sky', card: { name: 'Synthetic actor', description: '' } }],
    books: [{ id: 'book-a', name: 'Synthetic book', entries: [] }],
  });
  let nodes = h.render(); nodes.find(node => node.type === 'input').props.onChange({ target: { value: 'New synthetic story' } });
  nodes.find(node => node.type === 'button' && node.props.className.includes('flex-col')).props.onClick();
  nodes.find(node => node.type === 'button' && node.props.className.includes('flex w-full')).props.onClick();
  nodes = h.render(); assert.equal(h.button(nodes, '开始对话').props.disabled, false);
  await h.button(nodes, '开始对话').props.onClick();
  assert.deepEqual(created.lorebook_ids, ['book-a']); assert.deepEqual(created.character_ids, ['npc-a']);
  assert.equal(h.closed, true); assert.deepEqual(h.calls.at(-1), ['select', 'created-a']);
});


test('settings network failure leaves the draft and retry sends the same operation and frozen payload', async () => {
  const requests = []; let count = 0;
  const command = storyCommandModule.createStoryCommands(async (_url, request) => {
    requests.push(request); if (++count === 1) throw new TypeError('Synthetic response lost after write'); return {};
  }, () => 'setup-intent');
  const h = harness({ getSessionSetup: async id => setup(id), patchSessionSetup: async (id, body) => command(`/sessions/${id}`, 'PATCH', body) });
  h.render(); await flush(); let nodes = h.render();
  nodes.find(node => node.type === 'input' && node.props.value === 'Saved synthetic title').props.onChange({ target: { value: 'Reviewed draft' } });
  nodes = h.render(); await h.button(nodes, '保存设置').props.onClick(); nodes = h.render();
  assert.equal(h.closed, false);
  assert.equal(nodes.find(node => node.type === 'input').props.value, 'Reviewed draft');
  await h.button(nodes, '保存设置').props.onClick();
  assert.equal(h.closed, true); assert.equal(requests.length, 2);
  assert.equal(requests[0].headers['X-Operation-ID'], requests[1].headers['X-Operation-ID']);
  assert.equal(requests[0].body, requests[1].body);
});
