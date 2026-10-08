import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

test('keyboard CSS height caps retain scrolling without changing uncapped draft growth', () => {
  const names = ['window', 'getComputedStyle', 'ResizeObserver'];
  const previous = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  let effect;
  try {
    const viewport = new EventTarget();
    viewport.height = 460;
    const fakeWindow = new EventTarget();
    fakeWindow.visualViewport = viewport;
    fakeWindow.innerHeight = 460;
    Object.defineProperty(globalThis, 'window', { configurable: true, value: fakeWindow });
    Object.defineProperty(globalThis, 'getComputedStyle', { configurable: true, value: () => ({ minHeight: '64px', borderTopWidth: '1px', borderBottomWidth: '1px' }) });
    Object.defineProperty(globalThis, 'ResizeObserver', { configurable: true, value: class { observe() {} disconnect() {} } });
    const compiled = ts.transpileModule(readFileSync(new URL('../src/design-system/useAutoSizeTextarea.ts', import.meta.url), 'utf8'),
      { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
    const exports = {};
    new Function('require', 'exports', compiled)(() => ({ useLayoutEffect: callback => { effect = callback; } }), exports);
    for (const cap of [84, Infinity]) {
      const input = { style: {}, scrollHeight: 160, scrollTop: 42,
        getBoundingClientRect() { return { width: 304, height: Math.min(cap, parseFloat(this.style.height)) }; } };
      exports.useAutoSizeTextarea({ current: input }, '合成长草稿', 'branch.player');
      const cleanup = effect();
      assert.equal(input.style.height, '162px');
      assert.equal(input.style.overflowY, cap === 84 ? 'auto' : 'hidden');
      assert.equal(input.scrollTop, 42, 'Resizing preserves the reader position within a draft');
      cleanup();
    }
  } finally {
    for (const name of names) {
      if (previous.get(name)) Object.defineProperty(globalThis, name, previous.get(name));
      else delete globalThis[name];
    }
  }
});
