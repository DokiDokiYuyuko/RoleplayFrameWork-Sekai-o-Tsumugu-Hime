import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

function noticeHarness() {
  const exports = {};
  const scheduled = [];
  const jsx = (type, props) => ({ type, props });
  const imports = {
    react: { useEffect: callback => callback() },
    'react/jsx-runtime': { jsx, jsxs: jsx },
    '../design-system/Icon': { AlertCircle: 'alert-icon', X: 'close-icon' },
    './chat-notices.css': {},
  };
  const code = ts.transpileModule(readFileSync(new URL('../src/components/ChatErrorNotice.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  new Function('require', 'exports', code)(name => { assert.ok(name in imports, name); return imports[name]; }, exports);
  const find = (node, predicate) => {
    if (Array.isArray(node)) return node.map(child => find(child, predicate)).find(Boolean);
    if (!node || typeof node !== 'object') return null;
    return predicate(node) ? node : find(node.props?.children, predicate);
  };
  return { ...exports, scheduled, find };
}

test('generation errors survive elapsed time and expose a manual dismissal', () => {
  const h = noticeHarness();
  const oldWindow = globalThis.window;
  globalThis.window = { setTimeout: callback => { h.scheduled.push(callback); return h.scheduled.length; }, clearTimeout() {} };
  try {
    let dismissed = 0;
    const tree = h.ChatErrorNotice({ error: '合成生成失败，正文仍保留', onDismiss: () => dismissed++ });
    assert.equal(tree.props.role, 'alert');
    h.scheduled.forEach(callback => callback());
    assert.equal(dismissed, 0, 'elapsed time must not dismiss the generation failure');
    h.find(tree, node => node.props?.['aria-label'] === '关闭错误提示').props.onClick();
    assert.equal(dismissed, 1);
  } finally {
    if (oldWindow === undefined) delete globalThis.window;
    else globalThis.window = oldWindow;
  }
});

test('long diagnostic content remains available and conflict summaries retain the useful reason', () => {
  const h = noticeHarness();
  const error = '合成诊断。'.repeat(50);
  const tree = h.ChatErrorNotice({ error, onDismiss() {} });
  assert.ok(h.find(tree, node => node.type === 'details'));
  assert.equal(h.find(tree, node => node.type === 'pre').props.children, error);
  assert.equal(h.errorSummary('409: 合成版本已变化'), '合成版本已变化');
});
