import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const jsx = (type, props, key) => ({ type, props, key });
const exports = {};
const code = ts.transpileModule(readFileSync(new URL('../src/components/GenerationControls.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
new Function('require', 'exports', code)(name => name === 'react/jsx-runtime' ? { jsx, jsxs: jsx } : {}, exports);

test('input presentation adapter retains field labels, help, values and numeric updates', () => {
  const value = { temperature: 0, top_p: 0.8, frequency_penalty: -1, presence_penalty: null, max_output_tokens: 4000 };
  const changes = [];
  const Adapter = () => null;
  const tree = exports.GenerationControls({ value, idPrefix: 'chat', onChange: next => changes.push(next), inputComponent: Adapter });
  const labels = tree.props.children;
  assert.equal(labels.length, 5);
  for (const label of labels) {
    const [title, input, hint] = label.props.children;
    assert.equal(input.type, Adapter);
    assert.equal(label.props.htmlFor, input.props.id);
    assert.ok(title.props.children && hint.props.children);
  }
  const temperature = labels[0].props.children[1];
  assert.equal(temperature.props.value, 0);
  temperature.props.onChange({ target: { value: '0.75' } });
  assert.deepEqual(changes[0], { ...value, temperature: 0.75 });
  temperature.props.onChange({ target: { value: '' } });
  assert.equal(changes[1].temperature, null);
  temperature.props.onChange({ target: { value: 'invalid' } });
  assert.equal(changes.length, 2);
});

test('existing consumers keep native inputs and disabled behavior', () => {
  const value = { temperature: null, top_p: null, frequency_penalty: null, presence_penalty: null, max_output_tokens: null };
  const tree = exports.GenerationControls({ value, idPrefix: 'legacy', onChange: () => {}, disabled: true });
  for (const label of tree.props.children) {
    const input = label.props.children[1];
    assert.equal(input.type, 'input');
    assert.equal(input.props.disabled, true);
    assert.equal(input.props.value, '');
  }
});
