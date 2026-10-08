/** Resolve historical ownership without consulting mutable library cards. */
export function resolvePlayerIdentity(session, message) {
  const id = message ? message.player_identity_id : session?.player_identity_id;
  return session?.player_identities?.find((identity) => identity.id === id) ?? null;
}

export function isBeforePlayerBoundary(session, message) {
  const current = resolvePlayerIdentity(session);
  return Boolean(current && (message.seq < current.start_seq ||
    (message.player_identity_id && message.player_identity_id !== current.id)));
}

export function playerDraftKey(branchId, identityId) {
  return `mrp.playerDraft.${branchId ?? 'none'}.${identityId ?? 'legacy'}`;
}

export function playerAvatarUrl(branchId, identity) {
  return branchId && identity?.avatar_ref
    ? `/api/v1/sessions/${encodeURIComponent(branchId)}/player/avatar/${encodeURIComponent(identity.id)}`
    : undefined;
}

/** Current controls may use library art; historical messages keep frozen media. */
export function currentPlayerAvatarUrl(branchId, identity) {
  const captured = playerAvatarUrl(branchId, identity);
  if (captured) return captured;
  if (!branchId || !identity?.source_character_id) return undefined;
  return `/api/v1/characters/${encodeURIComponent(identity.source_character_id)}/avatar?v=${encodeURIComponent(String(identity.character?.updated_at ?? 0))}`;
}

export function canPersonSee(message, personId) {
  if (message.status === 'retracted' || message.control_event) return false;
  if (Array.isArray(message.known_to)) {
    if (!message.known_to.includes(personId)) return false;
    if (message.kind === 'inner' && message.person_id === personId) return true;
    return message.visible_to === 'all' || message.visible_to.includes(personId) || message.visible_to.includes('player');
  }
  return message.visible_to === 'all' || message.visible_to.includes(personId);
}
