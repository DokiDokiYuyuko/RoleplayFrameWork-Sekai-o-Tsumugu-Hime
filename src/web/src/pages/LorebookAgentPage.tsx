import { useQuery } from '@tanstack/react-query';
import { resourceQueries } from '../features/resources/resourceQueries';
import { queryClient } from '../queryClient';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Checkbox, EmptyState, Field, IconButton, Select, Tag, Textarea, TextInput, WritingPanel } from '../design-system';
import { WorkbenchColumn, WorkbenchDesk } from '../design-system/Workbench';
import { Check, ChevronRight, LibraryBig, RefreshCw, Save, Search, X } from '../design-system/Icon';
import { WorkshopDisclosure, WorkshopNotice, WorkshopPaneSwitch, WorkshopWait, useElapsedSeconds } from './WorkshopUi';
import { lorebookAgentStatusLabel } from '../utils/workshopPolicy';
import { createLorebookJobOwner, type LorebookJobToken } from '../features/creation/lorebookJobOwner';
import { creationClient as api } from '../features/creation/creationClient';
import type { ArchiveRecord, LorebookAgentJob, LorebookAgentJobSummary } from '../types';

const ACTIVE = new Set(['queued', 'running', 'regenerating']);
const splitLines = (value: string) => value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);

export default function LorebookAgentPage() {
  const { data: worlds = [] } = useQuery(resourceQueries.worlds());
  const [worldId, setWorldId] = useState('');
  const worldQuery = useQuery({ ...resourceQueries.world(worldId), enabled: !!worldId });
  const world = worldQuery.data ?? null;
  const { data: books = [] } = useQuery(resourceQueries.lorebooks());
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const [includeCore, setIncludeCore] = useState(false);
  const [targetBook, setTargetBook] = useState('new');
  const [newBookName, setNewBookName] = useState('');
  const [goal, setGoal] = useState('');
  const { data: jobSummaries = [] } = useQuery(resourceQueries.lorebookJobs());
  const setJobSummaries = (value: LorebookAgentJobSummary[] | ((previous: LorebookAgentJobSummary[]) => LorebookAgentJobSummary[])) => queryClient.setQueryData(resourceQueries.lorebookJobs().queryKey, value);
  const [job, setJob] = useState<LorebookAgentJob | null>(null);
  const [sources, setSources] = useState<Awaited<ReturnType<typeof api.getLorebookAgentSources>> | null>(null);
  const [selectedDraftId, setSelectedDraftId] = useState('');
  const [keysText, setKeysText] = useState('');
  const [secondaryText, setSecondaryText] = useState('');
  const [content, setContent] = useState('');
  const [comment, setComment] = useState('');
  const [positiveText, setPositiveText] = useState('');
  const [negativeText, setNegativeText] = useState('');
  const [rationale, setRationale] = useState('');
  const [anchor, setAnchor] = useState<'system' | 'at_depth' | 'near'>('system');
  const [depth, setDepth] = useState(4);
  const [order, setOrder] = useState(100);
  const [probability, setProbability] = useState(100);
  const [acceptSourceChanges, setAcceptSourceChanges] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [pane, setPane] = useState<'form' | 'results'>('form');
  const elapsedSeconds = useElapsedSeconds(Boolean(job && ACTIVE.has(job.status)), job?.created_at);
  const jobOwner = useRef(createLorebookJobOwner());
  const pollTimer = useRef<number | null>(null);
  const requestEpoch = jobOwner.current.capture().epoch;

  const sourceRecords = useMemo(() => world?.archive_records ?? [], [world]);
  const linkedBooks = useMemo(() => books.filter((book) => world?.lorebook_ids.includes(book.id)), [books, world]);
  const draft = job?.drafts.find((item) => item.id === selectedDraftId) ?? job?.drafts[0] ?? null;
  const sourceMap = useMemo(() => new Map((sources?.sources ?? []).map((source) => [source.id, source])), [sources]);
  const sourceChanged = job?.source_changed ?? sources?.source_changed ?? false;

  const stopPolling = () => {
    if (pollTimer.current !== null) window.clearInterval(pollTimer.current);
    pollTimer.current = null;
  };
  const beginOperation = (id: string | null) => { stopPolling(); return jobOwner.current.begin(id); };
  const installJob = (next: LorebookAgentJob, token: LorebookJobToken) => {
    if (!jobOwner.current.isCurrent(token, next.id)) return false;
    setJob(next); setJobSummaries((rows) => [
      { id: next.id, world_id: next.world_id, world_title: next.world_title, status: next.status, created_at: next.created_at, updated_at: next.updated_at },
      ...rows.filter((row) => row.id !== next.id),
    ]);
    return true;
  };
  const refreshJob = async (id: string, token: LorebookJobToken) => {
    const next = await api.getLorebookAgentJob(id);
    if (!installJob(next, token)) return null;
    return next;
  };

  useEffect(() => {
    let live = true;
    const token = beginOperation(null);
    void Promise.all([queryClient.fetchQuery(resourceQueries.worlds()), queryClient.fetchQuery(resourceQueries.lorebooks()), queryClient.fetchQuery(resourceQueries.lorebookJobs())])
      .then(([worldRows, _bookRows, jobs]) => {
        if (!live || !jobOwner.current.isCurrent(token)) return;
        const preferred = worldRows.find((row) => !row.archived)?.id ?? '';
        const savedJobId = localStorage.getItem('mrp.lorebookAgentJob') ?? jobs[0]?.id;
        if (savedJobId) {
          const restoreToken = jobOwner.current.bind(token, savedJobId);
          if (!restoreToken) return;
          void refreshJob(savedJobId, restoreToken).then((loaded) => {
          if (!live || !loaded || !jobOwner.current.isCurrent(restoreToken, loaded.id)) return;
          setWorldId(loaded.world_id);
          setPane('results');
          void api.getLorebookAgentSources(loaded.id).then((value) => { if (live && jobOwner.current.isCurrent(restoreToken, loaded.id)) setSources(value); }).catch(() => undefined);
          setSelectedDraftId(loaded.drafts[0]?.id ?? '');
        }).catch(() => { if (live && jobOwner.current.isCurrent(restoreToken)) { localStorage.removeItem('mrp.lorebookAgentJob'); setWorldId(preferred); } });
        }
        else setWorldId(preferred);
      })
      .catch((reason) => { if (live && jobOwner.current.isCurrent(token)) setError(reason instanceof Error ? reason.message : '加载世界或世界书失败'); });
    return () => { live = false; stopPolling(); jobOwner.current.invalidate(); };
  }, []);

  useEffect(() => {
    if (!world) return;
    setSelectedSources(world.archive_records.map(record => record.id)); setIncludeCore(false); setTargetBook('new'); setNewBookName(`${world.title} · 世界书`);
  }, [worldId, world?.id]);
  useEffect(() => { if (worldQuery.error) setError(worldQuery.error.message); }, [worldQuery.error]);

  useEffect(() => {
    if (!draft) return;
    setSelectedDraftId(draft.id);
    setKeysText((draft.payload.keys ?? []).join('\n'));
    setSecondaryText((draft.payload.secondary_keys ?? []).join('\n'));
    setContent(draft.payload.content ?? ''); setComment(draft.payload.comment ?? '');
    setPositiveText(draft.positive_examples.join('\n')); setNegativeText(draft.negative_examples.join('\n'));
    setRationale(draft.rationale); setAnchor(draft.payload.anchor ?? 'system');
    setDepth(draft.payload.depth ?? 4); setOrder(draft.payload.order ?? 100);
    setProbability(draft.payload.probability ?? 100);
  // Reset only when selecting a new draft/revision; polling does not overwrite active typing.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft?.id, draft?.revision]);

  useEffect(() => {
    if (!job?.id || !ACTIVE.has(job.status) || loading) return;
    const token = jobOwner.current.capture();
    if (!jobOwner.current.isCurrent(token, job.id)) return;
    let running = false;
    const timer = window.setInterval(() => {
      if (running || !jobOwner.current.isCurrent(token, job.id)) return;
      running = true;
      void refreshJob(job.id, token).then((next) => {
        if (!next || !jobOwner.current.isCurrent(token, next.id)) return;
        if (next.status === 'review' && next.drafts.length && !sources) {
          void api.getLorebookAgentSources(next.id).then(value => { if (jobOwner.current.isCurrent(token, next.id)) setSources(value); }).catch(() => undefined);
          setSelectedDraftId(next.drafts[0].id);
        }
      }).catch(() => undefined).finally(() => { running = false; });
    }, 1800);
    pollTimer.current = timer;
    return () => { window.clearInterval(timer); if (pollTimer.current === timer) pollTimer.current = null; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, job?.status, loading, requestEpoch]);

  const chooseJob = async (id: string) => {
    let token = beginOperation(id);
    let loadedSelection = false;
    setSources(null);
    setPane('results');
    setError(''); setNotice(''); setLoading(true);
    try {
      const loaded = await refreshJob(id, token); if (!loaded) return; loadedSelection = true; setWorldId(loaded.world_id);
      const material = await api.getLorebookAgentSources(id); if (!jobOwner.current.isCurrent(token, id)) return; setSources(material);
      setSelectedDraftId(loaded.drafts[0]?.id ?? ''); localStorage.setItem('mrp.lorebookAgentJob', id);
    } catch (reason) {
      if (jobOwner.current.isCurrent(token, id)) {
        setError(reason instanceof Error ? reason.message : '打开任务失败');
        // A failed switch leaves the previous task visible; restart its polling in the new epoch.
        if (!loadedSelection && job) { const previousToken = jobOwner.current.bind(token, job.id); if (previousToken) { token = previousToken; setSources(sources); } }
      }
    }
    finally { if (jobOwner.current.isCurrent(token)) setLoading(false); }
  };

  const create = async () => {
    if (!world || loading) return;
    let token = beginOperation(null);
    setSources(null);
    setLoading(true); setError(''); setNotice('');
    setPane('results');
    try {
      const created = await api.createLorebookAgentJob({
        world_id: world.id, source_ids: selectedSources, include_core_brief: includeCore,
        target_lorebook_id: targetBook === 'new' ? null : targetBook,
        new_lorebook_name: newBookName, goal,
      });
      const createdToken = jobOwner.current.bind(token, created.id); if (!createdToken) return; token = createdToken;
      if (!installJob(created, token)) return; setSelectedDraftId('');
      const material = await api.getLorebookAgentSources(created.id); if (!jobOwner.current.isCurrent(token, created.id)) return; setSources(material);
      localStorage.setItem('mrp.lorebookAgentJob', created.id);
      setNotice('任务已排入 DSH Agent；正式世界书尚未变更。');
    } catch (reason) { if (jobOwner.current.isCurrent(token)) setError(reason instanceof Error ? reason.message : '无法创建 Agent 任务'); }
    finally { if (jobOwner.current.isCurrent(token)) setLoading(false); }
  };

  const saveDraft = async () => {
    if (!job || !draft || loading) return;
    const token = beginOperation(job.id);
    setLoading(true); setError(''); setNotice('');
    try {
      const payload = {
        ...draft.payload, uid: undefined, keys: splitLines(keysText), secondary_keys: splitLines(secondaryText),
        content, comment, enabled: true, constant: false, selective: splitLines(secondaryText).length > 0,
        selective_logic: 0 as const, anchor, depth, order, probability, extensions: draft.payload.extensions ?? {},
      };
      await api.editLorebookAgentDraft(job.id, draft.id, {
        expected_revision: draft.revision, payload,
        source_refs: draft.source_refs, positive_examples: splitLines(positiveText),
        negative_examples: splitLines(negativeText), rationale,
        risk_notes: draft.risk_notes,
      });
      if (!jobOwner.current.isCurrent(token, job.id)) return;
      const next = await refreshJob(job.id, token); if (!next) return; setNotice('草稿已保存并完成结构与来源校验。');
      setSelectedDraftId(draft.id);
      if (next.drafts.find((row) => row.id === draft.id)?.simulation?.valid) setNotice('草稿已保存；正反例触发模拟通过。');
    } catch (reason) { if (jobOwner.current.isCurrent(token, job.id)) setError(reason instanceof Error ? reason.message : '保存草稿失败'); }
    finally { if (jobOwner.current.isCurrent(token, job.id)) setLoading(false); }
  };

  const simulate = async () => {
    if (!job || !draft || loading) return;
    const token = beginOperation(job.id);
    setLoading(true); setError('');
    try {
      const payload = {
        ...draft.payload, keys: splitLines(keysText), secondary_keys: splitLines(secondaryText),
        content, comment, enabled: true, constant: false, selective: splitLines(secondaryText).length > 0,
        selective_logic: 0 as const, anchor, depth, order, probability, extensions: draft.payload.extensions ?? {},
      };
      await api.editLorebookAgentDraft(job.id, draft.id, {
        expected_revision: draft.revision, payload, source_refs: draft.source_refs,
        positive_examples: splitLines(positiveText), negative_examples: splitLines(negativeText),
        rationale, risk_notes: draft.risk_notes,
      });
      if (!jobOwner.current.isCurrent(token, job.id)) return;
      await api.simulateLorebookAgentDraft(job.id, draft.id);
      if (!jobOwner.current.isCurrent(token, job.id)) return;
      const next = await refreshJob(job.id, token); if (!next) return;
      if (next.drafts.find((row) => row.id === draft.id)?.simulation?.valid) setNotice('保存完成；正反例触发模拟通过。');
    }
    catch (reason) { if (jobOwner.current.isCurrent(token, job.id)) setError(reason instanceof Error ? reason.message : '触发模拟失败'); }
    finally { if (jobOwner.current.isCurrent(token, job.id)) setLoading(false); }
  };

  const commit = async () => {
    if (!job || !draft || loading) return;
    const token = beginOperation(job.id);
    setLoading(true); setError(''); setNotice('');
    try {
      const result = await api.commitLorebookAgentDraft(job.id, draft.id, {
        expected_revision: draft.revision, accept_source_changes: acceptSourceChanges,
      });
      if (!jobOwner.current.isCurrent(token, job.id)) return;
      const [next] = await Promise.all([api.getLorebookAgentJob(job.id), queryClient.fetchQuery({ ...resourceQueries.lorebooks(), staleTime: 0 })]);
      if (!installJob(next, token)) return; setNotice(`已将条目写入《${result.book.name}》，可以继续逐条审核。`);
    } catch (reason) { if (jobOwner.current.isCurrent(token, job.id)) setError(reason instanceof Error ? reason.message : '写入世界书失败'); }
    finally { if (jobOwner.current.isCurrent(token, job.id)) setLoading(false); }
  };

  const cancel = async () => {
    if (!job || loading) return;
    const token = beginOperation(job.id);
    setLoading(true);
    try { installJob(await api.cancelLorebookAgentJob(job.id), token); }
    catch (reason) { if (jobOwner.current.isCurrent(token, job.id)) setError(reason instanceof Error ? reason.message : '取消任务失败'); }
    finally { if (jobOwner.current.isCurrent(token, job.id)) setLoading(false); }
  };

  const resume = async () => {
    if (!job || loading) return;
    const token = beginOperation(job.id);
    setLoading(true); setError('');
    try { installJob(await api.resumeLorebookAgentJob(job.id), token); }
    catch (reason) { if (jobOwner.current.isCurrent(token, job.id)) setError(reason instanceof Error ? reason.message : '恢复任务失败'); }
    finally { if (jobOwner.current.isCurrent(token, job.id)) setLoading(false); }
  };

  const regenerate = async () => {
    if (!job || !draft || loading) return;
    const token = beginOperation(job.id);
    setLoading(true); setError('');
    try { installJob(await api.regenerateLorebookAgentDraft(job.id, draft.id), token); }
    catch (reason) { if (jobOwner.current.isCurrent(token, job.id)) setError(reason instanceof Error ? reason.message : '重新生成失败'); }
    finally { if (jobOwner.current.isCurrent(token, job.id)) setLoading(false); }
  };

  const selectSource = (id: string, on: boolean) => setSelectedSources((current) =>
    on ? [...current, id] : current.filter((sourceId) => sourceId !== id));

  const active = Boolean(job && ACTIVE.has(job.status));
  const readOnly = loading || draft?.status === 'committed';
  return <WorkbenchDesk layout="form-main" className="workshop-desk" data-pane={pane}>
    <WorkshopPaneSwitch value={pane} onValueChange={setPane} />
    <WorkbenchColumn variant="form" className="workshop-form">
      <div className="workbench-column__head"><h2 className="workshop-column-title">从世界资料提炼世界书</h2></div>
      <div className="workbench-column__body">
        <Field label="目标世界"><Select value={worldId} placeholder="选择一个世界…" disabled={loading || active} onValueChange={setWorldId} options={worlds.map(item => ({ value: item.id, label: item.title }))} /></Field>
        <Field label={<>选择来源 <span className="workshop-hint">已选 {selectedSources.length + Number(includeCore)} 项</span></>} group hint="默认选中当前世界的资料，可取消；作者资料仅供整理，采用时另行决定故事用途。">
          {world ? <div className="workshop-checklist">
            {world.core_brief.trim() && <Checkbox checked={includeCore} disabled={loading || active} onChange={event => setIncludeCore(event.target.checked)} label={<span className="workshop-source-label"><strong>从核心补充提炼</strong><small>核心始终作为约束与去重参考；仅勾选时允许从中提炼词条 · {world.core_brief.length.toLocaleString()} 字</small></span>} />}
            {sourceRecords.map(record => <ArchiveChoice key={record.id} record={record} disabled={loading || active} checked={selectedSources.includes(record.id)} onChange={checked => selectSource(record.id, checked)} />)}
            {!world.core_brief.trim() && sourceRecords.length === 0 && <p className="workshop-hint">这个世界还没有原稿或档案，可先在世界页保存原稿。</p>}
          </div> : <p className="workshop-hint">选择世界后查看可提炼的资料。</p>}
        </Field>
        <Field label="写入位置"><Select value={targetBook} disabled={loading || active} onValueChange={setTargetBook} options={[{ value: 'new', label: '新建世界书' }, ...linkedBooks.map(book => ({ value: book.id, label: `追加到《${book.name}》` }))]} /></Field>
        {targetBook === 'new' && <Field label="新世界书名称"><TextInput value={newBookName} disabled={loading || active} onChange={event => setNewBookName(event.target.value)} maxLength={200} /></Field>}
        <Field label="提炼目标" optional className="workshop-grow"><WritingPanel showCount disabled={loading || active} value={goal} onChange={event => setGoal(event.target.value)} maxLength={4000} placeholder="例如：提炼魔法体系的触发式条目，保留施法条件、代价和限制。" /></Field>
      </div>
      <div className="workbench-column__foot workshop-foot"><Button variant="primary" skin="primary" size="lg" icon={ChevronRight} loading={loading || active} onClick={() => void create()} disabled={!world || loading || active || ((!includeCore || !world.core_brief.trim()) && selectedSources.length === 0)}>开始提炼</Button><p className="workshop-hint">Agent 只能读快照和运行模拟，不能修改正式资产。</p></div>
    </WorkbenchColumn>
    <WorkbenchColumn variant="main" className="workshop-results">
      <div className="workbench-column__head"><h2 className="workshop-column-title">任务与审阅</h2>{job && <Tag dot tone={job.status === 'committed' ? 'success' : ['failed', 'interrupted'].includes(job.status) ? 'danger' : 'accent'}>{lorebookAgentStatusLabel(job.status)}</Tag>}</div>
      <div className="workbench-column__band"><p className="workshop-hint">模型、网关和供应商沿用全局模型设置</p>{jobSummaries.length > 0 && <Field label="最近任务"><Select value={job?.id ?? ''} disabled={loading} placeholder="选择历史任务…" onValueChange={id => void chooseJob(id)} options={jobSummaries.slice(0, 12).map(row => ({ value: row.id, label: `${row.world_title} · ${lorebookAgentStatusLabel(row.status)} · ${new Date(row.updated_at).toLocaleString()}` }))} /></Field>}</div>
      <div className="workbench-column__body">
        {error && <WorkshopNotice tone="danger"><p>{error}</p><p>你的输入已保留，可修正后重试。</p></WorkshopNotice>}
        {notice && <WorkshopNotice tone="success">{notice}</WorkshopNotice>}
        {!job && <div className="workshop-empty"><EmptyState icon={LibraryBig} title="还没有活动任务" description="从左侧选择世界与来源后，Agent 会在这里展示进度和可编辑草稿；保存前逐条审核。" /></div>}
        {job && active && <WorkshopNotice busy actions={<IconButton label="取消任务" icon={X} disabled={loading} onClick={() => void cancel()} />}><p><strong>{job.stage}</strong> · <WorkshopWait seconds={elapsedSeconds} /></p><p>第 {job.progress.attempt || 0} 次校验 · 工具调用 {job.progress.tool_calls} 次{job.progress.last_tool ? ` · 最近：${job.progress.last_tool}` : ''}</p><p>已选档案会分段完整读取，并按主题拆成多条；正式世界书只会在你确认写入后修改。</p></WorkshopNotice>}
        {job && job.errors.length > 0 && <WorkshopNotice tone="danger" actions={['failed', 'interrupted', 'cancelled'].includes(job.status) ? <Button size="sm" disabled={loading} onClick={() => void resume()}>恢复任务</Button> : undefined}>{job.errors.join('；')}</WorkshopNotice>}
        {job && ['failed', 'interrupted', 'cancelled'].includes(job.status) && job.errors.length === 0 && <WorkshopNotice actions={<Button disabled={loading} onClick={() => void resume()}>恢复任务</Button>}><p>{lorebookAgentStatusLabel(job.status)}，输入和任务来源仍保留。</p></WorkshopNotice>}
        {job && ['review', 'committed'].includes(job.status) && job.drafts.length > 0 && <>
          {sourceChanged && <WorkshopNotice tone="warning">任务期间选中的世界资料有更新。请先查看冻结来源，确认仍适用后才能保存。</WorkshopNotice>}
          <div className="workshop-draft-tabs" aria-label="候选条目">{job.drafts.map((item, index) => <Button key={item.id} variant={draft?.id === item.id ? 'tonal' : 'secondary'} aria-pressed={draft?.id === item.id} onClick={() => setSelectedDraftId(item.id)}>{String(index + 1).padStart(2, '0')} · {item.payload.comment || item.payload.keys?.[0] || `候选条目 ${index + 1}`}{item.status === 'committed' && <Check className="ui-icon ui-icon--sm" />}</Button>)}</div>
          {draft && <div className="workshop-draft-editor" key={draft.id}>
            <Field label="触发关键词" hint="每行一个，尽量具体"><Textarea rows={2} disabled={readOnly} value={keysText} onChange={event => setKeysText(event.target.value)} /></Field>
            <Field label="注入正文"><WritingPanel rows={8} showCount disabled={readOnly} value={content} onChange={event => setContent(event.target.value)} /></Field>
            <Field label="注入位置"><Select value={anchor} disabled={readOnly} onValueChange={value => setAnchor(value as typeof anchor)} options={[{ value: 'system', label: '世界设定区' }, { value: 'at_depth', label: '对话深度' }, { value: 'near', label: '最新消息附近' }]} /></Field>
            <div className="workshop-numbers"><Field label="注入深度"><TextInput type="number" min={0} max={100} disabled={readOnly} value={depth} onChange={event => setDepth(Number(event.target.value))} /></Field><Field label="注入权重"><TextInput type="number" min={0} max={1000} disabled={readOnly} value={order} onChange={event => setOrder(Number(event.target.value))} /></Field><Field label="触发概率"><TextInput type="number" min={0} max={100} disabled={readOnly} value={probability} onChange={event => setProbability(Number(event.target.value))} /></Field></div>
            <Field label="次级关键词" hint="每行一个，全部作为辅助筛选词"><Textarea rows={2} disabled={readOnly} value={secondaryText} onChange={event => setSecondaryText(event.target.value)} /></Field>
            <div className="workshop-pair"><Field label="触发正例"><Textarea rows={3} disabled={readOnly} value={positiveText} onChange={event => setPositiveText(event.target.value)} /></Field><Field label="不应触发的反例"><Textarea rows={3} disabled={readOnly} value={negativeText} onChange={event => setNegativeText(event.target.value)} /></Field></div>
            <Field label="提炼理由 / 注释"><Textarea rows={2} disabled={readOnly} value={rationale || comment} onChange={event => { setRationale(event.target.value); setComment(event.target.value); }} /></Field>
            <WorkshopDisclosure title="查看来源引用与冻结原文">
              {draft.source_refs.map((ref, index) => <blockquote className="workshop-source-quote" key={`${ref.source_id}-${index}`}><strong>{sourceMap.get(ref.source_id)?.title ?? ref.source_id}</strong>“{ref.quote}”</blockquote>)}
              {sources?.sources.map(source => <WorkshopDisclosure key={source.id} title={`${source.title} · ${source.char_count.toLocaleString()} 字`}><pre className="workshop-source-text">{source.content}</pre></WorkshopDisclosure>)}
            </WorkshopDisclosure>
            {draft.simulation && <WorkshopNotice tone={draft.simulation.valid ? 'success' : 'danger'}><p><strong>{draft.simulation.valid ? '正反例模拟通过' : '正反例模拟未通过'}</strong></p><p>正文约 {draft.simulation.estimated_tokens} tokens · 正例命中 {draft.simulation.positive.filter(row => row.triggered).length}/{draft.simulation.positive.length} · 反例误触发 {draft.simulation.negative.filter(row => row.triggered).length}</p></WorkshopNotice>}
            {draft.validation_errors?.map(item => <WorkshopNotice tone="danger" key={item}>{item}</WorkshopNotice>)}
            {sourceChanged && <Checkbox label="我已查看冻结来源，仍按它保存" checked={acceptSourceChanges} disabled={readOnly} onChange={event => setAcceptSourceChanges(event.target.checked)} />}
          </div>}
        </>}
        {job?.status === 'review' && job.drafts.length === 0 && <div className="workshop-empty"><EmptyState title="任务完成但没有候选条目" description="查看任务错误，或恢复任务重试。" /></div>}
      </div>
      <div className="workbench-column__foot workshop-foot">
        {draft && job && ['review', 'committed'].includes(job.status) ? <>
          <Button size="sm" variant="ghost" icon={RefreshCw} disabled={readOnly} onClick={() => void regenerate()}>重新生成</Button>
          <Button size="sm" icon={Save} disabled={readOnly} onClick={() => void saveDraft()}>保存草稿</Button>
          <Button size="sm" icon={Search} disabled={readOnly} onClick={() => void simulate()}>保存并模拟</Button>
          <Button variant="tonal" icon={draft.status === 'committed' ? Check : ChevronRight} disabled={readOnly || (sourceChanged && !acceptSourceChanges) || !draft.simulation?.valid} onClick={() => void commit()}>{draft.status === 'committed' ? '已写入' : '确认写入世界书'}</Button>
        </> : <p className="workshop-hint">Agent 草稿默认不启用；每条写入都使用当前世界书修订校验。取消任务会关闭该任务专属的 DSH 与 MCP 子进程。</p>}
      </div>
    </WorkbenchColumn>
  </WorkbenchDesk>;
}

function ArchiveChoice({ record, checked, disabled, onChange }: { record: ArchiveRecord; checked: boolean; disabled: boolean; onChange: (value: boolean) => void }) {
  const kind = record.kind === 'biology' ? '生物设定' : '背景设定';
  const length = record.body.length + record.summary.length;
  return <Checkbox disabled={disabled} checked={checked} onChange={event => onChange(event.target.checked)} label={<span className="workshop-source-label"><strong>{record.title}</strong><small>{record.visibility === 'private' ? '作者资料' : '故事资料'} · {kind}{record.subtype ? ` · ${record.subtype}` : ''} · 修订 {record.revision} · {length.toLocaleString()} 字</small></span>} />;
}
