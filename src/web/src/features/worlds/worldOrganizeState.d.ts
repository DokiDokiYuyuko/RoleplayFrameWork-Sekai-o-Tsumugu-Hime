import type { OrganizeInput, OrganizeDraft, OrganizeJob, OrganizeCommitInput } from './worldOrganizeApi';
export function initialOrganizeInput(search?: string): OrganizeInput;
export function organizeCandidates(drafts: OrganizeDraft[], kind: OrganizeDraft['kind']): OrganizeDraft[];
export function toggleOrganizeReference(ids: string[], id: string): string[];
export function buildOrganizeCommit(job: OrganizeJob, draftIds: string[], approvedIds: string[], operationId: string, options?: { acceptSourceChanges?: boolean }): OrganizeCommitInput;
export function createOrganizeScope(worldId: string): { isCurrent: (worldId: string) => boolean; dispose: () => void };
export function readLegacyCandidates(job: { drafts?: unknown[]; bundle?: unknown }): { id: string; kind: string; title: string; payload: Record<string, unknown>; status: string }[];

export interface OrganizeDraftEdit { revision: number; payload: Record<string, unknown>; action: 'add' | 'replace'; target_uid?: number }
export function prepareOrganizeCommit(job: OrganizeJob, draftIds: string[], approvedIds: string[], operationId: string, edits: Record<string, OrganizeDraftEdit>, api: Pick<typeof import('./worldOrganizeApi').worldOrganizeApi, 'get' | 'edit'>, options?: { acceptSourceChanges?: boolean; onSaved?: (draft: OrganizeDraft) => void }): Promise<{ job: OrganizeJob; body: OrganizeCommitInput; savedDrafts: OrganizeDraft[] }>;
export function hasPrivateOrganizeSources(snapshot?: { sources?: { audience?: string; visibility?: string }[] } | null): boolean;

export function organizeCompletedBatches(job?: Pick<OrganizeJob, "completed_batches" | "progress">): number;
