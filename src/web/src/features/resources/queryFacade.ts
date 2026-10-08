import { useSyncExternalStore } from 'react';
import { queryClient } from '../../queryClient';
import { useQuery, type FetchQueryOptions } from '@tanstack/react-query';

/** Compatibility hook: remote values live only in Query; the closure owns local UI fields/actions. */
export function createQueryFacade<S extends object, D>(field: keyof S, options: FetchQueryOptions<D> | ((state: S) => FetchQueryOptions<D>),
  initialize: (set: (patch: Partial<S> | ((state: S) => Partial<S>)) => void, get: () => S) => S) {
  let local: Partial<S> = {}, snapshot: S;
  const currentOptions = () => typeof options === 'function' ? options(snapshot ?? defaults) : options;
  const listeners = new Set<() => void>();
  const refresh = () => {
    const query = queryClient.getQueryState<D>(currentOptions().queryKey);
    const next = { ...local, [field]: query?.data ?? defaults[field],
      ...('loadStatus' in defaults ? { loadStatus: query?.fetchStatus === 'fetching' ? 'loading'
        : query?.status === 'error' ? 'error' : query?.status === 'success' ? 'ready' : 'idle',
        loadError: query?.error instanceof Error ? query.error.message : null } : {}) } as S;
    if (snapshot && Object.keys(next).every(key => Object.is(next[key as keyof S], snapshot[key as keyof S]))) return;
    snapshot = next;
    for (const listener of listeners) listener();
  };
  const get = () => snapshot;
  const set = (input: Partial<S> | ((state: S) => Partial<S>)) => {
    const patch = typeof input === 'function' ? input(snapshot) : input;
    const rest = { ...patch };
    const remote = Object.prototype.hasOwnProperty.call(rest, field) ? rest[field] : undefined;
    const hasRemote = Object.prototype.hasOwnProperty.call(rest, field);
    delete rest[field];
    local = { ...local, ...rest };
    refresh();
    if (hasRemote) {
      if (queryClient.getQueryState(currentOptions().queryKey)?.fetchStatus === 'fetching') void queryClient.cancelQueries({ queryKey: currentOptions().queryKey, exact: true });
      queryClient.setQueryData(currentOptions().queryKey, remote);
    }
    refresh();
  };
  const defaults = initialize(set, get);
  local = { ...defaults }; delete local[field];
  refresh();
  queryClient.getQueryCache().subscribe(event => {
    if (!['added', 'updated', 'removed'].includes(event.type)) return;
    if (JSON.stringify(event.query.queryKey) === JSON.stringify(currentOptions().queryKey)) refresh();
  });
  const hook = <T = S>(selector: (state: S) => T = state => state as unknown as T): T => {
    const state = useSyncExternalStore(listener => { listeners.add(listener); return () => { listeners.delete(listener); }; }, get, get);
    // An actual observer gives invalidation/refocus/GC the same ownership semantics as direct useQuery consumers.
    useQuery({ ...currentOptions(), enabled: !currentOptions().queryKey.some(part => part === '') });
    return selector(state);
  };
  return Object.assign(hook, { getState: get });
}
