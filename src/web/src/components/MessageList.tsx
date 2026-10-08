import { Button } from '../design-system/Button';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useChatStore } from '../store/chatStore';
import { MessageBubble } from './MessageBubble';
import type { Message } from '../types';
import type { InputGroupEditPart } from '../types';
import { buildPlayerInputGroups } from '../utils/playerInputGroups';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { storiesClient as api } from '../features/stories/storiesClient';
import { storyMessageMotion, type StoryMotionWindow } from '../features/stories/storyMessageMotion';

/** 消息流：按 scene_id 变化插入场景分隔线（R35，纯客户端派生）。 */
export function MessageList({ locatedMessageId, onReturnLatest, onFork, onEvent, onEdit, onEditInputGroup }: {
  locatedMessageId?: string | null;
  onReturnLatest?: () => void;
  onFork?: (message: Message) => Promise<void>;
  onEvent?: (message: Message) => Promise<void>;
  onEdit?: (message: Message, content: string, branchRevision: number, fingerprint: string) => Promise<void>;
  onEditInputGroup?: (messageId: string, parts: InputGroupEditPart[], branchRevision: number) => Promise<void>;
}) {
  const messages = useChatStore((s) => s.messages);
  const nextBeforeSeq = useChatStore(s => s.nextBeforeSeq);
  const historyLoading = useChatStore(s => s.historyLoading);
  const historyError = useChatStore(s => s.historyError);
  const latestSeq = useChatStore(s => s.latestSeq);
  const snapshotSessionId = useChatStore(s => s.snapshotLoadedSessionId);
  const isOldWindow = messages.length > 0 && messages[messages.length - 1].seq < latestSeq;
  useEffect(() => {
    if (locatedMessageId && !messages.some(message => message.id === locatedMessageId)) {
      void useChatStore.getState().locateMessage(locatedMessageId);
    }
  }, [locatedMessageId, locatedMessageId ? messages[0]?.session_id : null]);
  const session = useChatStore((s) => s.sessions.find((item) => item.id === s.currentSessionId));
  const storyId = session?.story_id ?? session?.id ?? '';
  const queryClient = useQueryClient();
  const { data: bookmarks = [] } = useQuery({ queryKey: ['story-bookmarks', storyId],
    queryFn: () => api.listStoryBookmarks(storyId), enabled: Boolean(storyId) });
  const toggleBookmark = async (message: Message) => {
    const current = bookmarks.find((item) => item.branch_id === session?.id && item.message_id === message.id);
    if (current) await api.deleteBookmark(current.branch_id, current.id);
    else if (session) await api.createBookmark(session.id, message.id);
    await queryClient.invalidateQueries({ queryKey: ['story-bookmarks', storyId] });
  };
  const pageSize = 100;
  const [rangeStart, setRangeStart] = useState<number | null>(null);
  const start = rangeStart === null ? Math.max(0, messages.length - pageSize) : Math.min(rangeStart, Math.max(0, messages.length - 1));
  const end = Math.min(messages.length, start + pageSize);
  const listRef = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const motionWindow = useRef<StoryMotionWindow | null>(null);
  const lastLocatedId = useRef<string | null>(null);
  const pendingScroll = useRef<'top' | 'bottom' | null>(null);
  const [awayFromLatest, setAwayFromLatest] = useState(false);

  useLayoutEffect(() => {
    const next = storyMessageMotion(motionWindow.current, session?.id, snapshotSessionId, messages);
    motionWindow.current = next.window;
    // Leave the attribute in place across token updates. A remounted historical page has no arrival marker.
    for (const id of next.arriving) {
      const item = document.getElementById(`message-${id}`);
      if (item && listRef.current?.contains(item)) item.dataset.storyArrival = 'new';
    }
  }, [messages, session?.id, snapshotSessionId]);

  useLayoutEffect(() => {
    setRangeStart(null); lastLocatedId.current = null; nearBottom.current = true;
    pendingScroll.current = null; setAwayFromLatest(false);
  }, [session?.id]);
  useLayoutEffect(() => {
    if (pendingScroll.current && listRef.current) {
      listRef.current.scrollTop = pendingScroll.current === 'bottom' ? listRef.current.scrollHeight : 0;
      pendingScroll.current = null;
      lastLocatedId.current = locatedMessageId ?? null;
      return;
    }
    if (locatedMessageId && locatedMessageId !== lastLocatedId.current) {
      const index = messages.findIndex((message) => message.id === locatedMessageId);
      if (index >= 0 && (index < start || index >= end)) { setRangeStart(Math.floor(index / pageSize) * pageSize); return; }
      document.getElementById(`message-${locatedMessageId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      nearBottom.current = false;
    } else if (nearBottom.current && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
    lastLocatedId.current = locatedMessageId ?? null;
  }, [messages, locatedMessageId, rangeStart, start, end]);

  // Follow layout changes only while reading the latest response.
  // Located messages and older history keep their current reading position.
  useEffect(() => {
    const element = listRef.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      if (nearBottom.current && !locatedMessageId && rangeStart === null && !isOldWindow) {
        element.scrollTop = element.scrollHeight;
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [locatedMessageId, rangeStart, isOldWindow, session?.id]);

  const browsePage = (nextStart: number) => {
    nearBottom.current = false; setAwayFromLatest(true);
    lastLocatedId.current = locatedMessageId ?? null;
    pendingScroll.current = 'top'; setRangeStart(nextStart);
  };
  const returnToLatest = () => {
    nearBottom.current = true; setAwayFromLatest(false);
    pendingScroll.current = rangeStart === null ? null : 'bottom'; setRangeStart(null);
    void useChatStore.getState().locateMessage(null);
    onReturnLatest?.();
    // This also works when the latest page is already selected and no render occurs.
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  };

  // 场景分组：每个 scene_id 首次出现处插分隔线（无 scene_id 的旧消息不插）
  const groups = useMemo(() => {
    const out: { key: string; divider: boolean; message: Message }[] = [];
    const seen = new Set<string>();
    for (const m of messages) {
      const divider = m.scene_id != null && !seen.has(m.scene_id);
      if (m.scene_id != null) seen.add(m.scene_id);
      out.push({ key: m.id, divider, message: m });
    }
    return out;
  }, [messages]);
  const bookmarkedIds = useMemo(() => new Set(bookmarks.filter((b) => b.branch_id === session?.id).map((b) => b.message_id)), [bookmarks, session?.id]);
  const playerInputGroups = useMemo(() => buildPlayerInputGroups(messages), [messages]);

  return (
    <div className="v7-message-stream">
    <div ref={listRef} className="v7-message-list flex-1 space-y-5 overflow-y-auto px-6 py-5" onScroll={(event) => {
      const element = event.currentTarget;
      nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 120;
      setAwayFromLatest(!nearBottom.current);
    }}>
      {(start > 0 || nextBeforeSeq !== null) && <div className="v7-history-boundary">
        <Button variant="ghost" type="button" className="v7-history-link" disabled={historyLoading} onClick={() => {
          if (start > 0) browsePage(Math.max(0, start - pageSize));
          else { nearBottom.current = false; setAwayFromLatest(true); pendingScroll.current = 'top';
            void useChatStore.getState().loadEarlier().then(() => setRangeStart(0)); }
        }}>查看更早消息</Button>
      </div>}
      {historyError && <p role="alert">{historyError}</p>}
      {groups.slice(start, end).map(({ key, divider, message }) => (
        <div key={key} id={`message-${message.id}`} className={`v7-message-item space-y-5 rounded-xl ${locatedMessageId === message.id ? 'ring-2 ring-amber-300' : ''}`}>
          {divider && (
            <div className="story-scene-divider flex items-center gap-3 pt-2">
              <div className="h-px flex-1 bg-slate-200" />
              <span className="rounded-full border border-slate-200 bg-white px-3 py-0.5 text-[10px] text-slate-400">
                场景切换
              </span>
              <div className="h-px flex-1 bg-slate-200" />
            </div>
          )}
          <MessageBubble message={message} logicalInputMessages={playerInputGroups.get(message.id)}
            onFork={onFork} onEvent={onEvent} onEdit={onEdit} onEditInputGroup={onEditInputGroup}
            bookmarked={bookmarkedIds.has(message.id)}
            onBookmark={toggleBookmark} />
        </div>
      ))}
      {messages.length === 0 && (
        <div className="story-workspace-empty flex h-full items-center justify-center text-sm text-slate-400">
          <div><strong>故事的下一页，等你落笔</strong><p>写下回应、内心或旁白，让故事从这里继续。</p></div>
        </div>
      )}
      {end < messages.length && <div className="v7-history-boundary">
        <Button variant="ghost" type="button" className="v7-history-link" onClick={() => browsePage(Math.min(start + pageSize, Math.max(0, messages.length - pageSize)))}>查看后续消息</Button>
      </div>}
    </div>
    {(awayFromLatest || end < messages.length || isOldWindow) && <Button variant="ghost" type="button" className="v7-history-jump" aria-label="回到最新" onClick={returnToLatest}><span aria-hidden="true">↓ </span>回到最新</Button>}
    </div>
  );
}
