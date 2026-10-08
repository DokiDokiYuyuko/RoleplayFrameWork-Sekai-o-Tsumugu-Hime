import { storyRuntime } from '../features/stories/storyRuntime';
import { useEffect, useMemo, useState } from 'react';
import { Plus, UserRound, UsersRound } from 'lucide-react';
import { storiesClient as api } from '../features/stories/storiesClient';
import { useChatStore } from '../store/chatStore';
import { useCharacterStore } from '../store/characterStore';

export function AssistStoryCharacters() {
  const sessionId = useChatStore((state) => state.currentSessionId);
  const sending = useChatStore((state) => state.sending);
  const sessionCharacters = useChatStore((state) => state.sessionCharacters);
  const session = useChatStore((state) => state.sessions.find((item) => item.id === sessionId));
  const library = useCharacterStore((state) => state.characters);
  const loadLibrary = useCharacterStore((state) => state.load);
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [entryBrief, setEntryBrief] = useState('');
  const [joinScene, setJoinScene] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    if (library.length === 0) void loadLibrary();
  }, [library.length, loadLibrary]);

  const memberIds = useMemo(() => new Set(sessionCharacters.map((character) => character.id)), [sessionCharacters]);
  const candidates = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return library.filter((character) =>
      !memberIds.has(character.id)
      && character.id !== session?.player_character_id
      && (!needle || [character.card.name, ...character.aliases, ...character.card.tags]
        .some((value) => value.toLocaleLowerCase().includes(needle))),
    );
  }, [library, memberIds, query, session?.player_character_id]);
  const selected = library.find((character) => character.id === selectedId);

  const add = async () => {
    if (!sessionId || !selected || busy || sending) return;
    const ownsBranch = storyRuntime.capture(sessionId);
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await api.addStoryParticipant(sessionId, {
        character_id: selected.id,
        join_current_scene: joinScene,
        entry_brief: entryBrief.trim(),
      });
      const snapshot = await api.getStoryView(sessionId);
      if (ownsBranch()) {
        storyRuntime.applyView(sessionId, snapshot);
      }
      setNotice(result.added ? `${selected.card.name} 已加入故事${joinScene ? '并进入当前场景' : ''}。` : `${selected.card.name} 已在故事人物中。`);
      setSelectedId(''); setQuery(''); setEntryBrief('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message.replace(/^\d+:\s*/, '') : '加入人物失败，请重试');
    } finally {
      setBusy(false);
    }
  };

  return <section className="v7-panel-section v7-roster-section" data-testid="assist-story-characters">
    <div className="v7-panel-section-head">
      <div>
        <h3>故事人物</h3>
        <p>把素材库中的角色卡加入当前故事，之后可直接 @ 点名。</p>
      </div>
      <span className="v7-roster-count"><UsersRound size={13} /> {sessionCharacters.length}/20</span>
    </div>
    {error && <p className="v7-group-error" role="alert">{error}</p>}
    {notice && <p className="v7-roster-notice" role="status">{notice}</p>}
    <div className="v7-roster-members">
      {sessionCharacters.map((character) => <span key={character.id} title={character.card.name}>
        <UserRound size={12} />{character.card.name}
      </span>)}
      {sessionCharacters.length === 0 && <span className="v7-panel-muted">当前故事还没有人物</span>}
    </div>
    <label className="v7-roster-search">
      <span>从素材库选择角色卡</span>
      <input value={query} onChange={(event) => { setQuery(event.target.value); setSelectedId(''); }}
        placeholder="搜索名称、别名或标签…" disabled={!sessionId || busy || sending} />
    </label>
    {query.trim() && !selected && <div className="v7-roster-results" role="listbox" aria-label="角色卡候选">
      {candidates.slice(0, 8).map((character) => <button type="button" role="option" aria-selected="false"
        key={character.id} onClick={() => { setSelectedId(character.id); setQuery(character.card.name); }}>
        <UserRound size={14} /><span>{character.card.name}</span>
        {character.aliases.length > 0 && <small>{character.aliases.slice(0, 2).join('、')}</small>}
      </button>)}
      {candidates.length === 0 && <p>没有找到可加入的人物</p>}
    </div>}
    {selected && <div className="v7-roster-preview">
      <strong>{selected.card.name}</strong>
      <p>{selected.card.description || selected.card.personality || '角色卡已选中'}</p>
      <label className="v7-roster-toggle"><input type="checkbox" checked={joinScene} onChange={(event) => setJoinScene(event.target.checked)} />
        同时加入当前场景</label>
      <label className="v7-roster-brief"><span>入场简报（可选）</span>
        <textarea rows={3} maxLength={4000} value={entryBrief} onChange={(event) => setEntryBrief(event.target.value)}
          placeholder="留空时使用当前场景的标题和描述；旧对话不会自动交给新人物。" />
      </label>
      <button type="button" className="v7-group-add" onClick={() => void add()} disabled={busy || sending || sessionCharacters.length >= 20}>
        <Plus size={14} />{busy ? '正在加入…' : joinScene ? '加入当前场景' : '加入故事'}
      </button>
    </div>}
    {!selected && !query.trim() && candidates.length === 0 && <p className="v7-panel-muted v7-roster-empty">素材库没有可加入的角色卡，或故事已达到 20 人上限。</p>}
  </section>;
}
