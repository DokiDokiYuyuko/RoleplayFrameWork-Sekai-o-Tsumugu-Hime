/** Presentation only: a high-water mark excludes initial reads, branch switches and older history. */
export type StoryMotionWindow = { sessionId: string; ready: boolean; highSeq: number };
export function storyMessageMotion(previous: StoryMotionWindow | null, sessionId: string | null | undefined,
  snapshotSessionId: string | null | undefined, messages: readonly { id: string; seq: number }[]) {
  const currentId = sessionId ?? '';
  const ready = Boolean(currentId && snapshotSessionId === currentId);
  const highSeq = messages.reduce((high, message) => Math.max(high, message.seq), 0);
  const continuing = previous?.sessionId === currentId && previous.ready && ready;
  return {
    window: { sessionId: currentId, ready, highSeq: continuing ? Math.max(previous.highSeq, highSeq) : highSeq },
    arriving: continuing ? messages.filter(message => message.seq > previous.highSeq).map(message => message.id) : [],
  };
}
