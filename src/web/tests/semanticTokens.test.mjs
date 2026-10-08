import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import ts from "typescript";

const exports = {};
new Function("exports", ts.transpileModule(readFileSync(new URL("../src/appearance/semanticTokens.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText)(exports);
const { SEMANTIC_TOKEN_NAMES, SHARED_WITH_LEGACY, contrastRatio, deriveSemanticTokens, mix, parseColor, resolveTokenColor } = exports;

const packDir = new URL("../src/appearance/packs/", import.meta.url);
const packs = readdirSync(packDir).filter((name) => name.endsWith(".json")).map((name) => JSON.parse(readFileSync(new URL(name, packDir), "utf8")));
const byId = (id) => packs.find((pack) => pack.id === id);
const tokensOf = (pack) => deriveSemanticTokens(pack);

// Spec section 3.3, iris-light / iris-night.
const APPROVED = {
  "iris-light": {
    "bg-page": "#F3F1F8", "bg-shell": "#FAF8FD", "surface-1": "#FFFFFF", "surface-2": "#F6F3FA", "surface-3": "#FFFFFF", "surface-reading": "#F8F7FB",
    "border-subtle": "#E4DDEF", "border-default": "#CFC5E0", "border-strong": "#9286AA", "text-1": "#2B2438", "text-2": "#5E5470", "text-3": "#6E657B",
    "accent": "#6A4FB0", "accent-soft": "#EDE6FA", "region-story": "#23708A", "region-story-soft": "#E3F1F5", "region-character": "#94476A", "region-character-soft": "#F8E8EF",
    "gold-1": "#F6E3B8", "gold-2": "#E3C385", "gold-3": "#C9A265", "gold-border": "#B08D52", "gold-text": "#3B291A",
    "danger": "#A33653", "danger-soft": "#FAEAF0", "success": "#296E55", "success-soft": "#E4F2EB", "warning": "#8A5A12", "warning-soft": "#FCF1DA", "focus": "#7651B0",
  },
  "iris-night": {
    "bg-page": "#15161F", "bg-shell": "#1B1C28", "surface-1": "#20212E", "surface-2": "#282A39", "surface-3": "#2C2E3F", "surface-reading": "#181A24",
    "border-subtle": "#34354A", "border-default": "#46475F", "border-strong": "#767592", "text-1": "#F1EDF7", "text-2": "#BDB7CC", "text-3": "#9994AB",
    "accent": "#B9A2F0", "accent-soft": "#332A4D", "region-story": "#8ECDDD", "region-story-soft": "#1D3640", "region-character": "#E6A9C4", "region-character-soft": "#40263A",
    "gold-1": "#F0D9A8", "gold-2": "#D6B373", "gold-3": "#BC955A", "gold-border": "#A8854C", "gold-text": "#271D12",
    "danger": "#F1A4BB", "danger-soft": "#4A263D", "success": "#99D8B9", "success-soft": "#203D35", "warning": "#E8C77E", "warning-soft": "#423621", "focus": "#D2B7FF",
  },
};

test("iris-light and iris-night produce exactly the approved values", () => {
  for (const [id, expected] of Object.entries(APPROVED)) {
    const tokens = tokensOf(byId(id));
    for (const [name, value] of Object.entries(expected)) assert.equal(tokens[`--${name}`].toUpperCase(), value, `${id} ${name}`);
  }
});

test("iris packs match every colour token of the approved demo when it is present", (t) => {
  const demo = new URL("../../../document/design/ui-system-demo/tokens.css", import.meta.url);
  if (!existsSync(demo)) return t.skip("demo not in this checkout");
  const css = readFileSync(demo, "utf8");
  const grab = (re) => Object.fromEntries([...css.match(re)[1].matchAll(/--([\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim().replace(/\s+/g, " ")]));
  const light = grab(/:root \{([\s\S]*?)\n\}/);
  const dark = { ...light, ...grab(/:root\[data-theme="dark"\] \{([\s\S]*?)\n\}/) };
  for (const [id, expected] of [["iris-light", light], ["iris-night", dark]]) {
    const tokens = tokensOf(byId(id));
    for (const name of SEMANTIC_TOKEN_NAMES) assert.equal(tokens[`--${name}`], expected[name], `${id} ${name}`);
  }
});

test("tokens.css carries the iris-light values as its fallback", () => {
  const css = readFileSync(new URL("../src/design-system/tokens.css", import.meta.url), "utf8");
  const tokens = tokensOf(byId("iris-light"));
  for (const name of SEMANTIC_TOKEN_NAMES) {
    const match = new RegExp(`--${name}:\\s*([^;]+);`).exec(css);
    assert.ok(match, `${name} missing from tokens.css`);
    assert.equal(match[1].trim(), tokens[`--${name}`], name);
  }
});

test("every pack yields every semantic token; the manifest stays version 1", () => {
  assert.equal(packs.length, 11);
  for (const pack of packs) {
    assert.equal(pack.version, 1);
    const tokens = tokensOf(pack);
    for (const name of SEMANTIC_TOKEN_NAMES) assert.ok(tokens[`--${name}`], `${pack.id} ${name}`);
    for (const name of Object.keys(pack.semantic ?? {})) assert.ok(SEMANTIC_TOKEN_NAMES.includes(name), `${pack.id} unknown override ${name}`);
  }
  assert.deepEqual(packs.filter((pack) => pack.semantic).map((pack) => pack.id).sort(), ["iris-light", "iris-night"]);
});

test("explicit overrides win and unknown names are ignored", () => {
  const base = byId("forest");
  const tokens = deriveSemanticTokens({ ...base, semantic: { accent: "#123456", "not-a-token": "#000000" } });
  assert.equal(tokens["--accent"], "#123456");
  assert.equal(tokens["--not-a-token"], undefined);
});

test("parseColor reads the forms the packs use", () => {
  assert.deepEqual(parseColor("#fff"), { r: 255, g: 255, b: 255, a: 1 });
  assert.equal(parseColor("rgba(43, 36, 56, 0.42)").a, 0.42);
  const mixed = parseColor("color-mix(in srgb, #783f3d 34%, var(--surface))", { "--surface": "#2b2922" });
  const expected = mix(parseColor("#2b2922"), parseColor("#783f3d"), 0.34);
  assert.ok(Math.abs(mixed.r - expected.r) < 1e-9 && Math.abs(mixed.b - expected.b) < 1e-9);
  assert.equal(parseColor("notacolor"), null);
});

// [foreground, background, minimum] as listed by the demo's contrast check.
const SURFACES = ["surface-1", "surface-2", "bg-page", "surface-reading", "surface-3", "bg-shell"];
const PAIRS = [];
for (const text of ["text-1", "text-2", "text-3", "accent"]) for (const nav of ["nav-top", "nav-bottom"]) PAIRS.push([text, nav, 4.5]);
for (const text of ["text-1", "text-2", "text-3"]) for (const surface of SURFACES) PAIRS.push([text, surface, 4.5]);
for (const soft of ["region-story-soft", "region-character-soft"]) PAIRS.push(["text-1", soft, 4.5], ["text-2", soft, 4.5]);
for (const text of ["text-1", "text-2", "text-3"]) PAIRS.push([text, "accent-soft", 4.5]);
for (const background of ["surface-1", "accent-soft", "surface-2", "bg-page"]) PAIRS.push(["accent", background, 4.5]);
PAIRS.push(["on-accent", "accent", 4.5]);
for (const region of ["region-story", "region-character"]) for (const background of ["surface-1", "surface-reading", `${region}-soft`, "bg-page"]) PAIRS.push([region, background, 4.5]);
for (const gold of ["gold-1", "gold-2", "gold-3"]) PAIRS.push(["gold-text", gold, 4.5]);
for (const status of ["danger", "success", "warning"]) for (const background of ["surface-1", `${status}-soft`, "surface-2", "surface-3"]) PAIRS.push([status, background, 4.5]);
for (const outline of ["border-strong", "border-strong-hover", "focus"]) for (const surface of ["surface-1", "bg-page", "surface-2", "surface-reading", "surface-3"]) PAIRS.push([outline, surface, 3]);
PAIRS.push(["focus", "accent-soft", 3], ["border-strong", "accent-soft", 3]);
// The approved iris values miss 3:1 by a hair on a selected control background (2.78 and 3.00 measured).
// They are approved, so they are recorded here instead of changed; derived packs must pass.
const APPROVED_EXCEPTIONS = new Set(["iris-light border-strong accent-soft", "iris-night border-strong accent-soft"]);

test("text reaches 4.5:1 and outlines / focus reach 3:1 on the surfaces they sit on, for every pack", () => {
  const failures = [];
  for (const pack of packs) {
    const tokens = tokensOf(pack);
    for (const [foreground, background, minimum] of PAIRS) {
      const ratio = contrastRatio(resolveTokenColor(tokens, foreground), resolveTokenColor(tokens, background));
      if (ratio + 1e-9 < minimum && !APPROVED_EXCEPTIONS.has(`${pack.id} ${foreground} ${background}`))
        failures.push(`${pack.id}: ${foreground} on ${background} = ${ratio.toFixed(2)} (< ${minimum})`);
    }
  }
  assert.deepEqual(failures, []);
});

test("the approved exceptions are exactly the ones that fail", () => {
  for (const id of ["iris-light", "iris-night"]) {
    const tokens = tokensOf(byId(id));
    assert.ok(contrastRatio(resolveTokenColor(tokens, "border-strong"), resolveTokenColor(tokens, "accent-soft")) < 3, id);
  }
});

test("legacy variables shared with the design system keep the palette value unless it misses its contrast floor", (t) => {
  const adjusted = [];
  for (const pack of packs) {
    const tokens = tokensOf(pack);
    for (const [key, name] of Object.entries(SHARED_WITH_LEGACY)) {
      const value = tokens[`--${name}`];
      if (value.toLowerCase() === pack.palette[key].toLowerCase()) continue;
      adjusted.push(`${pack.id} --${name}`);
      assert.ok(!key.endsWith("Soft"), `${pack.id} ${key}: a fill is never adjusted`);
      const original = parseColor(pack.palette[key], { "--surface": pack.palette.surface });
      const floor = key === "focus" ? 3.15 : 4.5;
      const surfaces = ["surface-1", "surface-2", "surface-3", "bg-page"].map((surface) => resolveTokenColor(tokens, surface));
      assert.ok(surfaces.some((surface) => contrastRatio(original, surface) < floor), `${pack.id} ${key} was adjusted although it passes`);
    }
  }
  t.diagnostic(`shared legacy variables adjusted for contrast: ${adjusted.length ? adjusted.join(", ") : "none"}`);
});
