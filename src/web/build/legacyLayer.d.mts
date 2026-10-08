import type { Plugin } from 'postcss';
export const LEGACY_LAYER: string;
export function readLegacyManifest(file?: string): string[];
export function legacyLayer(options?: { manifest?: string[]; root?: string }): Plugin;
