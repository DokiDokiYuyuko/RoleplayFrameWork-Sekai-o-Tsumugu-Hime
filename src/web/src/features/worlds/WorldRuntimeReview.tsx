import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import type { World } from '../../types';
import { createClientId } from '../../utils/clientId';
import { worldCreationApi, type RuntimeProposal, type WorldCreationJob } from './worldCreationApi';
import './world-creation.css';

export function readableCreationMessage(value: string) {
  const text = value.replace(/^(?:ValueError|JSONDecodeError):\s*/, '');
  if (/Expecting value: line 1 column 1|整理助手没有返回内容|本批重试后仍未通过校验/.test(text)) {
    return '整理助手没有返回可读取的设定条目。原稿和已保存资料都没有改动；可以重试，或使用“粘贴设定”直接保存原文。';
  }
  return text;
}
export function creationError(cause: unknown) {
  if (!(cause instanceof Error)) return '操作失败，请重试';
  const raw = cause.message.replace(/^\d+:\s*/, '');
  try { return readableCreationMessage((JSON.parse(raw) as { detail?: string }).detail ?? raw); }
  catch { return readableCreationMessage(raw); }
}
const label = (item: RuntimeProposal) => String(item.payload?.comment || item.payload?.name || '设定条目');
const content = (item: RuntimeProposal) => item.content ?? String(item.payload?.content ?? '');
type PayloadEdit = { keys?: string; secondary_keys?: string; selective?: boolean; selective_logic?: number; enabled?: boolean; order?: string; anchor?: string; depth?: string };
type ExampleEdit = { positive_examples?: string; negative_examples?: string };
const lines = (value: unknown) => Array.isArray(value) ? value.map(String).join('\n') : typeof value === 'string' ? value : '';
const list = (value: string) => [...new Set(value.split(/\r?\n/).map(text => text.trim()).filter(Boolean))];
const editLabel = (field: string, value: unknown) => field === 'anchor' ? ({ system: '资料开头', near: '最近对话附近', at_depth: '指定对话深度' } as Record<string, string>)[String(value)] ?? String(value)
  : field === 'selective_logic' ? ['至少出现一个二级关键词', '二级关键词未全部出现', '不出现任何二级关键词', '出现全部二级关键词'][Number(value)] ?? String(value)
  : typeof value === 'boolean' ? value ? '是' : '否' : String(value);
const proposalVersion = (item: RuntimeProposal) => JSON.stringify([content(item), item.payload, item.positive_examples, item.negative_examples, item.runtime_scope, item.action, item.target_uid, item.conflict_reason, item.source_refs, item.candidate_targets]);
function editedPayload(original: Record<string, unknown>, edit: PayloadEdit = {}) {
  const payload: Record<string, unknown> = { ...original, ...edit };
  for (const field of ['keys', 'secondary_keys'] as const) if (edit[field] !== undefined) payload[field] = list(edit[field]);
  for (const field of ['order', 'depth'] as const) if (edit[field] !== undefined) {
    const value = Number(edit[field]);
    if (!edit[field].trim() || !Number.isSafeInteger(value) || (field === 'depth' && value < 0)) throw new Error(field === 'depth' ? '对话深度请填写零或正整数' : '顺序请填写整数');
    payload[field] = value;
  }
  return payload;
}

function TriggerEditor({ item, payloadEdit, exampleEdit, busy, dirty, onPayload, onExamples }: {
  item: RuntimeProposal; payloadEdit?: PayloadEdit; exampleEdit?: ExampleEdit; busy: boolean; dirty: boolean;
  onPayload: (patch: PayloadEdit) => void; onExamples: (patch: ExampleEdit) => void;
}) {
  const value = { ...item.payload, ...payloadEdit };
  const disabledCheck = item.simulation?.mode === 'disabled' || item.payload?.enabled === false;
  return <details><summary>触发与顺序设置</summary>
    <p className="world-creation-muted">一行填写一个词或一个试例。保存此项时会检查试例是否按预期触发，不调用模型。</p>
    <fieldset disabled={busy} className="min-w-0 border-0 p-0 disabled:opacity-60">
      <div className="grid gap-x-4 sm:grid-cols-2">
        <label className="world-creation-field">关键词<textarea aria-label="触发关键词" rows={3} value={lines(value.keys)} onChange={event => onPayload({ keys: event.target.value })} /></label>
        <label className="world-creation-field">二级关键词<textarea aria-label="二级关键词" rows={3} value={lines(value.secondary_keys)} onChange={event => onPayload({ secondary_keys: event.target.value })} /></label>
      </div>
      <label className="world-creation-checkbox"><input type="checkbox" checked={Boolean(value.selective)} onChange={event => onPayload({ selective: event.target.checked })} />使用二级关键词条件</label>
      <label className="world-creation-field">二级条件<select aria-label="二级关键词条件" disabled={!value.selective} value={Number(value.selective_logic ?? 0)} onChange={event => onPayload({ selective_logic: Number(event.target.value) })}>
        <option value="0">至少出现一个二级关键词</option><option value="1">二级关键词未全部出现</option><option value="2">不出现任何二级关键词</option><option value="3">出现全部二级关键词</option>
      </select></label>
      <div className="flex flex-wrap gap-x-6">
        <label className="world-creation-checkbox"><input type="checkbox" checked={value.enabled !== false} onChange={event => onPayload({ enabled: event.target.checked })} />启用条目</label>
      </div>
      <p className="world-creation-muted">需要每轮看到的事实可以放入核心。停用条目会检查试例均不触发；保存前请核对停用是否符合意图。</p>
      <div className="grid gap-x-4 sm:grid-cols-2">
        <label className="world-creation-field">顺序<input aria-label="条目顺序" type="number" step="1" value={String(value.order ?? 100)} onChange={event => onPayload({ order: event.target.value })} /></label>
        <label className="world-creation-field">放置位置<select aria-label="条目放置位置" value={String(value.anchor ?? 'system')} onChange={event => onPayload({ anchor: event.target.value })}>
          <option value="system">资料开头</option><option value="near">最近对话附近</option><option value="at_depth">指定对话深度</option>
        </select></label>
        {value.anchor === 'at_depth' && <label className="world-creation-field">从最新对话往前的深度<input aria-label="条目对话深度" type="number" min="0" step="1" value={String(value.depth ?? 4)} onChange={event => onPayload({ depth: event.target.value })} /></label>}
      </div>
      <p className="world-creation-muted">同一位置内，顺序越大越靠近输入末尾。二级条件只在启用该条件且填写二级关键词时生效。</p>
      <div className="grid gap-x-4 sm:grid-cols-2">
        <label className="world-creation-field">应该触发的试例<textarea aria-label="应该触发的试例" rows={3} value={exampleEdit?.positive_examples ?? lines(item.positive_examples)} onChange={event => onExamples({ positive_examples: event.target.value })} /></label>
        <label className="world-creation-field">不该触发的试例<textarea aria-label="不该触发的试例" rows={3} value={exampleEdit?.negative_examples ?? lines(item.negative_examples)} onChange={event => onExamples({ negative_examples: event.target.value })} /></label>
      </div>
    </fieldset>
    {item.simulation && <div aria-label="触发试例检查">
      {dirty && <p className="world-creation-muted">有未保存修改，以下检查基于上次保存内容。</p>}
      <p role="status">{disabledCheck ? (item.simulation.valid ? '停用检查通过：条目不会由试例触发' : '停用检查未通过，请核对条目设置') : item.simulation.valid ? '触发试例检查通过' : '触发试例检查未通过，请核对关键词和试例'}</p>
      {item.simulation.note && <p className="world-creation-muted">{item.simulation.note}</p>}
      {[...(item.simulation.positive ?? []).map(row => ({ ...row, expected: true })), ...(item.simulation.negative ?? []).map(row => ({ ...row, expected: false }))].map((row, index) =>
        <p key={index} className="world-creation-muted">{(disabledCheck ? !row.triggered : row.expected === row.triggered) ? '符合预期' : '需要修订'} · {disabledCheck ? '停用检查' : row.expected ? '应该触发' : '不该触发'}：{row.text} · {row.triggered ? '已触发' : '未触发'}</p>)}
    </div>}
  </details>;
}

/** Edits are local until explicitly saved. Polling never overwrites the review form. */
export function WorldRuntimeReview({ jobId, world, sourceIds, onCommitted }: {
  jobId: string; world?: World; sourceIds?: string[]; onCommitted?: (world: World) => void;
}) {
  const [job, setJob] = useState<WorldCreationJob | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState<string[]>([]);
  const [shared, setShared] = useState<string[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [payloadEdits, setPayloadEdits] = useState<Record<string, PayloadEdit>>({});
  const [exampleEdits, setExampleEdits] = useState<Record<string, ExampleEdit>>({});
  const [chosenTargets, setChosenTargets] = useState<Record<string, number>>({});
  const [mode, setMode] = useState<'raw' | 'compiled'>('compiled');
  const [committed, setCommitted] = useState<string>('');
  const initialized = useRef(new Map<string, string>());
  const initializedSources = useRef<string | null>(null);
  const editBaselines = useRef<Record<string, string>>({});
  const operation = useRef<string | null>(null);
  const requestVersion = useRef(0);
  const mutationBusy = useRef(false);
  const refresh = async () => { const version = ++requestVersion.current; const next = await worldCreationApi.get(jobId); if (version === requestVersion.current) setJob(next); return next; };
  useEffect(() => {
    let alive = true;
    const read = () => {
      if (mutationBusy.current) return;
      const version = ++requestVersion.current;
      void worldCreationApi.get(jobId).then((next) => { if (alive && version === requestVersion.current) setJob(next); }).catch((cause) => { if (alive && version === requestVersion.current) setError(creationError(cause)); });
    };
    void read();
    const timer = window.setInterval(() => void read(), 1800);
    return () => { alive = false; ++requestVersion.current; window.clearInterval(timer); };
  }, [jobId]);
  const bundle = job?.bundle;
  const proposals: RuntimeProposal[] = bundle ? [
    ...(bundle.manuscript_proposal ? [{ id: 'manuscript', content: bundle.manuscript_proposal.body, runtime_scope: bundle.manuscript_proposal.runtime_scope }] : []),
    ...(bundle.core_proposal ? [bundle.core_proposal] : []), ...bundle.entry_proposals,
  ] : [];
  useEffect(() => {
    if (!bundle) return;
    const sources = JSON.stringify(bundle.source_snapshot);
    const sourceChanged = initializedSources.current !== null && initializedSources.current !== sources;
    initializedSources.current = sources;
    if (sourceChanged) initialized.current.clear();
    const signature = proposalVersion;
    const added = proposals.filter((item) => initialized.current.get(item.id) !== signature(item));
    const ids = new Set(proposals.map((item) => item.id));
    const removed = [...initialized.current.keys()].filter((id) => !ids.has(id));
    for (const id of removed) initialized.current.delete(id);
    for (const item of added) initialized.current.set(item.id, signature(item));
    if (!added.length && !removed.length && !sourceChanged) return;
    const changed = new Set(added.map((item) => item.id));
    setAccepted((old) => [...(sourceChanged ? [] : old.filter((id) => ids.has(id) && !changed.has(id))), ...added.filter((item) => !item.conflict_reason && item.action !== 'keep').map((item) => item.id)]);
    setShared((old) => [...(sourceChanged ? [] : old.filter((id) => ids.has(id) && !changed.has(id))), ...added.filter((item) => item.runtime_scope === 'shared').map((item) => item.id)]);
    setChosenTargets((old) => Object.fromEntries(Object.entries(old).filter(([id]) => ids.has(id) && !changed.has(id))));
    operation.current = null;
  }, [jobId, bundle?.revision, proposals.length]);
  const toggle = (values: string[], id: string) => values.includes(id) ? values.filter((value) => value !== id) : [...values, id];
  const run = async (fn: () => Promise<void>) => {
    if (mutationBusy.current) return;
    mutationBusy.current = true; ++requestVersion.current; setBusy(true); setError('');
    try { await fn(); } catch (cause) { setError(creationError(cause)); }
    finally { ++requestVersion.current; mutationBusy.current = false; setBusy(false); }
  };
  const derive = () => run(async () => {
    if (unsaved) throw new Error('请先保存或撤销候选修改，再重新整理');
    initialized.current.clear(); initializedSources.current = null; setAccepted([]); setShared([]); setEdits({}); setPayloadEdits({}); setExampleEdits({}); editBaselines.current = {}; operation.current = null;
    setJob(await worldCreationApi.derive(jobId, bundle?.revision ?? job?.bundle_revision ?? 1, sourceIds));
  });
  const saveEdit = (item: RuntimeProposal) => run(async () => {
    if (!bundle) return;
    if (editBaselines.current[item.id] !== undefined && editBaselines.current[item.id] !== candidateVersion(item)) throw new Error('此项候选已在别处更新，请核对新内容；你的编辑仍保留，可复制或撤销后重新修改');
    const text = edits[item.id];
    const patch = item.id === 'core' ? { core_content: text } : item.id === 'manuscript' ? { manuscript_body: text }
      : { proposal_id: item.id, payload: { ...editedPayload(item.payload ?? {}, payloadEdits[item.id]), ...(text === undefined ? {} : { content: text }) },
        ...(exampleEdits[item.id]?.positive_examples === undefined ? {} : { positive_examples: list(exampleEdits[item.id].positive_examples!) }),
        ...(exampleEdits[item.id]?.negative_examples === undefined ? {} : { negative_examples: list(exampleEdits[item.id].negative_examples!) }) };
    setJob(await worldCreationApi.edit(jobId, { expected_bundle_revision: bundle.revision, ...patch }));
    discardEdits(item.id);
    operation.current = null;
  });
  const commit = () => run(async () => {
    if (!bundle) return;
    if (unsaved) throw new Error('请先保存或撤销候选修改，再采用整组');
    if (invalidAccepted) throw new Error('有选中词条未通过触发检查，请修正关键词或试例');
    operation.current ??= `bundle_${createClientId().replace(/-/g, '')}`;
    const result = await worldCreationApi.commit(jobId, {
      operation_id: operation.current, expected_bundle_revision: bundle.revision, review_digest: bundle.review_digest,
      expected_world_revision: world?.revision, expected_lorebook_revision: bundle.target_lorebook_revision,
      accepted_proposal_ids: accepted, shared_proposal_ids: shared.filter((id) => accepted.includes(id)), runtime_mode: mode,
    });
    setJob(result.job); setCommitted(result.world.id); onCommitted?.(result.world);
  });
  const working = ['running', 'preparing'].includes(bundle?.status ?? '') || ['generating', 'analyzing', 'regenerating'].includes(job?.status ?? '');
  const reviewReady = bundle?.status === 'review' || bundle?.status === 'failed';
  const hasEdits = (id: string) => edits[id] !== undefined || payloadEdits[id] !== undefined || exampleEdits[id] !== undefined;
  const unsaved = Object.keys(edits).length > 0 || Object.keys(payloadEdits).length > 0 || Object.keys(exampleEdits).length > 0;
  const orphanedEdits = [...new Set([...Object.keys(edits), ...Object.keys(payloadEdits), ...Object.keys(exampleEdits)])].filter(id => !proposals.some(item => item.id === id));
  const invalidAccepted = proposals.some(item => accepted.includes(item.id) && (!!item.validation_errors?.length || item.simulation?.valid === false));
  const candidateVersion = (item: RuntimeProposal) => JSON.stringify([proposalVersion(item), bundle?.source_snapshot]);
  const captureBaseline = (item: RuntimeProposal) => { editBaselines.current[item.id] ??= candidateVersion(item); };
  const staleEdit = (item: RuntimeProposal) => hasEdits(item.id) && editBaselines.current[item.id] !== candidateVersion(item);
  const discardEdits = (id: string) => {
    setEdits(old => { const next = { ...old }; delete next[id]; return next; });
    setPayloadEdits(old => { const next = { ...old }; delete next[id]; return next; });
    setExampleEdits(old => { const next = { ...old }; delete next[id]; return next; });
    delete editBaselines.current[id];
  };
  return <section className="world-creation-review" aria-label="集中核对设定">
    <header className="world-creation-heading"><div><h2>集中核对设定</h2><p>选中内容一次采用；“供故事使用”决定角色模型能否看到。未勾选的作者资料只留存。</p></div>
      <button className="v7-btn v7-btn-soft" disabled={busy} onClick={() => void run(async () => { await refresh(); })}>刷新状态</button></header>
    {error && <p className="v7-error" role="alert">{error}。草稿仍保留在任务中。</p>}
    {[...(job?.errors ?? []), ...(bundle?.errors ?? [])].map((text, index) => <p className="v7-error" key={index}>{readableCreationMessage(text)}</p>)}
    {bundle?.coverage_notes?.map((text, index) => <p role="status" key={`coverage-${index}`}>{text}</p>)}
    {!!bundle?.reading_coverage && <details><summary>查看分批阅读记录</summary><p>阅读记录只说明工具读取范围；设定是否保真仍需核对原文。</p>{Object.entries(bundle.reading_coverage).map(([batch, rows]) => <div key={batch}><strong>第 {Number(batch) + 1} 批</strong>{rows.map((row, index) => <p key={index}>{row.source_id} · 字符 {row.offset} 起，共 {row.char_count} 字 · {row.reading_verified ? `已核验读取 ${row.read_chars} 字` : '未核验实际读取'}</p>)}</div>)}</details>}
    {committed || bundle?.status === 'committed' ? <p role="status">已采用到世界。<Link to={`/worlds/${committed || job?.world_id || world?.id}`}>查看世界</Link></p> : <>
      {!bundle && !working && <button className="v7-btn v7-btn-primary" disabled={busy || !job} onClick={() => void derive()}>整理核心与世界书</button>}
      {working && <p role="status">正在从冻结原稿整理核心与词条，离开后可从整理任务继续查看。</p>}
      {!!orphanedEdits.length && <details open aria-label="保留的旧候选修改"><summary>保留的旧候选修改（{orphanedEdits.length}）</summary><p className="world-creation-muted">本任务的候选已重新生成，以下手改仍保留供复制。核对后可放弃旧编辑，继续采用新候选。</p>{orphanedEdits.map(id => <div className="world-review-proposal" key={id}>
        <textarea aria-label={`旧候选修改：${id}`} readOnly rows={6} value={[
          ...(edits[id] === undefined ? [] : [`正文：\n${edits[id]}`]),
          ...Object.entries(payloadEdits[id] ?? {}).map(([field, value]) => `${({ keys: '关键词', secondary_keys: '二级关键词', selective: '使用二级条件', selective_logic: '二级条件', enabled: '启用条目', order: '顺序', anchor: '放置位置', depth: '对话深度' } as Record<string, string>)[field]}：${editLabel(field, value)}`),
          ...(exampleEdits[id]?.positive_examples === undefined ? [] : [`应该触发的试例：\n${exampleEdits[id].positive_examples}`]),
          ...(exampleEdits[id]?.negative_examples === undefined ? [] : [`不该触发的试例：\n${exampleEdits[id].negative_examples}`]),
        ].join('\n\n')} />
        <button className="v7-btn v7-btn-soft" disabled={busy} onClick={() => discardEdits(id)}>放弃这项旧编辑</button>
      </div>)}</details>}
      {bundle && <details><summary>本次使用的资料（{bundle.source_snapshot.length}）</summary>{bundle.source_snapshot.map((source) => <div key={source.id} className="world-source-quote"><strong>{source.title || '原稿'} · {source.audience === 'author' || source.visibility === 'private' ? '作者资料' : '故事资料'}</strong><pre>{source.content}</pre></div>)}</details>}
      {reviewReady && <>
        <div className="world-creation-actions"><button className="v7-btn v7-btn-soft" disabled={busy} onClick={() => { setAccepted(proposals.filter((item) => !item.conflict_reason && item.action !== 'keep').map((item) => item.id)); operation.current = null; }}>选择全部</button>
          <button className="v7-btn v7-btn-soft" disabled={busy} onClick={() => { setAccepted([]); operation.current = null; }}>全部跳过</button>
          <button className="v7-btn v7-btn-soft" disabled={busy || !accepted.length} onClick={() => { setShared((old) => [...new Set([...old, ...accepted])]); operation.current = null; }}>选中内容供故事使用</button></div>
        {proposals.map((item) => <article key={item.id} className="world-review-proposal">
          <header><label><input type="checkbox" disabled={busy || !!item.conflict_reason} checked={accepted.includes(item.id)} onChange={() => { setAccepted((old) => toggle(old, item.id)); operation.current = null; }} />{item.id === 'manuscript' ? '完整原稿' : item.id === 'core' ? '每轮使用的核心' : label(item)}</label>
            <label><input type="checkbox" disabled={busy} checked={shared.includes(item.id)} onChange={() => { setShared((old) => toggle(old, item.id)); operation.current = null; }} />供故事使用</label></header>
          {item.action && <small>{({ add: '新增', replace: '更新原条目', disable: '来源删除，建议停用', keep: '保留手工修改' })[item.action]}{item.target_uid !== undefined ? ` · 原条目 ${item.target_uid}` : ''}</small>}
          {item.conflict_reason && <div className="world-creation-warning"><p>{item.conflict_reason}。默认保留现有内容。</p>
            {!!item.candidate_targets?.length && <label className="world-creation-field">选择要更新的现有条目<select aria-label={`更新目标：${label(item)}`} value={chosenTargets[item.id] ?? ''} onChange={(e) => setChosenTargets((old) => ({ ...old, [item.id]: Number(e.target.value) }))}><option value="" disabled>先核对原文，再选择目标</option>{item.candidate_targets.map((target) => <option key={target.uid} value={target.uid} disabled={target.reserved}>{target.content.slice(0, 90)}{target.reserved ? '（已被另一候选选用）' : ''}</option>)}</select></label>}
            {!!item.candidate_targets?.length && <details><summary>比较可更新的条目原文</summary>{item.candidate_targets.map(target => <div className="world-source-quote" key={target.uid}><strong>条目 {target.uid}{target.reserved ? ' · 已被另一候选选用' : ' · 可选'}</strong><p>关键词：{target.keys?.join('、') || '未填写'}</p><pre>{target.content}</pre></div>)}</details>}
            <button className="v7-btn v7-btn-soft" disabled={busy || unsaved || (!!item.candidate_targets?.length && chosenTargets[item.id] === undefined)} onClick={() => void run(async () => { if (!bundle || unsaved) return; setJob(await worldCreationApi.edit(jobId, { expected_bundle_revision: bundle.revision, proposal_id: item.id, action: item.proposed_action ?? 'replace', resolve_conflict: true, target_uid: chosenTargets[item.id] })); setAccepted((old) => [...new Set([...old, item.id])]); operation.current = null; })}>{item.proposed_action === 'disable' ? '确认停用此条目' : item.candidate_targets?.length ? '确认更新选定条目' : '确认用候选替换手修'}</button></div>}
          {item.original_content !== undefined && <details><summary>查看现有内容，对照本次修订</summary><pre>{item.original_content}</pre></details>}
          {item.validation_errors?.map((text, i) => <p className="v7-error" key={i}>{text}</p>)}
          <textarea aria-label={item.id === 'core' ? '待审核心' : item.id === 'manuscript' ? '待审原稿' : `待审条目：${label(item)}`} rows={item.id === 'manuscript' ? 6 : 4} disabled={busy}
            value={edits[item.id] ?? content(item)} onChange={(event) => { captureBaseline(item); setEdits((old) => ({ ...old, [item.id]: event.target.value })); }} />
          {item.payload && <TriggerEditor item={item} payloadEdit={payloadEdits[item.id]} exampleEdit={exampleEdits[item.id]} busy={busy} dirty={hasEdits(item.id)}
            onPayload={patch => { captureBaseline(item); setPayloadEdits(old => ({ ...old, [item.id]: { ...old[item.id], ...patch } })); }}
            onExamples={patch => { captureBaseline(item); setExampleEdits(old => ({ ...old, [item.id]: { ...old[item.id], ...patch } })); }} />}
          {staleEdit(item) && <p className="world-creation-warning" role="alert">此项候选或来源已在别处更新；你的编辑仍保留。请复制需要的内容，或撤销后重新核对。</p>}
          {hasEdits(item.id) && <div className="world-creation-actions"><button className="v7-btn v7-btn-soft" disabled={busy || staleEdit(item)} onClick={() => void saveEdit(item)}>保存此项修改</button>
            <button className="v7-btn v7-btn-soft" disabled={busy} onClick={() => discardEdits(item.id)}>撤销本次编辑</button></div>}
          {!!item.source_refs?.length && <details><summary>查看来源依据</summary>{item.source_refs.map((ref, index) => <blockquote key={index}>{ref.quote || ref.source_id}</blockquote>)}</details>}
        </article>)}
        <label className="world-creation-field">新故事使用方式<select value={mode} disabled={busy} onChange={(e) => { setMode(e.target.value as 'raw' | 'compiled'); operation.current = null; }}><option value="compiled">核心与词条（不重复发送同源原稿）</option><option value="raw">直接使用选定故事资料原文</option></select></label>
        <p className="world-creation-muted">仅修改这份素材，进行中的故事与其他路线保留各自的资料。每批最多 20 个词条，未完成的部分可继续整理。</p>
        {unsaved && <p role="status">请先保存或撤销上面的修改，再采用整组。</p>}
        {invalidAccepted && <p role="status">有选中词条未通过触发检查，修正关键词或试例后再采用。</p>}
        <div className="world-creation-actions"><button className="v7-btn v7-btn-primary" disabled={busy || unsaved || invalidAccepted || !accepted.length} onClick={() => void commit()}>{busy ? '处理中…' : `一次采用 ${accepted.length} 项`}</button>
          {bundle?.child_job_id && bundle.completed_batches !== bundle.batch_total && <button className="v7-btn v7-btn-soft" disabled={busy || unsaved} onClick={() => void run(async () => { await worldCreationApi.continue(jobId); await refresh(); })}>继续整理未完成部分</button>}</div>
      </>}
      {bundle?.status === 'failed' && <button className="v7-btn v7-btn-soft" disabled={busy || unsaved} onClick={() => void derive()}>重试整理</button>}
    </>}
  </section>;
}
