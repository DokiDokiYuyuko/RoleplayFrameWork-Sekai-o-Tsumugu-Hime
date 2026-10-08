import type { EventSourceLike, SseEvent } from '../../types';
import type { ConnectionStatus } from '../../api/sse';

type SourceFactory = (branchId: string, after: string | null, status: (value: ConnectionStatus) => void, resync: () => void) => EventSourceLike;
/** A replaced connection loses callback ownership immediately, including an A → B → A switch. */
export function createStoryConnection(factory: SourceFactory, eventTypes: readonly string[]) {
  let source: EventSourceLike | null = null;
  let epoch = 0;
  const close = () => { ++epoch; const previous = source; source = null; previous?.close(); };
  return {
    close,
    isConnected: () => source !== null,
    connect(branchId: string, after: string | null, event: (value: SseEvent) => void,
      status: (value: ConnectionStatus) => void, resync: () => void) {
      close();
      const ownedEpoch = epoch;
      const active = () => ownedEpoch === epoch;
      source = factory(branchId, after, value => { if (active()) status(value); }, () => { if (active()) resync(); });
      for (const type of eventTypes) source.addEventListener(type, data => {
        if (!active()) return;
        let value: SseEvent;
        try { value = JSON.parse(data.data) as SseEvent; } catch { return; }
        event(value);
      });
    },
  };
}
