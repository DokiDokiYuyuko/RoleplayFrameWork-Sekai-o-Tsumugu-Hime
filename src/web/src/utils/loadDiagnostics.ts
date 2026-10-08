export type LoadStage = 'module' | 'request' | 'state' | 'render';
export type LoadOutcome = 'ok' | 'error' | 'cancelled' | 'timeout';
export interface LoadRecord { stage: LoadStage; label: string; duration_ms: number; outcome: LoadOutcome }
const records: LoadRecord[] = [];

/** Only predefined labels/path templates are retained. Bodies and query values never enter diagnostics. */
export function safeLoadLabel(path: string): string {
  const clean = path.split('?')[0].split('#')[0];
  if (!clean.startsWith('/')) return 'page';
  return clean.replace(/\b(?:sess|char|chat|world|book|group|msg|job|draft|scenario|save)-[a-zA-Z0-9_-]+/g, ':id');
}
export function beginLoad(stage: LoadStage, label: string): (outcome?: LoadOutcome) => void {
  const start = performance.now();
  let ended = false;
  return (outcome = 'ok') => {
    if (ended) return;
    ended = true;
    records.push({ stage, label: safeLoadLabel(label), duration_ms: Math.round(performance.now() - start), outcome });
    if (records.length > 80) records.shift();
  };
}
export function getLoadDiagnostics(): LoadRecord[] { return records.map((item) => ({ ...item })); }
