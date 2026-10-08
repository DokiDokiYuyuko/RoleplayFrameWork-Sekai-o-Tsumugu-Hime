/** Only an active or unfinished run in this branch belongs beside the input. */
export function composerConversationRun(runs, branchId) {
  const local = runs.filter((run) => !run.session_id || run.session_id === branchId);
  const active = local.find((run) => ['queued', 'running'].includes(run.status));
  if (active) return active;
  const latest = local[local.length - 1];
  return latest && !['completed', 'cancelled'].includes(latest.status) ? latest : null;
}
