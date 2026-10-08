import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

function load(path, imports = {}) {
  const exports = {};
  const source = readFileSync(new URL(path, import.meta.url), "utf8").replace(
    /const packs = import\.meta\.glob[^;]+;/,
    "const packs = {};",
  );
  const code = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  new Function("require", "exports", code)((name) => {
    assert.ok(name in imports, name);
    return imports[name];
  }, exports);
  return exports;
}
const styles = load("../src/appearance/bubbleStyles.ts");
const visualStyles = load("../src/appearance/visualStyles.ts", {
  './buttons/catalog.json': { default: JSON.parse(readFileSync(new URL('../src/appearance/buttons/catalog.json', import.meta.url), 'utf8')) },
});
const registry = load("../src/appearance/registry.ts", {
  "./bubbleStyles": styles,
  "./visualStyles": visualStyles,
  './buttons/onDanger': load('../src/appearance/buttons/onDanger.ts'),
  './semanticTokens': load('../src/appearance/semanticTokens.ts'),
});

test("five unique bubble styles and safe unknown fallback", () => {
  assert.equal(styles.BUBBLE_STYLES.length, 5);
  assert.equal(new Set(styles.BUBBLE_STYLES.map((item) => item.id)).size, 5);
  for (const item of styles.BUBBLE_STYLES)
    assert.equal(styles.resolveBubbleStyle(item.id), item);
  assert.equal(styles.resolveBubbleStyle("unknown-future").id, "plain");
});
test("legacy preferences default and future IDs survive normalization", () => {
  assert.equal(
    registry.normalizeAppearancePreferences({ theme_id: "night" })
      .bubble_style_id,
    "star-track",
  );
  assert.equal(
    registry.normalizeAppearancePreferences({ bubble_style_id: "future-style" })
      .bubble_style_id,
    "future-style",
  );
  assert.equal(
    registry.normalizeAppearancePreferences({ bubble_style_id: "../bad" })
      .bubble_style_id,
    "star-track",
  );
});
test("expanded appearance IDs preserve old choices and safely render future choices", () => {
  const legacy = registry.normalizeAppearancePreferences({
    trail_id: "starlight",
    typography_id: "system",
  });
  assert.equal(legacy.click_effect_id, "none");
  assert.equal(legacy.trail_id, "starlight");
  assert.equal(legacy.typography_id, "system");
  assert.equal(
    registry.normalizeAppearancePreferences({ click_effect_id: "future-click" })
      .click_effect_id,
    "future-click",
  );
  assert.equal(
    registry.normalizeAppearancePreferences({ click_effect_id: "../bad" })
      .click_effect_id,
    "none",
  );
  assert.equal(visualStyles.VISUAL_STYLE_PACKS.length, 6);
  assert.equal(visualStyles.resolveVisualStyle('picturebook').frame, 'moonweave');
  assert.equal(visualStyles.resolveVisualStyle('picturebook').name, '月光织锦');
  for (const style of visualStyles.VISUAL_STYLE_PACKS)
    assert.equal(visualStyles.resolveVisualStyle(style.id), style);
  assert.equal(
    visualStyles.resolveVisualStyle("future").id,
    "celestial-atelier",
  );
  assert.equal(registry.TYPOGRAPHY_OPTIONS.length, 7);
  const picturebook = registry.TYPOGRAPHY_OPTIONS.find(option => option.id === 'picturebook');
  assert.equal(picturebook.story, picturebook.body, 'Approved Demo uses the clear body family for story prose');
  assert.match(picturebook.body, /system-ui/);
});
test('every stored visual style renders the legacy layer as picturebook and keeps its stored id', () => {
  const previousDocument = globalThis.document;
  const theme = JSON.parse(readFileSync(new URL('../src/appearance/packs/iris-light.json', import.meta.url), 'utf8'));
  registry.THEME_PACKS.push(theme);
  const values = new Map();
  const root = { dataset: {}, style: { setProperty: (key, value) => values.set(key, value) } };
  globalThis.document = { documentElement: root };
  try {
    for (const stored of ['picturebook', 'quiet-study', 'celestial-atelier', 'future-style']) {
      const kept = registry.applyAppearancePreferences({ visual_style_id: stored, theme_id: theme.id });
      assert.equal(kept.visual_style_id, stored, 'the stored id is not rewritten');
      assert.equal(root.dataset.visualStyle, 'picturebook');
      assert.equal(root.dataset.prototypeDesign, 'picturebook');
      assert.equal(root.dataset.buttonFrame, 'moonweave');
      assert.equal(values.get('--btn-kind'), 'moonweave');
    }
  } finally {
    registry.THEME_PACKS.pop();
    if (previousDocument === undefined) delete globalThis.document;
    else globalThis.document = previousDocument;
  }
});
test("decorations cannot intercept selection or clicks; native cursor has safe fallback", () => {
  const css = readFileSync(
    new URL("../src/appearance/bubbles.css", import.meta.url),
    "utf8",
  );
  assert.match(css, /bubble-frame__ornament[^}]*pointer-events:none/);
  assert.match(css, /bubble-frame__content[^}]*text-align:left/);
  const cursor = readFileSync(
    new URL("../src/appearance/pointer-effects.css", import.meta.url),
    "utf8",
  );
  assert.match(cursor, /star-arrow\.svg[^;]*auto/);
  assert.match(cursor, /cursor:text/);
  const renderer = readFileSync(
    new URL("../src/appearance/PointerEffects.tsx", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(renderer, /cursorRenderer|cursorRenderers|drawCursor/);
});
