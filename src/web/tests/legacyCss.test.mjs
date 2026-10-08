import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join, relative, resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import postcss from "postcss";

const web = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const posix = (path) => path.split("\\").join("/");
const manifest = JSON.parse(readFileSync(join(web, "build/legacyCss.json"), "utf8"));
const { legacyLayer } = await import(pathToFileURL(join(web, "build/legacyLayer.mjs")).href);

/** The legacy set when the design system was introduced. It may shrink; adding to it fails this test. */
const BASELINE = [
  "node_modules/@xyflow/react/dist/style.css",
  "src/appearance/app-shell.css",
  "src/appearance/appearance-settings.css",
  "src/appearance/bubbles.css",
  "src/appearance/buttons/buttons.css",
  "src/appearance/buttons/moonweave.css",
  "src/appearance/fonts.css",
  "src/appearance/moonweave-frame.css",
  "src/appearance/page-art.css",
  "src/appearance/pointer-effects.css",
  "src/appearance/styles/celestial-atelier.css",
  "src/appearance/styles/visual-variants.css",
  "src/appearance/workspace-refresh.css",
  "src/components/WritingAssistant.css",
  "src/components/character-creation.css",
  "src/components/chat-notices.css",
  "src/components/composer.css",
  "src/components/load-state.css",
  "src/components/lorebook-entry-fields.css",
  "src/components/message-actions.css",
  "src/components/player-control.css",
  "src/conversation.css",
  "src/design-system/dialogs.css",
  "src/design-system/picturebook.css",
  "src/features/asset-import/asset-import.css",
  "src/features/library/library-tools.css",
  "src/features/stories/homepage.css",
  "src/features/worlds/world-covers.css",
  "src/features/worlds/world-creation.css",
  "src/features/worlds/world-organize.css",
  "src/features/worlds/worlds.css",
  "src/group-actors.css",
  "src/index.css",
  "src/lorebook-agent.css",
  "src/mobile-access.css",
  "src/pages/characters-picturebook.css",
  "src/pages/story-workspace.css",
  "src/simple-chat.css",
  "src/story-panels.css",
  "src/workspace-layout.css",
];

/** New-system stylesheets: everything under design-system/ that is not legacy. Later phases add feature page styles (layer ui.pages) here. */
const NEW_SYSTEM = [/^src\/design-system\/(?:[\w-]+\/)*[\w-]+\.css$/];
const MIGRATED_PAGES = JSON.parse(readFileSync(join(web, "build/uiCss.json"), "utf8"));

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? walk(join(dir, entry.name)) : [join(dir, entry.name)],
  );
}
const sources = walk(join(web, "src"));
const cssFiles = sources.filter((file) => file.endsWith(".css")).map((file) => posix(relative(web, file))).sort();
const isNewSystem = (file) => !manifest.includes(file) && (MIGRATED_PAGES.includes(file) || NEW_SYSTEM.some((pattern) => pattern.test(file)));

test("the legacy set only shrinks", () => {
  assert.deepEqual([...manifest].sort(), manifest, "manifest is sorted");
  assert.equal(new Set(manifest).size, manifest.length, "manifest has no duplicates");
  const added = manifest.filter((file) => !BASELINE.includes(file));
  assert.deepEqual(added, [], "a stylesheet cannot be added to the legacy set; write it in the design system");
  assert.ok(manifest.length <= BASELINE.length);
});

test("every manifest entry exists, and every stylesheet is legacy or new system", () => {
  for (const file of manifest) assert.ok(existsSync(join(web, file)), `${file} is listed but missing`);
  const orphans = cssFiles.filter((file) => !manifest.includes(file) && !isNewSystem(file));
  assert.deepEqual(orphans, [], "stylesheet is neither in build/legacyCss.json nor a design-system file");
});

test("stylesheets imported from a package are covered by the manifest", () => {
  const imports = sources
    .filter((file) => /\.(tsx?|css)$/.test(file))
    .flatMap((file) => [...readFileSync(file, "utf8").matchAll(/import\s+["']([^."'/][^"']*\.css)["']/g)].map((match) => match[1]));
  assert.ok(imports.length > 0);
  for (const specifier of imports) assert.ok(manifest.includes(`node_modules/${specifier}`), `${specifier} must be layered as legacy`);
});

test("the canonical layer order is registered in HTML before any stylesheet, for dev and build", async () => {
  assert.equal(readFileSync(join(web, "src/design-system/layers.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "").trim(), "@layer legacy, ui.reset, ui.tokens, ui.primitives, ui.layouts, ui.pages;");
  const main = readFileSync(join(web, "src/main.tsx"), "utf8");
  assert.doesNotMatch(main, /import\s+["'].*layers\.css["']/);
  const { cascadeLayerOrder } = await import(pathToFileURL(join(web, 'build/cascadeLayerOrder.mjs')).href);
  const plugin = cascadeLayerOrder();
  assert.equal(plugin.transformIndexHtml.order, 'pre');
  const [tag] = plugin.transformIndexHtml.handler();
  assert.equal(tag.tag, 'style');
  assert.equal(tag.injectTo, 'head-prepend');
  assert.equal(tag.children, readFileSync(join(web, 'src/design-system/layers.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').trim());
  assert.ok(Object.hasOwn(tag.attrs, 'data-cascade-layer-order'));
  const vite = readFileSync(join(web, 'vite.config.ts'), 'utf8');
  assert.match(vite, /plugins:\s*\[cascadeLayerOrder\(\)/);
});

// ---------------------------------------------------------------- plugin

async function run(css, file, manifestEntries = ["src/legacy.css"]) {
  const result = await postcss([legacyLayer({ manifest: manifestEntries, root: web })]).process(css, { from: join(web, file) });
  return result.root;
}

test("a legacy file moves into @layer legacy and keeps its order", async () => {
  const root = await run(".a{color:red}@media (max-width:10px){.b{color:blue}}.c{color:red!important}", "src/legacy.css");
  assert.equal(root.nodes.length, 1);
  const layer = root.nodes[0];
  assert.equal(`${layer.name} ${layer.params}`, "layer legacy");
  assert.deepEqual(layer.nodes.map((node) => node.selector ?? `@${node.name}`), [".a", "@media", ".c"]);
  assert.equal(layer.last.first.important, true, "!important is untouched");
});

test("@charset, @import, @font-face, @keyframes and @property stay outside the layer", async () => {
  const root = await run(
    '@charset "utf-8";@import url("x.css");@font-face{font-family:F;src:url(f.woff2)}@keyframes spin{to{opacity:0}}@property --p{syntax:"<length>";inherits:false;initial-value:0px}.a{animation:spin 1s}@container name style(--k: v){.b{color:red}}',
    "src/legacy.css",
  );
  assert.deepEqual(root.nodes.map((node) => node.name ?? node.selector), ["charset", "import", "font-face", "keyframes", "property", "layer"]);
  const layer = root.last;
  assert.deepEqual(layer.nodes.map((node) => node.selector ?? `@${node.name}`), [".a", "@container"]);
});

test("the reset layer reverts native controls that carry a ui- class, and nothing else", () => {
  const css = readFileSync(join(web, "src/design-system/reset.css"), "utf8");
  assert.match(css, /@layer ui\.reset/);
  assert.match(css, /all: revert/);
  assert.match(css, /\[class\^="ui-"\]/);
  assert.doesNotMatch(css.replace(/\/\*[\s\S]*?\*\//g, ""), /(^|,)\s*\[class|(^|,)\s*\*/m, "never selects non-native elements or everything");
});

test("a file outside the manifest, and a file with no path, are left alone", async () => {
  const other = await run(".a{color:red}", "src/design-system/tokens.css");
  assert.equal(other.nodes[0].selector, ".a");
  const anonymous = await postcss([legacyLayer({ manifest: ["src/legacy.css"], root: web })]).process(".a{color:red}", { from: undefined });
  assert.equal(anonymous.root.nodes[0].selector, ".a");
});

test("postcss runs Tailwind first, then the legacy layer, then autoprefixer", async () => {
  const config = (await import(pathToFileURL(join(web, "postcss.config.js")).href)).default;
  assert.deepEqual(config.plugins.map((plugin) => plugin.postcssPlugin ?? plugin.name), ["tailwindcss", "sekai-legacy-layer", "autoprefixer"]);
});

test("index.css keeps its Tailwind output, all inside the legacy layer", async () => {
  const config = (await import(pathToFileURL(join(web, "postcss.config.js")).href)).default;
  const file = join(web, "src/index.css");
  const { root } = await postcss(config.plugins).process(readFileSync(file, "utf8"), { from: file });
  const unlayered = root.nodes.filter((node) => !(node.type === "atrule" && ["layer", "font-face", "keyframes", "property", "charset", "import"].includes(node.name)));
  assert.deepEqual(unlayered.map((node) => node.selector ?? node.name), []);
  const layer = root.nodes.find((node) => node.type === "atrule" && node.name === "layer");
  const selectors = new Set();
  layer.walkRules((rule) => selectors.add(rule.selector));
  assert.ok([...selectors].some((selector) => selector === ".flex"), "Tailwind utilities are expanded");
  assert.ok([...selectors].some((selector) => /\*|::backdrop/.test(selector)), "Tailwind base is expanded");
  assert.ok(!layer.toString().includes("@tailwind"), "no @tailwind directive survives");
});

// ---------------------------------------------------------------- new-system lint

/** Remove var(...) references (balanced) so only literal text remains. */
function stripVars(value) {
  let out = "";
  for (let i = 0; i < value.length; ) {
    if (value.startsWith("var(", i)) {
      i += 3; // at the opening parenthesis
      let depth = 0;
      do {
        if (value[i] === "(") depth += 1;
        if (value[i] === ")") depth -= 1;
        i += 1;
      } while (depth > 0 && i < value.length);
    } else out += value[i++];
  }
  return out;
}
const NAMED_COLORS = /\b(white|black|red|green|blue|yellow|orange|purple|pink|gray|grey|silver|gold|brown|cyan|magenta|navy|teal|maroon|lime|aqua|fuchsia|olive|violet|indigo|crimson|coral|ivory|beige|lavender)\b/i;
const LENGTH = /\d(px|rem|em|pt|ex|ch|vw|vh|vmin|vmax)\b/i;

export function lintNewSystem(css, { allowLiterals = false } = {}) {
  const problems = [];
  const root = postcss.parse(css);
  for (const node of root.nodes) {
    const ok = node.type === "comment" || (node.type === "atrule" && node.name === "layer" && /^ui\.(reset|tokens|primitives|layouts|pages)$/.test(node.params));
    if (!ok) problems.push(`top-level ${node.type === "atrule" ? `@${node.name} ${node.params}` : node.type} outside a ui.* layer`);
  }
  root.walkAtRules("layer", (rule) => {
    if (rule.parent !== root || !/^ui\.(reset|tokens|primitives|layouts|pages)$/.test(rule.params)) problems.push(`unexpected @layer ${rule.params}`);
  });
  root.walkDecls((decl) => {
    const where = `${decl.parent.selector ?? decl.parent.name ?? ""} { ${decl.prop} }`;
    if (decl.important) problems.push(`!important in ${where}`);
    if (allowLiterals) return;
    const value = decl.value.replace(/url\([^)]*\)/g, "");
    const bare = stripVars(value);
    if (/#[0-9a-f]{3,8}\b/i.test(bare) || /\b(rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch)\(/i.test(bare) || NAMED_COLORS.test(bare)) problems.push(`colour literal in ${where}: ${decl.value}`);
    if (decl.prop === "z-index" && /^\d{3,}$/.test(bare.trim())) problems.push(`raw stacking level in ${where}: ${decl.value} (use a --z-* token)`);
    if (decl.prop === "border-radius" && LENGTH.test(bare)) problems.push(`raw radius in ${where}: ${decl.value}`);
    if (decl.prop === "font-size" && LENGTH.test(bare)) problems.push(`raw font-size in ${where}: ${decl.value}`);
    if (decl.prop === "font" && LENGTH.test(bare)) problems.push(`raw font size in ${where}: ${decl.value}`);
    if (decl.prop === "box-shadow" && bare.replace(/\b(none|inset)\b|[,\s]/g, "") !== "") problems.push(`raw shadow in ${where}: ${decl.value}`);
  });
  return problems;
}

test("the lint catches what it is meant to catch", () => {
  const bad = "@layer ui.primitives{.a{color:#fff;background:rgba(0,0,0,.5);border-radius:6px;font-size:13px;font:600 14px/1 x;box-shadow:0 1px 2px #000;outline:1px solid red;z-index:150}.b{color:var(--c) !important}}.c{color:var(--c)}";
  const problems = lintNewSystem(bad).join("\n");
  for (const expected of ["colour literal", "raw stacking level", "raw radius", "raw font-size", "raw font size", "raw shadow", "!important", "outside a ui.* layer"]) assert.match(problems, new RegExp(expected));
  assert.deepEqual(lintNewSystem("@layer ui.primitives{.a{color:var(--text-1);border-radius:var(--radius-card);border-radius:50%;font:var(--fw-semibold) var(--fs-sm) / 1 var(--font-ui);box-shadow:var(--shadow-e1), var(--x, var(--y)),none;background:color-mix(in srgb, var(--a) 60%, transparent)}}"), []);
});

test("design-system stylesheets use tokens only, sit in a ui.* layer and never use !important", () => {
  const report = [];
  for (const file of cssFiles.filter(isNewSystem)) {
    // tokens.css is where the literals are defined; layers.css is just the order statement.
    const problems = lintNewSystem(readFileSync(join(web, file), "utf8").replace(/\/\*[\s\S]*?\*\//g, ""), { allowLiterals: file.endsWith("tokens.css") });
    if (file.endsWith("layers.css")) continue;
    for (const problem of problems) report.push(`${file}: ${problem}`);
  }
  assert.deepEqual(report, []);
});
