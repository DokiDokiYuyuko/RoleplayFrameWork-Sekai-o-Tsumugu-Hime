import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const source = fileURLToPath(new URL('../src/design-system/layers.css', import.meta.url));

/** Register the one canonical layer order before any eager or lazy CSS can load.
 * A JS-side CSS import cannot guarantee this once Vite extracts multiple sheets.
 */
export function cascadeLayerOrder() {
  return {
    name: 'canonical-cascade-layer-order',
    transformIndexHtml: {
      order: 'pre',
      handler() {
        const css = readFileSync(source, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').trim();
        if (!/^@layer\s+[\w.,\s-]+;$/.test(css)) throw new Error('layers.css must contain exactly the canonical layer-order declaration');
        return [{ tag: 'style', attrs: { 'data-cascade-layer-order': '' }, children: css, injectTo: 'head-prepend' }];
      },
    },
    configureServer(server) {
      server.watcher.add(source);
      server.watcher.on('change', file => {
        if (file.replaceAll('\\', '/') === source.replaceAll('\\', '/')) server.ws.send({ type: 'full-reload', path: '*' });
      });
    },
  };
}
