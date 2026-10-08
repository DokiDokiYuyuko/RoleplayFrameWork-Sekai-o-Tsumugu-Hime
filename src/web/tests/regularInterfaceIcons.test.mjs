import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseAst } from 'rollup/parseAst';
import { regularInterfaceIcons } from '../build/regularInterfaceIcons.mjs';

test('every interface glyph keeps the exact vendor regular SVG after unused weight pruning', () => {
  const adapter = readFileSync(new URL('../src/design-system/Icon.tsx', import.meta.url), 'utf8');
  assert.match(adapter, /weight="regular"/);
  assert.match(adapter, /Omit<PhosphorProps, "weight">/);
  const names = [...adapter.matchAll(/@phosphor-icons\/react\/dist\/csr\/([^"']+)/g)].map(match => match[1]);
  assert.ok(names.length > 40);
  const plugin = regularInterfaceIcons();
  const react = { Fragment: 'fragment', createElement: (...args) => args };
  const evaluate = code => {
    const alias = code.match(/import \* as (\w+) from "react"/)[1];
    const value = code.match(/(\w+) as default/)[1];
    return new Function(alias, code.replace(/^import[^\n]+\n/, '').replace(/export\s*\{[\s\S]*$/, `return ${value};`))(react);
  };
  for (const name of names) {
    const id = new URL(`../node_modules/@phosphor-icons/react/dist/defs/${name}.es.js`, import.meta.url).pathname;
    const code = readFileSync(new URL(`../node_modules/@phosphor-icons/react/dist/defs/${name}.es.js`, import.meta.url), 'utf8');
    const optimized = plugin.transform.call({ parse: parseAst }, code, id).code;
    const before = evaluate(code), after = evaluate(optimized);
    assert.deepEqual([...after.keys()], ['regular'], name);
    assert.deepEqual(after.get('regular'), before.get('regular'), `${name} SVG must remain identical`);
  }
  assert.equal(plugin.transform('unrelated', 'src/example.tsx'), null);
  assert.throws(() => plugin.transform.call({ parse: parseAst }, 'const broken = 1;', '/@phosphor-icons/react/dist/defs/Broken.es.js'), /Unsupported/);
});
