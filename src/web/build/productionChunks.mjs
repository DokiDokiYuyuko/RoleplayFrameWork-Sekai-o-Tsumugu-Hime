import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';

const require = createRequire(import.meta.url);

/** The package's default ESM export is its development build even during Vite build. */
export function productionRouterEntry() {
  return join(dirname(require.resolve('react-router/package.json')), 'dist/production/index.mjs');
}

const shellPrimitives = /\/(?:Button|Icon|IconButton|NavItem|Toast|Tooltip|SurfaceDialog|ConfirmDialog|PublicArt|useButtonSkin|useMediaQuery)\.tsx?$/;
const radixCore = /\/@radix-ui\/(?:primitive|number|rect|react-tooltip|react-dismissable-layer|react-dialog|react-popper|react-portal|react-slot|react-context|react-primitive|react-presence|react-focus-scope|react-compose-refs|react-id|react-use-layout-effect|react-focus-guards|react-use-controllable-state|react-use-effect-event|react-use-callback-ref|react-use-escape-keydown|react-arrow|react-visually-hidden|react-use-rect|react-use-size|react-use-previous)\//;

/** Keep shell dependencies separate from lazy forms, graph tools and QR rendering.
 * CSS and empty export barrels stay with Rollup's automatic grouping. Assigning
 * them to the lazy group creates an artificial reverse dependency from the shell.
 */
export function productionManualChunks(id) {
  const path = id.replaceAll('\\', '/');
  if (path.includes('commonjsHelpers')) return 'vendor-react';
  if (!path.includes('/node_modules/')) {
    if (path.includes('/src/features/worldline/')) return 'vendor-worldgraph';
    if (path.endsWith('/src/main.tsx') || path.includes('/src/api/') || /\/src\/features\/[^/]+\/[^/]*Client\.tsx?$/.test(path) || path.includes('/src/utils/')) return 'app-runtime';
    if (/\/src\/appearance\/(?:moonweaveAssets|moonweaveCatalog|Ornament)/.test(path)) return 'ui-core';
    if (path.includes('/src/design-system/')) {
      if (path.endsWith('.css') || path.endsWith('/index.ts')) return;
      return shellPrimitives.test(path) || path.includes('/internal/') || path.includes('/layouts/') ? 'ui-core' : 'ui-lazy';
    }
    return;
  }
  if (/\/(?:@xyflow|d3-[^/]+|internmap)\//.test(path)) return 'vendor-worldgraph';
  if (/\/node_modules\/(?:react|react-dom|scheduler|react-router|cookie|set-cookie-parser|zustand|use-sync-external-store)\//.test(path)) return 'vendor-react';
  if (path.includes('/@tanstack/')) return 'app-runtime';
  if (path.includes('/qrcode.react/')) return 'vendor-qrcode';
  if (path.includes('/@radix-ui/') && !radixCore.test(path)) return 'ui-lazy';
  return 'ui-core';
}
