import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * PostCSS step that moves the stylesheets listed in legacyCss.json into
 * `@layer legacy`. The design system stays unlayered, so a legacy rule can
 * never out-rank it whatever its specificity. Shrink the manifest as pages
 * migrate; it must never grow (tests/legacyCss.test.mjs).
 *
 * Runs after Tailwind, so `@tailwind` / `@apply` are already expanded and the
 * Tailwind output of index.css is layered with the rest of its file.
 */
export const LEGACY_LAYER = 'legacy';

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, '..');
const manifestPath = path.join(here, 'legacyCss.json');

export function readLegacyManifest(file = manifestPath) {
  return JSON.parse(readFileSync(file, 'utf8'));
}

/** `@charset` / `@import` must stay first; these have no cascade conflict worth layering. */
const HOISTED = new Set(['charset', 'import', 'font-face', 'keyframes', 'property']);

export function legacyLayer({ manifest = readLegacyManifest(), root: base = webRoot } = {}) {
  const legacy = new Set(manifest);
  return {
    postcssPlugin: 'sekai-legacy-layer',
    Once(css, { AtRule }) {
      const file = css.source?.input.file;
      if (!file) return;
      const relative = path.relative(base, file).split(path.sep).join('/');
      if (!legacy.has(relative)) return;
      const layer = new AtRule({ name: 'layer', params: LEGACY_LAYER });
      const hoisted = [];
      for (const node of [...css.nodes]) {
        if (node.type === 'atrule' && HOISTED.has(node.name.toLowerCase().replace(/^-\w+-/, ''))) hoisted.push(node);
        else layer.append(node);
      }
      css.removeAll();
      css.append(...hoisted, layer);
    },
  };
}
legacyLayer.postcss = true;
