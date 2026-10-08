import { resourceQueries } from '../resources/resourceQueries';
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation, useParams } from 'react-router';
import { ArrowLeft, BookOpen, Check, FileText, LoaderCircle, Save, Sparkles } from 'lucide-react';
import { Dialog } from 'radix-ui';
import { worldsClient as api } from './worldsClient';
import { legacyWorldApi } from './worldOrganizeApi';
import type { ArchiveInput, LorebookEntry } from '../../types';
import { LoadState } from '../../components/LoadState';
import { emptyLorebookEntry, LorebookEntryFields } from '../../components/LorebookEntryFields';
import { MoonOrnament } from '../stories/StoryHome';
import { ArchiveRecordEditor } from './ArchiveRecordEditor';
import { worldOrganizeApi, type OrganizeDraft, type OrganizeInput, type OrganizeJob, type OrganizeCommitInput } from './worldOrganizeApi';
import { prepareOrganizeCommit, hasPrivateOrganizeSources, organizeCompletedBatches, createOrganizeScope, initialOrganizeInput, organizeCandidates, readLegacyCandidates, toggleOrganizeReference } from './worldOrganizeState';
import { createClientId } from '../../utils/clientId';
import { useLorebookStore } from '../../store/lorebookStore';
import { compareNewestFirst } from '../../utils/timestamps';
import './worlds.css';
import '../stories/homepage.css';
import './world-organize.css';

const active = (status: string) => ['queued', 'running'].includes(status);
const statusLabel: Record<string, string> = { queued: '排队中', running: '正在整理', review: '等待核对', failed: '整理失败', interrupted: '整理中断', cancelled: '已取消', committed: '已保存' };
const errorMessage = (cause: unknown) => cause instanceof Error ? cause.message.replace(/^\d+:\s*/, '') : '操作失败，请重试。';
const storageRead = <T,>(key: string, fallback: T): T => { try { return JSON.parse(localStorage.getItem(key) || 'null') as T || fallback; } catch { return fallback; } };
const storageWrite = (key: string, value: unknown) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Server drafts remain available when browser storage is unavailable. */ } };
type DraftEdit = { revision: number; payload: Record<string, unknown>; action: 'add' | 'replace'; target_uid?: number };
type LegacySummary = { id: string; title?: string; world_id?: string; status: string; updated_at: string; type: 'import' | 'lorebook' };
type LegacyJob = { id: string; source: string; instruction: string; source_visibility: 'public' | 'private'; candidates: ReturnType<typeof readLegacyCandidates>; sources: { id: string; title?: string; content?: string; audience?: string; visibility?: string }[] };

function archiveValue(draft: OrganizeDraft, payload: Record<string, unknown>): ArchiveInput {
  return { kind: draft.kind === 'biology' ? 'biology' : 'background', subtype: '', title: '', aliases: [], tags: [], summary: '', body: '', visibility: 'public', kind_data: {}, ...payload } as ArchiveInput;
}
function bookValue(payload: Record<string, unknown>): LorebookEntry { return { ...emptyLorebookEntry(), ...payload } as LorebookEntry; }
const draftTitle = (draft: OrganizeDraft, payload = draft.payload) => String(payload.title || payload.comment || (Array.isArray(payload.keys) && payload.keys.join('、')) || '未命名候选');

export default function WorldOrganizePage() {
  const { worldId } = useParams();
  const { search } = useLocation();
  return worldId ? <Organizer key={`${worldId}:${search}`} worldId={worldId} /> : <LoadState title="请选择要整理的世界" />;
}

function Organizer({ worldId }: { worldId: string }) {
  const { search } = useLocation();
  const cache = useQueryClient();
  const scope = useRef(createOrganizeScope(worldId));
  useEffect(() => { const current = createOrganizeScope(worldId); scope.current = current; return () => current.dispose(); }, [worldId]);
  const inputKey = `mrp.world.organize.input.${worldId}`;
  const editKey = `mrp.world.organize.edits.${worldId}`;
  const operationKey = `mrp.world.organize.operations.${worldId}`;
  const [input, setInput] = useState<OrganizeInput>(() => {
    const saved = storageRead(inputKey, initialOrganizeInput(search));
    const selected = initialOrganizeInput(search);
    return new URLSearchParams(search).has('category') || selected.target_archive_id || selected.target_lorebook_id
      ? { ...saved, category: selected.category, target_archive_id: selected.target_archive_id, target_lorebook_id: selected.target_lorebook_id } : saved;
  });
  const [view, setView] = useState<'input' | 'results' | 'history' | 'legacy'>(() => new URLSearchParams(search).has('legacy') ? 'history' : new URLSearchParams(search).has('job') ? 'results' : 'input');
  const [jobId, setJobId] = useState(() => new URLSearchParams(search).get('job') || storageRead<string>(`${inputKey}.job`, ''));
  const [draftId, setDraftId] = useState('');
  const [kind, setKind] = useState<OrganizeDraft['kind']>('background');
  const [edits, setEdits] = useState<Record<string, DraftEdit>>(() => storageRead(editKey, {}));
  const [selected, setSelected] = useState<string[]>([]);
  const [approved, setApproved] = useState<string[]>([]);
  const approvedVersions = useRef<Record<string, number>>({});
  const [acceptChanges, setAcceptChanges] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [commitIds, setCommitIds] = useState<string[]>([]);
  const [pendingOperations, setPendingOperations] = useState<Record<string, OrganizeCommitInput>>(() => storageRead(operationKey, {}));
  const pendingRef = useRef(pendingOperations);
  const commitReview = useRef<{ job: OrganizeJob; edits: Record<string, DraftEdit>; approved: string[] } | null>(null);
  const [legacy, setLegacy] = useState<LegacyJob | null>(null);
  const [legacyDraftId, setLegacyDraftId] = useState('');
  const [legacyRefs, setLegacyRefs] = useState<string[]>([]);
  useEffect(() => { storageWrite(inputKey, input); }, [input, inputKey]);
  useEffect(() => { storageWrite(editKey, edits); }, [edits, editKey]);
  useEffect(() => { storageWrite(`${inputKey}.job`, jobId); }, [jobId, inputKey]);
  const worldQuery = useQuery({ ...resourceQueries.world(worldId) });
  const booksQuery = useQuery({ ...resourceQueries.lorebooks() });
  const settingsQuery = useQuery({ ...resourceQueries.settings() });
  const jobsQuery = useQuery({ queryKey: ['world-organize-jobs', worldId], queryFn: ({ signal }) => worldOrganizeApi.list(worldId, { signal }), refetchInterval: 6000 });
  const jobQuery = useQuery({ queryKey: ['world-organize-job', worldId, jobId], queryFn: ({ signal }) => worldOrganizeApi.get(jobId, { signal }), enabled: !!jobId, refetchInterval: (query) => active(query.state.data?.status || '') ? 2000 : false });
  const job = jobQuery.data?.world_id === worldId ? jobQuery.data : undefined;
  const sourcesQuery = useQuery({ queryKey: ['world-organize-sources', worldId, jobId], queryFn: ({ signal }) => worldOrganizeApi.source(jobId, { signal }), enabled: !!job });
  const pending = pendingOperations[jobId];
  useEffect(() => { setApproved((ids) => ids.filter((id) => job?.drafts.find((item) => item.id === id)?.revision === approvedVersions.current[id])); }, [job]);
  const world = worldQuery.data;
  const books = (booksQuery.data || []).filter((book) => world?.lorebook_ids.includes(book.id));
  const targetArchive = world?.archive_records.find((record) => record.id === input.target_archive_id);
  const targetBook = books.find((book) => book.id === input.target_lorebook_id);
  const currentBook = sourcesQuery.data?.target_lorebook || books.find((book) => book.id === job?.target_lorebook_id);
  const draft = job?.drafts.find((item) => item.id === draftId);
  const privateSources = job?.source_visibility === 'private' || hasPrivateOrganizeSources(sourcesQuery.data);
  const privacyLocked = !!draft && (privateSources || (draft.kind === 'lorebook' ? (effectiveBefore(draft)?.extensions as Record<string, unknown> | undefined)?.['mrp.runtime_scope'] === 'author' : sourcesQuery.data?.target_archive?.visibility === 'private'));
  function effectiveBefore(item: OrganizeDraft) { return currentBook?.entries.find((entry) => entry.uid === (edits[`${jobId}:${item.id}`]?.target_uid ?? item.target_uid)) as unknown as Record<string, unknown> || item.before_payload; }
  const editId = (item: OrganizeDraft) => `${jobId}:${item.id}`;
  const edited = (item: OrganizeDraft) => edits[editId(item)];
  const payloadOf = (item: OrganizeDraft) => edited(item)?.payload || item.payload;
  const effective = (item: OrganizeDraft): OrganizeDraft => ({ ...item, ...(edited(item) || {}), revision: item.revision });
  const changeInput = (patch: Partial<OrganizeInput>) => setInput((old) => ({ ...old, ...patch }));
  const chooseJob = (id: string) => { setJobId(id); setDraftId(''); setSelected([]); setApproved([]); setAcceptChanges(false); setError(''); setView('results'); commitReview.current = null; };
  const setJob = (value: OrganizeJob) => {
    if (!scope.current.isCurrent(value.world_id)) return;
    cache.setQueryData(['world-organize-job', worldId, value.id], value);
    void cache.invalidateQueries({ queryKey: ['world-organize-jobs', worldId] });
  };
  const rememberEdit = (item: OrganizeDraft, patch: Partial<DraftEdit>) => {
    setEdits((old) => ({ ...old, [editId(item)]: { ...(old[editId(item)] || { revision: item.revision, payload: item.payload, action: item.action, target_uid: item.target_uid }), ...patch } }));
    setApproved((old) => old.filter((id) => id !== item.id));
  };
  const rememberOperation = (id: string, body?: OrganizeCommitInput) => {
    const next = { ...storageRead<Record<string, OrganizeCommitInput>>(operationKey, pendingRef.current) };
    if (body) next[id] = body; else delete next[id];
    pendingRef.current = next; storageWrite(operationKey, next);
    if (scope.current.isCurrent(worldId)) setPendingOperations(next);
  };
  const openCommit = (ids: string[]) => {
    if (!job) return;
    commitReview.current = { job: structuredClone(job), edits: Object.fromEntries(ids.flatMap((id) => { const local = edits[`${job.id}:${id}`]; return local ? [[id, structuredClone(local)]] : []; })), approved: [...approved] };
    setCommitIds(ids); setError('');
  };
  const create = async () => {
    if (busy || !input.source_text.trim()) return;
    setBusy(true); setError(''); setNotice('');
    try { const next = await worldOrganizeApi.create(worldId, input); if (scope.current.isCurrent(next.world_id)) { setJob(next); chooseJob(next.id); } }
    catch (cause) { if (scope.current.isCurrent(worldId)) setError(errorMessage(cause)); }
    finally { if (scope.current.isCurrent(worldId)) setBusy(false); }
  };
  const forgetSavedEdit = (key: string, reviewed: DraftEdit) => {
    const stored = storageRead<Record<string, DraftEdit>>(editKey, {});
    if (JSON.stringify(stored[key]) === JSON.stringify(reviewed)) { delete stored[key]; storageWrite(editKey, stored); }
    if (scope.current.isCurrent(worldId)) setEdits((old) => { if (JSON.stringify(old[key]) !== JSON.stringify(reviewed)) return old; const next = { ...old }; delete next[key]; return next; });
  };
  const saveEdit = async (item: OrganizeDraft): Promise<void> => {
    const local = edited(item); if (!local) return;
    if (local.revision !== item.revision) throw new Error('候选在别处有更新。你的编辑仍保留，请重新打开任务并核对版本。');
    const saved = await worldOrganizeApi.edit(jobId, item.id, local.revision, local.payload, local.action, local.target_uid);
    forgetSavedEdit(editId(item), local);
    if (!scope.current.isCurrent(worldId)) return;
    cache.setQueryData<OrganizeJob>(['world-organize-job', worldId, jobId], (old) => old ? { ...old, drafts: old.drafts.map((value) => value.id === item.id ? saved : value) } : old);
  };
  const saveDraft = async () => {
    if (!draft) return; setBusy(true); setError('');
    try { await saveEdit(draft); if (scope.current.isCurrent(worldId)) { const next = await worldOrganizeApi.get(jobId); setJob(next); if (scope.current.isCurrent(worldId)) setNotice('草稿已保存，尚未写入世界。'); } }
    catch (cause) { if (scope.current.isCurrent(worldId)) setError(errorMessage(cause)); }
    finally { if (scope.current.isCurrent(worldId)) setBusy(false); }
  };
  const retry = async () => {
    if (!job) return; setBusy(true); setError('');
    try { setJob(await worldOrganizeApi.resume(job.id)); }
    catch (cause) { if (scope.current.isCurrent(worldId)) setError(errorMessage(cause)); }
    finally { if (scope.current.isCurrent(worldId)) setBusy(false); }
  };
  const cancel = async () => {
    if (!job) return; setBusy(true); setError('');
    try { setJob(await worldOrganizeApi.cancel(job.id)); }
    catch (cause) { if (scope.current.isCurrent(worldId)) setError(errorMessage(cause)); }
    finally { if (scope.current.isCurrent(worldId)) setBusy(false); }
  };
  const commit = async () => {
    if (!job || !commitIds.length || busy) return; setBusy(true); setError('');
    try {
      let body = pendingRef.current[job.id];
      if (!body) {
        const reviewed = commitReview.current;
        if (!reviewed || reviewed.job.id !== job.id) throw new Error('请重新选择候选并核对保存内容。');
        const prepared = await prepareOrganizeCommit(reviewed.job, commitIds, reviewed.approved, createClientId(), reviewed.edits, worldOrganizeApi, {
          acceptSourceChanges: acceptChanges,
          onSaved: (saved) => {
            const key = `${job.id}:${saved.id}`;
            forgetSavedEdit(key, reviewed.edits[saved.id]);
          },
        });
        if (!scope.current.isCurrent(worldId)) return;
        setJob(prepared.job); body = prepared.body;
        rememberOperation(job.id, body);
      }
      const result = await worldOrganizeApi.commit(job.id, body);
      rememberOperation(job.id);
      if (!scope.current.isCurrent(worldId)) return;
      setJob(result); setSelected([]); setCommitIds([]); setDraftId(''); setNotice('选中的候选已保存到世界。'); commitReview.current = null;
      void cache.invalidateQueries({ queryKey: ['world', worldId] }); void cache.invalidateQueries({ queryKey: ['worlds'] });
      void cache.invalidateQueries({ queryKey: resourceQueries.lorebooks().queryKey });
      void cache.invalidateQueries({ queryKey: ['world-owners'] });
      if (job.category === 'lorebook') void useLorebookStore.getState().load().catch(() => undefined);
    } catch (cause) {
      // A rejected request did not adopt anything. Network/5xx outcomes retry the exact persisted request.
      if (cause instanceof Error && /^4\d\d:/.test(cause.message)) rememberOperation(job.id);
      if (scope.current.isCurrent(worldId)) {
        setError(errorMessage(cause));
        void jobQuery.refetch();
      }
    } finally { if (scope.current.isCurrent(worldId)) setBusy(false); }
  };
  const legacyQuery = useQuery({ queryKey: ['world-organize-legacy', worldId], enabled: view === 'history', queryFn: async ({ signal }) => {
    const [imports, agents] = await Promise.all([
      cache.fetchQuery(resourceQueries.assetJobs()),
      cache.fetchQuery(resourceQueries.lorebookJobs()),
    ]);
    const importJobs = await Promise.all(imports.filter((item) => !item.world_id || item.world_id === worldId).map(async (item) => item.world_id ? item : { ...item, ...(await legacyWorldApi.importJob<{ world_id?: string }>(item.id, { signal })) }));
    return [...importJobs.filter((item) => item.world_id === worldId).map((item) => ({ ...item, world_id: item.world_id ?? undefined, title: item.title ?? '旧素材整理任务', updated_at: item.updated_at ?? '', type: 'import' as const })), ...agents.filter((item) => item.world_id === worldId).map((item) => ({ ...item, title: '原世界书整理任务', type: 'lorebook' as const }))].sort((a, b) => compareNewestFirst(a.updated_at, b.updated_at));
  } });
  const openLegacy = async (item: LegacySummary) => {
    setBusy(true); setError('');
    try {
      let next: LegacyJob;
      if (item.type === 'import') {
        const [old, source] = await Promise.all([
          legacyWorldApi.importJob<{ drafts?: unknown[]; source_visibility?: string; bundle?: { source_snapshot?: LegacyJob['sources']; manuscript_proposal?: { runtime_scope?: string } }; instruction?: string }>(item.id),
          legacyWorldApi.source(item.id),
        ]);
        next = { id: item.id, source: source.source, instruction: old.instruction || '', source_visibility: old.source_visibility === 'private' || old.bundle?.manuscript_proposal?.runtime_scope === 'author' ? 'private' : 'public', candidates: readLegacyCandidates(old), sources: old.bundle?.source_snapshot || [] };
      } else {
        const [old, source] = await Promise.all([api.getLorebookAgentJob(item.id), api.getLorebookAgentSources(item.id)]);
        next = { id: item.id, source: '', instruction: old.goal, source_visibility: 'public', candidates: readLegacyCandidates(old), sources: source.sources };
      }
      if (scope.current.isCurrent(worldId)) { setLegacy(next); setLegacyRefs([]); setLegacyDraftId(''); setView('legacy'); }
    } catch (cause) { if (scope.current.isCurrent(worldId)) setError(errorMessage(cause)); }
    finally { if (scope.current.isCurrent(worldId)) setBusy(false); }
  };
  const useLegacy = () => {
    if (!legacy) return;
    const references = legacy.sources.filter((source) => legacyRefs.includes(source.id));
    const text = [legacy.source, ...references.map((source) => `【参考：${source.title || source.id}】\n${source.content || ''}`)].filter(Boolean).join('\n\n');
    const privateSource = legacy.source_visibility === 'private' || references.some((source) => source.audience === 'author' || source.visibility === 'private');
    setInput((old) => ({ ...old, source_text: text, instruction: legacy.instruction, reference_source_ids: [], source_visibility: privateSource ? 'private' : 'public', target_archive_id: null, target_lorebook_id: null }));
    setView('input'); setNotice('已取回原文与勾选的参考，请选择本次整理目标。');
  };
  const routeSeed = new URLSearchParams(search).get('source_archive');
  const routeLegacy = new URLSearchParams(search).get('legacy');
  const openedLegacy = useRef('');
  useEffect(() => {
    if (!routeLegacy || openedLegacy.current === routeLegacy || !legacyQuery.data) return;
    const summary = legacyQuery.data.find((item) => item.id === routeLegacy);
    if (summary) { openedLegacy.current = routeLegacy; void openLegacy(summary); }
  }, [routeLegacy, legacyQuery.data]);
  const seededSource = useRef(storageRead<string>(`${inputKey}.seed`, ''));
  useEffect(() => {
    if (!routeSeed || !world || seededSource.current === routeSeed) return;
    const source = world.archive_records.find((item) => item.id === routeSeed);
    if (source) { seededSource.current = routeSeed; storageWrite(`${inputKey}.seed`, routeSeed); setInput((old) => ({ ...old, source_text: source.body, category: 'lorebook', target_archive_id: null, source_visibility: source.visibility })); }
  }, [routeSeed, world?.id]);
  if (worldQuery.isLoading) return <LoadState title="正在打开世界整理页" />;
  if (!world || worldQuery.error) return <LoadState title="世界读取失败" error={errorMessage(worldQuery.error)} onRetry={() => void worldQuery.refetch()} />;
  const readableError = error || (view === 'results' && jobQuery.error ? errorMessage(jobQuery.error) : '');
  const currentKind = job?.category === 'lorebook' ? 'lorebook' : kind === 'lorebook' ? 'background' : kind;
  const visibleDrafts = organizeCandidates(job?.drafts || [], currentKind);
  const selectedDrafts = (commitReview.current?.job.id === jobId && !pending ? commitReview.current.job.drafts.map((item) => ({ ...item, ...commitReview.current?.edits[item.id] })) : job?.drafts || []).filter((item) => commitIds.includes(item.id));
  const model = job?.model || settingsQuery.data?.auxiliary_model || settingsQuery.data?.model || '尚未设置';
  const provider = job?.provider || settingsQuery.data?.auxiliary_provider || settingsQuery.data?.model_provider || '自动选择';
  const selectedLegacy = legacy?.candidates.find((candidate) => candidate.id === legacyDraftId);
  return <main className="story-home worlds-page world-organizer">
    <Link to={`/worlds/${worldId}`} className="worlds-back"><ArrowLeft size={16} />返回 {world.title}</Link>
    <header className="story-home-header organize-header"><div className="home-page-title"><MoonOrnament /><h1>AI 整理</h1><span className="home-title-rule" aria-hidden="true" /></div><p>{world.title}</p></header>
    <nav className="organize-navigation" aria-label="整理视图">
      <button disabled={busy} className={`home-button ${view === 'input' ? 'is-active' : ''}`} onClick={() => { setView('input'); setDraftId(''); }}>原文与目标</button>
      <button disabled={busy} className={`home-button ${view === 'results' ? 'is-active' : ''}`} onClick={() => setView('results')}>整理结果</button>
      <button disabled={busy} className={`home-button ${view === 'history' || view === 'legacy' ? 'is-active' : ''}`} onClick={() => { setView('history'); setLegacyDraftId(''); }}>任务记录</button>
    </nav>
    {readableError && <div className="home-message is-error" role="alert"><p>{readableError}</p>{view === 'results' && (jobQuery.error || error) && <button className="home-button" onClick={() => void jobQuery.refetch()}>重新读取结果</button>}</div>}
    {notice && <div className="home-message" role="status">{notice}</div>}
    <fieldset className="organize-workflow-fields" disabled={busy} aria-label="整理工作区">
    {view === 'input' && <section className="organize-surface home-featured">
      <div className="home-section-heading"><h2>本次整理</h2><span className="home-section-rule" aria-hidden="true" /></div>
      <div className="organize-category" role="group" aria-label="整理类别">
        <button className={`home-button ${input.category === 'archives' ? 'is-active' : ''}`} onClick={() => changeInput({ category: 'archives', target_lorebook_id: null })}><FileText size={17} />世界档案</button>
        <button className={`home-button ${input.category === 'lorebook' ? 'is-active' : ''}`} onClick={() => changeInput({ category: 'lorebook', target_archive_id: null })}><BookOpen size={17} />世界书</button>
      </div>
      <p className="organize-muted">{input.category === 'archives' ? '保留原稿事实，按内容整理为背景文章或生物资料。' : '把原稿整理成供故事使用的触发词条；现有书默认补充新条目。'}</p>
      {input.category === 'archives' ? <label className="worlds-field">保存目标<select value={input.target_archive_id || ''} onChange={(event) => changeInput({ target_archive_id: event.target.value || null })}><option value="">新建档案，让 AI 按内容拆分</option>{world.archive_records.filter((record) => ['background', 'biology'].includes(record.kind)).map((record) => <option value={record.id} key={record.id}>替换 {record.kind === 'biology' ? '生物' : '背景'}档案：{record.title}</option>)}</select></label>
        : <div className="worlds-editor-grid"><label className="worlds-field">目标世界书<select value={input.target_lorebook_id || ''} onChange={(event) => changeInput({ target_lorebook_id: event.target.value || null })}><option value="">新建世界书</option>{books.map((book) => <option value={book.id} key={book.id}>{book.name}</option>)}</select></label>{!input.target_lorebook_id && <label className="worlds-field">新世界书名称<input value={input.new_lorebook_name} onChange={(event) => changeInput({ new_lorebook_name: event.target.value })} placeholder={`${world.title} · 世界书`} /></label>}</div>}
      {(targetArchive || targetBook) && <div className="organize-target-context"><strong>{targetArchive ? `将替换：${targetArchive.title}` : `将补充：${targetBook?.name}`}</strong><p>{targetArchive ? '新原文将成为替换依据。保存前可查看原档案与候选，原有可见性会保留。' : `${targetBook?.entries.length} 条现有词条仅用来核对重复和更新；其余词条保持原样。`}</p><div className="organize-context-preview">{targetArchive?.body || targetBook?.entries.slice(0, 4).map((entry) => `${entry.comment || entry.keys.join('、')}：${entry.content}`).join('\n\n')}</div></div>}
      <label className="worlds-field">完整原文<textarea className="organize-original-input" rows={14} value={input.source_text} onChange={(event) => changeInput({ source_text: event.target.value })} placeholder="在这里粘贴要整理的完整设定。离开页面后，这份输入仍会保存在当前浏览器。" /></label>
      <label className="worlds-field">整理要求（可选）<textarea rows={3} value={input.instruction} onChange={(event) => changeInput({ instruction: event.target.value })} placeholder="例如：保留所有事实，背景按地理和历史拆分。" /></label>
      <fieldset className="organize-reference-list"><legend>额外参考资料（可选）</legend><p>只向 AI 提供你勾选的资料。选中的保存目标会自动用于对照。</p>
        {!!world.core_brief && <label><input type="checkbox" checked={input.reference_source_ids.includes(`world:${worldId}:core`)} onChange={() => changeInput({ reference_source_ids: toggleOrganizeReference(input.reference_source_ids, `world:${worldId}:core`) })} /><span>世界核心摘要</span></label>}
        {world.archive_records.filter((record) => record.id !== input.target_archive_id).map((record) => <label key={record.id}><input type="checkbox" checked={input.reference_source_ids.includes(record.id)} onChange={() => changeInput({ reference_source_ids: toggleOrganizeReference(input.reference_source_ids, record.id) })} /><span>{record.title}<small>{record.kind === 'biology' ? '生物' : '背景'} · {record.visibility === 'private' ? '仅作者可见' : '供故事使用'}</small></span></label>)}
        {!world.core_brief && !world.archive_records.length && <p>当前世界没有其他参考资料。</p>}
      </fieldset>
      {!input.target_archive_id && <label className="organize-check"><input type="checkbox" checked={input.source_visibility === 'private'} onChange={(event) => changeInput({ source_visibility: event.target.checked ? 'private' : 'public' })} />新资料仅作者可见 <small>每条候选还可单独修改</small></label>}
      <div className="organize-model-summary"><Sparkles size={17} /><span>使用全局辅助模型：{settingsQuery.data?.auxiliary_model || settingsQuery.data?.model || '读取中'} · {settingsQuery.data?.auxiliary_provider || settingsQuery.data?.model_provider || '自动选择'}<small>思考：{settingsQuery.data?.thinking === 'on' ? '开启' : '关闭'} · 输出不限于固定字数</small></span><Link to="/settings">查看连接设置</Link></div>
      <div className="organize-actions"><button className="home-button home-button-primary" disabled={busy || !input.source_text.trim() || !settingsQuery.data || !!settingsQuery.error || (!!input.target_archive_id && !targetArchive) || (!!input.target_lorebook_id && !targetBook)} onClick={() => void create()}><Sparkles size={17} />{busy ? '提交中…' : '开始整理'}</button><span>任务可在后台继续，结果会保存到服务器。</span></div>
      {settingsQuery.error && <div className="home-message is-error">模型设置读取失败。<button className="home-button" onClick={() => void settingsQuery.refetch()}>重试</button></div>}
    </section>}
    {view === 'results' && <>
      {!!jobsQuery.data?.length && <label className="worlds-field organize-job-select">当前任务<select disabled={busy} value={jobId} onChange={(event) => chooseJob(event.target.value)}><option value="">选择一次整理</option>{jobsQuery.data.map((item) => <option key={item.id} value={item.id}>{item.category === 'archives' ? '档案' : '世界书'} · {statusLabel[item.status] || item.status} · {new Date(item.created_at).toLocaleString('zh-CN')}</option>)}</select></label>}
      {jobQuery.isLoading && jobId ? <LoadState title="正在读取整理结果" /> : !job ? <section className="home-empty"><Sparkles size={30} /><h3>还没有选中整理任务</h3><p>粘贴原文并选择目标后，候选会出现在这里。</p><button className="home-button" onClick={() => setView('input')}>填写原文与目标</button></section> : <>
        <div className="home-message organize-progress" role="status">{active(job.status) && <LoaderCircle className="home-spinner" size={18} />}<div><strong>{statusLabel[job.status] || job.status}</strong><p>{job.stage || (active(job.status) ? '你可以离开此页，任务会继续。' : '点击候选逐条核对，保存后才会写入世界。')}</p><small>辅助模型：{model} · {provider}{job.batch_total ? ` · ${organizeCompletedBatches(job)}/${job.batch_total} 批` : job.progress?.total_batches ? ` · ${job.progress.completed_batches || 0}/${job.progress.total_batches} 批` : ''}</small></div>{active(job.status) && <button className="home-button" disabled={busy} onClick={() => void cancel()}>取消整理</button>}{['failed', 'interrupted', 'cancelled'].includes(job.status) && <button className="home-button" disabled={busy} onClick={() => void retry()}>继续整理</button>}</div>
        {sourcesQuery.error && <div className="home-message is-error">冻结来源读取失败，可见性暂时锁定。<button className="home-button" onClick={() => void sourcesQuery.refetch()}>重新读取来源</button></div>}
        {pending && <div className="home-message" role="status"><p>上次保存的结果尚未确认。重新确认会核对同一次保存，离开页面后也可继续。</p><button className="home-button" disabled={busy} onClick={() => setCommitIds(pending.draft_ids)}>重新确认上次保存</button></div>}
        {!!job.errors.length && <div className="home-message is-error" role="alert">{job.errors.map((message, index) => <p key={index}>{message}</p>)}{['failed', 'interrupted', 'cancelled'].includes(job.status) && <button className="home-button" disabled={busy} onClick={() => void retry()}>重试本次整理</button>}<button className="home-button" onClick={() => setView('input')}>返回原文与目标</button></div>}
        {draft ? <section className="organize-surface home-featured organize-candidate-editor">
          <button className="home-button organize-editor-back" disabled={busy} onClick={() => setDraftId('')}><ArrowLeft size={16} />返回候选目录</button>
          <div className="home-section-heading"><h2>{draftTitle(draft, payloadOf(draft))}</h2><span className="home-section-rule" aria-hidden="true" /></div>
          <fieldset className="organize-editor-fields" disabled={busy || active(job.status) || !!pending}>
          {draft.status === 'committed' ? <><p>这条候选已保存。</p><ReadOnlyPayload payload={payloadOf(draft)} /></> : <>
            {edited(draft) && edited(draft)?.revision !== draft.revision && <p className="home-message is-error">服务器上的候选已更新。你的编辑保留在这里，复制需要的内容后可放弃本机修改并重新核对。<button className="home-button" onClick={() => setEdits((old) => { const next = { ...old }; delete next[editId(draft)]; return next; })}>放弃本机修改</button></p>}
            {draft.kind === 'lorebook' ? <>
              {!!currentBook && <label className="worlds-field">保存方式<select value={effective(draft).action === 'replace' ? String(effective(draft).target_uid ?? '') : 'add'} onChange={(event) => rememberEdit(draft, event.target.value === 'add' ? { action: 'add', target_uid: undefined } : { action: 'replace', target_uid: Number(event.target.value) })}><option value="add">新增词条</option>{currentBook.entries.map((entry) => <option key={entry.uid} value={entry.uid}>更新：{entry.comment || entry.keys.join('、') || `词条 ${entry.uid}`}</option>)}</select></label>}
              <LorebookEntryFields value={bookValue(payloadOf(draft))} onChange={(value) => rememberEdit(draft, { payload: value as unknown as Record<string, unknown> })} />
              <label className="organize-check"><input type="checkbox" disabled={privacyLocked || sourcesQuery.isLoading || !!sourcesQuery.error} checked={privacyLocked || bookValue(payloadOf(draft)).extensions['mrp.runtime_scope'] === 'author' || (!edited(draft) && draft.runtime_scope === 'author')} onChange={(event) => rememberEdit(draft, { payload: { ...payloadOf(draft), extensions: { ...bookValue(payloadOf(draft)).extensions, 'mrp.runtime_scope': event.target.checked ? 'author' : 'shared' } } })} />仅作者可见 <small>作者资料不会进入故事；私密来源不能转为公开</small></label>
              {effective(draft).action === 'replace' && <div className="organize-before"><h3>将被更新的原词条</h3><ReadOnlyPayload payload={effectiveBefore(draft) || {}} /><label className="organize-check"><input type="checkbox" checked={approved.includes(draft.id)} onChange={() => { approvedVersions.current[draft.id] = draft.revision; setApproved((old) => toggleOrganizeReference(old, draft.id)); }} />我已核对，确认更新这一条词条</label></div>}
              <div className="organize-actions"><button className="home-button" disabled={busy || !edited(draft)} onClick={() => void saveDraft()}><Save size={16} />保存草稿</button><button className="home-button home-button-primary" disabled={busy || !String(payloadOf(draft).content || '').trim() || (effective(draft).action === 'replace' && !approved.includes(draft.id))} onClick={() => openCommit([draft.id])}>保存此词条</button></div>
            </> : <ArchiveRecordEditor kind={draft.kind} visibilityLocked={privacyLocked || sourcesQuery.isLoading || !!sourcesQuery.error} bodyHelp="核对整理后的完整正文，确保事实、条件、代价和例外都已保留。" value={archiveValue(draft, privacyLocked ? { ...payloadOf(draft), visibility: 'private' } : payloadOf(draft))} onChange={(value) => rememberEdit(draft, { payload: value as unknown as Record<string, unknown> })} onSave={() => openCommit([draft.id])} onCancel={() => setDraftId('')} busy={busy} saveLabel={draft.action === 'replace' ? '核对并替换档案' : '保存此档案'} />}
            {draft.kind !== 'lorebook' && <button className="home-button" disabled={busy || !edited(draft)} onClick={() => void saveDraft()}><Save size={16} />保存草稿</button>}
          </>}
          {draft.kind !== 'lorebook' && draft.before_payload && <div className="organize-before"><h3>原档案对照</h3><ReadOnlyPayload payload={draft.before_payload} /></div>}
          {!!draft.source_refs?.length && <div className="organize-evidence"><h3>原文依据</h3>{draft.source_refs.map((ref, index) => <blockquote key={index}>{ref.quote || ref.source_id}</blockquote>)}</div>}
          {!!draft.errors?.length && <div className="home-message is-error">{draft.errors.join('；')}</div>}
          </fieldset>
        </section> : <section className="organize-surface home-featured">
          <div className="home-section-heading"><h2>{job.category === 'archives' ? '档案候选' : '世界书词条候选'}</h2><span className="home-section-rule" aria-hidden="true" /></div>
          {job.category === 'archives' && <div className="organize-category" role="group" aria-label="候选分类"><button className={`home-button ${currentKind === 'background' ? 'is-active' : ''}`} onClick={() => setKind('background')}>背景 {organizeCandidates(job.drafts, 'background').length}</button><button className={`home-button ${currentKind === 'biology' ? 'is-active' : ''}`} onClick={() => setKind('biology')}>生物 {organizeCandidates(job.drafts, 'biology').length}</button></div>}
          <p className="organize-muted">点击名称打开完整编辑器。返回目录时，修改会保留。</p>
          <div className="organize-candidate-list">{visibleDrafts.map((item) => <article className="story-card organize-candidate" key={item.id}>
            <label className="organize-select-check"><input type="checkbox" aria-label={`选择 ${draftTitle(item, payloadOf(item))}`} checked={selected.includes(item.id)} disabled={item.status !== 'review' || active(job.status)} onChange={() => { setSelected((old) => toggleOrganizeReference(old, item.id)); }} /></label>
            <button disabled={busy} className="organize-candidate-open" onClick={() => setDraftId(item.id)}><strong>{draftTitle(item, payloadOf(item))}</strong><span>{String(payloadOf(item).summary || payloadOf(item).body || payloadOf(item).content || '').slice(0, 130)}</span><small>{item.status === 'committed' ? '已保存' : effective(item).action === 'replace' ? '更新现有内容，待确认' : '新增'}{edited(item) ? ' · 有保留的修改' : ''}</small></button><span className="organize-candidate-arrow" aria-hidden="true">›</span>
          </article>)}</div>
          {!visibleDrafts.length && <p className="organize-muted">{active(job.status) ? '整理完成后，候选会出现在这里。' : '此分类没有候选。可以切换分类或返回原文发起新的整理。'}</p>}
          {!pending && !!job.drafts.some((item) => item.status === 'review') && <div className="organize-actions"><button className="home-button" onClick={() => setSelected(visibleDrafts.filter((item) => item.status === 'review').map((item) => item.id))} disabled={active(job.status)}>选择本类</button><button className="home-button" onClick={() => setSelected([])}>清空选择</button><button className="home-button home-button-primary" disabled={busy || active(job.status) || !selected.length || job.drafts.some((item) => selected.includes(item.id) && effective(item).kind === 'lorebook' && effective(item).action === 'replace' && !approved.includes(item.id))} onClick={() => openCommit(selected)}>保存所选 {selected.length} 项</button></div>}
          {job.drafts.some((item) => selected.includes(item.id) && effective(item).kind === 'lorebook' && effective(item).action === 'replace' && !approved.includes(item.id)) && <p className="organize-muted">所选内容包含待确认的更新，请先打开对应候选逐条确认。</p>}
          <button className="home-button" onClick={() => { setLegacy({ id: job.id, source: job.source_text, instruction: job.instruction, source_visibility: job.source_visibility, sources: [], candidates: [] }); setLegacyDraftId(''); setView('legacy'); }}>查看本次完整原文</button>
        </section>}
      </>}
    </>}
    {view === 'history' && <section className="organize-surface home-featured"><div className="home-section-heading"><h2>整理记录</h2><span className="home-section-rule" aria-hidden="true" /></div>
      {jobsQuery.isLoading ? <LoadState title="读取任务记录" /> : jobsQuery.error ? <LoadState title="任务记录读取失败" error={errorMessage(jobsQuery.error)} onRetry={() => void jobsQuery.refetch()} /> : <div className="organize-history-list">{jobsQuery.data?.map((item) => <button className="organize-history-row" disabled={busy} key={item.id} onClick={() => chooseJob(item.id)}><span>{item.category === 'archives' ? '世界档案' : '世界书'}</span><strong>{statusLabel[item.status] || item.status}</strong><small>{new Date(item.updated_at).toLocaleString('zh-CN')}</small></button>)}</div>}
      {!jobsQuery.data?.length && <p className="organize-muted">尚无新整理任务。</p>}
      <h3>旧任务 · 只读</h3><p className="organize-muted">旧任务的原文和候选可查看，并可带入新的整理流程。</p>
      {legacyQuery.isLoading ? <LoadState title="读取旧任务" /> : legacyQuery.error ? <LoadState title="旧任务读取失败" error={errorMessage(legacyQuery.error)} onRetry={() => void legacyQuery.refetch()} /> : <div className="organize-history-list">{legacyQuery.data?.map((item) => <button className="organize-history-row" key={`${item.type}:${item.id}`} disabled={busy} onClick={() => void openLegacy(item)}><span>{item.title || '旧整理任务'}</span><strong>{statusLabel[item.status] || item.status}</strong><small>只读</small></button>)}</div>}
      {!legacyQuery.data?.length && !legacyQuery.isLoading && <p className="organize-muted">没有此世界的旧整理任务。</p>}
    </section>}
    {view === 'legacy' && legacy && <section className="organize-surface home-featured">
      <button className="home-button" onClick={() => selectedLegacy ? setLegacyDraftId('') : setView('history')}><ArrowLeft size={16} />{selectedLegacy ? '返回旧候选' : '返回整理记录'}</button>
      {selectedLegacy ? <><div className="home-section-heading"><h2>{selectedLegacy.title}</h2><span className="home-section-rule" aria-hidden="true" /></div><p className="organize-muted">保留的旧候选 · 只读</p><ReadOnlyPayload payload={selectedLegacy.payload} /></> : <>
        <div className="home-section-heading"><h2>保留的原文</h2><span className="home-section-rule" aria-hidden="true" /></div><pre className="organize-original-readonly">{legacy.source || '旧任务没有单独粘贴的原文，可勾选保留的参考资料带入新任务。'}</pre>
        {!!legacy.sources.length && <fieldset className="organize-reference-list"><legend>带入新任务的参考（可选）</legend>{legacy.sources.map((source) => <label key={source.id}><input type="checkbox" checked={legacyRefs.includes(source.id)} onChange={() => setLegacyRefs((old) => toggleOrganizeReference(old, source.id))} /><span>{source.title || source.id}</span></label>)}</fieldset>}
        <button className="home-button home-button-primary" disabled={!legacy.source && !legacyRefs.length} onClick={useLegacy}>带入原文与所选参考</button>
        {!!legacy.candidates.length && <><h3>保留的旧候选</h3><div className="organize-history-list">{legacy.candidates.map((candidate) => <button className="organize-history-row" key={candidate.id} onClick={() => setLegacyDraftId(candidate.id)}><span>{candidate.title}</span><small>查看完整候选</small></button>)}</div></>}
      </>}
    </section>}
    </fieldset>
    <Dialog.Root open={!!commitIds.length} onOpenChange={(open) => { if (!open && !busy) setCommitIds([]); }}><Dialog.Portal><Dialog.Overlay className="v7-dialog-overlay" /><Dialog.Content className="v7-dialog-content organize-confirm-dialog"><Dialog.Title>保存所选候选</Dialog.Title><Dialog.Description>{pending ? '重新确认上次保存的结果；会使用上次已确认的内容和范围。' : `这次将保存 ${commitIds.length} 项。请核对写入目标与可见性。`}</Dialog.Description>
      <ul>{selectedDrafts.map((item) => <li key={item.id}><Check size={15} /><span><strong>{draftTitle(item, item.payload)}</strong><small>{item.action === 'replace' ? '替换已有内容' : '新增'} · {item.kind === 'lorebook' ? bookValue(item.payload).extensions['mrp.runtime_scope'] === 'author' || item.runtime_scope === 'author' ? '仅作者可见' : bookValue(item.payload).enabled ? '已启用，供故事使用' : '停用，作者留存' : item.payload.visibility === 'private' ? '仅作者可见' : '供故事使用'}</small></span></li>)}</ul>
      {!pending && job?.source_changed && <label className="organize-check"><input type="checkbox" checked={acceptChanges} onChange={(event) => setAcceptChanges(event.target.checked)} />参考资料已有变化，我已核对本次保存内容</label>}
      {error && <p className="v7-error" role="alert">{error}</p>}
      <div className="v7-dialog-actions"><button className="v7-btn v7-btn-soft" disabled={busy} onClick={() => setCommitIds([])}>继续核对</button><button className="v7-btn v7-btn-primary" disabled={busy || (!pending && !!job?.source_changed && !acceptChanges)} onClick={() => void commit()}>{busy ? '保存中…' : pending ? '重新确认上次保存' : selectedDrafts.some((item) => effective(item).action === 'replace') ? '确认保存与替换' : '确认保存'}</button></div>
    </Dialog.Content></Dialog.Portal></Dialog.Root>
  </main>;
}

function ReadOnlyPayload({ payload }: { payload: Record<string, unknown> }) {
  return <div className="organize-readonly-fields">{Object.entries(payload).filter(([key, value]) => !['extensions', 'uid'].includes(key) && value !== '' && value !== null && value !== undefined).map(([key, value]) => <div key={key}><strong>{{ title: '名称', body: '完整正文', content: '词条内容', summary: '摘要', comment: '词条备注', keys: '关键词', aliases: '别名', tags: '标签', visibility: '可见性', kind_data: '结构资料', enabled: '启用', constant: '常驻', secondary_keys: '辅助关键词', selective: '辅助关键词条件', selective_logic: '组合逻辑', order: '排序', anchor: '注入位置', depth: '深度', probability: '概率', subtype: '子类', runtime_scope: '使用范围' }[key] || key}</strong><pre>{typeof value === 'string' ? value : Array.isArray(value) ? value.join('、') : typeof value === 'object' ? Object.entries(value || {}).map(([name, text]) => `${name}：${text}`).join('\n') : String(value)}</pre></div>)}</div>;
}
