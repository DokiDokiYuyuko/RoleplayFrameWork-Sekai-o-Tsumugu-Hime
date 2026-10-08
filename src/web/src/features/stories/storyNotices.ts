import { create } from 'zustand';
export const useStoryNotice = create<{ message: string; dismiss: () => void; cleanupPending: () => void }>()(set => ({ message: '', dismiss: () => set({ message: '' }), cleanupPending: () => set({ message: '故事变更已保存，后台清理待恢复。可稍后检查备份与清理状态。' }) }));
export function showCleanupPending(result: { cleanup_pending?: boolean | null }) { if (result.cleanup_pending) useStoryNotice.getState().cleanupPending(); }
