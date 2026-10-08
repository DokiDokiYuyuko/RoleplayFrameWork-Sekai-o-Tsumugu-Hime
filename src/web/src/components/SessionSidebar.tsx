import { useMemo, useRef, useState } from 'react';
import { storyQueries } from '../features/stories/storyQueries';
import { exportStoryUrl } from '../api/Api';
import { useChatStore } from '../store/chatStore';
import type { BranchSummary, Session } from '../types';
import { SessionSetupModal } from './SessionSetupModal';
import { ConfirmDialog } from '../design-system/ConfirmDialog';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { archiveBranchAction, deleteBranchAction, deleteStoryAction, importStoryAction } from '../features/stories/storyActions';
import { DropdownMenu } from 'radix-ui';
import { Archive, MoreHorizontal, Plus, Upload, Download, GitBranch, Pencil, Trash2 } from 'lucide-react';

export function SessionSidebar({ onNavigate, onOpenWorldline }: {
  onNavigate?: () => void; onOpenWorldline?: (storyId?: string) => void;
}) {
  const sessions = useChatStore((s) => s.sessions);
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const storiesQuery = useQuery(storyQueries.stories());
  const stories = storiesQuery.data ?? [];
  const branchQueries = useQueries({ queries: stories.map(story => storyQueries.branches(story.story_id)) });
  const branches = Object.fromEntries(stories.map((story, index) => [story.story_id, branchQueries[index].data?.branches ?? []]));
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [modal, setModal] = useState<'create' | 'edit' | null>(null);
  const [editTarget, setEditTarget] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [importReport, setImportReport] = useState('');
  const [deleteMembership, setDeleteMembership] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const importInput = useRef<HTMLInputElement>(null);
  const branchTriggers = useRef<Record<string, HTMLButtonElement | null>>({});
  const queryClient = useQueryClient();
  const importFile = async (file?: File) => {
    if (!file) return;
    try {
      const report = await importStoryAction(file, queryClient);
      setImportReport(`已导入 ${report.branches.length} 条路线、${report.saves.length} 个存档和 ${report.memory_records} 条记忆。${report.missing_assets.length ? `有 ${report.missing_assets.length} 个头像需重新设置。` : ''}`);
      await useChatStore.getState().loadSessions();
      await select(report.story_id);
    } catch (e) { setError(e instanceof Error ? e.message : '导入失败'); }
    finally { if (importInput.current) importInput.current.value = ''; }
  };

  const activeStoryId = useMemo(() => {
    for (const [id, rows] of Object.entries(branches)) if (rows.some((branch) => branch.id === currentSessionId)) return id;
    return currentSessionId;
  }, [branches, currentSessionId]);
  const select = async (id: string) => {
    try { await useChatStore.getState().selectSession(id); onNavigate?.(); setError(null); }
    catch (e) { setError(e instanceof Error ? e.message : '路线打开失败'); }
  };
  const remove = async (id: string) => {
    setDeleteBusy(true);
    try {
      if (stories.some((story) => story.story_id === id)) await deleteStoryAction(id, queryClient, deleteMembership);
      else await deleteBranchAction(id, queryClient, deleteMembership);
      if (currentSessionId === id) {
        const next = useChatStore.getState().sessions[0];
        if (next) await select(next.id);
      }
      setDeleteTarget(null);
    } catch (e) { setError(e instanceof Error ? e.message : '删除失败'); }
    finally { setDeleteBusy(false); }
  };

  const branchRow = (branch: BranchSummary) => {
    const session = sessions.find((item) => item.id === branch.id);
    return <div key={branch.id} className={`v7-sidebar-branch ${currentSessionId === branch.id ? 'is-active' : ''} ${branch.archived ? 'is-archived' : ''}`}>
      <button type="button" onClick={() => void select(branch.id)} className="v7-sidebar-branch-link">
        <strong>{branch.parent_branch_id ? '↳ ' : '● '}{branch.name}</strong>
        <small>{branch.message_count} 条消息 · {branch.events.length} 个事件{session ? ` · 回合 ${session.turn}` : ''}</small>
      </button>
      <DropdownMenu.Root><DropdownMenu.Trigger ref={element => { branchTriggers.current[branch.id] = element; }} type="button" className="v7-sidebar-menu-trigger" aria-label={`${branch.name}的路线操作`}><MoreHorizontal size={17} /></DropdownMenu.Trigger>
        <DropdownMenu.Portal><DropdownMenu.Content className="v7-dropdown" sideOffset={5} align="end">
          {session && <DropdownMenu.Item onSelect={() => { setEditTarget(session); setModal('edit'); }}><Pencil size={15} /> 编辑设置</DropdownMenu.Item>}
          <DropdownMenu.Item onSelect={() => void archiveBranchAction(branch.id, !branch.archived, queryClient).catch((e) => setError(e instanceof Error ? e.message : '归档失败'))}><Archive size={15} /> {branch.archived ? '取消归档' : '归档路线'}</DropdownMenu.Item>
          <DropdownMenu.Item onSelect={() => onOpenWorldline?.(Object.entries(branches).find(([, rows]) => rows.some((item) => item.id === branch.id))?.[0])}><GitBranch size={15} /> 查看世界线</DropdownMenu.Item>
          <DropdownMenu.Item className="is-danger" onSelect={() => { const storyId = Object.entries(branches).find(([, rows]) => rows.some(item => item.id === branch.id))?.[0]; setDeleteMembership(stories.find(story => story.story_id === storyId)?.membership_revision ?? null); setDeleteTarget(branch.id); }}><Trash2 size={15} /> {stories.some((story) => story.story_id === branch.id) ? '删除故事' : '删除路线'}</DropdownMenu.Item>
        </DropdownMenu.Content></DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>;
  };

  return <aside className="v7-session-sidebar">
    <div className="v7-sidebar-head"><span>故事与路线</span><button type="button" onClick={() => setModal('create')} className="v7-sidebar-new"><Plus size={15} /> 新建</button></div>
    <div className="v7-sidebar-import"><input ref={importInput} type="file" accept=".zip,application/zip" className="hidden" onChange={(event) => void importFile(event.target.files?.[0])} /><button type="button" onClick={() => importInput.current?.click()}><Upload size={14} /> 导入故事 ZIP</button></div>
    {importReport && <p className="m-2 rounded bg-emerald-50 p-2 text-xs text-emerald-700">{importReport}</p>}
    {error && <p className="m-2 rounded bg-rose-50 p-2 text-xs text-rose-700">{error}</p>}
    {(storiesQuery.error || branchQueries.some(query => query.error)) && <p role="alert" className="m-2 rounded bg-rose-50 p-2 text-xs text-rose-700">故事列表加载失败。<button onClick={() => { void storiesQuery.refetch(); for (const query of branchQueries) void query.refetch(); }}>重试</button></p>}
    <div className="v7-sidebar-scroll">
      {stories.map((story) => {
        const open = expanded[story.story_id] ?? story.story_id === activeStoryId;
        const rows = branches[story.story_id] ?? [];
        return <section key={story.story_id} className="v7-sidebar-story">
          <div className="v7-sidebar-story-head">
            <button type="button" onClick={() => setExpanded((before) => ({ ...before, [story.story_id]: !open }))} className="v7-sidebar-story-toggle">{open ? '▾' : '▸'} {story.title}</button>
            <a href={exportStoryUrl(story.story_id)} className="v7-sidebar-small-action" title="导出故事和记忆" aria-label={`导出${story.title}`}><Download size={14} /></a><button type="button" onClick={() => { onOpenWorldline?.(story.story_id); onNavigate?.(); }} className="v7-sidebar-small-action" title="查看世界线" aria-label={`查看${story.title}的世界线`}><GitBranch size={14} /></button>
          </div>
          {open && <>{rows.map(branchRow)}{story.branch_count > rows.length && <button type="button" onClick={() => onOpenWorldline?.(story.story_id)} className="px-4 py-2 text-xs text-indigo-600">还有 {story.branch_count - rows.length} 条路线，查看世界线</button>}</>}
        </section>;
      })}
      {stories.length === 0 && <p className="px-4 py-8 text-center text-xs text-slate-400">还没有故事，点「+ 新建」开始</p>}
    </div>
    {modal === 'create' && <SessionSetupModal mode="create" onClose={() => setModal(null)} />}
    {modal === 'edit' && editTarget && <SessionSetupModal mode="edit" session={editTarget} returnFocus={branchTriggers.current[editTarget.id]} onClose={() => { setModal(null); setEditTarget(null); }} />}
    <ConfirmDialog open={Boolean(deleteTarget)} title={stories.some((story) => story.story_id === deleteTarget) ? '删除故事' : '删除路线'}
      description={stories.some((story) => story.story_id === deleteTarget)
        ? `确认把「${stories.find((item) => item.story_id === deleteTarget)?.title ?? '这个故事'}」及其全部路线、存档和记忆移入回收区？可在书架恢复。`
        : `确认删除「${sessions.find((item) => item.id === deleteTarget)?.branch_name ?? '这条路线'}」？删除前会备份整个故事；有子线或存档时请先归档。`}
      busy={deleteBusy} error={error ?? ''} onClose={() => setDeleteTarget(null)}
      onConfirm={() => { if (deleteTarget) void remove(deleteTarget); }} />
  </aside>;
}
