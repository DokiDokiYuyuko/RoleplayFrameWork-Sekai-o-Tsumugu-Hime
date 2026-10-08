export interface TagBatchItem { id: string; name: string; revision: number; tags: string[] }
export interface TagBatchResult { id: string; name: string; ok: boolean; error?: string }
/** Each write carries the selected snapshot revision; a failure cannot hide other results. */
export async function applyTagBatch(items: readonly TagBatchItem[], tag: string, remove: boolean,
  write: (id: string, tags: string[], expectedRevision: number) => Promise<void>): Promise<TagBatchResult[]> {
  const value = tag.trim();
  if (!value || value.length > 120) throw new Error('请填写 120 字以内的标签。');
  const results: TagBatchResult[] = [];
  for (const item of items) {
    const tags = remove ? item.tags.filter((current) => current !== value) : [...new Set([...item.tags, value])];
    try {
      if (tags.length > 30) throw new Error('标签超过 30 个，请先精简标签。');
      await write(item.id, tags, item.revision);
      results.push({ id: item.id, name: item.name, ok: true });
    } catch (cause) { results.push({ id: item.id, name: item.name, ok: false, error: cause instanceof Error ? cause.message : '修改失败' }); }
  }
  return results;
}
