import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

function load(path, imports = {}) {
  const exports = {};
  const source = readFileSync(new URL(path, import.meta.url), "utf8").replace(/const packs = import\.meta\.glob[^;]+;/, "const packs = {};");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  new Function("require", "exports", code)((name) => {
    assert.ok(name in imports, name);
    return imports[name];
  }, exports);
  return exports;
}
const semantic = load("../src/appearance/semanticTokens.ts");
const registry = load("../src/appearance/registry.ts", {
  "./bubbleStyles": load("../src/appearance/bubbleStyles.ts"),
  "./visualStyles": load("../src/appearance/visualStyles.ts", {
    "./buttons/catalog.json": { default: JSON.parse(readFileSync(new URL("../src/appearance/buttons/catalog.json", import.meta.url), "utf8")) },
  }),
  "./buttons/onDanger": load("../src/appearance/buttons/onDanger.ts"),
  "./semanticTokens": semantic,
});
const pack = (id) => JSON.parse(readFileSync(new URL(`../src/appearance/packs/${id}.json`, import.meta.url), "utf8"));

function render(preferences) {
  const previous = globalThis.document;
  const values = new Map();
  const root = { dataset: {}, style: { setProperty: (key, value) => values.set(key, value) } };
  globalThis.document = { documentElement: root };
  const packs = ["iris-light", "iris-night", "forest"].map(pack);
  packs.forEach((item) => registry.THEME_PACKS.push(item));
  try {
    registry.applyAppearancePreferences({ theme_id: "iris-light", ...preferences });
    return { root, values };
  } finally {
    packs.forEach(() => registry.THEME_PACKS.pop());
    if (previous === undefined) delete globalThis.document;
    else globalThis.document = previous;
  }
}

test("ornament levels: celestial is rich, none stays, unknown ids render the default", () => {
  assert.deepEqual(registry.ORNAMENT_OPTIONS.map((option) => option.id), ["none", "subtle", "rich"]);
  assert.equal(registry.resolveOrnament("celestial"), "rich");
  assert.equal(registry.resolveOrnament("rich"), "rich");
  assert.equal(registry.resolveOrnament("subtle"), "subtle");
  assert.equal(registry.resolveOrnament("none"), "none");
  assert.equal(registry.resolveOrnament("future-level"), "rich");
  assert.equal(registry.resolveOrnament(undefined), "rich");
});

test("data-ornament follows the preference while the legacy data-decoration keeps its two values", () => {
  const expected = { celestial: ["rich", "celestial"], rich: ["rich", "celestial"], subtle: ["subtle", "celestial"], none: ["none", "none"], "future-level": ["rich", "celestial"] };
  for (const [stored, [ornament, legacy]] of Object.entries(expected)) {
    const { root } = render({ decoration_id: stored });
    assert.equal(root.dataset.ornament, ornament, stored);
    assert.equal(root.dataset.decoration, legacy, stored);
  }
});

test("the stored decoration id is preserved by normalization", () => {
  for (const id of ["celestial", "rich", "subtle", "none", "future-level"])
    assert.equal(registry.normalizeAppearancePreferences({ decoration_id: id }).decoration_id, id);
  assert.equal(registry.normalizeAppearancePreferences({}).decoration_id, "celestial");
});

test("applyThemePack writes the semantic tokens and shares the same-meaning legacy variables", () => {
  const { values } = render({ theme_id: "forest" });
  for (const name of semantic.SEMANTIC_TOKEN_NAMES) assert.ok(values.get(`--${name}`), name);
  const forest = pack("forest").palette;
  assert.equal(values.get("--danger"), forest.danger);
  assert.equal(values.get("--color-danger"), values.get("--danger"));
  assert.equal(values.get("--focus"), forest.focus);
  assert.equal(values.get("--color-focus"), values.get("--focus"));
  assert.equal(values.get("--warning-soft"), forest.warningSoft);
  assert.equal(values.get("--paper"), forest.page, "legacy-only aliases still come from the palette");
  assert.equal(values.get("--bg-page"), semantic.toHex(semantic.parseColor(forest.page)));
});
