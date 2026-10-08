import { showCleanupPending } from './storyNotices';
import type { QueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { useChatStore } from '../../store/chatStore';
import { storyRuntime } from './storyRuntime';

async function refresh(queryClient?: QueryClient) {
  await useChatStore.getState().loadSessions().catch(() => undefined);
  if (queryClient) await Promise.allSettled([
    queryClient.invalidateQueries({ queryKey: ['stories'] }),
    queryClient.invalidateQueries({ queryKey: ['story-trash'] }),
    queryClient.invalidateQueries({ queryKey: ['worldline'] }),
  ]);
}

function clearCurrentIfDeleted(ids: string[]) {
  storyRuntime.clearDeletedBranches(ids);
}

export async function deleteStoryAction(storyId: string, queryClient?: QueryClient, membershipRevision?: string | null) {
  const result = await api.deleteStory(storyId, membershipRevision);
  showCleanupPending(result);
  clearCurrentIfDeleted(result.branch_ids);
  await refresh(queryClient);
  return result;
}

export async function deleteBranchAction(branchId: string, queryClient?: QueryClient, membershipRevision?: string | null) {
  const result = await api.deleteBranch(branchId, membershipRevision);
  showCleanupPending(result);
  clearCurrentIfDeleted([branchId]);
  await refresh(queryClient);
  return result;
}

export async function restoreStoryAction(storyId: string, queryClient?: QueryClient, generationId?: string | null) {
  const result = await api.restoreStory(storyId, generationId);
  showCleanupPending(result);
  await refresh(queryClient);
  return result;
}

export async function archiveBranchAction(branchId: string, archived: boolean, queryClient?: QueryClient) {
  const snapshot = await api.getSessionSetup(branchId);
  const result = await api.patchBranch(branchId, {
    archived, expected_revision: snapshot.meta.branch_revision,
  });
  await refresh(queryClient);
  return result;
}

export async function importStoryAction(file: File, queryClient?: QueryClient) {
  const result = await api.importStory(file);
  await refresh(queryClient);
  return result;
}
