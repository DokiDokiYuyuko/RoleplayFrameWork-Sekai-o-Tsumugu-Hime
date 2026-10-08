import { useState } from 'react';
import { storiesClient as api } from '../features/stories/storiesClient';
import { useChatStore } from '../store/chatStore';
import { useActiveCharacters } from '../store/useActiveCharacters';
import { useMemoryStore } from '../store/memoryStore';
import { Modal } from './common';
import { MEMORY_CATEGORIES } from './MemoryViewer';
import type { Message, MemoryRecord } from '../types';
import { canPersonSee } from '../utils/playerIdentity.js';

export function RememberMemoryModal({ message, onClose }: { message: Message; onClose: () => void }) {
  const sessionId = useChatStore((s) => s.currentSessionId);
  const session = useChatStore((s) => s.sessions.find((item) => item.id === sessionId));
  const groups = useChatStore((s) => s.sessionGroups);
  const characters = useActiveCharacters();
  const canSee = (id: string) => canPersonSee(message, id);
  const individuals = new Map(characters.filter((c) => session?.character_ids.includes(c.id)).map((c) => [c.id, { id: c.id, name: c.card.name }]));
  for (const [id, c] of Object.entries(session?.player_people ?? {})) individuals.set(id, { id, name: c.card.name });
  const owners = [...individuals.values()].filter((c) => canSee(c.id)).concat(
    ...groups.filter((g) => canSee(g.id) && message.seq >= g.joined_seq && message.scene_id === g.scene_id).map((g) => ({ id: g.id, name: `${g.label}（群体）` })));
  const ownerId = message.person_id ?? message.actor;
  const [selected, setSelected] = useState<string[]>(() => owners.some((c) => c.id === ownerId) ? [ownerId] : []);
  const [content, setContent] = useState(message.content);
  const [category, setCategory] = useState<NonNullable<MemoryRecord['category']>>('experience');
  const [status, setStatus] = useState<NonNullable<MemoryRecord['matter_status']>>('open');
  const [important, setImportant] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const save = async () => {
    if (!sessionId || !content.trim() || !selected.length || saving) return;
    setSaving(true); setError('');
    try {
      await api.rememberEvent(sessionId, { character_ids: selected, content: content.trim(), category, important,
        matter_status: category === 'unfinished' ? status : 'unknown', source_message_ids: [message.id],
        source_fingerprints: { [message.id]: message.fingerprint } });
      const store = useMemoryStore.getState();
      if (store.sessionId === sessionId && store.characterId) await store.select(store.characterId, sessionId);
      onClose();
    } catch (reason) { setError(reason instanceof Error ? reason.message : '保存失败'); }
    finally { setSaving(false); }
  };
  return <Modal title="记住这件事" onClose={onClose}>
    <div className="v7-remember-form">
      <p>选择知道这件事的角色，确认记忆正文。重要标记用于优先召回，不是每轮固定提示。</p>
      <fieldset><legend>知情角色</legend><div className="v7-remember-owners">{owners.map((c) => <label key={c.id}><input type="checkbox" checked={selected.includes(c.id)} onChange={(e) => setSelected((old) => e.target.checked ? [...old, c.id] : old.filter((id) => id !== c.id))} />{c.name}</label>)}</div>{!owners.length && <p>没有可见这条消息的角色。</p>}</fieldset>
      <label>记忆分类<select value={category} onChange={(e) => setCategory(e.target.value as typeof category)}>{Object.entries(MEMORY_CATEGORIES).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
      {category === 'unfinished' && <label>事项状态<select value={status} onChange={(e) => setStatus(e.target.value as typeof status)}><option value="open">待完成</option><option value="completed">已完成</option><option value="cancelled">已取消</option></select></label>}
      <label>记忆正文<textarea autoFocus rows={7} value={content} onChange={(e) => setContent(e.target.value)} /></label>
      <label className="v7-remember-important"><input type="checkbox" checked={important} onChange={(e) => setImportant(e.target.checked)} />标记为重要</label>
      {error && <p role="alert" className="v7-memory-warning">{error}</p>}
      <div className="v7-remember-actions"><button className="v7-memory-refresh" onClick={onClose}>取消</button><button className="v7-memory-save" disabled={saving || !selected.length || !content.trim()} onClick={() => void save()}>{saving ? '保存中…' : '保存记忆'}</button></div>
    </div>
  </Modal>;
}
