export interface ImportInputSnapshot {
  source: string;
  target: string;
  worldId: string;
}

export function sameImportInput(left: ImportInputSnapshot, right: ImportInputSnapshot) {
  return left.source === right.source && left.target === right.target && left.worldId === right.worldId;
}

const statuses: Record<string, string> = {
  ready:'待生成', analyzing:'识别中', classified:'待确认候选', generating:'生成中',
  regenerating:'重新生成中', review:'待审阅', needs_review:'待审阅', completed:'已完成',
  committed:'已保存', saved:'已保存', failed:'需修正', interrupted:'已中断', cancelled:'已取消',
};
export function importStatusLabel(status: string) { return statuses[status] ?? '待处理'; }

/** The existing commit contract creates revision 1 or advances the frozen target once. */
export function committedImportRevision(draft: { target_asset_id: string | null; target_revision: number | null }) {
  if (!draft.target_asset_id) return 1;
  if (!Number.isInteger(draft.target_revision) || (draft.target_revision ?? 0) < 1) {
    throw new Error('无法确认目标角色的提交修订，请重新审阅。');
  }
  return draft.target_revision! + 1;
}

const aliasKey = (jobId: string, draftId: string) => `mrp.asset-import.review-aliases.${jobId}:${draftId}`;
export function readImportReviewAliases(storage: Pick<Storage, 'getItem'>, jobId: string, draftId: string, revision: number, fallback: string[], targetAssetId = '') {
  try {
    const value = JSON.parse(storage.getItem(aliasKey(jobId, draftId)) || 'null');
    if (value?.revision === revision && (value.targetAssetId ?? '') === targetAssetId && Array.isArray(value.aliases) && value.aliases.every((item: unknown) => typeof item === 'string')) return value.aliases as string[];
  } catch { /* The server draft and the local editor remain usable without this adjunct. */ }
  return fallback;
}
export function saveImportReviewAliases(storage: Pick<Storage, 'setItem'>, jobId: string, draftId: string, revision: number, aliases: string[], targetAssetId = '') {
  storage.setItem(aliasKey(jobId, draftId), JSON.stringify({ revision, aliases, targetAssetId }));
}
export function clearImportReviewAliases(storage: Pick<Storage, 'removeItem'>, jobId: string, draftId: string) {
  storage.removeItem(aliasKey(jobId, draftId));
}
