import { resourceQueries } from '../resources/resourceQueries';
import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { worldsClient as api } from './worldsClient';
import type { World } from '../../types';
import { worldCreationApi, type WorldPreview } from './worldCreationApi';
import { creationError, WorldRuntimeReview } from './WorldRuntimeReview';
import './world-creation.css';

const draftKey = (id: string) => `mrp.world.manuscript.${id}`;
type Draft = { body: string; authorOnly: boolean; baseBody: string; baseRevision: number; baseAuthorOnly: boolean };
type Extension = { jobId?: string; before: string; text: string; prior?: string; ready?: boolean };
const extensionKey = (id: string) => `mrp.world.extension.${id}`;
function readExtension(id: string): Extension | null {
  try { const value = JSON.parse(localStorage.getItem(extensionKey(id)) ?? 'null') as Extension | null; return value && typeof value.before === 'string' && typeof value.text === 'string' ? { ...value, ready: value.ready ?? !!value.text } : null; } catch { return null; }
}
function readDraft(world: World): Draft {
  const record = world.archive_records.find((item) => item.id === world.manuscript_archive_id);
  try { const saved = localStorage.getItem(draftKey(world.id)); if (saved) { const parsed = JSON.parse(saved) as Draft; if (typeof parsed.body === 'string' && typeof parsed.baseBody === 'string') return { ...parsed, baseAuthorOnly: parsed.baseAuthorOnly ?? (record?.visibility === 'private') }; } } catch { /* storage may be unavailable */ }
  return { body: record?.body ?? '', authorOnly: record?.visibility === 'private', baseBody: record?.body ?? '', baseRevision: world.revision, baseAuthorOnly: record?.visibility === 'private' };
}
export function WorldManuscriptPanel({ world, onChange }: { world: World; onChange: (world: World) => void }) {
  const [draft, setDraft] = useState<Draft>(() => readDraft(world));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [jobId, setJobId] = useState(() => { try { return localStorage.getItem(`mrp.world.job.${world.id}`) ?? ''; } catch { return ''; } });
  const [sourceIds, setSourceIds] = useState(() => world.archive_records.filter((item) => item.id !== world.manuscript_archive_id).map((item) => item.id));
  const [includeManuscript, setIncludeManuscript] = useState(true);
  const [targetBook, setTargetBook] = useState(world.lorebook_ids[0] ?? '');
  const [preview, setPreview] = useState<WorldPreview | null>(null);
  const [previewMessage, setPreviewMessage] = useState('回到这里，看看这个世界。');
  const [instruction, setInstruction] = useState('');
  const [extension, setExtension] = useState<Extension | null>(() => readExtension(world.id));
  const [extensionJob, setExtensionJob] = useState(() => { const value = readExtension(world.id); return value?.ready ? '' : value?.jobId ?? ''; });
  const mutationBusy = useRef(false);
  const currentRecord = world.archive_records.find((item) => item.id === world.manuscript_archive_id);
  const changed = draft.body !== (currentRecord?.body ?? '') || draft.authorOnly !== (currentRecord?.visibility === 'private');
  const conflict = (draft.baseBody !== (currentRecord?.body ?? '') || draft.baseAuthorOnly !== (currentRecord?.visibility === 'private')) && changed;
  useEffect(() => {
    try { localStorage.setItem(draftKey(world.id), JSON.stringify(draft)); } catch { /* editing remains usable */ }
  }, [draft, world.id]);
  useEffect(() => {
    if (draft.body === draft.baseBody && draft.authorOnly === draft.baseAuthorOnly) setDraft({ body: currentRecord?.body ?? '', authorOnly: currentRecord?.visibility === 'private', baseBody: currentRecord?.body ?? '', baseRevision: world.revision, baseAuthorOnly: currentRecord?.visibility === 'private' });
  }, [world.revision]);
  useEffect(() => {
    try { if (extension) localStorage.setItem(extensionKey(world.id), JSON.stringify(extension)); else localStorage.removeItem(extensionKey(world.id)); } catch { /* backend tasks remain recoverable */ }
  }, [extension, world.id]);
  const { data: characters = [] } = useQuery({ ...resourceQueries.characters(), });
  const { data: books = [] } = useQuery({ ...resourceQueries.lorebooks(), });
  const { data: jobs = [], refetch: refreshJobs } = useQuery({ ...resourceQueries.assetJobs() });
  const relatedJobs = jobs.filter((job) => job.world_id === world.id && job.target_kind === 'world');
  const related = characters.filter((character) => character.source_world_id === world.id);
  const action = async (fn: () => Promise<void>) => { if (mutationBusy.current) return; mutationBusy.current = true; setBusy(true); setError(''); setNotice(''); try { await fn(); } catch (cause) { setError(creationError(cause)); } finally { mutationBusy.current = false; setBusy(false); } };
  const save = () => action(async () => {
    const frozen = { ...draft };
    const next = await worldCreationApi.saveManuscript(world.id, world.revision, frozen.body, frozen.authorOnly ? 'private' : 'public');
    onChange(next);
    setDraft((latest) => ({ ...latest, baseBody: frozen.body, baseRevision: next.revision, baseAuthorOnly: frozen.authorOnly }));
    setNotice('原稿已保存。未调用模型；新故事按本页的使用方式读取资料。');
  });
  const organize = () => action(async () => {
    const job = await worldCreationApi.create(draft.body, world.id, 'organize', '', draft.authorOnly ? 'private' : 'public');
    const id = job.id;
    setJobId(id); try { localStorage.setItem(`mrp.world.job.${world.id}`, id); } catch { /* optional convenience */ }
    void refreshJobs();
    await worldCreationApi.derive(id, job.bundle_revision ?? 1, [...(includeManuscript ? ['manuscript'] : []), ...sourceIds], targetBook || undefined);
  });
  const extend = () => action(async () => {
    const before = draft.body;
    const job = await worldCreationApi.create(before, world.id, 'extend', instruction, draft.authorOnly ? 'private' : 'public');
    setExtension({ jobId: job.id, before, text: '' });
    setExtensionJob(job.id);
    void refreshJobs();
    // The extension task keeps the requested portion separate from its frozen source.
    await worldCreationApi.generate(job.id);
  });
  const restoreJob = (id: string) => action(async () => {
    const job = await worldCreationApi.get(id);
    if (job.intent === 'extend') {
      const source = await worldCreationApi.source(id);
      const result = job.drafts?.find((item) => item.kind === 'world');
      const complete = ['failed', 'interrupted', 'review', 'needs_review', 'drafts_ready', 'complete', 'saved'].includes(job.status);
      setExtension((old) => old?.jobId === id ? old : { jobId: id, before: source.source, text: result?.addition_text ?? '', ready: complete && !!result });
      setExtensionJob(complete ? '' : id);
      if (job.errors?.length) setError(job.errors.join('；'));
      setNotice('已恢复补写任务。候选只适用于任务开始时的原稿。');
    } else {
      setJobId(id); try { localStorage.setItem(`mrp.world.job.${world.id}`, id); } catch { /* optional convenience */ }
    }
  });
  useEffect(() => {
    if (!extensionJob) return;
    let active = true;
    const timer = window.setInterval(() => {
      void worldCreationApi.get(extensionJob).then((job) => {
        if (!active) return;
        if (job.status === 'failed' || job.status === 'interrupted') { setError((job.errors ?? []).join('；') || '补写中断，原稿仍保留'); setExtensionJob(''); }
        if (job.status === 'review' || job.status === 'drafts_ready' || job.status === 'complete' || (job.drafts?.length && !['generating', 'ready'].includes(job.status))) {
          const result = job.drafts?.find((item) => item.kind === 'world');
          const text = result?.addition_text ?? '';
          setExtension((old) => old?.jobId === extensionJob && !old.ready ? { ...old, text, ready: true } : old); setExtensionJob(''); void refreshJobs();
        }
      }).catch((cause) => { if (active) { setError(creationError(cause)); setExtensionJob(''); } });
    }, 1200);
    return () => { active = false; window.clearInterval(timer); };
  }, [extensionJob]);
  return <>
    <section className="worlds-panel world-manuscript" aria-label="世界原稿">
      <header className="world-creation-heading"><div><span className="v7-eyebrow">从你的文字开始</span><h2>世界原稿</h2><p>自由写，或粘贴已有设定。可以留空，不必先分类或填写世界书。</p></div></header>
      {error && <p role="alert" className="v7-error">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <label className="world-creation-field">世界设定<textarea aria-label="世界设定原稿" rows={12} value={draft.body} onChange={(e) => setDraft((old) => ({ ...old, body: e.target.value }))} placeholder="这里的人住在哪里？遵循什么规则？想到什么就写什么，之后再整理。" /></label>
      <label className="world-creation-checkbox"><input type="checkbox" checked={draft.authorOnly} onChange={(e) => setDraft((old) => ({ ...old, authorOnly: e.target.checked }))} />仅作为作者资料留存，不直接供故事模型使用</label>
      <p className="world-creation-muted">本地草稿会保留到保存，刷新后可继续编辑。作者资料可以选给整理助手；整理结果仍需单独确认是否用于故事。</p>
      {conflict && <div className="world-creation-warning" role="alert">已保存的原稿或可见范围在别处发生变化。你的草稿仍保留，先核对再保存。<details><summary>查看已保存原稿及范围</summary><p>{currentRecord?.visibility === 'private' ? '仅作者资料，不直接供故事模型使用' : '可供故事模型使用'}</p><pre>{currentRecord?.body}</pre></details><button className="v7-btn v7-btn-soft" onClick={() => setDraft((old) => ({ ...old, baseBody: currentRecord?.body ?? '', baseRevision: world.revision, baseAuthorOnly: currentRecord?.visibility === 'private' }))}>已核对，以我的草稿继续修订</button></div>}
      <div className="world-creation-actions"><button className="v7-btn v7-btn-primary" disabled={busy || conflict || !changed || world.archived} onClick={() => void save()}>{busy ? '处理中…' : '保存原稿'}</button>
        <button className="v7-btn v7-btn-soft" disabled={busy || conflict || !draft.body.trim() || world.archived || (!includeManuscript && !sourceIds.length)} onClick={() => void organize()}>帮我整理核心与世界书</button>
        {changed && <button className="v7-btn v7-btn-soft" disabled={busy} onClick={() => setDraft({ body: currentRecord?.body ?? '', authorOnly: currentRecord?.visibility === 'private', baseBody: currentRecord?.body ?? '', baseRevision: world.revision, baseAuthorOnly: currentRecord?.visibility === 'private' })}>恢复已保存原稿</button>}</div>
      <details className="world-creation-details"><summary>整理助手可读取的资料</summary><p>默认只读取本次原稿和此世界的资料，可以逐项取消。不会读取其他世界或整库。</p>
        <label className="world-creation-checkbox"><input type="checkbox" checked={includeManuscript} disabled={busy} onChange={(e) => setIncludeManuscript(e.target.checked)} />本次原稿</label>
        {world.archive_records.filter((item) => item.id !== world.manuscript_archive_id).map((item) => <label className="world-creation-checkbox" key={item.id}><input type="checkbox" checked={sourceIds.includes(item.id)} onChange={() => setSourceIds((old) => old.includes(item.id) ? old.filter((id) => id !== item.id) : [...old, item.id])} />{item.title} · {item.visibility === 'private' ? '作者资料' : '故事资料'}</label>)}
        <label className="world-creation-field">采用到哪本世界书<select value={targetBook} onChange={(e) => setTargetBook(e.target.value)}><option value="">新建世界书</option>{books.filter((book) => world.lorebook_ids.includes(book.id)).map((book) => <option key={book.id} value={book.id}>更新 · {book.name}</option>)}</select></label>
        <p>更新现有书时，只重整来源已变化的内容，未选词条和手修内容会保留。</p>
      </details>
      {relatedJobs.length > 0 && <details className="world-creation-details"><summary>恢复此世界的整理与补写任务（{relatedJobs.length}）</summary><p>新任务不会删除旧候选。选择任务后继续核对，尚未采用的内容不会进入故事。</p><div className="world-creation-actions">{relatedJobs.map((job) => <button key={job.id} className="v7-btn v7-btn-soft" disabled={busy} onClick={() => void restoreJob(job.id)}>{job.intent === 'extend' ? '补写' : '整理'} · {job.title || '世界资料'} · {job.updated_at ? new Date(job.updated_at).toLocaleString() : ''}</button>)}</div></details>}
      <details className="world-creation-details"><summary>构思辅助：补写指定部分</summary><p>补写候选包含新增设定。采用后先进入你的原稿草稿，再自行保存或整理。</p>
        <label className="world-creation-field">想补哪一部分？<input value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="例如：只补充一项设定，保留已有规则" /></label>
        <button className="v7-btn v7-btn-soft" disabled={busy || !!extensionJob || !instruction.trim() || !draft.body.trim()} onClick={() => void extend()}>生成补写候选</button>
        {extensionJob && <p role="status">正在构思指定部分，原稿仍可编辑。</p>}
        {extension?.ready && <div className="world-review-proposal"><label className="world-creation-field">新增设定候选<textarea aria-label="新增设定候选" value={extension.text} onChange={(e) => setExtension((old) => old ? { ...old, text: e.target.value } : null)} rows={6} /></label>
          {extension.before !== draft.body && <p className="world-creation-warning">生成期间原稿已改变，候选已过期；请重新生成或复制需要的部分。</p>}
          {extension.prior === undefined && <button className="v7-btn v7-btn-soft" disabled={extension.before !== draft.body || !extension.text.trim()} onClick={() => { setDraft((old) => ({ ...old, body: `${old.body}\n\n${extension.text}` })); setExtension({ ...extension, before: `${draft.body}\n\n${extension.text}`, prior: draft.body }); }}>采用到原稿草稿</button>}
          {extension.prior !== undefined && <button className="v7-btn v7-btn-soft" disabled={extension.before !== draft.body} onClick={() => { setDraft((old) => ({ ...old, body: extension.prior! })); setExtension(null); }}>撤销补写采用</button>}</div>}
      </details>
      <details className="world-creation-details"><summary>新故事如何使用这些资料</summary>
        <label className="world-creation-field">使用方式<select value={world.runtime_policy ?? 'legacy_full'} disabled={busy || world.archived} onChange={(e) => void action(async () => onChange(await api.patchWorld(world.id, { expected_revision: world.revision, runtime_policy: e.target.value as 'raw' | 'compiled' | 'legacy_full' })))}>
          <option value="raw">直接使用选定故事资料原文</option><option value="compiled">使用核心与世界书，不发送完整原稿</option><option value="legacy_full">原有方式：核心、公开档案与世界书</option></select></label>
        <p>此设置只复制到新故事。已有故事和分支不会自动改变。</p>
        <label className="world-creation-field">检查时的输入<input value={previewMessage} onChange={(e) => setPreviewMessage(e.target.value)} /></label>
        <button className="v7-btn v7-btn-soft" disabled={busy} onClick={() => void action(async () => setPreview(await worldCreationApi.preview(world.id, previewMessage)))}>检查实际上下文（不生成回复）</button>
        {changed && <p>检查使用已保存资料，请先保存要检查的修改。</p>}
        {preview && <div role="status">{preview.valid ? <><p>约 {preview.estimated_tokens} tokens · {preview.input_limit === null ? '模型容量未知' : `输入容量 ${preview.input_limit}`}</p>{preview.sources.map((item) => <details key={item.id}><summary>{item.included ? '使用' : '未使用'} · {item.reason || item.id}</summary><pre>{item.content}</pre></details>)}<details><summary>查看完整输入</summary><pre>{preview.prompt}</pre></details></> : <p className="v7-error">{preview.error}</p>}</div>}
      </details>
    </section>
    {jobId && <WorldRuntimeReview key={jobId} jobId={jobId} world={world} sourceIds={[...(includeManuscript ? ['manuscript'] : []), ...sourceIds]} onCommitted={(next) => { onChange(next); setTargetBook((current) => next.lorebook_ids.includes(current) ? current : next.lorebook_ids.find((id) => !world.lorebook_ids.includes(id)) ?? next.lorebook_ids[0] ?? ''); }} />}
    <section className="worlds-panel" aria-label="相关角色"><header className="world-creation-heading"><h2>相关角色</h2><Link className="v7-btn v7-btn-soft" to={`/workshop/character?source_world=${encodeURIComponent(world.id)}`}>从这个世界创建角色</Link></header>
      <p className="world-creation-muted">来源世界用于归类与明确选择的创作参考，开局时仍由你选择世界和世界书。</p>
      {related.length ? <div className="world-related-characters">{related.map((character) => <Link key={character.id} to={`/library/characters/${encodeURIComponent(character.id)}`}>{character.card.name}</Link>)}</div> : <p>还没有关联的角色。</p>}
    </section>
  </>;
}
