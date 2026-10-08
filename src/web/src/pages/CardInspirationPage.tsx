import { queryClient } from '../queryClient';
import { resourceQueries } from '../features/resources/resourceQueries';
import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { creationClient as api } from '../features/creation/creationClient';
import CharacterEditor from '../components/CharacterEditor';
import type { CharacterEditorPayload } from '../components/CharacterEditor';
import { Avatar, Button, Checkbox, TextInput, Tabs, TabsList, TabsTrigger, WorkbenchColumn, WorkbenchDesk } from '../design-system';
import { X, Search } from '../design-system/Icon';
import { WorkshopDisclosure, WorkshopNotice, WorkshopWait, useElapsedSeconds } from './WorkshopUi';
import { committedInspirationSnapshot, createRequestScope, sameBrief } from './inspirationState';
import './inspiration-production.css';
import { useCharacterStore } from '../store/characterStore';
import type {
  CardInspirationDraft,
  CardInspirationBrief,
  CardInspirationJob,
  CardSearchPage,
  CharacterCard,
  DiscoveredCard,
  DiscoveredCardDetail,
} from '../types';

const hitKey = (hit: Pick<DiscoveredCard, 'source_id' | 'card_id'>) => `${hit.source_id}:${hit.card_id}`;

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function contentRatingLabel(rating: DiscoveredCard['content_rating']) {
  const labels: Record<DiscoveredCard['content_rating'], string> = {
    sfw: '适合公开阅读', sensitive: '敏感内容', unknown: '未标注',
  };
  return labels[rating];
}

function statusLabel(status: CardInspirationJob['status']) {
  const labels: Record<CardInspirationJob['status'], string> = {
    ready: '待生成', queued: '排队中', generating: '生成中', agent_running: 'Agent 构思中', review: '待审阅',
    failed: '失败', interrupted: '已中断', committed: '已保存', cancelled: '已取消',
  };
  return labels[status];
}

function fieldLabel(field: CardInspirationDraft['field_history'][number]['field']) {
  const labels: Record<CardInspirationDraft['field_history'][number]['field'], string> = {
    name: '姓名', description: '描述', appearance: '外貌', traits_label: '特质栏名称', traits: '核心特质', personality: '性格',
    scenario: '场景', first_mes: '开场语', mes_example: '示例对话',
  };
  return labels[field];
}

function CardFields({ card }: { card: CharacterCard }) {
  const fields: [string, string][] = [
    ['简介', card.description], ['外观', card.appearance],
    [card.traits_label?.trim() || '能力与实力', card.traits], ['性格', card.personality],
    ['场景', card.scenario], ['开场白', card.first_mes], ['对话示例', card.mes_example],
  ];
  return <div className="inspiration-fields-preview">
    {fields.filter(([, value]) => value?.trim()).map(([label, value]) => <div key={label}>
      <strong>{label}</strong><p className="inspiration-copy">{value}</p>
    </div>)}
  </div>;
}

export default function CardInspirationPage() {
  const { data: sources = [] } = useQuery(resourceQueries.cardSources());
  const [sourceError, setSourceError] = useState('');
  const [sourceIds, setSourceIds] = useState<string[]>(['botbooru', 'chub']);
  const [query, setQuery] = useState('');
  const [submittedQuery, setSubmittedQuery] = useState('');
  const [pages, setPages] = useState<CardSearchPage[]>([]);
  const [pageHistory, setPageHistory] = useState<Record<string, CardSearchPage[]>>({});
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [busyPage, setBusyPage] = useState('');
  const [detail, setDetail] = useState<DiscoveredCardDetail | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [previewHit, setPreviewHit] = useState<DiscoveredCard | null>(null);
  const [selectedRefs, setSelectedRefs] = useState<DiscoveredCard[]>([]);
  const [requirement, setRequirement] = useState('');
  const [detailBrief, setDetailBrief] = useState('');
  const [borrow, setBorrow] = useState('');
  const [avoid, setAvoid] = useState('');
  const [createBusy, setCreateBusy] = useState(false);
  const [job, setJob] = useState<CardInspirationJob | null>(null);
  const { data: jobs = [] } = useQuery(resourceQueries.cardJobs());
  const [taskError, setTaskError] = useState('');
  const [briefDraft, setBriefDraft] = useState<CardInspirationBrief>({ requirement: '', detail: '', borrow: '', avoid: '' });
  const [briefBusy, setBriefBusy] = useState(false);
  const [agentInput, setAgentInput] = useState('');
  const [agentBusy, setAgentBusy] = useState(false);
  const [editingDraft, setEditingDraft] = useState<CardInspirationDraft | null>(null);
  const [savingDraft, setSavingDraft] = useState(false);
  const [saveMessage, setSaveMessage] = useState('');

  const [resultTab, setResultTab] = useState<'search' | 'tasks'>('search');
  const [mobilePane, setMobilePane] = useState<'input' | 'result'>('input');
  const [detailOpen, setDetailOpen] = useState(false);
  const taskScope = useRef(createRequestScope());
  const previewScope = useRef(createRequestScope());
  const searchScope = useRef(createRequestScope());
  const briefDirty = useRef(false);
  const briefOwner = useRef('');
  const committedCharacter = useRef<{ id: string; revision: number | undefined } | null>(null);
  const taskBusy = createBusy || briefBusy || agentBusy || savingDraft;
  const working = createBusy || agentBusy || !!job && ['queued', 'generating', 'agent_running'].includes(job.status);
  const elapsed = useElapsedSeconds(working);
  useEffect(() => () => { taskScope.current.advance(); previewScope.current.advance(); searchScope.current.advance(); }, []);
  const changeBrief = (field: keyof CardInspirationBrief, value: string) => {
    briefDirty.current = true;
    setBriefDraft(current => ({ ...current, [field]: value }));
  };

  const selectedKeys = useMemo(() => new Set(selectedRefs.map(hitKey)), [selectedRefs]);
  const sourceName = useCallback((sourceId: string) => sources.find((source) => source.source_id === sourceId)?.label ?? sourceId, [sources]);

  const refreshJobs = useCallback(async () => {
    try { await queryClient.invalidateQueries({ queryKey: resourceQueries.cardJobs().queryKey }); await queryClient.fetchQuery(resourceQueries.cardJobs()); } catch { /* current task remains usable */ }
  }, []);

  useEffect(() => {
    let active = true;
    queryClient.fetchQuery(resourceQueries.cardSources())
      .catch((error) => { if (active) setSourceError(errorText(error)); });
    void refreshJobs();
    return () => { active = false; };
  }, [refreshJobs]);

  useEffect(() => {
    if (!job || !['queued', 'generating', 'agent_running'].includes(job.status)) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const owns = taskScope.current.capture();
    const poll = async () => {
      try {
        const updated = await api.getCardInspirationJob(job.id);
        if (!live || !owns()) return;
        setJob(updated);
        if (!['queued', 'generating', 'agent_running'].includes(updated.status)) void refreshJobs();
      } catch (error) { if (live && owns()) setTaskError(errorText(error)); }
      finally { if (live && owns()) timer = window.setTimeout(poll, 1800); }
    };
    timer = window.setTimeout(poll, 1800);
    return () => { live = false; window.clearTimeout(timer); };
  }, [job?.id, job?.status, refreshJobs]);

  useEffect(() => {
    if (!job) return;
    const changedTask = briefOwner.current !== job.id;
    if (changedTask || !briefDirty.current) {
      briefOwner.current = job.id;
      briefDirty.current = false;
      setBriefDraft({ requirement: job.requirement, detail: job.detail, borrow: job.borrow, avoid: job.avoid });
    }
  }, [job?.id, job?.brief_revision]);

  const toggleSource = (id: string) => setSourceIds((current) => current.includes(id)
    ? current.filter((value) => value !== id) : [...current, id]);

  const runSearch = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!query.trim() || sourceIds.length === 0 || searching || busyPage) return;
    const searchQuery = query.trim();
    searchScope.current.advance();
    previewScope.current.advance();
    const owns = searchScope.current.capture();
    setDetailOpen(false);
    setDetailBusy(false);
    setDetailError('');
    setResultTab('search');
    setMobilePane('result');
    setSearching(true);
    setSearchError('');
    setDetail(null);
    setSubmittedQuery(searchQuery);
    try {
      const response = await api.searchCards({ query: searchQuery, source_ids: sourceIds, limit: 12 });
      if (!owns()) return;
      setPages(response.sources);
      setPageHistory({});
    } catch (error) {
      if (!owns()) return;
      setSearchError(errorText(error));
      setPages([]);
      setPageHistory({});
    } finally {
      if (owns()) setSearching(false);
    }
  };

  const loadNext = async (page: CardSearchPage) => {
    if (!page.next_cursor || busyPage || searching || !submittedQuery) return;
    const owns = searchScope.current.capture();
    setBusyPage(page.source_id);
    try {
      const next = await api.nextCardPage({ source_id: page.source_id, query: submittedQuery, cursor: page.next_cursor, limit: 12 });
      if (!owns()) return;
      setPageHistory((current) => ({
        ...current,
        [page.source_id]: [...(current[page.source_id] ?? []), page],
      }));
      setPages((current) => current.map((row) => row.source_id === page.source_id ? next : row));
    } catch (error) {
      if (!owns()) return;
      setPages((current) => current.map((row) => row.source_id === page.source_id ? { ...row, error: errorText(error) } : row));
    } finally {
      if (owns()) setBusyPage('');
    }
  };

  const loadPrevious = (page: CardSearchPage) => {
    if (busyPage || searching) return;
    const history = pageHistory[page.source_id] ?? [];
    const previous = history[history.length - 1];
    if (!previous) return;
    setPages((current) => current.map((row) => row.source_id === page.source_id ? previous : row));
    setPageHistory((current) => ({
      ...current,
      [page.source_id]: (current[page.source_id] ?? []).slice(0, -1),
    }));
  };

  const openPreview = async (hit: DiscoveredCard) => {
    previewScope.current.advance();
    const owns = previewScope.current.capture();
    setDetailOpen(true);
    setPreviewHit(hit);
    setDetail(null);
    setDetailError('');
    setDetailBusy(true);
    try {
      const loaded = await api.getDiscoveredCard(hit.source_id, hit.card_id);
      if (owns()) setDetail(loaded);
    } catch (error) {
      if (owns()) setDetailError(errorText(error));
    } finally {
      if (owns()) setDetailBusy(false);
    }
  };

  const toggleReference = (hit: DiscoveredCard) => {
    setSelectedRefs((current) => current.some((item) => hitKey(item) === hitKey(hit))
      ? current.filter((item) => hitKey(item) !== hitKey(hit))
      : current.length >= 5 ? current : [...current, hit]);
  };

  const setCurrentJob = async (jobId: string) => {
    if (taskBusy) return;
    taskScope.current.advance();
    const owns = taskScope.current.capture();
    setEditingDraft(null);
    committedCharacter.current = null;
    setJob(null);
    setDetailOpen(true);
    setMobilePane('result');
    setTaskError('');
    setSaveMessage('');
    try {
      const loaded = await api.getCardInspirationJob(jobId);
      if (owns()) setJob(loaded);
    }
    catch (error) { if (owns()) setTaskError(errorText(error)); }
  };

  const createTask = async () => {
    if (!requirement.trim() || selectedRefs.length === 0 || taskBusy) return;
    setCreateBusy(true);
    setTaskError('');
    setSaveMessage('');
    taskScope.current.advance();
    const owns = taskScope.current.capture();
    setResultTab('tasks');
    setMobilePane('result');
    setDetailOpen(false);
    committedCharacter.current = null;
    let createdJob: CardInspirationJob | null = null;
    try {
      const created = await api.createCardInspirationJob({
        search_query: query.trim(),
        requirement: requirement.trim(), detail: detailBrief.trim(), borrow: borrow.trim(), avoid: avoid.trim(),
        references: selectedRefs.map(({ source_id, card_id }) => ({ source_id, card_id })),
      });
      createdJob = created;
      if (!owns()) return;
      setJob(created);
      await refreshJobs();
      const generated = await api.generateCardInspirationDrafts(created.id, 1);
      if (owns()) setJob(generated);
    } catch (error) {
      if (!owns()) return;
      setTaskError(errorText(error));
      if (createdJob) setJob(createdJob);
      await refreshJobs();
    } finally {
      if (owns()) setCreateBusy(false);
    }
  };

  const regenerate = async () => {
    if (!job || createBusy) return;
    const owns = taskScope.current.capture();
    setCreateBusy(true);
    setTaskError('');
    try { const updated = await api.generateCardInspirationDrafts(job.id, 1); if (owns()) setJob(updated); }
    catch (error) { if (owns()) setTaskError(errorText(error)); }
    finally { if (owns()) setCreateBusy(false); }
  };

  const saveBrief = async () => {
    if (!job || briefBusy || !briefDraft.requirement.trim()) return;
    const submitted = { ...briefDraft };
    const owns = taskScope.current.capture();
    setBriefBusy(true);
    setTaskError('');
    try {
      const updated = await api.updateCardInspirationBrief(job.id, {
        ...submitted, requirement: submitted.requirement.trim(), expected_revision: job.brief_revision,
      });
      if (!owns()) return;
      setBriefDraft(current => { if (sameBrief(current, submitted)) briefDirty.current = false; return current; });
      setJob(updated);
      setSaveMessage('创作简报已保存。');
    } catch (error) { if (owns()) setTaskError(errorText(error)); }
    finally { if (owns()) setBriefBusy(false); }
  };

  const sendAgentTurn = async () => {
    if (!job || !agentInput.trim() || agentBusy || !job.agent_enabled) return;
    const submitted = agentInput.trim();
    const owns = taskScope.current.capture();
    setAgentBusy(true);
    setTaskError('');
    setSaveMessage('');
    try {
      const updated = await api.sendCardInspirationAgentTurn(job.id, submitted);
      if (!owns()) return;
      setJob(updated);
      setAgentInput(current => current.trim() === submitted ? '' : current);
      await refreshJobs();
    } catch (error) { if (owns()) setTaskError(errorText(error)); }
    finally { if (owns()) setAgentBusy(false); }
  };

  const applySuggestion = (suggestion: NonNullable<CardInspirationJob['agent_suggestion']>) => {
    briefDirty.current = true;
    setBriefDraft((current) => ({
      requirement: suggestion.requirement ?? current.requirement,
      detail: suggestion.detail ?? current.detail,
      borrow: suggestion.borrow ?? current.borrow,
      avoid: suggestion.avoid ?? current.avoid,
    }));
    setSaveMessage('建议已填入简报编辑框；检查并保存后才会用于下一次 Agent 对话或角色生成。');
  };

  const applyFieldSuggestion = async (draft: CardInspirationDraft, suggestion: NonNullable<CardInspirationJob['agent_field_suggestions']>[number]) => {
    if (!job || working || draft.status === 'committed') return;
    const owns = taskScope.current.capture();
    setTaskError('');
    try {
      await api.editCardInspirationDraft(job.id, draft.id, {
        expected_revision: draft.revision,
        payload: { ...draft.payload, [suggestion.field]: suggestion.value },
        aliases: draft.aliases,
        source: 'agent',
      });
      const refreshed = await api.getCardInspirationJob(job.id);
      if (!owns()) return;
      setJob(refreshed);
      setSaveMessage(`字段建议已应用到「${draft.payload.name}」草稿。请打开编辑器审阅后再确认保存。`);
    } catch (error) { if (owns()) setTaskError(errorText(error)); }
  };

  const restoreFieldHistory = async (draft: CardInspirationDraft, entry: CardInspirationDraft['field_history'][number]) => {
    if (!job || working || draft.status === 'committed' || entry.previous_value === null) return;
    const owns = taskScope.current.capture();
    setTaskError('');
    try {
      await api.editCardInspirationDraft(job.id, draft.id, {
        expected_revision: draft.revision,
        payload: { ...draft.payload, [entry.field]: entry.previous_value },
        aliases: draft.aliases,
        source: 'user',
      });
      const updated = await api.getCardInspirationJob(job.id);
      if (!owns()) return;
      setJob(updated);
      setSaveMessage(`已恢复「${entry.field}」的旧内容，并创建新修订。`);
    } catch (error) { if (owns()) setTaskError(errorText(error)); }
  };

  const saveDraft = async ({ card, aliases }: CharacterEditorPayload) => {
    if (!job || !editingDraft) return;
    const owns = taskScope.current.capture();
    setSavingDraft(true);
    setTaskError('');
    try {
      await api.editCardInspirationDraft(job.id, editingDraft.id, {
        expected_revision: editingDraft.revision, payload: card, aliases,
      });
      const refreshed = await api.getCardInspirationJob(job.id);
      if (!owns()) return;
      setJob(refreshed);
      setEditingDraft(refreshed.drafts.find((draft) => draft.id === editingDraft.id) ?? null);
      setSaveMessage('草稿已保存，角色库尚未新增角色。');
    } catch (error) { if (owns()) setTaskError(errorText(error)); throw error; }
    finally { if (owns()) setSavingDraft(false); }
  };

  const commitDraft = async ({ card, aliases }: CharacterEditorPayload) => {
    if (!job || !editingDraft) return;
    const owns = taskScope.current.capture();
    setSavingDraft(true);
    setTaskError('');
    try {
      const existing = committedCharacter.current;
      if (existing) {
        await useCharacterStore.getState().updateCharacter(existing.id, { card, aliases, expected_revision: existing.revision });
        const updated = useCharacterStore.getState().characters.find(row => row.id === existing.id);
        committedCharacter.current = { id: existing.id, revision: updated?.revision };
      } else {
        const committed = await api.commitCardInspirationDraft(job.id, editingDraft.id, { expected_revision: editingDraft.revision, payload: card, aliases });
        committedCharacter.current = { id: committed.character.id, revision: committed.character.revision };
      }
      await useCharacterStore.getState().load();
      const refreshed = await api.getCardInspirationJob(job.id);
      if (!owns()) return;
      setJob(refreshed);
      setSaveMessage(`「${card.name}」已保存到角色库。`);
      await refreshJobs();
    } catch (error) { if (owns()) setTaskError(errorText(error)); throw error; }
    finally { if (owns()) setSavingDraft(false); }
  };

  const openDraft = async (draft: CardInspirationDraft) => {
    const owns = taskScope.current.capture();
    setTaskError('');
    if (!draft.committed_character_id) {
      committedCharacter.current = null;
      setEditingDraft(draft);
      return;
    }
    setSavingDraft(true);
    try {
      const characters = await queryClient.fetchQuery({ ...resourceQueries.characters(), staleTime: 0 });
      if (!owns()) return;
      const character = characters.find(row => row.id === draft.committed_character_id);
      if (!character) throw new Error('已保存的角色不存在，请在角色库核对。');
      const snapshot = committedInspirationSnapshot(draft, character);
      committedCharacter.current = snapshot.identity;
      setEditingDraft(snapshot.draft);
    } catch (error) { if (owns()) setTaskError(errorText(error)); }
    finally { if (owns()) setSavingDraft(false); }
  };

  const deleteTask = async () => {
    if (!job || taskBusy || !window.confirm('删除这个灵感任务和草稿？已确认保存到角色库的角色不会被删除。')) return;
    const owns = taskScope.current.capture();
    try {
      await api.deleteCardInspirationJob(job.id);
      if (!owns()) return;
      taskScope.current.advance();
      setJob(null);
      setEditingDraft(null);
      setSaveMessage('灵感任务已删除。');
      await refreshJobs();
    } catch (error) { if (owns()) setTaskError(errorText(error)); }
  };

  const reviewEvidence = job?.references.map((ref) =>
    `${sourceName(ref.source_id)}｜${ref.title}｜作者：${ref.creator || '未标注'}\n${ref.source_url}\n快照 SHA-256：${ref.content_sha256}`,
  ).join('\n\n') ?? '';

  return <>
    <WorkbenchDesk layout="form-main" className="inspiration-desk" data-pane={mobilePane} data-detail-open={detailOpen}>
      <nav className="inspiration-mobile-tabs" aria-label="灵感创作面板">
        <Button variant={mobilePane === 'input' ? 'tonal' : 'ghost'} onClick={() => setMobilePane('input')}>填写</Button>
        <Button variant={mobilePane === 'result' ? 'tonal' : 'ghost'} onClick={() => setMobilePane('result')}>结果</Button>
      </nav>
      <WorkbenchColumn variant="form" className="inspiration-input">
        <div className="workbench-column__head"><h2>创作简报</h2><span className="inspiration-status">参考 {selectedRefs.length}/5</span></div>
        <div className="workbench-column__body inspiration-fields">
          <p className="inspiration-muted">参考完整卡，借鉴创作方向。生成内容先成为草稿，确认后才进入角色库。</p>
          <div className="inspiration-refs">{selectedRefs.map(ref => <Button key={hitKey(ref)} size="sm" variant="tonal" icon={X} onClick={() => toggleReference(ref)} title={`移除参考：${ref.title}`}>{ref.title}</Button>)}</div>
          {selectedRefs.length === 0 && <p className="inspiration-muted">先在「发现卡片」中选取参考，最多 5 张。</p>}
          <label className="inspiration-field"><span>创作需求 *</span><textarea rows={3} value={requirement} onChange={event => setRequirement(event.target.value)} placeholder="你想创作什么角色？" /></label>
          <label className="inspiration-field"><span>补充设定</span><textarea rows={3} value={detailBrief} onChange={event => setDetailBrief(event.target.value)} placeholder="背景、关系、口吻、世界观约束等" /></label>
          <label className="inspiration-field"><span>希望借鉴</span><textarea rows={3} value={borrow} onChange={event => setBorrow(event.target.value)} placeholder="关系张力、开场节奏、角色弧光等抽象方向" /></label>
          <label className="inspiration-field"><span>明确避开</span><textarea rows={3} value={avoid} onChange={event => setAvoid(event.target.value)} placeholder="不希望出现的设定、语气或主题" /></label>
        </div>
        <div className="workbench-column__foot inspiration-foot">
          <Button variant="primary" disabled={!requirement.trim() || selectedRefs.length === 0 || taskBusy} onClick={() => void createTask()}>{createBusy ? '正在处理…' : '冻结参考并生成 1 个候选'}</Button>
          <p>完整参考保存为任务快照；输入会保留供失败后重试。</p>
        </div>
      </WorkbenchColumn>
      <WorkbenchColumn className="inspiration-results">
        <div className="workbench-column__head">
          <Tabs value={resultTab} onValueChange={value => { setResultTab(value as 'search' | 'tasks'); setDetailOpen(false); }} className="inspiration-result-tabs">
            <TabsList aria-label="灵感结果"><TabsTrigger value="search">发现卡片</TabsTrigger><TabsTrigger value="tasks">灵感任务 <span className="inspiration-status" aria-hidden="true">{jobs.length}</span></TabsTrigger></TabsList>
          </Tabs>
          {resultTab === 'tasks' && job && <Button size="sm" variant="tonal" onClick={() => setDetailOpen(true)}>任务详情</Button>}
        </div>
        {resultTab === 'search' && <form className="workbench-column__band inspiration-search" onSubmit={event => void runSearch(event)}>
          <label className="inspiration-muted" htmlFor="inspiration-query">检索词（中文、英文或站点支持的标签）</label>
          <div className="inspiration-search-row"><TextInput id="inspiration-query" leadingIcon={Search} value={query} onChange={event => setQuery(event.target.value)} placeholder="冷淡的太空站医生，writer:..." /><Button type="submit" variant="tonal" disabled={!query.trim() || !sourceIds.length || searching || !!busyPage}>{searching ? '搜索中…' : '搜索'}</Button></div>
          <div className="inspiration-sources">{sources.map(source => <Checkbox key={source.source_id} title={source.summary} checked={sourceIds.includes(source.source_id)} onChange={() => toggleSource(source.source_id)} label={source.label} />)}</div>
          <p className="inspiration-muted">悬停站点名称查看语言和特点；站点评级随结果显示。</p>
        </form>}
        <div className="workbench-column__body">
          {sourceError && <WorkshopNotice tone="danger" actions={<Button size="sm" variant="tonal" onClick={() => { void queryClient.fetchQuery({ ...resourceQueries.cardSources(), staleTime:0 }).then(() => setSourceError('')).catch(error => setSourceError(errorText(error))); }}>重试站点目录</Button>}>无法读取站点目录：{sourceError}</WorkshopNotice>}
          {searchError && resultTab === 'search' && <WorkshopNotice tone="danger">搜索失败：{searchError}。检索词已保留，可再次搜索。</WorkshopNotice>}
          {taskError && <WorkshopNotice tone="danger">任务操作失败：{taskError}</WorkshopNotice>}
          {saveMessage && <WorkshopNotice tone="success">{saveMessage}</WorkshopNotice>}
          {working && <WorkshopNotice busy>{job ? `${statusLabel(job.status)} · ${job.stage}` : '正在冻结参考并生成草稿'} · <WorkshopWait seconds={elapsed} /></WorkshopNotice>}
          {resultTab === 'search' ? <>
            {!pages.length && !searching && <div className="inspiration-empty">搜索角色卡，预览完整内容后选作参考。内容评级由原站提供，创作时仍需自行审阅。</div>}
            {pages.map(page => <section key={page.source_id} className="inspiration-site">
              <div className="inspiration-site-head"><h3>{sourceName(page.source_id)}</h3><span className="inspiration-status">{page.status === 'ok' ? `可用 · ${page.results.length} 张${page.skipped_count ? ` · 跳过无效数据 ${page.skipped_count} 条` : ''}` : '站点不可用'}</span><span className="inspiration-muted">第 {(pageHistory[page.source_id]?.length ?? 0) + 1} 页</span>
                {(pageHistory[page.source_id]?.length ?? 0) > 0 && <Button size="sm" disabled={!!busyPage || searching} onClick={() => loadPrevious(page)}>上一页</Button>}
                {page.next_cursor && <Button size="sm" disabled={!!busyPage || searching} onClick={() => void loadNext(page)}>{busyPage === page.source_id ? '读取中…' : '下一页'}</Button>}
              </div>
              {page.error && <WorkshopNotice tone="danger">{page.status === 'ok' ? '下一页读取失败' : '站点请求失败'}：{page.error}</WorkshopNotice>}
              {page.status === 'ok' && !page.results.length && <p className="inspiration-muted">没有找到符合条件的卡片。</p>}
              <div className="inspiration-list">{page.results.map(hit => <article key={hit.card_id} className="inspiration-row" data-selected={selectedKeys.has(hitKey(hit))}>
                <div className="inspiration-row-head"><div><h4>{hit.title}</h4><p className="inspiration-muted">作者：{hit.creator || '未标注'}</p></div><span className="inspiration-status" data-tone={hit.content_rating === 'sensitive' ? 'warning' : undefined}>{contentRatingLabel(hit.content_rating)}</span></div>
                {hit.summary && <p className="inspiration-copy inspiration-summary">{hit.summary}</p>}
                <div className="inspiration-tags">{hit.tags.slice(0, 8).map(tag => <span key={tag}>{tag}</span>)}{hit.tags.length > 8 && <span>+{hit.tags.length - 8}</span>}</div>
                <div className="inspiration-actions"><Button size="sm" variant="tonal" onClick={() => void openPreview(hit)}>预览完整卡</Button><Button size="sm" disabled={!selectedKeys.has(hitKey(hit)) && selectedRefs.length >= 5} onClick={() => toggleReference(hit)}>{selectedKeys.has(hitKey(hit)) ? '移除参考' : '选作参考'}</Button><a href={hit.source_url} target="_blank" rel="noreferrer">原站 ↗</a></div>
              </article>)}</div>
            </section>)}
          </> : <div className="inspiration-list">
            {!jobs.length && !job && <div className="inspiration-empty">还没有任务。创建后可以关闭页面，再从这里恢复草稿。</div>}
            {jobs.map(row => <button key={row.id} type="button" className="inspiration-row inspiration-task-button" data-selected={job?.id === row.id} disabled={createBusy || briefBusy || agentBusy || savingDraft} onClick={() => void setCurrentJob(row.id)}><div className="inspiration-row-head"><h3>{row.requirement || row.id}</h3><span className="inspiration-status">{statusLabel(row.status)}</span></div><p className="inspiration-muted">{new Date(row.updated_at).toLocaleString('zh-CN')}</p></button>)}
            {job && <div className="inspiration-task-header"><h3>{job.requirement || '当前任务'}</h3><span className="inspiration-status">{statusLabel(job.status)}</span></div>}
            {job?.drafts.map(draft => <article className="inspiration-row" key={draft.id}>
              <div className="inspiration-row-head"><Avatar name={draft.payload.name || '?'} dense /><div><h3>{draft.payload.name || '未命名角色'}</h3><p className="inspiration-muted">修订 {draft.revision} · {draft.status === 'committed' ? '已确认保存' : '草稿'}</p></div></div>
              <p className="inspiration-copy inspiration-summary">{draft.payload.description || '暂无简介'}</p>
              {draft.aliases.length > 0 && <p className="inspiration-muted">别名：{draft.aliases.join(' / ')}</p>}
              <WorkshopDisclosure title={`一致性审阅 · ${draft.quality_review?.passed ? '规则扫描通过' : `${draft.quality_review?.issues.length ?? 0} 项待检查`}`}>
                {(draft.quality_review?.issues ?? []).map((issue,index) => <p className="inspiration-copy" key={index}>{issue}</p>)}
                {draft.quality_review?.passed && <p className="inspiration-copy">未发现明显缺漏或重复内容。</p>}<p className="inspiration-muted">{draft.quality_review?.note ?? '语义一致性仍需人工判断。'}</p>
              </WorkshopDisclosure>
              {draft.field_history?.length > 0 && <WorkshopDisclosure title={`字段历史 · ${draft.field_history.length} 条修订`}><div className="inspiration-history">{[...draft.field_history].slice(-12).reverse().map((entry,index) => <div key={`${entry.to_revision}:${entry.field}:${index}`}><p className="inspiration-muted">{fieldLabel(entry.field)} · v{entry.from_revision} → v{entry.to_revision} · {entry.source === 'agent' ? 'Agent 建议' : '用户编辑'}</p><p className="inspiration-copy">之前：{entry.previous_value_preview || '（空）'}{entry.previous_value_truncated ? '…（内容过长，不能一键恢复）' : ''}</p>{entry.previous_value !== null && draft.status !== 'committed' && <Button size="sm" onClick={() => void restoreFieldHistory(draft,entry)}>恢复此前{fieldLabel(entry.field)}</Button>}</div>)}</div></WorkshopDisclosure>}
              <Button variant="tonal" disabled={savingDraft} onClick={() => void openDraft(draft)}>编辑、试聊并审阅</Button>
            </article>)}
          </div>}
        </div>
        <div className="workbench-column__foot"><p className="inspiration-muted">{resultTab === 'search' ? `已选 ${selectedRefs.length}/5 张参考` : job ? `${job.drafts.length} 个草稿 · 已冻结参考 ${job.references.length} 张` : `${jobs.length} 个任务`}</p></div>
      </WorkbenchColumn>
      <WorkbenchColumn variant="aside" className="inspiration-aside">
        <div className="workbench-column__head"><h2>{resultTab === 'search' ? '完整卡预览' : '任务详情'}</h2><Button size="sm" variant="ghost" className="inspiration-detail-close" icon={X} onClick={() => { previewScope.current.advance(); setDetailBusy(false); setDetailOpen(false); }}>收起</Button></div>
        <div className="workbench-column__body inspiration-detail-body">
          {resultTab === 'search' ? <>
            {detailBusy && <WorkshopNotice busy>正在读取并校验完整角色卡…</WorkshopNotice>}
            {detailError && <WorkshopNotice tone="danger" actions={previewHit && <Button size="sm" variant="tonal" onClick={() => void openPreview(previewHit)}>重新读取完整卡</Button>}>完整卡读取失败，不能将摘要卡作为生成参考：{detailError}</WorkshopNotice>}
            {detail && <><div className="inspiration-row-head"><Avatar name={detail.card.name || detail.hit.title} /><div><h3>{detail.card.name || detail.hit.title}</h3><p className="inspiration-muted">{sourceName(detail.hit.source_id)} · {detail.hit.creator || '作者未标注'} · {contentRatingLabel(detail.hit.content_rating)}</p></div></div><CardFields card={detail.card} /><p className="inspiration-muted">完整卡已校验 · SHA-256 {detail.content_sha256}</p><a href={detail.hit.source_url} target="_blank" rel="noreferrer">查看原站卡片 ↗</a><Button variant="tonal" disabled={!selectedKeys.has(hitKey(detail.hit)) && selectedRefs.length >= 5} onClick={() => toggleReference(detail.hit)}>{selectedKeys.has(hitKey(detail.hit)) ? '从参考中移除' : '将此完整卡设为参考'}</Button></>}
            {!detail && !detailBusy && !detailError && <div className="inspiration-empty">从搜索结果选择「预览完整卡」，在这里阅读人物全文。</div>}
          </> : job ? <>
            <p className="inspiration-muted">任务 {job.id} · {statusLabel(job.status)} · {job.stage}</p>
            <div className="inspiration-actions">{['ready','review','failed','interrupted'].includes(job.status) && <Button variant="tonal" disabled={createBusy} onClick={() => void regenerate()}>{createBusy ? '生成中…' : '再生成 1 个候选'}</Button>}<Button variant="ghost" disabled={working || taskBusy} onClick={() => void deleteTask()}>删除任务</Button></div>
            {job.errors.map((error,index) => <WorkshopNotice tone="danger" key={index}>{error}</WorkshopNotice>)}
            <WorkshopDisclosure title="当前创作简报" defaultOpen><p className="inspiration-muted">修订 {job.brief_revision} · Agent 每次只读取你已保存的版本</p><div className="inspiration-fields">{([['requirement','创作需求 *'],['detail','补充设定'],['borrow','希望借鉴'],['avoid','明确避开']] as const).map(([field,label]) => <label className="inspiration-field" key={field}><span>{label}</span><textarea rows={2} value={briefDraft[field]} onChange={event => changeBrief(field,event.target.value)} /></label>)}</div><Button variant="tonal" disabled={briefBusy || !briefDraft.requirement.trim() || working} onClick={() => void saveBrief()}>{briefBusy ? '保存中…' : '保存简报'}</Button></WorkshopDisclosure>
            <WorkshopDisclosure title="分步构思 Agent"><p className="inspiration-muted">Agent 只能读取本任务完整卡的冻结快照，可以讨论方向和提出建议；不能自行保存简报或角色。</p>{!job.agent_enabled ? <WorkshopNotice tone="warning">Agent 入口在真实 DSH 多步 MCP 调用验证通过后启用；现在可以保存简报并使用一次生成。</WorkshopNotice> : <><div className="inspiration-agent-messages">{job.agent_messages.map((message,index) => <div className="inspiration-agent-message" key={`${message.created_at}:${index}`} data-role={message.role}><strong>{message.role === 'user' ? '你' : 'Agent'}</strong><p className="inspiration-copy">{message.content}</p>{message.role === 'assistant' && message.brief_suggestion && <Button size="sm" onClick={() => applySuggestion(message.brief_suggestion!)}>把简报建议填入编辑框</Button>}{message.role === 'assistant' && message.field_suggestions?.map((suggestion,suggestionIndex) => <div className="inspiration-row" key={`${suggestion.field}:${suggestionIndex}`}><p className="inspiration-muted">字段建议 · {fieldLabel(suggestion.field)}</p><p className="inspiration-copy">{suggestion.value}</p>{suggestion.rationale && <p className="inspiration-muted">{suggestion.rationale}</p>}{job.drafts.filter(draft => draft.status !== 'committed').map(draft => <Button size="sm" key={draft.id} onClick={() => void applyFieldSuggestion(draft,suggestion)}>应用到「{draft.payload.name}」</Button>)}{job.drafts.every(draft => draft.status === 'committed') && <p className="inspiration-muted">请先生成未保存的草稿，才能应用字段建议。</p>}</div>)}</div>)}{!job.agent_messages.length && <p className="inspiration-muted">发送问题，讨论希望借鉴和调整的创作方向。</p>}</div><label className="inspiration-field"><span>构思消息</span><textarea rows={3} value={agentInput} onChange={event => setAgentInput(event.target.value)} placeholder="描述你想讨论或调整的创作方向" /></label><Button variant="tonal" disabled={!agentInput.trim() || agentBusy || job.status === 'agent_running'} onClick={() => void sendAgentTurn()}>{agentBusy || job.status === 'agent_running' ? '思考中…' : '发送'}</Button></>}</WorkshopDisclosure>
            <WorkshopDisclosure title="已冻结的参考来源">{job.references.map(ref => <a key={`${ref.source_id}:${ref.card_id}`} href={ref.source_url} target="_blank" rel="noreferrer">{sourceName(ref.source_id)} · {ref.title}（{ref.creator || '作者未标注'}）↗</a>)}</WorkshopDisclosure>
          </> : <div className="inspiration-empty">选择一个灵感任务，查看保存的简报、参考和构思对话。</div>}
        </div>
      </WorkbenchColumn>
    </WorkbenchDesk>
    {job && editingDraft && <CharacterEditor key={`${job.id}:${editingDraft.id}`} initialCard={editingDraft.payload} initialAliases={editingDraft.aliases} draftScope={`inspiration:${job.id}:${editingDraft.id}`}
      onSave={commitDraft} onSaveSucceeded={(_asCopy,hasLaterEdits) => { if (!hasLaterEdits) setEditingDraft(null); }}
      onSaveDraft={committedCharacter.current ? undefined : saveDraft} onClose={() => setEditingDraft(null)} busy={savingDraft} hideRuntime
      saveLabel={committedCharacter.current ? '保存人物修改' : '确认并保存到角色库'} reviewEvidence={reviewEvidence} />}
  </>;
}
