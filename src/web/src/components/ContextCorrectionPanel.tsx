import { queryClient } from '../queryClient';
import { resourceQueries } from '../features/resources/resourceQueries';
import { useStoryUiStore } from '../features/stories/storyUiStore';
import { createClientId } from "../utils/clientId.js";
import { WorkflowDialog } from "../design-system/WorkflowDialog";
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { X } from 'lucide-react';
import { storiesClient as api } from '../features/stories/storiesClient';
import { storyReviewApi } from '../features/stories/storyReviewApi';
import { storyRuntime } from '../features/stories/storyRuntime';
import { useChatStore } from '../store/chatStore';
import type { MemoryRecord } from '../types';
import { StoryContinuityPanel } from './StoryContinuityPanel';

interface Target { type: string; branch_id: string; character_id?: string; source_character_id?: string; memory_id?: string; lorebook_id?: string; world_id?: string }
interface Correction { source: string; entry_id: string; actual_content: string; current_content: string | null; included_in_actual: boolean; editable: boolean; target: Target }
interface CorrectionOptions {
  generation_id: string | null;
  expected_branch_revision: number; expected_fingerprint: string; expected_player_identity_id: string | null;
  options_digest: string;
  static_changes: Array<{ key: string; label: string; before: unknown; after: unknown }>;
  memories: Array<{ id: string; revision: number; content: string | null; eligible: boolean; reason: string | null }>;
  notes: string[];
}
interface Comparison { corrections: Correction[]; sources: Array<{ source: string; entry_id: string; status: string; actual: { content: string } | null; preview: { content: string } | null }>; notes: string[]; changed_sections: Array<{ kind: string; actual: { content: string } | null; preview: { content: string } | null }> }
const names: Record<string, string> = { character: '角色设定与示例', memory: '记忆', lorebook: '世界书', world: '世界设定', archive: '世界档案', system: '故事约定', preset: '提示词方案' };
const correctionPath = (target: Target) => target.type === 'character' ? `/library/characters/${encodeURIComponent(target.source_character_id ?? target.character_id ?? '')}` : target.type === 'lorebook' ? `/library/lorebooks/${encodeURIComponent(target.lorebook_id ?? '')}` : target.type === 'world' && target.world_id ? `/worlds/${encodeURIComponent(target.world_id)}` : target.type === 'prompt_preset' ? '/settings/prompt-presets' : null;

export function ContextCorrectionPanel({ messageId, generationId, onClose }: { messageId: string; generationId: string; onClose: () => void }) {
  const sid = useChatStore((s) => s.currentSessionId)!;
  const context = JSON.stringify([sid, messageId, generationId]);
  const contextRef = useRef({ key: context, epoch: 0 });
  if (contextRef.current.key !== context) contextRef.current = { key: context, epoch: contextRef.current.epoch + 1 };
  const [selected, setSelected] = useState('');
  const [record, setRecord] = useState<MemoryRecord | null>(null);
  const [related, setRelated] = useState<MemoryRecord[]>([]);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [adoption, setAdoption] = useState(false);
  const [regenerateOpen, setRegenerateOpen] = useState(false);
  const [selectedFields, setSelectedFields] = useState<string[]>([]);
  const [selectedMemories, setSelectedMemories] = useState<Record<string, number>>({});
  useEffect(() => {
    setBusy(false); setError(''); setNotice(''); setRegenerateOpen(false);
    setSelectedFields([]); setSelectedMemories({});
  }, [sid, messageId, generationId]);
  const options = useQuery({ queryKey: ['correction-options', sid, messageId],
    queryFn: () => storyReviewApi.correctionOptions<CorrectionOptions>(sid, messageId), enabled: regenerateOpen });
  const regenerate = async () => {
    if (!options.data) return;
    const ownsBranch = storyRuntime.capture(sid);
    const ownedContext = contextRef.current.epoch;
    const ownsRequest = () => ownsBranch() && contextRef.current.epoch === ownedContext;
    setBusy(true); setError('');
    try {
      const data = options.data;
      await storyReviewApi.regenerateCorrected(sid, messageId, {
        operation_id: createClientId(), expected_branch_revision: data.expected_branch_revision,
        expected_fingerprint: data.expected_fingerprint, expected_player_identity_id: data.expected_player_identity_id,
        options_digest: data.options_digest, selected_static_fields: selectedFields, selected_memory_revisions: selectedMemories,
      });
      if (!ownsRequest()) return;
      await Promise.allSettled([useChatStore.getState().refreshSession(sid)]);
      if (!ownsRequest()) return;
      setNotice('已按选定修订生成新候选，旧候选仍可查看。后续受影响的回应需要逐项核对。');
      setRegenerateOpen(false); setSelectedFields([]); setSelectedMemories({});
    } catch (cause) { if (ownsRequest()) setError(cause instanceof Error ? cause.message : '纠错生成失败'); }
    finally { if (ownsRequest()) setBusy(false); }
  };
  const comparison = useQuery({ queryKey: ['context-comparison', sid, messageId, generationId], queryFn: () => storyReviewApi.comparison<Comparison>(sid, messageId, generationId) });
  const source = comparison.data?.corrections.find((item) => `${item.source}/${item.entry_id}` === selected);
  const choose = async (item: Correction) => {
    setSelected(`${item.source}/${item.entry_id}`); setRecord(null); setRelated([]); setError(''); setNotice('');
    if (item.target.type !== 'memory' || !item.target.character_id) return;
    setBusy(true);
    try {
      const rows = await queryClient.fetchQuery({ ...resourceQueries.memories(item.target.character_id, sid), staleTime: 0 });
      const current = rows.find((row) => row.id === item.target.memory_id);
      if (!current) throw new Error('这条记忆已删除或不再属于可见来源，请重新比较。');
      setRecord(current); setText(current.content);
      const origins = new Set(current.source_message_ids ?? []);
      setRelated(rows.filter((row) => row.id !== current.id && row.source_changed && (row.source_message_ids ?? []).some((id) => origins.has(id))));
    } catch (cause) { setError(cause instanceof Error ? cause.message : '来源读取失败'); }
    finally { setBusy(false); }
  };
  const save = async () => {
    if (!record || !source?.target.character_id || !text.trim()) return;
    setBusy(true); setError('');
    try {
      const next = await api.updateMemory(source.target.character_id, record.id, { content: text.trim(), expected_revision: record.revision }, sid);
      setRecord(next); setNotice('记忆已修订。重新比较，核对下一轮输入中的来源变化。');
      await comparison.refetch();
    } catch (cause) { setError(cause instanceof Error ? cause.message : '修订失败'); }
    finally { setBusy(false); }
  };
  return <WorkflowDialog title="核对与修订依据" onClose={onClose} wide>
    <aside className="h-full w-full max-w-4xl overflow-y-auto bg-[var(--surface,#fff)] p-5 shadow-xl">
      <header className="mb-5 flex justify-between"><div><h2 className="text-xl font-semibold">核对与修订依据</h2><p className="mt-1 text-sm text-[var(--muted)]">从这条回应实际收到的资料开始，核对修改后的输入。</p></div><button type="button" className="v7-icon-btn" aria-label="关闭依据修订" onClick={onClose}><X size={18} /></button></header>
      <p className="mb-4 rounded-xl border border-[var(--line)] bg-[var(--surface-soft)] p-3 text-sm text-[var(--ink)]">左侧为这次候选的实际输入，右侧为当前路线的下一轮预览。对话时点可能不同；差异表示输入发生变化，不能据此判断模型内部推理。</p>
      {(error || comparison.error) && <p role="alert" className="mb-3 text-sm text-[var(--danger)]">{error || comparison.error?.message}</p>}{notice && <p role="status" className="mb-3 text-sm text-[var(--color-success)]">{notice}</p>}
      <button type="button" className="v7-btn v7-btn-soft mb-4" disabled={busy || comparison.isFetching} onClick={() => void comparison.refetch()}>重新比较当前输入</button>
      {comparison.isPending ? <p role="status">正在读取实际依据…</p> : comparison.data && <div className="grid gap-5 md:grid-cols-[220px_1fr]">
        <div className="space-y-2"><h3 className="text-sm font-medium">选择需要核对的来源</h3>{comparison.data.corrections.map((item) => <button key={`${item.source}/${item.entry_id}`} type="button" className={`block w-full rounded-lg border p-3 text-left text-sm ${selected === `${item.source}/${item.entry_id}` ? 'border-[var(--color-primary)] bg-[var(--color-primary-soft)]' : 'border-[var(--line)]'}`} onClick={() => void choose(item)}><strong>{names[item.source] ?? '上下文资料'}</strong><small className="block">{item.included_in_actual ? '实际使用' : '当时未进入输入'}</small><span className="mt-1 block line-clamp-3 text-xs text-[var(--muted)]">{item.actual_content.slice(0, 100)}</span></button>)}</div>
        <div>{source ? <><div className="grid gap-4 sm:grid-cols-2"><section><h3 className="mb-2 text-sm font-medium">当时的资料</h3><pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg border border-[var(--line)] p-3 text-xs">{source.actual_content || '未留存正文'}</pre></section><section><h3 className="mb-2 text-sm font-medium">当前预览的资料</h3><pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg border border-[var(--line)] p-3 text-xs">{source.current_content ?? '此来源未进入当前预览'}</pre></section></div>
          {correctionPath(source.target) && <div className="mt-4 space-y-3"><Link className="v7-btn v7-btn-soft" target="_blank" rel="noreferrer" to={correctionPath(source.target)!}>打开对应资料修订</Link><p className="text-xs text-[var(--muted)]">素材库修改后，还需比较新版并选择应用到本路线。</p><button type="button" className="v7-btn v7-btn-soft" onClick={() => setAdoption(true)}>比较与应用新版资料</button></div>}
          {source.target.type === 'pinned_facts' && <button type="button" className="v7-btn v7-btn-soft mt-4" onClick={() => { useStoryUiStore.getState().openPanel('pinned'); onClose(); }}>打开本路线固定信息</button>}
          {record && <section className="mt-5 space-y-3"><label className="v7-field">修订本路线记忆<textarea value={text} rows={5} onChange={(e) => setText(e.target.value)} /></label><button type="button" className="v7-btn v7-btn-primary" disabled={busy || !text.trim()} onClick={() => void save()}>{busy ? '保存中…' : '保存记忆修订'}</button><p className="text-xs text-[var(--muted)]">只修改这条记忆，保留修订记录；其他路线和同源记忆由你逐项核对。</p>{related.length > 0 && <div><h4 className="text-sm font-medium">同一来源的过期记忆</h4>{related.map((row) => <button key={row.id} type="button" className="mt-2 block rounded-lg border border-[var(--line)] p-3 text-left text-sm" onClick={() => { setRecord(row); setText(row.content); }}>{row.content}</button>)}</div>}</section>}
          {!source.editable && <p className="mt-4 text-sm text-[var(--muted)]">这项来自历史输入，保留为证据。请从原消息的编辑流程修订。</p>}
        </> : <p className="text-sm text-[var(--muted)]">选择一项资料，查看原文和当前输入。</p>}
          <details className="mt-5"><summary className="text-sm">查看其他输入变化</summary>{comparison.data.changed_sections.map((section) => <div key={section.kind} className="mt-3 grid gap-3 rounded-lg border p-3 sm:grid-cols-2"><pre className="max-h-52 overflow-auto whitespace-pre-wrap text-xs">{section.actual?.content ?? '原输入中无此节'}</pre><pre className="max-h-52 overflow-auto whitespace-pre-wrap text-xs">{section.preview?.content ?? '预览中无此节'}</pre></div>)}</details>
        </div>
      </div>}
      <section className="mt-6 space-y-3 border-t border-[var(--line)] pt-5">
        <h3 className="font-semibold">核对后重新生成</h3><p className="text-sm text-[var(--muted)]">仅对当前控制阶段、当前场景最新交互段的回应提供纠错候选。继续使用该回应当时的历史，加入你勾选的修订；普通重新生成仍沿用原来的依据。</p>
        <button type="button" className="v7-btn v7-btn-soft" disabled={busy} onClick={() => { setSelectedFields([]); setSelectedMemories({}); setRegenerateOpen(true); void options.refetch(); }}>核对可用于这条回应的修订</button>
        {regenerateOpen && (options.isPending ? <p role="status">正在核对原历史边界…</p> : options.error ? <p role="alert" className="text-sm text-[var(--color-accent)]">{options.error.message}</p> : options.data && <div className="space-y-4 rounded-xl border border-[var(--line)] p-4">
          {options.data.generation_id !== generationId && <p role="alert" className="text-sm text-[var(--color-accent)]">当前候选已变化。请关闭面板，从当前回应重新进入。</p>}
          {options.data.static_changes.map((item) => <label key={item.key} className="block rounded-lg border border-[var(--line)] p-3 text-sm"><span className="flex gap-2"><input type="checkbox" checked={selectedFields.includes(item.key)} onChange={(e) => setSelectedFields((keys) => e.target.checked ? [...keys, item.key] : keys.filter((key) => key !== item.key))} />{item.label}</span><div className="mt-2 grid gap-3 sm:grid-cols-2"><div><small>该回应原资料</small><pre className="max-h-40 overflow-auto whitespace-pre-wrap text-xs">{typeof item.before === 'string' ? item.before : JSON.stringify(item.before, null, 2)}</pre></div><div><small>本路线已采用的修订</small><pre className="max-h-40 overflow-auto whitespace-pre-wrap text-xs">{typeof item.after === 'string' ? item.after : JSON.stringify(item.after, null, 2)}</pre></div></div></label>)}
          {options.data.memories.map((item) => <label key={item.id} className="block rounded-lg border border-[var(--line)] p-3 text-sm"><span className="flex gap-2"><input type="checkbox" disabled={!item.eligible} checked={selectedMemories[item.id] === item.revision} onChange={(e) => setSelectedMemories((old) => { const next = { ...old }; if (e.target.checked) next[item.id] = item.revision; else delete next[item.id]; return next; })} />确认记忆修订 {item.revision}</span><p className="mt-2 whitespace-pre-wrap">{item.content ?? '当前视角不能读取这条来源'}</p>{item.reason && <p className="mt-2 text-xs text-[var(--color-accent)]">{item.reason}</p>}</label>)}
          {!options.data.static_changes.length && !options.data.memories.length && <p className="text-sm text-[var(--muted)]">尚无符合原历史边界的修订。请先修订资料并显式应用到本路线。</p>}
          <button type="button" className="v7-btn v7-btn-primary" disabled={busy || options.data.generation_id !== generationId || (!selectedFields.length && !Object.keys(selectedMemories).length)} onClick={() => void regenerate()}>{busy ? '正在生成纠错候选…' : '用选定修订生成新候选'}</button>
        </div>)}
      </section>
      {adoption && <StoryContinuityPanel initialTab="updates" onClose={() => { setAdoption(false); void comparison.refetch(); }} />}
    </aside>
  </WorkflowDialog>;
}
