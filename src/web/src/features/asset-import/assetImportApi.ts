import { jsonRequest, type ReadOptions } from '../../api/request';
export type ImportKind = "world" | "background" | "biology" | "character";

export interface ImportDraft {
  id: string;
  kind: ImportKind;
  title: string;
  payload: Record<string, unknown>;
  status: "needs_review" | "failed" | "committing" | "saved";
  error: string;
  warnings?: string[];
  evidence: string;
  source_start: number | null;
  source_end: number | null;
  schema_fingerprint: string;
  schema_changed?: boolean;
  world_id: string | null;
  target_asset_id: string | null;
  target_revision: number | null;
  revision: number;
  saved_asset_id: string | null;
  source_exact?: boolean;
  selected_fields?: string[] | null;
  proposal?: { payload: Record<string, unknown>; error: string; evidence: string; warnings?: string[] };
}

export interface ImportJob {
  character_seed?: Partial<import('../../types').CharacterCard>;
  character_aliases?: string[];
  id: string;
  title: string;
  status: string;
  interrupted_phase?: string;
  regenerate_draft_id?: string;
  target_kind: ImportKind | null;
  world_id: string | null;
  source_length: number;
  candidates: { kind: ImportKind; title: string; excerpt: string }[];
  drafts: ImportDraft[];
  errors: string[];
  created_at: string;
  updated_at: string;
}

const base = "/api/v1/asset-import-jobs";
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return jsonRequest<T>(`${base}${path}`, init);
}

export const assetImportApi = {
  list: (options?: ReadOptions) => request<Pick<ImportJob, "id" | "title" | "status" | "updated_at">[]>("", options),
  get: (id: string, options?: ReadOptions) => request<ImportJob>(`/${encodeURIComponent(id)}`, options),
  source: (id: string, options?: ReadOptions) => request<{ source: string }>(`/${encodeURIComponent(id)}/source`, options),
  create: (source: string, target_kind: ImportKind | null, world_id: string | null) =>
    request<ImportJob>("", { method: "POST", body: JSON.stringify({ source, target_kind, world_id }) }),
  generate: (id: string) => request<ImportJob>(`/${encodeURIComponent(id)}/generate`, { method: "POST" }),
  recoverWorldSource: (id: string) => request<ImportJob>(`/${encodeURIComponent(id)}/recover-world-source`, { method: "POST" }),
  analyze: (id: string) => request<ImportJob>(`/${encodeURIComponent(id)}/analyze`, { method: "POST" }),
  updateCandidates: (id: string, candidates: ImportJob["candidates"]) =>
    request<ImportJob>(`/${encodeURIComponent(id)}/candidates`, { method: "PATCH", body: JSON.stringify({ candidates }) }),
  edit: (jobId: string, draft: ImportDraft, payload: Record<string, unknown>, world_id: string | null,
         target_asset_id: string | null, target_revision: number | null, selected_fields?: string[] | null) =>
    request<ImportDraft>(`/${encodeURIComponent(jobId)}/drafts/${encodeURIComponent(draft.id)}`, {
      method: "PATCH", body: JSON.stringify({ payload, world_id, target_asset_id, target_revision, expected_revision: draft.revision, selected_fields }),
    }),
  commit: (jobId: string, draftId: string) =>
    request<ImportDraft>(`/${encodeURIComponent(jobId)}/drafts/${encodeURIComponent(draftId)}/commit`, { method: "POST" }),
  regenerate: (jobId: string, draftId: string) =>
    request<ImportJob>(`/${encodeURIComponent(jobId)}/drafts/${encodeURIComponent(draftId)}/regenerate`, { method: "POST" }),
  remove: (jobId: string) => request<{ ok: boolean }>(`/${encodeURIComponent(jobId)}`, { method: "DELETE" }),
};
