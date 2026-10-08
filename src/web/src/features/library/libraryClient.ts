import { api } from '../../api/client';
import type { Api } from '../../api/Api';

/** Public transport surface for library. UI consumers cannot depend on unrelated endpoints. */
export const libraryClient: Pick<Api, 'createLorebook' | 'extendLorebook' | 'generateLorebook' | 'getCharacter' | 'importBundle' | 'listWorldOwners' | 'listWorlds' | 'personaPreview'> = {
  createLorebook: api.createLorebook,
  extendLorebook: api.extendLorebook,
  generateLorebook: api.generateLorebook,
  getCharacter: api.getCharacter,
  importBundle: api.importBundle,
  listWorldOwners: api.listWorldOwners,
  listWorlds: api.listWorlds,
  personaPreview: api.personaPreview,
};
