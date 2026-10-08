import { resourceQueries } from '../resources/resourceQueries';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check, ImageIcon } from 'lucide-react';
import { Dialog } from 'radix-ui';
import { Button, Cover } from '../../design-system';
import { BUILTIN_WORLD_COVERS, resolveWorldCover } from '../../appearance/moonweaveAssets';
import './world-covers.css';

export function worldCoverUrl(id?: string | null) {
  return resolveWorldCover(id);
}

export function WorldCoverImage({ coverId, className, alt = '' }: { coverId?: string | null; className: string; alt?: string }) {
  const [failed, setFailed] = useState('');
  const source = worldCoverUrl(coverId);
  return source && failed !== source ? <img src={source} className={className} alt={alt} loading="lazy" onError={() => setFailed(source)} /> : null;
}

export function WorldCoverPicker({ value, onChange, disabled = false }: {
  value: string | null; onChange: (value: string | null) => void; disabled?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [theme, setTheme] = useState('');
  const { data: serverCovers = [], isLoading, error, refetch } = useQuery({
    ...resourceQueries.covers(),
  });
  const covers = [...BUILTIN_WORLD_COVERS, ...serverCovers.filter((item) => !BUILTIN_WORLD_COVERS.some((cover) => cover.id === item.id))];
  const selected = covers.find((cover) => cover.id === value);
  const themes = [...new Map(covers.map((cover) => [cover.theme, cover.theme_label])).entries()];
  return <section className="world-cover-picker" aria-label="世界封面">
    <div className="world-cover-picker-heading">
      <div><h3>世界封面 <small>可选</small></h3><p>为这个世界和它的故事选一幅风景。</p></div>
      <Button type="button" variant="secondary" disabled={disabled} aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}>{expanded ? '收起图库' : value ? '更换封面' : '选择封面'}</Button>
    </div>
    {value ? <figure className="world-cover-preview">
      <Cover name={selected?.title || '当前世界封面'} src={worldCoverUrl(value)} label={selected?.title || '当前世界封面'} />
      <figcaption>{selected?.title || '当前封面'}<Button type="button" disabled={disabled} onClick={() => onChange(null)}>移除封面</Button></figcaption>
    </figure> : !expanded && <div className="world-cover-unselected"><ImageIcon size={18} /> 暂未选择，之后也可以在“编辑世界”中添加。</div>}
    {expanded && <Dialog.Root open onOpenChange={setExpanded}><Dialog.Portal><Dialog.Overlay className="v7-dialog-overlay" /><Dialog.Content className="v7-dialog-content world-cover-gallery"><Dialog.Title>选择世界封面</Dialog.Title><Dialog.Description>封面随世界保存。可按题材筛选，或在编辑世界时移除。</Dialog.Description>
      <>
      {isLoading && <p role="status">正在读取早期封面…</p>}
      {error && <div role="alert">服务端封面读取失败，内置图库仍可使用。<Button onClick={() => void refetch()}>重新读取</Button></div>}
        <div className="world-cover-themes" aria-label="封面主题">
          <Button type="button" aria-pressed={!theme} onClick={() => setTheme('')}>全部</Button>
          {themes.map(([id, label]) => <Button key={id} type="button" aria-pressed={theme === id} onClick={() => setTheme(id)}>{label}</Button>)}
        </div>
        <div className="world-cover-options">
          {covers.filter((cover) => !theme || cover.theme === theme).map((cover) => <Button
            key={cover.id} type="button" disabled={disabled} className="world-cover-option"
            aria-label={`选择封面：${cover.title}`} aria-pressed={value === cover.id} onClick={() => onChange(cover.id)}>
            <Cover name={cover.title} src={cover.image_url} />
            <span>{cover.title}<small>{cover.theme_label}</small></span>
            {value === cover.id && <Check className="world-cover-selected" size={20} aria-hidden="true" />}
          </Button>)}
        </div>
      </>
      <Button onClick={() => setExpanded(false)}>完成</Button>
    </Dialog.Content></Dialog.Portal></Dialog.Root>}
  </section>;
}
