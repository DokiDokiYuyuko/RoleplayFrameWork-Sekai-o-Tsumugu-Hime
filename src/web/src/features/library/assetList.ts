export type AssetSort = 'updated' | 'name';
export interface AssetListFilters { query: string; tag: string; world: string; sort: AssetSort }
export interface AssetListDescription {
  id: string; name: string; description?: string; aliases?: string[]; tags?: string[];
  worldId?: string | null; updatedAt?: string | number;
}
export function readAssetFilters(params: URLSearchParams): AssetListFilters {
  return { query: params.get('q') ?? '', tag: params.get('tag') ?? '', world: params.get('world') ?? (params.get('unowned') === '1' ? 'unassigned' : ''),
    sort: params.get('sort') === 'name' ? 'name' : 'updated' };
}
export function updateAssetFilters(params: URLSearchParams, patch: Partial<AssetListFilters>): URLSearchParams {
  const next = new URLSearchParams(params);
  if ('world' in patch) next.delete('unowned');
  const keys = { query: 'q', tag: 'tag', world: 'world', sort: 'sort' } as const;
  for (const key of Object.keys(patch) as (keyof AssetListFilters)[]) {
    const value = patch[key];
    if (!value || (key === 'sort' && value === 'updated')) next.delete(keys[key]);
    else next.set(keys[key], value);
  }
  return next;
}
const lower = (value: string) => value.trim().toLocaleLowerCase();
const time = (value: string | number | undefined) => typeof value === 'number'
  ? value < 100_000_000_000 ? value * 1000 : value : Date.parse(value ?? '') || 0;
export function filterAndSortAssets<T>(items: readonly T[], filters: AssetListFilters, describe: (item: T) => AssetListDescription): T[] {
  const query = lower(filters.query);
  return items.map((item) => ({ item, row: describe(item) })).filter(({ row }) => {
    const text = [row.name, row.description ?? '', ...(row.aliases ?? []), ...(row.tags ?? [])].join(' ');
    return (!query || lower(text).includes(query)) && (!filters.tag || (row.tags ?? []).includes(filters.tag))
      && (!filters.world || (filters.world === 'unassigned' ? !row.worldId : row.worldId === filters.world));
  }).sort((a, b) => (filters.sort === 'updated' ? time(b.row.updatedAt) - time(a.row.updatedAt) : 0)
    || a.row.name.localeCompare(b.row.name, 'zh-CN') || a.row.id.localeCompare(b.row.id)).map(({ item }) => item);
}
export function collectTags(items: readonly { tags?: string[] }[]): string[] {
  return [...new Set(items.flatMap((item) => item.tags ?? []).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
}
export function selectionCounts(selected: readonly string[], visible: readonly string[]) {
  const visibleSet = new Set(visible);
  return { total: selected.length, hidden: selected.filter((id) => !visibleSet.has(id)).length };
}
export function toggleVisibleSelection(selected: readonly string[], visible: readonly string[]): string[] {
  const next = new Set(selected);
  const remove = visible.length > 0 && visible.every((id) => next.has(id));
  for (const id of visible) { if (remove) next.delete(id); else next.add(id); }
  return [...next];
}
