import { queryClient } from '../../queryClient';
import type { Settings } from '../../types';
const key = ['settings'] as const;
let writes: Promise<void> = Promise.resolve();
/** All settings writers share ordering; an older GET cannot publish across a write. */
export function readSettings(read: () => Promise<Settings>): Promise<Settings> {
  return writes.then(read);
}
export function writeSettings(write: () => Promise<Settings>): Promise<Settings> {
  const operation = writes.then(async () => {
    await queryClient.cancelQueries({ queryKey: key, exact: true });
    const settings = await write();
    await queryClient.cancelQueries({ queryKey: key, exact: true });
    queryClient.setQueryData(key, settings);
    void queryClient.invalidateQueries({ queryKey: ['gateway-models'] });
    void queryClient.invalidateQueries({ queryKey: ['model-providers'] });
    return settings;
  });
  writes = operation.then(() => undefined, () => undefined);
  return operation;
}
