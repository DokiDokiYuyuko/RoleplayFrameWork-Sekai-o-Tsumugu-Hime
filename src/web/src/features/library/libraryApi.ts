import { jsonRequest } from '../../api/request';
import { exportSelectedBundle } from '../../api/bundleExport';
export interface SelectedAssetRef { id: string; expected_revision: number }
export interface SelectedBundleInput { characters: SelectedAssetRef[]; lorebooks: SelectedAssetRef[]; include_bound_lorebooks: boolean }
export interface SelectedBundlePreview {
  items: { kind: 'character' | 'lorebook'; id: string; title: string; revision: number; dependency: boolean }[];
  notices: string[]; avatars: number;
  exclusions?: { author_entries: number; provenance_entries: number };
}
export function previewSelectedBundle(input: SelectedBundleInput) {
  return jsonRequest<SelectedBundlePreview>('/api/v1/bundle/export-selected/preview', { method: 'POST', body: JSON.stringify(input) });
}
export async function downloadSelectedBundle(preview: SelectedBundlePreview) {
  const input: SelectedBundleInput = { characters: [], lorebooks: [], include_bound_lorebooks: false };
  for (const item of preview.items) input[item.kind === 'character' ? 'characters' : 'lorebooks'].push({ id: item.id, expected_revision: item.revision });
  const blob = await exportSelectedBundle(input);
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url; link.download = '织界之姬-所选角色与世界书.zip';
  document.body.append(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const personalLibraryApi = {
  checklist: <T,>() => jsonRequest<T>('/api/v1/personal-library/backup-checklist'),
  verify: (file: File) => { const form = new FormData(); form.append('file', file); return jsonRequest<{ file_count?: number; files?: number; ok?: boolean }>('/api/v1/personal-library/verify', { method: 'POST', headers: {}, body: form }); },
};
