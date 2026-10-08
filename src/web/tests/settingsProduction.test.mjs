import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { readFileSync } from "node:fs";
import { load } from "./storySupport.mjs";

test("settings picker preserves empty selection, dynamic options, disabled options and true value callbacks", () => {
  const { SettingsSelect } = load("../src/components/SettingsSelect.tsx", {
    "../design-system/Select": { Select: "shared-select" },
  });
  const changes = [];
  const element = SettingsSelect({
    value: "",
    onValueChange: (value) => changes.push(value),
    children: [
      createElement("option", { value: "" }, "关闭"),
      [createElement("option", { value: "saved", disabled: true }, "已保存")],
      false,
    ],
  });
  assert.equal(element.type, "shared-select");
  assert.equal(element.props.options.length, 2);
  assert.equal(element.props.options[1].disabled, true);
  assert.equal(element.props.value, element.props.options[0].value);
  element.props.onValueChange("saved");
  element.props.onValueChange(element.props.options[0].value);
  assert.deepEqual(changes, ["saved", ""]);
});

const defaults = {
  theme_id: "iris-light",
  typography_id: "picturebook",
  decoration_id: "rich",
  effect_intensity: 0.65,
  avatar_frame_id: "iris-wreath",
  dialogue_avatar_size: 40,
  primary_button_skin: true,
  secondary_button_skin: false,
  card_ornaments: true,
  card_border: false,
  background_art: true,
  portrait_placeholder: "art",
  reading_width: "standard",
  reading_font_size: 18,
};
function appearanceStore(patchSettings) {
  const cache = new Map();
  const previous = globalThis.localStorage;
  globalThis.localStorage = {
    getItem: (key) => cache.get(key) ?? null,
    setItem: (key, value) => cache.set(key, value),
  };
  let durable = { ...defaults };
  const requests = [];
  const module = load("../src/appearance/store.ts", {
    "../queryClient": {
      queryClient: { fetchQuery: async () => ({ appearance: { ...durable } }) },
    },
    "../features/resources/resourceQueries": {
      resourceQueries: { settings: () => ({}) },
    },
    "../api/client": {
      api: {
        patchSettings: async (payload) => {
          requests.push(payload);
          await patchSettings?.(payload);
          durable = { ...durable, ...payload.appearance };
          return { appearance: { ...durable } };
        },
      },
    },
    "./registry": {
      DEFAULT_APPEARANCE: defaults,
      THEME_PACKS: [],
      applyAppearancePreferences: () => {},
      normalizeAppearancePreferences: (value) => ({ ...defaults, ...value }),
    },
  });
  return {
    store: module.useAppearanceStore,
    requests,
    cache,
    dispose() {
      if (previous === undefined) delete globalThis.localStorage;
      else globalThis.localStorage = previous;
    },
  };
}

test("appearance save failure restores confirmed settings and manual retry commits new fields durably", async () => {
  let fail = true;
  const h = appearanceStore(async () => {
    if (fail) throw new Error("合成保存失败");
  });
  try {
    await h.store.getState().initialize();
    await assert.rejects(
      h.store
        .getState()
        .update({ avatar_frame_id: "woven-knot", card_border: true }),
      /合成保存失败/,
    );
    assert.equal(h.store.getState().preferences.avatar_frame_id, "iris-wreath");
    assert.equal(h.store.getState().preferences.card_border, false);
    assert.match(h.store.getState().error, /外观未保存/);
    assert.equal(h.store.getState().saving, false);
    fail = false;
    await h.store
      .getState()
      .update({
        avatar_frame_id: "woven-knot",
        card_border: true,
        reading_width: "wide",
        reading_font_size: 24,
      });
    assert.equal(h.store.getState().preferences.avatar_frame_id, "woven-knot");
    assert.equal(h.store.getState().preferences.reading_font_size, 24);
    assert.equal(h.store.getState().error, null);
    assert.deepEqual(h.requests[1].appearance, {
      avatar_frame_id: "woven-knot",
      card_border: true,
      reading_width: "wide",
      reading_font_size: 24,
    });
    assert.equal(
      JSON.parse(h.cache.get("mrp.appearance")).reading_width,
      "wide",
    );
  } finally {
    h.dispose();
  }
});

test("queued preference failure does not roll back a later successful independent field", async () => {
  let calls = 0;
  const h = appearanceStore(async () => {
    if (++calls === 1) throw new Error("首个修改失败");
  });
  try {
    await h.store.getState().initialize();
    const results = await Promise.allSettled([
      h.store.getState().update({ avatar_frame_id: "moon-silver" }),
      h.store.getState().update({ dialogue_avatar_size: 56 }),
    ]);
    assert.deepEqual(
      results.map((result) => result.status),
      ["rejected", "fulfilled"],
    );
    assert.equal(h.store.getState().preferences.avatar_frame_id, "iris-wreath");
    assert.equal(h.store.getState().preferences.dialogue_avatar_size, 56);
    assert.deepEqual(
      h.requests.map((item) => item.appearance),
      [{ avatar_frame_id: "moon-silver" }, { dialogue_avatar_size: 56 }],
    );
  } finally {
    h.dispose();
  }
});

test("all eleven retained theme packs remain distinct selectable palettes", () => {
  const ids = [
    "light",
    "dark",
    "sakura",
    "forest",
    "midnight",
    "lavender",
    "astral",
    "iris-light",
    "iris-night",
    "rose-night",
    "amber-night",
  ];
  const packs = ids.map((id) =>
    JSON.parse(
      readFileSync(
        new URL(`../src/appearance/packs/${id}.json`, import.meta.url),
        "utf8",
      ),
    ),
  );
  assert.equal(new Set(packs.map((pack) => pack.id)).size, 11);
  assert.ok(packs.every((pack) => pack.name && pack.preview.length > 0));
});
