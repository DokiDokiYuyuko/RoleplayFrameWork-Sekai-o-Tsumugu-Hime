export interface LorebookJobToken { epoch: number; jobId: string | null; }

/** A response belongs to one viewing/command epoch, even when a task is selected again. */
export function createLorebookJobOwner() {
  let current: LorebookJobToken = { epoch: 0, jobId: null };
  return {
    begin(jobId: string | null): LorebookJobToken { current = { epoch: current.epoch + 1, jobId }; return { ...current }; },
    capture(): LorebookJobToken { return { ...current }; },
    isCurrent(token: LorebookJobToken, responseJobId?: string) {
      return token.epoch === current.epoch && token.jobId === current.jobId && (responseJobId === undefined || responseJobId === current.jobId);
    },
    bind(token: LorebookJobToken, jobId: string): LorebookJobToken | null {
      if (token.epoch !== current.epoch || token.jobId !== current.jobId) return null;
      current = { epoch: current.epoch, jobId }; return { ...current };
    },
    invalidate() { current = { epoch: current.epoch + 1, jobId: null }; },
  };
}
