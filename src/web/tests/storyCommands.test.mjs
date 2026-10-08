import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { storyCommandModule, storyRequest, receiptModule } from './storySupport.mjs';

test('POST model retry prepares the original identity and preconditions before optimistic rendering', async () => {
  const h = commands((_request, count) => { if (count === 1) throw new TypeError('Lost model response'); return {}; });
  const path = '/api/v1/sessions/A/messages/m/regenerate-one';
  const first = h.command.prepare(path, { operation_id: 'original-model', expected_branch_revision: 5, expected_fingerprint: 'old' });
  await assert.rejects(h.command(path, 'POST', first.payload));
  const retry = h.command.prepare(path, { operation_id: 'new-local-id', expected_branch_revision: 6, expected_fingerprint: 'new' });
  assert.deepEqual(retry, first);
  await h.command(path, 'POST', retry.payload);
  assert.equal(h.requests[0].id, 'original-model'); assert.equal(h.requests[1].body, h.requests[0].body);
});

test('a failed follow-up view read never reruns an already acknowledged model command', async () => {
  const h = commands(() => ({ ok: true })); let reads = 0;
  const complete = async value => { if (++reads === 1) throw new TypeError('View temporarily offline'); return value; };
  await assert.rejects(h.command('/A/continue', 'POST', { action: 'force' }, undefined, complete));
  await h.command('/A/continue', 'POST', { action: 'force' }, undefined, complete);
  assert.equal(h.requests.length, 1); assert.equal(reads, 2);
});

test('fork retries freeze the first compatibility key and original revision', async () => {
  const h = commands((_request, count) => { if (count === 1) throw new TypeError('Lost fork response'); return {}; });
  await assert.rejects(h.command('/branch/A/fork', 'POST', { title: 'New route', idempotency_key: 'fork-a', expected_revision: 2 }));
  await h.command('/branch/A/fork', 'POST', { title: 'New route', idempotency_key: 'fork-b', expected_revision: 3 });
  assert.equal(h.requests[1].id, 'fork-a'); assert.equal(h.requests[1].body, h.requests[0].body);
});

test('corrected regeneration retries retain model ID, options digest and CAS while selected revisions define a new intent', async () => {
  const h = commands(() => { throw new TypeError('Lost corrected model response'); });
  const path = '/api/v1/sessions/A/messages/m/regenerate-corrected';
  const first = { operation_id: 'corrected-original', expected_branch_revision: 2, expected_fingerprint: 'a', options_digest: 'original-options', selected_static_fields: ['character:description'], selected_memory_revisions: { memory: 3 } };
  await assert.rejects(h.command(path, 'POST', first));
  await assert.rejects(h.command(path, 'POST', { ...first, operation_id: 'wrong-new-local', expected_branch_revision: 3, expected_fingerprint: 'b', options_digest: 'new-options' }));
  assert.equal(h.requests[0].id, h.requests[1].id); assert.equal(h.requests[0].body, h.requests[1].body);
  await assert.rejects(h.command(path, 'POST', { ...first, operation_id: 'explicit-revision', selected_memory_revisions: { memory: 4 } }));
  assert.notEqual(h.requests[1].id, h.requests[2].id);
});

test('incomplete operations cannot be silently replaced just because refreshed CAS changed', async () => {
  const h = commands(() => { throw new storyRequest.ApiError(409, 'operation_incomplete', 'Check previous command'); });
  await assert.rejects(h.command('/create', 'POST', { title: 'Draft', expected_revision: 2 }));
  await assert.rejects(h.command('/create', 'POST', { title: 'Draft', expected_revision: 3 }), /前次操作尚未完成/);
  assert.equal(h.requests.length, 1);
  await assert.rejects(h.command('/create', 'POST', { title: 'Explicit new draft', expected_revision: 3 }));
  assert.notEqual(h.requests[0].id, h.requests[1].id);
});

test('only explicit review starts a new empty-payload model attempt and replaces compatibility IDs', async () => {
  const h = commands(() => { throw new storyRequest.ApiError(409, 'operation_incomplete', 'Unfinished'); });
  await assert.rejects(h.command('/A/swipe', 'POST', {}));
  await assert.rejects(h.command('/A/swipe', 'POST', {}));
  assert.equal(h.requests[0].id, h.requests[1].id);
  const pending = h.command.incomplete()[0];
  assert.equal(h.command.reviewNewAttempt(pending.path, pending.method, 'wrong-id'), false);
  assert.equal(h.command.reviewNewAttempt(pending.path, pending.method, pending.id), true);
  assert.equal(h.requests.length, 2, 'review alone must not call model or submit');
  await assert.rejects(h.command('/A/swipe', 'POST', {}));
  assert.notEqual(h.requests[1].id, h.requests[2].id);
  await assert.rejects(h.command('/A/group/reply', 'POST', { idempotency_key: 'legacy-key' }));
  const group = h.command.incomplete().find(value => value.path.endsWith('/reply'));
  h.command.reviewNewAttempt(group.path, group.method, group.id);
  await assert.rejects(h.command('/A/group/reply', 'POST', { idempotency_key: 'legacy-key' }));
  const retry = h.requests.at(-1);
  assert.notEqual(retry.id, 'legacy-key'); assert.equal(JSON.parse(retry.body).idempotency_key, retry.id);
});

test('binary retry keeps the first FormData and never emits JSON or fingerprint in the uploaded body', async () => {
  const h = commands((_request, count) => { if (count === 1) throw new TypeError('Lost import'); return {}; });
  const first = new File(['synthetic archive bytes'], 'first.zip');
  await assert.rejects(h.command('/stories/import', 'POST', { upload_sha256: 'same-digest' }, first));
  await h.command('/stories/import', 'POST', { upload_sha256: 'same-digest' }, new File(['synthetic archive bytes'], 'renamed.zip'));
  assert.equal(h.requests[0].body, h.requests[1].body); assert.equal(h.requests[0].id, h.requests[1].id);
  assert.deepEqual([...h.requests[0].body.keys()], ['file']); assert.equal(h.requests[1].body.get('file').name, 'first.zip');
});

test('group leave retries retain query revision while POST method stays unchanged', async () => {
  const h = commands((_request, count) => { if (count === 1) throw new TypeError('Lost leave'); return {}; });
  await assert.rejects(h.command('/groups/g/leave', 'POST', { expected_branch_revision: 3 }));
  await h.command('/groups/g/leave', 'POST', { expected_branch_revision: 4 });
  assert.equal(h.requests[1].url, '/groups/g/leave?expected_branch_revision=3'); assert.equal(h.requests[1].id, h.requests[0].id);
});

test('multipart transport lets the browser select a boundary and keeps the operation header', async () => {
  const prior = globalThis.fetch; let init;
  globalThis.fetch = async (_url, value) => { init = value; return new Response('{}'); };
  try {
    const form = new FormData(); form.append('file', new File(['fixture'], 'fixture.zip'));
    await storyRequest.jsonRequest('/import', { method: 'POST', headers: { 'X-Operation-ID': 'upload-1' }, body: form });
    assert.equal(new Headers(init.headers).get('content-type'), null);
    assert.equal(new Headers(init.headers).get('X-Operation-ID'), 'upload-1');
  } finally { globalThis.fetch = prior; }
});

function commands(handler) {
  const requests = []; let counter = 0;
  const command = storyCommandModule.createStoryCommands(async (url, init) => {
    requests.push({ url, method: init.method, id: init.headers['X-Operation-ID'], body: init.body });
    return handler?.(requests.at(-1), requests.length) ?? { ok: true };
  }, () => `operation-${++counter}`);
  return { command, requests };
}
test('lost response retry reuses the operation and original payload despite newer local preconditions', async () => {
  const h = commands((_request, count) => { if (count === 1) throw new TypeError('Synthetic response lost after commit'); return { content: 'New text' }; });
  const path = '/api/v1/sessions/branch-a/messages/m-1';
  await assert.rejects(h.command(path, 'PATCH', { content: 'New text', expected_branch_revision: 9, expected_fingerprint: 'first' }));
  await h.command(path, 'PATCH', { content: 'New text', expected_branch_revision: 10, expected_fingerprint: 'newer' });
  assert.equal(h.requests[0].id, h.requests[1].id);
  assert.equal(h.requests[0].body, h.requests[1].body);
  assert.equal(JSON.parse(h.requests[1].body).expected_branch_revision, 9);
});
test('changing a failed draft starts a new operation; branch and command paths stay isolated', async () => {
  const h = commands(() => { throw new TypeError('Synthetic offline'); });
  const submit = (path, content) => assert.rejects(h.command(path, 'PATCH', { content, expected_branch_revision: 9 }));
  await submit('/a/message', 'First'); await submit('/a/message', 'Changed'); await submit('/b/message', 'Changed');
  assert.equal(new Set(h.requests.map(request => request.id)).size, 3);
});
test('DELETE retry freezes its query CAS, and a reviewed 409 starts a new operation', async () => {
  const h = commands((_request, count) => { if (count === 1) throw new TypeError('Lost'); if (count === 2) throw new storyRequest.ApiError(409, 'conflict', 'Changed'); });
  await assert.rejects(h.command('/a/message', 'DELETE', { expected_branch_revision: 3 }));
  await assert.rejects(h.command('/a/message', 'DELETE', { expected_branch_revision: 4 }));
  await h.command('/a/message', 'DELETE', { expected_branch_revision: 4 });
  assert.equal(h.requests[0].id, h.requests[1].id);
  assert.equal(h.requests[1].url, '/a/message?expected_branch_revision=3');
  assert.notEqual(h.requests[1].id, h.requests[2].id);
  assert.equal(h.requests[2].url, '/a/message?expected_branch_revision=4');
});
test('concurrent clicks share one in-flight request and successful new actions get new IDs', async () => {
  let finish; const h = commands(() => new Promise(resolve => { finish = resolve; }));
  const first = h.command('/a/settings', 'PATCH', { title: 'A' });
  const duplicate = h.command('/a/settings', 'PATCH', { title: 'A' });
  assert.equal(h.requests.length, 1); finish({ ok: true }); await Promise.all([first, duplicate]);
  const next = h.command('/a/settings', 'PATCH', { title: 'A' }); finish({ ok: true }); await next;
  assert.notEqual(h.requests[0].id, h.requests[1].id);
});
test('variant retries preserve the body operation ID matching the header', async () => {
  const h = commands((_request, count) => { if (count === 1) throw new storyRequest.ApiError(503, undefined, 'Unavailable'); });
  await assert.rejects(h.command('/a/message', 'PATCH', { active_variant: 1, operation_id: 'variant-intent-a', expected_branch_revision: 7 }));
  await h.command('/a/message', 'PATCH', { active_variant: 1, operation_id: 'variant-intent-b', expected_branch_revision: 8 });
  assert.equal(h.requests[1].id, 'variant-intent-a');
  assert.equal(JSON.parse(h.requests[1].body).operation_id, 'variant-intent-a');
});
test('JSON transport merges the operation header with content type', async () => {
  const prior = globalThis.fetch; let init;
  globalThis.fetch = async (_url, input) => { init = input; return new Response(JSON.stringify({ ok: true }), { status: 200 }); };
  try { await storyRequest.jsonRequest('/api/v1/sessions/a', { method: 'PATCH', headers: { 'X-Operation-ID': 'test-id' }, body: '{}' }); }
  finally { globalThis.fetch = prior; }
  assert.equal(init.headers['content-type'], 'application/json');
  assert.equal(init.headers['x-operation-id'], 'test-id');
});


test('receipt headers retain the commit revision without changing public JSON payloads', async () => {
  const command = storyCommandModule.createStoryCommands(async (_url, _init, response) => {
    response(new Response('{}', { headers: { 'X-Command-ID': 'intent-a', 'X-Branch-Revision': '10' } }));
    return { content: 'At revision 10' };
  }, () => 'intent-a');
  const result = await command('/a/message', 'PATCH', { content: 'At revision 10' });
  assert.deepEqual(receiptModule.commandReceipt(result), { command_id: 'intent-a', branch_revision: 10 });
  assert.equal(JSON.stringify(result), '{"content":"At revision 10"}');
});


test('mismatched operation IDs and invalid receipt revisions never become trusted watermarks', async () => {
  for (const [id, revision] of [['another-intent', '99'], ['intent-a', '-1'], ['intent-a', ''], ['intent-a', 'NaN']]) {
    const command = storyCommandModule.createStoryCommands(async (_url, _init, response) => {
      response(new Response('{}', { headers: { 'X-Command-ID': id, 'X-Branch-Revision': revision } })); return { content: 'Unsafe' };
    }, () => 'intent-a');
    await assert.rejects(command('/a/message', 'PATCH', { content: 'Unsafe' }), /命令回执/);
  }
});


test('all client methods using the covered PATCH routes enter the durable command boundary (AST)', () => {
  const ast = ts.createSourceFile('client.ts', readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true);
  const covered = new Set(['patchSessionSetup', 'patchSessionOptions', 'patchNarrative', 'editMessage', 'switchVariant', 'editInputGroup', 'deleteMessage']);
  const checked = [];
  function visit(node) {
    if (ts.isPropertyAssignment(node) && covered.has(node.name.getText(ast))) {
      let durable = false;
      const scan = child => { if (ts.isCallExpression(child) && ts.isIdentifier(child.expression) && child.expression.text === 'storyCommand') durable = true; ts.forEachChild(child, scan); };
      scan(node.initializer); assert.ok(durable, node.name.getText(ast)); checked.push(node.name.getText(ast));
    }
    ts.forEachChild(node, visit);
  }
  visit(ast); assert.equal(checked.length, covered.size);
});


test('input-group fingerprint refresh preserves the first intent and payload without stripping user dictionaries elsewhere', async () => {
  const h = commands((_request, count) => { if (count === 1) throw new TypeError('Lost committed reply'); });
  const body = fingerprint => ({ expected_branch_revision: 9, parts: [{ message_id: 'm-1', kind: 'roleplay', content: 'Same text', expected_fingerprint: fingerprint }] });
  await assert.rejects(h.command('/a/messages/m-1/input-group', 'PATCH', body('original')));
  await h.command('/a/messages/m-1/input-group', 'PATCH', body('newer'));
  assert.equal(h.requests[0].id, h.requests[1].id); assert.equal(h.requests[0].body, h.requests[1].body);
  assert.equal(JSON.parse(h.requests[1].body).parts[0].expected_fingerprint, 'original');
  const other = commands(() => { throw new TypeError('Offline'); });
  await assert.rejects(other.command('/other', 'PATCH', body('user-value-a')));
  await assert.rejects(other.command('/other', 'PATCH', body('user-value-b')));
  assert.notEqual(other.requests[0].id, other.requests[1].id);
});


test('a failed 409 retry with identical reviewed payload retains its operation until preconditions are reviewed again', async () => {
  const h = commands(() => { throw new storyRequest.ApiError(409, 'branch_busy', 'Busy'); });
  await assert.rejects(h.command('/a/message', 'PATCH', { content: 'Draft', expected_branch_revision: 3 }));
  await assert.rejects(h.command('/a/message', 'PATCH', { content: 'Draft', expected_branch_revision: 3 }));
  assert.equal(h.requests[0].id, h.requests[1].id);
  await assert.rejects(h.command('/a/message', 'PATCH', { content: 'Draft', expected_branch_revision: 4 }));
  assert.notEqual(h.requests[1].id, h.requests[2].id);
});

test('lifecycle and memory query preconditions are frozen with the original ID and excluded from JSON', async () => {
  const h = commands((_request, count) => { if (count === 1) throw new TypeError('Lost lifecycle response'); return {}; });
  await assert.rejects(h.command('/api/v1/stories/A/prompt-preset', 'PATCH', { preset_id: 'preset', __command_query: { membership_revision: 'original' } }));
  await h.command('/api/v1/stories/A/prompt-preset', 'PATCH', { preset_id: 'preset', __command_query: { membership_revision: 'new-summary' } });
  assert.equal(h.requests[0].id, h.requests[1].id); assert.equal(h.requests[0].url, h.requests[1].url);
  assert.equal(h.requests[1].body, JSON.stringify({ preset_id: 'preset' }));
  assert.equal(h.requests[1].url, '/api/v1/stories/A/prompt-preset?membership_revision=original');
});
test('trash generation CAS freezes through lost response and changes only after an explicit conflict review', async () => {
  let conflict = false;
  const h = commands(() => { if (conflict) throw new storyRequest.ApiError(409, 'trash_conflict', 'Review changed'); throw new TypeError('Lost restore response'); });
  const path = '/api/v1/stories/trash/A/restore';
  await assert.rejects(h.command(path, 'POST', { __command_query: { generation_id: 'first' } }));
  await assert.rejects(h.command(path, 'POST', { __command_query: { generation_id: 'later' } }));
  assert.equal(h.requests[0].url, h.requests[1].url); assert.equal(h.requests[0].id, h.requests[1].id);
  conflict = true; await assert.rejects(h.command(path, 'POST', { __command_query: { generation_id: 'first' } }));
  await assert.rejects(h.command(path, 'POST', { __command_query: { generation_id: 'reviewed-new' } }));
  assert.notEqual(h.requests[2].id, h.requests[3].id);
});
