import { jsonRequest, type ReadOptions } from '../../api/request';
import type { ArchiveInput, LorebookEntry, Lorebook } from '../../types';

export interface OrganizeInput {
  category: 'archives' | 'lorebook'; source_text: string; instruction: string;
  reference_source_ids: string[]; source_visibility: 'public' | 'private';
  target_archive_id: string | null; target_lorebook_id: string | null; new_lorebook_name: string;
}
export interface OrganizeDraft {
  id: string; revision: number; status: 'review' | 'committed'; kind: 'background' | 'biology' | 'lorebook';
  action: 'add' | 'replace'; target_id?: string; target_uid?: number;
  payload: Record<string, unknown>; source_refs: { source_id?: string; quote?: string }[];
  before_payload?: Record<string, unknown>; errors?: string[];
  runtime_scope?: 'author' | 'shared';
}
export interface OrganizeJobSummary {
  id: string; world_id: string; category: 'archives' | 'lorebook';
  status: 'queued' | 'running' | 'review' | 'failed' | 'interrupted' | 'cancelled' | 'committed';
  stage?: string; title?: string; created_at: string; updated_at: string;
}
export interface OrganizeJob extends OrganizeJobSummary, OrganizeInput {
  revision: number; drafts: OrganizeDraft[]; errors: string[]; source_changed: boolean;
  model?: string; provider?: string; gateway?: string; thinking?: string;
  context_limit_override?: number | null; generation?: { max_output_tokens?: number | null };
  target_revision?: number | null; progress?: { completed_batches?: number; total_batches?: number; tool_calls?: number; last_tool?: string | null };
  batch_total?: number; completed_batches?: number[] | number;
  context_limit?: number | null; input_limit?: number | null; capacity_source?: string;
}
export interface OrganizeSourceSnapshot {
  source_text: string; instruction: string; source_changed: boolean;
  sources: { id: string; title: string; content: string; audience: 'author' | 'shared'; visibility?: string }[];
  target_archive?: ArchiveInput | null; target_lorebook?: Lorebook | null;
}
export interface OrganizeCommitInput {
  operation_id: string; expected_revision: number; draft_ids: string[];
  expected_lorebook_revision?: number; approved_replace_draft_ids: string[]; accept_source_changes: boolean;
}
const base = '/api/v1/world-organize-jobs';
const path = (id: string) => `${base}/${encodeURIComponent(id)}`;
const post = <T,>(url: string, input: unknown) => jsonRequest<T>(url, { method: 'POST', body: JSON.stringify(input) });
export const worldOrganizeApi = {
  list: (worldId: string, options?: ReadOptions) => jsonRequest<OrganizeJobSummary[]>(`${base}?world_id=${encodeURIComponent(worldId)}`, options),
  get: (id: string, options?: ReadOptions) => jsonRequest<OrganizeJob>(path(id), options),
  source: (id: string, options?: ReadOptions) => jsonRequest<OrganizeSourceSnapshot>(`${path(id)}/source`, options),
  create: (worldId: string, input: OrganizeInput) => post<OrganizeJob>(base, { ...input, world_id: worldId }),
  edit: (jobId: string, draftId: string, expected_revision: number, payload: Partial<ArchiveInput> | Partial<LorebookEntry> | Record<string, unknown>, action?: 'add' | 'replace', target_uid?: number) =>
    jsonRequest<OrganizeDraft>(`${path(jobId)}/drafts/${encodeURIComponent(draftId)}`, { method: 'PATCH', body: JSON.stringify({ expected_revision, payload, action, target_uid }) }),
  resume: (id: string) => post<OrganizeJob>(`${path(id)}/resume`, {}),
  cancel: (id: string) => post<OrganizeJob>(`${path(id)}/cancel`, {}),
  commit: (id: string, input: OrganizeCommitInput) => post<OrganizeJob>(`${path(id)}/commit-batch`, input),
};

export const legacyWorldApi = {
  listImports: <T,>(options?: ReadOptions) => jsonRequest<T>('/api/v1/asset-import-jobs', options),
  importJob: <T,>(id: string, options?: ReadOptions) => jsonRequest<T>(`/api/v1/asset-import-jobs/${encodeURIComponent(id)}`, options),
  source: (id: string) => jsonRequest<{ source: string }>(`/api/v1/asset-import-jobs/${encodeURIComponent(id)}/source`),
};
