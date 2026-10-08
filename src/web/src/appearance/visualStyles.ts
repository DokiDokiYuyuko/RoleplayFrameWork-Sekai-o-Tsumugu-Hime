import type { AppearanceOption } from "./registry";
import buttonCatalog from "./buttons/catalog.json";

/**
 * Visual silhouettes are independent from ThemePack palettes.
 * Style ids, names and frame kinds come from buttons/catalog.json, which the
 * server reads as well. Preview colors stay here because they are paint, not
 * a stored preference. Add a catalog row and a preview to ship another style
 * on an existing frame. A new frame also needs a block in buttons/buttons.css.
 */
export interface VisualStylePack extends AppearanceOption {
  frame: string;
  preview: {
    page: string;
    panel: string;
    accent: string;
    ornament: string;
  };
}

const PREVIEWS: Record<string, VisualStylePack["preview"]> = {
  picturebook: { page: "radial-gradient(ellipse at 15% 0%,#ffffff,#eee6fc 48%,#d6c4f1)", panel: "linear-gradient(145deg,#ffffffed,#f7f0ffe0 54%,#e8e0f8e6)", accent: "#7151a8", ornament: "☾" },
  "celestial-atelier": {
    page: "radial-gradient(ellipse at 12% 0%,#8798bf 0%,#1a263b 68%)",
    panel: "linear-gradient(145deg,#33445f,#202c42)",
    accent: "#d6bc89",
    ornament: "✧",
  },
  "ink-scroll": {
    page: "linear-gradient(160deg,#e9e4d8,#c8cfbd)",
    panel: "#f9f7ee",
    accent: "#6c775f",
    ornament: "山",
  },
  prism: {
    page: "linear-gradient(145deg,#b8dbea,#cebfef)",
    panel: "#f0f7ffb3",
    accent: "#6173a9",
    ornament: "◇",
  },
  starship: {
    page: "linear-gradient(145deg,#172b3d,#0b1828)",
    panel: "#1e3b4f",
    accent: "#81cecc",
    ornament: "⌖",
  },
  "quiet-study": {
    page: "#e8e1d5",
    panel: "#fcf9f2",
    accent: "#987456",
    ornament: "―",
  },
};

const fallbackPreview: VisualStylePack["preview"] = {
  page: "var(--paper)",
  panel: "var(--surface)",
  accent: "var(--gold)",
  ornament: "·",
};

export const VISUAL_STYLE_PACKS: VisualStylePack[] = buttonCatalog.styles.map(
  (style) => ({
    id: style.id,
    name: style.name,
    note: style.note,
    frame: style.frame,
    preview: PREVIEWS[style.id] ?? fallbackPreview,
  }),
);

export function resolveVisualStyle(id?: string | null): VisualStylePack {
  return (
    VISUAL_STYLE_PACKS.find((style) => style.id === id) ??
    VISUAL_STYLE_PACKS.find((style) => style.id === "celestial-atelier") ??
    VISUAL_STYLE_PACKS[0]
  );
}
