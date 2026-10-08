import { queryClient } from '../../queryClient';
import { SurfaceDialog } from '../../design-system/SurfaceDialog';
import { useQuery } from '@tanstack/react-query';
import { resourceQueries } from '../resources/resourceQueries';
import { useEffect, useRef, useState } from "react";
import { isCancelled } from "../../api/request";
import { LoadState } from "../../components/LoadState";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router";
import { ArrowLeft, FileText, Sparkles, Upload, X } from "../../design-system/Icon";
import { Button, Checkbox, Select, TextInput, WorkbenchColumn, WorkbenchDesk, WorkbenchPage, buttonClass } from "../../design-system";
import { WorkshopDisclosure, WorkshopNotice, WorkshopWait, useElapsedSeconds } from "../../pages/WorkshopUi";
import { useCharacterStore } from "../../store/characterStore";
import type { ArchiveInput, CharacterCard, WorldSummary } from "../../types";
import CharacterEditor from "../../components/CharacterEditor";
import { blankCard } from "../../components/CharacterEditorModal";
import { ArchiveRecordEditor } from "../worlds/ArchiveRecordEditor";
import { WorldOverviewEditor } from "../worlds/WorldOverviewEditor";
import { WorldRuntimeReview } from '../worlds/WorldRuntimeReview';
import { worldCreationApi } from '../worlds/worldCreationApi';
import { assetImportApi } from "./assetImportApi";
import type { ImportDraft, ImportJob, ImportKind } from "./assetImportApi";
import { clearImportReviewAliases, committedImportRevision, importStatusLabel as statusLabel, readImportReviewAliases, sameImportInput, saveImportReviewAliases, type ImportInputSnapshot } from "./importWorkbenchState";
import "../worlds/worlds.css";
import "./asset-import.css";

const names: Record<ImportKind, string> = { world: "世界概览", background: "背景设定", biology: "生物卡", character: "人物卡" };

const message = (error: unknown) => error instanceof Error ? error.message : String(error);

function archiveValue(draft: ImportDraft): ArchiveInput {
  const data = draft.payload;
  return {
    kind: draft.kind === "biology" ? "biology" : "background",
    title: String(data.title ?? ""), subtype: String(data.subtype ?? ""),
    aliases: Array.isArray(data.aliases) ? data.aliases as string[] : [],
    tags: Array.isArray(data.tags) ? data.tags as string[] : [],
    summary: String(data.summary ?? ""), body: String(data.body ?? ""),
    visibility: data.visibility === "private" ? "private" : "public",
    kind_data: (data.kind_data && typeof data.kind_data === "object" ? data.kind_data : {}) as Record<string, string>,
  };
}

function fillMissing(base: Record<string, unknown>, generated: Record<string, unknown>): Record<string, unknown> {
  const result = { ...base };
  for (const [key, value] of Object.entries(generated)) {
    if (key === "visibility") continue;
    if (typeof value === "string" && value.trim() && !(value === "other" && base[key])) result[key] = value;
    else if (Array.isArray(value) && value.length) result[key] = value;
    else if (value && typeof value === "object") result[key] = fillMissing(
      (base[key] && typeof base[key] === "object" ? base[key] : {}) as Record<string, unknown>,
      value as Record<string, unknown>,
    );
  }
  return result;
}

function editableCharacter(card: CharacterCard): Record<string, unknown> {
  return {
    name: card.name, description: card.description, appearance: card.appearance,
    traits_label: card.traits_label, traits: card.traits, personality: card.personality,
    scenario: card.scenario, first_mes: card.first_mes, mes_example: card.mes_example,
    alternate_greetings: card.alternate_greetings, system_prompt: card.system_prompt,
    post_history_instructions: card.post_history_instructions,
    creator_notes: card.creator_notes, creator: card.creator,
    character_version: card.character_version, tags: card.tags,
    extensions: card.extensions,
  };
}

function savedUrl(draft: ImportDraft): string | null {
  if (!draft.saved_asset_id) return null;
  if (draft.kind === "world") return `/worlds/${draft.saved_asset_id}`;
  if (draft.kind === "character") return `/library/characters/${draft.saved_asset_id}`;
  if (!draft.world_id) return null;
  return `/worlds/${draft.world_id}/archive/${draft.kind}/${draft.saved_asset_id}`;
}

function ReviewDraft({ job, draft, source, worlds, onChanged, onClose, onSaved }: {
  job: ImportJob; draft: ImportDraft; source: string; worlds: WorldSummary[];
  onChanged: () => Promise<void>; onClose: () => void; onSaved: (draft: ImportDraft) => void;
}) {
  const [currentDraft, setCurrentDraft] = useState(draft);
  const [selectedFields, setSelectedFields] = useState<string[] | null>(draft.selected_fields ?? null);
  const [payload, setPayload] = useState<Record<string, unknown>>(
    draft.proposal?.payload && !draft.proposal.error ? draft.proposal.payload : draft.payload,
  );
  const [worldId, setWorldId] = useState(draft.world_id ?? "");
  const [targetId, setTargetId] = useState(draft.target_asset_id ?? "");
  const [targetRevision, setTargetRevision] = useState<number | null>(draft.target_revision);
  const charactersQuery = useQuery({ ...resourceQueries.characters(), enabled: draft.kind === "character" });
  const characters = charactersQuery.data ?? [];
  const targetWorld = useQuery({ ...resourceQueries.world(worldId), enabled: !!worldId && (draft.kind === 'biology' || draft.kind === 'background') });
  const archives = (targetWorld.data?.archive_records ?? []).filter(record => record.kind === draft.kind);
  const [busy, setBusy] = useState(false);
  const [previewVersion, setPreviewVersion] = useState(0);
  const [priorPayload, setPriorPayload] = useState<Record<string, unknown> | null>(
    draft.proposal?.payload && !draft.proposal.error ? draft.payload : null,
  );
  const [error, setError] = useState("");
  // The review snapshot owns its editable form. Remote refreshes must never
  // replace typing or silently advance the CAS revision of an open editor.
  const committedDraft = useRef<ImportDraft | null>(draft.status === 'saved' ? draft : null);
  const committedCharacterRevision = useRef<number | undefined>(undefined);
  useEffect(() => { if (targetWorld.error) setError(message(targetWorld.error)); }, [targetWorld.error]);
  const chooseTarget = (id: string) => {
    if (busy || committedDraft.current) return;
    setTargetId(id);
    if (!id) { setTargetRevision(null); return; }
    if (draft.kind === "world") {
      const selected = worlds.find((item) => item.id === id);
      if (selected) { setTargetRevision(selected.revision); setPayload((old) => fillMissing({ title: selected.title, description: selected.description, core_brief: selected.core_brief }, old)); }
    } else if (draft.kind === "character") {
      const selected = characters.find((item) => item.id === id);
      if (selected) { setTargetRevision(selected.revision ?? null); setPayload((old) => fillMissing(editableCharacter(selected.card), old)); }
    } else {
      const selected = archives.find((item) => item.id === id);
      if (selected) { setTargetRevision(selected.revision); setPayload((old) => fillMissing({
        title: selected.title, subtype: selected.subtype, aliases: selected.aliases, tags: selected.tags,
        summary: selected.summary, body: selected.body, visibility: selected.visibility,
        kind_data: selected.kind_data,
      }, old)); }
    }
  };
  const targetChoices = draft.kind === "world" ? worlds.map((item) => ({ id: item.id, title: item.title }))
    : draft.kind === "character" ? characters.map((item) => ({ id: item.id, title: item.card.name }))
      : archives.map((item) => ({ id: item.id, title: item.title }));
  const edit = async (value: Record<string, unknown>) => {
    const updated = await assetImportApi.edit(job.id, currentDraft, value, worldId || null, targetId || null, targetRevision, selectedFields);
    setCurrentDraft(updated);
    await onChanged();
    return updated;
  };
  const save = async (value: Record<string, unknown>, rethrow = false, aliases: string[] = []) => {
    setBusy(true); setError("");
    try {
      if (draft.kind === 'character' && committedDraft.current?.saved_asset_id) {
        const identity = committedDraft.current.saved_asset_id;
        if (!Number.isInteger(committedCharacterRevision.current)) throw new Error('角色已创建，但尚未确认提交修订；请保留审阅内容并重新读取角色。');
        await useCharacterStore.getState().updateCharacter(identity, { card: value, aliases, expected_revision: committedCharacterRevision.current });
        committedCharacterRevision.current = useCharacterStore.getState().characters.find(row => row.id === identity)?.revision;
        clearImportReviewAliases(localStorage,job.id,draft.id);
        await onChanged();
        return;
      }
      const saved = await edit(value);
      const committed = await assetImportApi.commit(job.id, saved.id);
      committedDraft.current = committed;
      if (draft.kind === 'character') {
        // Persisted identity and exact commit CAS survive a later aliases PATCH failure.
        const committedRevision = committedImportRevision(saved);
        committedCharacterRevision.current = committedRevision;
        const roles = await queryClient.fetchQuery({ ...resourceQueries.characters(), staleTime:0 });
        const role = roles.find(row => row.id === committed.saved_asset_id);
        if (!role || role.revision !== committedRevision) throw new Error('角色入库后已有其他修改；已保留已创建角色和本次审阅，请到角色页比对后继续。');
        if (JSON.stringify(role.aliases) !== JSON.stringify(aliases)) {
          await useCharacterStore.getState().updateCharacter(role.id, { aliases, expected_revision:committedRevision });
          committedCharacterRevision.current = useCharacterStore.getState().characters.find(row => row.id === role.id)?.revision;
        }
        clearImportReviewAliases(localStorage,job.id,draft.id);
        await onChanged();
      } else { await onChanged(); onSaved(committed); }
    } catch (cause) { setError(message(cause)); if (rethrow) throw cause; }
    finally { setBusy(false); }
  };
  const regenerate = async (value: Record<string, unknown>) => {
    if (busy || committedDraft.current) return;
    setBusy(true); setError("");
    try {
      await edit(value);
      await assetImportApi.regenerate(job.id, draft.id);
      await onChanged();
      onClose();
    } catch (cause) { setError(message(cause)); throw cause; }
    finally { setBusy(false); }
  };
  const adoptProposal = () => {
    if (busy || committedDraft.current) return;
    if (!draft.proposal?.payload || draft.proposal.error) return;
    setPriorPayload(payload);
    setPayload(draft.proposal.payload);
    setPreviewVersion((value) => value + 1);
  };
  const restorePrior = () => {
    if (busy || committedDraft.current) return;
    if (!priorPayload) return;
    setPayload(priorPayload);
    setPriorPayload(null);
    setPreviewVersion((value) => value + 1);
  };
  const evidence = selectedFields !== null ? source : draft.source_start !== null ? draft.evidence : source.slice(0, 1600);
  if (draft.kind === "character") {
    if (targetId && !characters.some(row => row.id === targetId) && charactersQuery.isPending) return <SurfaceDialog title="读取目标人物" className="asset-import-overlay" onClose={onClose}><section className="asset-import-dialog"><LoadState title="正在读取目标人物的当前内容" /></section></SurfaceDialog>;
    if (targetId && !characters.some(row => row.id === targetId) && charactersQuery.error) return <SurfaceDialog title="目标人物读取失败" className="asset-import-overlay" onClose={onClose}><section className="asset-import-dialog"><LoadState title="目标人物读取失败" error={message(charactersQuery.error)} onRetry={() => void charactersQuery.refetch()} /></section></SurfaceDialog>;
    const targetCard = characters.find((item) => item.id === targetId)?.card;
    const initialCard: CharacterCard = { ...blankCard(), ...job.character_seed, ...targetCard, ...Object.fromEntries(Object.entries(payload).filter(([key]) => selectedFields === null || key === 'name' || selectedFields.includes(key))) };
    return <CharacterEditor key={`${draft.id}:${targetId}:${previewVersion}`} initialCard={initialCard} initialAliases={readImportReviewAliases(localStorage,job.id,draft.id,draft.revision, characters.find(row => row.id === targetId)?.aliases ?? job.character_aliases ?? [], targetId)} busy={busy}
      draftScope={`asset:${job.id}:${draft.id}`} initialAdvancedOpen hideSourceWorld
      onClose={onClose}
      hideRuntime
      saveLabel={committedDraft.current ? "保存人物修改" : "确认并保存人物卡"}
      reviewEvidence={evidence}
      reviewTarget={<><p className="asset-import-muted">别名草稿保存在本机，确认入库时保存到角色。</p>{charactersQuery.error && targetId && <WorkshopNotice tone="warning" actions={<Button size="sm" variant="tonal" onClick={() => void charactersQuery.refetch()}>重试读取</Button>}>无法刷新人物目录：{message(charactersQuery.error)}。当前审阅内容会保留。</WorkshopNotice>}{selectedFields !== null && <fieldset className="creation-organize-fields"><legend>本次组合采用的字段</legend><p>简介与选中的字段一起采用；原稿留作作者资料，未选字段保留已有内容。</p>{[['description','整理后的简介'],['appearance','外貌'],['traits','能力与实力'],['personality','性格'],['scenario','背景与关系'],['first_mes','开场'],['mes_example','口吻示例']].map(([field,label]) => <Checkbox key={field} label={label} disabled={field === 'description' || busy || !!committedDraft.current} checked={selectedFields.includes(field)} onChange={() => setSelectedFields((old) => old!.includes(field) ? old!.filter((key) => key !== field) : [...old!,field])} />)}</fieldset>}{draft.warnings?.map((warning, index) => <div className="asset-import-notice" key={index}>{warning}</div>)}<label className="asset-import-world-select">保存方式<Select disabled={busy || !!committedDraft.current} value={targetId || '__new'} onValueChange={value => chooseTarget(value === '__new' ? '' : value)} options={[{value:'__new',label:'新建人物卡'}, ...targetChoices.map(item => ({value:item.id,label:`更新 · ${item.title}`}))]} /></label>{draft.proposal && <div className="asset-import-proposal"><strong>重新生成的候选</strong><p>{draft.proposal.error || "新稿已自动填入下方编辑器，尚未保存；可以继续修改或恢复旧稿。"}</p>{draft.proposal.warnings?.map((warning, index) => <p key={index}>{warning}</p>)}{!draft.proposal.error && <Button variant="tonal" disabled={busy || !!committedDraft.current} onClick={adoptProposal}>重新应用新稿</Button>}{priorPayload && <Button variant="tonal" disabled={busy || !!committedDraft.current} onClick={restorePrior}>恢复旧稿</Button>}<WorkshopDisclosure title="查看新稿字段"><pre>{JSON.stringify(draft.proposal.payload, null, 2)}</pre></WorkshopDisclosure></div>}</>}
      onSaveSucceeded={(_asCopy, hasLaterEdits) => { if (!hasLaterEdits && committedDraft.current) onSaved(committedDraft.current); }}
      onSaveDraft={committedDraft.current ? undefined : async ({ card, aliases }) => { setBusy(true); try { const updated = await edit(editableCharacter(card)); saveImportReviewAliases(localStorage,job.id,draft.id,updated.revision,aliases,updated.target_asset_id ?? ''); } finally { setBusy(false); } }}
      onRegenerate={committedDraft.current ? undefined : async (card) => regenerate(editableCharacter(card))}
      onSave={async ({ card, aliases }) => {
        await save(editableCharacter(card), true, aliases);
      }} />;
  }
  return <SurfaceDialog title={`审阅${names[draft.kind]}`} className="asset-import-overlay" onClose={onClose} busy={busy} nested={false}>
    <section className="asset-import-dialog" aria-label={`审阅${names[draft.kind]}`} onMouseDown={(event) => event.stopPropagation()}>
      <header><div><span className="asset-import-muted">导入草稿 / {names[draft.kind]}</span><h2>{draft.title}</h2></div><div className="asset-import-head-actions"><Button variant="tonal" disabled={busy} onClick={() => void edit(payload).catch((cause) => setError(message(cause)))}>保存草稿</Button><Button variant="tonal" disabled={busy} onClick={() => void regenerate(payload).catch(() => {})}>重新生成</Button><Button variant="ghost" disabled={busy} onClick={onClose}>关闭</Button></div></header>
      {error && <div className="asset-import-notice" role="alert">{error}</div>}
      {draft.schema_changed && <div className="asset-import-notice">资产格式已更新；请检查新字段并重新保存草稿。</div>}
      {draft.error && <div className="asset-import-notice">生成校验未通过：{draft.error}。可在下方手工修正。</div>}
      {draft.proposal && <div className="asset-import-proposal"><strong>重新生成的候选</strong><p>{draft.proposal.error || "新稿不会覆盖当前草稿；采用后还可以修改。"}</p>{!draft.proposal.error && <Button variant="tonal" disabled={busy || !!committedDraft.current} onClick={adoptProposal}>采用新稿</Button>}{priorPayload && <Button variant="tonal" disabled={busy || !!committedDraft.current} onClick={restorePrior}>恢复旧稿</Button>}<WorkshopDisclosure title="查看新稿字段"><pre>{JSON.stringify(draft.proposal.payload, null, 2)}</pre></WorkshopDisclosure></div>}
      <div className="asset-import-review-grid">
        <fieldset className="asset-import-form" disabled={busy}>
          <label className="asset-import-world-select">保存方式<Select disabled={busy || !!committedDraft.current} value={targetId || '__new'} onValueChange={value => chooseTarget(value === '__new' ? '' : value)} options={[{value:'__new',label:`新建${names[draft.kind]}`}, ...targetChoices.map(item => ({value:item.id,label:`更新 · ${item.title}`}))]} /></label>
          {(draft.kind === "biology" || draft.kind === "background") ? <>
            <label className="asset-import-world-select">归入世界
              <Select value={worldId || '__none'} onValueChange={value => { setWorldId(value === '__none' ? '' : value); setTargetId(''); setTargetRevision(null); }} options={[{value:'__none',label:'请选择已保存的世界'}, ...worlds.map(world => ({value:world.id,label:world.title}))]} />
            </label>
            <ArchiveRecordEditor kind={draft.kind} value={archiveValue({ ...draft, payload })}
              onChange={(next) => { const { kind: _kind, ...data } = next; void _kind; setPayload(data); }}
              onSave={() => void save(payload)} onCancel={onClose} busy={busy} saveLabel="确认并保存" />
          </> : <WorldOverviewEditor
            value={{ title: String(payload.title ?? ""), description: String(payload.description ?? ""), core_brief: String(payload.core_brief ?? "") }}
            onChange={(next) => setPayload({ ...next })} onSave={() => void save(payload)} busy={busy} saveLabel="确认并保存世界" />}
        </fieldset>
        <aside className="asset-import-source"><strong>原稿对照</strong><p>生成内容请以原稿为准；模型推断可在保存前删改。</p>{draft.evidence && draft.source_start === null && <p>模型给出的依据未能在原稿定位，下面显示原稿开头供核对。</p>}<blockquote>{evidence}</blockquote><WorkshopDisclosure title="查看完整原稿"><pre>{source}</pre></WorkshopDisclosure></aside>
      </div>
    </section>
  </SurfaceDialog>;
}

export default function AssetImportPage() {
  const { jobId, draftId } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const [source, setSource] = useState("");
  const [mobilePane, setMobilePane] = useState<'input' | 'result'>('input');
  const sourceEpoch = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);
  const reviewSession = useRef<string | null>(null);
  const createdTask = useRef<{ job: ImportJob; input: ImportInputSnapshot } | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; sourceEpoch.current++; }; }, []);
  const [target, setTarget] = useState<ImportKind | "">((search.get("target") as ImportKind) || "");
  const [worldId, setWorldId] = useState(search.get("world") || "");
  const { data: worlds = [] } = useQuery(resourceQueries.worlds());
  const setWorlds = (value: WorldSummary[]) => queryClient.setQueryData(resourceQueries.worlds().queryKey, value);
  const jobsQuery = useQuery({ ...resourceQueries.assetJobs(), enabled: !jobId });
  const jobs = jobsQuery.data ?? [];
  const [job, setJob] = useState<ImportJob | null>(null);
  const [candidates, setCandidates] = useState<ImportJob["candidates"]>([]);
  const [original, setOriginal] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = async () => { if (jobId) { const owns = captureRoute(); const value = await assetImportApi.get(jobId); if (owns()) setJob(value); } };
  const refreshAll = async () => { await refresh(); setWorlds(await queryClient.fetchQuery({ ...resourceQueries.worlds(), staleTime: 0 })); };
  const activeJob = useRef(jobId);
  const routeEpoch = useRef(0);
  if (activeJob.current !== jobId) routeEpoch.current++;
  activeJob.current = jobId;
  const captureRoute = () => { const id = jobId, epoch = routeEpoch.current; return () => mounted.current && activeJob.current === id && routeEpoch.current === epoch; };
  const [readRetry, setReadRetry] = useState(0);
  const [readLoading, setReadLoading] = useState(false);
  const [readError, setReadError] = useState("");
  const running = busy || ['generating','analyzing','regenerating'].includes(job?.status ?? '');
  const elapsed = useElapsedSeconds(running);

  useEffect(() => {
    let live = true;
    const controller = new AbortController();
    setJob(null); setOriginal(""); setReadError(""); setReadLoading(true); setBusy(false); setError("");
    const fail = (cause: unknown) => { if (live && !isCancelled(cause)) setReadError(message(cause)); };
    if (jobId) {
      void assetImportApi.get(jobId, { signal: controller.signal }).then((value) => { if (live) setJob(value); }).catch(fail).finally(() => { if (live) setReadLoading(false); });
      void assetImportApi.source(jobId, { signal: controller.signal }).then(({ source }) => { if (live) setOriginal(source); }).catch(fail);
    } else { setReadLoading(false); void jobsQuery.refetch().catch(fail); }
    return () => { live = false; controller.abort(); };
  }, [jobId, readRetry]);
  useEffect(() => { if (job?.status === "classified") setCandidates(job.candidates); }, [job?.id, job?.status]);
  useEffect(() => {
    if (!jobId || !["generating", "analyzing", "regenerating"].includes(job?.status ?? "")) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const poll = async () => {
      try { const value = await assetImportApi.get(jobId, { signal: controller.signal }); if (live && activeJob.current === jobId) { setJob(value); setReadError(""); } }
      catch (cause) { if (live && !isCancelled(cause)) setReadError(message(cause)); }
      finally { if (live) timer = setTimeout(poll, 1500); }
    };
    timer = setTimeout(poll, 1500);
    return () => { live = false; controller.abort(); clearTimeout(timer); };
  }, [jobId, job?.status]);
  const start = async () => {
    if (busy || !source.trim()) return;
    const submitted = { source, target, worldId };
    const owns = captureRoute();
    setBusy(true); setError("");
    try {
      if (worldId && ['world', 'background', 'biology'].includes(target)) {
        const key = `mrp.world.organize.input.${worldId}`;
        let previous: Record<string, unknown> = {};
        try { previous = JSON.parse(localStorage.getItem(key) || '{}') as Record<string, unknown>; } catch { /* Start with a fresh input. */ }
        localStorage.setItem(key, JSON.stringify({ ...previous, category: 'archives', source_text: source, instruction: '', reference_source_ids: [], source_visibility: 'public', target_archive_id: null, target_lorebook_id: null, new_lorebook_name: '' }));
        navigate(`/worlds/${encodeURIComponent(worldId)}/organize?category=archives`);
        return;
      }
      const previous = createdTask.current;
      const created = previous && sameImportInput(previous.input, submitted) ? previous.job : await assetImportApi.create(submitted.source, submitted.target || null, submitted.worldId || null);
      createdTask.current = { job: created, input: submitted };
      if (!owns()) return;
      if (submitted.target === 'world') await worldCreationApi.derive(created.id, 1);
      else if (submitted.target) await assetImportApi.generate(created.id);
      else await assetImportApi.analyze(created.id);
      if (owns()) { setMobilePane("result"); navigate(`/library/imports/${created.id}`); }
    } catch (cause) { if (owns()) setError(message(cause)); }
    finally { if (owns()) setBusy(false); }
  };
  const retry = async () => {
    if (!job || busy) return;
    const owns = captureRoute();
    setBusy(true); setError("");
    try { const updated = job.interrupted_phase === "regenerating" && job.regenerate_draft_id
      ? await assetImportApi.regenerate(job.id, job.regenerate_draft_id)
      : job.target_kind || job.candidates.length ? await assetImportApi.generate(job.id) : await assetImportApi.analyze(job.id); if (owns()) setJob(updated); }
    catch (cause) { if (owns()) setError(message(cause)); }
    finally { if (owns()) setBusy(false); }
  };
  const generateCandidates = async () => {
    if (!job || busy) return;
    const owns = captureRoute();
    setBusy(true); setError("");
    try {
      await assetImportApi.updateCandidates(job.id, candidates);
      const updated = await assetImportApi.generate(job.id);
      if (owns()) setJob(updated);
    } catch (cause) { if (owns()) setError(message(cause)); }
    finally { if (owns()) setBusy(false); }
  };
  const remove = async () => {
    if (!job || busy || !window.confirm("删除这次导入任务和原稿？已经确认保存的素材会保留。")) return;
    const owns = captureRoute();
    setBusy(true); setError("");
    try { await assetImportApi.remove(job.id); if (owns()) navigate("/library/imports"); }
    catch (cause) { if (owns()) setError(message(cause)); }
    finally { if (owns()) setBusy(false); }
  };
  const recoverWorldSource = async () => {
    if (!job || busy) return;
    const owns = captureRoute();
    setBusy(true); setError("");
    try { const updated = await assetImportApi.recoverWorldSource(job.id); if (owns()) setJob(updated); }
    catch (cause) { if (owns()) setError(message(cause)); }
    finally { if (owns()) setBusy(false); }
  };
  const selected = job?.drafts.find((draft) => draft.id === draftId);
  useEffect(() => {
    if (!selected) { reviewSession.current = null; return; }
    if (selected.status === 'saved' && reviewSession.current !== selected.id) navigate(savedUrl(selected) ?? `/library/imports/${jobId}`, { replace:true });
    else reviewSession.current = selected.id;
  }, [selected?.status, selected?.id]);
  if (job?.world_id && job.target_kind !== 'character') return <Navigate replace to={`/worlds/${encodeURIComponent(job.world_id)}/organize?legacy=${encodeURIComponent(job.id)}`} />;
  return <WorkbenchPage className="asset-import-page">
    <header className="workbench-bar"><Link to="/library" className={buttonClass({variant:'ghost',size:'sm'})}><ArrowLeft size={16} />素材库</Link><div className="workbench-bar__title"><h1>导入素材</h1></div>{jobId && <Link to="/library/imports" className={buttonClass({variant:'ghost',size:'sm'})}>提交新原稿</Link>}</header>
    <WorkbenchDesk layout="form-main" className="asset-import-desk" data-pane={mobilePane}>
      <nav className="asset-import-mobile-tabs" aria-label="导入面板"><Button variant={mobilePane === 'input' ? 'tonal' : 'ghost'} onClick={() => setMobilePane('input')}>填写</Button><Button variant={mobilePane === 'result' ? 'tonal' : 'ghost'} onClick={() => setMobilePane('result')}>结果</Button></nav>
      <WorkbenchColumn variant="form" className="asset-import-input">
        <div className="workbench-column__head"><h2>{jobId ? '原稿与任务' : '提交原稿'}</h2></div>
        <div className="workbench-column__body asset-import-input-body">
          {jobId ? <>
            <p className="asset-import-muted">任务 {jobId} · {job ? statusLabel(job.status) : '读取中'}</p>
            {job && <><h3>{job.title}</h3><p className="asset-import-muted">{job.source_length} 字 · {new Date(job.created_at).toLocaleString('zh-CN')}</p></>}
            <WorkshopDisclosure title="查看完整原稿" defaultOpen><pre className="asset-import-source-text">{original || '正在读取原稿…'}</pre></WorkshopDisclosure>
            {job && <div className="asset-import-actions"><Button variant="ghost" disabled={busy} onClick={() => void remove()}>删除任务</Button>{job.drafts.some(draft => draft.kind === 'world' && draft.status === 'saved') && !job.drafts.some(draft => draft.source_exact) && <Button variant="tonal" disabled={busy} onClick={() => void recoverWorldSource()}>从原稿补建完整背景档案草稿</Button>}</div>}
            <p className="asset-import-muted">模型先生成草稿。检查原稿依据、编辑内容并确认后，素材才会进入素材库。</p>
          </> : <>
            <label className="asset-import-field asset-import-source-field"><span>原稿正文 *</span><textarea value={source} onChange={event => { sourceEpoch.current++; createdTask.current = null; setSource(event.target.value); }} placeholder="粘贴世界观、种族或人物设定原文…" rows={9} /></label>
            <input ref={fileInput} className="asset-import-hidden-file" type="file" accept=".txt,.md,text/plain,text/markdown" aria-label="选择原稿文件" tabIndex={-1} onChange={event => {
              const file = event.target.files?.[0]; if (!file) return;
              const epoch = ++sourceEpoch.current;
              void file.text().then(value => { if (mounted.current && sourceEpoch.current === epoch) { createdTask.current = null; setSource(value); } }).catch(cause => { if (mounted.current && sourceEpoch.current === epoch) setError(message(cause)); });
              event.target.value = '';
            }} />
            <Button variant="tonal" icon={Upload} onClick={() => fileInput.current?.click()}>选择 .txt / .md 文件</Button>
            <label className="asset-import-field"><span>目标类型</span><Select value={target || '__auto'} onValueChange={value => { createdTask.current = null; setTarget(value === '__auto' ? '' : value as ImportKind); }} options={[{value:'__auto',label:'自动识别'},...Object.entries(names).map(([value,label]) => ({value,label}))]} /></label>
            <label className="asset-import-field"><span>目标世界</span><Select value={worldId || '__none'} onValueChange={value => { createdTask.current = null; setWorldId(value === '__none' ? '' : value); }} options={[{value:'__none',label:'稍后选择'},...worlds.map(world => ({value:world.id,label:world.title}))]} /></label>
            <p className="asset-import-muted">原稿将发送给设置页当前渠道的辅助模型；完整原文会保存在导入任务中。</p>
          </>}
        </div>
        <div className="workbench-column__foot asset-import-foot">
          {!jobId ? <Button variant="primary" icon={Sparkles} disabled={busy || !source.trim()} onClick={() => void start()}>{busy ? '正在建立任务…' : error && createdTask.current && sameImportInput(createdTask.current.input, { source, target, worldId }) ? '重试当前任务' : '开始整理'}</Button> : job && (job.status === 'classified' || job.status === 'failed' && !job.drafts.length && !job.target_kind) ? <Button variant="primary" disabled={busy || !candidates.length} onClick={() => void generateCandidates()}>{busy ? '正在提交…' : `确认并生成 ${candidates.length} 项草稿`}</Button> : job && ['ready','failed','interrupted'].includes(job.status) ? <Button variant="primary" disabled={busy} onClick={() => void retry()}>{job.status === 'ready' ? '开始生成' : '继续未完成项'}</Button> : <p className="asset-import-muted">{job ? `${job.drafts.length} 项草稿 · ${statusLabel(job.status)}` : '正在读取任务'}</p>}
          <p className="asset-import-muted">已有素材与已保存的草稿会保留。</p>
        </div>
      </WorkbenchColumn>
      <WorkbenchColumn className="asset-import-results">
        <div className="workbench-column__head"><h2>{jobId ? job?.target_kind === 'world' ? '世界候选' : '整理结果' : '已有导入任务'}</h2><span className="asset-import-status">{jobId ? job?.drafts.length ?? 0 : jobs.length}</span>{job && <span className="asset-import-status">{statusLabel(job.status)}</span>}</div>
        <div className="workbench-column__body asset-import-results-body">
          {error && <WorkshopNotice tone="danger">{error}。输入和已生成内容会保留，可重试。</WorkshopNotice>}
          {(readError || !jobId && jobsQuery.error) && <LoadState title="导入资料读取失败" error={readError || message(jobsQuery.error)} onRetry={() => setReadRetry(value => value + 1)} />}
          {running && <WorkshopNotice busy>{job ? statusLabel(job.status) : '正在建立任务'} · <WorkshopWait seconds={elapsed} /><p>离开后可从任务列表继续查看。</p></WorkshopNotice>}
          {!jobId ? <>{jobsQuery.isPending ? <LoadState title="正在读取任务列表" /> : <div className="asset-import-tasks-list">{jobs.length ? jobs.map(row => <Link className="asset-import-row" key={row.id} to={`/library/imports/${row.id}`} onClick={() => setMobilePane('result')}><FileText size={20} /><span><strong>{row.title}</strong><small>{row.updated_at ? new Date(row.updated_at).toLocaleString('zh-CN') : '时间未记录'}</small></span><span className="asset-import-status">{statusLabel(row.status)}</span></Link>) : !readError && <p className="asset-import-empty">还没有导入任务。提交原稿后，识别和生成的结果会在这里出现。</p>}</div>}</> : job?.target_kind === 'world' ? <WorldRuntimeReview key={job.id} jobId={job.id} onCommitted={world => navigate(`/worlds/${world.id}`)} /> : job ? <>
            {job.errors.map((item,index) => <WorkshopNotice tone="danger" key={index}>{item}</WorkshopNotice>)}
            {(job.status === 'classified' || job.status === 'failed' && !job.drafts.length && !job.target_kind) && <section className="asset-import-candidates"><h3>先确认要生成哪些素材</h3><p className="asset-import-muted">可以改类型、标题、增减候选。原稿片段会交给对应的整理功能。</p>{candidates.map((candidate,index) => <article className="asset-import-candidate" key={index}><div className="asset-import-candidate-head"><Select aria-label="候选类型" value={candidate.kind} onValueChange={value => setCandidates(rows => rows.map((row,i) => i === index ? {...row,kind:value as ImportKind} : row))} options={Object.entries(names).map(([value,label]) => ({value,label}))} /><TextInput aria-label="候选标题" value={candidate.title} onChange={event => setCandidates(rows => rows.map((row,i) => i === index ? {...row,title:event.target.value} : row))} /><Button size="sm" variant="ghost" icon={X} onClick={() => setCandidates(rows => rows.filter((_,i) => i !== index))}>移除</Button></div><WorkshopDisclosure title="查看对应原稿"><pre className="asset-import-source-text">{candidate.excerpt}</pre></WorkshopDisclosure></article>)}<Button variant="tonal" disabled={!original || candidates.length >= 12} onClick={() => setCandidates(rows => [...rows,{kind:'background',title:'新候选',excerpt:original}])}>添加候选</Button></section>}
            <div className="asset-import-drafts">{job.drafts.map(draft => ['generating','regenerating'].includes(job.status) ? <div className="asset-import-row" key={draft.id}><FileText size={20} /><span><strong>{draft.title}</strong><small>{names[draft.kind]}</small></span><span className="asset-import-status">整理中，稍后审阅</span></div> : <Link className="asset-import-row" key={draft.id} to={draft.status === 'saved' ? savedUrl(draft) ?? `/library/imports/${job.id}` : `/library/imports/${job.id}/drafts/${draft.id}`}><FileText size={20} /><span><strong>{draft.title}</strong><small>{names[draft.kind]} · 修订 {draft.revision}</small></span><span className="asset-import-status" data-state={draft.status}>{draft.status === 'saved' ? '已保存 · 查看素材' : draft.status === 'failed' ? '需修正' : '待审阅'}</span></Link>)}</div>
            {!job.drafts.length && !candidates.length && !running && <p className="asset-import-empty">此任务尚无草稿；可以继续生成或从原稿开始确认候选。</p>}
          </> : !readError && <LoadState title={readLoading ? '正在载入导入任务' : '读取导入任务'} />}
        </div>
        <div className="workbench-column__foot"><p className="asset-import-muted">{job ? `任务更新于 ${new Date(job.updated_at).toLocaleString('zh-CN')}` : '任务、原稿与未完成的草稿可随时恢复'}</p></div>
      </WorkbenchColumn>
    </WorkbenchDesk>
    {job && selected && job.target_kind !== 'world' && <ReviewDraft key={selected.id} job={job} draft={selected} source={original} worlds={worlds} onChanged={refreshAll} onClose={() => navigate(`/library/imports/${job.id}`)} onSaved={draft => navigate(savedUrl(draft) ?? `/library/imports/${job.id}`)} />}
  </WorkbenchPage>;
}
