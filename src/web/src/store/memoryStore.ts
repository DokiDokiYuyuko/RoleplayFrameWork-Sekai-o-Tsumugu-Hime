import { createQueryFacade } from '../features/resources/queryFacade';
import { resourceQueries } from '../features/resources/resourceQueries';
import { queryClient } from '../queryClient';
import { commandReceipt } from '../utils/commandReceipt';
import { useChatStore } from './chatStore';
import { api } from '../api/client';
import type { MemoryRecord, MemoryPatch } from '../types';

export interface MemoryStoreState {
  characterId: string | null;
  jobOperationId: string | null;
  sessionId: string | null;
  requestId: number;
  records: MemoryRecord[];
  loading: boolean;
  error: string;
  select: (characterId: string, sessionId?: string) => Promise<void>;
  clear: () => void;
  remove: (recordId: string, expectedRevision?: number) => Promise<void>;
  edit: (recordId: string, patch: MemoryPatch) => Promise<void>;
  consolidateNow: (sessionId: string, characterId?: string, start?: number, end?: number) => Promise<void>;
}

export const useMemoryStore = createQueryFacade<MemoryStoreState, MemoryRecord[]>('records', state => resourceQueries.memories(state.characterId ?? '', state.sessionId ?? undefined), (set, get) => ({
  jobOperationId: null, characterId: null, sessionId: null, requestId: 0, records: [], loading: false, error: '',
  select: async (characterId, sessionId) => {
    const prior = get();
    if (prior.characterId && (prior.characterId !== characterId || prior.sessionId !== (sessionId ?? null))) void queryClient.cancelQueries({ queryKey: resourceQueries.memories(prior.characterId, prior.sessionId ?? undefined).queryKey, exact: true });
    const requestId = get().requestId + 1;
    const changed = get().characterId !== characterId || get().sessionId !== (sessionId ?? null);
    set({ characterId, sessionId: sessionId ?? null, requestId, error: '',
      ...(changed ? { loading: true, jobOperationId: null } : {}) });
    try {
      await queryClient.fetchQuery({ ...resourceQueries.memories(characterId, sessionId), staleTime: 0, retry: false });
      if (get().requestId === requestId) set({ loading: false });
    } catch (error) {
      if (get().requestId === requestId) set({ loading: false, error: error instanceof Error ? error.message : '记忆读取失败' });
    }
  },
  clear: () => set((s) => ({ characterId: null, sessionId: null, jobOperationId: null, requestId: s.requestId + 1, loading: false, error: '' })),
  remove: async (recordId, expectedRevision) => {
    const { characterId, sessionId, requestId } = get();
    if (!characterId) return;
    const record = get().records.find(item => item.id === recordId);
    const result = await api.deleteMemory(characterId, recordId, sessionId ?? undefined, expectedRevision ?? record?.revision, useChatStore.getState().sessions.find(item => item.id === sessionId)?.branch_revision);
    if (get().requestId === requestId && (!commandReceipt(result) || commandReceipt(result)!.branch_revision >= (useChatStore.getState().sessions.find(item => item.id === sessionId)?.branch_revision ?? 0))) set((s) => ({ records: s.records.filter((r) => r.id !== recordId || r.revision !== record?.revision) }));
  },
  edit: async (recordId, patch) => {
    const { characterId, sessionId, requestId, records } = get();
    if (!characterId) return;
    const revision = records.find((r) => r.id === recordId)?.revision;
    const fresh = await api.updateMemory(characterId, recordId, { expected_revision: revision, expected_branch_revision: useChatStore.getState().sessions.find(item => item.id === sessionId)?.branch_revision, ...patch }, sessionId ?? undefined);
    if (get().requestId === requestId && (!commandReceipt(fresh) || commandReceipt(fresh)!.branch_revision >= (useChatStore.getState().sessions.find(item => item.id === sessionId)?.branch_revision ?? 0))) set((s) => ({ records: s.records.map((r) => r.id === recordId && (fresh.revision ?? 0) >= (r.revision ?? 0) ? fresh : r) }));
  },
  consolidateNow: async (sessionId, characterId, start, end) => {
    const result = await api.consolidateAsync(sessionId, characterId, start, end);
    if (get().sessionId === sessionId && (!characterId || get().characterId === characterId)) set({ jobOperationId: result.operation_id });
  },
}));
