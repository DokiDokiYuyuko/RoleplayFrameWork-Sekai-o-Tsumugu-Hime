import type { Message } from '../types';

/** Each server player segment confirms only its matching local bubble. */
export function removeConfirmedLocalSegment(messages: Message[], incoming: Message): Message[] {
  if (incoming.actor !== 'player' || messages.some((message) => message.id === incoming.id)) return messages;
  const candidates = messages.filter((message) =>
    message.id.startsWith('local-channel-')
    && message.actor === 'player'
    && message.turn === incoming.turn
    && message.status === 'pending',
  );
  const confirmed = candidates.find((message) => message.kind === incoming.kind && message.content === incoming.content)
    ?? candidates.find((message) => message.kind === incoming.kind);
  return confirmed ? messages.filter((message) => message.id !== confirmed.id) : messages;
}

/** Preserve still-unconfirmed segments when a reconnect must install a server snapshot. */
export function pendingAfterSnapshot(current: Message[], snapshot: Message[]): Message[] {
  let pending = current.filter((message) => message.status === 'pending');
  for (const fresh of snapshot) {
    if (fresh.actor === 'player' && fresh.status === 'final'
      && !current.some((message) => message.id === fresh.id && message.status === 'final')) {
      pending = removeConfirmedLocalSegment(pending, fresh);
    }
  }
  return pending.filter((message) => !snapshot.some((fresh) =>
    fresh.id === message.id || (
      !message.id.startsWith('local-channel-')
      && fresh.turn === message.turn && fresh.actor === message.actor && fresh.status === 'final'
    ),
  ));
}
