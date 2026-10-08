import { api } from '../../api/client';
import { queryClient } from '../../queryClient';

/** Query owns remote summaries. Installing a summary never advances the detail commit cursor. */
export const storyQueries = {
  stories: () => ({ queryKey: ['stories'] as const, queryFn: () => api.listStories() }),
  sessions: () => ({ queryKey: ['story-sessions'] as const, queryFn: () => api.listSessions() }),
  branches: (storyId: string, query = '', cursor = 0) => ({ queryKey: cursor ? ['worldline', storyId, query, cursor] as const : ['worldline', storyId, query] as const,
    queryFn: () => api.getWorldline(storyId, cursor, query), enabled: Boolean(storyId) }),
};

export async function loadSessionSummaries() {
  await queryClient.invalidateQueries({ queryKey: ['story-sessions'], refetchType: 'none' });
  const result = await queryClient.fetchQuery(storyQueries.sessions());
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['stories'] }),
    queryClient.invalidateQueries({ queryKey: ['worldline'] }),
  ]);
  return result;
}
