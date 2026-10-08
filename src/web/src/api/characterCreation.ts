import type { CharacterCard, Character, CharacterView } from '../types';
import type { TrialMessage } from '../utils/characterCreation.js';
import { toView } from './adapters';
import { jsonRequest } from './request';
import type { ImportJob } from '../features/asset-import/assetImportApi';
async function request<T>(url: string, input: object, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input), signal });
  if (!response.ok) { const raw = await response.text(); let message = raw; try { const data = JSON.parse(raw); message = typeof data.detail === 'string' ? data.detail : '请求内容或长度无效'; } catch { /* Plain error. */ } throw new Error(message); }
  return response.json();
}
export const characterCreation = {
  organize: (input: { source: string; world_id: string | null; selected_fields: string[]; target_asset_id: string | null; target_revision: number | null; character_seed: CharacterCard; character_aliases: string[]; character_runtime: { model: string; base_url: string; max_tokens: number }; reference_world_id: string | null }) => jsonRequest<ImportJob>('/api/v1/asset-import-jobs', { method: 'POST', body: JSON.stringify({ ...input, target_kind: 'character', intent: 'organize' }) }),
  generateOrganized: (id: string) => jsonRequest<ImportJob>(`/api/v1/asset-import-jobs/${encodeURIComponent(id)}/generate`, { method: 'POST' }),
  previewTurn: (card: CharacterCard, message: string, history: TrialMessage[], signal?: AbortSignal) => request<{reply: string}>('/api/v1/characters/preview-turn', { card, message, history }, signal),
  edit: (card: CharacterCard, field: string, instruction: string, selection_text?: string, signal?: AbortSignal) => request<{value: string}>('/api/v1/characters/ai-edit', { card, field, instruction, scope: selection_text === undefined ? 'field' : 'selection', selection_text }, signal),
  create: async (card: CharacterCard, source_world_id: string | null): Promise<CharacterView> => {
    const body = new FormData(); body.append('file', new Blob([JSON.stringify(card)], {type:'application/json'}), 'card.json');
    if (source_world_id) body.append('source_world_id', source_world_id);
    const response = await fetch('/api/v1/characters/import', {method:'POST', body});
    if (!response.ok) throw new Error(await response.text());
    return toView(await response.json() as Character);
  },
};
