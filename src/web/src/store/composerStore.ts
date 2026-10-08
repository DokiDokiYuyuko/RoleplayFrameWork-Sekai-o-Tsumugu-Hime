import { create } from 'zustand';

/**
 * M12-R41 预填通道（D14：点击候选 → 输入框）。
 *
 * 语义（2026-09-25 用户修订，原 E-D4"追加为新行"改为覆盖）：
 * - 点击候选 = **清空后覆盖**输入框（换一条就是换一句，不叠加）
 * - 输入框已有**玩家自己写的/改过的**草稿时覆盖，但留一次性「恢复草稿」；
 *   若现有内容就是上一条候选（未改动），直接替换、不产生备份。
 */
interface PrefillSeed {
  text: string;
  mentionIds: string[];
  nonce: number;
}

interface ComposerState {
  prefill: PrefillSeed | null;
  /** 最近一次候选填入的文本（未改动时再点候选=直接替换，不产生草稿备份） */
  lastPrefill: string | null;
  /** 被候选覆盖的玩家草稿（一次性恢复入口；发送/恢复后清空） */
  draftBackup: string | null;
  requestPrefill: (text: string, mentionIds?: string[]) => void;
  clearPrefill: () => void;
  resetAfterSend: () => void;
  notePrefilled: (text: string) => void;
  backupDraft: (text: string) => void;
  restoreDraft: () => string | null;
  clearDraftBackup: () => void;
}

let nonce = 0;

export const useComposerStore = create<ComposerState>()((set, get) => ({
  prefill: null,
  lastPrefill: null,
  draftBackup: null,
  requestPrefill: (text, mentionIds = []) =>
    set({ prefill: { text, mentionIds, nonce: ++nonce } }),
  clearPrefill: () => set({ prefill: null }),
  resetAfterSend: () => set({ prefill: null, draftBackup: null, lastPrefill: null }),
  notePrefilled: (text) => set({ lastPrefill: text }),
  backupDraft: (text) => set({ draftBackup: text }),
  restoreDraft: () => {
    const backup = get().draftBackup;
    set({ draftBackup: null, lastPrefill: null });
    return backup;
  },
  clearDraftBackup: () => set({ draftBackup: null }),
}));
