import test from 'node:test';
import assert from 'node:assert/strict';
import { load } from './storySupport.mjs';

const ownerPolicy = load('../src/features/creation/lorebookJobOwner.ts');
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function harness() {
  const oldWindow = globalThis.window, oldStorage = globalThis.localStorage;
  const intervals = new Map(); let timerId = 0;
  globalThis.window = { setInterval: callback => { intervals.set(++timerId, callback); return timerId; }, clearInterval: id => intervals.delete(id) };
  const storage = new Map([['mrp.lorebookAgentJob', 'A']]);
  globalThis.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) };
  const world = { id: 'synthetic-world', title: '合成世界', archived: false, core_brief: '', archive_records: [], lorebook_ids: [] };
  const job = (id, status = 'running') => ({ id, world_id: world.id, world_title: world.title, status, stage: `合成阶段${id}`, created_at: '2026-01-01', updated_at: '2026-01-01', errors: [], progress: { attempt: 1, tool_calls: 0 }, drafts: [] });
  const jobs = new Map([['A', job('A')], ['B', job('B', 'review')], ['C', job('C', 'review')]]);
  const gates = new Map(), sourceGates = new Map();
  const calls = { reads: [], cancelled: [], sources: [], summaryWrites: [] };
  let summaries = [...jobs.values()].map(row => ({ id: row.id, world_id: world.id, world_title: world.title, status: row.status, updated_at: row.updated_at }));
  const queries = { worlds: () => ({ kind: 'worlds' }), world: id => ({ kind: 'world', id }), lorebooks: () => ({ kind: 'books' }), lorebookJobs: () => ({ kind: 'jobs', queryKey: ['jobs'] }) };
  const queryResult = query => query.kind === 'worlds' ? [world] : query.kind === 'world' ? world : query.kind === 'jobs' ? summaries : [];
  const slots = [], effects = new Map(); let cursor = 0, queued = [], tree;
  const react = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial; return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }]; },
    useRef(initial) { const i = cursor++; if (!(i in slots)) slots[i] = { current: initial }; return slots[i]; },
    useMemo: factory => factory(),
    useEffect(callback, deps) { const i = cursor++; const previous = effects.get(i); if (!previous || deps?.some((value, index) => value !== previous.deps?.[index])) queued.push(() => { previous?.cleanup?.(); effects.set(i, { deps, cleanup: callback() }); }); },
  };
  const api = {
    getLorebookAgentJob: async id => { calls.reads.push(id); const gate = gates.get(id); if (gate) { gates.delete(id); return gate.promise; } return jobs.get(id); },
    getLorebookAgentSources: async id => { calls.sources.push(id); const gate = sourceGates.get(id); if (gate) { sourceGates.delete(id); return gate.promise; } return { sources: [{ id: `source-${id}`, title: `来源${id}`, content: id, char_count: 1 }] }; },
    cancelLorebookAgentJob: async id => { calls.cancelled.push(id); const next = job(id, 'cancelled'); jobs.set(id, next); return next; },
    resumeLorebookAgentJob: async id => { const next = job(id, 'running'); jobs.set(id, next); return next; },
  };
  const controls = Object.fromEntries(['Button', 'Checkbox', 'EmptyState', 'Field', 'IconButton', 'Select', 'Tag', 'Textarea', 'TextInput', 'WritingPanel'].map(name => [name, name]));
  const { default: Page } = load('../src/pages/LorebookAgentPage.tsx', {
    react, '@tanstack/react-query': { useQuery: query => ({ data: queryResult(query) }) },
    '../features/resources/resourceQueries': { resourceQueries: queries }, '../queryClient': { queryClient: { fetchQuery: async query => queryResult(query), setQueryData: (_key, update) => { calls.summaryWrites.push(true); summaries = typeof update === 'function' ? update(summaries) : update; } } },
    '../features/creation/creationClient': { creationClient: api }, '../features/creation/lorebookJobOwner': ownerPolicy,
    '../utils/workshopPolicy': { lorebookAgentStatusLabel: status => ({ running: '整理中', review: '待审阅', cancelled: '已取消' })[status] ?? '状态待更新' },
    '../design-system': controls, '../design-system/Workbench': { WorkbenchColumn: 'column', WorkbenchDesk: 'desk' },
    '../design-system/Icon': { Check: 'icon', ChevronRight: 'icon', LibraryBig: 'icon', RefreshCw: 'icon', Save: 'icon', Search: 'icon', X: 'icon' },
    './WorkshopUi': { WorkshopDisclosure: 'disclosure', WorkshopNotice: 'notice', WorkshopPaneSwitch: 'pane', WorkshopWait: 'wait', useElapsedSeconds: () => 0 },
  });
  const visit = (node, predicate, out = []) => { if (!node || typeof node !== 'object') return out; if (Array.isArray(node)) { node.forEach(value => visit(value, predicate, out)); return out; } if (predicate(node)) out.push(node); visit(node.props?.children, predicate, out); return out; };
  const render = () => { cursor = 0; queued = []; tree = Page(); const current = queued; queued = []; current.forEach(effect => effect()); };
  const find = predicate => { const found = visit(tree, predicate); assert.ok(found.length, 'expected rendered control'); return found[0]; };
  render();
  return { job, jobs, calls, gates, sourceGates, intervals, render, find,
    status() { return find(node => node.type === 'Tag' && node.props.dot).props.children; },
    history() { return find(node => node.type === 'Select' && node.props.placeholder === '选择历史任务…'); },
    poll() { assert.ok(intervals.size, 'active task should have a poll timer'); [...intervals.values()][0](); },
    dispose() { effects.forEach(effect => effect.cleanup?.()); globalThis.window = oldWindow; globalThis.localStorage = oldStorage; },
  };
}

test('an in-flight old poll cannot overwrite a completed cancel and timers stop immediately', async () => {
  const h = harness();
  try {
    await tick(); h.render(); const oldPoll = deferred(); h.gates.set('A', oldPoll); h.poll();
    await h.find(node => node.type === 'notice' && node.props.busy).props.actions.props.onClick(); await tick(); h.render();
    assert.equal(h.status(), '已取消'); assert.equal(h.intervals.size, 0);
    oldPoll.resolve(h.job('A', 'running')); await tick(); h.render();
    assert.equal(h.status(), '已取消'); assert.equal(h.intervals.size, 0); assert.deepEqual(h.calls.cancelled, ['A']);
  } finally { h.dispose(); }
});

test('history A to B to A rejects a delayed response from the earlier A epoch', async () => {
  const h = harness();
  try {
    await tick(); h.render(); const oldPoll = deferred(); h.gates.set('A', oldPoll); h.poll();
    h.history().props.onValueChange('B'); await tick(); h.render(); assert.equal(h.history().props.value, 'B');
    h.jobs.set('A', h.job('A', 'cancelled')); h.history().props.onValueChange('A'); await tick(); h.render();
    assert.equal(h.status(), '已取消');
    oldPoll.resolve(h.job('A', 'running')); await tick(); h.render();
    assert.equal(h.history().props.value, 'A'); assert.equal(h.status(), '已取消'); assert.equal(h.intervals.size, 0);
  } finally { h.dispose(); }
});

test('an old selection source response cannot clear busy state or take over a newer selection', async () => {
  const h = harness();
  try {
    await tick(); h.render(); const oldSource = deferred(); h.sourceGates.set('B', oldSource);
    const oldHistoryHandler = h.history().props.onValueChange;
    oldHistoryHandler('B'); await tick(); h.render();
    const newSource = deferred(); h.sourceGates.set('C', newSource); oldHistoryHandler('C'); await tick(); h.render();
    assert.equal(h.history().props.value, 'C'); assert.equal(h.history().props.disabled, true);
    oldSource.resolve({ sources: [{ id: 'old', title: '不应采用的旧来源', content: 'old', char_count: 3 }] }); await tick(); h.render();
    assert.equal(h.history().props.value, 'C'); assert.equal(h.history().props.disabled, true);
    newSource.resolve({ sources: [] }); await tick(); h.render();
    assert.equal(h.history().props.value, 'C'); assert.equal(h.history().props.disabled, false);
  } finally { h.dispose(); }
});

test('unmount invalidates an in-flight poll before it can install a new task or restart timers', async () => {
  const h = harness(); await tick(); h.render(); const oldPoll = deferred(); h.gates.set('A', oldPoll); h.poll();
  const installedBeforeUnmount = h.calls.summaryWrites.length;
  h.dispose(); oldPoll.resolve(h.job('A', 'running')); await tick();
  assert.equal(h.intervals.size, 0);
  assert.equal(h.calls.summaryWrites.length, installedBeforeUnmount);
});

test('a failed history switch preserves the previous visible task and resumes its polling in a new epoch', async () => {
  const h = harness();
  try {
    await tick(); h.render(); const failure = deferred(); h.gates.set('B', failure);
    h.history().props.onValueChange('B'); assert.equal(h.intervals.size, 0);
    failure.reject(new Error('合成读取失败')); await tick(); h.render();
    assert.equal(h.history().props.value, 'A'); assert.equal(h.history().props.disabled, false);
    assert.equal(h.status(), '整理中'); assert.equal(h.intervals.size, 1);
    h.poll(); await tick(); h.render(); assert.equal(h.history().props.value, 'A');
  } finally { h.dispose(); }
});
