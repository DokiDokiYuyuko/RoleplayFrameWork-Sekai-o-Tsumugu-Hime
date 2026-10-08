import { applyThemePack, getThemePack, THEME_PACKS } from './appearance/registry';

/** Compatibility view for callers of the previous theme helper. */
export const APP_THEMES = THEME_PACKS.map(({ id, name, note, preview }) => ({ id, name, note, preview }));
export type AppTheme = string;

export function resolveAppTheme(value: string | null | undefined): AppTheme {
  return getThemePack(value).id;
}

export function applyAppTheme(value: string | null | undefined): AppTheme {
  return applyThemePack(value).id;
}
