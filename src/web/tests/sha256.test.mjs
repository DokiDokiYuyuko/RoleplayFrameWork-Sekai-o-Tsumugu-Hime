import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { sha256Module, storyCommandModule } from './storySupport.mjs';
const expected = bytes => createHash('sha256').update(bytes).digest('hex');

test('LAN HTTP SHA-256 matches independent crypto for known vectors, Unicode and padded block boundaries', () => {
  for (const text of ['', 'abc', '合成导入内容', 'a'.repeat(1000000)]) {
    const bytes = new TextEncoder().encode(text);
    assert.equal(sha256Module.sha256(bytes), expected(bytes));
  }
  for (const size of [1, 55, 56, 63, 64, 65, 127, 128, 129, 4096]) {
    const bytes = Uint8Array.from({ length: size }, (_, index) => (index * 101 + 53) % 256);
    assert.equal(sha256Module.sha256(bytes), expected(bytes));
  }
});

test('without Web Crypto, renamed identical bytes preserve import identity; different bytes begin a new intent', async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: {} });
  try {
    const calls = []; let next = 0;
    const command = storyCommandModule.createStoryCommands(async (_path, init) => { calls.push(init); throw new TypeError('Lost response'); }, () => `upload-${++next}`);
    const submit = async (name, content) => {
      const file = new File([content], name);
      const fingerprint = await sha256Module.fileSha256(file);
      assert.equal(fingerprint, expected(Buffer.from(content)));
      await assert.rejects(command('/import', 'POST', { upload_sha256: fingerprint }, file));
    };
    await submit('first.zip', 'fixture'); await submit('rename.zip', 'fixture'); await submit('rename.zip', 'changed fixture');
    assert.equal(calls[0].headers['X-Operation-ID'], calls[1].headers['X-Operation-ID']);
    assert.equal(calls[0].body, calls[1].body);
    assert.notEqual(calls[1].headers['X-Operation-ID'], calls[2].headers['X-Operation-ID']);
  } finally { if (descriptor) Object.defineProperty(globalThis, 'crypto', descriptor); else delete globalThis.crypto; }
});
