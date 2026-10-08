import { WorkflowDialog } from '../design-system/WorkflowDialog';
import { createClientId } from '../utils/clientId.js';
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Bookmark, Search, X } from 'lucide-react';
import { storiesClient as api } from '../features/stories/storiesClient';
import { writeStoryLocation } from '../api/navigation';
import { useChatStore } from '../store/chatStore';
import type { StorySearchResult } from '../types';
import { ActionDialog } from '../design-system/ActionDialog';

export function StoryNavigationPanel({ storyId, onClose }: { storyId: string; onClose: () => void }) {
  const [query, setQuery] = useState('');
  const [submitted, setSubmitted] = useState('');
  const [onlyBookmarks, setOnlyBookmarks] = useState(false);
  const [onlyEvents, setOnlyEvents] = useState(false);
  const [branchId, setBranchId] = useState('');
  const [actor, setActor] = useState('');
  const [sceneId, setSceneId] = useState('');
  const [cursor, setCursor] = useState(0);
  const [rows, setRows] = useState<StorySearchResult[]>([]);
  const [error, setError] = useState('');
  const [editingBookmark, setEditingBookmark] = useState<string | null>(null);
  const [titleDraft, setTitleDraft] = useState('');
  const [tagsDraft, setTagsDraft] = useState('');
  const [forkTarget, setForkTarget] = useState<StorySearchResult | null>(null);
  const [forkTitle, setForkTitle] = useState('');
  const [forkWarning, setForkWarning] = useState<string | null>(null);
  const [forkPointLoading, setForkPointLoading] = useState(false);
  const [forkBusy, setForkBusy] = useState(false);
  useEffect(() => {
    if (!forkTarget) return;
    let active = true; setForkPointLoading(true); setForkWarning(null);
    void api.getBranchPoint(forkTarget.branch_id, forkTarget.message_id).then(point => {
      if (!active) return;
      if (!point.forkable) { setError(point.reason ?? '该节点无法分支'); setForkTarget(null); }
      setForkWarning(point.history_warning ?? null);
    }).catch(cause => { if (active) { setError(cause instanceof Error ? cause.message : '节点读取失败'); setForkTarget(null); } })
      .finally(() => { if (active) setForkPointLoading(false); });
    return () => { active = false; };
  }, [forkTarget]);
  const queryClient = useQueryClient();
  const { data: bookmarks = [] } = useQuery({ queryKey: ['story-bookmarks', storyId],
    queryFn: () => api.listStoryBookmarks(storyId), enabled: Boolean(storyId) });
  const branches = useChatStore((s) => s.sessions.filter((item) => (item.story_id ?? item.id) === storyId));
  const { data: rosters = [] } = useQuery({ queryKey: ['story-navigation-rosters', storyId, branches.map((b) => b.id).join(',')],
    queryFn: () => Promise.all(branches.map(async (b) => {
      const [view, scenes] = await Promise.all([api.getStoryView(b.id), api.listScenes(b.id)]);
      return { ...view, scenes: scenes.scenes };
    })), enabled: branches.length > 0 });
  const choices = rosters.filter((r) => !branchId || r.session.id === branchId);
  const actors = new Map<string, string>([['player', '玩家']]);
  const scenes = new Map<string, string>();
  for (const r of choices) {
    for (const c of r.characters) actors.set(c.id, c.card.name);
    for (const g of r.groups) actors.set(g.id, g.label);
    for (const identity of r.session.player_identities ?? []) actors.set(identity.person_id, identity.name);
    for (const scene of r.scenes) scenes.set(scene.id, scene.title);
  }
  const search = async (term: string, nextCursor = 0) => {
    setError('');
    try {
      const page = await api.searchStory(storyId, term, {
        bookmarked: onlyBookmarks, event: onlyEvents, cursor: nextCursor,
        branch_id: branchId || undefined, actor: actor.trim() || undefined,
        scene_id: sceneId.trim() || undefined,
      });
      setRows((current) => nextCursor ? [...current, ...page.results] : page.results);
      setCursor(page.next_cursor ?? -1);
      setSubmitted(term);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '搜索失败'); }
  };
  const locate = async (branchId: string, messageId: string) => {
    try {
      await useChatStore.getState().selectSession(branchId);
      writeStoryLocation(storyId, branchId, messageId);
      onClose();
    } catch (cause) { setError(cause instanceof Error ? cause.message : '定位失败'); }
  };
  const saveBookmark = async (branchId: string, bookmarkId: string) => {
    try {
      await api.patchBookmark(branchId, bookmarkId, {
        title: titleDraft.trim(), tags: tagsDraft.split(',').map((item) => item.trim()).filter(Boolean),
      });
      await queryClient.invalidateQueries({ queryKey: ['story-bookmarks', storyId] });
      setEditingBookmark(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '书签保存失败'); }
  };
  const fork = async () => {
    if (!forkTarget || !forkTitle.trim()) return;
    setForkBusy(true); setError('');
    try {
      const point = await api.getBranchPoint(forkTarget.branch_id, forkTarget.message_id);
      if (!point.forkable) throw new Error(point.reason ?? '这个节点无法开线');
      if ((point.history_warning ?? null) !== forkWarning) { setForkWarning(point.history_warning ?? null); throw new Error('历史记忆状态已变化，请检查提示后再次确认。'); }
      const result = await api.forkBranch(forkTarget.branch_id, {
        message_id: forkTarget.message_id, title: forkTitle.trim(),
        expected_revision: point.branch_revision,
        idempotency_key: createClientId(),
      });
      await useChatStore.getState().loadSessions();
      await useChatStore.getState().selectSession(result.branch_id);
      writeStoryLocation(storyId, result.branch_id, forkTarget.message_id);
      onClose();
    } catch (cause) { setError(cause instanceof Error ? cause.message : '创建分支失败'); }
    finally { setForkBusy(false); }
  };
  return <WorkflowDialog narrow title="剧情导航" onClose={onClose}>
    <aside className="flex h-full w-full max-w-[480px] flex-col overflow-y-auto border-l border-slate-200 bg-[var(--surface,#fff)] p-5 shadow-xl">
      <header className="mb-5 flex items-start justify-between"><div><span className="v7-eyebrow">STORY NAVIGATION</span><h2 className="text-xl font-semibold">剧情导航</h2><p className="text-sm text-slate-500">查找消息与书签；定位不会新建路线。</p></div>
        <button type="button" className="v7-icon-btn" aria-label="关闭剧情导航" onClick={onClose}><X size={18} /></button></header>
      <form className="mb-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); setRows([]); void search(query, 0); }}>
        <input className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索故事正文" aria-label="搜索故事正文" />
        <button type="submit" className="v7-btn v7-btn-primary"><Search size={16} /> 搜索</button>
      </form>
      <div className="mb-4 flex gap-4 text-sm"><label><input type="checkbox" checked={onlyBookmarks} onChange={(e) => setOnlyBookmarks(e.target.checked)} /> 只看书签</label><label><input type="checkbox" checked={onlyEvents} onChange={(e) => setOnlyEvents(e.target.checked)} /> 只看事件</label></div>
      <div className="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <label className="v7-field">路线<select aria-label="路线" value={branchId} onChange={(e) => setBranchId(e.target.value)}><option value="">全部路线</option>{branches.map((item) => <option key={item.id} value={item.id}>{item.branch_name ?? item.title}</option>)}</select></label>
        <label className="v7-field">人物<select aria-label="人物" value={actor} onChange={(e) => setActor(e.target.value)}><option value="">全部人物</option>{[...actors].map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
        <label className="v7-field">场景<select aria-label="场景" value={sceneId} onChange={(e) => setSceneId(e.target.value)}><option value="">全部场景</option>{[...scenes].map(([id, name]) => <option key={id} value={id}>{name || "未命名场景"}</option>)}</select></label>
      </div>
      {error && <p className="v7-error" role="alert">{error}</p>}
      {submitted && rows.length === 0 && <p className="mb-4 text-sm text-slate-500">暂无匹配消息。</p>}
      {rows.length > 0 && <section className="mb-5"><h3 className="mb-2 font-semibold">搜索结果</h3><div className="space-y-2">{rows.map((item) => <div key={`${item.branch_id}:${item.message_id}`} className="rounded-xl border border-slate-200 p-3 hover:border-indigo-300 hover:bg-indigo-50">
        <button type="button" className="block w-full text-left" onClick={() => void locate(item.branch_id, item.message_id)}><small className="text-slate-500">{item.branch_name} · {item.actor}</small><p className="mt-1 line-clamp-3 text-sm">{item.excerpt}</p></button>
        <button type="button" className="mt-2 text-xs text-indigo-600" onClick={() => { setForkTarget(item); setForkTitle(`${item.branch_name} · 新路线`); }}>从此处开线</button>
      </div>)}</div>{cursor >= 0 && <button type="button" className="v7-btn v7-btn-soft mt-3" onClick={() => void search(submitted, cursor)}>加载更多</button>}</section>}
      <section><h3 className="mb-2 flex items-center gap-2 font-semibold"><Bookmark size={16} /> 书签 · {bookmarks.length}</h3>
        {bookmarks.length === 0 ? <p className="text-sm text-slate-500">在消息下方点书签图标即可收藏。</p> : <div className="space-y-2">{bookmarks.map((item) => <div key={`${item.branch_id}:${item.id}`} className="rounded-xl border border-slate-200 p-3">
          {editingBookmark === item.id ? <div className="space-y-2">
            <label className="v7-field">标题<input value={titleDraft} onChange={(e) => setTitleDraft(e.target.value)} maxLength={200} /></label>
            <label className="v7-field">标签（逗号分隔）<input value={tagsDraft} onChange={(e) => setTagsDraft(e.target.value)} /></label>
            <div className="flex gap-2"><button type="button" className="v7-btn v7-btn-primary" onClick={() => void saveBookmark(item.branch_id, item.id)}>保存</button><button type="button" className="v7-btn v7-btn-soft" onClick={() => setEditingBookmark(null)}>取消</button></div>
          </div> : <><button type="button" disabled={!item.valid} className="block w-full text-left disabled:opacity-50" onClick={() => void locate(item.branch_id, item.message_id)}>
            <strong className="block text-sm">{item.title || '未命名书签'}</strong><small className="text-slate-500">{item.branch_name ?? item.branch_id}{!item.valid ? ' · 原消息已失效' : ''}</small>
            {!!item.tags.length && <small className="block text-indigo-600">{item.tags.join(' · ')}</small>}
          </button><button type="button" className="mt-2 text-xs text-indigo-600" onClick={() => { setEditingBookmark(item.id); setTitleDraft(item.title); setTagsDraft(item.tags.join(', ')); }}>编辑标题与标签</button></>}
        </div>)}</div>}</section>
      {forkTarget && <ActionDialog kind="fork" excerpt={forkTarget.excerpt} title={forkTitle}
        warning={forkWarning} onTitleChange={setForkTitle} error={error} busy={forkBusy || forkPointLoading} onSubmit={() => void fork()}
        onClose={() => setForkTarget(null)} />}
    </aside>
  </WorkflowDialog>;
}
