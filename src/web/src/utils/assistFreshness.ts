import type { AssistBatch, Message } from '../types';

export function latestPlayerVisibleFinal(messages: Message[]): Message | null {
  return messages
    .filter((message) => message.status === 'final'
      && (message.visible_to === 'all' || message.visible_to.includes('player')))
    .reduce<Message | null>((latest, message) => !latest || message.seq > latest.seq ? message : latest, null);
}

export function isAssistBatchCurrent(batch: AssistBatch, branchId: string, messages: Message[], branchRevision?: number): boolean {
  const latest = latestPlayerVisibleFinal(messages);
  return Boolean(latest
    && batch.branch_id === branchId
    && (branchRevision === undefined || batch.branch_revision === branchRevision)
    && batch.anchor_message_id === latest.id
    && batch.anchor_fingerprint === latest.fingerprint);
}
