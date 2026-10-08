import { isCancelledError } from '@tanstack/react-query';
import { createQueryFacade } from '../features/resources/queryFacade';
import { resourceQueries } from '../features/resources/resourceQueries';
import { queryClient } from '../queryClient';
import { api } from '../api/client';
import type { Settings, SettingsPatch } from '../types';

/** 全局推理设置；patch 用返回的全量配置刷新。 */
interface SettingsState {
  settings: Settings | null;
  load: () => Promise<void>;
  patch: (patch: SettingsPatch) => Promise<void>;
}

export const useSettingsStore = createQueryFacade<SettingsState, Settings>('settings', resourceQueries.settings(), () => ({
  settings: null,

  load: async () => {
    try { await queryClient.fetchQuery({ ...resourceQueries.settings(), staleTime: 0 }); } catch (cause) { if (!isCancelledError(cause)) throw cause; }
  },

  patch: async (patch) => {
    await api.patchSettings(patch);
  },
}));
