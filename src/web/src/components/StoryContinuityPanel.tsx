import { WorkflowDialog } from "../design-system/WorkflowDialog";
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { X } from 'lucide-react';
import { storyReviewApi } from '../features/stories/storyReviewApi';
import { useChatStore } from '../store/chatStore';
import { writeStoryLocation } from '../api/navigation';

interface Evidence { id: string; content: string; message_ids: string[]; actor_name?: string; matter_status?: string; title?: string }
interface Review { branch_id: string; branch_name: string; messages: Evidence[]; memories: Evidence[]; unfinished: Evidence[]; events: Evidence[]; truncated: boolean; visible_message_count: number }
interface Comparison { common_anchor: { message_id: string; content: string } | null; left: Review; right: Review }
interface Updates { branch_revision: number; source_digest: string; changes: Array<{ key: string; label: string; before: unknown; after: unknown }> }
const format = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2);

export function StoryContinuityPanel({ onClose, initialTab = 'review' }: { onClose: () => void; initialTab?: 'review' | 'compare' | 'updates' }) {
  const sid = useChatStore((s) => s.currentSessionId)!;
  const session = useChatStore((s) => s.sessions.find((item) => item.id === sid));
  const branches = useChatStore((s) => s.sessions.filter((item) => item.story_id === session?.story_id && item.id !== sid));
  const characters = useChatStore((s) => s.sessionCharacters);
  const scene = useChatStore((s) => s.activeScene);
  const [tab, setTab] = useState<'review' | 'compare' | 'updates'>(initialTab);
  const [actor, setActor] = useState('player');
  const [other, setOther] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const review = useQuery({ queryKey: ['continuity-review', sid, actor], queryFn: () => storyReviewApi.review<Review>(sid, actor), enabled: tab === 'review' });
  const comparison = useQuery({ queryKey: ['continuity-compare', sid, other, actor], queryFn: () => storyReviewApi.compareBranches<Comparison>(sid, other, actor), enabled: tab === 'compare' && Boolean(other) });
  const updates = useQuery({ queryKey: ['asset-updates', sid], queryFn: () => storyReviewApi.assetUpdates<Updates>(sid), enabled: tab === 'updates' });
  const locate = async (branchId: string, messageId: string) => {
    try {
      if (branchId !== sid) await useChatStore.getState().selectSession(branchId);
      writeStoryLocation(session?.story_id ?? sid, branchId, messageId); onClose();
    } catch (cause) { setError(cause instanceof Error ? cause.message : '定位失败'); }
  };
  const evidence = (data: Review) => <section className="space-y-4"><h3 className="font-semibold">{data.branch_name}</h3>
    <div><h4 className="mb-2 text-sm font-medium">未完成事项</h4>{data.unfinished.filter((item) => item.matter_status !== 'completed' && item.matter_status !== 'resolved' && item.matter_status !== 'cancelled').map((item) => <button key={item.id} type="button" className="mb-2 block w-full rounded-lg border border-[var(--line)] p-3 text-left text-sm" onClick={() => void locate(data.branch_id, item.message_ids[0])}>{item.content}<small className="block text-[var(--muted)]">查看原消息 · {item.matter_status === 'open' ? '尚未完成' : '状态待核对'}</small></button>)}</div>
    {!!data.events.length && <div><h4 className="mb-2 text-sm font-medium">剧情事件</h4>{data.events.map((item) => <button key={item.id} type="button" className="mb-2 block text-left text-sm text-[var(--color-primary)]" onClick={() => void locate(data.branch_id, item.message_ids[0])}>{item.title} · 查看来源</button>)}</div>}
    {!!data.memories.length && <details><summary>查看有效记忆与来源</summary>{data.memories.map((item) => <button key={item.id} type="button" className="mt-2 block w-full rounded-lg border p-3 text-left text-sm" onClick={() => void locate(data.branch_id, item.message_ids[0])}>{item.content}</button>)}</details>}
    <div><h4 className="mb-2 text-sm font-medium">近期原消息</h4>{data.truncated && <p className="text-xs text-[var(--muted)]">共有 {data.visible_message_count} 条可见消息，这里列出最近 100 条。更多历史可从剧情导航查找。</p>}{data.messages.map((item) => <button key={item.id} type="button" className="mt-2 block w-full rounded-lg border border-[var(--line)] p-3 text-left text-sm" onClick={() => void locate(data.branch_id, item.id)}><strong className="block text-xs text-[var(--muted)]">{item.actor_name}</strong><span className="whitespace-pre-wrap">{item.content}</span></button>)}</div>
  </section>;
  const apply = async () => {
    if (!updates.data || !selected.length) return;
    setBusy(true); setError(''); setNotice('');
    try {
      await storyReviewApi.applyAssetUpdates(sid, { expected_revision: updates.data.branch_revision, source_digest: updates.data.source_digest, selected_fields: selected });
      await Promise.allSettled([useChatStore.getState().refreshSession(sid), updates.refetch()]);
      setNotice(`已将 ${selected.length} 项资料应用到本路线。`); setSelected([]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '应用失败'); }
    finally { setBusy(false); }
  };
  const currentError = error || (tab === 'review' ? review.error?.message : tab === 'compare' ? comparison.error?.message : updates.error?.message);
  const missingFeature = /^404: not found$/i.test(currentError ?? '');
  const activeQuery = tab === 'review' ? review : tab === 'compare' ? comparison : updates;
  return <WorkflowDialog title="继续故事" onClose={onClose}>
    <aside className="h-full w-full max-w-3xl overflow-y-auto border-l border-[var(--line)] bg-[var(--surface,#fff)] p-5 shadow-xl">
      <header className="mb-5 flex justify-between gap-3"><div><h2 className="text-xl font-semibold">继续故事</h2><p className="mt-1 text-sm text-[var(--muted)]">回到这条路线，核对经历与资料。</p></div><button type="button" className="v7-icon-btn" aria-label="关闭继续故事" onClick={onClose}><X size={18} /></button></header>
      <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label="继续故事操作">{(['review', 'compare', 'updates'] as const).map((value) => <button key={value} type="button" className={`v7-btn ${tab === value ? 'v7-btn-primary' : 'v7-btn-soft'}`} onClick={() => { setTab(value); setError(''); }}>{value === 'review' ? '当前路线' : value === 'compare' ? '路线分歧' : '设定更新'}</button>)}</div>
      {tab !== 'updates' && <label className="v7-field mb-4">查看视角<select aria-label="查看视角" value={actor} onChange={(e) => setActor(e.target.value)}><option value="player">玩家</option>{characters.map((c) => <option key={c.id} value={c.id}>{c.card.name}</option>)}</select><small>各项带有原消息来源；回顾只使用该视角可见的有效记录。</small></label>}
      {notice && <p role="status" className="mb-4 text-sm text-[var(--color-success)]">{notice}</p>}
      {currentError && <div role="alert" className="mb-4 rounded-xl border border-[var(--line)] p-4 text-sm">
        <p className="text-[var(--danger)]">{missingFeature
          ? '当前服务尚未加载此功能。请在没有生成任务时重新启动织界之姬，然后重试。'
          : `读取或更新未完成：${currentError}`}</p>
        <button type="button" className="v7-btn v7-btn-soft mt-3" disabled={activeQuery.isFetching}
          onClick={() => { setError(''); void activeQuery.refetch(); }}>{activeQuery.isFetching ? '正在重试…' : '重试'}</button>
      </div>}
      {tab === 'review' && <><div className="mb-5 rounded-xl border border-[var(--line)] p-3 text-sm"><strong>当前场景：{scene?.title ?? '未记录场景'}</strong><p className="mt-1">在场：{characters.filter((c) => c.present).map((c) => c.card.name).join('、') || '暂无人物'}</p></div>{review.isPending ? <p role="status">正在整理来源…</p> : review.data && evidence(review.data)}</>}
      {tab === 'compare' && <><label className="v7-field mb-4">比较路线<select aria-label="比较路线" value={other} onChange={(e) => setOther(e.target.value)}><option value="">选择另一条路线</option>{branches.map((b) => <option key={b.id} value={b.id}>{b.branch_name ?? b.title}</option>)}</select></label>{other && comparison.isPending && <p role="status">正在比较…</p>}{comparison.data && <><div className="mb-4 rounded-xl bg-[var(--surface-soft)] p-3 text-sm"><strong>共同锚点</strong><p className="mt-1 whitespace-pre-wrap">{comparison.data.common_anchor?.content ?? '没有可见的共同消息'}</p></div><div className="grid gap-6 sm:grid-cols-2">{evidence(comparison.data.left)}{evidence(comparison.data.right)}</div></>}</>}
      {tab === 'updates' && <><p className="mb-4 text-sm text-[var(--muted)]">只将勾选资料应用到本路线，从现在的故事状态开始生效。历史消息与其他路线保留原资料。</p>{updates.isPending ? <p role="status">正在比较资料…</p> : updates.data?.changes.length === 0 ? <p>当前资料没有可应用的变化。</p> : updates.data?.changes.map((item) => <div key={item.key} className="mb-4 rounded-xl border border-[var(--line)] p-4"><label className="mb-3 flex gap-2 text-sm font-medium"><input type="checkbox" checked={selected.includes(item.key)} onChange={(e) => setSelected((old) => e.target.checked ? [...old, item.key] : old.filter((key) => key !== item.key))} />{item.label}</label><div className="grid gap-3 sm:grid-cols-2"><div><small>故事中的资料</small><pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap text-xs">{format(item.before)}</pre></div><div><small>素材库新版</small><pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap text-xs">{format(item.after)}</pre></div></div></div>)}{updates.data && !updates.error && <button type="button" className="v7-btn v7-btn-primary" disabled={busy || !selected.length} onClick={() => void apply()}>{busy ? '应用中…' : `应用 ${selected.length} 项到本路线`}</button>}</>}
    </aside>
  </WorkflowDialog>;
}
