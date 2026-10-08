import type { Message } from '../types';

/**
 * Group adjacent player message segments that came from one submission.
 * New messages use input_group_id; legacy saves fall back to same-turn adjacency.
 */
export function buildPlayerInputGroups(messages: Message[]): Map<string, Message[]> {
  const groups = new Map<string, Message[]>();
  let index = 0;
  while (index < messages.length) {
    const first = messages[index];
    if (first.actor !== 'player') {
      index += 1;
      continue;
    }
    const explicitId = first.input_group_id ?? null;
    const sameGroup = (candidate: Message) => candidate.actor === 'player'
      && (explicitId
        ? candidate.input_group_id === explicitId
        : !candidate.input_group_id && candidate.turn === first.turn);
    const group = [first];
    let next = index + 1;
    while (next < messages.length && sameGroup(messages[next])) {
      group.push(messages[next]);
      next += 1;
    }
    for (const message of group) groups.set(message.id, group);
    index = next;
  }
  return groups;
}
