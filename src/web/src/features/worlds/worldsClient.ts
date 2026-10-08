import { api } from '../../api/client';
import type { Api } from '../../api/Api';
/** Public worlds endpoint surface. */
export const worldsClient: Pick<Api, 'createArchive' | 'createWorld' | 'deleteArchive' | 'getLorebookAgentJob' | 'getLorebookAgentSources' | 'getWorld' | 'importBiology' | 'importWorld' | 'linkWorldLorebook' | 'listLorebookAgentJobs' | 'listWorldCovers' | 'listWorldOwners' | 'listWorlds' | 'patchArchive' | 'patchWorld' | 'unlinkWorldLorebook'> = {
  createArchive: api.createArchive,
  createWorld: api.createWorld,
  deleteArchive: api.deleteArchive,
  getLorebookAgentJob: api.getLorebookAgentJob,
  getLorebookAgentSources: api.getLorebookAgentSources,
  getWorld: api.getWorld,
  importBiology: api.importBiology,
  importWorld: api.importWorld,
  linkWorldLorebook: api.linkWorldLorebook,
  listLorebookAgentJobs: api.listLorebookAgentJobs,
  listWorldCovers: api.listWorldCovers,
  listWorldOwners: api.listWorldOwners,
  listWorlds: api.listWorlds,
  patchArchive: api.patchArchive,
  patchWorld: api.patchWorld,
  unlinkWorldLorebook: api.unlinkWorldLorebook,
};
