import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import * as state from '../src/features/worlds/worldOrganizeState.js';

const flush = () => new Promise(resolve => setTimeout(resolve, 0));
function fixture() {
  return {
    world: { id: 'world-a', title: 'Synthetic canal world', lorebook_ids: [], core_brief: '', archive_records: [] },
    job: { id: 'job-a', world_id: 'world-a', revision: 9, status: 'review', category: 'archives', source_visibility: 'public', source_text: 'Synthetic full source.', instruction: '', reference_source_ids: [], errors: [], target_revision: null, target_lorebook_id: null, source_changed: false,
      drafts: [{ id: 'draft-a', revision: 1, status: 'review', kind: 'background', action: 'add', source_refs: [], payload: { title: 'Canal gates', body: 'Reviewed synthetic canal fact.', visibility: 'public' } }] },
    snapshot: { sources: [], target_archive: null, target_lorebook: null },
  };
}
function harness(data, storage = new Map(), commitFn, search = '?job=job-a') {
  const hooks = [], effects = [], calls = [];
  let cursor = 0;
  const jsx = (type, props, key) => ({ type, props, key });
  const storageApi = { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) };
  const react = {
    useState: initial => {
      const slot = cursor++;
      if (!(slot in hooks)) hooks[slot] = typeof initial === 'function' ? initial() : initial;
      return [hooks[slot], value => { hooks[slot] = typeof value === 'function' ? value(hooks[slot]) : value; }];
    },
    useRef: initial => { const slot = cursor++; if (!(slot in hooks)) hooks[slot] = { current: initial }; return hooks[slot]; },
    useEffect: (effect, deps) => {
      const slot = cursor++, previous = hooks[slot];
      if (!previous || deps.some((dep, i) => dep !== previous.deps[i])) effects.push(() => { previous?.cleanup?.(); hooks[slot] = { deps, cleanup: effect() }; });
    },
  };
  const cache = {
    setQueryData: (key, value) => { if (key[0] === 'world-organize-job') data.job = typeof value === 'function' ? value(data.job) : value; },
    invalidateQueries: async () => {},
  };
  const organizeApi = {
    get: async () => structuredClone(data.job),
    edit: async (jobId, draftId, revision, payload, action, target_uid) => {
      assert.equal(data.job.drafts[0].revision, revision);
      data.job = structuredClone(data.job);
      data.job.revision += 1;
      Object.assign(data.job.drafts[0], { revision: revision + 1, payload, action, target_uid });
      return structuredClone(data.job.drafts[0]);
    },
    commit: async (jobId, body) => { calls.push({ jobId, body: structuredClone(body) }); return commitFn ? commitFn(data, body, calls.length) : structuredClone(data.job); },
  };
  const imports = {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' },
    '@tanstack/react-query': {
      useQueryClient: () => cache,
      useQuery: ({ queryKey }) => {
        const value = queryKey[0] === 'world' ? data.world : queryKey[0] === 'world-organize-job' ? data.job : queryKey[0] === 'world-organize-sources' ? data.snapshot : queryKey[0] === 'world-organize-settings' ? { model: 'synthetic-model', thinking: 'off' } : [];
        return { data: value, isLoading: false, error: null, refetch: async () => ({ data: value }) };
      },
    },
    'react-router': { Link: 'link', useLocation: () => ({ search }), useParams: () => ({ worldId: 'world-a' }) },
    'lucide-react': Object.fromEntries(['ArrowLeft', 'BookOpen', 'Check', 'FileText', 'LoaderCircle', 'Save', 'Sparkles'].map(name => [name, 'icon'])),
    'radix-ui': { Dialog: Object.fromEntries(['Root', 'Portal', 'Overlay', 'Content', 'Title', 'Description'].map(name => [name, `dialog-${name}`])) },
    '../../api/client': { api: {} }, '../resources/resourceQueries': { resourceQueries: { world: id => ({ queryKey: ['world', id] }), lorebooks: () => ({ queryKey: ['lorebooks'] }), settings: () => ({ queryKey: ['settings'] }) } },
    '../../api/request': { jsonRequest: async () => [] },
    '../../components/LoadState': { LoadState: 'load-state' },
    '../../components/LorebookEntryFields': { emptyLorebookEntry: () => ({ extensions: {}, keys: [], enabled: true }), LorebookEntryFields: 'lorebook-fields' },
    '../stories/StoryHome': { MoonOrnament: 'moon' }, './ArchiveRecordEditor': { ArchiveRecordEditor: 'archive-editor' },
    './worldOrganizeApi': { worldOrganizeApi: organizeApi }, './worldOrganizeState': state,
    '../../utils/clientId': { createClientId: () => 'stable-operation' }, '../../store/lorebookStore': { useLorebookStore: { getState: () => ({ load: async () => {} }) } },
    '../../utils/timestamps': { compareNewestFirst: () => 0 },
  };
  const exports = {};
  const source = readFileSync(new URL('../src/features/worlds/WorldOrganizePage.tsx', import.meta.url), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  new Function('require', 'exports', 'localStorage', js)(name => imports[name] || {}, exports, storageApi);
  function render() {
    cursor = 0;
    const outer = exports.default();
    const tree = outer.type(outer.props);
    effects.splice(0).forEach(effect => effect());
    const nodes = [];
    const visit = node => { if (Array.isArray(node)) return node.forEach(visit); if (!node || typeof node !== 'object') return; nodes.push(node); visit(node.props?.children); };
    visit(tree);
    return nodes;
  }
  const textOf = value => Array.isArray(value) ? value.map(textOf).join('') : value && typeof value === 'object' ? textOf(value.props?.children) : String(value ?? '');
  const button = (nodes, text) => {
    const found = nodes.find(node => node.type === 'button' && textOf(node.props.children) === text);
    assert.ok(found, `Missing button ${text}`);
    return found;
  };
  return { render, button, calls, storage };
}

function openSave(h) {
  let nodes = h.render();
  h.button(nodes, 'Canal gatesReviewed synthetic canal fact.新增').props.onClick();
  nodes = h.render();
  nodes.find(node => node.type === 'archive-editor').props.onSave();
  nodes = h.render();
  h.button(nodes, '确认保存').props.onClick();
}

test('a lost adoption response can retry the identical persisted operation after leaving the page', async () => {
  const data = fixture();
  const storage = new Map();
  const commitFn = async (value, body, count) => {
    if (count === 1) {
      value.job = structuredClone(value.job);
      value.job.revision += 1;
      value.job.status = 'committed';
      value.job.drafts[0].status = 'committed';
      throw new TypeError('Connection lost after server adopted the draft.');
    }
    return structuredClone(value.job);
  };
  const first = harness(data, storage, commitFn);
  openSave(first);
  await flush();
  assert.equal(first.calls.length, 1);
  const savedBody = first.calls[0].body;
  assert.equal(JSON.parse(storage.get('mrp.world.organize.operations.world-a'))['job-a'].operation_id, savedBody.operation_id);
  // A fresh component reopens an already-committed task, without rebuilding a new selection.
  const reopened = harness(data, storage, async () => structuredClone(data.job));
  let nodes = reopened.render();
  reopened.button(nodes, '重新确认上次保存').props.onClick();
  nodes = reopened.render();
  const confirm = nodes.find(node => node.type === 'button' && node.props.className === 'v7-btn v7-btn-primary');
  assert.equal(confirm.props.disabled, false);
  confirm.props.onClick();
  await flush();
  assert.deepEqual(reopened.calls[0].body, savedBody);
  assert.deepEqual(JSON.parse(storage.get('mrp.world.organize.operations.world-a')), {});
});

test('a modal preserves the reviewed snapshot and never adopts an unseen server update', async () => {
  const data = fixture();
  const h = harness(data);
  let nodes = h.render();
  h.button(nodes, 'Canal gatesReviewed synthetic canal fact.新增').props.onClick();
  nodes = h.render();
  nodes.find(node => node.type === 'archive-editor').props.onSave();
  data.job = structuredClone(data.job);
  data.job.revision += 1;
  data.job.drafts[0].revision += 1;
  data.job.drafts[0].payload.title = 'Unreviewed server update';
  nodes = h.render();
  h.button(nodes, '确认保存').props.onClick();
  await flush();
  assert.equal(h.calls.length, 0);
  assert.ok(h.render().some(node => node.props?.role === 'alert'));
});

test('returning through an archive source link retains the player edited original input', () => {
  const data = fixture();
  data.world.archive_records = [{ id: 'archive-a', title: 'Synthetic source', kind: 'background', body: 'Original archive body.', visibility: 'public' }];
  const storage = new Map();
  const search = '?category=lorebook&source_archive=archive-a';
  const first = harness(data, storage, undefined, search);
  first.render();
  let nodes = first.render();
  const original = nodes.find(node => node.type === 'textarea' && node.props.className === 'organize-original-input');
  assert.equal(original.props.value, 'Original archive body.');
  original.props.onChange({ target: { value: 'Player revised source and full new tail.' } });
  first.render();
  const reopened = harness(data, storage, undefined, search);
  reopened.render();
  nodes = reopened.render();
  assert.equal(nodes.find(node => node.type === 'textarea' && node.props.className === 'organize-original-input').props.value, 'Player revised source and full new tail.');
});
