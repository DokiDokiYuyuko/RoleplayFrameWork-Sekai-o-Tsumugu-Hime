import { TextInput, Textarea, Select, Checkbox } from '../design-system';
import type { LorebookEntry } from '../types';
import './lorebook-entry-fields.css';

export const emptyLorebookEntry = (): LorebookEntry => ({ uid: 0, keys: [], secondary_keys: [], content: '', comment: '', enabled: true, constant: false, selective: false, selective_logic: 0, order: 100, anchor: 'at_depth', depth: 4, probability: 100, extensions: {} });
const split = (text: string) => text.split(/[,，、\n]/).map((word) => word.trim()).filter(Boolean);

/** The manual editor and AI review use the same worldbook fields. */
export function LorebookEntryFields({ value, onChange }: { value: LorebookEntry; onChange: (next: LorebookEntry) => void }) {
  const change = (patch: Partial<LorebookEntry>) => onChange({ ...value, ...patch });
  return <div className="worlds-editor lorebook-entry-fields">
    <label className="worlds-field">词条名称 / 备注<TextInput value={value.comment} onChange={(event) => change({ comment: event.target.value })} /></label>
    <label className="worlds-field">触发关键词 <small>用逗号分隔；可填写正则表达式</small><TextInput value={value.keys.join('，')} onChange={(event) => change({ keys: split(event.target.value) })} /></label>
    <label className="worlds-field">词条内容<Textarea rows={14} value={value.content} onChange={(event) => change({ content: event.target.value })} /></label>
    <div className="worlds-editor-grid">
      <label className="worlds-field">辅助关键词<TextInput value={value.secondary_keys.join('，')} onChange={(event) => change({ secondary_keys: split(event.target.value) })} /></label>
      <label className="worlds-field">关键词组合<Select aria-label="关键词组合" value={String(value.selective_logic)} onValueChange={(choice) => change({ selective_logic: Number(choice) as LorebookEntry['selective_logic'] })} options={[{value:'0',label:'任一辅助关键词匹配'},{value:'1',label:'并非所有辅助关键词匹配'},{value:'2',label:'所有辅助关键词都不匹配'},{value:'3',label:'所有辅助关键词匹配'}]} /></label>
    </div>
    <div className="worlds-editor-grid">
      <label className="worlds-field">注入位置<Select aria-label="注入位置" value={value.anchor} onValueChange={(choice) => change({ anchor: choice as LorebookEntry['anchor'] })} options={[{value:'system',label:'角色定义前'},{value:'near',label:'贴近最新消息'},{value:'at_depth',label:'指定深度'}]} /></label>
      <label className="worlds-field">注入深度<TextInput type="number" min={0} max={16} value={value.depth} onChange={(event) => change({ depth: Number(event.target.value) })} /></label>
    </div>
    <div className="worlds-editor-grid">
      <label className="worlds-field">排序权重<TextInput type="number" value={value.order} onChange={(event) => change({ order: Number(event.target.value) })} /></label>
      <label className="worlds-field">触发概率 %<TextInput type="number" min={0} max={100} value={value.probability} onChange={(event) => change({ probability: Number(event.target.value) })} /></label>
    </div>
    <div className="worlds-entry-options">
      <Checkbox label="启用，可用于故事" checked={value.enabled} onChange={(event) => change({ enabled: event.target.checked })} />
      <Checkbox label="常驻，每轮使用" checked={value.constant} onChange={(event) => change({ constant: event.target.checked })} />
      <Checkbox label="使用辅助关键词" checked={value.selective} onChange={(event) => change({ selective: event.target.checked })} />
    </div>
  </div>;
}
