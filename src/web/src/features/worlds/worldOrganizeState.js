export function initialOrganizeInput(search = '') {
  const query = new URLSearchParams(search);
  const category = query.get('category') === 'lorebook' || (!query.has('category') && (query.has('book') || query.has('source_archive'))) ? 'lorebook' : 'archives';
  return {
    category,
    source_text: '', instruction: '', reference_source_ids: [], source_visibility: 'public',
    target_archive_id: category === 'archives' ? query.get('archive') || null : null,
    target_lorebook_id: category === 'lorebook' ? query.get('book') || null : null,
    new_lorebook_name: '',
  };
}

export function organizeCandidates(drafts, kind) {
  return drafts.filter((draft) => draft.kind === kind);
}

export function toggleOrganizeReference(ids, id) {
  return ids.includes(id) ? ids.filter((value) => value !== id) : [...ids, id];
}

export function buildOrganizeCommit(job, draftIds, approvedIds, operationId, options = {}) {
  const drafts = job.drafts.filter((draft) => draftIds.includes(draft.id) && draft.status === 'review');
  if (!drafts.length || drafts.length !== new Set(draftIds).size) throw new Error('请选择尚未保存的候选。');
  const replacements = drafts.filter((draft) => draft.kind === 'lorebook' && draft.action === 'replace');
  if (replacements.some((draft) => !approvedIds.includes(draft.id))) throw new Error('请逐条确认要更新的现有词条。');
  return {
    operation_id: operationId, expected_revision: job.revision,
    draft_ids: drafts.map((draft) => draft.id),
    approved_replace_draft_ids: replacements.map((draft) => draft.id),
    ...(job.target_lorebook_id && job.target_revision !== null && job.target_revision !== undefined ? { expected_lorebook_revision: job.target_revision } : {}),
    accept_source_changes: options.acceptSourceChanges === true,
  };
}

/** Disposing a UI scope only ignores late responses; it never cancels a server task. */
export function createOrganizeScope(worldId) {
  let active = true;
  return { isCurrent: (candidateWorldId) => active && candidateWorldId === worldId, dispose: () => { active = false; } };
}

export function readLegacyCandidates(job) {
  const items = [...(job.drafts || [])];
  const bundle = job.bundle;
  if (bundle?.core_proposal) items.push({ ...bundle.core_proposal, kind: 'core', payload: { content: bundle.core_proposal.content || '' } });
  for (const proposal of bundle?.entry_proposals || []) items.push({ ...proposal, kind: 'lorebook' });
  if (bundle?.manuscript_proposal) items.push({ ...bundle.manuscript_proposal, kind: 'background', payload: bundle.manuscript_proposal });
  return items.map((draft) => ({
    id: draft.id, kind: draft.kind || 'lorebook',
    title: draft.title || draft.payload?.title || draft.payload?.comment || (draft.kind === 'core' ? '核心设定候选' : '词条候选'),
    payload: draft.payload || { content: draft.content || '' }, status: draft.status || 'review',
  }));
}

/** Save only the version the player reviewed, including while persisting local edits. */
export async function prepareOrganizeCommit(job, draftIds, approvedIds, operationId, edits, api, options = {}) {
  const selected = job.drafts.filter((draft) => draftIds.includes(draft.id));
  buildOrganizeCommit({ ...job, drafts: job.drafts.map((draft) => ({ ...draft, ...edits[draft.id] })) }, draftIds, approvedIds, operationId, options);
  const current = await api.get(job.id);
  const conflict = () => new Error('任务草稿已改变。你的编辑仍保留，请重新读取结果并核对后保存。');
  if (current.id !== job.id || current.world_id !== job.world_id || current.revision !== job.revision) throw conflict();
  let revision = job.revision;
  const reviewed = new Map(selected.map((draft) => [draft.id, draft]));
  const savedDrafts = [];
  for (const draft of selected) {
    const local = edits[draft.id];
    if (!local) continue;
    if (local.revision !== draft.revision) throw conflict();
    const saved = await api.edit(job.id, draft.id, local.revision, local.payload, local.action, local.target_uid);
    reviewed.set(draft.id, saved);
    savedDrafts.push(saved);
    revision += 1;
    options.onSaved?.(saved);
  }
  const latest = savedDrafts.length ? await api.get(job.id) : current;
  if (latest.id !== job.id || latest.world_id !== job.world_id || latest.revision !== revision || latest.target_revision !== job.target_revision) throw conflict();
  for (const expected of reviewed.values()) {
    const found = latest.drafts.find((draft) => draft.id === expected.id);
    if (!found || found.status !== 'review' || found.revision !== expected.revision) throw conflict();
  }
  return { job: latest, body: buildOrganizeCommit(latest, draftIds, approvedIds, operationId, options), savedDrafts };
}

export function hasPrivateOrganizeSources(snapshot) {
  return !!snapshot?.sources?.some((source) => source.audience === 'author' || source.visibility === 'private');
}

export function organizeCompletedBatches(job) {
  const completed = job?.completed_batches;
  return Array.isArray(completed) ? completed.length : typeof completed === 'number' ? completed : job?.progress?.completed_batches || 0;
}
