import { createQueryFacade } from '../features/resources/queryFacade';
import { resourceQueries } from '../features/resources/resourceQueries';
import { queryClient } from '../queryClient';
import type { CostReport } from '../types';

interface CostState {
  report: CostReport | null;
  sessionId: string | null;
  error: string;
  load: (sessionId: string) => Promise<void>;
}
let requestEpoch = 0;
export const useCostStore = createQueryFacade<CostState, CostReport>('report', state => resourceQueries.cost(state.sessionId ?? ''), (set) => ({
  report: null, sessionId: null, error: '',
  load: async (sessionId) => {
    const epoch = ++requestEpoch;
    if (queryClient.getQueryState(resourceQueries.cost(sessionId).queryKey)?.fetchStatus === 'fetching') void queryClient.cancelQueries({ queryKey: resourceQueries.cost(sessionId).queryKey, exact: true });
    set({ sessionId, error: '' });
    try {
      await queryClient.fetchQuery({ ...resourceQueries.cost(sessionId), staleTime: 0 });
    } catch (cause) {
      if (epoch === requestEpoch) set({ error: cause instanceof Error ? cause.message : '统计读取失败' });
    }
  },
}));
