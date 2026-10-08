import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { productionManualChunks, productionRouterEntry } from '../build/productionChunks.mjs';
import { staticBundleClosure } from '../build/bundleGraph.mjs';

test('startup closure counts transitive and shared static imports once, excluding lazy routes', async () => {
  const sources = new Map([
    ['entry.js', `import { a } from './core.js'; import './shared.js'; const route = () => import('./lazy.js'); export { a };`],
    ['core.js', `export { a } from './shared.js';`],
    ['shared.js', `export const a = 1; const example = "import './not-a-module.js'";`],
    ['lazy.js', `import './big-route.js';`],
    ['big-route.js', 'export const value = 2;'],
  ]);
  assert.deepEqual(await staticBundleClosure('entry.js', sources), ['entry.js', 'core.js', 'shared.js']);
  sources.set('shared.js', `import './entry.js'; export const a = 1;`);
  assert.equal((await staticBundleClosure('entry.js', sources)).length, 3);
});

test('a missing initial dependency fails instead of silently shrinking the startup budget', async () => {
  await assert.rejects(staticBundleClosure('entry.js', new Map([['entry.js', `import './missing.js';`]])), /Missing static bundle dependency/);
});

test('shell, lazy forms and vendor groups keep dependency direction and platform path boundaries', () => {
  const chunk = path => productionManualChunks(`C:/project/src/web/${path}`);
  assert.equal(chunk('src/design-system/Button.tsx'), 'ui-core');
  assert.equal(chunk('src/design-system/Select.tsx'), 'ui-lazy');
  assert.equal(chunk('src/design-system/primitives/button.css'), undefined);
  assert.equal(chunk('src/design-system/index.ts'), undefined);
  assert.equal(chunk('src/pages/SimpleChatPage.tsx'), undefined);
  assert.equal(chunk('node_modules/@floating-ui/react-dom/dist/floating-ui.react-dom.mjs'), 'ui-core');
  assert.equal(chunk('node_modules/lucide-react/dist/esm/shared/src/utils/mergeClasses.mjs'), 'ui-core');
  assert.equal(chunk('node_modules/@radix-ui/react-use-effect-event/dist/index.mjs'), 'ui-core');
  assert.equal(chunk('node_modules/@radix-ui/react-select/dist/index.mjs'), 'ui-lazy');
  assert.equal(chunk('node_modules/react-router/dist/production/index.mjs'), 'vendor-react');
  assert.equal(chunk('node_modules/qrcode.react/lib/esm/index.js'), 'vendor-qrcode');
  assert.equal(chunk('node_modules/@xyflow/react/dist/esm/index.js'), 'vendor-worldgraph');
  assert.equal(productionManualChunks('C:\\project\\src\\web\\src\\design-system\\Select.tsx'), 'ui-lazy');
});

test('same-version Router production entry retains the development entry public exports', async () => {
  const entry = productionRouterEntry();
  const production = await import(pathToFileURL(entry).href);
  const development = await import('react-router');
  assert.deepEqual(Object.keys(production).sort(), Object.keys(development).sort());
  for (const key of ['BrowserRouter', 'Routes', 'Route', 'Link', 'useNavigate', 'useParams', 'useLocation']) assert.equal(typeof production[key], typeof development[key]);
});
