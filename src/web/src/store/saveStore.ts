import { createQueryFacade } from '../features/resources/queryFacade';
import { resourceQueries } from '../features/resources/resourceQueries';
import { queryClient } from '../queryClient';
import { api } from '../api/client';
import type { Save } from '../types';
import { useChatStore } from './chatStore';

interface SaveState {
  saves: Save[];
  sessionId: string;
  loadError: string | null;
  restoreNotice: string | null;
  load: (sessionId: string) => Promise<void>;
  create: (sessionId: string, name: string) => Promise<void>;
  restore: (saveId: string) => Promise<void>;
}

export const useSaveStore = createQueryFacade<SaveState, Save[]>('saves', state => resourceQueries.saves(state.sessionId), (set, get) => ({
  saves: [], sessionId: '',
  loadError: null, restoreNotice: null,

  load: async (sessionId) => {
    set({ sessionId });
    try { await queryClient.fetchQuery({ ...resourceQueries.saves(sessionId), staleTime: 0 }); if (get().sessionId === sessionId) set({ loadError: null }); }
    catch (cause) { if (get().sessionId === sessionId) set({ loadError: cause instanceof Error ? cause.message : '存档列表读取失败' }); throw cause; }
  },

  create: async (sessionId, name) => {
    await api.createSave(sessionId, name);
    await get().load(sessionId).catch(() => undefined);
  },

  restore: async (saveId) => {
    const { session, messages, characters, lorebooks, memory_restore_status } = await api.restoreSave(saveId);
    set({ restoreNotice: memory_restore_status === 'unavailable' ? '这个旧存档未包含完整记忆快照，当前记忆已保留。' : null });
    useChatStore.getState().applyRestore(session, messages, characters, lorebooks);
    await Promise.allSettled([get().load(session.id), queryClient.invalidateQueries({ queryKey: ['memories', session.id] }), queryClient.invalidateQueries({ queryKey: ['memory-windows', session.id] })]);
  },
}));
