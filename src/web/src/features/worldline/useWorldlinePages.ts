import { useEffect, useRef, useState } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { queryClient } from '../../queryClient';
import { storyQueries } from '../stories/storyQueries';
/** Pagination owns cursors only. Query owns every response page shared with the sidebar. */
export function useWorldlinePages(storyId: string, search: string) {
  const scope = `${storyId}/${search}`, active = useRef(scope); active.current = scope;
  const [pagination, setPagination] = useState<{ scope: string; cursors: number[] }>({ scope, cursors: [] });
  const cursors = pagination.scope === scope ? pagination.cursors : [];
  const first = useQuery(storyQueries.branches(storyId, search));
  const older = useQueries({ queries: cursors.map(cursor => storyQueries.branches(storyId, search, cursor)) });
  const pages = [first.data, ...older.map(query => query.data)].filter(page => !!page);
  const branches = [...new Map(pages.flatMap(page => page.branches).map(branch => [branch.id, branch])).values()];
  const nextCursor = pages[pages.length - 1]?.next_cursor ?? null;
  useEffect(() => { setPagination({ scope, cursors: [] }); }, [scope]);
  const loadMore = async () => {
    if (!nextCursor) return;
    const ownedScope = scope, cursor = Number(nextCursor);
    await queryClient.fetchQuery(storyQueries.branches(storyId, search, cursor));
    if (active.current === ownedScope) setPagination(previous => ({ scope, cursors: [...new Set([...(previous.scope === scope ? previous.cursors : []), cursor])] }));
  };
  const refresh = async () => { setPagination({ scope, cursors: [] }); return first.refetch(); };
  return { ...first, page: first.data, branches, nextCursor, loadMore, refresh };
}
