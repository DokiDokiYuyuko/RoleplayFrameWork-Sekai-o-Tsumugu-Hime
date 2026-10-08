import type { AppearancePreferences } from "../types";
import { resolveBubbleStyle } from "./bubbleStyles";
import { onDangerColor } from "./buttons/onDanger";
import { SEMANTIC_TOKEN_NAMES, SHARED_WITH_LEGACY, deriveSemanticTokens } from "./semanticTokens";
import { resolveVisualStyle } from "./visualStyles";

export interface AppearanceOption {
  id: string;
  name: string;
  note: string;
}

export interface ThemePalette {
  page: string;
  surface: string;
  surfaceSoft: string;
  text: string;
  textMuted: string;
  border: string;
  primary: string;
  primaryHover: string;
  primaryButton: string;
  primaryButtonHover: string;
  onPrimary: string;
  primarySoft: string;
  accent: string;
  secondary: string;
  nav: string;
  navText: string;
  navMuted: string;
  rail: string;
  focus: string;
  danger: string;
  dangerSoft: string;
  success: string;
  successSoft: string;
  warningSoft: string;
  brandStart: string;
  brandEnd: string;
}

export interface ThemePack extends AppearanceOption {
  version: 1;
  order: number;
  color_scheme: "light" | "dark";
  preview: string[];
  palette: ThemePalette;
  /** Optional explicit design-system colour tokens (name without `--`); the rest are derived. */
  semantic?: Record<string, string>;
  shadows: { panel: string; card: string };
  assets?: {
    nav_art?: string;
    story_cover_forest?: string;
    story_cover_ocean?: string;
    story_cover_moon?: string;
  };
}

const safeId = /^[a-z][a-z0-9-]{0,63}$/;
const packs = import.meta.glob<ThemePack>("./packs/*.json", {
  eager: true,
  import: "default",
});
const paletteKeys: (keyof ThemePalette)[] = [
  "page",
  "surface",
  "surfaceSoft",
  "text",
  "textMuted",
  "border",
  "primary",
  "primaryHover",
  "primaryButton",
  "primaryButtonHover",
  "onPrimary",
  "primarySoft",
  "accent",
  "secondary",
  "nav",
  "navText",
  "navMuted",
  "rail",
  "focus",
  "danger",
  "dangerSoft",
  "success",
  "successSoft",
  "warningSoft",
  "brandStart",
  "brandEnd",
];

/** Adding a JSON pack here is enough to make it available in the appearance picker. */
export const THEME_PACKS: ThemePack[] = Object.values(packs)
  .filter(
    (pack) =>
      pack &&
      pack.version === 1 &&
      typeof pack.id === "string" &&
      safeId.test(pack.id) &&
      pack.id === pack.id.trim() &&
      typeof pack.name === "string" &&
      typeof pack.note === "string" &&
      Array.isArray(pack.preview) &&
      pack.preview.every((color) => typeof color === "string") &&
      Number.isFinite(pack.order) &&
      (pack.color_scheme === "light" || pack.color_scheme === "dark") &&
      paletteKeys.every((key) => typeof pack.palette?.[key] === "string") &&
      typeof pack.shadows?.panel === "string" &&
      typeof pack.shadows?.card === "string",
  )
  .sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));

export const TYPOGRAPHY_OPTIONS = [
  {
    id: "picturebook",
    name: "月光手稿",
    note: "小薇标题，故事、界面与编辑使用清晰黑体",
    display: '"Sekai XiaoWei", "Songti SC", "SimSun", serif',
    body: 'system-ui, "PingFang SC", "Microsoft YaHei", sans-serif',
    story: 'system-ui, "PingFang SC", "Microsoft YaHei", sans-serif',
  },
  {
    id: "mincho",
    name: "明朝书卷",
    note: "本机宋体与明朝体，带一点故事书的气息",
    display:
      '"Yu Mincho", "Hiragino Mincho ProN", "Songti SC", "Noto Serif CJK SC", "SimSun", serif',
    body: '"Songti SC", "Noto Serif CJK SC", "Yu Mincho", "SimSun", serif',
  },
  {
    id: "system",
    name: "系统字体",
    note: "清晰利落，使用设备自带字体",
    display: 'system-ui, "PingFang SC", "Microsoft YaHei", sans-serif',
    body: 'system-ui, "PingFang SC", "Microsoft YaHei", sans-serif',
  },
  {
    id: "wenkai",
    name: "霞鹜文楷",
    note: "温润的手写楷书，适合长篇阅读 · 本地字体",
    display: '"Sekai WenKai", serif',
    body: '"Sekai WenKai", "Microsoft YaHei", serif',
  },
  {
    id: "xiaowei",
    name: "站酷小薇",
    note: "清秀的宋体，像翻开一本轻巧的小说 · 本地字体",
    display: '"Sekai XiaoWei", serif',
    body: '"Sekai XiaoWei", "SimSun", serif',
  },
  {
    id: "huangyou",
    name: "庆科黄油",
    note: "圆润醒目的标题，正文使用系统字体 · 本地字体",
    display: '"Sekai HuangYou", sans-serif',
    body: 'system-ui, "PingFang SC", "Microsoft YaHei", sans-serif',
  },
  {
    id: "mashan",
    name: "毛笔楷书",
    note: "笔墨感标题，正文使用文楷保持清晰 · 本地字体",
    display: '"Sekai MaShan", serif',
    body: '"Sekai WenKai", "Microsoft YaHei", serif',
  },
] as const;
export type OrnamentLevel = "none" | "subtle" | "rich";
/** Ornament intensity of the design system; stored in `decoration_id`. */
export const ORNAMENT_OPTIONS: (AppearanceOption & { id: OrnamentLevel })[] = [
  { id: "none", name: "关", note: "只保留结构，不画装饰" },
  { id: "subtle", name: "素雅", note: "淡底纹、短织线与小角花" },
  { id: "rich", name: "华丽", note: "徽记题牌、大角花、玻璃与金线" },
];
/** The stored legacy value `celestial` is the 华丽 level; unknown ids render the default. */
export function resolveOrnament(id?: string | null): OrnamentLevel {
  return id === "none" || id === "subtle" ? id : "rich";
}
/** Unmigrated pages still read `data-decoration`, which only knows on and off. */
function legacyDecoration(id?: string | null): "celestial" | "none" {
  return id === "none" ? "none" : "celestial";
}
/** Until every page is migrated the legacy stylesheets render as this one style whatever is stored. */
const LEGACY_RENDER_STYLE = "picturebook";
export const CURSOR_OPTIONS: AppearanceOption[] = [
  { id: "system", name: "系统指针", note: "使用设备原有指针" },
  {
    id: "star",
    name: "星芒指针",
    note: "带星饰的原生指针，箭头与手形保持准确点击位置",
  },
];
export const TRAIL_OPTIONS: AppearanceOption[] = [
  { id: "iridescent", name: "虹彩星尘", note: "柔和的虹彩光点随移动淡去" },
  { id: "starlight", name: "金色星光", note: "稀疏的金色星点" },
  { id: "comet", name: "彗星微光", note: "细长光迹，末端有一颗小星" },
  { id: "petals", name: "落樱花瓣", note: "轻旋下落的淡粉花瓣" },
  { id: "fireflies", name: "萤火浮游", note: "缓缓散去的暖色萤光" },
  { id: "aurora", name: "极光丝带", note: "三缕轻柔交织的光带" },
  { id: "ink", name: "水墨余韵", note: "随移动淡去的细小墨点" },
  { id: "orbit", name: "星环轨迹", note: "疏落的细环与星芒" },
  { id: "none", name: "关闭拖尾", note: "不显示指针移动特效" },
];

export const CLICK_EFFECT_OPTIONS: AppearanceOption[] = [
  { id: "none", name: "关闭点击特效", note: "保持简净，点击不产生动画" },
  { id: "star-ring", name: "星芒绽放", note: "六颗星围着细金环轻轻散开" },
  { id: "ripple", name: "水面涟漪", note: "两圈清透的圆纹向外扩散" },
  { id: "petals", name: "樱花轻绽", note: "一簇花瓣轻旋散落" },
  { id: "rune", name: "星仪法阵", note: "双层圆环与六角星仪" },
  { id: "burst", name: "流光星火", note: "短小光束从落点散开" },
  { id: "ink", name: "点墨生花", note: "淡淡的墨滴晕开" },
];

export const AVATAR_FRAME_OPTIONS: AppearanceOption[] = [
  { id: "none", name: "无框", note: "只显示头像" }, { id: "ring", name: "细环", note: "轻盈轮廓" },
  ...[["moon-silver", "月相银环"], ["iris-wreath", "鸢尾花环"], ["gilt-enamel", "香槟金珐琅"], ["starburst", "星芒"], ["tide-teal", "青蓝潮纹"], ["rose-vine", "蔷薇藤"], ["silver-feather", "银羽"], ["vine-moon", "藤月"], ["frost-crystal", "霜晶"], ["woven-knot", "织结"]].map(([id, name]) => ({ id, name, note: "公共头像框 · 密集列表退为细环" })),
];

export const DEFAULT_APPEARANCE: AppearancePreferences = {
  theme_id: "astral",
  typography_id: "mincho",
  decoration_id: "celestial",
  visual_style_id: "celestial-atelier",
  cursor_id: "system",
  trail_id: "iridescent",
  click_effect_id: "none",
  effect_intensity: 0.65,
  density: "comfortable",
  bubble_style_id: "star-track",
  avatar_frame_id: "iris-wreath", dialogue_avatar_size: 40,
  primary_button_skin: true, secondary_button_skin: false,
  card_ornaments: true, card_border: false, background_art: true,
  portrait_placeholder: "art", reading_width: "standard", reading_font_size: 18,
};

function safePreferenceId(value: unknown, fallback: string): string {
  return typeof value === "string" &&
    safeId.test(value) &&
    value === value.trim()
    ? value
    : fallback;
}

/** Retain safe unknown IDs in storage; only the rendering layer falls back. */
export function normalizeAppearancePreferences(
  value: unknown,
): AppearancePreferences {
  const input =
    value && typeof value === "object"
      ? (value as Partial<AppearancePreferences>)
      : {};
  return {
    theme_id: safePreferenceId(input.theme_id, DEFAULT_APPEARANCE.theme_id),
    visual_style_id: safePreferenceId(
      input.visual_style_id,
      DEFAULT_APPEARANCE.visual_style_id,
    ),
    typography_id: safePreferenceId(
      input.typography_id,
      DEFAULT_APPEARANCE.typography_id,
    ),
    decoration_id: safePreferenceId(
      input.decoration_id,
      DEFAULT_APPEARANCE.decoration_id,
    ),
    cursor_id: safePreferenceId(input.cursor_id, DEFAULT_APPEARANCE.cursor_id),
    trail_id: safePreferenceId(input.trail_id, DEFAULT_APPEARANCE.trail_id),
    click_effect_id: safePreferenceId(
      input.click_effect_id,
      DEFAULT_APPEARANCE.click_effect_id,
    ),
    bubble_style_id: safePreferenceId(
      input.bubble_style_id,
      DEFAULT_APPEARANCE.bubble_style_id,
    ),
    effect_intensity:
      typeof input.effect_intensity === "number" &&
      Number.isFinite(input.effect_intensity)
        ? Math.max(0, Math.min(1, input.effect_intensity))
        : DEFAULT_APPEARANCE.effect_intensity,
    reading_width: input.reading_width === "narrow" || input.reading_width === "wide" ? input.reading_width : "standard",
    reading_font_size: typeof input.reading_font_size === "number" && Number.isFinite(input.reading_font_size) ? Math.max(14, Math.min(24, input.reading_font_size)) : 18,
    avatar_frame_id: safePreferenceId(input.avatar_frame_id, "iris-wreath"),
    dialogue_avatar_size: input.dialogue_avatar_size === 32 || input.dialogue_avatar_size === 56 ? input.dialogue_avatar_size : 40,
    primary_button_skin: input.primary_button_skin !== false,
    secondary_button_skin: input.secondary_button_skin === true,
    card_ornaments: input.card_ornaments !== false,
    card_border: input.card_border === true,
    background_art: input.background_art !== false,
    portrait_placeholder: input.portrait_placeholder === "initial" ? "initial" : "art",
    density: input.density === "compact" ? "compact" : "comfortable",
  };
}

export function resolveAppearanceOption<T extends AppearanceOption>(
  options: readonly T[],
  id: string | undefined,
  fallback: string,
): T {
  return (
    options.find((option) => option.id === id) ??
    options.find((option) => option.id === fallback) ??
    options[0]
  );
}

export function getThemePack(id?: string | null): ThemePack {
  return resolveAppearanceOption(
    THEME_PACKS,
    id ?? undefined,
    DEFAULT_APPEARANCE.theme_id,
  );
}

/** Legacy aliases per palette key. Keys listed in SHARED_WITH_LEGACY take the design-system value. */
const semanticTokens: Record<keyof ThemePalette, string[]> = {
  page: ["--color-page", "--paper"],
  surface: ["--color-surface", "--surface"],
  surfaceSoft: ["--color-surface-soft", "--surface-soft", "--soft"],
  text: ["--color-text", "--ink"],
  textMuted: ["--color-text-muted", "--muted"],
  border: ["--color-border", "--line"],
  primary: ["--color-primary", "--sea"],
  primaryHover: ["--color-primary-hover", "--sea-dark", "--sea-deep"],
  primaryButton: ["--color-primary-button", "--sea-button"],
  primaryButtonHover: ["--color-primary-button-hover", "--sea-button-hover"],
  onPrimary: ["--color-on-primary", "--on-sea-button"],
  primarySoft: ["--color-primary-soft", "--sea-soft"],
  accent: ["--color-accent", "--accent-gold", "--gold", "--amber"],
  secondary: ["--color-secondary", "--lilac"],
  nav: ["--color-nav", "--nav-bg"],
  navText: ["--color-nav-text", "--nav-ink"],
  navMuted: ["--color-nav-muted", "--nav-muted"],
  rail: ["--color-rail", "--rail"],
  focus: ["--color-focus", "--focus"],
  danger: ["--color-danger", "--danger"],
  dangerSoft: ["--color-danger-soft", "--danger-soft"],
  success: ["--color-success", "--success"],
  successSoft: ["--color-success-soft", "--success-soft"],
  warningSoft: ["--color-warning-soft", "--warning-soft"],
  brandStart: ["--brand-start"],
  brandEnd: ["--brand-end"],
};

function localAsset(value?: string): string {
  return value && /^\/[a-zA-Z0-9_./-]+$/.test(value) && !value.includes("..")
    ? `url("${value}")`
    : "none";
}

export function applyThemePack(id?: string | null): ThemePack {
  const pack = getThemePack(id);
  if (typeof document === "undefined") return pack;
  const root = document.documentElement;
  const semantic = deriveSemanticTokens(pack);
  root.dataset.theme = pack.id;
  root.dataset.colorScheme = pack.color_scheme;
  root.style.colorScheme = pack.color_scheme;
  for (const key of paletteKeys) {
    const shared = SHARED_WITH_LEGACY[key];
    const value = shared ? semantic[`--${shared}`] : pack.palette[key];
    for (const token of semanticTokens[key]) root.style.setProperty(token, value);
  }
  for (const name of SEMANTIC_TOKEN_NAMES) root.style.setProperty(`--${name}`, semantic[`--${name}`]);
  root.style.setProperty("--shadow", pack.shadows.panel);
  root.style.setProperty("--shadow-card", pack.shadows.card);
  root.style.setProperty("--on-danger", onDangerColor(semantic["--danger"]));
  root.style.setProperty("--nav-art", localAsset(pack.assets?.nav_art));
  root.style.setProperty(
    "--story-cover-forest",
    localAsset(pack.assets?.story_cover_forest),
  );
  root.style.setProperty(
    "--story-cover-ocean",
    localAsset(pack.assets?.story_cover_ocean),
  );
  root.style.setProperty(
    "--story-cover-moon",
    localAsset(pack.assets?.story_cover_moon),
  );
  return pack;
}

export function applyAppearancePreferences(
  value: AppearancePreferences,
): AppearancePreferences {
  const preferences = normalizeAppearancePreferences(value);
  if (typeof document === "undefined") return preferences;
  const root = document.documentElement;
  applyThemePack(preferences.theme_id);
  const visualStyle = resolveVisualStyle(LEGACY_RENDER_STYLE);
  root.dataset.visualStyle = visualStyle.id;
  root.dataset.prototypeDesign = visualStyle.id;
  root.dataset.buttonFrame = visualStyle.frame;
  root.style.setProperty("--btn-kind", visualStyle.frame);
  const typography = resolveAppearanceOption(
    TYPOGRAPHY_OPTIONS,
    preferences.typography_id,
    "mincho",
  );
  root.dataset.typography = typography.id;
  root.dataset.decoration = legacyDecoration(preferences.decoration_id);
  root.dataset.ornament = resolveOrnament(preferences.decoration_id);
  root.dataset.cursor = resolveAppearanceOption(
    CURSOR_OPTIONS,
    preferences.cursor_id,
    "system",
  ).id;
  root.dataset.trail = resolveAppearanceOption(
    TRAIL_OPTIONS,
    preferences.trail_id,
    "iridescent",
  ).id;
  root.dataset.clickEffect = resolveAppearanceOption(
    CLICK_EFFECT_OPTIONS,
    preferences.click_effect_id,
    "none",
  ).id;
  root.style.setProperty("--reading-measure", preferences.reading_width === "narrow" ? "38em" : preferences.reading_width === "wide" ? "52em" : "44em");
  root.style.setProperty("--reading-size", `${preferences.reading_font_size}px`);
  root.style.setProperty("--reading-lh", "1.85");
  root.dataset.avatarFrame = resolveAppearanceOption(AVATAR_FRAME_OPTIONS, preferences.avatar_frame_id, "iris-wreath").id;
  root.dataset.dialogueAvatarSize = String(preferences.dialogue_avatar_size);
  root.dataset.primaryButtonSkin = String(preferences.primary_button_skin);
  root.dataset.secondaryButtonSkin = String(preferences.secondary_button_skin);
  root.dataset.cardOrnaments = String(preferences.card_ornaments);
  root.dataset.cardBorder = String(preferences.card_border);
  root.dataset.backgroundArt = String(preferences.background_art);
  root.dataset.portraitPlaceholder = preferences.portrait_placeholder ?? "art";
  root.dataset.density = preferences.density;
  root.dataset.bubbleStyle = resolveBubbleStyle(preferences.bubble_style_id).id;
  root.style.setProperty("--font-display", typography.display);
  root.style.setProperty("--font-body", typography.body);
  root.style.setProperty("--font-story", "story" in typography ? typography.story : typography.body);
  root.style.setProperty("--font-editor", 'system-ui, "PingFang SC", "Microsoft YaHei", sans-serif');
  root.style.setProperty(
    "--font-ui",
    'system-ui, "PingFang SC", "Microsoft YaHei", sans-serif',
  );
  root.style.setProperty(
    "--effect-intensity",
    String(preferences.effect_intensity),
  );
  return preferences;
}
