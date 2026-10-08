import { storiesClient } from '../stories/storiesClient';

/** The browse detail and preview share the same remote package owner. */
export const scenarioQueries = {
  detail: (id: string) => ({ queryKey: ['scenario-package', id] as const, queryFn: () => storiesClient.getScenario(id), enabled: Boolean(id) }),
};
