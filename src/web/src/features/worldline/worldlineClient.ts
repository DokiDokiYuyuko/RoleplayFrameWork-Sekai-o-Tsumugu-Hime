import { api } from '../../api/client';
import type { Api } from '../../api/Api';
/** Public worldline endpoint surface. */
export const worldlineClient: Pick<Api, 'deleteStoryEvent' | 'forkBranch' | 'getBranchPoint' | 'getSessionSetup' | 'getStoryView' | 'getWorldline' | 'patchBranch' | 'updateStoryEvent'> = {
  deleteStoryEvent: api.deleteStoryEvent,
  forkBranch: api.forkBranch,
  getBranchPoint: api.getBranchPoint,
  getSessionSetup: api.getSessionSetup,
  getStoryView: api.getStoryView,
  getWorldline: api.getWorldline,
  patchBranch: api.patchBranch,
  updateStoryEvent: api.updateStoryEvent,
};
