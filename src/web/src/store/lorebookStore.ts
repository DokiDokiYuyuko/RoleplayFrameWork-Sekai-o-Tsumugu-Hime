import { isCancelledError } from '@tanstack/react-query';
import { createQueryFacade } from '../features/resources/queryFacade';
import { resourceQueries } from '../features/resources/resourceQueries';
import { queryClient } from '../queryClient';
import { api } from '../api/client';
import type { Lorebook, LorebookEntry } from '../types';
let libraryRead: Promise<void> | null = null;

interface LorebookState {
  books: Lorebook[];
  loadStatus: 'idle' | 'loading' | 'ready' | 'error';
  loadError: string | null;
  currentBookId: string | null;
  load: () => Promise<void>;
  selectBook: (id: string) => void;
  createBook: (input: { name: string; description?: string; tags?: string[] }) => Promise<Lorebook>;
  updateBook: (bookId: string, patch: { name?: string; description?: string; tags?: string[] }) => Promise<void>;
  copyBook: (bookId: string, input: { name: string; description?: string; tags?: string[] }) => Promise<Lorebook>;
  deleteBook: (bookId: string) => Promise<void>;
  /** 条目增删改在页面本地完成后，整本 PATCH 回后端 */
  updateEntries: (bookId: string, entries: LorebookEntry[]) => Promise<void>;
}

export const useLorebookStore = createQueryFacade<LorebookState, Lorebook[]>('books', resourceQueries.lorebooks(), (set, get) => ({
  books: [],
  loadStatus: 'idle',
  loadError: null,
  currentBookId: null,

  load: () => {
    if (libraryRead) return libraryRead;
    set({ loadStatus: 'loading', loadError: null });
    libraryRead = queryClient.fetchQuery({ ...resourceQueries.lorebooks(), staleTime: 0, retry: false }).then((books) => {
      set((s) => ({ currentBookId: s.currentBookId ?? books[0]?.id ?? null, loadStatus: 'ready' }));
    }).catch((cause) => { if (isCancelledError(cause)) return; set({ loadStatus: 'error', loadError: cause instanceof Error ? cause.message : '世界书读取失败' }); throw cause; })
      .finally(() => { libraryRead = null; });
    return libraryRead;
  },

  selectBook: (id) => set({ currentBookId: id }),

  createBook: async (input) => {
    const book = await api.createBlankLorebook(input);
    set((s) => ({ books: [...s.books, book], currentBookId: book.id }));
    return book;
  },

  updateBook: async (bookId, patch) => {
    const current = get().books.find((item) => item.id === bookId);
    const updated = await api.updateLorebook(bookId, {
      ...patch,
      expected_revision: current?.revision,
    });
    set((s) => ({ books: s.books.map((b) => (b.id === bookId && (updated.revision ?? 0) >= (b.revision ?? 0) ? updated : b)) }));
  },

  copyBook: async (bookId, input) => {
    const book = await api.copyLorebook(bookId, input);
    set((s) => ({ books: [...s.books, book], currentBookId: book.id }));
    return book;
  },

  deleteBook: async (bookId) => {
    await api.deleteLorebook(bookId);
    set((s) => {
      const books = s.books.filter((b) => b.id !== bookId);
      return {
        books,
        currentBookId: s.currentBookId === bookId ? books[0]?.id ?? null : s.currentBookId,
      };
    });
  },

  updateEntries: async (bookId, entries) => {
    const current = get().books.find((item) => item.id === bookId);
    const updated = await api.updateLorebook(bookId, { entries, expected_revision: current?.revision });
    set({ books: get().books.map((b) => (b.id === bookId && (updated.revision ?? 0) >= (b.revision ?? 0) ? updated : b)) });
  },
}));
