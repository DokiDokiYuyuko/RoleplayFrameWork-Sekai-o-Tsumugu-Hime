/** Preview entries can already have negative model-assigned ids; additions must not reuse them. */
export function nextWorkshopPreviewUid(entries) {
  return Math.min(0, ...entries.map(entry => entry.uid)) - 1;
}

export function formatWorkshopElapsed(seconds) {
  const value = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

const agentLabels = {
  queued: '排队中', running: '整理中', regenerating: '重新整理中', review: '待审阅',
  failed: '失败', interrupted: '已中断', cancelled: '已取消', committed: '已保存',
};
export function lorebookAgentStatusLabel(status) { return agentLabels[status] ?? '状态待更新'; }
