import type { SseEvent } from '../../types';
import { projectMessage, projectTurnRun } from './storyView';
const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value));
export function decodeStoryEvent(type: SseEvent['type'], payload: unknown): SseEvent | null {
  if (!record(payload)) return null;
  const event = { ...payload, type };
  if (['message.pending', 'message.final', 'message.updated'].includes(type) || (type === 'message.error' && payload.message)) {
    const message = payload.message;
    if (!record(message) || typeof message.id !== 'string' || typeof message.session_id !== 'string'
      || typeof message.content !== 'string' || !Number.isInteger(message.seq) || !Number.isInteger(message.turn)) return null;
    return { ...event, message: projectMessage(message as unknown as import('../../types').Message) } as SseEvent;
  }
  if (type === 'turn.run.updated') {
    if (!record(payload.run) || typeof payload.run.operation_id !== 'string' || !Array.isArray(payload.run.slots)
      || !Number.isInteger(payload.branch_revision)) return null;
    return { ...event, run: projectTurnRun(payload.run as unknown as import('../../types').TurnRun) } as SseEvent;
  }
  if (['message.delta', 'message.error', 'message.deleted', 'message.retracted'].includes(type)
    && typeof payload.message_id !== 'string') return null;
  if (type === 'message.delta' && (typeof payload.delta !== 'string' || !Number.isInteger(payload.offset))) return null;
  if (type === 'message.error' && typeof payload.error !== 'string') return null;
  if (type === 'conversation.run.updated' && (!record(payload.run) || !Number.isInteger(payload.branch_revision))) return null;
  if (['director.pending', 'director.decision'].includes(type) && !record(payload.decision)) return null;
  if (type === 'scene.switched' && !record(payload.new_scene)) return null;
  if (type === 'cost.update' && !record(payload.cost_by_model)) return null;
  if (type === 'reply.plan' && !Array.isArray(payload.order)) return null;
  return event as SseEvent;
}
