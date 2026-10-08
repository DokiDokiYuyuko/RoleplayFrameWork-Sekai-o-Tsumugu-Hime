import { Button, Select, TextInput } from '../../design-system';
import { Search } from '../../design-system/Icon';
import { useSearchParams } from 'react-router';
import { readAssetFilters, updateAssetFilters, type AssetListFilters } from './assetList';
import './library-tools.css';
export { filterAndSortAssets, collectTags } from './assetList';
export function useAssetListFilters() {
  const [params, setParams] = useSearchParams();
  return { filters: readAssetFilters(params), search: params.toString(),
    setFilters: (patch: Partial<AssetListFilters>) => setParams((current) => updateAssetFilters(current, patch), { replace: true }) };
}
export function AssetListToolbar({ label, filters, onChange, tags, count, worlds, recentLabel = '最近修改' }: {
  label: string; filters: AssetListFilters; onChange: (patch: Partial<AssetListFilters>) => void;
  tags?: string[]; count: number; worlds?: { id: string; title: string; archived?: boolean }[]; recentLabel?: string;
}) {
  const active = Boolean(filters.query || filters.tag || filters.world || filters.sort !== 'updated');
  return <div className="asset-list-toolbar">
    <TextInput className="asset-list-query" leadingIcon={Search} aria-label={`搜索${label}`} value={filters.query}
      onChange={(event) => onChange({ query: event.target.value })} placeholder={`搜索${label}名称、简介${tags ? '或标签' : ''}`} />
    {tags && <Select prefix="标签" width="auto" aria-label={`${label}标签筛选`} value={filters.tag || '__all'} onValueChange={(value) => onChange({ tag: value === '__all' ? '' : value })}
      options={[{ value: '__all', label: '全部标签' }, ...(filters.tag && !tags.includes(filters.tag) ? [{ value: filters.tag, label: `${filters.tag}（无记录）` }] : []), ...tags.map((tag) => ({ value: tag, label: tag }))]} />}
    {worlds && <Select prefix="世界" width="auto" aria-label={`${label}世界筛选`} value={filters.world || '__all'} onValueChange={(value) => onChange({ world: value === '__all' ? '' : value })}
      options={[{ value: '__all', label: '全部世界' }, { value: 'unassigned', label: '未指定世界' }, ...(filters.world && filters.world !== 'unassigned' && !worlds.some((world) => world.id === filters.world) ? [{ value: filters.world, label: '世界已不可用' }] : []), ...worlds.map((world) => ({ value: world.id, label: `${world.title}${world.archived ? '（已归档）' : ''}` }))]} />}
    <Select prefix="排序" width="auto" aria-label={`${label}排序`} value={filters.sort} onValueChange={(value) => onChange({ sort: value === 'name' ? 'name' : 'updated' })}
      options={[{ value: 'updated', label: recentLabel }, { value: 'name', label: '名称' }]} />
    <span className="asset-list-count" role="status">{count} 项</span>
    {active && <Button variant="ghost" size="sm" onClick={() => onChange({ query: '', tag: '', world: '', sort: 'updated' })}>清除筛选</Button>}
  </div>;
}
