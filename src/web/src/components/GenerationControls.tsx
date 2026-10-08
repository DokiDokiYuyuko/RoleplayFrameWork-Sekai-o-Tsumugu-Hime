import type { GenerationSettings } from '../types';
import type { ChangeEvent, ElementType } from 'react';

const fields = [
  { key: 'temperature', label: '温度', hint: '低值更稳定，高值更多变化', min: 0, max: 2, step: 0.05 },
  { key: 'top_p', label: 'Top P', hint: '控制候选词范围', min: 0, max: 1, step: 0.05 },
  { key: 'frequency_penalty', label: '频率惩罚', hint: '减少重复用词', min: -2, max: 2, step: 0.1 },
  { key: 'presence_penalty', label: '存在惩罚', hint: '鼓励引入新话题', min: -2, max: 2, step: 0.1 },
  { key: 'max_output_tokens', label: '最大输出 Token', hint: '留空则不指定上限', min: 1, max: 32768, step: 1 },
] as const;

export function GenerationControls({ value, onChange, idPrefix, disabled = false, inputComponent }: {
  value: GenerationSettings;
  onChange: (value: GenerationSettings) => void;
  idPrefix: string;
  disabled?: boolean;
  /** Optional presentation adapter; numeric parsing and saved values stay owned here. */
  inputComponent?: ElementType;
}) {
  const Input = inputComponent ?? 'input';
  return <div className="v7-generation-controls">
    {fields.map((field) => <label key={field.key} htmlFor={`${idPrefix}-${field.key}`}>
      <span>{field.label}</span>
      <Input id={`${idPrefix}-${field.key}`} type="number" min={field.min} max={field.max} step={field.step}
        value={value[field.key] ?? ''} disabled={disabled} placeholder="模型默认"
        onChange={(event: ChangeEvent<HTMLInputElement>) => {
          const raw = event.target.value;
          const parsed = raw === '' ? null : Number(raw);
          if (parsed !== null && !Number.isFinite(parsed)) return;
          onChange({ ...value, [field.key]: parsed });
        }}/>
      <small>{field.hint}</small>
    </label>)}
  </div>;
}
