import type { CSSProperties } from "react";
import type { ThemePack } from "../registry";
import { onDangerColor } from "./onDanger";

/** Palette variables for one theme card, so its sample button matches that pack. */
export function themeSampleStyle(pack: ThemePack): CSSProperties {
  const palette = pack.palette;
  return {
    background: palette.surface,
    color: palette.text,
    colorScheme: pack.color_scheme,
    "--paper": palette.page,
    "--surface": palette.surface,
    "--surface-soft": palette.surfaceSoft,
    "--soft": palette.surfaceSoft,
    "--ink": palette.text,
    "--muted": palette.textMuted,
    "--line": palette.border,
    "--sea": palette.primary,
    "--sea-dark": palette.primaryHover,
    "--sea-deep": palette.primaryHover,
    "--sea-button": palette.primaryButton,
    "--sea-button-hover": palette.primaryButtonHover,
    "--on-sea-button": palette.onPrimary,
    "--sea-soft": palette.primarySoft,
    "--gold": palette.accent,
    "--accent-gold": palette.accent,
    "--amber": palette.accent,
    "--danger": palette.danger,
    "--danger-soft": palette.dangerSoft,
    "--on-danger": onDangerColor(palette.danger),
    "--focus": palette.focus,
  } as CSSProperties;
}
