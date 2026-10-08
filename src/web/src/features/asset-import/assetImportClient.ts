import { api } from '../../api/client';
import type { Api } from '../../api/Api';
/** Public asset-import endpoint surface. */
export const assetImportClient: Pick<Api, 'getWorld'> = {
  getWorld: api.getWorld,
};
