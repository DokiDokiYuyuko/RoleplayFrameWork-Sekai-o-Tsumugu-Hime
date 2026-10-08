import { create } from 'zustand';
import { api } from '../api/client';
import type { Inspection, InspectionTarget } from '../types';

interface InspectorState {
  prompt: Inspection | null;
  loading: boolean;
  source: 'generation' | 'turn' | 'preview' | null;
  notice: string | null;
  load: (sessionId: string, characterId: string, turn: number, target?: InspectionTarget | null) => Promise<void>;
  preview: (sessionId: string, characterId: string) => Promise<void>;
  /** 切换会话时清空（结果不带 session_id，防跨会话串台） */
  clear: () => void;
}

let requestEpoch = 0;

export const useInspectorStore = create<InspectorState>()((set) => ({
  prompt: null,
  loading: false,
  source: null,
  notice: null,

  load: async (sessionId, characterId, turn, target) => {
    const epoch = ++requestEpoch;
    const exact = Boolean(target?.messageId && target.generationId);
    set({ loading: true, prompt: null, source: null, notice: null });
    try {
      const prompt = exact
        ? await api.getGenerationInspection(sessionId, target!.messageId!, target!.generationId!)
        : await api.getInspection(sessionId, characterId, turn);
      if (epoch !== requestEpoch) return;
      set({ prompt, source: exact ? 'generation' : 'turn',
        notice: target?.messageId && !exact ? '这条旧记录没有单次生成标识，显示该人物或群体的回合记录；同一回合多次发言时，无法确认它对应这条回应。' : null });
    } catch (error) {
      if (epoch === requestEpoch) throw error;
    } finally {
      if (epoch === requestEpoch) set({ loading: false });
    }
  },

  preview: async (sessionId, characterId) => {
    const epoch = ++requestEpoch;
    set({ loading: true, prompt: null, source: null, notice: null });
    try {
      const prompt = await api.getContextPreview(sessionId, characterId);
      if (epoch === requestEpoch) set({ prompt, source: 'preview' });
    } catch (error) { if (epoch === requestEpoch) throw error; }
    finally { if (epoch === requestEpoch) set({ loading: false }); }
  },

  clear: () => { ++requestEpoch; set({ prompt: null, loading: false, source: null, notice: null }); },
}));
