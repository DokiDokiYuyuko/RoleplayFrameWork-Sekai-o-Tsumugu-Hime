import { jsonRequest, type ReadOptions } from '../../api/request';
import { storyCommand, uploadStoryCommand } from '../../api/storyCommands';
const branch = (id: string) => `/api/v1/branches/${encodeURIComponent(id)}`;
const session = (id: string) => `/api/v1/sessions/${encodeURIComponent(id)}`;
export const storyReviewApi = {
  correctionOptions: <T,>(sid: string, mid: string, options?: ReadOptions) => jsonRequest<T>(`${session(sid)}/messages/${encodeURIComponent(mid)}/correction-options`, options),
  comparison: <T,>(sid: string, mid: string, gid: string, options?: ReadOptions) => jsonRequest<T>(`${session(sid)}/context-comparison/${encodeURIComponent(mid)}/${encodeURIComponent(gid)}`, options),
  regenerateCorrected: (sid: string, mid: string, payload: Record<string, unknown>) => storyCommand(`${session(sid)}/messages/${encodeURIComponent(mid)}/regenerate-corrected`, 'POST', payload),
  review: <T,>(sid: string, actor: string) => jsonRequest<T>(`${branch(sid)}/review?actor_id=${encodeURIComponent(actor)}`),
  compareBranches: <T,>(sid: string, other: string, actor: string) => jsonRequest<T>(`${branch(sid)}/compare/${encodeURIComponent(other)}?actor_id=${encodeURIComponent(actor)}`),
  assetUpdates: <T,>(sid: string) => jsonRequest<T>(`${branch(sid)}/asset-updates`),
  applyAssetUpdates: (sid: string, payload: Record<string, unknown>) => storyCommand(`${branch(sid)}/asset-updates/apply`, 'POST', payload),
  previewST: <T,>(file: File) => { const form = new FormData(); form.append('file', file); return jsonRequest<T>('/api/v1/chat-import/st/preview', { method: 'POST', headers: {}, body: form }); },
  importST: (file: File, fields: Record<string, string>) => uploadStoryCommand<{ story_id: string; branch_id: string }>('/api/v1/chat-import/st/file', file, fields),
};
