import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const exports = {};
const code = ts.transpileModule(
  readFileSync(
    new URL("../src/appearance/pointerArt.ts", import.meta.url),
    "utf8",
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
  },
).outputText;
new Function("exports", code)(exports);
const { createPointerEngine, trailRenderers, clickRenderers } = exports;

function harness(patch = {}) {
  let time = 0,
    enabled = true,
    id = 0;
  const frames = new Map(),
    commands = [];
  const ctx = new Proxy(
    {},
    {
      get: (_, name) =>
        name === "createLinearGradient"
          ? () => ({ addColorStop() {} })
          : (...args) => {
              for (const value of args)
                if (typeof value === "number")
                  assert.ok(Number.isFinite(value), name);
              commands.push([name, ...args]);
            },
      set: (_, name, value) => {
        if (typeof value === "number") assert.ok(Number.isFinite(value), name);
        return true;
      },
    },
  );
  const engine = createPointerEngine({
    ctx,
    trailId: "comet",
    clickId: "star-ring",
    intensity: 0.65,
    colors: {
      accent: "#ac854e",
      primary: "#365da8",
      ink: "#263552",
      secondary: "#9381c4",
    },
    size: () => ({ width: 1280, height: 720 }),
    enabled: () => enabled,
    now: () => time,
    requestFrame: (cb) => {
      frames.set(++id, cb);
      return id;
    },
    cancelFrame: (key) => frames.delete(key),
    ...patch,
  });
  return {
    engine,
    commands,
    frames,
    setEnabled: (value) => {
      enabled = value;
    },
    tick(value) {
      time = value;
      const pending = [...frames.values()];
      frames.clear();
      pending.forEach((cb) => cb(time));
    },
  };
}

test("click-only effects work with the trail disabled and stop completely after fading", () => {
  const h = harness({ trailId: "none" });
  h.engine.move(10, 10);
  assert.equal(h.frames.size, 0);
  h.engine.click(10, 10);
  h.tick(150);
  assert.ok(h.commands.some(([name]) => name === "arc"));
  assert.equal(h.engine.stats().points, 0);
  h.tick(900);
  assert.deepEqual(h.engine.stats(), {
    points: 0,
    pulses: 0,
    scheduled: false,
  });
  assert.equal(h.frames.size, 0);
});
test("rapid input is bounded, and a pointer re-entry never joins distant points", () => {
  const h = harness();
  for (let i = 0; i < 1000; i++) {
    h.engine.move(i * 3, 20);
    h.engine.click(i, 20);
  }
  assert.ok(h.engine.stats().points <= 80);
  assert.equal(h.engine.stats().pulses, 8);
  assert.equal(h.frames.size, 1);
  h.engine.move(10000, 50);
  assert.equal(h.engine.stats().points, 1);
  h.engine.clear();
  assert.equal(h.frames.size, 0);
});
test("reduced motion, hidden windows, zero intensity and disposal do not leave animation loops", () => {
  const h = harness();
  h.engine.click(10, 10);
  h.setEnabled(false);
  h.tick(20);
  assert.deepEqual(h.engine.stats(), {
    points: 0,
    pulses: 0,
    scheduled: false,
  });
  h.engine.move(10, 10);
  h.engine.click(10, 10);
  assert.equal(h.frames.size, 0);
  h.setEnabled(true);
  h.engine.click(20, 20);
  h.engine.dispose();
  assert.equal(h.frames.size, 0);
  h.engine.click(20, 20);
  assert.equal(h.frames.size, 0);
  for (const intensity of [0, NaN]) {
    const off = harness({ intensity });
    off.engine.click(1, 1);
    off.engine.move(1, 1);
    assert.equal(off.frames.size, 0);
  }
});
test("every shipped trail and click style renders finite canvas geometry and restores its context", () => {
  assert.equal(Object.keys(trailRenderers).length, 8);
  assert.equal(Object.keys(clickRenderers).length, 6);
  for (const trailId of Object.keys(trailRenderers))
    for (const clickId of Object.keys(clickRenderers)) {
      const h = harness({ trailId, clickId });
      for (let i = 0; i < 24; i++) h.engine.move(100 + i * 3, 100 + i);
      h.engine.click(130, 110);
      h.tick(250);
      assert.ok(
        h.commands.some(([name]) => name === "fill" || name === "stroke"),
        `${trailId}/${clickId}`,
      );
      assert.equal(
        h.commands.filter(([name]) => name === "save").length,
        h.commands.filter(([name]) => name === "restore").length,
      );
      h.tick(1000);
      assert.equal(h.frames.size, 0);
    }
});
test("invalid coordinates and disabled IDs never start frames", () => {
  const h = harness({ trailId: "none", clickId: "none" });
  h.engine.move(1, 2);
  h.engine.click(1, 2);
  assert.equal(h.frames.size, 0);
  const on = harness();
  on.engine.move(NaN, 2);
  on.engine.click(Infinity, 2);
  assert.equal(on.frames.size, 0);
});
