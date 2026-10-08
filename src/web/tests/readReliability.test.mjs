import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

function load(path, imports = {}) {
  const exports = {};
  const compiled = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  new Function('require', 'exports', compiled)((name) => { assert.ok(name in imports, name); return imports[name]; }, exports);
  return exports;
}
const diagnostics = load('../src/utils/loadDiagnostics.ts');
class LanSessionExpiredError extends Error {}
const { jsonRequest, readTimeout, RequestTimeoutError } = load('../src/api/request.ts', {
  './lanSession': { LanSessionExpiredError }, '../utils/loadDiagnostics': diagnostics,
});

test('finite reads are distinct from generation and writes', () => {
  assert.equal(readTimeout('/api/v1/simple-chats/chat-test'), 15000);
  assert.equal(readTimeout('/api/v1/settings/models?profile=example'), 25000);
  assert.equal(readTimeout('/api/v1/settings/providers?model=example'), 25000);
  assert.equal(readTimeout('/api/v1/sessions/x/messages', 'POST'), null);
  assert.equal(readTimeout('/api/v1/simple-chats/x/stop', 'POST'), null);
});

test('timeout covers JSON body, not just the arrival of headers', async () => {
  const nativeFetch = globalThis.fetch, nativeSet = globalThis.setTimeout, nativeClear = globalThis.clearTimeout;
  let expire;
  globalThis.setTimeout = (callback) => { expire = callback; return 1; };
  globalThis.clearTimeout = () => {};
  globalThis.fetch = async (_url, options) => ({ ok: true, json: () => new Promise((_, reject) => {
    options.signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')));
  }) });
  try {
    const request = jsonRequest('/api/v1/simple-chats/chat-test');
    await Promise.resolve();
    expire();
    await assert.rejects(request, RequestTimeoutError);
  } finally { globalThis.fetch = nativeFetch; globalThis.setTimeout = nativeSet; globalThis.clearTimeout = nativeClear; }
});

test('route cancellation is propagated and never retried', async () => {
  const nativeFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = (_url, options) => new Promise((_, reject) => {
    calls++; options.signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')));
  });
  try {
    const controller = new AbortController();
    const request = jsonRequest('/api/v1/simple-chats/chat-test', { signal: controller.signal });
    controller.abort();
    await assert.rejects(request, (error) => error.name === 'AbortError');
    assert.equal(calls, 1);
  } finally { globalThis.fetch = nativeFetch; }
});

test('pairing failures retain their dedicated exception', async () => {
  const nativeFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, status: 401, json: async () => ({ code: 'lan_session_expired' }) });
  try { await assert.rejects(jsonRequest('/api/v1/settings'), LanSessionExpiredError); }
  finally { globalThis.fetch = nativeFetch; }
});

test('diagnostics discard IDs and query secrets and remain bounded', () => {
  for (let n = 0; n < 100; n++) diagnostics.beginLoad('request', '/api/v1/simple-chats/chat-private-value?key=private-secret')();
  const records = diagnostics.getLoadDiagnostics();
  assert.equal(records.length, 80);
  assert.equal(records.at(-1).label, '/api/v1/simple-chats/:id');
  assert.equal(JSON.stringify(records).includes('private'), false);
});
