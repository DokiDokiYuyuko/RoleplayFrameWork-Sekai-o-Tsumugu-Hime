import { Check, LoaderCircle } from 'lucide-react';
import { BubbleFrame } from './BubbleFrame';
import { BUBBLE_STYLES, resolveBubbleStyle } from './bubbleStyles';

export function BubbleStylePicker({ value, disabled, onChange }: { value: string; disabled?: boolean; onChange: (value: string) => void }) {
  const selected = resolveBubbleStyle(value);
  return <fieldset className="bubble-style-picker" disabled={disabled}>
    <legend>聊天气泡</legend><p className="appearance-help">故事与简单聊天共用这款气泡，颜色随阅读主题变化。</p>
    <div className="bubble-style-options">{BUBBLE_STYLES.map((style) => <button type="button" key={style.id} className="bubble-style-option" aria-pressed={selected.id === style.id} onClick={() => onChange(style.id)}>
      <BubbleFrame styleId={style.id}>让故事在这里继续。</BubbleFrame>
      <strong>{style.name}{selected.id === style.id && <Check size={14}/>}</strong><small>{style.note}</small>
    </button>)}</div>
    <div className="bubble-style-preview" aria-label="当前气泡预览">
      <article data-speaker="npc"><small>角色 · 预览</small><BubbleFrame styleId={selected.id}>窗外的星光落在书页上。向导把地图展开，指向尚未走过的道路。{'\n\n'}“我们先确认路线，再一起出发。”</BubbleFrame></article>
      <article data-speaker="player"><small>你 · 预览</small><BubbleFrame speaker="player" styleId={selected.id}>好，先看看地图上的标记。</BubbleFrame></article>
      <article data-speaker="npc"><small>生成中 · 预览</small><BubbleFrame styleId={selected.id}><span className="bubble-preview-pending"><LoaderCircle className="page-load-spinner" size={14}/>正在生成…</span></BubbleFrame></article>
    </div>
  </fieldset>;
}
