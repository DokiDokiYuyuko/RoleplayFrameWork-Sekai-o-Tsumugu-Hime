import { useMemo, useState } from 'react';
import { ChevronDown } from 'lucide-react';

import type { ModelCatalogResult } from '../types';

export type CatalogModel = ModelCatalogResult['models'][number];

/** Shared model ID picker for connection settings and plain chat. */
export function ModelPicker({
  id, label, value, options, placeholder, onChange,
}: {
  id: string;
  label: string;
  value: string;
  options: CatalogModel[];
  placeholder: string;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [browseAll, setBrowseAll] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const shown = useMemo(() => {
    const query = browseAll ? '' : value.trim().toLocaleLowerCase();
    return options.filter((item) =>
      !query || item.id.toLocaleLowerCase().includes(query) || item.name.toLocaleLowerCase().includes(query)
    ).slice(0, 12);
  }, [options, value, browseAll]);

  const choose = (modelId: string) => {
    onChange(modelId);
    setOpen(false);
    setBrowseAll(false);
    setHighlight(0);
  };

  return (
    <div className="v7-field v7-ai-model-field">
      <label htmlFor={id}>{label}</label>
      <div className="v7-ai-model-picker" onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}>
        <div className="v7-ai-picker-control">
          <input id={id} role="combobox" aria-autocomplete="list" aria-expanded={open}
            aria-controls={`${id}-options`}
            aria-activedescendant={open && shown.length ? `${id}-option-${highlight}` : undefined}
            value={value} placeholder={placeholder} spellCheck={false}
            onFocus={() => setOpen(true)}
            onChange={(event) => { onChange(event.target.value); setBrowseAll(false); setHighlight(0); setOpen(true); }}
            onKeyDown={(event) => {
              if (event.key === 'Escape') setOpen(false);
              if (event.key === 'ArrowDown') { event.preventDefault(); setOpen(true); setHighlight((current) => Math.max(0, Math.min(current + 1, shown.length - 1))); }
              if (event.key === 'ArrowUp') { event.preventDefault(); setHighlight((current) => Math.max(current - 1, 0)); }
              if (event.key === 'Enter' && open && shown[highlight]) { event.preventDefault(); choose(shown[highlight].id); }
            }}
          />
          <button type="button" aria-label={`展开${label}候选`} onClick={() => { setBrowseAll(true); setHighlight(0); setOpen((current) => !current); }}><ChevronDown size={16} /></button>
        </div>
        {open && <div id={`${id}-options`} className="v7-ai-model-options" role="listbox" aria-label={`${label}候选`}>
          {shown.length ? shown.map((item, index) => <button key={item.id} id={`${id}-option-${index}`}
            type="button" role="option" aria-selected={item.id === value}
            className={index === highlight ? 'is-highlighted' : ''}
            onMouseDown={(event) => event.preventDefault()} onClick={() => choose(item.id)}>
            <strong>{item.name}</strong>{item.name !== item.id && <small>{item.id}</small>}
          </button>) : <p>{options.length ? '没有匹配项，可直接填写模型 ID。' : '候选暂不可用，可直接填写模型 ID。'}</p>}
        </div>}
      </div>
    </div>
  );
}
