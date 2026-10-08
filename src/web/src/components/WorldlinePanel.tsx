import { useWorldlinePages } from '../features/worldline/useWorldlinePages';
import { SurfaceDialog } from '../design-system/SurfaceDialog';
import { createClientId } from '../utils/clientId.js';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { storiesClient as api } from '../features/stories/storiesClient';
import { writeStoryLocation } from '../api/navigation';
import { useChatStore } from '../store/chatStore';
import type { BranchPoint, BranchSummary, Message } from '../types';

type Selection = { branch: BranchSummary; messageId: string | null };
type ActionForm = { kind: 'fork' | 'rename' | 'event'; value: string; eventId?: string };

export function WorldlinePanel({ storyId, currentBranchId, onClose }: {
  storyId: string; currentBranchId: string; onClose: () => void;
}) {
  const [view, setView] = useState<'graph' | 'list'>('graph');
  const [query, setQuery] = useState('');
  const [selection, setSelection] = useState<Selection | null>(null);
  const [point, setPoint] = useState<BranchPoint | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<Message[] | null>(null);
  const [historyAnchor, setHistoryAnchor] = useState<string | null>(null);
  const [action, setAction] = useState<ActionForm | null>(null);
  const graphRef = useRef<HTMLDivElement>(null);
  const requestToken = useRef(0);
  const [edges, setEdges] = useState<{ from: string; to: string; d: string }[]>([]);
  const [graphSize, setGraphSize] = useState({ width: 0, height: 0 });

  const [search, setSearch] = useState('');
  const worldline = useWorldlinePages(storyId, search);
  const { branches, nextCursor } = worldline;
  const fetchPage = async (_cursor = 0, append = false) => { setBusy(true); try { if (append) await worldline.loadMore(); else await worldline.refresh(); } catch (cause) { setError(cause instanceof Error ? cause.message : '世界线加载失败'); } finally { setBusy(false); } };
  useEffect(() => { const timer = setTimeout(() => setSearch(query), query ? 200 : 0); return () => { clearTimeout(timer); requestToken.current++; }; }, [query]);
  useEffect(() => { if (worldline.error) setError(worldline.error.message); }, [worldline.error]);
  useEffect(() => {
    if (!selection?.messageId) { setPoint(null); return; }
    let live = true;
    void api.getBranchPoint(selection.branch.id, selection.messageId)
      .then((result) => { if (live) setPoint(result); })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : '节点预览失败'); });
    return () => { live = false; };
  }, [selection]);

  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return branches;
    return branches.filter((branch) =>
      branch.name.toLocaleLowerCase().includes(needle)
      || branch.events.some((event) => event.title.toLocaleLowerCase().includes(needle)));
  }, [branches, query]);
  const byId = useMemo(() => new Map(branches.map((branch) => [branch.id, branch])), [branches]);
  const levels = useMemo(() => {
    const depth = (branch: BranchSummary): number => {
      let n = 0; let parent = branch.parent_branch_id; const seen = new Set([branch.id]);
      while (parent && byId.has(parent) && !seen.has(parent)) { n++; seen.add(parent); parent = byId.get(parent)!.parent_branch_id; }
      return n;
    };
    return visible.reduce<Record<number, BranchSummary[]>>((result, branch) => {
      const n = depth(branch); (result[n] ??= []).push(branch); return result;
    }, {});
  }, [visible, byId]);

  useLayoutEffect(() => {
    if (view !== 'graph' || !graphRef.current) return;
    const graph = graphRef.current;
    const measure = () => {
      const base = graph.getBoundingClientRect();
      const nodes = new Map<string, DOMRect>();
      graph.querySelectorAll<HTMLElement>('[data-branch-node]').forEach((element) => {
        nodes.set(element.dataset.branchNode!, element.getBoundingClientRect());
      });
      setGraphSize({ width: graph.scrollWidth, height: graph.scrollHeight });
      setEdges(visible.flatMap((branch) => {
        const parent = nodes.get(branch.parent_branch_id ?? '');
        const child = nodes.get(branch.id);
        if (!parent || !child) return [];
        const x1 = parent.right - base.left; const y1 = parent.top + parent.height / 2 - base.top;
        const x2 = child.left - base.left; const y2 = child.top + child.height / 2 - base.top;
        const bend = Math.max(18, (x2 - x1) / 2);
        return [{ from: branch.parent_branch_id!, to: branch.id, d: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}` }];
      }));
    };
    measure();
    const observer = new ResizeObserver(measure); observer.observe(graph);
    return () => observer.disconnect();
  }, [visible, view]);

  const select = (branch: BranchSummary, messageId: string | null) => { setSelection({ branch, messageId }); setPoint(null); setHistory(null); setAction(null); };
  const switchBranch = async (branchId: string) => {
    try {
      setBusy(true);
      await useChatStore.getState().loadSessions();
      await useChatStore.getState().selectSession(branchId);
      onClose();
    } catch (e) { setError(e instanceof Error ? e.message : '切换路线失败'); }
    finally { setBusy(false); }
  };
  const locate = async () => {
    if (!selection?.messageId) return;
    if (selection.branch.id === currentBranchId) {
      writeStoryLocation(storyId, currentBranchId, selection.messageId);
      onClose(); return;
    }
    try {
      setBusy(true);
      const { messages } = await api.getStoryView(selection.branch.id, { around: selection.messageId });
      setHistory(messages); setHistoryAnchor(selection.messageId);
      requestAnimationFrame(() => document.getElementById(`worldline-history-${selection.messageId}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }));
    } catch (e) { setError(e instanceof Error ? e.message : '消息定位失败'); }
    finally { setBusy(false); }
  };
  const submitAction = async () => {
    if (!selection || !action || !action.value.trim()) return;
    try {
      setBusy(true);
      if (action.kind === 'fork') {
        if (!selection.messageId || !point?.forkable) return;
        const currentPoint = await api.getBranchPoint(selection.branch.id, selection.messageId);
        if (!currentPoint.forkable) throw new Error(currentPoint.reason ?? '该节点无法安全分支');
        if ((currentPoint.history_warning ?? null) !== (point.history_warning ?? null)) { setPoint(currentPoint); throw new Error('历史记忆状态已变化，请检查提示后再次确认。'); }
        const result = await api.forkBranch(selection.branch.id, {
          message_id: selection.messageId, title: action.value.trim(),
          expected_revision: currentPoint.branch_revision,
          idempotency_key: createClientId(),
        });
        setAction(null);
        await switchBranch(result.branch_id);
      } else if (action.kind === 'rename') {
        const snapshot = await api.getSessionSetup(selection.branch.id);
        await api.patchBranch(selection.branch.id, { name: action.value.trim(), expected_revision: snapshot.meta.branch_revision });
        setAction(null); setSelection(null);
        await useChatStore.getState().loadSessions();
        await fetchPage();
      } else if (action.eventId) {
        await api.updateStoryEvent(selection.branch.id, action.eventId, { title: action.value.trim() });
        setAction(null); setSelection(null);
        await fetchPage();
      }
    } catch (e) { setError(e instanceof Error ? e.message : '操作失败'); }
    finally { setBusy(false); }
  };
  const archive = async () => {
    if (!selection) return;
    try {
      const snapshot = await api.getSessionSetup(selection.branch.id);
      await api.patchBranch(selection.branch.id, {
        archived: !selection.branch.archived,
        expected_revision: snapshot.meta.branch_revision,
      });
      await fetchPage(); setSelection(null);
    } catch (e) { setError(e instanceof Error ? e.message : '归档失败'); }
  };
  const deleteEvent = async (eventId: string) => {
    if (!selection) return;
    try {
      setBusy(true);
      await api.deleteStoryEvent(selection.branch.id, eventId);
      await fetchPage(); setSelection(null);
    } catch (e) { setError(e instanceof Error ? e.message : '事件更新失败'); }
    finally { setBusy(false); }
  };

  const node = (branch: BranchSummary) => (
    <div key={branch.id} className={`w-56 rounded-xl border p-3 text-left shadow-sm ${branch.id === currentBranchId ? 'border-indigo-400 bg-indigo-50' : 'border-slate-200 bg-white'} ${branch.archived ? 'opacity-60' : ''}`}>
      <button type="button" className="w-full text-left" onClick={() => select(branch, branch.last_message_id)}>
        <strong className="block truncate text-sm text-slate-800">{branch.name}</strong>
        <span className="mt-1 block text-[11px] text-slate-500">{branch.message_count} 条消息 · {branch.events.length} 个事件{branch.archived ? ' · 已归档' : ''}</span>
      </button>
      <div className="mt-2 flex flex-wrap gap-1">
        {branch.events.slice(0, 5).map((event) => <button key={event.id} type="button" onClick={() => select(branch, event.anchor_message_id)} className="max-w-full truncate rounded-full bg-amber-50 px-2 py-0.5 text-[10px] text-amber-700" title={event.title}>◆ {event.title}</button>)}
      </div>
    </div>
  );

  return (
    <SurfaceDialog title={"世界线"} className="fixed inset-0 z-40 flex bg-slate-900/40 p-3 sm:p-8" onClose={onClose} busy={busy} nested={false}>
      <div className="flex min-h-0 w-full flex-col overflow-hidden rounded-2xl bg-slate-50 shadow-xl">
        <header className="flex flex-wrap items-center justify-between gap-2 border-b bg-white px-5 py-3">
          <div><h2 className="text-base font-semibold text-slate-800">世界线</h2><p className="text-xs text-slate-500">点击节点预览；切换路线、定位消息与开新线是独立操作。</p></div>
          <div className="flex items-center gap-2"><button type="button" onClick={() => setView(view === 'graph' ? 'list' : 'graph')} className="rounded-lg border px-3 py-1.5 text-xs">{view === 'graph' ? '树形列表' : '关系图'}</button><button type="button" onClick={onClose} className="rounded-lg border px-3 py-1.5 text-xs">关闭</button></div>
        </header>
        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <div className="min-h-0 min-w-0 flex-1 overflow-auto p-4">
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索路线或剧情事件" className="mb-4 w-full max-w-md rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm" />
            {view === 'graph' ? (
              <div ref={graphRef} className="relative flex min-w-max items-start gap-10 overflow-auto pb-4">
                <svg width={graphSize.width} height={graphSize.height} className="pointer-events-none absolute left-0 top-0" aria-hidden="true">{edges.map((edge) => <path key={`${edge.from}-${edge.to}`} d={edge.d} fill="none" stroke="#a5b4fc" strokeWidth="2" />)}</svg>
                {Object.entries(levels).sort((a, b) => Number(a[0]) - Number(b[0])).map(([level, nodes]) => <div key={level} className="relative z-10 flex flex-col gap-4"><span className="text-xs font-medium text-slate-400">{level === '0' ? '起点' : `第 ${level} 层分支`}</span>{nodes.map((branch) => <div key={branch.id} data-branch-node={branch.id}>{node(branch)}</div>)}</div>)}
              </div>
            ) : <div className="space-y-2">{visible.map((branch) => <div key={branch.id} className="flex items-start gap-2"><span className="mt-5 text-xs text-slate-400">{branch.parent_branch_id ? '↳' : '●'}</span>{node(branch)}</div>)}</div>}
            {visible.length === 0 && !busy && <p className="text-sm text-slate-400">没有匹配的路线或事件</p>}
            {nextCursor && <button type="button" disabled={busy} onClick={() => void fetchPage(Number(nextCursor), true)} className="mt-4 rounded-lg border bg-white px-3 py-2 text-xs">加载更多路线</button>}
          </div>
          <aside className="w-full shrink-0 border-t bg-white p-4 md:w-80 md:border-l md:border-t-0 md:overflow-auto">
            {selection ? <>
              <h3 className="font-semibold text-slate-800">{selection.branch.name}</h3>
              <p className="mt-1 text-xs text-slate-500">{selection.branch.parent_branch_id ? `来自 ${byId.get(selection.branch.parent_branch_id)?.name ?? '上一条路线'}` : '故事起点'}</p>
              {selection.messageId && <div className="mt-4 rounded-lg bg-slate-50 p-3 text-xs text-slate-600"><strong>消息预览</strong>{point ? <><p className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap">{point.message.content}</p>{point.history_warning && <p role="alert" className="mt-2">{point.history_warning}</p>}{!point.forkable && <p className="mt-2 text-amber-700">{point.reason}</p>}</> : <p className="mt-1">加载中…</p>}</div>}
              <div className="mt-4 flex flex-wrap gap-2 text-xs">
                <button type="button" disabled={busy} onClick={() => void switchBranch(selection.branch.id)} className="rounded-lg border px-2 py-1.5">切换路线</button>
                {selection.messageId && <button type="button" disabled={busy} onClick={() => void locate()} className="rounded-lg border px-2 py-1.5">定位消息</button>}
                {selection.messageId && <button type="button" disabled={busy || !point?.forkable} onClick={() => setAction({ kind: 'fork', value: `${selection.branch.name} · 新路线` })} className="rounded-lg bg-indigo-500 px-2 py-1.5 text-white disabled:opacity-40">从这里开新线</button>}
                <button type="button" onClick={() => setAction({ kind: 'rename', value: selection.branch.name })} className="rounded-lg border px-2 py-1.5">改名</button>
                <button type="button" onClick={() => void archive()} className="rounded-lg border px-2 py-1.5">{selection.branch.archived ? '取消归档' : '归档'}</button>
              </div>
              {action && <form onSubmit={(e) => { e.preventDefault(); void submitAction(); }} className="mt-4 rounded-lg border border-indigo-200 bg-indigo-50 p-2"><label className="block text-xs text-slate-600">{action.kind === 'fork' ? '新路线名称' : action.kind === 'rename' ? '路线名称' : '事件名称'}<input autoFocus value={action.value} onChange={(e) => setAction({ ...action, value: e.target.value })} className="mt-1 w-full rounded border bg-white px-2 py-1.5 text-sm" /></label><div className="mt-2 flex gap-2"><button type="submit" disabled={busy || !action.value.trim()} className="rounded bg-indigo-500 px-2 py-1 text-xs text-white disabled:opacity-40">确认</button><button type="button" onClick={() => setAction(null)} className="rounded border px-2 py-1 text-xs">取消</button></div></form>}
              {selection.branch.events.length > 0 && <div className="mt-5"><h4 className="text-xs font-semibold text-slate-600">剧情事件</h4>{selection.branch.events.map((event) => <div key={event.id} className="mt-2 flex items-center gap-1 text-xs"><button type="button" onClick={() => select(selection.branch, event.anchor_message_id)} className="min-w-0 flex-1 truncate text-left text-amber-700">◆ {event.title}</button><button type="button" onClick={() => setAction({ kind: 'event', value: event.title, eventId: event.id })} className="text-slate-500">编辑</button><button type="button" disabled={busy} onClick={() => void deleteEvent(event.id)} className="text-rose-500">删除</button></div>)}</div>}
              {history && <div className="mt-4 max-h-80 space-y-2 overflow-y-auto rounded-lg border p-2"><p className="text-[11px] text-slate-500">该路线的只读消息位置；当前运行路线未切换。</p>{history.map((message) => <div id={`worldline-history-${message.id}`} key={message.id} className={`rounded p-2 text-xs ${historyAnchor === message.id ? 'bg-amber-50 ring-1 ring-amber-300' : 'bg-slate-50'}`}><span className="text-slate-400">{message.actor}</span><p className="whitespace-pre-wrap">{message.content}</p></div>)}</div>}
            </> : <p className="text-sm text-slate-400">选择一条路线或剧情事件查看详情。</p>}
            {error && <p className="mt-4 rounded-lg bg-rose-50 p-2 text-xs text-rose-700">{error}</p>}
          </aside>
        </div>
      </div>
    </SurfaceDialog>
  );
}
