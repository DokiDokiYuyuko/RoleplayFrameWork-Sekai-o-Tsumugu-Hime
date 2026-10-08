import test from 'node:test';
import assert from 'node:assert/strict';
import {
  initialOrganizeInput, organizeCandidates, toggleOrganizeReference,
  buildOrganizeCommit, createOrganizeScope, readLegacyCandidates, prepareOrganizeCommit, hasPrivateOrganizeSources, organizeCompletedBatches,
} from '../src/features/worlds/worldOrganizeState.js';

const candidate = (id, changes = {}) => ({
  id, revision: 1, status: 'review', kind: 'lorebook', action: 'add',
  payload: { keys: [`Canal marker ${id}`], content: `Full synthetic fact ${id}.` },
  ...changes,
});
const job = (drafts, changes = {}) => ({
  id: 'organizer-a', revision: 9, status: 'review', world_id: 'world-a',
  category: 'lorebook', target_lorebook_id: 'book-a', target_revision: 4,
  drafts, ...changes,
});

test('a new task has one category, blank material and no implicitly selected references', () => {
  const plain = initialOrganizeInput();
  assert.equal(plain.category, 'archives');
  assert.equal(plain.source_visibility, 'public');
  assert.equal(plain.source_text, '');
  assert.equal(plain.instruction, '');
  assert.deepEqual(plain.reference_source_ids, []);
  const targeted = initialOrganizeInput('?category=lorebook&archive=archive-a&book=book-a&references=all&include_core=true');
  assert.equal(targeted.category, 'lorebook');
  assert.equal(targeted.target_archive_id, null);
  assert.equal(targeted.target_lorebook_id, 'book-a');
  assert.deepEqual(targeted.reference_source_ids, []);
  assert.equal(initialOrganizeInput('?category=unknown').category, 'archives');
});

test('references are explicit independent checkboxes and toggling does not mutate old input', () => {
  const empty = [];
  const selected = toggleOrganizeReference(empty, 'private-ref');
  const both = toggleOrganizeReference(selected, 'public-ref');
  const removed = toggleOrganizeReference(both, 'private-ref');
  assert.deepEqual(empty, []);
  assert.deepEqual(selected, ['private-ref']);
  assert.deepEqual(both, ['private-ref', 'public-ref']);
  assert.deepEqual(removed, ['public-ref']);
});

test('result categories retain the full tail without changing candidate payloads', () => {
  const drafts = Array.from({ length: 83 }, (_, i) => candidate(`draft-${i}`, {
    kind: i % 3 === 0 ? 'background' : i % 3 === 1 ? 'biology' : 'lorebook',
  }));
  const snapshot = structuredClone(drafts);
  for (const kind of ['background', 'biology', 'lorebook']) {
    const filtered = organizeCandidates(drafts, kind);
    assert.deepEqual(filtered.map(row => row.id), drafts.filter(row => row.kind === kind).map(row => row.id));
    assert.equal(filtered.at(-1), drafts.findLast(row => row.kind === kind));
  }
  assert.deepEqual(drafts, snapshot);
});

test('adopting selected additions preserves revision and only sends selected draft IDs', () => {
  const state = job([candidate('first'), candidate('unselected'), candidate('last')]);
  const before = structuredClone(state);
  const body = buildOrganizeCommit(state, ['last', 'first'], [], 'one-operation');
  assert.deepEqual(body, {
    operation_id: 'one-operation', expected_revision: 9,
    draft_ids: ['first', 'last'], approved_replace_draft_ids: [],
    expected_lorebook_revision: 4, accept_source_changes: false,
  });
  assert.deepEqual(state, before);
});

test('every selected lorebook replacement needs its own explicit confirmation', () => {
  const state = job([
    candidate('add'), candidate('replace-one', { action: 'replace', target_uid: 2 }),
    candidate('replace-two', { action: 'replace', target_uid: 3 }),
  ]);
  assert.throws(() => buildOrganizeCommit(state, ['replace-one', 'replace-two'], [], 'adopt'), /逐条确认/);
  assert.throws(() => buildOrganizeCommit(state, ['replace-one', 'replace-two'], ['replace-one'], 'adopt'), /逐条确认/);
  const body = buildOrganizeCommit(state, ['replace-one', 'replace-two'], ['replace-one', 'replace-two', 'add'], 'adopt');
  assert.deepEqual(body.approved_replace_draft_ids, ['replace-one', 'replace-two']);
  const additionOnly = buildOrganizeCommit(state, ['add'], [], 'adopt-add');
  assert.deepEqual(additionOnly.draft_ids, ['add']);
  assert.deepEqual(additionOnly.approved_replace_draft_ids, []);
});

test('one archive replacement is approved by selecting the candidate for adoption', () => {
  const state = job([candidate('archive-edit', { kind: 'background', action: 'replace', target_id: 'archive-a' })], {
    category: 'archives', target_lorebook_id: null, target_revision: null,
  });
  const body = buildOrganizeCommit(state, ['archive-edit'], [], 'archive-adopt');
  assert.deepEqual(body.draft_ids, ['archive-edit']);
  assert.deepEqual(body.approved_replace_draft_ids, []);
  assert.equal('expected_lorebook_revision' in body, false);
});

test('empty, missing or already committed selections cannot be submitted', () => {
  const state = job([candidate('ready'), candidate('saved', { status: 'committed' })]);
  for (const selection of [[], ['unknown'], ['saved'], ['ready', 'unknown'], ['ready', 'saved']]) {
    assert.throws(() => buildOrganizeCommit(state, selection, [], 'adopt'), /尚未保存/);
  }
  assert.equal(buildOrganizeCommit(state, ['ready'], [], 'adopt', { acceptSourceChanges: true }).accept_source_changes, true);
  assert.equal(buildOrganizeCommit(state, ['ready'], [], 'adopt', { acceptSourceChanges: 'true' }).accept_source_changes, false);
});

test('disposed input and result views ignore delayed responses while server work completes', async () => {
  let finish;
  const serverTask = new Promise(resolve => { finish = resolve; });
  const oldScope = createOrganizeScope('world-a');
  const newScope = createOrganizeScope('world-b');
  const visible = { worldId: 'world-b', result: null };
  let completed = false;
  const request = serverTask.then(result => {
    completed = true;
    if (oldScope.isCurrent(visible.worldId)) visible.result = result;
  });
  assert.equal(oldScope.isCurrent('world-a'), true);
  assert.equal(oldScope.isCurrent('world-b'), false);
  oldScope.dispose();
  finish({ status: 'review', drafts: [candidate('finished-on-server')] });
  await request;
  assert.equal(completed, true);
  assert.equal(visible.result, null);
  assert.equal(oldScope.isCurrent('world-a'), false);
  assert.equal(newScope.isCurrent('world-b'), true);
});

test('legacy tasks expose complete original candidates without adding mutation actions', () => {
  const legacy = {
    source: 'Exact original source.  \nLong tail retained.',
    drafts: [{ id: 'old-world', kind: 'world', payload: { title: 'Old world', core_brief: 'Core candidate.' } }],
    bundle: {
      core_proposal: { id: 'old-core', content: 'Old core candidate.', status: 'review' },
      entry_proposals: [candidate('old-entry', { action: 'replace', target_uid: 8 })],
      manuscript_proposal: { id: 'old-manuscript', title: 'Old material', body: 'Full old manuscript tail.' },
    },
  };
  const before = structuredClone(legacy);
  const rows = readLegacyCandidates(legacy);
  assert.equal(rows.length, 4);
  assert.deepEqual(rows.map(row => row.kind), ['world', 'core', 'lorebook', 'background']);
  assert.equal(rows.find(row => row.id === 'old-core').payload.content, 'Old core candidate.');
  assert.equal(rows.find(row => row.id === 'old-manuscript').payload.body, 'Full old manuscript tail.');
  assert.ok(rows.every(row => !('action' in row) && !('target_uid' in row)));
  assert.deepEqual(legacy, before);
});

function editingApi(initial, mutate = () => {}) {
  let current = structuredClone(initial);
  const calls = [];
  return {
    calls,
    api: {
      get: async () => { mutate(current, calls); return structuredClone(current); },
      edit: async (jobId, id, revision, payload, action, target_uid) => {
        calls.push({ jobId, id, revision, payload: structuredClone(payload), action, target_uid });
        const row = current.drafts.find(draft => draft.id === id);
        assert.equal(row.revision, revision);
        Object.assign(row, { payload: structuredClone(payload), action, target_uid, revision: revision + 1 });
        current.revision += 1;
        return structuredClone(row);
      },
    },
  };
}

test('saving selected edits checks reviewed revisions and leaves other drafts untouched', async () => {
  const initial = job([candidate('first'), candidate('untouched'), candidate('replacement', { action: 'replace', target_uid: 2 })]);
  const edits = {
    first: { revision: 1, payload: { content: 'Player reviewed full tail.' }, action: 'add' },
    untouched: { revision: 1, payload: { content: 'Keep this only as a local draft.' }, action: 'add' },
    replacement: { revision: 1, payload: { content: 'Reviewed replacement facts.' }, action: 'replace', target_uid: 2 },
  };
  const { api, calls } = editingApi(initial);
  const saved = [];
  const prepared = await prepareOrganizeCommit(initial, ['first', 'replacement'], ['replacement'], 'operation-a', edits, api, { onSaved: row => saved.push(row.id) });
  assert.deepEqual(calls.map(call => call.id), ['first', 'replacement']);
  assert.deepEqual(saved, ['first', 'replacement']);
  assert.equal(prepared.body.expected_revision, 11);
  assert.deepEqual(prepared.body.draft_ids, ['first', 'replacement']);
  assert.deepEqual(prepared.body.approved_replace_draft_ids, ['replacement']);
  assert.equal(prepared.job.drafts.find(row => row.id === 'untouched').payload.content, 'Full synthetic fact untouched.');
  assert.equal(initial.revision, 9);
  assert.equal(edits.untouched.payload.content, 'Keep this only as a local draft.');
});

test('an unseen server edit before adoption is rejected without writing player edits', async () => {
  const initial = job([candidate('first')]);
  const edits = { first: { revision: 1, payload: { content: 'Local player edit remains.' }, action: 'add' } };
  const { api, calls } = editingApi(initial, current => { current.revision = 10; current.drafts[0].revision = 2; });
  await assert.rejects(prepareOrganizeCommit(initial, ['first'], [], 'operation', edits, api), /已改变/);
  assert.equal(calls.length, 0);
  assert.equal(edits.first.payload.content, 'Local player edit remains.');
});

test('a concurrent change while selected edits save cannot be silently adopted', async () => {
  const initial = job([candidate('first'), candidate('second')]);
  const edits = { first: { revision: 1, payload: { content: 'Reviewed first.' }, action: 'add' } };
  const { api } = editingApi(initial, (current, calls) => { if (calls.length) { current.revision += 1; current.drafts[1].revision += 1; } });
  await assert.rejects(prepareOrganizeCommit(initial, ['first', 'second'], [], 'operation', edits, api), /已改变/);
});

test('stale local editor versions and changed target versions require review', async () => {
  const initial = job([candidate('first')]);
  const { api, calls } = editingApi(initial);
  await assert.rejects(prepareOrganizeCommit(initial, ['first'], [], 'operation', { first: { revision: 0, payload: {}, action: 'add' } }, api), /已改变/);
  assert.equal(calls.length, 0);
  const changedTarget = editingApi(initial, current => { current.target_revision = 5; });
  await assert.rejects(prepareOrganizeCommit(initial, ['first'], [], 'operation', {}, changedTarget.api), /已改变/);
});

test('private source detection uses the frozen snapshot rather than unrelated world data', () => {
  assert.equal(hasPrivateOrganizeSources({ sources: [{ id: 'chosen', audience: 'author' }] }), true);
  assert.equal(hasPrivateOrganizeSources({ sources: [{ id: 'chosen', visibility: 'private' }] }), true);
  assert.equal(hasPrivateOrganizeSources({ sources: [{ id: 'chosen', audience: 'shared' }] }), false);
  assert.equal(hasPrivateOrganizeSources(undefined), false);
});

test('batch progress counts completed batch IDs and supports old numeric responses', () => {
  assert.equal(organizeCompletedBatches({ completed_batches: [0], batch_total: 1 }), 1);
  assert.equal(organizeCompletedBatches({ completed_batches: [0, 1, 2] }), 3);
  assert.equal(organizeCompletedBatches({ completed_batches: [] }), 0);
  assert.equal(organizeCompletedBatches({ completed_batches: 2 }), 2);
  assert.equal(organizeCompletedBatches({ progress: { completed_batches: 4 } }), 4);
});
