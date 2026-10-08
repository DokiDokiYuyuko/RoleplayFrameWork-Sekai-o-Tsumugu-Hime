/** Only reconcile saved targets after both the target list and branch snapshot are loaded. */
export function canReconcileComposerTargets(sessionId, loadedTargetsSessionId, snapshotLoadedSessionId) {
  return Boolean(sessionId && sessionId === loadedTargetsSessionId && sessionId === snapshotLoadedSessionId);
}

/** Remove actors the loaded branch says cannot speak. */
export function filterValidComposerTargets(recipients, characters, groups) {
  const characterById = new Map(characters.map((character) => [character.id, character]));
  const groupIds = new Set(groups.map((group) => group.id));
  return recipients.filter((id) => {
    const character = characterById.get(id);
    if (character) return character.present && !character.muted;
    return groupIds.has(id);
  });
}
