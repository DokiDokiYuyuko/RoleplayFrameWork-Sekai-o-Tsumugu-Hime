import type { GenerationEventIdentity, Message, SseEvent, TurnRun } from '../../types';
import { projectMessage } from './storyView';
import { removeConfirmedLocalSegment } from '../../utils/optimisticMessages';

export interface GenerationSlot extends GenerationEventIdentity { completed: boolean; retired: ReadonlySet<string> }
export const generationKey = (identity: GenerationEventIdentity) => `${identity.generation_id ?? ''}:${identity.attempt_id ?? ''}`;
/** Pure attempt transition: pending alone can activate a different attempt; retirement prevents ABA. */
export function reduceGenerationAttempt(slot: GenerationSlot | undefined, identity: GenerationEventIdentity,
  kind: 'pending' | 'delta' | 'final' | 'error'): { accepted: boolean; slot?: GenerationSlot } {
  const reject = { accepted: false, slot };
  if (!identity.generation_id) return { accepted: !slot?.generation_id && (!slot?.operation_id || slot.operation_id === identity.operation_id), slot };
  const key = generationKey(identity);
  if (slot?.retired.has(key) || (slot?.operation_id && identity.operation_id && slot.operation_id !== identity.operation_id && !slot.completed)) return reject;
  if (kind === 'pending') {
    if (slot?.generation_id && key === generationKey(slot) && slot.completed) return reject;
    const retired = new Set(slot?.retired);
    if (slot?.generation_id && key !== generationKey(slot)) retired.add(generationKey(slot));
    return { accepted: true, slot: { ...identity, completed: false, retired } };
  }
  if (slot?.generation_id && (key !== generationKey(slot) || slot.completed)) return reject;
  return { accepted: true, slot: { ...(slot ?? { ...identity, retired: new Set<string>() }), completed: kind === 'final' || kind === 'error' } };
}
export function acceptsTurnIdentity(branchId: string | null, runs: TurnRun[], messageId: string,
  identity: GenerationEventIdentity, kind: 'pending' | 'delta' | 'final' | 'error'): boolean {
  const run = identity.operation_id ? runs.find(run => run.operation_id === identity.operation_id) : undefined;
  if (!run) return true;
  if (run.session_id !== branchId) return false;
  const slot = run.slots.find(slot => slot.message_id === messageId);
  if (!slot || (slot.generation_id && identity.generation_id !== slot.generation_id)) return false;
  return kind === 'final' ? slot.status === 'committed' : run.status === 'running' && slot.status === 'pending';
}
export function upsertStoryMessage(messages: Message[], incoming: Message): Message[] {
  return messages.some(message => message.id === incoming.id)
    ? messages.map(message => message.id === incoming.id ? incoming : message)
    : [...messages, incoming].sort((a, b) => a.seq - b.seq);
}
export function commitStoryMessage(messages: Message[], incoming: Message): Message[] {
  return upsertStoryMessage(removeConfirmedLocalSegment(messages, incoming).filter(message => !(
    message.id.startsWith('local-regenerate-') && message.turn === incoming.turn && message.actor === incoming.actor)), projectMessage(incoming));
}
export interface StoryEventState {
  branchId: string | null; revision: number; seenOutboxIds: readonly string[];
  snapshotRevision?: number;
  messages: Message[]; latestSeq: number; turn: number; locatedMessageId: string | null;
  turnRuns: TurnRun[]; attempts: ReadonlyMap<string, GenerationSlot>; regenerationPrevious: Record<string, Message>;
}
export interface StoryEventReduction { accepted: boolean; deliver: boolean; event: SseEvent; state: StoryEventState; effects: readonly 'resync'[] }
/** All committed message merges and durable event gates are independent of connections, RAF and Zustand. */
export function reduceStoryEvent(state: StoryEventState, event: SseEvent): StoryEventReduction {
  const reject = (): StoryEventReduction => ({ accepted: false, deliver: false, event, state, effects: [] });
  const wire = event as SseEvent & { branch_revision?: number; outbox_event_id?: string; session_id?: string; branch_id?: string; delivery_source?: 'http' };
  const branchId = 'message' in event && event.message ? event.message.session_id
    : 'run' in event ? event.run.session_id : wire.session_id ?? wire.branch_id;
  if (!state.branchId || (branchId && branchId !== state.branchId)) return reject();
  const outboxKey = wire.outbox_event_id ? `${state.branchId}:${wire.outbox_event_id}` : null;
  if (outboxKey && state.seenOutboxIds.includes(outboxKey)) return reject();
  if (typeof wire.branch_revision === 'number' && wire.branch_revision < state.revision) {
    if (wire.delivery_source !== 'http' && outboxKey && wire.branch_revision > (state.snapshotRevision ?? state.revision)) {
      return { ...reject(), effects: ['resync'], state: { ...state, seenOutboxIds: [...state.seenOutboxIds, outboxKey].slice(-4096) } };
    }
    return reject();
  }
  // Unowned deltas must not seed an attempt that can block a later local pending.
  const deltaId = event.type === 'message.delta' ? event.message_id : null;
  if (deltaId && !state.messages.some(message => message.id === deltaId)) return reject();
  let attempts = state.attempts;
  if (['message.pending', 'message.final', 'message.delta', 'message.error'].includes(event.type)) {
    const message = 'message' in event ? event.message : undefined;
    const identity = { ...message, ...event } as GenerationEventIdentity;
    const messageId = message?.id ?? ('message_id' in event ? event.message_id : '');
    const kind = event.type.slice('message.'.length) as 'pending' | 'final' | 'delta' | 'error';
    const transition = reduceGenerationAttempt(state.attempts.get(messageId), identity, kind);
    if (!acceptsTurnIdentity(state.branchId, state.turnRuns, messageId, identity, kind) || !transition.accepted) return reject();
    if (transition.slot && transition.slot !== state.attempts.get(messageId)) attempts = new Map(state.attempts).set(messageId, transition.slot);
  }
  if (event.type === 'message.updated') {
    const operation = state.attempts.get(event.message.id)?.operation_id;
    if (state.regenerationPrevious[event.message.id] && operation && event.message.operation_id !== operation) return reject();
  }
  if ('message' in event && event.message) event = { ...event, message: projectMessage(event.message) };
  let next = { ...state, attempts,
    revision: Math.max(state.revision, wire.branch_revision ?? state.revision),
    seenOutboxIds: outboxKey ? [...state.seenOutboxIds, outboxKey].slice(-4096) : state.seenOutboxIds };
  if ('message' in event && event.message) {
    next = { ...next, latestSeq: Math.max(next.latestSeq, event.message.seq), turn: Math.max(next.turn, event.message.turn) };
    const end = state.messages[state.messages.length - 1]?.seq;
    if (state.locatedMessageId && end != null && event.message.seq > end + 1) return { accepted: true, deliver: false, event, state: next, effects: [] };
  }
  switch (event.type) {
    case 'message.final': next.messages = commitStoryMessage(state.messages, event.message); break;
    case 'message.updated': next.messages = upsertStoryMessage(state.messages, event.message); break;
    case 'message.deleted': next.messages = state.messages.filter(message => message.id !== event.message_id); break;
    case 'message.retracted': next.messages = state.messages.map(message => message.id === event.message_id ? { ...message, status: 'retracted' } : message); break;
    case 'reply.plan': next.messages = state.messages.map(message => message.actor === 'player' && message.turn === event.turn
      && (!event.basis_fingerprint || message.fingerprint === event.basis_fingerprint)
      ? { ...message, executed_reply_mode: event.mode, reply_order: event.order, reply_reason: event.reason, reply_basis_fingerprint: event.basis_fingerprint } : message); break;
  }
  return { accepted: true, deliver: true, event, state: next, effects: [] };
}
