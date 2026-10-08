import { STORY_EVENT_TYPES } from './generated/story-contracts';
import { decodeStoryEvent } from '../features/stories/storyEvents';
// Validate the generated event vocabulary and project wire payloads before delivery.
import type { EventSourceLike, SseEvent } from '../types';
import { probeLanSessionAfterSseClose } from './lanSession';

/** 后端事件名 → 前端 SseEvent.type（同名，列出以便与 SseEvent 联合类型对照） */
const RELAYED = STORY_EVENT_TYPES;

export type ConnectionStatus = 'open' | 'reconnecting' | 'closed';

class RealSSE implements EventSourceLike {
  private es: EventSource;
  private listeners = new Map<string, Set<(ev: { data: string }) => void>>();
  readyState = 0;

  constructor(
    sessionId: string,
    after: string | null,
    onConnectionChange?: (status: ConnectionStatus) => void,
    onResync?: () => void,
  ) {
    const url = `/api/v1/sessions/${encodeURIComponent(sessionId)}/events${after ? `?after=${encodeURIComponent(after)}` : ''}`;
    this.es = new EventSource(url);
    this.es.onopen = () => {
      this.readyState = 1;
      onConnectionChange?.('open');
    };
    this.es.onerror = () => {
      this.readyState = this.es.readyState;
      onConnectionChange?.(this.es.readyState === EventSource.CLOSED ? 'closed' : 'reconnecting');
      if (this.es.readyState === EventSource.CLOSED) probeLanSessionAfterSseClose();
    };

    let resyncRequested = false;
    const malformed = () => { if (!resyncRequested) { resyncRequested = true; onResync?.(); } };
    RELAYED.forEach((type) => {
      this.es.addEventListener(type, (ev: MessageEvent) => {
        let payload: unknown;
        try {
          payload = JSON.parse(ev.data);
        } catch {
          malformed(); return;
        }
        const event = decodeStoryEvent(type, payload);
        if (!event) { malformed(); return; }
        const data = JSON.stringify(event);
        this.listeners.get(type)?.forEach((fn) => fn({ data }));
      });
    });

    this.es.addEventListener('heartbeat', () => {
      const data = JSON.stringify({ type: 'heartbeat', ts: Date.now() } as SseEvent);
      this.listeners.get('heartbeat')?.forEach((fn) => fn({ data }));
    });
    this.es.addEventListener('stream.resync', () => onResync?.());
  }

  addEventListener(type: string, listener: (ev: { data: string }) => void): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }

  removeEventListener(type: string, listener: (ev: { data: string }) => void): void {
    this.listeners.get(type)?.delete(listener);
  }

  close(): void {
    this.es.close();
    this.listeners.clear();
  }
}

export const connectSSE = (
  sessionId: string,
  after: string | null,
  onConnectionChange?: (status: ConnectionStatus) => void,
  onResync?: () => void,
): EventSourceLike => new RealSSE(sessionId, after, onConnectionChange, onResync);
