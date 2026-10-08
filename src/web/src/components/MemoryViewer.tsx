import { useQuery } from '@tanstack/react-query';
import { resourceQueries } from '../features/resources/resourceQueries';
import { WorkflowDialog } from '../design-system/WorkflowDialog';
import { useEffect, useRef, useState } from 'react';
import { Brain, Check, Pencil, RefreshCw, Star, Trash2, X, BookOpen } from 'lucide-react';
import { useChatStore } from '../store/chatStore';
import { useMemoryStore } from '../store/memoryStore';
import { useSettingsStore } from '../store/settingsStore';
import { useActiveCharacters } from '../store/useActiveCharacters';
import { storiesClient as api } from '../features/stories/storiesClient';
import { writeStoryLocation } from '../api/navigation';
import { Modal } from './common';
import type { MemoryRecord, Message } from '../types';

export const MEMORY_CATEGORIES = { experience: '共同经历', relationship: '关系与称呼', unfinished: '未完成事项' };
const STATUS = { open: '待完成', completed: '已完成', cancelled: '已取消', unknown: '未设定' };
const WINDOW_STATUS: Record<string, string> = { pending: '正在整理', failed: '整理失败，可重试', dirty: '来源改变，待重新整理', complete: '已整理' };

export function MemoryViewer({ onClose }: { onClose: () => void }) {
  const session = useChatStore((s) => s.sessions.find((x) => x.id === s.currentSessionId));
  const groups = useChatStore((s) => s.sessionGroups);
  const characters = useActiveCharacters();
  const individuals = new Map(characters.filter((c) => session?.character_ids.includes(c.id)).map((c) => [c.id, { id: c.id, name: c.card.name }]));
  for (const [id, c] of Object.entries(session?.player_people ?? {})) if (!individuals.has(id)) individuals.set(id, { id, name: c.card.name });
  const owners = [...individuals.values(),
    ...groups.map((g) => ({ id: g.id, name: `${g.label}（群体）` }))];
  const { records, loading, error, select, remove, edit, consolidateNow } = useMemoryStore();
  const settings = useSettingsStore((s) => s.settings);
  const loadSettings = useSettingsStore((s) => s.load);
  const consolidationOn = Boolean(settings?.memory_consolidation_enabled);
  const [activeCid, setActiveCid] = useState('');
  const [editing, setEditing] = useState<MemoryRecord | null>(null);
  const [editText, setEditText] = useState('');
  const [confirmDelete, setConfirmDelete] = useState<MemoryRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [operationError, setOperationError] = useState('');
  const [refreshError, setRefreshError] = useState('');
  const [category, setCategory] = useState('all');
  const [importantOnly, setImportantOnly] = useState(false);
  const [query, setQuery] = useState('');
  const [scope, setScope] = useState<'all' | 'local' | 'inherited'>('all');
  const windowsQuery = useQuery({ ...resourceQueries.memoryWindows(session?.id ?? '', activeCid), enabled: !!session && !!activeCid, refetchInterval: 4000 });
  const windows = windowsQuery.data ?? [];
  const [rangeOpen, setRangeOpen] = useState(false);
  const [start, setStart] = useState('1');
  const [end, setEnd] = useState('');
  const [sources, setSources] = useState<Message[] | null>(null);
  const viewEpoch = useRef(0);
  const jobOperationId = useMemoryStore(state => state.jobOperationId);
  const memoryJob = useQuery({ ...resourceQueries.memoryJob(session?.id ?? '', jobOperationId ?? ''), enabled: !!session && !!jobOperationId, refetchInterval: query => query.state.data?.status === 'pending' ? 2000 : false });
  const maxTurn = session?.turn ?? 0;
  const shownRecords = records.filter((r) => r.content.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()) && !r.invalidated && (category === 'all' || (r.category ?? 'experience') === category)
    && (!importantOnly || r.important) && (scope === 'all' || (scope === 'inherited' ? !!r.inherited_from_id : !r.inherited_from_id)));

  useEffect(() => {
    if (!owners.some((c) => c.id === activeCid)) setActiveCid(owners[0]?.id ?? '');
  }, [owners, activeCid]);

  useEffect(() => {
    if (!settings) void loadSettings();
  }, [settings, loadSettings]);

  useEffect(() => {
    viewEpoch.current += 1;
    setEditing(null); setConfirmDelete(null); setSources(null); setNotice(''); setOperationError(''); setRefreshError(''); setBusy(false);
    if (!activeCid || !session) { useMemoryStore.getState().clear(); return; }
    const sid = session.id;
    let cancelled = false;
    let running = false;
    const refresh = async () => {
      if (running || cancelled) return;
      running = true;
      try {
        await select(activeCid, sid);
        if (!cancelled) setRefreshError('');
      } catch (reason) {
        if (!cancelled) setRefreshError(reason instanceof Error ? reason.message : '整理状态读取失败');
      } finally { running = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 4000);
    return () => { viewEpoch.current += 1; cancelled = true; window.clearInterval(timer); useMemoryStore.getState().clear(); };
  }, [activeCid, session?.id, select]); // eslint-disable-line react-hooks/exhaustive-deps

  const perform = async (operation: (isCurrent: () => boolean) => Promise<void>) => {
    setOperationError(''); setBusy(true);
    const key = `${session?.id}/${activeCid}`;
    const epoch = viewEpoch.current;
    const isCurrent = () => viewEpoch.current === epoch && `${useMemoryStore.getState().sessionId}/${useMemoryStore.getState().characterId}` === key;
    try { await operation(isCurrent); }
    catch (reason) {
      if (isCurrent()) setOperationError(reason instanceof Error ? reason.message : '操作失败');
    } finally {
      if (isCurrent()) setBusy(false);
    }
  };
  const organize = () => perform(async (isCurrent) => {
    if (!session || !activeCid) return;
    const from = rangeOpen ? Number(start) : undefined;
    const to = rangeOpen ? Number(end || maxTurn) : undefined;
    if (rangeOpen && (!Number.isInteger(from) || !Number.isInteger(to) || from! < 1 || to! < from! || to! > maxTurn)) throw new Error('请输入当前故事范围内的有效轮次');
    await consolidateNow(session.id, activeCid, from, to);
    if (isCurrent())
      setNotice('已开始后台整理，完成后自动更新；可以继续聊天。');
  });

  return <WorkflowDialog narrow title="角色记忆" onClose={onClose}>
    <aside className="v7-memory-panel" onClick={(e) => e.stopPropagation()}>
      {windowsQuery.error && <p role="alert">整理状态读取失败。<button onClick={() => void windowsQuery.refetch()}>重新读取</button></p>}
      {memoryJob.data && <p role={memoryJob.data.status === 'failed' ? 'alert' : 'status'}>{memoryJob.data.status === 'pending' ? '记忆正在整理…' : memoryJob.data.status === 'committed' ? '记忆整理已完成。' : `记忆整理未完成：${memoryJob.data.error ?? '请检查结果后决定新的尝试。'}`}</p>}
      {memoryJob.error && <p role="alert">整理状态读取失败。<button onClick={() => void memoryJob.refetch()}>重新读取状态</button></p>}
      <header className="v7-memory-head">
        <div className="v7-memory-head-title"><h2>角色记忆</h2><button onClick={onClose} aria-label="关闭角色记忆"><X size={19} /></button></div>
        <p>当前路线：{session?.branch_name ?? session?.title ?? '未选择故事'}</p>
      </header>
      <div className="v7-memory-intro">
        <label className="v7-memory-intro-line"><span>知情角色</span><select aria-label="选择角色" value={activeCid} onChange={(e) => setActiveCid(e.target.value)} disabled={!owners.length}>
          {!owners.length && <option value="">暂无角色</option>}{owners.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select></label>
        <button className="v7-memory-refresh" disabled={!session || !activeCid || busy || maxTurn < 1 || !consolidationOn} title={consolidationOn ? undefined : '到设置的高级选项打开记忆整理'} onClick={() => void organize()}>
          <RefreshCw size={15} className={busy ? 'animate-spin' : ''} />整理记忆
        </button>
      </div>
      <div className="v7-memory-controls">
        <button className="v7-memory-refresh" aria-expanded={rangeOpen} disabled={!consolidationOn} title={consolidationOn ? undefined : '到设置的高级选项打开记忆整理'} onClick={() => setRangeOpen(!rangeOpen)}>整理指定轮次</button>
        {rangeOpen && <div className="v7-memory-range">
          <label>从第<input aria-label="整理起始轮次" type="number" min={1} max={maxTurn} value={start} onChange={(e) => setStart(e.target.value)} /></label>
          <label>到第<input aria-label="整理结束轮次" type="number" min={1} max={maxTurn} value={end} placeholder={String(maxTurn)} onChange={(e) => setEnd(e.target.value)} /></label><span>轮</span>
        </div>}
        <p>重要记忆优先参与召回；需要每轮提醒的内容请使用「固定信息」。</p>
        {!consolidationOn && <p>记忆整理已关闭。到设置 → 高级选项打开后再整理。已经记下的记忆仍会在回复时召回。</p>}
        {notice && <p role="status">{notice}</p>}
        {(error || operationError || refreshError) && <p className="v7-memory-warning" role="alert">{operationError || error || refreshError}</p>}
        {windows.filter((w) => w.status !== 'complete').map((w) => <p key={`${w.start}-${w.end}`} role="status">第 {w.start}–{w.end} 轮：{WINDOW_STATUS[w.status] ?? w.status}</p>)}
      </div>
      <div className="v7-memory-toolbar">
        <label className="v7-field">查找记忆<input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="输入记忆中的词句" /></label>
        <div className="v7-memory-section-title"><h3>{owners.find((c) => c.id === activeCid)?.name ?? '角色'}的记忆</h3><span>{shownRecords.length} 条</span></div>
        <div className="v7-memory-filters" role="group" aria-label="记忆分类">
          <button onClick={() => setCategory('all')} aria-pressed={category === 'all'} className={category === 'all' ? 'is-active' : ''}>全部</button>
          {Object.entries(MEMORY_CATEGORIES).map(([id, name]) => <button key={id} onClick={() => setCategory(id)} aria-pressed={category === id} className={category === id ? 'is-active' : ''}>{name}</button>)}
        </div>
        <div className="v7-memory-options"><label><input type="checkbox" checked={importantOnly} onChange={(e) => setImportantOnly(e.target.checked)} />只看重要</label>
          <select aria-label="记忆来源范围" value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}><option value="all">本线与继承</option><option value="local">本线新增</option><option value="inherited">父线继承</option></select>
        </div>
      </div>
      <div className="v7-memory-list">
        {loading ? <div className="v7-memory-empty"><Brain size={24} /><h3>正在读取记忆</h3></div> : !shownRecords.length && <div className="v7-memory-empty"><Brain size={25} /><h3>这个范围暂无记忆</h3><p>{consolidationOn ? '新对话会按一小段自动整理。旧经历可用「整理指定轮次」补齐，也可以在消息操作中选择「记住这件事」。' : '已经记下的内容仍会在回复时召回。也可以在消息操作中选择「记住这件事」。'}</p></div>}
        {!loading && shownRecords.map((r) => <article key={r.id} className="v7-memory-record">
          <div className="v7-memory-record-meta"><span className="v7-memory-kind">{MEMORY_CATEGORIES[r.category ?? 'experience']}</span><span>第 {r.turn_start}–{r.turn_end} 轮</span>{r.inherited_from_id && <span>父线继承</span>}</div>
          <p>{r.content}</p>
          {r.category === 'unfinished' && <p className="v7-memory-state">事项状态：{STATUS[r.matter_status ?? 'unknown']}</p>}
          {r.source_changed && <p className="v7-memory-warning">来源已改变。这条手工记忆保留，但暂不召回；编辑确认后重新启用。</p>}
          <div className="v7-memory-record-foot"><span>修订 {r.revision ?? 1}{r.manually_revised ? ' · 玩家确认' : ''}</span><div>
            <button aria-pressed={!!r.important} aria-label={r.important ? '取消重要标记' : '标记重要'} disabled={busy} onClick={() => void perform(() => edit(r.id, { important: !r.important }))}><Star size={14} fill={r.important ? 'currentColor' : 'none'} />重要</button>
            <button disabled={busy} onClick={() => void perform(async (isCurrent) => {
              if (!session) return;
              const next = await api.memorySources(session.id, activeCid, r.id);
              if (isCurrent()) setSources(next);
            })}><BookOpen size={14} />来源</button>
            <button onClick={() => { setEditing(r); setEditText(r.content); setConfirmDelete(null); }}><Pencil size={13} />纠正</button>
            <button onClick={() => { setConfirmDelete(r); setEditing(null); }}><Trash2 size={13} />删除</button>
          </div></div>
        </article>)}
      </div>
      {editing && <div className="v7-memory-edit">
        <div className="v7-memory-edit-title"><strong>纠正记忆</strong><button onClick={() => setEditing(null)} aria-label="取消纠正"><X size={17} /></button></div>
        <select aria-label="记忆分类" value={editing.category ?? 'experience'} onChange={(e) => setEditing({ ...editing, category: e.target.value as MemoryRecord['category'] })}>{Object.entries(MEMORY_CATEGORIES).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select>
        {editing.category === 'unfinished' && <select aria-label="事项状态" value={editing.matter_status ?? 'open'} onChange={(e) => setEditing({ ...editing, matter_status: e.target.value as MemoryRecord['matter_status'] })}>{Object.entries(STATUS).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select>}
        <textarea aria-label="记忆内容" value={editText} rows={4} onChange={(e) => setEditText(e.target.value)} />
        <div className="v7-memory-edit-foot"><span>自动整理不会覆盖你的纠正。</span><button className="v7-memory-save" disabled={busy || !editText.trim()} onClick={() => void perform(async (isCurrent) => {
          const recordId = editing.id;
          await edit(recordId, { content: editText, category: editing.category ?? 'experience', matter_status: editing.matter_status, expected_revision: editing.revision });
          if (isCurrent()) setEditing((current) => current?.id === recordId ? null : current);
        })}><Check size={15} />保存</button></div>
      </div>}
      {confirmDelete && <div className="v7-memory-delete"><strong>删除这条记忆？</strong><p>原始对话保留，后续回复不再召回这条记忆。</p><div><button onClick={() => setConfirmDelete(null)}>保留</button><button disabled={busy} onClick={() => void perform(async (isCurrent) => { const id = confirmDelete.id; await remove(id, confirmDelete.revision); if (isCurrent()) setConfirmDelete((current) => current?.id === id ? null : current); })}>确认删除</button></div></div>}
      {sources && <Modal title="记忆来源" onClose={() => setSources(null)}>
        {!sources.length && <p>来源已删除或当前角色不可见。</p>}
        <div className="v7-memory-sources">{sources.map((m) => <article key={m.id}><strong>第 {m.turn} 轮 · {m.actor === 'player' ? '你' : owners.find((c) => c.id === m.actor)?.name ?? '旁白'}</strong><p>{m.content}</p><button className="v7-memory-refresh" onClick={() => {
          if (session) writeStoryLocation(session.story_id ?? session.id, session.id, m.id);
          onClose();
        }}>跳到原消息</button></article>)}</div>
      </Modal>}
    </aside>
  </WorkflowDialog>;
}
