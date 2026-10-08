import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import ts from 'typescript';
import { createClientId } from '../src/utils/clientId.js';

test('plain HTTP: UUIDs work with only getRandomValues and remain unique', () => {
  const httpCrypto = { getRandomValues: (bytes) => webcrypto.getRandomValues(bytes) };
  const ids = Array.from({ length: 1000 }, () => createClientId(httpCrypto));
  assert.equal(new Set(ids).size, 1000);
  for (const id of ids) assert.match(id, /^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/);
});

test('native UUID path and unavailable browser have explicit outcomes', () => {
  assert.equal(createClientId({ randomUUID: () => 'native-id' }), 'native-id');
  assert.throws(() => createClientId({}), /浏览器无法生成消息编号/);
});

function inputHarness() {
  const effects = [];
  const input = new EventTarget();
  const exports = {};
  const source = readFileSync(new URL('../src/components/useComposerInput.ts', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  new Function('require', 'exports', compiled)((name) => {
    assert.equal(name, 'react');
    return { useRef: (current) => ({ current }), useEffect: (effect) => effects.push(effect) };
  }, exports);
  let sent = 0;
  const handlers = exports.useComposerInput({ current: input }, () => { sent++; });
  const cleanup = effects[0]();
  return { handlers, input, cleanup, sent: () => sent };
}

function beforeInput(inputType, isComposing = false, cancelable = true) {
  const event = new Event('beforeinput', { cancelable });
  Object.defineProperties(event, { inputType: { value: inputType }, isComposing: { value: isComposing } });
  return event;
}

function key(overrides = {}) {
  return { key: 'Enter', shiftKey: false, nativeEvent: { keyCode: 13, isComposing: false },
    preventDefault() { this.prevented = true; }, ...overrides };
}

test('phone send action without keydown uses beforeinput once and cleans up', () => {
  const h = inputHarness();
  const event = beforeInput('insertParagraph');
  h.input.dispatchEvent(event);
  assert.equal(h.sent(), 1);
  assert.equal(event.defaultPrevented, true);
  h.cleanup();
  h.input.dispatchEvent(beforeInput('insertLineBreak'));
  assert.equal(h.sent(), 1);
});

test('Enter sends; Shift+Enter and Chinese IME confirmation never send', () => {
  const h = inputHarness();
  const enter = key();
  h.handlers.onKeyDown(enter);
  assert.equal(enter.prevented, true);
  assert.equal(h.sent(), 1);
  h.handlers.onKeyDown(key({ shiftKey: true }));
  const newline = beforeInput('insertLineBreak');
  h.input.dispatchEvent(newline);
  assert.equal(newline.defaultPrevented, false);
  h.handlers.onKeyUp();
  h.handlers.onCompositionStart();
  h.handlers.onKeyDown(key());
  h.input.dispatchEvent(beforeInput('insertParagraph'));
  h.handlers.onCompositionEnd();
  h.handlers.onKeyDown(key({ nativeEvent: { isComposing: false, keyCode: 229 } }));
  h.input.dispatchEvent(beforeInput('insertLineBreak', true));
  assert.equal(h.sent(), 1);
  h.input.dispatchEvent(beforeInput('insertLineBreak'));
  assert.equal(h.sent(), 2);
  h.cleanup();
});

test('ordinary text and uncancellable input do not trigger sends', () => {
  const h = inputHarness();
  h.input.dispatchEvent(beforeInput('insertText'));
  h.input.dispatchEvent(beforeInput('insertParagraph', false, false));
  assert.equal(h.sent(), 0);
  h.cleanup();
});
