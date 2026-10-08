import test from 'node:test';
import assert from 'node:assert/strict';
import { load } from './storySupport.mjs';

function harness() {
  const slots = []; let cursor = 0, tree;
  const calls = { generated: [], created: [], patched: [], uploaded: [] };
  const behavior = { generationFails: false, patchFails: false, creationGate: null };
  const react = {
    useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial; return [slots[index], next => { slots[index] = typeof next === 'function' ? next(slots[index]) : next; }]; },
    useRef(initial) { const index = cursor++; if (!(index in slots)) slots[index] = { current: initial }; return slots[index]; },
    useEffect() {},
  };
  const store = {
    updateCharacter: async (id, patch) => { calls.patched.push({ id, patch }); if (behavior.patchFails) { behavior.patchFails = false; throw new Error('合成PATCH失败'); } },
    upsert() {}, uploadMedia: async (id, kind, file) => calls.uploaded.push({ id, kind, file }),
  };
  const useCharacterStore = selector => selector(store); useCharacterStore.getState = () => store;
  const books = [{ id: 'synthetic-book', name: '合成世界书', entries: [] }];
  const controls = Object.fromEntries(['Avatar', 'Button', 'Card', 'EmptyState', 'Field', 'Segmented', 'Tag', 'Textarea', 'WritingPanel', 'Checkbox'].map(name => [name, name]));
  const { default: Page } = load('../src/pages/WorkshopCharacterPage.tsx', {
    react, 'react-router': { useSearchParams: () => [new URLSearchParams('source_world=synthetic-world')] },
    '../features/creation/creationClient': { creationClient: { generateCharacters: async form => { calls.generated.push(form); if (behavior.generationFails) { behavior.generationFails = false; throw new Error('合成生成失败'); } return []; } } },
    '../components/CharacterEditor': { default: 'editor' },
    '../design-system': controls, '../design-system/Workbench': { WorkbenchColumn: 'column', WorkbenchDesk: 'desk' },
    '../design-system/Icon': { Users: 'icon' }, './WorkshopUi': { WorkshopNotice: 'notice', WorkshopPaneSwitch: 'pane', WorkshopWait: 'wait' },
    '../store/lorebookStore': { useLorebookStore: selector => selector({ books, load: async () => {} }) },
    '../store/characterStore': { useCharacterStore },
    '../api/characterCreation': { characterCreation: { create: async (card, world) => { calls.created.push({ card, world }); if (behavior.creationGate) await behavior.creationGate; return { id: 'synthetic-created', card }; } } },
  });
  const visit = (node, predicate, out = []) => { if (!node || typeof node !== 'object') return out; if (Array.isArray(node)) { node.forEach(value => visit(value, predicate, out)); return out; } if (predicate(node)) out.push(node); visit(node.props?.children, predicate, out); return out; };
  const render = () => { cursor = 0; tree = Page(); };
  const find = predicate => { const found = visit(tree, predicate); assert.ok(found.length, 'expected rendered control'); return found[0]; };
  render();
  return { render, find, all: predicate => visit(tree, predicate), calls, behavior,
    writeBlank() { find(node => node.type === 'Button' && node.props.children === '写下人物设定').props.onClick(); render(); return find(node => node.type === 'editor'); },
    editor() { return find(node => node.type === 'editor'); },
  };
}

test('generation failure preserves the same form and selected references for explicit retry', async () => {
  const h = harness(); h.behavior.generationFails = true;
  h.find(node => node.type === 'Textarea').props.onChange({ target: { value: '合成药师需求' } });
  h.find(node => node.type === 'WritingPanel').props.onChange({ target: { value: '保留合成补充设定' } });
  h.find(node => node.type === 'Checkbox').props.onChange();
  h.find(node => node.type === 'Segmented').props.onValueChange('5'); h.render();
  await h.find(node => node.type === 'Button' && node.props.children === '生成候选角色').props.onClick(); h.render();
  assert.equal(h.find(node => node.type === 'Textarea').props.value, '合成药师需求');
  assert.equal(h.find(node => node.type === 'WritingPanel').props.value, '保留合成补充设定');
  await h.find(node => node.type === 'notice' && node.props.tone === 'danger').props.actions.props.onClick(); h.render();
  assert.equal(h.calls.generated.length, 2);
  assert.deepEqual(h.calls.generated[0], h.calls.generated[1]);
  assert.deepEqual(h.calls.generated[1].reference_lorebook_ids, ['synthetic-book']);
  assert.equal(h.calls.generated[1].count, 5);
});

test('partial create success followed by failed patch retries the same durable character identity', async () => {
  const h = harness(); const editor = h.writeBlank(); h.behavior.patchFails = true;
  const payload = { card: { ...editor.props.initialCard, name: '合成角色' }, aliases: ['旧别名'], source_world_id: 'synthetic-world' };
  await assert.rejects(editor.props.onSave(payload), /PATCH失败/); h.render();
  assert.equal(h.all(node => node.type === 'editor').length, 1);
  await h.editor().props.onSave(payload); h.render();
  assert.equal(h.calls.created.length, 1);
  assert.deepEqual(h.calls.patched.map(call => call.id), ['synthetic-created', 'synthetic-created']);
  h.editor().props.onSaveSucceeded(false, false); h.render();
  assert.equal(h.all(node => node.type === 'editor').length, 0);
});

test('successful save with later edits keeps identity and next save clears aliases and runtime inheritance', async () => {
  const h = harness(); const editor = h.writeBlank();
  const payload = { card: { ...editor.props.initialCard, name: '合成角色' }, aliases: ['合成别名'], runtime: { model: 'synthetic-model', base_url: 'https://example.invalid', max_tokens: 2048 } };
  await editor.props.onSave(payload); editor.props.onSaveSucceeded(false, true); h.render();
  assert.equal(h.all(node => node.type === 'editor').length, 1);
  await h.editor().props.onSave({ ...payload, card: { ...payload.card, description: '后续编辑' }, aliases: [], runtime: { model: '', base_url: '', max_tokens: 0 } }); h.render();
  assert.equal(h.calls.created.length, 1);
  assert.deepEqual(h.calls.patched[1].patch.aliases, []);
  assert.equal(h.calls.patched[1].patch.card.description, '后续编辑');
  assert.equal(h.calls.patched[1].patch.model, '');
  assert.equal(h.calls.patched[1].patch.base_url, '');
  assert.deepEqual(h.calls.patched[1].patch.sampling, { max_tokens: 0 });
});

test('a newer chosen image during a pending save is retained for the next save', async () => {
  const h = harness(); const editor = h.writeBlank(); let release;
  h.behavior.creationGate = new Promise(resolve => { release = resolve; });
  const first = { name: 'first-synthetic-avatar' }, later = { name: 'later-synthetic-avatar' };
  editor.props.onAvatarPicked(first);
  const payload = { card: { ...editor.props.initialCard, name: '合成角色' }, aliases: [] };
  const saving = editor.props.onSave(payload);
  editor.props.onAvatarPicked(later); release(); await saving;
  editor.props.onSaveSucceeded(false, true); h.render();
  await h.editor().props.onSave(payload);
  assert.deepEqual(h.calls.uploaded.map(call => call.file), [first, later]);
  assert.equal(h.calls.created.length, 1);
});
