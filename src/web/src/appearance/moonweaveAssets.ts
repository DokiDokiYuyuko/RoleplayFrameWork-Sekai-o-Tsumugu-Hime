import { covers, scenes } from './moonweaveCatalog.json';

/** Reviewed public interface artwork, separate from user media and story data. */
export function moonweaveAsset(path: string): string {
  const relative = path.replace(/^assets\//, '');
  if (!relative || relative.includes('..') || !/^[a-zA-Z0-9_./-]+$/.test(relative)) return '';
  return `${import.meta.env.BASE_URL}brand/moonweave/ui-v3/${relative}`;
}


export const BUILTIN_WORLD_COVERS = covers.map(item => ({
  id: `moonweave-${item.id}`, title: item.name,
  theme: item.source === 'legacy' ? 'legacy' : item.id,
  theme_label: item.source === 'legacy' ? '早期封面' : item.name,
  image_url: moonweaveAsset(item.file),
}));
export const BUILTIN_SCENE_IMAGES = scenes.map(item => ({
  ...item, image_url: moonweaveAsset(item.file),
}));

export function resolveWorldCover(id?: string | null): string {
  if (!id || !/^[a-z0-9][a-z0-9-]{0,79}$/.test(id)) return '';
  const builtin = covers.find(item => `moonweave-${item.id}` === id);
  return builtin ? moonweaveAsset(builtin.file) : `${import.meta.env.BASE_URL}api/v1/world-covers/${encodeURIComponent(id)}/image`;
}
export function resolveWorldBanner(id?: string | null): string {
  const builtin = covers.find(item => `moonweave-${item.id}` === id);
  return builtin && 'banner' in builtin && builtin.banner ? moonweaveAsset(builtin.banner) : resolveWorldCover(id);
}
export function worldCoverSigil(id?: string | null): string | undefined {
  const builtin = covers.find(item => `moonweave-${item.id}` === id);
  return builtin && 'sigil' in builtin && builtin.sigil ? moonweaveAsset(builtin.sigil) : undefined;
}
export function resolveSceneImage(id?: string | null): string {
  const builtin = scenes.find(item => item.id === id);
  return builtin ? moonweaveAsset(builtin.file) : '';
}
