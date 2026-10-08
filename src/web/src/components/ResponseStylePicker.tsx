import { Button } from '../design-system/Button';
import { storyRuntime } from '../features/stories/storyRuntime';
import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { Popover } from 'radix-ui';
import { useChatStore } from '../store/chatStore';
import { useSettingsStore } from '../store/settingsStore';
import { storiesClient as api } from '../features/stories/storiesClient';
import type { Session } from '../types';
import { ComposerPanel } from './ComposerPanel';

export function ResponseStylePicker({ open: controlledOpen, onToggle: controlledToggle, onClose: controlledClose }: {
  open?: boolean; onToggle?: () => void; onClose?: () => void;
} = {}) {
  const [localOpen, setLocalOpen] = useState(false);
  const open = controlledOpen ?? localOpen;
  const onToggle = controlledToggle ?? (() => setLocalOpen((value) => !value));
  const onClose = controlledClose ?? (() => setLocalOpen(false));
  const sid = useChatStore((s) => s.currentSessionId);
  const session = useChatStore((s) => s.sessions.find((r) => r.id === sid));
  const characters = useChatStore((s) => s.sessionCharacters);
  const groups = useChatStore((s) => s.sessionGroups);
  const sending = useChatStore((s) => s.sending);
  const { settings, load } = useSettingsStore();
  const styles = (settings?.response_styles ?? []).filter((s) => s.enabled);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const selected = styles.find((s) => s.id === session?.response_style_id);
  useEffect(() => {
    if (open) void load().catch((cause) => setError(String(cause)));
  }, [open, load]);
  const apply = async (patch: Pick<Partial<Session>, 'response_style_id' | 'response_style_overrides'>) => {
    if (!sid || busy || sending) return;
    setBusy(true); setError('');
    try {
      const ownsBranch = storyRuntime.capture(sid);
      const result = await api.patchNarrative(sid, patch);
      if (ownsBranch()) storyRuntime.applySessionPatch(sid, result);
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败'); }
    finally { setBusy(false); }
  };
  const options = (value?: string | null) => <>
    <option value="">跟随角色</option>
    {value && !styles.some((s) => s.id === value) && <option value={value}>已不可用（跟随角色）</option>}
    {styles.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
  </>;
  const members = [...characters.filter((c) => c.present).map((c) => ({ id: c.id, name: c.card.name })),
    ...groups.filter((g) => g.status === 'active').map((g) => ({ id: g.id, name: g.label }))];
  const button = <Button variant="ghost" type="button" className="v7-response-style-trigger" aria-expanded={open} aria-haspopup="dialog" onClick={onToggle}>回应风格 · {selected?.name ?? '跟随角色'}</Button>;
  const panel = open && <ComposerPanel title="回应风格" onClose={onClose}>
    <div className="v7-response-style-body">
      <label className="v7-field">本分支默认<select disabled={busy || sending} value={session?.response_style_id ?? ''} onChange={(e) => void apply({ response_style_id: e.target.value || null })}>
        {options(session?.response_style_id)}
      </select></label>
      {selected?.description && <p className="v7-muted">{selected.description}</p>}
      <p className="v7-muted">选择影响下一次回复与重新生成，不改写已有消息。</p>
      <details><summary>为角色单独设置</summary>
        {members.map((member) => {
          const overrides = session?.response_style_overrides ?? {};
          const value = Object.prototype.hasOwnProperty.call(overrides, member.id) ? overrides[member.id] ?? '' : '__inherit__';
          return <label className="v7-field" key={member.id}>{member.name}<select disabled={busy || sending} value={value} onChange={(e) => {
            const next = { ...overrides };
            if (e.target.value === '__inherit__') delete next[member.id]; else next[member.id] = e.target.value || null;
            void apply({ response_style_overrides: next });
          }}><option value="__inherit__">使用分支默认</option>{options(overrides[member.id])}</select></label>;
        })}
      </details>
      {error && <p role="alert">{error}</p>}
      <Link to="/settings/response-styles" onClick={onClose}>管理回应风格 →</Link>
    </div>
    </ComposerPanel>;
  return controlledOpen !== undefined ? <>{button}{panel}</> : <Popover.Root open={open} onOpenChange={setLocalOpen}>
    <Popover.Anchor asChild>{button}</Popover.Anchor>{panel}
  </Popover.Root>;
}
