import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const shell = read("../src/appearance/AppShell.tsx");
const drawer = read("../src/design-system/layouts/useNavDrawer.ts");
const css = read("../src/design-system/layouts/shell.css");

test("navigation targets and order are unchanged", () => {
  const routes = [...shell.matchAll(/\bto: "([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(routes, ["/", "/simple-chats", "/library/characters", "/worlds", "/library/lorebooks", "/library/scenarios", "/create", "/library/imports", "/settings"]);
  assert.match(shell, /path === "\/" \|\| path\.startsWith\("\/stories"\)/, "story entry stays active on /stories/*");
  assert.match(shell, /path\.startsWith\(to\)/, "other entries match by prefix");
});

test("unmigrated pages keep the ancestor hooks they depend on", () => {
  assert.match(shell, /className="tpl-shell v7-app app-shell"/);
  for (const hook of ["data-layout", "data-editor", "data-immersive"]) assert.match(shell, new RegExp(hook));
  assert.doesNotMatch(shell, /picturebook-shell|app-main|app-rail|app-mobile/);
});

test("theme toggle, appearance error and route motion keep their behaviour", () => {
  assert.match(shell, /theme_id: dark \? "iris-light" : "iris-night"/);
  assert.match(shell, /"切换为亮色外观" : "切换为深色外观"/);
  assert.match(shell, /外观保存失败，请重试。/);
  assert.match(shell, /useMoonweaveMotion\(true, preferences\.effect_intensity, pathname, route\)/);
});

test("drawer: inert main, Escape, focus trap and focus return are implemented", () => {
  for (const pattern of [/main\.current\.inert = open/, /event\.key === "Escape"/, /event\.key !== "Tab"/, /main\.current\.inert = false/, /trigger\.current\?\.focus\(\)/, /useEffect\(\(\) => setOpen\(false\), \[pathname\]\)/]) assert.match(drawer, pattern);
});

test("breakpoints: 220px nav, 64px rail from 900px, drawer below 900px", () => {
  assert.match(css, /--nav-w: 220px/);
  assert.match(css, /@media \(min-width: 900px\) and \(max-width: 1199px\)[\s\S]*--nav-w: 64px/);
  assert.match(css, /@media \(max-width: 899px\)/);
  assert.match(css, /height: calc\(var\(--size-bar\) \+ env\(safe-area-inset-top\)\)/);
});
