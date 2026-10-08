import { queryClient } from '../queryClient';
import { resourceQueries } from '../features/resources/resourceQueries';
import { create } from 'zustand';
import { api } from '../api/client';
import type { AppearancePreferences, Settings } from '../types';
import { applyAppearancePreferences, DEFAULT_APPEARANCE, normalizeAppearancePreferences, THEME_PACKS } from './registry';

const CACHE_KEY = 'mrp.appearance';
const MIGRATION_KEY = 'mrp.appearance.legacy-migrated';

function readLocal(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

function writeLocal(key: string, value: string): void {
  try { localStorage.setItem(key, value); } catch { /* Preferences still work when browser storage is unavailable. */ }
}

function readCached(): AppearancePreferences {
  try { return normalizeAppearancePreferences(JSON.parse(readLocal(CACHE_KEY) ?? 'null')); }
  catch { return { ...DEFAULT_APPEARANCE }; }
}

function hasAppearance(settings: Settings): boolean {
  return Object.prototype.hasOwnProperty.call(settings, 'appearance');
}

interface AppearanceState {
  render: (preferences: AppearancePreferences, saving: boolean) => void;
  preferences: AppearancePreferences;
  initialized: boolean;
  saving: boolean;
  error: string | null;
  initialize: () => Promise<void>;
  update: (partial: Partial<AppearancePreferences>) => Promise<void>;
}

type PendingUpdate = { id: number; patch: Partial<AppearancePreferences> };
let confirmed = readCached();
let initialization: Promise<void> | null = null;
let mutationQueue: Promise<void> = Promise.resolve();
let nextUpdateId = 0;
const pending: PendingUpdate[] = [];

function visiblePreferences(): AppearancePreferences {
  let value = { ...confirmed };
  for (const item of pending) value = { ...value, ...item.patch };
  return normalizeAppearancePreferences(value);
}

function renderPreferences(): void {
  const preferences = visiblePreferences();
  applyAppearancePreferences(preferences);
  writeLocal(CACHE_KEY, JSON.stringify(preferences));
  useAppearanceStore.getState().render(preferences, pending.length > 0);
}

function removePending(id: number): void {
  const index = pending.findIndex((item) => item.id === id);
  if (index >= 0) pending.splice(index, 1);
}

export const useAppearanceStore = create<AppearanceState>()((set, get) => ({
  render: (preferences, saving) => set({ preferences, saving }),
  preferences: confirmed,
  initialized: false,
  saving: false,
  error: null,

  initialize: () => {
    if (get().initialized) return Promise.resolve();
    if (initialization) return initialization;
    applyAppearancePreferences(get().preferences);
    set({ error: null });
    initialization = (async () => {
      try {
        const settings = await queryClient.fetchQuery(resourceQueries.settings());

        if (hasAppearance(settings)) {
          // The shared value wins even when it happens to equal the defaults.
          confirmed = normalizeAppearancePreferences(settings.appearance);
          writeLocal(MIGRATION_KEY, '1');
        } else if (readLocal(MIGRATION_KEY) !== '1') {
          const legacy: Partial<AppearancePreferences> = {};
          const theme = readLocal('mrp.v7.theme');
          const density = readLocal('mrp.v7.density');
          if (THEME_PACKS.some((pack) => pack.id === theme)) legacy.theme_id = theme!;
          if (density === 'compact' || density === 'comfortable') legacy.density = density;
          if (Object.keys(legacy).length) {
            const migrated = await api.patchSettings({ appearance: legacy });

            confirmed = hasAppearance(migrated)
              ? normalizeAppearancePreferences(migrated.appearance)
              : normalizeAppearancePreferences({ ...confirmed, ...legacy });
          }
          writeLocal(MIGRATION_KEY, '1');
        }
        renderPreferences();
        set({ initialized: true, error: null });
      } catch (cause) {
        set({ error: cause instanceof Error ? cause.message : '外观设置加载失败，请重试' });
        throw cause;
      } finally {
        initialization = null;
      }
    })();
    return initialization;
  },

  update: async (partial) => {
    // Wait for the authoritative value before constructing a user's first edit.
    await get().initialize();
    const normalized = normalizeAppearancePreferences({ ...get().preferences, ...partial });
    const patch: Partial<AppearancePreferences> = {};
    for (const key of Object.keys(DEFAULT_APPEARANCE) as (keyof AppearancePreferences)[]) {
      if (Object.prototype.hasOwnProperty.call(partial, key)) Object.assign(patch, { [key]: normalized[key] });
    }
    if (!Object.keys(patch).length) return;
    const id = ++nextUpdateId;
    pending.push({ id, patch });
    set({ error: null });
    renderPreferences();
    // Send only edited fields; a palette selected on another device survives a density edit.
    const operation = mutationQueue.then(async () => {
      try {
        const settings = await api.patchSettings({ appearance: patch });
        if (!hasAppearance(settings)) throw new Error('当前服务尚未支持外观保存，请更新服务后重试');
        confirmed = normalizeAppearancePreferences(settings.appearance);

        removePending(id);
        renderPreferences();
      } catch (cause) {
        removePending(id);
        renderPreferences();
        set({ error: cause instanceof Error ? `外观未保存：${cause.message}` : '外观未保存，请重试' });
        throw cause;
      }
    });
    mutationQueue = operation.catch(() => undefined);
    await operation;
  },
}));

export function getAppearancePreferences(): AppearancePreferences {
  return useAppearanceStore.getState().preferences;
}
