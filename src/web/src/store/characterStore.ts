import { isCancelledError } from '@tanstack/react-query';
import { createQueryFacade } from '../features/resources/queryFacade';
import { resourceQueries } from '../features/resources/resourceQueries';
import { queryClient } from '../queryClient';
import { api } from '../api/client';
import type { CharacterAction } from '../api/Api';
import type { CharacterCard, CharacterPatch, CharacterView } from '../types';
import { storyRuntime } from '../features/stories/storyRuntime';

export type { CharacterAction };
let libraryRead: Promise<void> | null = null;

interface CharacterState {
  characters: CharacterView[];
  loadStatus: 'idle' | 'loading' | 'ready' | 'error';
  loadError: string | null;
  load: () => Promise<void>;
  upsert: (character: CharacterView) => void;
  importCharacter: (card: Partial<CharacterCard> & { name: string }) => Promise<CharacterView>;
  uploadMedia: (cid: string, kind: "avatar" | "full-body", file: File) => Promise<void>;
  updateCharacter: (cid: string, patch: CharacterPatch) => Promise<void>;
  /** R50：删除角色（库级；卡片 + 头像 + 该角色记忆） */
  removeCharacter: (cid: string) => Promise<void>;
  /** POST /api/v1/characters/{cid} {action}；force 需传 sessionId 触发发言 */
  action: (cid: string, action: CharacterAction, sessionId?: string) => Promise<void>;
}

export const useCharacterStore = createQueryFacade<CharacterState, CharacterView[]>('characters', resourceQueries.characters(), (set, get) => ({
  characters: [],
  loadStatus: 'idle',
  loadError: null,

  load: () => {
    if (libraryRead) return libraryRead;
    set({ loadStatus: 'loading', loadError: null });
    libraryRead = queryClient.fetchQuery({ ...resourceQueries.characters(), staleTime: 0, retry: false }).then(() => { set({ loadStatus: 'ready' }); })
      .catch((cause) => { if (isCancelledError(cause)) return; set({ loadStatus: 'error', loadError: cause instanceof Error ? cause.message : '角色库读取失败' }); throw cause; })
      .finally(() => { libraryRead = null; });
    return libraryRead;
  },

  upsert: (character) => set(state => ({ characters: state.characters.some(item => item.id === character.id && (item.revision ?? 0) > (character.revision ?? 0)) ? state.characters : [...state.characters.filter(item => item.id !== character.id), character] })),

  importCharacter: async (card) => {
    const character = await api.createCharacter(card);
    get().upsert(character);
    return character;
  },

  updateCharacter: async (cid, patch) => {
    const current = get().characters.find((item) => item.id === cid);
    const updated = await api.updateCharacter(cid, {
      ...patch,
      expected_revision: patch.expected_revision ?? current?.revision,
    });
    get().upsert(updated);
  },

  uploadMedia: async (cid, kind, file) => {
    const current = get().characters.find((item) => item.id === cid);
    if (!current) throw new Error('角色尚未保存，请先保存角色');
    const uploaded = await (kind === 'avatar' ? api.uploadAvatar : api.uploadFullBody)(cid, file, current.revision);
    if (!Number.isInteger(uploaded.revision)) throw new Error('服务需要重启后才能同步图片修订信息');
    set((state) => ({ characters: state.characters.map((item) => item.id === cid && item.revision === current.revision
      ? { ...item, revision: uploaded.revision, card: { ...item.card, avatar_path: uploaded.avatar_path } } : item) }));
  },

  removeCharacter: async (cid) => {
    await api.deleteCharacter(cid);
    set({ characters: get().characters.filter((c) => c.id !== cid) });
  },

  action: async (cid, action, sessionId) => {
    const ownsBranch = sessionId ? storyRuntime.capture(sessionId) : () => false;
    const updated = await api.characterAction(cid, action, sessionId);
    get().upsert(updated);
    if (sessionId && ownsBranch()) {
      storyRuntime.applyCharacter(sessionId, updated);
    }
  },
}));
