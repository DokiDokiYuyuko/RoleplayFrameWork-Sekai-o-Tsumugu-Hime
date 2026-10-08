/**
 * Colour pack -> semantic colour tokens of the design system
 * (document/design/界面设计系统规格.md section 3.3 plus the decoration tokens).
 *
 * Pure: no DOM, no React. `applyThemePack` writes the result to the root.
 * A pack may carry explicit `semantic` overrides (token name without `--`);
 * iris-light and iris-night supply the approved values that way. Every other
 * pack is derived from its 26-key palette: direct mapping where a role exists,
 * computed mixes where it does not, then a contrast pass so text roles reach
 * 4.5:1 and outlines / focus reach 3:1 on the surfaces they sit on.
 */

export interface Rgba {
  r: number;
  g: number;
  b: number;
  a: number;
}

export interface SemanticSource {
  color_scheme: "light" | "dark";
  palette: object;
  semantic?: Record<string, string>;
}

/** Colour tokens this module owns. The static tokens live in design-system/tokens.css. */
export const SEMANTIC_TOKEN_NAMES = [
  "bg-page", "bg-shell", "surface-1", "surface-2", "surface-3", "surface-reading",
  "border-subtle", "border-default", "border-strong", "border-strong-hover",
  "text-1", "text-2", "text-3",
  "accent", "accent-soft", "on-accent",
  "region-story", "region-story-soft", "region-character", "region-character-soft",
  "gold-1", "gold-2", "gold-3", "gold-border", "gold-text", "gold-highlight",
  "danger", "danger-soft", "success", "success-soft", "warning", "warning-soft", "focus",
  "overlay-hover", "overlay-active", "scrim", "wash-a", "wash-b", "weave-line",
  "nav-moon", "nav-top", "nav-bottom", "nav-halo", "glow-iris",
  "gold-hair", "gold-glow", "gold-shade", "glass-hi", "glass-lo", "glass-tint",
  "shadow-e1", "shadow-e2", "shadow-e3",
] as const;

export type SemanticTokenName = (typeof SEMANTIC_TOKEN_NAMES)[number];

/**
 * Same name, same meaning as a legacy variable written from the palette.
 * The legacy aliases of these palette keys take their value from here so
 * there is one source (a contrast fix is visible to old and new CSS alike).
 */
export const SHARED_WITH_LEGACY: Record<string, string> = {
  danger: "danger",
  dangerSoft: "danger-soft",
  success: "success",
  successSoft: "success-soft",
  warningSoft: "warning-soft",
  focus: "focus",
};

const WHITE: Rgba = { r: 255, g: 255, b: 255, a: 1 };
const BLACK: Rgba = { r: 0, g: 0, b: 0, a: 1 };

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

function splitArguments(text: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = "";
  for (const char of text) {
    if (char === "(") depth += 1;
    if (char === ")") depth -= 1;
    if (char === "," && depth === 0) {
      parts.push(current.trim());
      current = "";
    } else current += char;
  }
  parts.push(current.trim());
  return parts;
}

/**
 * Hex, rgb()/rgba(), var(--x) from `scope`, and `color-mix(in srgb, A p%, B)`
 * (the forms the packs use). Returns null for anything else.
 */
export function parseColor(value: string, scope: Record<string, string> = {}): Rgba | null {
  const text = value.trim();
  const hex = /^#([0-9a-f]{3,8})$/i.exec(text);
  if (hex) {
    let digits = hex[1];
    if (digits.length === 3 || digits.length === 4) digits = [...digits].map((d) => d + d).join("");
    if (digits.length !== 6 && digits.length !== 8) return null;
    const number = (from: number) => Number.parseInt(digits.slice(from, from + 2), 16);
    return { r: number(0), g: number(2), b: number(4), a: digits.length === 8 ? number(6) / 255 : 1 };
  }
  const rgb = /^rgba?\(([^)]+)\)$/i.exec(text);
  if (rgb) {
    const parts = rgb[1].split(/[\s,/]+/).filter(Boolean);
    if (parts.length < 3) return null;
    const channel = (part: string) => (part.endsWith("%") ? (Number.parseFloat(part) / 100) * 255 : Number.parseFloat(part));
    const alpha = parts[3] === undefined ? 1 : parts[3].endsWith("%") ? Number.parseFloat(parts[3]) / 100 : Number.parseFloat(parts[3]);
    const color = { r: channel(parts[0]), g: channel(parts[1]), b: channel(parts[2]), a: alpha };
    return Object.values(color).every(Number.isFinite) ? color : null;
  }
  const variable = /^var\(\s*(--[\w-]+)\s*(?:,(.*))?\)$/i.exec(text);
  if (variable) {
    const found = scope[variable[1]] ?? variable[2];
    return found === undefined ? null : parseColor(found, scope);
  }
  const mixed = /^color-mix\((.*)\)$/i.exec(text);
  if (mixed) {
    const [space, first, second] = splitArguments(mixed[1]);
    if (!/^in\s+srgb$/i.test(space ?? "") || !first || !second) return null;
    const operand = (raw: string) => {
      const match = /^(.*?)(?:\s+(\d+(?:\.\d+)?)%)?$/.exec(raw.trim());
      return { color: parseColor(match?.[1] ?? "", scope), share: match?.[2] === undefined ? undefined : Number(match[2]) / 100 };
    };
    const a = operand(first);
    const b = operand(second);
    if (!a.color || !b.color) return null;
    const share = a.share ?? (b.share === undefined ? 0.5 : 1 - b.share);
    return mix(b.color, a.color, share);
  }
  return null;
}

/** `amount` of `to` over `from`, per channel. */
export function mix(from: Rgba, to: Rgba, amount: number): Rgba {
  const t = clamp(amount, 0, 1);
  return {
    r: from.r + (to.r - from.r) * t,
    g: from.g + (to.g - from.g) * t,
    b: from.b + (to.b - from.b) * t,
    a: from.a + (to.a - from.a) * t,
  };
}

function channelHex(value: number): string {
  return Math.round(clamp(value, 0, 255)).toString(16).padStart(2, "0");
}

export function toHex(color: Rgba): string {
  return `#${channelHex(color.r)}${channelHex(color.g)}${channelHex(color.b)}`.toUpperCase();
}

function alphaText(value: number): string {
  return String(Number(clamp(value, 0, 1).toFixed(2)));
}

export function withAlpha(color: Rgba, alpha: number): string {
  return `rgba(${Math.round(color.r)}, ${Math.round(color.g)}, ${Math.round(color.b)}, ${alphaText(alpha)})`;
}

function luminance(color: Rgba): number {
  const linear = (channel: number) => {
    const value = channel / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * linear(color.r) + 0.7152 * linear(color.g) + 0.0722 * linear(color.b);
}

/** WCAG contrast ratio of two opaque colours. */
export function contrastRatio(a: Rgba, b: Rgba): number {
  const first = luminance(a);
  const second = luminance(b);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

/**
 * Smallest step from `color` towards `toward` that reaches `minimum` on every
 * background. Returns the colour unchanged when it already passes.
 */
function ensureContrast(color: Rgba, backgrounds: Rgba[], minimum: number, toward: Rgba): Rgba {
  if (backgrounds.every((background) => contrastRatio(color, background) >= minimum)) return color;
  for (let step = 1; step <= 50; step += 1) {
    const candidate = mix(color, toward, step / 50);
    if (backgrounds.every((background) => contrastRatio(candidate, background) >= minimum)) return candidate;
  }
  return toward;
}

// Constants of the approved look: champagne gold, status hues, region hues.
const BASES = {
  light: {
    gold: ["#F6E3B8", "#E3C385", "#C9A265"],
    goldBorder: "#B08D52",
    goldText: "#3B291A",
    goldHighlight: "rgba(255, 255, 255, 0.65)",
    goldShade: "rgba(120, 78, 22, 0.30)",
    warning: "#8A5A12",
    story: "#23708A",
    character: "#94476A",
  },
  dark: {
    gold: ["#F0D9A8", "#D6B373", "#BC955A"],
    goldBorder: "#A8854C",
    goldText: "#271D12",
    goldHighlight: "rgba(255, 255, 255, 0.38)",
    goldShade: "rgba(60, 36, 8, 0.45)",
    warning: "#E8C77E",
    story: "#8ECDDD",
    character: "#E6A9C4",
  },
} as const;

function required(value: string | null, label: string): Rgba {
  const color = value === null ? null : parseColor(value);
  if (!color) throw new Error(`Cannot read ${label}`);
  return color;
}

export function deriveSemanticTokens(source: SemanticSource): Record<string, string> {
  const dark = source.color_scheme === "dark";
  const base = BASES[dark ? "dark" : "light"];
  const palette = source.palette as Record<string, string>;
  const scope = { "--surface": palette.surface, "--paper": palette.page };
  const read = (key: string): Rgba => {
    const color = parseColor(palette[key] ?? "", scope);
    if (!color) throw new Error(`Cannot read palette.${key}`);
    return color;
  };
  const hex = (value: string) => required(value, value);
  const toward = dark ? WHITE : BLACK;

  const page = read("page");
  const surface = read("surface");
  const soft = read("surfaceSoft");
  const text = read("text");
  const muted = read("textMuted");
  const border = read("border");

  // Surfaces
  const shell = mix(page, surface, 0.5);
  const surface3 = dark ? mix(surface, text, 0.06) : surface;
  const reading = mix(page, surface, 0.4);
  const accentSoft = read("primarySoft");
  const surfaces = [surface, soft, page, reading, surface3, shell];

  // Region colours: fixed hues, softened into the pack surface.
  const regionSoft = (hue: Rgba) => mix(surface, hue, dark ? 0.2 : 0.1);
  const storyHue = hex(base.story);
  const characterHue = hex(base.character);
  const storySoft = regionSoft(storyHue);
  const characterSoft = regionSoft(characterHue);

  // Navigation gradient ends
  const navTop = dark ? mix(accentSoft, shell, 0.75) : accentSoft;
  const navBottom = dark ? mix(page, BLACK, 0.1) : mix(accentSoft, shell, 0.5);

  const textBackgrounds = [...surfaces, accentSoft, storySoft, characterSoft, navTop, navBottom];
  const text1 = ensureContrast(text, textBackgrounds, 4.5, toward);
  const text2 = ensureContrast(mix(text1, muted, 0.5), textBackgrounds, 4.7, toward);
  const text3 = ensureContrast(muted, textBackgrounds, 4.6, toward);

  const accent = ensureContrast(read("primary"), [...surfaces, accentSoft, navTop, navBottom], 4.5, toward);
  const lightEnd = hex("#FFFFFF");
  const darkEnd = [page, surface, text].reduce((a, b) => (luminance(a) <= luminance(b) ? a : b));
  const onAccent = contrastRatio(lightEnd, accent) >= 4.5 ? lightEnd : darkEnd;

  // Outlines: pack border towards text until 3:1 holds on every surface it sits on.
  const outlineBackgrounds = [...surfaces, accentSoft];
  let borderStrong = border;
  for (let step = 0; step <= 50; step += 1) {
    borderStrong = mix(border, text1, step / 50);
    if (outlineBackgrounds.every((background) => contrastRatio(borderStrong, background) >= 3.15)) break;
  }
  const borderStrongHover = ensureContrast(mix(borderStrong, text1, 0.22), outlineBackgrounds, 3.15, text1);

  const region = (hue: Rgba, fill: Rgba) =>
    ensureContrast(mix(hue, accent, 0.15), [surface, reading, fill, page, soft], 4.5, toward);
  const storyRegion = region(storyHue, storySoft);
  const characterRegion = region(characterHue, characterSoft);

  // Gold stays champagne in every pack; a small share of the pack accent tints it.
  const accentTint = read("accent");
  const gold = base.gold.map((value) => mix(hex(value), accentTint, 0.1));
  const goldText = ensureContrast(hex(base.goldText), gold, 4.5, BLACK);
  const goldBorder = mix(hex(base.goldBorder), accentTint, 0.1);

  // Status: the pack's own hue, adjusted only when it misses 4.5:1.
  const shared: Record<string, string> = {};
  const status = (key: string, softKey: string) => {
    const fill = read(softKey);
    const original = read(key);
    const adjusted = ensureContrast(original, [surface, soft, surface3, page, fill], 4.5, toward);
    const unchanged = adjusted === original;
    return { fill, color: adjusted, rawColor: unchanged ? palette[key] : undefined, rawFill: palette[softKey] };
  };
  const danger = status("danger", "dangerSoft");
  const success = status("success", "successSoft");
  const warningFill = read("warningSoft");
  const warning = ensureContrast(hex(base.warning), [surface, soft, surface3, page, warningFill], 4.5, toward);
  const focusOriginal = read("focus");
  const focus = ensureContrast(focusOriginal, [...surfaces, accentSoft], 3.15, toward);
  shared["--danger"] = danger.rawColor ?? toHex(danger.color);
  shared["--danger-soft"] = danger.rawFill;
  shared["--success"] = success.rawColor ?? toHex(success.color);
  shared["--success-soft"] = success.rawFill;
  shared["--warning-soft"] = palette.warningSoft;
  shared["--focus"] = focus === focusOriginal ? palette.focus : toHex(focus);

  const shadowInk = dark ? BLACK : text1;
  const shadowAlpha = dark ? [0.3, 0.38, 0.58] : [0.06, 0.1, 0.22];
  const shadow = (y: number, blur: number, alpha: number) => `0 ${y}px ${blur}px ${withAlpha(shadowInk, alpha)}`;

  const tokens: Record<string, string> = {
    "--bg-page": toHex(page),
    "--bg-shell": toHex(shell),
    "--surface-1": toHex(surface),
    "--surface-2": toHex(soft),
    "--surface-3": toHex(surface3),
    "--surface-reading": toHex(reading),
    "--border-subtle": toHex(mix(border, surface, 0.5)),
    "--border-default": toHex(border),
    "--border-strong": toHex(borderStrong),
    "--border-strong-hover": toHex(borderStrongHover),
    "--text-1": toHex(text1),
    "--text-2": toHex(text2),
    "--text-3": toHex(text3),
    "--accent": toHex(accent),
    "--accent-soft": toHex(accentSoft),
    "--on-accent": toHex(onAccent),
    "--region-story": toHex(storyRegion),
    "--region-story-soft": toHex(storySoft),
    "--region-character": toHex(characterRegion),
    "--region-character-soft": toHex(characterSoft),
    "--gold-1": toHex(gold[0]),
    "--gold-2": toHex(gold[1]),
    "--gold-3": toHex(gold[2]),
    "--gold-border": toHex(goldBorder),
    "--gold-text": toHex(goldText),
    "--gold-highlight": base.goldHighlight,
    "--danger": shared["--danger"],
    "--danger-soft": shared["--danger-soft"],
    "--success": shared["--success"],
    "--success-soft": shared["--success-soft"],
    "--warning": toHex(warning),
    "--warning-soft": shared["--warning-soft"],
    "--focus": shared["--focus"],
    "--overlay-hover": withAlpha(text1, dark ? 0.07 : 0.06),
    "--overlay-active": withAlpha(text1, dark ? 0.13 : 0.11),
    "--scrim": dark ? "rgba(8, 8, 14, 0.62)" : withAlpha(text1, 0.42),
    "--wash-a": withAlpha(accent, dark ? 0.1 : 0.09),
    "--wash-b": withAlpha(storyRegion, dark ? 0.06 : 0.07),
    "--weave-line": withAlpha(accent, dark ? 0.07 : 0.06),
    "--nav-moon": withAlpha(dark ? gold[1] : gold[2], dark ? 0.1 : 0.16),
    "--nav-top": toHex(navTop),
    "--nav-bottom": toHex(navBottom),
    "--nav-halo": withAlpha(gold[1], dark ? 0.26 : 0.42),
    "--glow-iris": withAlpha(accent, dark ? 0.26 : 0.3),
    "--gold-hair": withAlpha(dark ? gold[1] : goldBorder, 0.4),
    "--gold-glow": withAlpha(gold[1], dark ? 0.34 : 0.62),
    "--gold-shade": base.goldShade,
    "--glass-hi": dark ? withAlpha(mix(surface, accent, 0.12), 0.78) : withAlpha(surface, 0.8),
    "--glass-lo": withAlpha(shell, dark ? 0.66 : 0.64),
    "--glass-tint": dark ? toHex(mix(page, accent, 0.8)) : toHex(surface),
    "--shadow-e1": shadow(1, 2, shadowAlpha[0]),
    "--shadow-e2": shadow(4, 16, shadowAlpha[1]),
    "--shadow-e3": shadow(16, 48, shadowAlpha[2]),
  };

  for (const [name, value] of Object.entries(source.semantic ?? {})) {
    if ((SEMANTIC_TOKEN_NAMES as readonly string[]).includes(name) && typeof value === "string") tokens[`--${name}`] = value;
  }
  return tokens;
}

/** Names whose value can be parsed to a colour, for the contrast tests. */
export function resolveTokenColor(tokens: Record<string, string>, name: string): Rgba {
  const color = parseColor(tokens[`--${name}`] ?? "", { "--surface": tokens["--surface-1"] });
  if (!color) throw new Error(`Cannot read ${name}`);
  return color;
}
