import { useEffect, useMemo, useRef, useState } from 'react';
import { Dialog } from 'radix-ui';
import { ArrowRightLeft, Search, X } from 'lucide-react';
import { useChatStore } from '../store/chatStore';
import { useCharacterStore } from '../store/characterStore';
import { Avatar } from './common';
import { toView } from '../api/adapters';
import { resolvePlayerIdentity, playerAvatarUrl } from '../utils/playerIdentity.js';
import { createClientId } from '../utils/clientId.js';
import './player-control.css';

export function PlayerSwitchDialog({ onClose }: { onClose: () => void }) {
  const sid = useChatStore((s) => s.currentSessionId);
  const session = useChatStore((s) => s.sessions.find((x) => x.id === sid));
  const characters = useChatStore((s) => s.sessionCharacters);
  const library = useCharacterStore((s) => s.characters);
  const current = resolvePlayerIdentity(session);
  const [query, setQuery] = useState('');
  const [target, setTarget] = useState('');
  const [disposition, setDisposition] = useState<'npc' | 'leave'>(current?.character ? 'npc' : 'leave');
  const [brief, setBrief] = useState('');
  const [error, setError] = useState('');
  const busy = useChatStore((s) => s.switchingPlayer);
  const initial = useRef({ sid, identity: current?.id, revision: session?.branch_revision ?? 0 });
  const request = useRef({ body: '', key: '' });
  useEffect(() => { void useCharacterStore.getState().load().catch(() => setError('角色库读取失败，可以先选择故事中的人物')); }, []);
  const storyPeople = useMemo(() => {
    const all = new Map(characters.map((c) => [c.id, c]));
    for (const c of Object.values(session?.player_people ?? {})) if (!all.has(c.id)) all.set(c.id, toView(c));
    return [...all.values()].filter((c) => c.id !== current?.person_id);
  }, [characters, session?.player_people, current?.person_id]);
  const freshPeople = library.filter((c) => c.id !== current?.person_id && !storyPeople.some((p) => p.id === c.id));
  const selected = [...storyPeople, ...freshPeople].find((c) => c.id === target);
  const isNew = freshPeople.some((c) => c.id === target);
  const match = (c: typeof library[number]) => [c.card.name, ...c.aliases, ...c.card.tags].join(' ').toLocaleLowerCase().includes(query.toLocaleLowerCase());
  const changed = initial.current.sid !== sid || initial.current.identity !== current?.id;
  const confirm = async () => {
    if (!sid || !target || !initial.current.identity || changed || busy) return;
    setError('');
    const body = JSON.stringify([target, disposition, isNew ? brief : '']);
    if (request.current.body !== body) request.current = { body, key: createClientId() };
    try {
      await useChatStore.getState().switchPlayer({ target_character_id: target, previous_disposition: disposition,
        expected_branch_revision: initial.current.revision, expected_player_identity_id: initial.current.identity,
        idempotency_key: request.current.key, entry_brief: isNew ? brief : '' });
      onClose();
    } catch (cause) { setError(cause instanceof Error ? cause.message : '切换失败，请刷新后重试'); }
  };
  const personList = (items: typeof library, title: string) => <section className="player-switch-list">
    <h3>{title}</h3>
    {items.filter(match).map((c) => <button type="button" key={c.id} aria-pressed={target === c.id}
      disabled={busy || changed} onClick={() => { setTarget(c.id); setError(''); }}>
      <Avatar name={c.card.name} color={c.color} size="sm" characterId={c.id}
        src={playerAvatarUrl(sid, session?.player_identities?.find((x) => x.person_id === c.id && x.avatar_ref) ?? null)} />
      <span><strong>{c.card.name}</strong><small>{c.card.tags.join(' · ') || (c.present ? '人物资料已保存' : '当前不在场')}</small></span>
    </button>)}
    {!items.some(match) && <p className="player-switch-empty">没有匹配的人物</p>}
  </section>;
  return <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <Dialog.Portal><Dialog.Overlay className="v7-dialog-overlay" />
      <Dialog.Content className="v7-dialog-content player-switch-dialog" onEscapeKeyDown={(e) => { if (busy) e.preventDefault(); }}
        onPointerDownOutside={(e) => { if (busy) e.preventDefault(); }}>
        <header><div><Dialog.Title>切换控制角色</Dialog.Title>
          <Dialog.Description>开始以另一个人物的身份行动，双方保留各自的经历。</Dialog.Description></div>
          <button type="button" className="v7-btn v7-btn-soft" aria-label="关闭切换角色" disabled={busy} onClick={onClose}><X size={18} /></button>
        </header>
        <div className="player-switch-scroll">
          <label className="player-switch-search"><Search size={17} /><input autoFocus value={query} disabled={busy}
            onChange={(e) => setQuery(e.target.value)} placeholder="搜索姓名、别名或标签" aria-label="搜索切换人物" /></label>
          <div className="player-switch-columns"><div>{personList(storyPeople, '故事人物')}{personList(freshPeople, '角色库')}</div>
            <section className="player-switch-preview"><h3>{selected?.card.name ?? '选择要控制的人物'}</h3>
              {selected ? <><p>{selected.card.description || '（未填写背景）'}</p>
                {selected.card.traits && <details><summary>{selected.card.traits_label || '能力与实力'}</summary><p>{selected.card.traits}</p></details>}
                {selected.card.personality && <details><summary>性格</summary><p>{selected.card.personality}</p></details>}
              </> : <p>已有故事人物会沿用故事中的资料与知情范围。</p>}
            </section></div>
          <fieldset className="player-switch-fate" disabled={busy || changed}><legend>{current?.name ?? '原角色'}接下来如何参与？</legend>
            <label><input type="radio" name="previous-role" value="npc" checked={disposition === 'npc'} disabled={!current?.character}
              onChange={() => setDisposition('npc')} />留作 NPC，可点名回应</label>
            <label><input type="radio" name="previous-role" value="leave" checked={disposition === 'leave'} onChange={() => setDisposition('leave')} />暂时离场，以后可切回</label>
            {!current?.character && <p>原身份没有完整角色卡，本次只能离场。</p>}
          </fieldset>
          {isNew && <label className="v7-field">新人物的入场说明（可选）<textarea rows={3} value={brief} disabled={busy}
            onChange={(e) => setBrief(e.target.value)} placeholder="这个人物入场时知道什么？留空时只提供当前公开场景。" /></label>}
          {(error || changed) && <p className="v7-error" role="alert">{error || '当前故事或控制角色已变化，请关闭后重新打开。'}</p>}
        </div>
        <footer><span>{target ? `开始控制 ${selected?.card.name ?? ''}` : '先选择人物'}</span>
          <button type="button" className="v7-btn v7-btn-primary" disabled={!selected || busy || changed}
            onClick={() => void confirm()}><ArrowRightLeft size={16} />{busy ? '正在切换…' : '确认切换'}</button></footer>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
