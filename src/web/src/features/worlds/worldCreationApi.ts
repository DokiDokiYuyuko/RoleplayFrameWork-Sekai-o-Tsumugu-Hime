import { jsonRequest } from '../../api/request';
import type { Lorebook, World } from '../../types';
export interface RuntimeProposal {
  id: string;
  revision?: number;
  content?: string;
  payload?: Record<string, unknown>;
  runtime_scope?: 'author' | 'shared';
  action?: 'add' | 'replace' | 'disable' | 'keep';
  proposed_action?: 'replace' | 'disable';
  target_uid?: number;
  source_refs?: { source_id?: string; quote?: string }[];
  positive_examples?: string[];
  negative_examples?: string[];
  simulation?: { valid: boolean; mode?: string; note?: string; positive?: { text: string; triggered: boolean; reason?: string }[]; negative?: { text: string; triggered: boolean; reason?: string }[] };
  validation_errors?: string[];
  conflict_reason?: string;
  candidate_targets?: { uid: number; content: string; keys?: string[]; score?: number; reserved?: boolean }[];
  before_payload?: Record<string, unknown>; original_content?: string;
  status?: string;
}
export interface WorldBundle {
  revision: number; review_digest: string; status: string;
  world_draft: Record<string, unknown>;
  core_proposal: RuntimeProposal | null; entry_proposals: RuntimeProposal[];
  manuscript_proposal?: { id: string; title: string; body: string; runtime_scope: 'author' | 'shared' };
  source_snapshot: { id: string; title?: string; audience?: string; visibility?: string; content?: string; revision?: number }[];
  errors?: string[];
  coverage_notes?: string[];
  reading_coverage?: Record<string, { source_id: string; offset: number; char_count: number; read_chars: number; reading_verified: boolean }[]>;
  batch_total?: number; completed_batches?: number;
  target_book_id?: string | null; target_lorebook_revision?: number; child_job_id?: string | null;
}
export interface WorldCreationJob {
  id: string; title?: string; updated_at?: string; status: string; world_id?: string | null; target_kind?: string; intent?: string; errors?: string[];
  bundle?: WorldBundle | null; drafts?: { id: string; kind: string; payload: Record<string, unknown>; addition_text?: string; proposed_source?: string }[];
  bundle_revision?: number;
}
export interface WorldPreview {
  valid: boolean; error?: string; prompt?: string; estimated_tokens?: number;
  input_limit: number | null; sources: { id: string; content: string; included: boolean; reason: string }[];
}
const jobPath = (id: string) => `/api/v1/asset-import-jobs/${encodeURIComponent(id)}`;
const post = <T,>(path: string, value: unknown) => jsonRequest<T>(path, { method: 'POST', body: JSON.stringify(value) });
export const worldCreationApi = {
  saveManuscript: (id: string, revision: number, body: string, visibility: 'public' | 'private') =>
    post<World>(`/api/v1/worlds/${encodeURIComponent(id)}/manuscript`, { expected_revision: revision, body, visibility }),
  preview: (id: string, message: string, character_id?: string) =>
    post<WorldPreview>(`/api/v1/worlds/${encodeURIComponent(id)}/runtime-preview`, { message, character_id }),
  create: (source: string, world_id: string | null, intent: 'organize' | 'extend' = 'organize', instruction = '', source_visibility?: 'public' | 'private') =>
    post<WorldCreationJob>('/api/v1/asset-import-jobs', { source, world_id, target_kind: 'world', intent, instruction, source_visibility }),
  get: (id: string) => jsonRequest<WorldCreationJob>(jobPath(id)),
  list: () => jsonRequest<WorldCreationJob[]>('/api/v1/asset-import-jobs'),
  source: (id: string) => jsonRequest<{ source: string }>(`${jobPath(id)}/source`),
  generate: (id: string) => post<WorldCreationJob>(`${jobPath(id)}/generate`, {}),
  derive: (id: string, expected_bundle_revision: number, source_ids?: string[], target_lorebook_id?: string) =>
    post<WorldCreationJob>(`${jobPath(id)}/derive-world-runtime`, { expected_bundle_revision, source_ids, target_lorebook_id }),
  commit: (id: string, input: Record<string, unknown>) =>
    post<{ world: World; book?: Lorebook; job: WorldCreationJob }>(`${jobPath(id)}/commit-bundle`, input),
  edit: (id: string, input: { expected_bundle_revision: number; core_content?: string; manuscript_body?: string; proposal_id?: string; payload?: Record<string, unknown>; positive_examples?: string[]; negative_examples?: string[]; action?: 'add' | 'replace' | 'disable' | 'keep'; resolve_conflict?: boolean; target_uid?: number }) =>
    jsonRequest<WorldCreationJob>(`${jobPath(id)}/runtime-draft`, { method: 'PATCH', body: JSON.stringify(input) }),
  continue: (id: string) => post<unknown>(`${jobPath(id)}/resume-world-runtime`, {}),
};
