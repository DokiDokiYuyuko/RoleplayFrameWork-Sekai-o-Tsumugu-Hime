import type { Character } from '../types';
export type CharacterImportCompatibility = { converted: string[]; partial: string[]; unsupported: string[] };
async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData(); form.append('file', file);
  const response = await fetch(path, { method: 'POST', body: form });
  if (!response.ok) throw new Error((await response.text()).slice(0, 200));
  return response.json();
}
export const characterImport = {
  preview: (file: File) => upload<CharacterImportCompatibility>('/api/v1/characters/import-preview', file),
  import: (file: File) => upload<Character>('/api/v1/characters/import', file),
};
