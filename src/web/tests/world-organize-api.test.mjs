import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

function harness() {
  const calls = [];
  const imports = {
    '../../api/request': {
      jsonRequest: async (url, options) => {
        calls.push({ url, options });
        return { id: 'server-persisted-task', status: 'queued' };
      },
    },
  };
  const exports = {};
  const source = readFileSync(new URL('../src/features/worlds/worldOrganizeApi.ts', import.meta.url), 'utf8');
  const js = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  new Function('require', 'exports', js)(name => imports[name], exports);
  return { api: exports.worldOrganizeApi, calls };
}

test('generation sends full source, optional instruction and only checked references', async () => {
  const { api, calls } = harness();
  const input = {
    category: 'archives', source_text: 'First exact paragraph.\n\n' + 'Complete facts.\n'.repeat(4000) + 'Last exact fact.  ',
    instruction: 'Retain mechanisms and limitations.', reference_source_ids: ['checked-private-ref'],
    source_visibility: 'private', target_archive_id: null, target_lorebook_id: null, new_lorebook_name: '',
  };
  const before = structuredClone(input);
  const created = await api.create('world-a', input);
  assert.equal(created.id, 'server-persisted-task');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/world-organize-jobs');
  assert.equal(calls[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(calls[0].options.body), { ...input, world_id: 'world-a' });
  assert.equal('signal' in calls[0].options, false);
  assert.deepEqual(input, before);
});

test('world and task reads encode IDs and retain caller cancellation for view freshness', async () => {
  const { api, calls } = harness();
  const controller = new AbortController();
  const readOptions = { signal: controller.signal };
  await api.list('world/特殊?', readOptions);
  await api.get('job/with space?', readOptions);
  assert.equal(calls[0].url, '/api/v1/world-organize-jobs?world_id=world%2F%E7%89%B9%E6%AE%8A%3F');
  assert.equal(calls[1].url, '/api/v1/world-organize-jobs/job%2Fwith%20space%3F');
  assert.equal(calls[0].options.signal, controller.signal);
  assert.equal(calls[1].options.signal, controller.signal);
});

test('saving an editor uses reviewed draft revision and exact reviewed fields', async () => {
  const { api, calls } = harness();
  const payload = { keys: ['Silver moth'], content: 'Complete reviewed biology conditions.', extensions: { custom: 'kept' } };
  await api.edit('job/a', 'draft/b', 17, payload, 'replace', 8);
  assert.equal(calls[0].url, '/api/v1/world-organize-jobs/job%2Fa/drafts/draft%2Fb');
  assert.equal(calls[0].options.method, 'PATCH');
  assert.deepEqual(JSON.parse(calls[0].options.body), { expected_revision: 17, payload, action: 'replace', target_uid: 8 });
});

test('resume asks the server to use its persisted snapshot', async () => {
  const { api, calls } = harness();
  await api.resume('failed-task');
  assert.equal(calls[0].url, '/api/v1/world-organize-jobs/failed-task/resume');
  assert.equal(calls[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(calls[0].options.body), {});
});

test('batch adoption forwards one operation ID and explicit replacement approvals', async () => {
  const { api, calls } = harness();
  const body = {
    operation_id: 'adopt-once', expected_revision: 22, draft_ids: ['add-a', 'replace-b'],
    expected_lorebook_revision: 5, approved_replace_draft_ids: ['replace-b'], accept_source_changes: false,
  };
  await api.commit('task-a', body);
  await api.commit('task-a', body);
  assert.equal(calls[0].url, '/api/v1/world-organize-jobs/task-a/commit-batch');
  assert.deepEqual(JSON.parse(calls[0].options.body), body);
  assert.deepEqual(calls[0], calls[1]);
});

test('frozen original sources and target snapshots support cancelable scoped reads', async () => {
  const { api, calls } = harness();
  const controller = new AbortController();
  await api.source('job/with space?', { signal: controller.signal });
  assert.equal(calls[0].url, '/api/v1/world-organize-jobs/job%2Fwith%20space%3F/source');
  assert.equal(calls[0].options.signal, controller.signal);
});
